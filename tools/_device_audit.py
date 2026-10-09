# -*- coding: utf-8 -*-
"""多设备尺寸实测：iPhone / iPad 各档位下，界面到底长什么样。

为什么要量而不是看：
  「适配 iPad」很容易被做成「把内容拉满 1366px」—— 但那是错的：
  英文正文的舒适行长是 60~75 字符，拉到 1300px 会到 150+ 字符，读起来眼睛要来回扫。
  所以必须先量出「现在各尺寸下真实的行长 / 溢出 / 点击目标」，
  再决定哪些该放大、哪些该保持。

做法（沿用本项目已验证的两招）：
  1. 一个 iframe 就是一个「设备视口」—— iframe 内部的媒体查询按 iframe 尺寸求值，
     所以能在一个 Chrome 进程里量完所有档位，iframes 全摆在屏幕外。
  2. 测量结果由页面自己 PUT 回 /api/annotations（借 qa: 前缀），
     绕开 --dump-dom 对多 iframe 异步链路的时序不可靠问题。

线上库、线上 8848 全程不碰：临时库由真实库快照而来。

跑法：python tools/_device_audit.py
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

from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8894                      # 和别的测试脚本错开
BASE = f"http://127.0.0.1:{PORT}"

TMP = Path(tempfile.mkdtemp(prefix="devaudit_"))
DB = TMP / "app.db"
SRC = ROOT / "data" / "app.db"
if not SRC.exists():
    sys.exit("找不到 data/app.db")

# ---- 真实库快照 + 还原成「没有账号」的年代（否则 / 会 303 到 /login） ----
s = sqlite3.connect(SRC)
dd = sqlite3.connect(DB)
dd.__enter__()
s.backup(dd)
dd.close()
s.close()

c = sqlite3.connect(DB)
for t in ("articles", "events", "favorites", "study_log", "user_profile", "annotations"):
    try:
        c.execute(f"UPDATE {t} SET user_id=0")
    except sqlite3.OperationalError:
        pass
c.execute("DELETE FROM sessions")
c.execute("DELETE FROM users")
c.commit()
art = c.execute(
    "SELECT id, date, length(body) AS n FROM articles WHERE source_type != 'exam' ORDER BY date DESC LIMIT 1"
).fetchone()
c.close()
if not art:
    sys.exit("副本里没有可用的每日文章")
print(f"临时库 {DB}\n今日文章 id={art[0]}（{art[1]}），正文 {art[2]} 字符\n")

env = dict(os.environ)
env.update({
    "DB_PATH": str(DB).replace("\\", "/"),
    "AI_API_KEY": "",
    "DAILY_GENERATE_HOUR": "23",
    "ACCESS_PASSWORD": "",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONPATH": str(ROOT),
})
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

FRAME = ROOT / "web" / "_qa_device.html"
SHOTFRAME = ROOT / "web" / "_qa_shot.html"   # 出图用：精确尺寸 iframe 的承载页

# ---------------------------------------------------------------- 脚手架
PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>device audit</title>
<style>
  html,body{margin:0;background:#111;color:#ddd;font:12px/1.5 monospace}
  /* ⚠️ 设备 iframe 必须留在**正常文档流**里：放到 left:-300000px 那种离屏位置，
     Chrome 不给它发 rAF 帧，await rAF 的等待会死锁（本次踩过）。
     这里不做截图，重叠/超宽都无所谓，只求「真的被渲染」。 */
  .dev{display:block;margin:0}
  iframe{border:0;display:block;background:#fff}
  #out{white-space:pre-wrap;padding:8px}
</style></head><body>
<div id="out">running</div>
<script>
const CFG = __PAYLOAD__;
/* 单位：CSS px。取自真机逻辑分辨率。 */
const DEVICES = [
  { n: "iPhone SE 一代",    w: 320,  h: 568  },
  { n: "iPhone SE",        w: 375,  h: 667  },
  { n: "iPhone 15 Pro Max", w: 430, h: 932  },
  { n: "iPhone 15 横屏",    w: 852,  h: 393  },
  { n: "iPad mini 竖",      w: 744,  h: 1133 },
  { n: "iPad Pro 11 竖",    w: 834,  h: 1194 },
  { n: "iPad Pro 12.9 竖",  w: 1024, h: 1366 },
  { n: "iPad Pro 12.9 横",  w: 1366, h: 1024 },
];

const log = [];
const out = document.getElementById("out");
function L(s) {
  log.push(s);
  out.textContent = log.join("\n");
  try {
    fetch("/api/annotations", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "qa:device",
        data: { v: 1, w: 1, strokes: [{ c: log.join("\n"), s: 0, p: [[log.length, 0]] }] } }),
    }).catch(() => {});
  } catch (e) {}
}

/* rAF 两帧；但**必须带定时器兜底**：万一这个 window 没在发帧（离屏/未渲染的 iframe），
   只 await rAF 会永远不 resolve。谁先到用谁。 */
function raf(win) {
  return new Promise(r => {
    let done = false;
    const fin = () => { if (!done) { done = true; r(); } };
    try { win.requestAnimationFrame(() => win.requestAnimationFrame(fin)); }
    catch (e) { /* 跨域之类，交给定时器 */ }
    setTimeout(fin, 24);
  });
}

/* ⚠️ 等待一律用 rAF 死等，**不要用 setTimeout**：
   --virtual-time-budget 会把定时器快进烧掉，轮询会瞬间跑完几百次然后判定超时。 */
function loadFrame(f, url) {
  return new Promise(res => { f.addEventListener("load", () => res(), { once: true }); f.src = url; });
}

/* 等应用把今日文章真的渲染出来。
   判据必须带上「#bodyText 有真实宽度」—— 它在 <section id="articleWrap" hidden> 里，
   光看「有子节点」会在 articleWrap 还没取消 hidden 时就通过，量出来全是 0。 */
async function waitReady(win, d, tries = 600) {
  for (let i = 0; i < tries; i++) {
    const bt = d.getElementById("bodyText");
    if (bt && bt.children.length > 0 && bt.getBoundingClientRect().width > 8) return true;
    await raf(win);
  }
  return false;
}

/* 用 canvas 量当前字体下「一个字符平均多宽」，折算出一行多少字符 */
function charsPerLine(win, d, el, cs) {
  const cv = d.createElement("canvas").getContext("2d");
  cv.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const sample = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const avg = cv.measureText(sample).width / sample.length;
  return avg > 0 ? Math.round(el.getBoundingClientRect().width / avg) : 0;
}

function overflow(win, d) {
  const ox = d.documentElement.scrollWidth - win.innerWidth;
  const bad = [];
  if (ox > 0) {
    for (const el of d.querySelectorAll("body *")) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > win.innerWidth + 1) {
        bad.push((el.id ? "#" + el.id : el.tagName.toLowerCase()) +
                 (el.className ? "." + String(el.className).split(" ")[0] : "") +
                 "@" + Math.round(r.right));
        if (bad.length >= 4) break;
      }
    }
  }
  return { ox, bad };
}

function minTap(win, d, sel) {
  const rs = [...d.querySelectorAll(sel)].map(e => e.getBoundingClientRect()).filter(r => r.width > 0);
  if (!rs.length) return 0;
  return Math.round(Math.min(...rs.map(r => Math.min(r.width, r.height))));
}

async function measure(dev) {
  const box = document.createElement("div");
  box.className = "dev";
  const f = document.createElement("iframe");
  f.width = dev.w; f.height = dev.h;
  f.setAttribute("width", dev.w); f.setAttribute("height", dev.h);
  box.appendChild(f); document.body.appendChild(box);
  /* 直接 loadFrame：不要去等 about:blank 那次 load —— 刚 createElement 出来的
     iframe 不一定发得出那次 load，等下去就是死锁。 */
  /* ⚠️ 必须是 `/#today`，不能是 `/`：2026-10-07 起**主界面改成科目选择页**了，
     开 `/` 落的是科目选择，`#bodyText`（今日文章正文）永远不出现，
     waitReady 必然判定「应用没渲染出来」—— 8 个档全会假失败。 */
  await loadFrame(f, "/#today");

  const w = f.contentWindow, d = f.contentDocument;
  if (!await waitReady(w, d)) { L(`[${dev.n}] 应用没渲染出来`); return null; }

  const bt = d.getElementById("bodyText");
  const bcs = w.getComputedStyle(bt);
  const main = d.querySelector("main");
  const tb = d.querySelector(".topbar");
  const brand = w.getComputedStyle(d.querySelector(".brand"));
  const hero = d.querySelector(".hero-title");
  const ov = overflow(w, d);

  const rec = {
    n: dev.n, vw: dev.w, vh: dev.h,
    ox: ov.ox, bad: ov.bad,
    mainW: Math.round(main.getBoundingClientRect().width),
    textW: Math.round(bt.getBoundingClientRect().width),
    cpl: charsPerLine(w, d, bt, bcs),
    bodyFs: parseFloat(bcs.fontSize),
    bodyLh: +(parseFloat(bcs.lineHeight) / parseFloat(bcs.fontSize)).toFixed(2),
    topbarH: Math.round(tb.getBoundingClientRect().height),
    brandFs: parseFloat(brand.fontSize),
    heroFs: hero ? parseFloat(w.getComputedStyle(hero).fontSize) : 0,
    tapTop: minTap(w, d, ".topbar .icon-btn"),
    padBottom: w.getComputedStyle(d.body).paddingBottom,
    safeTop: w.getComputedStyle(tb).paddingTop,
    /* --topbar-h 是粘性小标题（.page-head）的 top 偏移，必须等于顶栏真实高度，
       写死/不同步就会出现「小标题被顶栏压住」。 */
    varTopH: Math.round(parseFloat(
      w.getComputedStyle(d.documentElement).getPropertyValue("--topbar-h")) || 0),
    /* 窄屏「连续天数」应当只在 hero 的 chip 里出现一次：
       topbar 的 .streak 必须 display:none，hero 里才该多出一个 chip。 */
    streakDisp: d.getElementById("streakChip")
      ? w.getComputedStyle(d.getElementById("streakChip")).display : "?",
    heroChipN: d.querySelectorAll("#heroChips .chip").length,
    topbarRightW: Math.round(d.querySelector(".topbar-right").getBoundingClientRect().width),
  };

  /* 解析区要翻开才量得到（默认 hidden）。宽屏档应该让解析区比正文列更宽。 */
  const an = d.getElementById("analysis");
  const rc = d.getElementById("readingCard");
  rec.readCardW = rc ? Math.round(rc.getBoundingClientRect().width) : 0;
  if (an) {
    an.hidden = false;
    await raf(w);
    rec.analysisW = Math.round(an.getBoundingClientRect().width);
  } else {
    rec.analysisW = 0;
  }

  /* 批注工具栏：开一下量完再关掉 */
  try {
    w.Pen.setMode(true);
    await raf(w);
    const bar = d.getElementById("penBar");
    const br = bar.getBoundingClientRect();
    const rs = [...bar.querySelectorAll("button")].map(e => e.getBoundingClientRect()).filter(r => r.width > 0);
    const rows = new Set(rs.map(r => Math.round(r.top))).size;
    rec.penH = Math.round(br.height);
    rec.penW = Math.round(br.width);
    rec.penRows = rows;
    rec.penMinTap = rs.length ? Math.round(Math.min(...rs.map(r => Math.min(r.width, r.height)))) : 0;
    rec.penOnPadBottom = w.getComputedStyle(d.body).paddingBottom;
    w.Pen.setMode(false);
    await raf(w);
  } catch (e) {
    rec.penH = -1;
  }
  /* ---- 英语任务页顶栏几何（坑位最多的一屏：任务区 6 + 批注 + 账号 + 设置）----
     用户 2026-10-07 把主界面收成三样之后，这一屏就是唯一的极限情况。

     ⚠️⚠️ 落点必须是**英语模块**的页面：任务类入口按白名单只在它们上面出现
     （app.js EN_TASK_VIEWS）。原来这里是切 `#math` —— 改白名单之后数学页
     那一排全被藏了，量到一堆 0，看着"全达标"其实什么都没测到。
     直接用已经加载好的 `/#today`（它就在白名单里）。

     ⚠️ 光看 overflowX=0 会漏掉「可收缩的 flex 子项被压扁 / 品牌被截断」：
     430px 上品牌被截成「Pri」、图标互相压，宽度却一点没溢出（真实踩到）。 */
  try {
    await raf(w); await raf(w); await raf(w);
    const tr = d.querySelector(".topbar-right");
    const kids = [...tr.children].filter(el => el.getClientRects().length);
    const bEl = d.querySelector(".brand");
    const dEl = d.getElementById("topDate");
    const ics = [...tr.querySelectorAll(".icon-btn")].filter(el => el.getClientRects().length);
    rec.taskRightW = Math.round(tr.getBoundingClientRect().width);
    rec.taskKids = kids.map(el => Math.round(el.getBoundingClientRect().width)).join(",");
    rec.taskMiniIconW = ics.length
      ? Math.round(Math.min(...ics.map(el => el.getBoundingClientRect().width))) : -1;
    rec.taskAcctW = Math.round(d.getElementById("btnAccount").getBoundingClientRect().width);
    rec.taskBrandW = Math.round(bEl.getBoundingClientRect().width);
    const mEl = d.querySelector(".brand-mark");
    rec.taskMarkW = mEl ? Math.round(mEl.getBoundingClientRect().width) : -1;
    rec.taskMarkH = mEl ? Math.round(mEl.getBoundingClientRect().height) : -1;
    rec.taskLeftEdgeToRight = Math.round(tr.getBoundingClientRect().left -
                                         bEl.getBoundingClientRect().right);
    rec.taskBrandClipped =
      bEl.scrollWidth > Math.round(bEl.getBoundingClientRect().width) + 1 ? 1 : 0;
    rec.taskDateClipped =
      dEl && dEl.scrollWidth > Math.round(dEl.getBoundingClientRect().width) + 1 ? 1 : 0;
    rec.taskDateW = dEl ? Math.round(dEl.getBoundingClientRect().width) : -1;
    rec.taskOver = Math.round(Math.max(
      0, ...[...tr.querySelectorAll("*")].map(el => el.getBoundingClientRect().right - w.innerWidth)));
    /* 任务区自己有没有真的摊开 —— 白名单写错就会"全藏起来还全绿"（新版真正的坑） */
    const tsk = d.getElementById("topbarTask");
    rec.taskBarH = tsk && tsk.getClientRects().length
      ? Math.round(tsk.getBoundingClientRect().height) : 0;
    rec.taskBarBtns = ["#streakChip", "#btnReview", "#btnSubjects",
                       "#btnLibrary", "#btnStats", "#btnHistory"]
      .filter(s => { const e = d.querySelector(s); return e && e.getClientRects().length; }).length;
  } catch (e) { rec.taskErr = String((e && e.message) || e); }

  /* ---- 数学页顶栏几何：两个**文字**入口比图标按钮宽得多，是最容易挤爆的一屏 ----
     ⚠️ 又踩了一次「落点要选对」：这里必须切到 `#math`，因为数学入口组只在
        数学视图出现（body.view-math）。在英语页上量它只会得到 0。 */
  try {
    w.location.hash = "#math";
    await raf(w); await raf(w); await raf(w); await raf(w);
    const mb = d.getElementById("topbarTaskMath");
    rec.mathBarH = mb && mb.getClientRects().length
      ? Math.round(mb.getBoundingClientRect().height) : 0;
    rec.mathTabsN = ["#btnMathLib", "#btnMathDrill"]
      .filter(s => { const e = d.querySelector(s); return e && e.getClientRects().length; }).length;
    const mtr = d.querySelector(".topbar-right");
    rec.mathOver = mtr ? Math.round(Math.max(
      0, ...[...mtr.querySelectorAll("*")].map(el => el.getBoundingClientRect().right - w.innerWidth))) : -1;
    const mv = d.getElementById("topbarTask");
    rec.mathEnTaskHidden = mv && mv.getClientRects().length ? 1 : 0;
    rec.mathTabMinW = rec.mathTabsN
      ? Math.round(Math.min(...["#btnMathLib", "#btnMathDrill"]
          .map(s => d.querySelector(s))
          .filter(e => e && e.getClientRects().length)
          .map(e => e.getBoundingClientRect().width))) : -1;
  } catch (e) { rec.mathErr = String((e && e.message) || e); }

  L("DEV " + JSON.stringify(rec));
  return rec;
}

(async () => {
  try {
    L("START 设备数=" + DEVICES.length);
    for (const dev of DEVICES) await measure(dev);
    L("DONE");
  } catch (e) {
    L("ERR:" + String((e && e.message) || e));
  }
  document.title = "READY";
})();
</script></body></html>"""


SHOT_PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>device shot</title>
<style>
  html,body{margin:0;padding:0;background:#fff;overflow:hidden}
  iframe{border:0;display:block;background:#fff}
</style></head><body>
<script>
  const q = new URLSearchParams(location.search);
  const w = +(q.get("w") || 393), h = +(q.get("h") || 852);
  const src = q.get("src") || "/";
  if (q.get("probe")) {
    /* probe=1：不建 iframe，只把**真实视口**写成文本，供 --dump-dom 读回。
       （不能在承载页里用 fetch 回报：那一次请求会在 Chrome 退出前被掐掉。） */
    document.body.textContent = "VPROBE " + innerWidth + "x" + innerHeight
      + " need=" + w + "x" + h;
  } else {
    const f = document.createElement("iframe");
    f.width = w; f.height = h;
    f.setAttribute("width", w); f.setAttribute("height", h);
    f.style.width = w + "px"; f.style.height = h + "px";
    f.src = src;
    document.body.appendChild(f);
  }
</script></body></html>
"""

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path: str):
    try:
        with OPENER.open(BASE + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def read_report(tries: int = 90) -> str:
    """取回页面回传的报告。先等 DONE，等不到就把现有的部分报告拿回来好排查。"""
    partial = ""
    for _ in range(tries):
        rep = get("/api/annotations?prefix=qa")
        strokes = ((rep or {}).get("items") or {}).get("qa:device", {}).get("strokes") or []
        text = strokes[0].get("c") if strokes else ""
        if text:
            partial = text
            if "DONE" in text:
                return text
        time.sleep(0.5)
    return partial


try:
    for _ in range(80):
        try:
            OPENER.open(BASE + "/api/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        sys.exit("临时实例没起来")

    FRAME.write_text(PAGE.replace("__PAYLOAD__", json.dumps({}, ensure_ascii=False)), encoding="utf-8")

    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--window-size=1200,900", "--virtual-time-budget=600000",
         "--dump-dom", f"{BASE}/static/_qa_device.html"],
        capture_output=True, timeout=900,
    )
    report = read_report()

    if not report:
        print("没读到测量报告"); sys.exit(1)

    rows = []
    for line in report.split("\n"):
        if line.startswith("DEV "):
            rows.append(json.loads(line[4:]))

    if not rows:
        print("报告里没有 DEV 行：\n" + report); sys.exit(1)

    # ---------------- 打印成表 ----------------
    print("=" * 118)
    print("实测（数字均为 CSS px；cpl = 正文一行约多少字符，英文阅读舒适区 60~75）")
    print("=" * 118)
    hdr = f"{'设备':<18}{'视口':<12}{'溢出':>5}{'正文宽':>7}{'cpl':>5}{'正文字号':>9}" \
          f"{'行高':>6}{'顶栏高':>7}{'顶栏按钮':>9}{'批注栏高':>9}{'批注行数':>9}{'批注最小':>9}"
    print(hdr)
    print("-" * 118)
    for r in rows:
        print(f"{r['n']:<18}{str(r['vw']) + 'x' + str(r['vh']):<12}{r['ox']:>5}{r['mainW']:>7}"
              f"{r['cpl']:>5}{r['bodyFs']:>9}{r['bodyLh']:>6}{r['topbarH']:>7}{r['tapTop']:>9}"
              f"{r.get('penH', -1):>9}{r.get('penRows', -1):>9}{r.get('penMinTap', -1):>9}")

    print("\n横向溢出明细（overflowX > 0 的）：")
    any_ov = False
    for r in rows:
        if r["ox"] > 0:
            any_ov = True
            print(f"  {r['n']}：多出 {r['ox']}px → {r['bad']}")
    if not any_ov:
        print("  无")

    print("\n排查")
    fail = 0

    def chk(cond, label):
        global fail
        print(("  [OK]   " if cond else "  [FAIL] ") + label)
        if not cond:
            fail += 1

    tap_floor = lambda r: 36 if r["vw"] < 375 else 40   # 320px 那档实在放不下 40

    chk(all(r["ox"] <= 0 for r in rows), "所有尺寸都没有横向溢出")
    # 任务态顶栏（坑位最多）：只有这几项才看得见「被压扁 / 被截断」
    chk(all(r.get("taskOver", 0) <= 0 for r in rows), "任务态顶栏不横向溢出")
    # 英语页任务区必须**真的摊开**（≥4 个入口；手机档会藏掉连续天数与「选科目」）
    chk(all(r.get("taskBarH", 0) > 0 and r.get("taskBarBtns", 0) >= 4 for r in rows),
        "英语页顶栏任务区真的摊开（隐含：白名单没写反）")
    # ---- 数学页：只该有数学自己的 2 个入口，且不许挤爆/溢出 ----
    chk(all(r.get("mathTabsN", 0) == 2 for r in rows),
        "数学页 2 个数学入口都在（错题总库 / 开始刷题）")
    chk(all(r.get("mathBarH", 0) > 0 for r in rows), "数学页入口组真的摊开了")
    chk(all(r.get("mathEnTaskHidden", 1) == 0 for r in rows),
        "数学页没混进英语任务区")
    chk(all(r.get("mathOver", 0) <= 0 for r in rows), "数学页顶栏不横向溢出")
    chk(all(r.get("mathTabMinW", 0) >= 40 for r in rows),
        "数学入口的点击目标没被挤小")
    chk(all(r.get("taskLeftEdgeToRight", -99) >= 4 for r in rows),
        "任务态顶栏：棱镜标记与右侧按钮不重叠")
    # 手机档是**故意**只留棱镜标记（文字与日期挪走，见 style.css 窄机顶栏段），
    # 所以这里只盯「标记本体没被压扁」；品牌文字只在宽屏上要求完整。
    chk(all(r.get("taskMarkW", 0) >= 14 and r.get("taskMarkH", 0) >= 14 for r in rows),
        "任务态顶栏棱镜标记没被压扁（横向挤压曾把它压到 5px 宽）")
    chk(all(r.get("taskBrandClipped", 0) == 0 for r in rows if r["vw"] > 560),
        "宽屏上品牌文字没被截断")
    chk(all(r.get("taskMiniIconW", 0) >= (36 if r["vw"] < 375 else 38) for r in rows),
        "任务态顶栏图标点击目标没被挤小")
    chk(all(r.get("taskDateClipped", 1) == 0 for r in rows if r["vw"] > 560),
        "宽屏上顶栏日期不被截断")
    chk(all(40 <= r["cpl"] <= 100 for r in rows if r["vw"] >= 700),
        "宽屏上正文行长落在可读区间（40~100 字符）")
    chk(all(r["cpl"] <= 80 for r in rows if r["vw"] >= 700),
        "宽屏上正文不会长到一行 80 字符以上（拉宽就难读了）")
    chk(all(r["tapTop"] >= tap_floor(r) for r in rows),
        f"顶栏点击目标达标（≥375px 要 40px，320px 那档 36px）")
    chk(all(r.get("penMinTap", 0) >= tap_floor(r) for r in rows if r.get("penH", 0) > 0),
        "批注工具栏按钮达标")
    chk(all(r.get("penRows", 9) <= 2 for r in rows if r.get("penH", 0) > 0),
        "批注工具栏不超过 2 行")
    chk(all(r.get("penRows", 9) == 1 for r in rows if r["vw"] >= 700),
        "平板及以上批注工具栏是单排（修掉 50vw 宽度陷阱后的效果）")
    chk(all(abs(r["varTopH"] - r["topbarH"]) <= 1 for r in rows),
        "--topbar-h 与顶栏实测高度一致（粘性小标题不会被压住）")
    chk(all(r["bodyFs"] >= 17 for r in rows if r["vw"] >= 700),
        "大屏正文字号 ≥17px（拿远看也舒服）")
    chk(all(r["bodyFs"] >= 19 for r in rows if r["vw"] >= 1200),
        "宽屏正文字号 ≥19px（iPad Pro 横屏不该还是手机的字号）")
    chk(all(r.get("analysisW", 0) > r.get("readCardW", 0)
            for r in rows if r["vw"] >= 1200),
        "宽屏上解析区比正文列更宽（宽度用在真正需要的地方）")
    chk(all(r["padBottom"] != "104px" for r in rows),
        "批注时底部留白按实测高度算（不再是写死的 104px）")
    # 窄屏「连续天数」不许同时出现在两处
    chk(all(r["streakDisp"] == "none" for r in rows if r["vw"] <= 560),
        "窄屏顶栏的连续天数已隐藏（改由今日页 chip 承担）")
    chk(all(r["streakDisp"] != "none" for r in rows if r["vw"] > 560),
        "宽屏顶栏仍显示连续天数")
    print("\n任务态顶栏几何（5 任务入口 + 批注 + 账号 + 设置）：")
    for r in rows:
        print(f"  {r['n']:<18} 右侧宽={r.get('taskRightW')}  子项={r.get('taskKids')}"
              f"  最小图标={r.get('taskMiniIconW')}  账号={r.get('taskAcctW')}"
              f"  品牌={r.get('taskBrandW')}(截断={r.get('taskBrandClipped')})"
              f"  日期={r.get('taskDateW')}(截断={r.get('taskDateClipped')})"
              f"  数学入口={r.get('mathTabsN')}(宽{r.get('mathTabMinW')},溢出{r.get('mathOver')})"
              f"  任务区高={r.get('taskBarH')}(入口={r.get('taskBarBtns')})"
              f"  品牌→右侧间距={r.get('taskLeftEdgeToRight')}  溢出={r.get('taskOver')}"
              f"{('  ERR=' + r['taskErr']) if r.get('taskErr') else ''}")

    print("\n「连续天数」落位：")
    for r in rows:
        print(f"  {r['n']:<18} 顶栏 .streak display={r['streakDisp']:<6} "
              f"hero chips={r['heroChipN']}  顶栏右侧宽={r['topbarRightW']}")

    # 批注工具栏不许压住正文最后一行
    print("\n附：各尺寸关键数值")
    for r in rows:
        print(f"  {r['n']:<18} 正文 {r['textW']}px / 字号 {r['bodyFs']}px / 行高 {r['bodyLh']} / "
              f"顶栏 {r['topbarH']}px（--topbar-h={r['varTopH']}） / "
              f"正文卡 {r['readCardW']} / 解析区 {r['analysisW']} / "
              f"body 底部留白 {r['padBottom']} → 批注时 {r.get('penOnPadBottom', '-')}")

    # ---------------- 出图：用"精确尺寸的 iframe"承载设备视口 ----------------
    # ⚠️ 不能用 --window-size 直接当设备视口。实测（本机 Chrome）：
    #      · --window-size 是 **CSS 像素**，--force-device-scale-factor=D 只放大光栅；
    #      · 最终 PNG = window-size × D（不是「CSS × D」那套想当然的算法）；
    #      · window 与 window.innerWidth 之间还有一段**不固定的**差值
    #        （实测宽 500 差 9px、宽 786 差 18px；高恒差 96px）
    #      → 视口宽度对不准：iPhone 会量成 768px、iPad 竖会量成 2048px，
    #        拿到的是平板/桌面版式（第一版就是这样，把图读出来才发现）。
    #    所以改成：把首页装进一个 width/height **精确等于设备尺寸**的 iframe，
    #    再按 iframe 的 rect 裁掉四周留白。裁切用 PIL，像素级可控、可断言。
    print("\n出图（设备视口 = 精确尺寸的 iframe，按 rect 裁切）")
    DSF = 2
    SHOTFRAME.write_text(SHOT_PAGE, encoding="utf-8")
    for name, dev_w, dev_h, label in (("iphone15", 393, 852, "iPhone 15"),
                                      ("ipad_竖", 1024, 1366, "iPad Pro 12.9 竖"),
                                      ("ipad_横", 1366, 1024, "iPad Pro 12.9 横")):
        png = ROOT / "data" / f"_device_{name}.png"
        raw = TMP / f"_raw_{name}.png"
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
             f"--force-device-scale-factor={DSF}",
             # ⚠️ 留白必须给够：Chrome 对窗口宽度有**最小值**（本机实测约 522px 外层）。
             #    写 393+80=473 会被悄悄抬到 522 → 实际 innerWidth 504，
             #    虽然 iframe 还是装得下，但"给足了留白"这句话就不成立了。
             f"--window-size={dev_w + 200},{dev_h + 320}",
             "--virtual-time-budget=20000", f"--screenshot={raw}",
             f"{BASE}/static/_qa_shot.html?w={dev_w}&h={dev_h}&src=/"],
            capture_output=True, timeout=180,
        )
        # 先用**同尺寸**的 --dump-dom 探针确认"真实视口 ≥ 设备尺寸"：
        # 窗口不够大时 iframe 会被裁掉，而裁切是按 iframe 的 rect 做的 ——
        # 不查就会静默出半张图（比例对、内容缺）。dump-dom 比 fetch 回报可靠。
        dom = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--force-device-scale-factor={DSF}",
             f"--window-size={dev_w + 200},{dev_h + 320}",
             "--virtual-time-budget=4000", "--dump-dom",
             f"{BASE}/static/_qa_shot.html?probe=1&w={dev_w}&h={dev_h}"],
            capture_output=True, timeout=120,
        ).stdout.decode("utf-8", "ignore")
        m = re.search(r"VPROBE (\d+)x(\d+) need=(\d+)x(\d+)", dom)
        room = bool(m) and int(m.group(1)) >= dev_w and int(m.group(2)) >= dev_h
        info = f"{m.group(1)}x{m.group(2)}" if m else "读不到"
        if raw.exists():
            with Image.open(raw) as im:
                im.crop((0, 0, dev_w * DSF, dev_h * DSF)).save(png)
            with Image.open(png) as im2:
                got = im2.size
        else:
            got = (0, 0)
        chk(room, f"出图视口够大（{label}：窗口给足 {dev_w}x{dev_h} + 留白）")
        chk(got == (dev_w * DSF, dev_h * DSF),
            f"出图尺寸精确（{label}：{got[0]}x{got[1]}，期望 {dev_w * DSF}x{dev_h * DSF}）")
        print(f"    {label:<18} {dev_w}x{dev_h} → {png.name}  "
              f"{png.stat().st_size if png.exists() else '不存在'} bytes"
              f"   （真实视口 {info}）")

    print(f"\n== {fail} 项待修 ==")
    sys.exit(0)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    FRAME.unlink(missing_ok=True)
    SHOTFRAME.unlink(missing_ok=True)
    cleanup(TMP)
