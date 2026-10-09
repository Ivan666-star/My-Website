# -*- coding: utf-8 -*-
"""积累闭环 ①「素材使用率 + 批改点名」回归。

覆盖：
  迁移（新列 / 幂等 / 回填）· 本地比对规则（大小写 / 引号 / 太短 / 纯中文）
  · used_count 落库 · mastered_unused 口径 · usage_stats 使用率
  · 归属隔离（B 的作文匹配不到 A 的素材，也改不动 A 的 used_count）
  · weekly_stats 带上 material_usage / material_unused
  · grade_submission 端到端（用假 Provider，不联网、不花额度）

用法（在项目根目录执行）：
    python tools/_material_usage_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TMP = ROOT / "data" / "_mustest"
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
os.environ["DB_PATH"] = str(TMP / "app.db").replace("\\", "/")

from app import db  # noqa: E402
from app.ai import pipeline  # noqa: E402

ok = 0
fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {label} {extra}".rstrip())
    else:
        fail += 1
        print(f"  [FAIL] {label} {extra}".rstrip())


db.init_db()
db.init_db()   # 幂等：跑两次不该报错，也不该重复建表 / 重复回填

UID_A, UID_B = 101, 202

print("=" * 64)
print("积累闭环 ① 素材使用率 + 批改点名 —— 回归")
print("=" * 64)

# ------------------------------------------------------------
# 1) 迁移：新列
# ------------------------------------------------------------
print("\n[1] 迁移与列")
cols = {c["name"] for c in db.query("PRAGMA table_info(favorites)")}
for need in ("used_count", "last_used_at", "review_count", "next_review"):
    check(f"列 {need} 存在", need in cols)
integrity = db.query_one("PRAGMA integrity_check")
check("完整性检查", (integrity or {}).get("integrity_check") == "ok")

# ------------------------------------------------------------
# 2) 本地比对规则
# ------------------------------------------------------------
print("\n[2] 比对规则")
with db.as_user(UID_A):
    def add(content, cn="", **kw):
        fields = {"item_type": "phrase", "library": "writing", "position": "body",
                  "review_count": 0, "used_count": 0, "next_review": ""}
        fields.update(kw)
        sql = ("INSERT INTO favorites(user_id,content,cn,item_type,library,position,"
               "review_count,used_count,next_review,created_at,updated_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),datetime('now','localtime'))")
        return db.execute(sql, (UID_A, content, cn, fields["item_type"], fields["library"],
                                fields["position"], fields["review_count"],
                                fields["used_count"], fields["next_review"]))

    f_phrase = add("play a crucial role in", "起关键作用")
    f_case = add("In the long run", "从长远看")
    f_quote = add("people's awareness", "人们的意识")
    f_short = add("in", "在…里")
    f_cn = add("从某种意义上说", "in a sense")
    f_act = add("act on", "对…起作用")     # 用来测词首边界

    txt = ("Technology plays a crucial role in education. IN THE LONG RUN, "
           "people\u2019s awareness will rise. 从某种意义上说，这是好事。")
    hits = db.materials_matched(txt)
    ids = [h["id"] for h in hits]
    check("命中词组（素材是 play，正文写的是 plays —— 容忍词形变化）", f_phrase in ids)
    check("命中句子首字母大小写不同", f_case in ids)
    check("命中弯引号 people’s", f_quote in ids)
    check("太短的素材不参与匹配", f_short not in ids)
    check("纯中文素材不参与匹配", f_cn not in ids)
    check("命中数正好 3", len(hits) == 3, f"({len(hits)})")
    check("长的排前面", ids[0] == f_phrase, f"({hits[0]['content'] if hits else '—'})")
    check("空文本不崩且返回空", db.materials_matched("") == [])
    check("词首边界：act on 不误伤 factor on",
          f_act not in [h["id"] for h in db.materials_matched("the factor on the market")])
    check("词形变化：act on 能命中 acts on",
          f_act in [h["id"] for h in db.materials_matched("it acts on the market")])

# ------------------------------------------------------------
# 3) used_count 落库
# ------------------------------------------------------------
print("\n[3] used_count / last_used_at")
with db.as_user(UID_A):
    n = db.mark_materials_used([f_phrase])
    check("改动 1 条", n == 1, f"({n})")
    r = db.get_favorite(f_phrase)
    check("used_count 变 1", r["used_count"] == 1, f"({r['used_count']})")
    check("last_used_at 已写", bool(r["last_used_at"]))
    db.mark_materials_used([f_phrase])
    check("再命中累加到 2", db.get_favorite(f_phrase)["used_count"] == 2)

# ------------------------------------------------------------
# 4) 已巩固口径 + 使用率
# ------------------------------------------------------------
print("\n[4] 已巩固口径 / 使用率")
with db.as_user(UID_A):
    # 把 f_case / f_quote 推成「已巩固」：在复习周期内 + review_count 走满阶梯
    for fid in (f_case, f_quote):
        db.execute("UPDATE favorites SET next_review=date('now','+1 day'), review_count=? WHERE id=?",
                   (db.MASTERED_AT, fid))
    # f_phrase 也巩固，但它已经用过
    db.execute("UPDATE favorites SET next_review=date('now','+1 day'), review_count=? WHERE id=?",
               (db.MASTERED_AT, f_phrase))

    st = db.usage_stats()
    check("已巩固 3 条", st["mastered"] == 3, f"({st['mastered']})")
    check("其中用过 1 条", st["mastered_used"] == 1, f"({st['mastered_used']})")
    check("一次没用过 2 条", st["mastered_unused"] == 2, f"({st['mastered_unused']})")
    check("使用率 33%", st["mastered_ratio"] == 33, f"({st['mastered_ratio']})")
    check("总量 6 条", st["total"] == 6, f"({st['total']})")

    un = db.mastered_unused(5)
    un_ids = [r["id"] for r in un]
    check("未用清单只有那 2 条", sorted(un_ids) == sorted([f_case, f_quote]), f"({un_ids})")
    check("已用过的不在清单里", f_phrase not in un_ids)
    check("exclude 生效", f_case not in [r["id"] for r in db.mastered_unused(5, exclude={f_case})])

    # 零除不能炸：一个没有任何已巩固素材的账号，使用率应为 0 而不是除零崩掉
with db.as_user(999):
    check("没有已巩固素材时使用率 0（不除零崩）", db.usage_stats()["mastered_ratio"] == 0)

# ------------------------------------------------------------
# 5) annotate_material_usage 汇总
# ------------------------------------------------------------
print("\n[5] annotate_material_usage")
with db.as_user(UID_A):
    res = db.annotate_material_usage("We should play a crucial role in saving energy.", unused_limit=1)
    check("used 命中 1 条", len(res["used"]) == 1, f"({len(res['used'])})")
    check("used 里回带 / 回写后的 used_count", res["used"][0]["used_count"] == 3,
          f"({res['used'][0]['used_count']})")
    check("unused 受 limit 约束", len(res["unused"]) == 1, f"({len(res['unused'])})")
    check("unused 不重复 used", all(u["id"] not in {x["id"] for x in res["used"]} for u in res["unused"]))
    check("带 stats", "mastered" in res["stats"])

# ------------------------------------------------------------
# 6) 归属隔离
# ------------------------------------------------------------
print("\n[6] 归属隔离")
with db.as_user(UID_B):
    check("B 看不到 A 的素材", db.favorites_all() == [])
    check("B 的作文匹配不到 A 的素材", db.materials_matched(
        "Technology plays a crucial role in education.") == [])
    check("B 改不动 A 的 used_count", db.mark_materials_used([f_phrase]) == 0)
    b_st = db.usage_stats()
    check("B 的使用率全 0", b_st["total"] == 0 and b_st["mastered"] == 0)
    b_res = db.annotate_material_usage("Technology plays a crucial role in education.")
    check("B 的批改点名为空", b_res["used"] == [] and b_res["unused"] == [])
with db.as_user(UID_A):
    check("A 的 used_count 没被 B 改动", db.get_favorite(f_phrase)["used_count"] == 3)

# ------------------------------------------------------------
# 7) weekly_stats 带上素材使用率
# ------------------------------------------------------------
print("\n[7] weekly_stats")
with db.as_user(UID_A):
    s = db.weekly_stats(7)
    check("带 material_usage", isinstance(s.get("material_usage"), dict)
          and s["material_usage"]["mastered"] == 3)
    check("带 material_unused 清单", isinstance(s.get("material_unused"), list)
          and len(s["material_unused"]) == 2)
    check("material_unused 可 JSON 序列化", isinstance(
        json.dumps(s["material_unused"], ensure_ascii=False), str))

# ------------------------------------------------------------
# 8) grade_submission 端到端（假 Provider，不联网）
# ------------------------------------------------------------
print("\n[8] grade_submission 端到端")
FAKE_FB = {
    "score": 7.5, "verdict": "结构完整，表达偏平。", "issues": [],
    "polished": "", "dimensions": {"语言": 7, "结构": 8},
    "recommended_expressions": [], "worth_saving": False, "next_step": "再写一遍。",
}


class _FakeProvider:
    def chat(self, *a, **kw):
        return json.dumps(FAKE_FB, ensure_ascii=False)


pipeline.get_provider = lambda: _FakeProvider()

with db.as_user(UID_A):
    aid = db.execute(
        "INSERT INTO articles(user_id,date,title,body) VALUES(?,?,?,?)",
        (UID_A, "2026-10-07", "Test Article", "Body paragraph."),
    )
    tid = db.execute(
        "INSERT INTO imitation_tasks(article_id,prompt_cn,reference_answer) VALUES(?,?,?)",
        (aid, "谈谈科技对教育的作用。", "Technology plays a crucial role in education."),
    )
    out = pipeline.grade_submission(tid, "We should play a crucial role in saving energy.")
    fb = out["feedback"]
    check("批改返回 round=1", out["round"] == 1)
    check("fb 带 material_usage", isinstance(fb.get("material_usage"), dict))
    mu = fb.get("material_usage") or {}
    check("点名到 1 条用上的素材", len(mu.get("used", [])) == 1,
          f"({[u['content'] for u in mu.get('used', [])]})")
    check("带 unused 提示", len(mu.get("unused", [])) >= 1)
    check("带 stats", (mu.get("stats") or {}).get("mastered") == 3)
    check("used_count 真的落库了", db.get_favorite(f_phrase)["used_count"] == 4)

# ------------------------------------------------------------
# 9) 异常兜底：比对炸了也不能拖垮批改
# ------------------------------------------------------------
print("\n[9] 兜底")
_orig = db.annotate_material_usage


def _boom(*a, **kw):
    raise RuntimeError("模拟比对失败")


db.annotate_material_usage = _boom
with db.as_user(UID_A):
    out2 = pipeline.grade_submission(tid, "anything")
    m2 = out2["feedback"].get("material_usage") or {}
    check("比对失败时批改仍成功", out2["round"] >= 2)
    check("兜底为空名单 + 带 error", m2.get("used") == [] and "error" in m2)
db.annotate_material_usage = _orig

# ------------------------------------------------------------
print("\n" + "=" * 64)
print(f"结果：PASS {ok} / FAIL {fail}")
print("=" * 64)

try:
    import _qa_tmp
    _qa_tmp.cleanup(TMP)
except Exception as exc:  # noqa: BLE001
    print(f"⚠️ 临时目录清理未完成：{exc}")

sys.exit(1 if fail else 0)
