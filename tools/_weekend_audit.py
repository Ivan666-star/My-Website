# -*- coding: utf-8 -*-
"""只读检查：真实账号的 next_review 里有没有落在周六/周日的（周末休息上线前的存量）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DATA = ROOT / "data"
TABLES = ("favorites", "reading_notes", "math_problems")

targets = list(sorted(DATA.glob("users/*/learning.db")))
if (DATA / "app.db").exists():
    targets.append(DATA / "app.db")      # 中心库：早期未拆分的历史数据也在这里

for db_file in targets:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    print(f"\n{db_file.relative_to(DATA)}")
    for t in TABLES:
        try:
            rows = conn.execute(
                f"SELECT next_review, COUNT(*) c FROM {t} "
                "WHERE next_review IS NOT NULL AND next_review<>'' "
                "AND strftime('%w', next_review) IN ('0','6') GROUP BY next_review "
                "ORDER BY next_review"
            ).fetchall()
        except sqlite3.Error as exc:
            print(f"  {t}: {exc}")
            continue
        total = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()[0]
        weekend = sum(r["c"] for r in rows)
        detail = "，".join(f"{r['next_review']}×{r['c']}" for r in rows) or "无"
        print(f"  {t:<14} 共 {total:>3} 条，其中排在周末 {weekend} 条  {detail}")
    conn.close()