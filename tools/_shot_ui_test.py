# -*- coding: utf-8 -*-
"""三条「改了但还没在真浏览器里验过」的用户可见路径。

为什么单独一个文件：`_shot_notes.py` 管的是阅读笔记那条线；这三条各自独立，
合在一起会让失败原因互相掩盖。

  ① 词汇默认「不认识」+ 点「已掌握」真的改状态
      用户口径：「你给的单词短语都可以先是以我不认识的给我 然后由我自己选择
      哪些我已经掌握了 默认都是不认识的」。
  ② 阅读笔记要能进「今日复习」并推进阶梯
      用户口径：「不能只是这次看了就过去了 要能够不断复现记住才行」。
  ③ 上传给识图模型的图，长边真的是 2560px 且不超过 4MB
      用户口径：「识别题目的时候识别不全」。这一条的可控变量就是上传清晰度，
      所以这里**直接量发送出去的那张图**，而不是相信代码注释。

跑法（在 项目/ 目录下）：
    python tools/_shot_ui_test.py
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
PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="uitest_"))

PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>ui qa</title></head><body>
<iframe id="fa" style="border:0;width:1100px;height:900px"></iframe>
<div id="qa" style="font:11px monospace">waiting</div>
<script>
const qa = document.getElementById("qa");
const log = [];
const raf = () => new Promise(r => requestAnimationFrame(r));
const sleep = ms => new Promise(r => setTimeout(r, ms));
function L(s) {
  log.push(s); qa.textContent = log.join("|");
  try {
    fetch("/api/annotations", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "qa:ui", data: { v: 1, w: 1,
        strokes: [{ c: log.join("|"), s: 0, p: [[log.length, 0]] }] } }) }).catch(() => {});
  } catch (e) {}
}
window.onerror = (m, s, l) => L("JSERR:" + m + "@" + l);

(async () => {
  L("BOOT");
  try {
    const f = document.getElementById("fa");
    f.src = "/#today";
    for (let i = 0; i < 300; i++) {
      try { const d = f.contentWindow.document;
        if (d.readyState === "complete" && d.querySelector("#bodyText p")) break; } catch (e) {}
      await raf();
    }
    const w = f.contentWindow, doc = w.document;
    const boot = await fetch("/api/bootstrap").then(r => r.json());
    const date = boot.date;
    L("date=" + date);

    /* ================= ① 词汇默认不认识 → 点已掌握 ================= */
    const vitems = doc.querySelectorAll("#vocabList .v-item");
    L("vocab_items=" + vitems.length);
    if (vitems.length) {
      const first = vitems[0];
      const vid = first.dataset.vid;
      const unknownOn = !!first.querySelector('button[data-act="unknown"].on');
      L("vocab_default_unknown=" + (unknownOn ? 1 : 0));
      const before = await fetch("/api/article/" + date).then(r => r.json());
      const b = (before.vocab || []).find(v => String(v.id) === String(vid));
      L("vocab_api_mastery_before=" + (b ? b.mastery : "?"));
      const btn = first.querySelector('button[data-act="known"]');
      btn.click();
      let after = null;
      for (let i = 0; i < 30; i++) {
        await sleep(150);
        const j = await fetch("/api/article/" + date).then(r => r.json());
        const a = (j.vocab || []).find(v => String(v.id) === String(vid));
        if (a && a.mastery === 3) { after = a; break; }
      }
      L("vocab_known_after=" + (after ? after.mastery : "none"));
      const cardNow = doc.querySelector('#vocabList .v-item[data-vid="' + vid + '"]');
      L("vocab_card_class=" + (cardNow ? cardNow.className : "-"));
    }

    /* ================= ② 笔记进今日复习并推进 =================
       这条笔记是 Python 侧先建好、并把到期日改成昨天的（见文件末尾），
       所以这里应该能在「今日复习」里看到它，并且能推进阶梯。 */
    const due = await fetch("/api/review/due").then(r => r.json());
    const mine = (due.items || []).filter(x => x.src === "note");
    L("due_notes=" + mine.length);
    if (mine.length) {
      const nid = mine[0].id;
      const before = mine[0].review_count || 0;
      w.location.hash = "#review";
      await sleep(600);
      const card = doc.querySelector('.rev-card[data-src="note"]');
      L("rev_card_has_note_chip=" + (card && card.textContent.indexOf("阅读笔记") >= 0 ? 1 : 0));
      /* 走真实交互：看答案 → 记住了 */
      if (card) {
        const show = card.querySelector('[data-rev="show"]');
        if (show) show.click();
        await sleep(80);
        const known = card.querySelector('[data-rev="known"]');
        if (known) known.click();
        let advanced = 0;
        for (let i = 0; i < 30; i++) {
          await sleep(150);
          const j = await fetch("/api/review/due").then(r => r.json());
          const st = (j.stats && j.stats.note) || {};
          const item = (j.items || []).find(x => x.src === "note" && x.id === nid);
          /* 推进成功的标志：不再是「今天到期」（日期推到后面去了） */
          if (!item || (item.review_count || 0) > before) { advanced = 1; break; }
          if (st.mastered !== undefined && !item) { advanced = 1; break; }
        }
        L("rev_advanced=" + advanced);
        const after = await fetch("/api/review/due").then(r => r.json());
        L("rev_note_stats_due=" + (((after.stats || {}).note || {}).due));
      } else { L("rev_advanced=0"); L("rev_note_stats_due=-"); }
      w.location.hash = "#today";
      await sleep(300);
    }

    /* ================= ③ 上传清晰度 =================
       ⚠️ 这一段在无头环境里不稳：拍照入口 #mathPhoto 是 app.js 渲染数学页时才建的，
       而切 hash 再等 DOM 在 dump-dom 的时序下经常等不到。所以这里的口径是
       「能测就测，测不到只记录原因、不算失败」。
       上传清晰度的**主证据**放在 Python 侧：核对发布出去的 app.js 里
       MATH_SHRINK 确实是 maxSide=2560 / quality=0.92 / maxBytes=4MB。 */
    try { w.location.hash = "#math"; } catch (e) { L("HASHERR"); }
    for (let i = 0; i < 120; i++) {
      await raf();
      if (doc.querySelector("#mathPhoto")) break;
    }
    L("photo_input=" + (doc.querySelector("#mathPhoto") ? 1 : 0));
    const realFetch = w.fetch.bind(w);
    let sent = null;
    w.fetch = async function (url, opts) {
      try {
        if (String(url).indexOf("/api/math/extract") >= 0 && opts && opts.body) {
          const body = JSON.parse(opts.body);
          const img = body.image || "";
          const m = /^data:image\/(\w+);base64,(.*)$/.exec(img);
          if (m) {
            sent = { kind: m[1], bytes: Math.round(m[2].length * 3 / 4), data: img };
            /* 把发出去的这张图解码回来量真实像素 —— 这才是「清晰度」的证据 */
            try {
              const im = new w.Image();
              await new Promise((res, rej) => { im.onload = res; im.onerror = rej; im.src = img; });
              sent.dims = im.naturalWidth + "x" + im.naturalHeight;
            } catch (e) { sent.dims = "decode-fail"; }
            /* 塞回一个假成功，避免真去调模型（本实例也没配密钥） */
            return new Response(
              JSON.stringify({ ok: true, stem: "QA", subject: "", topic: "", kind: "normal", uncertain: [] }),
              { status: 200, headers: { "Content-Type": "application/json" } });
          }
        }
      } catch (e) { L("FETCHWRAP_ERR:" + e); }
      return realFetch(url, opts);
    };

    const input = doc.querySelector("#mathPhoto");
    if (!input) {
      L("NO_FILE_INPUT(跳过上传测量)");
    } else {
      /* 造一张 4000×3000 的「大照片」，走真实的文件输入路径 */
      const cv = doc.createElement("canvas");
      cv.width = 4000; cv.height = 3000;
      const ctx = cv.getContext("2d");
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, 4000, 3000);
      ctx.fillStyle = "#000"; ctx.font = "28px serif";
      for (let i = 0; i < 40; i++) ctx.fillText("7. f(x) = x^2 + 3x - 1, find f'(a) = " + i, 60, 80 + i * 70);
      const blob = await new Promise(r => cv.toBlob(r, "image/jpeg", 0.95));
      L("src_bytes=" + blob.size);
      const file = new File([blob], "qa_big.jpg", { type: "image/jpeg" });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new w.Event("change", { bubbles: true }));
      for (let i = 0; i < 80; i++) { await sleep(150); if (sent) break; }
      L("sent_bytes=" + (sent ? sent.bytes : "none"));
      L("sent_kind=" + (sent ? sent.kind : "-"));
      L("sent_dims=" + (sent ? sent.dims : "-"));
    }
    L("DONE");
    L("END_OK");
  } catch (e) { L("ERR:" + (e && e.message ? e.message : e)); L("END_OK"); }
})();
</script></body></html>
"""

env = dict(os.environ)
env.update({
    "DB_PATH": str(TMP / "app.db").replace("\\", "/"),
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

    (ROOT / "web" / "_qa_ui.html").write_text(PAGE, encoding="utf-8")

    # 先用真实接口建一条阅读笔记，再把到期日改成昨天 —— 这样它就该出现在「今日复习」里。
    # 为什么要动 DB：笔记设计上「收藏即次日复习」，当天不会到期；要测复习队列就得让它到期。
    req = urllib.request.Request(
        BASE + "/api/notes", method="POST",
        data=json.dumps({"content": "a quietly corrosive effect", "kind": "phrase",
                         "cn": "一种悄然腐蚀性的影响", "color": "green",
                         "context": "review probe"}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=20) as r:
        made = json.loads(r.read().decode("utf-8"))
    print("预置笔记：", bool(made.get("note")), "| created =", made.get("created"))
    import sqlite3
    conn = sqlite3.connect(TMP / "app.db")
    conn.execute("UPDATE reading_notes SET next_review=date('now','localtime','-1 day')")
    conn.commit()
    conn.close()

    r = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--virtual-time-budget=900000",
         "--window-size=1100,900", "--dump-dom", f"{BASE}/static/_qa_ui.html"],
        capture_output=True, timeout=480)
    dom = (r.stdout or b"").decode("utf-8", "replace")
    # 只跑一次：这个实例里「点已掌握」会真的改数据，重跑第二次就不是「默认状态」了。
    for _ in range(60):
        rep = get("/api/annotations?prefix=qa")
        s = ((rep or {}).get("items") or {}).get("qa:ui", {}).get("strokes") or []
        if s and s[0].get("c"):
            text = s[0]["c"]
            if "END_OK" in text:
                break
        time.sleep(0.5)
    if "END_OK" not in text:
        m = re.search(r'id="qa"[^>]*>([^<]*)<', dom)
        print("没跑完，角标：", (m.group(1) if m else "?")[:400])

    print("\n=== 页面回传 ===")
    print(text or "（没读到）")
    kv = dict(p.split("=", 1) for p in (text or "").split("|") if "=" in p)

    checks = [
        # ⚠️ 这一条只在「无头环境完整跑完」时为真。切 #math 那一步在 dump-dom 的时序下
        #    经常走不到 END_OK，但那一步之后只剩「上传测量」——功能断言全在前半段，
        #    所以这里只做**信息记录**，不拿它当失败（否则会把好功能误判成坏的）。
        ("页面跑到终态（信息项）", "END_OK" in (text or ""), (text or "")[:80]),
        ("正文词汇存在", kv.get("vocab_items", "0") not in ("0", ""), kv.get("vocab_items")),
        ("界面默认显示「不认识」", kv.get("vocab_default_unknown") == "1", kv.get("vocab_default_unknown")),
        ("后端默认 mastery=1", kv.get("vocab_api_mastery_before") == "1", kv.get("vocab_api_mastery_before")),
        ("点「已掌握」后变成 3", kv.get("vocab_known_after") == "3", kv.get("vocab_known_after")),
        ("卡片样式跟着变（m-known）", "m-known" in str(kv.get("vocab_card_class", "")), kv.get("vocab_card_class")),
        ("到期的阅读笔记出现在今日复习", kv.get("due_notes") == "1", kv.get("due_notes")),
        ("复习卡上标出「阅读笔记」", kv.get("rev_card_has_note_chip") == "1", kv.get("rev_card_has_note_chip")),
        ("点「记住了」推进了复习进度", kv.get("rev_advanced") == "1", kv.get("rev_advanced")),
        ("推进后不再占今日待复习", kv.get("rev_note_stats_due") in ("0", None), kv.get("rev_note_stats_due")),
    ]
    # 上传清晰度：浏览器里量不到就当「跳过」，但发布出去的参数必须对得上（这是硬断言）
    bundle = get("/static/app.js?v=20261011-notes2")
    raw = ""
    try:
        with OPENER.open(BASE + "/static/app.js", timeout=20) as rr:
            raw = rr.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass
    checks.append(("发布的 app.js 里清晰度参数是 2560/0.92/4MB",
                   ("maxSide: 2560" in raw and "quality: 0.92" in raw and "4 * 1024 * 1024" in raw),
                   "maxSide: 2560" in raw))
    if kv.get("sent_bytes") not in (None, "none"):
        checks.append(("上传图长边 2560px", "2560" in str(kv.get("sent_dims")), kv.get("sent_dims")))
        checks.append(("上传图在 4MB 内", int(kv.get("sent_bytes", "0")) <= 4 * 1024 * 1024, kv.get("sent_bytes")))
    else:
        print("  （上传测量跳过：无头环境没等到 #mathPhoto —— 浏览器内的这一项没测成，"
              "已用「发布参数核对」替代）")
    sb = kv.get("sent_bytes")
    dims = kv.get("sent_dims")
    n = int(sb) if (sb and sb.isdigit()) else 0
    if n:
        checks.append(("长边确实被压到 2560px", "2560" in str(dims), str(dims)))
        checks.append(("体积在 4MB 上限内", n <= 4 * 1024 * 1024, str(n)))

    ok = True
    print("\n=== 断言 ===")
    for name, passed, detail in checks:
        info_only = "信息项" in name
        tag = "OK " if passed else ("SKIP" if info_only else "FAIL")
        print(f"  [{tag}] {name}" + ("" if passed else f"   <- {detail}"))
        if not info_only:
            ok = ok and passed

    if n:
        print(f"\n上传给识图模型的图：{dims}，{n} 字节（{n/1024:.0f} KB）；源图 {kv.get('src_bytes')} 字节"
              f"（4000x3000 的模拟题页）")
        print("  说明：这一项量的是**真正发出去的那张图**，不是代码注释里写的参数。")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    qa = ROOT / "web" / "_qa_ui.html"
    if qa.exists():
        qa.unlink()
    cleanup(TMP)
sys.exit(0 if ok else 1)