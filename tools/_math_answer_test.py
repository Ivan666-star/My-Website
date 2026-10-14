# -*- coding: utf-8 -*-
"""数学错题：「只留最终答案」的回归测试（用户 2026-10-12 口径）。

用户原话：
  「我希望的是通过照片识别到图片以后的题目只有 最终答案 然后就是会做了 不会做
    我不希望有解析了 我觉得那样太费时间 我只需要知道我每次做完了对不对就可以了」
  「不用点一下对答案 就保留两个按键会做 不会做 我做完了自己去点即可」

这里钉住四件事：
  ① answer 是**独立一列**，不再塞进 solution（历史数据要能搬过来）；
  ② 刷题取题时（with_analysis=False）**答案必须还在** —— 解析清掉没关系，答案不能清；
  ③ 老库里「答案：xxx + 一大段推导」这种脏数据，迁移后只留答案、推导被摘掉；
  ④ 拍照识别这条路：拿到的是「题干 + 最终答案」，不会产出也不会自动生成解析。

⚠️ 安全：所有读写都在**临时目录**里完成 —— 先把真实用户库**拷**进去再迁移。
   绝不把 settings.db_path 指向 data/，否则 db.as_user() 会直接改真库。

跑法（在 项目/ 目录下）：
    python tools/_math_answer_test.py
"""
from __future__ import annotations

import io
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app import config, db  # noqa: E402

UID = 1
OLD_STRUCT = (
    "CREATE TABLE math_problems ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, subject TEXT DEFAULT '',"
    " kind TEXT DEFAULT 'normal', topic TEXT DEFAULT '', stem TEXT NOT NULL,"
    " image_path TEXT DEFAULT '', source_note TEXT DEFAULT '', knowledge TEXT DEFAULT '',"
    " key_steps TEXT DEFAULT '[]', solution TEXT DEFAULT '', alt_solution TEXT DEFAULT '',"
    " pitfalls TEXT DEFAULT '', ai_model TEXT DEFAULT '', analyzed_at TEXT DEFAULT '',"
    " review_count INTEGER DEFAULT 0, next_review TEXT DEFAULT '', last_result TEXT DEFAULT '',"
    " last_review_at TEXT DEFAULT '', created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '');"
)


def setup() -> Path:
    """造一个只有临时文件的沙盒：中心库 + 一个**老结构**的用户库。"""
    tmp = Path(tempfile.mkdtemp(prefix="mathans_"))

    central = sqlite3.connect(tmp / "app.db")
    central.executescript(db.SCHEMA)
    central.execute(
        "INSERT INTO users(id, username, display_name, password_hash, salt, is_admin, active, approved) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (UID, "t", "测试", "x", "y", 0, 1, 1),
    )
    central.commit()
    central.close()

    userdir = tmp / "users" / "1_t"
    userdir.mkdir(parents=True)
    dst = userdir / "learning.db"

    # 优先拿真实库里的题来测（更有代表性）；拿不到就用造的老数据
    real = None
    realdir = ROOT / "data" / "users"
    if realdir.exists():
        for d in sorted(realdir.iterdir()):
            p = d / "learning.db"
            if p.exists() and p.stat().st_size > 0:
                real = p
                break
    if real:
        shutil.copy2(real, dst)
        # 真实库已经是新结构（answer 列已存在）——为了测「老数据搬家」，
        # 把 answer 列去掉、把答案塞回 solution，还原成迁移前的样子。
        c = sqlite3.connect(dst)
        has_ans = "answer" in {r[1] for r in c.execute("PRAGMA table_info(math_problems)")}
        if has_ans:
            c.executescript(
                "ALTER TABLE math_problems RENAME TO _mp_old;"
                + OLD_STRUCT +
                "INSERT INTO math_problems (id, user_id, subject, kind, topic, stem, image_path,"
                " source_note, knowledge, key_steps, solution, alt_solution, pitfalls, ai_model,"
                " analyzed_at, review_count, next_review, last_result, last_review_at,"
                " created_at, updated_at)"
                " SELECT id, user_id, subject, kind, topic, stem, image_path, source_note, knowledge,"
                " key_steps,"
                " CASE WHEN TRIM(COALESCE(answer,''))<>'' THEN '答案：'||answer||CHAR(10)||CHAR(10)||solution"
                "      ELSE solution END,"
                " alt_solution, pitfalls, ai_model, analyzed_at, review_count, next_review,"
                " last_result, last_review_at, created_at, updated_at FROM _mp_old;"
                "DROP TABLE _mp_old;"
            )
            c.commit()
        c.close()
    else:
        c = sqlite3.connect(dst)
        c.executescript(OLD_STRUCT)
        c.executescript(
            "INSERT INTO math_problems (user_id, stem, solution) VALUES"
            " (1, '求 $x$', '答案：$x=2$'),"
            " (1, '求 $y$', '答案：$y=3$'),"
            " (1, '求 $z$', '第一步先设 $u$，然后……（一大段推导，没有答案标记）');"
        )
        c.commit()
        c.close()

    config.settings.db_path = tmp / "app.db"
    db.settings.db_path = tmp / "app.db"
    db._initialized_user_dbs.clear()
    return tmp


def main() -> int:
    tmp = setup()
    checks: list[tuple[str, bool, str]] = []
    try:
        with db.as_user(UID):
            # 触发 ensure_user_db → 迁移
            cols = [r["name"] for r in db.query("PRAGMA table_info(math_problems)")]
            checks.append(("answer 列已补上", "answer" in cols, str(cols)))

            rows = db.query("SELECT id, answer, solution FROM math_problems ORDER BY id")
            checks.append(("库里有题可测", len(rows) > 0, f"{len(rows)} 条"))

            legacy = [r for r in rows if (r["solution"] or "").lstrip().startswith("答案")]
            checks.append(("历史的「答案：…」都被搬走了", len(legacy) == 0, f"还剩 {len(legacy)} 条"))

            with_ans = [r for r in rows if (r["answer"] or "").strip()]
            checks.append(("确实搬出了答案", len(with_ans) > 0, f"{len(with_ans)} 条"))
            if with_ans:
                s = with_ans[0]
                checks.append(("答案是短的、单行的",
                               "\n" not in s["answer"] and len(s["answer"]) < 200,
                               repr(s["answer"])[:80]))
            dirty = [r for r in with_ans if "第一步" in r["answer"] or "设 $" in r["answer"]]
            checks.append(("答案里没有夹带推导", len(dirty) == 0, f"{len(dirty)} 条有问题"))

            # ② 刷题态：答案保留、解析清空
            from app import math as M  # noqa: E402
            if with_ans:
                one = db.query_one(
                    "SELECT * FROM math_problems WHERE TRIM(COALESCE(answer,''))<>'' LIMIT 1")
                d_drill = M._row(dict(one), with_analysis=False)
                d_full = M._row(dict(one), with_analysis=True)
                checks.append(("刷题态仍带着 answer", bool(d_drill.get("answer")),
                               str(d_drill.get("answer"))[:60]))
                checks.append(("刷题态把解析清掉了",
                               not d_drill.get("solution") and not d_drill.get("knowledge"),
                               f"solution={str(d_drill.get('solution'))[:20]!r}"))
                checks.append(("完整态也带着 answer", bool(d_full.get("answer")), ""))

            # ③ 幂等
            before = {r["id"]: (r["answer"] or "") for r in
                      db.query("SELECT id, answer FROM math_problems")}
        # 再跑一次迁移（新连接）——幂等性
        db.ensure_user_db(UID)
        with db.as_user(UID):
            after = {r["id"]: (r["answer"] or "") for r in
                     db.query("SELECT id, answer FROM math_problems")}
        checks.append(("重复迁移不改变已有答案", before == after,
                       f"{sum(1 for k in before if before[k] != after.get(k))} 条变了"))

        # ④ 拍照识别：题干 + 最终答案，不产出解析
        from app import math as M2  # noqa: E402
        from app.ai import provider as P  # noqa: E402

        class FakeVision:
            model = "fake-vision"
            model_vision = "fake-vision"

            def chat_vision(self, system, user, image, **kw):
                return json.dumps({
                    "stem": "求 $\\int_0^1 x^2\\,dx$ 的值",
                    "answer": "$\\frac{1}{3}$",
                    "subject": "高等数学", "topic": "", "kind": "normal", "uncertain": [],
                }, ensure_ascii=False)

        real_p, real_m = P.get_provider, M2.get_provider
        P.get_provider = lambda: FakeVision()
        M2.get_provider = lambda: FakeVision()
        try:
            got = M2.extract_from_image("data:image/png;base64,AAAA")
            checks.append(("识图能拿到最终答案", got.get("answer") == "$\\frac{1}{3}$",
                           str(got.get("answer"))))
            checks.append(("识图不产出解析字段",
                           not any(got.get(k) for k in
                                   ("solution", "knowledge", "key_steps", "pitfalls")), ""))
            with db.as_user(UID):
                row = M2.create_problem(got["stem"], subject="高等数学", answer=got["answer"])
                checks.append(("新题把答案写进 answer 列",
                               (row or {}).get("answer") == "$\\frac{1}{3}$",
                               str((row or {}).get("answer"))))
                checks.append(("新题的 solution 依然为空",
                               not (row or {}).get("solution"), repr((row or {}).get("solution"))))
                checks.append(("新题没有被标成「已解析」", (row or {}).get("analyzed") is False,
                               str((row or {}).get("analyzed"))))
        finally:
            P.get_provider, M2.get_provider = real_p, real_m
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    print("=== 断言 ===")
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"))
        ok = ok and passed
    print("全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())