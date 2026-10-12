# -*- coding: utf-8 -*-
"""两个用户反馈的回归（2026-10-11）：

  ① 英语正文字体看久了眼睛花 → 「Aa 阅读外观」要能调字号/行距/字体，而且**记得住**
     （刷新后还得是调过的那档）。
  ② 数学重刷错题时，点「做对了 / 还是不会」会**跳走**到题库，得重新往上翻
     → 现在应当就地移除这道题、滚动位置不动。

跑法（在 项目/ 目录下）：
    python tools/_shot_read_math.py
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
TMP = Path(tempfile.mkdtemp(prefix="readmath_"))

PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>qa</title></head>
<body><iframe id="fa" style="border:0;width:1100px;height:900px"></iframe>
<div id="qa" style="font:11px monospace">waiting</div>
<script>
const ONLY = new URLSearchParams(location.search).get("only") || "read";
const qa = document.getElementById("qa"), log = [];
const raf = () => new Promise(r => requestAnimationFrame(r));
const sleep = ms => new Promise(r => setTimeout(r, ms));
function L(s) {
  log.push(s); qa.textContent = log.join("|");
  try { fetch("/api/annotations", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: "qa:rm", data: { v: 1, w: 1,
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
    const w = f.contentWindow, doc = w.document;

    const sizeOf = () => w.getComputedStyle(doc.querySelector("#bodyText")).fontSize;
    const lhOf = () => w.getComputedStyle(doc.querySelector("#bodyText")).lineHeight;
    const fontOf = () => w.getComputedStyle(doc.querySelector("#bodyText")).fontFamily;

    if (ONLY === "read") {
      L("base_size=" + sizeOf());
      L("base_lh=" + lhOf());
      L("panel_hidden_initial=" + (doc.querySelector("#readPanel").hidden ? 1 : 0));
      /* 打开面板 */
      doc.querySelector("#btnRead").click();
      await sleep(120);
      L("panel_open=" + (doc.querySelector("#readPanel").hidden ? 0 : 1));
      L("panel_has_opts=" + (doc.querySelectorAll("#readPanel [data-read-set]").length));
      L("panel_has_preview=" + (doc.querySelector(".read-preview") ? 1 : 0));

      /* 调到「特大」 */
      doc.querySelector('#readPanel [data-read-val="xl"]').click();
      await sleep(80);
      const bigSize = sizeOf();
      L("after_xl_size=" + bigSize);
      L("xl_marked=" + (doc.querySelector('#readPanel [data-read-set="size"][data-read-val="xl"]').className.indexOf("on") >= 0 ? 1 : 0));

      /* 行距调「很宽」 */
      doc.querySelector('#readPanel [data-read-val="xloose"]').click();
      await sleep(80);
      L("after_xloose_lh=" + lhOf());

      /* 换无衬线 */
      doc.querySelector('#readPanel [data-read-val="sans"]').click();
      await sleep(80);
      const fam = fontOf();
      L("after_sans_font=" + (fam.indexOf("system-ui") >= 0 || fam.indexOf("-apple-system") >= 0 || fam.indexOf("Segoe") >= 0 ? "sans" : fam.slice(0, 40)));

      /* 存到本地没有 */
      L("stored=" + (w.localStorage.getItem("prism.reading.v1") || "none"));

      /* 关键验证：把内存里的配置清掉再 load() —— 等价于「换个页面重新进来」，
         看能不能从本地把「特大 / 很宽 / 无衬线」读回来。
         不靠 iframe 的 location.reload()：那个和 app 启动有竞态，
         那样测出来的是时序而不是功能（踩过）。 */
      w.eval("Reading.cfg={size:'m',lh:'normal',font:'serif'}; Reading.load();");
      await sleep(150);
      L("restore_size=" + w.getComputedStyle(doc.querySelector("#bodyText")).fontSize);
      {
        const fam3 = w.getComputedStyle(doc.querySelector("#bodyText")).fontFamily;
        L("restore_font=" + (fam3.indexOf("system-ui") >= 0 || fam3.indexOf("-apple-system") >= 0 || fam3.indexOf("Segoe") >= 0 ? "sans" : "serif"));
      }
      L("restore_attr=" + (doc.documentElement.getAttribute("data-read") || "none"));

      /* 刷新后应当还是特大/很宽/无衬线。
         ⚠️ reload 会换掉整个文档，之后所有旧引用都会失效 —— 一律重新查。 */
      w.location.reload();
      let size2 = "", fam2 = "", dr = "none";
      for (let i = 0; i < 400; i++) {
        await raf();
        try {
          const d2 = w.document;
          const bt = d2.querySelector("#bodyText");
          if (bt) {
            size2 = w.getComputedStyle(bt).fontSize;
            fam2 = w.getComputedStyle(bt).fontFamily;
            dr = d2.documentElement.getAttribute("data-read") || "none";
            if (size2) break;
          }
        } catch (e) {}
      }
      L("reload_size=" + size2);
      L("reload_is_sans=" + (fam2.indexOf("system-ui") >= 0 || fam2.indexOf("-apple-system") >= 0 || fam2.indexOf("Segoe") >= 0 ? 1 : 0));
      L("reload_kept=" + (size2 === bigSize ? 1 : 0));
      L("data_read=" + dr);

      /* 恢复默认：重新查一遍顶部 Aa 按钮（刷新后旧引用已失效），打开面板再点重置 */
      let resetOk = 0, resetSize = "";
      for (let i = 0; i < 60 && !resetOk; i++) {
        const rb = w.document.querySelector("#btnRead");
        const panel = w.document.querySelector("#readPanel");
        if (rb && panel) {
          if (panel.hidden) { rb.click(); await sleep(200); }
          const resetBtn = w.document.querySelector("[data-read-reset]");
          if (resetBtn) {
            resetBtn.click();
            await sleep(200);
            resetSize = w.getComputedStyle(w.document.querySelector("#bodyText")).fontSize;
            if (resetSize === "18.5px") resetOk = 1;
          }
        }
        if (!resetOk) await sleep(200);
      }
      L("reset_size=" + resetSize);
      L("reset_back_to_default=" + resetOk);
    }

    if (ONLY === "math") {
      /* 造两道到期错题：直接打接口 */
      for (const t of ["QA 错题一号", "QA 错题二号"]) {
        await fetch("/api/math/problems", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stem: t + "：求 $\\int_0^1 x^2 dx$", subject: "高等数学", topic: "QA", kind: "normal" }) });
      }
      w.location.hash = "#math";
      for (let i = 0; i < 300; i++) {
        await raf();
        if (doc.querySelector("#pageBody-math")) break;
      }
      /* 等错题列表出来 */
      let cards = 0;
      for (let i = 0; i < 80; i++) {
        await sleep(150);
        cards = doc.querySelectorAll("[data-due-id]").length;
        if (cards) break;
      }
      L("due_cards=" + cards);
      if (!cards) { L("END_OK"); return; }

      /* 展开第一道的解析 → 出现「做对了」 */
      const first = doc.querySelector("[data-due-id]");
      const fid = first.dataset.dueId;
      const reveal = first.querySelector("[data-due-reveal]");
      if (reveal) { reveal.click(); }
      let hasRes = 0;
      for (let i = 0; i < 80; i++) {
        await sleep(150);
        hasRes = doc.querySelectorAll("[data-due-res]").length;
        if (hasRes) break;
      }
      L("res_buttons=" + hasRes);

      /* 滚到下面一点，模拟「刷到中间」的位置 */
      w.scrollTo(0, 260);
      await sleep(150);
      const yBefore = w.scrollY;

      const rightBtn = doc.querySelector('[data-due-res="right"]');
      if (rightBtn) rightBtn.click();

      /* 等这道消失 */
      let gone = 0, yAfter = -1;
      for (let i = 0; i < 60; i++) {
        await sleep(150);
        if (!doc.querySelector('[data-due-id="' + fid + '"]')) { gone = 1; yAfter = w.scrollY; break; }
      }
      L("answered_removed=" + gone);
      L("scroll_before=" + yBefore);
      L("scroll_after=" + (yAfter >= 0 ? yAfter : Math.round(w.scrollY)));
      /* 关键：没有被甩回顶部（允许小幅回补，但不许归零） */
      L("scroll_kept=" + (yAfter >= 0 && Math.abs(yAfter - yBefore) <= 60 ? 1 : 0));
      /* 而且还在数学页，没跳走 */
      L("still_math=" + (w.location.hash.indexOf("#math") >= 0 || w.location.hash.indexOf("#sig") >= 0 ? 1 : 0));
      L("drill_box_present=" + (doc.querySelector("#mathDrillWrap") ? 1 : 0));
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

    (ROOT / "web" / "_qa_rm.html").write_text(PAGE, encoding="utf-8")

    # 先建两道错题，并把复习日期改成昨天 —— 这样它们才会出现在「今天该复习」里。
    # （新录的题默认次日才复习，当天不出现，所以必须手动推到昨天。）
    import sqlite3
    for t in ("QA 错题一号：求 $\\int_0^1 x^2 dx$", "QA 错题二号：求 $\\sum_{n=1}^{\\infty} 1/n^2$"):
        req = urllib.request.Request(
            BASE + "/api/math/problems", method="POST",
            data=json.dumps({"stem": t, "subject": "高等数学", "topic": "QA", "kind": "normal"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=20) as r:
                r.read()
        except Exception as exc:  # noqa: BLE001
            print("预置错题失败：", exc)
    conn = sqlite3.connect(TMP / "app.db")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(math_problems)")]
    if "next_review" in cols:
        conn.execute("UPDATE math_problems SET next_review=date('now','localtime')")
    # 把解析补上：这个临时实例没配 AI 密钥，点「看解析」会去调模型然后失败，
    # 于是永远出不来「做对了/还是不会」这两个按钮 —— 那样就测不到跳转问题。
    conn.execute(
        "UPDATE math_problems SET analyzed_at=datetime('now','localtime'), "
        "knowledge='QA 知识点', solution='QA 解法', key_steps='[\"第一步\",\"第二步\"]'"
    )
    conn.commit()
    conn.close()

    def run_mode(mode: str) -> str:
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
             "--force-device-scale-factor=1", "--virtual-time-budget=600000",
             "--window-size=1100,900", "--dump-dom",
             f"{BASE}/static/_qa_rm.html?only={mode}"],
            capture_output=True, timeout=420)
        last = ""
        for _ in range(60):
            rep = get("/api/annotations?prefix=qa")
            s = ((rep or {}).get("items") or {}).get("qa:rm", {}).get("strokes") or []
            if s and s[0].get("c"):
                last = s[0]["c"]
                if "END_OK" in last and f"mode={mode}" in last:
                    return last
            time.sleep(0.4)
        return last

    parts = []
    for mode in ("read", "math"):
        got = run_mode(mode)
        print(f"[{mode}] {got or '（没读到）'}")
        parts.append(got)
    text = "|".join(p for p in parts if p)
    kv = dict(x.split("=", 1) for x in text.split("|") if "=" in x)

    def px(v):
        try:
            return float(str(v or "0").replace("px", "").strip())
        except Exception:  # noqa: BLE001
            return 0.0

    checks = [
        ("阅读面板默认收起", kv.get("panel_hidden_initial") == "1", kv.get("panel_hidden_initial")),
        ("点 Aa 能展开，且有选项", kv.get("panel_open") == "1" and (kv.get("panel_has_opts") or "0") != "0",
         f"{kv.get('panel_open')}/{kv.get('panel_has_opts')}"),
        ("有实时预览", kv.get("panel_has_preview") == "1", kv.get("panel_has_preview")),
        ("调大字号立刻生效", px(kv.get("after_xl_size")) > px(kv.get("base_size")),
         f"{kv.get('base_size')} -> {kv.get('after_xl_size')}"),
        ("选中态跟着变", kv.get("xl_marked") == "1", kv.get("xl_marked")),
        ("行距能调宽", px(kv.get("after_xloose_lh")) > px(kv.get("base_lh")),
         f"{kv.get('base_lh')} -> {kv.get('after_xloose_lh')}"),
        ("字体能换成无衬线", kv.get("after_sans_font") == "sans", kv.get("after_sans_font")),
        ("设置写进了本地", "xl" in str(kv.get("stored", "")), kv.get("stored")),
        ("重新载入后字号保持（读回本地设置）", kv.get("restore_size") == "22.5px", kv.get("restore_size")),
        ("重新载入后字体保持", kv.get("restore_font") == "sans", kv.get("restore_font")),
        ("恢复默认回到标准字号", kv.get("reset_back_to_default") == "1", kv.get("reset_size")),
        ("到期错题卡片渲染出来", (kv.get("due_cards") or "0") != "0", kv.get("due_cards")),
        ("点「做对了」这道就地消失", kv.get("answered_removed") == "1", kv.get("answered_removed")),
        ("滚动位置没有被甩回顶部", kv.get("scroll_kept") == "1",
         f"{kv.get('scroll_before')} -> {kv.get('scroll_after')}"),
        ("仍留在数学页（没跳去题库顶部）", kv.get("still_math") == "1", kv.get("still_math")),
    ]
    ok = True
    print("\n=== 断言 ===")
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"))
        ok = ok and passed
    print("全部通过" if ok else "有失败项")
    sys.exit(0 if ok else 1)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    qa = ROOT / "web" / "_qa_rm.html"
    if qa.exists():
        qa.unlink()
    cleanup(TMP)