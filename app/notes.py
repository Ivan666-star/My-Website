"""阅读笔记 + 语法积累库 + 它们的复现复习。

用户 2026-10-11 的原话（这四条决定本文件怎么写）：
    2. 笔的功能修改为「划过所选的句子或单词，用颜色填充」
    3. 填充的单词或短语自动识别保留进阅读的笔记中，笔记单独是一个内容，用于存放我的阅读笔记
    7. 还应有单独的语法积累库
    8. 总而言之就是这些东西不能只是这次看了就过去了，要能够不断复现记住才行

所以：
  · 高亮（划选填充）**自动**生成一条笔记 —— 不需要用户再点一次「保存」；
  · 笔记是**独立**的一份数据（`reading_notes` 表），有自己的库页面；
  · 语法点单独攒在 `favorites` 的 grammar 库里，**只供随时查阅，不进每日复习**
    （用户 2026-10-14：复习提醒只针对词汇和表达，长难句和语法先不管）；
  · 笔记**收藏即入复习节奏**：next_review = 次日，和素材库同一套
    1 → 2 → 4 → 7 → 15 → 30 → 60 天阶梯（间隔常量在 db.py，别在这里另写一份）。
    笔记记的是划选出来的词和短语，属于「表达」，所以照旧进复习。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import db

# 与 db.REVIEW_INTERVALS / db.MASTERED_AT 保持同一套阶梯
_NOT_IN_CYCLE = "next_review IS NULL OR next_review=''"
KINDS = ("vocab", "phrase", "sentence", "other")
COLORS = ("yellow", "green", "blue", "pink")


def _load_tags(rows: list[dict]) -> list[dict]:
    for r in rows:
        try:
            r["tags"] = json.loads(r.get("tags") or "[]")
        except Exception:  # noqa: BLE001
            r["tags"] = []
    return rows


# ------------------------------------------------------------
# 阅读笔记
# ------------------------------------------------------------
def add_note(
    content: str,
    *,
    cn: str = "",
    kind: str = "vocab",
    color: str = "yellow",
    context: str = "",
    note: str = "",
    article_id: int | None = None,
    doc_date: str = "",
    para_index: int | None = None,
    char_start: int | None = None,
) -> dict:
    """高亮一段原文 → 落一条笔记（同篇同内容同类型只留一条，不重复堆）。

    返回的 dict 里带 `created` 标记：前端据此决定提示「已记入阅读笔记」
    还是「这条已经在笔记里了」。
    """
    text = (content or "").strip()
    if not text:
        raise ValueError("选中的内容为空")
    k = kind if kind in KINDS else "vocab"
    c = color if color in COLORS else "yellow"
    uid = db.require_user()

    exist = db.query_one(
        "SELECT * FROM reading_notes WHERE user_id=? AND COALESCE(article_id,0)=COALESCE(?,0) "
        "AND content=? AND kind=?",
        (uid, article_id, text, k),
    )
    if exist:
        # 同一条再次划到：只更新颜色/释义，不新建、不动复习进度
        db.execute(
            "UPDATE reading_notes SET color=?, cn=CASE WHEN ?<>'' THEN ? ELSE cn END, "
            "updated_at=datetime('now','localtime') WHERE id=? AND user_id=?",
            (c, cn or "", cn or "", exist["id"], uid),
        )
        row = db.query_one("SELECT * FROM reading_notes WHERE id=?", (exist["id"],))
        return {**(row or exist), "created": False}

    nid = db.execute(
        """INSERT INTO reading_notes(user_id,article_id,doc_date,kind,content,cn,note,color,context,
                                     para_index,char_start,
                                     created_at,updated_at,review_count,next_review)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),datetime('now','localtime'),
                  0,date('now','localtime','+1 day'))""",
        (uid, article_id, doc_date or "", k, text, cn or "", note or "", c, context or "",
         para_index, char_start),
    )
    db.log_event("note_added", nid, {"kind": k, "color": c, "len": len(text)})
    row = db.query_one("SELECT * FROM reading_notes WHERE id=?", (nid,))
    return {**(row or {}), "created": True}


def list_notes(limit: int = 300, *, kind: str | None = None, q: str = "") -> list[dict]:
    w, a = db.scope()
    sql = f"SELECT * FROM reading_notes WHERE {w}"
    args: list = list(a)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if q:
        sql += " AND (content LIKE ? OR cn LIKE ? OR note LIKE ? OR context LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY COALESCE(updated_at,created_at) DESC, id DESC LIMIT ?"
    args.append(limit)
    return _load_tags(db.query(sql, tuple(args)))


def update_note(nid: int, *, cn: str | None = None, note: str | None = None,
                color: str | None = None) -> dict | None:
    w, a = db.scope()
    row = db.query_one(f"SELECT * FROM reading_notes WHERE id=? AND {w}", (int(nid), *a))
    if not row:
        return None
    sets, args = [], []
    if cn is not None:
        sets.append("cn=?"); args.append(cn)
    if note is not None:
        sets.append("note=?"); args.append(note)
    if color is not None and color in COLORS:
        sets.append("color=?"); args.append(color)
    if sets:
        sets.append("updated_at=datetime('now','localtime')")
        db.execute(f"UPDATE reading_notes SET {', '.join(sets)} WHERE id=? AND {w}", (*args, int(nid), *a))
    return db.query_one(f"SELECT * FROM reading_notes WHERE id=? AND {w}", (int(nid), *a))


def delete_note(nid: int) -> bool:
    w, a = db.scope()
    row = db.query_one(f"SELECT id FROM reading_notes WHERE id=? AND {w}", (int(nid), *a))
    if not row:
        return False
    db.execute(f"DELETE FROM reading_notes WHERE id=? AND {w}", (int(nid), *a))
    return True


def notes_for_article(article_id: int) -> list[dict]:
    """某篇文章/真题上有哪些高亮笔记 —— 阅读页要把高亮重新画回正文用。"""
    w, a = db.scope()
    return _load_tags(db.query(
        f"SELECT * FROM reading_notes WHERE {w} AND COALESCE(article_id,0)=? ORDER BY id",
        (*a, int(article_id)),
    ))


def notes_stats() -> dict:
    w, a = db.scope()
    today = db.today_str()
    due = db.query_one(
        f"SELECT COUNT(*) c FROM reading_notes WHERE {w} AND NOT ({_NOT_IN_CYCLE}) AND next_review<=?",
        (*a, today))
    in_cycle = db.query_one(
        f"SELECT COUNT(*) c FROM reading_notes WHERE {w} AND NOT ({_NOT_IN_CYCLE})", a)
    mastered = db.query_one(
        f"SELECT COUNT(*) c FROM reading_notes WHERE {w} AND NOT ({_NOT_IN_CYCLE}) AND review_count>=?",
        (*a, db.MASTERED_AT))
    total = db.query_one(f"SELECT COUNT(*) c FROM reading_notes WHERE {w}", a)
    return {
        "due": due["c"] if due else 0,
        "in_cycle": in_cycle["c"] if in_cycle else 0,
        "mastered": mastered["c"] if mastered else 0,
        "total": total["c"] if total else 0,
    }


def notes_review_due(limit: int = 200) -> list[dict]:
    """今天该复习的笔记，包成复习卡片同一种形状（多带一个 src=note）。"""
    w, a = db.scope()
    rows = db.query(
        f"SELECT * FROM reading_notes WHERE {w} AND NOT ({_NOT_IN_CYCLE}) AND next_review<=? "
        f"ORDER BY next_review ASC, id ASC LIMIT ?",
        (*a, db.today_str(), limit),
    )
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "src": "note",
            "content": r["content"],
            "cn": r.get("cn") or "",
            "example": r.get("context") or "",
            "usage": "阅读笔记 · " + (r.get("doc_date") or ""),
            "tags": ["阅读笔记"],
            "review_count": r.get("review_count") or 0,
            "next_review": r.get("next_review") or "",
            "position": "universal",
            "genre": "",
            "level": "B",
            "kind": r.get("kind") or "vocab",
        })
    return out


def notes_apply_review(nid: int, result: str) -> dict | None:
    """推进一条笔记的复习进度（阶梯与素材库完全一致，见 db.REVIEW_INTERVALS）。"""
    w, a = db.scope()
    row = db.query_one(f"SELECT * FROM reading_notes WHERE id=? AND {w}", (int(nid), *a))
    if not row:
        return None
    today = date.today()
    rc = row.get("review_count") or 0
    if result == "forgot":
        rc, nxt = 0, (today + timedelta(days=1)).isoformat()
    elif result == "known":
        rc = rc + 1
        interval = db.REVIEW_INTERVALS[min(rc - 1, len(db.REVIEW_INTERVALS) - 1)]
        nxt = (today + timedelta(days=interval)).isoformat()
    elif result == "skip":
        nxt = (today + timedelta(days=1)).isoformat()
    elif result == "drop":
        nxt = ""
    elif result == "readd":
        rc, nxt = 0, (today + timedelta(days=1)).isoformat()
    else:
        raise ValueError(f"未知的复习结果：{result}")
    db.execute(
        "UPDATE reading_notes SET review_count=?, next_review=?, last_result=?, "
        "updated_at=datetime('now','localtime') WHERE id=? AND user_id=?",
        (rc, nxt, result, int(nid), db.require_user()),
    )
    db.log_event(f"note_review_{result}", int(nid), {"count": rc, "next": nxt})
    return db.query_one(f"SELECT * FROM reading_notes WHERE id=? AND {w}", (int(nid), *a))


# ------------------------------------------------------------
# 语法积累库（跨文章攒语法点，走 favorites 的 grammar 库；只查阅，不进每日复习）
# ------------------------------------------------------------
def save_grammar_point(
    name: str,
    *,
    explanation: str = "",
    pattern: str = "",
    example: str = "",
    category: str = "",
    article_id: int | None = None,
    doc_date: str = "",
) -> dict:
    """把一个语法点收进语法积累库。同名同来源只收一次。

    next_review 故意留空：语法属于「闲时精读」，不进每日复习队列
    （db._NOT_IN_REVIEW 也会把 library='grammar' 挡在复习之外，这里是双保险）。
    """
    title = (name or "").strip()
    if not title:
        raise ValueError("语法点名称为空")
    uid = db.require_user()
    exist = db.query_one(
        "SELECT * FROM favorites WHERE user_id=? AND library='grammar' AND content=?",
        (uid, title),
    )
    if exist:
        return {**exist, "created": False}
    fid = db.execute(
        """INSERT INTO favorites(user_id,item_type,content,cn,example,tags,source_article_id,
                                 library,category,position,genre,function,level,usage,
                                 auto,confidence,updated_at,review_count,next_review)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),0,'')""",
        (
            uid, "grammar", title,
            explanation or pattern or "",
            example or "",
            json.dumps(["语法", category or "句法", doc_date or ""], ensure_ascii=False),
            article_id,
            "grammar", category or "句法", "universal", "universal", "grammar", "A",
            pattern or "",
            0, 0.0,
        ),
    )
    db.log_event("grammar_saved", fid, {"name": title, "category": category or ""})
    row = db.query_one("SELECT * FROM favorites WHERE id=?", (fid,))
    return {**(row or {}), "created": True}


def grammar_library(limit: int = 300) -> list[dict]:
    return _load_tags(db.favorites_all(limit=limit, library="grammar"))