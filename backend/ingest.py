"""Document ingestion, run as a background job: extract -> clean -> chunk -> embed -> summarise -> quiz + flashcards."""
import json
import random

import ai
import db
import embeddings
import jobs
import learning
import ocr
import pdfproc
from config import EMBED_MODEL


def save_items(uid, sid, did, items):
    db.many("INSERT INTO questions(user_id, subject_id, document_id, topic, question, options, answer, explanation, difficulty) "
            "VALUES(?,?,?,?,?,?,?,?,?)", [_shuffled(uid, sid, did, q) for q in items["questions"]])
    db.many("INSERT INTO flashcards(user_id, subject_id, document_id, topic, front, back, due) VALUES(?,?,?,?,?,?,?)",
            [(uid, sid, did, c["topic"], c["front"], c["back"], learning.today()) for c in items["flashcards"]])


def _shuffled(uid, sid, did, q):
    """LLMs favour certain answer positions; shuffle and re-point the correct index."""
    correct = q["options"][q["answer"]]
    opts = q["options"][:]
    random.shuffle(opts)
    return (uid, sid, did, q["topic"], q["question"], json.dumps(opts), opts.index(correct), q["explanation"], q["difficulty"])


def process_document(document_id):
    """Idempotent: a retry wipes whatever a failed attempt left behind and starts clean."""
    d = db.one("SELECT * FROM documents WHERE id=?", document_id)
    if not d:  # deleted while it was queued
        return
    did = document_id
    for t in ("flashcards", "questions", "chunks"):
        db.run(f"DELETE FROM {t} WHERE document_id=?", did)
    db.run("UPDATE documents SET note=NULL WHERE id=?", did)

    def progress(n, total):  # shown live in the UI; also keeps the job's heartbeat fresh during long scans
        db.run("UPDATE documents SET note=? WHERE id=?", f"Reading scanned pages with OCR: {n} of {total}", did)
        jobs.touch()

    try:
        pages, scan = pdfproc.extract_with_ocr(d["path"], progress)
    except Exception as e:  # unreadable file: retrying will not help
        raise jobs.Permanent(f"Could not read this PDF ({type(e).__name__}). Is it damaged or password-protected?")
    chunks = pdfproc.chunk(pages)
    if sum(len(c["text"]) for c in chunks) < 200:
        if scan["unavailable"]:
            why = "OCR support is not installed on this server, so scanned pages cannot be read."
        elif scan["tried"]:
            why = "OCR could not find readable text on the scanned pages (blank, very low quality, or handwriting?)."
        else:
            why = "The document contains almost no text."
        raise jobs.Permanent(f"No extractable text found. {why}")
    vecs = embeddings.embed([c["text"] for c in chunks])  # None -> BM25-only; the startup backfill fills them in later
    db.many("INSERT INTO chunks(user_id, subject_id, document_id, idx, page, text, emb, emb_model) VALUES(?,?,?,?,?,?,?,?)",
            [(d["user_id"], d["subject_id"], did, c["idx"], c["page"], c["text"],
              db.vec(vecs[i]) if vecs is not None else None, EMBED_MODEL if vecs is not None else None)
             for i, c in enumerate(chunks)])
    info = ai.analyze(pdfproc.sample_text(chunks, 24000))
    concepts = info["concepts"] or ["General"]
    db.run("UPDATE documents SET summary=?, concepts=?, important_qs=?, pages=? WHERE id=?",
           info["summary"], json.dumps(concepts), json.dumps(info["important_questions"]), len(pages), did)
    save_items(d["user_id"], d["subject_id"], did, ai.study_items(pdfproc.sample_text(chunks, 14000), concepts))
    note = None
    if scan["ocr"]:
        note = f"{scan['ocr']} scanned page{'s were' if scan['ocr'] != 1 else ' was'} read with OCR, so some words may be misread."
    if scan["skipped"]:
        note = (note + " " if note else "") + f"{scan['skipped']} more scanned pages were not read (limit {ocr.MAX_PAGES} pages per document)."
    db.run("UPDATE documents SET status='ready', error=NULL, note=? WHERE id=?", note, did)


def mark_failed(payload, err):
    """Called by the queue once retries are exhausted, so the UI stops spinning and shows the reason."""
    db.run("UPDATE documents SET status='failed', error=? WHERE id=?", err[:300], payload["document_id"])


jobs.register("process_document", process_document, on_fail=mark_failed)
