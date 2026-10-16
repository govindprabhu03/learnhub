"""All LLM interaction. Uses Claude when ANTHROPIC_API_KEY is set; otherwise a small extractive
fallback keeps the whole app usable offline (clearly labelled in tutor replies)."""
import json
import os
import random
import re
from collections import Counter

import retrieval
from config import MODEL, TUTOR_MODEL

_client = None


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _llm(system, messages, max_tokens, model):
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    r = _client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=messages)
    return "".join(b.text for b in r.content if b.type == "text")


def _json(system, prompt, max_tokens=4000):
    msgs = [{"role": "user", "content": prompt}]
    for _ in range(2):
        txt = _llm(system + " Respond with a single valid JSON object and nothing else.", msgs, max_tokens, MODEL)
        try:
            return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        except ValueError:
            msgs = [{"role": "user", "content": prompt + "\n\nYour previous reply was not valid JSON. Return JSON only."}]
    raise ValueError("AI returned malformed JSON")


# ---------- offline fallback helpers ----------
def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if 40 < len(s.strip()) < 300]


def _terms(text, n=8):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", text) if w.lower() not in retrieval.STOP]
    return [w.title() for w, _ in Counter(w.lower() for w in words).most_common(n)]


# ---------- document analysis ----------
def analyze(text):
    if not enabled():
        sents = _sentences(text)
        terms = _terms(text)
        return {"summary": " ".join(sents[:4]) or text[:600], "concepts": terms,
                "important_questions": [f"Explain {t}." for t in terms[:5]]}
    d = _json("You are an expert study assistant who turns course material into study aids.",
              f"Study material:\n\"\"\"\n{text}\n\"\"\"\n\nReturn JSON: {{\"summary\": \"150-250 word clear summary\", "
              "\"concepts\": [\"5-12 short topic names covering the material\"], "
              "\"important_questions\": [\"5 likely exam questions\"]}")
    return {"summary": str(d.get("summary", "")), "concepts": [str(c) for c in d.get("concepts", [])][:12],
            "important_questions": [str(q) for q in d.get("important_questions", [])][:8]}


# ---------- quiz + flashcards ----------
def study_items(context, concepts, n_q=10, n_c=10, level="mixed"):
    if not enabled():
        return _offline_items(context, concepts, n_q, n_c)
    d = _json("You are an expert exam-question writer. Use ONLY the supplied material; never invent facts.",
              f"Material:\n\"\"\"\n{context}\n\"\"\"\n\nCreate {n_q} multiple-choice questions (difficulty: {level}) and {n_c} flashcards. "
              f"Every item's \"topic\" must be one of: {json.dumps(concepts)}. "
              "Each MCQ has exactly 4 options and exactly one correct answer; distractors must be plausible. "
              "Return JSON: {\"questions\": [{\"topic\": str, \"question\": str, \"options\": [4 strings], "
              "\"answer\": 0-3, \"explanation\": str, \"difficulty\": \"easy|medium|hard\"}], "
              "\"flashcards\": [{\"topic\": str, \"front\": str, \"back\": str}]}")
    qs = []
    for q in d.get("questions", []):
        try:
            opts = [str(o) for o in q["options"]]
            a = int(q["answer"])
            if len(opts) == 4 and 0 <= a < 4:
                qs.append({"topic": str(q.get("topic") or concepts[0]), "question": str(q["question"]), "options": opts,
                           "answer": a, "explanation": str(q.get("explanation", "")),
                           "difficulty": q.get("difficulty") if q.get("difficulty") in ("easy", "medium", "hard") else "medium"})
        except (KeyError, ValueError, TypeError):
            continue
    cards = [{"topic": str(c.get("topic") or concepts[0]), "front": str(c["front"]), "back": str(c["back"])}
             for c in d.get("flashcards", []) if c.get("front") and c.get("back")]
    return {"questions": qs, "flashcards": cards}


def _offline_items(context, concepts, n_q, n_c):
    sents = _sentences(context)
    qs, cards, used = [], [], set()
    for s in sents:
        t = next((c for c in concepts if c.lower() in s.lower()), None)
        if not t or s in used:
            continue
        used.add(s)
        cloze = re.sub(re.escape(t), "_____", s, count=1, flags=re.I)
        others = [c for c in concepts if c != t]
        if len(others) >= 3 and len(qs) < n_q:
            qs.append({"topic": t, "question": "Fill in the blank: " + cloze, "options": [t] + random.sample(others, 3),
                       "answer": 0, "explanation": s, "difficulty": "medium"})
        if len(cards) < n_c:
            cards.append({"topic": t, "front": f"What does the material say about {t}?", "back": s})
    return {"questions": qs, "flashcards": cards}


# ---------- tutor ----------
_EXPLAIN = ("You are LearnHub's AI tutor. Answer the student using ONLY the numbered excerpts from their own study material. "
            "Cite sources inline like [1], [2]. If the excerpts do not contain the answer, say so plainly instead of guessing. "
            "Use simple language and a short example where it helps. If asked to quiz the student, write the questions from "
            "the excerpts and wait for their answers before revealing solutions.")
_SOCRATIC = ("You are LearnHub's Socratic AI tutor. Use ONLY the numbered excerpts from the student's material. "
             "Do NOT give the full answer immediately: ask ONE short guiding question that moves the student toward it. "
             "When they reply, check their answer against the excerpts, confirm what is right, gently correct what is wrong "
             "(cite [n]), then ask the next question. Reveal the full answer only if they give up or ask directly.")


def tutor(mode, message, hits, history, names):
    if not hits:
        return ("I couldn't find anything about that in your uploaded material. Try rephrasing, or upload the "
                "relevant notes for this subject.")
    if not enabled():
        top = hits[0]
        return (f"**Offline mode** (set `ANTHROPIC_API_KEY` for real AI answers). Most relevant passage "
                f"[1] from {names[top['document_id']]}, p.{top['page']}:\n\n{top['text']}")
    ctx = "\n\n".join(f"[{i}] ({names[h['document_id']]}, p.{h['page']})\n{h['text']}" for i, h in enumerate(hits, 1))
    msgs = [m for m in history]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    msgs.append({"role": "user", "content": f"Excerpts:\n{ctx}\n\nStudent: {message}"})
    return _llm(_SOCRATIC if mode == "socratic" else _EXPLAIN, msgs, 1200, TUTOR_MODEL)
