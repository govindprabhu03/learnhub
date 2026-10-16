import hmac
import json
import os
import random
import re
import secrets
import threading
import uuid
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import ai
import auth
import config
import db
import embeddings
import ingest
import jobs
import learning
import mailer
import migrate
import oauth
import observability
import planner
import ratelimit
import retrieval
from config import MAX_UPLOAD, ROOT, UPLOADS

observability.init("web")  # before FastAPI() is created, so Sentry can instrument it
migrate.upgrade()  # Alembic owns the schema (SQLite or Postgres+pgvector)
db.init()
WORKERS = None
if config.WORKER_MODE == "inline":  # default: job workers live in this process. "external": run backend/worker.py instead
    jobs.recover()
    WORKERS = jobs.start_workers(config.WORKER_THREADS)  # (stop_event, threads)
threading.Thread(target=embeddings.warm_and_backfill, daemon=True).start()  # load model + embed pre-existing chunks
app = FastAPI(title="LearnHub")


# ---------- auth / ownership ----------
def user(authorization: str = Header(None)):
    d = auth.unsign((authorization or "").removeprefix("Bearer ").strip(), "session")
    u = d and db.one("SELECT id, email, name, pw, email_verified FROM users WHERE id=?", d["uid"])
    # a token minted before the last password change carries an old fingerprint -> rejected
    if not u or d.get("f") not in (None, auth.fingerprint(u["pw"])):
        raise HTTPException(401, "Not authenticated")
    return {"id": u["id"], "email": u["email"], "name": u["name"], "verified": bool(u["email_verified"])}


def owned(table, id, uid):
    """Every row access is scoped by user_id, so one user can never read another's data."""
    row = db.one(f"SELECT * FROM {table} WHERE id=? AND user_id=?", id, uid)
    if not row:
        raise HTTPException(404, "Not found")
    return row


def limit(key, bucket, n=20, per=60):
    """At most n calls per `per` seconds for this key (user id or email). Counted in the database, so it is shared
    by every process and replica."""
    wait = ratelimit.hit(f"{bucket}:{key}", n, per)
    if wait:
        raise HTTPException(429, "Too many requests, slow down a little", headers={"Retry-After": str(wait)})


class Login(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=128)


class Cred(Login):
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=60)


class EmailIn(BaseModel):
    email: str = Field(max_length=200)


class TokenIn(BaseModel):
    token: str = Field(max_length=2000)


class ResetIn(TokenIn):
    password: str = Field(min_length=8, max_length=128)


def _session(u):
    return {"token": auth.make_token(u["id"], u["pw"]), "user": {"id": u["id"], "name": u["name"], "email": u["email"]}}


def _by_email(email):
    return db.one("SELECT * FROM users WHERE email=?", email.strip().lower())


def _link(page, token):
    return f"{config.APP_URL}/#/{page}?token={token}"


def _send_verification(u):
    tok = auth.sign({"p": "verify", "uid": u["id"], "email": u["email"]}, 3 * 86400)
    mailer.send(u["email"], "Verify your LearnHub email",
                f"Hi {u['name']},\n\nConfirm your email to finish setting up LearnHub:\n{_link('verify', tok)}\n\n"
                "This link expires in 3 days. If you didn't sign up, you can ignore this email.")


def _send_reset(u):
    tok = auth.sign({"p": "reset", "uid": u["id"], "f": auth.fingerprint(u["pw"])}, 3600)
    mailer.send(u["email"], "Reset your LearnHub password",
                f"Hi {u['name']},\n\nChoose a new password here:\n{_link('reset', tok)}\n\n"
                "This link works once and expires in 1 hour. If you didn't ask for it, ignore this email - your password is unchanged.")


@app.get("/api/auth/config")
def auth_config():
    return {"google": oauth.configured(), "verification_required": config.REQUIRE_VERIFIED}


@app.post("/api/auth/register")
def register(b: Cred, bg: BackgroundTasks):
    email = b.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Enter a valid email")
    if db.one("SELECT id FROM users WHERE email=?", email):
        raise HTTPException(409, "That email is already registered")
    name = b.name.strip() or email.split("@")[0]
    uid = db.run("INSERT INTO users(email, name, pw) VALUES(?,?,?)", email, name, auth.hash_pw(b.password))
    u = db.one("SELECT * FROM users WHERE id=?", uid)
    bg.add_task(_send_verification, u)
    return {"verify": True} if config.REQUIRE_VERIFIED else _session(u)


@app.post("/api/auth/login")
def login(b: Login):
    limit(b.email.strip().lower(), "login", n=10, per=300)  # slows password guessing per account
    u = _by_email(b.email)
    if not u or not auth.check_pw(b.password, u["pw"]):
        raise HTTPException(401, "Wrong email or password")
    if config.REQUIRE_VERIFIED and not u["email_verified"]:
        raise HTTPException(403, "Please verify your email first - check your inbox, or request a new link")
    return _session(u)


@app.post("/api/auth/resend")
def resend(b: EmailIn, bg: BackgroundTasks):
    limit(b.email.strip().lower(), "mail", n=5, per=3600)
    u = _by_email(b.email)
    if u and not u["email_verified"]:
        bg.add_task(_send_verification, u)
    return {"ok": True}  # same answer whether or not the account exists


@app.post("/api/auth/verify")
def verify(b: TokenIn):
    d = auth.unsign(b.token, "verify")
    u = d and db.one("SELECT * FROM users WHERE id=? AND email=?", d["uid"], d["email"])
    if not u:
        raise HTTPException(400, "This link is invalid or has expired")
    db.run("UPDATE users SET email_verified=1 WHERE id=?", u["id"])
    return _session(u)


@app.post("/api/auth/forgot")
def forgot(b: EmailIn, bg: BackgroundTasks):
    limit(b.email.strip().lower(), "mail", n=5, per=3600)
    u = _by_email(b.email)
    if u:
        bg.add_task(_send_reset, u)
    return {"ok": True}  # never reveals whether the email is registered


@app.post("/api/auth/reset")
def reset(b: ResetIn):
    d = auth.unsign(b.token, "reset")
    u = d and db.one("SELECT * FROM users WHERE id=?", d["uid"])
    if not u or d["f"] != auth.fingerprint(u["pw"]):  # the fingerprint changes once the password does -> single use
        raise HTTPException(400, "This link is invalid or has expired")
    db.run("UPDATE users SET pw=?, email_verified=1 WHERE id=?", auth.hash_pw(b.password), u["id"])  # inbox access proves the email
    return _session(db.one("SELECT * FROM users WHERE id=?", u["id"]))


# ---------- Google sign-in ----------
def _bounce(error):
    return RedirectResponse(f"/#/signin?error={quote(error)}")


@app.get("/api/auth/google/start")
def google_start():
    if not oauth.configured():
        raise HTTPException(404, "Google sign-in is not configured")
    nonce = secrets.token_urlsafe(16)
    r = RedirectResponse(oauth.authorize_url(auth.sign({"p": "gstate", "n": nonce}, 600)))
    # the nonce also lives in a cookie, so a callback can only complete in the browser that started it (login CSRF)
    r.set_cookie("lh_oauth", nonce, max_age=600, httponly=True, samesite="lax", secure=config.APP_URL.startswith("https"))
    return r


@app.get("/api/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    st = auth.unsign(state, "gstate")
    if error or not code or not st or not hmac.compare_digest(st["n"].encode(), request.cookies.get("lh_oauth", "").encode()):
        return _bounce("Google sign-in was cancelled or could not be verified. Please try again.")
    try:
        info = oauth.fetch_userinfo(code)
    except Exception:
        return _bounce("Could not reach Google. Please try again.")
    if not info.get("sub") or not info.get("email") or not info.get("email_verified"):
        return _bounce("Your Google account's email is not verified.")
    email, sub = info["email"].strip().lower(), info["sub"]
    u = db.one("SELECT * FROM users WHERE google_sub=?", sub) or _by_email(email)
    if u:
        if u["google_sub"] and u["google_sub"] != sub:
            return _bounce("That email is already linked to a different Google account.")
        if not u["email_verified"]:
            # pre-hijack defence: someone may have registered this address with a password they chose before its owner
            # ever proved control. Google just did, so that password is revoked (this also kills their sessions).
            db.run("UPDATE users SET pw=? WHERE id=?", "!" + secrets.token_hex(16), u["id"])
        db.run("UPDATE users SET google_sub=?, email_verified=1 WHERE id=?", sub, u["id"])
    else:
        db.run("INSERT INTO users(email, name, pw, email_verified, google_sub) VALUES(?,?,?,?,?)",
               email, (info.get("name") or email.split("@")[0])[:60], "!" + secrets.token_hex(16), 1, sub)
    u = db.one("SELECT * FROM users WHERE google_sub=?", sub)
    resp = RedirectResponse(f"/#/signin?token={auth.make_token(u['id'], u['pw'])}")  # fragment: never sent to any server
    resp.delete_cookie("lh_oauth")
    return resp


@app.get("/api/health")
def health():
    return {"ok": True, "db": "postgres+pgvector" if db.IS_PG else "sqlite", "semantic": embeddings.available(),
            "workers": config.WORKER_MODE, "queue": jobs.stats()}


@app.get("/api/me")
def me(u=Depends(user)):
    return {**u, "ai": ai.enabled(), "semantic": embeddings.available()}


# ---------- subjects ----------
class SubjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    emoji: str = Field(default="📘", max_length=8)


@app.get("/api/subjects")
def subjects(u=Depends(user)):
    return db.rows("SELECT id, name, emoji FROM subjects WHERE user_id=? ORDER BY id", u["id"])


@app.post("/api/subjects")
def add_subject(b: SubjectIn, u=Depends(user)):
    return {"id": db.run("INSERT INTO subjects(user_id, name, emoji) VALUES(?,?,?)", u["id"], b.name.strip(), b.emoji)}


@app.delete("/api/subjects/{sid}")
def del_subject(sid: int, u=Depends(user)):
    owned("subjects", sid, u["id"])
    for d in db.rows("SELECT path FROM documents WHERE subject_id=?", sid):
        _rm(d["path"])
    db.run("DELETE FROM reviews WHERE card_id IN (SELECT id FROM flashcards WHERE subject_id=?)", sid)
    for t in ("messages", "attempts", "flashcards", "questions", "chunks"):
        db.run(f"DELETE FROM {t} WHERE subject_id=?", sid)
    db.run("DELETE FROM subjects WHERE id=?", sid)
    return {"ok": True}


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


@app.get("/api/subjects/{sid}")
def subject(sid: int, u=Depends(user)):
    s = owned("subjects", sid, u["id"])
    docs = db.rows("SELECT id, filename, status, error, note, pages, summary, concepts, important_qs, created FROM documents "
                   "WHERE subject_id=? AND user_id=? ORDER BY id DESC", sid, u["id"])
    for d in docs:
        d["concepts"], d["important_qs"] = json.loads(d["concepts"]), json.loads(d["important_qs"])
    n = lambda t, extra="": db.one(f"SELECT COUNT(*) n FROM {t} WHERE subject_id=? AND user_id=?{extra}", sid, u["id"], *([learning.today()] if extra else []))["n"]
    return {"subject": {"id": s["id"], "name": s["name"], "emoji": s["emoji"]}, "documents": docs,
            "questions": n("questions"), "cards": n("flashcards"), "due_cards": n("flashcards", " AND due<=?"),
            "topics": learning.topic_stats(u["id"], sid), "recommendations": learning.recommendations(u["id"], sid)}


# ---------- documents ----------
@app.post("/api/subjects/{sid}/documents")
async def upload(sid: int, file: UploadFile = File(...), u=Depends(user)):
    owned("subjects", sid, u["id"])
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "PDF is larger than 25 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Only PDF files are supported")
    folder = os.path.join(UPLOADS, str(u["id"]))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, uuid.uuid4().hex + ".pdf")
    with open(path, "wb") as f:
        f.write(data)
    did = db.run("INSERT INTO documents(user_id, subject_id, filename, path) VALUES(?,?,?,?)",
                 u["id"], sid, os.path.basename(file.filename or "document.pdf")[:120], path)
    jobs.enqueue("process_document", document_id=did)  # durable: picked up by a worker, retried on failure
    return {"id": did, "status": "processing"}


@app.delete("/api/documents/{did}")
def del_document(did: int, u=Depends(user)):
    d = owned("documents", did, u["id"])
    db.run("DELETE FROM reviews WHERE card_id IN (SELECT id FROM flashcards WHERE document_id=?)", did)
    for t in ("flashcards", "questions", "chunks"):
        db.run(f"DELETE FROM {t} WHERE document_id=?", did)
    db.run("DELETE FROM documents WHERE id=?", did)
    _rm(d["path"])
    return {"ok": True}


# ---------- tutor (RAG) ----------
def _chunks(uid, sid, did=None):
    """Chunk text for BM25. SQLite also loads the vectors (scanned in memory); Postgres keeps them in the database."""
    cols = "id, document_id, page, text" + ("" if db.IS_PG else ", emb, emb_model")
    sql = f"SELECT {cols} FROM chunks WHERE user_id=? AND subject_id=?"
    return db.rows(sql + (" AND document_id=?" if did else ""), uid, sid, *([did] if did else []))


def _dense(uid, sid, did=None):
    """Postgres: similarity search runs inside the database with pgvector. SQLite: None -> in-memory scan."""
    return (lambda qv: db.vector_search(uid, sid, did, qv)) if db.IS_PG else None


class ChatIn(BaseModel):
    subject_id: int
    message: str = Field(min_length=1, max_length=2000)
    mode: str = "explain"
    document_id: int | None = None


@app.get("/api/chat/{sid}")
def chat_history(sid: int, u=Depends(user)):
    owned("subjects", sid, u["id"])
    r = db.rows("SELECT role, content, sources FROM messages WHERE user_id=? AND subject_id=? ORDER BY id", u["id"], sid)
    return [{**m, "sources": json.loads(m["sources"] or "[]")} for m in r]


@app.delete("/api/chat/{sid}")
def chat_clear(sid: int, u=Depends(user)):
    owned("subjects", sid, u["id"])
    db.run("DELETE FROM messages WHERE user_id=? AND subject_id=?", u["id"], sid)
    return {"ok": True}


@app.post("/api/chat")
def chat(b: ChatIn, u=Depends(user)):
    limit(u["id"], "ai")
    owned("subjects", b.subject_id, u["id"])
    if b.document_id:
        owned("documents", b.document_id, u["id"])
    hist = [{"role": m["role"], "content": m["content"]} for m in reversed(db.rows(
        "SELECT role, content FROM messages WHERE user_id=? AND subject_id=? ORDER BY id DESC LIMIT 8", u["id"], b.subject_id))]
    prev = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
    chunks = _chunks(u["id"], b.subject_id, b.document_id)
    hits = retrieval.search(chunks, b.message, k=5, context=prev, dense=_dense(u["id"], b.subject_id, b.document_id))
    if not hits and retrieval.is_generic(b.message):  # "summarise this" / "quiz me": no keywords, so sample the material
        hits = retrieval.overview(chunks, 5)
    names = {d["id"]: d["filename"] for d in db.rows("SELECT id, filename FROM documents WHERE user_id=?", u["id"])}
    try:
        answer = ai.tutor("socratic" if b.mode == "socratic" else "explain", b.message, hits, hist, names)
    except Exception as e:
        raise HTTPException(502, f"AI service error: {str(e)[:160]}")
    sources = [{"n": i, "doc": names[h["document_id"]], "page": h["page"], "snippet": h["text"][:240]} for i, h in enumerate(hits, 1)]
    db.run("INSERT INTO messages(user_id, subject_id, role, content) VALUES(?,?,?,?)", u["id"], b.subject_id, "user", b.message)
    db.run("INSERT INTO messages(user_id, subject_id, role, content, sources) VALUES(?,?,?,?,?)",
           u["id"], b.subject_id, "assistant", answer, json.dumps(sources))
    return {"answer": answer, "sources": sources}


# ---------- quiz ----------
class QuizReq(BaseModel):
    subject_id: int
    n: int = Field(default=10, ge=1, le=30)
    topic: str | None = None


class AnswerIn(BaseModel):
    question_id: int
    chosen: int = Field(ge=0, le=3)


class GenReq(BaseModel):
    subject_id: int
    topic: str | None = None
    n: int = Field(default=8, ge=1, le=15)
    level: str = "mixed"


@app.post("/api/quiz/next")
def quiz_next(b: QuizReq, u=Depends(user)):
    owned("subjects", b.subject_id, u["id"])
    qs = learning.pick_questions(u["id"], b.subject_id, b.n, b.topic)
    return {"questions": [{**q, "options": json.loads(q["options"])} for q in qs]}


@app.post("/api/quiz/answer")
def quiz_answer(b: AnswerIn, u=Depends(user)):
    q = owned("questions", b.question_id, u["id"])
    ok = int(b.chosen == q["answer"])
    db.run("INSERT INTO attempts(user_id, question_id, subject_id, topic, correct, ts) VALUES(?,?,?,?,?,?)",
           u["id"], q["id"], q["subject_id"], q["topic"], ok, learning.now())
    return {"correct": bool(ok), "answer": q["answer"], "explanation": q["explanation"]}


@app.post("/api/quiz/generate")
def quiz_generate(b: GenReq, u=Depends(user)):
    """Targeted practice: retrieve the passages for a (weak) topic and write fresh questions from them."""
    limit(u["id"], "ai", n=10)
    owned("subjects", b.subject_id, u["id"])
    chunks = _chunks(u["id"], b.subject_id)
    if not chunks:
        raise HTTPException(400, "Upload and process a document first")
    hits = (retrieval.search(chunks, b.topic, k=8, dense=_dense(u["id"], b.subject_id)) if b.topic
            else random.sample(chunks, min(8, len(chunks))))
    if not hits:
        hits = random.sample(chunks, min(6, len(chunks)))
    topics = [b.topic] if b.topic else sorted({t["topic"] for t in db.rows("SELECT topic FROM questions WHERE user_id=? AND subject_id=?", u["id"], b.subject_id)}) or ["General"]
    try:
        items = ai.study_items("\n\n".join(h["text"] for h in hits), topics, n_q=b.n, n_c=4 if b.topic else 0, level=b.level)
    except Exception as e:
        raise HTTPException(502, f"AI service error: {str(e)[:160]}")
    ingest.save_items(u["id"], b.subject_id, hits[0]["document_id"], items)
    return {"questions": len(items["questions"]), "flashcards": len(items["flashcards"])}


# ---------- flashcards ----------
class ReviewIn(BaseModel):
    grade: str


@app.get("/api/flashcards/due")
def cards_due(subject_id: int, u=Depends(user)):
    owned("subjects", subject_id, u["id"])
    return db.rows("SELECT id, topic, front, back FROM flashcards WHERE user_id=? AND subject_id=? AND due<=? ORDER BY due, id LIMIT 30",
                   u["id"], subject_id, learning.today())


@app.post("/api/flashcards/{cid}/review")
def card_review(cid: int, b: ReviewIn, u=Depends(user)):
    if b.grade not in learning.GRADES:
        raise HTTPException(400, "Unknown grade")
    c = owned("flashcards", cid, u["id"])
    n = learning.sm2(c, b.grade)
    db.run("UPDATE flashcards SET ease=?, reps=?, interval=?, due=? WHERE id=?", n["ease"], n["reps"], n["interval"], n["due"], cid)
    db.run("INSERT INTO reviews(user_id, card_id, grade, ts) VALUES(?,?,?,?)", u["id"], cid, b.grade, learning.now())
    return n


# ---------- study planner ----------
class PlanIn(BaseModel):
    subject_id: int
    exam_date: str
    daily_minutes: int = Field(default=90, ge=30, le=600)
    start_time: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@app.post("/api/plans")
def plan_create(b: PlanIn, u=Depends(user)):
    owned("subjects", b.subject_id, u["id"])
    try:
        exam = date.fromisoformat(b.exam_date)
    except ValueError:
        raise HTTPException(400, "Enter a valid exam date")
    if not date.today() < exam <= date.today() + timedelta(days=365):
        raise HTTPException(400, "Exam date must be between tomorrow and one year from now")
    planner.create(u["id"], b.subject_id, exam, b.daily_minutes, b.start_time)
    return planner.view(u["id"], b.subject_id)


@app.get("/api/plans/{sid}")
def plan_get(sid: int, u=Depends(user)):
    owned("subjects", sid, u["id"])
    return planner.view(u["id"], sid)  # null when no plan exists


@app.delete("/api/plans/{sid}")
def plan_delete(sid: int, u=Depends(user)):
    owned("subjects", sid, u["id"])
    db.run("DELETE FROM plans WHERE user_id=? AND subject_id=?", u["id"], sid)
    return {"ok": True}


@app.post("/api/plans/tasks/{tid}/toggle")
def plan_toggle(tid: int, u=Depends(user)):
    t = owned("plan_tasks", tid, u["id"])
    db.run("UPDATE plan_tasks SET done=? WHERE id=?", 1 - t["done"], tid)
    plan = db.one("SELECT * FROM plans WHERE id=?", t["plan_id"])
    tomorrow = date.today() + timedelta(days=1)
    if tomorrow < date.fromisoformat(plan["exam_date"]):
        planner.replan(plan, tomorrow)  # today's list stays put; the rest adapts to the new progress
    return {"done": 1 - t["done"]}


# ---------- dashboard + static ----------
@app.get("/api/dashboard")
def dashboard(u=Depends(user)):
    return learning.dashboard(u["id"])


FRONT = os.path.join(ROOT, "frontend")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONT, "index.html"))


app.mount("/static", StaticFiles(directory=FRONT), name="static")
