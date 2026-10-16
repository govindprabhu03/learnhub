"""Durable job queue stored in the application database (SQLite or Postgres) - no Redis needed.

* enqueue() inserts a row; workers claim rows with one atomic UPDATE ... RETURNING
  (Postgres adds FOR UPDATE SKIP LOCKED), so a job is never run twice concurrently.
* Failures are retried with backoff; `Permanent` errors and exhausted retries mark the job failed and call
  the handler's `on_fail` hook (e.g. mark the document failed).
* A job whose worker died stays `running` until it goes stale, then recover() re-queues it. Jobs survive restarts.
* Workers run inside the web process (WORKER_MODE=inline) or as separate processes (python backend/worker.py).
  Swapping in Redis/RQ later only means re-implementing enqueue/claim."""
import json
import logging
import threading
import time

import db
import observability
import ratelimit
from config import IS_PG

log = logging.getLogger("jobs")
MAX_ATTEMPTS = 3
BACKOFF = 30       # seconds, multiplied by the attempt number
STALE_AFTER = 300  # a 'running' job untouched for this long is presumed dead
HANDLERS = {}


class Permanent(Exception):
    """Raise from a handler when retrying cannot help (bad input). The message is shown to the user."""


def register(kind, fn, on_fail=None):
    HANDLERS[kind] = (fn, on_fail)


def enqueue(kind, **payload):
    return db.run("INSERT INTO jobs(kind, payload, run_after) VALUES(?,?,?)", kind, json.dumps(payload), time.time())


_CLAIM = ("UPDATE jobs SET status='running', locked_at=?, attempts=attempts+1 WHERE id=("
          "SELECT id FROM jobs WHERE status='queued' AND run_after<=? ORDER BY id LIMIT 1{lock}) "
          "RETURNING id, kind, payload, attempts")


def claim():
    now = time.time()
    rows = db.write_returning(_CLAIM.format(lock=" FOR UPDATE SKIP LOCKED" if IS_PG else ""), now, now)
    return rows[0] if rows else None


_ctx = threading.local()


def touch():
    """Long handlers call this to show they are alive; otherwise a job running past STALE_AFTER would be
    presumed dead and handed to another worker."""
    if getattr(_ctx, "job_id", None):
        db.run("UPDATE jobs SET locked_at=? WHERE id=?", time.time(), _ctx.job_id)


def _fail(job_id, kind, payload, err):
    db.run("UPDATE jobs SET status='failed', error=? WHERE id=?", err, job_id)
    on_fail = HANDLERS.get(kind, (None, None))[1]
    if on_fail:
        try:
            on_fail(payload, err)
        except Exception:
            log.exception("on_fail hook for job %s failed", job_id)


def run_one():
    """Claim and run a single job. Returns False when the queue had nothing runnable."""
    job = claim()
    if not job:
        return False
    payload = json.loads(job["payload"])
    fn = HANDLERS.get(job["kind"], (None, None))[0]
    _ctx.job_id = job["id"]
    try:
        if fn is None:
            raise Permanent(f"no handler registered for job kind '{job['kind']}'")
        fn(**payload)
        db.run("UPDATE jobs SET status='done', error=NULL WHERE id=?", job["id"])
    except Exception as e:
        err = (str(e) if isinstance(e, Permanent) else f"{type(e).__name__}: {e}")[:500]
        if not isinstance(e, Permanent) and job["attempts"] < MAX_ATTEMPTS:
            log.warning("job %s (%s) failed on attempt %s, retrying: %s", job["id"], job["kind"], job["attempts"], err)
            db.run("UPDATE jobs SET status='queued', error=?, run_after=? WHERE id=?",
                   err, time.time() + BACKOFF * job["attempts"], job["id"])
        else:
            log.error("job %s (%s) failed for good: %s", job["id"], job["kind"], err)
            _fail(job["id"], job["kind"], payload, err)
            observability.capture(e)
    finally:
        _ctx.job_id = None
    return True


def recover():
    """Handle jobs whose worker died: re-queue them, or fail them if they already used up their attempts."""
    cutoff = time.time() - STALE_AFTER
    for j in db.rows("SELECT id, kind, payload FROM jobs WHERE status='running' AND locked_at<? AND attempts>=?", cutoff, MAX_ATTEMPTS):
        _fail(j["id"], j["kind"], json.loads(j["payload"]), "The worker stopped before finishing this job")
    db.run("UPDATE jobs SET status='queued', run_after=? WHERE status='running' AND locked_at<?", time.time(), cutoff)


def stats():
    return {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}


def worker_loop(stop, idle=1.0, housekeeping=False):
    last = 0.0
    while not stop.is_set():
        if housekeeping and time.time() - last > STALE_AFTER / 2:
            last = time.time()
            try:
                recover()
                ratelimit.prune()
            except Exception:
                log.exception("housekeeping failed")
        try:
            busy = run_one()
        except Exception:  # e.g. the database was briefly unreachable
            log.exception("worker error")
            busy = False
        if not busy:
            stop.wait(idle)


def start_workers(n):
    """Start n daemon worker threads. Returns (stop_event, threads)."""
    stop = threading.Event()
    threads = [threading.Thread(target=worker_loop, args=(stop, 1.0, i == 0), daemon=True, name=f"job-worker-{i}") for i in range(n)]
    for t in threads:
        t.start()
    return stop, threads
