# -*- coding: utf-8 -*-
"""账号界面渲染回归：用真 Chrome 走一遍「设置里的我的账号」和「用户管理整页」。

为什么不只做接口测试：
    接口全绿但页面点不动 / 渲染不出来，是这个项目最容易出的问题
    （事件委托绑错节点、renderTarget 指向隐藏容器…都不会报错）。
    所以用同源 iframe 真跑一遍，断言写进父页面 DOM，再用 --dump-dom 读回来。

自己起、自己收：临时库 + 临时端口，跑完删掉临时页，**不碰线上 8848**。
跑法：python tools/_admin_view_test.py
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import shutil
import socket
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

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FRAME = WEB / "_qa_admin.html"
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

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


# ==========================================================
# 驱动页：先登录（同源 fetch，Set-Cookie 会被浏览器收下），再驱动应用
# ==========================================================
FRAME_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>QA admin</title>
<style>
html,body{margin:0;padding:0;background:#fff}
iframe{border:0;display:block;background:#fff}
#qa{padding:10px;font:12px/1.6 monospace;white-space:pre-wrap}
</style></head><body>
<iframe id="f"></iframe>
<div id="qa">running</div>
<script>
const Q = new URLSearchParams(location.search);
const SCENE = Q.get("scene") || "admin";
const USER = Q.get("user") || "";
const PASS = Q.get("pass") || "";
const f = document.getElementById("f");
f.style.width = "430px"; f.style.height = "1100px";

const LOG = [];
const emit = (ok, msg) => {
  LOG.push((ok ? "PASS" : "FAIL") + " | " + msg);
  document.getElementById("qa").textContent = LOG.join("\n");
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const txt = el => (el ? el.textContent : "");

async function login() {
  if (!USER) return true;
  const body = new URLSearchParams({ username: USER, password: PASS, next: "/" });
  const r = await fetch("/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  return r.ok || r.status === 200 || r.status === 303;
}

async function waitFor(fn, ms) {
  ms = ms || 20000;
  for (let w = 0; w < ms; w += 120) {
    try { if (fn()) return true; } catch (e) { /* 还没准备好 */ }
    await sleep(120);
  }
  return false;
}

async function main() {
  const okLogin = await login();
  emit(okLogin, "登录请求已发出（" + (USER || "免登录") + "）");

  f.src = "/";
  const win = await new Promise(res => f.addEventListener("load", () => res(f.contentWindow), { once: true }));
  const doc = f.contentDocument;
  const errs = [];
  win.addEventListener("error", e => errs.push("JS:" + e.message));
  win.addEventListener("unhandledrejection", e => errs.push("REJ:" + ((e.reason && e.reason.message) || e.reason)));

  const ready = await waitFor(() => {
    const load = doc.getElementById("loadingState");
    return typeof win.navigate === "function" && load && load.hidden === true;
  });
  emit(ready, "应用加载完成");
  if (!ready) { emit(false, "应用没起来，后面没法测"); return; }

  const openSheet = () => new Promise(res => {
    doc.getElementById("btnSettings").click();
    res();
  });

  const sheetLoaded = () => {
    const b = doc.getElementById("sheetBody");
    return b && b.textContent.length > 40 && b.textContent.indexOf("加载中") < 0;
  };

  if (SCENE === "open") {
    /* 还没建账号：设置里应该只有一个「创建第一个账号」的入口 */
    await openSheet();
    await waitFor(sheetLoaded);
    await sleep(400);
    const s = txt(doc.getElementById("sheetBody"));
    emit(s.indexOf("单人自用") >= 0, "设置里说明了「单人自用」状态");
    emit(!!doc.getElementById("btnGotoSetup"), "有「创建第一个账号」按钮");
    emit(!doc.getElementById("btnAdmin"), "没有用户管理入口（还没建号）");
    emit(errs.length === 0, "没有 JS 报错 " + errs.join(" / "));
    return;
  }

  const isAdmin = SCENE === "admin" || SCENE === "pending";
  const expectAdminUI = isAdmin;

  /* ---- 场景 1：设置里的「我的账号」 ---- */
  await openSheet();
  await waitFor(sheetLoaded);
  await sleep(400);
  const sheet = txt(doc.getElementById("sheetBody"));
  emit(sheet.indexOf("我的账号") >= 0, "设置里有「我的账号」分组");
  emit(sheet.indexOf("当前登录") >= 0, "显示了当前登录的是谁");
  emit(sheet.indexOf("@" + USER) >= 0, "显示了用户名 @" + USER, sheet.slice(0, 200));
  emit(!!doc.getElementById("btnChangePwd"), "有「修改密码」按钮");
  emit(!!doc.getElementById("btnLogout"), "有「退出登录」按钮");
  emit(!!doc.getElementById("btnAdmin") === expectAdminUI,
       (expectAdminUI ? "管理员能看到「打开用户管理」" : "普通用户看不到用户管理入口"));
  emit(doc.querySelectorAll(".u-chip.u-admin").length === (expectAdminUI ? 1 : 0),
       "管理员标记只在管理员身上出现");

  /* ---- 场景 2：整页渲染 ---- */
  doc.getElementById("sheet").hidden = true;
  win.navigate("admin");
  await waitFor(() => {
    const b = doc.getElementById("pageBody-admin");
    return b && b.textContent.indexOf("加载中") < 0 && b.textContent.length > 30;
  });
  await sleep(600);
  const body = doc.getElementById("pageBody-admin");
  const bt = txt(body);
  emit(doc.getElementById("page-admin").hidden === false, "#admin 整页显示出来了");

  if (isAdmin) {
    emit(bt.indexOf("共 2 个已批准的账号") >= 0, "列出 2 个已批准的账号", bt.slice(0, 260));
    emit(body.querySelectorAll(".u-row:not(.u-pending)").length === 2, "渲染出 2 行已批准的账号");
    /* 自助注册 + 批准：没有待批账号时不该冒出待批区块，但要给出注册入口 */
    if (SCENE === "admin") {
      emit(body.querySelectorAll(".u-row.u-pending").length === 0,
           "没有待批准账号时不出现待批行");
      emit(body.querySelector('[data-uop="approve"]') === null,
           "没有待批准账号时不给「批准」按钮");
    }
    emit(bt.indexOf("/register") >= 0, "给出自助注册入口地址给管理员转发");
    emit(!!doc.getElementById("nuName") && !!doc.getElementById("nuPass"),
         "有「添加账号」表单");
    emit(!!doc.getElementById("btnNewUser"), "有「创建账号」按钮");
    emit(body.querySelector('[data-uop="pwd"]') !== null, "有「重置密码」按钮");
    emit(body.querySelector('[data-uop="active"]') !== null, "有「停用」按钮");
    emit(body.querySelector('[data-uop="admin"]') !== null, "有「设为管理员/取消」按钮");
    emit(bt.indexOf("这是你") >= 0, "自己那一行标了「这是你」");
    emit(body.querySelector(".u-row.u-me") !== null, "自己那一行有高亮样式");
    const meRow = body.querySelector(".u-row.u-me");
    emit(meRow && meRow.querySelector('[data-uop="kick"]') === null,
         "自己那一行没有「踢下线」（免得把自己踢了）");
    emit(body.querySelectorAll('[data-uop="kick"]').length === 1,
         "别人那一行才有「踢下线」");
  } else {
    emit(bt.indexOf("管理员") >= 0, "普通用户看到的是权限提示", bt.slice(0, 200));
    emit(body.querySelectorAll(".u-row").length === 0, "普通用户看不到用户列表");
    emit(!doc.getElementById("btnNewUser"), "普通用户看不到建号表单");
  }

  /* ---- 场景 3：点一下「重置密码」的弹层，确认事件委托是活的 ---- */
  if (isAdmin) {
    body.querySelector('[data-uop="pwd"]').click();
    await waitFor(() => doc.getElementById("rpNew"));
    emit(!!doc.getElementById("rpNew"), "点「重置密码」弹出了改密表单");
    emit(doc.getElementById("sheet").hidden === false, "弹层打开了");
    doc.getElementById("sheet").hidden = true;
  }

  /* ---- 场景 4：有待批准申请时，申请者要单独占一块、给批准/拒绝 ---- */
  if (SCENE === "pending") {
    emit(bt.indexOf("条申请待批准") >= 0, "出现待批准区块", bt.slice(0, 300));
    emit(body.querySelectorAll(".u-row.u-pending").length === 1, "待批申请渲染成一行");
    emit(body.querySelectorAll(".u-chip.u-wait").length === 1, "申请者带「等批准」标记");
    emit(body.querySelector('[data-uop="approve"]') !== null, "给出「批准」按钮");
    emit(body.querySelector('[data-uop="reject"]') !== null, "给出「拒绝」按钮");
    emit(body.querySelectorAll('[data-uop="approve"]').length === 1, "「批准」只在申请者那一行出现");
    emit(bt.indexOf("共 2 个已批准的账号") >= 0,
         "已批准列表仍是 2 个人（申请者不算进去）", bt.slice(0, 300));
    emit(body.querySelectorAll(".u-row:not(.u-pending)").length === 2,
         "待批申请不混进已批准列表");
  }

  emit(errs.length === 0, "没有 JS 报错 " + errs.join(" / "));
}

main().catch(e => emit(false, "驱动脚本异常：" + e.message));
</script>
</body></html>"""


# ==========================================================
# 简易 HTTP 客户端（Python 侧准备数据用）
# ==========================================================
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.ProxyHandler({}),
            NoRedirect(),
        )

    def req(self, method, path, *, form=None, js=None):
        data, hdr = None, {}
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        elif js is not None:
            data = json.dumps(js).encode()
            hdr["Content-Type"] = "application/json"
        rq = urllib.request.Request(self.base + path, data=data, method=method, headers=hdr)
        try:
            with self.op.open(rq, timeout=30) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_chrome(base: str, scene: str, user: str = "", password: str = "") -> str:
    url = f"{base}/static/_qa_admin.html?scene={scene}&user={urllib.parse.quote(user)}&pass={urllib.parse.quote(password)}"
    r = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--no-sandbox", "--disable-dev-shm-usage",
         "--virtual-time-budget=60000", "--window-size=1600,1400",
         "--dump-dom", url],
        capture_output=True, timeout=180,
    )
    return r.stdout.decode("utf-8", "replace")


def parse(html: str) -> tuple[list[str], list[str]]:
    box = re.search(r'<div id="qa">(.*?)</div>', html, re.S)
    raw = box.group(1) if box else ""
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = (raw.replace("&lt;", "<").replace("&gt;", ">")
              .replace("&quot;", '"').replace("&amp;", "&"))
    passed, failed = [], []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("PASS |"):
            passed.append(line[7:])
        elif line.startswith("FAIL |"):
            failed.append(line[7:])
    return passed, failed


def verdict(passed: list[str], failed: list[str], title: str) -> None:
    print(f"\n  —— {title} ——")
    for p in passed:
        check(p, True)
    for f in failed:
        check(f, False, "（浏览器里断言失败）")


def main() -> int:
    if not Path(CHROME).exists():
        print("找不到 Chrome，跳过：", CHROME)
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="qa_admin_"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env.update(DB_PATH=str(tmp / "app.db"), AI_API_KEY="", DAILY_GENERATE_HOUR="23",
               ACCESS_PASSWORD="", SETUP_CODE="", TZ="Asia/Shanghai")

    FRAME.write_text(FRAME_HTML, encoding="utf-8")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    try:
        c = Client(base)
        up = False
        for _ in range(120):
            if proc.poll() is not None:
                print(proc.stdout.read().decode("utf-8", "replace")[-2500:])
                break
            try:
                if c.req("GET", "/api/health")[0] == 200:
                    up = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        if not up:
            check("临时服务起来了", False)
            return 1
        print(f"临时实例：{base}")

        # ---- 场景一：还没建账号 ----
        print("\n[1] 还没建账号时，设置里只给一个「创建第一个账号」")
        html = run_chrome(base, "open")
        verdict(*parse(html), "open 模式")

        # ---- 准备数据：建管理员 + 一个普通账号 ----
        st, _, body = c.req("POST", "/setup", form={
            "username": "ivan", "password": "abc12345",
            "password2": "abc12345", "display_name": "Ivan"})
        if st != 303:
            check("用 /setup 建出管理员", False, f"status={st} {body[:200]}")
            return 1
        check("用 /setup 建出管理员", True)
        st, _, body = c.req("POST", "/api/admin/users", js={
            "username": "bob", "password": "bob12345", "display_name": "小 Bob"})
        check("管理员建出普通账号 bob", st == 200, f"status={st} {body[:160]}")

        # ---- 场景二：管理员 ----
        print("\n[2] 管理员看设置 + 用户管理整页")
        html = run_chrome(base, "admin", "ivan", "abc12345")
        passed, failed = parse(html)
        verdict(passed, failed, "admin 场景")
        if not passed and not failed:
            print("  !! 浏览器没回结果，DOM 片段：")
            print(html[:1500])

        # ---- 场景三：普通用户 ----
        print("\n[3] 普通用户看设置 + 撞用户管理页")
        html = run_chrome(base, "member", "bob", "bob12345")
        passed, failed = parse(html)
        verdict(passed, failed, "member 场景")
        if not passed and not failed:
            print("  !! 浏览器没回结果，DOM 片段：")
            print(html[:1500])

        # ---- 场景四：有人自助申请，管理员看「待批准」那块 ----
        print("\n[4] 有人自助申请后，管理员看到待批准申请")
        guest = Client(base)  # 未登录的新客户端去申请
        st, _, body = guest.req("POST", "/register", form={
            "username": "newbie", "password": "newbie123",
            "password2": "newbie123", "display_name": "小新"})
        check("自助申请提交成功（不是登录、只是提交）", st == 200, f"status={st} {body[:160]}")
        html = run_chrome(base, "pending", "ivan", "abc12345")
        passed, failed = parse(html)
        verdict(passed, failed, "pending 场景")
        if not passed and not failed:
            print("  !! 浏览器没回结果，DOM 片段：")
            print(html[:1500])

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
        FRAME.unlink(missing_ok=True)
        cleanup(tmp)

    print(f"\n{'=' * 58}")
    print(f"通过 {OK} 项，失败 {BAD} 项")
    if _FAILED:
        print("失败清单：")
        for f in _FAILED:
            print("   -", f)
    print("=" * 58)
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
