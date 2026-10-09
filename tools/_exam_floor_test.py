"""难度下限 + 英一水位线（词数闸门）+ 命题视角落库 的回归测试。

自己起自己收：复制真实库到临时目录，所有写入只落副本，不碰线上 data/app.db。

跑法：python tools/_exam_floor_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="examfloor_"))
DB_PATH = TMP / "app.db"
SRC_DB = ROOT / "data" / "app.db"

if SRC_DB.exists():
    # 用 SQLite 在线备份 API 拿一致快照（比直接复制 .db/-wal/-shm 三个文件更稳）
    s = sqlite3.connect(SRC_DB)
    d = sqlite3.connect(DB_PATH)
    d.__enter__()
    s.backup(d)
    d.close()
    s.close()
    COPIED = True
else:
    COPIED = False

os.environ["DB_PATH"] = str(DB_PATH)
os.environ["AI_API_KEY"] = ""            # 不真的调模型
os.environ["DAILY_GENERATE_HOUR"] = "23"  # 关掉内置调度的自动生成窗口

from app import db                      # noqa: E402
from app.ai import pipeline, prompts    # noqa: E402
from app.ai.provider import AIError     # noqa: E402
from app.config import settings         # noqa: E402

PASS = 0
FAIL: list[str] = []


def ok(cond, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL.append(label)
        print(f"  ✗ {label}")


print(f"临时库：{DB_PATH}（{'由真实库快照而来' if COPIED else '全新空库'}）")

# ============================================================
print("\n[1] 迁移：articles.exam_focus / vocab.exam_note")
# ============================================================
db.init_db()
db.use_local_context()  # 多用户之后：独立脚本要自己声明上下文（0 号桶 = 未认领/本地自用）
cols_art = {r["name"] for r in db.query("PRAGMA table_info(articles)")}
cols_voc = {r["name"] for r in db.query("PRAGMA table_info(vocab)")}
ok("exam_focus" in cols_art, "articles 表有 exam_focus 列")
ok("exam_note" in cols_voc, "vocab 表有 exam_note 列")

# 幂等：再跑一次不能崩
try:
    db.init_db()
    ok(True, "init_db() 重复执行不报错（迁移幂等）")
except Exception as exc:  # noqa: BLE001
    ok(False, f"init_db() 重复执行报错：{exc}")

if COPIED:
    arts = db.query("SELECT id, exam_focus FROM articles")
    if arts:
        bad = [a["id"] for a in arts if a["exam_focus"] in (None, "")]
        ok(not bad, f"存量文章 exam_focus 已回填（{len(arts)} 篇，空值 {len(bad)} 篇）")
    else:
        ok(True, "副本里没有文章，跳过回填检查")

# ============================================================
print("\n[2] 难度下限：绝不掉到英一以下")
# ============================================================
ok(settings.exam_min_difficulty == 3, f"EXAM_MIN_DIFFICULTY 默认为 3（实际 {settings.exam_min_difficulty}）")

_orig_base = settings.base_difficulty
try:
    settings.base_difficulty = 1          # 手动把基线压到最低档
    got = pipeline.decide_difficulty()
    ok(got >= 3, f"基线 1 时仍返回 {got}（≥3）")

    settings.base_difficulty = 5
    got = pipeline.decide_difficulty()
    ok(got == 5, f"基线 5 时返回 {got}（保留上调空间）")
finally:
    settings.base_difficulty = _orig_base

# ============================================================
print("\n[3] 词数：真数词，不是数字符")
# ============================================================
ok(pipeline._count_words("one two three") == 3, "_count_words 数的是词")
ok(pipeline._count_words("") == 0, "空串 → 0 词")
# 下面这串 320 个字符只有 8 个词——旧写法 len(body) >= 300 会误判为「够长」
fake = ("word " * 8).strip()
ok(len(fake) < 300 and pipeline._count_words(fake) == 8,
   f"旧闸门的假阳性样本：{len(fake)} 字符 / {pipeline._count_words(fake)} 词")

ok(prompts.word_band(3) == (430, 500), f"3 档词数区间 {prompts.word_band(3)}")
ok(prompts.word_band(5)[0] >= 500, f"5 档下限 {prompts.word_band(5)[0]} 词")
ok(prompts.word_band(1)[0] < prompts.word_band(3)[0], "低档区间仍存在（仅作兜底）")

# ============================================================
print("\n[4] 水位线闸门：达标直通 / 不足先扩写 / 扩写无效才报错")
# ============================================================
SHORT = "This is a short paragraph. " * 6          # 约 30 词
LONG = ("The assumption that technological progress inevitably improves "
        "the quality of public deliberation deserves closer scrutiny. ") * 30


class FakeProvider:
    """记录调用次数，按脚本返回内容。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def chat(self, system, user, **kw):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


# 4a 本来就够长 → 不该调模型
p = FakeProvider({"body": "unused"})
art = {"title": "T", "body": LONG}
out = pipeline._ensure_exam_length(p, art, LONG, 3, "测试")
ok(out == LONG and p.calls == 0, "已达标时原样返回，且不额外调用模型")

# 4b 太短 → 扩写一次，采用扩写结果
p = FakeProvider({"title": "T2", "source": "Imitating X", "body": LONG})
art = {"title": "T", "source": "", "body": SHORT}
out = pipeline._ensure_exam_length(p, art, SHORT, 3, "测试")
ok(p.calls == 1, f"不足时扩写调用 1 次（实际 {p.calls}）")
ok(pipeline._count_words(out) >= 430, f"扩写后 {pipeline._count_words(out)} 词，达到下限")
ok(art["body"] == out, "扩写结果写回了 art")
ok(art["source"] == "Imitating X", "扩写结果里非空字段被采用")
ok(art["title"] == "T2", "标题按扩写结果更新")

# 4c 扩写还是不够 → 必须报错，不许把低于英一的文章放进库
p = FakeProvider({"body": SHORT})
art = {"title": "T", "body": SHORT}
raised = None
try:
    pipeline._ensure_exam_length(p, art, SHORT, 3, "测试")
except AIError as exc:
    raised = str(exc)
ok(raised is not None, "两次都不达标 → 抛 AIError")
if raised:
    ok("430" in raised, f"报错信息里带下限：{raised[:60]}…")

# ============================================================
print("\n[5] 命题视角：exam_focus / exam_note 落库并下发")
# ============================================================
EXAM_FOCUS = {
    "pattern": "观点对立型",
    "main_thread": "首段抛争议 → 中段让步 → 末段收束到有条件成立",
    "set_points": [
        {
            "qtype": "推理判断",
            "anchor_en": "The assumption deserves closer scrutiny.",
            "para": 1,
            "why_here": "转折之后",
            "stem": "It can be inferred from Paragraph 1 that the author ____.",
            "right_direction": "对流行看法持保留态度",
            "traps": [
                {"trap": "过度推断", "sample_option": "The author denies any value of technology.",
                 "why_wrong": "原文是保留态度，选项写成了全盘否定"},
                {"trap": "反向干扰", "sample_option": "The author fully endorses the assumption.",
                 "why_wrong": "与原文态度相反"},
            ],
        }
    ],
    "trap_summary": "本篇最容易踩过度推断",
}

pkg = {
    "article": {
        "title": "A Test Article",
        "subtitle": "",
        "source": "Imitating the leader section of The Economist",
        "topic": "科技发展",
        "body": LONG,
    },
    "analysis": {
        "translation": [{"en": "x", "zh": "y", "note": ""}],
        "vocab": [
            {
                "word": "scrutiny",
                "pos": "n.",
                "meaning_in_context": "仔细审视",
                "common_meanings": "审查",
                "exam_note": "真题常把它和 examination 放一起做词义辨析",
                "cn_definition": "细看",
                "example": "closer scrutiny",
                "importance": 5,
            }
        ],
        "exam_focus": EXAM_FOCUS,
    },
    "writing": {},
    "date": "2099-01-01",
    "source_type": "ai",
    "model": "test-model",
    "difficulty": 3,
    "topic": "科技发展",
}
row = pipeline.store_package(pkg, replace=True)
bundle = pipeline.story_bundle(row["id"])

ok(bundle["article"]["exam_focus"].get("pattern") == "观点对立型",
   "exam_focus.pattern 正确落库并下发")
ok(len(bundle["article"]["exam_focus"].get("set_points") or []) == 1,
   "set_points 完整保留")
sp = (bundle["article"]["exam_focus"].get("set_points") or [{}])[0]
ok(len(sp.get("traps") or []) == 2, "干扰项样本保留 2 条")
ok(bundle["vocab"] and bundle["vocab"][0].get("exam_note", "").startswith("真题常把"),
   "vocab.exam_note 落库并下发")

# 老文章（没有 exam_focus）→ 解析成空对象，前端据此隐藏
old = db.query_one("SELECT id FROM articles WHERE date != '2099-01-01' LIMIT 1")
if old:
    b2 = pipeline.story_bundle(old["id"])
    ok(b2["article"]["exam_focus"] == {}, "老文章 exam_focus 安全降级为 {}")

# ============================================================
print("\n[6] 提示词：难度与去空谈的要求确实写进去了")
# ============================================================
ap = prompts.build_article_prompt("科技发展", 3)
ok("430" in ap and "绝对不许低于" in ap, "文章提示词带英一词数下限")
ok("难度必须达到考研英语一" in prompts.ARTICLE_SYSTEM, "文章 system 提示词写明难度要求")
ok("设题点植入清单" in ap, "文章提示词注入了设题点植入清单")
ok("转折处" in ap and "让步与对比" in ap, "设题点清单含具体位置")
ok("Adapted from" in ap, "禁止伪造出处的要求在提示词里")

an = prompts.build_analysis_prompt("T", LONG, exam_mode=False)
ok("exam_focus" in an, "解析提示词要求输出 exam_focus")
ok("禁止空谈" in prompts.ANALYSIS_SYSTEM, "解析 system 提示词写明禁止空谈")
ok("在哪里设题" in an, "解析提示词给出设题位置规律")
ok("exam_note" in an, "解析提示词要求每个词给 exam_note")

ep = prompts.build_expand_prompt("T", SHORT, 30, 3)
ok("430" in ep and "30" in ep, "扩写提示词带现有词数与目标下限")

# ============================================================
print("\n" + "=" * 56)
print(f"通过 {PASS} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"临时目录（可删）：{TMP}")
sys.exit(1 if FAIL else 0)
