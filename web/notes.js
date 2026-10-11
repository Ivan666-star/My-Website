/* Prism · 阅读笔记（划选填充高亮 + 双击查词 + 独立笔记库）
   ============================================================
   用户 2026-10-11 的原话，逐条对应到这里：
     1. 「英语阅读文章 希望能够有双击单词给出翻译的功能」
        → dblclick 取词 → POST /api/word/lookup → 右下角小卡片（含"记入阅读笔记"）
     2. 「笔的功能修改 修改为划过所选的句子或单词 将其用颜色填充」
        → 原来的手写笔（pen.js）不再加载；选中文字后浮出颜色条，点一下就填充
     3. 「填充的单词或短语自动识别保留进阅读的笔记中 笔记单独是一个内容」
        → 填充即写库（不用再点保存），笔记是独立的一份数据 + 独立页面
     8. 「不能只是这次看了就过去了 要能够不断复现记住」
        → 笔记自动进入「今日复习」队列（1→2→4→7→15→30→60 天），和素材库同一个阶梯

   为什么单独一个文件：app.js 已经三千多行，再往里塞交互会越来越难改。
   这里只碰「正文选区 / 查词 / 高亮」这三件事，其他一律交给 app.js。
   ============================================================ */
(function () {
  "use strict";

  const HL_COLORS = ["yellow", "green", "blue", "pink"];
  const BODY_SEL = "#bodyText";

  let currentColor = "yellow";
  let pending = null;        // 待填充的选区：{text, para, start, context}
  let popWord = null;        // 查词卡片当前展示的词

  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#x27;");

  /* app.js 里的 state / toast / api / navigate 都是模块作用域，看不见；
     它显式挂在 window.PrismApp 上（见 app.js 里那段「给 notes.js 留的三个接口」）。
     ⚠️ 千万别再写成 window.state —— app.js 从来没有把 state 挂到 window 上，
     那会导致「填充成功、后端也落库了，但正文上就是不上色」。 */
  const bridge = () => window.PrismApp || {};

  function toast(msg, bad) {
    const fn = bridge().toast;
    if (fn) return fn(msg, bad);
    console.log("[notes]", msg);
  }
  async function api(path, opts) {
    const fn = bridge().api;
    if (fn) return fn(path, opts);
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...opts,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `请求失败（${r.status}）`);
    return data;
  }
  function appState() {
    const s = bridge().state;
    return s || window.state || {};
  }
  function currentDate() {
    return appState().date || "";
  }

  /* ---------------- 段落与偏移：高亮要能精确画回去 ---------------- */
  function paragraphs() {
    const body = $(BODY_SEL);
    return body ? Array.from(body.querySelectorAll("p")) : [];
  }

  /* 选区落在第几段、段内从第几个字符开始（按**纯文本**算，和高亮存的内容对齐） */
  function locateRange(range) {
    const ps = paragraphs();
    for (let i = 0; i < ps.length; i++) {
      if (!ps[i].contains(range.startContainer)) continue;
      const pre = range.cloneRange();
      pre.selectNodeContents(ps[i]);
      pre.setEnd(range.startContainer, range.startOffset);
      return { para: i, start: pre.toString().length };
    }
    return null;
  }

  /* 选区所在的那一句（给复习时当回忆线索） */
  function sentenceAround(text, start, len) {
    const before = text.slice(0, start);
    const after = text.slice(start + len);
    const left = Math.max(before.lastIndexOf(". "), before.lastIndexOf("? "), before.lastIndexOf("! "));
    const rightIdx = [after.indexOf(". "), after.indexOf("? "), after.indexOf("! ")]
      .filter(n => n >= 0);
    const right = rightIdx.length ? Math.min(...rightIdx) : -1;
    const s = (left >= 0 ? before.slice(left + 2) : before) + text.slice(start, start + len)
      + (right >= 0 ? after.slice(0, right + 1) : after.slice(0, 80));
    return s.trim().slice(0, 300);
  }

  function clearBar() {
    const bar = $("#hlBar");
    if (bar) bar.hidden = true;
    pending = null;
  }

  function showBar() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) { clearBar(); return; }
    const text = sel.toString().replace(/\s+/g, " ").trim();
    if (!text || text.length > 200) { clearBar(); return; }
    const range = sel.getRangeAt(0);
    const body = $(BODY_SEL);
    if (!body || !body.contains(range.commonAncestorContainer)) { clearBar(); return; }
    const pos = locateRange(range);
    if (!pos) { clearBar(); return; }
    const paraText = paragraphs()[pos.para].textContent || "";
    pending = {
      text,
      para: pos.para,
      start: pos.start,
      context: sentenceAround(paraText, pos.start, text.length),
    };
    const bar = $("#hlBar");
    const pv = $("#hlPreview");
    if (pv) pv.textContent = text.length > 42 ? text.slice(0, 42) + "…" : text;
    if (bar) {
      bar.hidden = false;
      bar.querySelectorAll("[data-hl-color]").forEach(b => {
        b.classList.toggle("on", b.dataset.hlColor === currentColor);
      });
    }
  }

  /* ---------------- 把高亮画回正文 ---------------- */
  /* 锚点优先用「第几段 + 段内偏移」，对不上再退回全文找一次 ——
     段落文本永远来自同一篇文章，所以正常情况下第一条就命中。 */
  function paintOne(note) {
    const ps = paragraphs();
    if (!ps.length) return false;
    const text = note.content || "";
    if (!text) return false;
    const order = [];
    if (typeof note.para_index === "number" && note.para_index >= 0 && ps[note.para_index]) {
      order.push(ps[note.para_index]);
    }
    ps.forEach(p => { if (!order.includes(p)) order.push(p); });
    for (const p of order) {
      if (p.querySelector(`[data-note="${note.id}"]`)) return true;
      const raw = p.textContent || "";
      const at = (typeof note.para_index === "number" && p === ps[note.para_index]
        && typeof note.char_start === "number" && raw.startsWith(text, note.char_start))
        ? note.char_start : raw.indexOf(text);
      if (at < 0) continue;
      wrap(p, at, text.length, note);
      return true;
    }
    return false;
  }

  /* 在纯文本偏移处包一个高亮 span：按文本节点走，避免破坏已有标签 */
  function wrap(p, start, len, note) {
    let seen = 0;
    const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT, null);
    let node;
    while ((node = walker.nextNode())) {
      const t = node.nodeValue || "";
      const nodeStart = seen;
      seen += t.length;
      if (seen <= start) continue;
      if (nodeStart >= start + len) break;
      const from = Math.max(0, start - nodeStart);
      const to = Math.min(t.length, start + len - nodeStart);
      if (to <= from) continue;
      const span = document.createElement("span");
      span.className = "hl hl-" + (HL_COLORS.includes(note.color) ? note.color : "yellow");
      span.dataset.note = note.id;
      span.title = (note.cn ? note.cn + "\n" : "") + "点击查看/编辑这条阅读笔记";
      const target = node.splitText(from);
      target.splitText(to - from);
      target.parentNode.replaceChild(span, target);
      span.appendChild(target);
      return;
    }
  }

  async function renderForArticle(articleId) {
    if (!articleId) return;
    let data;
    try { data = await api(`/api/notes/article/${articleId}`); } catch { return; }
    (data.items || []).forEach(paintOne);
  }

  /* ---------------- 交互绑定 ---------------- */
  function wordAtPoint(x, y, fallbackText) {
    let node = null, offset = 0;
    if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(x, y);
      if (pos) { node = pos.offsetNode; offset = pos.offset; }
    } else if (document.caretRangeFromPoint) {
      const r = document.caretRangeFromPoint(x, y);
      if (r) { node = r.startContainer; offset = r.startOffset; }
    }
    if (!node || node.nodeType !== 3) return (fallbackText || "").trim();
    const t = node.nodeValue || "";
    const isWord = c => /[A-Za-z'’-]/.test(c);
    let a = offset, b = offset;
    while (a > 0 && isWord(t[a - 1])) a--;
    while (b < t.length && isWord(t[b])) b++;
    const w = t.slice(a, b).replace(/^[-'’]+|[-'’]+$/g, "");
    return w || (fallbackText || "").trim();
  }

  async function lookup(word, context) {
    const pop = $("#wordPop");
    if (!pop) return;
    popWord = { word, context };
    $("#wpWord").textContent = word;
    $("#wpPhon").textContent = "";
    $("#wpBody").innerHTML = `<p class="muted small">查询中…</p>`;
    pop.hidden = false;
    try {
      const r = await api("/api/word/lookup", {
        method: "POST",
        body: JSON.stringify({ word, context }),
      });
      $("#wpWord").textContent = r.word || word;
      $("#wpPhon").textContent = r.phonetic || "";
      $("#wpBody").innerHTML = `
        <div class="wp-cn">${esc(r.cn || "（没查到简明释义）")}</div>
        ${r.in_context ? `<div class="wp-ctx"><b>在这句里：</b>${esc(r.in_context)}</div>` : ""}
        ${r.pos ? `<div class="muted small">词性：${esc(r.pos)}</div>` : ""}
        ${r.example ? `<div class="wp-ex">${esc(r.example)}</div>` : ""}
        ${r.cached ? `<div class="muted small">（这次用的是刚才查过的结果）</div>` : ""}`;
    } catch (e) {
      $("#wpBody").innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
    }
  }

  function bind() {
    const body = $(BODY_SEL);
    if (!body) return;

    /* 桌面双击 / 手机双击都走 dblclick；手机上先有选区，所以也允许
       「选中 → 点查词」这条更稳的路（见下面的选区判断）。 */
    body.addEventListener("dblclick", e => {
      const sel = window.getSelection();
      const picked = sel && !sel.isCollapsed ? sel.toString().trim() : "";
      const word = (picked && picked.length <= 40)
        ? picked.replace(/\s+/g, " ")
        : wordAtPoint(e.clientX, e.clientY, "");
      if (!word) return;
      const ps = paragraphs();
      const p = ps.find(x => x.contains(e.target)) || e.target.closest("p");
      const paraText = (p && p.textContent) || "";
      const at = paraText.toLowerCase().indexOf(word.toLowerCase());
      lookup(word, sentenceAround(paraText, Math.max(0, at), word.length));
    });

    body.addEventListener("mouseup", () => setTimeout(showBar, 10));
    body.addEventListener("touchend", () => setTimeout(showBar, 120));

    document.addEventListener("selectionchange", () => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) {
        /* 手机上点空白处收卡片；但刚点完工具条时不要收（否则点了等于没点） */
        setTimeout(() => {
          const active = document.activeElement;
          const inBar = active && active.closest && active.closest("#hlBar");
          if (!inBar && !pending) return;
        }, 0);
      }
    });

    document.addEventListener("click", async e => {
      const dot = e.target.closest("[data-hl-color]");
      if (dot) {
        currentColor = dot.dataset.hlColor;
        $("#hlBar").querySelectorAll("[data-hl-color]").forEach(b =>
          b.classList.toggle("on", b === dot));
        return;
      }
      if (e.target.closest("#hlCancel")) { window.getSelection()?.removeAllRanges(); clearBar(); return; }
      if (e.target.closest("#hlSave")) { await saveHighlight(); return; }
      if (e.target.closest("#wpClose")) { $("#wordPop").hidden = true; return; }
      if (e.target.closest("#wpSave")) { await saveFromPop(); return; }

      /* 点正文里已有的高亮 → 打开笔记页对应条目（简单起见：跳到笔记页） */
      const marked = e.target.closest("[data-note]");
      if (marked) {
        if (bridge().navigate) bridge().navigate("notes");
        return;
      }
      if (!e.target.closest("#hlBar") && !e.target.closest("#wordPop")) {
        /* 选区没了就收工具条 */
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed) clearBar();
      }
    });
  }

  async function saveHighlight() {
    if (!pending) { toast("先选中要填充的词或句子", true); return; }
    const st = appState();
    const art = st.bundle && st.bundle.article;
    const btn = $("#hlSave");
    btn.disabled = true;
    try {
      const r = await api("/api/notes", {
        method: "POST",
        body: JSON.stringify({
          content: pending.text,
          color: currentColor,
          context: pending.context,
          para: pending.para,
          start: pending.start,
          date: currentDate(),
        }),
      });
      if (r.created) toast("已填充并记入阅读笔记，明天会出现在今日复习里");
      else toast("这条已经在笔记里了（颜色已更新）");
      if (art && art.id) paintOne(r.note);
      window.getSelection()?.removeAllRanges();
      clearBar();
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  }

  async function saveFromPop() {
    if (!popWord) return;
    const cn = ($("#wpBody").querySelector(".wp-cn") || {}).textContent || "";
    const st = appState();
    const art = st.bundle && st.bundle.article;
    try {
      const r = await api("/api/notes", {
        method: "POST",
        body: JSON.stringify({
          content: popWord.word,
          cn: cn.trim(),
          context: popWord.context,
          color: currentColor,
          date: currentDate(),
        }),
      });
      toast(r.created ? "已记入阅读笔记" : "这条已经在笔记里了");
      $("#wordPop").hidden = true;
    } catch (e) { toast(e.message, true); }
  }

  window.Notes = {
    init: bind,
    renderForArticle,
    colors: HL_COLORS,
    /* 调试/测试用 */
    _paintOne: paintOne,
    _locateRange: locateRange,
    _sentenceAround: sentenceAround,
  };
})();