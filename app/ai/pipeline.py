"""内容流水线：生成文章 → 解析 → 写作任务 → 落库。以及批改与真实材料导入。

设计上把内容生成拆成 3 次模型调用（文章 / 解析 / 写作），
比一次性让模型吐一个大 JSON 更稳、更省 token、单步失败也更容易重试。
"""
from __future__ import annotations

import json
import random
from typing import Any

from .. import db
from ..config import (
    MATERIAL_CATEGORIES,
    MATERIAL_FUNCTIONS,
    MATERIAL_GENRE_HINT,
    MATERIAL_GENRES,
    MATERIAL_LIBRARIES,
    MATERIAL_LEVELS,
    MATERIAL_POSITION_USAGE,
    MATERIAL_POSITIONS,
    TOPIC_POOL,
    settings,
)
from . import prompts
from .provider import AIError, get_provider, parse_json


# ------------------------------------------------------------
# 个性化：难度与主题的选择（V1 用最朴素但真实有效的规则）
# ------------------------------------------------------------

def effective_stage() -> str:
    """当前账号的备考阶段。设置页改过就用改过的，否则用 .env 里的 STAGE。"""
    return db.get_user_pref("stage", settings.stage) or settings.stage


def effective_base_difficulty() -> int:
    """当前账号的难度基准。同样要被英一难度下限夹住，只能比下限高。"""
    raw = db.get_user_pref("base_difficulty", "")
    try:
        v = int(raw) if raw else settings.base_difficulty
    except (TypeError, ValueError):
        v = settings.base_difficulty
    return max(settings.exam_min_difficulty, min(5, v))


def decide_difficulty() -> int:
    """根据最近表现决定今日难度（只看当前账号自己的记录）。"""
    base = effective_base_difficulty()
    w, a = db.scope("a.user_id")
    rows = db.query(
        f"""SELECT a.date, sub.score FROM submissions sub
            JOIN imitation_tasks t ON t.id = sub.task_id
            JOIN articles a ON a.id = t.article_id
            WHERE {w} AND sub.score IS NOT NULL ORDER BY sub.id DESC LIMIT 5""",
        a,
    )
    if len(rows) >= 3:
        avg = sum(r["score"] for r in rows) / len(rows)
        if avg >= 8.0:
            base += 1
        elif avg <= 5.0:
            base -= 1
    # 生词密度：连续几天不认识的词过多，说明太难
    recent = db.query(
        f"""SELECT a.date,
                   SUM(CASE WHEN v.mastery IN (1,2) THEN 1 ELSE 0 END) AS unk,
                   COUNT(*) AS total
            FROM vocab v JOIN articles a ON a.id=v.article_id
            WHERE {w} AND a.date >= date('now','localtime','-3 day')
            GROUP BY a.date ORDER BY a.date DESC LIMIT 3""",
        a,
    )
    if len(recent) >= 2:
        ratios = [r["unk"] / r["total"] for r in recent if r["total"]]
        if ratios and sum(ratios) / len(ratios) > 0.6:
            base -= 1
    # 难度只能往上浮动，绝不能掉到英一以下（settings.exam_min_difficulty，默认 3）
    return max(settings.exam_min_difficulty, min(5, base))


def decide_topic(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    used = {r["topic"] for r in db.recent_articles(10) if r["topic"]}
    pool = [t for t in TOPIC_POOL if t not in used] or TOPIC_POOL
    return random.choice(pool)


def personalization_directives() -> str:
    """把长期数据转成给模型的「本篇特别要求」。"""
    bits: list[str] = []
    weak = db.weak_words(12)
    if weak:
        words = "、".join(w["word"] for w in weak)
        bits.append(f"请自然复用以下用户反复不认识的词或短语（3-5 个即可，不要堆砌）：{words}")
    grams = db.active_grammar_weak(4)
    if grams:
        bits.append("适当增加以下结构在文中的复现：" + "、".join(g["name"] for g in grams))
    return "；".join(bits)


# ------------------------------------------------------------
# 生成
# ------------------------------------------------------------

def _count_words(text: str) -> int:
    return len([w for w in text.replace("\n", " ").split(" ") if w.strip()])


def _ensure_exam_length(provider, art: dict, body: str, diff: int, topic: str) -> str:
    """保证文章达到英一水位线（词数下限）。

    旧实现写的是 ``len(body) < 300``——那是数**字符**不是数**词**，
    一篇 300 字符的中文式短文都能过闸，等于没有校验。
    现在改成：
      ① 按难度档位取区间下限，真数词；
      ② 不达标就先让模型**扩写一次**（扩论证层次，不是凑废话）；
      ③ 扩写后仍不达标才报错，绝不把低于英一的文章放进库。
    """
    lo, hi = prompts.word_band(diff)
    have = _count_words(body)
    if have >= lo:
        return body

    print(f"[pipeline] 文章只有 {have} 词（英一下限 {lo} 词），触发一次扩写")
    try:
        raw = provider.chat(
            prompts.ARTICLE_SYSTEM,
            prompts.build_expand_prompt(art.get("title", ""), body, have, diff),
            temperature=0.7,
            max_tokens=5000,
        )
        art2 = parse_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise AIError(
            f"生成的文章只有 {have} 词，未达到考研英语一的下限（{lo} 词），且扩写失败：{exc}"
        ) from exc

    body2 = (art2.get("body") or "").strip()
    if _count_words(body2) > have:
        # 只覆盖这次真正给出来的字段，避免模型漏字段把标题等抹掉
        for k, v in art2.items():
            if v:
                art[k] = v
        art["body"] = body2
        body = body2

    final = _count_words(body)
    if final < lo:
        raise AIError(
            f"生成的文章只有 {final} 词，未达到考研英语一的下限（{lo} 词），请重试"
        )
    return body


def _generate_json(provider, stage: str, system: str, user: str, *,
                   strong: bool = False, temperature: float, max_tokens: int) -> dict:
    """模型偶发输出无效 JSON 时，只重试当前生成阶段一次。"""
    for attempt in range(2):
        raw = provider.chat(
            system,
            user if attempt == 0 else user + "\n\n上一次输出格式有误。请重新完整输出一个合法 JSON 对象，注意引号、转义和闭合括号，不要附加说明。",
            strong=strong,
            temperature=temperature if attempt == 0 else min(temperature, 0.3),
            max_tokens=max_tokens if attempt == 0 else min(max_tokens * 2, 32000),
        )
        try:
            result = parse_json(raw)
            if not isinstance(result, dict):
                raise AIError("模型返回的不是 JSON 对象")
            return result
        except AIError as exc:
            print(f"[pipeline] {stage} JSON 解析失败（第 {attempt + 1} 次，长度 {len(raw)}）：{exc}")
    raise AIError(f"{stage}的模型输出格式连续两次不正确，请稍后重试")


def generate_daily(
    date_str: str | None = None,
    *,
    topic: str | None = None,
    difficulty: int | None = None,
    directives: str | None = None,
    source_type: str = "ai",
    force: bool = False,
) -> dict:
    """生成并落库一天的内容包，返回文章行。"""
    date_str = date_str or db.today_str()
    existing = db.get_article_by_date(date_str)
    if existing and not force:
        return existing

    provider = get_provider()
    diff = difficulty or decide_difficulty()
    tp = decide_topic(topic)
    dirs = directives if directives is not None else personalization_directives()
    recent_titles = [r["title"] for r in db.recent_articles(8)]

    # --- 1) 文章主体 ---
    art = _generate_json(
        provider, "文章生成",
        prompts.ARTICLE_SYSTEM,
        prompts.build_article_prompt(
            tp, diff, directives=dirs, recent_titles=recent_titles,
            stage=effective_stage(),
        ),
        temperature=0.85,
        max_tokens=4000,
    )
    body = (art.get("body") or "").strip()
    body = _ensure_exam_length(provider, art, body, diff, tp)

    # --- 2) 精读解析 ---
    ana = _generate_json(
        provider, "精读解析",
        prompts.ANALYSIS_SYSTEM,
        prompts.build_analysis_prompt(art.get("title", ""), body, db.weak_words(12)),
        strong=True,
        temperature=0.4,
        max_tokens=12000,
    )

    # --- 3) 写作任务 ---
    wr = _generate_json(
        provider, "写作任务",
        prompts.WRITING_SYSTEM,
        prompts.build_writing_prompt(art.get("title", ""), body),
        temperature=0.7,
        max_tokens=4000,
    )

    pkg = {
        "article": art,
        "analysis": ana,
        "writing": wr,
        "date": date_str,
        "source_type": source_type,
        "model": settings.ai_model,
        "difficulty": diff,
        "topic": tp,
    }
    return store_package(pkg, replace=force)


def analyze_real_article(
    text: str,
    *,
    title: str = "",
    source: str = "",
    date_str: str | None = None,
    difficulty: int | None = None,
) -> dict:
    """导入真实外刊材料 → AI 解析适配（规格书 3.1 A 路径）。"""
    date_str = date_str or db.today_str()
    provider = get_provider()
    diff = difficulty or decide_difficulty()
    t = title or "Untitled"
    pkg = {
        "article": {
            "title": t,
            "subtitle": "",
            "source": source or "Real material",
            "topic": "真实材料",
            "difficulty": diff,
            "body": text.strip(),
        },
        "analysis": parse_json(
            provider.chat(
                prompts.ANALYSIS_SYSTEM,
                prompts.build_analysis_prompt(t, text.strip(), db.weak_words(12)),
                strong=True,
                temperature=0.4,
                max_tokens=12000,
            )
        ),
        "writing": parse_json(
            provider.chat(
                prompts.WRITING_SYSTEM,
                prompts.build_writing_prompt(t, text.strip()),
                temperature=0.7,
                max_tokens=4000,
            )
        ),
        "date": date_str,
        "source_type": "real",
        "model": settings.ai_model,
        "difficulty": diff,
        "topic": "真实材料",
    }
    return store_package(pkg, replace=True)


def analyze_exam_paper(
    text: str,
    *,
    year: int,
    slot: str,
    title: str = "",
) -> dict:
    """导入一篇做完的真题 → 精读解析 + 写作积累（规格书 3.1 A 路径的真题版）。

    与外刊导入（analyze_real_article）的关键区别：
    - **不占用、不覆盖任何一天的每日内容**。date 用 exam-2010-text2 这种伪日期，
      每日文章按真实日期查找，永远不会撞上。
    - 解析走「真题精读模式」：词汇和长难句优先挑真题爱设题的部分。
    - 同一篇真题重复导入会整体覆盖（replace=True），方便纠错重导。
    """
    from ..config import EXAM_SLOTS

    label = EXAM_SLOTS.get(slot) or slot
    date_key = f"exam-{year}-{slot}"
    source = f"{year} 年考研英语一真题 · {label}"
    t = title or source
    provider = get_provider()
    diff = decide_difficulty()
    pkg = {
        "article": {
            "title": t,
            "subtitle": "",
            "source": source,
            "topic": "真题精读",
            "body": text.strip(),
        },
        "analysis": parse_json(
            provider.chat(
                prompts.ANALYSIS_SYSTEM,
                prompts.build_analysis_prompt(
                    t, text.strip(), db.weak_words(12), exam_mode=True
                ),
                strong=True,
                temperature=0.4,
                max_tokens=12000,
            )
        ),
        "writing": parse_json(
            provider.chat(
                prompts.WRITING_SYSTEM,
                prompts.build_writing_prompt(t, text.strip()),
                temperature=0.7,
                max_tokens=4000,
            )
        ),
        "date": date_key,
        "source_type": "exam",
        "model": settings.ai_model,
        "difficulty": diff,
        "topic": "真题精读",
    }
    row = store_package(pkg, replace=True)
    # 真题学习同样计入「今天」：连续天数不能因为读的是真题就断掉
    db.touch_study_log(db.today_str(), row["id"])
    db.log_event("exam_imported", row["id"], {"year": year, "slot": slot})
    return row


def store_package(pkg: dict, *, replace: bool = False) -> dict:
    """把内容包写入数据库。"""
    art = pkg["article"]
    ana = pkg.get("analysis") or {}
    wr = pkg.get("writing") or {}
    d = pkg["date"]

    body = (art.get("body") or "").strip()
    wc = _count_words(body)
    est = max(10, round(wc / 22))

    if replace:
        old = db.get_article_by_date(d)
        if old:
            for tbl in ("vocab", "sentences", "grammar", "writing_items", "imitation_tasks"):
                db.execute(f"DELETE FROM {tbl} WHERE article_id=?", (old["id"],))

    uid = db.require_user()
    db.execute(
        """INSERT INTO articles(user_id,date,title,subtitle,source,source_type,topic,difficulty,
                                word_count,est_minutes,body,translation,logic,exam_focus,model,status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ready')
           ON CONFLICT(user_id,date) DO UPDATE SET
             title=excluded.title, subtitle=excluded.subtitle, source=excluded.source,
             source_type=excluded.source_type, topic=excluded.topic,
             difficulty=excluded.difficulty, word_count=excluded.word_count,
             est_minutes=excluded.est_minutes, body=excluded.body,
             translation=excluded.translation, logic=excluded.logic,
             exam_focus=excluded.exam_focus, model=excluded.model""",
        (
            uid,
            d,
            art.get("title", "Untitled"),
            art.get("subtitle", ""),
            art.get("source", ""),
            pkg.get("source_type", "ai"),
            pkg.get("topic") or art.get("topic", ""),
            int(pkg.get("difficulty") or art.get("difficulty") or 3),
            wc,
            est,
            body,
            json.dumps(ana.get("translation") or [], ensure_ascii=False),
            json.dumps(ana.get("logic") or {}, ensure_ascii=False),
            json.dumps(ana.get("exam_focus") or {}, ensure_ascii=False),
            pkg.get("model", ""),
        ),
    )
    row = db.get_article_by_date(d)
    article_id = row["id"]

    # 词汇：同一单词跨文章累加 occurrence
    for v in ana.get("vocab") or []:
        word = (v.get("word") or "").strip()
        if not word:
            continue
        prev = db.query_one(
            "SELECT SUM(v.occurrence) c FROM vocab v JOIN articles a ON a.id=v.article_id "
            "WHERE v.word=? AND a.user_id=?",
            (word, uid),
        )
        occ = int(prev["c"] or 0) + 1 if prev and prev["c"] else 1
        try:
            db.execute(
                """INSERT INTO vocab(article_id,word,pos,meaning_in_context,common_meanings,
                                     exam_note,cn_definition,example,importance,occurrence)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(article_id,word) DO UPDATE SET
                     meaning_in_context=excluded.meaning_in_context,
                     exam_note=excluded.exam_note,
                     cn_definition=excluded.cn_definition""",
                (
                    article_id, word, v.get("pos", ""), v.get("meaning_in_context", ""),
                    v.get("common_meanings", ""), v.get("exam_note", ""),
                    v.get("cn_definition", ""),
                    v.get("example", ""), int(v.get("importance") or 3), occ,
                ),
            )
        except Exception:  # noqa: BLE001
            continue

    for i, s in enumerate(ana.get("sentences") or []):
        db.execute(
            """INSERT INTO sentences(article_id,en,zh,skeleton,main_clause,subject,predicate,object,
                                     clauses,modifiers,non_finite,insertion,appositive,logic,why_hard,order_index)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                article_id, s.get("en", ""), s.get("zh", ""), s.get("skeleton", ""),
                s.get("main_clause", ""), s.get("subject", ""), s.get("predicate", ""),
                s.get("object", ""),
                json.dumps(s.get("clauses") or [], ensure_ascii=False),
                json.dumps(s.get("modifiers") or [], ensure_ascii=False),
                s.get("non_finite", ""), s.get("insertion", ""), s.get("appositive", ""),
                s.get("logic", ""), s.get("why_hard", ""), i,
            ),
        )

    for i, g in enumerate(ana.get("grammar") or []):
        db.execute(
            """INSERT INTO grammar(article_id,name,category,explanation,pattern,examples,order_index)
               VALUES(?,?,?,?,?,?,?)""",
            (
                article_id, g.get("name", ""), g.get("category", ""), g.get("explanation", ""),
                g.get("pattern", ""), json.dumps(g.get("examples") or [], ensure_ascii=False), i,
            ),
        )

    for i, w in enumerate(wr.get("items") or []):
        pos = _clean_position(w.get("position_hint"))
        usage = str(w.get("position_usage") or "").strip() or MATERIAL_POSITION_USAGE.get(pos, "")
        kind = w.get("kind", "phrase")
        gen = _clean_genre(w.get("genre")) or _rule_genre(
            w.get("content", ""),
            _WI_KIND_TO_ITEM_TYPE.get(kind, "phrase"),
            pos,
            extra=str(w.get("cn", "")),
        )
        db.execute(
            """INSERT INTO writing_items(article_id,kind,content,cn,example,usage_note,
                                         position_hint,position_usage,genre,order_index)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                article_id, w.get("kind", "phrase"), w.get("content", ""), w.get("cn", ""),
                w.get("example", ""), w.get("usage_note", ""),
                pos, usage, gen, i,
            ),
        )

    im = wr.get("imitation") or {}
    if im.get("prompt_cn"):
        db.execute(
            """INSERT INTO imitation_tasks(article_id,prompt_cn,prompt_en,hint,reference_answer)
               VALUES(?,?,?,?,?)""",
            (
                article_id, im.get("prompt_cn", ""), im.get("prompt_en", ""),
                im.get("hint", ""), im.get("reference_answer", ""),
            ),
        )

    db.touch_study_log(d, article_id)
    db.log_event("article_generated", article_id, {"date": d, "topic": pkg.get("topic"), "difficulty": pkg.get("difficulty")})
    return row


# ------------------------------------------------------------
# 素材库自动分类（AI 优先，规则兜底）
# ------------------------------------------------------------

def _clean_position(v) -> str:
    v = (v or "").strip()
    return v if v in MATERIAL_POSITIONS else ""


def _clean_genre(v) -> str:
    v = (v or "").strip()
    return v if v in MATERIAL_GENRES else ""


def _default_library(item_type: str) -> str:
    """词汇 / 长难句默认进阅读积累库，其余默认进作文素材库。"""
    return "reading" if item_type in ("vocab", "sentence") else "writing"


# writing_items.kind → 分类时用的 item_type（word 视作词汇，直接判「通用文体」）
_WI_KIND_TO_ITEM_TYPE = {"word": "vocab", "phrase": "phrase", "pattern": "phrase"}


# 关键词 → 主题分类（AI 不可用时的兜底判断）
_RULE_CATEGORY: list[tuple[str, tuple[str, ...]]] = [
    ("科技发展", ("technolog", "internet", "digital", "online", "artificial intelligence",
                  "algorithm", "smartphone", "social media", "科技", "互联网", "数字",
                  "人工智能", "手机", "网络", "平台", "算法", "智能")),
    ("教育学习", ("educat", "study", "studen", "learn", "school", "university",
                  "curricul", "teach", "academ", "教育", "学习", "学校", "大学",
                  "学生", "教学", "知识", "读书")),
    ("环境保护", ("environment", "pollut", "climate", "carbon", "energy", "green",
                  "sustainab", "emission", "recycl", "环境", "污染", "气候", "碳",
                  "能源", "绿色", "可持续", "排放", "生态")),
    ("文化交流", ("cultur", "tradition", "heritage", "museum", "identity",
                  "文化", "传统", "遗产", "博物馆", "身份", "文明")),
    ("经济就业", ("econom", "employ", "career", "labour", "labor", "market", "wage",
                  "income", "workplace", "经济", "就业", "职业", "劳动", "市场",
                  "工资", "收入", "职场", "行业")),
    ("社会现象", ("society", "social", "public", "community", "government", "policy",
                  "urban", "citizen", "inequal", "社会", "公众", "社区", "政府",
                  "政策", "城市", "市民", "平等", "民生")),
    ("人生哲理", ("happiness", "meaning", "purpose", "wisdom", "memor", "solitude",
                  "attitude", "人生", "幸福", "意义", "智慧", "记忆", "孤独",
                  "态度", "成长")),
    ("品质精神", ("persist", "dilig", "courage", "patien", "curios", "resilien",
                  "integrity", "grit", "responsib", "坚持", "勤奋", "勇气", "耐心",
                  "好奇", "韧性", "诚信", "责任", "品质")),
]

# 关键词 → 作文位置（按命中次数打分，命中多者优先）
_RULE_POSITION: list[tuple[str, tuple[str, ...]]] = [
    ("opening", ("with the development", "with the rapid", "in recent years", "nowadays",
                 "it is widely", "has become", "have become", "there is a growing",
                 "as is vividly", "in contemporary", "随着", "近年来", "如今", "越来越",
                 "普遍", "逐渐", "成为", "正在")),
    ("ending", ("in conclusion", "to sum up", "to conclude", "in a word", "all in all",
                "it is high time", "only by", "we should", "it is advisable", "in the long run",
                "综上所述", "总之", "唯有", "我们应该", "呼吁", "归根结底")),
    ("transition", ("however", "moreover", "furthermore", "nevertheless", "on the contrary",
                    "in contrast", "what is more", "what's more", "nonetheless", "in addition",
                    "by contrast", "然而", "而且", "此外", "相反", "另一方面")),
    ("body", ("for example", "for instance", "such as", "as an example", "compared with",
              "in comparison", "because", "due to", "owing to", "as a result",
              "例如", "比如", "因为", "由于", "导致", "对比")),
]

# 功能线索
_RULE_FUNCTION: list[tuple[str, tuple[str, ...]]] = [
    ("让步转折", ("however", "although", "nevertheless", "然而", "虽然", "尽管")),
    ("对比论证", ("in contrast", "compared", "by contrast", "对比", "相反")),
    ("举例论证", ("for example", "for instance", "such as", "例如", "比如")),
    ("因果分析", ("because", "due to", "as a result", "owing to", "因为", "由于", "导致")),
    ("提出建议", ("should", "it is advisable", "建议", "应当", "呼吁")),
]

# 文体线索：按顺序判断，命中即停（应用文/说明文/描写文的标志词最独特，放在前面）
# 注意：不要放过于宽泛的裸词——"notice" 会误匹配动词 noticed，"memo" 会误匹配 memory。
_RULE_GENRE: list[tuple[str, tuple[str, ...]]] = [
    ("practical", (
        "i am writing to", "i'm writing to", "dear sir", "dear madam", "dear mr", "dear ms",
        "yours sincerely", "yours faithfully", "truly yours", "look forward to your reply",
        "look forward to hearing", "on behalf of", "i would like to apply", "thank you for your",
        "a notice", "this notice", "notice is hereby given", "notice board",
        "announcement", "memorandum", "memo:", "agenda", "minutes of",
        "敬启者", "兹", "敬请", "谨代表", "来信", "回信",
    )),
    ("descriptive", (
        "as is vividly depicted", "as is shown in the picture", "the picture shows",
        "in the picture", "the drawing depicts", "as can be seen from the picture",
        "画面", "图画中", "如图所示", "图中",
    )),
    ("expository", (
        "according to the chart", "according to the figure", "as is shown in the chart",
        "the chart", "the figure", "the graph", "account for", "the percentage of",
        "the proportion of", "increased from", "decreased from",
        "数据显示", "图表", "占比", "比例", "上升了", "下降了", "统计",
    )),
    ("narrative", (
        "i once", "when i was", "one day", "my experience", "i remember", "last year i",
        "last summer", "last winter", "last spring", "last week", "last month",
        "i used to", "my first", "back in", "i will never forget",
        "曾经", "有一次", "记得", "那段经历", "那一年",
    )),
    ("argumentative", (
        "in my opinion", "from my perspective", "it is undeniable", "there is no denying",
        "i firmly believe", "it is widely acknowledged", "therefore", "consequently",
        "however", "nevertheless", "in conclusion", "to sum up", "to conclude",
        "admittedly", "it follows that",
        "我认为", "在我看来", "由此可见", "显而易见", "无庸置疑", "综上所述",
    )),
]


def _rule_genre(blob: str, item_type: str, position: str, extra: str = "") -> str:
    """规则的文体判断。词汇/长难句默认通用——它们本来就不绑定特定文体。

    判定分两轮，避免「例句里的偶然用词」压过「素材本身的信号」：
      第一轮只看素材正文（content）；
      正文没命中时，第二轮才看中文释义 / 例句这些辅助文本。
    注意：内部统一转小写，避免调用方忘记 lower() 导致英文标志词全部失配。
    """
    if item_type in ("vocab", "sentence"):
        return "universal"
    for probe in ((blob or "").lower(), (extra or "").lower()):
        if not probe:
            continue
        for name, kws in _RULE_GENRE:
            if any(k in probe for k in kws):
                return name
    # 没有明确标志词时，按位置给一个合理猜测
    # 考研大作文的过渡段/主体段/结尾段实质都是议论文的论证部分
    if position in ("transition", "body", "ending"):
        return "argumentative"
    if position == "opening":
        return "expository"
    return "universal"


def _rule_classify(content: str, cn: str = "", example: str = "", item_type: str = "phrase") -> dict:
    """不依赖 AI 的兜底分类，保证没配模型时素材也能自动归位。"""
    blob = f"{content} {cn} {example}".lower()

    category = "通用表达"
    for name, kws in _RULE_CATEGORY:
        if any(k in blob for k in kws):
            category = name
            break

    scores: dict[str, int] = {}
    for pos, kws in _RULE_POSITION:
        scores[pos] = sum(1 for k in kws if k in blob)
    best = max(scores, key=lambda p: scores[p]) if scores else "universal"
    position = best if scores.get(best, 0) > 0 else "universal"

    function = ""
    if item_type == "vocab":
        function = "精准释义"
    elif item_type == "sentence":
        function = "句子结构"
    else:
        for name, kws in _RULE_FUNCTION:
            if any(k in blob for k in kws):
                function = name
                break
        if not function:
            function = {
                "opening": "引出话题", "transition": "让步转折",
                "ending": "总结归纳", "body": "提出观点",
            }.get(position, "提出观点")

    genre = _rule_genre(content, item_type, position, extra=f"{cn} {example}")
    level = "C" if item_type in ("vocab", "sentence") else "B"
    return {
        "library": _default_library(item_type),
        "category": category,
        "position": position,
        "genre": genre,
        "function": function,
        "level": level,
        "usage": MATERIAL_POSITION_USAGE.get(position, MATERIAL_POSITION_USAGE["universal"]),
        "tags": [category] if category != "通用表达" else [],
        "confidence": 0.3,
        "reason": "规则匹配（未启用 AI 或 AI 不可用）",
    }


def _normalize_classification(
    d: dict, content: str, cn: str = "", example: str = "", item_type: str = "phrase"
) -> dict:
    """把模型输出收敛到合法取值，缺失项用规则结果补齐。"""
    fallback = _rule_classify(content, cn, example, item_type)

    def pick(v, allowed, default):
        v = str(v or "").strip()
        return v if v in allowed else default

    position = pick(d.get("position"), set(MATERIAL_POSITIONS), "")
    usage = str(d.get("usage") or "").strip()

    # 位置兜底：优先用规则判断
    if not position:
        position = fallback["position"]
        if not usage:
            usage = fallback["usage"]
    if not usage:
        usage = MATERIAL_POSITION_USAGE[position]

    tags = [str(t).strip() for t in (d.get("tags") or []) if str(t).strip()][:4]
    try:
        conf = float(d.get("confidence"))
    except (TypeError, ValueError):
        conf = 0.0

    return {
        "library": pick(d.get("library"), set(MATERIAL_LIBRARIES), fallback["library"]),
        "category": pick(d.get("category"), set(MATERIAL_CATEGORIES), fallback["category"]),
        "position": position,
        "genre": pick(d.get("genre"), set(MATERIAL_GENRES), fallback["genre"]),
        "function": pick(d.get("function"), set(MATERIAL_FUNCTIONS), fallback["function"]),
        "level": pick(d.get("level"), set(MATERIAL_LEVELS), fallback["level"]),
        "usage": usage,
        "tags": tags or fallback["tags"],
        "confidence": max(0.0, min(1.0, conf)),
        "reason": str(d.get("reason") or "").strip(),
    }


def classify_material(
    content: str,
    *,
    cn: str = "",
    example: str = "",
    item_type: str = "phrase",
    source_title: str = "",
    source_topic: str = "",
    use_ai: bool = True,
) -> dict:
    """给一条素材分类：决定进哪个库、主题、文体，以及「用在作文什么位置」。

    AI 可用时走模型（更准，能给出具体使用建议）；否则退回规则匹配。
    """
    if use_ai and settings.ai_ready:
        try:
            raw = get_provider().chat(
                prompts.CLASSIFY_SYSTEM,
                prompts.build_classify_prompt(
                    content, cn, example, item_type, source_title, source_topic
                ),
                temperature=0.2,
                # 推理模型会先吃思考额度：800 太紧，会随机被判「输出达到长度限制」
                # （和 lookup_word 同一个坑，2026-10-14 实测确认）
                max_tokens=2500,
            )
            out = _normalize_classification(parse_json(raw), content, cn, example, item_type)
            out["ai"] = True
            return out
        except Exception:  # noqa: BLE001  —— 分类失败不能阻塞收藏，直接走规则
            pass
    out = _rule_classify(content, cn, example, item_type)
    out["ai"] = False
    return out


def save_material(
    content: str,
    *,
    cn: str = "",
    example: str = "",
    item_type: str = "phrase",
    tags: list[str] | None = None,
    source_article_id: int | None = None,
    source_title: str = "",
    source_topic: str = "",
    classification: dict | None = None,
    use_ai: bool = True,
) -> dict:
    """分类 + 写入素材库，返回 {id, library, position, usage, ...}。

    传入 classification 时直接采用（不再调模型），用于批改场景复用已有结果。
    """
    if classification:
        cls = _normalize_classification(classification, content, cn, example, item_type)
        cls["ai"] = True
    else:
        cls = classify_material(
            content, cn=cn, example=example, item_type=item_type,
            source_title=source_title, source_topic=source_topic, use_ai=use_ai,
        )

    final_tags = cls.get("tags") or tags or []
    fid = db.execute(
        """INSERT INTO favorites(user_id,item_type,content,cn,example,tags,source_article_id,
                                 library,category,position,genre,function,level,usage,
                                 auto,confidence,updated_at,review_count,next_review)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),0,
                  date('now','localtime','+1 day'))""",
        (
            db.require_user(),
            item_type, content, cn, example,
            json.dumps(final_tags, ensure_ascii=False),
            source_article_id,
            cls["library"], cls["category"], cls["position"], cls["genre"], cls["function"],
            cls["level"], cls["usage"], 1 if cls.get("ai") else 0, float(cls.get("confidence") or 0),
        ),
    )
    db.log_event(
        "material_saved", fid,
        {
            "library": cls["library"],
            "position": cls["position"],
            "genre": cls["genre"],
            "auto": bool(cls.get("ai")),
        },
    )
    return {"id": fid, **cls}


def label_materials(article_id: int) -> list[dict]:
    """为某篇文章的写作表达批量标注「位置 + 文体」，一次模型调用搞定。"""
    art = db.get_article(article_id)
    if not art:
        raise AIError("文章不存在")

    def _with_hints(rows: list[dict]) -> list[dict]:
        """给每条补上「文体一句话说明」，方便前端直接展示。"""
        for it in rows:
            it["genre_hint"] = MATERIAL_GENRE_HINT.get(it.get("genre") or "", "")
        return rows

    items = db.query(
        "SELECT * FROM writing_items WHERE article_id=? ORDER BY order_index", (article_id,)
    )
    if not items:
        return []
    if all(i.get("position_hint") and i.get("position_usage") and i.get("genre") for i in items):
        return _with_hints(items)

    raw = get_provider().chat(
        prompts.LABEL_MATERIALS_SYSTEM,
        prompts.build_label_materials_prompt(art.get("title", ""), items),
        temperature=0.2,
        # 一次可能标注多条素材，输出本就偏长；再叠上思考额度，1500 会不够
        max_tokens=4000,
    )
    data = parse_json(raw)
    labels = data.get("labels") or []
    for lab in labels:
        try:
            idx = int(lab.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(items)):
            continue
        pos = _clean_position(lab.get("position"))
        if not pos:
            continue
        usage = str(lab.get("usage") or "").strip() or MATERIAL_POSITION_USAGE[pos]
        gen = _clean_genre(lab.get("genre"))
        db.execute(
            "UPDATE writing_items SET position_hint=?, position_usage=?, genre=? WHERE id=?",
            (pos, usage, gen or None, items[idx]["id"]),
        )
        items[idx]["position_hint"] = pos
        items[idx]["position_usage"] = usage
        if gen:
            items[idx]["genre"] = gen

    # 模型漏标的用规则补上，保证前端每条都有位置和文体
    for it in items:
        itype = _WI_KIND_TO_ITEM_TYPE.get(it.get("kind") or "phrase", "phrase")
        if not it.get("position_hint") or not it.get("position_usage"):
            pos = it.get("position_hint") or _rule_classify(
                it.get("content", ""), it.get("cn") or "", it.get("example") or "", itype,
            )["position"]
            usage = it.get("position_usage") or MATERIAL_POSITION_USAGE[pos]
            db.execute(
                "UPDATE writing_items SET position_hint=?, position_usage=? WHERE id=?",
                (pos, usage, it["id"]),
            )
            it["position_hint"] = pos
            it["position_usage"] = usage
        if not it.get("genre"):
            gen = _rule_genre(
                it.get("content", ""), itype, it.get("position_hint") or "universal",
                extra=f"{it.get('cn') or ''} {it.get('example') or ''}",
            )
            db.execute("UPDATE writing_items SET genre=? WHERE id=?", (gen, it["id"]))
            it["genre"] = gen
    db.log_event("writing_materials_labeled", article_id, {"count": len(items)})
    return _with_hints(items)


# ------------------------------------------------------------
# 历史数据补全
# ------------------------------------------------------------
def rule_fill_blank_genres() -> int:
    """把 genre 为空的历史素材用规则补上（纯确定性，不调模型、不产生费用）。

    场景：给已有库新增 genre 列时，老数据会是 NULL。这里在启动时补齐，
    保证素材库一打开就有文体标签，而不是等用户逐条点「AI 重新归类」。
    已经标好 genre 的记录不会被覆盖。
    """
    filled = 0
    # 启动回填需要逐个打开独立数据库，不能把所有账号当成同一个库。
    with db.as_system():
        user_ids = [int(row["id"]) for row in db.query("SELECT id FROM users")]
    with db.as_local():
        filled = _fill_blank_genres_inner()
    for uid in user_ids:
        with db.as_user(uid):
            filled += _fill_blank_genres_inner()
    return filled


def _fill_blank_genres_inner() -> int:
    filled = 0
    for row in db.query(
        "SELECT id,content,cn,example,item_type,position FROM favorites "
        "WHERE genre IS NULL OR genre=''"
    ):
        blob = f"{row.get('content') or ''} {row.get('cn') or ''} {row.get('example') or ''}"
        g = _rule_genre(blob, row.get("item_type") or "phrase", row.get("position") or "universal")
        db.execute("UPDATE favorites SET genre=? WHERE id=?", (g, row["id"]))
        filled += 1

    for row in db.query(
        "SELECT id,content,cn,example,kind,position_hint FROM writing_items "
        "WHERE genre IS NULL OR genre=''"
    ):
        blob = f"{row.get('content') or ''} {row.get('cn') or ''} {row.get('example') or ''}"
        item_type = _WI_KIND_TO_ITEM_TYPE.get(row.get("kind") or "phrase", "phrase")
        g = _rule_genre(blob, item_type, row.get("position_hint") or "universal")
        db.execute("UPDATE writing_items SET genre=? WHERE id=?", (g, row["id"]))
        filled += 1

    if filled:
        db.log_event("genre_backfilled", None, {"count": filled})
    return filled


# ------------------------------------------------------------
# 批改
# ------------------------------------------------------------

def story_bundle(article_id: int) -> dict:
    """组装前端需要的完整内容包。"""
    art = db.get_article(article_id)
    if not art:
        return {}
    art = dict(art)
    art["translation"] = json.loads(art.get("translation") or "[]")
    art["logic"] = json.loads(art.get("logic") or "{}")
    # 命题视角：老文章没有这个字段，解析成空对象，前端据此优雅隐藏
    art["exam_focus"] = json.loads(art.get("exam_focus") or "{}")

    def _j(rows, keys):
        out = []
        for r in rows:
            r = dict(r)
            for k in keys:
                try:
                    r[k] = json.loads(r.get(k) or "[]")
                except Exception:  # noqa: BLE001
                    r[k] = []
            out.append(r)
        return out

    writing_items = db.query(
        "SELECT * FROM writing_items WHERE article_id=? ORDER BY order_index", (article_id,)
    )
    # 注意：这里不补通用文案，让前端能区分「AI 逐条标注过」和「只有位置」
    for w in writing_items:
        w["position_usage"] = (w.get("position_usage") or "").strip()
        w["genre_hint"] = MATERIAL_GENRE_HINT.get(w.get("genre") or "", "")

    return {
        "article": art,
        "vocab": db.query("SELECT * FROM vocab WHERE article_id=? ORDER BY importance DESC, id", (article_id,)),
        "sentences": _j(db.query("SELECT * FROM sentences WHERE article_id=? ORDER BY order_index", (article_id,)), ["clauses", "modifiers"]),
        "grammar": _j(db.query("SELECT * FROM grammar WHERE article_id=? ORDER BY order_index", (article_id,)), ["examples"]),
        "writing_items": writing_items,
        "imitation": db.query_one("SELECT * FROM imitation_tasks WHERE article_id=? LIMIT 1", (article_id,)),
        "submissions": db.query(
            """SELECT sub.* FROM submissions sub JOIN imitation_tasks t ON t.id=sub.task_id
               WHERE t.article_id=? ORDER BY sub.round""",
            (article_id,),
        ),
        "study_log": db.get_study_log(art["date"]),
    }


def grade_submission(task_id: int, answer: str) -> dict:
    """批改一次仿写，落库并返回反馈。"""
    task = db.query_one("SELECT * FROM imitation_tasks WHERE id=?", (task_id,))
    if not task:
        raise AIError("仿写任务不存在")
    art = db.get_article(task["article_id"])
    exprs = [
        r["content"]
        for r in db.query(
            "SELECT content FROM writing_items WHERE article_id=? ORDER BY order_index",
            (task["article_id"],),
        )
    ]
    history = db.query(
        "SELECT round,answer,score FROM submissions WHERE task_id=? ORDER BY round", (task_id,)
    )
    raw = get_provider().chat(
        prompts.GRADE_SYSTEM,
        prompts.build_grade_prompt(
            task["prompt_cn"], task["reference_answer"], answer, exprs, history
        ),
        strong=True,
        temperature=0.3,
        max_tokens=4000,
    )
    fb = parse_json(raw)
    rnd = (db.query_one("SELECT MAX(round) r FROM submissions WHERE task_id=?", (task_id,))["r"] or 0) + 1
    db.execute(
        "INSERT INTO submissions(task_id,round,answer,feedback,score) VALUES(?,?,?,?,?)",
        (task_id, rnd, answer, json.dumps(fb, ensure_ascii=False), fb.get("score")),
    )
    d = art["date"]
    db.touch_study_log(d, art["id"])
    # 真题里做的仿写同样计入今天（保住连续天数）
    if art.get("source_type") == "exam":
        db.touch_study_log(db.today_str(), art["id"])
    logs = db.get_study_log(d) or {}
    db.touch_study_log(
        d,
        imitation_rounds=(logs["imitation_rounds"] or 0) + 1,
        imitation_score=fb.get("score"),
    )
    db.log_event("imitation_submitted", task_id, {"round": rnd, "score": fb.get("score")})

    if fb.get("worth_saving"):
        saved = []
        for item in fb.get("recommended_expressions") or []:
            if not item.get("en"):
                continue
            rec = save_material(
                item["en"],
                cn=item.get("cn", ""),
                example=fb.get("polished", ""),
                item_type="expression",
                tags=["AI推荐", "仿写批改"],
                source_article_id=art["id"],
                source_title=art.get("title", ""),
                source_topic=art.get("topic", ""),
                classification={
                    "library": "writing",
                    "position": item.get("position"),
                    "genre": item.get("genre"),
                    "usage": item.get("usage"),
                    "category": "通用表达",
                    "level": "A",
                    "confidence": 0.7,
                    "function": "",
                    "tags": ["AI推荐", "仿写批改"],
                },
            )
            saved.append(
                {
                    "en": item["en"], "cn": item.get("cn", ""),
                    "library": rec["library"], "position": rec["position"],
                    "genre": rec["genre"], "usage": rec["usage"],
                }
            )
        fb["saved_materials"] = saved
    # 积累闭环 ①（10-07）：比对积累库，算出「这次用上了哪些 / 哪些一直没用过」，并落 used_count。
    # 纯本地字符串比对，不额外调模型；万一出错也不能把整个批改拖垮，所以兜一层。
    try:
        fb["material_usage"] = db.annotate_material_usage(answer)
    except Exception as exc:  # noqa: BLE001
        fb["material_usage"] = {"used": [], "unused": [], "stats": {}, "error": str(exc)}
    return {"round": rnd, "feedback": fb}


# ============================================================
# 双击查词（2026-10-11 用户要求：阅读文章里双击单词就给翻译）
# ------------------------------------------------------------
# 定位：
#   它和「本篇词汇表」不是一回事 —— 词汇表是 AI 提前筛过的重点词，
#   而双击是「我读到这儿不认识，立刻想知道」。所以：
#     · 只做一次很短的调用，只要中文意思 + 在本句里的意思，不做长篇讲解；
#     · 结果在进程内缓存，同一个词反复点不重复烧额度；
#     · 不自动写进词汇表（用户划线时才会进「阅读笔记」，那是另一条路）。
# ============================================================
_LOOKUP_CACHE: dict[str, dict] = {}
_LOOKUP_CACHE_MAX = 300

LOOKUP_SYSTEM = """你是考研英语一的阅读老师。学生读文章时点了一个词/短语，你要立刻给他能用的信息。

只输出一个 JSON 对象，不要 markdown 围栏，不要任何解释性文字：
{
  "word": "原词或原短语",
  "phonetic": "音标（拿不准就给空字符串）",
  "pos": "词性缩写，如 n. / v. / adj. / phr.",
  "cn": "最贴切的中文意思，一行，多个义项用「；」隔开",
  "in_context": "**在这一句里**具体是什么意思（中文，一句话）",
  "example": "一个简短英文例句（优先用学生给的那句原文）"
}

要求：
1. 内容要短。这是在手机上弹一个小卡片，不是写词条。
2. 如果是短语或固定搭配，按搭配给意思，不要拆成单词。
3. 拿不准的意思就不要编，宁可少给一个义项。"""


def lookup_word(word: str, context: str = "") -> dict:
    """双击查词：返回 {word, phonetic, pos, cn, in_context, example}。

    AI 不可用 / 失败会抛 AIError，由上层转成人话提示。
    """
    w = (word or "").strip().strip(".,;:!?()[]\"'“”‘’")
    if not w:
        raise AIError("没有取到单词")
    if len(w) > 60:
        raise AIError("选中的内容太长了，双击单词或用划线记笔记更合适")
    key = w.lower()
    cached = _LOOKUP_CACHE.get(key)
    if cached:
        return {**cached, "cached": True}

    user = f"要查的词/短语：{w}"
    if context:
        user += f"\n它出现的句子：{context[:400]}"
    raw = get_provider().chat(
        LOOKUP_SYSTEM, user,
        temperature=0.1,      # 查词不需要发挥
        # ⚠️ 额度给足：模型是推理模型，会先吃掉一大块思考额度。之前给 1200 时，
        # 实测连「判断 impulses 在句中的意思」这种小任务都会被判「输出达到长度限制」
        # 直接失败 —— 双击查词就变成弹一个错误。额度只是上限，调高不会多花钱。
        max_tokens=3000,
        retries=1,
    )
    try:
        d = parse_json(raw)
    except AIError as exc:
        raise AIError(f"查词结果不是合法 JSON：{exc}") from exc
    if not isinstance(d, dict):
        raise AIError("查词返回的不是 JSON 对象")

    out = {
        "word": str(d.get("word") or w).strip(),
        "phonetic": str(d.get("phonetic") or "").strip(),
        "pos": str(d.get("pos") or "").strip(),
        "cn": str(d.get("cn") or "").strip(),
        "in_context": str(d.get("in_context") or "").strip(),
        "example": str(d.get("example") or "").strip(),
    }
    if not out["cn"] and not out["in_context"]:
        raise AIError("这次没查到意思，再点一次试试")
    if len(_LOOKUP_CACHE) >= _LOOKUP_CACHE_MAX:
        _LOOKUP_CACHE.clear()
    _LOOKUP_CACHE[key] = out
    db.log_event("word_lookup", None, {"word": w})
    return {**out, "cached": False}
