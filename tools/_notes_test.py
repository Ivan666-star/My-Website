"""阅读笔记 / 语法积累库 / 复习范围 的回归测试。

覆盖用户 2026-10-11 提的这几条：
  · 划选填充 → 自动记入独立笔记库（同篇同内容只记一次）
  · 笔记要能**不断复现**：进复习队列、按阶梯推进
  · 语法要有单独的积累库
  · 词汇默认「不认识」（mastery 默认 1）

覆盖用户 2026-10-14 补的这条：
  · 复习提醒**只针对词汇和表达**：长难句（item_type='sentence'）和语法积累
    （library='grammar'）留在库里随时查阅，不进每日复习队列。

⚠️ 全部跑在临时目录里的**假库**上：不碰 data/ 里的真实账号与学习记录。

跑法（在 项目/ 目录下）：
    python tools/_notes_test.py
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from app import config, db, notes  # noqa: E402

UID = 999


def setup_temp_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="prism_notes_"))
    # 中心库：只需要一张 users 表 + 一条测试账号，user_data_dir() 要用它拼目录名
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


def main() -> int:
    tmp = setup_temp_db()
    checks: list[tuple[str, bool, str]] = []
    try:
        with db.as_user(UID):
            # ---- 阅读笔记：新增 / 去重 / 精确锚点 ----
            a = notes.add_note("sustainable", cn="可持续的", color="yellow", context="…sustainable growth…",
                               article_id=1, doc_date="2026-10-11", para_index=2, char_start=15)
            checks.append(("笔记能新建", bool(a.get("id")) and a.get("created") is True, str(a)))
            b = notes.add_note("sustainable", cn="可持续的", color="green",
                               article_id=1, doc_date="2026-10-11")
            checks.append(("同篇同内容不重复", b.get("created") is False and b.get("id") == a.get("id"), str(b)))
            checks.append(("重复时颜色被更新", b.get("color") == "green", str(b.get("color"))))
            checks.append(("段落锚点存下来了", b.get("para_index") == 2 and b.get("char_start") == 15,
                           f"{b.get('para_index')}/{b.get('char_start')}"))
            c = notes.add_note("a rising tide of scepticism", kind="sentence",
                               article_id=1, doc_date="2026-10-11", para_index=3)
            checks.append(("句子也能记", bool(c.get("id")) and c.get("kind") == "sentence", str(c)))

            listed = notes.list_notes()
            checks.append(("列表能查到 2 条", len(listed) == 2, f"{len(listed)}"))
            checks.append(("按文章过滤", len(notes.notes_for_article(1)) == 2, ""))
            checks.append(("别的文章查不到", len(notes.notes_for_article(9)) == 0, ""))

            # ---- 进复习队列 + 阶梯推进 ----
            st0 = notes.notes_stats()
            checks.append(("新笔记默认在复习周期内", st0["in_cycle"] == 2, str(st0)))
            checks.append(("新笔记不是今天到期（次日再见）", st0["due"] == 0, str(st0)))
            due_now = notes.notes_review_due()
            checks.append(("今天到期数为 0", len(due_now) == 0, f"{len(due_now)}"))

            # 把一条的到期日改到昨天 → 应该出现在今日队列里
            db.execute("UPDATE reading_notes SET next_review=date('now','localtime','-1 day') WHERE id=?",
                       (a["id"],))
            due_now = notes.notes_review_due()
            checks.append(("到期后进今日复习", len(due_now) == 1, f"{len(due_now)}"))
            checks.append(("复习卡片带 src=note（前端要靠它区分素材与笔记）",
                           due_now[0].get("src") == "note", str(due_now[0].get("src"))))

            r1 = notes.notes_apply_review(a["id"], "known")
            checks.append(("记住了：次数 +1", (r1 or {}).get("review_count") == 1, str(r1)))
            checks.append(("记住了：下次日期往后推", bool((r1 or {}).get("next_review")) and
                           (r1 or {}).get("next_review") > db.today_str(), str((r1 or {}).get("next_review"))))
            r2 = notes.notes_apply_review(a["id"], "forgot")
            checks.append(("忘记了：回到次日重来", (r2 or {}).get("review_count") == 0, str(r2)))
            r3 = notes.notes_apply_review(a["id"], "drop")
            checks.append(("不再复习：退出队列（笔记仍在）",
                           (r3 or {}).get("next_review") == "" and notes.notes_stats()["total"] == 2, str(r3)))
            r4 = notes.notes_apply_review(a["id"], "readd")
            checks.append(("重新加入：回到队列", bool((r4 or {}).get("next_review")), str(r4)))

            # ---- 语法积累库 ----
            g1 = notes.save_grammar_point("倒装句", explanation="否定词提前，主句部分倒装",
                                          pattern="Never + 助动词 + 主语", example="Never have I seen…",
                                          category="句法", article_id=1, doc_date="2026-10-11")
            checks.append(("语法点能入库", bool(g1.get("id")) and g1.get("created") is True, str(g1)))
            g2 = notes.save_grammar_point("倒装句", explanation="换了个说法")
            checks.append(("语法点不重复收", g2.get("created") is False, str(g2)))
            lib = notes.grammar_library()
            checks.append(("语法库能列出", len(lib) == 1 and lib[0]["content"] == "倒装句", str(len(lib))))
            # 用户 2026-10-14：复习提醒只针对词汇和表达，长难句和语法先不管
            checks.append(("语法点不入复习队列（next_review 为空）",
                           (lib[0].get("next_review") or "") == "", str(lib[0].get("next_review"))))

            # ---- 复习范围：只有词汇和表达进队列 ----
            # 四条收藏都设成「昨天到期」，用来验证过滤是真的按类型在挡
            def add_fav(item_type: str, content: str, library: str = "reading") -> int:
                return db.execute(
                    "INSERT INTO favorites(user_id,item_type,content,cn,example,tags,source_article_id,"
                    "library,category,position,genre,function,level,usage,auto,confidence,"
                    "created_at,updated_at,review_count,next_review) "
                    "VALUES(?,?,?,?,?,'[]',NULL,?,'通用','universal','universal','','B','',0,0.0,"
                    "datetime('now','localtime'),datetime('now','localtime'),0,"
                    "date('now','localtime','-1 day'))",
                    (UID, item_type, content, "中文释义", "example", library),
                )

            add_fav("vocab", "TEST-vocab")
            add_fav("phrase", "TEST-phrase")
            add_fav("sentence", "TEST-sentence")          # 长难句：不进复习
            add_fav("grammar", "TEST-grammar", "grammar")  # 语法：不进复习（老数据带日期也不进）

            due = db.review_due()
            due_contents = {r["content"] for r in due}
            checks.append(("词汇收藏进复习队列", "TEST-vocab" in due_contents, str(sorted(due_contents))))
            checks.append(("短语收藏进复习队列", "TEST-phrase" in due_contents, str(sorted(due_contents))))
            checks.append(("长难句不进复习队列", "TEST-sentence" not in due_contents, str(sorted(due_contents))))
            checks.append(("语法不进复习队列（带日期的老数据也挡）",
                           "TEST-grammar" not in due_contents, str(sorted(due_contents))))
            rstats = db.review_stats()
            checks.append(("复习统计只数词汇与表达（due/in_cycle 都是 2）",
                           rstats["due"] == 2 and rstats["in_cycle"] == 2, str(rstats)))

            # ---- 词汇默认「不认识」 ----
            aid = db.execute(
                "INSERT INTO articles(user_id,date,title,subtitle,source,source_type,topic,difficulty,"
                "word_count,est_minutes,body,translation,logic,exam_focus,status,model) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (UID, "2026-10-11", "T", "", "", "ai", "教育", 3, 400, 20, "b", "[]", "{}", "{}", "ready", "m"),
            )
            db.execute("INSERT INTO vocab(article_id,word) VALUES(?,?)", (aid, "tide"))
            row = db.query_one("SELECT mastery FROM vocab WHERE article_id=?", (aid,))
            checks.append(("新词默认「不认识」", (row or {}).get("mastery") == 1, str(row)))

            # ---- 删除 ----
            checks.append(("删除笔记", notes.delete_note(c["id"]) is True, ""))
            checks.append(("删除后总量减一", notes.notes_stats()["total"] == 1, str(notes.notes_stats())))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"), flush=True)
        ok = ok and passed
    print("全部通过" if ok else "有失败项", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())