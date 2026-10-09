# -*- coding: utf-8 -*-
"""给「命题视角」块拍一张图，确认排版没问题。

两个坑（都踩过了，记下来）：
  1. 不能让截图和应用的异步加载抢跑 → Python 先把内容取回来，
     页面直接调应用自己的 renderExamFocus() 渲染，不依赖网络。
  2. **不能用 setTimeout 轮询** —— Chrome 的 --virtual-time-budget 会把定时器
     瞬间烧完，轮询还没等到 iframe 加载就耗尽了。必须挂在 iframe 的 load 事件上。

只拍一次（截图是稀缺资源，不是进度条）。
跑法：python tools/_shot_examfocus.py
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402  （删临时目录要重试，见该模块注释）

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8897
BASE = f"http://127.0.0.1:{PORT}"

SRC_DB = Path(r"C:\Users\Ivan\AppData\Local\Temp\newpipe_bnz4u492\app.db")
if not SRC_DB.exists():
    sys.exit(f"找不到演示库：{SRC_DB}（先跑 tools/_demo_new_pipeline.py）")

TMP = Path(tempfile.mkdtemp(prefix="efshot_"))
DB = TMP / "app.db"
s = sqlite3.connect(SRC_DB)
dd = sqlite3.connect(DB)
dd.__enter__()
s.backup(dd)
dd.close()
s.close()

c = sqlite3.connect(DB)
today = c.execute("SELECT date('now','localtime')").fetchone()[0]
new_id = c.execute("SELECT id FROM articles WHERE date='2099-01-02'").fetchone()[0]
c.execute("DELETE FROM articles WHERE date=?", (today,))
c.execute("UPDATE articles SET date=? WHERE id=?", (today, new_id))
c.commit()
c.close()

env = dict(os.environ)
env.update({"DB_PATH": str(DB), "AI_API_KEY": "", "DAILY_GENERATE_HOUR": "23",
            "ACCESS_PASSWORD": "", "PYTHONIOENCODING": "utf-8"})
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

FRAME = ROOT / "web" / "_qa_shot.html"
shot = ROOT / "data" / "_shot_examfocus.png"

PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>shot</title>
<link rel="stylesheet" href="/static/style.css">
<style>
  html, body { margin: 0; padding: 0; background: #fff; }
  /* iframe 必须足够高：里面的内容是按 iframe 视口裁切的，40px 只会拍到一条缝 */
  iframe { border: 0; display: block; width: 900px; height: 2600px; }
  #qa { position: fixed; left: 0; top: 0; font: 11px monospace; color: #bbb; z-index: -1; }
</style></head><body>
<iframe id="f" src="/"></iframe>
<div id="qa">waiting</div>
<script id="payload" type="application/json">__PAYLOAD__</SCRIPTCLOSE>
<script>
const EF = JSON.parse(document.getElementById("payload").textContent);
const f = document.getElementById("f");
function apply() {
  const doc = f.contentDocument, win = f.contentWindow;
  const qa = document.getElementById("qa");
  if (!doc || !win) { qa.textContent = "no doc"; return false; }
  if (typeof win.renderExamFocus !== "function") {
    qa.textContent = "no renderExamFocus (errors=" + ERRORS.join("|") + ")";
    return false;
  }
  if (!doc.getElementById("examBody") || !doc.getElementById("examSub")) {
    qa.textContent = "no anchors";
    return false;
  }
  win.renderExamFocus(EF);
  const st = doc.createElement("style");
  st.textContent = [
    "* { visibility: hidden !important; }",
    "#todayView, #articleWrap, #analysis { display: block !important; }",
    "#accExam, #accExam * { visibility: visible !important; }",
    "#accExam { display: block !important; position: absolute !important;",
    "           left: 0 !important; top: 0 !important; width: 876px !important;",
    "           margin: 0 !important; border: 0 !important; box-shadow: none !important;",
    "           background: #fff !important; }",
    "#accExam > summary { display: none !important; }",
    "#accExam .acc-body { display: block !important; }"
  ].join("\\n");
  doc.head.appendChild(st);
  const r = doc.getElementById("accExam").getBoundingClientRect();
  qa.textContent = "H=" + Math.round(r.height) + " W=" + Math.round(r.width) +
    " items=" + doc.querySelectorAll("#examBody .ef-item").length +
    " traps=" + doc.querySelectorAll("#examBody .ef-trap").length +
    " sumlen=" + doc.getElementById("examSub").textContent.length;
  document.title = "READY";
  return true;
}
const ERRORS = [];
let done = false;
function go() {
  if (done) return;
  if (apply()) { done = true; return; }
  requestAnimationFrame(go);          // load 之后还差一点点时再补几帧
}
f.addEventListener("load", () => {
  const w = f.contentWindow;
  if (w) w.addEventListener("error", e => ERRORS.push(String(e.message).slice(0, 60)));
  requestAnimationFrame(go);
});
</script></body></html>"""

try:
    for _ in range(80):
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            break
        except Exception:
            time.sleep(0.3)
    else:
        sys.exit("临时实例没起来")

    bundle = json.load(urllib.request.urlopen(f"{BASE}/api/article/{today}", timeout=20))
    ef = (bundle.get("article") or {}).get("exam_focus") or {}
    print(f"取到 exam_focus：pattern={ef.get('pattern')}，设题点 {len(ef.get('set_points') or [])} 个")
    if not ef.get("set_points"):
        sys.exit("exam_focus 里没有 set_points，没法截图")

    FRAME.write_text(
        PAGE.replace("__PAYLOAD__", json.dumps(ef, ensure_ascii=False))
            .replace("</SCRIPTCLOSE>", "</" + "script>"),
        encoding="utf-8",
    )

    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--force-device-scale-factor=1",
         "--virtual-time-budget=45000", "--window-size=900,2400",
         f"--screenshot={shot}", f"{BASE}/static/_qa_shot.html"],
        capture_output=True, timeout=180,
    )
    dom = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=45000", "--dump-dom", f"{BASE}/static/_qa_shot.html"],
        capture_output=True, timeout=180,
    ).stdout.decode("utf-8", "replace")
    m = re.search(r'id="qa">([^<]*)<', dom)
    print("测量：", m.group(1) if m else "（没读到）")
    print("截图：", shot, shot.stat().st_size if shot.exists() else "不存在")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    FRAME.unlink(missing_ok=True)
    cleanup(TMP)
