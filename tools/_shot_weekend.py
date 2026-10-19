# -*- coding: utf-8 -*-
"""真浏览器验证「周末休息」的界面分支（2026-10-19 用户口径：周末休息）。

为什么还要跑真浏览器：`_weekend_test.py` 已经把后端算得明明白白（周末队列为空、
复习日顺延到周一），但**用户看到的那两句话**是前端按 `rest` 标记分出来的分支：
  · 今日复习页 →「🌴 周末休息，今天不用复习」
  · 错题页 →「🌴 周末休息」那一块
前端写错分支（比如永远走「今天没有要复习的素材」）后端测试是发现不了的。

做法沿用 tools/_shot_notes.py 那套（已验证过的路子）：
  · 起一个**临时实例**：独立 DB（TEMP 里）、ACCESS_PASSWORD=""（开放模式，免登录）、
    AI_API_KEY=""（不花钱）、DAILY_GENERATE_HOUR=23（不自动生成）；
  · 无头 Chrome 打开 QA 页，QA 页用 iframe 装真站点并驱动它；
  · 结果由页面自己 PUT 回服务端（不受截图/时序影响），Python 侧取回断言。

⚠️ 今天多半不是周末，所以「rest 分支」是**在页面里把 REV.rest / MATH.rest 置真**
   （后端在该字段为真时给的就是同一套数据形状），用来验证前端分支本身；
   「后端只在该置真时才置真」由 _weekend_test.py 用打桩日期证明。两边合起来才算闭环。

⚠️ 两个页面分开跑：Chrome 的 `--dump-dom` 会在虚拟时间预算到点时提前收走页面，
   实测一次跑太长，后半段（错题页那几步）会被随机截断。所以：
     _qa_weekend_rev.html  → 接口形状 + 今日复习页；**进来就先摆好「周末休息」画面**，
                              所以即便被提前收走，截图和断言也已经拿到想要的状态；
     _qa_weekend_math.html → 错题页，同样先摆画面再看两个分支。

⚠️ 不碰 data/ 里的真实库：DB_PATH 指向临时目录。

跑法（在 项目/ 目录下）：
    python tools/_shot_weekend.py
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOT_DIR = ROOT.parent / "检测证据" / "2026-10-19-周末休息"
# ⚠️ 存到**仓库外面**（桌面 Prism/检测证据，和 测试工具/qa_stability.py 一个地方）：
#    测试证据永远不进 Git，放 项目/检测证据 会被 git status 当成待提交内容。


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"

TMP = Path(tempfile.mkdtemp(prefix="weekendshot_"))
DB = TMP / "app.db"

# 两页共用的脚手架：日志回传 + iframe realm 里注入代码 + 文本判断
COMMON = r"""
const qa = document.getElementById("qa");
const KEY = document.body.dataset.key;
const log = [];
const raf = () => new Promise(r => requestAnimationFrame(r));
function L(s) {
  log.push(s);
  qa.textContent = log.join("|");
  try {
    fetch("/api/annotations", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: KEY, data: { v: 1, w: 1,
        strokes: [{ c: log.join("|"), s: 0, p: [[log.length, 0]] }] } }),
    }).catch(() => {});
  } catch (e) {}
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
window.onerror = (m, s, l) => { L("JSERR:" + m + "@" + l); };

/* 在 iframe 的**自己的 realm** 里跑一段代码。
   为什么不用 f.contentWindow.REV：REV 是顶层 const，落在全局词法环境里，
   不进 window 对象 —— 只有注入的 <script> 才看得见它。 */
function runInFrame(w, code) {
  const s = w.document.createElement("script");
  s.textContent = code;
  w.document.body.appendChild(s);
  s.remove();
}
function has(el, text) { return !!el && (el.textContent || "").indexOf(text) >= 0; }
async function waitFor(fn, tries, gap) {
  for (let i = 0; i < tries; i++) {
    const v = fn();
    if (v) return v;
    await sleep(gap);
  }
  return null;
}
async function bootFrame() {
  const f = document.getElementById("fa");
  f.src = "/";
  const w = await waitFor(() => {
    try {
      const d = f.contentWindow && f.contentWindow.document;
      return d && d.readyState === "complete" && d.querySelector("#btnReview") ? f.contentWindow : null;
    } catch (e) { return null; }
  }, 300, 40);
  if (!w) return null;
  /* 把 iframe 里的报错捞出来 —— 注入脚本炸了的话，没这个就只剩「日志断在半路」 */
  w.addEventListener("error", e => L("IFERR:" + (e.message || e.type)));
  w.addEventListener("unhandledrejection", e => L("IFREJ:" + ((e.reason && e.reason.message) || e.reason)));
  await sleep(1000);
  return w;
}
"""

PAGE_REVIEW = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>weekend qa · review</title></head><body data-key="qa:weekend-rev" style="margin:0">
<iframe id="fa" style="border:0;width:1100px;height:1000px"></iframe>
<div id="qa" style="font:11px monospace">waiting</div>
<script>__COMMON__
(async () => {
  L("BOOT");
  try {
    const w = await bootFrame();
    if (!w) { L("NO_IFRAME"); return; }
    const d = w.document;
    L("iframe=ok");

    /* ---- ① 接口形状：rest 字段在不在、周中是不是 false ---- */
    const rev = await fetch("/api/review/due").then(r => r.json());
    L("api_has_rest=" + (typeof rev.rest === "boolean" ? 1 : 0));
    L("api_rest=" + (rev.rest ? 1 : 0));
    L("stats_has_rest=" + (rev.stats && typeof rev.stats.rest === "boolean" ? 1 : 0));
    L("today=" + (rev.stats && rev.stats.today));
    const md = await fetch("/api/math/review/due?module=math").then(r => r.json());
    L("math_api_has_rest=" + (typeof md.rest === "boolean" ? 1 : 0));
    L("math_api_rest=" + (md.rest ? 1 : 0));
    /* 后端口径：rest 只该在周六/周日为真 —— 用今天是不是周末反推，两边必须一致 */
    const wd = new Date().getDay();                    // 0 = 周日，6 = 周六
    L("is_weekend=" + (wd === 0 || wd === 6 ? 1 : 0));
    L("rest_matches_weekday=" + ((!!rev.rest) === (wd === 0 || wd === 6) ? 1 : 0));
    L("math_rest_matches=" + ((!!md.rest) === (!!rev.rest) ? 1 : 0));

    /* ---- ② 打开今日复习，**先摆好周末画面**（截图就拍这一版） ---- */
    d.querySelector("#btnReview").click();
    const box = await waitFor(() => {
      const b = d.querySelector("#pageBody-review");
      return b && b.textContent.length > 30 && b.textContent.indexOf("加载中") < 0 ? b : null;
    }, 60, 120);
    L("review_loaded=" + (box ? 1 : 0));
    if (!box) { L("NO_REVIEW_BOX"); return; }
    runInFrame(w, "REV.rest=true;REV.items=[];REV.stats=Object.assign({},REV.stats,{due:0,rest:true});renderReview();");
    L("review_rest_banner=" + (has(box, "🌴 周末休息") ? 1 : 0));
    L("review_rest_hint=" + (has(box, "攒到周一") ? 1 : 0));
    L("review_rest_not_empty=" + (has(box, "今天没有要复习的素材") ? 0 : 1));
    L("review_rest_due_zero=" + ((d.querySelector('[data-rev-stat="due"]') || {}).textContent === "0" ? 1 : 0));
    L("review_rule_note=" + (has(box, "复习只排在工作日") ? 1 : 0));

    /* ---- ③ 反过来：平常日子不许挂周末横幅，且照旧显示「今天没有要复习的素材」 ----
       页面顶部那段「周末休息：复习只排在工作日」的**规则说明**是一直都在的，
       所以这里认的是横幅里的 🌴 图标，不是「周末休息」四个字。 */
    runInFrame(w, "REV.rest=false;REV.items=[];REV.stats=Object.assign({},REV.stats,{due:0,rest:false});renderReview();");
    L("review_normal_empty=" + (has(box, "今天没有要复习的素材") ? 1 : 0));
    L("review_normal_no_banner=" + (has(box, "🌴") ? 0 : 1));

    /* ---- ④ 收尾：画面停回「周末休息」那一版 ---- */
    runInFrame(w, "REV.rest=true;REV.items=[];REV.stats=Object.assign({},REV.stats,{due:0,rest:true});renderReview();");
    L("DONE");
  } catch (e) {
    L("ERR:" + (e && e.message ? e.message : e));
  }
})();
</script></body></html>
""".replace("__COMMON__", COMMON)

PAGE_MATH = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>weekend qa · math</title></head><body data-key="qa:weekend-math" style="margin:0">
<iframe id="fa" style="border:0;width:1100px;height:1000px"></iframe>
<div id="qa" style="font:11px monospace">waiting</div>
<script>__COMMON__
(async () => {
  L("BOOT");
  try {
    const w = await bootFrame();
    if (!w) { L("NO_IFRAME"); return; }
    const d = w.document;
    L("iframe=ok");

    w.navigate("math");
    const mbox = await waitFor(() => {
      const b = d.querySelector("#pageBody-math");
      return b && b.textContent.length > 30 && b.textContent.indexOf("加载中") < 0 ? b : null;
    }, 60, 120);
    L("math_loaded=" + (mbox ? 1 : 0));
    if (!mbox) { L("NO_MATH_BOX"); return; }

    /* ---- ① 先摆好周末画面（截图就拍这一版） ---- */
    runInFrame(w, "MATH.rest=true;MATH.due=[];renderDrill();");
    const wrap = d.querySelector("#mathDrillWrap");
    L("math_rest_banner=" + (has(wrap, "🌴 周末休息") ? 1 : 0));
    L("math_rest_hint=" + (has(wrap, "顺延到下周一") ? 1 : 0));
    L("math_due_empty_note=" + (has(wrap, "周末队列为空") ? 1 : 0));
    L("math_stat_due_zero=" + ((d.querySelector('[data-m-stat="due"]') || {}).textContent === "0" ? 1 : 0));

    /* ---- ② 反过来：平常日子不挂周末横幅 ---- */
    runInFrame(w, "MATH.rest=false;MATH.due=[];renderDrill();");
    L("math_normal_no_banner=" + (has(d.querySelector("#mathDrillWrap"), "🌴") ? 0 : 1));

    /* ---- ③ 收尾：画面停回「周末休息」那一版 ---- */
    runInFrame(w, "MATH.rest=true;MATH.due=[];renderDrill();");
    L("DONE");
  } catch (e) {
    L("ERR:" + (e && e.message ? e.message : e));
  }
})();
</script></body></html>
""".replace("__COMMON__", COMMON)

env = dict(os.environ)
env.update({
    "DB_PATH": str(DB).replace("\\", "/"),
    "AI_API_KEY": "",              # 不调模型
    "DAILY_GENERATE_HOUR": "23",   # 不自动生成
    "ACCESS_PASSWORD": "",         # 开放模式，不用登录
    "PYTHONIOENCODING": "utf-8",
    "PYTHONPATH": str(ROOT),
})
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path: str):
    try:
        with OPENER.open(BASE + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def chrome(*extra: str, budget: int = 600000, dsf: int = 1):
    return subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", f"--force-device-scale-factor={dsf}",
         f"--virtual-time-budget={budget}", *extra],
        capture_output=True, timeout=300,
    )


def report(key: str, must: str = "DONE", tries: int = 60) -> str:
    """把页面回传的日志取回来。先等 DONE；等不到就退而求其次拿最后一份半成品。"""
    last = ""
    for _ in range(tries):
        rep = get("/api/annotations?prefix=qa")
        strokes = ((rep or {}).get("items") or {}).get(key, {}).get("strokes") or []
        text = strokes[0].get("c") if strokes else ""
        if text:
            last = text
            if must in text:
                return text
        time.sleep(0.5)
    return last


def run_page(name: str, html: str, key: str, shot: str) -> str:
    path = ROOT / "web" / name
    path.write_text(html, encoding="utf-8")
    text = ""
    dom = ""
    try:
        for attempt in range(3):
            r = chrome("--window-size=1100,1000", "--dump-dom", f"{BASE}/static/{name}")
            dom = (r.stdout or b"").decode("utf-8", "replace")
            text = report(key)
            if "DONE" in text:
                break
            print(f"（{name} 第 {attempt + 1} 次没跑完，重试）")
        if "DONE" not in text:
            m = re.search(r'id="qa"[^>]*>([^<]*)<', dom)
            print(f"  {name} 页面角标：", (m.group(1) if m else "（没找到 #qa）")[:300])
        # 截图：页面进来就先摆好了「周末休息」那一版，所以拍到的一定是这个状态
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        chrome("--window-size=1100,1000", f"--screenshot={SHOT_DIR / shot}", f"{BASE}/static/{name}")
    finally:
        if path.exists():
            path.unlink()
    return text


def kv_of(text: str) -> dict:
    kv = {}
    for part in (text or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k] = v
    return kv


try:
    for _ in range(80):
        try:
            OPENER.open(BASE + "/api/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        sys.exit("临时实例没起来")

    print("=== 今日复习页 ===")
    rev_text = run_page("_qa_weekend_rev.html", PAGE_REVIEW, "qa:weekend-rev", "复习页-周末休息.png")
    print(rev_text or "（没读到报告）")
    print("\n=== 错题页 ===")
    math_text = run_page("_qa_weekend_math.html", PAGE_MATH, "qa:weekend-math", "错题页-周末休息.png")
    print(math_text or "（没读到报告）")

    kv = {**kv_of(rev_text), **kv_of(math_text)}
    checks = [
        ("今日复习页：真站点起来了", (rev_text or "").startswith("BOOT|iframe=ok"), (rev_text or "")[:60]),
        ("/api/review/due 带 rest 字段", kv.get("api_has_rest") == "1", kv.get("api_has_rest")),
        ("/api/review/due 的 stats 也带 rest", kv.get("stats_has_rest") == "1", kv.get("stats_has_rest")),
        ("/api/math/review/due 带 rest 字段", kv.get("math_api_has_rest") == "1", kv.get("math_api_has_rest")),
        ("后端 rest 与「今天是不是周末」一致", kv.get("rest_matches_weekday") == "1",
         f"is_weekend={kv.get('is_weekend')} rest={kv.get('api_rest')}"),
        ("英语与数学的 rest 口径一致", kv.get("math_rest_matches") == "1",
         f"eng={kv.get('api_rest')} math={kv.get('math_api_rest')}"),
        ("今日复习页能进", kv.get("review_loaded") == "1", kv.get("review_loaded")),
        ("周末：显示「🌴 周末休息」", kv.get("review_rest_banner") == "1", kv.get("review_rest_banner")),
        ("周末：提示写明攒到周一", kv.get("review_rest_hint") == "1", kv.get("review_rest_hint")),
        ("周末：不再显示「今天没有要复习的素材」", kv.get("review_rest_not_empty") == "1",
         kv.get("review_rest_not_empty")),
        ("周末：「今日待复习」是 0", kv.get("review_rest_due_zero") == "1", kv.get("review_rest_due_zero")),
        ("页面写明「复习只排在工作日」", kv.get("review_rule_note") == "1", kv.get("review_rule_note")),
        ("平常日子仍显示「今天没有要复习的素材」", kv.get("review_normal_empty") == "1",
         kv.get("review_normal_empty")),
        ("平常日子不挂周末横幅", kv.get("review_normal_no_banner") == "1", kv.get("review_normal_no_banner")),
        ("错题页：真站点起来了", (math_text or "").startswith("BOOT|iframe=ok"), (math_text or "")[:60]),
        ("错题页能进", kv.get("math_loaded") == "1", kv.get("math_loaded")),
        ("错题页周末显示「🌴 周末休息」", kv.get("math_rest_banner") == "1", kv.get("math_rest_banner")),
        ("错题页说明顺延到下周一", kv.get("math_rest_hint") == "1", kv.get("math_rest_hint")),
        ("错题页说明周末队列为空", kv.get("math_due_empty_note") == "1", kv.get("math_due_empty_note")),
        ("错题页「今日待复习」是 0", kv.get("math_stat_due_zero") == "1", kv.get("math_stat_due_zero")),
        ("错题页平常日子不挂周末横幅", kv.get("math_normal_no_banner") == "1", kv.get("math_normal_no_banner")),
        ("两页都跑到 DONE", "DONE" in (rev_text or "") and "DONE" in (math_text or ""),
         f"rev={'DONE' in (rev_text or '')} math={'DONE' in (math_text or '')}"),
    ]

    ok = True
    print("\n=== 断言 ===")
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"))
        ok = ok and passed
    print("\n截图目录：", SHOT_DIR)
    print("\n" + ("全部通过" if ok else "有失败项"))
    sys.exit(0 if ok else 1)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    for n in ("_qa_weekend_rev.html", "_qa_weekend_math.html"):
        p = ROOT / "web" / n
        if p.exists():
            p.unlink()
    cleanup(TMP)