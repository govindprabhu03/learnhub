# 🎓 LearnHub — AI-powered adaptive learning platform

Upload a PDF → it is processed → ask a document-grounded tutor → take adaptive quizzes → revise with spaced repetition → get told what to study next.

## Run
```
pip install -r requirements.txt
copy .env.example .env      # add ANTHROPIC_API_KEY (optional; offline mode works without it)
run.bat                     # http://localhost:8100
python tests/smoke.py       # end-to-end API test, incl. cross-user authorization (SQLite)
set TEST_PG=1 && python tests/smoke.py   # the same suite on a throwaway Postgres + pgvector
```

### Database
SQLite by default. For Postgres + pgvector set `DATABASE_URL` in `.env` (a real server, or `embedded` for a zero-install local one).
The schema is owned by **Alembic** and upgraded automatically at startup; databases created before Alembic are stamped in place.
```
alembic upgrade head                       # manual run (same URL rules as the app)
alembic revision -m "add thing"            # new migration (edit it, then upgrade)
```
The vector column is `vector(384)` (bge-small). A model with another dimension needs a new migration.

### Run with Docker (web + worker + Postgres/pgvector)
```
copy .env.example .env      # set SECRET_KEY (any long random string) and optionally ANTHROPIC_API_KEY, SMTP_*, SENTRY_DSN
docker compose up --build   # http://localhost:8100
docker compose up --scale worker=3    # more document-processing capacity
```
The image bakes in the embedding model, runs as a non-root user, and has a health check on `/api/health`. The web container only
enqueues work; `worker` containers process documents. **The Docker files and the GitHub Actions workflow were written and
syntax-checked but not run** (no Docker on the dev machine) - the first CI run is their real test.

### Quality gates
`ruff check .` (lint) · `python tests/smoke.py` (API, security, jobs; SQLite or `TEST_PG=1` / `TEST_DATABASE_URL=...` for Postgres) ·
`python tests/worker_process.py` (web + separate worker process). `.github/workflows/ci.yml` runs all of them plus a Docker build.

### Accounts: email verification, password reset, Google sign-in
- **Email** (verify + reset): set `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` in `.env` (Gmail: `smtp.gmail.com`, port 587, an app password). With no SMTP the emails are printed to the server console, so everything works in development. Once SMTP is set, new accounts must verify before logging in (`REQUIRE_VERIFIED_EMAIL` overrides).
- **Google**: in [Google Cloud Console](https://console.cloud.google.com/apis/credentials) create an *OAuth client ID* of type *Web application*, add the authorised redirect URI `http://localhost:8100/api/auth/google/callback` (use your real `APP_URL` in production), then put the client ID and secret in `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and restart. The "Continue with Google" button appears automatically.

## How it works
| Concern | Implementation |
|---|---|
| PDF → text | `pypdf`, de-hyphenation, soft-wrap joining, page-number stripping (`pdfproc.py`) |
| Chunking | sentence-aware ~900-char chunks, never cross a page, 1-sentence overlap |
| Retrieval (RAG) | **Hybrid**: BM25 (exact terms/acronyms) + local dense embeddings (`BAAI/bge-small-en-v1.5` via ONNX, no API key, ~70 MB, cached in `data/models`) fused with Reciprocal Rank Fusion (`retrieval.py`, `embeddings.py`). Vectors are stored per chunk: **pgvector `vector(384)` + `<=>` cosine search inside Postgres** (always filtered by owner/subject), or a numpy scan on SQLite; a cosine floor (0.5) keeps off-topic questions from getting context. Follow-ups ("give me an example") borrow the previous turn only when they look like follow-ups; "summarise this"/"quiz me" sample the whole document. Old documents are embedded by a startup backfill; no model → falls back to BM25 |
| Hallucination control | tutor prompt allows only numbered excerpts, requires `[n]` citations, says "not in your material" when retrieval is empty |
| Tutor modes | Explain · Socratic (guides with one question at a time) · follow-ups reuse the previous turn for retrieval |
| Adaptive quiz | weighted sampling (Efraimidis–Spirakis): weak topics + unseen questions up, just-mastered down; mastery = last 20 attempts per topic |
| Weak-topic loop | weak topic → recommended steps → "Practice" retrieves that topic's passages and generates fresh MCQs |
| Flashcards | SM-2 spaced repetition (Again/Hard/Good/Easy → ease, interval, due date) |
| Study planner | exam date + daily time → day-by-day schedule (`planner.py`): weak topics first, spaced repeat passes, revision-only last day, daily flashcards, spare time → extra practice on weak topics. Re-plans from current mastery when you tick a session and once a day (missed sessions roll over); warns when the work can't fit |
| Analytics | streak, daily goal, weekly activity, 4-week accuracy trend, per-topic mastery |
| Security | scrypt password hashes, HMAC-signed expiring tokens, **every query scoped by `user_id`**, PDF magic-byte + size checks, per-user AI rate limit |
| Accounts | email verification and password reset via purpose-tagged signed tokens (a verify token can't reset a password or act as a session; reset links are single-use and expire in 1 h; changing a password invalidates every existing session); no user enumeration on forgot/resend; login attempts rate-limited per account. Google sign-in is a server-side authorization-code flow: signed `state` + a cookie nonce (login-CSRF), email must be verified by Google, and **pre-hijack defence** (if someone pre-registered your address with their own password, signing in with Google revokes it) |
| OCR | scanned/image-only PDFs are read automatically, page by page: pages with no text layer are rendered (pypdfium2) and recognised locally (RapidOCR, ONNX, no API key; ~1.5 s/page on CPU). Digital pages never touch OCR, mixed documents are combined, lines the engine is unsure about are dropped, and the Materials tab shows live progress plus a "read with OCR, some words may be misread" caveat. Capped at 150 pages per document (`OCR_MAX_PAGES`); long scans keep their job alive with a heartbeat. Printed text only - handwriting and multi-column layouts are weak spots |
| Background jobs | uploads return immediately and enqueue a job in a **durable queue stored in the app database** (`jobs.py`): atomic claim (`UPDATE … RETURNING`, plus `FOR UPDATE SKIP LOCKED` on Postgres) so a job never runs twice at once; retries with backoff; permanent errors (scanned/corrupt PDF) fail fast with a readable reason; jobs survive restarts and a dead worker's job is recovered; ingestion is idempotent. Workers run in-process (default) or as separate `worker.py` processes. Redis/RQ was skipped on purpose: no extra server, and RQ does not run on Windows |
| ANN index | exact pgvector search by default; on pgvector >= 0.8 an HNSW index (`vector_cosine_ops`) is built at startup, concurrently and idempotently, and queries set `hnsw.iterative_scan = relaxed_order` so the owner/subject filter cannot starve the result list. Earlier versions would silently under-return, so they never get the index |
| Rate limiting | login attempts (per account), password-reset/verification emails and the AI endpoints (per user) are limited with a fixed-window counter stored in the database (`ratelimit.py`): one atomic upsert per request, so the limit is shared by every process/replica and survives restarts; blocked requests get `429` + `Retry-After` |
| Observability | `SENTRY_DSN` turns on Sentry (request errors + finally-failed jobs, no PII); `/api/health` reports database, workers and queue depth |
| Offline mode | no API key → extractive summary, cloze quizzes, excerpt answers (app stays fully usable) |


## Roadmap
- [x] Study planner (exam date → daily schedule that re-plans as you progress)
- [x] Embeddings (hybrid dense + BM25)
- [x] PostgreSQL + pgvector, Alembic migrations (SQLite remains the zero-config default)
- [x] HNSW index for large collections, version-gated: created automatically (CONCURRENTLY) only on pgvector >= 0.8, which adds `hnsw.iterative_scan` so per-user filtered searches don't under-return; older servers keep exact search. The gate, index SQL, repair of interrupted builds, and the fallback were tested on the bundled pgvector 0.6.2; **the >= 0.8 path (real iterative scans) has only been exercised by CI's `pgvector/pgvector:pg16` job, which has not run yet**
- [x] Google OAuth, email verification, password reset (Google flow tested with stubbed Google calls; needs your OAuth client to try live)
- [x] Docker + compose, GitHub Actions CI, Sentry, durable job queue + separate worker (Redis intentionally not used)
- [x] Shared rate limiter (counters in the database; holds across processes, replicas and restarts)
- [x] OCR for scanned PDFs
