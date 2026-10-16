import logging
import re

from pypdf import PdfReader

import ocr

log = logging.getLogger("pdfproc")


def clean(t: str) -> str:
    t = t.replace("\x00", "")
    t = re.sub(r"-\n(?=[a-z])", "", t)          # de-hyphenate line breaks
    t = re.sub(r"(?<![.!?:\n])\n(?!\n)", " ", t)  # join soft-wrapped lines
    t = re.sub(r"^\s*\d{1,4}\s*$", "", t, flags=re.M)  # lone page numbers
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def extract_pages(path: str):
    return [clean(p.extract_text() or "") for p in PdfReader(path).pages]


MIN_TEXT_CHARS = 40  # fewer letters/digits than this on a page -> treat it as scanned


def extract_with_ocr(path: str, progress=None):
    """(pages, info). Pages that have a text layer are used as-is; pages without one (scans, photos) are OCR'd.
    info = {"ocr": pages read with OCR, "tried": pages OCR was attempted on, "skipped": scanned pages left
    unread because of the page limit, "unavailable": OCR packages missing}.
    `progress(done, total)` is called after each OCR'd page."""
    pages = extract_pages(path)
    weak = [i for i, t in enumerate(pages) if sum(c.isalnum() for c in t) < MIN_TEXT_CHARS]
    info = {"ocr": 0, "tried": 0, "skipped": 0, "unavailable": False}
    if not weak:
        return pages, info
    if not ocr.available():
        info["unavailable"] = True
        return pages, info
    todo = weak[:ocr.MAX_PAGES]
    info["tried"], info["skipped"] = len(todo), len(weak) - len(todo)
    try:
        for n, (i, img) in enumerate(ocr.render_pages(path, todo), 1):
            try:
                text = clean(ocr.recognize(img))
            except Exception:  # one bad page must not sink the whole document
                log.exception("OCR failed on page %d", i + 1)
                text = ""
            if text:
                pages[i], info["ocr"] = text, info["ocr"] + 1
            if progress:
                progress(n, len(todo))
    except Exception:
        log.exception("could not render pages for OCR")
    return pages, info


def chunk(pages, size=900):
    """Sentence-aware chunks (~size chars) that never cross a page, with one sentence of overlap."""
    out = []
    for pno, text in enumerate(pages, 1):
        if not text:
            continue
        sents = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
        cur = []
        for s in sents:
            if cur and sum(len(x) + 1 for x in cur) + len(s) > size:
                out.append({"page": pno, "text": " ".join(cur)})
                cur = cur[-1:]
            cur.append(s)
        if cur:
            out.append({"page": pno, "text": " ".join(cur)})
    for i, c in enumerate(out):
        c["idx"] = i
    return out


def sample_text(chunks, limit):
    """Evenly spaced chunks up to `limit` chars so long documents are still covered end to end."""
    texts = [c["text"] for c in chunks]
    if sum(len(t) for t in texts) <= limit:
        return "\n\n".join(texts)
    per = max(1, limit // 900)
    step = len(texts) / per
    return "\n\n".join(texts[int(i * step)] for i in range(per))
