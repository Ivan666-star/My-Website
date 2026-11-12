"""数学和信号的日期批次：周六→周一、周日→周二，后续批次依次顺延。"""
from datetime import date, timedelta
import sqlite3
from .exam_review import DEFAULT_EXAM_DATE


def arrange(conn: sqlite3.Connection, user_id: int | None = None) -> None:
    """在调用方的写事务内重排。原计划不变，已公布日期只允许往后顺延。

    相同原计划日期为一批；不同科目模块、不同账号分别排期。
    没有题目的日期不占位，空出来的位置也不会让其他题突然提前。
    """
    clause, args = (" AND user_id=?", (user_id,)) if user_id is not None else ("", ())
    rows = conn.execute(
        "SELECT id,user_id,subject,planned_review,next_review FROM math_problems "
        "WHERE COALESCE(next_review,'')<>''" + clause + " ORDER BY planned_review,id", args,
    ).fetchall()
    groups: dict[tuple, dict[str, list]] = {}
    for r in rows:
        original = r["planned_review"] or r["next_review"]
        key = (r["user_id"], "sig" if r["subject"] == "信号与系统" else "math")
        groups.setdefault(key, {}).setdefault(original, []).append(r)
    for (owner, _module), batches in groups.items():
        previous = None
        schedule = []
        for original, batch in sorted(batches.items()):
            day = max(date.fromisoformat(original), *(date.fromisoformat(r["next_review"]) for r in batch))
            if previous is not None:
                day = max(day, previous + timedelta(days=1))
            while day.weekday() >= 5:
                day += timedelta(days=1)
            schedule.append((original, batch, day))
            previous = day
        # 顺延接近考试日期时，尽量从考前最后一个工作日向前错开，保持批次分离。
        pref = conn.execute("SELECT value FROM user_profile WHERE user_id=? AND key='exam_date'", (owner,)).fetchone()
        deadline = date.fromisoformat((pref[0] if pref else "") or DEFAULT_EXAM_DATE) - timedelta(days=1)
        while deadline.weekday() >= 5:
            deadline -= timedelta(days=1)
        if schedule and schedule[-1][2] > deadline >= date.today():
            cursor, fitted = deadline, []
            for original, batch, desired in reversed(schedule):
                if desired < date.today():
                    fitted.append((original, batch, desired))
                    continue
                actual = min(desired, cursor)
                while actual.weekday() >= 5:
                    actual -= timedelta(days=1)
                fitted.append((original, batch, actual))
                cursor = actual - timedelta(days=1)
            if all(day >= date.today() or desired < date.today()
                   for (_, _, day), (_, _, desired) in zip(reversed(fitted), schedule)):
                schedule = list(reversed(fitted))
            # 工作日确实不足时保留队列并由计划面板明确提示，不静默叠到同一天。
        for original, batch, day in schedule:
            for r in batch:
                conn.execute("UPDATE math_problems SET planned_review=?,next_review=? WHERE id=?",
                             (original, day.isoformat(), r["id"]))
