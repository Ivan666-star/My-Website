# -*- coding: utf-8 -*-
"""给英语一侧灌一批「示例数据」，方便手动点着考察每一页。

用法（在项目根目录执行）：
    python tools/_seed_english.py                  # 灌进账号 1（素材库 + 2 篇示例文章）
    python tools/_seed_english.py --user 2         # 指定账号
    python tools/_seed_english.py --materials-only # 只灌素材库（不动每日文章）
    python tools/_seed_english.py --articles-only  # 只灌 2 篇示例文章（不动素材库）
    python tools/_seed_english.py --clear          # 只删本脚本灌的，不动你自己收藏/生成的
    python tools/_seed_english.py --dry            # 只打印将要写入什么，不落库

为什么要补这两块（现状核对过）：
    账号 1 里已经有 5 篇**真实 AI 生成**的每日文章（10-03 ~ 10-07）+ 1 篇真题精读，
    「今日内容」「历史」都不空。真正空着的是：
      · 素材库（/api/favorites → favorites 表 0 行）
      · 复习（/api/review/due → 没有素材进周期，统计全是 0）
      · 学习数据的 7 天窗口是 10-01 ~ 10-07，前两天空着
    所以这里灌：20 条素材（覆盖两个库 / 5 个位置 / 6 种文体 / A·B·C 级 / 各复习状态）
    + 2 篇补窗口的示例文章（10-01 教育学习、10-02 环境保护）。

诚实说明（别改成假装真题）：
    两篇文章是**按英一标准原创**的样刊，不是任何一年的真题原文 ——
    真题必须用户自己贴原文导入，编造原文等于教错东西。
    文章 source 一律以 `[示例] ` 开头，素材 tags 一律带 `示例`，
    两者都能被 --clear 精确删掉，不会误伤用户自己的数据。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app import db  # noqa: E402
from app.ai.pipeline import store_package  # noqa: E402
from _seed_en_articles import ARTICLES  # noqa: E402

TAG = "示例"             # 打进 favorites.tags，卡片上看得见，也用来精确清理
SRC_PREFIX = "[示例] "    # 打进 articles.source 前缀，用来精确清理


# ============================================================
# 素材库示例：20 条
# ------------------------------------------------------------
# cyc：due 今天到期 · overdue 已逾期 · futureN 在周期里（N 天后）· mastered 已巩固 · out 已移出
# art：挂到哪天的文章上（"" = 不挂，模拟手动收藏）
# ============================================================
MATERIALS = [
    # ---------------- 📖 阅读积累库（为读懂服务） ----------------
    {
        "item": "be prone to", "cn": "易于……的；有……倾向的（多指不好的事）",
        "ex": "Centralised systems are prone to single points of failure.",
        "lib": "reading", "cat": "通用表达", "pos": "universal", "gen": "universal",
        "fn": "精准释义", "lv": "C", "art": "2026-10-07", "cyc": "due", "rc": 0,
        "tags": ["阅读积累", "熟词"],
        "usage": "后面跟名词或动名词，主语多为系统、人、地区；含轻微贬义，写「风险」时比 easy to 准确。",
    },
    {
        "item": "by no means", "cn": "绝不；一点也不",
        "ex": "By no means is this a trivial question.",
        "lib": "reading", "cat": "通用表达", "pos": "universal", "gen": "universal",
        "fn": "精准释义", "lv": "C", "art": "2026-10-06", "cyc": "due", "rc": 1,
        "tags": ["阅读积累", "倒装信号"],
        "usage": "放在句首时主句必须**部分倒装**（By no means is ...）。看到它就知道作者在强烈否定。",
    },
    {
        "item": "at the expense of", "cn": "以……为代价（后面是被牺牲掉的那一方）",
        "ex": "Output rose steadily, at the expense of the soil that produced it.",
        "lib": "reading", "cat": "经济就业", "pos": "universal", "gen": "universal",
        "fn": "精准释义", "lv": "B", "art": "2026-10-05", "cyc": "overdue", "rc": 2,
        "tags": ["阅读积累", "因果"],
        "usage": "天然带「得不偿失」的评价色彩，比 lose 更书面。写作里也能直接用。",
    },
    {
        "item": "take a toll on", "cn": "对……造成损害（往往是缓慢、累积的）",
        "ex": "Night shifts take a toll on memory that shows up years later.",
        "lib": "reading", "cat": "社会现象", "pos": "universal", "gen": "universal",
        "fn": "精准释义", "lv": "C", "art": "2026-10-04", "cyc": "future1", "rc": 2,
        "tags": ["阅读积累", "健康"],
        "usage": "toll 原指过路费、伤亡人数。主语是造成损害的事，宾语是被损害的人或能力。",
    },
    {
        "item": "hold true for", "cn": "对……（依然）成立、适用",
        "ex": "The finding holds true for cities, but not for rural districts.",
        "lib": "reading", "cat": "科学研究", "pos": "universal", "gen": "universal",
        "fn": "精准释义", "lv": "C", "art": "2026-10-07", "cyc": "future2", "rc": 1,
        "tags": ["阅读积累", "易错"],
        "usage": "**不是**「保持真实」。hold 在这里是「成立、有效」，常与 for 连用限定适用范围。",
    },
    {
        "item": "It is not that ... ; it is that ...", "cn": "倒不是……，而是……",
        "ex": "It is not that people have lost the capacity to wait; it is that they have lost the occasions that once required it.",
        "lib": "reading", "cat": "通用表达", "pos": "universal", "gen": "universal",
        "fn": "句子结构", "lv": "B", "art": "2026-10-03", "cyc": "future4", "rc": 1,
        "item_type": "pattern", "tags": ["阅读积累", "句型"],
        "usage": "作者用来**纠正常见误解**的固定结构：先排除大家以为的原因，再给出真实原因。读到它要重点看第二半。",
    },
    {
        "item": "no sooner ... than ...", "cn": "一……就……",
        "ex": "No sooner had the report appeared than the industry began to dispute its figures.",
        "lib": "reading", "cat": "通用表达", "pos": "universal", "gen": "universal",
        "fn": "句子结构", "lv": "B", "art": "2026-10-05", "cyc": "mastered", "rc": 7,
        "item_type": "pattern", "tags": ["阅读积累", "句型"],
        "usage": "两个坑：主句用**过去完成时**；no sooner 置于句首要**部分倒装**（had the report appeared）。",
    },
    {
        "item": "Whether ... depends less on ... than on ...", "cn": "……在多大程度上取决于……，更多取决于……而非……",
        "ex": "Whether a policy works depends less on its design than on who is asked to carry it out.",
        "lib": "reading", "cat": "教育学习", "pos": "universal", "gen": "universal",
        "fn": "句子结构", "lv": "B", "art": "2026-10-04", "cyc": "future7", "rc": 2,
        "item_type": "pattern", "tags": ["阅读积累", "句型"],
        "usage": "whether 引导的主语从句 + less A than on B 的否定比较，重心永远在 than 之后。",
    },

    # ---------------- 📝 作文素材库（能直接写进作文） ----------------
    {
        "item": "With the relentless advance of ..., it has become commonplace to ...",
        "cn": "随着……的迅猛发展，……已经变得司空见惯。",
        "ex": "With the relentless advance of artificial intelligence, it has become commonplace to let algorithms decide what we read next.",
        "lib": "writing", "cat": "科技发展", "pos": "opening", "gen": "argumentative",
        "fn": "引出话题", "lv": "A", "art": "2026-10-05", "cyc": "future1", "rc": 0,
        "tags": ["开头句", "科技"],
        "usage": "开头段点出现象。比 With the development of 具体得多，relentless + commonplace 两个词就能撑起画面感。",
    },
    {
        "item": "As is vividly depicted in the picture, a growing number of ... are ...",
        "cn": "正如图画中生动描绘的那样，越来越多的……正在……",
        "ex": "As is vividly depicted in the picture, a growing number of students are still glued to their screens long after midnight.",
        "lib": "writing", "cat": "通用表达", "pos": "opening", "gen": "descriptive",
        "fn": "描述现象", "lv": "A", "art": "", "cyc": "out", "rc": 0,
        "tags": ["图画作文", "开头句"],
        "usage": "图画作文首段标准句式，两个逗号之间换成自己的描写。已移出复习，可以在素材库里试「重新加入复习」。",
    },
    {
        "item": "Granted, ... is not without its merits; yet ...",
        "cn": "诚然，……并非没有好处；然而……",
        "ex": "Granted, the app is not without its merits; yet its convenience should not blind us to what it quietly takes away.",
        "lib": "writing", "cat": "通用表达", "pos": "transition", "gen": "argumentative",
        "fn": "让步转折", "lv": "A", "art": "2026-10-07", "cyc": "future2", "rc": 1,
        "tags": ["让步", "过渡句"],
        "usage": "过渡段先承认对方再反驳，逻辑最稳。not without its merits 是「双重否定表委婉肯定」，比 it has advantages 高级。",
    },
    {
        "item": "What has changed is not so much ... as the speed at which ...",
        "cn": "改变的与其说是……，不如说是……的速度。",
        "ex": "What has changed is not so much the work itself as the speed at which we are now expected to do it.",
        "lib": "writing", "cat": "社会现象", "pos": "transition", "gen": "argumentative",
        "fn": "对比论证", "lv": "A", "art": "2026-10-04", "cyc": "future4", "rc": 2,
        "tags": ["对比", "not so much as"],
        "usage": "not so much A as B 和 less A than B 是同一家族，都是「与其说 A 不如说 B」，重心在 B。",
    },
    {
        "item": "The root of the problem lies not in ..., but in ...",
        "cn": "问题的根源不在于……，而在于……",
        "ex": "The root of the problem lies not in a shortage of money, but in a shortage of political will.",
        "lib": "writing", "cat": "环境保护", "pos": "body", "gen": "argumentative",
        "fn": "因果分析", "lv": "A", "art": "2026-10-02", "cyc": "future1", "rc": 0,
        "tags": ["因果", "主体段"],
        "usage": "分析根源时的主力句式，用 lie in（在于）比 be 更像英文评论。not in A but in B 让句子自带辨析力。",
    },
    {
        "item": "Take ... as a case in point: ...",
        "cn": "以……为例：……",
        "ex": "Take the humble roadside verge as a case in point: it supports more species than the field beside it.",
        "lib": "writing", "cat": "经济就业", "pos": "body", "gen": "argumentative",
        "fn": "举例论证", "lv": "B", "art": "2026-10-06", "cyc": "future2", "rc": 1,
        "tags": ["举例"],
        "usage": "举例子用，比 For example 更书面。case in point 是固定搭配，不要写成 case of point。",
    },
    {
        "item": "It is precisely because ... is slow that it is worth pursuing.",
        "cn": "恰恰因为……很慢，它才值得追求。",
        "ex": "It is precisely because reading is slow that it is worth protecting.",
        "lib": "writing", "cat": "人生哲理", "pos": "body", "gen": "argumentative",
        "fn": "提出观点", "lv": "A", "art": "2026-10-03", "cyc": "future4", "rc": 0,
        "tags": ["强调句", "观点句"],
        "usage": "It is ... that ... 强调句 + 因果关系，一下子把「慢」从缺点翻成优点，适合写品质、成长、读书类话题。",
    },
    {
        "item": "Only when ... becomes a matter of habit rather than of willpower can we ...",
        "cn": "只有当……成为一种习惯而非意志力的较量时，我们才能……",
        "ex": "Only when reading becomes a matter of habit rather than of willpower can we keep it up for years.",
        "lib": "writing", "cat": "品质精神", "pos": "ending", "gen": "argumentative",
        "fn": "呼吁行动", "lv": "A", "art": "2026-10-07", "cyc": "due", "rc": 3,
        "tags": ["结尾句", "倒装"],
        "usage": "结尾段呼吁。Only when 置于句首 → 主句部分倒装（can we ...），阅卷老师对倒装很敏感，用对了加分明显。",
    },
    {
        "item": "In the long run, what matters is not ... but ...",
        "cn": "从长远看，重要的不是……，而是……",
        "ex": "In the long run, what matters is not how fast we move, but in which direction.",
        "lib": "writing", "cat": "通用表达", "pos": "ending", "gen": "argumentative",
        "fn": "总结归纳", "lv": "A", "art": "2026-10-05", "cyc": "future7", "rc": 1,
        "tags": ["结尾句", "升华"],
        "usage": "结尾段升华。what 引导主语从句 + not ... but ... 收束，句子短、力量足，比 In conclusion 有内容。",
    },
    {
        "item": "I am writing to express my heartfelt gratitude for ...",
        "cn": "我写信是为了对……表达我由衷的感谢。",
        "ex": "I am writing to express my heartfelt gratitude for the reference you kindly provided.",
        "lib": "writing", "cat": "通用表达", "pos": "universal", "gen": "practical",
        "fn": "提出建议", "lv": "A", "art": "", "cyc": "out", "rc": 0,
        "tags": ["小作文", "感谢信"],
        "usage": "小作文感谢信开头，公式化但不能错。heartfelt 一定要带 d（形容词），别写成 heartfeel。",
    },
    {
        "item": "Should you need any further information, please do not hesitate to contact me.",
        "cn": "若您需要更多信息，请随时与我联系。",
        "ex": "Should you need any further information, please do not hesitate to contact me at the address below.",
        "lib": "writing", "cat": "通用表达", "pos": "ending", "gen": "practical",
        "fn": "提出建议", "lv": "B", "art": "2026-10-06", "cyc": "future15", "rc": 2,
        "tags": ["小作文", "结尾句"],
        "usage": "小作文（书信 / 邮件 / 通知）通用收尾。Should 提到句首 = If you should need，条件句的倒装省略 if，很显功底。",
    },
    {
        "item": "Culture is not a museum piece to be preserved, but a living conversation to be continued.",
        "cn": "文化不是供保存的博物馆展品，而是一场有待延续的活的对话。",
        "ex": "Culture is not a museum piece to be preserved, but a living conversation to be continued.",
        "lib": "writing", "cat": "文化交流", "pos": "universal", "gen": "argumentative",
        "fn": "情感升华", "lv": "A", "art": "2026-10-02", "cyc": "future1", "rc": 0,
        "tags": ["金句", "文化交流"],
        "usage": "文化交流类话题的升华句。对举结构（not a ..., but a ...）+ 两个 to be done 后置定语，可整句引用。",
    },
]


# ============================================================
# 清理
# ============================================================
def clear_materials(uid: int) -> int:
    with db.as_user(uid):
        return db.execute(
            "DELETE FROM favorites WHERE user_id=? AND tags LIKE ?",
            (uid, f"%{TAG}%"),
        )


def clear_articles(uid: int) -> int:
    with db.as_user(uid):
        rows = db.query(
            "SELECT id, date FROM articles WHERE user_id=? AND source LIKE ?",
            (uid, SRC_PREFIX + "%"),
        )
        n = 0
        for r in rows:
            aid, d = r["id"], r["date"]
            db.execute(
                "DELETE FROM submissions WHERE task_id IN "
                "(SELECT id FROM imitation_tasks WHERE article_id=?)", (aid,),
            )
            for tbl in ("vocab", "sentences", "grammar", "writing_items", "imitation_tasks"):
                db.execute(f"DELETE FROM {tbl} WHERE article_id=?", (aid,))
            db.execute("DELETE FROM study_log WHERE user_id=? AND date=?", (uid, d))
            db.execute("DELETE FROM articles WHERE id=? AND user_id=?", (aid, uid))
            n += 1
        return n


# ============================================================
# 写入
# ============================================================
def seed_articles(uid: int) -> list[tuple[str, str, int]]:
    """把示例文章写进去（source 前缀标好，便于清理）。返回 (日期, 标题, 词数)。"""
    out = []
    with db.as_user(uid):
        for art in ARTICLES:
            pkg = dict(art["pkg"])
            pkg["article"] = dict(pkg["article"])
            src = pkg["article"]["source"]
            if not src.startswith(SRC_PREFIX):      # 防重复加前缀（重跑时幂等）
                pkg["article"]["source"] = SRC_PREFIX + src
            pkg["date"] = art["date"]
            pkg["topic"] = art["topic"]
            pkg["difficulty"] = art["difficulty"]
            row = store_package(pkg, replace=True)
            out.append((art["date"], pkg["article"]["title"], row["word_count"]))
    return out


def _next_review(cyc: str, today: date) -> str:
    if cyc == "due":
        return today.isoformat()
    if cyc == "overdue":
        return (today - timedelta(days=3)).isoformat()
    if cyc == "out":
        return ""
    if cyc == "mastered":
        return (today + timedelta(days=60)).isoformat()
    if cyc.startswith("future"):
        return (today + timedelta(days=int(cyc[6:]))).isoformat()
    raise ValueError(f"未知的复习状态：{cyc}")


def seed_materials(uid: int) -> list[tuple[int, str, str, str]]:
    """写入 20 条素材。返回 (id, 库, 位置, 复习状态)。"""
    today = date.today()
    out = []
    with db.as_user(uid):
        amap = {r["date"]: r["id"] for r in db.query("SELECT id, date FROM articles")}
        now = _now()
        for i, m in enumerate(MATERIALS):
            src = amap.get(m.get("art") or "")
            if m.get("art") and not src:
                print(f"  ⚠️ 找不到文章 {m['art']}，这条素材不挂来源：《{m['item'][:24]}》")
            # 造一条「看起来像真的」的收藏时间：分散在过去 10 天，列表排序才自然
            created = f"{(today - timedelta(days=i % 10)).isoformat()} {9 + (i % 10):02d}:{10 + i % 40:02d}:00"
            tags = list(m.get("tags") or [])
            if TAG not in tags:
                tags.append(TAG)
            auto = 1 if i % 5 else 0          # 八成走 AI 归类，两成手动，两种标签都能看到
            conf = round(0.78 + (i % 5) * 0.04, 2) if auto else 0.0
            fid = db.execute(
                """INSERT INTO favorites(user_id,item_type,content,cn,example,tags,source_article_id,
                                         library,category,position,genre,function,level,usage,
                                         auto,confidence,created_at,updated_at,
                                         review_count,next_review)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uid, m.get("item_type", "phrase"), m["item"], m["cn"], m["ex"],
                    _dumps(tags), src,
                    m["lib"], m["cat"], m["pos"], m["gen"], m["fn"], m["lv"], m["usage"],
                    auto, conf, created, now,
                    m.get("rc", 0), _next_review(m["cyc"], today),
                ),
            )
            out.append((fid, m["lib"], m["pos"], m["cyc"]))
    return out


def _dumps(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _summary(uid: int) -> dict:
    with db.as_user(uid):
        fav = db.favorites_summary()
        rev = db.review_stats()
        arts = db.query(
            "SELECT date, title, source_type FROM articles WHERE user_id=? ORDER BY date", (uid,)
        )
        return {"fav": fav, "rev": rev, "arts": [dict(a) for a in arts]}


def backup() -> Path | None:
    """库先备份：这是正式库，写坏了要能退回去。

    ⚠️ 别用 shutil.copy2 —— 库跑在 WAL 模式下，刚提交的数据还在 `-wal` 里，
       单拷 app.db 会得到一份**不含最新写入的旧库**（而且看着"备份成功"）。
       用 SQLite 自带的在线备份接口，一次拿到一致的单文件快照。
    """
    src = ROOT / "data" / "app.db"
    if not src.exists():
        return None
    from datetime import datetime as _dt
    bd = ROOT / "data" / ("backup_pre_seeden_" + _dt.now().strftime("%Y%m%d_%H%M%S"))
    bd.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(src))
    try:
        dst = sqlite3.connect(str(bd / "app.db"))
        try:
            con.backup(dst)
        finally:
            dst.close()
    finally:
        con.close()
    return bd / "app.db"


def main() -> None:
    args = sys.argv[1:]
    uid = 1
    if "--user" in args:
        uid = int(args[args.index("--user") + 1])
    dry = "--dry" in args
    mats_only = "--materials-only" in args
    arts_only = "--articles-only" in args

    if "--clear" in args:
        nm = clear_materials(uid)
        na = 0 if mats_only else clear_articles(uid)
        print(f"已清掉账号 {uid} 里的示例：素材 {nm} 条，文章 {na} 篇")
        s = _summary(uid)
        print(f"剩余：素材 {s['fav']['total']} 条 ｜ 待复习 {s['rev']['due']} ｜ "
              f"文章 {len(s['arts'])} 篇")
        return

    if dry:
        print(f"[dry-run] 将写入账号 {uid}："
              + ("素材 0 条" if arts_only else f"素材 {len(MATERIALS)} 条")
              + (" + 文章 0 篇" if mats_only else f" + 文章 {len(ARTICLES)} 篇"))
        if not arts_only:
            for m in MATERIALS:
                print(f"  素材 {m['lib']:<7} {m['pos']:<10} {m['gen']:<13} {m['lv']} {m['cyc']:<9} {m['item'][:44]}")
        if not mats_only:
            for a in ARTICLES:
                print(f"  文章 {a['date']}  {a['topic']}  难度 {a['difficulty']}  "
                      f"{a['pkg']['article']['title']}")
        return

    b = backup()
    if b:
        print(f"备份 -> {b}")

    if not mats_only:
        arts = seed_articles(uid)
        print(f"写入文章 {len(arts)} 篇：")
        for d, t, wc in arts:
            print(f"  {d}  {wc:>4} 词  {t}")

    if not arts_only:
        mats = seed_materials(uid)
        print(f"写入素材 {len(mats)} 条")

    s = _summary(uid)
    fav, rev = s["fav"], s["rev"]
    print("\n素材库：总量 %d ｜ 作文素材库 %d ｜ 阅读积累库 %d"
          % (fav["total"], fav["by_library"].get("writing", 0), fav["by_library"].get("reading", 0)))
    print("　　　　位置分布 " + str({k: v for k, v in fav["by_position"].items() if v}))
    print("　　　　文体分布 " + str({k: v for k, v in fav["by_genre"].items() if v}))
    print("　　　　等级分布 " + str({k: v for k, v in fav["by_level"].items() if v})
          + f" ｜ AI 自动归类 {fav['auto_classified']} 条")
    print("复习：今天待复习 %d ｜ 在周期 %d ｜ 已巩固 %d ｜ 素材总量 %d"
          % (rev["due"], rev["in_cycle"], rev["mastered"], rev["total"]))
    print(f"文章共 {len(s['arts'])} 篇：")
    for a in s["arts"]:
        print(f"  {a['date']:<16} {a['source_type']:<5} {a['title'][:44]}")
    print(f"\n（清空示例数据：python tools/_seed_english.py --clear）")


if __name__ == "__main__":
    main()
