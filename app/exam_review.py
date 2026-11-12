"""按账号的目标考试日期调整复习间隔；阶段参数是默认策略，不是唯一最优公式。"""
from datetime import date, timedelta

DEFAULT_EXAM_DATE = "2027-12-18"  # 预计日期，正式公布后在设置中修改。


def phase(today: date, exam: date) -> dict:
    left = (exam - today).days
    if left <= 0:
        name, cap = "考试期间及之后", 0
    elif left <= 7:
        name, cap = "考前整理", 3
    elif left <= 30:
        name, cap = "考前回顾", 7
    elif left <= 60:
        name, cap = "冲刺巩固", 14
    elif left <= 120:
        name, cap = "强化复习", 30
    else:
        name, cap = "长期巩固", 60
    return {"name": name, "max_interval": cap, "days_left": left, "exam_date": exam.isoformat()}


def target(today: date, interval: int, exam: date) -> str:
    """保留短期学习间隔；跨入下一备考阶段前必须再复习一次。"""
    policy = phase(today, exam)
    if not policy["max_interval"]:
        return ""
    last = exam - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    if last < today:
        return ""
    day = min(today + timedelta(days=min(interval, policy["max_interval"])), last)
    for days in (120, 60, 30, 7):
        boundary = exam - timedelta(days=days)
        if today < boundary < day:
            day = boundary
    return day.isoformat()
