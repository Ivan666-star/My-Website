# -*- coding: utf-8 -*-
"""在**全新临时库**上灌一份英语种子数据，再用无头浏览器逐页出图并断言：英语模块渲染是否正常。

为什么要费这个事：
    "数据在库里" 和 "页面上看得对" 是两件事。任何一处字段名对不上，前端都不会报错，
    只会**默默空白** —— 正是用户最反感的那种。所以不信 SQL，直接看像素。

怎么做到不碰线上：
    临时目录里新建一份中心库（只有 users/sessions/profile）→ 以「未认领 0 号桶」
    灌入 `_seed_english` 的两篇示例文章 + 20 条素材 → 该桶的 learning.db 就落在临时目录里；
    AI_API_KEY="" 起服务 → 没有账号 → auth 进 OPEN 模式，免登录。线上 8848 与线上库全程不动。

    ⚠️ 2026-10-15 之前这里靠「拷一份线上 data/app.db」拿数据，早就坏了：学习数据从
    2026-10-09 起按账号分库（data/users/<id>_<名字>/learning.db），拷中心库只会得到空站点。
    现在改成自己灌种子，症状也就不会随线上库变化而漂移。

三个阶段各出一张图（每阶段单独跑一次 Chrome，否则只有最后一张）：
    stage=materials  430×900   素材库（含类型分栏：词汇/短语/句型/长难句/语法）
    stage=review     430×900   复习（展开第一条的答案）
    stage=article   1024×1366  打开 2026-10-01 那篇示例文章，展开解析（两组：日常必记/闲时精读）

跑法：python tools/_shot_en_seeded.py
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from _qa_tmp import cleanup  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PORT = 8898
BASE = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="enshot_"))
HARNESS = ROOT / "web" / "_qa_en.html"
PY = sys.executable
CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe"

NEW_DATE = "2026-10-01"
NEW_TITLE = "When the Measure Becomes the Mission"

PASS, FAIL = [], []


def check(label: str, cond, extra: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


_ENV = dict(os.environ)          # ⚠️ 必须在系统环境之上**叠加**，别整个替换 ——
_ENV.update({                    #    替换掉之后服务连 `PATH` 以外的东西都没了，起不来
    "DB_PATH": str(TMP / "app.db").replace("\\", "/"),
    "AI_API_KEY": "",            # 不调模型：只验界面渲染
    "DAILY_GENERATE_HOUR": "23",  # 不自动生成当天内容
    "ACCESS_PASSWORD": "",
    "PYTHONIOENCODING": "utf-8",
})


def req(path: str):
    o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with o.open(BASE + path, timeout=25) as fh:
            txt = fh.read().decode()
            try:
                return json.loads(txt)
            except ValueError:
                return {"_raw": txt[:300]}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:300]}


# ============================================================
# 探针页：iframe 装 app → 按 stage 走 → keepalive 回传
# ============================================================
HARNESS_TPL = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>en seed qa</title>
<style>
  html,body{margin:0;background:#e9e6e0;overflow:hidden;height:100%}
  #stage{display:block;position:relative;width:100%;height:100%}
  iframe{border:0;background:#f5f3ef;display:block;position:absolute;left:0;top:0}
</style></head><body>
<div id="stage"><iframe id="fr"></iframe></div>
<script>
const Q = new URLSearchParams(location.search);
const W = +(Q.get("w") || 430), H = +(Q.get("h") || 900), STAGE = Q.get("stage") || "materials";
const fr = document.getElementById("fr");
fr.style.width = W + "px"; fr.style.height = H + "px";
fr.src = "/";
const report = { w: W, h: H, stage: STAGE, notes: {} };
const note = (k, v) => { report.notes[k] = v; };
function raf(n){ return new Promise(r => setTimeout(r, 16 * n)); }
function until(fn, tries = 400){
  return new Promise((res, rej) => {
    let i = 0;
    const f = () => { let v = null; try { v = fn(); } catch (e) { v = null; }
      if (v) return res(v); if (++i > tries) return rej(new Error("until 超时"));
      setTimeout(f, 50); };
    f();
  });
}
function send(){
  return fetch("/api/annotations", {
    method: "PUT", headers: { "Content-Type": "application/json" }, keepalive: true,
    body: JSON.stringify({ key: "qa-en:" + STAGE + ":" + W, data: { strokes: [{ t: 1 }], report } }),
  }).then(r => r.status).catch(() => -1);
}
function overflowIn(d, sel){
  const bad = [];
  d.querySelectorAll(sel).forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && (r.right > W + 1 || r.left < -1)) {
      bad.push((el.className || el.tagName) + "@" + Math.round(r.right));
    }
  });
  return bad.length ? bad.slice(0, 4).join(" | ") : "无";
}
function num(d, sel){
  const el = d.querySelector(sel);
  return el ? (parseInt((el.textContent || "0").trim(), 10) || 0) : -1;
}
(async () => {
  try {
    await until(() => fr.contentDocument && fr.contentDocument.querySelector("#page-subjects"));
    await raf(6);
    const w = fr.contentWindow, d = fr.contentDocument;

    if (STAGE === "materials") {
      /* 倒计时卡片是 loadSubjects() 异步画上去的，刚拿到 #page-subjects 时它可能还没来 ——
         给它 5 秒，别把"还没画完"误报成"卡片没了"。 */
      try { await until(() => d.querySelector(".exam-count"), 120); } catch (e) {}
      note("has_countdown", d.querySelector(".exam-count") ? 1 : 0);
      w.location.hash = "#library";
      await until(() => d.querySelectorAll(".mat-item").length > 0, 400);
      await raf(10);
      note("mat_items", d.querySelectorAll(".mat-item").length);
      note("lib_tabs", d.querySelectorAll(".lib-tabs:not(.lib-type-tabs) .lib-tab").length);
      note("type_tabs", d.querySelectorAll(".lib-type-tabs .lib-tab").length);
      note("type_labels", [...d.querySelectorAll(".lib-type-tabs .lib-tab")].map(b => b.dataset.type).join(","));
      /* 类型分栏要真的在过滤（用户 2026-10-14：语法在语法一栏、词汇在词汇一栏、长难句在长难句一栏） */
      const typeTab = t => [...d.querySelectorAll(".lib-type-tabs .lib-tab")].find(b => b.dataset.type === t);
      const clickType = async t => { const b = typeTab(t); if (b) { b.click(); await raf(10); } };
      await clickType("pattern");
      note("after_pattern", d.querySelectorAll(".mat-item").length);
      await clickType("phrase");
      note("after_phrase", d.querySelectorAll(".mat-item").length);
      await clickType("grammar");
      note("after_grammar", d.querySelectorAll(".mat-item").length);
      note("grammar_note", (d.querySelector(".lib-type-note") || {}).textContent ? 1 : 0);
      note("grammar_pos_filters", d.querySelectorAll(".pos-filter").length);
      await clickType("");   // 回到「全部」，后面的库/位置筛选断言照旧
      note("back_all", d.querySelectorAll(".mat-item").length);
      note("pos_filters", d.querySelectorAll(".pos-filter").length);
      note("genre_filters", d.querySelectorAll(".genre-filter").length);
      note("cats", d.querySelectorAll(".mat-item .mat-cat").length);
      note("level_abc", d.querySelectorAll(".mat-item .mat-lv").length);
      note("tag_chips", d.querySelectorAll(".mat-item .mat-tags .tag").length);
      note("auto_pct", d.querySelectorAll(".mat-item .mat-auto:not(.manual)").length);
      note("usage_lines", d.querySelectorAll(".mat-item .mat-usage").length);
      note("rev_line", d.querySelectorAll(".mat-item .mat-rev").length);
      note("type_chips", d.querySelectorAll(".mat-item .mat-type").length);
      const sum = d.querySelectorAll(".lib-sum-item b");
      note("sum_total", sum.length ? (parseInt(sum[0].textContent.trim(), 10) || 0) : -1);
      /* 筛「作文素材库」→ 12 条；再筛「开头段」→ 2 条（验证筛选真的在过滤，不是装饰） */
      const tabW = [...d.querySelectorAll(".lib-tabs:not(.lib-type-tabs) .lib-tab")].find(b => b.dataset.lib === "writing");
      if (tabW) { tabW.click(); await raf(10); }
      note("after_writing", d.querySelectorAll(".mat-item").length);
      const posOpen = [...d.querySelectorAll(".pos-filter")].find(b => b.dataset.pos === "opening");
      if (posOpen) { posOpen.click(); await raf(10); }
      note("writing_opening", d.querySelectorAll(".mat-item").length);
      /* 展开一条的「调整分类」面板，确认位置/文体按钮都在 */
      const card = d.querySelector(".mat-item");
      const rt = card && card.querySelector('[data-act="retag"]');
      if (rt) { rt.click(); await raf(6); }
      note("retag_pos_btns", card ? card.querySelectorAll("[data-set-pos]").length : 0);
      note("retag_genre_btns", card ? card.querySelectorAll("[data-set-genre]").length : 0);
      note("overflow", overflowIn(d, "#pageBody-library > *, .mat-item, .lib-tab, .lib-type-tabs .lib-tab, .lib-type-note, .pos-filter, .genre-filter"));
    }

    if (STAGE === "review") {
      w.location.hash = "#review";
      await until(() => d.querySelectorAll(".rev-card").length > 0, 400);
      await raf(10);
      note("rev_cards", d.querySelectorAll(".rev-card").length);
      note("due", num(d, '[data-rev-stat="due"]'));
      note("in_cycle", num(d, '[data-rev-stat="in_cycle"]'));
      note("mastered", num(d, '[data-rev-stat="mastered"]'));
      /* 复习只收词汇和表达（用户 2026-10-14）：种子里 4 条都是短语/句型 → 只该有「表达」一栏 */
      note("rev_groups", d.querySelectorAll(".rev-group").length);
      note("rev_group_titles", [...d.querySelectorAll(".rev-group-title")].map(e => e.textContent).join("|"));
      note("rev_scope_note", (d.querySelector("#pageBody-review").textContent || "").indexOf("这里只有") >= 0 ? 1 : 0);
      const sum = d.querySelectorAll(".lib-sum-item b");
      note("total", sum.length > 3 ? (parseInt(sum[3].textContent.trim(), 10) || 0) : -1);
      note("pos_chips", d.querySelectorAll(".rev-card .pos-chip").length);
      note("lv_chips", d.querySelectorAll(".rev-card .mat-lv").length);
      /* 看答案：答案区默认 hidden，点开必须真显示内容 */
      const first = d.querySelector(".rev-card");
      const btn = first && first.querySelector('[data-rev="show"]');
      if (btn) { btn.click(); await raf(8); }
      const ans = first && first.querySelector(".rev-answer");
      note("ans_shown", ans && !ans.hidden ? 1 : 0);
      note("ans_cn_len", ans ? (ans.querySelector(".rev-cn") || {}).textContent ? ans.querySelector(".rev-cn").textContent.length : 0 : 0);
      note("ans_usage", ans && ans.querySelector(".rev-usage") ? 1 : 0);
      note("overflow", overflowIn(d, "#pageBody-review > *, .rev-card"));
    }

    if (STAGE === "article") {
      w.location.hash = "#history";
      await until(() => d.querySelectorAll(".hist-item").length > 0, 400);
      await raf(8);
      note("hist_items", d.querySelectorAll(".hist-item").length);
      note("hist_tabs", d.querySelectorAll(".hist-tab").length);
      const it = d.querySelector('.hist-item[data-hist-date="@@DATE@@"]');
      note("has_new_day", it ? 1 : 0);
      if (it) { it.click(); await raf(20); }
      await until(() => { const t = d.querySelector("#heroTitle");
                          return t && t.textContent.indexOf("@@TITLE@@") >= 0; }, 400);
      await raf(8);
      note("title_ok", d.querySelector("#heroTitle").textContent.indexOf("@@TITLE@@") >= 0 ? 1 : 0);
      note("difficulty_chip", d.querySelector("#heroChips").textContent.indexOf("难度 4/5") >= 0 ? 1 : 0);
      note("demo_chip", d.querySelector("#heroChips").textContent.indexOf("内置示例") >= 0 ? 1 : 0);
      note("src_mark", d.querySelector("#heroMeta").textContent.indexOf("[示例]") >= 0 ? 1 : 0);
      note("gate_before", d.querySelector("#gate").hidden ? 0 : 1);
      note("analysis_hidden", d.querySelector("#analysis").hidden ? 1 : 0);
      /* 展开解析 */
      d.querySelector("#btnReveal").click();
      await until(() => !d.querySelector("#analysis").hidden, 200);
      await raf(60);   /* 等应用的平滑滚动走完（约 0.6~1s），否则量到的是动画中间态 */
      const tbEl = d.querySelector(".topbar");
      const tbh = tbEl ? Math.round(tbEl.getBoundingClientRect().height) : 0;
      const at = Math.round(d.querySelector("#analysis").getBoundingClientRect().top);
      note("topbar_h", tbh);
      note("analysis_top", at);
      /* 展开后应用会 scrollIntoView 到解析区顶部；吸顶栏高 59px，
         如果 analysis 顶边落到 59 以内，第一行「翻译」就被顶栏压住了。 */
      note("analysis_covered", at < tbh - 1 ? 1 : 0);
      await raf(14);
      /* ⚠️ 用具体类名数，别数 `#transBody > *`：容器里可能还留着占位子节点，
            数子节点会把 5 段读成 6 段（第一次就踩了）。 */
      note("trans_rows", d.querySelectorAll("#transBody .trans-item").length);
      note("trans_notes", d.querySelectorAll("#transBody .trans-note").length);
      note("vocab_rows", d.querySelectorAll("#vocabList .v-item").length);
      note("vocab_sub", (d.querySelector("#vocabSub").textContent || "").trim());
      note("vocab_exam_hint", d.querySelector("#vocabList").textContent.indexOf("考研真题的高频义") >= 0 ? 1 : 0);
      /* 长难句与语法合并成一个板块（用户 2026-10-14），两个小节都数出来 */
      note("sent_rows", d.querySelectorAll("#sentGramList .s-item").length);
      note("gram_rows", d.querySelectorAll("#sentGramList .g-item").length);
      note("sg_sections", d.querySelectorAll("#sentGramList .sg-section").length);
      /* 解析区分两组：日常必记（词汇+表达）/ 闲时精读（长难句与语法） */
      note("ana_groups", d.querySelectorAll("#analysis .ana-group").length);
      note("ana_titles", [...d.querySelectorAll("#analysis .ana-group-title")].map(e => e.textContent).join("|"));
      /* 用户 2026-10-11 撤掉的入口（阅读逻辑 / 命题视角）不该偷偷回来 */
      note("logic_present", d.querySelector("#logicBody") ? 1 : 0);
      note("exam_present", d.querySelector("#examBody") ? 1 : 0);
      note("write_items", d.querySelectorAll("#writeList .w-item").length);
      note("wf_note", d.querySelectorAll("#writeList .w-pos-hint").length);
      note("wf_genre", d.querySelectorAll("#writeList .w-genre-hint").length);
      note("katex_nodes", d.querySelectorAll(".katex-html").length);
      note("mtex_fallback", d.querySelectorAll(".mtex").length);
      note("overflow", overflowIn(d, "#articleWrap > *, #analysis > *"));
      const ap = d.querySelector("#analysis");
      if (ap) ap.scrollIntoView({ block: "start" });
      await raf(6);
    }
    await send();
    await raf(4);
    await send();
  } catch (e) {
    note("ERR", (e && e.message) || String(e));
    await send();
  }
})();
</script></body></html>"""


def wait_health(proc, timeout=60):
    o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("服务进程退了，看临时目录里的 srv.log")
        try:
            with o.open(BASE + "/api/health", timeout=3) as fh:
                if fh.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError("服务没起来")


def capture(name: str, stage: str, w: int, h: int) -> bool:
    p = ROOT / "data" / name
    q = urllib.parse.urlencode({"w": w, "h": h, "stage": stage})
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           f"--window-size={w},{h}", "--virtual-time-budget=300000",
           f"--screenshot={p}", f"{BASE}/static/_qa_en.html?{q}"]
    try:
        subprocess.run(cmd, env=_ENV, timeout=150,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False
    return p.exists() and p.stat().st_size > 5000


def read_reports() -> dict:
    """回传报告**直接读临时库文件**，不要走 HTTP（强杀 Chrome 会留下半开连接把 GET 拖死）。"""
    f = TMP / "app.db"
    if not f.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT doc_key, data FROM annotations WHERE doc_key LIKE 'qa-en:%'"
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for r in rows:
        try:
            rep = (json.loads(r["data"] or "{}") or {}).get("report")
        except (TypeError, ValueError):
            continue
        if not rep:
            continue
        parts = str(r["doc_key"]).split(":")      # qa-en:<stage>:<w>
        if len(parts) == 3:
            out[(parts[1], int(parts[2]))] = rep
    return out


def main() -> None:
    if not Path(CHROME).exists():
        sys.exit(f"找不到 Chrome：{CHROME}")

    # ---- 临时库：全新建一份，种子直接灌进去（线上库全程不碰）----
    # ⚠️ 2026-10-09 起学习数据**按账号分库**：中心库只剩 users/sessions/profile，
    #    文章/素材/笔记都在 data/users/<id>_<名字>/learning.db 里。
    #    所以「拷一份线上 app.db」这条老路子现在只会拿到一篇空白站点
    #    （2026-10-15 发现并改掉）。这里改成：临时中心库新建 → 以「未认领 0 号桶」
    #    灌一份种子 → 服务在 MODE_OPEN 下读的就是 0 号桶。
    #    只调 _seed_english 的两个写库函数，绝不去碰它的 main()（那个会备份+写线上库）。
    os.environ["DB_PATH"] = str(TMP / "app.db").replace("\\", "/")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    from app import db as _db          # noqa: E402  必须在 DB_PATH 设好之后再导入
    import _seed_english as _se        # noqa: E402

    with _db.as_system():
        _db.init_db()
    with _db.as_local():
        _db.ensure_user_db(_db.UNCLAIMED)
        n_art = len(_se.seed_articles(_db.UNCLAIMED))
        n_fav = len(_se.seed_materials(_db.UNCLAIMED))
    print(f"临时库就绪（素材 {n_fav} 条 / 文章 {n_art} 篇）-> {TMP}\n")

    HARNESS.write_text(
        HARNESS_TPL.replace("@@DATE@@", NEW_DATE).replace("@@TITLE@@", NEW_TITLE),
        encoding="utf-8",
    )
    srv_log = TMP / "srv.log"
    fh = srv_log.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT),
         "--no-use-colors", "--log-level", "warning"],
        cwd=str(ROOT), env=_ENV, stdout=fh, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_health(proc)
        print("[1] 临时实例就绪（免登录）\n")

        # ---------------- 服务端先自己确认数据在 ----------------
        print("[2] 接口层核对")
        fav = req("/api/favorites")
        s = fav.get("summary", {})
        check("素材库 20 条", fav.get("total") == 20, f"(实得 {fav.get('total')})")
        check("分库：作文 12 / 阅读 8",
              s.get("by_library", {}).get("writing") == 12
              and s.get("by_library", {}).get("reading") == 8, f"({s.get('by_library')})")
        check("位置覆盖 5 档（含开头/过渡/主体/结尾）",
              set(s.get("by_position", {})) >= {"opening", "transition", "body", "ending", "universal"},
              f"({s.get('by_position')})")
        check("文体覆盖 4 档", len(s.get("by_genre", {})) >= 4, f"({s.get('by_genre')})")
        check("等级 A/B/C 都有", set(s.get("by_level", {})) >= {"A", "B", "C"}, f"({s.get('by_level')})")

        rev = req("/api/review/due")
        st = rev.get("stats", {})
        check("今天待复习 4 条（含 1 条逾期排最前）", len(rev.get("items", [])) == 4,
              f"(实得 {len(rev.get('items', []))})")
        check("复习统计 4/18/1/20",
              st.get("due") == 4 and st.get("in_cycle") == 18
              and st.get("mastered") == 1 and st.get("total") == 20, f"({st})")
        check("逾期那条排在第一（最久没碰的在前）",
              (rev.get("items") or [{}])[0].get("content") == "at the expense of",
              f"({(rev.get('items') or [{}])[0].get('content')!r})")

        sn = req("/api/stats?days=30")
        # ⚠️ 种子文章的日期是**写死的**（2026-10-01 / 10-02），这个测试又必须能隔几天再跑，
        #    所以这里统一用 30 天窗口：7 天窗口在种子之后一周就自然为空了（老版本就是这么过期的）。
        check("学习数据：30 天内 2 篇文章（种子就两篇）", sn.get("articles") == 2,
              f"(实得 {sn.get('articles')})")
        check("学习数据：活跃天数 >= 2", (sn.get("active_days") or 0) >= 2,
              f"({sn.get('active_days')})")
        check("学习数据：素材总量已计入 20", sn.get("favorites_total") == 20,
              f"({sn.get('favorites_total')})")

        bo = req("/api/bootstrap")
        # 种子的日期是写死的（都在过去），所以「今天」本来就没有文章：
        # bootstrap 返回今天、两篇种子都在历史里，这才是正常状态。
        check("默认落在今天（今天没文章，种子都在历史里）",
              (bo.get("date") or "") == date.today().isoformat(),
              f"({bo.get('date')} vs {date.today().isoformat()})")
        check("历史列表 2 天（种子就两篇）", len(bo.get("history", [])) == 2,
              f"({len(bo.get('history', []))})")
        check("顶部复习角标数据显示 4", (bo.get("meta") or {}).get("review_due") == 4)

        nb = req("/api/bootstrap?date_str=" + NEW_DATE)
        b = nb.get("bundle") or {}
        a = b.get("article") or {}
        check(f"{NEW_DATE} 新文章在（标题对）", a.get("title") == NEW_TITLE, f"({a.get('title')!r})")
        check("新文章标成「内置示例」且 source 带 [示例] 前缀",
              a.get("source_type") == "demo" and (a.get("source") or "").startswith("[示例]"),
              f"({a.get('source_type')} / {a.get('source')!r})")
        check("翻译 5 段 / 词汇 10 / 长难句 3 / 语法 2",
              len(a.get("translation") or []) == 5 and len(b.get("vocab") or []) == 10
              and len(b.get("sentences") or []) == 3 and len(b.get("grammar") or []) == 2,
              f"({len(a.get('translation') or [])}/{len(b.get('vocab') or [])}"
              f"/{len(b.get('sentences') or [])}/{len(b.get('grammar') or [])})")
        ef = a.get("exam_focus") or {}
        check("命题视角 3 个设题点 + 有干扰项设计",
              len(ef.get("set_points") or []) == 3
              and all(p.get("traps") for p in (ef.get("set_points") or [])),
              f"({len(ef.get('set_points') or [])})")
        check("写作表达 5 条 + 仿写任务在",
              len(b.get("writing_items") or []) == 5 and bool(b.get("imitation")))

        # ---------------- 出图 + 页面侧测量 ----------------
        print("\n[3] 出图 + 页面侧测量")
        shots = [
            ("materials", 430, 900, "_seed_en_materials_phone.png"),
            ("review", 430, 900, "_seed_en_review_phone.png"),
            ("article", 1024, 1366, "_seed_en_article_ipad.png"),
        ]
        for stage, w, h, name in shots:
            ok = capture(name, stage, w, h)
            check(f"[{stage}] 出图", ok, name)

        reps = read_reports()
        print(f"  （页面回传：{sorted(reps)}）")

        r = reps.get(("materials", 430))
        if not r:
            check("素材库页回传了报告", False, "(看 data/_seed_en_materials_phone.png 定位)")
        else:
            n = r["notes"]
            check("素材库 20 张卡都渲染出来", n.get("mat_items") == 20, f"({n.get('mat_items')})")
            check("三个库标签在（全部/作文/阅读）", n.get("lib_tabs") == 3, f"({n.get('lib_tabs')})")
            check("类型分栏 5 类 + 全部（词汇/短语/句型/长难句/语法）",
                  n.get("type_tabs") == 6
                  and n.get("type_labels") == ",vocab,phrase,pattern,sentence,grammar",
                  f"({n.get('type_tabs')} / {n.get('type_labels')})")
            check("点「句型」只剩 3 条", n.get("after_pattern") == 3, f"({n.get('after_pattern')})")
            check("点「短语」只剩 17 条", n.get("after_phrase") == 17, f"({n.get('after_phrase')})")
            check("点「语法」0 条 + 有「不进复习」说明",
                  n.get("after_grammar") == 0 and n.get("grammar_note") == 1,
                  f"({n.get('after_grammar')} / note {n.get('grammar_note')})")
            check("语法栏不摆位置筛选（位置对语法没意义）",
                  n.get("grammar_pos_filters") == 0, f"({n.get('grammar_pos_filters')})")
            check("切回「全部」回到 20 条", n.get("back_all") == 20, f"({n.get('back_all')})")
            check("每张卡都带类型标签", n.get("type_chips") == 20, f"({n.get('type_chips')})")
            check("位置筛选项齐全", (n.get("pos_filters") or 0) >= 5, f"({n.get('pos_filters')})")
            check("文体筛选项齐全", (n.get("genre_filters") or 0) >= 5, f"({n.get('genre_filters')})")
            check("等级标记 / 主题 / 标签都渲染出来",
                  n.get("level_abc") == 20 and n.get("cats") == 20 and (n.get("tag_chips") or 0) >= 20,
                  f"({n.get('level_abc')}/{n.get('cats')}/{n.get('tag_chips')})")
            check("「AI 归类」与「手动 / 规则」两种都看得到",
                  0 < (n.get("auto_pct") or 0) < 20, f"(AI {n.get('auto_pct')} / 20)")
            check("复习进度行都在（含「已移出复习」）", n.get("rev_line") == 20, f"({n.get('rev_line')})")
            check("使用建议每张卡都有", n.get("usage_lines") == 20, f"({n.get('usage_lines')})")
            check("汇总条：素材总量 20", n.get("sum_total") == 20, f"({n.get('sum_total')})")
            check("点「作文素材库」真的筛出 12 条", n.get("after_writing") == 12,
                  f"({n.get('after_writing')})")
            check("再筛「开头段」真的只剩 2 条", n.get("writing_opening") == 2,
                  f"({n.get('writing_opening')})")
            check("「调整分类」面板给出位置/文体全部可选",
                  n.get("retag_pos_btns") == 5 and n.get("retag_genre_btns") == 6,
                  f"({n.get('retag_pos_btns')}/{n.get('retag_genre_btns')})")
            check("素材库手机档无横向溢出", n.get("overflow") == "无", f"({n.get('overflow')})")
            check("主界面倒计时还在", n.get("has_countdown") == 1)

        r = reps.get(("review", 430))
        if not r:
            check("复习页回传了报告", False, "(看 data/_seed_en_review_phone.png 定位)")
        else:
            n = r["notes"]
            check("今日待复习 4 张卡", n.get("rev_cards") == 4, f"({n.get('rev_cards')})")
            check("统计条 4 / 18 / 1 / 20",
                  n.get("due") == 4 and n.get("in_cycle") == 18
                  and n.get("mastered") == 1 and n.get("total") == 20,
                  f"({n.get('due')}/{n.get('in_cycle')}/{n.get('mastered')}/{n.get('total')})")
            check("每张卡都带位置/等级标记",
                  n.get("pos_chips") == 4 and n.get("lv_chips") == 4,
                  f"({n.get('pos_chips')}/{n.get('lv_chips')})")
            check("复习页分了「表达」一栏（种子数据里都是短语/句型，没有单词）",
                  n.get("rev_groups") == 1 and "表达" in (n.get("rev_group_titles") or ""),
                  f"({n.get('rev_groups')} / {n.get('rev_group_titles')!r})")
            check("复习页写明「只有词汇和表达」", n.get("rev_scope_note") == 1)
            check("「看答案」点开后答案真的显示", n.get("ans_shown") == 1)
            check("答案里有中文释义", (n.get("ans_cn_len") or 0) >= 6, f"({n.get('ans_cn_len')} 字)")
            check("答案里有使用建议", n.get("ans_usage") == 1)
            check("复习页手机档无横向溢出", n.get("overflow") == "无", f"({n.get('overflow')})")

        r = reps.get(("article", 1024))
        if not r:
            check("文章页回传了报告", False, "(看 data/_seed_en_article_ipad.png 定位)")
        else:
            n = r["notes"]
            check("历史页 2 天 + 两个页签", n.get("hist_items") == 2 and n.get("hist_tabs") == 2,
                  f"({n.get('hist_items')}/{n.get('hist_tabs')})")
            check(f"历史里有 {NEW_DATE} 这一天", n.get("has_new_day") == 1)
            check("点开后打开的是新文章", n.get("title_ok") == 1)
            check("标签：难度 4/5 + 内置示例 + 来源带 [示例]",
                  n.get("difficulty_chip") == 1 and n.get("demo_chip") == 1 and n.get("src_mark") == 1)
            check("先卡在「读完了才展开」的状态", n.get("gate_before") == 1 and n.get("analysis_hidden") == 1)
            check("展开后自动滚动没有把解析区顶边藏进吸顶栏",
                  n.get("analysis_covered") == 0,
                  f"(顶栏 {n.get('topbar_h')}px，解析区顶边 y={n.get('analysis_top')})")
            check("翻译 5 段", n.get("trans_rows") == 5, f"({n.get('trans_rows')})")
            check("每段都带「翻译要点」", n.get("trans_notes") == 5, f"({n.get('trans_notes')})")
            check("词汇 10 条", n.get("vocab_rows") == 10, f"({n.get('vocab_rows')})")
            check("词汇计数文案带「10 个」与前缀", (n.get("vocab_sub") or "").startswith("10 个"),
                  f"({n.get('vocab_sub')!r})")
            check("词汇区写明释义按考研高频义", n.get("vocab_exam_hint") == 1)
            check("长难句 3 句 + 语法 2 条（合并板块里两个小节）",
                  n.get("sent_rows") == 3 and n.get("gram_rows") == 2 and n.get("sg_sections") == 2,
                  f"({n.get('sent_rows')}/{n.get('gram_rows')}/{n.get('sg_sections')})")
            check("解析区分了两组（日常必记 / 闲时精读）",
                  n.get("ana_groups") == 2
                  and "日常必记" in (n.get("ana_titles") or "")
                  and "闲时精读" in (n.get("ana_titles") or ""),
                  f"({n.get('ana_groups')} / {n.get('ana_titles')!r})")
            check("撤掉的阅读逻辑 / 命题视角入口没有回来",
                  (n.get("logic_present") or 0) == 0 and (n.get("exam_present") or 0) == 0,
                  f"({n.get('logic_present')}/{n.get('exam_present')})")
            check("写作表达 5 条", n.get("write_items") == 5, f"({n.get('write_items')})")
            check("每条写作表达都标了位置与文体",
                  n.get("wf_note") == 5 and n.get("wf_genre") == 5,
                  f"({n.get('wf_note')}/{n.get('wf_genre')})")
            check("没有公式退化成源码", (n.get("mtex_fallback") or 0) == 0, f"({n.get('mtex_fallback')})")
            check("文章页平板档无横向溢出", n.get("overflow") == "无", f"({n.get('overflow')})")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=12)
        except Exception:  # noqa: BLE001
            proc.kill()
        fh.close()
        if HARNESS.exists():
            HARNESS.unlink()
        cleanup(TMP)

    print("\n" + "=" * 56)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 待修")
    for x in FAIL:
        print(f"  ✗ {x}")
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
