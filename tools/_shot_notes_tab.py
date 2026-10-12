# -*- coding: utf-8 -*-
"""复现并钉住笔记页的两个问题（用户 2026-10-11 反馈）：

  ① 「点阅读笔记和语法积累库互相点击根本不会切换」
  ② 「如果我误填充了 我应该怎么取消」——需要有一条撤销路径

这个文件的职责是**先复现**：把标签点击前后的 DOM 状态、事件是否被处理、
以及后端返回，都量出来。修好之后它同时充当回归测试。

跑法（在 项目/ 目录下）：
    python tools/_shot_notes_tab.py
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
TMP = Path(tempfile.mkdtemp(prefix="tabqa_"))

PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>tab qa</title></head>
<body><iframe id="fa" style="border:0;width:1100px;height:900px"></iframe>
<div id="qa" style="font:11px monospace">waiting</div>
<script>
const ONLY = new URLSearchParams(location.search).get("only") || "all";
const qa = document.getElementById("qa"), log = [];
const raf = () => new Promise(r => requestAnimationFrame(r));
const sleep = ms => new Promise(r => setTimeout(r, ms));
function L(s) {
  log.push(s); qa.textContent = log.join("|");
  try { fetch("/api/annotations", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: "qa:tab", data: { v: 1, w: 1,
      strokes: [{ c: log.join("|"), s: 0, p: [[log.length, 0]] }] } }) }).catch(() => {}); } catch (e) {}
}
window.onerror = (m, s, l) => L("JSERR:" + m + "@" + l);

(async () => {
  L("BOOT"); L("mode=" + ONLY);
  try {
    const f = document.getElementById("fa");
    f.src = "/#today";
    for (let i = 0; i < 300; i++) {
      try { const d = f.contentWindow.document;
        if (d.readyState === "complete" && d.querySelector("#bodyText p")) break; } catch (e) {}
      await raf();
    }
    const w = f.contentWindow;
    let doc = w.document;
    L("app=" + (w.PrismApp ? 1 : 0) + (w.Notes ? "N" : "-"));

    /* 预置一条笔记 + 一条语法，保证两个标签都有内容可看 */
    const boot = await fetch("/api/bootstrap").then(r => r.json());
    await fetch("/api/notes", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: "tab probe note", kind: "phrase", cn: "标签探针",
        color: "yellow", context: "x", date: boot.date }) });
    await fetch("/api/grammar/save", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "标签探针语法点", explanation: "只讲一个点", example: "e.g.",
        category: "句法", date: boot.date }) });

    if (ONLY !== "undo") {
    /* ---- 进笔记页 ---- */
    w.location.hash = "#notes";
    for (let i = 0; i < 300; i++) {
      await raf();
      if (doc.querySelector("#pageBody-notes .hist-tabs")) break;
    }
    const tabs = doc.querySelectorAll("#pageBody-notes [data-ntab]");
    L("tabs=" + tabs.length);
    L("tab_default_on=" + (doc.querySelector('#pageBody-notes [data-ntab="note"]').className.indexOf("on") >= 0 ? 1 : 0));

    /* ---- 点「语法积累库」----
       顺便量两件事（用户反馈「切换响应非常慢」）：
         · switch_reqs：这一下有没有打网络请求（应该为 0，走本地缓存重绘）
         · switch_sync_ms：点下去到界面换完的毫秒数（应该是 0，同步换完） */
    let reqCount = 0;
    const origFetch = w.fetch.bind(w);
    w.fetch = function (u, o) {
      const s = String(u);
      if (s.indexOf("/api/notes") >= 0 || s.indexOf("/api/grammar") >= 0) reqCount++;
      return origFetch(u, o);
    };
    const g = doc.querySelector('#pageBody-notes [data-ntab="grammar"]');
    const t0 = performance.now();
    g.click();
    const syncMs = Math.round(performance.now() - t0);
    /* ⚠️ 必须**重新查一次**节点：renderNotesPage 会把整个容器重建，
       上面拿到的 g 已经脱离文档了，读它的 className 永远是旧值。 */
    const gAfter = doc.querySelector('#pageBody-notes [data-ntab="grammar"]');
    const onGnow = gAfter && gAfter.className.indexOf("on") >= 0 ? 1 : 0;
    const gramNow = (doc.querySelector("#pageBody-notes").textContent || "").indexOf("标签探针语法点") >= 0 ? 1 : 0;
    L("switch_sync_ms=" + syncMs);
    L("switch_instant=" + (onGnow && gramNow ? 1 : 0));
    L("switch_reqs=" + reqCount);

    let swapped = gramNow, onG = onGnow, onN = 0;
    for (let i = 0; i < 60 && !swapped; i++) {
      await sleep(120);
      const gb = doc.querySelector('#pageBody-notes [data-ntab="grammar"]');
      const nb = doc.querySelector('#pageBody-notes [data-ntab="note"]');
      onG = gb && gb.className.indexOf("on") >= 0 ? 1 : 0;
      onN = nb && nb.className.indexOf("on") >= 0 ? 1 : 0;
      const txt = (doc.querySelector("#pageBody-notes") || {}).textContent || "";
      if (txt.indexOf("标签探针语法点") >= 0) { swapped = 1; break; }
    }
    if (!onN) {
      const nb = doc.querySelector('#pageBody-notes [data-ntab="note"]');
      onN = nb && nb.className.indexOf("on") >= 0 ? 1 : 0;
    }
    L("switch_to_grammar=" + swapped);
    L("grammar_tab_on=" + onG); L("note_tab_on=" + onN);

    /* ---- 再点回「阅读笔记」 ---- */
    doc.querySelector('#pageBody-notes [data-ntab="note"]').click();
    let back = (doc.querySelector("#pageBody-notes").textContent || "").indexOf("tab probe note") >= 0 ? 1 : 0;
    for (let i = 0; i < 60 && !back; i++) {
      await sleep(120);
      const txt = (doc.querySelector("#pageBody-notes") || {}).textContent || "";
      if (txt.indexOf("tab probe note") >= 0) { back = 1; break; }
    }
    L("switch_back=" + back);
    }   /* end of ONLY !== "undo" */

    /* ---- 误填充的撤销（?only=undo 时单独跑，避免和标签流程抢时序）---- */
    if (ONLY === "tab") { L("END_OK"); return; }
    w.location.hash = "#today";
    for (let i = 0; i < 200; i++) {
      await raf();
      if (doc.querySelector("#bodyText p")) break;
    }
    const ps = doc.querySelectorAll("#bodyText p");
    const p = ps[Math.min(1, ps.length - 1)];
    const raw = p.textContent || "";
    const mm = raw.match(/[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*){1,3}/);
    const phrase = mm ? mm[0] : "";
    L("undo_pick=" + JSON.stringify(phrase));
    if (phrase) {
      const at = raw.indexOf(phrase);
      const walker = doc.createTreeWalker(p, NodeFilter.SHOW_TEXT, null);
      let seen = 0, sn = null, so = 0, en = null, eo = 0, node;
      while ((node = walker.nextNode())) {
        const len = node.nodeValue.length;
        if (sn === null && seen + len > at) { sn = node; so = at - seen; }
        if (sn !== null && seen + len >= at + phrase.length) { en = node; eo = at + phrase.length - seen; break; }
        seen += len;
      }
      const rng = doc.createRange();
      rng.setStart(sn, so); rng.setEnd(en, eo);
      const sel = w.getSelection(); sel.removeAllRanges(); sel.addRange(rng);
      p.dispatchEvent(new w.MouseEvent("mouseup", { bubbles: true }));
      await sleep(60);
      doc.querySelector("#hlSave").click();
      for (let i = 0; i < 40; i++) { await sleep(120); if (doc.querySelector('.hl[data-note]')) break; }
      L("undo_filled=" + (doc.querySelector('.hl[data-note]') ? 1 : 0));
      const undoBtn = doc.querySelector("#hlUndo");
      L("undo_btn_visible=" + (undoBtn && !undoBtn.hidden ? 1 : 0));
      const before = await fetch("/api/notes/article/" + (boot.bundle && boot.bundle.article ? boot.bundle.article.id : 0)).then(r => r.json());
      L("undo_notes_before=" + (before.items || []).length);
      if (undoBtn) {
        undoBtn.click();
        let removed = 0;
        for (let i = 0; i < 40; i++) {
          await sleep(150);
          const still = doc.querySelector('.hl[data-note]');
          const after = await fetch("/api/notes/article/" + (boot.bundle && boot.bundle.article ? boot.bundle.article.id : 0)).then(r => r.json());
          if (!still && (after.items || []).length === 0) { removed = 1; break; }
        }
        L("undo_removed=" + removed);
        const after2 = await fetch("/api/notes/article/" + (boot.bundle && boot.bundle.article ? boot.bundle.article.id : 0)).then(r => r.json());
        L("undo_notes_after=" + (after2.items || []).length);
      }
    }
    L("END_OK");
  } catch (e) { L("ERR:" + (e && e.message ? e.message : e)); L("END_OK"); }
})();
</script></body></html>
"""

env = dict(os.environ)
env.update({
    "DB_PATH": str(TMP / "app.db").replace("\\", "/"),
    "AI_API_KEY": "", "DAILY_GENERATE_HOUR": "23", "ACCESS_PASSWORD": "",
    "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT),
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


try:
    for _ in range(80):
        try:
            OPENER.open(BASE + "/api/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        sys.exit("临时实例没起来")

    (ROOT / "web" / "_qa_tab.html").write_text(PAGE, encoding="utf-8")

    def run_mode(mode: str) -> str:
        """每次只跑一条短流程。整条长流程在 --dump-dom 下时序不稳，
        拆成「标签切换」「误填充撤销」两次跑，结果靠 API 回传合并。"""
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
             "--force-device-scale-factor=1", "--virtual-time-budget=600000",
             "--window-size=1100,900", "--dump-dom",
             f"{BASE}/static/_qa_tab.html?only={mode}"],
            capture_output=True, timeout=420)
        last = ""
        for _ in range(60):
            rep = get("/api/annotations?prefix=qa")
            s = ((rep or {}).get("items") or {}).get("qa:tab", {}).get("strokes") or []
            if s and s[0].get("c"):
                last = s[0]["c"]
                if "END_OK" in last:
                    # 必须带着本轮模式的标记，避免读到上一轮的残留
                    if f"mode={mode}" in last:
                        return last
            time.sleep(0.4)
        return last

    parts = []
    for mode in ("tab", "undo"):
        got = run_mode(mode)
        print(f"[{mode}] {got or '（没读到）'}")
        parts.append(got)
    text = "|".join(p for p in parts if p)
    if "END_OK" not in text:
        print("有流程没跑完（下面按已拿到的部分断言）")
    kv = dict(x.split("=", 1) for x in (text or "").split("|") if "=" in x)
    kw_before = int(kv.get("undo_notes_before") or 0)
    kw_after = int(kv.get("undo_notes_after") or 0)
    checks = [
        ("笔记页两个标签都在", kv.get("tabs") == "2", kv.get("tabs")),
        ("默认停在阅读笔记", kv.get("tab_default_on") == "1", kv.get("tab_default_on")),
        ("能切到语法积累库", kv.get("switch_to_grammar") == "1", kv.get("switch_to_grammar")),
        ("切过去后高亮的是语法标签", kv.get("grammar_tab_on") == "1" and kv.get("note_tab_on") == "0",
         f"{kv.get('grammar_tab_on')} / {kv.get('note_tab_on')}"),
        ("能再切回阅读笔记", kv.get("switch_back") == "1", kv.get("switch_back")),
        ("切换是同步完成的（无需等待网络）", kv.get("switch_instant") == "1", kv.get("switch_instant")),
        ("切换没打接口（走本地缓存）", kv.get("switch_reqs") == "0", kv.get("switch_reqs")),
        ("填充成功", kv.get("undo_filled") == "1", kv.get("undo_filled")),
        ("填充后出现「取消刚才的填充」", kv.get("undo_btn_visible") == "1", kv.get("undo_btn_visible")),
        ("撤销后这篇的高亮被去掉", kw_after < kw_before, f"笔记数 {kw_before} -> {kw_after}"),
    ]
    ok = True
    print("\n=== 断言 ===")
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"))
        ok = ok and passed
    sys.exit(0 if ok else 1)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    qa = ROOT / "web" / "_qa_tab.html"
    if qa.exists():
        qa.unlink()
    cleanup(TMP)