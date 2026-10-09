/* ==========================================================
   Prism · 手写批注层（自由手写）
   ----------------------------------------------------------
   设计要点（改之前先读这段）：
   1. **一个「区块」一层画布**，不是整页一张大画布。
      因为区块之间会各自折叠/展开（<details>），整页定位的笔迹会跟着漂。
      每层画布是区块内的绝对定位元素，尺寸跟着区块走，块内不会动。
   2. **存的是向量笔迹，不是图片**。分辨率无关、体积极小、以后能按笔画编辑。
      每条 = {c:颜色, s:粗细, hl:荧光, p:[[x,y]…]}，坐标是**区块内容坐标（CSS px）**。
   3. 存一份「基准宽度」w。换设备/转屏后区块宽度变了，渲染时按 宽比 缩放 x
      （高度方向因为换行会变，做不到精确，这是已知取舍）。
   4. **事件挂在区块容器上，画布本身 pointer-events:none**。
      这样不动笔时底下的一切照常可点（按钮、链接、选文字），
      只有真的落笔那一刻才 preventDefault 抢占。
   5. **不在控件上落笔**：目标在 input/textarea/button/a/select/summary 里就不画，
      否则批注模式一开、仿写输入框就点不动了。
   6. 笔优先：笔（pointerType==="pen"）任何时候都能写；手指/鼠标要开了批注模式才写。
      写字时手掌压下产生的 touch 会被忽略（按 lastPenAt 判定），避免边写边滚屏。
   ========================================================== */
(function () {
  "use strict";

  /* 可批注的区块。想多覆盖一处，在这里加一行就行，别的逻辑与它无关。 */
  const AREAS = [
    ["#bodyText", "body", "文章正文"],
    ["#transBody", "trans", "中文翻译"],
    ["#sentList", "sent", "长难句"],
    ["#gramList", "gram", "语法"],
    ["#logicBody", "logic", "阅读逻辑"],
    ["#examBody", "exam", "命题视角"],
    ["#writeList", "write", "写作表达"],
    ["#imitateBody", "imitate", "仿写 / 批改"],
  ];

  const INK = [
    { v: "ink", c: "#1b1a17", label: "黑笔" },
    { v: "red", c: "#c0392b", label: "红笔" },
    { v: "blue", c: "#1f6fd0", label: "蓝笔" },
    { v: "hl", c: "#ecc400", label: "荧光笔", hl: true },
  ];
  const WIDTHS = [2, 3.5, 6];
  const HL_WIDTH = 17;

  const MAX_POINTS = 1600;      // 单笔画点数上限，防呆
  const SAVE_DELAY = 900;       // 停笔后多久落库
  const ERASE_R = 16;           // 橡皮判定半径（CSS px）
  const PALM_MS = 900;          // 用完笔之后多久内的触摸算手掌
  const MAX_PX = 8e6;           // 单层画布的像素上限，长文章别把内存吃光

  let docPrefix = "";
  let mode = false;
  let tool = "pen";
  let ink = INK[0];
  let width = WIDTHS[1];
  let fingerDraws = true;
  let lastPenAt = 0;
  let swallowClick = false;
  let lastArea = "";
  const cache = {};             // doc_key → {v,w,strokes}
  const layers = new Map();     // 区块名 → Layer
  let history = [];
  const dirty = new Set();
  let saveTimer = null;
  let writeSeq = 0;            // 本地落笔次数；取数返回时用它判断缓存是不是已经过期

  const q = (s, r = document) => r.querySelector(s);
  const keyOf = name => docPrefix + ":" + name;
  const isDark = () => !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);

  /* ---------------------------------------------------------
     一层画布
     --------------------------------------------------------- */
  class Layer {
    constructor(el, name, label) {
      this.el = el;
      this.name = name;
      this.label = label;
      this.canvas = document.createElement("canvas");
      this.canvas.className = "anno-canvas";
      this.canvas.setAttribute("aria-hidden", "true");
      this.ctx = this.canvas.getContext("2d");
      this.live = null;            // 正在写的这一笔
      this.w = 0; this.h = 0; this.scale = 1; this.visible = false;
      this.bound = false;
      this.load();
      this.bind();
      this.prepare();
    }

    get key() { return keyOf(this.name); }

    /* 从内存缓存里取这一层的笔迹（缓存由 setDoc 时一次性拉回来）。
       fresh=false 表示「这份缓存是取数请求发出**之前**的，期间用户可能已经落过笔」——
       这时只补空层，绝不能用它冲掉已经画上去的笔迹。
       真实场景：进页面时 renderBundle 会发一次取数请求，用户手快、请求还没回来就落笔了，
       等请求回来把 strokes 覆盖成空数组，刚写的字就白写了。 */
    load(fresh = true) {
      if (this.live) return;                    // 正在写的这一笔不能被打断
      const d = cache[this.key];
      const incoming = (d && Array.isArray(d.strokes)) ? d.strokes : [];
      if (!fresh && this.strokes.length) return;
      this.strokes = incoming;
      this.baseW = (d && Number(d.w)) || 0;
    }

    /* 换文档时用：清空本地笔迹，并把它从脏集合摘掉（上一篇已经交给 flush 了） */
    reset() {
      this.strokes = [];
      this.live = null;
      this.baseW = 0;
      dirty.delete(this.name);
    }

    data() {
      return { v: 1, w: Math.round(this.w) || this.baseW || 0, strokes: this.strokes };
    }

    bind() {
      if (this.bound) return;
      this.bound = true;
      this.el.addEventListener("pointerdown", e => onDown(this, e), { passive: false });
    }

    /* 画布可能被 innerHTML 重绘冲掉，这里保证它一直在、尺寸也对 */
    prepare() {
      if (this.canvas.parentNode !== this.el) this.el.appendChild(this.canvas);
      this.el.classList.add("anno-host");
      const rect = this.el.getBoundingClientRect();
      const w = Math.max(0, Math.round(rect.width));
      const h = Math.max(0, Math.round(this.el.scrollHeight || rect.height));
      if (!w || !h) { this.visible = false; return false; }
      const dpr = window.devicePixelRatio || 1;
      let scale = Math.min(dpr, 2);
      if (w * h * scale * scale > MAX_PX) {
        scale = Math.max(1, Math.sqrt(MAX_PX / (w * h)));
      }
      const bw = Math.max(1, Math.round(w * scale));
      const bh = Math.max(1, Math.round(h * scale));
      if (this.canvas.width !== bw || this.canvas.height !== bh) {
        this.canvas.width = bw;
        this.canvas.height = bh;
      }
      this.canvas.style.width = w + "px";
      this.canvas.style.height = h + "px";
      this.w = w; this.h = h; this.scale = scale;
      this.visible = true;
      return true;
    }

    /* 区块宽度变了就按比例缩 x；首次落笔前把基准宽定下来 */
    kick() { return this.baseW ? this.w / this.baseW : 1; }

    draw() {
      const ctx = this.ctx;
      ctx.setTransform(this.scale, 0, 0, this.scale, 0, 0);
      ctx.clearRect(0, 0, this.w, this.h);
      if (!this.visible) return;
      const k = this.kick();
      for (const st of this.strokes) paint(ctx, st, k);
      if (this.live) paint(ctx, this.live, k);
    }

    /* 只补画最新一小段，避免每一 move 都全量重绘（长文章会卡） */
    drawTail(st, from) {
      const ctx = this.ctx;
      ctx.setTransform(this.scale, 0, 0, this.scale, 0, 0);
      styleCtx(ctx, st);
      const k = this.kick();
      const p = st.p;
      const a = p[Math.max(0, from - 1)], b = p[from];
      if (!a || !b) return;
      ctx.beginPath();
      ctx.moveTo(a[0] * k, a[1]);
      ctx.lineTo(b[0] * k, b[1]);
      ctx.stroke();
    }

    dot(st) {
      const ctx = this.ctx;
      ctx.setTransform(this.scale, 0, 0, this.scale, 0, 0);
      styleCtx(ctx, st);
      const k = this.kick();
      const p = st.p[0];
      ctx.beginPath();
      ctx.arc(p[0] * k, p[1], (st.s || 3) / 2, 0, Math.PI * 2);
      ctx.fillStyle = st.c;
      ctx.fill();
    }
  }

  function styleCtx(ctx, st) {
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = st.c || "#1b1a17";
    ctx.lineWidth = st.s || 3;
    if (st.hl) {
      ctx.globalAlpha = isDark() ? 0.3 : 0.42;
      /* 浅色底上用正片叠底 = 记号笔透出下面的字；深色底上叠底会变黑，改回普通叠加 */
      ctx.globalCompositeOperation = isDark() ? "source-over" : "multiply";
    } else {
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    }
  }

  /* 平滑：相邻点之间用二次贝塞尔（中点当控制点），比直线自然 */
  function paint(ctx, st, k) {
    const p = (st && st.p) || [];
    if (!p.length) return;
    ctx.save();
    styleCtx(ctx, st);
    if (p.length === 1) {
      ctx.beginPath();
      ctx.arc(p[0][0] * k, p[0][1], (st.s || 3) / 2, 0, Math.PI * 2);
      ctx.fillStyle = st.c;
      ctx.fill();
      ctx.restore();
      return;
    }
    ctx.beginPath();
    ctx.moveTo(p[0][0] * k, p[0][1]);
    for (let i = 1; i < p.length - 1; i++) {
      const x1 = p[i][0] * k, y1 = p[i][1];
      const x2 = p[i + 1][0] * k, y2 = p[i + 1][1];
      ctx.quadraticCurveTo(x1, y1, (x1 + x2) / 2, (y1 + y2) / 2);
    }
    const last = p[p.length - 1];
    ctx.lineTo(last[0] * k, last[1]);
    ctx.stroke();
    ctx.restore();
  }

  /* ---------------------------------------------------------
     落笔判定
     --------------------------------------------------------- */
  const DEAD_SEL = "input,textarea,button,a,select,label,summary,[contenteditable]";

  function onDown(L, e) {
    if (e.pointerType === "pen") lastPenAt = Date.now();

    /* 手掌误触：刚用完笔，这一下触摸当没看见，并且别让它把页面滚走 */
    if (e.pointerType === "touch" && Date.now() - lastPenAt < PALM_MS) {
      e.preventDefault();
      return;
    }
    /* 没开批注模式时只有笔能写（鼠标/手指不落笔）。
       ⚠️ 但这条在 iPad 上不可靠：touch-action 只在 pen-on 时才禁掉滚屏手势，
       所以模式关着的时候，Apple Pencil 的笔势仍可能被 Safari 抢去滚屏。
       iPad 上请先点开批注模式（顶栏 ✍️）再写。 */
    if (!mode && e.pointerType !== "pen") return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (e.target.closest && e.target.closest(DEAD_SEL)) return;  // 别在控件上落笔
    /* 手指落笔：只有 pen-finger 开着才写。这一档只管"手指要不要落笔"，
       跟能不能滚屏无关 —— 滚屏由 style.css 的 touch-action 统一处理 */
    if (e.pointerType === "touch" && !fingerDraws) return;
    if (!L.prepare() || !L.visible) return;

    e.preventDefault();
    document.body.classList.add("pen-writing");
    lastArea = L.name;
    const pt = toLocal(L, e);

    if (tool === "eraser") {
      eraseAt(L, pt);
      return;
    }
    if (!L.baseW) L.baseW = L.w;
    L.live = { c: ink.c, s: ink.hl ? HL_WIDTH : width, hl: ink.hl ? 1 : 0, p: [pt] };
    L.dot(L.live);

    const move = ev => {
      if (!L.live) return;
      const np = toLocal(L, ev);
      const p = L.live.p;
      const last = p[p.length - 1];
      if (Math.abs(np[0] - last[0]) < 1.1 && Math.abs(np[1] - last[1]) < 1.1) return;
      if (p.length >= MAX_POINTS) return;
      /* getCoalescedEvents 在真机上至少含事件本身，但合成事件（测试里 dispatchEvent
         造出来的）会返回空数组 —— 空就退回事件本身，免得一个点都画不出来 */
      let pts = [];
      try { pts = (ev.getCoalescedEvents && ev.getCoalescedEvents()) || []; } catch (_) { pts = []; }
      if (!pts.length) pts = [ev];
      for (const c of pts) {
        const cp = toLocal(L, c);
        const lp = p[p.length - 1];
        if (Math.abs(cp[0] - lp[0]) < 0.9 && Math.abs(cp[1] - lp[1]) < 0.9) continue;
        p.push(cp);
      }
      L.drawTail(L.live, p.length - 1);
    };

    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.body.classList.remove("pen-writing");
      const live = L.live;
      L.live = null;
      if (!live) return;
      if (live.p.length === 1) live.p.push([live.p[0][0] + 0.6, live.p[0][1] + 0.6]);
      L.strokes.push(live);
      history.push({ key: L.key, kind: "add" });
      if (live.p.length > 4) swallowClick = true;   // 别把这一笔当成点了底下的按钮
      L.draw();
      markDirty(L.name);
    };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  /* 屏幕坐标 → 区块内容坐标 */
  function toLocal(L, e) {
    const r = L.el.getBoundingClientRect();
    return [Math.round((e.clientX - r.left) * 10) / 10,
            Math.round((e.clientY - r.top) * 10) / 10];
  }

  function eraseAt(L, pt) {
    let hit = -1, best = ERASE_R;
    for (let i = L.strokes.length - 1; i >= 0; i--) {
      const near = L.strokes[i].p.some(sp => Math.hypot(sp[0] - pt[0], sp[1] - pt[1]) < best);
      if (near) { hit = i; break; }
    }
    if (hit < 0) return;
    const [removed] = L.strokes.splice(hit, 1);
    history.push({ key: L.key, kind: "erase", stroke: removed, index: hit });
    L.draw();
    markDirty(L.name);
  }

  /* ---------------------------------------------------------
     撤销 / 清空
     --------------------------------------------------------- */
  function layerByKey(key) {
    for (const L of layers.values()) if (L.key === key) return L;
    return null;
  }

  function undo() {
    const h = history.pop();
    if (!h) { warn("没有可撤销的笔迹了"); return; }
    const L = layerByKey(h.key);
    if (!L) return;
    if (h.kind === "add") {
      L.strokes.pop();
    } else if (h.kind === "erase") {
      L.strokes.splice(h.index, 0, h.stroke);
    } else if (h.kind === "clear") {
      L.strokes = h.removed.concat(L.strokes);
    }
    L.draw();
    markDirty(L.name);
  }

  function clearLayer() {
    const L = layers.get(lastArea) || layerByKey(history.length ? history[history.length - 1].key : "");
    if (!L) { warn("先在这一页上落笔，再点清空"); return; }
    if (!L.strokes.length) { warn(`「${L.label}」本来就是空的`); return; }
    const removed = L.strokes.slice();
    L.strokes = [];
    L.draw();
    history.push({ key: L.key, kind: "clear", removed });
    markDirty(L.name);
    note(`已清空「${L.label}」的批注（可撤销）`);
  }

  /* ---------------------------------------------------------
     存 / 取
     --------------------------------------------------------- */
  function markDirty(name) {
    writeSeq++;                  // 本地落笔计数：取数回来时用它判断"期间用户写过没有"
    dirty.add(name);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, SAVE_DELAY);
  }

  async function flush() {
    clearTimeout(saveTimer);
    if (!dirty.size) return;
    const names = Array.from(dirty);
    dirty.clear();
    for (const name of names) {
      const L = layers.get(name);
      if (!L) continue;
      try {
        await api("/api/annotations", {
          method: "PUT",
          body: JSON.stringify({ key: L.key, data: L.data() }),
        });
      } catch (err) {
        dirty.add(name);   // 存失败就留个脏标记，下次再试
        warn("批注没存上：" + err.message);
      }
    }
  }

  function flushBeacon() {
    clearTimeout(saveTimer);
    if (!dirty.size) return;
    for (const name of Array.from(dirty)) {
      const L = layers.get(name);
      if (!L) continue;
      const body = JSON.stringify({ key: L.key, data: L.data() });
      try {
        /* 关页面时普通 fetch 会被掐断，这里用 keepalive 搏一把；失败也不弹提示 */
        fetch("/api/annotations", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body, keepalive: true,
        }).catch(() => {});
      } catch (_) { /* 忽略 */ }
    }
    dirty.clear();
  }

  /* ---------------------------------------------------------
     对外：文档切换 / 区块同步 / 模式切换
     --------------------------------------------------------- */
  async function setDoc(prefix) {
    const p = String(prefix || "");
    if (p === docPrefix) { sync(); return; }
    flush();                    // 换文档前先把上一篇存掉（只负责发出去，不等回执）
    docPrefix = p;
    history = [];
    lastArea = "";
    for (const L of layers.values()) { L.reset(); L.draw(); }
    if (!p) return;
    const seq = writeSeq;         // 取数之前先记一笔，回来时对一下就知道用户有没有抢先落笔
    try {
      const r = await api("/api/annotations?prefix=" + encodeURIComponent(p));
      const items = (r && r.items) || {};
      let n = 0;
      for (const k of Object.keys(items)) {
        cache[k] = items[k];
        if (items[k] && items[k].strokes && items[k].strokes.length) n++;
      }
      if (n) note(`这篇有 ${n} 处批注`);
      const fresh = seq === writeSeq;
      for (const L of layers.values()) { L.load(fresh); L.draw(); }
    } catch (_) { /* 拿不到就当没有批注，不影响阅读 */ }
  }

  function sync() {
    for (const [sel, name, label] of AREAS) {
      const el = q(sel);
      if (!el) continue;
      let L = layers.get(name);
      if (!L) { L = new Layer(el, name, label); layers.set(name, L); }
      else if (L.el !== el) { L.el = el; L.bound = false; L.bind(); }
      L.prepare();
      L.draw();
    }
  }

  function setMode(on) {
    mode = !!on;
    const bar = q("#penBar");
    if (bar) bar.hidden = !mode;
    const btn = q("#btnPen");
    if (btn) btn.classList.toggle("on", mode);
    paintBar();                       // 统一刷 body 上的 pen-on / pen-finger
    if (mode) {
      sync();
      if (!localStorage.getItem("prism_pen_hint")) {
        localStorage.setItem("prism_pen_hint", "1");
        note("批注已打开：用笔直接写在内容上。要滚动页面先关掉批注（再点一次 ✍️）");
      }
    } else {
      flush();
      document.body.classList.remove("pen-writing");
    }
  }

  /* ---------------------------------------------------------
     工具栏
     --------------------------------------------------------- */
  function buildBar() {
    if (q("#penBar")) return;
    const bar = document.createElement("div");
    bar.className = "penbar";
    bar.id = "penBar";
    bar.hidden = true;
    const dots = INK.map(i =>
      `<button class="pb-btn pb-dot" data-pb="ink" data-v="${i.v}" title="${i.label}"
         style="--c:${i.c}" aria-label="${i.label}"><i></i></button>`).join("");
    bar.innerHTML = `
      <button class="pb-btn" data-pb="hand" title="切换：书写 / 手指滚动">✍️</button>
      <span class="pb-sep"></span>
      <span class="pb-group">${dots}</span>
      <span class="pb-sep"></span>
      <button class="pb-btn pb-w" data-pb="width" title="切换粗细（细 / 中 / 粗）">
        <i></i></button>
      <span class="pb-sep"></span>
      <button class="pb-btn" data-pb="eraser" title="橡皮（擦掉整条笔画）">🧽</button>
      <button class="pb-btn" data-pb="undo" title="撤销">↶</button>
      <button class="pb-btn" data-pb="clear" title="清空当前这一块">🗑</button>
      <span class="pb-sep"></span>
      <button class="pb-btn" data-pb="close" title="退出批注">✕</button>`;
    document.body.appendChild(bar);
    bar.addEventListener("click", e => {
      const b = e.target.closest("[data-pb]");
      if (!b) return;
      const op = b.dataset.pb;
      if (op === "ink") {
        ink = INK.find(i => i.v === b.dataset.v) || INK[0];
        tool = "pen";
      } else if (op === "width") {
        width = WIDTHS[(WIDTHS.indexOf(width) + 1) % WIDTHS.length] || WIDTHS[1];
        ink = INK[0];
        tool = "pen";
      } else if (op === "hand") {
        fingerDraws = !fingerDraws;
      } else if (op === "eraser") {
        tool = tool === "eraser" ? "pen" : "eraser";
      } else if (op === "undo") {
        undo();
      } else if (op === "clear") {
        clearLayer();
      } else if (op === "close") {
        setMode(false);
        return;
      }
      paintBar();
    });
    paintBar();
  }

  function paintBar() {
    const bar = q("#penBar");
    document.body.classList.toggle("pen-on", mode);
    document.body.classList.toggle("pen-finger", !!fingerDraws);
    if (!bar) return;
    bar.dataset.tool = tool;
    const hand = bar.querySelector('[data-pb="hand"]');
    hand.textContent = fingerDraws ? "✍️" : "✋";
    /* 注意：这一档只决定「手指要不要落笔」，跟能不能滚屏无关。
       批注模式下这一块的滚动手势统一让给书写（见 style.css 里 touch-action 那段），
       所以别在提示语里承诺「手指只负责滚动」—— iPad 上做不到。 */
    hand.title = fingerDraws
      ? "现在是「手指也能写」，点一下改成「只有笔能写」"
      : "现在是「只有笔能写」，点一下改成「手指也能写」";
    bar.querySelector('[data-pb="eraser"]').classList.toggle("on", tool === "eraser");
    bar.querySelectorAll('[data-pb="ink"]').forEach(d => {
      d.classList.toggle("on", tool === "pen" && d.dataset.v === ink.v);
    });
    const w = bar.querySelector('[data-pb="width"]');
    const idx = Math.max(0, WIDTHS.indexOf(width));
    w.querySelector("i").style.width = (5 + idx * 4) + "px";
    w.querySelector("i").style.height = (5 + idx * 4) + "px";
    w.classList.toggle("on", tool === "pen" && !ink.hl);
    w.title = `粗细：${["细", "中", "粗"][idx]}（点一下换下一档）`;
    measureBar();     // 换档会改变工具栏宽度，可能从一排变两排
  }

  /* ---------------------------------------------------------
     小提示：复用 app.js 的 toast；它还没定义时安静降级
     --------------------------------------------------------- */
  function warn(msg) { if (typeof toast === "function") toast(msg, true); }
  function note(msg) { if (typeof toast === "function") toast(msg); }

  /* ---------------------------------------------------------
     初始化
     --------------------------------------------------------- */
  function init() {
    buildBar();
    const pb = q("#btnPen");
    if (pb) pb.addEventListener("click", () => setMode(!mode));
    document.addEventListener("click", e => {
      if (!swallowClick) return;
      swallowClick = false;
      e.stopPropagation();
      e.preventDefault();
    }, true);
    document.addEventListener("toggle", () => sync(), true);   // <details> 开合后重新量尺寸
    let rt = null;
    window.addEventListener("resize", () => {
      clearTimeout(rt);
      rt = setTimeout(sync, 200);
    });
    window.addEventListener("orientationchange", () => setTimeout(sync, 300));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flushBeacon();
    });
    window.addEventListener("pagehide", flushBeacon);
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => {
        for (const L of layers.values()) {
          if (!L.el.isConnected) continue;
          const w = Math.round(L.el.getBoundingClientRect().width);
          if (w && Math.abs(w - L.w) > 1) sync();
        }
      });
      /* 区块是懒挂的，这里定时把新出现的区块补进观察列表 */
      setInterval(() => {
        try {
          for (const [sel] of AREAS) {
            const el = q(sel);
            if (el && !el.__annoObs) { el.__annoObs = 1; ro.observe(el); }
          }
        } catch (_) { /* 忽略 */ }
      }, 1500);
    }
    sync();
  }

  /* 把工具栏实测高度写进 --penbar-h。
     body.pen-on 的底部留白靠它算 —— 写死一个数必然有设备不对：
     手机上工具栏会折成两排（~84px），iPad 上一排（~58px），差一倍。 */
  function measureBar() {
    const bar = q("#penBar");
    if (!bar || bar.hidden) return;
    const h = Math.round(bar.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty("--penbar-h", h + "px");
  }

  window.Pen = {
    setDoc, sync, setMode, flush, measureBar,
    isOn: () => mode,
    toggle: () => setMode(!mode),
    /* 给测试/调试用 */
    _layers: layers,
    _keyOf: keyOf,
    _setTool: (t, i) => { tool = t; if (i) ink = i; paintBar(); },
    _setFinger: v => { fingerDraws = !!v; paintBar(); },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
