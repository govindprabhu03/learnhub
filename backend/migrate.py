"""Runs Alembic migrations programmatically at startup (same as `alembic upgrade head` from the project root)."""
import logging
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from config import DATABASE_URL, IS_PG, ROOT, SA_URL

log = logging.getLogger("migrate")

def _upgrade(cfg):
    eng = create_engine(SA_URL)
    tables = set(inspect(eng).get_table_names())
    eng.dispose()
    if "users" in tables and "alembic_version" not in tables:  # database created before Alembic was introduced
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")


ANN_INDEX = "ix_chunks_emb_hnsw"


def _pgvector_version(conn):
    return tuple(int(x) for x in conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0].split(".")[:3])


def ensure_ann_index(conn, min_version=(0, 8, 0)):
    """Create the HNSW index on chunks.emb, but only where it is safe. An approximate index plus a WHERE filter
    (our per-user/per-subject search) silently returns too few rows on pgvector < 0.8, which adds
    `hnsw.iterative_scan` to keep scanning until enough rows pass the filter. On older servers search stays exact.
    Idempotent, built CONCURRENTLY (no write lock), and repairs an index left invalid by an interrupted build.
    `conn` must be an autocommit psycopg connection. Returns a short status string."""
    ver = _pgvector_version(conn)
    if ver < min_version:
        return f"skipped (pgvector {'.'.join(map(str, ver))} < {'.'.join(map(str, min_version))}; search stays exact)"
    row = conn.execute("SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid WHERE c.relname = %s", (ANN_INDEX,)).fetchone()
    if row and row[0]:
        return "exists"
    if row:
        conn.execute(f"DROP INDEX IF EXISTS {ANN_INDEX}")
    conn.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {ANN_INDEX} ON chunks USING hnsw (emb vector_cosine_ops)")
    return "recreated" if row else "created"


def upgrade():
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    if IS_PG:  # web + worker (or several replicas) may start together: let exactly one migrate at a time
        import psycopg
        with psycopg.connect(DATABASE_URL, autocommit=True) as lock:
            lock.execute("SELECT pg_advisory_lock(727301)")
            _upgrade(cfg)  # the lock is released when this connection closes
            try:  # also picks the index up later if pgvector is upgraded; never blocks startup
                log.info("ANN index: %s", ensure_ann_index(lock))
            except Exception:
                log.exception("could not create the ANN index; search stays exact")
    else:
        _upgrade(cfg)
