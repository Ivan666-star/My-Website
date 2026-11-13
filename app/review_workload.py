"""分别统计两科的到期任务，不用时间预算截断应复习的题目。"""
from datetime import date, timedelta
from . import db
from .notebooks import subjects_for


def daily_selection(today: date, exam: date, module: str = "math") -> dict:
    uid = db.require_user()
    subjects = subjects_for(module)
    placeholders = ",".join("?" for _ in subjects)
    rest = today.weekday() >= 5 or today >= exam
    with db.connect() as conn:
        completed = conn.execute(
            "SELECT COUNT(DISTINCT l.problem_id) FROM math_review_log l "
            "JOIN math_problems p ON p.id=l.problem_id AND p.user_id=l.user_id "
            f"WHERE l.user_id=? AND p.subject IN ({placeholders}) "
            "AND l.result IN ('right','wrong') AND l.created_at>=? AND l.created_at<?",
            (uid, *subjects, today.isoformat(), (today+timedelta(days=1)).isoformat()),
        ).fetchone()[0]
        pending = conn.execute(
            f"SELECT COUNT(*) FROM math_problems WHERE user_id=? AND subject IN ({placeholders}) "
            "AND COALESCE(next_review,'')<>'' AND next_review<=?",
            (uid, *subjects, today.isoformat()),
        ).fetchone()[0]
    return {"module": module, "completed": completed, "remaining": 0 if rest else pending,
            "pending_total": pending, "rest": rest}
