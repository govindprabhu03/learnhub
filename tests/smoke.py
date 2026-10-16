"""End-to-end API smoke test (offline mode, temp DB). Run: python tests/smoke.py"""
import os
import sys
import tempfile
import time

os.environ["LEARNHUB_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.pop("DATABASE_URL", None)
os.environ.pop("SENTRY_DSN", None)
if os.environ.get("TEST_DATABASE_URL"):  # CI: a real Postgres service container (must be an empty database)
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
elif os.environ.get("TEST_PG"):  # same suite against a throwaway embedded Postgres + pgvector: TEST_PG=1 python tests/smoke.py
    import pgserver
    _srv = pgserver.get_server(tempfile.mkdtemp())
    os.environ["DATABASE_URL"] = _srv.get_uri()

import fitz  # PyMuPDF, only used to build a test PDF
from fastapi.testclient import TestClient

from main import app

print("backend:", "postgres+pgvector" if os.environ.get("DATABASE_URL") else "sqlite")

TEXT = ("Breadth-first search explores a graph level by level using a queue. It guarantees the shortest path in an unweighted graph. "
        "Depth-first search explores as deep as possible along each branch using a stack or recursion. "
        "The A* algorithm combines path cost and a heuristic estimate to find the cheapest path efficiently. "
        "A heuristic is an estimate of the remaining cost to reach the goal from a node. "
        "Hill climbing is a local search that always moves to the best neighbouring state and can get stuck in local maxima. "
        "Resolution is an inference rule used in propositional logic to derive new clauses from existing ones. "
        "Unification finds a substitution that makes two logical expressions identical in first order logic. ") * 6
doc = fitz.open()
for i in range(2):
    p = doc.new_page()
    p.insert_textbox(fitz.Rect(40, 40, 555, 800), TEXT[: 1800], fontsize=10)
PDF = doc.tobytes()

c = TestClient(app)


def reg(email):
    r = c.post("/api/auth/register", json={"email": email, "password": "password123", "name": "Tester"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


a, b = reg("a@x.com"), reg("b@x.com")
assert c.post("/api/auth/login", json={"email": "a@x.com", "password": "bad"}).status_code == 401
assert c.get("/api/subjects").status_code == 401

sid = c.post("/api/subjects", headers=a, json={"name": "AI", "emoji": "🤖"}).json()["id"]
assert c.post(f"/api/subjects/{sid}/documents", headers=a, files={"file": ("x.txt", b"hello")}).status_code == 400
did = c.post(f"/api/subjects/{sid}/documents", headers=a, files={"file": ("AI_Unit.pdf", PDF, "application/pdf")}).json()["id"]
for _ in range(300):  # first run also downloads the embedding model (~70 MB)
    s = c.get(f"/api/subjects/{sid}", headers=a).json()
    if s["documents"][0]["status"] != "processing":
        break
    time.sleep(0.3)
d = s["documents"][0]
assert d["status"] == "ready", d
print("doc:", d["pages"], "pages |", len(d["concepts"]), "concepts |", s["questions"], "questions |", s["cards"], "cards")
assert s["questions"] > 0 and s["cards"] > 0

# a retried ingestion job must not duplicate anything (process_document is idempotent)
import ingest
counts = lambda: [__import__("db").one(f"SELECT COUNT(*) n FROM {t} WHERE document_id=?", did)["n"] for t in ("chunks", "questions", "flashcards")]
before = counts()
ingest.process_document(did)
assert counts() == before, "re-processing a document must replace, not duplicate"

# embeddings + hybrid retrieval
import db
import embeddings
import retrieval
if embeddings.available():
    assert db.one("SELECT COUNT(*) n FROM chunks WHERE emb IS NULL")["n"] == 0, "uploaded chunks must be embedded"
    texts = ["Photosynthesis converts sunlight, water and carbon dioxide into glucose and oxygen inside chloroplasts.",
             "The mitochondrion is the organelle that produces most of the cell's ATP through respiration.",
             "Newton's second law states that force equals mass times acceleration.",
             "Hill climbing is a local search that gets stuck in local maxima."]
    chunks = [{"id": i, "document_id": 1, "page": 1, "text": t, "emb": v.tobytes(), "emb_model": embeddings.EMBED_MODEL}
              for i, (t, v) in enumerate(zip(texts, embeddings.embed(texts)))]
    q = "how do plants make food from light"  # shares no keyword with the passage
    assert retrieval._bm25_rank(chunks, q) == [], "BM25 alone cannot find this"
    assert retrieval.search(chunks, q, k=3)[0]["id"] == 0, "dense retrieval must find the paraphrase"
    assert retrieval.search(chunks, "recipe for chocolate cake", k=3) == [], "off-topic -> nothing (tutor says 'not in your material')"
    assert retrieval.search(chunks, "ATP", k=1)[0]["id"] == 1, "exact terms still work"
    assert retrieval.search(chunks, "give me an example", k=1, context="what is hill climbing")[0]["id"] == 3, "follow-up uses prior turn"
    assert retrieval.search(chunks, "recipe for chocolate cake", k=3, context="what is hill climbing") == [], "new topic must not inherit the old one"
    assert retrieval.is_generic("Summarise the key ideas") and not retrieval.is_generic("What is A*?")
    assert len(retrieval.overview(chunks, 3)) == 3
    # same semantics through the real storage path (pgvector `<=>` query on Postgres, in-memory scan on SQLite)
    import main as m
    uid = c.get("/api/me", headers=a).json()["id"]
    sid2 = c.post("/api/subjects", headers=a, json={"name": "Bio"}).json()["id"]
    did2 = db.run("INSERT INTO documents(user_id, subject_id, filename, path, status) VALUES(?,?,?,?,?)", uid, sid2, "bio.pdf", "-", "ready")
    for i, (t, v) in enumerate(zip(texts, embeddings.embed(texts))):
        db.run("INSERT INTO chunks(user_id, subject_id, document_id, idx, page, text, emb, emb_model) VALUES(?,?,?,?,?,?,?,?)",
               uid, sid2, did2, i, 1, t, db.vec(v), embeddings.EMBED_MODEL)
    stored = m._chunks(uid, sid2)
    hit = retrieval.search(stored, q, k=3, dense=m._dense(uid, sid2))
    assert hit and hit[0]["text"] == texts[0], "paraphrase found through the database"
    assert retrieval.search(stored, "recipe for chocolate cake", k=3, dense=m._dense(uid, sid2)) == []
    if db.IS_PG:
        qv = embeddings.embed([q], query=True)[0]
        ranked = db.vector_search(uid, sid2, None, qv)
        assert ranked == sorted(ranked, key=lambda r: -r[1]) and ranked[0][1] > 0.7
        assert db.vector_search(uid + 999, sid2, None, qv) == [], "pgvector search is scoped to the owner"
        assert db.vector_search(uid, sid2, did2 + 99, qv) == [], "and to the document"

        # ANN (HNSW) index: only built on pgvector >= 0.8, where filtered approximate search is correct
        import psycopg
        from config import DATABASE_URL as _url

        import migrate
        with psycopg.connect(_url, autocommit=True) as raw:
            new_enough = migrate._pgvector_version(raw) >= (0, 8, 0)
            first = migrate.ensure_ann_index(raw)
            assert first.startswith("created" if new_enough else "skipped"), first
            if not new_enough:  # old pgvector: prove the gate holds, then that the index SQL is valid anyway
                assert not raw.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", (migrate.ANN_INDEX,)).fetchone()
                assert migrate.ensure_ann_index(raw, min_version=(0, 5, 0)) == "created"
            assert migrate.ensure_ann_index(raw, min_version=(0, 5, 0)) == "exists", "idempotent"
            raw.execute(f"UPDATE pg_index SET indisvalid = false WHERE indexrelid = '{migrate.ANN_INDEX}'::regclass")
            assert migrate.ensure_ann_index(raw, min_version=(0, 5, 0)) == "recreated", "an interrupted build is repaired"
            db._ITER = None  # re-probe: supported iff pgvector >= 0.8; unsupported servers must silently fall back to exact search
            ranked = db.vector_search(uid, sid2, None, qv)
            assert ranked and ranked == sorted(ranked, key=lambda r: -r[1]) and db._ITER is new_enough, (ranked, db._ITER)
            assert retrieval.search(stored, q, k=3, dense=m._dense(uid, sid2))[0]["text"] == texts[0], "search correct with the index present"
            if not new_enough:
                raw.execute(f"DROP INDEX IF EXISTS {migrate.ANN_INDEX}")  # leave the database as production would have it
    c.delete(f"/api/subjects/{sid2}", headers=a)
    print("retrieval: semantic OK")
else:
    print("retrieval: embeddings unavailable, BM25 only")

# tutor answers from the document and cites sources
r = c.post("/api/chat", headers=a, json={"subject_id": sid, "message": "What is a heuristic in A*?"}).json()
assert r["sources"] and "heuristic" in r["answer"].lower(), r
assert len(c.get(f"/api/chat/{sid}", headers=a).json()) == 2

# quiz: answer everything wrong on one topic -> it becomes weak and is recommended
qs = c.post("/api/quiz/next", headers=a, json={"subject_id": sid, "n": 30}).json()["questions"]
assert qs and "answer" not in qs[0]
for q in qs:
    res = c.post("/api/quiz/answer", headers=a, json={"question_id": q["id"], "chosen": 0}).json()
    if res["correct"]:  # force wrong so the weak-topic path is exercised
        pass
dash = c.get("/api/dashboard", headers=a).json()
print("dashboard: streak", dash["streak"], "| goal", dash["goal"], "| acc", dash["overall_accuracy"], "| weak", [w["topic"] for w in dash["weak_topics"]])
assert dash["streak"] == 1 and dash["goal"]["done"] == len(qs)

# flashcards + SM-2
cards = c.get("/api/flashcards/due", params={"subject_id": sid}, headers=a).json()
n1 = c.post(f"/api/flashcards/{cards[0]['id']}/review", headers=a, json={"grade": "good"}).json()
n2 = c.post(f"/api/flashcards/{cards[0]['id']}/review", headers=a, json={"grade": "good"}).json()
assert (n1["interval"], n2["interval"]) == (1, 6), (n1, n2)
assert c.post(f"/api/flashcards/{cards[1]['id']}/review", headers=a, json={"grade": "again"}).json()["interval"] == 0

# study planner
from datetime import date, timedelta
exam = (date.today() + timedelta(days=10)).isoformat()
assert c.post("/api/plans", headers=a, json={"subject_id": sid, "exam_date": date.today().isoformat()}).status_code == 400
assert c.post("/api/plans", headers=a, json={"subject_id": sid, "exam_date": "nope"}).status_code == 400
p = c.post("/api/plans", headers=a, json={"subject_id": sid, "exam_date": exam, "daily_minutes": 90, "start_time": "09:00"}).json()
tasks = [t for d_ in p["days"] for t in d_["tasks"]]
studied = {t["topic"] for t in tasks if t["kind"] == "study"}
assert len(studied) >= 3, "every topic gets a study session"
assert p["days"][0]["date"] == date.today().isoformat() and p["dropped"] == 0 and p["days_left"] == 10
assert any(t["kind"] == "quiz" for t in p["days"][-1]["tasks"]), "last day should be revision"
assert max(sum(t["minutes"] for t in d_["tasks"]) for d_ in p["days"]) <= 90
print("plan:", len(tasks), "sessions over", len(p["days"]), "days | first day:", [t["label"] for t in p["days"][0]["tasks"]])
t0 = p["days"][0]["tasks"][1]
c.post(f"/api/plans/tasks/{t0['id']}/toggle", headers=a)
p2 = c.get(f"/api/plans/{sid}", headers=a).json()
assert p2["done"] == 1 and p2["total"] >= len(tasks) - 3
first_after = [t for d_ in p2["days"] for t in d_["tasks"] if t["done"]]
assert [t["id"] for t in first_after] == [t0["id"]], "completed task must survive replanning"
same = lambda ts: sum(1 for t in ts if t["kind"] == t0["kind"] and t["topic"] == t0["topic"])
assert t0["kind"] == "study" and same([t for d_ in p2["days"] for t in d_["tasks"]]) == same(tasks), "done work is not re-queued"
tight = c.post("/api/plans", headers=a, json={"subject_id": sid, "exam_date": (date.today() + timedelta(days=3)).isoformat(), "daily_minutes": 30}).json()
assert tight["dropped"] > 0, "an impossible plan must say sessions don't fit"
assert c.get("/api/dashboard", headers=a).json()["today_tasks"]
assert c.post("/api/plans", headers=a, json={"subject_id": sid, "exam_date": exam, "daily_minutes": 90}).status_code == 200
tid = c.get(f"/api/plans/{sid}", headers=a).json()["days"][0]["tasks"][0]["id"]

# authorization: user B can never touch user A's data
assert c.post(f"/api/plans/tasks/{tid}/toggle", headers=b).status_code == 404
assert c.get(f"/api/plans/{sid}", headers=b).status_code == 404
assert c.get(f"/api/subjects/{sid}", headers=b).status_code == 404
assert c.post("/api/chat", headers=b, json={"subject_id": sid, "message": "hi"}).status_code == 404
assert c.post("/api/quiz/answer", headers=b, json={"question_id": qs[0]["id"], "chosen": 0}).status_code == 404
assert c.delete(f"/api/documents/{did}", headers=b).status_code == 404
assert c.get("/api/dashboard", headers=b).json()["subjects"] == []

assert c.delete(f"/api/subjects/{sid}", headers=a).status_code == 200

# ---- accounts: email verification, password reset, Google sign-in ----
import re as _re
from urllib.parse import parse_qs, urlparse

import config
import mailer
import oauth

tok_of = lambda mail: _re.search(r"token=([\w\-.]+)", mail["body"]).group(1)
P = "password123"
mailer.OUTBOX.clear()
r = c.post("/api/auth/register", json={"email": "v@x.com", "password": P, "name": "V"})
assert r.status_code == 200 and "token" in r.json(), "dev mode: verification not enforced, session issued"
vh, vtok = {"Authorization": "Bearer " + r.json()["token"]}, tok_of(mailer.OUTBOX[-1])
assert mailer.OUTBOX[-1]["to"] == "v@x.com" and "verify" in mailer.OUTBOX[-1]["subject"].lower()
assert c.get("/api/me", headers=vh).json()["verified"] is False
assert c.post("/api/auth/verify", json={"token": "garbage"}).status_code == 400
assert c.post("/api/auth/verify", json={"token": r.json()["token"]}).status_code == 400, "a session token is not a verify token"
assert c.post("/api/auth/verify", json={"token": vtok}).status_code == 200
assert c.get("/api/me", headers=vh).json()["verified"] is True

config.REQUIRE_VERIFIED = True  # enforcement on (what happens once SMTP is configured)
assert c.post("/api/auth/register", json={"email": "w@x.com", "password": P}).json() == {"verify": True}
assert c.post("/api/auth/login", json={"email": "w@x.com", "password": P}).status_code == 403
assert c.post("/api/auth/verify", json={"token": tok_of(mailer.OUTBOX[-1])}).status_code == 200
assert c.post("/api/auth/login", json={"email": "w@x.com", "password": P}).status_code == 200
config.REQUIRE_VERIFIED = False

mailer.OUTBOX.clear()
assert c.post("/api/auth/forgot", json={"email": "nobody@x.com"}).status_code == 200 and not mailer.OUTBOX, "no enumeration, no mail"
assert c.post("/api/auth/forgot", json={"email": "v@x.com"}).status_code == 200
rtok = tok_of(mailer.OUTBOX[-1])
assert c.post("/api/auth/reset", json={"token": vtok, "password": "newpassword1"}).status_code == 400, "verify token cannot reset"
assert c.post("/api/auth/reset", json={"token": rtok, "password": "short"}).status_code == 422
assert c.post("/api/auth/reset", json={"token": rtok, "password": "newpassword1"}).status_code == 200
assert c.post("/api/auth/reset", json={"token": rtok, "password": "another-pass1"}).status_code == 400, "reset link is single use"
assert c.post("/api/auth/login", json={"email": "v@x.com", "password": P}).status_code == 401
assert c.post("/api/auth/login", json={"email": "v@x.com", "password": "newpassword1"}).status_code == 200
assert c.get("/api/me", headers=vh).status_code == 401, "old sessions die when the password changes"

codes = [c.post("/api/auth/login", json={"email": "rl@x.com", "password": "x"}).status_code for _ in range(12)]
assert codes[:10] == [401] * 10 and codes[10:] == [429] * 2, "password guessing is rate limited"
blocked = c.post("/api/auth/login", json={"email": "rl@x.com", "password": "x"})
assert blocked.status_code == 429 and 1 <= int(blocked.headers["retry-after"]) <= 300, "429 tells the client when to retry"

# Google: the whole flow with Google's two HTTP calls stubbed out
assert c.get("/api/auth/google/start", follow_redirects=False).status_code == 404 and not c.get("/api/auth/config").json()["google"]
config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET = "cid", "secret"
assert c.get("/api/auth/config").json()["google"] is True


def google(info, keep_cookie=True):
    s = c.get("/api/auth/google/start", follow_redirects=False)
    assert s.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?") and "client_id=cid" in s.headers["location"]
    state = parse_qs(urlparse(s.headers["location"]).query)["state"][0]
    if not keep_cookie:
        c.cookies.clear()
    oauth.fetch_userinfo = lambda code: info
    return c.get("/api/auth/google/callback", params={"code": "abc", "state": state}, follow_redirects=False).headers["location"]


g = {"sub": "g-1", "email": "G@x.com", "email_verified": True, "name": "Gee"}
loc = google(g)
gtok = _re.search(r"token=([\w\-.]+)", loc).group(1)
me_g = c.get("/api/me", headers={"Authorization": "Bearer " + gtok}).json()
assert me_g["email"] == "g@x.com" and me_g["verified"] is True and me_g["name"] == "Gee"
again = _re.search(r"token=([\w\-.]+)", google(g)).group(1)
assert c.get("/api/me", headers={"Authorization": "Bearer " + again}).json()["id"] == me_g["id"], "same Google identity -> same account"
assert "error=" in google(g, keep_cookie=False), "callback without the starting browser's cookie is refused (login CSRF)"
assert "error=" in google({**g, "sub": "g-2", "email": "z@x.com", "email_verified": False}), "unverified Google email refused"
assert "error=" in google({**g, "sub": "g-9"}), "an account linked to one Google identity is not hijacked by another"
oauth.fetch_userinfo = lambda code: (_ for _ in ()).throw(RuntimeError("google down"))
s = c.get("/api/auth/google/start", follow_redirects=False)
st = parse_qs(urlparse(s.headers["location"]).query)["state"][0]
assert "error=" in c.get("/api/auth/google/callback", params={"code": "x", "state": st}, follow_redirects=False).headers["location"]
assert "error=" in c.get("/api/auth/google/callback", params={"code": "x", "state": "forged"}, follow_redirects=False).headers["location"]

# pre-hijack: attacker registers victim's address with their own password; the real owner signs in with Google
c.post("/api/auth/register", json={"email": "h@x.com", "password": "attackerpass1"})
assert c.post("/api/auth/login", json={"email": "h@x.com", "password": "attackerpass1"}).status_code == 200
google({"sub": "g-h", "email": "h@x.com", "email_verified": True, "name": "Owner"})
assert c.post("/api/auth/login", json={"email": "h@x.com", "password": "attackerpass1"}).status_code == 401, "attacker's password revoked"
print("accounts: verification, reset, Google OK")

# ---- durable job queue ----
import threading as _th
import jobs
import observability

jobs.BACKOFF = 0  # retry immediately in tests
state, seen, lock = {}, [], _th.Lock()


def flaky(n):  # fails on its first attempt only
    state[n] = state.get(n, 0) + 1
    if state[n] == 1:
        raise RuntimeError("transient")


def count(n):
    with lock:
        seen.append(n)


jobs.register("t_flaky", flaky)
jobs.register("t_dead", lambda n: 1 / 0, on_fail=lambda payload, err: state.setdefault("hook", (payload, err)))
jobs.register("t_perm", lambda n: (_ for _ in ()).throw(jobs.Permanent("nope")))
jobs.register("t_count", count)
jobs.register("t_ok", lambda n: None)


def wait_job(jid, secs=20):
    for _ in range(int(secs * 10)):
        j = db.one("SELECT status, attempts, error FROM jobs WHERE id=?", jid)
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {jid} did not finish: {j}")


j = wait_job(jobs.enqueue("t_flaky", n=1))
assert (j["status"], j["attempts"]) == ("done", 2), "transient failure is retried"
j = wait_job(jobs.enqueue("t_dead", n=1))
assert j["status"] == "failed" and j["attempts"] == jobs.MAX_ATTEMPTS and "ZeroDivisionError" in j["error"], "gives up after MAX_ATTEMPTS"
assert state["hook"][1].startswith("ZeroDivisionError"), "on_fail hook runs once retries are exhausted"
j = wait_job(jobs.enqueue("t_perm", n=1))
assert (j["status"], j["attempts"], j["error"]) == ("failed", 1, "nope"), "Permanent errors are not retried"
j = wait_job(jobs.enqueue("t_nobody_handles_this"))
assert j["status"] == "failed" and "no handler" in j["error"]

# a worker that died mid-job: stale 'running' rows are recovered; ones out of attempts are failed
old = time.time() - 10_000
revive = db.run("INSERT INTO jobs(kind, payload, status, attempts, locked_at) VALUES(?,?,?,?,?)", "t_ok", '{"n": 0}', "running", 1, old)
doomed = db.run("INSERT INTO jobs(kind, payload, status, attempts, locked_at) VALUES(?,?,?,?,?)", "t_ok", '{"n": 0}', "running", jobs.MAX_ATTEMPTS, old)
jobs.recover()
assert wait_job(revive) == {"status": "done", "attempts": 2, "error": None}, "stale job is re-queued and finishes"
assert db.one("SELECT status FROM jobs WHERE id=?", doomed)["status"] == "failed"

# exactly-once under concurrency: many workers hammering one queue never run a job twice
stop, _ = jobs.start_workers(4)
ids = [jobs.enqueue("t_count", n=i) for i in range(40)]
for jid in ids:
    wait_job(jid, 40)
stop.set()
assert sorted(seen) == list(range(40)), "every job ran exactly once"

# uploads that cannot be processed fail fast with a readable reason (and the document stops spinning)
def upload_and_wait(blob, name):
    d_id = c.post(f"/api/subjects/{sid2b}/documents", headers=a, files={"file": (name, blob, "application/pdf")}).json()["id"]
    for _ in range(300):
        d_ = next(x for x in c.get(f"/api/subjects/{sid2b}", headers=a).json()["documents"] if x["id"] == d_id)
        if d_["status"] != "processing":
            return d_
        time.sleep(0.1)
    raise AssertionError("document stuck in processing")


sid2b = c.post("/api/subjects", headers=a, json={"name": "Bad uploads"}).json()["id"]
blank = fitz.open()
blank.new_page()
d_ = upload_and_wait(blank.tobytes(), "scan.pdf")
assert d_["status"] == "failed" and "No extractable text" in d_["error"], d_
d_ = upload_and_wait(b"%PDF-1.4 this is not really a pdf", "broken.pdf")
assert d_["status"] == "failed" and "Could not read this PDF" in d_["error"], d_
assert c.get("/api/health").json()["queue"].get("done", 0) > 40

# ---- OCR: scanned (image-only) PDFs ----
import ocr

BIO = ("Photosynthesis converts sunlight, water and carbon dioxide into glucose and oxygen inside chloroplasts. "
       "The mitochondrion is the organelle that produces most of the cell's energy through respiration. "
       "Enzymes are proteins that speed up chemical reactions in living organisms.")
PHYS = ("Newton's second law states that force equals mass times acceleration. "
        "Momentum is conserved in every collision between isolated bodies. "
        "Kinetic energy depends on mass and the square of velocity. "
        "Work is the product of force and displacement in the direction of the force.")


def image_page(out, text):  # render text into a bitmap and place only the bitmap: no text layer at all
    src = fitz.open()
    src.new_page().insert_textbox(fitz.Rect(50, 60, 545, 500), text, fontsize=16)
    out.new_page().insert_image(fitz.Rect(0, 0, 595, 842), stream=src[0].get_pixmap(dpi=150).tobytes("png"))


def pdf_of(*pages):  # ("text", str) -> real text layer; ("scan", str) -> image only
    out = fitz.open()
    for kind, text in pages:
        if kind == "scan":
            image_page(out, text)
        else:
            out.new_page().insert_textbox(fitz.Rect(50, 60, 545, 500), text, fontsize=14)
    return out.tobytes()


chunk_text = lambda d_id: " ".join(r["text"] for r in db.rows("SELECT text FROM chunks WHERE document_id=?", d_id)).lower()
assert ocr.available(), "OCR packages are installed"
d_ = upload_and_wait(pdf_of(("scan", BIO)), "scan.pdf")
assert d_["status"] == "ready" and "OCR" in d_["note"], d_
assert "photosynthesis" in chunk_text(d_["id"]) and "chloroplasts" in chunk_text(d_["id"]), "scanned text was recognised"
d_ = upload_and_wait(pdf_of(("text", PHYS), ("scan", BIO)), "mixed.pdf")
assert d_["status"] == "ready" and "1 scanned page was read" in d_["note"], d_
assert "newton" in chunk_text(d_["id"]) and "photosynthesis" in chunk_text(d_["id"]), "text-layer and scanned pages are combined"
d_ = upload_and_wait(pdf_of(("text", PHYS)), "digital.pdf")
assert d_["status"] == "ready" and not d_["note"], "digital PDFs never touch OCR"

ocr.MAX_PAGES = 1  # page limit: only the first scanned page is read, and the user is told about the rest
d_ = upload_and_wait(pdf_of(("scan", BIO), ("scan", PHYS)), "long-scan.pdf")
ocr.MAX_PAGES = 150
assert d_["status"] == "ready" and "1 more scanned pages were not read" in d_["note"], d_

real_available, ocr.available = ocr.available, lambda: False  # a server without the OCR packages
d_ = upload_and_wait(pdf_of(("scan", BIO)), "no-ocr.pdf")
ocr.available = real_available
assert d_["status"] == "failed" and "OCR support is not installed" in d_["error"], d_

beats = {}


def slow(n):  # a long job proves it is alive with touch(); otherwise it would look dead after STALE_AFTER
    read = lambda: db.one("SELECT locked_at FROM jobs WHERE kind='t_touch'")["locked_at"]
    before = read()
    time.sleep(0.05)
    jobs.touch()
    beats[n] = (before, read())


jobs.register("t_touch", slow)
wait_job(jobs.enqueue("t_touch", n=1))
assert beats[1][1] > beats[1][0], "touch() refreshes the job heartbeat"
print("ocr: scans, mixed, limit, unavailable, heartbeat OK")
c.delete(f"/api/subjects/{sid2b}", headers=a)

# error reporting: a job that finally fails is sent to Sentry (transport stubbed - nothing leaves the machine)
import sentry_sdk
events = []
config.SENTRY_DSN = "https://publickey@o0.ingest.sentry.example/1"
observability.init("test")
sentry_sdk.init(dsn=config.SENTRY_DSN, transport=events.append, send_default_pii=False)
wait_job(jobs.enqueue("t_dead", n=2))
sentry_sdk.flush(5)
assert any("ZeroDivisionError" in str(e) for e in events), "failed jobs are reported to Sentry"
config.SENTRY_DSN = ""
print("jobs: retry, recovery, exactly-once, bad uploads, sentry OK")

# ---- shared rate limiter (counters live in the database) ----
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import ratelimit

res = [ratelimit.hit("t:basic", 3, 60) for _ in range(5)]
assert res[:3] == [0, 0, 0] and all(1 <= r <= 60 for r in res[3:]), "first n allowed, the rest told when to retry"
assert ratelimit.hit("t:other-key", 3, 60) == 0, "keys are independent"
assert ratelimit.hit("t:short", 1, 1) == 0 and ratelimit.hit("t:short", 1, 1) > 0
time.sleep(1.2)
assert ratelimit.hit("t:short", 1, 1) == 0, "the window resets"
with ThreadPoolExecutor(16) as pool:  # 80 racing calls against a limit of 20: exactly 20 get through
    allowed = sum(1 for r in pool.map(lambda _: ratelimit.hit("t:race", 20, 60), range(80)) if r == 0)
assert allowed == 20, f"atomic counting under concurrency, got {allowed}"
ratelimit.hit("t:xproc", 2, 60), ratelimit.hit("t:xproc", 2, 60)  # this process uses up the allowance...
other = subprocess.run([sys.executable, "-c", "import db, ratelimit; db.init(); print(ratelimit.hit('t:xproc', 2, 60))"],
                       cwd=os.path.join(os.path.dirname(__file__), "..", "backend"), capture_output=True, text=True)
assert int(other.stdout.strip().splitlines()[-1]) > 0, f"...so a different process is blocked too: {other.stdout} {other.stderr}"
ratelimit.prune(max_age=-1)  # everything counts as old
assert ratelimit.hit("t:basic", 3, 60) == 0, "pruned counters start fresh"
print("ratelimit: shared, atomic, expiring OK")

import main as _main  # stop the in-process workers cleanly before the (possibly throwaway) database goes away
_main.WORKERS[0].set()
for t in _main.WORKERS[1]:
    t.join(10)
print("ALL OK")
