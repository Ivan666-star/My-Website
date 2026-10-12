# -*- coding: utf-8 -*-
"""真浏览器里验证「划选填充 → 阅读笔记 → 复习」这条链路（2026-10-11 新增功能）。

为什么必须用真浏览器：这一版最危险的代码不是后端（已有 _notes_test.py），
而是前端**把高亮画回正文**的定位逻辑 —— 选区在第几段、段内第几个字符、
跨文本节点怎么切。这些只有真的走一遍 DOM 才能证明，靠读代码看不出对不对。

做法沿用 tools/_shot_pen.py 那套（已验证过的路子）：
  · 起一个**临时实例**：独立 DB（TEMP 里）、ACCESS_PASSWORD=""（开放模式，免登录）、
    AI_API_KEY=""（不花钱）、DAILY_GENERATE_HOUR=23（不自动生成）；
  · 用无头 Chrome 打开临时实例的首页（内置示例文章会自动灌好）；
  · 页面里造一个真实选区 → 点「填充并记入笔记」→ 直接查后端接口核对；
  · 刷新页面 → 检查高亮有没有按段落位置画回去；
  · 顺手验证语法入库、笔记页渲染、双击查词卡片；
  · 测量结果由页面自己 PUT 回服务端（不受截图/时序影响），Python 侧再取回断言。

⚠️ 不碰 data/ 里的真实库：DB_PATH 指向临时目录。

跑法（在 项目/ 目录下）：
    python tools/_shot_notes.py
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
def _free_port() -> int:
    """取一个空闲端口。
    ⚠️ 别写死端口：这几套测试会连着跑，前一个实例还没退干净时，
    写死端口就会出现「临时实例没起来」这种假失败（踩过好几次）。"""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"

TMP = Path(tempfile.mkdtemp(prefix="notesshot_"))
DB = TMP / "app.db"

PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>notes qa</title></head><body>
<iframe id="fa" style="border:0;width:1100px;height:900px"></iframe>
<div id="qa" style="font:11px monospace">waiting</div>
<script>
const qa = document.getElementById("qa");
const log = [];
const raf = () => new Promise(r => requestAnimationFrame(r));
function L(s) {
  log.push(s);
  qa.textContent = log.join("|");
  try {
    fetch("/api/annotations", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "qa:notes", data: { v: 1, w: 1,
        strokes: [{ c: log.join("|"), s: 0, p: [[log.length, 0]] }] } }),
    }).catch(() => {});
  } catch (e) {}
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
window.onerror = (m, s, l) => { L("JSERR:" + m + "@" + l); };

async function ready(w) {
  for (let i = 0; i < 400; i++) {
    const ps = w.document.querySelectorAll("#bodyText p");
    if (ps.length) return ps;
    await raf();
  }
  return null;
}

(async () => {
  L("BOOT");
  try {
    const f = document.getElementById("fa");
    f.src = "/#today";                       /* 必须显式给 iframe 一个地址 */
    for (let i = 0; i < 200; i++) {
      try {
        const d = f.contentWindow && f.contentWindow.document;
        if (d && d.readyState === "complete" && d.querySelector("#bodyText")) break;
      } catch (e) {}
      await raf();
    }
    L("iframe=" + (f.contentWindow ? "ok" : "none"));
    const w = f.contentWindow;
    const ps = await ready(w);
    if (!ps) { L("NOPARAS"); return; }
    L("paras=" + ps.length);
    if (!w.Notes) { L("NO_NOTES_MODULE"); return; }

    /* ---- ① 造一个真实选区：从第 2 段里截一段英文 ---- */
    const p = ps[Math.min(1, ps.length - 1)];
    const raw = p.textContent || "";
    const m = raw.match(/[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*){1,3}/);
    if (!m) { L("NOMATCH"); return; }
    const phrase = m[0];
    const at = raw.indexOf(phrase);
    L("pick=" + JSON.stringify(phrase) + "@" + at);

    /* 按纯文本偏移定位到具体文本节点，模拟用户划选 */
    const walker = w.document.createTreeWalker(p, NodeFilter.SHOW_TEXT, null);
    let seen = 0, startNode = null, startOff = 0, endNode = null, endOff = 0;
    let node;
    while ((node = walker.nextNode())) {
      const len = node.nodeValue.length;
      if (startNode === null && seen + len > at) { startNode = node; startOff = at - seen; }
      if (startNode !== null && seen + len >= at + phrase.length) {
        endNode = node; endOff = at + phrase.length - seen; break;
      }
      seen += len;
    }
    if (!startNode || !endNode) { L("NONODE"); return; }
    const range = w.document.createRange();
    range.setStart(startNode, startOff);
    range.setEnd(endNode, endOff);
    const sel = w.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    p.dispatchEvent(new w.MouseEvent("mouseup", { bubbles: true }));
    await sleep(60);

    const bar = w.document.querySelector("#hlBar");
    if (!bar || bar.hidden) { L("NOBAR"); return; }
    L("bar=shown");

    /* ---- ② 点「填充并记入笔记」 ---- */
    const pickBlue = bar.querySelector('[data-hl-color="blue"]');
    if (pickBlue) pickBlue.click();
    await sleep(30);
    /* 把 iframe 里的报错捞出来 —— 填充失败时这才是关键证据 */
    w.addEventListener("error", e => L("IFERR:" + (e.message || e.type)));
    w.addEventListener("unhandledrejection", e => L("IFREJ:" + ((e.reason && e.reason.message) || e.reason)));
    w.document.querySelector("#hlSave").click();
    let hl = null;
    for (let i = 0; i < 12; i++) {
      hl = w.document.querySelector('.hl[data-note]');
      if (hl) break;
      await sleep(120);
    }
    L("painted=" + (hl ? "1:" + hl.textContent : "0"));
    L("hlcolor=" + (hl ? hl.className : "-"));

    /* ---- ③ 直接问后端：笔记落库没有、锚点对不对 ---- */
    const art = await fetch("/api/bootstrap").then(r => r.json());
    const artId = art.bundle && art.bundle.article ? art.bundle.article.id : 0;
    const nres = await fetch("/api/notes/article/" + artId).then(r => r.json());
    const notes = nres.items || [];
    L("notes=" + notes.length);
    if (notes[0]) {
      L("note_content_ok=" + (notes[0].content === phrase ? 1 : 0));
      L("note_para=" + notes[0].para_index + "/" + notes[0].char_start);
      L("note_color=" + notes[0].color);
      L("note_ctx_len=" + ((notes[0].context || "").length > 10 ? 1 : 0));
      L("note_review=" + (notes[0].next_review ? 1 : 0));
      /* 手工再画一次：区分「paintOne 本身有问题」还是「保存回调没走到」 */
      const direct = w.Notes._paintOne(notes[0]);
      L("direct_paint=" + (direct ? 1 : 0));
      L("dom_after_direct=" + (w.document.querySelector('.hl[data-note]') ? 1 : 0));
    }

    /* ---- ④ 刷新后高亮应画回原位 ---- */
    try { f.contentWindow.location.reload(); } catch (e) { L("RELOADERR:" + e); }
    let back = 0, backText = "";
    for (let i = 0; i < 150; i++) {
      await raf();
      try {
        const el = f.contentWindow.document.querySelector('.hl[data-note]');
        if (el) { back = 1; backText = el.textContent; break; }
      } catch (e) {}
    }
    L("repaint=" + back + ":" + backText);

    /* ---- ⑤ 语法收进积累库 ---- */
    const w2 = f.contentWindow;
    const gbtn = w2.document.querySelector("[data-gram-save]");
    if (gbtn) {
      gbtn.click();
      /* 轮询等库里有东西 —— 固定 sleep 一次会把「网络快慢」当成功能好坏 */
      let got = 0;
      for (let i = 0; i < 40; i++) {
        await sleep(150);
        const lib = await fetch("/api/grammar/library").then(r => r.json());
        got = (lib.items || []).length;
        if (got > 0) break;
      }
      L("grammar=" + got);
    } else {
      L("grammar=no-button");
    }

    /* ---- ⑥ 双击查词（没配 AI，所以只验证取词与小卡片这条路走通） ---- */
    const ps3 = f.contentWindow.document.querySelectorAll("#bodyText p");
    const target = ps3[0];
    const tw = (target.textContent || "").match(/[A-Za-z]{4,}/);
    if (tw) {
      const r2 = f.contentWindow.document.createRange();
      const tn = target.firstChild;
      const idx = tn.nodeValue.indexOf(tw[0]);
      r2.setStart(tn, Math.max(0, idx));
      r2.setEnd(tn, Math.max(0, idx) + tw[0].length);
      const rc = r2.getBoundingClientRect ? r2.getBoundingClientRect() : { left: 10, top: 10 };
      target.dispatchEvent(new f.contentWindow.MouseEvent("dblclick", {
        bubbles: true, clientX: Math.round(rc.left) + 1 || 10, clientY: Math.round(rc.top) + 1 || 10,
      }));
      await sleep(700);
      const pop = f.contentWindow.document.querySelector("#wordPop");
      L("pop=" + (pop && !pop.hidden ? 1 : 0) + ":" + (f.contentWindow.document.querySelector("#wpWord") || {}).textContent);
    } else { L("pop=noword"); }

    /* ---- ⑦ 笔记页渲染 ---- */
    f.contentWindow.location.hash = "#notes";
    await sleep(900);
    const np = f.contentWindow.document.querySelector("#pageBody-notes");
    L("notespage=" + (np && np.textContent.indexOf(phrase) >= 0 ? 1 : 0));

    L("DONE");
  } catch (e) {
    L("ERR:" + (e && e.message ? e.message : e));
  }
})();
</script></body></html>
"""

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
        capture_output=True, timeout=420,
    )


def report(must: str = "DONE", tries: int = 60) -> str:
    """取回页面回传的报告。

    先等带 DONE 的完整报告；等不到就退而求其次，把**最后一次**报告拿回来 ——
    页面中途挂掉时，这份半成品才是唯一能说明卡在哪儿的证据。
    """
    last = ""
    for _ in range(tries):
        rep = get("/api/annotations?prefix=qa")
        strokes = ((rep or {}).get("items") or {}).get("qa:notes", {}).get("strokes") or []
        text = strokes[0].get("c") if strokes else ""
        if text:
            last = text
            if must in text:
                return text
        time.sleep(0.5)
    return last


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
    print("临时实例日期：", boot.get("date"), "| 有内容：", bool(boot.get("bundle")))

    (ROOT / "web" / "_qa_notes.html").write_text(PAGE, encoding="utf-8")

    text = ""
    dom = ""
    for attempt in range(3):
        r = chrome("--window-size=1100,900", "--dump-dom", f"{BASE}/static/_qa_notes.html")
        dom = (r.stdout or b"").decode("utf-8", "replace")
        text = report("notespage=")
        if text:
            break
        print(f"（第 {attempt + 1} 次没跑完，重试）")
    if not text:
        # 把页面里的角标抠出来，看看卡在哪一步
        m = re.search(r'id="qa"[^>]*>([^<]*)<', dom)
        print("页面角标：", (m.group(1) if m else "（没找到 #qa）")[:400])

    print("\n=== 页面回传的测量 ===")
    print(text or "（没读到任何报告）")

    checks = []
    kv = {}
    for part in (text or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k] = v
    checks.append(("页面跑完并回传", "notespage=" in (text or ""), (text or "")[:80]))
    checks.append(("正文有段落可测", kv.get("paras", "0") != "0", kv.get("paras")))
    checks.append(("notes.js 已加载", "NO_NOTES_MODULE" not in (text or ""), ""))
    checks.append(("选中后工具条出现", kv.get("bar") == "shown", kv.get("bar")))
    checks.append(("高亮已画在正文上", str(kv.get("painted", "")).startswith("1:"), kv.get("painted")))
    checks.append(("高亮颜色按选择生效", "hl-blue" in str(kv.get("hlcolor", "")), kv.get("hlcolor")))
    checks.append(("后端确实落了一条笔记", kv.get("notes") == "1", kv.get("notes")))
    checks.append(("笔记内容与选中的一致", kv.get("note_content_ok") == "1", kv.get("note_content_ok")))
    checks.append(("锚点（段落/偏移）已记录", str(kv.get("note_para", "")).split("/")[0] not in ("", "undefined", "-1"),
                   kv.get("note_para")))
    checks.append(("颜色写进笔记", kv.get("note_color") == "blue", kv.get("note_color")))
    checks.append(("上下文句子已保存", kv.get("note_ctx_len") == "1", kv.get("note_ctx_len")))
    checks.append(("自动进复习节奏", kv.get("note_review") == "1", kv.get("note_review")))
    checks.append(("刷新后高亮画回原位", str(kv.get("repaint", "")).startswith("1:"), kv.get("repaint")))
    checks.append(("语法能收进积累库", kv.get("grammar") == "1", kv.get("grammar")))
    checks.append(("双击弹出查词卡片", str(kv.get("pop", "")).startswith("1:"), kv.get("pop")))
    checks.append(("双击取到的词正确", "pop=noword" not in (text or ""), kv.get("pop")))
    checks.append(("笔记页渲染出这条", kv.get("notespage") == "1", kv.get("notespage")))

    ok = True
    print("\n=== 断言 ===")
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"))
        ok = ok and passed
    print("\n" + ("全部通过" if ok else "有失败项"))
    sys.exit(0 if ok else 1)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    qa = ROOT / "web" / "_qa_notes.html"
    if qa.exists():
        qa.unlink()
    cleanup(TMP)