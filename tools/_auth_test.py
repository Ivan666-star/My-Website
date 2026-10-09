"""登录门的端到端回归：自己起两个临时实例、打真实端口、跑完自己收掉。

为什么这么测：
    中间件最容易被"单测通过、真跑就漏"坑到。所以这里用真 HTTP 请求，
    连 cookie 的 HttpOnly / SameSite 都得从响应头上肉眼可见地验一遍。

    又因为要验「设了口令」和「没设口令」两种行为，必须同时有带口令和不带口令两个实例。
    这两个实例由本脚本自己拉起（用临时数据库 + 空 AI Key，绝不碰 data/app.db、
    也绝不会产生任何模型调用），跑完自动关掉。

用法：
    python tools/_auth_test.py

注意：
    - uvicorn 发出去的响应头名是小写的，取 Location 要写 "location"。
      所以下面统一把 header key 归一化成小写，否则断言全假失败。
    - 不要手动去起 8898 / 8899，脚本会自己管。若这两个端口被别的程序占着，
      脚本会直接报错退出，不会静默失败。
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402  （删临时目录要重试，见该模块注释）

ROOT = Path(__file__).resolve().parents[1]

GUARDED = "http://127.0.0.1:8899"   # 带口令的测试实例
PLAIN = "http://127.0.0.1:8898"     # 未设口令的测试实例
PWD = "test1234"

ok_n = 0
bad_n = 0


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) != 0


@contextmanager
def instance(port: int, password: str):
    """起一个临时实例，退出上下文时保证被杀掉。"""
    if not port_free(port):
        raise RuntimeError(
            f"端口 {port} 已被占用。这个端口是本测试专用的，请先关掉占用它的程序"
            f"（或改 GUARDED / PLAIN 常量）。"
        )
    tmp = Path(tempfile.mkdtemp(prefix=f"authtest{port}_"))
    env = dict(os.environ)
    env.update({
        "ACCESS_PASSWORD": password,
        "DB_PATH": str(tmp / "app.db").replace("\\", "/"),
        # 临时实例绝不调用模型、绝不自动生成内容
        "AI_API_KEY": "",
        "DAILY_GENERATE_HOUR": "23",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        ready = False
        for _ in range(60):
            if proc.poll() is not None:
                err = (proc.stderr.read() or b"").decode("utf-8", "replace")
                raise RuntimeError(f"实例 {port} 启动即退出：\n{err[-800:]}")
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                ready = True
                break
            except urllib.error.HTTPError:
                ready = True   # 401 也算活着（带口令实例的健康检查会被挡）
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        if not ready:
            raise RuntimeError(f"实例 {port} 在 24 秒内没起来")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        cleanup(tmp)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """别自动跟着跳，我要看 303 本身和 Location。"""

    def redirect_request(self, *a, **k):  # noqa: D102
        return None


def opener_with(jar=None):
    jar = jar if jar is not None else http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),          # 本机 http_proxy 会劫持 127.0.0.1
        NoRedirect(),
        urllib.request.HTTPCookieProcessor(jar),
    )
    return op, jar


def lower(hd) -> dict:
    """uvicorn 下发的响应头名是小写的；统一归一化，免得断言踩大小写的坑。"""
    return {str(k).lower(): v for k, v in hd.items()}


def call(base, path, method="GET", body=None, cookie=None, accept=None, op=None):
    """返回 (status, headers(小写键的 dict), body(bytes))。HTTP 错误也当正常结果返回。"""
    headers = {}
    if accept:
        headers["Accept"] = accept
    if cookie:
        headers["Cookie"] = cookie

    data = None
    if body is not None:
        if isinstance(body, bytes):
            data = body
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    op = op or opener_with()[0]
    try:
        with op.open(req, timeout=20) as r:
            return r.status, lower(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, lower(e.headers), e.read()
    except Exception as e:  # noqa: BLE001
        return -1, {}, str(e).encode("utf-8")


def check(label, cond, detail=""):
    global ok_n, bad_n
    if cond:
        ok_n += 1
        print(f"  [OK]   {label}")
    else:
        bad_n += 1
        print(f"  [FAIL] {label}   {detail}")


def form(pairs) -> bytes:
    return urllib.parse.urlencode(pairs).encode("utf-8")


def run_checks() -> int:
    print("=== 1. 未登录：页面被挡、接口 401、静态资源放行 ===")
    st, hd, bd = call(GUARDED, "/", accept="text/html,application/xhtml+xml")
    check("GET / -> 303 跳登录", st == 303, f"got {st}")
    check("  Location 指向 /login?next=/", hd.get("location", "").startswith("/login"), hd.get("location"))

    # /api/health 故意免登录：部署脚本和探活要用它。但它不能泄露任何隐私。
    st, hd, bd = call(GUARDED, "/api/health")
    check("GET /api/health -> 200（探活用，故意免登录）", st == 200, f"got {st}")
    txt = bd.decode("utf-8", "replace")
    check("  health 里没有学习数据字段",
          all(k not in txt for k in ("streak", "favorites", "vocab", "title", "body")), txt[:160])

    st, hd, bd = call(GUARDED, "/api/favorites")
    check("GET /api/favorites -> 401（学习数据不外泄）", st == 401, f"got {st}")

    st, hd, bd = call(GUARDED, "/static/app.js")
    check("GET /static/app.js -> 200（静态资源放行）", st == 200, f"got {st}")
    check("  app.js 有内容", len(bd) > 1000, f"{len(bd)}B")

    st, hd, bd = call(GUARDED, "/login", accept="text/html")
    check("GET /login -> 200 出登录页", st == 200, f"got {st}")
    check("  页面是口令表单", b'name="password"' in bd)

    print("\n=== 2. 口令错误 -> 401 且不回 cookie ===")
    st, hd, bd = call(GUARDED, "/login", method="POST", body=form({"password": "wrong-pwd", "next": "/"}),
                      accept="text/html")
    check("POST /login(错) -> 401", st == 401, f"got {st}")
    check("  没有下发 cookie", "set-cookie" not in hd, hd.get("set-cookie"))
    check("  页面提示口令不对", "口令不对".encode("utf-8") in bd)

    print("\n=== 3. 口令正确 -> 303 + 长期 cookie ===")
    op, jar = opener_with()
    st, hd, bd = call(GUARDED, "/login", method="POST", body=form({"password": PWD, "next": "/"}),
                      accept="text/html", op=op)
    check("POST /login(对) -> 303", st == 303, f"got {st}")
    sc = hd.get("set-cookie", "")
    check("  下发 kaoyan_auth", sc.startswith("kaoyan_auth="), sc[:80])
    check("  HttpOnly（JS 读不到，防 XSS 偷令牌）", "httponly" in sc.lower(), sc)
    check("  SameSite=lax（防跨站伪造）", "samesite=lax" in sc.lower().replace(" ", ""), sc)
    check("  有效期一年", "max-age=31536000" in sc.lower().replace(" ", ""), sc)

    print("\n=== 4. 带上 cookie：正常通行 ===")
    st, hd, bd = call(GUARDED, "/", accept="text/html,application/xhtml+xml", op=op)
    check("GET / -> 200", st == 200, f"got {st}")
    check("  拿到的是主页面（标题含『Prism』）", "Prism".encode("utf-8") in bd)
    check("  不是登录页", "访问口令".encode("utf-8") not in bd)

    st, hd, bd = call(GUARDED, "/api/health", op=op)
    check("GET /api/health -> 200", st == 200, f"got {st}")
    st, hd, bd = call(GUARDED, "/api/favorites", op=op)
    check("GET /api/favorites -> 200（能看到自己的数据）", st == 200, f"got {st}")

    print("\n=== 5. 伪造 cookie 一律拒绝 ===")
    st, hd, bd = call(GUARDED, "/api/favorites", cookie="kaoyan_auth=fake-token-123")
    check("伪造令牌 -> 401", st == 401, f"got {st}")
    st, hd, bd = call(GUARDED, "/", accept="text/html", cookie="kaoyan_auth=fake-token-123")
    check("伪造令牌访问页面 -> 303 跳登录", st == 303, f"got {st}")
    st, hd, bd = call(GUARDED, "/api/favorites", cookie="kaoyan_auth=")
    check("空令牌 -> 401", st == 401, f"got {st}")

    print("\n=== 6. 开放重定向防护 ===")
    st, hd, bd = call(GUARDED, "/login", method="POST",
                      body=form({"password": PWD, "next": "//evil.com/x"}), accept="text/html")
    loc = hd.get("location", "")
    check("next=//evil.com 被压回站内 /", loc == "/" and "evil.com" not in loc, f"Location={loc!r}")

    st, hd, bd = call(GUARDED, "/login", method="POST",
                      body=form({"password": PWD, "next": "https://evil.com"}), accept="text/html")
    loc = hd.get("location", "")
    check("next=https://evil.com 被压回站内 /", loc == "/" and "evil.com" not in loc, f"Location={loc!r}")

    st, hd, bd = call(GUARDED, "/login", method="POST",
                      body=form({"password": PWD, "next": "/library"}), accept="text/html")
    check("合法的 next=/library 予以保留", hd.get("location") == "/library", hd.get("location"))

    st, hd, bd = call(GUARDED, "/", accept="text/html")
    check("未登录访问 / -> 回跳 next=/",
          hd.get("location") == "/login?next=/", f"location={hd.get('location')!r}")

    st, hd, bd = call(GUARDED, "/library", accept="text/html")
    check("未登录访问 /library -> next 记住原路径",
          hd.get("location") == "/login?next=/library", f"location={hd.get('location')!r}")

    print("\n=== 7. 退出登录 -> 服务端令牌作废（所有设备一起掉线） ===")
    op2, jar2 = opener_with()
    call(GUARDED, "/login", method="POST", body=form({"password": PWD, "next": "/"}), accept="text/html", op=op2)
    st, hd, bd = call(GUARDED, "/api/health", op=op2)
    check("退出前可访问", st == 200, f"got {st}")

    st, hd, bd = call(GUARDED, "/logout", op=op2)
    check("GET /logout -> 303 跳回登录", st == 303, f"got {st}")
    check("  清除 cookie", "kaoyan_auth=" in hd.get("set-cookie", ""), hd.get("set-cookie"))

    # 关键：拿"退出前那份 cookie"再打一次，服务端令牌已换，必须 401
    st, hd, bd = call(GUARDED, "/api/favorites", op=op2)
    check("退出后同一浏览器 -> 401", st == 401, f"got {st}")
    st, hd, bd = call(GUARDED, "/api/favorites", cookie="kaoyan_auth=" + "x" * 43)
    check("随便构造的旧令牌 -> 401", st == 401, f"got {st}")

    print("\n=== 8. 没设口令时：行为必须和以前一模一样 ===")
    st, hd, bd = call(PLAIN, "/")
    check("GET / -> 200（无登录页拦截）", st == 200, f"got {st}")
    st, hd, bd = call(PLAIN, "/api/health")
    check("GET /api/health -> 200", st == 200, f"got {st}")
    st, hd, bd = call(PLAIN, "/login")
    check("GET /login -> 303 直接放回首页", st == 303, f"got {st}")

    print(f"\n===== 结果：{ok_n} 通过 / {bad_n} 失败 =====")
    return 1 if bad_n else 0


def main() -> int:
    print(f"拉起临时实例：{PLAIN}（无口令）、{GUARDED}（口令 {PWD}）…")
    try:
        with instance(8898, ""), instance(8899, PWD):
            print("两个实例都就绪。\n")
            return run_checks()
    except RuntimeError as e:
        print(f"\n[起不来] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
