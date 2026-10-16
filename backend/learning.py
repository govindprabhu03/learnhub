"""Adaptive-learning logic: topic mastery, weighted question selection, SM-2 spaced repetition,
streaks, analytics and study recommendations."""
import random
from datetime import date, datetime, timedelta

import db
from config import DAILY_GOAL

WEAK, STRONG, MIN_ATTEMPTS, WINDOW = 60, 80, 3, 20


def now():
    return datetime.now().isoformat(timespec="seconds")


def today():
    return date.today().isoformat()


def topic_stats(uid, sid=None):
    """Per-topic accuracy over the most recent WINDOW attempts (so old mistakes fade as you improve)."""
    extra, args = (" AND subject_id=?", (uid, sid)) if sid else ("", (uid,))
    r = db.rows(f"""SELECT subject_id, topic, COUNT(*) n, SUM(correct) c FROM (
        SELECT subject_id, topic, correct, ROW_NUMBER() OVER (PARTITION BY subject_id, topic ORDER BY id DESC) rn
        FROM attempts WHERE user_id=?{extra}) t WHERE rn<=? GROUP BY subject_id, topic""", *args, WINDOW)
    out = []
    for x in r:
        acc = round(100 * x["c"] / x["n"])
        status = "new" if x["n"] < MIN_ATTEMPTS else "weak" if acc < WEAK else "strong" if acc >= STRONG else "average"
        out.append({"subject_id": x["subject_id"], "topic": x["topic"], "attempts": x["n"], "accuracy": acc, "status": status})
    return sorted(out, key=lambda s: s["accuracy"])


def pick_questions(uid, sid, n, topic=None):
    """Weighted sampling: weak topics and unseen questions are favoured; just-mastered ones are damped."""
    qs = db.rows("SELECT id, topic, question, options, difficulty FROM questions WHERE user_id=? AND subject_id=?", uid, sid)
    if topic:
        qs = [q for q in qs if q["topic"] == topic]
    acc = {s["topic"]: s["accuracy"] for s in topic_stats(uid, sid)}
    last = {r["question_id"]: r["correct"] for r in
            db.rows("SELECT question_id, correct FROM attempts WHERE user_id=? AND subject_id=? ORDER BY id", uid, sid)}

    def weight(q):
        w = 1 + 3 * (100 - acc.get(q["topic"], 50)) / 100
        if q["id"] not in last:
            w += 1.5
        elif last[q["id"]]:
            w *= 0.3
        return w

    # Efraimidis-Spirakis weighted sampling without replacement
    return sorted(qs, key=lambda q: random.random() ** (1 / weight(q)), reverse=True)[:n]


GRADES = {"again": 1, "hard": 3, "good": 4, "easy": 5}


def sm2(card, grade):
    q = GRADES[grade]
    ease, reps, interval = card["ease"], card["reps"], card["interval"]
    if q < 3:
        reps, interval = 0, 0  # lapse: back into today's queue
    else:
        reps += 1
        interval = 1 if reps == 1 else 6 if reps == 2 else max(1, round(interval * ease))
    ease = max(1.3, ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    return {"ease": round(ease, 3), "reps": reps, "interval": interval,
            "due": (date.today() + timedelta(days=interval)).isoformat()}


def _activity(uid):
    r = db.rows("""SELECT substr(ts,1,10) d, COUNT(*) n FROM (
        SELECT ts FROM attempts WHERE user_id=? UNION ALL SELECT ts FROM reviews WHERE user_id=?) x GROUP BY d""", uid, uid)
    return {x["d"]: x["n"] for x in r}


def streak(days):
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def recommendations(uid, sid=None, limit=4):
    out = []
    for s in [s for s in topic_stats(uid, sid) if s["status"] == "weak"][:limit]:
        t = s["topic"]
        out.append({"topic": t, "subject_id": s["subject_id"], "accuracy": s["accuracy"], "steps": [
            {"label": f"Review “{t}” with your tutor", "action": "tutor"},
            {"label": f"Practice questions on “{t}”", "action": "practice"},
            {"label": "Revise flashcards", "action": "cards"},
            {"label": "Retake the adaptive quiz", "action": "quiz"}]})
    return out


def dashboard(uid):
    days = _activity(uid)
    attempts_today = db.one("SELECT COUNT(*) n FROM attempts WHERE user_id=? AND substr(ts,1,10)=?", uid, today())["n"]
    stats = topic_stats(uid)
    by_subject = {}
    for s in stats:
        by_subject.setdefault(s["subject_id"], []).append(s)
    subjects = []
    for sub in db.rows("SELECT id, name, emoji FROM subjects WHERE user_id=? ORDER BY id", uid):
        st = by_subject.get(sub["id"], [])
        att = sum(x["attempts"] for x in st)
        acc = round(sum(x["accuracy"] * x["attempts"] for x in st) / att) if att else None
        sub.update(accuracy=acc, attempts=att,
                   docs=db.one("SELECT COUNT(*) n FROM documents WHERE subject_id=?", sub["id"])["n"],
                   due_cards=db.one("SELECT COUNT(*) n FROM flashcards WHERE subject_id=? AND due<=?", sub["id"], today())["n"])
        subjects.append(sub)
    names = {s["id"]: s["name"] for s in subjects}
    weak = [dict(s, subject=names.get(s["subject_id"])) for s in stats if s["status"] == "weak"][:6]
    d0 = date.today()
    weekly = [{"day": (d0 - timedelta(days=i)).strftime("%a"), "count": days.get((d0 - timedelta(days=i)).isoformat(), 0)}
              for i in range(6, -1, -1)]
    trend = []
    for w in range(3, -1, -1):
        hi, lo = (d0 - timedelta(days=7 * w)).isoformat(), (d0 - timedelta(days=7 * w + 6)).isoformat()
        r = db.one("SELECT COUNT(*) n, COALESCE(SUM(correct),0) c FROM attempts WHERE user_id=? AND substr(ts,1,10) BETWEEN ? AND ?", uid, lo, hi)
        trend.append({"label": "This week" if w == 0 else f"{w}w ago", "accuracy": round(100 * r["c"] / r["n"]) if r["n"] else None})
    allatt = sum(s["attempts"] for s in stats)
    recent = db.rows("""SELECT d.id, d.filename, d.status, d.created, d.subject_id, s.name subject FROM documents d
                        JOIN subjects s ON s.id=d.subject_id WHERE d.user_id=? ORDER BY d.id DESC LIMIT 5""", uid)
    plan_today = db.rows("""SELECT t.id, t.start, t.minutes, t.label, t.kind, t.topic, t.done, p.subject_id, s.name subject
        FROM plan_tasks t JOIN plans p ON p.id=t.plan_id JOIN subjects s ON s.id=p.subject_id
        WHERE t.user_id=? AND t.day=? ORDER BY t.start""", uid, today())
    return {"today_tasks": plan_today, "streak": streak(days), "goal": {"done": attempts_today, "target": DAILY_GOAL}, "subjects": subjects,
            "weak_topics": weak, "weekly": weekly, "trend": trend, "recent_documents": recent,
            "overall_accuracy": round(sum(s["accuracy"] * s["attempts"] for s in stats) / allatt) if allatt else None,
            "recommendations": recommendations(uid)}
