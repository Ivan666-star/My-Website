/* ==========================================================
   Prism · 前端逻辑（无框架，纯原生）
   ========================================================== */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  date: null,
  bundle: null,
  meta: null,
  auth: {},            // 当前账号 / 运行模式（未启用账号体系时是空对象）
  revealed: false,
  readStart: Date.now(),
  elapsed: 0,
  timerId: null,
  submitting: false,
};

/* ---------------- 工具 ---------------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), isErr ? 5200 : 2600);
}
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
  } catch (e) {
    /* fetch 本身失败 = 后端根本没在应答（服务没启动 / 断网）。
       必须单独标记出来：调用方要能把它和「服务答了但返回错误」分开，
       否则界面会显示成「今天的内容还没准备好」，甚至「还没有配置 AI 模型」，
       把人往 .env 上带，而实际只是服务没在跑（真实踩过）。 */
    const err = new Error("连不上后端服务");
    err.offline = true;
    throw err;
  }
  let data = null;
  try { data = await res.json(); } catch { /* ignore */ }
  // 服务暴露在公网且会话过期时，直接回登录页，不要抛一堆看不懂的错误
  if (res.status === 401 && (data ? data.login_required : false)) {
    // 登录后回到原来的整页（#library / #stats / #history / #review），而不是一律丢回首页
    const back = /^#[a-z-]+$/.test(location.hash) ? "/" + location.hash : "/";
    location.href = "/login?next=" + encodeURIComponent(back);
    throw new Error("需要重新登录");
  }
  if (!res.ok) throw new Error((data && (data.detail || data.message)) || `请求失败（${res.status}）`);
  return data;
}
function fmtDate(d) {
  const dt = new Date(d + "T00:00:00");
  return `${dt.getMonth() + 1} 月 ${dt.getDate()} 日 · 周${"日一二三四五六"[dt.getDay()]}`;
}
/* 真题用 exam-2010-text2 这种伪日期，不能丢给 fmtDate，
   否则会渲染成 NaN。这里统一由 dateLabel 判断该显示成什么。 */
const IS_REAL_DATE = /^\d{4}-\d{2}-\d{2}$/;
function dateLabel(d, fallback = "") {
  if (IS_REAL_DATE.test(d || "")) return fmtDate(d);
  return fallback || "真题精读";
}
function mmss(sec) {
  const m = String(Math.floor(sec / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}
function paragraphs(text) {
  return String(text || "").split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
}

/* ---------------- 搜索：多关键词 AND + 命中高亮 ---------------- */
function queryTerms() {
  return (libState.q || "").toLowerCase().split(/\s+/).filter(Boolean);
}
function hitSearch(it, terms) {
  if (!terms.length) return true;
  const hay = [it.content, it.cn, it.example, it.usage, it.category, (it.tags || []).join(" ")]
    .join(" ").toLowerCase();
  return terms.every(t => hay.includes(t));
}
/* 先转义再高亮：顺序反了会把标签本身也转义掉 */
function hl(s) {
  let out = esc(s);
  for (const t of queryTerms()) {
    const safe = esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (!safe) continue;
    out = out.replace(new RegExp(safe, "gi"), m => `<mark>${m}</mark>`);
  }
  return out;
}

/* ==========================================================
   阅读外观：字号 / 行距 / 字体
   ----------------------------------------------------------
   用户 2026-10-11：「在英语里面 那个字体看久了眼睛会花 希望有可以涉及字体调节的方式」。
   做法：三个 CSS 变量（--read-size / --read-lh / --read-font）写在 <html> 上，
   中文翻译另有一套（--read-zh-*）。存 localStorage，**不上云**：
   这是设备偏好（手机上想大一点、电脑上想小一点很正常），跟账号走反而不合适。
   ========================================================== */
const READING_KEY = "prism.reading.v1";
const READING_PRESETS = {
  size: [
    { k: "s", label: "小", en: "17px", lh: "", zh: "13.5px" },
    { k: "m", label: "标准", en: "18.5px", lh: "", zh: "14.5px" },
    { k: "l", label: "大", en: "20.5px", lh: "", zh: "16px" },
    { k: "xl", label: "特大", en: "22.5px", lh: "", zh: "17.5px" },
    { k: "xxl", label: "超大", en: "25px", lh: "", zh: "19px" },
  ],
  lh: [
    { k: "tight", label: "紧凑", v: "1.75" },
    { k: "normal", label: "标准", v: "1.92" },
    { k: "loose", label: "宽松", v: "2.15" },
    { k: "xloose", label: "很宽", v: "2.4" },
  ],
  font: [
    { k: "serif", label: "衬线（阅读感）", v: 'var(--serif)' },
    { k: "sans", label: "无衬线（护眼清晰）", v: 'var(--sans)' },
  ],
};
const READING_DEFAULT = { size: "m", lh: "normal", font: "serif" };

const Reading = {
  cfg: { ...READING_DEFAULT },
  load() {
    try {
      const raw = localStorage.getItem(READING_KEY);
      if (raw) this.cfg = { ...READING_DEFAULT, ...JSON.parse(raw) };
    } catch (e) { this.cfg = { ...READING_DEFAULT }; }
    this.apply();
  },
  save() {
    try { localStorage.setItem(READING_KEY, JSON.stringify(this.cfg)); } catch (e) { /* 隐身模式等，忽略 */ }
  },
  set(key, val) {
    this.cfg[key] = val;
    this.save();
    this.apply();
  },
  reset() {
    this.cfg = { ...READING_DEFAULT };
    this.save();
    this.apply();
  },
  apply() {
    const root = document.documentElement;
    const sizeP = READING_PRESETS.size.find(x => x.k === this.cfg.size) || READING_PRESETS.size[1];
    const lhP = READING_PRESETS.lh.find(x => x.k === this.cfg.lh) || READING_PRESETS.lh[1];
    const fontP = READING_PRESETS.font.find(x => x.k === this.cfg.font) || READING_PRESETS.font[0];
    root.style.setProperty("--read-size", sizeP.en);
    root.style.setProperty("--read-lh", lhP.v);
    root.style.setProperty("--read-zh-size", sizeP.zh);
    /* 中文行距跟着英文走一档，但别太夸张（中文方块字密，行距过大反而断行难读） */
    root.style.setProperty("--read-zh-lh", String(Math.min(2.1, Number(lhP.v) * 0.96)));
    root.style.setProperty("--read-font", fontP.v);
    /* 一旦用户动过，就把 data-read 打上：
       style.css 里各档设备的 .body-text 默认值都写成 :root:not([data-read]) ...，
       这样用户的选择永远压得住设备默认值。 */
    root.setAttribute("data-read", this.cfg.font === "serif" && this.cfg.size === "m" && this.cfg.lh === "normal" ? "default" : "custom");
  },
  html() {
    const seg = (name, items, cur) => `<div class="read-seg">${items.map(it =>
      `<button data-read-set="${name}" data-read-val="${it.k}" class="${cur === it.k ? "on" : ""}">${it.label}</button>`
    ).join("")}</div>`;
    const sizeP = READING_PRESETS.size.find(x => x.k === this.cfg.size) || READING_PRESETS.size[1];
    const lhP = READING_PRESETS.lh.find(x => x.k === this.cfg.lh) || READING_PRESETS.lh[1];
    return `<div class="read-panel">
      <div class="read-row"><span class="read-lbl">字号</span>${seg("size", READING_PRESETS.size, this.cfg.size)}</div>
      <div class="read-row"><span class="read-lbl">行距</span>${seg("lh", READING_PRESETS.lh, this.cfg.lh)}
        <button class="btn tiny read-reset" data-read-reset="1">恢复默认</button></div>
      <div class="read-row"><span class="read-lbl">字体</span>${seg("font", READING_PRESETS.font, this.cfg.font)}</div>
      <div class="read-preview">The designers of casinos understand this well: they create
        environments in which rational impulses are turned against the decision-maker.<br>
        <span style="font-family:var(--sans)">设计者很清楚这一点：他们营造出一种环境，让理性的冲动反过来对付决策者本身。</span></div>
    </div>`;
  },
};
Reading.load();

/* ---------------- 启动 ---------------- */
async function boot() {
  try {
    const data = await api("/api/bootstrap");
    state.date = data.date;
    state.meta = data.meta;
    state.bundle = data.bundle;
    state.auth = data.auth || {};
    renderMeta(data);
    if (data.bundle) renderBundle(data.bundle);
    else showEmpty(data.meta);
  } catch (e) {
    if (e.offline) showOffline();
    else { toast(e.message, true); showEmpty({}); }
  } finally {
    $("#loadingState").hidden = true;
  }
  /* 阅读笔记：划选填充 + 双击查词。挂一次就够，它会自己在正文上做事件委托。 */
  if (window.Notes) window.Notes.init();
  /* 后台可能还有一次没跑完的生成（用户点了之后刷新/换设备回来了）——
     接着把进度显示出来，别让他以为白点了。 */
  resumePendingJob();
}

/* ------------------------------------------------------------
  给 notes.js 留的三个接口
  ------------------------------------------------------------
  app.js 里的 state / toast / api 都是**模块作用域**，别的文件看不见。
  notes.js 需要知道「当前是哪一天、哪篇文章」才能把高亮画到正确的位置、
  写进正确的笔记；也需要复用同一个 toast 与同一个 api 封装（带错误处理）。
  这里显式挂三个引用出去 —— 别再让 notes.js 去猜 window.state 存在不存在：
  那个 bug 的具体表现是「填充成功、后端也落库了，但正文上没上色」，
  排查起来很费劲，所以宁可留这一小段显式桥接。 */
window.PrismApp = {
  get state() { return state; },
  toast: (msg, bad) => toast(msg, bad),
  api: (path, opts) => api(path, opts),
  navigate: v => navigate(v),
};

function renderMeta(data) {
  $("#topDate").textContent = dateLabel(data.date);
  $("#streakNum").textContent = data.meta.streak || 0;
  setReviewBadge(data.meta.review_due);
  setPendingBadge(data.auth && data.auth.pending_count);
  renderAccountChip();
  refreshMathBadge();
  const who = data.auth && data.auth.username ? `　·　账号：${data.auth.display_name || data.auth.username}` : "";
  $("#footNote").textContent = (data.meta.ai_ready
    ? `模型：${data.meta.model}　·　阶段：${data.meta.stage}`
    : "未配置 AI 模型，当前展示内置示例内容") + who;
}

function setReviewBadge(n) {
  const el = $("#revBadge");
  if (!el) return;
  const v = Number(n) || 0;
  el.hidden = v <= 0;
  el.textContent = v > 99 ? "99+" : String(v);
  $("#btnReview").title = v > 0 ? `今日待复习 ${v} 条` : "今日复习";
}

/* 只有管理员看得到：有几个人自己提交了注册申请、在等他点头。
   不给角标的话，申请就静静躺在用户管理页里没人知道。 */
function setPendingBadge(n) {
  const el = $("#pendBadge");
  if (!el) return;
  const v = Number(n) || 0;
  el.hidden = v <= 0;
  el.textContent = v > 99 ? "99+" : String(v);
  const b = $("#btnSettings");
  if (b) b.title = v > 0 ? `设置（有 ${v} 条注册申请待批准）` : "设置";
}

async function refreshPendingBadge() {
  try { setPendingBadge((await api("/api/me")).pending_count); } catch { /* 拿不到就不显示 */ }
}

/* 顶栏右上角的「我的账号」标志（用户 2026-10-07 要求：主界面就摆这一个）。
   账号体系下 = 首字 + 昵称；单人自用 / 离线退化成「我」。
   点开都走设置（改密码 / 退出登录 / 用户管理都在里面）。 */
function renderAccountChip() {
  const a = state.auth || {};
  const who = String(a.display_name || a.username || "").trim();
  const av = $("#acctAvatar"), nm = $("#acctName"), btn = $("#btnAccount");
  if (av) av.textContent = who ? Array.from(who)[0] : "我";
  if (nm) nm.textContent = who;
  if (btn) {
    btn.title = who
      ? `我的账号：${who}${a.is_admin ? "（管理员）" : ""} · 点开设置`
      : "我的账号 · 点开设置";
  }
}

function showEmpty(meta) {
  $("#articleWrap").hidden = true;
  $("#emptyState").hidden = false;
  /* 从别的空态切回来时，按钮显隐要复位，否则会残留上一态的样子 */
  $("#btnExamToday").hidden = false;
  $("#btnImport").hidden = false;
  $("#btnRetry").hidden = true;
  /* 页脚那个"重新生成今日内容"是给"已经有内容"准备的；没内容时卡里已经有生成按钮，
     留着它只会多一个看不懂的入口（它由 renderBundle 控制，空态下不会自动藏） */
  $("#btnRegen").hidden = true;
  if (!meta.ai_ready) {
    $("#emptyTitle").textContent = "还没有配置 AI 模型";
    $("#emptyDesc").textContent =
      "在项目根目录的 .env 里填好 AI_API_KEY / AI_BASE_URL / AI_MODEL，重启服务后即可每天自动生成内容。也可以先点「粘贴真实外刊材料」导入一段英文试试解析效果。";
  } else {
    $("#emptyTitle").textContent = "今天的内容还没准备好";
    $("#emptyDesc").textContent = "点下面的按钮，让 AI 现在生成今天这一套（约需 1～2 分钟）。";
  }
  $("#btnGenToday").hidden = !meta.ai_ready;
}

/* 后端完全没应答时的空态。
   ⚠️ 不要复用它上面那个 showEmpty()：那条路径按 meta.ai_ready 分支，
   而网络失败时 meta 是空的 → 会显示成「还没有配置 AI 模型 + 去改 .env」，
   把人带偏。这里必须直说「服务没在跑」，并给出一个能照做的动作。 */
function showOffline() {
  state.bundle = null;
  $("#articleWrap").hidden = true;
  $("#emptyState").hidden = false;
  $("#emptyTitle").textContent = "连不上后端服务";
  $("#emptyDesc").innerHTML =
    "界面打开了，但读不到数据 —— 后端服务没有在运行（或本页是从离线缓存打开的）。<br>"
    + "在电脑上双击项目根目录的 <b>启动.bat</b> 把服务起起来，再点下面的「重试」。";
  $("#btnGenToday").hidden = true;
  $("#btnExamToday").hidden = true;
  $("#btnImport").hidden = true;
  $("#btnRegen").hidden = true;      // 离线时点它只会再报一次错，别给假指望
  $("#btnRetry").hidden = false;
}

/* ---------------- 渲染文章 ---------------- */
function renderBundle(b) {
  const a = b.article;
  $("#emptyState").hidden = true;
  $("#articleWrap").hidden = false;
  $("#btnRetry").hidden = true;

  $("#heroMeta").textContent = a.source || "";
  $("#heroTitle").textContent = a.title;
  $("#heroSub").textContent = a.subtitle || "";
  $("#heroSub").hidden = !a.subtitle;

  const chips = [
    `<span class="chip accent">难度 ${a.difficulty}/5</span>`,
    `<span class="chip">${esc(a.topic || "")}</span>`,
    `<span class="chip">${a.word_count} 词</span>`,
    `<span class="chip">预计 ${a.est_minutes} 分钟</span>`,
    a.source_type === "demo" ? `<span class="chip">内置示例</span>` : "",
    a.source_type === "real" ? `<span class="chip">真实材料</span>` : "",
    a.source_type === "exam" ? `<span class="chip exam-chip">🎯 真题精读</span>` : "",
  ];
  /* 手机上顶栏塞不下「日期」（见 style.css 窄机顶栏段：窄屏 .sub display:none），
     和「连续天数」同一套办法 —— 挪进 chip，信息不丢，位置还更顺手。 */
  const td = $("#topDate");
  if (td && getComputedStyle(td).display === "none") {
    chips.unshift(`<span class="chip">${esc(dateLabel(a.date, a.source))}</span>`);
  }
  /* 手机上顶栏塞不下「连续天数」（见 style.css 设备自适应：窄屏它 display:none），
     与其为了塞图标把信息丢掉，不如挪到今日页的 chip 里 —— 位置还更顺手。 */
  const sc = $("#streakChip");
  if (sc && getComputedStyle(sc).display === "none") {
    const n = parseInt(($("#streakNum").textContent || "0").trim(), 10) || 0;
    if (n > 0) chips.push(`<span class="chip">🔥 连续 ${n} 天</span>`);
  }
  $("#heroChips").innerHTML = chips.join("");

  // 真题是导入来的，没有「重新生成」这回事，改成回今日内容
  const isExam = a.source_type === "exam";
  $("#btnRegen").hidden = isExam;
  $("#btnBackToday").hidden = !isExam;

  const body = $("#bodyText");
  body.innerHTML = paragraphs(a.body).map(p => `<p>${esc(p)}</p>`).join("");
  body.classList.remove("hide-body");

  // 计数器
  clearInterval(state.timerId);
  state.readStart = Date.now();
  state.timerId = setInterval(() => {
    state.elapsed = Math.floor((Date.now() - state.readStart) / 1000);
    $("#readTimer").textContent = mmss(state.elapsed);
  }, 1000);

  $("#gate").hidden = false;
  $("#analysis").hidden = true;

  renderTranslation(b);
  renderVocab(b.vocab || []);
  renderSentGram(b.sentences || [], b.grammar || []); // 合并长难句和语法
  /* 阅读逻辑 / 命题视角：用户 2026-10-11 说暂时不需要，入口已从 index.html 撤掉。
     两个函数仍保留（数据也还在库里），容器不存在时它们自己会安静跳过 ——
     以后想接回来，把 index.html 里那两块 <details> 加回去就行。 */
  renderLogic(a.logic || {});
  renderExamFocus(a.exam_focus || {});
  renderWriting(b.writing_items || []);
  renderImitation(b);
  renderTodayStats(b.study_log);
  /* 高亮比手写笔好在这儿：它是「数据」不是「像素」，
     换设备、重开文章都能按段落位置精确画回去。 */
  if (window.Notes) window.Notes.renderForArticle(a.id);
}

/* 统一入口：把任意一个 bundle 送到主视图（每日内容与真题共用同一套精读界面） */
function showBundle(bundle) {
  if (!bundle || !bundle.article) { toast("内容不存在", true); return; }
  state.bundle = bundle;
  state.date = bundle.article.date;
  state.revealed = false;
  $("#topDate").textContent = dateLabel(bundle.article.date, bundle.article.source);
  $("#emptyState").hidden = true;
  renderBundle(bundle);
}

/* ---------------- 翻译 ---------------- */
function renderTranslation(b) {
  const rows = b.article.translation || [];
  $("#transBody").innerHTML = rows.map(r => `
    <div class="trans-item">
      <div class="trans-en">${esc(r.en)}</div>
      <div class="trans-zh">${esc(r.zh)}</div>
      ${r.note ? `<div class="trans-note"><b>翻译要点：</b>${esc(r.note)}</div>` : ""}
    </div>`).join("");
}

/* ---------------- 词汇 ---------------- */
const MASTERY_CLASS = { 1: "m-unknown", 2: "m-focus", 3: "m-known" };
function renderVocab(list) {
  const unknown = list.filter(v => (v.mastery || 1) === 1).length;
  $("#vocabSub").textContent = `${list.length} 个 · 待攻克 ${unknown}`;
  const box = $("#vocabList");
  if (!list.length) { box.innerHTML = `<p class="muted small">本篇暂无词汇条目</p>`; return; }
  /* 用户 2026-10-11 的口径：这些词**默认都按「不认识」给我**，
     由我自己挑哪些已经掌握 —— 所以列表默认全是不认识态，点一下就改。 */
  box.innerHTML = `<p class="muted small" style="margin:0 0 8px">
      释义按<b>考研真题的高频义</b>给（不是词典的全部义项）。默认都按「不认识」收着，已经会的直接点「已掌握」，就不会再占你的待攻克数。
    </p>` + list.map(v => `
    <div class="v-item ${MASTERY_CLASS[v.mastery] || ""}" data-vid="${v.id}">
      <div class="v-head">
        <span class="v-word">${esc(v.word)}</span>
        <span class="v-pos">${esc(v.pos || "")}</span>
        <span class="v-imp">考研重要度 ${"●".repeat(v.importance || 3)}${v.occurrence > 1 ? ` · 累计出现 ${v.occurrence} 次` : ""}</span>
      </div>
      <div class="v-mean"><b>考研常考义：</b>${esc(v.meaning_in_context || v.cn_definition || "")}</div>
      ${v.common_meanings ? `<div class="v-sub">其他高频义：${esc(v.common_meanings)}</div>` : ""}
      ${v.exam_note ? `<div class="v-exam"><b>🎯 真题考法：</b>${esc(v.exam_note)}</div>` : ""}
      ${v.example ? `<div class="v-ex">${esc(v.example)}</div>` : ""}
      <div class="v-actions">
        <button class="btn tiny ${v.mastery === 1 ? "on" : ""}" data-act="unknown">不认识</button>
        <button class="btn tiny ${v.mastery === 2 ? "on" : ""}" data-act="focus">重点学习</button>
        <button class="btn tiny ${v.mastery === 3 ? "on" : ""}" data-act="known">已掌握</button>
        <button class="btn tiny ${v.favorite ? "on" : ""}" data-act="${v.favorite ? "unfavorite" : "favorite"}">${v.favorite ? "★ 已收藏" : "☆ 收藏"}</button>
      </div>
    </div>`).join("");
}

$("#vocabList").addEventListener("click", async e => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const vid = btn.closest(".v-item").dataset.vid;
  const act = btn.dataset.act;
  const isFav = act === "favorite" || act === "unfavorite";
  const orig = btn.textContent;
  if (isFav) { btn.disabled = true; if (act === "favorite") btn.textContent = "归类中…"; }
  try {
    const r = await api(`/api/vocab/${vid}/mark`, { method: "POST", body: JSON.stringify({ action: act }) });
    const item = state.bundle.vocab.find(v => String(v.id) === String(vid));
    if (item) Object.assign(item, r.vocab);
    renderVocab(state.bundle.vocab);
    if (act === "favorite" && r.material) {
      const libName = r.material.library === "writing" ? "作文素材库" : "阅读积累库";
      toast(`已归入【${libName} · ${POS_LABEL[r.material.position] || "通用"}】${r.material.usage ? "　" + r.material.usage : ""}`);
    } else if (act === "favorite") {
      toast("已加入素材库");
    }
    if (act === "unfavorite") toast("已取消收藏");
    refreshTodayStats();
  } catch (err) { toast(err.message, true); }
  finally { if (isFav) { btn.disabled = false; btn.textContent = orig; } }
});

/* ---------------- 长难句 + 语法（合并） ---------------- */
function renderSentGram(sentList, gramList) {
  const box = $("#sentGramList");
  if (!box) return;

  const sentCount = sentList.length;
  const gramCount = gramList.length;
  $("#sentGramSub").textContent = `${sentCount} 句长难句 · ${gramCount} 个语法点`;

  if (!sentCount && !gramCount) {
    box.innerHTML = `<p class="muted small">本篇暂无长难句和语法条目</p>`;
    return;
  }

  // 长难句部分
  const sentRows = [
    ["句子主干", s => s.skeleton, "en"],
    ["主句", s => s.main_clause, "en"],
    ["主语", s => s.subject],
    ["谓语", s => s.predicate],
    ["宾语/表语", s => s.object],
    ["非谓语", s => s.non_finite],
    ["插入结构", s => s.insertion],
    ["同位语", s => s.appositive],
    ["逻辑关系", s => s.logic],
  ];

  const sentHTML = sentCount ? `
    <div class="sg-section">
      <div class="sg-section-title">📐 长难句分析 (${sentCount})</div>
      ${sentList.map((s, i) => `
        <div class="s-item">
          <div class="s-idx">SENTENCE ${i + 1}</div>
          <div class="s-en">${esc(s.en)}</div>
          <div class="s-zh">${esc(s.zh)}</div>
          <button class="btn tiny s-toggle" data-toggle>展开成分分析 ↓</button>
          <div class="s-detail" hidden>
            ${sentRows.filter(([, f]) => f(s)).map(([k, f, cls]) =>
              `<div class="s-row"><div class="s-key">${k}</div><div class="s-val ${cls || ""}">${esc(f(s))}</div></div>`).join("")}
            ${(s.clauses || []).length ? `<div class="s-row"><div class="s-key">从句</div><div class="s-val"><ul class="s-list">${s.clauses.map(c => `<li>${esc(c)}</li>`).join("")}</ul></div></div>` : ""}
            ${(s.modifiers || []).length ? `<div class="s-row"><div class="s-key">修饰成分</div><div class="s-val"><ul class="s-list">${s.modifiers.map(c => `<li>${esc(c)}</li>`).join("")}</ul></div></div>` : ""}
            ${s.why_hard ? `<div class="s-why"><b>为什么值得学：</b>${esc(s.why_hard)}</div>` : ""}
            <div class="w-actions"><button class="btn tiny" data-fav-sent="${i}">☆ 收藏这句</button></div>
          </div>
        </div>`).join("")}
    </div>
  ` : "";

  // 语法部分
  const gramHTML = gramCount ? `
    <div class="sg-section">
      <div class="sg-section-title">🧠 语法知识点 (${gramCount})</div>
      ${gramList.map((g, i) => {
        const ex = (g.examples || [])[0] || "";
        return `
          <div class="g-item">
            <div class="g-name">${esc(g.name)}<span class="g-cat">${esc(g.category || "句法")}</span></div>
            <div class="g-text">${esc(g.explanation)}</div>
            ${g.pattern ? `<div class="g-pattern">结构：${esc(g.pattern)}</div>` : ""}
            ${ex ? `<div class="g-ex">${esc(ex)}</div>` : ""}
            <div class="g-actions">
              <button class="btn tiny" data-gram-save="${i}">＋ 收进语法积累库</button>
            </div>
          </div>`;
      }).join("")}
    </div>
  ` : "";

  box.innerHTML = sentHTML + gramHTML;
}

// 长难句和语法的事件监听
$("#sentGramList").addEventListener("click", async e => {
  // 长难句展开/收起
  const tg = e.target.closest("[data-toggle]");
  if (tg) {
    const d = tg.parentElement.querySelector(".s-detail");
    d.hidden = !d.hidden;
    tg.textContent = d.hidden ? "展开成分分析 ↓" : "收起 ↑";
    return;
  }

  // 收藏长难句
  const fav = e.target.closest("[data-fav-sent]");
  if (fav) {
    const s = state.bundle.sentences[Number(fav.dataset.favSent)];
    fav.disabled = true; fav.textContent = "归类中…";
    try {
      const r = await api("/api/favorite", {
        method: "POST",
        body: JSON.stringify({
          item_type: "sentence", content: s.en, cn: s.zh, example: s.skeleton,
          tags: ["长难句"], date: state.date,
          source_title: state.bundle.article.title, source_topic: state.bundle.article.topic,
        }),
      });
      const libName = r.library === "writing" ? "作文素材库" : "阅读积累库";
      toast(`已归入【${libName} · ${POS_LABEL[r.position] || "通用"}】${r.usage ? "　" + r.usage : ""}`);
      refreshTodayStats();
    } catch (err) { toast(err.message, true); }
    finally { fav.disabled = false; fav.textContent = "★ 已收藏"; }
    return;
  }

  // 收藏语法
  const btn = e.target.closest("[data-gram-save]");
  if (btn) {
    const g = (state.bundle.grammar || [])[Number(btn.dataset.gramSave)];
    if (!g) return;
    btn.disabled = true; btn.textContent = "收集中…";
    try {
      const r = await api("/api/grammar/save", {
        method: "POST",
        body: JSON.stringify({
          name: g.name, explanation: g.explanation, pattern: g.pattern,
          example: (g.examples || [])[0] || "", category: g.category, date: state.date,
        }),
      });
      btn.textContent = "★ 已在语法库";
      if (r.created && r.item) {
        NOTES_CACHE.grammars = [r.item, ...(NOTES_CACHE.grammars || [])];
      }
      toast(r.created
        ? "已收进语法积累库（不会进入复习队列）"
        : "这条语法点已经在库里了");
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false; btn.textContent = "＋ 收进语法积累库";
    }
  }
});

/* ---------------- 阅读逻辑 ----------------
   入口已在 index.html 撤掉（用户 2026-10-11：暂时不需要）。
   这里保留渲染逻辑本身，容器不在就直接跳过 —— 别让缺元素把整页渲染打断。 */
function renderLogic(lg) {
  const box = $("#logicBody");
  if (!box) return;
  const struct = lg.structure || [];
  box.innerHTML = `
    ${lg.overall ? `<div class="lg-overall"><b>中心论点：</b>${esc(lg.overall)}</div>` : ""}
    ${struct.map(p => `
      <div class="lg-para">
        <div class="lg-num">${p.para}</div>
        <div>
          <div class="lg-role">${esc(p.role || "")}</div>
          <div class="lg-idea">${esc(p.main_idea || "")}</div>
        </div>
      </div>`).join("")}
    ${lg.author_attitude ? `<div class="lg-att"><b>作者态度：</b>${esc(lg.author_attitude)}</div>` : ""}
    ${(lg.reading_tips || []).length ? `<ul class="lg-tips">${lg.reading_tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
  `;
}

/* ---------------- 命题视角（出题人怎么设题） ---------------- */
function efQtypeCount(sp) {
  return new Set(sp.map(p => (p.qtype || "").trim()).filter(Boolean)).size;
}

function renderExamFocus(f) {
  const box = $("#examBody");
  if (!box) return;                      /* 入口已撤，见 renderLogic 上面的说明 */
  const sp = f.set_points || [];
  const sub = $("#examSub");
  if (sub) {
    sub.textContent = f.pattern
      ? `${f.pattern} · ${sp.length} 个设题点 · 覆盖 ${efQtypeCount(sp)} 种题型`
      : "";
  }
  if (!f.pattern && !sp.length) {
    box.innerHTML = `<p class="muted small">本篇没有命题视角数据。</p>
      <p class="muted small">更早生成的文章解析里不含这部分——点上方「重新生成本篇」或导入真题后，就会有出题人视角的完整拆解。</p>`;
    return;
  }

  const traps = (list) => !(list || []).length ? "" : `
    <div class="ef-traps">
      ${list.map(t => `
        <div class="ef-trap">
          <div class="ef-trap-head"><span class="ef-trap-tag">${esc(t.trap || "干扰项")}</span></div>
          ${t.sample_option ? `<div class="ef-trap-opt">${esc(t.sample_option)}</div>` : ""}
          ${t.why_wrong ? `<div class="ef-trap-why">${esc(t.why_wrong)}</div>` : ""}
        </div>`).join("")}
    </div>`;

  box.innerHTML = `
    ${f.pattern ? `<div class="ef-pattern-row">
        <span class="ef-pattern">${esc(f.pattern)}</span>
        <span class="ef-meta">${sp.length} 个设题点 · 覆盖 ${efQtypeCount(sp)} 种题型</span>
      </div>` : ""}
    ${f.main_thread ? `<div class="ef-thread"><b>出题人眼里的主线：</b>${esc(f.main_thread)}</div>` : ""}
    ${sp.map(p => `
      <div class="ef-item">
        <div class="ef-item-head">
          <span class="ef-qtype">${esc(p.qtype || "未标注题型")}</span>
          ${p.para ? `<span class="ef-para">第 ${esc(p.para)} 段</span>` : ""}
        </div>
        ${p.anchor_en ? `<div class="ef-anchor">“${esc(p.anchor_en)}”</div>` : ""}
        ${p.why_here ? `<div class="ef-row"><b>为什么设在这里：</b>${esc(p.why_here)}</div>` : ""}
        ${p.stem ? `<div class="ef-stem"><b>真题这样问：</b>${esc(p.stem)}</div>` : ""}
        ${p.right_direction ? `<div class="ef-right"><b>正确项方向：</b>${esc(p.right_direction)}</div>` : ""}
        ${traps(p.traps)}
      </div>`).join("")}
    ${f.trap_summary ? `<div class="ef-summary"><b>本篇最该提防：</b>${esc(f.trap_summary)}</div>` : ""}
  `;
}

/* ---------------- 写作表达 ---------------- */
function renderWriting(items) {
  $("#writeSub").textContent = `${items.length} 条`;
  const words = items.filter(i => i.kind !== "pattern");
  const pats = items.filter(i => i.kind === "pattern");
  const missing = items.filter(i => !i.position_hint || !i.position_usage || !i.genre).length;
  const block = (title, arr, kind) => !arr.length ? "" : `
    <div class="w-group-title">${title}</div>
    ${arr.map(w => `
      <div class="w-item">
        <div class="w-head">
          <div class="w-content">${esc(w.content)}</div>
          ${posChip(w.position_hint)}
          ${genreChip(w.genre)}
        </div>
        <div class="w-cn">${esc(w.cn)}</div>
        ${w.example ? `<div class="w-ex">${esc(w.example)}</div>` : ""}
        ${w.usage_note ? `<div class="w-note">${esc(w.usage_note)}</div>` : ""}
        ${(w.position_usage || POS_USAGE[w.position_hint]) ? `<div class="w-pos-hint">💡 ${esc(w.position_usage || POS_USAGE[w.position_hint])}</div>` : ""}
        ${(w.genre_hint || GENRE_HINT[w.genre]) ? `<div class="w-genre-hint">📄 ${esc(w.genre_hint || GENRE_HINT[w.genre])}</div>` : ""}
        <div class="w-actions">
          <button class="btn tiny" data-fav="${esc(w.content)}" data-cn="${esc(w.cn)}" data-ex="${esc(w.example || "")}" data-kind="${kind}" data-pos="${esc(w.position_hint || "")}">☆ 加入作文素材库</button>
        </div>
      </div>`).join("")}`;
  $("#writeList").innerHTML =
    (missing && state.meta && state.meta.ai_ready
      ? `<div class="label-cta">
           <span class="muted small">还有 ${missing} 条没有标注「该用在作文哪一段 / 什么文体」</span>
           <button class="btn tiny tonal" id="btnLabelPos">让 AI 标注位置与文体</button>
         </div>` : "")
    + block("重点词汇与搭配", words, "phrase") + block("高分句型模板", pats, "pattern");

  const lb = $("#btnLabelPos");
  if (lb) lb.addEventListener("click", async () => {
    lb.disabled = true; lb.textContent = "AI 标注中…";
    try {
      const r = await api(`/api/article/${state.date}/label`, { method: "POST" });
      state.bundle.writing_items = r.items;
      renderWriting(r.items);
      toast("已标注每条表达该用在作文哪一段、属于什么文体");
    } catch (err) { toast(err.message, true); lb.disabled = false; lb.textContent = "让 AI 标注位置与文体"; }
  });
}

$("#writeList").addEventListener("click", async e => {
  const b = e.target.closest("[data-fav]");
  if (!b) return;
  const orig = b.textContent;
  b.disabled = true;
  b.textContent = state.meta && state.meta.ai_ready ? "AI 正在归类…" : "正在入库…";
  try {
    const r = await api("/api/favorite", {
      method: "POST",
      body: JSON.stringify({
        item_type: b.dataset.kind, content: b.dataset.fav, cn: b.dataset.cn,
        example: b.dataset.ex, tags: [b.dataset.kind === "pattern" ? "句型模板" : "写作表达"], date: state.date,
        source_title: state.bundle.article.title, source_topic: state.bundle.article.topic,
      }),
    });
    const libName = r.library === "writing" ? "作文素材库" : "阅读积累库";
    const gName = GENRE_LABEL[r.genre] ? ` · ${GENRE_LABEL[r.genre]}` : "";
    toast(`已归入【${libName} · ${POS_LABEL[r.position] || "通用"}${gName}】${r.usage ? "　" + r.usage : ""}`);
    refreshTodayStats();
  } catch (err) { toast(err.message, true); }
  finally { b.disabled = false; b.textContent = "★ 已加入（再点一次可再收一条）"; }
});

/* ---------------- 仿写 ---------------- */
function renderImitation(b) {
  const task = b.imitation;
  const box = $("#imitateBody");
  if (!task) { box.innerHTML = `<p class="muted small">今日暂无仿写任务</p>`; return; }
  const subs = b.submissions || [];
  box.innerHTML = `
    <div class="im-prompt">${esc(task.prompt_cn)}</div>
    ${task.hint ? `<div class="im-hint"><b>提示：</b>${esc(task.hint)}</div>` : ""}
    <textarea id="imAnswer" placeholder="在这里写下你的英文表达…">${subs.length ? esc(subs[subs.length - 1].answer) : ""}</textarea>
    <div class="im-actions">
      <button class="btn primary" id="btnSubmitIm">${subs.length ? "提交修改后的版本" : "提交，让 AI 批改"}</button>
      <button class="btn ghost small" id="btnShowRef">先看看参考译文</button>
      <span class="muted small" id="imRound">${subs.length ? `已提交 ${subs.length} 次` : "建议先自己写，再看批改"}</span>
    </div>
    <div id="imFeedback"></div>
    <div id="imRef" hidden></div>
  `;
  const last = subs[subs.length - 1];
  if (last) renderFeedback(JSON.parse(last.feedback || "{}"), last.round, false);

  $("#btnShowRef").addEventListener("click", () => {
    const r = $("#imRef");
    r.hidden = !r.hidden;
    r.innerHTML = r.hidden ? "" : `<div class="trans-note" style="margin-top:12px"><b>参考译文：</b>${esc(task.reference_answer || task.prompt_en || "—")}</div>`;
    $("#btnShowRef").textContent = r.hidden ? "先看看参考译文" : "收起参考译文";
  });

  $("#btnSubmitIm").addEventListener("click", submitImitation);
}

async function submitImitation() {
  if (state.submitting) return;
  const answer = $("#imAnswer").value.trim();
  if (!answer) { toast("先写下你的答案吧", true); return; }
  const task = state.bundle.imitation;
  const btn = $("#btnSubmitIm");
  state.submitting = true;
  btn.disabled = true;
  btn.textContent = "AI 正在批改…";
  try {
    const r = await api("/api/imitation/submit", {
      method: "POST",
      body: JSON.stringify({ task_id: task.id, answer }),
    });
    renderFeedback(r.feedback, r.round, true);
    toast("批改完成，可以按建议修改后再提交一次");
    refreshTodayStats();
  } catch (e) {
    toast(e.message, true);
  } finally {
    state.submitting = false;
    btn.disabled = false;
    btn.textContent = "提交修改后的版本";
  }
}

function renderFeedback(fb, round, scroll = true) {
  const box = $("#imFeedback");
  const dims = fb.dimensions || {};
  const score = fb.score ?? "—";
  box.innerHTML = `
    <div class="fb">
      <div class="fb-head">
        <div class="fb-score">${esc(score)}<small>/10</small></div>
        <div>
          <div class="fb-verdict">${esc(fb.verdict || "")}</div>
          <div class="fb-round">第 ${round} 次提交</div>
        </div>
      </div>
      ${(fb.issues || []).length ? fb.issues.map(it => `
        <div class="issue">
          <span class="issue-type">${esc(it.type || "问题")}</span>
          ${it.original ? `<div class="issue-orig">${esc(it.original)}</div>` : ""}
          ${it.suggestion ? `<div class="issue-fix">→ ${esc(it.suggestion)}</div>` : ""}
          ${it.problem ? `<div class="issue-prob">${esc(it.problem)}</div>` : ""}
          ${it.why ? `<div class="issue-why">为什么这样改：${esc(it.why)}</div>` : ""}
        </div>`).join("") : `<p class="muted small">这一版没有发现明显问题。</p>`}
      ${fb.polished ? `<div class="polished"><div class="label">润色后的版本（保留你的思路）</div><div class="txt">${esc(fb.polished)}</div></div>` : ""}
      ${Object.keys(dims).length ? `<div class="dims">${Object.entries(dims).map(([k, v]) => `
        <div class="dim">
          <div class="dim-top"><span>${esc(k)}</span><span>${v}</span></div>
          <div class="bar"><i style="width:${Math.max(0, Math.min(100, Number(v) * 10))}%"></i></div>
        </div>`).join("")}</div>` : ""}
      ${(fb.recommended_expressions || []).length ? `<div class="rec">
        <div class="w-group-title">推荐升级表达${fb.saved_materials ? "（已自动归入素材库）" : ""}</div>
        ${fb.recommended_expressions.map(r => `<div class="rec-item">
          <div class="rec-line"><span class="rec-en">${esc(r.en)}</span>${posChip(r.position)}${genreChip(r.genre)}</div>
          <span class="muted">${esc(r.cn || "")}</span>
          ${r.usage ? `<div class="rec-usage">💡 ${esc(r.usage)}</div>` : ""}
        </div>`).join("")}
      </div>` : ""}
      ${fb.next_step ? `<div class="fb-next">下一步：${esc(fb.next_step)}</div>` : ""}
      ${materialUsageBlock(fb.material_usage)}
    </div>`;
  const roundLabel = $("#imRound");
  if (roundLabel) roundLabel.textContent = `已提交 ${round} 次`;
  if (scroll) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------------- 积累闭环 ①：批改点名（素材用上了吗） ----------------
   后端 /api/imitation/submit 会在 feedback.material_usage 里给三样东西：
     used   —— 这次作文里命中的已积累素材（后端已 used_count+1）
     unused —— 「已巩固但一次没用过」的，挑出来的几条，带 position / usage 提示
     stats  —— 素材使用率（分母 = 已巩固，与复习队列同源）
   ⚠️ 全是本地比对的结果，没多花一次模型调用。整块缺失时静默不渲染。 */
function materialUsageBlock(mu) {
  if (!mu) return "";
  const used = mu.used || [], unused = mu.unused || [], st = mu.stats || {};
  if (!used.length && !unused.length && !st.mastered) return "";
  const rate = st.mastered
    ? `<div class="mu-rate">已巩固 <b>${st.mastered}</b> 条，其中用过的 <b>${st.mastered_used || 0}</b> 条
        （<b>${st.mastered_ratio || 0}%</b>）${st.mastered_unused ? `，还有 <b>${st.mastered_unused}</b> 条一次没用过` : ""}</div>`
    : `<div class="mu-rate">还没有「已巩固」的素材 —— 多复习几轮，这里才会开始统计。</div>`;
  return `
    <div class="mu">
      <div class="w-group-title">你的积累，这次用上了吗</div>
      ${rate}
      ${used.length
        ? `<div class="mu-sub">✅ 这次用上了 ${used.length} 条</div>
           ${used.map(u => `<div class="rec-item">
             <div class="rec-line"><span class="rec-en">${esc(u.content)}</span>${posChip(u.position)}</div>
             <span class="muted">${esc(u.cn || "")} · ${u.used_count > 1 ? `累计用过 ${u.used_count} 次` : "首次使用"}</span>
           </div>`).join("")}`
        : `<div class="mu-sub mu-none">这次没用上积累库里已有的表达 —— 下一版试着塞进去一两条。</div>`}
      ${unused.length
        ? `<div class="mu-sub">💡 这些你已巩固、却一直没用过，可以考虑用上</div>
           ${unused.map(u => `<div class="rec-item">
             <div class="rec-line"><span class="rec-en">${esc(u.content)}</span>${posChip(u.position)}</div>
             <span class="muted">${esc(u.cn || "")}</span>
             ${u.usage ? `<div class="rec-usage">💡 ${esc(u.usage)}</div>` : ""}
           </div>`).join("")}`
        : ""}
    </div>`;
}

/* ---------------- 今日学习情况 ---------------- */
function statCard(v, k) {
  return `<div class="ms"><div class="ms-v">${v}</div><div class="ms-k">${k}</div></div>`;
}
function renderTodayStats(log) {
  if (!log) { $("#todayStats").innerHTML = `<p class="muted small">还没有记录，读完文章、标记词汇后这里会出现数据。</p>`; return; }
  const total = (log.vocab_known || 0) + (log.vocab_focus || 0) + (log.vocab_unknown || 0);
  $("#todayStats").innerHTML = [
    statCard(mmss(log.read_seconds || 0), "阅读用时"),
    statCard(log.analysis_opened ? "✓" : "—", "是否完成精读"),
    statCard(total, "标记词汇数"),
    statCard((log.vocab_unknown || 0) + (log.vocab_focus || 0), "待攻克"),
    statCard(log.imitation_rounds || 0, "仿写提交次数"),
    statCard(log.imitation_score != null ? log.imitation_score : "—", "最近批改得分"),
    statCard(log.favorites_added || 0, "今日收藏"),
  ].join("");
}
async function refreshTodayStats() {
  try {
    const d = await api("/api/bootstrap");
    state.bundle.study_log = d.bundle ? d.bundle.study_log : null;
    state.meta = d.meta;
    renderTodayStats(state.bundle.study_log);
    $("#streakNum").textContent = d.meta.streak || 0;
  } catch { /* 静默 */ }
}

/* ---------------- 展开解析 ---------------- */
$("#btnReveal").addEventListener("click", async () => {
  clearInterval(state.timerId);
  state.revealed = true;
  state.elapsed = Math.floor((Date.now() - state.readStart) / 1000);
  $("#analysis").hidden = false;
  /* 解析区原来是 display:none，画布量不到尺寸；展开这一刻必须重新量一遍 */
  if (window.Pen) requestAnimationFrame(() => window.Pen.sync());
  $("#gate").innerHTML = `<p class="muted small">已在 ${mmss(state.elapsed)} 读完全文　·　下面按「翻译 → 词汇 → 长难句 → 语法 → 逻辑 → 写作」的顺序精读</p>`;
  $("#analysisNote").textContent = `本次阅读用时 ${mmss(state.elapsed)}`;
  try {
    const r = await api("/api/read-log", {
      method: "POST",
      body: JSON.stringify({ date: state.date, seconds: state.elapsed, analysis_opened: true }),
    });
    state.bundle.study_log = r.study_log;
    renderTodayStats(r.study_log);
  } catch { /* 静默 */ }
  $("#analysis").scrollIntoView({ behavior: "smooth", block: "start" });
});

/* ---------------- 生成 / 重新生成 ---------------- */
/* 后台长任务：POST 只负责「开始」，真正跑完的进度靠轮询拿。
   为什么不能直接 await 那个长请求：Cloudflare 对源站响应有 125 秒硬上限，
   而生成一套内容要连续调 3 次模型（1～2 分钟），必然被掐断报 524 ——
   更坑的是服务器其实还在跑并写进了库，用户却什么都看不到，只好再点一次。
   每次轮询都是毫秒级请求，永远碰不到这条线。 */
const JOB_POLL_MS = 2000;

async function runJob(startPath, statusPath, body, onTick) {
  await api(startPath, { method: "POST", body: JSON.stringify(body || {}) });
  const t0 = Date.now();
  for (;;) {
    let st;
    try {
      st = await api(statusPath);
    } catch (e) {
      if (e.offline) throw new Error("网络断了，恢复后刷新页面就能看到结果");
      throw e;
    }
    const sec = Math.floor((Date.now() - t0) / 1000);
    if (onTick) onTick(st, sec);
    if (!st.running) {
      if (st.error) throw new Error(st.error);
      if (st.result && st.result.bundle) return st.result;
      throw new Error("任务结束了但没拿到内容，刷新页面看看");
    }
    await new Promise(r => setTimeout(r, JOB_POLL_MS));
  }
}

let generating = false;
async function generate(force) {
  if (generating) return;
  if (!state.meta || !state.meta.ai_ready) {
    toast("还没有配置 AI 模型，请先在 .env 里填写 API Key 并重启服务", true);
    return;
  }
  generating = true;
  const btns = [$("#btnGenToday"), $("#btnRegen")].filter(Boolean);
  btns.forEach(b => { b.disabled = true; });
  $("#loadingState").hidden = false;
  const tipEl = $("#loadingState").querySelector("p");
  tipEl.textContent = "AI 正在为你准备今天的内容…";
  try {
    const r = await runJob("/api/generate", "/api/generate/status", { force }, (st, sec) => {
      const what = force ? "重新生成" : "生成";
      tipEl.textContent = `AI 正在${what}今天的内容…已等待 ${sec} 秒（通常 1～2 分钟，可以先去别的页面，回来还在跑）`;
      btns.forEach(b => { b.textContent = `生成中…${sec}s`; });
    });
    state.bundle = r.bundle;
    state.meta.ai_ready = true;
    renderMeta({ date: r.article.date, meta: state.meta });
    renderBundle(r.bundle);
    toast("今日内容已生成");
  } catch (e) {
    toast(e.message, true);
  } finally {
    generating = false;
    $("#loadingState").hidden = true;
    btns.forEach(b => { b.disabled = false; });
    $("#btnRegen").textContent = "重新生成今日内容";
    $("#btnGenToday").textContent = "生成今日内容";
  }
}

/* 刷新页面 / 换设备回来时，如果后台还在跑，就接着显示进度 ——
   不然用户会以为白点了。 */
async function resumePendingJob() {
  let st;
  try { st = await api("/api/generate/status"); } catch { return; }
  if (!st.running) return;
  const btns = [$("#btnGenToday"), $("#btnRegen")].filter(Boolean);
  btns.forEach(b => { b.disabled = true; b.textContent = "生成中…"; });
  $("#loadingState").hidden = false;
  const tipEl = $("#loadingState").querySelector("p");
  try {
    const r = await runJob("/api/generate", "/api/generate/status", {}, (s, sec) => {
      tipEl.textContent = `AI 正在生成今天的内容…已等待 ${sec} 秒`;
      btns.forEach(b => { b.textContent = `生成中…${sec}s`; });
    });
    state.bundle = r.bundle;
    renderBundle(r.bundle);
    toast("今日内容已生成");
  } catch (e) {
    toast(e.message, true);
  } finally {
    $("#loadingState").hidden = true;
    btns.forEach(b => { b.disabled = false; });
    if ($("#btnRegen")) $("#btnRegen").textContent = "重新生成今日内容";
    if ($("#btnGenToday")) $("#btnGenToday").textContent = "生成今日内容";
  }
}
$("#btnGenToday").addEventListener("click", () => generate(false));
$("#btnRegen").addEventListener("click", () => {
  if (confirm("重新生成会替换今天现有的内容和解析，确定吗？")) generate(true);
});

/* ---------------- 导入真实材料 ---------------- */
$("#btnImport").addEventListener("click", () => openImport());
/* 服务和"网络"都没变过，浏览器不会自己重试 —— 给一个明确的动作 */
$("#btnRetry").addEventListener("click", () => location.reload());

function openImport() {
  openSheet("粘贴真实外刊材料", `
    <p class="muted small">把一段英文原文粘进来（建议 300 词以上），AI 会按考研英语一标准做完整精读解析，并直接作为今天的内容。</p>
    <input id="impTitle" placeholder="标题（可留空）" style="width:100%;margin-top:12px;padding:10px 12px;border:1px solid var(--line-2);border-radius:10px;background:var(--surface);color:var(--ink);font-family:inherit;font-size:14px">
    <textarea id="impText" placeholder="Paste the English article here…" style="min-height:200px"></textarea>
    <div class="im-actions">
      <button class="btn primary" id="impGo">开始解析并作为今日内容</button>
    </div>`);
  $("#impGo").addEventListener("click", async () => {
    const text = $("#impText").value.trim();
    if (text.length < 200) { toast("正文太短，请粘贴完整文章", true); return; }
    const btn = $("#impGo");
    btn.disabled = true; btn.textContent = "AI 正在解析…";
    try {
      const r = await runJob("/api/import", "/api/import/status",
        { text, title: $("#impTitle").value.trim(), date: state.date },
        (st, sec) => { btn.textContent = `AI 正在解析…${sec}s`; });
      state.bundle = r.bundle;
      renderBundle(r.bundle);
      closeSheet();
      toast("解析完成");
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = "开始解析并作为今日内容"; }
  });
}

/* ---------------- 历史 / 真题精读 ---------------- */
let histTab = "daily";

$("#btnHistory").addEventListener("click", () => { histTab = "daily"; navigate("history"); });
$("#btnExamToday").addEventListener("click", () => openExamImport());

/* 整页：历史与真题（内容写进 #pageBody-history） */
async function loadHistory(tab) {
  if (tab) histTab = tab;
  const box = $("#pageBody-history");
  if (!box.innerHTML.trim()) box.innerHTML = `<p class="muted small">加载中…</p>`;
  await renderHistory();
}

function histTabs() {
  return `<div class="hist-tabs">
    <button class="hist-tab ${histTab === "daily" ? "on" : ""}" data-htab="daily">🗓 每日内容</button>
    <button class="hist-tab ${histTab === "exam" ? "on" : ""}" data-htab="exam">🎯 真题精读</button>
  </div>`;
}

async function renderHistory() {
  const { box, isCurrent } = pageLoadContext("history");
  const tabs = histTabs();
  const tab = histTab;
  try {
    if (tab === "daily") {
      const d = await api("/api/bootstrap");
      if (!isCurrent()) return;
      const items = d.history || [];
      box.innerHTML = tabs + (items.length
        ? items.map(h => `
          <div class="hist-item" data-hist-date="${h.date}" title="打开这一天">
            <div class="hist-date">${h.date.slice(5)}</div>
            <div class="hist-title">${esc(h.title)}</div>
            <div class="muted small">${esc(h.topic || "")} · ${h.word_count} 词</div>
          </div>`).join("")
        : `<p class="muted small" style="margin-top:14px">还没有历史内容。</p>`);
    } else {
      await ensureExamMeta();
      if (!isCurrent()) return;
      const m2 = EXAM_META || {};
      const tgt = m2.target_year || 2027;
      const rf = m2.redline_from || 2024;
      const lt = m2.latest_year || 2026;
      const r = await api("/api/exam/list");
      if (!isCurrent()) return;
      const items = r.items || [];
      box.innerHTML = tabs + `
        <div class="exam-head">
          <p class="muted small" style="line-height:1.75;margin:0 0 10px">
            把<b>做完的</b>真题原文粘进来，AI 按真题模式做精读解析：优先挑真题爱设题的词句、给阅读策略，
            好表达可以直接收进素材库，和每日外刊的素材汇进同一个库。
          </p>
          <button class="btn tonal small" id="btnExamNew">＋ 导入一篇真题</button>
        </div>` + (items.length
          ? items.map(ex => `
            <div class="exam-item" data-exam="${ex.id}">
              <div class="exam-src">🎯 ${esc(ex.source)}</div>
              <div class="exam-title">${esc(ex.title)}</div>
              <div class="muted small">${ex.word_count} 词 · ${ex.vocab_count} 词汇 · ${ex.sent_count} 长难句</div>
            </div>`).join("")
          : `<p class="muted small" style="margin-top:12px;line-height:1.75">
               还没有导入真题。<br>
               建议顺序：现在到 ${tgt} 年 6 月用 ${Math.max(1998, rf - 12)} 年以前的题做精读；
               最近 3 年（${rf}–${lt}）留到最后做全真模考。
             </p>`);
    }
  } catch (e) {
    if (isCurrent()) box.innerHTML = tabs + `<p class="muted small">${esc(e.message)}</p>`;
  }
}

/* 真题年份相关全部来自后端配置（app/config.py 的 EXAM_TARGET_YEAR），这里只做缓存 */
let EXAM_META = null;

async function ensureExamMeta() {
  if (EXAM_META) return EXAM_META;
  try {
    const r = await api("/api/exam/list");
    EXAM_META = r.meta || null;
  } catch (e) { /* 拿不到就用兜底值 */ }
  return EXAM_META;
}

/* 兜底：万一 meta 没取到，也不至于显示错的年份 */
function examYears() {
  const m = EXAM_META;
  if (m && m.years && m.years.length) return m.years;
  const latest = (m && m.latest_year) || 2026;
  const out = [];
  for (let y = latest; y >= 1998; y--) out.push(y);
  return out;
}

async function openExamImport() {
  await ensureExamMeta();
  const years = examYears();
  const meta = EXAM_META || {};
  const targetYear = meta.target_year || 2027;
  const redFrom = meta.redline_from || 2024;
  const latest = meta.latest_year || 2026;
  const defaultYear = years.includes(2010) ? 2010 : years[years.length - 1];
  openSheet("导入真题精读", `
    <p class="muted small" style="line-height:1.8">
      把<b>做完的</b>真题原文整篇粘进来（300 词以上）。AI 会按<b>真题模式</b>解析：
      挑真题最爱设题的词句、给针对性阅读策略，好表达一键收进素材库。
    </p>
    <div class="field-row">
      <label class="field"><span>年份</span>
        <select id="expYear">${years.map(y => `<option value="${y}"${y === defaultYear ? " selected" : ""}>${y}</option>`).join("")}</select>
      </label>
      <label class="field"><span>篇目</span>
        <select id="expSlot">
          <option value="text1">阅读 Text 1</option>
          <option value="text2" selected>阅读 Text 2</option>
          <option value="text3">阅读 Text 3</option>
          <option value="text4">阅读 Text 4</option>
          <option value="cloze">完形填空</option>
          <option value="new">新题型</option>
          <option value="trans">翻译</option>
        </select>
      </label>
    </div>
    <input id="expTitle" class="field-input" placeholder="标题（可留空，会自动生成）">
    <textarea id="expText" placeholder="Paste the exam passage here…"></textarea>
    <div class="im-actions">
      <button class="btn primary" id="expGo">开始解析</button>
      <span class="muted small">解析约需 1～2 分钟</span>
    </div>
    <div class="exam-redline">⚠️ 最近 3 年的题（${redFrom}–${latest}）建议留到最后做全真模考 —— 模考的价值来自「第一次见」。
    现在精读优先用 ${Math.max(1998, redFrom - 12)} 年以前的，题目更早、语料不过时，正好拿来练精读。</div>`);
  $("#expGo").addEventListener("click", submitExamImport);
}

async function submitExamImport() {
  const text = $("#expText").value.trim();
  const year = Number($("#expYear").value);
  const slot = $("#expSlot").value;
  if (text.length < 200) { toast("正文太短，请粘贴完整的一篇真题文章", true); return; }
  const redFrom = (EXAM_META && EXAM_META.redline_from) || 2024;
  if (year >= redFrom &&
      !confirm(`${year} 年的题属于「最近 3 年」，建议留到最后做全真模考。\n确定现在就精读它吗？`)) return;
  const btn = $("#expGo");
  btn.disabled = true; btn.textContent = "AI 正在按真题模式解析…";
  try {
    const r = await runJob("/api/exam/import", "/api/exam/import/status",
      { text, year, slot, title: $("#expTitle").value.trim() },
      (st, sec) => { btn.textContent = `AI 正在按真题模式解析…${sec}s`; });
    // 必须用 backToToday 而不是 closeSheet：
    // 这个表单是从「历史整页」里打开的，只关弹层的话内容会被渲染进隐藏的今日视图，
    // 用户只看到一个 toast，界面毫无反应。
    backToToday();
    showBundle(r.bundle);
    toast("真题解析完成，已切到精读界面");
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = "开始解析"; }
}

document.addEventListener("click", e => {
  const set = e.target.closest("[data-read-set]");
  if (set) {
    Reading.set(set.dataset.readSet, set.dataset.readVal);
    const box = $("#readPanel");
    if (box) box.innerHTML = Reading.html();     // 重画一次，让选中态跟上
    return;
  }
  if (e.target.closest("[data-read-reset]")) {
    Reading.reset();
    const box = $("#readPanel");
    if (box) box.innerHTML = Reading.html();
    toast("已恢复默认阅读外观");
    return;
  }
});

/* 历史与真题两个标签的点击都走这里。
   委托在 document 上，这样整页和弹层两个容器都能响应。 */
document.addEventListener("click", e => {
  const t = e.target.closest("[data-htab]");
  if (t) { histTab = t.dataset.htab; renderHistory(); return; }
  if (e.target.closest("#btnExamNew")) { openExamImport(); return; }
  const h = e.target.closest("[data-hist-date]");
  if (h) { loadDate(h.dataset.histDate); return; }
  const ex = e.target.closest("[data-exam]");
  if (ex) loadExam(ex.dataset.exam);
});

/* 从整页里点开某一天 / 某篇真题：先切回今日内容，再把内容塞进去 */
async function loadExam(id) {
  backToToday();
  $("#loadingState").hidden = false;
  try {
    const bundle = await api("/api/exam/" + id);
    showBundle(bundle);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) { toast(e.message, true); }
  finally { $("#loadingState").hidden = true; }
}

async function loadDate(d) {
  backToToday();
  $("#loadingState").hidden = false;
  try {
    const data = await api("/api/bootstrap?date_str=" + d);
    state.date = data.date;
    state.meta = data.meta;
    renderMeta(data);
    if (data.bundle) { state.bundle = data.bundle; state.revealed = false; renderBundle(data.bundle); }
    else showEmpty(data.meta);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) { toast(e.message, true); }
  finally { $("#loadingState").hidden = true; }
}

/* 从真题回到今日内容：直接重载最省事，也顺带把计时器等状态复位 */
$("#btnBackToday").addEventListener("click", () => location.reload());

/* ---------------- 数据 / 周报 ---------------- */
$("#btnStats").addEventListener("click", () => navigate("stats"));

/* 整页：学习数据（内容写进 #pageBody-stats） */
async function loadStats() {
  const { box, isCurrent } = pageLoadContext("stats");
  if (!box.innerHTML.trim()) box.innerHTML = `<p class="muted small">加载中…</p>`;
  try {
    const s = await api("/api/stats?days=7");
    if (!isCurrent()) return;
    const weak = s.weak_words || [];
    box.innerHTML = `
      <div class="w-group-title">最近 ${s.days} 天</div>
      <div class="stat-grid">
        ${statCard(s.articles, "完成文章")}
        ${statCard(s.words_read, "阅读词数")}
        ${statCard(s.read_minutes, "阅读分钟")}
        ${statCard(s.active_days, "学习天数")}
        ${statCard(s.streak, "连续天数")}
      </div>
      <div class="w-group-title">积累与输出</div>
      <div class="stat-grid">
        ${statCard(s.new_vocab, "接触词汇")}
        ${statCard(s.known_vocab, "已掌握")}
        ${statCard(s.unknown_vocab, "待攻克")}
        ${statCard(s.sentences, "长难句")}
        ${statCard(s.writing_attempts, "仿写提交")}
        ${statCard(s.writing_avg_score || "—", "批改均分")}
        ${statCard(s.favorites_total, "收藏总量")}
      </div>
      ${(s.material_usage && s.material_usage.total) ? `<div class="w-group-title">素材使用率（全量口径）</div>
        <div class="stat-grid">
          ${statCard(s.material_usage.mastered, "已巩固素材")}
          ${statCard(s.material_usage.mastered_used, "用过的")}
          ${statCard(s.material_usage.mastered_unused, "一次没用过")}
          ${statCard((s.material_usage.mastered_ratio || 0) + "%", "使用率")}
        </div>
        <div class="dims">
          <div class="dim"><div class="dim-top"><span>已巩固里用过多少</span><span>${s.material_usage.mastered_used} / ${s.material_usage.mastered}</span></div>
            <div class="bar"><i style="width:${s.material_usage.mastered_ratio || 0}%"></i></div></div>
        </div>
        <p class="muted small">「用上了」= 批改你的仿写时，在作文正文里比对到了这条素材。攒了不用等于没攒 —— 批改页会点名提醒。</p>` : ""}
      ${(s.material_unused || []).length ? `<div class="w-group-title">已巩固、却一次没用过</div>
        <div>${s.material_unused.map(u => `<span class="weak-word"><b>${esc(u.content)}</b><span>${esc(u.cn || posChip(u.position))}</span></span>`).join("")}</div>` : ""}
      ${(s.grammar_top || []).length ? `<div class="w-group-title">高频语法（近 ${s.days} 天）</div>
        <p class="muted small">${s.grammar_top.map(g => `${esc(g.name)} ×${g.c}`).join("　·　")}</p>` : ""}
      ${weak.length ? `<div class="w-group-title">反复不认识的词（下一步会优先复现）</div>
        <div>${weak.map(w => `<span class="weak-word"><b>${esc(w.word)}</b><span>出现 ${w.occ} 次</span></span>`).join("")}</div>` : ""}
      <div class="w-group-title">词汇掌握分布</div>
      <div class="dims">
        <div class="dim"><div class="dim-top"><span>已掌握</span><span>${s.known_vocab}</span></div><div class="bar"><i style="width:${s.new_vocab ? (s.known_vocab / s.new_vocab * 100) : 0}%"></i></div></div>
        <div class="dim"><div class="dim-top"><span>待攻克</span><span>${s.unknown_vocab}</span></div><div class="bar"><i style="width:${s.new_vocab ? (s.unknown_vocab / s.new_vocab * 100) : 0}%"></i></div></div>
      </div>`;
  } catch (e) { if (isCurrent()) box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; }
}

/* ---------------- 今日复习（间隔重复） ---------------- */
const REV = { items: [], stats: {}, masteredAt: 7, rest: false };

$("#btnReview").addEventListener("click", () => navigate("review"));

/* 整页：今日复习（内容写进 #pageBody-review） */
async function loadReview() {
  const { box, isCurrent } = pageLoadContext("review");
  if (!box.innerHTML.trim()) box.innerHTML = `<p class="muted small">加载中…</p>`;
  try {
    const r = await api("/api/review/due");
    if (!isCurrent()) return;
    REV.items = r.items || [];
    REV.stats = r.stats || {};
    /* 周末休息：周六/周日后端给空队列 + rest 标记（2026-10-19 用户口径） */
    REV.rest = !!(r.rest || (r.stats && r.stats.rest));
    if (r.mastered_at) REV.masteredAt = r.mastered_at;
    renderReview();
    setReviewBadge(REV.stats.due);
  } catch (e) {
    if (isCurrent()) box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
  }
}

const REV_INTERVAL_TXT = "1 → 2 → 4 → 7 → 15 → 30 → 60 天";

function renderReview() {
  if (currentView !== "review") return;
  const box = $("#pageBody-review");
  const st = REV.stats || {};
  const head = `
    <div class="lib-summary">
      <div class="lib-sum-item"><b data-rev-stat="due">${st.due || 0}</b><span>今日待复习</span></div>
      <div class="lib-sum-item"><b data-rev-stat="in_cycle">${st.in_cycle || 0}</b><span>在复习周期</span></div>
      <div class="lib-sum-item"><b data-rev-stat="mastered">${st.mastered || 0}</b><span>已巩固</span></div>
      <div class="lib-sum-item"><b>${st.total || 0}</b><span>素材总量</span></div>
    </div>
    <p class="muted small" style="line-height:1.8">
      先只看英文，回忆它的意思和用法，再点「看答案」自测。<br>
      记住了间隔会拉长（${REV_INTERVAL_TXT}），忘记了退回起点、明天再见。<br>
      <b>周末休息</b>：复习只排在工作日，落在周六/周日的顺延到下周一，周末队列为空。<br>
      这里只有<b>词汇</b>和<b>表达</b>；长难句和语法留在素材库里随时翻，不每天提醒。
    </p>`;
  if (!REV.items.length) {
    box.innerHTML = head + (REV.rest
      ? `<p class="center" style="margin:26px 0;font-size:14px">🌴 周末休息，今天不用复习
           <br><span class="muted small">周末到期的都攒到周一一起过</span></p>`
      : `<p class="center" style="margin:26px 0;font-size:14px">今天没有要复习的素材 🎉</p>`);
    return;
  }
  /* 用户 2026-10-14：复习只针对「词汇」和「表达」。
     后端已经过滤掉长难句和语法（db._NOT_IN_REVIEW），这里再把剩下的分成两栏，
     一眼能看出今天要复习的是单词还是短语/句型/笔记。 */
  const isVocab = it => it.src !== "note" && (it.item_type || "") === "vocab";
  let seq = 0;
  const section = (title, hint, list) => (!list.length ? "" : `
    <div class="rev-group">
      <div class="rev-group-head">
        <span class="rev-group-title">${title}</span>
        <span class="muted small">${list.length} 条${hint}</span>
      </div>
      ${list.map(it => revCard(it, seq++)).join("")}
    </div>`);
  box.innerHTML = head
    + section("🔤 词汇", " · 来自文章里的今日词汇与收藏", REV.items.filter(isVocab))
    + section("✍️ 表达", " · 短语 / 句型 / 阅读笔记", REV.items.filter(it => !isVocab(it)));
}

function revCard(it, i) {
  const rc = it.review_count || 0;
  const lv = it.level || "B";
  const isNote = it.src === "note";
  const isVocab = !isNote && (it.item_type || "") === "vocab";
  // 单词不写作文，位置/文体/等级这些标签对它没意义，换成「词汇」标签
  const plain = isNote || isVocab;
  return `
    <div class="rev-card" data-id="${it.id}" data-src="${isNote ? "note" : "fav"}">
      <div class="rev-top">
        <span class="rev-idx">${i + 1}</span>
        ${isNote ? `<span class="chip accent">阅读笔记</span>`
          : isVocab ? `<span class="chip accent">词汇</span>`
            : posChip(it.position || "universal")}
        ${plain ? "" : genreChip(it.genre)}
        ${plain ? "" : `<span class="mat-lv lv-${lv}">${lv}</span>`}
        <span class="rev-due muted">${rc ? `已复习 ${rc} 次` : "第一次复习"}</span>
      </div>
      <div class="rev-en">${esc(it.content)}</div>
      <div class="rev-answer" hidden>
        ${it.cn ? `<div class="rev-cn">${esc(it.cn)}</div>` : ""}
        ${it.usage ? `<div class="rev-usage">💡 ${esc(it.usage)}</div>` : ""}
        ${it.example ? `<div class="rev-ex">${esc(it.example)}</div>` : ""}
      </div>
      <div class="rev-actions"><button class="btn small" data-rev="show">看答案</button></div>
    </div>`;
}

document.addEventListener("click", async e => {
  const card = e.target.closest(".rev-card");
  if (!card) return;
  const btn = e.target.closest("[data-rev]");
  if (!btn) return;
  const id = card.dataset.id;
  const act = btn.dataset.rev;
  const src = card.dataset.src || "fav";

  if (act === "show") {
    card.querySelector(".rev-answer").hidden = false;
    card.querySelector(".rev-actions").innerHTML = `
      <button class="btn small warnish" data-rev="forgot">忘记了</button>
      <button class="btn small primary" data-rev="known">记住了</button>
      <button class="btn tiny" data-rev="skip">稍后</button>
      <button class="btn tiny" data-rev="drop">不再复习</button>`;
    return;
  }

  btn.disabled = true;
  try {
    const r = await api(`/api/review/${id}/result`, {
      method: "POST",
      body: JSON.stringify({ result: act, src }),
    });
    REV.stats = r.stats || {};
    markReviewed(card, act, r.item);
    setReviewBadge(REV.stats.due);
    syncRevStats();
  } catch (err) { toast(err.message, true); btn.disabled = false; }
});

function markReviewed(card, act, item) {
  card.classList.add("rev-done");
  const label = { known: "✓ 记住了", forgot: "↻ 忘记了", skip: "→ 稍后再说", drop: "⊘ 已移出复习" }[act] || "完成";
  const rc = item ? (item.review_count || 0) : 0;
  const masterAt = REV.masteredAt || 7;
  const nextTxt = item && item.next_review ? `下次 ${item.next_review}` : "";
  card.querySelector(".rev-actions").innerHTML =
    `<span class="rev-result r-${act}">${label}</span><span class="muted small">${nextTxt}</span>`;
  card.querySelector(".rev-due").innerHTML =
    !item ? "" : rc >= masterAt ? `已复习 ${rc} 次 <b class="rev-mastered">已巩固</b>`
      : rc ? `已复习 ${rc} 次` : "";
}

function syncRevStats() {
  const map = { due: REV.stats.due || 0, in_cycle: REV.stats.in_cycle || 0, mastered: REV.stats.mastered || 0 };
  Object.entries(map).forEach(([k, v]) => {
    const el = $(`[data-rev-stat="${k}"]`);
    if (el) el.textContent = v;
  });
}

/* ---------------- 阅读笔记 + 语法积累库 ----------------
   用户 2026-10-11：「填充的单词或短语自动识别保留进阅读的笔记中，
   笔记单独是一个内容，用于存放我的阅读笔记」+「还应有单独的语法积累库」。
   两件事放在同一页的两个标签下：都是「攒起来 + 每天复现」的东西。 */
let NOTES_TAB = "note";

async function loadNotes(tab, opts) {
  if (tab) NOTES_TAB = tab;
  const force = !!(opts && opts.force);
  const { box, isCurrent } = pageLoadContext("notes");
  if (!box.innerHTML.trim()) box.innerHTML = `<p class="muted small">加载中…</p>`;
  /* ⚠️ 切换标签**不能**再跑一遍网络请求。
     用户 2026-10-11 反馈「切换这两个响应非常慢」：原因是每次点标签都重新拉
     /api/notes + /api/grammar/library，而手机要从 Cloudflare 境外节点绕一圈回来，
     一次就是几百毫秒到一秒多 —— 明明数据已经在本地的 NOTES_CACHE 里了。
     所以：有缓存就先**立刻画出来**（切换是瞬时的），再在后台静默刷新一次。 */
  const hasCache = (NOTES_CACHE.notes.length || NOTES_CACHE.grammars.length || NOTES_CACHE.loaded);
  if (hasCache && !force) {
    /* 进页面且已有缓存：先立刻画出来，再后台对一次服务端（保证别的地方改过也同步） */
    renderNotesPage();
    refreshNotesQuietly();
    return;
  }
  try {
    const [n, g] = await Promise.all([
      api("/api/notes?limit=300"),
      api("/api/grammar/library?limit=300"),
    ]);
    if (!isCurrent()) return;
    NOTES_CACHE = { notes: n.items || [], grammars: g.items || [], stats: n.stats || {}, loaded: true };
    renderNotesPage();
  } catch (e) {
    if (isCurrent()) box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
  }
}

/* 后台静默刷新：拿到了就更新界面，没拿到就保持现在这样（不闪 loading、不报错打断） */
let notesRefreshSeq = 0;
async function refreshNotesQuietly() {
  const seq = ++notesRefreshSeq;
  try {
    const [n, g] = await Promise.all([
      api("/api/notes?limit=300"),
      api("/api/grammar/library?limit=300"),
    ]);
    if (seq !== notesRefreshSeq || currentView !== "notes") return;
    NOTES_CACHE = { notes: n.items || [], grammars: g.items || [], stats: n.stats || {}, loaded: true };
    renderNotesPage();
  } catch (e) { /* 静默：切标签失败不该弹错，用户下次进页面会再刷 */ }
}

let NOTES_CACHE = { notes: [], grammars: [], stats: {}, loaded: false };

const NOTE_KIND_LABEL = { vocab: "单词", phrase: "短语", sentence: "句子", other: "其他" };

function renderNotesPage() {
  if (currentView !== "notes") return;
  const box = $("#pageBody-notes");
  const st = NOTES_CACHE.stats || {};
  const tab = NOTES_TAB;
  const head = `
    <div class="lib-summary">
      <div class="lib-sum-item"><b>${st.total || 0}</b><span>笔记总量</span></div>
      <div class="lib-sum-item"><b>${st.due || 0}</b><span>今日待复习</span></div>
      <div class="lib-sum-item"><b>${st.in_cycle || 0}</b><span>在复习周期</span></div>
      <div class="lib-sum-item"><b>${(NOTES_CACHE.grammars || []).length}</b><span>语法积累</span></div>
    </div>
    <div class="hist-tabs">
      <button class="hist-tab ${tab === "note" ? "on" : ""}" data-ntab="note">📝 阅读笔记（${(NOTES_CACHE.notes || []).length}）</button>
      <button class="hist-tab ${tab === "grammar" ? "on" : ""}" data-ntab="grammar">🧠 语法积累库（${(NOTES_CACHE.grammars || []).length}）</button>
    </div>
    <p class="muted small" style="line-height:1.8">
      在文章里<b>划选</b>词句就会自动记到这里，第二天起进入「今日复习」，
      按 1 → 2 → 4 → 7 → 15 → 30 → 60 天不断复现。
      <br><b>误填充了怎么办</b>：回到文章里点一下那段颜色，选「取消填充」即可；
      刚填充完时工具条上也有「取消刚才的填充」。
    </p>`;
  const body = tab === "grammar" ? notesGrammarHtml() : notesListHtml();
  box.innerHTML = head + body;
}

function notesListHtml() {
  const items = NOTES_CACHE.notes || [];
  if (!items.length) {
    return `<p class="center muted" style="margin:26px 0">还没有阅读笔记。<br>
      回到文章里选中一个词或一句话，点「填充并记入笔记」就会出现在这里。<br>
      <span class="small">（误填充了？在文章里点一下那段高亮，就能取消。）</span></p>`;
  }
  return `<div class="note-list">` + items.map(n => `
    <div class="note-card hl-${esc(n.color || "yellow")}" data-note-id="${n.id}">
      <div class="note-top">
        <span class="note-kind">${esc(NOTE_KIND_LABEL[n.kind] || "笔记")}</span>
        <span class="muted small">${esc(n.doc_date || "")}</span>
        <span class="muted small">${n.review_count ? `已复习 ${n.review_count} 次` : "未复习"}</span>
      </div>
      <div class="note-content">${esc(n.content)}</div>
      ${n.cn ? `<div class="note-cn">${esc(n.cn)}</div>` : ""}
      ${n.context ? `<div class="note-context">${esc(n.context)}</div>` : ""}
      <input class="field-input note-input" data-note-input="${n.id}"
        placeholder="写点自己的批注…" value="${esc(n.note || "")}">
      <div class="note-actions">
        ${n.cn ? "" : `<button class="btn tiny" data-note-cn="${n.id}">查意思</button>`}
        <button class="btn tiny" data-note-save="${n.id}">保存批注</button>
        <button class="btn tiny warnish" data-note-del="${n.id}">删除</button>
      </div>
    </div>`).join("") + `</div>`;
}

function notesGrammarHtml() {
  const items = NOTES_CACHE.grammars || [];
  if (!items.length) {
    return `<p class="center muted" style="margin:26px 0">语法库还是空的。<br>
      在「今日语法」里点「＋ 收进语法积累库」，就会攒到这里。</p>`;
  }
  return `<div class="note-list">` + items.map(g => `
    <div class="note-card gram">
      <div class="note-top">
        <span class="note-kind">${esc((g.tags || [])[1] || "句法")}</span>
        <span class="muted small">${esc((g.tags || [])[2] || "")}</span>
        <span class="muted small">${g.review_count ? `已复习 ${g.review_count} 次` : "未复习"}</span>
      </div>
      <div class="note-content">${esc(g.content)}</div>
      ${g.cn ? `<div class="note-cn">${esc(g.cn)}</div>` : ""}
      ${g.example ? `<div class="note-context">${esc(g.example)}</div>` : ""}
    </div>`).join("") + `</div>`;
}

/* 笔记页里的按钮 */
document.addEventListener("click", async e => {
  const t = e.target.closest("[data-ntab]");
  if (t) {
    /* 切换标签走**纯本地重绘**（点下去 0 毫秒就换完），不打任何接口。
       用户 2026-10-11 反馈「切换响应非常慢」：以前这里直接调 loadNotes()，
       每次都拉两个接口，手机绕 Cloudflare 一圈就好几百毫秒。
       数据在 NOTES_CACHE 里已经有了，进页面时刷过一次就够；
       要再对一次服务端，由「进页面 / 增删之后」负责，不放在每次点标签上。 */
    NOTES_TAB = t.dataset.ntab;
    renderNotesPage();
    return;
  }

  const cn = e.target.closest("[data-note-cn]");
  if (cn) {
    const id = cn.dataset.noteCn;
    cn.disabled = true; cn.textContent = "查询中…";
    try {
      const r = await api(`/api/notes/${id}/enrich`, { method: "POST", body: "{}" });
      const it = NOTES_CACHE.notes.find(x => String(x.id) === String(id));
      if (it) it.cn = (r.note && r.note.cn) || "";
      renderNotesPage();
      toast("已补上中文意思");
    } catch (err) { toast(err.message, true); cn.disabled = false; cn.textContent = "查意思"; }
    return;
  }

  const sv = e.target.closest("[data-note-save]");
  if (sv) {
    const id = sv.dataset.noteSave;
    const input = document.querySelector(`[data-note-input="${id}"]`);
    sv.disabled = true;
    try {
      await api(`/api/notes/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ note: input ? input.value : "" }),
      });
      toast("批注已保存");
    } catch (err) { toast(err.message, true); }
    finally { sv.disabled = false; }
    return;
  }

  const del = e.target.closest("[data-note-del]");
  if (del) {
    const id = del.dataset.noteDel;
    if (!confirm("删掉这条阅读笔记？")) return;
    try {
      await api(`/api/notes/${id}`, { method: "DELETE" });
      NOTES_CACHE.notes = NOTES_CACHE.notes.filter(x => String(x.id) !== String(id));
      renderNotesPage();
      toast("已删除");
    } catch (err) { toast(err.message, true); }
  }
});

/* ---------------- 素材库入口（放在设置弹层里） ---------------- */
const POS_LABEL = { opening: "开头段", transition: "过渡段", body: "主体段", ending: "结尾段", universal: "通用" };
const POS_ORDER = ["opening", "transition", "body", "ending", "universal"];
const POS_USAGE = {
  opening: "用在开头段：引出话题 / 描述现象 / 点出争议",
  transition: "用在过渡段：承上启下，衔接前后两段观点",
  body: "用在主体段：举例、对比、因果或让步论证",
  ending: "用在结尾段：总结观点、提出建议或升华主题",
  universal: "通用：开头、主体、结尾都能用",
};
const LEVEL_LABEL = { A: "亮点表达", B: "常用表达", C: "理解型" };
const CAT_ORDER = ["教育学习", "科技发展", "社会现象", "环境保护", "文化交流", "人生哲理", "经济就业", "品质精神", "通用表达"];

/* ---- 文体（genre）：这条素材适合写进哪一类文章 ---- */
const GENRE_LABEL = {
  argumentative: "议论文", expository: "说明文", practical: "应用文",
  descriptive: "描写文", narrative: "记叙文", universal: "通用文体",
};
const GENRE_ORDER = ["argumentative", "expository", "practical", "descriptive", "narrative", "universal"];
const GENRE_HINT = {
  argumentative: "议论文（大作文主体）：立论、分析原因、论证观点、提出建议",
  expository: "说明文：客观陈述现象、数据、趋势，如「According to the chart…」",
  practical: "应用文（小作文）：书信、邮件、通知、告示、备忘录、纪要专用措辞",
  descriptive: "描写文：图画、场景、人物描写，常用于大作文首段「As is vividly depicted…」",
  narrative: "记叙文：讲述经历或案例，可放进举例论证里",
  universal: "通用文体：议论文、说明文、应用文都能用",
};
const GENRE_MARK = {
  argumentative: "⚖️", expository: "📊", practical: "✉️",
  descriptive: "🖼️", narrative: "📖", universal: "🔗",
};

function posChip(p) {
  return p && POS_LABEL[p] ? `<span class="pos-chip pos-${p}">${POS_LABEL[p]}</span>` : "";
}

function genreChip(g, withMark = true) {
  if (!g || !GENRE_LABEL[g]) return "";
  const m = withMark ? `${GENRE_MARK[g] || ""} ` : "";
  return `<span class="genre-chip g-${g}" title="${esc(GENRE_HINT[g] || "")}">${m}${GENRE_LABEL[g]}</span>`;
}

const libState = { type: "", library: "", position: "", genre: "", q: "", groupBy: "position", raw: null };

/* 素材的「类型」= 用户 2026-10-14 说的「语法在语法一栏、词汇在词汇一栏、长难句在长难句一栏」。
   数据里是 item_type（vocab | phrase | pattern | sentence | expression | grammar），
   归类规则只写这一处，筛选和计数都复用它。 */
const LIB_TYPES = [
  ["vocab", "🔤 词汇"],
  ["phrase", "🧩 短语"],
  ["pattern", "🏗 句型"],
  ["sentence", "📐 长难句"],
  ["grammar", "🧠 语法"],
];
/* 只有词汇和表达进每日复习（见后端 db._NOT_IN_REVIEW），这里标记出来，
   免得用户在长难句/语法栏里以为它们也会每天提醒。 */
const LIB_TYPE_NOTE = {
  sentence: "长难句属于「闲时精读」，不进每日复习，随时在这里翻。",
  grammar: "语法积累只供查阅，不进每日复习；点文章里的「＋ 收进语法积累库」就会攒到这里。",
};
function libTypeOf(it) {
  const t = (it.item_type || "").trim();
  if (t === "grammar" || (it.library || "") === "grammar") return "grammar";
  if (t === "sentence") return "sentence";
  if (t === "pattern") return "pattern";
  if (t === "vocab") return "vocab";
  return "phrase";   // phrase / expression / 老数据没有 item_type 的，都算短语表达
}

/* 整页：素材库（内容写进 #pageBody-library） */
async function loadLibrary() {
  const { box, isCurrent } = pageLoadContext("library");
  if (!box.innerHTML.trim()) box.innerHTML = `<p class="muted small">加载中…</p>`;
  try {
    const raw = await api("/api/favorites");
    if (!isCurrent()) return;
    libState.raw = raw;
    renderLibrary();
  } catch (e) {
    if (isCurrent()) box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
  }
}

async function reloadLibrary() {
  const { isCurrent } = pageLoadContext("library");
  try {
    const raw = await api("/api/favorites");
    if (!isCurrent()) return;
    libState.raw = raw;
    renderLibrary();
  } catch (e) { if (isCurrent()) toast(e.message, true); }
}

function renderLibrary() {
  if (currentView !== "library") return;
  const data = libState.raw || { items: [], summary: {}, meta: {} };
  const all = data.items || [];
  const s = data.summary || {};

  // 1) 先按「类型」筛选（词汇 / 短语 / 句型 / 长难句 / 语法）
  const byType = libState.type ? all.filter(i => libTypeOf(i) === libState.type) : all;
  // 2) 再按「库」筛选
  const byLib = libState.library ? byType.filter(i => (i.library || "reading") === libState.library) : byType;
  // 3) 再按「位置」筛选
  const byPos = libState.position ? byLib.filter(i => (i.position || "universal") === libState.position) : byLib;
  // 4) 再按「文体」筛选
  const byGenre = libState.genre ? byPos.filter(i => (i.genre || "universal") === libState.genre) : byPos;
  // 5) 最后按关键词搜索（多词 = AND，跨英文/中文/例句/用法/主题/标签）
  const terms = queryTerms();
  const shown = byGenre.filter(i => hitSearch(i, terms));

  const isGrammarTab = libState.type === "grammar";
  const libCount = k => byType.filter(i => (i.library || "reading") === k).length;
  const posCount = p => byLib.filter(i => (i.position || "universal") === p).length;
  const genreCount = g => byPos.filter(i => (i.genre || "universal") === g).length;
  const typeCount = k => all.filter(i => libTypeOf(i) === k).length;

  const typeTabs = [["", "全部", all.length]]
    .concat(LIB_TYPES.map(([k, label]) => [k, label, typeCount(k)]))
    .map(([k, label, n]) => `<button class="lib-tab lib-type ${libState.type === k ? "on" : ""}" data-type="${k}">${label}<i>${n}</i></button>`).join("");

  const tabs = [["", "全部", byType.length],
    ["writing", "📝 作文素材库", libCount("writing")],
    ["reading", "📖 阅读积累库", libCount("reading")]]
    .map(([k, label, n]) => `<button class="lib-tab ${libState.library === k ? "on" : ""}" data-lib="${k}">${label}<i>${n}</i></button>`).join("");

  // 语法栏里「位置/文体」没有意义（它不写作文），直接按语法分类（句法/词法）看
  const grp = isGrammarTab ? "category" : libState.groupBy;

  const posChips = isGrammarTab ? "" : [`<button class="pos-filter ${!libState.position ? "on" : ""}" data-pos="">全部位置</button>`]
    .concat(POS_ORDER.map(p => {
      const n = posCount(p);
      if (!n) return "";
      return `<button class="pos-filter ${libState.position === p ? "on" : ""}" data-pos="${p}">${POS_LABEL[p]}<i>${n}</i></button>`;
    })).join("");

  const genreChips = isGrammarTab ? "" : [`<button class="genre-filter ${!libState.genre ? "on" : ""}" data-genre="">全部文体</button>`]
    .concat(GENRE_ORDER.map(g => {
      const n = genreCount(g);
      if (!n) return "";
      return `<button class="genre-filter g-${g} ${libState.genre === g ? "on" : ""}" data-genre="${g}">${GENRE_MARK[g] || ""} ${GENRE_LABEL[g]}<i>${n}</i></button>`;
    })).join("");

  // 分组
  const groups = {};
  const groupKeyOf = it => (
    grp === "position" ? (it.position || "universal")
      : grp === "genre" ? (it.genre || "universal")
        : (it.category || "通用表达")
  );
  shown.forEach(it => {
    const key = groupKeyOf(it);
    (groups[key] = groups[key] || []).push(it);
  });
  const order = grp === "position" ? POS_ORDER
    : grp === "genre" ? GENRE_ORDER
      : [...CAT_ORDER, ...Object.keys(groups).filter(k => !CAT_ORDER.includes(k))];
  const keys = order.filter(k => groups[k] && groups[k].length);
  // 兜底：order 里没覆盖到的分组
  Object.keys(groups).forEach(k => { if (!keys.includes(k)) keys.push(k); });

  const groupNote = grp === "position"
    ? "按「用在作文的位置」分组"
    : grp === "genre"
      ? "按「文体」分组"
      : "按「主题分类」分组";

  const summaryBar = `
    <div class="lib-summary">
      <div class="lib-sum-item"><b>${s.total || 0}</b><span>素材总量</span></div>
      <div class="lib-sum-item"><b>${(s.by_library || {}).writing || 0}</b><span>作文素材</span></div>
      <div class="lib-sum-item"><b>${(s.by_library || {}).reading || 0}</b><span>阅读积累</span></div>
      <div class="lib-sum-item"><b>${s.auto_classified || 0}</b><span>AI 归类</span></div>
    </div>`;

  const emptyMsg = terms.length
    ? `没有匹配「${esc(libState.q)}」的素材。试试更短的关键词，或点右上角的 ✕ 清空搜索。`
    : libState.type === "grammar"
      ? "语法积累库还是空的。读文章时在「长难句与语法」里点「＋ 收进语法积累库」，语法点就会攒到这里。"
      : libState.type === "sentence"
        ? "还没有收藏过长难句。读文章时展开「长难句与语法」，点「☆ 收藏这句」就会进这里。"
        : libState.type === "vocab"
          ? "还没有收藏过单词。读文章时在「今日词汇」里点「☆ 收藏」，单词就会进这里。"
          : "这里还没有素材。阅读时点「☆ 收藏」或「加入作文素材库」，AI 会自动判断该进哪个库、用在作文的哪一段，以及它属于什么文体。";

  const body = keys.length
    ? keys.map(k => `
      <div class="lib-group">
        <div class="lib-group-head">
          ${grp === "position"
            ? `<span class="pos-chip pos-${k}">${POS_LABEL[k] || k}</span>`
            : grp === "genre"
              ? genreChip(k)
              : `<span class="lib-cat-name">${esc(k)}</span>`}
          <span class="muted small">${groups[k].length} 条</span>
        </div>
        ${grp === "position" && POS_USAGE[k] ? `<div class="lib-group-note">${POS_USAGE[k]}</div>` : ""}
        ${grp === "genre" && GENRE_HINT[k] ? `<div class="lib-group-note">${GENRE_HINT[k]}</div>` : ""}
        ${groups[k].map(matCard).join("")}
      </div>`).join("")
    : `<p class="muted small" style="margin-top:18px;line-height:1.75">${emptyMsg}</p>`;

  const gDist = GENRE_ORDER
    .map(g => [g, GENRE_MARK[g] || "", GENRE_LABEL[g], shown.filter(i => (i.genre || "universal") === g).length])
    .filter(([, , , n]) => n);

  // 输入框在每次重绘里都是新节点，所以要把「焦点和光标位置」手动还回去，
  // 否则打字打一个字符就会掉焦点。
  const oldInput = $("#libSearch");
  const keepFocus = oldInput && document.activeElement === oldInput;
  const caret = keepFocus ? oldInput.selectionStart : 0;

  $("#pageBody-library").innerHTML = `
    ${summaryBar}
    <div class="lib-tabs lib-type-tabs">${typeTabs}</div>
    ${LIB_TYPE_NOTE[libState.type] ? `<p class="muted small lib-type-note">${LIB_TYPE_NOTE[libState.type]}</p>` : ""}
    <div class="lib-tabs">${tabs}</div>
    <div class="lib-search">
      <span class="lib-search-ico">🔍</span>
      <input id="libSearch" type="search" autocomplete="off"
             placeholder="搜索素材：英文 / 中文 / 用法…（空格分隔多个关键词）"
             value="${esc(libState.q)}">
      ${libState.q ? `<button class="lib-search-clear" data-qclear title="清空">✕</button>` : ""}
    </div>
    <div class="lib-toolbar">
      ${isGrammarTab ? "" : `<div class="pos-filters">${posChips}</div>
      <div class="pos-filters genre-filters">${genreChips}</div>`}
      <div class="group-switch">
        ${isGrammarTab
          ? `<button class="on" data-group="category">按语法分类</button>`
          : `<button class="${grp === "position" ? "on" : ""}" data-group="position">按位置</button>
        <button class="${grp === "genre" ? "on" : ""}" data-group="genre">按文体</button>
        <button class="${grp === "category" ? "on" : ""}" data-group="category">按主题</button>`}
      </div>
    </div>
    <div class="lib-statline">
      <span class="muted small">
        ${terms.length ? `搜索 <b>${esc(libState.q)}</b> · 命中 <b>${shown.length}</b> 条`
                       : `${groupNote} · 当前显示 <b>${shown.length}</b> 条`}
      </span>
      ${gDist.length && !isGrammarTab ? `<span class="lib-genre-dist"><span class="gd-label">文体分布</span>${gDist.map(([g, mark, label, n]) =>
        `<span class="gd-item"><span class="genre-chip g-${g}">${mark} ${label}</span><b>${n}</b></span>`).join("")}</span>` : ""}
    </div>
    ${body}
    ${isGrammarTab ? "" : `<div class="lib-legend">
      <div class="w-group-title">位置说明</div>
      ${POS_ORDER.map(p => `<div class="lib-legend-row"><span class="pos-chip pos-${p}">${POS_LABEL[p]}</span><span>${POS_USAGE[p]}</span></div>`).join("")}
      <div class="w-group-title" style="margin-top:14px">文体说明</div>
      ${GENRE_ORDER.map(g => `<div class="lib-legend-row">${genreChip(g)}<span>${GENRE_HINT[g]}</span></div>`).join("")}
    </div>`}`;

  if (keepFocus) {
    const n = $("#libSearch");
    n.focus();
    try { n.setSelectionRange(caret, caret); } catch { /* type=search 可能不支持，忽略 */ }
  }
}

function matCard(it) {
  const pos = it.position || "universal";
  const gen = it.genre || "universal";
  const lv = it.level || "B";
  const aiPct = it.auto && it.confidence ? `${Math.round(it.confidence * 100)}%` : "";
  const rc = it.review_count || 0;
  const nr = it.next_review || "";
  const tp = libTypeOf(it);
  const typeLabel = (LIB_TYPES.find(([k]) => k === tp) || ["", "素材"])[1];
  /* 长难句 / 语法是「闲时精读」：不进每日复习，所以卡片上不摆位置/文体这些作文标签，
     也不给「重新加入复习」—— 免得让人以为它们本该每天提醒（见 db._NOT_IN_REVIEW）。 */
  const quiet = tp === "sentence" || tp === "grammar";
  const revLine = quiet
    ? `<span class="mat-rev off">不进每日复习</span>`
    : !nr
      ? `<span class="mat-rev off">已移出复习</span>`
      : rc >= 6
        ? `<span class="mat-rev done">已巩固 · 复习 ${rc} 次</span>`
        : `<span class="mat-rev">复习 ${rc} 次 · 下次 ${esc(nr)}</span>`;
  return `
    <div class="mat-item" data-id="${it.id}">
      <div class="mat-top">
        <span class="mat-type t-${tp}">${typeLabel}</span>
        ${quiet ? "" : `<span class="pos-chip pos-${pos}">${POS_LABEL[pos] || pos}</span>
        ${genreChip(gen)}`}
        <span class="mat-cat">${esc(it.category || "通用表达")}</span>
        <span class="mat-lv lv-${lv}" title="${LEVEL_LABEL[lv] || ""}">${lv}</span>
        <span class="mat-auto ${it.auto ? "" : "manual"}">${it.auto ? `AI 归类 ${aiPct}` : "手动 / 规则"}</span>
      </div>
      <div class="mat-en">${hl(it.content)}</div>
      ${it.cn ? `<div class="mat-cn">${hl(it.cn)}</div>` : ""}
      ${it.usage ? `<div class="mat-usage">💡 ${hl(it.usage)}</div>` : ""}
      ${it.example ? `<div class="mat-ex">${hl(it.example)}</div>` : ""}
      <div class="mat-foot">
        <span class="mat-tags">${(it.tags || []).map(t => `<span class="tag">${hl(t)}</span>`).join("")}</span>
        ${revLine}
        <span class="mat-date">${esc((it.created_at || "").slice(0, 10))}</span>
      </div>
      <div class="mat-actions">
        ${quiet ? "" : `<button class="btn tiny" data-act="reclass">↻ AI 重新归类</button>
        <button class="btn tiny" data-act="retag">调整分类</button>`}
        ${!quiet && !nr ? `<button class="btn tiny" data-act="readd">↻ 重新加入复习</button>` : ""}
        <button class="btn tiny warnish" data-act="del">删除</button>
      </div>
      ${quiet ? "" : `<div class="mat-retag" hidden>
        <div class="retag-row"><span class="retag-lbl">位置</span>
          ${POS_ORDER.map(p => `<button class="btn tiny ${pos === p ? "on" : ""}" data-set-pos="${p}">${POS_LABEL[p]}</button>`).join("")}
        </div>
        <div class="retag-row"><span class="retag-lbl">文体</span>
          ${GENRE_ORDER.map(g => `<button class="btn tiny ${gen === g ? "on" : ""}" data-set-genre="${g}">${GENRE_LABEL[g]}</button>`).join("")}
        </div>
      </div>`}
    </div>`;
}

/* 素材库交互（委托在 document 上：整页和弹层两个容器都生效） */
document.addEventListener("click", async e => {
  if (e.target.closest("[data-qclear]")) {
    libState.q = "";
    renderLibrary();
    return;
  }
  const typeBtn = e.target.closest("[data-type]");
  if (typeBtn) {
    libState.type = typeBtn.dataset.type;
    libState.position = "";
    libState.genre = "";
    renderLibrary();
    return;
  }
  const libBtn = e.target.closest("[data-lib]");
  if (libBtn) {
    libState.library = libBtn.dataset.lib;
    libState.position = "";
    libState.genre = "";
    renderLibrary();
    return;
  }
  const posBtn = e.target.closest("[data-pos]");
  if (posBtn) {
    libState.position = posBtn.dataset.pos;
    renderLibrary();
    return;
  }
  const genreBtn = e.target.closest("[data-genre]");
  if (genreBtn) {
    libState.genre = genreBtn.dataset.genre;
    renderLibrary();
    return;
  }
  const grpBtn = e.target.closest("[data-group]");
  if (grpBtn) {
    libState.groupBy = grpBtn.dataset.group;
    renderLibrary();
    return;
  }

  const card = e.target.closest(".mat-item");
  if (!card) return;
  const id = card.dataset.id;
  const act = (e.target.closest("[data-act]") || {}).dataset?.act;

  try {
    if (act === "retag") {
      const box = card.querySelector(".mat-retag");
      box.hidden = !box.hidden;
      return;
    }
    if (act === "reclass") {
      const btn = e.target.closest("[data-act]");
      if (!state.meta || !state.meta.ai_ready) { toast("还没有配置 AI 模型", true); return; }
      btn.disabled = true; btn.textContent = "AI 归类中…";
      const r = await api(`/api/favorites/${id}/reclassify`, { method: "POST" });
      toast(`已归入【${r.item.library === "writing" ? "作文素材库" : "阅读积累库"} · ${POS_LABEL[r.item.position] || ""} · ${GENRE_LABEL[r.item.genre] || ""}】`);
      await reloadLibrary();
      return;
    }
    const setPos = e.target.closest("[data-set-pos]");
    if (setPos) {
      await api(`/api/favorites/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ position: setPos.dataset.setPos }),
      });
      toast(`已调整为「${POS_LABEL[setPos.dataset.setPos]}」`);
      await reloadLibrary();
      return;
    }
    const setGenre = e.target.closest("[data-set-genre]");
    if (setGenre) {
      await api(`/api/favorites/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ genre: setGenre.dataset.setGenre }),
      });
      toast(`文体已调整为「${GENRE_LABEL[setGenre.dataset.setGenre]}」`);
      await reloadLibrary();
      return;
    }
    if (act === "readd") {
      await api(`/api/review/${id}/result`, { method: "POST", body: JSON.stringify({ result: "readd" }) });
      toast("已重新加入复习节奏");
      await reloadLibrary();
      return;
    }
    if (act === "del") {
      if (!confirm("确定要删除这条素材吗？")) return;
      await api(`/api/favorites/${id}`, { method: "DELETE" });
      toast("已删除");
      await reloadLibrary();
      return;
    }
  } catch (err) { toast(err.message, true); }
});

/* 搜索框边打边筛（renderLibrary 里会把焦点和光标位置还原） */
document.addEventListener("input", e => {
  if (e.target && e.target.id === "libSearch") {
    libState.q = e.target.value;
    renderLibrary();
  }
});

/* ---------------- 设置 ---------------- */
$("#btnLibrary").addEventListener("click", () => navigate("library"));
$("#btnNotes").addEventListener("click", () => navigate("notes"));
/* 阅读外观 Aa：在正文上方展开/收起，面板里的按钮走下面那个委托 */
$("#btnRead").addEventListener("click", () => {
  const box = $("#readPanel");
  if (!box) return;
  const willShow = box.hidden;
  box.hidden = !willShow;
  box.innerHTML = willShow ? Reading.html() : "";
  $("#btnRead").classList.toggle("on", willShow);
  if (willShow) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
});

$("#btnSettings").addEventListener("click", async () => {
  openSheet("设置", `<p class="muted small">加载中…</p>`);
  const box = $("#sheetBody");
  const request = sheetEpoch;
  try {
    const s = await api("/api/settings");
    if (request !== sheetEpoch) return;
    box.innerHTML = `
      <div class="w-group-title">运行状态</div>
      <p class="small" style="line-height:1.9">
        AI 模型：<b>${s.ai_ready ? esc(s.model) : "未配置"}</b><br>
        服务地址：${esc(s.base_url)}<br>
        当前阶段：${esc(s.stage)}<br>
        明日难度基准：${"★".repeat(s.next_difficulty)}<br>
        自动生成时间：每天 ${s.daily_hour}:00 之后<br>
        ${s.scheduler && s.scheduler.last_error ? `<span style="color:var(--warn)">最近一次自动生成失败：${esc(s.scheduler.last_error)}</span>` : ""}
      </p>
      <div class="w-group-title">考研初试日期</div>
      <p class="muted small" style="line-height:1.8;margin-bottom:8px">
        主界面那个倒计时按这个日期算。官方日期公布前先用<b>预计值 2027-12-18</b>
        （近几年的规律是 12 月倒数第二个周六）—— 公布了在这里改一次就行。
      </p>
      <div class="im-actions">
        <input type="date" id="examDate" class="field-input"
               value="${esc(s.exam_date || "")}" style="max-width:190px">
        <button class="btn small" id="btnSaveExamDate">保存</button>
      </div>
      <div class="w-group-title">学习阶段</div>
      <div class="im-actions">
        <button class="btn small" data-stage="基础期">基础期</button>
        <button class="btn small" data-stage="强化期">强化期</button>
        <button class="btn small" data-stage="冲刺期">冲刺期</button>
      </div>
      <div class="w-group-title">素材库</div>
      <p class="muted small" style="line-height:1.8;margin-bottom:8px">
        收藏的素材会自动归类到「作文素材库 / 阅读积累库」，并标注该用在作文的哪一段（开头 / 过渡 / 主体 / 结尾）以及它属于什么文体（议论文 / 说明文 / 应用文 / 描写文 / 记叙文）。
      </p>
      <button class="btn small" id="btnFavList">打开素材库</button>
      <div class="w-group-title">模型与密钥</div>
      <p class="muted small" style="line-height:1.8">
        模型、接口地址、API Key 都写在项目根目录的 <b>.env</b> 文件里，改完重启服务即可生效。
        支持任何 OpenAI 兼容接口（DeepSeek / 智谱 GLM / 通义千问 / Kimi / OpenAI / 本地 Ollama）。
      </p>
      ${accountBlock(s.auth)}`;
    $("#btnFavList").addEventListener("click", () => { closeSheet(); navigate("library"); });
    const sx = $("#btnSaveExamDate");
    if (sx) sx.addEventListener("click", async () => {
      const v = ($("#examDate") || {}).value || "";
      await api("/api/settings", { method: "POST", body: JSON.stringify({ exam_date: v }) });
      EXAM.date = v;
      toast("已保存考试日期");
      $("#btnSettings").click();
    });
    const lo = $("#btnLogout");
    if (lo) lo.addEventListener("click", () => { location.href = "/logout"; });
    const adm = $("#btnAdmin");
    if (adm) adm.addEventListener("click", () => { closeSheet(); navigate("admin"); });
    const gs = $("#btnGotoSetup");
    if (gs) gs.addEventListener("click", () => { location.href = "/setup"; });
    const cp = $("#btnChangePwd");
    if (cp) cp.addEventListener("click", openChangePassword);
    $$("#sheetBody [data-stage]").forEach(b => b.addEventListener("click", async () => {
      await api("/api/settings", { method: "POST", body: JSON.stringify({ stage: b.dataset.stage }) });
      toast(`已切换到${b.dataset.stage}`);
      $("#btnSettings").click();
    }));
  } catch (e) { if (request === sheetEpoch) box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; }
});

/* 顶栏那个「我的账号」标志：点开就是设置 */
$("#btnAccount").addEventListener("click", () => $("#btnSettings").click());

/* ---------------- 数学模块顶栏两个入口（用户 2026-10-07 指定） ----------------
   「错题总库」= 回到浏览态（收起刷题面板）；「开始刷题」= 从全部错题随机抽。 */
$("#btnMathLib").addEventListener("click", () => {
  MATH.drill = null; MATH.revealed = false;
  renderDrill();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
$("#btnMathDrill").addEventListener("click", () => mathRandom());

/* 顶栏亮哪个入口：在刷题态就亮「开始刷题」，否则亮「错题总库」 */
function paintMathTabs() {
  const lib = $("#btnMathLib"), dr = $("#btnMathDrill");
  if (!lib || !dr) return;
  const drilling = !!MATH.drill;
  lib.classList.toggle("on", !drilling);
  dr.classList.toggle("on", drilling);
}

/* ---------------- 我的账号（设置页里的一段） ---------------- */
function accountBlock(a) {
  a = a || {};
  if (a.mode === "accounts") {
    const who = esc(a.display_name || a.username);
    return `
      <div class="w-group-title">我的账号</div>
      <p class="small" style="line-height:1.9">
        当前登录：<b>${who}</b>
        <span class="muted">@${esc(a.username)}</span>
        ${a.is_admin ? '<span class="u-chip u-admin">管理员</span>' : ""}
      </p>
      <p class="muted small" style="line-height:1.8">
        你的文章、词汇、素材库、复习进度和学习数据都是独立的一份，别人看不到。
      </p>
      <div class="im-actions">
        <button class="btn small" id="btnChangePwd">修改密码</button>
        <button class="btn small warnish" id="btnLogout">退出登录</button>
      </div>
      ${a.is_admin ? `
      <div class="w-group-title">用户管理</div>
      <p class="muted small" style="line-height:1.8">
        给熟人开号、受理别人自己提交的注册申请${a.pending_count ? `（<b>${a.pending_count}</b> 条待批准）` : ""}、
        重置密码、停用账号。每人各有自己的一整套数据。
      </p>
      <button class="btn small" id="btnAdmin">打开用户管理</button>` : ""}`;
  }
  if (a.can_setup) {
    return `
      <div class="w-group-title">账号</div>
      <p class="muted small" style="line-height:1.8">
        现在还是<b>单人自用</b>状态：没有账号，也不需要登录，数据都放在「未认领」里。
        创建第一个账号后系统会自动切到账号体系，并把这批数据交给它。
      </p>
      <button class="btn small" id="btnGotoSetup">创建第一个账号</button>`;
  }
  return `
      <div class="w-group-title">访问安全</div>
      <p class="muted small" style="line-height:1.8">
        已开启访问口令。换口令改 <b>.env</b> 里的 <b>ACCESS_PASSWORD</b> 后重启，
        所有设备都需要重新登录。
      </p>
      <button class="btn small warnish" id="btnLogout">退出登录</button>`;
}

function openChangePassword() {
  openSheet("修改密码", `
    <div class="u-form">
      <label>当前密码<input id="pwOld" type="password" autocomplete="current-password"></label>
      <label>新密码（至少 6 位）<input id="pwNew" type="password" autocomplete="new-password"></label>
      <label>再输一次<input id="pwNew2" type="password" autocomplete="new-password"></label>
      <button class="btn primary" id="btnPwSave">保存</button>
      <p class="muted small" style="line-height:1.8">
        改完之后所有设备（包括这台）都要重新登录，这是有意的。
      </p>
    </div>`);
  $("#btnPwSave").addEventListener("click", async () => {
    const o = $("#pwOld").value, n1 = $("#pwNew").value, n2 = $("#pwNew2").value;
    if (!o || !n1) { toast("请填完整", true); return; }
    if (n1 !== n2) { toast("两次输入的新密码不一致", true); return; }
    try {
      await api("/api/me/password", { method: "POST", body: JSON.stringify({ old: o, new: n1 }) });
      toast("已改好，正在回到登录页…");
      setTimeout(() => (location.href = "/login"), 900);
    } catch (e) { toast(e.message, true); }
  });
}

/* ---------------- 用户管理（#admin 整页） ---------------- */
async function loadAdmin() {
  const { box, isCurrent } = pageLoadContext("admin");
  box.innerHTML = `<p class="muted small">加载中…</p>`;
  try {
    const data = await api("/api/admin/users");
    if (isCurrent()) renderAdmin(data);
  } catch (e) {
    if (isCurrent()) box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
  }
}

/* 自助申请的账号：现在登不进来，只能批准或拒绝 */
function pendingRow(u) {
  const when = u.created_at
    ? `申请于 ${esc(String(u.created_at).slice(0, 16))}`
    : "刚刚申请";
  return `
    <div class="u-row u-pending">
      <div class="u-head">
        <b>${esc(u.display_name || u.username)}</b>
        <span class="muted small">@${esc(u.username)}</span>
        <span class="u-chip u-wait">等批准</span>
      </div>
      <div class="u-sub">${when}　·　批准之后他才有自己的一套文章、素材和数据</div>
      <div class="u-ops">
        <button class="btn tiny primary" data-uop="approve" data-uid="${u.id}">✓ 批准</button>
        <button class="btn tiny ghost" data-uop="reject" data-uid="${u.id}">拒绝</button>
      </div>
    </div>`;
}

function userRow(u) {
  const me = (state.auth && state.auth.username) === u.username;
  const last = u.last_login_at ? `最近登录 ${esc(String(u.last_login_at).slice(0, 16))}` : "还没登录过";
  return `
    <div class="u-row${me ? " u-me" : ""}">
      <div class="u-head">
        <b>${esc(u.display_name || u.username)}</b>
        <span class="muted small">@${esc(u.username)}</span>
        ${u.is_admin ? '<span class="u-chip u-admin">管理员</span>' : ""}
        ${u.active ? "" : '<span class="u-chip u-off">已停用</span>'}
        ${me ? '<span class="u-chip">这是你</span>' : ""}
      </div>
      <div class="u-sub">
        文章 ${u.article_count}　·　素材 ${u.favorite_count}　·　${last}
      </div>
      <div class="u-ops">
        <button class="btn tiny" data-uop="pwd" data-uid="${u.id}">重置密码</button>
        <button class="btn tiny${u.active ? " warnish" : ""}" data-uop="active" data-uid="${u.id}" data-val="${u.active ? 0 : 1}">${u.active ? "停用" : "启用"}</button>
        <button class="btn tiny" data-uop="admin" data-uid="${u.id}" data-val="${u.is_admin ? 0 : 1}">${u.is_admin ? "取消管理员" : "设为管理员"}</button>
        ${me ? "" : `<button class="btn tiny ghost" data-uop="kick" data-uid="${u.id}">踢下线</button>`}
      </div>
    </div>`;
}

function renderAdmin(d) {
  if (currentView !== "admin") return;
  const all = d.items || [];
  // 待批准的单独占一块：混在已批准列表里就看不清「谁还没被放进来」
  const pending = all.filter(u => !u.approved);
  const items = all.filter(u => u.approved);
  const regUrl = `${location.origin}/register`;
  $("#pageBody-admin").innerHTML = `
    ${pending.length ? `
    <section class="card">
      <div class="acc-title-row">
        <span class="ico">⏳</span><span class="acc-title">${pending.length} 条申请待批准</span>
      </div>
      <p class="u-note">
        这些人是自己在注册页填的。他们<b>现在登不进来</b> —— 你点了「批准」才能用。
        不认识的人直接「拒绝」：账号会被删掉，那个用户名也释放出来，他可以重新申请。
      </p>
      <div class="u-list">${pending.map(pendingRow).join("")}</div>
    </section>` : ""}

    <section class="card">
      <div class="acc-title-row">
        <span class="ico">👥</span><span class="acc-title">共 ${items.length} 个已批准的账号</span>
      </div>
      <p class="u-note">
        每个人有<b>各自独立</b>的文章、词汇、素材库、复习进度和学习数据。
        ${d.self_register
          ? `把这个网址发给对方，他就能自己申请：<code>${esc(regUrl)}</code>（要你批准后才生效）；<br>也可以直接在下面给他开一个，立即可用。`
          : `自助注册已关闭（<code>.env</code> 里的 <code>ALLOW_SELF_REGISTER=0</code>），只能在下面手动开号。`}
      </p>
      <div class="u-list">${items.map(userRow).join("")}</div>
    </section>

    <section class="card">
      <div class="acc-title-row">
        <span class="ico">➕</span><span class="acc-title">添加账号</span>
      </div>
      <div class="u-form">
        <label>用户名<input id="nuName" type="text" autocapitalize="off" autocorrect="off"
               placeholder="3-24 位字母 / 数字 / 下划线 / 点 / 短横线"></label>
        <label>显示名（选填）<input id="nuDisplay" type="text" placeholder="比如：小王"></label>
        <label>密码<input id="nuPass" type="password" placeholder="至少 6 位"
               autocomplete="new-password"></label>
        <label class="u-chk"><input type="checkbox" id="nuAdmin"> 也给他管理员权限</label>
        <button class="btn primary" id="btnNewUser">创建账号</button>
        <p class="muted small" style="line-height:1.8">
          管理员可以给别人开号、改密码、停用账号，权限较大 —— 一般只给信得过的人。
        </p>
      </div>
    </section>`;

  $("#btnNewUser").addEventListener("click", async () => {
    const username = $("#nuName").value.trim();
    const password = $("#nuPass").value;
    if (!username || !password) { toast("用户名和密码都要填", true); return; }
    try {
      const r = await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username, password,
          display_name: $("#nuDisplay").value.trim(),
          is_admin: $("#nuAdmin").checked,
        }),
      });
      toast(`账号 ${r.user.username} 已创建`);
      loadAdmin();
    } catch (e) { toast(e.message, true); }
  });
}

/* 用户管理里的按钮用事件委托挂在 document 上：
   绑在 #pageBody-admin 上的话，整页重绘后按钮会集体失灵且不报错。 */
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-uop]");
  if (!b) return;
  e.preventDefault();
  const uid = Number(b.dataset.uid);
  const op = b.dataset.uop;
  try {
    if (op === "pwd") {
      openSheet("重置密码", `
        <div class="u-form">
          <p class="muted small" style="margin:0 0 4px">
            给这个账号设一个新密码。改完对方所有设备都会掉线，需要重新登录。
          </p>
          <label>新密码<input id="rpNew" type="password" autocomplete="new-password"
                 placeholder="至少 6 位"></label>
          <label>再输一次<input id="rpNew2" type="password" autocomplete="new-password"></label>
          <button class="btn primary" id="btnRpSave">保存</button>
        </div>`);
      $("#btnRpSave").addEventListener("click", async () => {
        const a = $("#rpNew").value, c = $("#rpNew2").value;
        if (!a) { toast("请填新密码", true); return; }
        if (a !== c) { toast("两次输入不一致", true); return; }
        try {
          await api(`/api/admin/users/${uid}/password`, {
            method: "POST", body: JSON.stringify({ password: a }),
          });
          closeSheet();
          toast("密码已重置，对方需要重新登录");
          loadAdmin();
        } catch (err) { toast(err.message, true); }
      });
    } else if (op === "active") {
      const on = b.dataset.val === "1";
      if (!on && !confirm("停用后这个人会立刻掉线、也登不进来（数据全部保留）。确认停用吗？")) return;
      await api(`/api/admin/users/${uid}/active`, {
        method: "POST", body: JSON.stringify({ active: on }),
      });
      toast(on ? "已启用" : "已停用");
      loadAdmin();
    } else if (op === "admin") {
      await api(`/api/admin/users/${uid}/admin`, {
        method: "POST", body: JSON.stringify({ is_admin: b.dataset.val === "1" }),
      });
      toast("权限已更新");
      loadAdmin();
    } else if (op === "approve") {
      await api(`/api/admin/users/${uid}/approve`, {
        method: "POST", body: JSON.stringify({ approved: true }),
      });
      toast("已批准，对方现在可以登录了");
      loadAdmin();
      refreshPendingBadge();
    } else if (op === "reject") {
      if (!confirm("拒绝后这个账号会被删除，那个用户名也会释放出来（他可以重新申请）。确认拒绝吗？")) return;
      await api(`/api/admin/users/${uid}`, { method: "DELETE" });
      toast("已拒绝，账号已删除");
      loadAdmin();
      refreshPendingBadge();
    } else if (op === "kick") {
      /* 只让对方的其他设备掉线（这里是直接把该账号所有会话清掉） */
      await api(`/api/admin/users/${uid}/logout_all`, { method: "POST", body: "{}" });
      toast("已让这个账号的所有设备重新登录");
    }
  } catch (err) { toast(err.message, true); }
});

/* ---------------- 视图路由：整页 vs 弹层 ----------------
   分工：需要「待一会儿」的（素材库 / 数据 / 历史 / 复习）走整页；
        一次性办完就走的（设置 / 导入表单）继续走弹层。
   整页会往浏览历史里记一笔，所以手机返回键和侧滑手势都能回到今日内容 ——
   这件事弹层永远做不到，也是这次改动最实在的收益。 */
/* 哪些视图属于**英语模块** —— 顶栏的任务类入口只在它们上面出现（见 showView）。
   ⚠️ 白名单：默认收起。数学一是独立模块，别加进来（用户 2026-10-07 指出）。
   "today" 不在 PAGES 里（它有自己的 #todayView），要单独列。 */
const EN_TASK_VIEWS = new Set(["today", "review", "library", "notes", "stats", "history"]);

const PAGES = {
  subjects: { el: "#page-subjects", body: "#pageBody-subjects" },
  library: { el: "#page-library", body: "#pageBody-library" },
  notes:   { el: "#page-notes",   body: "#pageBody-notes" },
  stats:   { el: "#page-stats",   body: "#pageBody-stats" },
  history: { el: "#page-history", body: "#pageBody-history" },
  review:  { el: "#page-review",  body: "#pageBody-review" },
  math:    { el: "#page-math",    body: "#pageBody-math" },
  admin:   { el: "#page-admin",   body: "#pageBody-admin" },
};
let currentView = "today";
let renderTarget = "#sheetBody";   // 当前内容写到哪里（弹层 or 某个整页）
let viewEpoch = 0;
const pageLoadSeq = {};

function T() { return $(renderTarget); }
function pageLoadContext(view) {
  const box = $(PAGES[view].body);
  const epoch = viewEpoch;
  const request = pageLoadSeq[view] = (pageLoadSeq[view] || 0) + 1;
  return {
    box,
    isCurrent: () => currentView === view && viewEpoch === epoch && pageLoadSeq[view] === request,
  };
}
function parseView() {
  const h = location.hash.replace(/^#/, "");
  if (h === "today") return "today";
  if (h === "sig") return "math";
  /* ⚠️ 不带 hash 时落在**科目选择页** —— 用户 2026-10-07 明确要求：
     主界面先选科目（英语一 / 数学一），而不是直接进英语。 */
  return PAGES[h] ? h : "subjects";
}
function showView(view) {
  viewEpoch += 1;
  currentView = view;
  /* 顶栏的任务类入口只在**英语模块的页面**出现（白名单，默认收起）。
     ⚠️ 别改成「非主界面就摊开」：数学一是独立模块，有自己的页内工具条；
        第一版那样写会让数学专题页里冒出一排英语按钮（用户 2026-10-07 抓到）。 */
  document.body.classList.toggle("view-en", EN_TASK_VIEWS.has(view));
  /* 数学模块有自己的两个入口（错题总库 / 开始刷题），同样按白名单只在这里出现。 */
  document.body.classList.toggle("view-math", view === "math");
  $("#todayView").hidden = view !== "today";
  Object.keys(PAGES).forEach(k => { $(PAGES[k].el).hidden = k !== view; });
  renderTarget = view === "today" ? "#sheetBody" : PAGES[view].body;
  /* 切回今日内容时，之前躲在整页后面的区块刚重新可见，画布要重新量尺寸 */
  if (view === "today" && window.Pen) requestAnimationFrame(() => window.Pen.sync());
}
function route() {
  const v = parseView();
  const wasMath = currentView === "math";
  let moduleChanged = false;
  if (v !== "math" && MATH.markCurrent) mathMarkClose(true);
  if (v === "math") {
    const module = location.hash === "#sig" ? "sig" : "math";
    if (MATH.module !== module) {
      moduleChanged = true;
      if (MATH.markCurrent) mathMarkClose(true);
      MATH.module = module;
      MATH.f.subject = module === "sig" ? "信号与系统" : "";
      MATH.f.topic = "";
      MATH.meta = null;
      MATH.openId = null;
      MATH.drill = null;
      MATH.revealed = false;
      MATH.dueAnswers.clear();
    }
  }
  showView(v);
  if (v === "math" && (!wasMath || moduleChanged)) T().innerHTML = `<p class="muted small">加载中…</p>`;
  window.scrollTo({ top: 0 });
  if (v !== "today") loadPage(v);
}
function navigate(view) {
  closeSheet();                                            // 从设置等弹层里跳过来时先收掉
  if (location.hash === "#" + view) { route(); return; }   // 再点一次同一个入口 = 刷新
  location.hash = view;
}
/* 回今日内容。直接替换掉当前这条历史记录，而不是 history.back()：
   否则「素材库 → 学习数据 → ←」会退回素材库，和按钮上写的「回到今日内容」不符。
   手机返回键 / 侧滑仍然走浏览器自己的历史后退，两个入口各司其职。 */
function goHome() {
  // 用 replaceState 而不是 history.back()：否则「素材库 → 学习数据 → ←」
  // 会退回素材库，和按钮上写的「回到今日内容」不符。
  if (location.hash !== "#today") history.replaceState(null, "", location.pathname + "#today");
  route();
}
/* 在整页里点开某篇真题 / 某天内容时用这个：同步切回今日，避免要等 history.back() 的异步时序 */
function backToToday() {
  closeSheet();
  if (currentView !== "today") {
    history.replaceState(null, "", location.pathname + "#today");
    route();
  }
}
function loadPage(view) {
  if (view === "subjects") return loadSubjects();
  if (view === "library") return loadLibrary();
  if (view === "notes")   return loadNotes();
  if (view === "stats")   return loadStats();
  if (view === "history") return loadHistory();
  if (view === "review")  return loadReview();
  if (view === "math")    return loadMath();
  if (view === "admin")   return loadAdmin();
}
window.addEventListener("hashchange", route);
document.addEventListener("click", e => {
  const back = e.target.closest("[data-back]");
  if (!back) return;
  e.preventDefault();
  /* 默认回今日内容；数学页要回它的上一层 —— 科目选择 */
  if (back.dataset.backTo === "subjects") navigate("subjects");
  else goHome();
});
$("#brandHome").addEventListener("click", () => navigate("subjects"));  // 点品牌 = 回主界面

/* ---------------- 弹层控制（只留给一次性操作） ---------------- */
let sheetEpoch = 0;
function openSheet(title, html) {
  sheetEpoch += 1;
  renderTarget = "#sheetBody";
  $("#sheetTitle").textContent = title;
  T().innerHTML = html;
  $("#sheet").hidden = false;
  document.body.style.overflow = "hidden";
}
function closeSheet() {
  sheetEpoch += 1;
  $("#sheet").hidden = true;
  document.body.style.overflow = "";
  // 关掉弹层后把渲染目标交还给当前视图，否则后续重绘会写错地方
  renderTarget = currentView === "today" ? "#sheetBody" : PAGES[currentView].body;
}
$$("[data-close]").forEach(el => el.addEventListener("click", closeSheet));
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSheet(); });

/* ---------------- 离开时补记阅读时长 ---------------- */
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && state.bundle && !state.revealed) {
    const sec = Math.floor((Date.now() - state.readStart) / 1000);
    if (sec > 20) {
      fetch("/api/read-log", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: state.date, seconds: sec }),
        keepalive: true,
      }).catch(() => {});
    }
  }
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

/* ---------------- 尺寸相关的实测量 ----------------
   有些值必须实测，写死一定会在某个设备上出错：
     · --topbar-h 给粘性小标题（.page-head 的 top 偏移）用。
       图标尺寸分档之后顶栏高度会变（手机 ~57px / 平板 ~66px），
       原来写死 53px，小标题会被顶栏压住 4~13px。
     · --penbar-h 交给 pen.js（工具栏是它建的）。
   转屏、缩放窗口、以及不同设备档都会重新命中这些媒体查询，所以必须跟着重量。 */
function syncMetrics() {
  const tb = document.querySelector(".topbar");
  if (tb) {
    const h = Math.round(tb.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty("--topbar-h", h + "px");
  }
  if (window.Pen && window.Pen.measureBar) window.Pen.measureBar();
}

if (window.ResizeObserver) {
  const tb = document.querySelector(".topbar");
  if (tb) { try { new ResizeObserver(syncMetrics).observe(tb); } catch (_) { /* 忽略 */ } }
}
window.addEventListener("resize", syncMetrics, { passive: true });
syncMetrics();

/* ⚠️ boot() / route() 的调用放在**整个文件最末尾**（见文件结尾）。
   原因：下面 `const MATH` 是数学模块的顶层常量，而 route() 是**同步**执行的 ——
   hash 一旦是 #math，它会立刻进 loadMath() 去读 MATH。调用若写在 MATH 声明之前，
   会直接抛 `Cannot access 'MATH' before initialization`（暂时性死区），**整页白屏**。
   已经踩过一次（出图时才发现），别再把这两行往上挪。 */

/* ==========================================================
   数学一错题库
   ----------------------------------------------------------
   三条口径（用户明确要求，改之前先想清楚）：
     1. 录题 = 拍照 → **只取题干** → 人工确认分类 → 保存。
        题干是唯一必填项；原图是附带证据，存不下也不该卡住录题。
     2. 刷题 = 题干 + 最终答案 + 「会做了 / 不会做」两个键（2026-10-12 起不再生成解析）。
     3. 复习 = 3 / 7 / 14 / 30 / 60 天阶梯；做错回到起点（次日再见）。
   ========================================================== */
/* ==========================================================
   科目选择（主界面）
   ----------------------------------------------------------
   ⚠️ 用户口径（2026-10-07）：**先选科目，再进科目**。
   以前把数学入口（🎓）塞在英语顶栏里，等于「在英语里面选数学」——
   这是错的。现在冷启动落在这一页：英语一 / 数学一 两个入口。
   ========================================================== */
$("#btnSubjects").addEventListener("click", () => navigate("subjects"));

const SUBJECTS = [
  {
    key: "en", icon: "📖", name: "英语", tag: "每日精读",
    desc: "真题命题视角 · 词汇长难句 · 写作仿写",
    stat: "",
  },
  {
    key: "math", icon: "🧮", name: "数学", tag: "错题本",
    desc: "拍照录题 · AI 归类解析 · 3/7/14/30 天复习",
    stat: "",
  },
  {
    key: "sig", icon: "📡", name: "信号与系统", tag: "错题本 · 专业课",
    desc: "拍照录题 · AI 归类解析 · 3/7/14/30 天复习",
    stat: "",
  },
];

/* ---- 主界面：考研倒计时（用户 2026-10-07 要求）----
   日期按账号存在 /api/settings 的 exam_date；官方没公布前是预计值，设置里能改。 */
const EXAM = { date: "" };
const WEEK_CN = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function daysUntil(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return null;
  const t = new Date(+m[1], +m[2] - 1, +m[3]);
  if (isNaN(t.getTime())) return null;
  const n = new Date();
  const today = new Date(n.getFullYear(), n.getMonth(), n.getDate());
  /* 两端都是本地零点 → 纯天数差；round 顺带扛住夏令时那 1 小时 */
  return Math.round((t - today) / 86400000);
}

function examCountdownHtml() {
  const d = daysUntil(EXAM.date);
  if (d === null) return "";
  const wd = WEEK_CN[new Date(EXAM.date + "T00:00:00").getDay()];
  return `
    <div class="exam-count card">
      <div class="exam-count-line">
        <span class="exam-count-label">距离 <b>2028 考研初试</b></span>
        <span class="exam-count-tail">${d < 0
          ? "初试已经结束"
          : `<b class="exam-count-num">${d}</b> 天`}</span>
      </div>
      <div class="exam-count-sub">${esc(EXAM.date)} ${wd}</div>
    </div>`;
}

let subjectsLoadSeq = 0;
async function loadSubjects() {
  const request = ++subjectsLoadSeq;
  // 英语侧的状态直接读已经 boot 好的 state，不再多打一次接口
  const b = state.bundle;
  SUBJECTS[0].stat = b
    ? `今日内容已就绪 · ${((b.article && b.article.title) || "").slice(0, 34)}`
    : "今日内容还没生成";
  SUBJECTS[1].stat = "";
  SUBJECTS[2].stat = "";
  renderSubjects();
  try {
    const m = await api("/api/math/meta");
    if (request !== subjectsLoadSeq || currentView !== "subjects") return;
    const st = m.stats || {};
    /* 信号与系统与数学共用同一个错题库引擎（同一张表），
       卡片要各显各的数，所以按 by_subject 拆开算，不能拿模块总数直接填。 */
    const bs = st.by_subject || {}, bsd = st.by_subject_due || {};
    const MATH_SUBS = ["高等数学", "线性代数", "概率论与数理统计"];
    const mTotal = MATH_SUBS.reduce((n, s) => n + (bs[s] || 0), 0);
    const mDue = MATH_SUBS.reduce((n, s) => n + (bsd[s] || 0), 0);
    SUBJECTS[1].stat = mTotal ? `${mTotal} 道错题 · ${mDue} 道今天该复习` : "还没有错题";
    SUBJECTS[2].stat = (bs["信号与系统"] || 0)
      ? `${bs["信号与系统"]} 道错题 · ${bsd["信号与系统"] || 0} 道今天该复习`
      : "还没有错题";
    /* 🎓 上的角标只算数学模块 —— 信号的到期数不许出现在英语顶栏（模块隔离） */
    setMathBadge(mDue);
  } catch {
    /* 拿不到统计就不显示，别挡着入口 */
  }
  try {
    const settings = await api("/api/settings");
    if (request !== subjectsLoadSeq || currentView !== "subjects") return;
    EXAM.date = settings.exam_date || "";
  } catch {
    /* 倒计时拉不到就整块不显示，别让主界面少一块骨架 */
  }
  if (request === subjectsLoadSeq && currentView === "subjects") renderSubjects();
}

function renderSubjects() {
  $("#pageBody-subjects").innerHTML = `
    ${examCountdownHtml()}
    <div class="subject-grid">
      ${SUBJECTS.map(s => `
        <button class="subject-card card" data-subject="${s.key}">
          <span class="subject-icon">${s.icon}</span>
          <span class="subject-main">
            <span class="subject-name">${esc(s.name)}<em>${esc(s.tag)}</em></span>
            <span class="subject-desc">${esc(s.desc)}</span>
            ${s.stat ? `<span class="subject-stat">${esc(s.stat)}</span>` : ""}
          </span>
          <span class="subject-go">进入 →</span>
        </button>`).join("")}
    </div>`;
}

/* ==========================================================
   数学一错题库
   ----------------------------------------------------------
   四条口径（用户明确要求，改之前先想清楚）：
     1. 录题 = 拍照 → **只取题干** → AI 归类 → 人工确认 → 保存。
        题干是唯一必填项；原图是附带证据，存不下也不该卡住录题。
        **一次可以放多张照片**，并发识别，别让人一张一张来。
     2. 刷题 = 题干 + 最终答案 + 「会做了 / 不会做」两个键（2026-10-12 起不再生成解析）。
     3. 复习 = 3 / 7 / 14 / 30 / 60 天阶梯；做错回到起点（次日再见）。
     4. 公式**要能直接看懂**：用本地打包的 KaTeX 渲染，不许把 LaTeX 源码甩给用户。
   ========================================================== */

const MATH = {
  meta: null,
  loadSeq: 0,
  module: "math",        // 当前从哪张科目卡进来：math（数学一）| sig（信号与系统）
                         // ⚠️ 两个模块共用引擎但互不相通 —— 列表 / 到期 / 随机刷题都只看本模块的科目
  items: [],
  due: [],
  rest: false,           // 周末休息：周六/周日没有到期错题（见 db.is_rest_day）
  dueAnswers: new Map(), // 已揭示的到期题解析；未揭示前接口不返回解法
  f: { subject: "", topic: "", kind: "", q: "", cycle: "" },
  openId: null,          // 列表里展开了哪一道
  editId: null,          // 正在就地改题干/答案的那道
  drafts: [],            // 录题草稿（可能有多张照片）
  draftSeq: 0,
  draftOpen: false,
  markQueue: [],
  markCurrent: null,
  markPoints: [],
  markIndex: 0,
  markBusy: false,
  markImageReady: false,
  markPrevOverflow: "",
  editActive: false,
  editBusy: false,
  editTool: "draw",
  editColor: "#db3b2d",
  editWidth: 8,
  editStrokes: [],
  editLive: null,
  editCrop: null,
  editHistory: [],
  editOriginal: "",
  extractRunning: false,
  drill: null,           // 正在刷的那道（2026-10-12 起：带最终答案，不带解析）
  revealed: false,
};

/* ---- 公式渲染：本地 KaTeX（PWA 离线也能用） ----
   只认这几种定界：$$…$$ / $…$ / \[…\] / \(…\)。
   ⚠️ 渲染不出来时**必须让人看得出来**：以前默默退回一个等宽灰块，用户看到的就是
   「AI 识别出来的题里带着数学代码」，根本分不清是模型输出烂还是渲染坏了。
   现在失败会在源码前挂一个明确的「公式没能排版」标记。 */
function mathKatex(tex, displayMode, auto) {
  const src = String(tex == null ? "" : tex).trim();
  if (!src) return "";
  const fail = (why) =>
    `<span class="mtex-badge" title="${esc(why)}">公式没能排版</span><span class="mtex">${esc(src)}</span>`;
  const k = window.katex;
  if (!k || typeof k.renderToString !== "function") {
    return fail("KaTeX 还没加载出来（首次离线访问会这样），刷新一下就能排版");
  }
  try {
    const html = k.renderToString(src, {
      displayMode: !!displayMode,
      throwOnError: false,   // 个别生僻命令不要整段炸掉
      strict: false,
      trust: false,
      errorColor: "#C0392B",
      output: "htmlAndMathml",
    });
    /* auto = 不是用户/AI 明说「独立成行」，是我们看它太高自己抬的。
       这种要包一层 .momath 当 **inline-block** 用：
       句子能绕着排就绕着排，只有真放不下才换行 ——
       否则会切出「7. 设」/（矩阵）/「，a = 9…」这种行首挂逗号的排版，更难看。 */
    const body = auto ? `<span class="momath">${html}</span>` : html;
    /* throwOnError:false 时 KaTeX 把不认识的命令**就地标红**而不是抛错，
       所以要主动扫一眼 katex-error —— 有就照样给提示。 */
    if (html.includes("katex-error")) {
      return `<span class="mtex-badge">公式里有没认出来的部分</span>${body}`;
    }
    return body;
  } catch (e) {
    return fail(`KaTeX 报错：${(e && e.message) || e}`);
  }
}

const MATH_DELIM = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)/g;

/* 这个公式该不该**独立成行**（display 排版）？
   用户 2026-10-07 的原话：「要像课本那样」。行内排版会把分式 / 分段函数压扁，
   \dfrac 那种还会被行高挤坏；而 `$…$` 只是**书写习惯**，不代表必须行内显示。
   但**也不能见公式就抬成块** —— 块会把一句话切成
   「7. 设」/（矩阵）/「，a = 9，求 |A|。」，行首还挂个逗号，比原来更难看。
   所以只挑**真的高**的：
     · \dfrac / \cfrac / \binom —— 天生比一行高
     · 三行及以上的矩阵 / 分段函数 / 对齐环境（两行的小矩阵行内完全放得下）
     · 太长的（> 40 字符），塞回一行也没法看
   其余一律行内。 */
const MATH_TALL_CMD = /\\(dfrac|cfrac|binom|tbinom|dbinom)\b/;
function mathWantsDisplay(tex) {
  const s = String(tex == null ? "" : tex);
  if (MATH_TALL_CMD.test(s)) return true;
  const env = s.match(/\\begin\{([a-zA-Z*]+)\}([\s\S]*?)\\end\{\1\}/);
  if (env && (env[2].match(/\\\\/g) || []).length >= 2) return true;
  return s.length > 40;
}

/* 题干 / 解析统一走这里：文本原样转义，公式交给 KaTeX */
function mathMtex(s) {
  const t = String(s == null ? "" : s);
  if (!t) return "";
  let out = "", last = 0, m;
  MATH_DELIM.lastIndex = 0;
  while ((m = MATH_DELIM.exec(t))) {
    out += esc(t.slice(last, m.index));
    const tex = m[1] != null ? m[1] : (m[2] != null ? m[2] : (m[3] != null ? m[3] : m[4]));
    /* $$…$$ / \[…\] 是用户/AI 明说「这块要独立成行」；
       单 $ 再看内容是不是「高」公式（分段函数、分式、高矩阵…）——
       原因见 mathWantsDisplay 上面那段。 */
    const explicit = (m[1] != null) || (m[3] != null);
    const auto = !explicit && mathWantsDisplay(tex);
    out += mathKatex(tex, explicit || auto, auto);
    last = m.index + m[0].length;
  }
  out += esc(t.slice(last));
  return out;
}

/* 纯文本版（给 title / aria 用；把公式标记摘掉，别把源码读出来） */
function mathPlain(s, n = 150) {
  const t = String(s || "").replace(/\$\$?/g, " ").replace(/\\[a-zA-Z]+/g, " ").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n) + "…" : t;
}

/* ---- 下拉选项（科目 → 专题是二级联动，专题清单随科目变） ---- */
function mathSubjectOpts(cur) {
  /* 模块隔离：每个入口只能建 / 筛自己科目的题 ——
     在信号页里不该能选「高等数学」，否则又混回去了 */
  const m = MATH.meta || {};
  const subs = m.module_subjects || m.subjects || [];
  return `<option value="">未分类</option>` +
    subs.map(s => `<option value="${esc(s)}"${s === cur ? " selected" : ""}>${esc(s)}</option>`).join("");
}
function mathTopicOpts(subject, cur) {
  const m = MATH.meta || {};
  const map = m.topics || {};
  /* 用户 2026-10-15：「数学的各项专题拉下列表没有」。
     原因是这里以前**只**认「科目」：科目默认是「未分类」（value=""），
     于是 map[""] 是 undefined → 下拉里只剩「未选专题」一项，看着像坏了。
     现在改成：没选科目时，把本入口的**所有专题按科目分组**列全（optgroup）；
     选了科目就只列那一科 —— 二级联动的意思保留，但下拉永远有东西可选。
     只按专题筛也行：后端 list_problems(topic=...) 不带科目一样能查。 */
  const subs = subject
    ? [subject]
    : (m.module_subjects || m.subjects || []).filter(s => (map[s] || []).length);
  const groups = subs.filter(s => (map[s] || []).length);
  const head = `<option value="">未选专题</option>`;
  if (!groups.length) return head;
  if (groups.length === 1) {
    return head + map[groups[0]]
      .map(t => `<option value="${esc(t)}"${t === cur ? " selected" : ""}>${esc(t)}</option>`).join("");
  }
  return head + groups.map(s =>
    `<optgroup label="${esc(s)}">` +
    map[s].map(t => `<option value="${esc(t)}"${t === cur ? " selected" : ""}>${esc(t)}</option>`).join("") +
    `</optgroup>`).join("");
}
function mathKindOpts(cur) {
  const k = (MATH.meta && MATH.meta.kinds) || { normal: "普通错题", exam: "考研真题错题" };
  return Object.keys(k).map(x =>
    `<option value="${x}"${x === (cur || "normal") ? " selected" : ""}>${esc(k[x])}</option>`).join("");
}
function mathKindLabel(v) {
  const k = (MATH.meta && MATH.meta.kinds) || { normal: "普通错题", exam: "考研真题错题" };
  return k[v] || k.normal;
}

function mathStatusChip(it) {
  if (it.mastered) return `<span class="chip accent">已掌握</span>`;
  if (it.in_cycle) return `<span class="chip">待复习 · ${esc(it.next_review || "—")}</span>`;
  return `<span class="chip">已移出复习</span>`;
}
function mathChips(it) {
  const parts = [];
  if (it.subject) parts.push(`<span class="chip accent">${esc(it.subject)}</span>`);
  if (it.topic) parts.push(`<span class="chip">${esc(it.topic)}</span>`);
  parts.push(`<span class="chip${it.kind === "exam" ? " exam-chip" : ""}">${esc(it.kind_label || "")}</span>`);
  parts.push(mathStatusChip(it));
  /* 原来这里挂的是「未解析」——那是要生成解析的年代留下的。
     用户 2026-10-12 定了不要解析，所以现在值得提醒的是**缺答案**（缺了就没法对错）。 */
  if (!String(it.answer || "").trim()) parts.push(`<span class="chip warn-chip">缺答案</span>`);
  return parts.join("");
}

/* ---- 客户端把手机大图压一压再上传：原图 4~8MB 直接发又慢又费 token ----
   ⚠️ 这里的清晰度直接决定识图准不准，别为了省流量往下调：
   数学题的下标、指根号上的小数字、矩阵元素本来就细，一整页 30 行时
     1600px → 一行约 40px、下标只剩约 13px（再叠 JPEG 噪点）→ 模型必然看错、漏字
     2560px → 一行约 64px、下标约 21px，才够用
   服务端上限是 12MB，这里自己再设 4MB 上限（Cloudflare 对上传还有 30 秒写入超时，
   图太大在移动网络下会白传），超了就自动降质量再来一次，绝不直接失败。 */
const MATH_SHRINK = { maxSide: 2560, quality: 0.92, maxBytes: 4 * 1024 * 1024 };

function mathShrink(file, maxSide = MATH_SHRINK.maxSide, quality = MATH_SHRINK.quality) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      try {
        let w = img.naturalWidth, h = img.naturalHeight;
        const scale = Math.min(1, maxSide / Math.max(w, h));
        w = Math.max(1, Math.round(w * scale));
        h = Math.max(1, Math.round(h * scale));
        const cv = document.createElement("canvas");
        cv.width = w; cv.height = h;
        cv.getContext("2d").drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        /* dataURL 的字节数 ≈ 长度 × 3/4；超上限就一档一档降质量，
           还超就按比例缩边（宁可小一点，也不能让用户卡在上传上）。 */
        let data = cv.toDataURL("image/jpeg", quality);
        const bytes = () => Math.round((data.length - data.indexOf(",") - 1) * 3 / 4);
        let q = quality;
        while (bytes() > MATH_SHRINK.maxBytes && q > 0.6) {
          q = Math.round((q - 0.08) * 100) / 100;
          data = cv.toDataURL("image/jpeg", q);
        }
        if (bytes() > MATH_SHRINK.maxBytes) {
          const cv2 = document.createElement("canvas");
          const k = Math.sqrt(MATH_SHRINK.maxBytes / bytes());
          cv2.width = Math.max(1, Math.round(w * k));
          cv2.height = Math.max(1, Math.round(h * k));
          cv2.getContext("2d").drawImage(cv, 0, 0, cv2.width, cv2.height);
          data = cv2.toDataURL("image/jpeg", 0.86);
        }
        resolve(data);
      } catch (e) { URL.revokeObjectURL(url); reject(e); }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      /* ⚠️ 浏览器解不开这张图时**不要直接判死**。
         iPhone 在个别路径下会给出浏览器不认识的 HEIC，`new Image()` 直接 onerror ——
         以前就弹一句「这张图片读不出来」把路堵死。
         改成：原样读成 dataURL 交给服务端，让模型那边再试一次
         （服务端 `_DATA_URL_RE` 允许 image/heic 这种子类型）。
         能认出来最好；认不出来至少报的是模型的错，不是我们这边先卡住。
         ⚠️ 必须带上限：没压缩过的原图可能几 MB 甚至十几 MB，
            服务端 `_IMG_MAX_BYTES` 是 12MB，超了白传。 */
      if (file.size > 8 * 1024 * 1024) {
        reject(new Error("这张照片读不出来（可能是不支持的格式），换一张或改用相册导入试试"));
        return;
      }
      const fr = new FileReader();
      fr.onload = () => {
        const s = String(fr.result || "");
        if (s) resolve(s);
        else reject(new Error("这张图片读不出来，换一张试试"));
      };
      fr.onerror = () => reject(new Error("这张图片读不出来，换一张试试"));
      fr.readAsDataURL(file);
    };
    img.src = url;
  });
}

function setMathBadge(n) {
  /* 这个角标挂在英语顶栏的 🎓 上 —— 它**只代表数学模块**的今日待复习数。
     信号与系统的到期数不许出现在这里（2026-10-08 用户要求模块互不串）。 */
  const b = $("#mathBadge");
  if (!b) return;
  const v = Number(n) || 0;
  b.textContent = v > 99 ? "99+" : String(v);
  b.hidden = v <= 0;
}

function setMathLibBadge(n) {
  /* 数学/信号顶栏「错题总库」上的角标 = **当前模块**的今日待复习数。
     两个模块共用这个页面，所以它跟着 MATH.module 走。 */
  const b = $("#mathLibBadge");
  if (!b) return;
  const v = Number(n) || 0;
  b.textContent = v > 99 ? "99+" : String(v);
  b.hidden = v <= 0;
  const btn = $("#btnMathLib");
  if (btn) btn.title = v > 0
    ? `错题总库：今天有 ${v} 道该复习（${MATH.module === "sig" ? "信号与系统" : "数学"}）`
    : "错题总库：每道错题 + 它对应的知识点";
}

/* 顶栏那个科目入口上的角标 = 今天该复习几道错题。拿不到就当 0，别打断首屏。
   ⚠️ 固定取 **math 模块**：🎓 挂在英语顶栏上，只许显示数学的到期数；
   就算用户上次停留在信号页（MATH.module==="sig"），也不能把信号数带过来。 */
async function refreshMathBadge() {
  try {
    const d = await api("/api/math/review/due?module=math");
    setMathBadge((d.items || []).length);
  } catch { /* 未登录 / 服务抖动都不显示 */ }
}

/* 就地做完一道错题之后，把角标数字按本地列表同步一下。
   为什么不重新请求：为了两个数字去拉整页（列表 + meta + due 三个接口），
   在手机上就是一次明显的卡顿；而 MATH.due 已经是我们自己删过的最新状态了。 */
function bumpMathDueCount() {
  const n = (MATH.due || []).length;
  setMathLibBadge(n);
  if (MATH.module === "math") setMathBadge(n);
}

/* ==========================================================
   错题列表
   ========================================================== */
async function loadMath() {
  const box = T();
  const module = MATH.module;
  const request = ++MATH.loadSeq;
  if (!box.innerHTML.trim()) box.innerHTML = `<p class="muted small">加载中…</p>`;
  try {
    const qs = new URLSearchParams();
    Object.entries(MATH.f).forEach(([k, v]) => { if (v) qs.set(k, v); });
    qs.set("with_analysis", "1");
    qs.set("module", module);        // ⚠️ 模块隔离：另一边的科目不许漏进来
    const [meta, list, due] = await Promise.all([
      api("/api/math/meta?module=" + encodeURIComponent(module)),
      api("/api/math/problems?" + qs.toString()),
      api("/api/math/review/due?module=" + encodeURIComponent(module)),
    ]);
    if (request !== MATH.loadSeq || currentView !== "math" || MATH.module !== module) return;
    MATH.meta = meta;
    MATH.items = list.items || [];
    MATH.due = due.items || [];
    /* 周末休息：周六/周日后端给空队列 + rest 标记（2026-10-19 用户口径） */
    MATH.rest = !!(due.rest || (meta.stats && meta.stats.rest));
    setMathLibBadge(MATH.due.length);                 // 页内角标 = 当前模块
    if (module === "math") setMathBadge(MATH.due.length);  // 🎓 只认数学，别被信号改掉
    renderMath();
  } catch (e) {
    if (request === MATH.loadSeq && currentView === "math" && MATH.module === module) {
      box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
    }
  }
}

function renderMath() {
  /* 标题跟着模块走：两个模块共用一个页面，标题必须让人一眼知道自己在哪一侧 */
  const mt = $("#mathTitle");
  if (mt) {
    mt.textContent = MATH.f.subject
      ? `${MATH.f.subject} · 错题库`
      : (MATH.module === "sig" ? "信号与系统错题库" : "数学错题库");
  }
  /* stats 已经是**按模块过滤后**的（后端 stats(module)），总量/到期/已掌握天然只算本模块 */
  const st = (MATH.meta && MATH.meta.stats) || {};
  const bs = st.by_subject || {};
  const chipKey = MATH.module === "sig" ? "信号与系统" : "高等数学";
  const chipLabel = MATH.module === "sig" ? "信号" : "高数";
  const head = `
    <div class="lib-summary">
      <div class="lib-sum-item"><b>${st.total || 0}</b><span>错题总量</span></div>
      <div class="lib-sum-item"><b data-m-stat="due">${st.due || 0}</b><span>今日待复习</span></div>
      <div class="lib-sum-item"><b data-m-stat="mastered">${st.mastered || 0}</b><span>已掌握</span></div>
      <div class="lib-sum-item"><b>${bs[chipKey] || 0}</b><span>${chipLabel}</span></div>
    </div>
    <div class="math-toolbar">
      <button class="btn primary" id="mathAddBtn">＋ 拍照录题（可多张）</button>
      <button class="btn" id="mathManualBtn">手动录题</button>
    </div>
    ${st.with_image ? `<div class="math-imgbar">
      <span class="muted small">库里还留着 ${st.with_image} 张原题照片（现在录题已经不留图了）</span>
      <button class="btn tiny" id="mathPurgeImg">清理掉，释放空间</button>
    </div>` : ""}
    <div id="mathDraftWrap"></div>
    <div id="mathDrillWrap"></div>
    <div class="field-row math-filter">
      <label class="field"><span>科目</span>
        <select id="mfSubject">${mathSubjectOpts(MATH.f.subject)}</select></label>
      <label class="field"><span>专题</span>
        <select id="mfTopic">${mathTopicOpts(MATH.f.subject, MATH.f.topic)}</select></label>
      <label class="field"><span>类型</span>
        <select id="mfKind"><option value="">全部</option>${mathKindOpts(MATH.f.kind)}</select></label>
    </div>
    <div class="field-row math-search-row">
      <label class="field"><span>复习状态</span>
        <select id="mfCycle">
          <option value="">全部</option>
          <option value="due"${MATH.f.cycle === "due" ? " selected" : ""}>今天该复习</option>
          <option value="in"${MATH.f.cycle === "in" ? " selected" : ""}>在复习周期</option>
          <option value="mastered"${MATH.f.cycle === "mastered" ? " selected" : ""}>已掌握</option>
          <option value="out"${MATH.f.cycle === "out" ? " selected" : ""}>已移出</option>
        </select></label>
      <label class="field"><span>搜索题干 / 知识点 / 解法</span>
        <input class="field-input" id="mfQ" value="${esc(MATH.f.q)}" placeholder="例如：特征值、中值定理、傅里叶变换"></label>
    </div>`;

  const list = MATH.items.length
    ? MATH.items.map(mathCard).join("")
    : `<p class="center muted" style="margin:26px 0">还没有错题。点「＋ 拍照录题」，一次可以丢好几张照片进来。</p>`;

  $("#pageBody-math").innerHTML = head + `<div class="math-list">${list}</div>`;
  /* 草案区 / 刷题区是独立容器，renderMath 会把它们一起换掉，
     所以这里必须补画一次 —— 否则切个筛选就把正在刷的题弄没了。 */
  renderDraft();
  renderDrill();
  markClampedMath();
}

/* 折叠题干的渐隐条只在**真的被截断**时才盖上去。
   否则那条 2.2em 的渐变会糊在最后一行上 —— 遇到 \dfrac 这种高公式，
   正好把分母洗掉，看着像渲染坏了（2026-10-07 取证时发现）。 */
function markClampedMath() {
  document.querySelectorAll(".math-stem.clamp").forEach(el => {
    el.classList.toggle("is-clamped", el.scrollHeight > el.clientHeight + 2);
  });
}

/* 库里的每道题都该看得见「它对的是什么知识点」（用户 2026-10-07 口径：
   「错题总库就是这道题和它所对应的知识点」）—— 所以别再藏在「展开」里。 */
function mathKpHtml(it) {
  if (!it.knowledge) return "";
  return `<div class="math-kp"><span class="kp-tag">知识点</span><div class="kp-content">${mathMtex(it.knowledge)}</div></div>`;
}

function mathCard(it) {
  const open = MATH.openId === it.id;
  const img = it.has_image
    ? `<img class="math-photo" src="/api/math/problems/${it.id}/image" alt="原题照片" loading="lazy">`
    : "";

  /* 答案直接读 it.answer（独立列）。
     ⚠️ 以前是从 solution 里正则抠「答案：」——那是把答案和解法混在一列时期的临时办法，
     用户 2026-10-12 定了「只要最终答案、不要解析」，答案已经有自己的列了。 */
  /* 就地编辑：改题干 / 改答案。
   为什么必须有：答案是 AI 算出来的，偶尔会错（或者题目残缺算不出）。
   如果只有「让 AI 算答案」而没有手改的入口，用户遇到错答案就卡住了。 */
  if (MATH.editId === it.id) {
    return `<div class="math-edit">
      <label class="field"><span>最终答案（只要答案，不要过程）</span>
        <input class="field-input" id="meAnswer" value="${esc(it.answer || "")}"
          placeholder="例如：$x=2$ 或 A"></label>
      <div class="math-actions">
        <button class="btn tiny primary" data-m-edit-save="${it.id}">保存</button>
        <button class="btn tiny" data-m-edit-cancel="1">取消</button>
      </div>
    </div>`;
  }
  const quickButtons = `
    <div class="math-quick-btns">
      <button class="btn small primary" data-m-quick="right" data-m-qid="${it.id}">✅ 会做了</button>
      <button class="btn small warnish" data-m-quick="wrong" data-m-qid="${it.id}">❌ 不会做</button>
    </div>`;

  return `
    <div class="card math-card${open ? " open" : ""}" data-m-id="${it.id}">
      <div class="math-chips">${mathChips(it)}</div>
      <div class="math-stem${open ? "" : " clamp"}" title="${esc(mathPlain(it.stem, 120))}">${mathMtex(it.stem)}</div>
      ${mathKpHtml(it)}
      ${mathAnswerBlock(it)}
      ${quickButtons}
      ${open ? img : ""}
      <div class="math-actions">
        <button class="btn tiny" data-m-toggle="${it.id}">${open ? "收起" : "展开"}</button>
        <button class="btn tiny" data-m-drill="${it.id}">刷这道</button>
        <button class="btn tiny" data-m-edit="${it.id}">改题干 / 答案</button>
        <button class="btn tiny warnish" data-m-del="${it.id}">删除</button>
      </div>
    </div>`;
}

/* ⚠️ 这里原先是 mathDetail()（推荐解法 / 关键步骤 / 知识点 / 易错点 一大块）。
   用户 2026-10-12 明确「我不希望有解析了 我觉得那样太费时间」，
   所以整个渲染函数删掉了。数据（solution / knowledge / key_steps …）仍留在库里——
   只是不再生成、不再显示；哪天想接回来，后端字段都还在。 */

/* ==========================================================
   录题：一次可放多张照片 → 并发识别 → AI 归类 → 逐张核对 → 全部保存
   ========================================================== */
const MATH_EXTRACT_PARALLEL = 2;   // 同时问模型几张；太高会被限流，太低又慢

function draftReady(d) {
  return !!(d.stem || "").trim() && d.status !== "saving" && d.status !== "saved";
}

function mathNewDraft(image) {
  MATH.draftSeq += 1;
  return {
    key: "d" + MATH.draftSeq,
    image: image || "",
    marker: null,
    stem: "",
    subject: MATH.f.subject || "",
    topic: "",
    kind: "normal",
    source_note: "",
    uncertain: [],
    status: image ? "new" : "manual", // manual / new / extracting / ready / error / saving / saved
    imageDropped: false,  // 识别成功后原图已释放（不再上传 / 不再占用存储）
    error: "",
    model: "",
    aiClassified: false,  // 分类是不是 AI 给的（用户手选的不能被标成「AI 归类」）
    touched: false,       // 用户手动改过分类 → AI 别再覆盖
  };
}

function mathMarkPaint() {
  const canvas = $("#mathMarkCanvas");
  canvas.querySelectorAll(".math-mark-point").forEach(el => el.remove());
  MATH.markPoints.forEach((point, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "math-mark-point";
    button.dataset.markIndex = String(index);
    button.style.left = `${point.x * 100}%`;
    button.style.top = `${point.y * 100}%`;
    button.textContent = String(index + 1);
    button.setAttribute("aria-label", `移除第 ${index + 1} 个题号标记`);
    button.title = "移除这个标记";
    canvas.append(button);
  });
  $("#mathMarkSelected").textContent = `已标记 ${MATH.markPoints.length} 道`;
  const confirm = $("#mathMarkConfirm");
  confirm.disabled = !MATH.markPoints.length || MATH.markBusy;
  confirm.textContent = MATH.markBusy ? "正在生成草稿…" : `识别所标记的题（${MATH.markPoints.length}）`;
  for (const id of ["mathMarkUndo", "mathMarkSkip", "mathMarkWhole", "mathMarkCancel"]) {
    $("#" + id).disabled = MATH.markBusy;
  }
  $("#mathMarkEdit").disabled = MATH.markBusy || !MATH.markImageReady || MATH.editBusy;
}

function mathMarkResize() {
  const img = $("#mathMarkImage");
  if (!img.naturalWidth) return;
  const stage = $("#mathMarkStage");
  const zoom = Number($("#mathMarkZoom").value);
  const baseWidth = Math.min(img.naturalWidth, stage.clientWidth - 24);
  $("#mathMarkCanvas").style.width = `${Math.round(baseWidth * zoom)}px`;
  $("#mathMarkZoomValue").textContent = `${Math.round(zoom * 100)}%`;
  mathEditDraw();
}

function mathEditSync() {
  const active = MATH.editActive;
  const canvas = $("#mathMarkCanvas");
  canvas.classList.toggle("is-editing", active);
  canvas.classList.toggle("is-drawing", active && MATH.editTool === "draw");
  canvas.classList.toggle("is-cropping", active && MATH.editTool === "crop");
  $("#mathMarkTitle").textContent = active ? "编辑照片" : "标记要录入的题号";
  $("#mathMarkActions").hidden = active;
  $("#mathMarkSelected").hidden = active;
  $("#mathEditTools").hidden = !active;
  $("#mathMarkEdit").hidden = active;
  $("#mathEditCrop").disabled = MATH.editBusy || !MATH.editCrop || !MATH.markImageReady;
  $("#mathEditUndo").disabled = MATH.editBusy || !MATH.editHistory.length;
  for (const id of ["mathEditRotateLeft", "mathEditRotateRight", "mathEditDone", "mathEditCancel"]) {
    $("#" + id).disabled = MATH.editBusy || !MATH.markImageReady;
  }
  for (const button of document.querySelectorAll("[data-photo-tool]")) {
    button.setAttribute("aria-pressed", String(button.dataset.photoTool === MATH.editTool));
    button.disabled = MATH.editBusy;
  }
  for (const button of document.querySelectorAll("[data-photo-color]")) {
    button.setAttribute("aria-pressed", String(button.dataset.photoColor === MATH.editColor));
    button.disabled = MATH.editBusy || MATH.editTool !== "draw";
  }
  $("#mathEditWidth").disabled = MATH.editBusy || MATH.editTool !== "draw";
  mathEditDraw();
}

function mathEditPaintStrokes(ctx, width, height, strokes) {
  for (const stroke of strokes) {
    if (!stroke.points.length) continue;
    ctx.beginPath();
    ctx.strokeStyle = stroke.color;
    ctx.fillStyle = stroke.color;
    ctx.lineWidth = Math.max(2, stroke.width * width / 1000);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const first = stroke.points[0];
    ctx.moveTo(first.x * width, first.y * height);
    for (const point of stroke.points.slice(1)) ctx.lineTo(point.x * width, point.y * height);
    if (stroke.points.length === 1) {
      ctx.arc(first.x * width, first.y * height, ctx.lineWidth / 2, 0, Math.PI * 2);
      ctx.fill();
    } else ctx.stroke();
  }
}

function mathEditDraw() {
  const overlay = $("#mathEditOverlay");
  const image = $("#mathMarkImage");
  if (!image.naturalWidth) return;
  if (overlay.width !== image.naturalWidth || overlay.height !== image.naturalHeight) {
    overlay.width = image.naturalWidth;
    overlay.height = image.naturalHeight;
  }
  const ctx = overlay.getContext("2d");
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  if (!MATH.editActive) return;
  mathEditPaintStrokes(ctx, overlay.width, overlay.height,
    MATH.editLive ? [...MATH.editStrokes, MATH.editLive] : MATH.editStrokes);
  if (MATH.editCrop) {
    const a = MATH.editCrop.start, b = MATH.editCrop.end;
    const x = Math.min(a.x, b.x) * overlay.width;
    const y = Math.min(a.y, b.y) * overlay.height;
    const w = Math.abs(a.x - b.x) * overlay.width;
    const h = Math.abs(a.y - b.y) * overlay.height;
    ctx.fillStyle = "rgba(0,0,0,.42)";
    ctx.fillRect(0, 0, overlay.width, y);
    ctx.fillRect(0, y, x, h);
    ctx.fillRect(x + w, y, overlay.width - x - w, h);
    ctx.fillRect(0, y + h, overlay.width, overlay.height - y - h);
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = Math.max(3, overlay.width / 400);
    ctx.strokeRect(x, y, w, h);
  }
}

function mathEditSnapshot() {
  MATH.editHistory.push({ image: MATH.markCurrent.image, strokes: structuredClone(MATH.editStrokes) });
  if (MATH.editHistory.length > 10) MATH.editHistory.shift();
}

function mathEditOpen() {
  if (!MATH.markImageReady || MATH.editBusy) return;
  if (MATH.markPoints.length) { toast("请先撤销题号标记，再编辑照片", true); return; }
  MATH.editOriginal = MATH.markCurrent.image;
  MATH.editActive = true;
  MATH.editTool = "draw";
  MATH.editStrokes = [];
  MATH.editLive = null;
  MATH.editCrop = null;
  MATH.editHistory = [];
  mathEditSync();
}

function mathEditStop() {
  MATH.editActive = false;
  MATH.editStrokes = [];
  MATH.editLive = null;
  MATH.editCrop = null;
  MATH.editHistory = [];
  MATH.editOriginal = "";
  mathEditSync();
  mathMarkPaint();
}

function mathEditCancel() {
  if (!MATH.editActive || MATH.editBusy) return;
  if (MATH.markCurrent.image !== MATH.editOriginal) {
    MATH.markCurrent.image = MATH.editOriginal;
    MATH.markImageReady = false;
    $("#mathMarkImage").src = MATH.editOriginal;
  }
  mathEditStop();
}

async function mathEditComposite() {
  const image = new Image();
  image.src = MATH.markCurrent.image;
  await image.decode();
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(image, 0, 0);
  mathEditPaintStrokes(ctx, canvas.width, canvas.height, MATH.editStrokes);
  return canvas;
}

async function mathEditTransform(kind) {
  if (!MATH.editActive || MATH.editBusy || !MATH.markImageReady) return;
  const current = MATH.markCurrent;
  const crop = MATH.editCrop;
  if (kind === "crop") {
    if (!crop) return;
    if (Math.abs(crop.start.x - crop.end.x) < .08 || Math.abs(crop.start.y - crop.end.y) < .08) {
      toast("裁剪范围太小，请重新框选", true);
      return;
    }
  }
  MATH.editBusy = true;
  mathEditSync();
  try {
    const source = await mathEditComposite();
    if (MATH.markCurrent !== current) return;
    const result = document.createElement("canvas");
    const ctx = result.getContext("2d");
    if (kind === "crop") {
      const x = Math.floor(Math.min(crop.start.x, crop.end.x) * source.width);
      const y = Math.floor(Math.min(crop.start.y, crop.end.y) * source.height);
      const width = Math.max(1, Math.ceil(Math.abs(crop.start.x - crop.end.x) * source.width));
      const height = Math.max(1, Math.ceil(Math.abs(crop.start.y - crop.end.y) * source.height));
      result.width = width;
      result.height = height;
      ctx.drawImage(source, x, y, width, height, 0, 0, width, height);
    } else {
      result.width = source.height;
      result.height = source.width;
      ctx.translate(result.width / 2, result.height / 2);
      ctx.rotate(kind === "right" ? Math.PI / 2 : -Math.PI / 2);
      ctx.drawImage(source, -source.width / 2, -source.height / 2);
    }
    const updated = result.toDataURL("image/jpeg", .9);
    mathEditSnapshot();
    current.image = updated;
    MATH.editStrokes = [];
    MATH.editCrop = null;
    MATH.markImageReady = false;
    $("#mathMarkImage").src = updated;
  } catch (error) { toast("编辑照片失败：" + error.message, true); }
  finally { MATH.editBusy = false; mathEditSync(); }
}

async function mathEditDone() {
  if (!MATH.editActive || MATH.editBusy || !MATH.markImageReady) return;
  const current = MATH.markCurrent;
  MATH.editBusy = true;
  mathEditSync();
  try {
    if (MATH.editStrokes.length) {
      const canvas = await mathEditComposite();
      if (MATH.markCurrent !== current) return;
      current.image = canvas.toDataURL("image/jpeg", .9);
      MATH.markImageReady = false;
      $("#mathMarkImage").src = current.image;
    }
    mathEditStop();
  } catch (error) { toast("保存照片批注失败：" + error.message, true); }
  finally { MATH.editBusy = false; mathEditSync(); mathMarkPaint(); }
}

function mathMarkClose(force = false) {
  if (MATH.markBusy && !force) return;
  MATH.markQueue = [];
  MATH.markCurrent = null;
  MATH.markPoints = [];
  MATH.markIndex = 0;
  MATH.markImageReady = false;
  MATH.editActive = false;
  MATH.editStrokes = [];
  MATH.editHistory = [];
  MATH.editOriginal = "";
  mathEditSync();
  $("#mathMarkImage").removeAttribute("src");
  $("#mathMarker").hidden = true;
  document.body.style.overflow = MATH.markPrevOverflow;
}

function mathMarkNext() {
  const next = MATH.markQueue.shift();
  if (!next) { mathMarkClose(); return; }
  MATH.markCurrent = next;
  MATH.markPoints = [];
  MATH.markImageReady = false;
  MATH.editActive = false;
  MATH.editStrokes = [];
  MATH.editHistory = [];
  MATH.editOriginal = "";
  mathEditSync();
  MATH.markIndex += 1;
  const modal = $("#mathMarker");
  if (modal.hidden) {
    MATH.markPrevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    modal.hidden = false;
  }
  $("#mathMarkCount").textContent = `第 ${MATH.markIndex} / ${MATH.markIndex + MATH.markQueue.length} 张照片`;
  $("#mathMarkZoom").value = "1";
  $("#mathMarkCanvas").style.width = "";
  $("#mathMarkImage").src = next.image;
  mathMarkPaint();
}

function mathMarkedImage(source, point) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);
        const size = Math.min(canvas.width, canvas.height);
        const x = point.x * canvas.width, y = point.y * canvas.height;
        ctx.beginPath();
        ctx.arc(x, y, Math.max(28, size * .04), 0, Math.PI * 2);
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = Math.max(9, size * .01);
        ctx.stroke();
        ctx.strokeStyle = "#087cff";
        ctx.lineWidth = Math.max(5, size * .006);
        ctx.stroke();
        resolve(canvas.toDataURL("image/jpeg", .9));
      } catch (error) { reject(error); }
    };
    img.onerror = () => reject(new Error("这张照片无法标记，请尝试识别整张或换一张 JPG 照片"));
    img.src = source;
  });
}

$("#mathMarkImage").addEventListener("load", () => {
  if (!MATH.markCurrent) return;
  MATH.markImageReady = true;
  mathMarkResize();
  mathEditSync();
  mathMarkPaint();
});
$("#mathMarkImage").addEventListener("error", () => {
  if (!MATH.markCurrent) return;
  MATH.markImageReady = false;
  toast("照片无法在浏览器中显示，可识别整张或换一张 JPG 照片", true);
});
const mathEditOverlay = $("#mathEditOverlay");
function mathEditPoint(event) {
  const rect = mathEditOverlay.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  };
}
mathEditOverlay.addEventListener("pointerdown", event => {
  if (!MATH.editActive || MATH.editBusy || MATH.editTool === "pan") return;
  event.preventDefault();
  event.stopPropagation();
  mathEditOverlay.setPointerCapture(event.pointerId);
  const point = mathEditPoint(event);
  if (MATH.editTool === "draw") {
    mathEditSnapshot();
    MATH.editLive = { color: MATH.editColor, width: MATH.editWidth, points: [point] };
  } else {
    MATH.editCrop = { start: point, end: point };
  }
  mathEditSync();
});
mathEditOverlay.addEventListener("pointermove", event => {
  if (!mathEditOverlay.hasPointerCapture(event.pointerId)) return;
  const point = mathEditPoint(event);
  if (MATH.editLive) {
    if (MATH.editLive.points.length < 1600) MATH.editLive.points.push(point);
  } else if (MATH.editCrop && MATH.editTool === "crop") MATH.editCrop.end = point;
  mathEditDraw();
});
function mathEditPointerEnd(event) {
  if (!mathEditOverlay.hasPointerCapture(event.pointerId)) return;
  if (MATH.editLive) {
    MATH.editLive.points.push(mathEditPoint(event));
    MATH.editStrokes.push(MATH.editLive);
    MATH.editLive = null;
  } else if (MATH.editCrop && MATH.editTool === "crop") {
    MATH.editCrop.end = mathEditPoint(event);
  }
  mathEditSync();
}
mathEditOverlay.addEventListener("pointerup", mathEditPointerEnd);
mathEditOverlay.addEventListener("pointercancel", mathEditPointerEnd);
mathEditOverlay.addEventListener("click", event => event.stopPropagation());
$("#mathMarkEdit").addEventListener("click", mathEditOpen);
$("#mathEditCancel").addEventListener("click", mathEditCancel);
$("#mathEditDone").addEventListener("click", mathEditDone);
$("#mathEditRotateLeft").addEventListener("click", () => mathEditTransform("left"));
$("#mathEditRotateRight").addEventListener("click", () => mathEditTransform("right"));
$("#mathEditCrop").addEventListener("click", () => mathEditTransform("crop"));
$("#mathEditUndo").addEventListener("click", () => {
  if (!MATH.editActive || MATH.editBusy || !MATH.editHistory.length) return;
  const previous = MATH.editHistory.pop();
  MATH.editStrokes = previous.strokes;
  MATH.editCrop = null;
  if (MATH.markCurrent.image !== previous.image) {
    MATH.markCurrent.image = previous.image;
    MATH.markImageReady = false;
    $("#mathMarkImage").src = previous.image;
  }
  mathEditSync();
});
for (const button of document.querySelectorAll("[data-photo-tool]")) {
  button.addEventListener("click", () => {
    MATH.editTool = button.dataset.photoTool;
    if (MATH.editTool !== "crop") MATH.editCrop = null;
    mathEditSync();
  });
}
for (const button of document.querySelectorAll("[data-photo-color]")) {
  button.addEventListener("click", () => { MATH.editColor = button.dataset.photoColor; mathEditSync(); });
}
$("#mathEditWidth").addEventListener("input", event => { MATH.editWidth = Number(event.target.value); });
$("#mathMarkCanvas").addEventListener("click", event => {
  if (MATH.markBusy || MATH.editActive) return;
  const existing = event.target.closest(".math-mark-point");
  const existingRect = existing?.getBoundingClientRect();
  const removeHit = existingRect && Math.hypot(
    event.clientX - (existingRect.left + existingRect.width / 2),
    event.clientY - (existingRect.top + existingRect.height / 2),
  ) <= 10;
  if (removeHit) {
    MATH.markPoints.splice(Number(existing.dataset.markIndex), 1);
  } else if (MATH.markImageReady) {
    if (MATH.markPoints.length >= 12) { toast("一张照片最多标记 12 道题", true); return; }
    const rect = $("#mathMarkImage").getBoundingClientRect();
    MATH.markPoints.push({
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    });
  }
  mathMarkPaint();
});
$("#mathMarkZoom").addEventListener("input", mathMarkResize);
$("#mathMarkUndo").addEventListener("click", () => { if (!MATH.markBusy) { MATH.markPoints.pop(); mathMarkPaint(); } });
$("#mathMarkSkip").addEventListener("click", () => { if (!MATH.markBusy) mathMarkNext(); });
$("#mathMarkCancel").addEventListener("click", () => mathMarkClose());
$("#mathMarkWhole").addEventListener("click", () => {
  if (MATH.markBusy) return;
  const draft = mathNewDraft(MATH.markCurrent.image);
  MATH.drafts.push(draft);
  renderDraft();
  mathRunExtract();
  mathMarkNext();
});
$("#mathMarkConfirm").addEventListener("click", async () => {
  if (!MATH.markPoints.length || !MATH.markImageReady || MATH.markBusy) return;
  const current = MATH.markCurrent;
  const source = current.image;
  const points = MATH.markPoints.slice();
  MATH.markBusy = true;
  mathMarkPaint();
  try {
    const images = [];
    for (const point of points) {
      images.push(await mathMarkedImage(source, point));
      if (MATH.markCurrent !== current) return;
    }
    if (MATH.markCurrent !== current) return;
    images.forEach((image, index) => {
      const draft = mathNewDraft(image);
      draft.marker = points[index];
      MATH.drafts.push(draft);
    });
    renderDraft();
    mathRunExtract();
    MATH.markBusy = false;
    mathMarkNext();
  } catch (error) { toast(error.message, true); }
  finally {
    MATH.markBusy = false;
    if (MATH.markCurrent) mathMarkPaint();
  }
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("#mathMarker").hidden) {
    if (MATH.editActive) mathEditCancel();
    else mathMarkClose();
  }
});
window.addEventListener("resize", () => {
  if (!$("#mathMarker").hidden) mathMarkResize();
}, { passive: true });

async function mathAddPhotos(files) {
  /* 个别 iOS 路径给出的照片 type 是空串 —— 别把它当非图片丢掉
     （input 上已经有 accept="image/*" 兜着，选不到别的） */
  const list = Array.from(files || []).filter(f => !f.type || f.type.startsWith("image/"));
  if (!list.length) return;
  MATH.draftOpen = true;
  renderDraft();
  for (const f of list) {
    try {
      MATH.markQueue.push({ image: await mathShrink(f) });
    } catch (e) { toast(e.message, true); }
  }
  if (!MATH.markCurrent && MATH.markQueue.length) mathMarkNext();
}

/* 并发池：两个 worker 抢「还没识别」的草稿，谁抢到谁去问模型 */
async function mathRunExtract() {
  if (MATH.extractRunning) return;
  if (!MATH.drafts.some(d => d.status === "new" && d.image)) return;
  MATH.extractRunning = true;
  try {
    while (MATH.drafts.some(d => d.status === "new" && d.image)) {
      const workers = [];
      for (let i = 0; i < MATH_EXTRACT_PARALLEL; i++) workers.push(mathExtractWorker());
      await Promise.all(workers);
    }
    const ok = MATH.drafts.filter(d => d.status === "ready").length;
    const bad = MATH.drafts.filter(d => d.status === "error").length;
    if (ok || bad) toast(`识别完成：${ok} 道成功${bad ? `，${bad} 道失败` : ""}，请核对后保存`, !!bad);
  } finally {
    MATH.extractRunning = false;
    if (MATH.drafts.some(d => d.status === "new" && d.image)) mathRunExtract();
  }
}

async function mathExtractWorker() {
  for (;;) {
    const d = MATH.drafts.find(x => x.status === "new" && x.image);
    if (!d) return;
    d.status = "extracting";
    paintDraftCard(d.key);
    try {
      const r = await api("/api/math/extract", {
        method: "POST",
        body: JSON.stringify({ image: d.image, subject: d.subject || "", topic: d.topic || "", marker: d.marker }),
      });
      d.stem = r.stem || "";
      d.answer = r.answer || "";  // 保存提取的答案
      // AI 归类：用户没手动动过分类时才覆盖，别把人改的又冲掉
      if (!d.touched) {
        d.subject = r.subject || d.subject || "";
        d.topic = r.topic || "";
        d.kind = r.kind || "normal";
        /* 模块隔离：在信号页录的题，AI 看走眼认成数学也不收 ——
           强制回到信号科目；它配的专题若不在信号树里就丢掉，别带进数学的树 */
        if (MATH.module === "sig") {
          const okTopics = ((MATH.meta || {}).topics || {})["信号与系统"] || [];
          if (d.subject !== "信号与系统") { d.subject = "信号与系统"; d.topic = ""; }
          else if (d.topic && !okTopics.includes(d.topic)) d.topic = "";
        }
      }
      d.uncertain = r.uncertain || [];
      d.model = r.model || "";
      d.aiClassified = true;
      d.status = "ready";
      d.error = "";
      /* ⭐ 题干已经拿到手，原图就没用了 —— 立刻释放。
         用户 2026-10-07 明确要求「识图了以后就不用再保留图片了，节省存储空间」：
         这里清掉内存里的 base64（缩略图跟着消失），保存时也不再上传。
         ⚠️ 只在成功时清：识别失败要留着图给「重新识别」用。 */
      d.image = "";
      d.imageDropped = true;
    } catch (e) {
      d.status = "error";
      d.error = e.message;
    }
    paintDraftCard(d.key);
  }
}

function draftStatusHtml(d) {
  const map = {
    manual:     ["", "手动录入"],
    new:        ["", "待识别"],
    extracting: ["busy", "识别中…"],
    ready:      ["ok", "已识别"],
    error:      ["err", "识别失败"],
    saving:     ["busy", "保存中…"],
    saved:      ["ok", "已保存"],
  };
  const s = map[d.status] || map.new;
  return `<span class="draft-badge ${s[0]}">${s[1]}</span>`;
}

/* AI 归类要「显著」：单独一行摆出来，下面是可改的下拉 */
function aiClassifyHtml(d) {
  if (d.status === "extracting") return `<div class="ai-class muted">AI 正在读题并归类…</div>`;
  const parts = [];
  if (d.subject) parts.push(d.subject);
  if (d.topic) parts.push(d.topic);
  if (d.kind) parts.push(mathKindLabel(d.kind));
  if (!parts.length) {
    return `<div class="ai-class muted">AI 没能判断出分类（照片可能不够清楚），自己选一下就行</div>`;
  }
  const chips = parts.map((p, i) =>
    `<span class="chip${i === 0 ? " accent" : ""}">${esc(p)}</span>`).join("");
  // 文案要对得上事实：不是 AI 给的就别写「AI 归类」
  if (!d.aiClassified) return `<div class="ai-class muted"><b>当前分类</b>${chips}</div>`;
  return `<div class="ai-class"><b>AI 归类</b>${chips}</div>`;
}

/* 确认录入时**必须给一份排版好的预览**。
   下面的文本框里存的是 LaTeX（要能改），可用户看不懂源码 ——
   他的原话就是「你给我的这是代码，我要能直接看明白的公式」。
   所以「能读的」放上面（渲染好），「能改的」放下面（文本框），两样都要。 */
function draftPreviewHtml(d) {
  const body = d.stem
    ? mathMtex(d.stem)
    : `<span class="muted">${d.status === "manual" ? "输入题干后会显示在这里" : "识别完会自动显示在这里"}</span>`;
  return `<div class="draft-preview-wrap">
      <div class="draft-preview-label">题干预览（公式已排版）</div>
      <div class="draft-preview">${body}</div>
    </div>`;
}

/* 草稿卡顶部：缩略图 + 状态 + 工具按钮。
   识别成功后原图被释放（d.image 清空）→ 缩略图和「重新识别」一起消失，
   所以两个元素都按 d.image 判断，别写死。 */
function draftTopHtml(d) {
  return `
        ${d.image ? `<img class="draft-thumb" src="${d.image}" alt="题目照片">` : ""}
        <div class="draft-top-main">
          <div class="draft-head">${draftStatusHtml(d)}
            ${d.model ? `<span class="muted tiny">${esc(d.model)}</span>` : ""}</div>
          <div class="draft-tools">
            ${d.image ? `<button class="btn tiny" data-d-re="1">重新识别</button>` : ""}
            <button class="btn tiny warnish" data-d-del="1">移除</button>
          </div>
        </div>`;
}

/* 原图释放后要说一句 —— 否则用户会以为「我拍的照片怎么没了」。
   用 hidden 而不是删节点：局部重绘时要能再切回来。 */
function droppedNoteHtml(d) {
  const on = !d.image && d.imageDropped;
  return `<div class="muted tiny draft-dropped"${on ? "" : " hidden"}>`
       + `原图已释放，不再占用存储（题干已转成文字，可直接改）</div>`;
}

function draftCard(d) {
  const unc = (d.uncertain || []).length
    ? `<div class="math-warn">⚠️ 这几处没把握，保存前核对一下：${d.uncertain.map(esc).join("；")}</div>`
    : "";
  const err = d.status === "error" && d.error ? `<div class="math-warn">${esc(d.error)}</div>` : "";
  return `
    <div class="card math-draft-item${d.status === "error" ? " is-error" : ""}" data-draft="${d.key}">
      <div class="draft-top">${draftTopHtml(d)}</div>
      ${droppedNoteHtml(d)}
      ${err}${unc}
      ${draftPreviewHtml(d)}
      <!-- 源码框默认折叠：用户的原话是「你给我的这是代码」。
           上面的「题干预览」才是给他看的；源码只在需要改的时候才展开。 -->
      <details class="draft-src">
        <summary>编辑题干源码（LaTeX，改公式时才需要）</summary>
        <label class="field"><span>题干${d.status === "manual" ? "" : "（AI 只取题干，答案与解析不会进来）"}</span>
          <textarea class="field-input math-textarea math-draft-stem"
            placeholder="${d.status === "manual" ? "在这里输入题目，公式可用 LaTeX" : "识别完会自动填进来，也可以直接手打"}">${esc(d.stem || "")}</textarea></label>
      </details>
      ${aiClassifyHtml(d)}
      <div class="field-row">
        <label class="field"><span>科目</span>
          <select class="math-draft-subject">${mathSubjectOpts(d.subject)}</select></label>
        <label class="field"><span>专题</span>
          <select class="math-draft-topic">${mathTopicOpts(d.subject, d.topic)}</select></label>
        <label class="field"><span>类型</span>
          <select class="math-draft-kind">${mathKindOpts(d.kind)}</select></label>
      </div>
      <div class="field-row">
        <label class="field" style="flex:2"><span>备注（可选，会一直留着）</span>
          <input class="field-input math-draft-note" value="${esc(d.source_note || "")}"
            placeholder="例如：880 题 第 3 章 12 题"></label>
      </div>
    </div>`;
}

function renderDraft() {
  const box = $("#mathDraftWrap");
  if (!box) return;
  if (!MATH.draftOpen && !MATH.drafts.length) { box.innerHTML = ""; return; }
  const n = MATH.drafts.filter(draftReady).length;
  const busy = MATH.drafts.some(d => d.status === "extracting");
  box.innerHTML = `
    <div class="card math-draft">
      <div class="math-sec-title">录入错题${MATH.drafts.length ? `（${MATH.drafts.length} 道待确认）` : ""}</div>
      <input type="file" id="mathPhoto" accept="image/*" multiple hidden>
      <input type="file" id="mathShot" accept="image/*" capture="environment" hidden>
      <div class="math-drop">
        <button class="btn" id="mathShotBtn">📷 直接拍照</button>
        <button class="btn" id="mathPick">🖼️ 从相册选（可多选）</button>
        <p class="muted small" style="margin:8px 0 0">
          也可以 Ctrl+V 一次粘贴多张截图。识别后由 AI 归类，你可以改；
          原图只用来认字，认完就释放，不占你的存储。</p>
      </div>
      <div class="math-draft-actions">
        <button class="btn primary" id="mathSaveAll" ${n && !busy ? "" : "disabled"}>
          ${busy ? "识别中…" : (n ? `全部保存（${n} 道）` : "全部保存")}</button>
        <button class="btn ghost" id="mathCancel">关闭</button>
      </div>
      <p class="muted small" style="margin-top:8px">保存后会按 3 → 7 → 14 → 30 天的节奏提醒你重做。</p>
    </div>
    ${MATH.drafts.length ? `<div class="math-draft-list">${MATH.drafts.map(draftCard).join("")}</div>` : ""}`;
}

/* 局部重绘一张草稿卡：不碰 textarea（否则正在打字的人会丢焦点） */
function paintDraftCard(key) {
  const el = document.querySelector(`[data-draft="${key}"]`);
  const d = MATH.drafts.find(x => x.key === key);
  if (!el || !d) return;
  const ta = el.querySelector(".math-draft-stem");
  if (ta && document.activeElement !== ta) ta.value = d.stem || "";
  const note = el.querySelector(".math-draft-note");
  if (note && document.activeElement !== note) note.value = d.source_note || "";
  el.classList.toggle("is-error", d.status === "error");
  /* 整块重绘顶部：识别成功后缩略图要消失，只换角标是去不掉的。
     用签名挡住重复渲染 —— 打字时每个字符都会走到这里，不必重建 DOM。 */
  const top = el.querySelector(".draft-top");
  if (top) {
    const sig = `${d.status}|${d.image ? 1 : 0}|${d.model || ""}`;
    if (top.dataset.sig !== sig) { top.innerHTML = draftTopHtml(d); top.dataset.sig = sig; }
  }
  const dn = el.querySelector(".draft-dropped");
  if (dn) dn.hidden = !(!d.image && d.imageDropped);
  const ai = el.querySelector(".ai-class");
  if (ai && d.status !== "extracting") ai.outerHTML = aiClassifyHtml(d);
  /* 预览跟着题干走（用户边改 LaTeX 边看排版结果，改错了立刻能发现） */
  const pv = el.querySelector(".draft-preview");
  if (pv) pv.innerHTML = d.stem ? mathMtex(d.stem) : `<span class="muted">${d.status === "manual" ? "输入题干后会显示在这里" : "识别完会自动显示在这里"}</span>`;
  const ss = el.querySelector(".math-draft-subject");
  if (ss) ss.value = d.subject || "";
  const ts = el.querySelector(".math-draft-topic");
  if (ts) ts.innerHTML = mathTopicOpts(d.subject, d.topic);
  const ks = el.querySelector(".math-draft-kind");
  if (ks) ks.value = d.kind || "normal";
  mathSyncSaveBtn();
}

function mathSyncSaveBtn() {
  const btn = $("#mathSaveAll");
  if (!btn) return;
  const n = MATH.drafts.filter(draftReady).length;
  const busy = MATH.drafts.some(d => d.status === "extracting");
  btn.disabled = !n || busy;
  btn.textContent = busy ? "识别中…" : (n ? `全部保存（${n} 道）` : "全部保存");
}

async function mathSaveAll() {
  const list = MATH.drafts.filter(draftReady);
  if (!list.length) { toast("还没有可保存的题干", true); return; }
  let ok = 0, fail = 0;
  for (const d of list) {
    d.status = "saving";
    paintDraftCard(d.key);
    try {
      await api("/api/math/problems", {
        method: "POST",
        body: JSON.stringify({
          stem: d.stem, subject: d.subject, topic: d.topic, kind: d.kind,
          /* ⚠️ 故意**不上传原图**：识图只为拿题干，图片用户不要留（省存储）。
             服务端 create_problem 只在收到 image 时才落盘，不发就不会存。 */
          source_note: d.source_note || "",
          answer: d.answer || "",  // 传递识图时提取的答案
        }),
      });
      d.status = "saved"; ok += 1;
    } catch (e) {
      d.status = "error"; d.error = e.message; fail += 1;
    }
    paintDraftCard(d.key);
  }
  // 存成功的移出队列；失败的 / 还没识别的留着让人接着处理
  MATH.drafts = MATH.drafts.filter(d => d.status !== "saved");
  if (!MATH.drafts.length) MATH.draftOpen = false;
  MATH.meta = null;                 // 统计要重算
  await loadMath();
  toast(`已存入错题库 ${ok} 道${fail ? `，${fail} 道失败` : ""}`, !!fail);
}

/* ==========================================================
   刷题面板：题干 + 最终答案 + 「会做了 / 不会做」
   ----------------------------------------------------------
   用户 2026-10-12 的口径：
     「题目只有 最终答案 然后就是会做了 不会做 我不希望有解析了
       我觉得那样太费时间 我只需要知道我每次做完了对不对就可以了」
     「不用点一下对答案 就保留两个按键会做 不会做 我做完了自己去点即可」
   所以这里**没有**「看解析」那一步：答案直接印在题目下面，做完自己点两个键。
   解析（推荐解法 / 关键步骤 / 易错点）不再生成、也不再显示。
   ========================================================== */
function mathAnswerBlock(it) {
  const ans = String(it.answer || "").trim();
  if (ans) {
    return `<div class="math-answer"><b>最终答案</b><div class="ma-val">${mathMtex(ans)}</div></div>`;
  }
  return `<div class="math-answer is-empty">
    <b>最终答案</b>
    <div class="muted small">这道题还没有答案。
      <button class="btn tiny" data-m-ask-answer="${it.id}">让 AI 只算个答案</button>
    </div>
  </div>`;
}

function renderDrill() {
  paintMathTabs();          // 刷题面板开没开 → 决定顶栏亮哪个入口
  const box = $("#mathDrillWrap");
  if (!box) return;
  const p = MATH.drill;
  /* 周末休息：周六/周日没有到期队列，后端给空列表 + rest 标记 →
     这里明说「周末休息」，别让人以为只是「今天恰好没有错题」（2026-10-19）。 */
  const due = MATH.due.length ? `
    <section class="math-due" aria-label="今天该复习的错题">
      <div class="math-sec-title">今天该复习的错题（${MATH.due.length}）</div>
      ${MATH.due.map(it => `
        <div class="card math-due-card" data-due-id="${it.id}">
          <div class="math-chips">${mathChips(it)}</div>
          <div class="math-stem">${mathMtex(it.stem)}</div>
          ${mathAnswerBlock(it)}
          <div class="math-actions">
            <button class="btn small primary" data-due-res="right">✅ 会做了</button>
            <button class="btn small warnish" data-due-res="wrong">❌ 不会做</button>
          </div>
        </div>`).join("")}
    </section>` : (MATH.rest ? `
    <section class="math-due" aria-label="周末休息">
      <div class="math-sec-title">🌴 周末休息</div>
      <p class="muted small" style="margin:0;line-height:1.8">
        复习只排在工作日：落在周六/周日的到期日顺延到下周一，周末队列为空，
        周末攒下的错题周一一起刷。想主动多练几道，随时点顶栏「开始刷题」。
      </p>
    </section>` : "");
  if (!p) { box.innerHTML = due; return; }
  const img = p.has_image
    ? `<img class="math-photo" src="/api/math/problems/${p.id}/image" alt="原题照片">` : "";
  box.innerHTML = `
    <div class="card math-drill">
      <div class="math-sec-title">刷题 · 自己先做，做完点下面两个键</div>
      <div class="math-chips">${mathChips(p)}</div>
      <div class="math-stem">${mathMtex(p.stem)}</div>
      ${img}
      ${mathAnswerBlock(p)}
      <div class="math-actions">
        <button class="btn small primary" data-m-res="right">✅ 会做了</button>
        <button class="btn small warnish" data-m-res="wrong">❌ 不会做</button>
        <button class="btn tiny" data-m-close-drill="1">关闭</button>
      </div>
    </div>${due}`;
}

/* 开始刷题：从**本模块全部**错题里随机抽一道 —— 用户 2026-10-07 明确口径「从我所有错题
   里面随机找」，所以**故意不带**科目/专题筛选（筛选只管列表，不管刷题抽哪道）；
   但 2026-10-08 起必须带 module：信号的刷题不许抽出数学题，反之亦然。 */
async function mathRandom() {
  try {
    const r = await api("/api/math/random?module=" + encodeURIComponent(MATH.module));
    if (!r.problem) { toast("错题库还是空的，先拍照录几道", true); return; }
    MATH.drill = r.problem;
    MATH.revealed = false;
    renderDrill();
    paintMathTabs();
    document.querySelector(".math-drill")?.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) { toast(e.message, true); }
}

async function mathDrillOne(id) {
  try {
    const p = await api("/api/math/problems/" + id);
    MATH.drill = p;
    MATH.revealed = false;
    if (currentView !== "math") { navigate(MATH.module === "sig" ? "sig" : "math"); await loadMath(); }
    renderDrill();
    document.querySelector(".math-drill")?.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) { toast(e.message, true); }
}

/* ==========================================================
   交互（统一委托到 document；科目卡在整页外，单独先判）
   ========================================================== */
document.addEventListener("click", async e => {
  /* --- 科目选择 --- */
  const sc = e.target.closest("[data-subject]");
  if (sc) {
    const k = sc.dataset.subject;
    /* 数学一 / 信号与系统进的是**同一个错题库页面**，但模块互不相通：
       sig 会预设科目筛选，且列表 / 复习到期 / 随机刷题都只看本模块的科目。
       切换模块必须把缓存的 meta 作废 —— 里面缓存的是上一个模块的统计和可选科目。 */
    if (k === "math" || k === "sig") {
      navigate(k);
    } else {
      navigate("today");
    }
    return;
  }

  if (!e.target.closest("#pageBody-math")) return;

  /* --- 清理历史遗留的原图（新录的题已经不留图了） --- */
  if (e.target.closest("#mathPurgeImg")) {
    if (!confirm("把所有已存的原题照片删掉？（题干、解析、复习记录都不动）")) return;
    try {
      const r = await api("/api/math/images/purge", { method: "POST", body: "{}" });
      const mb = r.bytes ? `，约 ${(r.bytes / 1048576).toFixed(2)} MB` : "";
      toast(`已释放 ${r.removed || 0} 张原图${mb}`);
      MATH.meta = null;
      await loadMath();
    } catch (err) { toast(err.message, true); }
    return;
  }

  /* --- 录题 --- */
  if (e.target.closest("#mathManualBtn")) {
    MATH.draftOpen = true;
    const draft = mathNewDraft("");
    MATH.drafts.push(draft);
    renderDraft();
    document.querySelector(`[data-draft="${draft.key}"] .math-draft-stem`)?.focus();
    return;
  }
  if (e.target.closest("#mathAddBtn")) {
    MATH.draftOpen = true;
    renderDraft();
    $("#mathPhoto")?.click();      // 点了就直接弹选图，少一步
    return;
  }
  if (e.target.closest("#mathCancel")) {
    MATH.drafts = []; MATH.draftOpen = false; renderDraft(); return;
  }
  if (e.target.closest("#mathPick")) { $("#mathPhoto")?.click(); return; }
  /* 手机上的「直接拍照」：走带 capture 的那个 input，一键进相机（少一次系统选择器）。
     桌面端这个按钮是藏着的（capture 在桌面会被忽略，两个按钮就重复了）。 */
  if (e.target.closest("#mathShotBtn")) { $("#mathShot")?.click(); return; }

  /* --- 单张草稿：移除 / 重新识别 --- */
  const draftEl = e.target.closest("[data-draft]");
  if (draftEl) {
    const d = MATH.drafts.find(x => x.key === draftEl.dataset.draft);
    if (d) {
      if (e.target.closest("[data-d-del]")) {
        MATH.drafts = MATH.drafts.filter(x => x.key !== d.key);
        if (!MATH.drafts.length) MATH.draftOpen = false;
        renderDraft();
        return;
      }
      if (e.target.closest("[data-d-re]")) {
        d.status = "new"; d.error = ""; d.touched = false;
        paintDraftCard(d.key);
        mathRunExtract();
        return;
      }
    }
  }

  /* --- 全部保存 --- */
  if (e.target.closest("#mathSaveAll")) { await mathSaveAll(); return; }

  /* --- 快捷按钮：做对了/还是不会 --- */
  const quickBtn = e.target.closest("[data-m-quick]");
  if (quickBtn) {
    const act = quickBtn.dataset.mQuick;  // "right" 或 "wrong"
    const id = Number(quickBtn.dataset.mQid);
    quickBtn.disabled = true;

    // 保存当前滚动位置
    const scrollY = window.scrollY;

    try {
      await api(`/api/math/problems/${id}/result`, {
        method: "POST",
        body: JSON.stringify({ result: act }),
      });
      toast(act === "right" ? "✅ 会做了，下次间隔拉长" : "没关系，明天再来一次");
      // 刷新列表
      MATH.meta = null;
      await loadMath();
      bumpMathDueCount();

      // 恢复滚动位置
      window.scrollTo({ top: scrollY });
    } catch (err) {
      toast(err.message, true);
      quickBtn.disabled = false;
    }
    return;
  }

  /* --- 就地改答案 --- */
  const eSave = e.target.closest("[data-m-edit-save]");
  if (eSave) {
    const id = Number(eSave.dataset.mEditSave);
    const input = $("#meAnswer");
    const val = input ? input.value.trim() : "";
    eSave.disabled = true;
    try {
      const r = await api(`/api/math/problems/${id}`, {
        method: "PATCH", body: JSON.stringify({ answer: val }),
      });
      const ans = (r.problem && r.problem.answer) || val;
      const hit = (MATH.items || []).find(x => Number(x.id) === id);
      if (hit) hit.answer = ans;
      if (MATH.drill && Number(MATH.drill.id) === id) MATH.drill = { ...MATH.drill, answer: ans };
      MATH.editId = null;
      renderMath();
      toast("已保存答案");
    } catch (err) { toast(err.message, true); eSave.disabled = false; }
    return;
  }
  if (e.target.closest("[data-m-edit-cancel]")) { MATH.editId = null; renderMath(); return; }
  const eBtn = e.target.closest("[data-m-edit]");
  if (eBtn) {
    const id = Number(eBtn.dataset.mEdit);
    MATH.editId = MATH.editId === id ? null : id;
    MATH.openId = id;                  // 编辑时把卡片展开，别让人找不到输入框
    renderMath();
    return;
  }

  /* --- 列表操作 --- */
  const tgl = e.target.closest("[data-m-toggle]");
  if (tgl) {
    const id = Number(tgl.dataset.mToggle);
    MATH.openId = MATH.openId === id ? null : id;
    renderMath();
    return;
  }
  /* ⚠️ 这里原先是「AI 解析」按钮的处理（调 /analyze 生成整篇解析）。
   用户 2026-10-12 明确不要解析，按钮已撤，这段也删掉 ——
   别留着一条会重新触发「费时间那件事」的路径。
   后端 /analyze 接口仍然保留（万一以后想用），只是前端不再有入口。 */
  const del = e.target.closest("[data-m-del]");
  if (del) {
    const id = Number(del.dataset.mDel);
    if (!confirm("删除这道错题？（复习记录会一起删掉）")) return;
    try {
      await api("/api/math/problems/" + id, { method: "DELETE" });
      if (MATH.openId === id) MATH.openId = null;
      if (MATH.drill && MATH.drill.id === id) { MATH.drill = null; renderDrill(); }
      MATH.meta = null;
      await loadMath();
      toast("已删除");
    } catch (err) { toast(err.message, true); }
    return;
  }
  const dri = e.target.closest("[data-m-drill]");
  if (dri) { await mathDrillOne(Number(dri.dataset.mDrill)); return; }
  if (e.target.closest("[data-m-close-drill]")) {
    MATH.drill = null; MATH.revealed = false; renderDrill(); return;
  }

  /* --- 补「最终答案」 ---------------------------------------------------
   用户 2026-10-12：「我不希望有解析了，我觉得那样太费时间」。
   所以刷题里**没有**「看解析」这一步了，这里只处理一种情况：
   历史题 / 手录题缺答案 → 让 AI **只算答案**（不生成解析，快得多也便宜得多）。
   ⚠️ 不要在这里改回调 /analyze —— 那正是用户嫌慢的那件事。 */
  const askAns = e.target.closest("[data-m-ask-answer]");
  if (askAns) {
    const id = Number(askAns.dataset.mAskAnswer);
    askAns.disabled = true;
    askAns.textContent = "算答案中…";
    try {
      const r = await api(`/api/math/problems/${id}/answer`, { method: "POST" });
      const ans = (r.problem && r.problem.answer) || "";
      if (!ans) throw new Error("还是没算出来，可以自己把答案填进去");
      /* 就地更新三个可能持有这道题的地方，避免整页重载（用户也不喜欢跳走） */
      if (MATH.drill && Number(MATH.drill.id) === id) MATH.drill = { ...MATH.drill, answer: ans };
      [MATH.due, MATH.items].forEach(arr => {
        const hit = (arr || []).find(x => Number(x.id) === id);
        if (hit) hit.answer = ans;
      });
      renderDrill();
      renderMath();
      toast("答案已补上");
    } catch (err) {
      toast(err.message, true);
      askAns.disabled = false;
      askAns.textContent = "让 AI 只算个答案";
    }
    return;
  }

  const dueResult = e.target.closest("[data-due-res]");
  if (dueResult) {
    const cardEl = dueResult.closest("[data-due-id]");
    const id = Number(cardEl.dataset.dueId);
    const act = dueResult.dataset.dueRes;
    dueResult.disabled = true;
    /* ⚠️ 这里**不能**再 loadMath()。
       用户 2026-10-11 反馈：「选择做对了和没有做对以后 他就会直接跳开你当前的刷题
       直接跳到题库里面去了 又需要重新往上翻」。原因就是这个整页重载：
       MATH.due 重新拉一遍、整块面板重绘、滚动位置被打回顶部。
       改成：把刚做完的这道**就地移除**，并保持滚动位置不动 ——
       这样连续刷错题时，下一道会顶上来，你原地继续做就行。 */
    const keepY = window.scrollY;
    try {
      await api(`/api/math/problems/${id}/result`, {
        method: "POST", body: JSON.stringify({ result: act }),
      });
      MATH.dueAnswers.delete(id);
      MATH.due = (MATH.due || []).filter(x => Number(x.id) !== id);
      MATH.meta = null;
      renderDrill();
      /* 就地移除后内容会变短，先把滚动位置补回去，避免「跳一下」 */
      window.scrollTo({ top: Math.min(keepY, Math.max(0, document.body.scrollHeight - window.innerHeight)) });
      /* 计数是本地推的：不为了两个数字去重拉整页 */
      bumpMathDueCount();
      toast({ right: "做对了，下次间隔拉长", wrong: "没关系，明天再来一次", skip: "已推到明天",
              drop: "已移出复习周期" }[act] || "已记录");
    } catch (err) { toast(err.message, true); dueResult.disabled = false; }
    return;
  }

  /* --- 复习结果 --- */
  const res = e.target.closest("[data-m-res]");
  if (res) {
    const act = res.dataset.mRes;
    res.disabled = true;
    const drillId = MATH.drill && MATH.drill.id;
    try {
      await api(`/api/math/problems/${MATH.drill.id}/result`, {
        method: "POST", body: JSON.stringify({ result: act }),
      });
      toast({ right: "做对了，下次间隔拉长", wrong: "没关系，明天再来一次", skip: "已推到明天",
              drop: "已移出复习周期" }[act] || "已记录");
      MATH.drill = null; MATH.revealed = false;
      MATH.dueAnswers.delete(drillId);
      MATH.due = (MATH.due || []).filter(x => Number(x.id) !== Number(drillId));
      MATH.meta = null;
      /* 同样不整页重载：刷题面板就地收掉，你原来的位置（错题列表那一段）保持不动 */
      renderDrill();
      bumpMathDueCount();
    } catch (err) { toast(err.message, true); res.disabled = false; }
    return;
  }
});

/* 输入的活儿：题干 / 分类 / 备注 都要记进草稿对象，否则重绘一次就丢 */
function mathDraftOf(el) {
  const card = el.closest("[data-draft]");
  if (!card) return null;
  return MATH.drafts.find(x => x.key === card.dataset.draft) || null;
}

document.addEventListener("input", e => {
  const d = mathDraftOf(e.target);
  if (!d) return;
  if (e.target.classList.contains("math-draft-stem")) {
    d.stem = e.target.value;
    if (d.status === "new" && (d.stem || "").trim()) d.status = "ready";
    paintDraftCard(d.key);
  }
  if (e.target.classList.contains("math-draft-note")) d.source_note = e.target.value;
});

document.addEventListener("change", async e => {
  const id = e.target.id || "";

  const d = mathDraftOf(e.target);
  if (d) {
    if (e.target.classList.contains("math-draft-subject")) {
      d.subject = e.target.value; d.topic = ""; d.touched = true;
      const ts = e.target.closest("[data-draft]").querySelector(".math-draft-topic");
      if (ts) ts.innerHTML = mathTopicOpts(d.subject, "");
      return;
    }
    if (e.target.classList.contains("math-draft-topic")) { d.topic = e.target.value; d.touched = true; return; }
    if (e.target.classList.contains("math-draft-kind")) { d.kind = e.target.value; d.touched = true; return; }
  }

  /* 相册（#mathPhoto，可多选）和拍照（#mathShot，capture，单张）走同一条路。
     ⚠️ 必须先 **Array.from 快照** 再清空 input。
     写成 `const files = e.target.files; e.target.value = "";` 是错的：
     `value=""` 会把 input 的文件列表清掉，之前那个 FileList 引用会跟着变空，
     于是多张照片一张都进不去（症状：选了 2 张，草稿区空着，识图接口一次都没调）。
     2026-10-07 取证时钉死的。 */
  if (id === "mathPhoto" || id === "mathShot") {
    const files = Array.from(e.target.files || []);
    e.target.value = "";                 // 允许再次选同一张
    await mathAddPhotos(files);
    return;
  }

  if (!e.target.closest("#pageBody-math")) return;
  /* 筛选条件变了 → 重新拉列表 */
  if (id === "mfSubject") { MATH.f.subject = e.target.value; MATH.f.topic = ""; await loadMath(); return; }
  if (id === "mfTopic")   { MATH.f.topic = e.target.value; await loadMath(); return; }
  if (id === "mfKind")    { MATH.f.kind = e.target.value; await loadMath(); return; }
  if (id === "mfCycle")   { MATH.f.cycle = e.target.value; await loadMath(); return; }
});

/* 搜索框：输入停顿 350ms 再查，别每敲一个字打一次接口 */
let _mathQTimer = null;
document.addEventListener("input", e => {
  if (e.target.id !== "mfQ") return;
  MATH.f.q = e.target.value;
  clearTimeout(_mathQTimer);
  _mathQTimer = setTimeout(() => loadMath(), 350);
});

/* 粘贴截图：一次粘多张也全部收下 */
document.addEventListener("paste", async e => {
  if (currentView !== "math") return;
  const items = (e.clipboardData && e.clipboardData.items) || [];
  const files = [];
  for (const it of items) {
    if (it.type && it.type.startsWith("image/")) {
      const f = it.getAsFile();
      if (f) files.push(f);
    }
  }
  if (!files.length) return;
  e.preventDefault();
  await mathAddPhotos(files);
});

/* ==========================================================
   启动
   ----------------------------------------------------------
   ⚠️ 这两行必须留在文件**最末尾**：上面对数学模块的顶层 `const MATH` /
   `const SUBJECTS` 还没初始化时，route() 跑到 #math / #subjects 会直接抛
   暂时性死区错误、整页白屏。已经踩过一次（出图时才发现），别再往上挪。
   ========================================================== */
boot();

/* 支持直达任一整页：…/#subjects · #today · #library · #stats · #history · #review · #math · #sig
   （不带 hash 时落在科目选择页 —— 这是「主界面」） */
route();
