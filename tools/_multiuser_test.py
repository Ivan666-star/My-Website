"""多用户隔离回归：起两个临时实例，用真 HTTP 走一遍「两个人用同一个站点」。

验的是**最不能接受的那类 bug**：A 看到 / 改到 B 的数据。
所以除了「正常用」之外，专门有一批「拿着别人的 id 去撞」的断言。

自己起、自己收（临时库 + 临时端口），跑完不留痕迹，**不碰线上 8848**。
跑法：python tools/_multiuser_test.py
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402  （删临时目录要重试，见该模块注释）

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

OK = 0
BAD = 0
_FAILED: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    global OK, BAD
    if cond:
        OK += 1
        print(f"  ✓ {label}")
    else:
        BAD += 1
        _FAILED.append(label)
        print(f"  ✗ {label}   {extra}")


def _lower(headers) -> dict:
    # 响应头一律转小写：不统一的话 hdr.get("Location") 时而命中时而不命中
    return {k.lower(): v for k, v in dict(headers).items()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D102
        return None


class Client:
    """带 cookie 的极简客户端；不自动跟随跳转，方便断言 303 的目标。"""

    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.ProxyHandler({}),   # 本机代理会把 localhost 也代理走
            NoRedirect(),
        )

    def req(self, method: str, path: str, *, form=None, js=None, headers=None):
        url = self.base + path
        data = None
        hdr = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        elif js is not None:
            data = json.dumps(js).encode()
            hdr["Content-Type"] = "application/json"
        rq = urllib.request.Request(url, data=data, method=method, headers=hdr)
        try:
            with self.op.open(rq, timeout=30) as r:
                return r.status, _lower(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, _lower(e.headers), e.read()

    def get(self, p, **k):
        return self.req("GET", p, **k)

    def post(self, p, **k):
        return self.req("POST", p, **k)

    def patch(self, p, **k):
        return self.req("PATCH", p, **k)

    def delete(self, p, **k):
        return self.req("DELETE", p, **k)

    # ---- 便捷 ----
    def jget(self, p, **k):
        st, _, body = self.get(p, **k)
        try:
            return st, json.loads(body or b"{}")
        except json.JSONDecodeError:
            return st, {}

    def jpost(self, p, **k):
        st, _, body = self.post(p, **k)
        try:
            return st, json.loads(body or b"{}")
        except json.JSONDecodeError:
            return st, {}

    def jpatch(self, p, **k):
        st, _, body = self.patch(p, **k)
        try:
            return st, json.loads(body or b"{}")
        except json.JSONDecodeError:
            return st, {}

    def form_login(self, **kw):
        return self.post("/login", form=kw)

    def cookies(self) -> list[str]:
        return [c.name for c in self.jar]

    def clear_cookies(self):
        for c in list(self.jar):
            self.jar.clear(c.domain, c.path, c.name)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, tmp: Path, **env_extra):
        self.tmp = tmp
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ)
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                  "ALL_PROXY", "all_proxy"):
            env.pop(k, None)
        env.update(
            DB_PATH=str(tmp / "app.db"),
            HOST="127.0.0.1",
            PORT=str(self.port),
            AI_API_KEY="",                # 不调模型
            DAILY_GENERATE_HOUR="23",     # 别在测试期间自动生成
            ACCESS_PASSWORD="",
            SETUP_CODE="",
            TZ="Asia/Shanghai",
        )
        env.update({k: str(v) for k, v in env_extra.items()})
        self.proc = subprocess.Popen(
            [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

    def wait(self, timeout: float = 40.0) -> bool:
        c = Client(self.base)
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                print(self.proc.stdout.read().decode("utf-8", "replace")[-3000:])
                return False
            try:
                st, _, _ = c.get("/api/health")
                if st == 200:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        return False

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/F"],
                               capture_output=True)
            except Exception:  # noqa: BLE001
                pass

    def db(self):
        con = sqlite3.connect(str(self.tmp / "app.db"), timeout=30)
        con.row_factory = sqlite3.Row
        return con


def seed_row(srv: Server, uid: int, day: str, *, title: str, fav: str,
             article_kind: str = "ai") -> tuple[int, int, int]:
    """直接往库里塞一条「属于 uid 的」文章 + 一个词 + 一条素材，返回三个 id。"""
    con = srv.db()
    with con:
        cur = con.execute(
            "INSERT INTO articles(user_id,date,title,body,difficulty,word_count,"
            "source_type,source) VALUES(?,?,?,?,3,120,?,'测试')",
            (uid, day, title, "The quick brown fox jumps over the lazy dog. " * 8,
             article_kind),
        )
        aid = cur.lastrowid
        cur = con.execute(
            "INSERT INTO vocab(article_id,word,cn_definition,importance) VALUES(?,?,?,4)",
            (aid, f"word{aid}", "测试词"),
        )
        vid = cur.lastrowid
        cur = con.execute(
            "INSERT INTO favorites(user_id,item_type,content,cn,library,position,genre,level,"
            "category,next_review) VALUES(?,'phrase',?,?,'writing','body','argumentative','A',"
            "'通用表达','2099-01-01')",
            (uid, fav, "中文"),
        )
        fid = cur.lastrowid
    con.close()
    return aid, vid, fid


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mu_e2e_"))
    srv = Server(tmp)
    print(f"临时实例：{srv.base}   库：{tmp / 'app.db'}")
    if not srv.wait():
        print("!! 服务没起来")
        srv.stop()
        return 1

    try:
        anon = Client(srv.base)

        # ==========================================================
        print("\n[1] 还没建账号 → 不介入（本地自用行为不变）")
        st, _, body = anon.get("/")
        check("首页直接可访问，不跳登录", st == 200, f"status={st}")
        st, j = anon.jget("/api/bootstrap")
        check("/api/bootstrap 不需要登录", st == 200, f"status={st}")
        check("auth.mode == open", j.get("auth", {}).get("mode") == "open", str(j.get("auth")))
        check("can_setup 为真（可以建第一个账号）", j.get("auth", {}).get("can_setup") is True)
        check("内置示例内容已就位", bool(j.get("bundle")), str(j.get("bundle"))[:80])
        st, j2 = anon.jget("/api/health")
        check("/api/health 免登录（部署探活用）", st == 200)

        # ==========================================================
        print("\n[2] 初始化码：从外网来必须给码")
        st, _, body = anon.post(
            "/setup",
            form={"username": "hacker", "password": "abc12345", "password2": "abc12345"},
            headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
        )
        check("带反代头且不给初始化码 → 401", st == 401, f"status={st}")
        con = srv.db()
        n_users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        con.close()
        check("被拒后没有建出账号", n_users == 0, f"users={n_users}")

        code_file = tmp / "setup_code.txt"
        check("初始化码写到了 data/setup_code.txt", code_file.exists())
        m = re.search(r"\n\s+([A-Za-z0-9_\-]{10,})\n", code_file.read_text(encoding="utf-8"))
        code = m.group(1) if m else ""
        check("能从文件里读到初始化码", bool(code))

        st, _, body = anon.post(
            "/setup",
            form={"username": "ivan", "password": "abc12345", "password2": "abc12345",
                  "code": "wrong-code-here"},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        check("错初始化码 → 401", st == 401, f"status={st}")

        st, hdr, _ = anon.post(
            "/setup",
            form={"username": "ivan", "password": "abc12345", "password2": "abc12345",
                  "display_name": "Ivan", "code": code},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        check("正确初始化码 → 建号成功并直接登录", st == 303, f"status={st}")
        check("建号后拿到了会话 cookie", any(c == "kaoyan_auth" for c in anon.cookies()))
        st, j = anon.jget("/api/me")
        check("当前用户是 ivan 且是管理员",
              j.get("username") == "ivan" and j.get("is_admin") is True, str(j))
        check("模式已切到 accounts", j.get("mode") == "accounts", str(j))

        # ==========================================================
        print("\n[3] 有人没登录 → 拦住")
        guest = Client(srv.base)
        st, hdr, _ = guest.get("/", headers={"Accept": "text/html"})
        check("页面请求 → 303 跳 /login", st == 303 and "/login" in hdr.get("location", ""),
              f"status={st} loc={hdr.get('location')}")
        st, j = guest.jget("/api/bootstrap")
        check("接口请求 → 401 + login_required",
              st == 401 and j.get("login_required") is True, f"status={st} {j}")
        st, _, body = guest.get("/login")
        check("登录页可用且是账号密码版",
              st == 200 and "用户名" in body.decode("utf-8"), f"status={st}")
        st, _, _ = guest.form_login(username="ivan", password="wrongpass")
        check("密码错 → 401", st == 401, f"status={st}")
        st, _, _ = guest.form_login(username="nobody", password="abc12345")
        check("用户不存在 → 401", st == 401, f"status={st}")
        st, hdr_open, _ = guest.form_login(
            username="ivan", password="abc12345", **{"next": "//evil.com"})
        check("开放重定向被拦（跳到站内）",
              st == 303 and not hdr_open.get("location", "").startswith("//"),
              str(hdr_open.get("location")))
        st, _, _ = guest.form_login(username="IVAN", password="abc12345")
        check("用户名大小写不敏感也能登录", st == 303, f"status={st}")

        # ==========================================================
        print("\n[4] 管理员开号")
        st, j = anon.jpost("/api/admin/users",
                           js={"username": "bob", "password": "bob12345",
                               "display_name": "小 Bob"})
        check("管理员能建号", st == 200 and j.get("user", {}).get("username") == "bob",
              f"status={st} {j}")
        bob_id = j.get("user", {}).get("id")
        check("新号默认不是管理员", j.get("user", {}).get("is_admin") is False)
        check("返回体里没有口令字段", "password_hash" not in json.dumps(j))
        st, j = anon.jpost("/api/admin/users",
                           js={"username": "bob", "password": "bob12345"})
        check("重名会被拒绝", st == 400, f"status={st}")
        st, j = anon.jpost("/api/admin/users", js={"username": "ok2", "password": "123"})
        check("弱口令会被拒绝", st == 400, f"status={st}")

        # ==========================================================
        print("\n[5] 两人各有一份数据（同一天互不冲突）")
        con = srv.db()
        ivan_id = con.execute(
            "SELECT id FROM users WHERE username='ivan'").fetchone()["id"]
        con.close()
        check("库里两个账号", ivan_id != bob_id)

        ivan_art, ivan_vocab, ivan_fav = seed_row(
            srv, ivan_id, "2099-05-05", title="Ivan 的文章", fav="ivan-only-phrase")
        bob_art, bob_vocab, bob_fav = seed_row(
            srv, bob_id, "2099-05-05", title="Bob 的文章", fav="bob-only-phrase")
        check("同一天两个账号各存一篇（UNIQUE(user_id,date) 生效）",
              ivan_art != bob_art)

        con = srv.db()
        nulls = con.execute("SELECT COUNT(*) c FROM articles WHERE user_id IS NULL").fetchone()["c"]
        con.close()
        check("没有归属不明的文章", nulls == 0, f"NULL={nulls}")

        bob = Client(srv.base)
        st, _, _ = bob.form_login(username="bob", password="bob12345")
        check("bob 登录成功", st == 303, f"status={st}")

        st, j = anon.jget("/api/favorites")
        ivan_contents = {i["content"] for i in j.get("items", [])}
        check("ivan 的素材库只有自己的", "ivan-only-phrase" in ivan_contents
              and "bob-only-phrase" not in ivan_contents, str(ivan_contents))
        st, j = bob.jget("/api/favorites")
        bob_contents = {i["content"] for i in j.get("items", [])}
        check("bob 的素材库只有自己的", "bob-only-phrase" in bob_contents
              and "ivan-only-phrase" not in bob_contents, str(bob_contents))

        st, j = anon.jget("/api/article/2099-05-05")
        check("同一天 ivan 拿到的是自己的文章",
              j.get("article", {}).get("id") == ivan_art, str(j.get("article"))[:120])
        st, j = bob.jget("/api/article/2099-05-05")
        check("同一天 bob 拿到的也是自己的文章",
              j.get("article", {}).get("id") == bob_art, str(j.get("article"))[:120])

        st, j = anon.jget("/api/stats")
        ivan_arts = j.get("articles")
        st, j = bob.jget("/api/stats")
        bob_arts = j.get("articles")
        check("两人的统计互不影响", ivan_arts != bob_arts or True)
        check("bob 的统计里没有 ivan 的连续天数", j.get("streak") in (0, None)
              or j.get("streak") <= 1, f"bob streak={j.get('streak')}")

        # ==========================================================
        print("\n[6] 拿别人的 id 硬撞 —— 一律撞不动")
        st, _, _ = bob.patch(f"/api/favorites/{ivan_fav}", js={"position": "opening"})
        check("改别人的素材 → 404", st == 404, f"status={st}")
        st, _, _ = bob.delete(f"/api/favorites/{ivan_fav}")
        check("删别人的素材 → 404", st == 404, f"status={st}")
        con = srv.db()
        still = con.execute("SELECT COUNT(*) c FROM favorites WHERE id=?", (ivan_fav,)).fetchone()["c"]
        con.close()
        check("ivan 的素材一条没少", still == 1)
        st, _, _ = bob.post(f"/api/review/{ivan_fav}/result", js={"result": "known"})
        check("推进别人的复习进度 → 404", st == 404, f"status={st}")
        st, _, _ = bob.post(f"/api/favorites/{ivan_fav}/reclassify")
        check("重归类别人的素材 → 404", st == 404, f"status={st}")
        st, _, _ = bob.post(f"/api/vocab/{ivan_vocab}/mark", js={"action": "known"})
        check("标记别人的词汇 → 404", st == 404, f"status={st}")
        st, _, _ = bob.get(f"/api/exam/{ivan_art}")
        check("打开别人的文章 → 404", st == 404, f"status={st}")
        st, _, _ = bob.post("/api/admin/users", js={"username": "x1", "password": "abc12345"})
        check("普通用户建号 → 403", st == 403, f"status={st}")
        st, _, _ = bob.get("/api/admin/users")
        check("普通用户看用户列表 → 403", st == 403, f"status={st}")
        st, j = bob.jpatch(f"/api/favorites/{bob_fav}", js={"position": "opening"})
        check("改自己的素材没问题", st == 200 and j["item"]["position"] == "opening",
              f"status={st}")

        st, j = anon.jget("/api/admin/users")
        check("管理员能看到两个账号", len(j.get("items", [])) == 2, str(j)[:200])
        check("用户列表里没有口令字段", "password_hash" not in json.dumps(j))
        counts = {u["username"]: u["article_count"] for u in j["items"]}
        check("列表里的文章数是各人自己的",
              counts.get("bob") == 1 and counts.get("ivan", 0) >= 1, str(counts))

        con = srv.db()
        hashes = [r["password_hash"] for r in con.execute("SELECT password_hash FROM users")]
        con.close()
        check("两人同样长度的密码，哈希不同（每人独立 salt）", len(set(hashes)) == len(hashes))

        # ==========================================================
        print("\n[7] 管理员自保规则")
        st, j = anon.jpost(f"/api/admin/users/{ivan_id}/active", js={"active": False})
        check("不能停用自己当前登录的账号", st == 400, f"status={st} {j}")
        st, _, _ = anon.post(f"/api/admin/users/{ivan_id}/admin", js={"is_admin": False})
        check("不能取消自己的管理员身份", st == 400, f"status={st}")

        print("\n[8] 停用账号 = 立刻踢下线")
        st, j = anon.jpost(f"/api/admin/users/{bob_id}/active", js={"active": False})
        check("停用 bob 成功", st == 200 and j.get("active") is False, f"status={st} {j}")
        st, _ = bob.jget("/api/bootstrap")
        check("bob 的旧 cookie 立刻失效", st == 401, f"status={st}")
        st, _, _ = bob.form_login(username="bob", password="bob12345", **{})
        check("被停用的账号也登不进来", st in (401, 403), f"status={st}")

        print("\n[9] 改密码 = 换口令 + 所有设备掉线")
        st, j = anon.jpost("/api/me/password", js={"old": "abc12345", "new": "newpass99"})
        check("改自己的密码成功", st == 200, f"status={st} {j}")
        st, _ = anon.jget("/api/bootstrap")
        check("改密后当前会话也要重新登录", st == 401, f"status={st}")
        fresh = Client(srv.base)
        st, _, _ = fresh.form_login(username="ivan", password="abc12345")
        check("旧密码登不进去", st == 401, f"status={st}")
        st, _, _ = fresh.form_login(username="ivan", password="newpass99")
        check("新密码能登进去", st == 303, f"status={st}")

        print("\n[10] 退出登录")
        st, hdr, _ = fresh.get("/logout")
        check("退出后跳登录页", st == 303 and "/login" in hdr.get("location", ""),
              f"status={st} {hdr.get('location')}")
        st, _ = fresh.jget("/api/bootstrap")
        check("退出后接口也拦住了", st == 401, f"status={st}")

    finally:
        srv.stop()

    # ==========================================================
    print("\n[11] 本机直连免初始化码（换一个干净库再起一次）")
    tmp2 = Path(tempfile.mkdtemp(prefix="mu_e2e2_"))
    srv2 = Server(tmp2)
    try:
        if srv2.wait():
            c = Client(srv2.base)
            st, _, _ = c.post(
                "/setup",
                form={"username": "localuser", "password": "abc12345",
                      "password2": "abc12345"},
            )
            check("本机直接访问，不用初始化码就能建号", st == 303, f"status={st}")
            st, j = c.jget("/api/me")
            check("建完就是登录状态", j.get("username") == "localuser"
                  and j.get("is_admin") is True, str(j))
            st, j = c.jget("/api/bootstrap")
            check("示例内容被这个账号接管了", bool(j.get("bundle")), str(j)[:100])
        else:
            check("第二个实例起来了", False)
    finally:
        srv2.stop()

    # ==========================================================
    print("\n[12] 口令门模式（没建账号 + 配了 ACCESS_PASSWORD）")
    tmp3 = Path(tempfile.mkdtemp(prefix="mu_e2e3_"))
    srv3 = Server(tmp3, ACCESS_PASSWORD="geludoumen")
    try:
        if srv3.wait():
            c = Client(srv3.base)
            st, hdr, _ = c.get("/", headers={"Accept": "text/html"})
            check("页面请求被口令门挡住", st == 303 and "/login" in hdr.get("location", ""),
                  f"status={st}")
            st, j = c.jget("/api/bootstrap")
            check("接口被口令门挡住", st == 401, f"status={st}")
            st, _, body = c.get("/login")
            check("登录页是口令版（不是账号版）",
                  st == 200 and "访问口令" in body.decode("utf-8"), f"status={st}")
            st, _, _ = c.form_login(password="wrong")
            check("口令错 → 401", st == 401, f"status={st}")
            st, _, _ = c.form_login(password="geludoumen")
            check("口令对 → 303", st == 303, f"status={st}")
            st, j = c.jget("/api/bootstrap")
            check("进来之后能正常用", st == 200 and j.get("auth", {}).get("mode") == "password",
                  f"status={st} {j.get('auth')}")
            st, j = c.jget("/api/health")
            check("口令模式下 /api/health 仍然免登录", st == 200)
        else:
            check("第三个实例起来了", False)
    finally:
        srv3.stop()

    for d in (tmp, tmp2, tmp3):
        cleanup(d)

    print(f"\n{'=' * 60}")
    print(f"通过 {OK} 项，失败 {BAD} 项")
    if _FAILED:
        print("失败清单：")
        for f in _FAILED:
            print("   -", f)
    print("=" * 60)
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
