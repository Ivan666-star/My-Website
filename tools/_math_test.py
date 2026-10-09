# -*- coding: utf-8 -*-
"""数学一错题库回归：建表 / 归属隔离 / 复习阶梯 / 原图存取 / 识图取题干。

用法（在项目根目录执行）：
    python tools/_math_test.py            # 全跑（含一次真实识图，要联网+耗额度）
    python tools/_math_test.py --no-ai    # 跳过联网识图那一段

为什么单独一份临时库而不是拷正式库：
    这个模块是新增的，正式库里大概率一条数学数据都没有；从零建一份更干净，
    也避免测试写脏正式库（正式库在 data/app.db，用户的真实数据）。
"""
from __future__ import annotations

import base64
import io
import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TMP = ROOT / "data" / "_mathtest"
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

os.environ["DB_PATH"] = str(TMP / "app.db").replace("\\", "/")

from app import db  # noqa: E402
from app import math as M  # noqa: E402
from app.ai.provider import AIError, get_provider  # noqa: E402
from app.config import settings  # noqa: E402

db.init_db()
db.init_db()   # 幂等：跑两次不该报错、也不该重复建表

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


def plus(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def fetch(pid: int):
    """取一条并套上 _row 装饰。

    ⚠️ `M._get()` 给的是**原始行**（没有 kind_label / mastered / in_cycle / analyzed
    这些算出来的字段），直接当完整对象用会 KeyError —— 测试里踩过一次。
    """
    return M._row(M._get(pid))


print("=" * 64)
print("数学一错题库回归")
print("=" * 64)

# ------------------------------------------------------------
# 1) 表 / 索引
# ------------------------------------------------------------
print("\n[1] 建表与索引")
tables = {r["name"] for r in db.query(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'math%'")}
check("math_problems 建出", "math_problems" in tables)
check("math_review_log 建出", "math_review_log" in tables)
idx = {r["name"] for r in db.query(
    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_math%'")}
check("数学索引建出", len(idx) >= 3, f"({len(idx)} 个)")

cols = {c["name"] for c in db.query("PRAGMA table_info(math_problems)")}
for need in ("user_id", "stem", "image_path", "next_review", "review_count",
             "knowledge", "solution", "pitfalls", "analyzed_at"):
    check(f"列 {need} 存在", need in cols)
check("按用户要求没有「我的答案」字段",
      not any(c in cols for c in ("my_answer", "my_solution", "user_answer")))
integrity = db.query_one("PRAGMA integrity_check")
check("完整性检查", (integrity or {}).get("integrity_check") == "ok")

# ------------------------------------------------------------
# 2) 录题 / 列表 / 必填校验
# ------------------------------------------------------------
print("\n[2] 录题与列表")
UID_A, UID_B = 101, 202
with db.as_user(UID_A):
    p1 = M.create_problem("求 $\\lim_{x\\to 0}\\frac{\\sin x}{x}$。", subject="高等数学",
                          topic="函数与极限", kind="normal")
    p2 = M.create_problem("7. 设 $A$ 为 3 阶矩阵，求 $|A|$。", subject="线性代数",
                          topic="行列式", kind="exam")
    check("建档成功", bool(p1 and p1["id"]))
    check("首刷日期 = 今天 +3 天", p1["next_review"] == plus(3), f"({p1['next_review']})")
    check("kind_label 正确", p2["kind_label"] == "考研真题错题", f"({p2['kind_label']})")
    check("in_cycle 为真", p1["in_cycle"] is True)
    check("未解析时 analyzed=False", p1["analyzed"] is False)

    try:
        M.create_problem("   ")
        check("空题干被拒", False)
    except ValueError:
        check("空题干被拒", True)

    items = M.list_problems()
    check("列表拿到 2 条", len(items) == 2, f"(实得 {len(items)})")
    only_math = M.list_problems(subject="高等数学")
    check("按科目筛出 1 条", len(only_math) == 1, f"(实得 {len(only_math)})")
    only_exam = M.list_problems(kind="exam")
    check("按类型筛出真题 1 条", len(only_exam) == 1)
    hit = M.list_problems(q="sin")
    check("搜 LaTeX 题干能命中", len(hit) == 1, f"(实得 {len(hit)})")

# ------------------------------------------------------------
# 3) 归属隔离
# ------------------------------------------------------------
print("\n[3] 归属隔离（越权一律当不存在）")
with db.as_user(UID_B):
    check("B 看不到 A 的错题", len(M.list_problems()) == 0)
    check("B 直接按 id 取 A 的题 → None", M._get(p1["id"]) is None)
    check("B 改 A 的题 → None", M.update_problem(p1["id"], {"stem": "篡改"}) is None)
    check("B 删 A 的题 → False", M.delete_problem(p1["id"]) is False)
    check("B 的统计为 0", M.stats()["total"] == 0)
    b1 = M.create_problem("B 自己的题", subject="概率论与数理统计")
    check("B 能建自己的题", bool(b1 and b1["id"]))

with db.as_user(UID_A):
    check("A 的数据没被 B 动过", len(M.list_problems()) == 2)
    check("A 的题干未被篡改", fetch(p1["id"])["stem"].startswith("求 $"))

# ------------------------------------------------------------
# 4) 复习阶梯 3/7/14/30/60，做错归零
# ------------------------------------------------------------
print("\n[4] 复习阶梯")
with db.as_user(UID_A):
    pid = M.create_problem("阶梯测试题", subject="高等数学")["id"]
    seq = []
    for _ in range(len(M.REVIEW_INTERVALS)):
        r = M.apply_result(pid, "right")
        seq.append(r["next_review"])
    expect = [plus(d) for d in M.REVIEW_INTERVALS]
    check("连续做对依次推到 " + "/".join(str(d) for d in M.REVIEW_INTERVALS),
          seq == expect, f"(实得 {seq})")
    check("达到 MASTERED_AT 后 mastered=True", fetch(pid)["mastered"] is True)
    check("MASTERED_AT 与阶梯长度一致", M.MASTERED_AT == len(M.REVIEW_INTERVALS))

    r = M.apply_result(pid, "wrong")
    check("做错 → 计数归零", r["review_count"] == 0, f"(实得 {r['review_count']})")
    check("做错 → 次日再见", r["next_review"] == plus(1), f"({r['next_review']})")

    r = M.apply_result(pid, "skip")
    check("稍后再说 → 进度不变、推到明天",
          r["review_count"] == 0 and r["next_review"] == plus(1))

    r = M.apply_result(pid, "drop")
    check("不再复习 → 移出周期", r["in_cycle"] is False)
    check("已掌握的筛不出这条", all(x["id"] != pid for x in M.list_problems(cycle="mastered")))

    r = M.apply_result(pid, "readd")
    check("重新加入 → 回到 +3 天", r["in_cycle"] is True and r["next_review"] == plus(3))

    log = M.review_log(pid)
    check("复习历史已留痕", len(log) == len(M.REVIEW_INTERVALS) + 4, f"(实得 {len(log)})")
    check("历史里的 result 都是已知动作",
          all(x["result"] in M.REVIEW_ACTIONS for x in log))

    try:
        M.apply_result(pid, "乱写的结果")
        check("非法复习动作被拒", False)
    except ValueError:
        check("非法复习动作被拒", True)

    due = M.list_problems(cycle="due")
    check("cycle=due 只返回到期的", all(x["next_review"] <= db.today_str() for x in due))

# ------------------------------------------------------------
# 4.5) 「先做题后看解析」：刷题接口在**服务端**就不许带解析出来
#      （截图只能证明"这一次没渲染出来"，接口层断言才是真闸门）
# ------------------------------------------------------------
print("\n[4.5] 刷题不泄漏解析（接口层）")
with db.as_user(UID_A):
    leak = M.create_problem("泄漏检查题：$\\int_0^1 x^2\\,dx$", subject="高等数学")
    lid = leak["id"]
    M.update_problem(lid, {
        "knowledge": "牛顿-莱布尼茨公式",
        "solution": "原函数是 x^3/3，代入得 1/3。",
        "pitfalls": "别忘了代上限。",
        "key_steps": ["求原函数", "代入上下限"],
    })
    check("写了解析后 analyzed=True", fetch(lid)["analyzed"] is True)

    # ⚠️ 必须带上能唯一定位这道题的条件：random_problem() 不带条件会随机抽到别的题，
    #    那样断言"没有解析"会假通过（踩过）。这里用题面里的独特串搜。
    one = M.random_problem(q="泄漏检查题")
    check("random 抽到的是这道题", bool(one) and one["id"] == lid, f"({one and one['id']})")
    if one:
        check("random 拿到的题**不含** knowledge", not one.get("knowledge"),
              f"({one.get('knowledge')!r})")
        check("random 拿到的题**不含** solution", not one.get("solution"),
              f"({one.get('solution')!r})")
        check("random 拿到的题**不含** pitfalls", not one.get("pitfalls"))
        check("random 的 key_steps 被清空", one.get("key_steps") == [])
        check("random 但 has_analysis=True（前端知道有解析）", one.get("has_analysis") is True)

    out = M.list_problems(with_analysis=False)
    hit = [x for x in out if x["id"] == lid]
    check("列表（刷题视角）同样不带解析",
          bool(hit) and not hit[0].get("solution") and not hit[0].get("knowledge"))
    check("列表（刷题视角）但 has_analysis=True",
          bool(hit) and hit[0].get("has_analysis") is True)

    full = M.list_problems(with_analysis=True)
    me = [x for x in full if x["id"] == lid][0]
    check("详情/管理视角能看到解析", bool(me.get("solution") and me.get("knowledge")))

# ------------------------------------------------------------
# 5) 原图存取（base64 → 落盘 → 读回 → 防穿越）
# ------------------------------------------------------------
print("\n[5] 原题照片")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def png_data_url(text: str) -> str:
    img = Image.new("RGB", (420, 120), "white")
    ImageDraw.Draw(img).text((12, 40), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


DATA_URL = png_data_url("7. Compute |A|")
with db.as_user(UID_A):
    pimg = M.create_problem("带照片的题", subject="线性代数", image_data=DATA_URL)
    check("图片已落盘并写进 image_path", bool(pimg["image_path"]), f"({pimg['image_path']})")
    check("has_image 为真", pimg["has_image"] is True)
    f = M.image_file(pimg["image_path"])
    check("能按 image_path 读回文件", bool(f and f.is_file()))
    check("落盘目录是 math_images/<uid>/",
          pimg["image_path"].startswith(f"math_images/{UID_A}/"))
    check("路径里没有双反斜杠 / 双斜杠",
          "//" not in pimg["image_path"] and "\\\\" not in pimg["image_path"])
    check("目录穿越被拒", M.image_file("../../etc/passwd") is None)
    check("不存在的文件 → None", M.image_file("math_images/999/nope.png") is None)
    check("坏 base64 不炸、只是存不下",
          M.save_image("data:image/png;base64,@@@@", UID_A) == "")
    check("非图片 data url 被拒", M.save_image("data:text/plain;base64,aGk=", UID_A) == "")

    # 越权读图：接口层要 404
    from fastapi import HTTPException
    with db.as_user(UID_B):
        try:
            M.api_image(pimg["id"])
            check("B 读 A 的原图 → 404", False)
        except HTTPException as exc:
            check("B 读 A 的原图 → 404", exc.status_code == 404, f"(实得 {exc.status_code})")

# ------------------------------------------------------------
# 5b) 不识图就不留图：stats().with_image + purge_images()
#     用户 2026-10-07 要求：「识图了以后就不用再保留图片了，节省存储空间」。
#     新录的题前端不再上传图片；这一节验的是**历史遗留**那张能一次清干净。
# ------------------------------------------------------------
print("\n[5b] 原图清理（识图后不留图）")
with db.as_user(UID_A):
    st0 = M.stats()
    check("stats 里有 with_image", "with_image" in st0)
    check("with_image 数到了那张图", st0.get("with_image") == 1, f"({st0.get('with_image')})")

    M.create_problem("第二张带照片的题", subject="高等数学", image_data=DATA_URL)
    check("补第二张后 with_image=2", M.stats().get("with_image") == 2)

    probe = [(it["id"], it["image_path"]) for it in M.list_problems() if it.get("image_path")]
    check("清理前有 2 条带图记录", len(probe) == 2, f"({[p[0] for p in probe]})")
    check("清理前文件确实在磁盘上", all(M.image_file(p) is not None for _, p in probe))

    # 快照：清理前每道题的「是否已有解析」——清图不该动它
    snap_analyzed = {it["id"]: it["analyzed"] for it in M.list_problems()}

    before = M.stats().get("with_image") or 0
    r = M.purge_images()
    check("purge 返回 ok", r.get("ok") is True)
    check("purge 张数 = 清理前的张数", r.get("removed") == before, f"({r.get('removed')}/{before})")
    check("purge 报出了释放的字节", (r.get("bytes") or 0) > 0, f"({r.get('bytes')} B)")
    check("清完 with_image 归零", M.stats().get("with_image") == 0)
    check("磁盘上那两张图已删", all(M.image_file(p) is None for _, p in probe))

    after = M.list_problems()
    check("所有题的 has_image 都变假", all(not it["has_image"] for it in after))
    check("题干没被误删", all((it["stem"] or "").strip() for it in after))
    check("复习周期没被误动", all(it["in_cycle"] for it in after))
    check("清理前后每道题的「是否已解析」完全一致（没被误动）",
          {it["id"]: it["analyzed"] for it in after} == snap_analyzed,
          f"({len(snap_analyzed)} 道)")

    r2 = M.purge_images()
    check("重复清理幂等（0 张 0 字节）",
          r2.get("removed") == 0 and r2.get("bytes") == 0, f"({r2})")

    with db.as_user(UID_B):
        rb = M.purge_images()
        check("B 清理动不到 A 的数据（本次应为 0）", rb.get("removed") == 0, f"({rb})")

# 清完之后 /problems/{pid}/image 应回 404（不是 500）
with db.as_user(UID_A):
    from fastapi import HTTPException as _HE
    try:
        M.api_image(probe[0][0])
        check("清图后取原图 → 404", False)
    except _HE as exc:
        check("清图后取原图 → 404", exc.status_code == 404, f"(实得 {exc.status_code})")


# ------------------------------------------------------------
# 6) 识图取题干（真实调 deepseek-flash）
# ------------------------------------------------------------
print("\n[6] 拍照取题干（真实调用）")
if "--no-ai" in sys.argv:
    print("  [SKIP] --no-ai：跳过联网识图")
elif not settings.ai_ready:
    print("  [SKIP] .env 里没配 AI_API_KEY")
else:
    prov = get_provider()
    print(f"  模型：{getattr(prov, 'model', '?')}")
    # 一张"题目照片"：题干 + 一段"解答/答案"（解答必须**不要**被抄进来）
    img = Image.new("RGB", (940, 470), "white")
    dr = ImageDraw.Draw(img)
    try:
        f30 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
    except Exception:  # noqa: BLE001
        f30 = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", 30)
    lines = [
        "8. 设 A = [[1,2,3],[4,5,6],[7,8,9]]，求 |A| 的值。",
        "",
        "解：按第一行展开，|A| = 1*(5*9-6*8) - 2*(4*9-6*7) + 3*(4*8-5*7)",
        "= 1*(-3) - 2*(-6) + 3*(-3) = -3 + 12 - 9 = 0。",
        "答案：0",
    ]
    for i, ln in enumerate(lines):
        dr.text((26, 26 + i * 58), ln, fill="black", font=f30)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    photo = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    (TMP / "extract_input.png").write_bytes(buf.getvalue())   # 留一份供肉眼核对

    try:
        d = M.extract_from_image(photo)
        print(f"  识别到题干：{d['stem'][:160]}")
        print(f"  分类建议：subject={d['subject']!r} topic={d['topic']!r} kind={d['kind']!r}")
        print(f"  不确定项：{d['uncertain']}")
        check("取回了题干", bool(d["stem"].strip()))
        check("题干里有题目关键内容（|A| / 行列式）", "|A|" in d["stem"] or "A" in d["stem"])
        check("**没有**把「解：」的解答抄进来", "解：" not in d["stem"])
        check("**没有**把答案抄进来", "答案" not in d["stem"])
        check("科目落在白名单内", d["subject"] in M.SUBJECTS or d["subject"] == "",
              f"({d['subject']!r})")
        check("专题与科目匹配", d["topic"] in M.TOPICS.get(d["subject"], []) or d["topic"] == "",
              f"({d['topic']!r})")
        check("类型在 KINDS 内", d["kind"] in M.KINDS, f"({d['kind']!r})")
    except AIError as exc:
        check("识图调用成功", False, f"({exc})")

# ------------------------------------------------------------
# 7) 公式分隔符兜底
# ------------------------------------------------------------
print("\n[7] 公式分隔符兜底（前端靠 $...$ 才能渲染成排版公式）")
# 用户原话：「录入进去的题目并不是我能够直接看明白的公式，是什么代码」。
# 前端现在用本地 KaTeX 渲染 $...$，所以这里守两件事：
#   1) 没写 $ 的裸 LaTeX 必须能补上（手打、模型漏加都兜得住）
#   2) **已经写了 $ 的一个字都不许动**（动了就是切坏正常文本）
N = M.normalize_math_delims

bare = ("例2.17 设随机变量 X 的概率密度为 f_X(x)=\\begin{cases} \\dfrac{1}{2}, & -1<x<0, "
        "\\\\ \\dfrac{1}{4}, & 0\\le x<2, \\\\ 0, & \\text{其他}. \\end{cases} "
        "令 Y=X^2，求 Y 的概率密度 f_Y(y).")
got = N(bare)
check("裸 LaTeX 补上了 $", "$" in got)
check("$ 成对（不产生嵌套 / 落单）", got.count("$") % 2 == 0, f"({got.count('$')} 个)")
check("\\text{其他} 里的中文没被从花括号中间切开", "\\text{其他}" in got, f"({got[:56]}…)")
check("中文描述本身没被包进公式", "设随机变量 X 的概率密度为" in got)

keep = "已经有 $ 的：设 $A$ 为三阶方阵，求 $|A|$。"
check("已有 $ 的原样不动", N(keep) == keep)
check("纯中文原样不动", N("纯中文，没有任何公式。") == "纯中文，没有任何公式。")
check("英文句子（无 LaTeX 命令）原样不动",
      N("Find x such that 2x+1=5.") == "Find x such that 2x+1=5.")
check("空串不炸", N("") == "")

with db.as_user(UID_A):
    # 落库路径也得兜：create_problem 存进去的就应该是**能渲染的**
    row = M.create_problem(bare, subject="概率论与数理统计", topic="随机变量及其分布")
    check("落库后题干已带 $", "$" in row["stem"])
    check("落库后公式没被切坏", "\\text{其他}" in row["stem"])
    # 用户明确说了「解析也要看得懂」→ 解析字段同样要兜
    upd = M.update_problem(row["id"], {
        "solution": "由 $F_Y(y)$ 求导得 f_Y(y)=\\dfrac{1}{4}",
        "key_steps": ["先写 F_Y(y)=P(Y≤y)"],
    })
    check("解析字段的裸 LaTeX 也被补了 $", "$" in (upd or {}).get("solution", ""),
          f"({(upd or {}).get('solution', '')[:40]}…)")
    check("key_steps 里的裸 LaTeX 也被补了 $",
          "$" in ((upd or {}).get("key_steps") or [""])[0],
          f"({((upd or {}).get('key_steps') or [''])[0][:40]}…)")

# ------------------------------------------------------------
print("\n" + "=" * 64)
print(f"结果：{ok} 通过 / {fail} 待修")
print("=" * 64)

# 收尾：删掉本次的临时库与原图。
# 用 _qa_tmp.cleanup（删不掉会挂到 atexit 等句柄释放），别用 rmtree(ignore_errors=True)
# —— 那个会静默失败，每跑一次就悄悄攒一份垃圾（本模块踩过 60+ 个）。
import _qa_tmp  # noqa: E402

_qa_tmp.cleanup(TMP)
print("临时目录已清理" if not TMP.exists() else f"临时目录暂留（进程退出后再删）：{TMP}")
