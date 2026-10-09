# -*- coding: utf-8 -*-
"""给「手写批注」出两张效果图，并验证笔迹真的落库、重开还在。

出图：
  data/_shot_pen.png        正文上用红笔/黑笔/荧光笔批注 + 底部工具栏
  data/_shot_pen_trans.png  中文翻译区批注（说明全站文本区都能写）

验证（全部断言，不靠肉眼）：
  - 顶栏 ✍️ 按钮在、点开工具栏出现、body 进入批注态
  - 用合成的 PointerEvent(pointerType='pen') 在 #bodyText 上"真的画三笔"，
    走的是 onDown / pointermove / pointerup 全流程（不是往数组里塞假数据）
  - 画完数画布上的不透明像素 > 0（笔迹真的画出来了，不只是存了数据）
  - Pen.flush() 之后服务端确实有 3 笔
  - 再开一个全新 iframe 加载同一篇，笔迹自动恢复 3 笔（「关掉再打开还在」）

四个坑（都踩过，改之前先看）：
  1. Chrome 的 --virtual-time-budget 会把 setTimeout 轮询瞬间烧完 → 流程只挂
     iframe 的 load 事件 + async/await，不写定时器轮询。
  2. **必须等到区块真的有尺寸再落笔**：无头环境下 getBoundingClientRect() 会在布局
     落定前返回 0×0，那时 pen.js 的 prepare() 判 visible=false、这一笔会被直接放弃。
     所以先 settle() 轮 rAF 直到尺寸出现（rAF 不是定时器，不受虚拟时间影响）。
  3. --screenshot / --dump-dom 是各自独立的 Chrome 运行；--dump-dom 对"跨两个 iframe
     的长异步流程"经常在半路就 dump。所以测量结果由页面自己 PUT 回服务端，
     Python 侧再 GET 回来 —— 完全不受时序影响，还能拿到中途挂掉时的部分日志。
  4. 第二张图不要靠"点按钮 + 平滑滚动"定位：滚动动画和截图时机在无头下都不稳。
     照 _shot_examfocus.py 的做法，用 CSS 把别的区块全藏掉、只留目标区块。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
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
PORT = 8895
BASE = f"http://127.0.0.1:{PORT}"

TMP = Path(tempfile.mkdtemp(prefix="penshot_"))
DB = TMP / "app.db"

env = dict(os.environ)
env.update({
    "DB_PATH": str(DB).replace("\\", "/"),
    "AI_API_KEY": "",              # 不调模型
    "DAILY_GENERATE_HOUR": "23",   # 不自动生成
    "ACCESS_PASSWORD": "",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONPATH": str(ROOT),
})
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

FRAME = ROOT / "web" / "_qa_pen.html"
shot_body = ROOT / "data" / "_shot_pen.png"
shot_trans = ROOT / "data" / "_shot_pen_trans.png"

PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>pen shot</title>
<link rel="stylesheet" href="/static/style.css">
<style>
  html, body { margin: 0; padding: 0; background: #fff; }
  iframe { border: 0; display: block; width: 900px; }
  #fa { height: 100vh; }
  /* 第二个 iframe 放在窗口之外：照样参与布局和加载，但不会进截图 */
  #fb { position: absolute; left: 0; top: 1500px; height: 1200px; }
  #qa { position: fixed; left: 0; top: 0; z-index: -1; font: 11px monospace; color: #eee; }
</style></head><body>
<iframe id="fa"></iframe>
<iframe id="fb"></iframe>
<div id="qa">waiting</div>
<script id="payload" type="application/json">__PAYLOAD__</SCRIPTCLOSE>
<script>
const CFG = JSON.parse(document.getElementById("payload").textContent);
const QS = new URLSearchParams(location.search);
const FULL = QS.get("full") === "1";          // 要不要跑"重开恢复"那一步
const PROBE = QS.get("probe") === "1";        // 调试角标：出现它 = 截图发生在流程之后
const BLOCK = QS.get("block") || "body";      // body | trans —— 拍哪个区块
const qa = document.getElementById("qa");
const log = [];
const raf = () => new Promise(r => requestAnimationFrame(r));

/* 每记一条就写进 DOM，并且把整份报告当一条"批注"回传服务端。 */
function L(s) {
  log.push(s);
  qa.textContent = log.join("|");
  try {
    fetch("/api/annotations", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        key: FULL ? "qa:full" : "qa:shot",
        data: { v: 1, w: 1, strokes: [{ c: log.join("|"), s: 0, p: [[log.length, 0]] }] },
      }),
    }).catch(() => {});
  } catch (e) { /* 忽略 */ }
}

/* 区块上算出来的 touch-action。iPad 上 Safari 拿不到 touch-action:none
   就会把笔势当成滚屏（Apple 官方：iOS 上 preventDefault 拦不住滚动），
   所以这一项是「Apple Pencil 能不能写」的关键闸门。
   注意元素属于 iframe 文档，得用 iframe 自己的 getComputedStyle。 */
function taOf(w, el) {
  return el ? w.getComputedStyle(el).touchAction : "?";
}

function inkedPx(layer) {
  try {
    const ctx = layer.canvas.getContext("2d");
    const px = ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height).data;
    let n = 0;
    for (let i = 3; i < px.length; i += 4) if (px[i] > 8) n++;
    return n;
  } catch (e) { return "err"; }
}

function loadFrame(f, url) {
  return new Promise(res => {
    f.addEventListener("load", () => res(), { once: true });
    f.src = url;
  });
}

/* 等应用把今日内容渲染出来、批注层也挂好。
   ⚠️ 必须先把 iframe 指到 /#today：`/` 是**科目选择页**（2026-10-07 起），
   那时 #todayView 是 hidden，正文区块量出来是 0×0，而 settle() 靠 rAF 轮询 ——
   无头 + --virtual-time-budget 下 rAF 几乎不发，整轮会静默挂死（真实踩到过）。 */
async function waitApp(f) {
  for (let i = 0; i < 200; i++) {
    const w = f.contentWindow, d = f.contentDocument;
    const body = d && d.getElementById("bodyText");
    if (w && w.Pen && w.Pen._layers && w.Pen._layers.size > 0 &&
        body && body.children.length > 0) return w;
    await raf();
  }
  throw new Error("应用没渲染出来");
}

/* 关键：等到区块真的有尺寸。无头环境下布局落定前 rect 是 0×0，
   这一笔会被 pen.js 直接放弃，所以必须先等它长出来。 */
async function settle(el) {
  for (let i = 0; i < 200; i++) {
    const r = el.getBoundingClientRect();
    if (r.width > 8 && r.height > 8) return r;
    await raf();
  }
  return el.getBoundingClientRect();
}

/* 真的"用笔"画一笔：pointerdown 落在区块上，pointermove / pointerup 挂在 window 上
   —— 和 pen.js 里的监听位置完全一致 */
function penStroke(win, el, pts, name) {
  const r = el.getBoundingClientRect();
  const layer = win.Pen._layers.get(name || "body");
  const mk = (type, x, y) => new win.PointerEvent(type, {
    bubbles: true, cancelable: true, pointerType: "pen", pointerId: 1, isPrimary: true,
    clientX: r.left + x, clientY: r.top + y, button: 0, buttons: type === "pointerup" ? 0 : 1,
  });
  el.dispatchEvent(mk("pointerdown", pts[0][0], pts[0][1]));
  const live = !!(layer && layer.live);
  for (let i = 1; i < pts.length; i++) win.dispatchEvent(mk("pointermove", pts[i][0], pts[i][1]));
  const livePts = (layer && layer.live) ? layer.live.p.length : -1;
  const last = pts[pts.length - 1];
  win.dispatchEvent(mk("pointerup", last[0], last[1]));
  return { live, livePts, after: layer ? layer.strokes.length : -1 };
}

function wave(from, to, y, amp, step) {
  const out = [];
  let i = 0;
  for (let x = from; x <= to; x += step, i++) out.push([x, y + Math.sin(i / 2.2) * amp]);
  return out;
}

async function main() {
  const fa = document.getElementById("fa");
  await loadFrame(fa, "/#today");   // ← 必须是 /#today：/ 现在落在科目选择页
  const w = await waitApp(fa);
  const d = fa.contentDocument;
  const date = CFG.date;
  L("block=" + BLOCK);

  L("btn=" + (d.getElementById("btnPen") ? 1 : 0));
  w.Pen.setDoc(date);
  w.Pen.setMode(true);                       // 等价于点顶栏那个 ✍️
  L("bar=" + (d.getElementById("penBar") && !d.getElementById("penBar").hidden ? 1 : 0));
  L("on=" + (d.body.classList.contains("pen-on") ? 1 : 0));
  /* 批注模式开着 → 区块必须禁掉滚屏手势，否则 iPad 上 Apple Pencil 会变成滚屏 */
  L("ta.on=" + taOf(w, d.getElementById(BLOCK === "trans" ? "transBody" : "bodyText")));
  /* 手指落笔那一档（✋）不该影响 touch-action —— 关了它，iPad 上笔照样要能写 */
  w.Pen._setFinger(false);
  await raf();
  L("ta.offFinger=" + taOf(w, d.getElementById("bodyText")));
  w.Pen._setFinger(true);
  await raf();

  if (BLOCK === "trans") {
    /* 照 _shot_examfocus.py 的做法：只留「中文翻译」这一块，别的全藏掉。
       这样不用滚动、不依赖动画，截图一定落在目标区块上。 */
    d.getElementById("analysis").hidden = false;
    const acc = d.getElementById("accTrans");
    if (acc) acc.open = true;
    const st = d.createElement("style");
    st.textContent = [
      "* { visibility: hidden !important; }",
      "#todayView, #articleWrap, #analysis { display: block !important; }",
      "#accTrans, #accTrans * { visibility: visible !important; }",
      "#accTrans { display: block !important; position: absolute !important;",
      "           left: 0 !important; top: 0 !important; width: 876px !important;",
      "           margin: 0 !important; border: 0 !important; box-shadow: none !important;",
      "           background: #fff !important; }",
      "#accTrans > summary { display: none !important; }",
      "#penBar, #penBar * { visibility: visible !important; }",   // 工具栏也得留着
      "#accTrans .acc-body { display: block !important; }",
    ].join("\\n");
    d.head.appendChild(st);

    const tr = d.getElementById("transBody");
    const rt = await settle(tr);
    L("laid=trans " + Math.round(rt.width) + "x" + Math.round(rt.height));
    w.scrollTo(0, 0);

    w.Pen._setTool("pen", { v: "red", c: "#c0392b", label: "红笔" });
    L("t1=" + JSON.stringify(penStroke(w, tr, wave(24, 250, 76, 1.8, 6), "trans")));
    w.Pen._setTool("pen", { v: "blue", c: "#1f6fd0", label: "蓝笔" });
    L("t2=" + JSON.stringify(penStroke(w, tr, [[34, 106], [92, 107], [150, 106], [210, 108]], "trans")));
    const Lt = w.Pen._layers.get("trans");
    L("transStrokes=" + (Lt ? Lt.strokes.length : -1));
    L("transInked=" + (Lt ? inkedPx(Lt) : -1));
  } else if (!FULL) {
    /* 完整流程那次不重画：笔迹已经由前面的运行真的画过并落库了，
       这次只要看"全新加载一个 iframe 能不能自动恢复"。流程越短越不容易被虚拟时间掐断。 */
    const body = d.getElementById("bodyText");
    const r0 = await settle(body);
    L("laid=" + Math.round(r0.width) + "x" + Math.round(r0.height));
    w.scrollTo(0, Math.max(0, body.getBoundingClientRect().top + w.scrollY - 150));

    /* ① 红笔画下划线（细） */
    w.Pen._setTool("pen", { v: "red", c: "#c0392b", label: "红笔" });
    L("s1=" + JSON.stringify(penStroke(w, body, wave(56, 300, 34, 2.2, 7))));
    /* ② 黑笔把某个词划掉（中） */
    w.Pen._setTool("pen", { v: "ink", c: "#1b1a17", label: "黑笔" });
    L("s2=" + JSON.stringify(penStroke(w, body, [[70, 78], [120, 80], [175, 79], [235, 81], [280, 79]])));
    /* ③ 荧光笔涂一句（粗） */
    w.Pen._setTool("pen", { v: "hl", c: "#ecc400", hl: true, label: "荧光笔" });
    L("s3=" + JSON.stringify(penStroke(w, body, [[62, 116], [120, 117], [180, 116], [240, 118], [300, 116]])));

    const Lb = w.Pen._layers.get("body");
    L("drawn=" + (Lb ? Lb.strokes.length : -1));
    L("pts=" + (Lb ? Lb.strokes.map(s => s.p.length).join(",") : "-"));
    L("inks=" + (Lb ? Lb.strokes.map(s => (s.hl ? "hl" : s.c)).join(",") : "-"));
    L("inked=" + (Lb ? inkedPx(Lb) : "-"));
  }

  await w.Pen.flush();                        // 停笔落库（正常是 900ms 后自动触发）
  L("canvas=" + d.querySelectorAll(".anno-canvas").length);
  await raf(); await raf();
  {
    const l = w.Pen._layers.get(BLOCK === "trans" ? "trans" : "body");
    L("inkedEnd=" + (l ? inkedPx(l) : "-"));
    if (l && l.canvas) {
      const cs = w.getComputedStyle(l.canvas), rr = l.canvas.getBoundingClientRect();
      L("cvinfo=" + JSON.stringify({
        inDoc: d.contains(l.canvas), pos: cs.position, z: cs.zIndex, vis: cs.visibility,
        rect: [Math.round(rr.width), Math.round(rr.height)],
        at: [Math.round(rr.left), Math.round(rr.top)],
      }));
    }
  }

  /* 全新加载一个 iframe：验「关掉再打开，批注还在」（只在 ?full=1 时跑） */
  if (FULL) {
    const fb = document.getElementById("fb");
    fb.style.position = "static";             // 挪进文档流，保证它真的会被加载
    await loadFrame(fb, "/#today");   // 同上
    const w2 = await waitApp(fb);
    await w2.Pen.setDoc("");                  // 先清空
    await w2.Pen.setDoc(date);                // 再从服务端拉一遍（renderBundle 里那次没等回执）
    const l2 = w2.Pen._layers.get("body");
    L("restored=" + (l2 ? l2.strokes.length : -1));
  }

  /* 关掉批注模式 → touch-action 必须回到 auto，否则读文章时手指滑不动 */
  w.Pen.setMode(false);
  await raf(); await raf();
  L("ta.modeOff=" + taOf(w, d.getElementById("bodyText")));
  w.Pen.setMode(true);
  await raf();

  if (PROBE) {                                // 探针：截图里能看见它 = 截图发生在流程走完之后
    const b = document.createElement("div");
    b.textContent = "PROBE-READY";
    b.style.cssText = "position:fixed;right:0;top:0;background:#e00;color:#fff;" +
      "font:16px monospace;padding:6px 12px;z-index:99";
    document.body.appendChild(b);
  }
  document.title = "READY";
}

main().catch(e => {
  L("ERR:" + String((e && e.message) || e));
  document.title = "READY";
});
</script></body></html>"""

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机代理会劫持 127.0.0.1


def get(path: str):
    try:
        with OPENER.open(BASE + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def chrome(*extra: str, budget: int = 300000, dsf: int = 2):
    return subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", f"--force-device-scale-factor={dsf}",
         f"--virtual-time-budget={budget}", *extra],
        capture_output=True, timeout=300,
    )


def report(key: str, must: str, tries: int = 50) -> str:
    """取回页面回传的测量报告。`must` 是本次运行特有的标记，避免读到上一次的残留。"""
    for _ in range(tries):
        rep = get("/api/annotations?prefix=qa")
        strokes = ((rep or {}).get("items") or {}).get(key, {}).get("strokes") or []
        text = strokes[0].get("c") if strokes else ""
        if text and must in text:
            return text
        time.sleep(0.3)
    return ""


try:
    for _ in range(80):
        try:
            OPENER.open(BASE + "/api/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        sys.exit("临时实例没起来")

    boot = get("/api/bootstrap")
    date = boot.get("date") or ""
    if not date:
        sys.exit(f"拿不到今日日期：{boot}")
    print("临时实例今日日期：", date)

    FRAME.write_text(
        PAGE.replace("__PAYLOAD__", json.dumps({"date": date}, ensure_ascii=False))
            .replace("</SCRIPTCLOSE>", "</" + "script>"),
        encoding="utf-8",
    )

    # ① 正文批注图
    chrome("--window-size=900,860", f"--screenshot={shot_body}", f"{BASE}/static/_qa_pen.html")
    shot_qa = report("qa:shot", "cvinfo=")
    print("正文那次：", shot_qa or "（没读到）")

    # ② 中文翻译区批注图
    chrome("--window-size=900,860", f"--screenshot={shot_trans}",
           f"{BASE}/static/_qa_pen.html?block=trans")
    trans_qa = report("qa:shot", "transInked=")
    print("翻译区那次：", trans_qa or "（没读到）")

    # ③ 完整流程（含"重开恢复"）：不截图，只跑一遍读测量
    qa = ""
    for attempt in range(3):
        chrome("--window-size=900,1440", "--dump-dom", f"{BASE}/static/_qa_pen.html?full=1", dsf=1)
        qa = report("qa:full", "restored=")
        if qa:
            break
        print(f"（第 {attempt + 1} 次完整流程没跑完，重试）")
    print("完整流程：", qa or "（没读到）")

    saved = get("/api/annotations?prefix=" + date)
    items = (saved or {}).get("items") or {}
    got = items.get(date + ":body") or {}
    n = len(got.get("strokes") or [])
    print(f"库里批注：{list(items)} → body 笔数 = {n}，点数 =",
          [len(s.get("p") or []) for s in (got.get("strokes") or [])])

    def num(text: str, field: str):
        m = re.search(field + r"=(\d+)", text)
        return int(m.group(1)) if m else -1

    checks = [
        ("顶栏 ✍️ 按钮存在", "btn=1" in shot_qa),
        ("工具栏已弹出", "bar=1" in shot_qa),
        ("页面进入批注态（body.pen-on）", "on=1" in shot_qa),
        # --- iPad / Apple Pencil：touch-action 闸门（Apple 官方：iOS 上 preventDefault 拦不住滚动）---
        ("批注模式下区块 touch-action=none（iPad 上笔才画得动）", "ta.on=none" in shot_qa),
        ("关掉「手指也能写」也不收回 touch-action（笔照样能写）", "ta.offFinger=none" in shot_qa),
        ("退出批注模式后 touch-action=auto（手指恢复滑动）", "ta.modeOff=auto" in shot_qa),
        ("正文区块量到了真实尺寸（不是 0×0）", num(shot_qa, "laid") > 100),
        ("合成笔事件真的画进 3 笔（不是塞假数据）", "drawn=3" in shot_qa),
        ("三种墨色 = 红笔 / 黑笔 / 荧光笔", "inks=#c0392b,#1b1a17,hl" in shot_qa),
        ("8 个可批注区块都挂上了画布", "canvas=8" in shot_qa),
        ("正文画布在流程结束后仍有笔迹像素", num(shot_qa, "inkedEnd") > 900),
        ("换一篇全新加载后笔迹自动恢复 3 笔", "restored=3" in qa),
        ("服务端确实存了 3 笔", n == 3),
        ("翻译区也画进去了 2 笔", "transStrokes=2" in trans_qa),
        ("翻译区画布上有笔迹像素", num(trans_qa, "transInked") > 300),
    ]
    bad = 0
    for label, good in checks:
        print(("  [OK]   " if good else "  [FAIL] ") + label)
        bad += 0 if good else 1
    print(f"== {len(checks) - bad} 通过 / {bad} 失败 ==")
    for f in (shot_body, shot_trans):
        print("截图：", f.name, f.stat().st_size if f.exists() else "不存在")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    FRAME.unlink(missing_ok=True)
    cleanup(TMP)
