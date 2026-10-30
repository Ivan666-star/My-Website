# -*- coding: utf-8 -*-
"""把「旧规则挤到周一」的复习日按新规则往后推一格（2026-10-30 用户口径）。

背景：
    旧的「周末休息」是「撞到周六/周日 → 一律顺延到下周一」。所以周日那一档的任务
    被挤到了周一，和周一原本的任务叠在一起 —— 用户 2026-10-30 的原话是
    「不要把周末的复习任务累加到工作日去，而是直接往后推」。
    代码已经从 `db.review_day()` 改成按工作日数（周六的进周一、周日的进周二），
    这个脚本是一次性的**存量清理**：把已经按老规则排好、被周末挤过的未来日期重算一遍。

安全阀（三条都满足才动，否则只跳过）：
    1. 只动**未来**的日期（next_review > 今天）；已逾期的不碰。
    2. 基准日取「最后一次复习的日期」（没有就用 updated_at / created_at）。
    3. 存着的日期必须**正好等于**老规则算出来的那一天（撞周末 → 下周一）。
       对不上就说明这条日期不是老规则排的（手工改过 / 种子数据），一律跳过。
    重算的结果 = 按**工作日**数往后推进，和 db.review_day 完全同一套算法。

幂等：改完之后存的就是新规则的结果，再跑一次不会命中。

跑法（在 项目/ 目录下）：
    python tools/_weekend_pile_fix.py            # 只看会改哪些（默认 dry-run）
    python tools/_weekend_pile_fix.py --apply    # 真改（先确保 data/backups 里有备份）
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from app import db, math  # noqa: E402

DATA = ROOT / "data"

# 表 → (间隔阶梯, 动作名映射)
#   英语侧（素材库 / 阅读笔记）：known/forgot/skip/drop/readd
#   数学侧（错题）：right/wrong/skip/drop/readd
TABLES = {
    "favorites": {
        "intervals": db.REVIEW_INTERVALS,
        "short": ("forgot", "skip"),      # 这些动作 → 1 个工作日后
        "reset": ("readd",),              # 重新加入 → 阶梯第一档
        "advance": ("known",),
    },
    "reading_notes": {
        "intervals": db.REVIEW_INTERVALS,
        "short": ("forgot", "skip"),
        "reset": ("readd",),
        "advance": ("known",),
    },
    "math_problems": {
        "intervals": math.REVIEW_INTERVALS,
        "short": ("wrong", "skip"),
        "reset": ("readd",),
        "advance": ("right",),
    },
}


def _parse_day(value: str | None) -> date | None:
    text = (value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _workday(base: date, days: int) -> date:
    """和 db.review_day 同一套算法：往后数 days 个工作日。"""
    d, left = base, max(int(days), 0)
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _old_rule_day(base: date, days: int) -> date:
    """老规则：自然日相加，撞到周六/周日 → 顺延到下周一。"""
    d = base + timedelta(days=int(days))
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _interval(spec: dict, review_count: int, last_result: str) -> int:
    intervals = spec["intervals"]
    rc = int(review_count or 0)
    act = (last_result or "").strip()
    if act in spec["short"]:
        return 1
    if act in spec["reset"] or not act:
        return int(intervals[0])
    if act in spec["advance"]:
        return int(intervals[min(max(rc, 1) - 1, len(intervals) - 1)])
    return int(intervals[0])          # drop 之类：next_review 本来就空，走不到这里


def plan_for(conn: sqlite3.Connection, table: str, today: date) -> list[tuple]:
    """返回 [(id, 旧日期, 新日期, 基准日, 间隔)]，只含真的要改的行。"""
    spec = TABLES[table]
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    base_col = "last_review_at" if "last_review_at" in cols else "updated_at"
    rows = conn.execute(
        f"SELECT id, next_review, review_count, COALESCE(last_result,'') AS last_result, "
        f"{base_col} AS base_at, created_at FROM {table} "
        "WHERE next_review IS NOT NULL AND next_review<>'' AND next_review>?",
        (today.isoformat(),),
    ).fetchall()
    out = []
    for r in rows:
        base = _parse_day(r["base_at"] or r["created_at"])
        if base is None:
            continue
        gap = _interval(spec, r["review_count"], r["last_result"])
        cal = base + timedelta(days=gap)
        if cal.weekday() < 5:          # 老规则下这一天不会被挤，说明未被周末影响
            continue
        stored = _parse_day(r["next_review"])
        if stored != _old_rule_day(base, gap):   # 不是老规则排出来的 → 手工/种子，跳过
            continue
        new = _workday(base, gap)
        if new != stored:
            out.append((r["id"], r["next_review"], new.isoformat(), base.isoformat(), gap))
    return out


def targets() -> list[Path]:
    out = sorted(DATA.glob("users/*/learning.db"))
    if (DATA / "app.db").exists():
        out.append(DATA / "app.db")       # 早期未拆分的历史数据也在这里
    return out


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    today = date.today()
    week = "一二三四五六日"
    print(f"今天 {today.isoformat()}（周{week[today.weekday()]}）"
          f"　模式：{'真改' if apply else 'dry-run（只看看）'}\n")
    total = 0
    for path in targets():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            print(f"{path.relative_to(DATA)}")
            for table in TABLES:
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    continue
                changes = plan_for(conn, table, today)
                if not changes:
                    print(f"  {table:<14} 没有要挪的")
                    continue
                for pid, old, new, base, gap in changes:
                    print(f"  {table:<14} #{pid:<5} {old} → {new}   基准 {base} +{gap} 个工作日")
                if apply:
                    conn.executemany(
                        f"UPDATE {table} SET next_review=? WHERE id=?",
                        [(new, pid) for pid, old, new, base, gap in changes],
                    )
                    conn.commit()
                total += len(changes)
        finally:
            conn.close()
    print(f"\n合计 {total} 条" + ("（已写入）" if apply else "（没写入，加 --apply 才真改）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
