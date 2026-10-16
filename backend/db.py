"""Thin data layer over SQLite (default) or Postgres + pgvector (DATABASE_URL). Callers write SQL with `?`
placeholders and get dicts back; the schema itself is owned by Alembic (see migrate.py)."""
import sqlite3

from config import DATABASE_URL, DB_PATH, EMBED_MODEL, IS_PG

_pool = None
_ITER = None  # does this Postgres support hnsw.iterative_scan (pgvector >= 0.8)? None = not probed yet


def init():
    """Open the connection pool. Run `migrate.upgrade()` first - the schema must already exist."""
    global _pool
    if IS_PG:
        from pgvector.psycopg import register_vector
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        # check=...: validate a connection before handing it out, so serverless Postgres that suspends when idle
        # (Neon, Supabase) cannot leave us with dead pooled connections
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row},
                               configure=register_vector, check=ConnectionPool.check_connection, open=True)
        _pool.wait()
    else:
        c = _sqlite()
        c.execute("PRAGMA journal_mode=WAL")
        c.close()


def _sqlite():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _q(sql):
    return sql.replace("?", "%s")  # no SQL text in this app contains a literal '?'


def rows(sql, *args):
    if IS_PG:
        with _pool.connection() as c:
            return c.execute(_q(sql), args).fetchall()
    c = _sqlite()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def one(sql, *args):
    r = rows(sql, *args)
    return r[0] if r else None


def run(sql, *args):
    """Execute a write. For INSERTs returns the new row id."""
    if IS_PG:
        ins = sql.lstrip().upper().startswith("INSERT")
        with _pool.connection() as c:
            cur = c.execute(_q(sql) + (" RETURNING id" if ins else ""), args)
            return cur.fetchone()["id"] if ins else None
    c = _sqlite()
    try:
        with c:
            return c.execute(sql, args).lastrowid
    finally:
        c.close()


def write_returning(sql, *args):
    """Execute an UPDATE/DELETE ... RETURNING and return the affected rows as dicts (atomic job claiming)."""
    if IS_PG:
        with _pool.connection() as c:
            return c.execute(_q(sql), args).fetchall()
    c = _sqlite()
    try:
        with c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def many(sql, seq):
    if IS_PG:
        with _pool.connection() as c, c.cursor() as cur:
            cur.executemany(_q(sql), seq)
        return
    c = _sqlite()
    try:
        with c:
            c.executemany(sql, seq)
    finally:
        c.close()


def vec(v):
    """numpy float32 embedding -> value for chunks.emb (pgvector adapts ndarray; SQLite stores raw bytes)."""
    return v if IS_PG else v.tobytes()


def vector_search(uid, sid, did, qv, limit=20):
    """Postgres only: [(chunk_id, cosine_similarity)] best first, via pgvector's <=> operator. Always scoped to the
    owner and subject. Exact search over that filtered set (correct at any size; add an HNSW index and
    `SET hnsw.iterative_scan` on pgvector >= 0.8 if a single user ever holds hundreds of thousands of chunks)."""
    global _ITER
    sql = ("SELECT id, 1 - (emb <=> %s) AS sim FROM chunks WHERE user_id=%s AND subject_id=%s AND emb_model=%s "
           "AND emb IS NOT NULL" + (" AND document_id=%s" if did else "") + " ORDER BY emb <=> %s LIMIT %s")
    args = [qv, uid, sid, EMBED_MODEL, *([did] if did else []), qv, limit]
    with _pool.connection() as c, c.transaction():
        # With the HNSW index (pgvector >= 0.8) keep scanning until enough rows pass the owner/subject filter.
        # Servers without the setting are probed once (in a savepoint, so the failure cannot poison the query).
        if _ITER is None:
            import psycopg
            try:
                with c.transaction():
                    c.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                _ITER = True
            except psycopg.Error:
                _ITER = False
        elif _ITER:
            c.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = c.execute(sql, args).fetchall()
    return sorted(((r["id"], float(r["sim"])) for r in rows), key=lambda r: -r[1])  # relaxed_order may be slightly out of order
