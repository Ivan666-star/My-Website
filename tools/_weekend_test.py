# -*- coding: utf-8 -*-
"""周末休息（用户 2026-10-19）的回归测试。

用户原话：「周末可以不用增加复习任务，周末休息」。
落地的规则只有一条 —— **复习日一律排在工作日**：
  · 算出来落在周六/周日的复习日，顺延到下周一（db.review_day）；
  · 周六/周日当天的复习队列整个为空、到期数按 0 报（db.is_rest_day）；
  · 周末到期没做的不会丢，周一用 next_review<=今天 一起带出来；
  · 老库里已经排在周六/周日的日期，迁移时统一顺延到周一。

三个模块都要覆盖：素材库（favorites）、阅读笔记（reading_notes）、
数学/信号错题（math_problems）。

⚠️ 全部跑在临时目录里的**假库**上：不碰 data/ 里的真实账号与学习记录。
   日期靠 `db.date` 打桩固定，所以无论哪天跑结果都一样（周末跑也是绿）。

跑法（在 项目/ 目录下）：
    python tools/_weekend_test.py
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from app import config, db, math, notes  # noqa: E402

UID = 999
FRI = date(2026, 10, 16)
SAT = date(2026, 10, 17)
SUN = date(2026, 10, 18)
MON = date(2026, 10, 19)
WED = date(2026, 10, 21)
THU = date(2026, 10, 22)


class _FakeDate(date):
    """把「今天」钉死在指定那一天；其余行为就是真的 date。"""

    _today = SAT

    @classmethod
    def today(cls) -> "_FakeDate":
        return cls._today


def freeze(day: date) -> None:
    _FakeDate._today = day
    for mod in (db, notes, math):
        mod.date = _FakeDate  # type: ignore[assignment]


def unfreeze() -> None:
    for mod in (db, notes, math):
        mod.date = date  # type: ignore[assignment]


def setup_temp_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="prism_weekend_"))
    conn = sqlite3.connect(tmp / "app.db")
    conn.executescript(db.SCHEMA)
    conn.execute(
        "INSERT INTO users(id, username, display_name, password_hash, salt, is_admin, active, approved) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (UID, "tester", "测试账号", "x", "y", 0, 1, 1),
    )
    conn.commit()
    conn.close()
    config.settings.db_path = tmp / "app.db"
    db.settings.db_path = tmp / "app.db"
    db._initialized_user_dbs.clear()
    return tmp


def add_fav(content: str, item_type: str, next_review: str) -> int:
    """塞一条素材：到期日直接指定，方便造「排在周末」的场景。"""
    return db.execute(
        "INSERT INTO favorites(user_id,item_type,content,cn,example,tags,source_article_id,"
        "library,category,position,genre,function,level,usage,auto,confidence,"
        "created_at,updated_at,review_count,next_review) "
        "VALUES(?,?,?,'中文释义','example','[]',NULL,'reading','通用','universal','universal',"
        "'','B','',0,0.0,datetime('now','localtime'),datetime('now','localtime'),0,?)",
        (UID, item_type, content, next_review),
    )


def main() -> int:
    tmp = setup_temp_db()
    checks: list[tuple[str, bool, str]] = []
    try:
        # ---------- 1. 纯函数：日期落点 ----------
        # 2026-10-17/18 是周六/周日，2026-10-19 是周一
        cases = [
            ("周五次日（周六）→ 周一", db.review_day(1, base=FRI), "2026-10-19"),
            ("周六次日（周日）→ 周一", db.review_day(1, base=SAT), "2026-10-19"),
            ("周日次日 → 周一", db.review_day(1, base=SUN), "2026-10-19"),
            ("工作日不动：周一 +3 → 周四", db.review_day(3, base=MON), "2026-10-22"),
            ("周四 +3（落到周日）→ 周一", db.review_day(3, base=THU), "2026-10-26"),
            ("周三 +15 → 照常往后推", db.review_day(15, base=WED), "2026-11-05"),
        ]
        for name, got, want in cases:
            checks.append((name, got == want, f"得到 {got}，应为 {want}"))

        checks.append(("周六、周日是休息日", db.is_rest_day(SAT) and db.is_rest_day(SUN), ""))
        checks.append(("周日到周五都不是休息日",
                       not any(db.is_rest_day(date(2026, 10, 19 + i)) for i in range(5)), ""))

        with db.as_user(UID):
            # ---------- 2. 周六当天：队列为空、统计按 0 ----------
            fav = add_fav("WEEKEND-phrase", "phrase", "2026-10-16")   # 周五就到期了
            note = notes.add_note("WEEKEND-note", cn="周末笔记", article_id=1, doc_date="2026-10-17")
            prob = math.create_problem("周末录的一道题", subject="高等数学")

            freeze(SAT)
            checks.append(("周六：素材队列为空", db.review_due() == [], str(db.review_due())))
            fs = db.review_stats()
            checks.append(("周六：素材到期数按 0 报", fs["due"] == 0 and fs["rest"] is True, str(fs)))
            ns = notes.notes_stats()
            checks.append(("周六：笔记到期数按 0 报", ns["due"] == 0 and ns["rest"] is True, str(ns)))
            checks.append(("周六：笔记队列为空", notes.notes_review_due() == [], ""))
            ms = math.stats()
            checks.append(("周六：错题到期数按 0 报",
                           ms["due"] == 0 and ms["rest"] is True and ms["by_subject_due"] == {}, str(ms)))
            checks.append(("周六：错题到期列表为空", math.list_problems(cycle="due") == [], ""))
            checks.append(("周六：随机刷题不会抽出到期题",
                           math.random_problem(cycle="due") is None, ""))

            # ---------- 3. 复习动作落在周末 → 顺延到下周一 ----------
            r = db.review_apply(fav, "known") or {}
            checks.append(("周六点「记住了」→ 下次是周一",
                           r.get("next_review") == "2026-10-19", str(r.get("next_review"))))
            r = db.review_apply(fav, "forgot") or {}
            checks.append(("周六点「忘记了」→ 下次是周一",
                           r.get("next_review") == "2026-10-19", str(r.get("next_review"))))
            r = db.review_apply(fav, "skip") or {}
            checks.append(("周六点「稍后」→ 下次是周一",
                           r.get("next_review") == "2026-10-19", str(r.get("next_review"))))
            r = notes.notes_apply_review(note["id"], "known") or {}
            checks.append(("周六推笔记 → 下次是周一",
                           r.get("next_review") == "2026-10-19", str(r.get("next_review"))))
            r = math.apply_result(prob["id"], "wrong") or {}
            checks.append(("周六错题「不会做」→ 下次是周一",
                           r.get("next_review") == "2026-10-19", str(r.get("next_review"))))

            freeze(FRI)
            r = db.review_apply(fav, "known") or {}
            checks.append(("周五点「记住了」（次日是周六）→ 顺延到周一",
                           r.get("next_review") == "2026-10-19", str(r.get("next_review"))))
            nfri = notes.add_note("WEEKEND-note-fri", cn="周五填充", article_id=1, doc_date="2026-10-16")
            checks.append(("周五填充的笔记 → 首次复习排到周一",
                           nfri.get("next_review") == "2026-10-19", str(nfri.get("next_review"))))
            pfri = math.create_problem("周五录的题", subject="高等数学")
            checks.append(("周五录题（+3 天落到周一）→ 首次复习 2026-10-19",
                           pfri.get("next_review") == "2026-10-19", str(pfri.get("next_review"))))

            freeze(WED)
            pwed = math.create_problem("周三录的题", subject="高等数学")
            checks.append(("周三录题（+3 天落到周六）→ 首次复习排到周一",
                           pwed.get("next_review") == "2026-10-26", str(pwed.get("next_review"))))

            # ---------- 4. 周一：周末攒下的一起带出来 ----------
            freeze(MON)
            db.execute("UPDATE favorites SET next_review='2026-10-17' WHERE id=?", (fav,))
            db.execute("UPDATE reading_notes SET next_review='2026-10-18' WHERE id=?", (note["id"],))
            db.execute("UPDATE math_problems SET next_review='2026-10-15' WHERE id=?", (prob["id"],))
            m = db.review_stats()
            checks.append(("周一不是休息日", m["rest"] is False, str(m)))
            due_ids = {r["id"] for r in db.review_due()}
            checks.append(("周一能补上周六到期的素材", fav in due_ids, str(sorted(due_ids))))
            checks.append(("周一能补上周日到期的笔记",
                           note["id"] in {r["id"] for r in notes.notes_review_due()}, ""))
            checks.append(("周一能补上周末到期的错题",
                           prob["id"] in {r["id"] for r in math.list_problems(cycle="due")}, ""))

            # ---------- 5. 老数据迁移：排在周末的复习日顺延到周一 ----------
            # 工作日那条用来验证「迁移不乱动工作日」。先在用户上下文里插好，
            # 免得和下面那条独占写事务的连接抢锁。
            keep = add_fav("KEEP-weekday", "vocab", "2026-11-05")
            path = db.user_data_dir(UID) / "learning.db"
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("UPDATE favorites SET next_review='2026-10-17' WHERE id=?", (fav,))
                conn.execute("UPDATE reading_notes SET next_review='2026-10-18' WHERE id=?", (note["id"],))
                conn.execute("UPDATE math_problems SET next_review='2026-10-17' WHERE id=?", (prob["id"],))
                db._migrate(conn)
                got = {
                    "fav": conn.execute("SELECT next_review FROM favorites WHERE id=?", (fav,)).fetchone()[0],
                    "note": conn.execute("SELECT next_review FROM reading_notes WHERE id=?", (note["id"],)).fetchone()[0],
                    "math": conn.execute("SELECT next_review FROM math_problems WHERE id=?", (prob["id"],)).fetchone()[0],
                    "weekday": conn.execute("SELECT next_review FROM favorites WHERE id=?", (keep,)).fetchone()[0],
                }
            checks.append(("迁移：素材的周六 → 周一", got["fav"] == "2026-10-19", str(got)))
            checks.append(("迁移：笔记的周日 → 周一", got["note"] == "2026-10-19", str(got)))
            checks.append(("迁移：错题的周六 → 周一", got["math"] == "2026-10-19", str(got)))
            checks.append(("迁移：工作日的复习日不动", got["weekday"] == "2026-11-05", str(got)))
    finally:
        unfreeze()
        shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"), flush=True)
        ok = ok and passed
    print("全部通过" if ok else "有失败项", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())