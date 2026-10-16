"""Hybrid retrieval over a user's chunks: BM25 (exact terms, rare words, acronyms) fused with dense
embedding similarity (meaning, paraphrase) by Reciprocal Rank Fusion. With no embeddings it is plain BM25."""
import math
import re
from collections import Counter

import numpy as np

import embeddings
from config import EMBED_MODEL

STOP = set("a an the of to in on for and or is are was were be been it its this that these those with as by at from "
           "what which who how why when explain give me about tell can you do does did using use between difference "
           "define describe simple terms example examples please".split())
_TOK = re.compile(r"[a-z0-9]+")
RRF_K = 60       # standard RRF damping constant
MIN_COS = 0.5    # bge-small: related passages ~0.6-0.85, unrelated ~0.4-0.46 -> below this is "not in the material"
POOL = 20        # candidates taken from each ranker before fusion


def tokens(s: str):
    return [t for t in _TOK.findall(s.lower()) if t not in STOP and len(t) > 1]


def _bm25_rank(chunks, query, k1=1.5, b=0.75):
    q = set(tokens(query))
    if not q or not chunks:
        return []
    docs = [tokens(c["text"]) for c in chunks]
    avg = sum(len(d) for d in docs) / len(docs) or 1
    df = Counter(t for d in docs for t in set(d) if t in q)
    n, scored = len(docs), []
    for i, d in enumerate(docs):
        tf, s = Counter(d), 0.0
        for t in q:
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        if s > 0:
            scored.append((s, i))
    return [i for _, i in sorted(scored, reverse=True)]


_FOLLOW = re.compile(r"\b(it|its|this|that|these|those|them|they|above|previous|earlier|last|more|another|again|"
                     r"examples?|answers?|questions?\s*\d+|ask me|quiz me)\b", re.I)
_GENERIC = re.compile(r"summar|overview|key (ideas|points|concepts)|main (ideas|points)|ask me|quiz me|test me", re.I)


def is_followup(query):
    """A message with no topic of its own ("give me an example", "explain it more") refers to the previous turn."""
    return len(tokens(query)) <= 1 or bool(_FOLLOW.search(query))


def is_generic(query):
    return bool(_GENERIC.search(query))


def overview(chunks, k=5):
    """Evenly spaced chunks, for requests about the whole material that have no keywords to retrieve on."""
    step = len(chunks) / max(1, min(k, len(chunks)))
    return [chunks[int(i * step)] for i in range(min(k, len(chunks)))]


def _scan(chunks):
    """In-memory exact cosine scan (SQLite backend, tests). Same contract as db.vector_search: qv -> [(id, sim)]."""
    idx = [i for i, c in enumerate(chunks) if c.get("emb") and c.get("emb_model") == EMBED_MODEL]
    if not idx:
        return lambda qv: []
    mat = np.stack([np.frombuffer(chunks[i]["emb"], dtype=np.float32) for i in idx])

    def fn(qv):
        sims = mat @ qv
        return [(chunks[idx[j]]["id"], float(sims[j])) for j in np.argsort(-sims)[:POOL]]
    return fn


def _dense_rank(dense, q):
    qv = embeddings.embed([q], query=True)
    return [] if qv is None else [cid for cid, sim in dense(qv[0]) if sim >= MIN_COS]


def search(chunks, query, k=5, context="", dense=None):
    """Top-k chunks. `context` (the previous user turn) is only used when `query` looks like a follow-up,
    so switching topics mid-chat does not drag the old topic along. `dense` is a vector-search callable
    (qv -> [(chunk_id, similarity)]): pgvector in Postgres mode, otherwise an in-memory scan of `chunks`."""
    dense = dense or _scan(chunks)
    follow = bool(context) and is_followup(query)
    sem = _dense_rank(dense, query)
    if not sem and follow:
        sem = _dense_rank(dense, f"{query} {context}")
    lexical = [chunks[i]["id"] for i in _bm25_rank(chunks, f"{query} {context}" if follow else query)[:POOL]]
    by_id, score = {c["id"]: c for c in chunks}, {}
    for ranked in (lexical, sem[:POOL]):
        for r, cid in enumerate(ranked):
            if cid in by_id:
                score[cid] = score.get(cid, 0.0) + 1 / (RRF_K + r + 1)
    return [by_id[i] for i in sorted(score, key=score.get, reverse=True)[:k]]
