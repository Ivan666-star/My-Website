"""自助注册 + 管理员批准的回归。

验的是这条新规则：**别人可以自己申请账号，但必须管理员点头之后才能用。**

重点盯三类容易出事的地方：
  1. 首个自助申请者会不会把系统锁死 —— 如果「有没有账号」按总人数算，
     /setup 就被关掉了，而他还没被批准、也登不进去 → 谁都进不来。
  2. 待批准状态会不会被误当成「已停用」（两者都在 users 表里，语义完全不同）。
  3. 待批准的人会不会被调度器当成正常用户，每天白生成一份没人看的文章。

自己起、自己收（临时库 + 临时端口），跑完不留痕迹，**不碰线上 8848**。
跑法：python tools/_register_test.py
"""
from __future__ import annotations

import http.cookiejar
import json
import os
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

    def delete(self, p, **k):
        return self.req("DELETE", p, **k)

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

    def cookies(self) -> list[str]:
        return [c.name for c in self.jar]


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

    def user(self, username: str) -> dict | None:
        con = self.db()
        try:
            r = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return dict(r) if r else None
        finally:
            con.close()


def scheduler_ids(db_path: Path) -> list[int] | None:
    """在独立进程里直接问调度器：它今天会为哪些人生成内容。

    单独跑一个进程，是为了不污染测试进程的 app 模块状态；
    先覆盖 settings.db_path 再导入 —— 这样不受项目根 .env 的影响。
    返回 None = 这个子进程没跑起来（宁可跳过，也不给假结论）。
    """
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from app.config import settings\n"
        f"settings.db_path = Path({str(db_path)!r})\n"
        "from app.scheduler import active_user_ids\n"
        "print('@@' + json.dumps(active_user_ids()))\n"
    )
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        print("      （调度器子进程失败：%s）" % r.stderr.strip()[-300:])
        return None
    for line in reversed(r.stdout.splitlines()):
        if line.startswith("@@"):
            return json.loads(line[2:])
    return None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="prism_reg_"))
    srv = Server(tmp)
    try:
        if not srv.wait():
            print("实例没起来，中止")
            return 1
        anon = Client(srv.base)

        # ==========================================================
        print("\n[1] 注册入口：无账号时也能用，且不会把系统锁死")
        st, _, body = anon.get("/register")
        check("GET /register → 200", st == 200, f"status={st}")
        check("注册页有申请表单", "提交申请" in body.decode("utf-8", "replace"))

        st, _, _ = anon.post("/register", form={
            "username": "ab", "password": "pw123456", "password2": "pw123456"})
        check("用户名太短 → 400", st == 400, f"status={st}")

        st, _, _ = anon.post("/register", form={
            "username": "friend1", "password": "pw123456", "password2": "nope1234"})
        check("两次密码不一致 → 400", st == 400, f"status={st}")

        st, _, body = anon.post("/register", form={
            "username": "friend1", "password": "pw123456", "password2": "pw123456",
            "display_name": "小王"})
        check("提交申请 → 200", st == 200, f"status={st}")
        check("拿到「申请已提交」页", "申请已提交" in body.decode("utf-8", "replace"))
        check("提交后没有自动登录", "kaoyan_auth" not in anon.cookies(),
              str(anon.cookies()))

        st, _, _ = anon.post("/register", form={
            "username": "friend1", "password": "pw123456", "password2": "pw123456"})
        check("同名不能重复注册 → 400", st == 400, f"status={st}")

        u = srv.user("friend1")
        check("库里建出了 friend1", bool(u))
        check("状态是「待批准」approved=0", bool(u) and u["approved"] == 0,
              str(u and u["approved"]))
        check("没有被误标成停用 active=1", bool(u) and u["active"] == 1,
              str(u and u["active"]))

        st, _, body = anon.get("/setup")
        check("★ 有待批准账号时 /setup 仍可用（没被锁死）", st == 200, f"status={st}")
        check("★ 且还是建管理员的页面", "创建管理员" in body.decode("utf-8", "replace"))

        ids = scheduler_ids(tmp / "app.db")
        if ids is None:
            print("  · 跳过：调度器子进程没起来")
        else:
            check("★ 待批准账号不进每日生成（此时没有任何可生成对象）", ids == [],
                  f"ids={ids}")

        # ==========================================================
        print("\n[2] 建管理员 → 批准 → 才能登录")
        admin = Client(srv.base)
        st, hdr, _ = admin.post("/setup", form={
            "username": "boss", "password": "boss12345", "password2": "boss12345",
            "display_name": "管理员"})
        check("建第一个管理员 → 303", st == 303, f"status={st}")
        check("管理员已登录", "kaoyan_auth" in admin.cookies())

        st, j = admin.jget("/api/admin/users")
        check("管理页能读出账号列表", st == 200, f"status={st}")
        check("待批准计数 = 1", j.get("pending_count") == 1, str(j.get("pending_count")))
        check("自助注册开关为开", j.get("self_register") is True, str(j.get("self_register")))
        pend = [x for x in j.get("items", []) if x["username"] == "friend1"]
        # 管理接口给的是原始行（0/1，不是布尔），前端按真值判断 —— 跟 active 一致
        check("friend1 出现在列表里且 approved=0",
              bool(pend) and not pend[0]["approved"],
              str(pend[:1]))

        fid = srv.user("friend1")["id"]

        # 批准之前：登不进去
        f1 = Client(srv.base)
        st, _, body = f1.post("/login", form={
            "username": "friend1", "password": "pw123456"})
        check("★ 未批准账号登录被拒 → 403", st == 403, f"status={st}")
        check("★ 提示说的是「等批准」而不是密码错",
              "批准" in body.decode("utf-8", "replace"))
        st, _ = f1.jget("/api/bootstrap")
        check("未批准账号拿不到数据 → 401", st == 401, f"status={st}")

        # 批准
        st, j = admin.jpost(f"/api/admin/users/{fid}/approve", js={"approved": True})
        check("批准接口 → ok", st == 200 and j.get("ok") is True, f"status={st} {j}")
        u = srv.user("friend1")
        check("库里 approved 变成 1", u["approved"] == 1, str(u["approved"]))

        st, j = admin.jget("/api/admin/users")
        check("待批准计数归零", j.get("pending_count") == 0, str(j.get("pending_count")))

        st, hdr, _ = f1.post("/login", form={
            "username": "friend1", "password": "pw123456"})
        check("★ 批准后能登录 → 303", st == 303, f"status={st}")
        check("拿到会话 cookie", "kaoyan_auth" in f1.cookies(), str(f1.cookies()))
        st, j = f1.jget("/api/bootstrap")
        check("批准后能读自己的数据", st == 200, f"status={st}")

        # 数据仍然互相看不到
        st, ja = admin.jget("/api/bootstrap")
        st2, jf = f1.jget("/api/bootstrap")
        a_ids = {a["id"] for a in (ja.get("bundle") or {}).get("articles", [])} \
            if isinstance(ja.get("bundle"), dict) else set()
        f_ids = {a["id"] for a in (jf.get("bundle") or {}).get("articles", [])} \
            if isinstance(jf.get("bundle"), dict) else set()
        check("两个人的内容不重叠（隔离仍然成立）", not (a_ids & f_ids),
              f"admin={a_ids} friend={f_ids}")

        # ==========================================================
        print("\n[3] 拒绝：删掉待批准账号，用户名释放出来")
        st, _, _ = anon.post("/register", form={
            "username": "friend2", "password": "pw123456", "password2": "pw123456"})
        check("再注册一个 → 200", st == 200, f"status={st}")
        f2id = srv.user("friend2")["id"]

        f2 = Client(srv.base)
        st, _, _ = f2.post("/login", form={"username": "friend2", "password": "pw123456"})
        check("friend2 未批准时登录 → 403", st == 403, f"status={st}")

        st, j = admin.jget("/api/admin/users")
        check("待批准计数 = 1", j.get("pending_count") == 1, str(j.get("pending_count")))

        st, _, _ = admin.post(f"/api/admin/users/{f2id}/approve", js={"approved": False})
        check("撤回批准不报错", st == 200, f"status={st}")

        st, _, _ = admin.delete(f"/api/admin/users/{f2id}")
        check("★ 拒绝（删除）→ ok", st == 200, f"status={st}")
        check("账号真的没了", srv.user("friend2") is None)

        st, _, _ = anon.post("/register", form={
            "username": "friend2", "password": "pw123456", "password2": "pw123456"})
        check("★ 同名可以重新申请（用户名已释放）", st == 200, f"status={st}")

        # ==========================================================
        print("\n[4] 删除的安全绳")
        st, _, j = admin.delete(f"/api/admin/users/{fid}")
        check("已批准的账号不能删 → 400", st == 400, f"status={st} {j}")
        boss_id = srv.user("boss")["id"]
        st, _, _ = admin.delete(f"/api/admin/users/{boss_id}")
        check("不能删自己 → 400", st == 400, f"status={st}")

        stranger = Client(srv.base)
        st, _, _ = stranger.delete(f"/api/admin/users/{fid}")
        check("没登录不能删账号 → 401", st == 401, f"status={st}")
        st, _ = f1.jget("/api/admin/users")
        check("普通账号读不到管理接口 → 403", st == 403, f"status={st}")

        # ==========================================================
        print("\n[5] 待批准账号不进每日生成（有管理员时）")
        anon.post("/register", form={
            "username": "friend3", "password": "pw123456", "password2": "pw123456"})
        f3id = srv.user("friend3")["id"]
        f2row = srv.user("friend2")   # [3] 里删掉后又重新申请过，现在也是待批准
        ids = scheduler_ids(tmp / "app.db")
        if ids is None:
            print("  · 跳过：调度器子进程没起来")
        else:
            # friend1 在 [2] 里已经批准过，所以他该在里面；待批准的都不该在
            check("★ 两位已批准账号都在生成名单里",
                  ids is not None and set(ids) == {fid, boss_id},
                  f"ids={ids} 已批准={{{fid},{boss_id}}}")
            check("★ 待批准的 friend3 不在生成名单里", f3id not in ids,
                  f"ids={ids} friend3={f3id}")
            if f2row:
                check("待批准的 friend2 也不在生成名单里", f2row["id"] not in ids,
                      f"ids={ids} friend2={f2row['id']}")

        # ==========================================================
        print("\n[6] 待批准计数只看 approved，与是否停用无关")
        # 此刻待批准的有两个：重新申请的 friend2、以及 friend3。
        # 把 friend3 停用之后，他仍然算「还没被批准」—— 管理员还是得处理他，
        # 所以计数不该因为停用而变小。
        con = srv.db()
        con.execute("UPDATE users SET active=0 WHERE username='friend3'")
        con.commit()
        con.close()
        st, j = admin.jget("/api/admin/users")
        check("被停用的待批准账号仍计入待批准（管理员还得处理）",
              j.get("pending_count") == 2, str(j.get("pending_count")))
    finally:
        srv.stop()
        cleanup(tmp)

    # ==========================================================
    print("\n[7] 开关：ALLOW_SELF_REGISTER=0 时关掉注册入口")
    tmp2 = Path(tempfile.mkdtemp(prefix="prism_reg_off_"))
    srv2 = Server(tmp2, ALLOW_SELF_REGISTER="0")
    try:
        if not srv2.wait():
            print("实例没起来，跳过开关这一组")
        else:
            c = Client(srv2.base)
            st, _, _ = c.get("/register")
            check("GET /register → 403", st == 403, f"status={st}")
            st, _, _ = c.post("/register", form={
                "username": "nobody1", "password": "pw123456", "password2": "pw123456"})
            check("POST /register → 403", st == 403, f"status={st}")
            check("关掉后没建出账号", srv2.user("nobody1") is None)
            # 管理员照常能手动开号
            st, _, _ = c.post("/setup", form={
                "username": "boss2", "password": "boss12345", "password2": "boss12345"})
            check("关掉注册不影响建管理员", st == 303, f"status={st}")
            st, j = c.jget("/api/admin/users")
            check("管理页给出「注册已关闭」", j.get("self_register") is False,
                  str(j.get("self_register")))
            st, _, _ = c.post("/api/admin/users", js={
                "username": "manual1", "password": "pw123456"})
            check("管理员手动开号仍然可用且立即可用", st == 200, f"status={st}")
            m = srv2.user("manual1")
            check("手动开的号直接是已批准", bool(m) and m["approved"] == 1,
                  str(m and m["approved"]))
    finally:
        srv2.stop()
        cleanup(tmp2)

    # ==========================================================
    print("\n" + "=" * 60)
    print(f"通过 {OK} 项，失败 {BAD} 项")
    if _FAILED:
        print("失败清单：")
        for x in _FAILED:
            print(f"  - {x}")
    print("=" * 60)
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
