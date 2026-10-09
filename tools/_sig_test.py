# -*- coding: utf-8 -*-
"""信号与系统错题（2026-10-08 新增科目）回归。

设计决定（改之前先读）：信号与系统**不另起模块**，和数学一共用同一套错题库引擎
（同一张表 / 同一条复习阶梯 / 同一条拍照录题链路），只是科目与知识点树不同。
这里验的就是「加了一个科目之后，引擎的各个环节都还认它」。

用法（在项目根目录执行）：
    python tools/_sig_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TMP = ROOT / "data" / "_sigtest"
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

# ⚠️ 必须在 import app.* 之前写进「当前进程」的 os.environ（env 字典只对子进程生效）
os.environ["DB_PATH"] = str(TMP / "app.db").replace("\\", "/")
os.environ["AI_API_KEY"] = ""

from app import db  # noqa: E402
from app import math as M  # noqa: E402

SIG = "信号与系统"

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
db.init_db()

print("=" * 64)
print("信号与系统错题（共用错题库引擎）回归")
print("=" * 64)

# ------------------------------------------------------------
# 1) 科目体系
# ------------------------------------------------------------
print("\n[1] 科目与知识点树")
check("SUBJECTS 含信号与系统", SIG in M.SUBJECTS, f"({len(M.SUBJECTS)} 个科目)")
check("原有三科没被动", all(s in M.SUBJECTS for s in ("高等数学", "线性代数", "概率论与数理统计")))
tp = M.TOPICS.get(SIG) or []
check("知识点树有 8 个专题", len(tp) == 8, f"({len(tp)})")
check("知识点无重复", len(tp) == len(set(tp)))
check("关键专题在（Z变换 / 拉普拉斯 / 傅里叶）",
      all(k in tp for k in ("Z变换与z域分析", "拉普拉斯变换与s域分析", "傅里叶级数与傅里叶变换")))

sys_prompt = M.MATH_SYSTEM
check("分析提示词提到信号与系统", SIG in sys_prompt)
check("录题提示词提到信号与系统", SIG in M.EXTRACT_SYSTEM)
big = M.build_math_prompt("已知 $x(t)$，求 $X(s)$。", SIG, "拉普拉斯变换与s域分析")
check("分析提示词列出全部科目树", all(s in big for s in M.SUBJECTS))
check("识图提示词列出信号与系统专题树", SIG in M.build_extract_prompt() and "Z变换与z域分析" in M.build_extract_prompt())

# ------------------------------------------------------------
# 2) 建题 / 列表 / 筛选
# ------------------------------------------------------------
print("\n[2] 建题与筛选")
UID_A, UID_B = 301, 402
with db.as_user(UID_A):
    p1 = M.create_problem(
        "已知 $f(t)=u(t)-u(t-2)$，求其拉普拉斯变换 $F(s)$。",
        subject=SIG, topic="拉普拉斯变换与s域分析", kind="exam",
        source_note="2025 年真题回忆")
    check("建题成功", bool(p1 and p1["id"]))
    check("科目落库正确", p1["subject"] == SIG, f"({p1['subject']})")
    check("专题落库正确", p1["topic"] == "拉普拉斯变换与s域分析")
    check("类型标记为真题", p1["kind_label"] == "考研真题错题", f"({p1['kind_label']})")
    check("首刷 = 今天 +3 天",
          p1["next_review"] == (date.today() + timedelta(days=3)).isoformat(),
          f"({p1['next_review']})")

    p2 = M.create_problem(
        "求序列 $x[n]=a^{n}u[n]$ 的 $z$ 变换及其收敛域。",
        subject=SIG, topic="Z变换与z域分析", kind="normal")
    # 顺手放一道数学题，确认两个科目在同一库里互不干扰
    p3 = M.create_problem("求 $\\lim_{x\\to 0}\\frac{\\sin x}{x}$。",
                          subject="高等数学", topic="函数与极限", kind="normal")

    only_sig = M.list_problems(subject=SIG)
    check("按科目筛出 2 道信号题", len(only_sig) == 2, f"({len(only_sig)})")
    check("筛选不混入数学题", all(r["subject"] == SIG for r in only_sig))
    only_topic = M.list_problems(subject=SIG, topic="Z变换与z域分析")
    check("按专题筛出 1 道", len(only_topic) == 1 and only_topic[0]["id"] == p2["id"])

    rnd = M.random_problem(subject=SIG)
    check("随机刷题限定在信号科目", bool(rnd) and rnd["subject"] == SIG)

    st = M.stats()
    check("stats.by_subject 含信号与系统", (st["by_subject"] or {}).get(SIG) == 2,
          f"({st['by_subject']})")
    check("stats.by_subject_due 存在（主界面卡片各显各的数用）",
          isinstance(st.get("by_subject_due"), dict))
    check("数学题没被信号影响", (st["by_subject"] or {}).get("高等数学") == 1)

# ------------------------------------------------------------
# 3) 归属隔离
# ------------------------------------------------------------
print("\n[3] 归属隔离")
with db.as_user(UID_B):
    check("B 看不到 A 的信号题", M.list_problems(subject=SIG) == [])
    check("B 的 stats 信号为 0", (M.stats()["by_subject"] or {}).get(SIG) in (None, 0))
    check("B 改不动 A 的题", M.update_problem(p1["id"], {"topic": "状态变量分析"}) is None)
with db.as_user(UID_A):
    check("A 的专题没被 B 改掉", M._get(p1["id"])["topic"] == "拉普拉斯变换与s域分析")

# ------------------------------------------------------------
# 4) 复习阶梯照常工作（信号题和数学题共用一条阶梯）
# ------------------------------------------------------------
print("\n[4] 复习阶梯")
with db.as_user(UID_A):
    r1 = M.apply_result(p1["id"], "right")   # 数学侧动作名：right/wrong/skip/drop/readd
    check("做对推进一档", r1["review_count"] == 1, f"({r1['review_count']})")
    # ⚠️ 数学的阶梯是 3/7/14/30/60（和英语的 1/2/4…不同）：第 1 次做对落在 3 天档
    check("下次复习 = 今天 +3 天（数学阶梯第一档）",
          r1["next_review"] == (date.today() + timedelta(days=3)).isoformat(),
          f"({r1['next_review']})")
    r2 = M.apply_result(p1["id"], "wrong")
    check("做错归零次日再见", r2["review_count"] == 0
          and r2["next_review"] == (date.today() + timedelta(days=1)).isoformat())
    check("复习记录落库", len(M.review_log(p1["id"])) == 2)

# ------------------------------------------------------------
# 5) 模块隔离（2026-10-08 用户明确要求：信号与数学互不相通）
# ------------------------------------------------------------
print("\n[5] 模块隔离")
with db.as_user(UID_A):
    sig_all = M.list_problems(module="sig")
    check("信号模块只看到信号题",
          len(sig_all) == 2 and all(r["subject"] == SIG for r in sig_all), f"({len(sig_all)})")
    math_all = M.list_problems(module="math")
    check("数学模块看不到信号题", all(r["subject"] != SIG for r in math_all),
          f"({len(math_all)} 道里 0 道信号)")
    check("数学模块能看到自己三科的题", any(r["subject"] == "高等数学" for r in math_all))

    # 未分类的历史题归数学侧 —— 否则它会从两个入口里同时消失（数据还在、界面看不见）
    p_unc = M.create_problem("没选科目的旧题")
    check("未分类题归数学模块", any(r["id"] == p_unc["id"] for r in M.list_problems(module="math")))
    check("未分类题不出现在信号模块",
          all(r["id"] != p_unc["id"] for r in M.list_problems(module="sig")))

    s_sig, s_math = M.stats(module="sig"), M.stats(module="math")
    check("信号模块统计只算自己", s_sig["total"] == 2, f"({s_sig['total']})")
    check("数学模块统计不含信号", s_math["total"] == 2, f"({s_math['total']}：高数1+未分类1)")
    check("两边合计 = 全库", s_sig["total"] + s_math["total"] == M.stats()["total"])

    # ★ 复习提醒必须各归各的：信号题到期，数学侧的到期数 / 到期列表都必须是 0
    db.execute("UPDATE math_problems SET next_review=? WHERE id=?", (db.today_str(), p2["id"]))
    check("信号模块到期数 = 1", M.stats(module="sig")["due"] == 1)
    check("数学模块到期数 = 0（信号提醒不漏过去）", M.stats(module="math")["due"] == 0)
    check("信号到期列表只有信号题",
          all(r["subject"] == SIG for r in M.list_problems(module="sig", cycle="due")))
    check("数学模块到期列表为空", M.list_problems(module="math", cycle="due") == [])
    db.execute("UPDATE math_problems SET next_review=? WHERE id=?",
               ((date.today() + timedelta(days=9)).isoformat(), p2["id"]))

    for _ in range(8):
        rp = M.random_problem(module="sig")
        if rp is None or rp["subject"] != SIG:
            check("随机刷题不跨模块", False, f"({rp and rp['subject']})")
            break
    else:
        check("随机刷题不跨模块（连抽 8 次都是信号）", True)

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
