# -*- coding: utf-8 -*-
"""积累闭环 ①「素材使用率 + 批改点名」的页面取证：临时库起服务 → 灌数据 → 出图 + DOM 断言。

用法（项目根目录执行）：
    python tools/_shot_mu.py            # 出图并断言
    python tools/_shot_mu.py --keep     # 保留临时目录（排查用）

产出（data/ 下，验证用，不是交付物）：
    data/_shot_mu_批改.png   430×2600  今日页 · 仿写区 —— 批改反馈里新的「素材用上了吗」块
    data/_shot_mu_统计.png   430×1900  #stats —— 新的「素材使用率」一组 + 未用清单

做法沿袭 _shot_math.py（先读它的注释再改这里）：
  1. 临时库 + AI_API_KEY="" → 没有账号 → auth 进 OPEN 模式（免登录，数据落 user 0）
  2. 用 _seed_english.py 灌 20 条素材 + 2 篇示例文章；把其中一篇的 date 改成今天，
     再手工造一条**已批改**的提交（feedback 里带 material_usage），今日页就能还原出反馈块
  3. 断言结果走 PUT /api/annotations 回传（key 含冒号、strokes 非空），Python 读临时库
  4. 等异步用 setTimeout 轮询（virtual-time-budget 下 rAF 几乎不发，用它=原地卡死）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from _qa_tmp import cleanup  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8897
BASE = f"http://127.0.0.1:{PORT}"
TMP = ROOT / "data" / "_mushot"
HARNESS = ROOT / "web" / "_qa_mu.html"
KEEP = "--keep" in sys.argv

# ⚠️ 必须在 import app.* 之前把 DB_PATH 写进**当前进程**的 os.environ：
#    下面那个 env 字典只发给子进程，当前进程的 settings 读的是 os.environ。
#    漏了这步的话，本脚本里所有 db.* 都会打在**正式库**上
#    （踩过：UPDATE 撞 UNIQUE 约束报错才发现跑错了库，幸好那一句原子失败没写成）。
os.environ["DB_PATH"] = str(TMP / "app.db").replace("\\", "/")
os.environ["AI_API_KEY"] = ""            # 不调模型：本脚本只验界面
os.environ["DAILY_GENERATE_HOUR"] = "23"

env = dict(os.environ)

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


# ------------------------------------------------------------
# HARNESS：同源 iframe 装真实页面，量完 DOM 把报告 PUT 回服务端
# ------------------------------------------------------------
HARNESS_TPL = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>mu qa</title>
<style>
  html,body{margin:0;background:#e9e6e0;overflow:hidden;height:100%}
  /* 沿用 _shot_math 的教训：绝对定位 + 容器满高，别用 flex 居中 */
  #stage{display:block;position:relative;width:100%;height:100%}
  iframe{border:0;background:#f5f3ef;display:block;position:absolute;left:0;top:0}
</style></head><body>
<div id="stage"><iframe id="fr"></iframe></div>
<script>
const Q = new URLSearchParams(location.search);
const W = +(Q.get("w") || 430), H = +(Q.get("h") || 1800);
const MODE = Q.get("mode") || "today";
const fr = document.getElementById("fr");
fr.style.width = W + "px"; fr.style.height = H + "px";
fr.src = MODE === "stats" ? "/#stats" : "/";

const report = { mode: MODE, lines: [], notes: {} };
const note = (k, v) => { report.notes[k] = v; report.lines.push(k + "=" + v); };
function raf(n){ return new Promise(r => setTimeout(r, 16 * n)); }
function until(fn, tries = 400){
  return new Promise((res, rej) => {
    let i = 0;
    const f = () => { let v = null; try { v = fn(); } catch (e) { v = null; }
      if (v) return res(v); if (++i > tries) return rej(new Error("until 超时")); setTimeout(f, 50); };
    f();
  });
}
/* 回传：keepalive + 里程碑各发一次（千万别 setInterval —— 会卡住虚拟时间） */
function send(){
  return fetch("/api/annotations", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    keepalive: true,
    body: JSON.stringify({ key: "qa-mu:" + MODE, data: { strokes: [{ t: 1 }], report: report } }),
  }).then(r => { report.notes.send_status = "ok:" + r.status; return r.status; })
    .catch(e => { report.notes.send_status = "err:" + (e && e.message); return -1; });
}
(async () => {
  try {
    const doc = () => fr.contentDocument;
    if (MODE === "today") {
      /* 今日页：等批改反馈块出现（boot → /api/bootstrap → renderImitation → renderFeedback） */
      await until(() => { const d = doc(); return d && d.querySelector("#imFeedback .mu"); });
      const d = doc();
      note("mu_block", 1);
      note("rate_text", (d.querySelector(".mu-rate") || {}).textContent || "");
      note("used_count", d.querySelectorAll(".mu .rec-item").length);
      note("first_used", (d.querySelector(".mu .rec-en") || {}).textContent || "");
      note("has_unused_group",
        [...d.querySelectorAll(".mu-sub")].some(e => (e.textContent || "").includes("没用过")) ? 1 : 0);
      note("fb_score", (d.querySelector(".fb-score") || {}).textContent || "");
    } else {
      /* 统计页：等「素材使用率」一组出现（route → loadStats → /api/stats） */
      await until(() => { const d = doc(); return d && (d.body.innerText || "").includes("素材使用率"); });
      const d = doc();
      const txt = d.body.innerText || "";
      note("stats_block", 1);
      note("has_mastered_card", txt.includes("已巩固素材") ? 1 : 0);
      note("has_used_card", txt.includes("用过的") ? 1 : 0);
      note("has_unused_card", txt.includes("一次没用过") ? 1 : 0);
      note("has_ratio_card", txt.includes("使用率") ? 1 : 0);
      note("unused_chips", d.querySelectorAll(".weak-word").length);
      note("hint_text",
        txt.includes("攒了不用等于没攒") ? 1 : 0);
    }
    await send();
  } catch (e) {
    note("ERR", e.message);
  }
  await send();
  await raf(4);
  await send();
})();
</script></body></html>"""


def capture(name: str, size, query: str, budget="300000"):
    w, h = size
    p = ROOT / "data" / name
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           f"--window-size={w},{h}", f"--virtual-time-budget={budget}",
           f"--screenshot={p}", f"{BASE}/static/_qa_mu.html?{query}"]
    try:
        subprocess.run(cmd, env=env, timeout=100,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False
    return p.exists() and p.stat().st_size > 5000


def read_reports() -> dict:
    import sqlite3
    db_file = TMP / "app.db"
    if not db_file.exists():
        return {}
    try:
        c = sqlite3.connect(db_file)
        rows = c.execute(
            "SELECT doc_key, data FROM annotations WHERE doc_key LIKE 'qa-mu:%'").fetchall()
        c.close()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for k, v in rows:
        try:
            out[k.split(":", 1)[1]] = json.loads(v).get("report", {}).get("notes", {})
        except Exception:  # noqa: BLE001
            pass
    return out


print("=" * 64)
print("积累闭环 ① 页面取证（批改点名 + 素材使用率）")
print("=" * 64)

# ------------------------------------------------------------
# 1) 临时库 + 灌数据
# ------------------------------------------------------------
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

from app import db  # noqa: E402

db.init_db()
db.init_db()

print("\n[1] 灌示例数据（_seed_english.py --user 0）")
r = subprocess.run([sys.executable, str(ROOT / "tools" / "_seed_english.py"), "--user", "0"],
                   env=env, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
check("种子脚本跑通", r.returncode == 0, (r.stderr or r.stdout)[-120:] if r.returncode else "")

TODAY = db.today_str()
with db.as_user(0):
    # 取一篇示例文章改成「今天」，今日页才有内容
    art = db.query_one(
        "SELECT * FROM articles WHERE COALESCE(source_type,'ai')!='exam' ORDER BY id LIMIT 1")
    check("有示例文章可改期", bool(art))
    db.execute("UPDATE articles SET date=? WHERE id=?", (TODAY, art["id"]))
    # 挑一条作文库素材，写进「学生的作文」里 —— 批改点名应认出它
    mat = db.query_one(
        "SELECT * FROM favorites WHERE library='writing' ORDER BY id LIMIT 1")
    check("有作文库素材可引用", bool(mat))
    essay = (f"From my perspective, {mat['content']} in the long run, "
             "and we should not ignore it.")
    mu = db.annotate_material_usage(essay)
    check("点名算出 used>=1", len(mu["used"]) >= 1,
          f"({[u['content'] for u in mu['used']]})")

    task = db.query_one(
        "SELECT * FROM imitation_tasks WHERE article_id=? ORDER BY id", (art["id"],))
    if not task:      # 兜底：种子没带任务就补一条
        db.execute(
            "INSERT INTO imitation_tasks(article_id,prompt_cn,reference_answer) VALUES(?,?,?)",
            (art["id"], "用今天的表达写一两句你的看法。", essay))
        task = db.query_one(
            "SELECT * FROM imitation_tasks WHERE article_id=? ORDER BY id DESC", (art["id"],))
    check("有仿写任务", bool(task))

    fb = {
        "score": 7.5, "verdict": "结构完整，表达可以更主动。",
        "issues": [], "polished": "", "dimensions": {"语言": 7, "结构": 8},
        "recommended_expressions": [], "worth_saving": False,
        "next_step": "把「用上了」的那条表达换个主语再用一次。",
        "material_usage": mu,
    }
    db.execute(
        "INSERT INTO submissions(task_id,round,answer,feedback,score) VALUES(?,?,?,?,?)",
        (task["id"], 1, essay, json.dumps(fb, ensure_ascii=False), 7.5))
    db.touch_study_log(TODAY, art["id"])
    check("已造一条带 material_usage 的批改记录", True)

# ------------------------------------------------------------
# 2) 起服务 + 出图
# ------------------------------------------------------------
print("\n[2] 起临时服务并出图")
HARNESS.write_text(HARNESS_TPL, encoding="utf-8")
svc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app",
     "--host", "127.0.0.1", "--port", str(PORT), "--no-use-colors"],
    env=env, cwd=str(ROOT),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    ready = False
    for _ in range(40):
        time.sleep(0.5)
        try:
            o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            rr = o.open(f"{BASE}/api/health", timeout=3)
            if rr.status == 200:
                ready = True
                break
        except Exception:  # noqa: BLE001
            continue
    check("服务就绪", ready)
    if not ready:
        raise SystemExit(1)

    check("出图：批改页", capture("_shot_mu_批改.png", (430, 2600), "mode=today"))
    check("出图：统计页", capture("_shot_mu_统计.png", (430, 1900), "mode=stats"))
finally:
    svc.terminate()
    try:
        svc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        svc.kill()

# ------------------------------------------------------------
# 3) 读回报告并断言
# ------------------------------------------------------------
print("\n[3] DOM 断言（页面回传）")
rep = read_reports()
check("两份报告都回传了", set(rep.keys()) >= {"today", "stats"}, f"({sorted(rep.keys())})")

t = rep.get("today", {})
check("today：反馈里出现「素材用上了吗」块", t.get("mu_block") == 1, f"({t.get('ERR', '')})")
check("today：使用率句子有内容", bool(t.get("rate_text")))
check("today：点名到用上的素材", (t.get("used_count") or 0) >= 1,
      f"({t.get('first_used', '')})")
check("today：有「一直没用过」提示组", t.get("has_unused_group") == 1)
check("today：批改本体也在（分数）", bool(t.get("fb_score")))

s = rep.get("stats", {})
check("stats：出现「素材使用率」组", s.get("stats_block") == 1, f"({s.get('ERR', '')})")
check("stats：四张卡都在",
      s.get("has_mastered_card") == 1 and s.get("has_used_card") == 1
      and s.get("has_unused_card") == 1 and s.get("has_ratio_card") == 1)
check("stats：未用素材清单有内容", (s.get("unused_chips") or 0) >= 1,
      f"({s.get('unused_chips')} 个)")
check("stats：解释文案在", s.get("hint_text") == 1)

# ------------------------------------------------------------
print("\n" + "=" * 64)
print(f"结果：PASS {ok} / FAIL {fail}")
print("=" * 64)

if HARNESS.exists():
    HARNESS.unlink()
if not KEEP:
    cleanup(TMP)
sys.exit(1 if fail else 0)
