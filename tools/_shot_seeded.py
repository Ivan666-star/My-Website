# -*- coding: utf-8 -*-
"""把**线上库的副本**装进无头浏览器，出图并断言：刚灌进去的 13 道题渲染是否正常。

为什么要费这个事：
    "数据在库里" 和 "页面上看得对" 是两件事。这批题干里有矩阵、分段函数、
    长知识点，全都走 KaTeX —— 一旦有哪条渲染崩了，用户在手机上看到的就是坏页面。
    所以不信 SQL，直接看像素。

怎么做到不碰线上：
    拷一份 data/app.db 到临时目录 → 把 users 清掉、把错题 user_id 改成 0（未认领桶）
    → AI_API_KEY="" 起服务 → 没有账号 → auth 进 OPEN 模式，免登录。
    线上 8848 完全不动。

跑法：python tools/_shot_seeded.py
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from _qa_tmp import cleanup  # noqa: E402

PORT = 8897
BASE = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="seedshot_"))
HARNESS = ROOT / "web" / "_qa_seed.html"
PY = sys.executable
CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe"

PASS, FAIL = [], []


def check(label: str, cond, extra: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


_env = dict(os.environ)          # ⚠️ 必须在系统环境之上**叠加**，别整个替换 ——
_env.update({                    #    替换掉之后服务连 `PATH` 以外的东西都没了，起不来
    "DB_PATH": str(TMP / "app.db").replace("\\", "/"),
    "AI_API_KEY": "",            # 不调模型：只验界面渲染
    "DAILY_GENERATE_HOUR": "23",  # 不自动生成当天内容
})


def req(path: str, body=None):
    url = BASE + path
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data,
                               headers={"Content-Type": "application/json"} if data else {},
                               method="POST" if data is not None else "GET")
    o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with o.open(r, timeout=20) as fh:
            txt = fh.read().decode()
            try:
                return json.loads(txt)
            except ValueError:
                return {"_raw": txt[:300]}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:200]}


# ============================================================
# 探针页：iframe 装 app → 跳 #math → 量数据 → keepalive 回传
# ============================================================
HARNESS_TPL = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>seed qa</title>
<style>
  html,body{margin:0;background:#e9e6e0;overflow:hidden;height:100%}
  #stage{display:block;position:relative;width:100%;height:100%}
  iframe{border:0;background:#f5f3ef;display:block;position:absolute;left:0;top:0}
</style></head><body>
<div id="stage"><iframe id="fr"></iframe></div>
<script>
const Q = new URLSearchParams(location.search);
const W = +(Q.get("w") || 430), H = +(Q.get("h") || 900);
const fr = document.getElementById("fr");
fr.style.width = W + "px"; fr.style.height = H + "px";
fr.src = "/";
const report = { w: W, h: H, notes: {} };
const note = (k, v) => { report.notes[k] = v; };
function raf(n){ return new Promise(r => setTimeout(r, 16 * n)); }
function until(fn, tries = 300){
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
    body: JSON.stringify({ key: "qa-seed:" + W, data: { strokes: [{ t: 1 }], report } }),
  }).then(r => r.status).catch(() => -1);
}
(async () => {
  try {
    await until(() => fr.contentDocument && fr.contentDocument.querySelector("#page-subjects"));
    await raf(6);
    const w = fr.contentWindow, d = fr.contentDocument;
    /* 截图首屏：科目选择（顺带看倒计时卡片还在不在） */
    note("has_countdown", d.querySelector(".exam-count") ? 1 : 0);
    await send();

    w.location.hash = "#math";
    await until(() => d.querySelectorAll(".math-card").length > 0, 400);
    await raf(10);
    note("view_math", d.body.classList.contains("view-math") ? 1 : 0);
    note("cards", d.querySelectorAll(".math-card").length);
    note("kp_rows", d.querySelectorAll(".math-card .math-kp").length);
    note("kp_with_tag", d.querySelectorAll(".math-kp .kp-tag").length);
    note("katex_nodes", d.querySelectorAll(".katex-html").length);
    note("mtex_fallback", d.querySelectorAll(".mtex").length);
    note("due_rows", d.querySelectorAll(".math-due-row").length);
    note("img_bar", d.querySelector(".math-imgbar") ? 1 : 0);
    const bad = [];
    d.querySelectorAll("#pageBody-math > *, .math-card, .topbar > *").forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.right > W + 1 || r.left < -1)) {
        bad.push((el.className || el.tagName) + "@" + Math.round(r.right));
      }
    });
    note("overflow", bad.length ? bad.slice(0, 4).join(" | ") : "无");
    note("scroll_w", d.documentElement.scrollWidth);

    /* 展开一条**有解析**的。
       ⚠️ 别直接点第一条：列表按 id 倒序，第一条恰好就是故意不解析的那条，
          点开只会看到"还没有解析"占位，`0 步 / 0 行` 一眼看着像渲染坏了（第一次就踩了）。 */
    const all = [...d.querySelectorAll(".math-card")];
    const noAna = all.find(c => !c.querySelector(".math-kp"));
    note("noana_has_analyze_btn", noAna && noAna.querySelector("[data-m-analyze]") ? 1 : 0);
    note("noana_shows_chip", noAna && noAna.textContent.indexOf("未解析") >= 0 ? 1 : 0);
    const withAna = all.find(c => c.querySelector(".math-kp"));
    const tg = withAna && withAna.querySelector("[data-m-toggle]");
    if (tg && Q.get("open") === "1") { tg.click(); await raf(12); }
    const open = d.querySelector(".math-card.open");
    note("opened", open ? 1 : 0);
    note("open_katex", open ? open.querySelectorAll(".katex-html").length : 0);
    note("open_steps", open ? open.querySelectorAll(".ma-steps li").length : 0);
    note("open_notes", open ? open.querySelectorAll(".ma-row").length : 0);
    if (open) open.scrollIntoView({ block: "center" });
    await raf(6);
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
            raise RuntimeError("服务进程退了，看 srv.log")
        try:
            with o.open(BASE + "/api/health", timeout=3) as fh:
                if fh.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError("服务没起来")


def capture(name: str, w: int, h: int, open_card: int = 0, budget="300000") -> bool:
    p = ROOT / "data" / name
    q = f"w={w}&h={h}&open={open_card}"
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           f"--window-size={w},{h}", f"--virtual-time-budget={budget}",
           f"--screenshot={p}", f"{BASE}/static/_qa_seed.html?{q}"]
    try:
        subprocess.run(cmd, env=_env, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False
    return p.exists() and p.stat().st_size > 5000


def read_reports() -> dict:
    """回传报告**直接读临时库文件**，不要走 HTTP。

    踩过的坑：走 `/api/annotations` 轮询时，Chrome 被强杀留下的半开连接会让 GET
    一路挂到超时，一次出图能被拖成十分钟。读文件是稳的。
    """
    db_file = TMP / "app.db"
    if not db_file.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT doc_key, data FROM annotations WHERE doc_key LIKE 'qa-seed:%'"
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for r in rows:
        try:
            data = json.loads(r["data"] or "{}")
        except (TypeError, ValueError):
            continue
        rep = (data or {}).get("report")
        if not rep:
            continue
        try:
            out[int(str(r["doc_key"]).split(":", 1)[1])] = rep
        except (ValueError, IndexError):
            continue
    return out


def main() -> None:
    if not Path(CHROME).exists():
        sys.exit(f"找不到 Chrome：{CHROME}")

    src = ROOT / "data" / "app.db"
    # ⚠️⚠️ 必须连着 `-wal` / `-shm` 一起拷！
    # 库跑在 WAL 模式下，刚提交的数据**还在 -wal 里**，只拷 app.db 会拿到一份
    # **早于本次写入**的旧库（第一次就踩了：拷过来一查 0 条，还以为没写进去）。
    for suf in ("", "-wal", "-shm"):
        s = Path(str(src) + suf)
        if s.exists():
            shutil.copy2(s, Path(str(TMP / "app.db") + suf))
    con = sqlite3.connect(str(TMP / "app.db"))
    con.execute("DELETE FROM users")                      # 让 auth 进 OPEN 模式
    con.execute("UPDATE math_problems SET user_id=0")     # 挪到「未认领桶」
    con.execute("UPDATE math_review_log SET user_id=0")
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM math_problems").fetchone()[0]
    con.close()
    print(f"临时库就绪（{n} 道题）-> {TMP}\n")

    HARNESS.write_text(HARNESS_TPL, encoding="utf-8")
    SRV_LOG = TMP / "srv.log"
    f = SRV_LOG.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT),
         "--no-use-colors", "--log-level", "warning"],
        cwd=str(ROOT), env=_env, stdout=f, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_health(proc)
        print("[1] 临时实例就绪（免登录）\n")

        print("[2] 服务端先自己确认数据在")
        lst = req("/api/math/problems?with_analysis=1")
        items = lst.get("items", [])
        check("接口返回 13 道错题", len(items) == 13, f"(实得 {len(items)})")
        check("12 条有知识点（未解析那条按设计为空）",
              sum(1 for x in items if (x.get("knowledge") or "").strip()) == 12,
              f"(有知识点的 {sum(1 for x in items if (x.get('knowledge') or '').strip())} 条)")
        check("每条题干都带 $ 定界符", all("$" in (x.get("stem") or "") for x in items))
        check("有一条未解析（留给「AI 解析」按钮）",
              sum(1 for x in items if not x.get("analyzed")) == 1)
        meta = req("/api/math/meta")
        st = meta.get("stats", {})
        check("统计：13 总量 / 5 今天该复习 / 1 已掌握",
              st.get("total") == 13 and st.get("due") == 5 and st.get("mastered") == 1,
              f"({st.get('total')}/{st.get('due')}/{st.get('mastered')})")
        rnd = req("/api/math/random")
        check("开始刷题能抽到题（不带筛选）", bool((rnd.get("problem") or {}).get("id")))
        check("抽到的题**不泄露**解析（先做题后看解析）",
              not (rnd.get("problem") or {}).get("solution"))

        print("\n[3] 出图 + 页面侧测量")
        ok1 = capture("_seed_math_phone.png", 430, 900, open_card=1)   # 手机档：展开一条看解析
        ok2 = capture("_seed_math_ipad.png", 1024, 1366, open_card=0)  # 平板档：看页面顶部与卡片列
        check("手机档出图", ok1)
        check("平板档出图", ok2)
        reps = read_reports()
        rep = reps.get(430)
        if not rep:
            check("页面回传了报告", False, "(没回传，看 data/_seed_math_phone.png 定位)")
        else:
            n = rep["notes"]
            check("数学页挂上 view-math", n.get("view_math") == 1)
            check("13 张错题卡都渲染出来", n.get("cards") == 13, f"({n.get('cards')})")
            check("12 张卡直接露出「知识点」（未解析那条没有）",
                  n.get("kp_rows") == 12, f"({n.get('kp_rows')})")
            check("知识点标签样式都在", n.get("kp_with_tag") == n.get("kp_rows"))
            check("公式真的渲染成排版结果（矩阵/分段函数）",
                  (n.get("katex_nodes") or 0) >= 40, f"({n.get('katex_nodes')} 处)")
            check("没有任何公式退化成源码", (n.get("mtex_fallback") or 0) == 0,
                  f"({n.get('mtex_fallback')} 处兜底)")
            check("手机档无横向溢出", n.get("overflow") == "无", f"({n.get('overflow')})")
            check("今日待复习区有 5 行", n.get("due_rows") == 5, f"({n.get('due_rows')})")
            check("没有原图就不出现「清理原图」横条", n.get("img_bar") == 0)
            check("主界面倒计时还在", n.get("has_countdown") == 1)
            check("展开一条后有解析块", n.get("opened") == 1)
            check("展开后关键步骤渲染成列表", (n.get("open_steps") or 0) >= 3,
                  f"({n.get('open_steps')} 步)")
            check("展开后解析区字段齐全（备注/知识点/解法…）",
                  (n.get("open_notes") or 0) >= 4, f"({n.get('open_notes')} 行)")
            check("未解析那条给了「AI 解析」按钮", n.get("noana_has_analyze_btn") == 1)
            check("未解析那条带「未解析」标记", n.get("noana_shows_chip") == 1)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=12)
        except Exception:  # noqa: BLE001
            proc.kill()
        f.close()
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
