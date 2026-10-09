"""FastAPI 应用：路由 + 静态页面 + 启动初始化。

多用户相关约定（改这个文件前先看）：
    - 谁在请求、他的 user_id 是多少，全部由 `auth.AuthGate` 中间件在处理前放进
      `db` 的上下文里。**路由函数里不要再自己传 user_id 参数**，直接用
      `db.get_article_by_date()` 这类封装好的函数即可 —— 它们自带归属过滤。
    - 需要写原始 SQL 时，务必带上 `scope()` 给的 WHERE 片段，
      否则就是在跨用户读写（最容易出、也最不能接受的 bug）。
    - `auth.current_user(request)` 拿当前登录者；返回 None 表示「还没启用账号体系」。
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, scheduler, seed, users
from .ai.pipeline import (
    AIError,
    analyze_exam_paper,
    analyze_real_article,
    classify_material,
    decide_difficulty,
    effective_base_difficulty,
    effective_stage,
    generate_daily,
    grade_submission,
    label_materials,
    rule_fill_blank_genres,
    save_material,
    story_bundle,
)
from .config import (
    BASE_DIR,
    DIFFICULTY_LABEL,
    EXAM_SLOTS,
    MATERIAL_CATEGORIES,
    MATERIAL_FUNCTIONS,
    MATERIAL_GENRE_HINT,
    MATERIAL_GENRES,
    MATERIAL_GENRES_ORDER,
    MATERIAL_LIBRARIES,
    MATERIAL_LIBRARY_DESC,
    MATERIAL_LEVELS,
    MATERIAL_POSITIONS,
    MATERIAL_POSITIONS_ORDER,
    MATERIAL_POSITION_USAGE,
    settings,
)
from .ai.provider import reset_provider
from .math import router as math_router

WEB_DIR = BASE_DIR / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # 还没有账号时，准备好「创建第一个账号」要用的初始化码
    if not users.has_users():
        auth.ensure_setup_code()
    # 内置示例内容只在「整个库一篇都没有」时灌；写入走未认领桶，
    # 第一个账号建好时会被 claim_orphan_data 一起接管过去。
    try:
        with db.as_local():
            n = seed.ensure_seed()
        if n:
            print(f"[startup] 已写入 {n} 篇内置示例内容")
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] 示例内容跳过：{exc}")
    # 结构升级后补齐历史数据的「文体」标签（纯规则、幂等、不调模型）
    try:
        filled = rule_fill_blank_genres()
        if filled:
            print(f"[startup] 已为 {filled} 条历史素材补全文体标签")
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] 文体回填跳过：{exc}")
    task = scheduler.start_scheduler()
    yield
    if task:
        task.cancel()


app = FastAPI(title="Prism", version="1.0.0", lifespan=lifespan)

# 这道门同时负责三件事：拦未登录、设用户上下文、在还没建账号时保持原来的行为
app.add_middleware(auth.AuthGate)

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# 数学一错题库（app/math.py）。它自带 /api/math 前缀，且**不在** auth.AuthGate 的
# 放行名单里 —— 也就是必须登录才能碰，和英语模块的隔离要求一致。
app.include_router(math_router)


# ============================================================
# 登录 / 初始化 / 退出
# ============================================================
def _auth_payload(request: Request) -> dict:
    m = auth.mode()
    u = auth.current_user(request)
    return {
        "mode": m,
        "username": u["username"] if u else "",
        "display_name": ((u.get("display_name") or u["username"]) if u else ""),
        "is_admin": bool(u and u.get("is_admin")),
        "user_count": users.count_approved(),
        "pending_count": users.count_pending() if (u and u.get("is_admin")) else 0,
        "can_setup": m != auth.MODE_ACCOUNTS,
        "setup_code_needed": (
            auth.setup_code_needed(request) if m != auth.MODE_ACCOUNTS else False
        ),
        "password_gate": bool(settings.access_password),
    }


def _me(request: Request) -> dict | None:
    return auth.current_user(request)


def _require_admin(request: Request) -> dict:
    u = _me(request)
    if not u:
        raise HTTPException(401, "需要登录")
    if not u.get("is_admin"):
        raise HTTPException(403, "只有管理员能做这件事")
    return u


@app.get("/login")
def login_form(request: Request, next: str = "/"):
    if auth.mode() == auth.MODE_OPEN:
        return RedirectResponse("/", status_code=303)
    if auth.current_user(request):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return HTMLResponse(auth.login_page_html(nxt=next))


@app.post("/login")
async def login_submit(request: Request):
    if auth.mode() == auth.MODE_OPEN:
        return RedirectResponse("/", status_code=303)
    return await auth.handle_login(request)


@app.get("/setup")
def setup_form(request: Request):
    if auth.mode() == auth.MODE_ACCOUNTS:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(
        auth.setup_page_html(
            need_code=auth.setup_code_needed(request),
            claimed=users.orphan_counts(),
        )
    )


@app.post("/setup")
async def setup_submit(request: Request):
    return await auth.handle_setup(request)


@app.get("/register")
def register_form(request: Request, next: str = "/"):
    # 自助注册：任何模式下都能进。建出来的是「待批准」账号，
    # 所以不会把系统从自用模式切成账号模式，也就不会把自己锁在外面。
    if not auth.register_enabled():
        return HTMLResponse(auth.register_page_html(enabled=False), status_code=403)
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth.register_page_html(nxt=next, m=auth.mode()))


@app.post("/register")
async def register_submit(request: Request):
    return await auth.handle_register(request)


@app.get("/logout")
async def logout(request: Request):
    return await auth.handle_logout(request)


# ============================================================
# 页面
# ============================================================
@app.get("/")
def index():
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(str(WEB_DIR / "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(str(WEB_DIR / "sw.js"), media_type="application/javascript")


@app.get("/favicon.ico")
def favicon():
    # 浏览器即使看到页面里的 <link rel="icon">，也会再直接请求一次根路径的 /favicon.ico。
    # 这里必须真的返回文件，否则标签页上会是空白图标。
    return FileResponse(str(WEB_DIR / "favicon.ico"), media_type="image/x-icon")


# ============================================================
# 我的账号
# ============================================================
@app.get("/api/me")
def api_me(request: Request):
    return _auth_payload(request)


@app.post("/api/me/password")
def api_change_my_password(request: Request, payload: dict = Body(...)):
    u = _me(request)
    if not u:
        raise HTTPException(400, "当前没有启用账号体系")
    old = payload.get("old") or ""
    new = payload.get("new") or ""
    if not users.verify_password(u, old):
        raise HTTPException(400, "原密码不对")
    try:
        users.set_password(u["id"], new)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 改密会把所有设备踢下线（含当前这台），所以提示前端重新登录
    return {"ok": True, "relogin": True}


@app.post("/api/me/profile")
def api_update_my_profile(request: Request, payload: dict = Body(...)):
    u = _me(request)
    if not u:
        raise HTTPException(400, "当前没有启用账号体系")
    if "display_name" in payload:
        users.set_display_name(u["id"], str(payload.get("display_name") or ""))
    return {"ok": True, "me": users.public_user(users.get_user(u["id"]))}


# ============================================================
# 用户管理（管理员）
# ============================================================
@app.get("/api/admin/users")
def api_admin_users(request: Request):
    _require_admin(request)
    return {
        "items": users.list_users(),
        "active_count": _active_user_count(),
        "pending_count": users.count_pending(),
        "self_register": auth.register_enabled(),
    }


def _active_user_count() -> int:
    row = db.query_one("SELECT COUNT(*) c FROM users WHERE active=1 AND is_admin=1")
    return int(row["c"]) if row else 0


@app.post("/api/admin/users")
def api_admin_create_user(request: Request, payload: dict = Body(...)):
    _require_admin(request)
    try:
        u = users.create_user(
            payload.get("username") or "",
            payload.get("password") or "",
            display_name=payload.get("display_name") or "",
            is_admin=bool(payload.get("is_admin")),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": u}


@app.post("/api/admin/users/{uid}/password")
def api_admin_set_password(uid: int, request: Request, payload: dict = Body(...)):
    _require_admin(request)
    target = users.get_user(uid)
    if not target:
        raise HTTPException(404, "账号不存在")
    try:
        users.set_password(uid, payload.get("password") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 改完密码这个人的所有设备都会掉线，这是有意为之
    return {"ok": True, "sessions_revoked": True}


@app.post("/api/admin/users/{uid}/active")
def api_admin_set_active(uid: int, request: Request, payload: dict = Body(...)):
    me = _require_admin(request)
    target = users.get_user(uid)
    if not target:
        raise HTTPException(404, "账号不存在")
    want = bool(payload.get("active"))
    if not want:
        if uid == me["id"]:
            raise HTTPException(400, "不能停用自己当前登录的账号")
        if target.get("is_admin") and _active_user_count() <= 1:
            raise HTTPException(400, "这是最后一个可用的管理员，停用后就没人能管理了")
    users.set_active(uid, want)
    return {"ok": True, "active": want}


@app.post("/api/admin/users/{uid}/approve")
def api_admin_approve(uid: int, request: Request, payload: dict = Body(...)):
    """批准（或撤回批准）一个自助申请的账号。"""
    _require_admin(request)
    target = users.get_user(uid)
    if not target:
        raise HTTPException(404, "账号不存在")
    want = bool(payload.get("approved"))
    if want and not target.get("active"):
        # 批准一个停用中的账号，顺手启用，否则批准了他还是进不来
        users.set_active(uid, True)
    users.set_approved(uid, want)
    return {"ok": True, "approved": want}


@app.delete("/api/admin/users/{uid}")
def api_admin_delete_user(uid: int, request: Request):
    """删除账号 —— 只用来收拾「还没批准、也没任何数据」的空账号。

    已经批准过的账号里有这个人的全部学习数据，不该因为点错就消失，
    要让他别来用就用「停用」。
    """
    me = _require_admin(request)
    target = users.get_user(uid)
    if not target:
        raise HTTPException(404, "账号不存在")
    if uid == me["id"]:
        raise HTTPException(400, "不能删除自己当前登录的账号")
    if target.get("approved", 1):
        raise HTTPException(400, "这个账号已经批准过，可能已有学习数据，不能删除；要停用请用「停用」")
    if target.get("is_admin"):
        raise HTTPException(400, "不能删除管理员账号")
    users.delete_user(uid)
    return {"ok": True, "deleted": uid}


@app.post("/api/admin/users/{uid}/admin")
def api_admin_set_admin(uid: int, request: Request, payload: dict = Body(...)):
    me = _require_admin(request)
    if uid == me["id"] and not bool(payload.get("is_admin")):
        raise HTTPException(400, "不能取消自己的管理员身份")
    if not users.get_user(uid):
        raise HTTPException(404, "账号不存在")
    users.set_admin(uid, bool(payload.get("is_admin")))
    return {"ok": True}


@app.post("/api/admin/users/{uid}/display_name")
def api_admin_set_display_name(uid: int, request: Request, payload: dict = Body(...)):
    _require_admin(request)
    if not users.get_user(uid):
        raise HTTPException(404, "账号不存在")
    users.set_display_name(uid, payload.get("display_name") or "")
    return {"ok": True}


@app.post("/api/admin/users/{uid}/logout_all")
def api_admin_logout_all(uid: int, request: Request):
    _require_admin(request)
    n = users.revoke_user_sessions(uid)
    return {"ok": True, "revoked": n}


# ============================================================
# 内容
# ============================================================
def _bootstrap_payload(request: Request, d: str | None = None) -> dict:
    d = d or db.today_str()
    art = db.get_article_by_date(d)
    bundle = story_bundle(art["id"]) if art else None
    return {
        "date": d,
        "today": db.today_str(),
        "bundle": bundle,
        "auth": _auth_payload(request),
        "meta": {
            "streak": db.streak_days(),
            "ai_ready": settings.ai_ready,
            "model": settings.ai_model if settings.ai_ready else "",
            "stage": effective_stage(),
            "difficulty_label": DIFFICULTY_LABEL.get(decide_difficulty(), ""),
            "next_difficulty": decide_difficulty(),
            "daily_hour": scheduler.daily_hour(),
            "scheduler": scheduler.state(),
            "review_due": db.review_stats()["due"],
        },
        "history": db.recent_articles(14),
    }


@app.get("/api/bootstrap")
def bootstrap(request: Request, date_str: str | None = None):
    return _bootstrap_payload(request, date_str)


@app.get("/api/article/{date_str}")
def get_article(date_str: str):
    art = db.get_article_by_date(date_str)
    if not art:
        raise HTTPException(404, "当天内容尚未生成")
    return story_bundle(art["id"])


@app.post("/api/generate")
def api_generate(payload: dict = Body(default={})):
    d = payload.get("date") or db.today_str()
    try:
        row = generate_daily(
            d,
            topic=payload.get("topic") or None,
            difficulty=int(payload["difficulty"]) if payload.get("difficulty") else None,
            directives=payload.get("directives"),
            force=bool(payload.get("force")),
        )
    except AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"生成失败：{exc}") from exc
    return {"ok": True, "article": row, "bundle": story_bundle(row["id"])}


@app.post("/api/article/{date_str}/label")
def api_label_materials(date_str: str):
    """给当天文章的写作表达批量标注「该用在作文哪一段 + 属于什么文体」。"""
    art = db.get_article_by_date(date_str)
    if not art:
        raise HTTPException(404, "当天内容不存在")
    try:
        items = label_materials(art["id"])
    except AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "items": items}


@app.post("/api/import")
def api_import(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if len(text) < 200:
        raise HTTPException(400, "正文太短，请粘贴完整文章（建议 300 词以上）")
    try:
        row = analyze_real_article(
            text,
            title=payload.get("title") or "",
            source=payload.get("source") or "",
            date_str=payload.get("date") or db.today_str(),
            difficulty=int(payload["difficulty"]) if payload.get("difficulty") else None,
        )
    except AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "article": row, "bundle": story_bundle(row["id"])}


# ============================================================
# 真题精读：导入 / 列表 / 打开
#
# 为什么用「伪日期」而不是独立表：
#   精读所需的一切（词汇/长难句/语法/写作表达/仿写）都挂在 articles.id 上，
#   另起一张表就得把这些结构复制一遍。这里让真题也进 articles，
#   只是 date 写成 exam-2010-text2 —— 每日内容按真实日期查，永远撞不上，
#   而收藏、标记、批改这些按 date 反查文章的地方又能原样复用。
# ============================================================
@app.post("/api/exam/import")
def api_exam_import(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if len(text) < 200:
        raise HTTPException(400, "正文太短，请粘贴完整的一篇真题文章")
    try:
        year = int(payload.get("year") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "年份不合法") from exc
    if not (1998 <= year <= 2030):
        raise HTTPException(400, "年份请填 1998～2030 之间")
    slot = (payload.get("slot") or "").strip()
    if slot not in EXAM_SLOTS:
        raise HTTPException(400, "请选择篇目")
    try:
        row = analyze_exam_paper(
            text, year=year, slot=slot, title=(payload.get("title") or "").strip()
        )
    except AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"真题解析失败：{exc}") from exc
    return {"ok": True, "article": row, "bundle": story_bundle(row["id"])}


@app.get("/api/exam/list")
def api_exam_list():
    return {
        "items": db.exam_articles(),
        "slots": EXAM_SLOTS,
        # 年份相关全部由配置推导，前端不要再写死年份
        "meta": {
            "target_year": settings.exam_target_year,
            "latest_year": settings.exam_latest_year,
            "redline_from": settings.exam_redline_from,
            "years": settings.exam_year_range(),
        },
    }


@app.get("/api/exam/{exam_id}")
def api_exam_bundle(exam_id: int):
    art = db.get_exam_article(exam_id)
    if not art:
        raise HTTPException(404, "这篇真题还没导入")
    return story_bundle(exam_id)


# ============================================================
# 手写批注（自由手写）
#
# 归属靠 ContextVar 传递：这里不碰 user_id，db 层封装自带隔离。
# key 形如 "2026-10-05:body"；真题是 "exam-2010-text2:body"。
# 前端每停笔一次就 PUT 一回，所以只做最必要的形状校验。
# ============================================================
@app.get("/api/annotations")
def api_annotations_get(prefix: str = Query("", max_length=120)):
    """取某篇文档下当前用户的全部批注，按区块键返回。"""
    return {"items": db.annotations_for(prefix)}


@app.put("/api/annotations")
def api_annotations_put(payload: dict = Body(...)):
    """写一个区块的批注。data.strokes 为空时会删掉这条记录。"""
    key = (payload.get("key") or "").strip()
    data = payload.get("data") or {}
    if not key or len(key) > 160 or ":" not in key:
        raise HTTPException(400, "批注 key 不合法")
    if not isinstance(data, dict):
        raise HTTPException(400, "批注内容格式不对")
    try:
        db.save_annotation(key, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# ============================================================
# 学习行为
# ============================================================
@app.post("/api/read-log")
def api_read_log(payload: dict = Body(...)):
    d = payload.get("date") or db.today_str()
    art = db.get_article_by_date(d)
    db.touch_study_log(d, art["id"] if art else None)
    fields = {}
    if payload.get("seconds"):
        fields["read_seconds"] = int(payload["seconds"])
    if payload.get("analysis_opened"):
        fields["analysis_opened"] = 1
    if fields:
        db.touch_study_log(d, **fields)
        # 读的是真题（伪日期）时，阅读记录也算进今天，别让连续天数断掉
        if art and art.get("source_type") == "exam":
            db.touch_study_log(db.today_str(), art["id"], **fields)
    db.log_event("read_log", art["id"] if art else None, payload)
    return {"ok": True, "study_log": db.get_study_log(d)}


def _pub_vocab(row: dict | None) -> dict | None:
    """去掉 get_vocab 为鉴权附带的下划线字段，只把词汇本身回给前端。"""
    if not row:
        return None
    return {k: v for k, v in row.items() if not k.startswith("_")}


@app.post("/api/vocab/{vocab_id}/mark")
def api_vocab_mark(vocab_id: int, payload: dict = Body(...)):
    action = payload.get("action")
    # get_vocab 会顺着 article_id 校验归属：别人的词查不到
    v = db.get_vocab(vocab_id)
    if not v:
        raise HTTPException(404, "词汇不存在")
    mapping = {"known": 3, "focus": 2, "unknown": 1, "reset": 0}
    mat = None
    if action in mapping:
        db.execute(
            "UPDATE vocab SET mastery=?, last_review_at=datetime('now','localtime') WHERE id=?",
            (mapping[action], vocab_id),
        )
        art = db.get_article(v["article_id"])
        d = art["date"] if art else db.today_str()
        db.touch_study_log(d, art["id"] if art else None)
        key = {"known": "vocab_known", "focus": "vocab_focus", "unknown": "vocab_unknown"}.get(action)
        if key:
            db.bump_study_log(d, key, 1)
    elif action in ("favorite", "unfavorite"):
        fav = 1 if action == "favorite" else 0
        db.execute("UPDATE vocab SET favorite=? WHERE id=?", (fav, vocab_id))
        art = db.get_article(v["article_id"])
        if fav:
            mat = save_material(
                v["word"],
                cn=v["cn_definition"] or v["meaning_in_context"] or "",
                example=v["example"] or "",
                item_type="vocab",
                tags=["阅读积累"],
                source_article_id=v["article_id"],
                source_title=(art["title"] if art else ""),
                source_topic=(art["topic"] if art else ""),
            )
            if art:
                db.touch_study_log(art["date"], art["id"])
                db.bump_study_log(art["date"], "favorites_added", 1)
        else:
            # 取消收藏时，同步移除由词汇收藏产生的素材条目（只删自己的）
            db.execute(
                "DELETE FROM favorites WHERE item_type='vocab' AND content=? "
                "AND source_article_id IS ? AND user_id=?",
                (v["word"], v["article_id"], db.require_user()),
            )
            mat = None
    else:
        raise HTTPException(400, "未知操作")
    db.log_event(f"vocab_{action}", vocab_id, {"word": v["word"]})
    return {"ok": True, "vocab": _pub_vocab(db.get_vocab(vocab_id)), "material": mat}


@app.post("/api/imitation/submit")
def api_imitation_submit(payload: dict = Body(...)):
    task_id = payload.get("task_id")
    answer = (payload.get("answer") or "").strip()
    if not task_id or not answer:
        raise HTTPException(400, "请先写下你的答案")
    # 先确认这个任务挂在自己的文章上，否则等于帮别人批改/写库
    if not db.get_task(int(task_id)):
        raise HTTPException(404, "仿写任务不存在")
    try:
        return {"ok": True, **grade_submission(int(task_id), answer)}
    except AIError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/favorite")
def api_favorite(payload: dict = Body(...)):
    """收藏一条素材：先由 AI 判断该进哪个库、用在作文什么位置，再入库。"""
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "内容为空")
    art = db.get_article_by_date(payload.get("date") or db.today_str())
    mat = save_material(
        content,
        cn=payload.get("cn", ""),
        example=payload.get("example", ""),
        item_type=payload.get("item_type", "phrase"),
        tags=payload.get("tags"),
        source_article_id=art["id"] if art else None,
        source_title=(payload.get("source_title") or (art["title"] if art else "")),
        source_topic=(payload.get("source_topic") or (art["topic"] if art else "")),
        use_ai=not payload.get("skip_ai"),
    )
    if art:
        db.touch_study_log(art["date"], art["id"])
        db.bump_study_log(art["date"], "favorites_added", 1)
    return {"ok": True, **mat}


@app.get("/api/materials/meta")
def api_materials_meta():
    """素材库分类体系（前端用来渲染筛选器与图例）。"""
    return {
        "libraries": MATERIAL_LIBRARIES,
        "library_desc": MATERIAL_LIBRARY_DESC,
        "positions": MATERIAL_POSITIONS,
        "position_order": MATERIAL_POSITIONS_ORDER,
        "position_usage": MATERIAL_POSITION_USAGE,
        "genres": MATERIAL_GENRES,
        "genre_order": MATERIAL_GENRES_ORDER,
        "genre_hint": MATERIAL_GENRE_HINT,
        "categories": MATERIAL_CATEGORIES,
        "functions": MATERIAL_FUNCTIONS,
        "levels": MATERIAL_LEVELS,
    }


@app.get("/api/favorites")
def api_favorites(
    library: str | None = Query(default=None),
    position: str | None = Query(default=None),
    category: str | None = Query(default=None),
    genre: str | None = Query(default=None),
):
    items = db.favorites_all(
        library=library or None,
        position=position or None,
        category=category or None,
        genre=genre or None,
    )
    return {
        "items": items,
        "total": len(items),
        "summary": db.favorites_summary(),
        "filter": {
            "library": library, "position": position,
            "category": category, "genre": genre,
        },
        "meta": {
            "libraries": MATERIAL_LIBRARIES,
            "positions": MATERIAL_POSITIONS,
            "position_order": MATERIAL_POSITIONS_ORDER,
            "position_usage": MATERIAL_POSITION_USAGE,
            "genres": MATERIAL_GENRES,
            "genre_order": MATERIAL_GENRES_ORDER,
            "genre_hint": MATERIAL_GENRE_HINT,
            "categories": MATERIAL_CATEGORIES,
            "levels": MATERIAL_LEVELS,
        },
    }


def _material_row(fid: int) -> dict:
    """取一条素材；不存在**或不属于自己**一律 404（不要回 403，别泄露 id 是否存在）。"""
    row = db.get_favorite(fid)
    if not row:
        raise HTTPException(404, "素材不存在")
    row = dict(row)
    try:
        row["tags"] = json.loads(row.get("tags") or "[]")
    except Exception:  # noqa: BLE001
        row["tags"] = []
    return row


@app.patch("/api/favorites/{fid}")
def api_favorite_patch(fid: int, payload: dict = Body(...)):
    """手动纠偏：用户可以改库 / 位置 / 文体 / 类别 / 功能 / 使用建议。"""
    _material_row(fid)
    allowed = {
        "library": set(MATERIAL_LIBRARIES),
        "position": set(MATERIAL_POSITIONS),
        "genre": set(MATERIAL_GENRES),
        "category": set(MATERIAL_CATEGORIES),
        "function": set(MATERIAL_FUNCTIONS),
        "level": set(MATERIAL_LEVELS),
    }
    sets, args = [], []
    for field, allowed_values in allowed.items():
        if field in payload and payload[field] not in (None, ""):
            if payload[field] not in allowed_values:
                raise HTTPException(400, f"{field} 取值不合法")
            sets.append(f"{field}=?")
            args.append(payload[field])
    if "usage" in payload:
        sets.append("usage=?")
        args.append(str(payload["usage"]))
    if "tags" in payload and isinstance(payload["tags"], list):
        sets.append("tags=?")
        args.append(json.dumps(payload["tags"], ensure_ascii=False))
    if not sets:
        raise HTTPException(400, "没有需要更新的字段")
    # 手动改过就不再标记为 AI 自动归类
    sets.append("auto=0")
    sets.append("updated_at=datetime('now','localtime')")
    db.execute(
        f"UPDATE favorites SET {', '.join(sets)} WHERE id=? AND user_id=?",
        (*args, fid, db.require_user()),
    )
    db.log_event("material_patched", fid, {k: payload.get(k) for k in allowed if k in payload})
    return {"ok": True, "item": _material_row(fid)}


@app.post("/api/favorites/{fid}/reclassify")
def api_favorite_reclassify(fid: int):
    """让 AI 重新归一次类（覆盖原有分类）。"""
    row = _material_row(fid)
    art = db.get_article(row.get("source_article_id")) if row.get("source_article_id") else None
    result = classify_material(
        row["content"],
        cn=row.get("cn") or "",
        example=row.get("example") or "",
        item_type=row.get("item_type") or "phrase",
        source_title=(art["title"] if art else ""),
        source_topic=(art["topic"] if art else ""),
    )
    db.execute(
        """UPDATE favorites SET library=?,category=?,position=?,genre=?,function=?,level=?,usage=?,
                               auto=?,confidence=?,updated_at=datetime('now','localtime')
           WHERE id=? AND user_id=?""",
        (
            result["library"], result["category"], result["position"],
            result.get("genre") or "universal", result["function"],
            result["level"], result["usage"], 1 if result.get("ai") else 0,
            float(result.get("confidence") or 0), fid, db.require_user(),
        ),
    )
    db.log_event(
        "material_reclassified", fid,
        {"position": result["position"], "genre": result.get("genre"), "ai": result.get("ai")},
    )
    return {"ok": True, "item": _material_row(fid), "ai": bool(result.get("ai"))}


@app.delete("/api/favorites/{fid}")
def api_favorite_delete(fid: int):
    _material_row(fid)  # 不属于自己的直接 404
    db.execute(
        "DELETE FROM favorites WHERE id=? AND user_id=?", (fid, db.require_user())
    )
    return {"ok": True}


# ============================================================
# 复习（间隔重复）
# ============================================================
@app.get("/api/review/due")
def api_review_due():
    """今天该复习的素材 + 复习进度概览。"""
    return {
        "items": db.review_due(),
        "stats": db.review_stats(),
        "intervals": db.REVIEW_INTERVALS,
        "mastered_at": db.MASTERED_AT,
    }


@app.post("/api/review/{fid}/result")
def api_review_result(fid: int, payload: dict = Body(...)):
    result = (payload.get("result") or "").strip()
    if result not in ("known", "forgot", "skip", "drop", "readd"):
        raise HTTPException(400, "未知的复习结果")
    row = db.review_apply(fid, result)
    if row is None:
        raise HTTPException(404, "素材不存在")
    return {"ok": True, "item": row, "stats": db.review_stats()}


# ============================================================
# 数据统计 / 设置
# ============================================================
@app.get("/api/stats")
def api_stats(days: int = 7):
    s = db.weekly_stats(days)
    s["weak_words"] = db.weak_words(15)
    return s


# 2028 考研初试的**预计**日期。教育部自 2023 考研起固定在「12 月倒数第二个周末」，
# 2027-12 的倒数第二个周六 = 2027-12-18。⚠️ 官方公布后让用户在「设置」里改一次。
DEFAULT_EXAM_DATE = "2027-12-18"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_iso_date(s: str) -> bool:
    if not _ISO_DATE.match(s or ""):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


@app.get("/api/settings")
def api_get_settings(request: Request):
    return {
        "ai_ready": settings.ai_ready,
        "base_url": settings.ai_base_url,
        "model": settings.ai_model,
        "model_strong": settings.ai_model_strong,
        "stage": effective_stage(),
        "stage_options": STAGE_OPTIONS,
        "base_difficulty": effective_base_difficulty(),
        "daily_hour": settings.daily_hour,
        "exam_date": db.user_prefs().get("exam_date") or DEFAULT_EXAM_DATE,
        "next_difficulty": decide_difficulty(),
        "scheduler": scheduler.state(),
        "auth": _auth_payload(request),
    }


STAGE_OPTIONS = ["基础期", "强化期", "冲刺期"]


@app.post("/api/settings")
def api_set_settings(request: Request, payload: dict = Body(...)):
    """学习阶段 / 难度基准是**每个账号各自一份**；生成时间点是全服务器共用的。"""
    for k in ("stage", "base_difficulty"):
        if k in payload and payload[k] not in (None, ""):
            db.set_user_pref(k, str(payload[k]))
    if "exam_date" in payload:
        ed = str(payload["exam_date"] or "").strip()
        if ed and not _is_iso_date(ed):
            raise HTTPException(400, "考试日期要写成 2027-12-18 这样的格式")
        db.set_user_pref("exam_date", ed)       # 传空 = 回到默认值
    if payload.get("daily_hour") not in (None, ""):
        db.set_profile("daily_hour", str(payload["daily_hour"]))
    if payload.get("reload_provider"):
        reset_provider()
    return api_get_settings(request)


@app.get("/api/health")
def api_health():
    """探活用，不含隐私。部署脚本会打这个接口，所以必须免登录。"""
    with db.as_system():
        has_today = bool(db.get_article_by_date(db.today_str()))
    return {
        "ok": True,
        "today": db.today_str(),
        "has_today": has_today,
        "ai_ready": settings.ai_ready,
        "scheduler": scheduler.state(),
    }


@app.exception_handler(AIError)
async def ai_error_handler(_request, exc: AIError):
    return JSONResponse({"detail": str(exc)}, status_code=400)
