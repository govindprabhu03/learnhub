"""Local sentence embeddings (ONNX via fastembed, no API key, CPU only). Everything degrades gracefully:
if the package or model is unavailable, `embed` returns None and retrieval falls back to BM25 alone."""
import importlib.util
import threading

import numpy as np

import db
from config import EMBED_MODEL, MODEL_DIR

_lock = threading.Lock()
_model = None
_failed = False


def available() -> bool:
    return not _failed and importlib.util.find_spec("fastembed") is not None


def _load():
    global _model, _failed
    with _lock:
        if _model is None and not _failed:
            try:
                from fastembed import TextEmbedding
                _model = TextEmbedding(EMBED_MODEL, cache_dir=MODEL_DIR)  # first run downloads ~70 MB, then cached
            except Exception:
                _failed = True
    return _model


def embed(texts, query=False):
    """Unit-length float32 vectors, shape (n, dim), or None. `query=True` adds the model's retrieval prefix."""
    if not texts or not available():
        return None
    m = _load()
    if m is None:
        return None
    try:
        with _lock:  # one ONNX session shared by all request threads
            vecs = list(m.query_embed(texts) if query else m.embed(texts, batch_size=32))
    except Exception:
        return None
    v = np.asarray(vecs, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def warm_and_backfill():
    """Background thread at startup: load the model, then embed chunks stored before embeddings existed
    (or by a different model) so old documents get semantic search too."""
    if _load() is None:
        return
    while True:
        rows = db.rows("SELECT id, text FROM chunks WHERE emb IS NULL OR emb_model IS NULL OR emb_model <> ? LIMIT 128", EMBED_MODEL)
        vecs = embed([r["text"] for r in rows]) if rows else None
        if vecs is None:
            return
        db.many("UPDATE chunks SET emb=?, emb_model=? WHERE id=?", [(db.vec(v), EMBED_MODEL, r["id"]) for v, r in zip(vecs, rows)])
