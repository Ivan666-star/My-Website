# -*- coding: utf-8 -*-
"""「命题视角」渲染回归：起一个临时实例（临时库 + 别的端口），用真 Chrome 读回 DOM。

为什么要这么测：
  这次新增的 exam_focus 是「嵌套对象里再套数组里再套对象」，
  前端一旦字段名对不上，页面不会报错，只会**默默空白**——正是用户最反感的那种。
  所以必须把这个块真的渲染出来看一眼 DOM 里有没有东西。

线上库、线上 8848 全程不碰：临时库由真实库快照而来，所有写入只落在副本上。

⚠️ 副本会被「还原成没有账号的年代」（清 users/sessions、数据归 0 号桶），
   否则真实库里的账号会让服务进 MODE_ACCOUNTS、/ 跳 /login，
   于是 dump 回来的是登录页、断言集体失败。见下方「清账号」那段。

跑法：python tools/_examfocus_view_test.py
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402  （删临时目录要重试，见该模块注释）

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8896
BASE = f"http://127.0.0.1:{PORT}"

TMP = Path(tempfile.mkdtemp(prefix="efview_"))
DB = TMP / "app.db"
SRC = ROOT / "data" / "app.db"
if not SRC.exists():
    sys.exit("找不到 data/app.db")
s = sqlite3.connect(SRC)
d = sqlite3.connect(DB)
d.__enter__()
s.backup(d)
d.close()
s.close()

# ---- 把副本还原成「还没有账号」的年代 ----
# 这个测试早于多用户功能。真实库里现在有账号，/ 会 303 跳 /login，
# dump-dom 抓回来的是登录页，于是 20 多条断言集体失败、却看不出原因。
# 副本里清掉账号、数据归到 0 号桶，让服务落回 MODE_OPEN，行为与当年一致。
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
for _t in ("articles", "events", "favorites", "study_log", "user_profile", "annotations"):
    try:
        c.execute(f"UPDATE {_t} SET user_id=0")
    except sqlite3.OperationalError:
        pass  # 老快照可能还没有这张表
c.execute("DELETE FROM sessions")
c.execute("DELETE FROM users")
c.commit()

# ---- 找今天的文章，塞一份 exam_focus 进去 ----
art = c.execute(
    "SELECT id, date FROM articles WHERE source_type != 'exam' ORDER BY date DESC LIMIT 1"
).fetchone()
if not art:
    sys.exit("副本里没有可用的每日文章")
TODAY_ID, TODAY = art["id"], art["date"]

EXAM_FOCUS = {
    "pattern": "观点对立型",
    "main_thread": "首段抛出「技术进步必然改善公共讨论」这一流行看法，第二段用证据指出它站不住脚，"
                   "末段收束到一个带条件的立场。出题人会把主旨题挂在末段。",
    "set_points": [
        {
            "qtype": "推理判断",
            "anchor_en": "The assumption that technological progress inevitably improves public deliberation deserves closer scrutiny.",
            "para": 1,
            "why_here": "转折之后 + 段首。作者用一个抽象名词短语做主语，把态度藏在 deserves 里",
            "stem": "It can be inferred from Paragraph 1 that the author ____.",
            "right_direction": "对流行看法持保留态度，只主张「需要更仔细地审视」，不说它错",
            "traps": [
                {"trap": "过度推断", "sample_option": "The author denies that technology has any value for public debate.",
                 "why_wrong": "原文只是要求「更仔细审视」，选项写成了全盘否定"},
                {"trap": "反向干扰", "sample_option": "The author wholeheartedly endorses the prevailing assumption.",
                 "why_wrong": "与 deserves closer scrutiny 的保留语气正好相反"},
                {"trap": "张冠李戴", "sample_option": "Researchers have long questioned the value of public deliberation.",
                 "why_wrong": "把作者的态度安到了研究者头上"},
            ],
        },
        {
            "qtype": "态度观点",
            "anchor_en": "What looks like a triumph of access may in fact be a retreat from judgement.",
            "para": 3,
            "why_here": "may in fact be 是典型的「让步之后亮真观点」，态度题答案区",
            "stem": "The author's attitude towards greater access to information is best described as ____.",
            "right_direction": "谨慎的（cautious）而不是赞成的（approving）",
            "traps": [
                {"trap": "断章取义", "sample_option": "Approving, because access to information has expanded.",
                 "why_wrong": "只抓住了 triumph of access，没读 in fact be a retreat 的转折"},
                {"trap": "无中生有", "sample_option": "Indifferent, as the issue is of limited consequence.",
                 "why_wrong": "原文情绪明确，indifferent 是凭空安的"},
            ],
        },
        {
            "qtype": "例证指代逻辑",
            "anchor_en": "A study of news consumption patterns found that readers exposed to more sources were no better at distinguishing evidence from assertion.",
            "para": 2,
            "why_here": "引言处 + 研究修正型常见设点，例证题问「引这个研究是为了说明什么」",
            "stem": "The study mentioned in Paragraph 2 is cited to show that ____.",
            "right_direction": "服务于上一段提出的质疑：来源更多不等于判断力更强",
            "traps": [
                {"trap": "偷换范围", "sample_option": "Readers are always misled by a greater number of sources.",
                 "why_wrong": "no better 被放大成 always misled，程度和方向都变了"},
                {"trap": "张冠李戴", "sample_option": "News consumption patterns have changed dramatically in recent years.",
                 "why_wrong": "研究的结论被换成了研究的话题"},
            ],
        },
        {
            "qtype": "词义句意",
            "anchor_en": "The machinery of exposure has expanded, while the discipline of judgement has not.",
            "para": 4,
            "why_here": "特殊标点/对比处。machinery 与 discipline 是一组被刻意对照的抽象名词",
            "stem": "By saying “the discipline of judgement has not [expanded]”, the author means that ____.",
            "right_direction": "判断力这一侧没有跟着同步增长",
            "traps": [
                {"trap": "断章取义", "sample_option": "People are no longer willing to be disciplined.",
                 "why_wrong": "把 discipline 当成动词「管束」的字面义，忽略了它与 machinery 的对照结构"},
                {"trap": "过度推断", "sample_option": "Judgement can never be taught or improved.",
                 "why_wrong": "原文只说没同步扩张，没有说不可改进"},
            ],
        },
    ],
    "trap_summary": "「过度推断」和「断章取义」最危险：本篇作者的保留语气全靠 may in fact、deserves 这类软词承载，"
                    "选项一旦把它写成明确支持或明确反对，就都是错的。",
}
c.execute("UPDATE articles SET exam_focus=? WHERE id=?", (json.dumps(EXAM_FOCUS, ensure_ascii=False), TODAY_ID))
vrow = c.execute("SELECT id FROM vocab WHERE article_id=? LIMIT 1", (TODAY_ID,)).fetchone()
VOCAB_ID = vrow["id"] if vrow else None
if VOCAB_ID:
    c.execute(
        "UPDATE vocab SET exam_note=? WHERE id=?",
        ("真题常考 machinery 的抽象义「体制/机制」，而把 machine 的「机器」义拿来设词义题。", VOCAB_ID),
    )
c.commit()
c.close()
print(f"临时库 {DB}；今日文章 id={TODAY_ID}（{TODAY}）；词汇 id={VOCAB_ID}")

# ---- 起临时实例 ----
env = dict(os.environ)
env.update({
    "DB_PATH": str(DB),
    "AI_API_KEY": "",
    "DAILY_GENERATE_HOUR": "23",
    "ACCESS_PASSWORD": "",
    "PYTHONIOENCODING": "utf-8",
})
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)


def wait_up(ms: int = 25000) -> bool:
    for _ in range(ms // 300):
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


PASS = 0
FAIL: list[str] = []


def ok(cond, label):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL.append(label)
        print(f"  ✗ {label}")


def dump(url: str) -> str:
    p = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=30000", "--window-size=1400,1200",
         "--dump-dom", url],
        capture_output=True, timeout=120,
    )
    return p.stdout.decode("utf-8", "replace")


def text_of(html: str) -> str:
    body = html
    body = re.sub(r"<script[\s\S]*?</script>", " ", body)
    body = re.sub(r"<style[\s\S]*?</style>", " ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body)


try:
    if not wait_up():
        sys.exit(f"临时实例没起来（端口 {PORT} 被占？）")
    print(f"临时实例已就绪：{BASE}")

    html = dump(BASE + "/")
    txt = text_of(html)

    # 防线：一旦服务判定成 MODE_ACCOUNTS，/ 会跳登录页，
    # 后面 20 多条断言会集体失败却看不出原因。这里直接点名。
    if 'name="password"' in html and "精读解析" not in txt:
        sys.exit(
            "抓回来的是登录页，不是应用首页 —— 临时库副本里还残留账号，"
            "/ 被 303 重定向到了 /login。请检查上面的「清账号、归 0 号桶」步骤。"
        )

    print("\n[1] 命题视角块渲染")
    ok("命题视角" in txt, "页面上出现「命题视角」标题")
    ok("观点对立型" in txt, "篇章模式渲染出来")
    ok("出题人眼里的主线" in txt, "主线说明渲染出来")
    ok("4 个设题点" in txt and "覆盖 4 种题型" in txt, "summary 里显示设题点数量与题型覆盖数")

    print("\n[2] 每个设题点的四要素")
    for qt in ("推理判断", "态度观点", "例证指代逻辑", "词义句意"):
        ok(qt in txt, f"题型「{qt}」渲染出来")
    ok("第 1 段" in txt, "段落定位渲染出来")
    ok("deserves closer scrutiny" in txt, "设题原句（anchor_en）渲染出来")
    ok("为什么设在这里" in txt, "设题理由渲染出来")
    ok("It can be inferred from Paragraph 1" in txt, "真题题干（stem）渲染出来")
    ok("正确项方向" in txt, "正确项方向渲染出来")

    print("\n[3] 干扰项样本")
    ok("过度推断" in txt and "断章取义" in txt, "干扰项套路名渲染出来")
    ok("The author denies that technology has any value" in txt, "干扰项样本选项渲染出来")
    ok("原文只是要求「更仔细审视」" in txt, "干扰项错因渲染出来")
    ok("本篇最该提防" in txt, "trap_summary 渲染出来")

    print("\n[4] 词汇的「真题里怎么考」")
    ok("真题里怎么考" in txt, "词汇卡出现「真题里怎么考」")
    ok("设词义题" in txt, "exam_note 内容渲染出来")

    print("\n[5] 没有崩溃、没有半边空白")
    ok("<div class=\"acc-body\" id=\"examBody\"></div>" not in html,
       "examBody 不是空的（确实写进去了内容）")
    errs = re.findall(r"Uncaught[^<]{0,120}", html)
    ok(not errs, f"DOM 里没有未捕获错误（{errs[:1]}）")
    ok("本篇没有命题视角数据" not in txt, "有数据时不显示降级提示")

    # ---- 降级路径：把 exam_focus 清掉再看一次 ----
    print("\n[6] 老文章（无 exam_focus）优雅降级")
    c2 = sqlite3.connect(DB)
    c2.execute("UPDATE articles SET exam_focus='{}' WHERE id=?", (TODAY_ID,))
    c2.commit()
    c2.close()
    txt2 = text_of(dump(BASE + "/"))
    ok("本篇没有命题视角数据" in txt2, "显示降级说明而不是空白")
    ok("重新生成本篇" in txt2 or "导入真题" in txt2, "降级说明里给出下一步动作")
    ok("精读解析" in txt2, "页面其余部分照常渲染（没有连带崩掉）")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

print("\n" + "=" * 56)
print(f"通过 {PASS} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print(f"  ✗ {f}")
cleanup(TMP)
sys.exit(1 if FAIL else 0)
