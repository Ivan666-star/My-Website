# -*- coding: utf-8 -*-
"""只读检查：真实账号的复习队列现在是否真的只含「词汇 + 表达」。

不动任何数据：全程 SELECT。跑法（在 项目/ 下）：
    python tools/_review_scope_check.py [user_id]
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app import db  # noqa: E402

uid = int(sys.argv[1]) if len(sys.argv) > 1 else 1

with db.as_user(uid):
    rows = db.query(
        "SELECT item_type, library, COUNT(*) c, "
        "SUM(CASE WHEN next_review IS NOT NULL AND next_review<>'' THEN 1 ELSE 0 END) in_cycle "
        "FROM favorites GROUP BY item_type, library ORDER BY item_type"
    )
    print(f"账号 {uid} 的素材库（按类型）:")
    for r in rows:
        print(f"  {r['item_type']:<9} library={r['library']:<8} 共 {r['c']:>2} 条，其中在复习周期 {r['in_cycle']} 条")

    st = db.review_stats()
    due = db.review_due()
    print(f"\n复习统计（只数词汇与表达）: due={st['due']} in_cycle={st['in_cycle']} "
          f"mastered={st['mastered']} total={st['total']}")
    print(f"今天队列 {len(due)} 条:")
    for r in due:
        print(f"  [{r['item_type']:<8}] {r['content'][:44]}  下次 {r['next_review']}")
    bad = [r for r in due if r["item_type"] in ("sentence", "grammar") or (r.get("library") or "") == "grammar"]
    print("\n结论：" + ("队列里没有长难句/语法 ✅" if not bad else f"仍有 {len(bad)} 条不该在队列里 ❌"))