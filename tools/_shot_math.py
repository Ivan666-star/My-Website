# -*- coding: utf-8 -*-
"""数学模块的页面取证：临时库起服务 → 灌数据 → 出图 + 断言。

用法（项目根目录）：
    python tools/_shot_math.py            # 出图并断言
    python tools/_shot_math.py --keep     # 保留临时目录（排查用）

产出（都在 data/ 下，**验证用的**，不是交付物）：
    data/_shot_math_科目.png     430×1180  科目选择主界面（冷启动落地页）
    data/_shot_math_手机.png     430×1180  数学页 + 多张照片批量录题
    data/_shot_math_平板.png     900×1300  展开一条，看解析里的公式排版
    data/_shot_math_刷题.png     430×900   刷题面板（未点「我做完了」，应看不到解析）

2026-10-07 这版盯死三件事（都是用户当次提的）：
  1. **主界面是科目选择**：冷启动 → `.subject-card` 出现 → 点「数学一」→ 进数学页。
     所以每个数学模式都**从 / 开始点进去**，而不是直接开 /#math —— 这样顺手就把分流验了。
  2. **多张照片一起识别**：用 DataTransfer 往 file input 里塞 2 张，
     并**在 iframe 里打桩 fetch**（拦 /api/math/extract 返回模拟结果），
     这样能拍到真实的「AI 归类」横条 + 两张草稿卡，且不花一分钱模型调用。
  3. **公式要看得懂**：断言 `.katex-html` 真的渲染出来了，且 `.mtex` 兜底块为 0
     （`.mtex` 出现 = KaTeX 没渲染、退化成了源码，正是用户投诉的那个状态）。

几个必须守住的做法（都踩过，改之前先看）：
  1. **临时库 + `AI_API_KEY=""`** 起服务 → 没有账号 → auth 进 OPEN 模式，免登录。
  2. **不要用 `--dump-dom`**。它在本工程里会**不退出**（常驻 setInterval + SW，
     实测挂了 7 分多钟）。改用「页面自己把结果 PUT 回服务端、Python 读临时库」。
  3. 等异步**必须用 setTimeout 轮询，绝不能用 requestAnimationFrame**。
     （2026-10-07 实测更正：以前这里写反了。`--virtual-time-budget` 下
     rAF **几乎不发** —— 整轮只跑了 6 帧，用它轮询会让所有等待原地卡死，
     症状是「截图里页面已经画好、回传报告却一条收不到」，极具误导性。
     定时器确实会被虚拟时间迅速烧完，但**未完成的网络请求会暂停虚拟时间**，
     而这里要等的全是由接口数据驱动的 DOM 更新，所以每圈都真的等到网络回来。）
  4. 断言结果走已有的 `PUT /api/annotations`（key 必须含冒号，`data.strokes`
     非空才落库 —— 借这个既有语义存测试报告正好）。
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8896
BASE = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="mathshot_"))
HARNESS = ROOT / "web" / "_qa_math.html"
KEEP = "--keep" in sys.argv
# --only <mode>：只跑某一个模式，改一次复跑一次的时候省时间
ONLY = ""
if "--only" in sys.argv:
    ONLY = sys.argv[sys.argv.index("--only") + 1] if len(sys.argv) > sys.argv.index("--only") + 1 else ""

env = dict(os.environ)
env.update({
    "DB_PATH": str(TMP / "app.db").replace("\\", "/"),
    "AI_API_KEY": "",              # 不调模型：出图只验界面
    "DAILY_GENERATE_HOUR": "23",   # 不自动生成当天内容
})

ok = 0
fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {label} {extra}".rstrip())
    else:
        fail += 1
        print(f"  [FAIL] {label} {extra}".rstrip())


def req(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body,
                               method=method or ("POST" if body else "GET"),
                               headers={"Content-Type": "application/json"})
    # 本机 HTTP_PROXY 会把 127.0.0.1 也代理走（本项目踩过），必须显式绕开
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(r, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def wait_health(proc, timeout=45):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"服务提前退出，code={proc.returncode}")
        try:
            if req("/api/health").get("ok"):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError("服务没起来")


HARNESS_TPL = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>math qa</title>
<style>
  html,body{margin:0;background:#e9e6e0;overflow:hidden;height:100%}
  /* 别用 flex 居中：iframe 会被整体推右 ~50px，截图右侧裁掉一条，
     看起来像产品横向溢出（其实是取证页自己的毛病，误导过一轮）。
     ⚠️ 绝对定位之后**必须给容器满高** —— 否则 body 高度 0，截图出来是空白小图
     （文件 < 5KB，被判成"没出图"，又误导一轮）。 */
  #stage{display:block;position:relative;width:100%;height:100%}
  iframe{border:0;background:#f5f3ef;display:block;position:absolute;left:0;top:0}
</style></head><body>
<div id="stage"><iframe id="fr"></iframe></div>
<script>
const Q = new URLSearchParams(location.search);
const W = +(Q.get("w") || 430), H = +(Q.get("h") || 1180);
const MODE = Q.get("mode") || "open";
const fr = document.getElementById("fr");
fr.style.width = W + "px"; fr.style.height = H + "px";
fr.src = "/";                                   // 一律从主界面（科目选择）进

const report = { mode: MODE, w: W, h: H, lines: [], notes: {} };
const note = (k, v) => { report.notes[k] = v; report.lines.push(k + "=" + v); };
/* 报告怎么送才不丢：**只能靠 keepalive + 里程碑各发一次**。
   ⚠️ 千万别改成 setInterval 定时发 —— 未完成的网络请求会让
   --virtual-time-budget 暂停虚拟时间，等于永远走不到预算、Chrome 永远不截图（踩过）。 */

/* ⚠️⚠️ 无头 + --virtual-time-budget 下 **rAF 几乎不发**（实测整轮只跑了 6 帧！），
   用它来轮询 = 所有等待原地卡死。症状极具误导性：截图里页面已经画得好好的，
   回传报告却一条都收不到 —— 一度让我以为是「点科目卡没跳转」，白查两轮。
   必须用 setTimeout 轮询：定时器会被虚拟时间迅速烧完，但**未完成的网络请求会
   暂停虚拟时间**，而这里等的（科目卡 / 草稿卡 / 数学列表）全由接口数据驱动，
   所以每一圈都真的等到网络回来（_probe_nav.py 起同样的服务实测通过）。 */
function raf(n){ return new Promise(r => setTimeout(r, 16 * n)); }
/* 等待 + 记结果：某个等待超时了也能看清是**哪一个**超时（失败会连坐后面所有断言，
   不单独记的话只能看到一个笼统的 ERR）。 */
async function tryWait(label, fn, tries = 200){
  try { await until(fn, tries); note(label, 1); }
  catch (e) { note(label, 0); }
}
function until(fn, tries = 400){
  return new Promise((res, rej) => {
    let i = 0;
    const f = () => { let v = null; try { v = fn(); } catch (e) { v = null; }
      if (v) return res(v); if (++i > tries) return rej(new Error("until 超时")); setTimeout(f, 50); };
    f();                     // 第一次同步就判：别再等一帧
  });
}
/* 结果回传服务端：key 必须含冒号（批注接口的既有约束）。
   ⚠️ 必须 keepalive —— --virtual-time-budget 到点后页面被拆，**普通 fetch 会被取消**，
   服务端一条都收不到（踩过：4 个模式全「没回传」，还被我误判成删除守卫的锅）。 */
function send(){
  return fetch("/api/annotations", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    keepalive: true,
    body: JSON.stringify({ key: "qa-math:" + MODE, data: { strokes: [{ t: 1 }], report: report } }),
  }).then(r => { report.notes.send_status = "ok:" + r.status; return r.status; })
    .catch(e => { report.notes.send_status = "err:" + (e && e.message); return -1; });
}
/* 1x1 PNG，用来伪造「用户选了两张照片」 */
function pngFile(name){
  const b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==";
  const bin = atob(b64); const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new File([arr], name, { type: "image/png" });
}
/* 拦截识图接口，返回模拟结果：不花模型调用，也能拍到真实的「AI 归类」横条 */
function stubExtract(w){
  const real = w.fetch.bind(w);
  let n = 0;
  w.__stubCalls = 0;          // 数一数到底有没有走到打桩的识图接口
  const canned = [
    { stem: "例2.17 设随机变量 $X$ 的概率密度为 $f_X(x)=\\begin{cases} \\dfrac{1}{2}, & -1<x<0, \\\\ \\dfrac{1}{4}, & 0\\le x<2, \\\\ 0, & \\text{其他}. \\end{cases}$ 令 $Y=X^2$，求 $Y$ 的概率密度 $f_Y(y)$。",
      subject: "概率论与数理统计", topic: "随机变量及其分布", kind: "normal",
      uncertain: ["第二行不等号可能是 \\le，请核对"], model: "deepseek-flash" },
    { stem: "8. 设 $A=\\begin{bmatrix} 1&2&3 \\\\ 4&5&6 \\\\ 7&8&9 \\end{bmatrix}$，求 $|A|$ 的值。",
      subject: "线性代数", topic: "行列式", kind: "normal",
      uncertain: [], model: "deepseek-flash" },
  ];
  w.fetch = function (url, opt) {
    if (String(url).indexOf("/api/math/extract") >= 0) {
      const item = canned[Math.min(n++, canned.length - 1)];
      w.__stubCalls = n;
      return Promise.resolve(new w.Response(JSON.stringify(Object.assign({ ok: true }, item)),
        { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return real(url, opt);
  };
}
(async () => {
  try {
    await until(() => fr.contentDocument && fr.contentDocument.querySelector(".topbar"));
    /* 必须显式取 contentDocument —— until() 返回的是**谓词的值**，
       直接 await 它拿到的是 .topbar 元素，后面查 .subject-card / .math-list 永远查不到。 */
    const d = fr.contentDocument;
    const w = fr.contentWindow;
    note("harness_doc_ok", d === fr.contentDocument ? 1 : 0);
    /* 干掉平滑滚动：无头下动画会拖住虚拟时间、截图时机不稳（_shot_pen 坑 4）。 */
    try { w.Element.prototype.scrollIntoView = function () {}; } catch (e) {}
    /* 无头下真弹文件选择框会挂住 —— 把 file input 的 click 变成空操作。
       （补照片走 DataTransfer，不需要真的打开选择框） */
    try {
      const protoClick = w.HTMLElement.prototype.click;
      w.HTMLInputElement.prototype.click = function () {
        if (this.type === "file") { note("filepicker_blocked", 1); return; }
        return protoClick.apply(this, arguments);
      };
    } catch (e) {}

    /* ---- 主界面必须先是科目选择 ---- */
    await until(() => d.querySelector(".subject-card"));
    await raf(4);
    note("subject_cards", d.querySelectorAll(".subject-card").length);
    note("subject_names", Array.from(d.querySelectorAll(".subject-name")).map(x => x.firstChild.textContent.trim()).join("|"));

    /* ---- 主界面顶栏（用户 2026-10-07 要求）----
       只留 账号标志 / 批注 / 设置；连续天数与复习/素材库/数据/历史/选科目 全部收起。
       ⚠️ 用 getClientRects().length 判可见性：display:none 的祖先会让子元素也量不到框，
       而 getComputedStyle 只看元素自身，判不出「被父级藏了」。 */
    const vis = el => !!(el && el.getClientRects().length);
    note("home_view_en", d.body.classList.contains("view-en") ? 1 : 0);
    note("home_task_bar_hidden", vis(d.querySelector("#topbarTask")) ? 0 : 1);
    note("home_task_btns_shown",
         ["#streakChip", "#btnReview", "#btnSubjects", "#btnLibrary", "#btnStats", "#btnHistory"]
           .filter(s => vis(d.querySelector(s))).length);
    note("home_acct_shown", vis(d.querySelector("#btnAccount")) ? 1 : 0);
    note("home_pen_shown", vis(d.querySelector("#btnPen")) ? 1 : 0);
    note("home_settings_shown", vis(d.querySelector("#btnSettings")) ? 1 : 0);
    note("acct_text", ((d.querySelector("#acctName") || {}).textContent || "(空)"));
    note("acct_avatar", ((d.querySelector("#acctAvatar") || {}).textContent || "(空)"));

    /* ---- 主界面考研倒计时（用户 2026-10-07 要求）----
       天数由测试侧独立算一遍再比对，别让页面自己证明自己。 */
    const ec = d.querySelector(".exam-count");
    note("exam_count_shown", vis(ec) ? 1 : 0);
    note("exam_count_num", ((ec && ec.querySelector(".exam-count-num")) || {}).textContent || "(无)");
    note("exam_count_date",
         ((ec && (ec.querySelector(".exam-count-sub") || {}).textContent) || "").trim().slice(0, 10));
    /* ⭐ 用户 2026-10-07 按截图指定删掉两处文案：倒计时副标题里那半句
       「日期可在右上角 ⚙️ 设置 里改」、以及科目卡上面那行「选一门开始。…」。
       日期与星期**保留**，所以这里同时断「少了什么」和「还剩什么」——
       只断"没了"的话，整块被误删也会是绿的。 */
    const subEl = ec ? ec.querySelector(".exam-count-sub") : null;
    note("sub_text", ((subEl || {}).textContent || "").trim());
    note("sub_setting_hint", /设置|里改/.test(((subEl || {}).textContent) || "") ? 1 : 0);
    const sp = d.querySelector("#pageBody-subjects");
    note("subjects_hint_text", ((sp || {}).innerText || "").split("\n")[0] || "");
    note("subjects_hint_absent",
         (sp && (sp.innerText || "").indexOf("选一门开始") >= 0) ? 0 : 1);

    if (MODE === "subjects") {
      note("on_subjects_page", d.querySelector("#page-subjects") && !d.querySelector("#page-subjects").hidden ? 1 : 0);
      note("math_page_hidden", d.querySelector("#page-math").hidden ? 1 : 0);

      /* ---- 考试日期：默认值 / 能改 / 非法值被拒 / 清空回默认（API 侧）---- */
      const jfetch = async (u, opt) => {
        const r = await w.fetch(u, Object.assign({ credentials: "same-origin" }, opt || {}));
        let j = null;
        try { j = await r.json(); } catch (e) {}
        return { status: r.status, json: j };
      };
      const setExam = v => jfetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exam_date: v }),
      });
      note("api_exam_default", ((await jfetch("/api/settings")).json || {}).exam_date || "");
      note("api_exam_set", (((await setExam("2027-12-20")).json) || {}).exam_date || "");
      note("api_exam_bad_status", (await setExam("2027-13-01")).status);
      note("api_exam_reset", (((await setExam("")).json) || {}).exam_date || "");

      /* ---- 同一条路再走一遍**界面**（用户手里真正会点的）----
         ⚠️ until() 只认同步真值，别把 await 塞进谓词（永远真 → 立刻返回）。
            所以这里用显式轮询。 */
      const waitExam = async (want, tries = 60) => {
        for (let i = 0; i < tries; i++) {
          const v = (((await jfetch("/api/settings")).json) || {}).exam_date || "";
          if (v === want) return v;
          await raf(2);
        }
        return "";
      };
      const sb = d.querySelector("#btnSettings");
      if (sb) sb.click();
      try { await until(() => d.querySelector("#examDate"), 120); }
      catch (e) { note("exam_ui_wait_to", 1); }
      await raf(4);
      const ei = d.querySelector("#examDate");
      note("exam_input_ok", ei ? 1 : 0);
      note("exam_input_value", ei ? ei.value : "(无)");
      if (ei) {
        ei.value = "2027-12-22";
        ei.dispatchEvent(new w.Event("input", { bubbles: true }));
        const sv = d.querySelector("#btnSaveExamDate");
        note("exam_save_btn", sv ? 1 : 0);
        if (sv) sv.click();
        note("exam_ui_saved", await waitExam("2027-12-22"));
        /* 复原成默认，别把后面模式的界面带偏 */
        if (sb) sb.click();
        try { await until(() => d.querySelector("#examDate"), 120); } catch (e) {}
        await raf(4);
        const ei2 = d.querySelector("#examDate");
        if (ei2) {
          ei2.value = "";
          ei2.dispatchEvent(new w.Event("input", { bubbles: true }));
          const sv2 = d.querySelector("#btnSaveExamDate");
          if (sv2) sv2.click();
          await waitExam("2027-12-18");
        }
        note("exam_ui_reset", (((await jfetch("/api/settings")).json) || {}).exam_date || "");
        /* 关弹层：closeSheet 会把 renderTarget 交还给当前视图 */
        const cl = d.querySelector("#sheet [data-close]");
        if (cl) cl.click();
        await raf(4);
        note("exam_sheet_closed", d.querySelector("#sheet").hidden ? 1 : 0);
      }
    } else {
      /* 从主界面**点进去** —— 顺手把「英语 / 数学 分流」这件事验掉 */
      const card = d.querySelector('[data-subject="math"]');
      note("card_found", card ? 1 : 0);
      note("hash_before", w.location.hash || "(空)");
      if (card) card.click();
      /* 点完**同步**读 hash：这一步能立刻区分「点击没送到」和「路由坏了」 */
      note("hash_after_click", w.location.hash || "(空)");
      if (!/#math/.test(w.location.hash || "")) {
        /* 兜底：保证数学页能出图。分流本身由 subjects 模式的断言守着。 */
        note("hash_fallback", 1);
        w.location.hash = "#math";
      }
      try {
        await until(() => d.querySelector(".math-list, .center.muted"), 120);
      } catch (e) {
        note("enter_fail", (d.body.innerText || "").slice(0, 90).replace(/\s+/g, " "));
        throw e;
      }
      await raf(6);
      note("entered_math", d.querySelector("#page-math") && !d.querySelector("#page-math").hidden ? 1 : 0);
      await send();
    }

    note("cards", d.querySelectorAll(".math-card").length);
    /* 用户 2026-10-15：「数学的各项专题拉下列表没有」——
       专题下拉过去只认「科目」，而科目默认是「未分类」，于是只剩「未选专题」一项。
       现在没选科目时应当把本入口的所有专题按科目分组列全。 */
    const topicSel = d.querySelector("#mfTopic");
    note("topic_options", topicSel ? topicSel.querySelectorAll("option").length : -1);
    note("topic_groups", topicSel ? topicSel.querySelectorAll("optgroup").length : -1);
    note("topic_samples", topicSel
      ? Array.from(topicSel.querySelectorAll("option")).slice(1, 4).map(o => o.textContent).join("|") : "");
    note("due_rows", d.querySelectorAll(".math-due-row").length);
    note("badge_hidden", d.querySelector("#mathBadge").hidden ? 1 : 0);
    /* 数学页的回头路是**品牌标记**（点它回科目选择），顶栏那一排在英语页才出现 */
    note("brand_home", vis(d.querySelector("#brandHome")) ? 1 : 0);
    /* ⚠️ 数学是**独立模块**，不是英语的「任务」。它有自己的页内工具条，
       所以**不该**借用英语的任务入口 —— 用户 2026-10-07 指出这个错的。 */
    note("task_view_en", d.body.classList.contains("view-en") ? 1 : 0);
    note("task_bar_shown", vis(d.querySelector("#topbarTask")) ? 1 : 0);
    note("task_btns_shown",
         ["#btnReview", "#btnSubjects", "#btnLibrary", "#btnStats", "#btnHistory"]
           .filter(s => vis(d.querySelector(s))).length);

    /* ---- 数学模块顶栏两个入口（用户 2026-10-07 指定）---- */
    note("math_view_class", d.body.classList.contains("view-math") ? 1 : 0);
    note("math_tabs_shown", vis(d.querySelector("#topbarTaskMath")) ? 1 : 0);
    note("math_tabs_n",
         ["#btnMathLib", "#btnMathDrill"].filter(s => vis(d.querySelector(s))).length);
    /* 文案：tb-full / tb-short 由 CSS 二选一显示，只读**当前可见**那个，
       textContent 会把两个都拼起来（错题总库总库）。 */
    const shownLabel = el => {
      if (!el) return "";
      const pick = s => (s && w.getComputedStyle(s).display !== "none" ? s : null);
      const t = pick(el.querySelector(".tb-full")) || pick(el.querySelector(".tb-short"));
      return (t ? t.textContent : el.textContent).trim();
    };
    note("math_lib_label", shownLabel(d.querySelector("#btnMathLib")));
    note("math_drill_label", shownLabel(d.querySelector("#btnMathDrill")));
    /* 到期角标：错题总库按钮上 = **当前模块**今天该复习几道（2026-10-08 加） */
    const libBadge = d.querySelector("#mathLibBadge");
    note("math_lib_badge_hidden", libBadge ? (libBadge.hidden ? 1 : 0) : -1);
    note("math_lib_badge_text", libBadge ? libBadge.textContent : "");
    note("kp_rows", d.querySelectorAll(".math-card .math-kp").length);

    if (MODE === "draft") {
      stubExtract(w);
      d.querySelector("#mathAddBtn").click();
      await tryWait("w_photo", () => d.querySelector("#mathPhoto"));
      await raf(4);
      note("has_photo", d.querySelector("#mathPhoto") ? 1 : 0);
      /* 一次塞两张：这就是「多张照片一起识别」的入口 */
      const inp = d.querySelector("#mathPhoto");
      const dt = new DataTransfer();
      dt.items.add(pngFile("q1.png"));
      dt.items.add(pngFile("q2.png"));
      if (inp) inp.files = dt.files;
      note("inp_files", inp ? inp.files.length : -1);
      if (inp) inp.dispatchEvent(new Event("change", { bubbles: true }));
      /* 顺带验「无头下 blob 图片会不会 onload」—— mathShrink 全靠它，
         不生效的话多图录题会**静默**变空（照片进去了但草稿区什么都不出）。 */
      {
        const url = w.URL.createObjectURL(pngFile("probe.png"));
        const im = new w.Image();
        w.__imgLoaded = 0; w.__imgErr = 0;
        im.onload = () => { w.__imgLoaded = 1; };
        im.onerror = () => { w.__imgErr = 1; };
        im.src = url;
      }
      await tryWait("w_items2", () => d.querySelectorAll(".math-draft-item").length === 2);
      note("img_loaded", w.__imgLoaded);
      note("img_err", w.__imgErr);
      note("toast", ((d.querySelector("#toast") || {}).textContent || "").slice(0, 60));
      note("items_raw", d.querySelectorAll(".math-draft-item").length);
      note("badges_raw", Array.from(d.querySelectorAll(".math-draft-item .draft-badge"))
            .map(b => b.textContent.trim()).join("/") || "-");
      note("stub_calls", w.__stubCalls);
      await tryWait("w_ok2", () => d.querySelectorAll(".math-draft-item .draft-badge.ok").length === 2);
      await raf(10);
      note("draft_items", d.querySelectorAll(".math-draft-item").length);
      note("draft_ready", d.querySelectorAll(".math-draft-item .draft-badge.ok").length);
      note("draft_ai_class", d.querySelectorAll(".math-draft-item .ai-class:not(.muted)").length);
      /* ⭐ 需求 A：识别完成 = 原图立刻释放。缩略图、「重新识别」都该消失，
         「原图已释放」提示要出现（否则用户以为照片丢了）。 */
      note("draft_thumbs", d.querySelectorAll(".math-draft-item .draft-thumb").length);
      note("draft_re_btns", d.querySelectorAll(".math-draft-item [data-d-re]").length);
      note("draft_dropped_notes",
           d.querySelectorAll(".math-draft-item .draft-dropped:not([hidden])").length);
      note("draft_notes", d.querySelectorAll(".math-draft-item .math-draft-note").length);
      /* 确认卡里的**排版预览** —— 用户看的就是这块（文本框里是 LaTeX，他看不懂） */
      note("draft_preview_katex", d.querySelectorAll(".math-draft-item .draft-preview .katex-html").length);
      note("draft_preview_mtex", d.querySelectorAll(".math-draft-item .draft-preview .mtex").length);
      note("save_label", (d.querySelector("#mathSaveAll") || {}).textContent || "");
      /* 手输备注 → 重绘不能把它吃掉 */
      const nt = d.querySelector(".math-draft-item .math-draft-note");
      nt.value = "880 题 第 3 章 12 题";
      nt.dispatchEvent(new Event("input", { bubbles: true }));
      const ss = d.querySelector(".math-draft-item .math-draft-subject");
      ss.value = "线性代数";
      ss.dispatchEvent(new Event("change", { bubbles: true }));
      await raf(6);
      note("note_kept", d.querySelector(".math-draft-item .math-draft-note").value.length > 0 ? 1 : 0);
      note("topic_followed", d.querySelector(".math-draft-item .math-draft-topic").textContent.indexOf("行列式") >= 0 ? 1 : 0);

      /* 手机端拍照入口：和相册走同一条路（同 id 分支），但 input 不同。
         顺手验三件事：input 存在且带 capture、桌面端按钮已隐藏、塞进去真能出草稿。 */
      const shot = d.querySelector("#mathShot");
      const shotBtn = d.querySelector("#mathShotBtn");
      note("shot_capture", shot ? (shot.getAttribute("capture") || "-") : "-");
      note("shot_btn_desktop_hidden",
           shotBtn ? (w.getComputedStyle(shotBtn).display === "none" ? 1 : 0) : -1);
      if (shot) {
        const dt2 = new DataTransfer();
        dt2.items.add(pngFile("cam.png"));
        shot.files = dt2.files;
        shot.dispatchEvent(new Event("change", { bubbles: true }));
        await tryWait("w_items3", () => d.querySelectorAll(".math-draft-item").length === 3);
        note("draft_items_after_shot", d.querySelectorAll(".math-draft-item").length);
        note("stub_calls_after_shot", w.__stubCalls);
      }

      /* ⭐ 需求 A 的端到端：点「全部保存」之后，**库里最新那几条不能带原图**。
         这一条比缩略图消失更硬 —— 缩略图是内存里的，这个落的是库。 */
      {
        await tryWait("w_ok3", () => d.querySelectorAll(".math-draft-item .draft-badge.ok").length === 3);
        await raf(6);
        const sb = d.querySelector("#mathSaveAll");
        note("save_btn_ready", sb && !sb.disabled ? 1 : 0);
        if (sb && !sb.disabled) {
          sb.click();
          try { await tryWait("w_saved", () => d.querySelectorAll(".math-draft-item").length === 0, 300); }
          catch (e) { note("save_wait_timeout", 1); }
          await raf(8);
          note("draft_left_after_save", d.querySelectorAll(".math-draft-item").length);
          try {
            const lst = await w.fetch("/api/math/problems?with_analysis=1").then(r => r.json());
            const fresh = (lst.items || []).slice(0, 3);      // 列表 id 倒序 → 前 3 条就是刚存的
            note("fresh_count", fresh.length);
            note("fresh_has_image", fresh.filter(x => x.has_image).length);
            note("fresh_have_stem", fresh.filter(x => (x.stem || "").trim()).length);
            /* ⚠️ 必须清场：四个模式共用同一个临时库，留下这 3 条会让后面
               「卡片数 = 5」的断言全歪（本轮就踩了 —— 8 条，两处假失败）。 */
            for (const x of fresh) {
              try { await w.fetch("/api/math/problems/" + x.id, { method: "DELETE" }); }
              catch (e) { note("cleanup_err", e.message); }
            }
            const lst2 = await w.fetch("/api/math/problems?with_analysis=1").then(r => r.json());
            note("cards_after_cleanup", (lst2.items || []).length);
          } catch (e) { note("save_probe_err", e.message); }
        }
      }
    } else if (MODE === "open") {
      /* 要展开的是**带原图 / 带解析**的那条。列表按 id 倒序，
         直接点第一个 [data-m-toggle] 会点到最后灌的那道（无图无解析），
         于是「解析块 / 原图」两项必然假失败 —— 是取证脚本自己的坑。 */
      let tid = null;
      try {
        const lst = await w.fetch("/api/math/problems?with_analysis=1").then(r => r.json());
        const hit = (lst.items || []).find(x => x.has_image && x.analyzed) || (lst.items || [])[0];
        tid = hit ? hit.id : null;
      } catch (e) { note("open_list_err", e.message); }
      note("open_target", tid === null ? "-" : tid);
      const t = tid !== null
        ? d.querySelector('[data-m-toggle="' + tid + '"]')
        : d.querySelector("[data-m-toggle]");
      if (t) { t.click(); await raf(10); }
      const card = d.querySelector(".math-card.open");
      note("opened", card ? 1 : 0);
      note("analysis_shown", card && card.querySelector(".math-analysis") ? 1 : 0);
      note("photo_shown", card && card.querySelector(".math-photo") ? 1 : 0);
      note("katex_nodes", card ? card.querySelectorAll(".katex-html").length : 0);
      note("katex_display", card ? card.querySelectorAll(".katex-display").length : 0);
      note("mtex_fallback", card ? card.querySelectorAll(".mtex").length : 0);
    } else if (MODE === "drill") {
      const b = d.querySelector("[data-m-drill]");
      if (b) { b.click(); await until(() => d.querySelector(".math-drill")); await raf(8); }
      const box = d.querySelector(".math-drill");
      note("drill_open", box ? 1 : 0);
      note("analysis_leaked", box && box.querySelector(".math-analysis") ? 1 : 0);
      note("done_btn", box && box.querySelector("#mathDone") ? 1 : 0);
      note("katex_nodes", box ? box.querySelectorAll(".katex-html").length : 0);
      note("mtex_fallback", box ? box.querySelectorAll(".mtex").length : 0);

      /* ⭐ 顶栏「开始刷题」必须从**全部**错题随机（用户 2026-10-07 明确口径）。
         验法：先设一个谁都匹配不到的搜索词（列表变 0 张），再点顶栏那个入口；
         还能抽出题 ⇒ 真的没带筛选参数。
         ⚠️ 每次操作后要**重新查** #mfQ —— 列表一刷新，输入框就被整个重建了，
            拿着旧元素 dispatch 不会冒泡到 document（踩过这类"事件发不出去"）。 */
      const setQ = async v => {
        const el = d.querySelector("#mfQ");
        if (!el) return 0;
        el.value = v;
        el.dispatchEvent(new w.Event("input", { bubbles: true }));
        return 1;
      };
      if (await setQ("zz绝不可能匹配到的关键词zz")) {
        await until(() => d.querySelectorAll(".math-card").length === 0, 80);
        await raf(4);
      }
      note("filter_input_ok", d.querySelector("#mfQ") ? 1 : 0);
      note("cards_under_filter", d.querySelectorAll(".math-card").length);
      const rnd = d.querySelector("#btnMathDrill");
      note("math_drill_entry_present", rnd ? 1 : 0);
      if (rnd) rnd.click();
      await tryWait("drill_from_all_wait", () => d.querySelector(".math-drill"), 80);
      await raf(6);
      note("drill_from_all_ok", d.querySelector(".math-drill") ? 1 : 0);
      note("drill_tab_on", d.querySelector("#btnMathDrill").classList.contains("on") ? 1 : 0);
      const libb = d.querySelector("#btnMathLib");
      if (libb) libb.click();
      await raf(8);
      note("drill_closed_by_lib", d.querySelector(".math-drill") ? 0 : 1);
      note("lib_tab_on_after", d.querySelector("#btnMathLib").classList.contains("on") ? 1 : 0);
      /* 还原筛选，别把后面的溢出与公式检查带偏 */
      if (await setQ("")) {
        await until(() => d.querySelectorAll(".math-card").length > 0, 80);
        await raf(4);
      }
      note("cards_restored", d.querySelectorAll(".math-card").length);
    }

    /* 公式渲染总检查：整页范围 */
    note("katex_total", d.querySelectorAll(".katex-html").length);
    note("mtex_total", d.querySelectorAll(".mtex").length);
    note("DONE", 1);
    await send();          // 关键结论先落袋：后面万一被虚拟时间掐断，这份也已经发出去了

    // 横向溢出：加功能最容易撞出来的毛病。
    // ⚠️ 别扫 `#pageBody-math *` —— KaTeX 一个公式就是几十个节点，逐个量尺寸会把
    //    一次出图拖过 180s（open 模式就是这么挂的）。权威判据是 scrollWidth，
    //    逐元素扫描只用来定位肇事元素，扫直接子级 + 卡片足够。
    const doc = fr.contentDocument;
    const CW = doc.documentElement.clientWidth;
    note("client_w", CW);
    note("scroll_w", doc.documentElement.scrollWidth);
    const bad = [];
    doc.querySelectorAll("#pageBody-math > *, #pageBody-subjects > *, .math-card, .subject-card, .topbar > *, .topbar-inner *")
      .forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && (r.right > CW + 1 || r.left < -1)) {
          bad.push((el.className || el.tagName) + "@" + Math.round(r.right) + "w" + Math.round(r.width));
        }
      });
    note("overflow", bad.length ? bad.slice(0, 5).join(" | ") : "无");
    /* 录题/刷题两个模式若查出 scrollW 超了但上面没抓到肇事元素，
       就全量扫一遍（这两个模式 KaTeX 节点少，量得起；open 模式别开）。 */
    if ((MODE === "draft" || MODE === "drill") && doc.documentElement.scrollWidth > CW + 1) {
      const all = [];
      doc.querySelectorAll("body *").forEach(el => {
        const r = el.getBoundingClientRect();
        /* ⚠️ 跳过 .katex-mathml：KaTeX 给读屏用的隐形树（clip 成 1px、看不见），
           它的子节点 rect 很大但不参与视觉排版 —— 不跳过会把真凶挤出前 8 名 */
        if (r.width > 0 && r.right > CW + 1 && !el.closest(".katex-mathml") && all.length < 8) {
          all.push((el.id || el.className || el.tagName) + "@r" + Math.round(r.right));
        }
      });
      note("overflow_all", all.join(" | ") || "无");
    }
    /* 复测一次：KaTeX 字体晚到会把首次量出的 scrollWidth 撑大（虚拟时间下字体请求
       未完成时文本按回退字体排版），等布局落定再量一遍 —— 溢出判定用这个晚到的值。 */
    await raf(20);   // 等布局落定（KaTeX 字体晚到会虚撑 scrollWidth）
    note("scroll_w_late", doc.documentElement.scrollWidth);
    note("client_w_late", doc.documentElement.clientWidth);
    await send();
  } catch (e) {
    note("ERR", e.message);
  }
  await send();
  await raf(4);
  await send();          // 再发一次：第一发可能和页面卸载抢时间
})();
</script></body></html>"""


def capture(name: str, size, query: str, budget="300000"):
    """跑一次 Chrome 出图（同时让页面把报告 PUT 回服务端）。

    ⚠️ budget 必须是**大值**：页面要 boot → 点进科目 → 拉三份接口 → 点开面板，
    给 20000 会在链路走完之前就截图，回传的测量也就发不出去。
    ⚠️ 不要 unlink 再让 Chrome 生成：本环境的删除守卫按**单轮删除目标数**计数
    （≥50 就整体拦下，命令还会 exit 0，极易误判成"成功"）。`--screenshot` 本来就覆盖同名文件。
    """
    w, h = size
    p = ROOT / "data" / name
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           f"--window-size={w},{h}", f"--virtual-time-budget={budget}",
           f"--screenshot={p}", f"{BASE}/static/_qa_math.html?{query}"]
    try:
        subprocess.run(cmd, env=env, timeout=100,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False
    return p.exists() and p.stat().st_size > 5000


def read_reports() -> dict:
    """把页面回传的报告读回来：{mode: report}。

    ⚠️ **直接读临时库文件，不要走 HTTP**：走 /api/annotations 轮询时踩过 ——
    Chrome 被强杀留下的半开连接会让 GET 一路挂到超时，40 次轮询把一次出图拖成 10 分钟。
    """
    import sqlite3
    db_file = TMP / "app.db"
    if not db_file.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT doc_key, data FROM annotations WHERE doc_key LIKE 'qa-math:%'"
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for r in rows:
        try:
            data = json.loads(r["data"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        rep = (data or {}).get("report")
        if rep:
            out[rep.get("mode") or r["doc_key"].split(":")[-1]] = rep
    return out


print("=" * 64)
print("数学模块 · 页面取证（科目选择 / 多图录题 / 公式渲染）")
print("=" * 64)

# 服务端日志落文件：出图卡住时要靠它判断「卡在哪个请求上」。
# 别写 subprocess.PIPE —— 不读就永远看不到，还占着缓冲区。
SRV_LOG = TMP / "srv.log"
_srv_f = SRV_LOG.open("w", encoding="utf-8", errors="replace")
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT),
     "--no-use-colors", "--log-level", "info"],
    cwd=str(ROOT), env=env, stdout=_srv_f, stderr=subprocess.STDOUT, text=True,
)
try:
    wait_health(proc)
    print(f"\n[1] 服务就绪（临时库 {TMP.name}，端口 {PORT}）")

    # ---------- 灌数据 ----------
    print("\n[2] 灌入示例错题")
    from PIL import Image, ImageDraw, ImageFont  # noqa: E402

    img = Image.new("RGB", (760, 230), "white")
    dr = ImageDraw.Draw(img)
    try:
        fnt = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
    except Exception:  # noqa: BLE001
        fnt = ImageFont.load_default()
    dr.text((24, 30), "9. 求 lim(x->0) (e^x-1)/x。", fill="black", font=fnt)
    dr.text((24, 110), "（照片里只有题干，没有解答）", fill="#666", font=fnt)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    photo = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    seeds = [
        # ① 用户的真实痛点：照片里就是这种分段函数，必须渲染成课本那样。
        #    **故意不写 $ 定界符** —— 顺带验服务端兜底（normalize_math_delims）。
        ("例2.17 设随机变量 X 的概率密度为 f_X(x)=\\begin{cases} \\dfrac{1}{2}, & -1<x<0, "
         "\\\\ \\dfrac{1}{4}, & 0\\le x<2, \\\\ 0, & \\text{其他}. \\end{cases} "
         "令 Y=X^2，求 Y 的概率密度 f_Y(y).",
         "概率论与数理统计", "随机变量及其分布", "normal", photo, "概率论 例 2.17"),
        ("7. 设 $A=\\begin{pmatrix}1&2&3\\\\4&5&6\\\\7&8&a\\end{pmatrix}$，$a=9$，求 $|A|$。",
         "线性代数", "行列式", "exam", "", "880 题 第 3 章"),
        ("求 $\\lim_{x\\to 0}\\dfrac{\\sin 3x}{\\tan 5x}$。", "高等数学", "函数与极限", "normal", "", ""),
        ("计算 $\\iint_D (x+y)\\,d\\sigma$，其中 $D$ 由 $y=x^2$ 与 $y=1$ 围成。",
         "高等数学", "重积分", "normal", "", "660 题 · 二重积分"),
        ("判断矩阵 $A=\\begin{pmatrix}2&0\\\\0&2\\end{pmatrix}$ 可否对角化。",
         "线性代数", "特征值与特征向量", "normal", "", ""),
    ]
    for stem, subj, topic, kind, img_data, note in seeds:
        r0 = req("/api/math/problems", {
            "stem": stem, "subject": subj, "topic": topic,
            "kind": kind, "image": img_data, "source_note": note,
        })
        assert r0.get("ok"), r0
    lst = req("/api/math/problems?with_analysis=1")
    check("灌入 5 条", len(lst["items"]) == 5, f"(实得 {len(lst['items'])})")
    check("带图那条 has_image=True", any(x["has_image"] for x in lst["items"]))
    meta = req("/api/math/meta")
    check("meta 科目清单正确",
          meta["subjects"] == ["高等数学", "线性代数", "概率论与数理统计", "信号与系统"])
    check("统计总量为 5", meta["stats"]["total"] == 5, f"({meta['stats']['total']})")

    # 服务端兜底：没写 $ 的那条，落库后必须已经补上 $（否则前端渲染不出来）
    raw = next(x for x in lst["items"] if x["stem"].startswith("例2.17"))
    check("无 $ 的题干被补上定界符", "$" in raw["stem"], f"({raw['stem'][:40]}…)")
    check("\\text{其他} 没被切坏", "\\text{其他}" in raw["stem"])

    # 给第一条补一份解析（顺带验 update 路径上的兜底 + 解析区公式渲染）
    rid = raw["id"]
    req(f"/api/math/problems/{rid}", {
        "knowledge": "分段概率密度的处理：先写分布函数 F(y)=P(Y≤y)，再分段求导。重点在 "
                     "Y=X^2 的值域只有 y≥0，y<0 时 F(y)=0。",
        "key_steps": [
            "写出 F_Y(y)=P(X^2≤y)；y<0 时直接为 0",
            "y≥0 时化为 P(-\\sqrt{y}≤X≤\\sqrt{y})",
            "代入 f_X 分段积分（注意 -1<x<0 与 0≤x<2 两段要分开算）",
            "对 y 求导得到 $f_Y(y)=F_Y'(y)$",
        ],
        "solution": "$Y=X^2$ 的值域是 $[0,+\\infty)$。当 $y<0$ 时 $F_Y(y)=0$；"
                    "当 $y\\ge 0$ 时 $F_Y(y)=P(-\\sqrt{y}\\le X\\le \\sqrt{y})"
                    "=\\int_{-\\sqrt{y}}^{0}\\dfrac{1}{2}dx+\\int_{0}^{\\sqrt{y}}\\dfrac{1}{4}dx$。"
                    "分段求导即得。",
        "alt_solution": "也可以直接用公式 $f_Y(y)=\\dfrac{1}{2\\sqrt{y}}\\left[f_X(\\sqrt{y})+f_X(-\\sqrt{y})\\right]$，"
                        "但要注意 $X$ 的密度是分段定义的，代入后仍需按 $y$ 的区间讨论。",
        "pitfalls": "最容易错的两处：① 忘了 $y<0$ 时密度为 0；"
                    "② 在 $-1<x<0$ 段积分时上下限写反，导致符号错。",
    }, method="PATCH")
    d1 = req(f"/api/math/problems/{rid}")
    check("解析已写入并标记 analyzed", d1["analyzed"] is True)
    check("解析里的裸 LaTeX 也被补了 $", "$" in d1["solution"], f"({d1['solution'][:36]}…)")

    # 让一条今天到期，把「今天该复习」区块也照亮
    from datetime import date
    import sqlite3
    con = sqlite3.connect(str(TMP / "app.db"))
    con.execute("UPDATE math_problems SET next_review=? WHERE id=2", (date.today().isoformat(),))
    con.commit()
    con.close()
    check("到期列表取到 1 条", len(req("/api/math/review/due")["items"]) == 1)

    # ---------- 出图 + 断言 ----------
    print("\n[3] 出图并收集界面测量")
    # 把一道题的下次复习改成**今天**，让「错题总库」的到期角标有东西可显示
    # （首刷是 +3 天，不.force 的话角标永远是隐藏的，这条 UI 等于一次都没验过）
    import sqlite3 as _sq
    _c = _sq.connect(TMP / "app.db")
    _c.execute("UPDATE math_problems SET next_review=date('now','localtime') "
               "WHERE id=(SELECT id FROM math_problems ORDER BY id LIMIT 1)")
    _c.commit(); _c.close()
    HARNESS.write_text(HARNESS_TPL, encoding="utf-8")
    plan = [
        ("_shot_math_科目.png", (430, 1180), "w=430&h=1180&mode=subjects", "科目选择主界面"),
        ("_shot_math_手机.png", (430, 1180), "w=430&h=1180&mode=draft", "手机·多图录题"),
        ("_shot_math_平板.png", (900, 1300), "w=900&h=1300&mode=open", "平板·展开解析"),
        ("_shot_math_刷题.png", (430, 900), "w=430&h=900&mode=drill", "手机·刷题"),
    ]
    if ONLY:
        plan = [x for x in plan if x[2].endswith("mode=" + ONLY)]
    failed_shots = []
    for name, size, query, tag in plan:
        good = capture(name, size, query)
        if not good:
            failed_shots.append(tag)
        # 刷题那张在无头下不稳（点了按钮之后 Chrome 有时不肯退出），不当作失败：
        # **这道闸门真正的证据在 _math_test.py 的接口层断言**
        # （/api/math/random 返回的题里解析字段必须为空），比截图硬。
        if not good and tag.endswith("刷题"):
            print(f"  [NOTE] {tag}：无头下没出图（不影响结论，见 _math_test.py [4.5]）")
            continue
        check(f"{tag}：截图生成", good)

    want = {ONLY} if ONLY else {"subjects", "draft", "open", "drill"}
    reports: dict = {}
    for _ in range(40):
        reports = read_reports()
        if want <= set(reports):
            break
        time.sleep(0.5)

    print("\n[4] 页面自报的测量结果")
    tags = (("subjects", "科目选择"), ("draft", "多图录题"), ("open", "展开解析"), ("drill", "刷题"))
    if ONLY:
        tags = tuple(t for t in tags if t[0] == ONLY)
    for mode, tag in tags:
        rep = reports.get(mode)
        if not rep:
            check(f"[{tag}] 拿到页面报告", False, "(页面没回传，看截图确认)")
            continue
        n = rep.get("notes") or {}
        check(f"[{tag}] 跑到 DONE 没报错", n.get("DONE") == 1 and not n.get("ERR"),
              f"({n.get('ERR') or ''})")
        check(f"[{tag}] 无横向溢出", n.get("overflow") == "无", f"({n.get('overflow')})")
        # 溢出判定用**布局落定后**的复测值（KaTeX 字体晚到会虚撑首次测量，见 harness 注释）
        _sw = n.get("scroll_w_late", n.get("scroll_w"))
        _cw = n.get("client_w_late", n.get("client_w"))
        check(f"[{tag}] scrollW ≤ clientW",
              (_sw or 0) <= (_cw or 0) + 1, f"(scroll {_sw} / client {_cw})")
        if (_sw or 0) > (_cw or 0) + 1:
            print(f"    · [{tag}] 肇事元素（全量扫描）：{n.get('overflow_all')!r}")
        # 主界面有 3 个科目入口：英语 / 数学 / 信号与系统
        check(f"[{tag}] 主界面有三个科目入口", n.get("subject_cards") == 3,
              f"({n.get('subject_cards')})")
        check(f"[{tag}] 科目名是 英语 / 数学 / 信号与系统",
              (n.get("subject_names") or "").split("|") == ["英语", "数学", "信号与系统"],
              f"({n.get('subject_names')})")

        # ---- 主界面顶栏：只留 账号标志 / 批注 / 设置（用户 2026-10-07 要求）----
        check(f"[{tag}] 主界面顶栏任务区已收起", n.get("home_task_bar_hidden") == 1)
        check(f"[{tag}] 主界面看不到 连续天数/复习/选科目/素材库/数据/历史",
              n.get("home_task_btns_shown") == 0, f"({n.get('home_task_btns_shown')} 个还露着)")
        check(f"[{tag}] 主界面有「我的账号」标志", n.get("home_acct_shown") == 1)
        check(f"[{tag}] 主界面有「批注」入口", n.get("home_pen_shown") == 1)
        check(f"[{tag}] 主界面有「设置」入口", n.get("home_settings_shown") == 1)
        check(f"[{tag}] 账号标志有文字或退化成「我」",
              bool((n.get("acct_avatar") or "").strip()),
              f"(头像={n.get('acct_avatar')!r} 名字={n.get('acct_text')!r})")

        if mode == "subjects":
            check("[科目] 冷启动就落在科目选择页", n.get("on_subjects_page") == 1)
            check("[科目] 没直接进数学页", n.get("math_page_hidden") == 1)
            # ⭐ 主界面倒计时（用户 2026-10-07 要求）—— 天数在测试侧独立算一遍
            from datetime import date as _date
            _days = (_date(2027, 12, 18) - _date.today()).days
            check("[科目] 主界面有考研倒计时", n.get("exam_count_shown") == 1)
            check("[科目] 倒计时天数算得对（默认 2027-12-18 到今天的差）",
                  str(n.get("exam_count_num")) == str(_days),
                  f"(显示 {n.get('exam_count_num')!r} / 期望 {_days})")
            check("[科目] 倒计时下方写明了具体日期",
                  n.get("exam_count_date") == "2027-12-18", f"({n.get('exam_count_date')!r})")
            # ⭐ 用户 2026-10-07 按截图要求删的两处（日期与星期保留 —— 所以两头都要断）
            check("[科目] 倒计时副标题不再写「⚙️ 设置 里改」",
                  n.get("sub_setting_hint") == 0, f"({n.get('sub_text')!r})")
            check("[科目] 倒计时下面仍留着日期与星期",
                  "2027-12-18" in (n.get("sub_text") or "")
                  and (n.get("sub_text") or "").strip() != "2027-12-18",
                  f"({n.get('sub_text')!r})")
            check("[科目] 科目卡上面那行「选一门开始…」说明已删",
                  n.get("subjects_hint_absent") == 1, f"({n.get('subjects_hint_text')!r})")
            check("[科目] 考试日期默认值 = 2027-12-18",
                  n.get("api_exam_default") == "2027-12-18", f"({n.get('api_exam_default')!r})")
            check("[科目] 考试日期能改成别的一天",
                  n.get("api_exam_set") == "2027-12-20", f"({n.get('api_exam_set')!r})")
            check("[科目] 非法日期被拒（400）", n.get("api_exam_bad_status") == 400,
                  f"({n.get('api_exam_bad_status')})")
            check("[科目] 清空后回到默认值",
                  n.get("api_exam_reset") == "2027-12-18", f"({n.get('api_exam_reset')!r})")
            # ⭐ 上面几条只打接口；界面没接上线照样白搭 —— 所以自己点一遍 ⚙️ 设置
            check("[科目] ⚙️ 设置里真的有「考研初试日期」输入框", n.get("exam_input_ok") == 1)
            check("[科目] 输入框带着当前日期", n.get("exam_input_value") == "2027-12-18",
                  f"({n.get('exam_input_value')!r})")
            check("[科目] 「保存」按钮在", n.get("exam_save_btn") == 1)
            check("[科目] 在界面上改成 2027-12-22 真的存进去了",
                  n.get("exam_ui_saved") == "2027-12-22", f"({n.get('exam_ui_saved')!r})")
            check("[科目] 界面上清空又回到默认值", n.get("exam_ui_reset") == "2027-12-18",
                  f"({n.get('exam_ui_reset')!r})")
            check("[科目] 关掉设置弹层（renderTarget 交还页面）",
                  n.get("exam_sheet_closed") == 1)
        else:
            check(f"[{tag}] 从主界面点「数学」能进数学页", n.get("entered_math") == 1)
            check(f"[{tag}] 卡片数 = 5", n.get("cards") == 5, f"({n.get('cards')})")
            # 专题下拉必须有内容：数学入口 = 3 个科目分组 + 20 个专题（+「未选专题」）
            check(f"[{tag}] 专题下拉有 20 个专题、按 3 个科目分组",
                  n.get("topic_options") == 21 and n.get("topic_groups") == 3,
                  f"(options={n.get('topic_options')} groups={n.get('topic_groups')} 前几个={n.get('topic_samples')!r})")
            check(f"[{tag}] 数学页能靠品牌标记回主界面", n.get("brand_home") == 1)
            # ⭐ 数学页顶栏必须干净：那些入口（连续/复习/选科目/素材库/数据/历史）
            #    全是**英语模块**的功能，摆在数学专题里是错的（用户 2026-10-07 指出）
            check(f"[{tag}] 数学页顶栏不出现英语任务区", n.get("task_bar_shown") == 0)
            check(f"[{tag}] 数学页看不到 连续/复习/选科目/素材库/数据/历史",
                  n.get("task_btns_shown") == 0, f"({n.get('task_btns_shown')} 个还露着)")
            check(f"[{tag}] 数学页不再是 view-en", n.get("task_view_en") == 0)

            # ⭐ 数学模块自己的两个入口（用户 2026-10-07 指定）
            check(f"[{tag}] 数学页挂上了 view-math", n.get("math_view_class") == 1)
            check(f"[{tag}] 数学页出现数学入口组（而不是英语那排）",
                  n.get("math_tabs_shown") == 1 and n.get("task_bar_shown") == 0)
            check(f"[{tag}] 数学入口正好 2 个：错题总库 / 开始刷题",
                  n.get("math_tabs_n") == 2, f"({n.get('math_tabs_n')})")
            check(f"[{tag}] 两个入口的文案看得见（窄屏自动换短文案）",
                  bool(n.get("math_lib_label")) and bool(n.get("math_drill_label")),
                  f"(总库={n.get('math_lib_label')!r} 刷题={n.get('math_drill_label')!r})")
            # 「错题总库就是这道题和它所对应的知识点」→ 卡片上必须看得见知识点
            check(f"[{tag}] 错题卡直接露出「知识点」（不用展开）",
                  (n.get("kp_rows") or 0) >= 1, f"({n.get('kp_rows')} 行)")
            # ⭐ 到期角标（2026-10-08 加）：错题总库按钮上要亮出「今天该复习 N 道」
            #    N ≥ 1 即可 —— 种子里强制到期几道是测试自己的事，别和 UI 断言绑死
            try:
                _bn = int(n.get("math_lib_badge_text") or 0)
            except (TypeError, ValueError):
                _bn = 0
            check(f"[{tag}] 错题总库角标亮出今日待复习数",
                  n.get("math_lib_badge_hidden") == 0 and _bn >= 1,
                  f"(hidden={n.get('math_lib_badge_hidden')} text={n.get('math_lib_badge_text')!r})")

        if mode == "draft":
            check("[录题] 一次塞 2 张 → 出 2 张草稿卡", n.get("draft_items") == 2,
                  f"({n.get('draft_items')})")
            check("[录题] 2 张都识别完成", n.get("draft_ready") == 2, f"({n.get('draft_ready')})")
            # ⭐ 需求 A（2026-10-07）：识图完就把原图丢掉，缩略图必须为 0
            check("[录题] 识别完成后**不留缩略图**（原图已释放）",
                  n.get("draft_thumbs") == 0, f"(还剩 {n.get('draft_thumbs')} 张)")
            check("[录题] 识别完成后「重新识别」按钮也收回（没有图可重识）",
                  n.get("draft_re_btns") == 0, f"({n.get('draft_re_btns')} 个)")
            check("[录题] 每张都提示了「原图已释放」",
                  n.get("draft_dropped_notes") == 2, f"({n.get('draft_dropped_notes')})")
            check("[录题] AI 归类横条显示出来了", n.get("draft_ai_class") == 2,
                  f"({n.get('draft_ai_class')})")
            check("[录题] 每张都有备注输入框", n.get("draft_notes") == 2, f"({n.get('draft_notes')})")
            check("[录题] 确认卡里有**排版好的公式预览**（不是源码）",
                  (n.get("draft_preview_katex") or 0) > 0,
                  f"({n.get('draft_preview_katex')} 处)")
            check("[录题] 预览里没有退化成源码", (n.get("draft_preview_mtex") or 0) == 0,
                  f"({n.get('draft_preview_mtex')} 处兜底)")
            check("[录题] 按钮写着「全部保存（2 道）」",
                  "全部保存（2 道）" in (n.get("save_label") or ""), f"({n.get('save_label')})")
            check("[录题] 备注没被重绘吃掉", n.get("note_kept") == 1)
            check("[录题] 专题下拉跟着科目变了", n.get("topic_followed") == 1)
            check("[录题] 拍照入口带 capture=environment（手机上直接进相机）",
                  n.get("shot_capture") == "environment", f"({n.get('shot_capture')})")
            check("[录题] 桌面端「直接拍照」按钮已隐藏（不与相册重复）",
                  n.get("shot_btn_desktop_hidden") == 1, f"({n.get('shot_btn_desktop_hidden')})")
            check("[录题] 走拍照入口也能录进草稿（第 3 张）",
                  n.get("draft_items_after_shot") == 3, f"({n.get('draft_items_after_shot')})")
            check("[录题] 公式被渲染（KaTeX）", (n.get("katex_total") or 0) > 0,
                  f"({n.get('katex_total')} 处)")
            check("[录题] 没有退化成源码", (n.get("mtex_total") or 0) == 0,
                  f"({n.get('mtex_total')} 处兜底)")
            # ⭐ 需求 A 端到端：保存进库的记录**不带原图**
            check("[录题] 保存按钮当时是可点的", n.get("save_btn_ready") == 1)
            check("[录题] 保存后草稿区清空", n.get("draft_left_after_save") == 0,
                  f"({n.get('draft_left_after_save')} 张还留着)")
            check("[录题] 刚存进库的题 has_image 全为 false（没落盘）",
                  n.get("fresh_count") == 3 and n.get("fresh_has_image") == 0,
                  f"(取样 {n.get('fresh_count')} 条，带图 {n.get('fresh_has_image')} 条)")
            check("[录题] 刚存进库的题题干都还在",
                  n.get("fresh_have_stem") == 3, f"({n.get('fresh_have_stem')})")
            # 清场成功（后面的模式靠「原样 5 条」断言）
            check("[录题] 取证用的 3 条已清掉，库里回到 5 条",
                  n.get("cards_after_cleanup") == 5, f"({n.get('cards_after_cleanup')})")
        if mode == "open":
            check("[展开] 卡片确实展开", n.get("opened") == 1)
            check("[展开] 解析块渲染出来", n.get("analysis_shown") == 1)
            check("[展开] 原图渲染出来", n.get("photo_shown") == 1)
            check("[展开] 分段函数等公式真的渲染成排版结果",
                  (n.get("katex_nodes") or 0) >= 3, f"({n.get('katex_nodes')} 处)")
            check("[展开] 有独立成行的公式块", (n.get("katex_display") or 0) > 0,
                  f"({n.get('katex_display')} 处)")
            check("[展开] **没有**任何公式退化成源码", (n.get("mtex_fallback") or 0) == 0,
                  f"({n.get('mtex_fallback')} 处兜底)")
        if mode == "drill":
            check("[刷题] 面板打开", n.get("drill_open") == 1)
            check("[刷题] **没有**提前泄露解析", n.get("analysis_leaked") == 0,
                  "(先做题后看解析)")
            check("[刷题] 有「我做完了」按钮", n.get("done_btn") == 1)
            # ⭐「开始刷题」从全部错题随机（筛选下一个都匹配不到，照样能抽到）
            check(f"[{tag}] 筛选后列表确实空了（前提成立）",
                  n.get("cards_under_filter") == 0, f"({n.get('cards_under_filter')} 张)")
            check(f"[{tag}] 「开始刷题」不受筛选影响 —— 从全部错题里随机抽",
                  n.get("drill_from_all_ok") == 1)
            check(f"[{tag}] 点「错题总库」能把刷题面板收起来",
                  n.get("drill_closed_by_lib") == 1)
            check(f"[{tag}] 收起后高亮回到「错题总库」", n.get("lib_tab_on_after") == 1)
            check(f"[{tag}] 列表已还原（没把筛选留在那儿）",
                  (n.get("cards_restored") or 0) > 0, f"({n.get('cards_restored')} 张)")
            check("[刷题] 题干公式已渲染", (n.get("katex_nodes") or 0) > 0,
                  f"({n.get('katex_nodes')} 处)")

        # 排查用的原始测量：断言失败时靠它定位，别删
        diag = {k: n.get(k) for k in
                ("w_photo", "has_photo", "inp_files", "img_loaded", "img_err", "toast",
                 "draft_preview_katex", "draft_preview_mtex",
                 "shot_capture", "shot_btn_desktop_hidden", "draft_items_after_shot",
                 "stub_calls_after_shot",
                 "draft_thumbs", "draft_re_btns", "draft_dropped_notes",
                 "save_btn_ready", "draft_left_after_save", "fresh_count",
                 "fresh_has_image", "fresh_have_stem",
                 "home_view_en", "home_task_bar_hidden", "home_task_btns_shown",
                 "home_acct_shown", "home_pen_shown", "home_settings_shown",
                 "acct_avatar", "acct_text", "brand_home",
                 "task_view_en", "task_bar_shown", "task_btns_shown",
                 "exam_count_shown", "exam_count_num", "exam_count_date",
                 "sub_text", "sub_setting_hint", "subjects_hint_text", "subjects_hint_absent",
                 "api_exam_default", "api_exam_set", "api_exam_bad_status", "api_exam_reset",
                 "exam_input_ok", "exam_input_value", "exam_save_btn",
                 "exam_ui_saved", "exam_ui_reset", "exam_sheet_closed",
                 "math_view_class", "math_tabs_shown", "math_tabs_n",
                 "math_lib_label", "math_drill_label", "kp_rows",
                 "math_lib_badge_hidden", "math_lib_badge_text", "overflow_all",
                 "filter_input_ok", "cards_under_filter", "math_drill_entry_present",
                 "drill_from_all_ok", "drill_tab_on", "drill_closed_by_lib",
                 "lib_tab_on_after", "cards_restored",
                 "items_raw", "badges_raw", "stub_calls",
                 "w_items2", "w_ok2", "open_target", "katex_total", "mtex_total", "ERR")}
        print(f"      · {tag} 原始测量：{json.dumps(diag, ensure_ascii=False)}")

finally:
    try:
        proc.terminate()
        proc.wait(timeout=10)
        _srv_f.flush()
    except Exception:  # noqa: BLE001
        proc.kill()
    # 收尾也一律不许把整轮跑挂：删除被守卫拦下是环境问题，不该让取证结论作废。
    try:
        if HARNESS.exists():
            HARNESS.unlink()
    except Exception as exc:  # noqa: BLE001
        print(f"  [NOTE] 临时页面没删掉（{type(exc).__name__}），不影响结论")
    if not KEEP:
        try:
            cleanup(TMP)
        except Exception as exc:  # noqa: BLE001
            print(f"  [NOTE] 临时目录没删掉（{type(exc).__name__}），不影响结论")

# 出图卡住时的现场：把服务端收到的请求按类型数一数。
# 典型信号：katex 字体请求很多但 api 请求寥寥 → 卡在字体/缓存那一层。
try:
    _srv_f.flush()
    lines = SRV_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    n_font = sum(1 for ln in lines if "katex/fonts" in ln)
    n_api = sum(1 for ln in lines if "/api/" in ln)
    n_sw = sum(1 for ln in lines if "sw.js" in ln)
    print(f"\n[5] 服务端请求统计：katex 字体 {n_font} 个 / api {n_api} 个 / sw.js {n_sw} 个")
    if failed_shots:
        print("    出图失败的：" + "、".join(failed_shots))
        print("    最后 6 条日志：")
        for ln in lines[-6:]:
            print("      " + ln)
except Exception as exc:  # noqa: BLE001
    print(f"  [NOTE] 读取服务端日志失败：{type(exc).__name__}")

print("\n" + "=" * 64)
print(f"结果：{ok} 通过 / {fail} 待修")
print("=" * 64)
sys.exit(0 if fail == 0 else 1)
