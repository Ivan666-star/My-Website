"""登录门：账号密码 + 会话，外加「还没建账号」时的两种兜底模式。

============================================================================
这个文件同时管三件事，读之前先分清（它们互斥，按顺序判定）
============================================================================
    MODE_ACCOUNTS  已经建过账号 → 账号密码登录。每个人只看得到自己的数据。
    MODE_PASSWORD  还没建账号 + 配了 ACCESS_PASSWORD → 沿用最早的「单口令门」。
                   整站只认那一个口令，进门后数据都在 0 号桶（未认领）。
    MODE_OPEN      还没建账号 + 没配口令 → 完全不介入。
                   本机自用就跟以前一模一样，浏览器不会看到任何登录界面。

为什么保留后两种：用户本地自用了一段时间，不能因为加了账号体系就把他的日常打断。
只在真正需要的时候（建了账号 / 配了口令）才多出一道门。

============================================================================
中间件为什么写成纯 ASGI（而不是 BaseHTTPMiddleware）
============================================================================
它要在调用下游之前把「当前用户」放进 ContextVar（db.as_user）。
纯 ASGI 里 `await self.app(...)` 和中间件在同一个 task 里，ContextVar 直接可见；
BaseHTTPMiddleware 会把下游放进子 task，靠 anyio 复制上下文，多一层不确定性。
数据隔离这种东西不能建立在「应该会传播」上。

============================================================================
安全取舍
============================================================================
  - 口令：PBKDF2 派生值 + 每人独立 salt（见 users.py），比对用 compare_digest。
  - 会话：cookie 里放随机令牌，库里只存它的 SHA-256。
  - cookie：HttpOnly + SameSite=lax + Secure（由 X-Forwarded-Proto 判定，
    所以反向代理必须传这个头）。
  - 连续输错会逐步减速，抬高暴力破解成本；不锁死账号，免得把自己也挡在外面。
  - 只允许站内跳转（safe_next），避免被当成开放重定向利用。
"""

from __future__ import annotations

import asyncio
import hmac
import html
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import db, users
from .config import settings

COOKIE_NAME = "kaoyan_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 一年：自己的设备不用反复登录

MODE_ACCOUNTS = "accounts"
MODE_PASSWORD = "password"
MODE_OPEN = "open"

# 不设防的路径：
#   /static/        静态资源不含隐私
#   /login /logout  门本身
#   /setup          还没账号时要能建第一个账号
#   /register       自助注册（建出来是待批准状态，管理员点头后才能用）
#   /api/health     部署脚本用它探活，不含任何隐私
#   manifest / sw / favicon  PWA 的三个固定文件
OPEN_PREFIXES = (
    "/static/",
    "/login",
    "/logout",
    "/setup",
    "/register",
    "/api/health",
    "/manifest.webmanifest",
    "/sw.js",
    "/favicon.ico",
)

# 连续输错时的减速（进程内计数，重启即清零）
_fails = {"n": 0, "setup": 0, "reg": 0}


# ============================================================
# 模式判定
# ============================================================
def mode() -> str:
    if users.has_users():
        return MODE_ACCOUNTS
    if settings.access_password:
        return MODE_PASSWORD
    return MODE_OPEN


def accounts_enabled() -> bool:
    return mode() == MODE_ACCOUNTS


def register_enabled() -> bool:
    """自助注册入口是否开着。关掉不影响已有账号。"""
    return bool(settings.allow_self_register)


# ============================================================
# 口令门（MODE_PASSWORD 用；沿用最早那套「服务端长期令牌」）
# ============================================================
def _password_token() -> str:
    """当前有效口令令牌。口令变更（或首次启用）会自动作废历史会话。"""
    fp = _fingerprint()
    if db.get_profile("auth_pwd_fingerprint", "") != fp:
        db.set_profile("auth_token", "")
        db.set_profile("auth_pwd_fingerprint", fp)
    return db.get_profile("auth_token", "")


def _fingerprint() -> str:
    import hashlib

    return hashlib.sha256(settings.access_password.encode("utf-8")).hexdigest()[:16]


def verify_access_password(raw: str) -> bool:
    if not settings.access_password:
        return False
    return hmac.compare_digest((raw or "").strip(), settings.access_password)


def issue_access_token() -> str:
    tok = _password_token()
    if not tok:
        tok = secrets.token_urlsafe(32)
        db.set_profile("auth_token", tok)
    return tok


def revoke_access_token() -> None:
    db.set_profile("auth_token", "")


def _access_token_ok(cookie: str) -> bool:
    tok = _password_token()
    return bool(tok and cookie) and hmac.compare_digest(cookie, tok)


# ============================================================
# 初始化码（建第一个账号时用）
# ============================================================
def setup_code() -> str:
    """当前生效的初始化码：SETUP_CODE → ACCESS_PASSWORD → 自动生成的那个。"""
    if settings.setup_code:
        return settings.setup_code
    if settings.access_password:
        return settings.access_password
    return db.get_profile("setup_code", "")


def ensure_setup_code() -> str:
    """启动时调一次。两个环境变量都没配就自动生成，落盘 + 打印，免得用户找不到。"""
    code = setup_code()
    if code:
        return code
    code = secrets.token_urlsafe(9)
    db.set_profile("setup_code", code)
    path = settings.db_path.parent / "setup_code.txt"
    try:
        path.write_text(
            "创建第一个账号时需要这个初始化码（只在本机以外访问时要求）：\n\n"
            f"    {code}\n\n"
            "在 .env 里设置 SETUP_CODE 或 ACCESS_PASSWORD 可以换成你自己记得住的。\n"
            "建好账号后这个文件就没用了，可以删掉。\n",
            encoding="utf-8",
        )
    except OSError:
        path = None
    print("=" * 64)
    print("  【初始化码】创建第一个账号时需要：", code)
    if path:
        print("  同时写在了：", path)
    print("  （在本机 127.0.0.1 直接访问不需要它；从外面访问才需要）")
    print("=" * 64)
    return code


def _is_local_direct(request: Request) -> bool:
    """本机直连：客户端是回环地址，且没有任何反向代理留下的头。

    为什么这么判：反向代理（nginx / Caddy）一定会补 X-Forwarded-*，而且经它转发后
    客户端地址也是 127.0.0.1。所以「回环 + 无代理头」等价于「用户坐在自己电脑前」。
    """
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        return False
    for h in (
        "x-forwarded-for",
        "x-real-ip",
        "x-forwarded-proto",
        "x-forwarded-host",
        "forwarded",
    ):
        if request.headers.get(h):
            return False
    return True


def setup_code_needed(request: Request) -> bool:
    """这次请求建账号要不要输初始化码。"""
    if settings.setup_code or settings.access_password:
        return True  # 用户明确配过 → 一律要，哪怕在自己电脑上
    return not _is_local_direct(request)


def verify_setup_code(raw: str, request: Request) -> bool:
    if not setup_code_needed(request):
        return True
    code = setup_code()
    return bool(code) and hmac.compare_digest((raw or "").strip(), code)


# ============================================================
# 会话令牌
# ============================================================
def current_user(request: Request) -> dict | None:
    """当前请求是谁（没登录返回 None）。"""
    if not accounts_enabled():
        return None
    return users.session_user(request.cookies.get(COOKIE_NAME, ""))


def is_admin(request: Request) -> bool:
    u = current_user(request)
    return bool(u and u.get("is_admin"))


def safe_next(raw: str) -> str:
    """只允许站内跳转，避免被当成开放重定向利用。"""
    nxt = (raw or "/").strip()
    if not nxt.startswith("/") or nxt.startswith("//"):
        return "/"
    return nxt


def _cookie_value(raw_header: str, name: str) -> str:
    try:
        jar = SimpleCookie()
        jar.load(raw_header or "")
        return jar[name].value if name in jar else ""
    except Exception:  # noqa: BLE001
        return ""


def _is_open(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in OPEN_PREFIXES)


def _set_session_cookie(resp, request: Request, token: str):
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=(scheme == "https"),
        path="/",
    )
    return resp


def _clear_cookie(resp):
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# ============================================================
# 中间件：顺带把「当前用户」塞进 db 的上下文
# ============================================================
async def _deny(scope, receive, send, headers: dict, path: str):
    if "text/html" in (headers.get("accept") or ""):
        resp = RedirectResponse(
            f"/login?next={quote(safe_next(path), safe='/')}", status_code=303
        )
    else:
        resp = JSONResponse({"detail": "需要登录", "login_required": True}, status_code=401)
    await resp(scope, receive, send)


class AuthGate:
    """纯 ASGI 中间件。除了拦人，还负责给每个请求设置用户上下文。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path") or "/"
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in (scope.get("headers") or [])
        }
        cookie = _cookie_value(headers.get("cookie", ""), COOKIE_NAME)
        m = mode()

        ctx = None
        if m == MODE_ACCOUNTS:
            user = users.session_user(cookie)
            if user:
                ctx = db.as_user(user["id"])
            elif _is_open(path):
                ctx = db.as_system()
            else:
                return await _deny(scope, receive, send, headers, path)
        elif m == MODE_PASSWORD:
            if _is_open(path) or _access_token_ok(cookie):
                # 口令门背后的数据还没有归属 → 走未认领桶
                ctx = db.as_local()
            else:
                return await _deny(scope, receive, send, headers, path)
        else:  # MODE_OPEN：没账号也没口令，完全按本地自用处理
            ctx = db.as_local()

        with ctx:
            return await self.app(scope, receive, send)


# ============================================================
# 页面（登录 / 初始化）—— 服务端渲染，不依赖任何外部资源
# ============================================================
_PAGE_CSS = """
  :root{--paper:#f5f3ef;--surface:#fff;--ink:#1b1a17;--ink-2:#3f3c36;--muted:#7c7568;
        --line:#e5e0d6;--accent:#1a6a5c;--warn:#a9502c;
        --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
        --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;}
  @media (prefers-color-scheme:dark){:root{--paper:#17181a;--surface:#212326;--ink:#eceae5;
        --ink-2:#c9c6bf;--muted:#918b80;--line:#313438;--accent:#5fbfa9;--warn:#e08a63;}}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:var(--paper);color:var(--ink);font-family:var(--sans);padding:24px}
  .card{width:100%;max-width:392px;background:var(--surface);border:1px solid var(--line);
        border-radius:16px;padding:28px 26px 24px;
        box-shadow:0 1px 2px rgba(27,26,23,.04),0 8px 24px -16px rgba(27,26,23,.18)}
  h1{margin:0 0 4px;font-family:var(--serif);font-size:19px;font-weight:600;letter-spacing:.01em}
  .sub{margin:0 0 20px;font-size:12.5px;color:var(--muted);line-height:1.6}
  label{display:block;font-size:12.5px;color:var(--ink-2);margin:14px 0 6px}
  label:first-of-type{margin-top:0}
  input{width:100%;font-family:inherit;font-size:16px;padding:11px 13px;border-radius:11px;
        border:1px solid var(--line);background:var(--paper);color:var(--ink)}
  input:focus{outline:none;border-color:var(--accent)}
  button{width:100%;margin-top:18px;font-family:inherit;font-size:15px;font-weight:600;
         padding:12px;border:0;border-radius:11px;background:var(--accent);color:#fff;cursor:pointer}
  button:hover{opacity:.92}
  .hint{margin:14px 0 0;font-size:12px;color:var(--muted);line-height:1.7}
  .err{margin:14px 0 0;font-size:12.5px;color:var(--warn);line-height:1.7}
  .sep{margin:18px 0 0;padding-top:14px;border-top:1px solid var(--line);font-size:12px;
       color:var(--muted);line-height:1.7}
  code{background:var(--paper);border:1px solid var(--line);border-radius:5px;
       padding:1px 5px;font-size:11.5px}
  a{color:var(--accent)}
"""


def _shell(title: str, sub: str, inner: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f5f3ef">
<title>Prism</title>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/static/icons/icon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/static/icons/icon-180.png">
<style>{_PAGE_CSS}</style>
</head>
<body>
  <form class="card" method="post">
    <img src="/static/icons/mark.png" alt="" style="height:34px;width:auto;display:block;margin:0 auto 12px">
    <h1>Prism</h1>
    <p class="sub">AI 考研英语一训练 · {sub}</p>
    {inner}
  </form>
</body>
</html>"""


def login_page_html(
    *, error: str = "", nxt: str = "/", m: str | None = None, username: str = ""
) -> str:
    m = m or mode()
    nxt = safe_next(nxt)
    if m == MODE_ACCOUNTS:
        inner = f"""
    <label for="username">用户名</label>
    <input id="username" name="username" type="text" autocomplete="username"
           autocapitalize="off" autocorrect="off" autofocus required
           value="{html.escape(username)}" placeholder="你的用户名">
    <label for="password">密码</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
           required placeholder="你的密码">
    <input type="hidden" name="next" value="{html.escape(nxt)}">
    <button type="submit">登录</button>
    {f'<p class="err">{html.escape(error)}</p>' if error else
     '<p class="hint">登录后只显示你自己账号下的文章、素材、复习和统计。</p>'}
    <p class="sep">还没有账号？<a href="/register">自己申请一个</a>，管理员批准后就能用；
    也可以让管理员在「设置 → 用户管理」里直接给你开。</p>"""
    else:
        inner = f"""
    <label for="password">访问口令</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
           autofocus required placeholder="输入口令">
    <input type="hidden" name="next" value="{html.escape(nxt)}">
    <button type="submit">进入</button>
    {f'<p class="err">{html.escape(error)}</p>' if error else
     '<p class="hint">这台服务放在了公网上，输入你设置的口令即可进入。</p>'}
    <p class="sep">想让每个人有自己的数据？进去之后在「设置」里创建第一个账号。</p>"""
    return _shell("登录", "请先验证身份", inner)


def setup_page_html(
    *, error: str = "", need_code: bool = False, claimed: dict | None = None
) -> str:
    code_block = ""
    if need_code:
        code_block = """
    <label for="code">初始化码</label>
    <input id="code" name="code" type="text" autocomplete="off" autocapitalize="off"
           spellcheck="false" required placeholder="SETUP_CODE 或 ACCESS_PASSWORD">"""
    claim_tip = ""
    if claimed:
        parts = [f"{k} {v} 条" for k, v in claimed.items() if v]
        if parts:
            claim_tip = (
                '<p class="sep">这个账号会接管升级前留下的数据：'
                + "、".join(parts)
                + "。</p>"
            )
    inner = f"""
    <label for="username">用户名</label>
    <input id="username" name="username" type="text" autocomplete="username"
           autocapitalize="off" autocorrect="off" autofocus required
           placeholder="3-24 位字母/数字/下划线">
    <label for="display_name">显示名（选填）</label>
    <input id="display_name" name="display_name" type="text" autocomplete="off"
           placeholder="比如：小明">
    <label for="password">密码</label>
    <input id="password" name="password" type="password" autocomplete="new-password"
           required placeholder="至少 6 位">
    <label for="password2">再输一次</label>
    <input id="password2" name="password2" type="password" autocomplete="new-password"
           required placeholder="确认密码">{code_block}
    <button type="submit">创建管理员账号</button>
    {f'<p class="err">{html.escape(error)}</p>' if error else
     '<p class="hint">这是系统里第一个账号，会自动成为管理员，可以在「设置」里给其他人开号。</p>'}
    {claim_tip}"""
    return _shell("创建账号", "先给系统建一个管理员账号", inner)


def register_page_html(
    *,
    error: str = "",
    nxt: str = "/",
    done: bool = False,
    m: str | None = None,
    username: str = "",
    display_name: str = "",
    enabled: bool = True,
) -> str:
    if not enabled:
        inner = """
    <p class="err">管理员关掉了自助注册。</p>
    <p class="hint">需要账号的话跟管理员说一声，让他在「设置 → 用户管理」里给你开一个。</p>
    <p class="hint"><a href="/login">← 回到登录页</a></p>"""
        return _shell("注册已关闭", "暂时不接受自助注册", inner)

    if done:
        inner = f"""
    <p class="hint" style="font-size:13.5px">
      申请交上去了。管理员批准之后，用 <b>{html.escape(username)}</b>
      加你刚才设的密码就能登录。
    </p>
    <p class="sep">为什么要等批准：这个系统是一小圈人共用的，
    管理员得先确认是认识的人，才让他进来。</p>
    <p class="hint"><a href="/login">← 回到登录页</a></p>"""
        return _shell("申请已提交", "等管理员批准", inner)

    mode_hint = ""
    if (m or mode()) == MODE_ACCOUNTS:
        mode_hint = (
            "批准之后你会有一套自己独立的文章、词汇、素材库、复习进度和学习数据，"
            "跟别人互不干扰。"
        )
    inner = f"""
    <label for="username">用户名</label>
    <input id="username" name="username" type="text" autocomplete="username"
           autocapitalize="off" autocorrect="off" autofocus required
           value="{html.escape(username)}" placeholder="3-24 位字母/数字/下划线">
    <label for="display_name">显示名（选填）</label>
    <input id="display_name" name="display_name" type="text" autocomplete="off"
           value="{html.escape(display_name)}" placeholder="比如：小王">
    <label for="password">密码</label>
    <input id="password" name="password" type="password" autocomplete="new-password"
           required placeholder="至少 6 位">
    <label for="password2">再输一次</label>
    <input id="password2" name="password2" type="password" autocomplete="new-password"
           required placeholder="确认密码">
    <input type="hidden" name="next" value="{html.escape(nxt)}">
    <button type="submit">提交申请</button>
    {f'<p class="err">{html.escape(error)}</p>' if error else
     '<p class="hint">提交后还不能马上用 —— 要等管理员批准。</p>'}
    {f'<p class="hint">{mode_hint}</p>' if mode_hint else ""}
    <p class="sep">已经有账号了？<a href="/login">去登录</a></p>"""
    return _shell("申请账号", "提交后由管理员批准", inner)


# ============================================================
# 表单处理
# ============================================================
async def _form(request: Request) -> dict:
    """自己解析表单 body，不引入 python-multipart。"""
    body = await request.body()
    if not body:
        return {}
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}


async def _slow_down(key: str, html_resp):
    _fails[key] += 1
    if _fails[key] >= 5:
        # 越错越慢，最多等 5 秒；不锁死，避免把自己也挡在外面
        await asyncio.sleep(min(1 + (_fails[key] - 5) * 0.5, 5))
    return html_resp


def _next_or_home(request: Request, form: dict) -> str:
    return safe_next(form.get("next") or request.query_params.get("next") or "/")


async def handle_login(request: Request):
    form = await _form(request)
    m = mode()
    nxt = _next_or_home(request, form)

    if m == MODE_ACCOUNTS:
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        user = users.get_user_by_name(username) if username else None
        if not user or not users.verify_password(user, password):
            return await _slow_down(
                "n",
                HTMLResponse(
                    login_page_html(error="用户名或密码不对，再试一次。", nxt=nxt,
                                    m=m, username=username),
                    status_code=401,
                ),
            )
        if not user.get("approved", 1):
            # 密码是对的，只是还没被批准 —— 要说清楚，否则用户以为密码错了
            return await _slow_down(
                "n",
                HTMLResponse(
                    login_page_html(
                        error="这个账号还在等管理员批准，批准之后就能登录了。",
                        nxt=nxt,
                        m=m,
                        username=username,
                    ),
                    status_code=403,
                ),
            )
        if not user.get("active"):
            return await _slow_down(
                "n",
                HTMLResponse(
                    login_page_html(error="这个账号已被停用，请联系管理员。", nxt=nxt, m=m),
                    status_code=403,
                ),
            )
        _fails["n"] = 0
        users.touch_login(user["id"])
        token = users.create_session(
            user["id"], request.headers.get("user-agent") or ""
        )
        return _set_session_cookie(RedirectResponse(nxt, status_code=303), request, token)

    # 口令门
    if not verify_access_password(form.get("password") or ""):
        return await _slow_down(
            "n",
            HTMLResponse(
                login_page_html(error="口令不对，再试一次。", nxt=nxt, m=m), status_code=401
            ),
        )
    _fails["n"] = 0
    return _set_session_cookie(
        RedirectResponse(nxt, status_code=303), request, issue_access_token()
    )


async def handle_setup(request: Request):
    """创建系统里的第一个账号（管理员），并把未认领的数据划给它。"""
    if accounts_enabled():
        return RedirectResponse("/", status_code=303)

    form = await _form(request)
    need = setup_code_needed(request)
    if not verify_setup_code(form.get("code") or "", request):
        return await _slow_down(
            "setup",
            HTMLResponse(
                setup_page_html(error="初始化码不对。", need_code=need), status_code=401
            ),
        )

    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    password2 = form.get("password2") or ""
    if password != password2:
        return HTMLResponse(
            setup_page_html(error="两次输入的密码不一致。", need_code=need), status_code=400
        )
    try:
        user = users.create_user(
            username,
            password,
            display_name=form.get("display_name") or "",
            is_admin=True,
        )
    except ValueError as exc:
        return HTMLResponse(
            setup_page_html(error=str(exc), need_code=need), status_code=400
        )

    claimed = users.claim_orphan_data(user["id"])
    print(f"[setup] 第一个账号 {user['username']!r} 已创建，接管数据：{claimed}")
    _fails["setup"] = 0
    # 初始化码用掉就作废，免得留在 profile 里
    db.set_profile("setup_code", "")
    users.touch_login(user["id"])
    token = users.create_session(user["id"], request.headers.get("user-agent") or "")
    resp = RedirectResponse("/", status_code=303)
    _set_session_cookie(resp, request, token)
    return resp


async def handle_register(request: Request):
    """自助注册：建出来是「待批准」账号，管理员批准前登不进去，也不会自动登录。"""
    if not register_enabled():
        return HTMLResponse(register_page_html(enabled=False), status_code=403)

    form = await _form(request)
    m = mode()
    nxt = _next_or_home(request, form)
    username = form.get("username") or ""
    display_name = form.get("display_name") or ""

    if (form.get("password") or "") != (form.get("password2") or ""):
        return HTMLResponse(
            register_page_html(
                error="两次输入的密码不一致。", nxt=nxt, m=m,
                username=username, display_name=display_name,
            ),
            status_code=400,
        )
    try:
        user = users.register_user(
            username, form.get("password") or "", display_name=display_name
        )
    except ValueError as exc:
        return HTMLResponse(
            register_page_html(
                error=str(exc), nxt=nxt, m=m,
                username=username, display_name=display_name,
            ),
            status_code=400,
        )

    print(f"[register] {user['username']!r} 已提交申请，等待管理员批准")
    # 复用同一套「越试越慢」：顺手挡一下被人反复提交注册
    return await _slow_down(
        "reg",
        HTMLResponse(register_page_html(done=True, username=user["username"], m=m)),
    )


async def handle_logout(request: Request):
    token = request.cookies.get(COOKIE_NAME, "")
    if mode() == MODE_ACCOUNTS:
        users.revoke_session(token)
    else:
        revoke_access_token()
    resp = RedirectResponse("/login" if mode() != MODE_OPEN else "/", status_code=303)
    return _clear_cookie(resp)
