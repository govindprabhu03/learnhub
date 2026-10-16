"""Adaptive study planner. Deterministic (no LLM): reads topic mastery, gives weak topics more and earlier
sessions, spaces repeat passes later, reserves the last days for revision, and re-plans as work is completed."""
import itertools
from collections import Counter
from datetime import date, timedelta

import db
import learning

DUR = {"study": 30, "practice": 20, "flashcards": 15, "quiz": 20}
GAP = 10  # minutes between sessions


def _topics(uid, sid):
    """[(topic, weakness 0..1)] weakest first. Unattempted topics get 0.6 (assume shaky until proven)."""
    names = [r["topic"] for r in db.rows(
        "SELECT topic FROM questions WHERE user_id=? AND subject_id=? UNION "
        "SELECT topic FROM flashcards WHERE user_id=? AND subject_id=? AND topic IS NOT NULL", uid, sid, uid, sid)]
    st = {s["topic"]: s for s in learning.topic_stats(uid, sid)}
    weak = lambda t: (100 - st[t]["accuracy"]) / 100 if t in st and st[t]["attempts"] >= learning.MIN_ATTEMPTS else 0.6
    return sorted(((t, weak(t)) for t in names), key=lambda p: -p[1]) or [("General", 0.6)]


def _label(kind, topic, final=False):
    return {"study": f"Study: {topic}", "practice": f"Practice: {topic}", "flashcards": "Flashcards review",
            "quiz": "Final revision quiz" if final else "Mock quiz (adaptive)"}[kind]


def _build(uid, sid, start, exam, daily, t0, skip):
    """Lay the remaining work over [start, exam). Returns (task rows, number of sessions that did not fit)."""
    topics = _topics(uid, sid)
    order = [t for t, _ in topics]
    # work queue in priority order; pass 2/3 are spaced repeats for weaker topics
    queue = [(k, t) for t in order for k in ("study", "practice")]
    queue += [("practice", t) for t, w in topics if w >= 0.35] + [("quiz", "")]
    queue += [(k, t) for t, w in topics if w >= 0.5 for k in ("study", "practice")] + [("quiz", "")]
    skip = Counter(skip)
    rest = []
    for item in queue:  # drop what is already done / already scheduled
        if skip[item] > 0:
            skip[item] -= 1
        else:
            rest.append(item)
    queue = rest
    days = [start + timedelta(i) for i in range((exam - start).days)]
    has_cards = bool(db.one("SELECT 1 x FROM flashcards WHERE user_id=? AND subject_id=? LIMIT 1", uid, sid))
    spare = itertools.cycle([t for t, w in topics for _ in range(1 + int(w * 3))])  # weak topics repeat more
    rows = []
    for i, day in enumerate(days):
        final = len(days) >= 3 and i == len(days) - 1
        items, budget = [], daily
        if has_cards:
            d = min(DUR["flashcards"], budget)
            items.append(("flashcards", "", d)); budget -= d
        if final:  # last day before the exam: no new material, just revise weak spots
            for kind, topic in [("quiz", "")] + [("practice", t) for t in order[:2]]:
                if budget >= DUR[kind]:
                    items.append((kind, topic, DUR[kind])); budget -= DUR[kind]
        else:
            placed = False
            while queue and budget >= 15:
                kind, topic = queue[0]
                need = DUR[kind]
                if budget >= need:
                    dur = need
                elif not placed:
                    dur = budget  # tiny budgets: shrink the first session rather than skip the day
                else:
                    break
                items.append((kind, topic, dur)); queue.pop(0); budget -= dur; placed = True
            while not queue and budget >= DUR["practice"]:  # spare capacity -> extra practice, weakest first
                items.append(("practice", next(spare), DUR["practice"])); budget -= DUR["practice"]
        t = int(t0[:2]) * 60 + int(t0[3:])
        for kind, topic, dur in items:
            rows.append((day.isoformat(), f"{t // 60 % 24:02d}:{t % 60:02d}", dur, kind, topic, _label(kind, topic, final)))
            t += dur + GAP
    return rows, len(queue)


def _insert(plan, rows, dropped):
    db.many("INSERT INTO plan_tasks(plan_id, user_id, day, start, minutes, kind, topic, label) VALUES(?,?,?,?,?,?,?,?)",
            [(plan["id"], plan["user_id"], *r) for r in rows])
    db.run("UPDATE plans SET dropped=?, last_replan=? WHERE id=?", dropped, learning.today(), plan["id"])


def create(uid, sid, exam, daily, start_time):
    db.run("DELETE FROM plans WHERE user_id=? AND subject_id=?", uid, sid)
    pid = db.run("INSERT INTO plans(user_id, subject_id, exam_date, daily_minutes, start_time) VALUES(?,?,?,?,?)",
                 uid, sid, exam.isoformat(), daily, start_time)
    plan = db.one("SELECT * FROM plans WHERE id=?", pid)
    _insert(plan, *_build(uid, sid, date.today(), exam, daily, start_time, {}))
    return plan


def replan(plan, start):
    """Rebuild everything not yet done from `start` on, using current mastery. Missed (past, undone) sessions
    are not counted as done, so they come back into the queue and get rescheduled."""
    exam = date.fromisoformat(plan["exam_date"])
    db.run("DELETE FROM plan_tasks WHERE plan_id=? AND done=0 AND day>=?", plan["id"], start.isoformat())
    kept = db.rows("SELECT kind, topic, done, day FROM plan_tasks WHERE plan_id=?", plan["id"])
    skip = Counter((k["kind"], k["topic"]) for k in kept if k["done"] or k["day"] >= learning.today())
    _insert(plan, *_build(plan["user_id"], plan["subject_id"], start, exam, plan["daily_minutes"], plan["start_time"], skip))


def view(uid, sid):
    plan = db.one("SELECT * FROM plans WHERE user_id=? AND subject_id=?", uid, sid)
    if not plan:
        return None
    today, exam = date.today(), date.fromisoformat(plan["exam_date"])
    if exam > today and plan["last_replan"] != learning.today():  # once per day: adapt to progress + roll over misses
        replan(plan, today)
        plan = db.one("SELECT * FROM plans WHERE id=?", plan["id"])
    tasks = db.rows("SELECT id, day, start, minutes, kind, topic, label, done FROM plan_tasks WHERE plan_id=? ORDER BY day, start", plan["id"])
    upcoming = [t for t in tasks if t["day"] >= today.isoformat()]
    done = sum(t["done"] for t in tasks)
    total = done + sum(1 for t in upcoming if not t["done"])  # missed (past, undone) sessions were re-queued, so excluded
    by_day = {}
    for t in upcoming:
        by_day.setdefault(t["day"], []).append(t)
    return {"exam_date": plan["exam_date"], "days_left": (exam - today).days, "daily_minutes": plan["daily_minutes"],
            "start_time": plan["start_time"], "dropped": plan["dropped"],
            "missed": sum(1 for t in tasks if not t["done"] and t["day"] < today.isoformat()),
            "done": done, "total": total, "days": [{"date": d, "tasks": ts} for d, ts in sorted(by_day.items())]}
