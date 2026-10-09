# -*- coding: utf-8 -*-
"""后端连不上时，界面必须说实话（专项回归）。

为什么要有这个测试：
  用户真实踩到过 —— 服务停了，界面却显示「还没有配置 AI 模型」，还叫去改 .env。
  成因是一条完整的链路，缺一环都不复现：
    1) sw.js 对非 /api/ 的 GET 是「网络优先、失败回缓存」→ 服务停了，**界面外壳照样能打开**；
    2) 但 /api/bootstrap 的 fetch 直接失败（reject）；
    3) 旧代码把它当成"内容没生成"，而 meta 是空的 → meta.ai_ready 为 undefined
       → 走到「还没有配置 AI 模型」那一支，把人往 .env 上带。
  这里测第 2、3 环（第 1 环由 sw.js 自身逻辑保证，不必牵进浏览器缓存）：
  **起一个静态文件照常、/api/* 直接掐断连接的迷你服务**，让 fetch 真的 reject。

怎么掐断：不写任何响应就把连接关掉 → Chrome 侧是 net::ERR_EMPTY_RESPONSE → fetch reject。
（返回 404/500 是不行的：那是"服务答了但出错"，走不到 offline 分支。）

⚠️ 出图别用 `--window-size=393,852` 直接当手机视口：
   Chrome 对窗口宽度有**最小值**（本机实测约 522px 外层）。写 393 会被悄悄抬到 522，
   实际 innerWidth=504，而 PNG 只有 393 宽 → **右边 111px 被切掉**，
   看上去像"版式溢出"，其实是截图方式错（本次踩过）。
   所以：装进一个**精确 393×852 的 iframe**，窗口给足，按 iframe 的 rect 裁切。

跑法：python tools/_offline_state_test.py
"""
from __future__ import annotations

import http.server
import io
import socket
import subprocess
import sys
import threading
from pathlib import Path

from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8898                       # 和别的测试脚本错开
BASE = f"http://127.0.0.1:{PORT}"
PNG = ROOT / "data" / "_offline_state.png"
DEV_W, DEV_H = 393, 852           # iPhone 15
WIN_W, WIN_H = DEV_W + 200, DEV_H + 320   # 必须大于 Chrome 的窗口最小宽度（约 522）

# 实时服务把 web/ 挂在 /static，index.html 挂在 /；这里照搬，好让相对路径都对
ROUTES = {
    "/": "index.html",
    "/login": "index.html",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/favicon.ico": "favicon.ico",
}

SHOT_PAGE = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>shot</title>
<style>html,body{{margin:0;padding:0;background:#fff;overflow:hidden}}
iframe{{border:0;display:block;background:#fff}}</style></head><body>
<iframe width="{DEV_W}" height="{DEV_H}" style="width:{DEV_W}px;height:{DEV_H}px" src="/"></iframe>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # 关掉访问日志
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/"):
            return "__ABORT__"        # ← 关键：装作"服务根本不在"
        if p in ROUTES:
            return WEB / ROUTES[p]
        if p.startswith("/static/"):
            return WEB / p[len("/static/"):]
        return None

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/__shot":            # 出图承载页（精确尺寸 iframe）
            self._send(SHOT_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        target = self._resolve()
        if target == "__ABORT__":
            # 不写任何字节就断开 → fetch reject（这才是"连不上"）
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.close_connection = True
            return
        if target is None or not Path(target).is_file():
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript",
            ".css": "text/css",
            ".json": "application/json",
            ".webmanifest": "application/manifest+json",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(Path(target).suffix, "application/octet-stream")
        self._send(Path(target).read_bytes(), ctype)


fail = 0


def chk(cond, label):
    global fail
    print(("  [OK]   " if cond else "  [FAIL] ") + label)
    if not cond:
        fail += 1


srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

try:
    # 先确认 /api/* 真的取不到（自检，不然整个测试就是空的）
    import urllib.request

    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    api_dead = False
    try:
        op.open(BASE + "/api/bootstrap", timeout=5)
    except Exception:
        api_dead = True
    chk(api_dead, "自检：迷你服务的 /api/* 确实连不上（否则测不到 offline 分支）")

    with op.open(BASE + "/", timeout=5) as r:
        chk(b"/static/app.js" in r.read(), "自检：静态外壳能正常取到")

    dom = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=8000", "--dump-dom", BASE + "/"],
        capture_output=True, timeout=180,
    ).stdout.decode("utf-8", "ignore")

    print("\n排查")
    chk("连不上后端服务" in dom, "标题直说「连不上后端服务」")
    chk("启动.bat" in dom, "说明里给出可照做的动作（双击 启动.bat）")
    chk("还没有配置 AI 模型" not in dom, "不再误导成「还没有配置 AI 模型」（旧 bug 的那句）")
    chk("今天的内容还没准备好" not in dom, "不再误导成「今天的内容还没准备好」")
    chk('id="btnRetry"' in dom and 'btnRetry" hidden' not in dom, "「重试」按钮已露出")
    # 离线时不该给"生成/导入"这类假指望 —— 按钮存在但必须 hidden
    for bid in ("btnGenToday", "btnExamToday", "btnImport", "btnRegen"):
        seg = dom.split(f'id="{bid}"', 1)
        seg = seg[1][:60] if len(seg) > 1 else ""
        chk("hidden" in seg, f"离线时 {bid} 已隐藏")

    # ---- 出图：精确尺寸 iframe + 按 rect 裁切 ----
    raw = ROOT / "data" / "_offline_state_raw.png"
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         f"--window-size={WIN_W},{WIN_H}", "--virtual-time-budget=8000",
         f"--screenshot={raw}", BASE + "/__shot"],
        capture_output=True, timeout=180,
    )
    if raw.exists():
        with Image.open(raw) as im:
            im.crop((0, 0, DEV_W, DEV_H)).save(PNG)
        with Image.open(PNG) as im2:
            got = im2.size
        raw.unlink(missing_ok=True)
    else:
        got = (0, 0)
    chk(got == (DEV_W, DEV_H), f"离线空态效果图尺寸精确（{got[0]}x{got[1]}，期望 {DEV_W}x{DEV_H}）")
    print(f"  出图：{PNG.name} {PNG.stat().st_size if PNG.exists() else '不存在'} bytes")
finally:
    srv.shutdown()
    srv.server_close()

print(f"\n== {fail} 项待修 ==")
sys.exit(0)
