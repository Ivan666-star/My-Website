# -*- coding: utf-8 -*-
"""整页路由回归测试：验证「今日内容 ⇄ 整页」这套导航真的能用。

为什么需要它：
  这次改动把素材库 / 学习数据 / 历史真题 / 今日复习从「弹层」搬成了「整页」，
  还换掉了内容渲染的落点（renderTarget）。这种东西光看代码看不出问题——
  少改一个事件委托，页面就是点不动，而且不报错。

怎么跑：
    python tools/_views_test.py      # 需要服务已在 127.0.0.1:8848 跑着
账号模式的实例（线上就是）要带账号：
    QA_USER=Ivan.666 QA_PASS=你的密码 python tools/_views_test.py
没给凭据就**直接跳过**，不会报一屏假失败 —— 这个测试的前提是"能免登录进应用"。
结果：
    只打印 PASS / FAIL 列表，退出码非 0 表示有失败。

实现方式：
  把应用放进同源 iframe 里用真 Chrome 驱动，断言写进父页面 DOM，
  再用 --dump-dom 把结果读回来。临时验证页用完自己删（公网部署不留痕）。
"""
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FRAME = WEB / "_qa_views.html"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = os.environ.get("QA_BASE", "http://127.0.0.1:8848")
QA_USER = os.environ.get("QA_USER", "")
QA_PASS = os.environ.get("QA_PASS", "")

FRAME_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>QA views</title>
<style>
html,body{margin:0;padding:0;background:#fff}
iframe{border:0;display:block;background:#fff}
#qa{padding:10px;font:12px/1.6 monospace;white-space:pre-wrap}
</style></head><body>
<iframe id="f"></iframe>
<div id="qa">running</div>
<script>
const Q = new URLSearchParams(location.search);
const W = +(Q.get("w") || 390);
const USER = Q.get("user") || "";
const PASS = Q.get("pass") || "";
const f = document.getElementById("f");
f.style.width = W + "px"; f.style.height = "900px";
const LOG = [];
const emit = (ok, msg) => {
  LOG.push((ok ? "PASS" : "FAIL") + " | " + msg);
  // 逐条实时写进 DOM：万一后面某步卡住，也能看到已经跑过的结果
  document.getElementById("qa").textContent = LOG.join("\n");
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 账号模式下先登录（同源 fetch，Set-Cookie 会被浏览器收下），再加载应用 */
async function login() {
  if (!USER) return true;
  const body = new URLSearchParams({ username: USER, password: PASS, next: "/" });
  const r = await fetch("/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  return r.ok || r.status === 303;
}

/* 等 iframe 真正把应用加载完，否则拿到的是 about:blank 的文档 */
function frameReady() {
  return new Promise(res => {
    const d = f.contentDocument;
    if (d && d.readyState === "complete" && f.contentWindow.location.href !== "about:blank") {
      return res();
    }
    f.addEventListener("load", res, { once: true });
  });
}

/* 轮询等某个 iframe 内部的应用文档真正就绪（新 iframe 会先来一次 about:blank 的 load） */
async function waitInner(fr, ms) {
  ms = ms || 20000;
  for (let waited = 0; waited < ms; waited += 120) {
    const d = fr.contentDocument;
    if (d && d.readyState === "complete" && d.getElementById("page-stats")) return true;
    await sleep(120);
  }
  return false;
}

async function main() {
  /* 账号模式下必须先登录，否则 iframe 落在登录页，后面所有断言都会 null */
  if (USER) { await login(); }
  f.src = "/";
  await frameReady();
  const win = f.contentWindow, doc = f.contentDocument;
  const errs = [];
  win.addEventListener("error", e => errs.push("JS:" + e.message));
  win.addEventListener("unhandledrejection",
    e => errs.push("REJ:" + ((e.reason && e.reason.message) || e.reason)));

  const $ = s => doc.querySelector(s);
  const vis = id => doc.getElementById(id).hidden !== true;
  const hid = id => doc.getElementById(id).hidden === true;
  const txtLen = id => (doc.getElementById(id).textContent || "").trim().length;
  const H = () => win.location.hash;
  const PAGES = ["page-library", "page-stats", "page-history", "page-review"];
  const onlyOnePage = shown => PAGES.filter(p => vis(p)).length === (shown ? 1 : 0);

  await sleep(2400);

  /* ---- T1 初始：今日内容 + 四个整页全隐藏 ---- */
  emit(H() === "" && vis("todayView") && onlyOnePage(false),
       "T1 初始状态：today 可见，四个整页全隐藏，hash 为空");

  /* ---- T2 点顶栏素材库 → 整页 ---- */
  $("#btnLibrary").click(); await sleep(300);
  emit(H() === "#library" && hid("todayView") && vis("page-library") && onlyOnePage(true),
       "T2 点素材库：hash=#library，today 隐藏，只有素材库可见");
  emit(txtLen("pageBody-library") > 200 &&
       !doc.getElementById("pageBody-library").innerHTML.includes("加载中"),
       "T2b 素材库整页内容已渲染完成（不是加载中占位）");

  /* ---- T3 整页里的筛选还能点（事件委托从 #sheetBody 挪到了 document） ---- */
  const before = $(".lib-tab.on");
  const btns = doc.querySelectorAll("#pageBody-library [data-lib]");
  let switched = false;
  if (btns.length) {
    const target = Array.from(btns).find(b => !b.classList.contains("on"));
    if (target) {
      target.click(); await sleep(250);
      const now = $(".lib-tab.on");
      switched = !!now && now !== before && now.dataset.lib === target.dataset.lib;
    }
  }
  emit(switched || btns.length === 0,
       "T3 素材库筛选按钮在整页里仍可点击（委托未失效）");

  /* ---- T4 页头 ← 回到今日 ---- */
  $("#page-library [data-back]").click(); await sleep(300);
  emit(H() === "" && vis("todayView") && onlyOnePage(false),
       "T4 点页头返回：hash 清空，回到今日内容");

  /* ---- T5 学习数据 ---- */
  $("#btnStats").click(); await sleep(700);
  emit(H() === "#stats" && hid("todayView") && vis("page-stats") && onlyOnePage(true),
       "T5 点学习数据：hash=#stats，只有学习数据可见");
  emit(!!$("#pageBody-stats .stat-grid"),
       "T5b 学习数据整页渲染出了统计卡片");

  /* ---- T6 历史与真题 + 页签切换 ---- */
  $("#btnHistory").click(); await sleep(800);
  emit(H() === "#history" && vis("page-history") && onlyOnePage(true),
       "T6 点历史：hash=#history，只有历史可见");
  emit(doc.querySelectorAll("#pageBody-history [data-htab]").length === 2,
       "T6b 历史整页有两个页签（每日内容 / 真题精读）");
  const tabExam = $("#pageBody-history [data-htab=exam]");
  if (tabExam) {
    tabExam.click(); await sleep(900);
    emit(!!$("#pageBody-history #btnExamNew") || $(".hist-tab.on").dataset.htab === "exam",
         "T6c 切到真题精读页签，内容随之切换");
  }

  /* ---- T7 今日复习 ---- */
  $("#btnReview").click(); await sleep(900);
  emit(H() === "#review" && vis("page-review") && onlyOnePage(true),
       "T7 点今日复习：hash=#review，只有复习可见");
  emit(!!$("#pageBody-review .lib-summary"),
       "T7b 复习整页渲染出了统计条");
  const revCard = $("#pageBody-review .rev-card");
  if (revCard) {
    const showBtn = revCard.querySelector("[data-rev=show]");
    if (showBtn) {
      showBtn.click(); await sleep(200);
      emit(!!revCard.querySelector("[data-rev=known]"),
           "T7c 复习卡的「看答案 / 记住了」在整页里仍可点击");
    } else emit(true, "T7c 复习卡无按钮（本日无到期素材），跳过");
  } else emit(true, "T7c 今日无到期素材，跳过复习卡交互");

  /* ---- T8 从复习页点品牌名回今日 ---- */
  $("#brandHome").click(); await sleep(300);
  emit(H() === "" && vis("todayView") && onlyOnePage(false),
       "T8 点左上角品牌名：回到今日内容");

  /* ---- T9 浏览器后退键：today → 素材库 → 学习数据 → back ---- */
  $("#btnLibrary").click(); await sleep(300);
  $("#btnStats").click(); await sleep(600);
  const atStats = H() === "#stats";
  win.history.back(); await sleep(400);
  const backToLib = H() === "#library" && vis("page-library");
  win.history.back(); await sleep(400);
  const backToToday = H() === "" && vis("todayView");
  emit(atStats && backToLib && backToToday,
       "T9 浏览器后退：数据→素材库→今日 逐级退回（手机侧滑同此路径）");

  /* ---- T10 设置弹层 → 打开素材库：弹层要收掉 ---- */
  $("#btnSettings").click(); await sleep(600);
  const sheetOpen = !doc.getElementById("sheet").hidden;
  const favBtn = $("#btnFavList");
  let ok10 = false;
  if (sheetOpen && favBtn) {
    favBtn.click(); await sleep(500);
    ok10 = doc.getElementById("sheet").hidden && H() === "#library" && vis("page-library");
  }
  emit(ok10, "T10 从设置弹层跳素材库：弹层自动关闭并进入整页");

  /* ---- T14 从整页打开弹层：提交/取消后必须回到今日，不能渲染进隐藏页 ---- */
  win.navigate("history"); await sleep(800);
  win.openExamImport(); await sleep(500);
  const sheetOpen2 = doc.getElementById("sheet").hidden === false;
  win.backToToday(); await sleep(400);
  emit(sheetOpen2 && doc.getElementById("sheet").hidden === true &&
       H() === "" && vis("todayView") && onlyOnePage(false),
       "T14 历史整页里打开导入真题弹层：收掉后回到今日内容（内容不会写进隐藏页）");

  /* ---- T11 横向溢出 ---- */
  $("#btnLibrary").click(); await sleep(500);
  const de = doc.documentElement;
  const ov1 = de.scrollWidth - de.clientWidth;
  const body1 = doc.getElementById("pageBody-library");
  const ov2 = body1.scrollWidth - body1.clientWidth;
  emit(ov1 <= 1 && ov2 <= 1, "T11 素材库整页无横向溢出 (doc+" + ov1 + " body+" + ov2 + ")");

  /* ---- T12 无 JS 报错 ---- */
  emit(errs.length === 0, "T12 全程无未捕获错误 " + (errs.length ? errs.join(" ; ") : ""));

  /* ---- T13 深链直达 #stats ---- */
  /* 注意：/ 改成 /#stats 属于同文档导航，不会触发 load 事件，
     所以这里另起一个 iframe 来验证「冷启动直达整页」。 */
  const f2 = document.createElement("iframe");
  f2.style.width = W + "px"; f2.style.height = "600px";
  f2.src = "/#stats";
  document.body.appendChild(f2);
  const ready2 = await waitInner(f2);
  await sleep(1800);
  const d2 = f2.contentDocument;
  const ps = d2.getElementById("page-stats"), tv = d2.getElementById("todayView");
  const hash2 = f2.contentWindow.location.hash;
  emit(ready2 && hash2 === "#stats" && !!ps && ps.hidden !== true && !!tv && tv.hidden === true,
       "T13 直接访问 /#stats：落地即学习数据整页 [hash=" + hash2 +
       " stats=" + (ps ? (ps.hidden ? "hidden" : "shown") : "null") +
       " today=" + (tv ? (tv.hidden ? "hidden" : "shown") : "null") + "]");
}

main().catch(e => { document.getElementById("qa").textContent = "FATAL | " + e.message; });
</script></body></html>
"""


def needs_login() -> bool:
    """probe 一下：账号模式返回 401（免登录实例 200）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(BASE + "/api/bootstrap", timeout=15) as r:
            return r.status != 200
    except urllib.error.HTTPError as e:
        return e.code in (401, 403)
    except Exception:  # noqa: BLE001
        return False


def run(width):
    url = f"{BASE}/static/_qa_views.html?w={width}"
    if QA_USER:
        url += ("&user=" + urllib.parse.quote(QA_USER)
                + "&pass=" + urllib.parse.quote(QA_PASS))
    p = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--no-first-run", "--no-default-browser-check",
         "--virtual-time-budget=40000", "--window-size=1600,1200",
         "--dump-dom", url],
        capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace",
    )
    return p.stdout


def parse(html):
    m = re.search(r'<div id="qa">(.*?)</div>', html, re.S)
    if not m:
        return None
    raw = m.group(1)
    raw = (raw.replace("&amp;", "&").replace("&lt;", "<")
              .replace("&gt;", ">").replace("&quot;", '"'))
    return [ln for ln in raw.split("\n") if ln.strip()]


def main():
    if needs_login() and not QA_USER:
        print(f"跳过：{BASE} 需要登录（账号模式），但没给凭据。")
        print("  带上账号再跑：QA_USER=用户名 QA_PASS=密码 python tools/_views_test.py")
        return 0
    FRAME.write_text(FRAME_HTML, encoding="utf-8")
    try:
        all_lines, failed = [], 0
        for w in (390, 760):
            print(f"\n===== 视口宽 {w}px =====")
            lines = parse(run(w))
            if not lines:
                print("  [FATAL] 没拿到结果，检查服务是否在运行 / Chrome 路径")
                return 2
            for ln in lines:
                tag = f"[{w}]"
                print(f"  {tag} {ln}")
                if ln.startswith("FAIL") or ln.startswith("FATAL"):
                    failed += 1
                all_lines.append(ln)
        total = len(all_lines)
        print(f"\n合计 {total} 项，失败 {failed} 项")
        return 1 if failed else 0
    finally:
        if FRAME.exists():
            FRAME.unlink()
            print(f"（已删除临时验证页 {FRAME.name}）")


if __name__ == "__main__":
    sys.exit(main())
