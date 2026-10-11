"""数学一错题库：数据层 + 接口。

依据用户提供的《2028考研备考系统开发计划书（更新版）》第四 ~ 十七节实现。

几条必须守住的约定（和 app/main.py 顶部那段一致）：
    - 归属由 `auth.AuthGate` 放进 db 上下文，这里一律用 `db.scope()` / `db.require_user()`，
      **不要自己往 SQL 里拼 user_id 参数** —— 漏一处就是静默跨用户串数据。
    - 层级：数一错题总库 → 科目 → 普通错题 / 考研真题错题 → 具体专题 → 错题。
    - 只保存题干 + 原图 + AI 分析结果；**不保存「我的答案」**（用户明确不要这个字段）。
    - 「先做题后看解析」：刷题接口默认只发题目，解析要用户点了「我做完了」之后再取详情。

AI 只做辅助：分类建议 + 错题分析。分类结果一律允许用户改。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from . import db
from .ai import prompts  # noqa: F401  （保留导入位，提示词写在本文件里）
from .ai.provider import AIError, get_provider, parse_json
from .config import settings

router = APIRouter(prefix="/api/math", tags=["math"])

# 未认领数据沿用 data/math_images/<user_id>/；账号数据存入各自的 users/<id>_<用户名> 目录。
# 数据库保存相对 data/ 的路径，移动项目时不依赖绝对路径。
MATH_IMG_DIR = settings.db_path.parent / "math_images"
_IMG_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_IMG_MAX_BYTES = 12 * 1024 * 1024
_DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", re.S)


# ============================================================
# 分类体系（计划书第五节）
# ============================================================
SUBJECTS = ["高等数学", "线性代数", "概率论与数理统计", "信号与系统"]

TOPICS = {
    "高等数学": [
        "函数与极限", "一元函数微分学", "一元函数积分学", "微分方程",
        "多元函数微分学", "重积分", "曲线与曲面积分", "无穷级数",
    ],
    "线性代数": [
        "行列式", "矩阵", "向量", "线性方程组", "特征值与特征向量", "二次型",
    ],
    "概率论与数理统计": [
        "随机事件与概率", "随机变量及其分布", "多维随机变量", "数字特征",
        "大数定律与中心极限定理", "数理统计",
    ],
    # 信号与系统（2026-10-08 加：专业课）。
    # ⚠️ 它和数学**共用同一套错题库引擎**（同一张表 / 同一条复习阶梯 / 同一条拍照录题链路），
    #    只是科目与知识点树不同 —— 不要复制出第二个模块，那会让复习、统计、归属隔离各养一套。
    "信号与系统": [
        "信号与系统基本概念", "连续系统的时域分析", "离散系统的时域分析",
        "傅里叶级数与傅里叶变换", "拉普拉斯变换与s域分析", "Z变换与z域分析",
        "系统函数与稳定性", "状态变量分析",
    ],
}

# 错题类型：普通错题 / 考研真题错题（计划书第六节要求明确分开）
KINDS = {"normal": "普通错题", "exam": "考研真题错题"}

# ============================================================
# 模块隔离（2026-10-08 用户明确要求）
# ------------------------------------------------------------
# 「数学一」和「信号与系统」是**两个入口**：谁的数据都不许漏到对方那边去 ——
# 数学页里不该看到信号题，复习提醒也各归各的（信号的到期数不许出现在数学的角标里）。
# 引擎仍然共用（同一张表、同一条复习阶梯），但**界面上每一次读写都带 module**，
# 查询层面就把另一边的科目挡掉。module="" 表示不过滤，只留给回归脚本。
# ============================================================
MODULES = {
    # 数学侧把「未分类」也算进来：历史数据里可能还有没选科目的题，
    # 不算进来它们会从两个入口里同时消失（数据没丢，但界面上看不见了）。
    "math": ["高等数学", "线性代数", "概率论与数理统计", ""],
    "sig": ["信号与系统"],
}

# ============================================================
# 公式分隔符兜底
# ------------------------------------------------------------
# 前端用 KaTeX 渲染 $...$。模型一般会照要求加 $，但**手打的题干**、以及偶发的
# 漏加，都会让公式退化成 \frac{...} 这种源码 —— 用户明确说「看不懂，要像课本那样」。
# 这里做一层**保守**兜底：只有整段一个 $ 都没有、且确实像 LaTeX 时才动手，
# 把「不含中文、且带命令/上下标/等号」的连续片段包上 $...$。
# 有 $ 就完全不动 —— 动它有把正常文本切坏的风险，收益远小于代价。
# ============================================================
_CJK_CHAR = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
_LATEX_HINT = re.compile(r"\\[a-zA-Z]{2,}|[\^_]\{|[\^_][0-9A-Za-z(]")
_LOOKS_MATH = re.compile(r"\\[a-zA-Z]{2,}|[\^_]\{|[\^_][0-9A-Za-z]|[=<>]")


def normalize_math_delims(text: str) -> str:
    """给没写 $ 的 LaTeX 补上定界符；已经有 $（或 \\( \\[ ）的一律原样返回。"""
    s = str(text or "")
    if not s or "$" in s or "\\(" in s or "\\[" in s:
        return s
    if not _LATEX_HINT.search(s):
        return s
    out: list[str] = []
    buf: list[str] = []
    depth = 0   # 花括号嵌套深度：中文可能落在 \text{其他} 里面，那时候**不能断**
                # （断了会变成 \text{$其他$}，嵌套 $ 会让 KaTeX 直接报错）

    def flush() -> None:
        if not buf:
            return
        seg = "".join(buf)
        buf.clear()
        core = seg.strip()
        out.append(f"${core}$" if core and _LOOKS_MATH.search(core) else seg)

    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if depth == 0 and _CJK_CHAR.match(ch):
            flush()
            out.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


# ============================================================
# 定期复习阶梯（计划书第十四节：3 / 7 / 14 / 30 天）
# ------------------------------------------------------------
# ⚠️ MASTERED_AT 一律 = 阶梯长度，不要另写一个数：
#    第 n 次做对用的是 INTERVALS[n-1]，硬编码 4 会和「已推到 30 天」对不上。
# ============================================================
REVIEW_INTERVALS = [3, 7, 14, 30, 60]
MASTERED_AT = len(REVIEW_INTERVALS)
_NOT_IN_CYCLE = "next_review IS NULL OR next_review=''"

# 复习动作（返回给前端的说明文案也由这里派生）
REVIEW_ACTIONS = {
    "right": "做对了",
    "wrong": "还是不会",
    "skip": "稍后再说",
    "drop": "不再复习",
    "readd": "重新加入",
}


# ============================================================
# 行 → 对象
# ============================================================
_ANALYSIS_FIELDS = ("knowledge", "key_steps", "solution", "alt_solution", "pitfalls")


def _row(row: dict | None, with_analysis: bool = True) -> dict | None:
    if not row:
        return None
    d = dict(row)
    try:
        steps = json.loads(d.get("key_steps") or "[]")
    except (TypeError, ValueError):
        steps = []
    d["key_steps"] = steps if isinstance(steps, list) else []
    d["kind_label"] = KINDS.get(d.get("kind") or "normal", "普通错题")
    d["in_cycle"] = bool(d.get("next_review"))
    d["analyzed"] = bool(d.get("analyzed_at"))
    d["has_image"] = bool(d.get("image_path"))
    d["mastered"] = (d.get("review_count") or 0) >= MASTERED_AT
    if not with_analysis:
        # 刷题阶段：只给题目，解析一律不发（防止前端手滑渲染出来）
        d["has_analysis"] = any(d.get(f) for f in _ANALYSIS_FIELDS)
        for f in _ANALYSIS_FIELDS:
            d[f] = "" if f != "key_steps" else []
    return d


# ============================================================
# 数据层
# ============================================================
def _get(pid: int) -> dict | None:
    """取**原始行**（只有表里的列）。

    ⚠️ 这里**不套 `_row()`**：数据库里的 `kind_label` / `in_cycle` / `mastered` /
    `analyzed` / `has_image` 这些是算出来的，原始行里没有。
    内部逻辑读的都是真列（stem / review_count / analyzed_at …），所以直接用没问题；
    要给前端返回完整形状，请写成 `_row(_get(pid))`。
    """
    w, a = scope_of()
    return db.query_one(f"SELECT * FROM math_problems WHERE id=? AND {w}", (int(pid), *a))


def scope_of():
    return db.scope()


# ============================================================
# 原题照片
# ------------------------------------------------------------
# 走 base64 JSON 而不是 multipart：现有工程刻意没引 python-multipart，
# 前端 `api()` 也是统一发 JSON，保持一致比多引一个依赖划算。
# ============================================================
def save_image(data_url: str, uid: int) -> str:
    """把 `data:image/...;base64,xxx` 存成文件，返回相对 data/ 的路径；失败返回空串。

    存不下来不报错是有意的：**题干才是必须的**，照片是附带证据。
    宁可少一张图，也不要因为图片格式不标准就把整条录题流程卡死。
    """
    m = _DATA_URL_RE.match((data_url or "").strip())
    if not m:
        return ""
    ext = _IMG_EXT.get(m.group(1).lower())
    if not ext:
        return ""
    try:
        blob = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        return ""
    if not blob or len(blob) > _IMG_MAX_BYTES:
        return ""
    name = f"{uuid.uuid4().hex}{ext}"
    if int(uid) > db.UNCLAIMED:
        d = db.user_data_dir(int(uid)) / "math_images"
        relative = db.user_data_dir(int(uid)).relative_to(settings.db_path.parent).as_posix()
        stored_path = f"{relative}/math_images/{name}"
    else:
        d = MATH_IMG_DIR / str(int(uid))
        stored_path = f"math_images/{int(uid)}/{name}"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(blob)
    return stored_path
def image_file(rel: str) -> Path | None:
    """把图片路径限制在当前用户的图片目录内，避免越权读取或目录穿越。"""
    if not rel:
        return None
    uid = db.current_user_id(required=False)
    if uid is not None and uid > db.UNCLAIMED:
        root = (db.user_data_dir(uid) / "math_images").resolve()
    else:
        root = MATH_IMG_DIR.resolve()
    p = (settings.db_path.parent / rel).resolve()
    if root not in p.parents:
        return None
    return p if p.is_file() else None


def create_problem(
    stem: str,
    *,
    subject: str = "",
    topic: str = "",
    kind: str = "normal",
    image_path: str = "",
    source_note: str = "",
    image_data: str = "",
) -> dict:
    uid = db.require_user()
    stem = normalize_math_delims((stem or "").strip())
    if not stem:
        raise ValueError("题干不能为空")
    if kind not in KINDS:
        kind = "normal"
    # 传了图片就顺手存下来（存不下来就拿调用方给的 image_path 兜底）
    if image_data:
        image_path = save_image(image_data, uid) or image_path
    # 首次复习 = 添加后 3 天（计划书第十四节的「第一次：3天后」）
    first = (date.today() + timedelta(days=REVIEW_INTERVALS[0])).isoformat()
    pid = db.execute(
        "INSERT INTO math_problems (user_id, subject, kind, topic, stem, image_path, "
        "source_note, review_count, next_review, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,0,?,datetime('now','localtime'),datetime('now','localtime'))",
        (uid, subject, kind, topic, stem, image_path, source_note, first),
    )
    db.log_event("math_add", pid, {"subject": subject, "topic": topic, "kind": kind})
    return _row(_get(pid))  # type: ignore[return-value]


_EDITABLE = {
    "stem", "subject", "kind", "topic", "image_path", "source_note",
    "knowledge", "solution", "alt_solution", "pitfalls",
}

# 这几个字段里可能带公式，写库前统一补一次 $ 定界符
_TEXT_FIELDS = {"stem", "knowledge", "solution", "alt_solution", "pitfalls"}


def update_problem(pid: int, payload: dict) -> dict | None:
    row = _get(pid)
    if not row:
        return None  # 不存在或不属于自己 → 上层回 404（不区分，避免泄露「存在但无权限」）
    sets, args = [], []
    for k, v in payload.items():
        if k == "key_steps":
            if isinstance(v, list):
                # 步骤里也常带公式，同样要补 $ 定界符（`_TEXT_FIELDS` 管不到这里）
                steps_v = [normalize_math_delims(str(x)) for x in v]
                sets.append("key_steps=?")
                args.append(json.dumps(steps_v, ensure_ascii=False))
            continue
        if k in _EDITABLE:
            if k == "kind" and v not in KINDS:
                continue
            val = "" if v is None else v
            if k in _TEXT_FIELDS and isinstance(val, str):
                val = normalize_math_delims(val)
            sets.append(f"{k}=?")
            args.append(val)
    # 用户手动改过分类/解析 → 标记为已分析（否则界面一直提示「AI 分析」）
    if any(k in payload for k in _ANALYSIS_FIELDS) and not row.get("analyzed_at"):
        sets.append("analyzed_at=datetime('now','localtime')")
    if not sets:
        return _row(row)
    sets.append("updated_at=datetime('now','localtime')")
    uid = db.require_user()
    db.execute(
        f"UPDATE math_problems SET {','.join(sets)} WHERE id=? AND user_id=?",
        (*args, int(pid), uid),
    )
    return _row(_get(pid))


def delete_problem(pid: int) -> bool:
    row = _get(pid)
    if not row:
        return False
    uid = db.require_user()
    db.execute("DELETE FROM math_review_log WHERE problem_id=? AND user_id=?", (int(pid), uid))
    db.execute("DELETE FROM math_problems WHERE id=? AND user_id=?", (int(pid), uid))
    db.log_event("math_delete", int(pid), None)
    return True


def _filters(subject="", topic="", kind="", q="", cycle="", module=""):
    w, a = scope_of()
    parts, args = [w], list(a)
    # 模块隔离：只看这个入口该看的科目（见 MODULES 的注释）
    subs = MODULES.get(module) or []
    if subs:
        parts.append(f"subject IN ({','.join('?' * len(subs))})")
        args += subs
    if subject:
        parts.append("subject=?")
        args.append(subject)
    if topic:
        parts.append("topic=?")
        args.append(topic)
    if kind in KINDS:
        parts.append("kind=?")
        args.append(kind)
    if q:
        # 关键词搜题干 + 知识点 + 解法（转义 LIKE 元字符）
        like = "%" + str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        parts.append(
            "(stem LIKE ? ESCAPE '\\' OR knowledge LIKE ? ESCAPE '\\' OR solution LIKE ? ESCAPE '\\')"
        )
        args += [like, like, like]
    if cycle == "due":
        parts.append(f"NOT ({_NOT_IN_CYCLE}) AND next_review<=?")
        args.append(db.today_str())
    elif cycle == "in":
        parts.append(f"NOT ({_NOT_IN_CYCLE})")
    elif cycle == "out":
        parts.append(f"({_NOT_IN_CYCLE})")
    elif cycle == "mastered":
        parts.append(f"NOT ({_NOT_IN_CYCLE}) AND review_count>=?")
        args.append(MASTERED_AT)
    return " AND ".join(parts), tuple(args)


def list_problems(**kw) -> list[dict]:
    limit = int(kw.pop("limit", 200) or 200)
    with_analysis = bool(kw.pop("with_analysis", False))
    where, args = _filters(**kw)
    rows = db.query(
        f"SELECT * FROM math_problems WHERE {where} ORDER BY id DESC LIMIT ?", (*args, limit)
    )
    return [_row(r, with_analysis) for r in rows]


def random_problem(**kw) -> dict | None:
    where, args = _filters(**kw)
    row = db.query_one(
        f"SELECT * FROM math_problems WHERE {where} ORDER BY RANDOM() LIMIT 1", args
    )
    return _row(row, with_analysis=False)


def stats(module: str = "") -> dict:
    w, a = scope_of()
    # 模块隔离：总量 / 到期 / 已掌握 / 按科目分布，全部只统计这个入口的科目
    subs = MODULES.get(module) or []
    if subs:
        w = f"{w} AND subject IN ({','.join('?' * len(subs))})"
        a = (*a, *subs)
    total = db.query_one(f"SELECT COUNT(*) c FROM math_problems WHERE {w}", a)
    due = db.query_one(
        f"SELECT COUNT(*) c FROM math_problems WHERE {w} AND NOT ({_NOT_IN_CYCLE}) "
        f"AND next_review<=?",
        (*a, db.today_str()),
    )
    mastered = db.query_one(
        f"SELECT COUNT(*) c FROM math_problems WHERE {w} AND NOT ({_NOT_IN_CYCLE}) "
        f"AND review_count>=?",
        (*a, MASTERED_AT),
    )
    by_subject = db.query(
        f"SELECT subject, COUNT(*) c FROM math_problems WHERE {w} GROUP BY subject", a
    )
    # 每个科目各自「今天该复习」的条数 —— 主界面的科目卡要各显各的，不能共用一个总数
    by_subject_due = db.query(
        f"SELECT subject, COUNT(*) c FROM math_problems WHERE {w} AND NOT ({_NOT_IN_CYCLE}) "
        f"AND next_review<=? GROUP BY subject",
        (*a, db.today_str()),
    )
    # 还留着原图的条数：前端拿它决定要不要给「清理原图」的入口。
    # 新录的题已经不留图了，这个数只会来自历史数据。
    with_image = db.query_one(
        f"SELECT COUNT(*) c FROM math_problems WHERE {w} "
        f"AND image_path IS NOT NULL AND image_path<>''",
        a,
    )
    return {
        "total": total["c"] if total else 0,
        "due": due["c"] if due else 0,
        "mastered": mastered["c"] if mastered else 0,
        "with_image": with_image["c"] if with_image else 0,
        "by_subject": {r["subject"] or "未分类": r["c"] for r in by_subject},
        "by_subject_due": {r["subject"] or "未分类": r["c"] for r in by_subject_due},
        "today": db.today_str(),
    }


def purge_images() -> dict:
    """删掉**当前账号**所有已存的原题照片（磁盘文件 + 库里的 image_path）。

    用户 2026-10-07 的要求：识图只为了拿到题干，原图没必要长期留着占地方。
    只动照片 —— 题干 / 解析 / 复习记录一律不碰。
    删文件失败不抛错：数据库指向清掉之后前端就不会再引用它，
    剩个孤儿文件顶多占点空间，不该为它让整个操作失败。
    """
    w, a = scope_of()
    rows = db.query(
        f"SELECT image_path FROM math_problems WHERE {w} "
        f"AND image_path IS NOT NULL AND image_path<>''",
        a,
    )
    removed, freed = 0, 0
    for r in rows:
        f = image_file(r.get("image_path") or "")
        if f:
            try:
                freed += f.stat().st_size
                f.unlink()
            except OSError:
                pass          # 文件已经不在 / 被占用：仍然把库里的指向清掉
        removed += 1
    db.execute(
        f"UPDATE math_problems SET image_path='', updated_at=datetime('now','localtime') "
        f"WHERE {w} AND image_path IS NOT NULL AND image_path<>''",
        a,
    )
    # 顺手收掉空目录（一人一个），非空目录 rmdir 会失败，忽略即可
    try:
        for d in sorted(MATH_IMG_DIR.glob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass
    except OSError:
        pass
    db.log_event("math_purge_images", 0, {"removed": removed, "bytes": freed})
    return {"ok": True, "removed": removed, "bytes": freed}


def apply_result(pid: int, result: str) -> dict | None:
    """推进一道错题的复习进度，并把这次重做记进 history（长期保存）。"""
    row = _get(pid)
    if not row:
        return None
    if result not in REVIEW_ACTIONS:
        raise ValueError(f"未知的复习结果：{result}")
    today = date.today()
    rc = row.get("review_count") or 0

    if result == "wrong":
        rc, nxt = 0, (today + timedelta(days=1)).isoformat()   # 做错 → 回队列，缩短间隔
    elif result == "right":
        rc = rc + 1
        nxt = (today + timedelta(days=REVIEW_INTERVALS[min(rc - 1, len(REVIEW_INTERVALS) - 1)])).isoformat()
    elif result == "skip":
        nxt = (today + timedelta(days=1)).isoformat()          # 进度不变
    elif result == "drop":
        nxt = ""                                               # 停止提醒；错题记录保留
    else:  # readd
        rc, nxt = 0, (today + timedelta(days=REVIEW_INTERVALS[0])).isoformat()

    uid = db.require_user()
    db.execute(
        "UPDATE math_problems SET review_count=?, next_review=?, last_result=?, "
        "last_review_at=datetime('now','localtime'), updated_at=datetime('now','localtime') "
        "WHERE id=? AND user_id=?",
        (rc, nxt, result, int(pid), uid),
    )
    db.execute(
        "INSERT INTO math_review_log (user_id, problem_id, result, review_count_after) "
        "VALUES (?,?,?,?)",
        (uid, int(pid), result, rc),
    )
    db.log_event(f"math_{result}", int(pid), {"count": rc, "next": nxt})
    return _row(_get(pid))


def review_log(pid: int) -> list[dict]:
    w, a = scope_of()
    return db.query(
        f"SELECT * FROM math_review_log WHERE problem_id=? AND {w} ORDER BY id DESC",
        (int(pid), *a),
    )


# ============================================================
# AI：分类建议 + 错题分析
# ============================================================
MATH_SYSTEM = """你是考研理科（数学一：高等数学 / 线性代数 / 概率论与数理统计；专业课：信号与系统）的资深讲授老师。
给你一道错题的题干，你要做两件事：判断它属于哪个科目与专题，以及写出这道题该怎么想、怎么解。

硬性要求：
1. 只依据题干本身分析。题干可能有抄录误差，但**不要凭空补条件、不要编造数据**。
2. 输出必须是**严格 JSON**，不要 markdown 代码围栏，不要任何解释性前后缀。
3. 禁止空话。以下这些一律不许出现：
   「注意审题」「结合条件分析」「灵活运用公式」「视具体情况而定」「多加练习」。
   每一条结论都要能落到**具体的式子、定理名、操作方法**上。
4. 解法要写清「第一步做什么、为什么这么做」，让人能照着复现，而不是给个结论。
5. subject 与 topic 只能从给定的清单里选，不要自创名词。
6. **所有数学式子一律写成 LaTeX，并用 $...$ 包起来**（独立成行的用 $$...$$）。
   前端会把它渲染成排版好的公式；漏掉 $ 就会把 \\frac 这种源码直接甩到用户脸上。"""


def build_math_prompt(stem: str, subject: str = "", topic: str = "") -> str:
    topics_txt = "\n".join(f"- {s}：{' / '.join(ts)}" for s, ts in TOPICS.items())
    hint = ""
    if subject or topic:
        hint = f"\n（用户自己填的线索：科目={subject or '未填'}，专题={topic or '未填'}；仅供参考，以你的判断为准）"
    return f"""题干：
{stem}
{hint}

可选的科目与专题清单：
{topics_txt}

请输出 JSON，字段与含义如下：
{{
  "subject": "必须是上面列出的科目之一",
  "topic": "必须是该科目下列出的专题之一",
  "kind": "exam 或 normal —— 判断它更像历年考研真题还是普通练习题；拿不准就给 normal",
  "knowledge": "本题考查的核心知识点（1~3 句，具体到方法/定理名）",
  "key_steps": ["最关键的第 1 步（写清做什么）", "第 2 步", "……"],
  "solution": "第一行必须是'答案：'加最终结论（能算出具体值就写具体值；证明/判断题写结论；题干条件不足就明确写无法确定），空一行后再写推荐解法的完整步骤",
  "alt_solution": "如果存在明显更快/更巧的另一种解法就写，没有就给空字符串",
  "pitfalls": "最容易错在哪、会错成什么样"
}}"""


# ============================================================
# AI：识图取题干（拍照录题）
# ------------------------------------------------------------
# 用户口径：**给一张题目照片，只要题干，别的都不要**。
# 实测：能识图的是 `deepseek-flash`；`deepseek-v4-pro` 和旧的 `deepseek-chat`
# 都看不了图（会回「无法读取图片」），换模型时别把这条弄丢。
# ============================================================
EXTRACT_SYSTEM = """你是考研理科错题库（数学 / 信号与系统）的录题助手。给你一张题目照片，你要做两件事。

【第一件：转录题干】
1. 只转录**题干**。答案、解析、「解：」后面的内容、讲解、页码页眉，一律不要。
2. 如果用户给照片加了蓝色圆圈，圆圈标出的题号就是唯一目标。只转录这个题号开始的完整题干（包含下方续行、选项），到下一道同级题号或新章节标题为止；忽略照片中其他题和原有笔迹。不要把蓝圈转录进题干。
   没有蓝圈时，才取最完整、位置最靠上的一道；绝对不要合并多道题。
3. **完整优先**：题干里的每一行、每一个条件、每一个小问「(1)(2)(3)」、每一个选项 (A)(B)(C)(D) 都要转录，一个都不许省。
   宁可把看不清的地方标成 □，也不许跳过整行或整段；同一个式子的上下标、根号上的指数、矩阵的每一行都要逐个核对。
4. 数学公式一律写成 LaTeX：行内用 $...$，独立成行的用 $$...$$。
   极限、积分、求和、矩阵、行列式、向量、分式都必须用 LaTeX 表达，不要用普通文字糊过去。
5. 照片里看不清的字符用 □ 占位。**绝对不要猜、不要替它补条件、不要改数字**。
6. 保留题号（如「7.」「(3)」），但不要额外加「题目：」这类前缀。
7. 不要输出任何解释、不要解题、不要给思路。

【输出格式的硬要求】
- 只输出一个 JSON 对象，前后不要任何说明文字。
- **不要**用 markdown 代码围栏（```）包起来，**不要**输出 HTML 标签（<br>、&nbsp;、<div> 等）。
- 公式只用 $...$ 与 $$...$$ 两种定界，**不要**用 \\( \\) 或 \\[ \\]。
- 每个 \\begin{...} 都必须有配对的 \\end{...}，不要写出没闭合的环境。

【第二件：给出分类建议】
按题意判断它属于哪个科目、哪个专题，以及更像历年考研真题还是普通练习。
这只是建议，用户后面能改，所以拿不准就照实说。

输出必须是**严格 JSON**，不要 markdown 代码围栏，不要任何解释性前后缀：
{
  "stem": "题干原文（含 LaTeX）",
  "subject": "必须是给定清单里的一个科目；判断不出来给空字符串",
  "topic": "必须是该科目下列出的专题之一；判断不出来给空字符串",
  "kind": "exam=历年考研真题 / normal=普通练习题；拿不准给 normal",
  "uncertain": ["转录时没把握的地方，例如『指数 2 与 3 分不清』；没有就给空数组"]
}"""


def clean_extracted_stem(text: str) -> str:
    """把模型转录结果清理成「能直接交给 KaTeX」的题干。

    为什么要在服务端再洗一遍（提示词里已经写了要求）：
      · 模型偶尔还是会把整体包进 ```json / ```latex 围栏；
      · 偶尔写成 \\( ... \\) / \\[ ... \\] 这种定界 —— 前端只认 $ 与 $$，
        不统一就会被当成普通文字，用户看到的正是「题目里带着数学代码」；
      · 偶尔混进 <br>、&nbsp;、**加粗** 这类标记，KaTeX 与题干都不需要它们。
    这里只做**无损清理**：不猜内容、不改数字，洗不掉的东西原样留着。
    """
    s = str(text or "").replace("\r\n", "\n").strip()
    if not s:
        return ""
    # 1) 去掉整体/局部的 markdown 代码围栏
    s = re.sub(r"```[a-zA-Z]*\s*", "", s)
    # 2) 统一公式定界：\( \) → $ $，\[ \] → $$ $$
    s = re.sub(r"\\\[(.*?)\\\]", lambda m: "$$" + m.group(1).strip() + "$$", s, flags=re.S)
    s = re.sub(r"\\\((.*?)\\\)", lambda m: "$" + m.group(1).strip() + "$", s, flags=re.S)
    # 3) 去掉 HTML 标记与常见实体（题干不需要它们）
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</?(div|p|span|b|i|em|strong|ul|ol|li)[^>]*>", "", s, flags=re.I)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    # 4) 去掉 markdown 加粗/斜体标记（保留 * 号本身可能出现在数学里，所以只处理成对的）
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    # 5) 没闭合的 \begin{env}：直接删掉这个半截命令，别让 KaTeX 整个式子报错
    opens = re.findall(r"\\begin\{([a-zA-Z*]+)\}", s)
    closes = re.findall(r"\\end\{([a-zA-Z*]+)\}", s)
    for env in set(opens):
        if opens.count(env) != closes.count(env):
            s = s.replace("\\begin{%s}" % env, "")
    # 6) 连续空行压一压
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def build_extract_prompt(hint_subject: str = "", hint_topic: str = "", marker: dict | None = None) -> str:
    topics_txt = "\n".join(f"- {s}：{' / '.join(ts)}" for s, ts in TOPICS.items())
    hint = ""
    if hint_subject or hint_topic:
        hint = (
            f"\n用户自己填的线索：科目={hint_subject or '未填'}，专题={hint_topic or '未填'}"
            "（仅供参考，以照片为准）"
        )
    if marker:
        hint += (
            f"\n用户在整图约横向 {marker['x'] * 100:.0f}%、纵向 {marker['y'] * 100:.0f}% "
            "的位置给目标题号画了蓝圈。只识别蓝圈题号对应的一道题；"
            "蓝圈之外的其他题不要录入。若圈中的题号仍无法确定，在 uncertain 说明，不能换录别题。"
        )
    return f"""请转录这张照片里的题干，并给出分类建议。{hint}

可选的科目与专题清单（subject / topic 只能从这里选）：
{topics_txt}"""


def extract_from_image(image_data: str, *, hint_subject: str = "", hint_topic: str = "",
                       marker: dict | None = None) -> dict:
    """照片 → 题干 + 分类建议。AI 不可用 / 失败抛 AIError，由上层转成人话提示。"""
    raw = get_provider().chat_vision(
        EXTRACT_SYSTEM,
        build_extract_prompt(hint_subject, hint_topic, marker),
        image_data,
        temperature=0.1,   # 转录任务不需要发挥，越低越忠实
        max_tokens=6000,   # 推理模型会先吃思考额度，给足
        retries=1,
    )
    try:
        d = parse_json(raw)
    except AIError as exc:
        raise AIError(f"识图结果不是合法 JSON：{exc}") from exc
    if not isinstance(d, dict):
        raise AIError("识图返回的 JSON 不是对象")

    stem = normalize_math_delims(clean_extracted_stem(str(d.get("stem") or "")))
    if not stem:
        raise AIError("没能从这张照片里认出题干文字。换一张更清晰、只拍题干的照片再试")

    subject = str(d.get("subject") or "").strip()
    topic = str(d.get("topic") or "").strip()
    # 清单外的名词一律丢弃：宁可留空让用户自己选，也不往库里塞自创分类
    if subject not in SUBJECTS:
        subject = ""
    if topic not in TOPICS.get(subject, []):
        topic = ""
    kind = str(d.get("kind") or "").strip()
    if kind not in KINDS:
        kind = "normal"

    raw_unc = d.get("uncertain") or []
    if not isinstance(raw_unc, list):
        raw_unc = [raw_unc]
    uncertain = [str(x).strip() for x in raw_unc if str(x).strip()][:5]

    return {
        "stem": stem,
        "subject": subject,
        "topic": topic,
        "kind": kind,
        "uncertain": uncertain,
        "model": getattr(get_provider(), "model", ""),
    }


def analyze(pid: int, *, use_ai: bool = True) -> dict | None:
    """对一道错题做 AI 分析并落库。AI 不可用/失败时抛 AIError，由上层给出人话提示。"""
    row = _get(pid)
    if not row:
        return None
    if not use_ai:
        return _row(row)
    raw = get_provider().chat(
        MATH_SYSTEM,
        build_math_prompt(row["stem"], row.get("subject") or "", row.get("topic") or ""),
        temperature=0.3,
        max_tokens=6000,
    )
    try:
        d = parse_json(raw)
    except AIError as exc:
        raise AIError(f"模型返回的内容不是合法 JSON：{exc}") from exc
    if not isinstance(d, dict):
        raise AIError("模型返回的 JSON 不是对象")

    subject = str(d.get("subject") or "").strip()
    topic = str(d.get("topic") or "").strip()
    # 清单外的科目/专题一律丢弃，宁可留空让用户自己选，也不要往库里塞自创名词
    if subject not in SUBJECTS:
        subject = row.get("subject") or ""
    if topic not in TOPICS.get(subject, []):
        topic = ""

    kind = str(d.get("kind") or "").strip()
    if kind not in KINDS:
        kind = row.get("kind") or "normal"

    steps = d.get("key_steps") or []
    if not isinstance(steps, list):
        steps = [str(steps)]
    steps = [normalize_math_delims(str(s).strip()) for s in steps if str(s).strip()][:8]

    uid = db.require_user()
    db.execute(
        "UPDATE math_problems SET subject=?, topic=?, kind=?, knowledge=?, key_steps=?, "
        "solution=?, alt_solution=?, pitfalls=?, ai_model=?, "
        "analyzed_at=datetime('now','localtime'), updated_at=datetime('now','localtime') "
        "WHERE id=? AND user_id=?",
        (
            subject, topic, kind,
            normalize_math_delims(str(d.get("knowledge") or "").strip()),
            json.dumps(steps, ensure_ascii=False),
            normalize_math_delims(str(d.get("solution") or "").strip()),
            normalize_math_delims(str(d.get("alt_solution") or "").strip()),
            normalize_math_delims(str(d.get("pitfalls") or "").strip()),
            getattr(get_provider(), "model", ""),
            int(pid), uid,
        ),
    )
    db.log_event("math_analyze", int(pid), {"subject": subject, "topic": topic})
    return _row(_get(pid))


# ============================================================
# 接口
# ============================================================
@router.get("/meta")
def api_meta(module: str = ""):
    return {
        "module": module,
        "module_subjects": MODULES.get(module) or SUBJECTS,   # 这个入口能选的科目
        "modules": MODULES,
        "subjects": SUBJECTS,
        "topics": TOPICS,
        "kinds": KINDS,
        "actions": REVIEW_ACTIONS,
        "intervals": REVIEW_INTERVALS,
        "mastered_at": MASTERED_AT,
        "stats": stats(module),
    }


@router.get("/problems")
def api_list(
    subject: str = "", topic: str = "", kind: str = "",
    q: str = "", cycle: str = "", module: str = "",
    limit: int = 200, with_analysis: int = 1,
):
    return {
        "items": list_problems(
            subject=subject, topic=topic, kind=kind, q=q, cycle=cycle, module=module,
            limit=limit, with_analysis=bool(int(with_analysis)),
        )
    }


@router.get("/problems/{pid}")
def api_detail(pid: int):
    row = _row(_get(pid))
    if not row:
        raise HTTPException(404, "错题不存在")
    row["review_log"] = review_log(pid)
    return row


@router.post("/extract")
def api_extract(payload: dict = Body(...)):
    """拍照录题：把图片交给识图模型，只取回题干（附带分类建议）。"""
    img = (payload.get("image") or "").strip()
    if not img:
        raise HTTPException(400, "没有收到图片")
    if not _DATA_URL_RE.match(img):
        raise HTTPException(400, "图片格式不对（需要 data:image/...;base64,... 形式）")
    marker = payload.get("marker")
    if marker is not None:
        try:
            marker = {"x": float(marker["x"]), "y": float(marker["y"])}
            if not all(0 <= value <= 1 for value in marker.values()):
                raise ValueError
        except (TypeError, KeyError, ValueError):
            raise HTTPException(400, "题号标记位置无效") from None
    try:
        d = extract_from_image(
            img,
            hint_subject=(payload.get("subject") or "").strip(),
            hint_topic=(payload.get("topic") or "").strip(),
            marker=marker,
        )
    except AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, **d}


@router.post("/images/purge")
def api_purge_images():
    """清掉本账号所有已存的原图（录题流程本身已经不留图了）。"""
    return purge_images()


@router.post("/problems")
def api_create(payload: dict = Body(...)):
    try:
        row = create_problem(
            payload.get("stem") or "",
            subject=(payload.get("subject") or "").strip(),
            topic=(payload.get("topic") or "").strip(),
            kind=(payload.get("kind") or "normal").strip(),
            image_path=(payload.get("image_path") or "").strip(),
            source_note=(payload.get("source_note") or "").strip(),
            image_data=(payload.get("image") or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "problem": row}


@router.get("/problems/{pid}/image")
def api_image(pid: int):
    """原题照片。归属不对一律 404（不区分「不存在」和「不是你的」，避免泄露）。"""
    row = _get(pid)
    if not row:
        raise HTTPException(404, "错题不存在")
    f = image_file(row.get("image_path") or "")
    if not f:
        raise HTTPException(404, "这道题没有原图")
    return FileResponse(str(f))


@router.patch("/problems/{pid}")
def api_update(pid: int, payload: dict = Body(...)):
    row = update_problem(pid, payload or {})
    if not row:
        raise HTTPException(404, "错题不存在")
    return {"ok": True, "problem": row}


@router.delete("/problems/{pid}")
def api_delete(pid: int):
    if not delete_problem(pid):
        raise HTTPException(404, "错题不存在")
    return {"ok": True}


@router.post("/problems/{pid}/analyze")
def api_analyze(pid: int):
    try:
        row = analyze(pid)
    except AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not row:
        raise HTTPException(404, "错题不存在")
    return {"ok": True, "problem": row}


@router.post("/problems/{pid}/result")
def api_result(pid: int, payload: dict = Body(...)):
    try:
        row = apply_result(pid, (payload.get("result") or "").strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not row:
        raise HTTPException(404, "错题不存在")
    return {"ok": True, "problem": row}


@router.get("/random")
def api_random(subject: str = "", topic: str = "", kind: str = "", cycle: str = "",
               module: str = ""):
    row = random_problem(subject=subject, topic=topic, kind=kind, cycle=cycle, module=module)
    if not row:
        return {"ok": True, "problem": None}
    return {"ok": True, "problem": row}


@router.get("/review/due")
def api_due(limit: int = 100, module: str = ""):
    return {"items": list_problems(cycle="due", limit=limit, module=module, with_analysis=False)}


@router.get("/stats")
def api_stats(module: str = ""):
    return stats(module)
