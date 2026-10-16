"""Fixed-window rate limiting with the counters in the database, so a limit holds across every process, replica
and restart (an in-memory limiter would let an attacker multiply their allowance by the number of web containers).
One atomic upsert per request - no read-modify-write race, on SQLite and Postgres alike."""
import math
import time

import db

_HIT = ("INSERT INTO rate_limits(k, window_start, hits) VALUES(?, ?, 1) "
        "ON CONFLICT (k) DO UPDATE SET "
        "hits = CASE WHEN rate_limits.window_start <= ? THEN 1 ELSE rate_limits.hits + 1 END, "
        "window_start = CASE WHEN rate_limits.window_start <= ? THEN ? ELSE rate_limits.window_start END "
        "RETURNING hits, window_start")


def hit(key: str, n: int, per: int) -> int:
    """Count one request against `key` (at most `n` per `per` seconds).
    Returns 0 if allowed, otherwise the number of seconds until the window resets."""
    now = time.time()
    row = db.write_returning(_HIT, key, now, now - per, now - per, now)[0]
    return 0 if row["hits"] <= n else max(1, math.ceil(row["window_start"] + per - now))


def prune(max_age: int = 86400):
    """Drop counters whose window ended long ago (run periodically by a job worker)."""
    db.run("DELETE FROM rate_limits WHERE window_start < ?", time.time() - max_age)
