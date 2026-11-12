"""按时间预算给出每日建议量；数学和信号共用额度，其他题仍可自主练习。"""
import json
from datetime import date, timedelta
from . import db


def daily_selection(today: date, exam: date) -> dict:
    uid = db.require_user()
    low = int(db.get_user_pref("review_minutes_min", "20"))
    high = int(db.get_user_pref("review_minutes_max", "30"))
    per_item = max(1, min(30, int(db.get_user_pref("review_item_minutes", "6"))))
    capacity = max(1, int((low + high) / 2 / per_item))
    rest = today.weekday() >= 5 or today >= exam
    day = today.isoformat()
    key = "review_daily_" + day
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        done = {r[0] for r in conn.execute(
            "SELECT DISTINCT problem_id FROM math_review_log WHERE user_id=? AND result IN ('right','wrong') "
            "AND created_at>=? AND created_at<?", (uid, day, (today+timedelta(days=1)).isoformat()))}
        rows = conn.execute(
            "SELECT p.id,p.subject,p.next_review,p.review_count,COALESCE(m.wrongs,0) wrongs FROM math_problems p "
            "LEFT JOIN (SELECT problem_id,COUNT(*) wrongs FROM math_review_log WHERE user_id=? "
            "AND result='wrong' AND created_at>=? GROUP BY problem_id) m ON m.problem_id=p.id "
            "WHERE p.user_id=? AND COALESCE(p.next_review,'')<>'' AND p.next_review<=? ORDER BY p.next_review,p.id",
            (uid, (today-timedelta(days=30)).isoformat(), uid, day),
        ).fetchall()
        candidates = {r["id"]: r for r in rows if r["id"] not in done}
        stored = conn.execute("SELECT value FROM user_profile WHERE user_id=? AND key=?", (uid,key)).fetchone()
        try:
            previous = json.loads(stored[0]) if stored else []
        except (TypeError, ValueError):
            previous = []
        if not isinstance(previous, list):
            previous = []
        slots = max(0, capacity-len(done)) if not rest else 0
        selected = list(dict.fromkeys(i for i in previous if isinstance(i,int) and i in candidates))[:slots]

        def add(pid):
            if len(selected)<slots and pid not in selected:
                selected.append(pid)

        # 至少四分之一的建议位照顾最早到期的题，避免旧题一直排不到。
        for r in rows:
            if len(selected)>=max(1,slots//4):
                break
            if r["id"] in candidates:
                add(r["id"])
        # 两个模块都有到期题且仍有额度时，每边至少照顾一道。
        for signal in (False,True):
            if any((candidates[i]["subject"]=="信号与系统")==signal for i in selected):
                continue
            for r in rows:
                if r["id"] in candidates and (r["subject"]=="信号与系统")==signal:
                    add(r["id"])
                    break
        ranked = sorted(candidates.values(), key=lambda r: (
            -((today-date.fromisoformat(r["next_review"])).days + min(r["wrongs"],3)*2 + max(0,6-r["review_count"])),
            r["next_review"],r["id"],
        ))
        for r in ranked:
            add(r["id"])
        if not rest:
            conn.execute("INSERT INTO user_profile(user_id,key,value) VALUES(?,?,?) "
                         "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                         (uid,key,json.dumps(selected)))
            # 只清理旧的建议清单缓存，实际做题记录长期保留。
            conn.execute("DELETE FROM user_profile WHERE user_id=? AND key LIKE 'review_daily_%' AND key<?",
                         (uid,"review_daily_"+(today-timedelta(days=90)).isoformat()))
    return {"ids":selected,"capacity":capacity,"completed":len(done),"remaining":len(selected),
            "pending_total":len(candidates),"waiting":len(candidates)-len(selected),
            "minutes_min":low,"minutes_max":high,"item_minutes":per_item,"rest":rest}
