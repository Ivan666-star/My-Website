"""所有 Prompt 模板集中管理，方便单独迭代调优（内容质量主要由这里决定）。"""
from __future__ import annotations

import json

from ..config import DIFFICULTY_LABEL, settings
from . import exam_knowledge as ek

# ============================================================
# 1. 文章生成
# ============================================================
ARTICLE_SYSTEM = """你是一位考研英语一命题与教研专家。你既熟悉近十几年英语一真题的选材与行文风格，也清楚出题人在这类文章里爱在哪里设题。
你的任务是写出「读起来像真题原文、而且天然长着设题点」的英语文章。

硬性要求：
1. 语言必须是地道、自然的现代英语，禁止中式英语，禁止出现"as an AI"之类的话。
2. **难度必须达到考研英语一，可以更难，绝不允许更简单。** 难度不是靠堆生僻词，而是靠这些：
   · 名词化程度高（the emergence of / the assumption that 这类抽象名词短语做主语或宾语）
   · 抽象概念可以充当句子主语（The belief that… / The idea that…）
   · 多重后置修饰（一个名词后面挂定语从句、分词短语、同位语从句好几层）
   · 让步 + 转折结构高频，作者的真正观点藏在主句里
   · 插入语、破折号、冒号做补充说明
   · 动词常被"藏"进名词里
3. **必须有真实的争论张力**，不能平铺直叙地介绍一个话题。
   要写成「有一种流行看法 → 但证据指向另一面 → 更准确的说法是有条件的」这种有层次的推进。
4. 必须刻意包含考研高频句式：定语从句、状语从句、名词性从句、非谓语动词、插入语、被动语态、比较结构。
5. 段落 4-6 段，每段 3-5 句。**首段必须信息量前置**——真题首段通常直接抛出具体现象或争议，
   不铺垫背景、不讲客套话。
6. 词数**必须落在给定区间内，宁多勿少**。写不够就展开论证层次，不要靠重复和废话凑。
7. 只输出 JSON，不要输出任何解释性文字。"""

ARTICLE_USER_TMPL = """请生成一篇用于考研英语一长期训练的英语文章。

【今日主题】{topic}
【难度档位】{difficulty}（{difficulty_label}）
【目标词数】{min_words}-{max_words} 词 —— **绝对不许低于 {min_words} 词**（这是英一的水位线）
【当前备考阶段】{stage}
{directives}

{knowledge}

同时请为这篇文章起一个符合外刊风格、有点「标题党但不低俗」的英文标题。

输出严格为如下 JSON：
{{
  "title": "英文标题",
  "subtitle": "英文副标题，可留空字符串",
  "source": "如实标注你模仿了哪家刊物的哪个版面，例如 Imitating the science section of The Economist。**禁止写成 Adapted from… 这种假装有真实出处的说法**——那是在骗人。",
  "topic": "中文主题词",
  "difficulty": {difficulty},
  "body": "英文正文。段落之间必须用 \\n\\n 分隔。不要在段落里加序号。"
}}
"""

EXPAND_USER_TMPL = """下面这篇文章**词数不达标**，没有到考研英语一的阅读水位线。

【现有标题】{title}
【现有正文】
{body}
【现有词数】{have} 词
【必须达到】{min_words}-{max_words} 词 —— 少于 {min_words} 词就算失败

请在**保留原有主题、观点走向和难度**的前提下，把文章扩写到 {min_words}-{max_words} 词。要求：
1. 扩的是**论证层次**，不是形容词和废话：补上原因分析、反面可能性、限定条件、具体例证、影响推演。
2. 可以新增 1-2 段，也可以把现有段落展开；段落总数控制在 4-6 段。
3. 保持英一难度：名词化抽象主语、多重后置修饰、让步+转折、插入语照旧，不许为了凑字数把句子写简单。
4. 首段仍然信息量前置；结尾段收束到作者立场。
5. **不要复述前面已经说过的话**，不要写 "as mentioned above" 这类填充。

输出严格为如下 JSON（结构与原文章一致，正文段落之间用 \n\n 分隔）：
{{
  "title": "英文标题（可微调）",
  "subtitle": "英文副标题，可留空字符串",
  "source": "沿用原来的来源标注写法（Imitating …）",
  "topic": "中文主题词",
  "difficulty": {difficulty},
  "body": "扩写后的完整英文正文"
}}
"""


# ============================================================
# 2. 文章解析（翻译 + 词汇 + 长难句 + 语法 + 阅读逻辑）
# ============================================================
ANALYSIS_SYSTEM = """你是一位考研英语一精读老师，同时非常清楚出题人在这类文章里是怎么设题的。
你的分析必须准确、具体、可操作。

最重要的原则：**禁止空谈。**
不许出现「注意转折词」「结合语境理解」「区分事实与观点」这类正确的废话。
你写的每一条都必须落在**具体的句子上**，并且说清楚：
「出题人会怎么拿这句话设题、正确答案往哪写、干扰项会怎么骗人」。

只输出 JSON，不要输出任何解释性文字。"""

ANALYSIS_USER_TMPL = """下面是一篇考研英语训练文章，请完成精读解析。

【文章标题】{title}
【正文】
{body}
{weak_hint}
{knowledge}
请输出严格为如下 JSON：
{{
  "translation": [
    {{"en": "第1段英文原文", "zh": "对应中文翻译", "note": "本段翻译的**具体**难点，要点出是哪个结构/哪个词造成的，没有就留空字符串"}}
  ],
  "vocab": [
    {{
      "word": "单词或短语",
      "pos": "词性，如 n. / v. / adj. / phr.",
      "meaning_in_context": "在该句中的确切含义（中文）",
      "common_meanings": "该词在考研中常见的其他含义（中文，分号分隔）",
      "exam_note": "这个词在真题里怎么考（中文，一句话）：常考哪个义项、最容易和哪个词混、会以什么方式变成干扰项。例如「真题常考 intend 的『打算』义而把『意味着』的 mean 拿来设词义题」这种。禁止写「注意区分」这类空话",
      "cn_definition": "简明中文释义",
      "example": "文中的原句（英文）",
      "importance": 1-5 的整数，表示考研重要程度
    }}
  ],
  "sentences": [
    {{
      "en": "原句（必须是文章里真实存在的句子）",
      "zh": "中文翻译",
      "skeleton": "去掉所有修饰后的句子主干（英文）",
      "main_clause": "主句（英文）",
      "subject": "主语及其修饰",
      "predicate": "谓语",
      "object": "宾语/表语",
      "clauses": ["从句1：类型 + 内容 + 作用", "从句2"],
      "modifiers": ["修饰成分：定语/状语/插入语等 + 修饰对象"],
      "non_finite": "非谓语结构说明，没有则空字符串",
      "insertion": "插入结构说明，没有则空字符串",
      "appositive": "同位语说明，没有则空字符串",
      "logic": "该句内部的逻辑关系（转折/因果/让步/递进等）",
      "why_hard": "两句合一写（中文）：①这句的句法难在哪（要具体到成分，不能只写「结构复杂」）②**这句最可能被出题人设成什么题型、为什么会设在这里**（要在六大题型里指名）"
    }}
  ],
  "grammar": [
    {{
      "name": "语法点名，如 定语从句",
      "category": "句法 / 词法",
      "explanation": "结合文中例句讲清楚：这个结构在**这篇文章里**具体干了什么、怎么识别它的边界（中文）。不要复述语法书定义",
      "pattern": "结构公式，如 n. + that/which + 不完整句",
      "examples": ["文中例句（英文）"]
    }}
  ],
  "logic": {{
    "overall": "全文中心论点，一句话（中文）",
    "structure": [
      {{"para": 1, "role": "本段功能，如 引出话题/提出质疑/举例论证/收束结论", "main_idea": "本段主旨（中文）"}}
    ],
    "author_attitude": "作者态度及依据（中文）。**依据必须引用原文的具体词**（评价性形容词/副词/情态动词），并说明它出现在转折前还是转折后",
    "reading_tips": ["读这篇文章时该盯住哪几处（要指明段落和句子），1-3 条（中文）"]
  }},
  "exam_focus": {{
    "pattern": "本篇属于哪一种篇章模式：现象分析型 / 观点对立型 / 问题对策型 / 研究修正型",
    "main_thread": "出题人眼里的文章主线：哪一段承载什么功能，主旨题会围绕什么出（中文）",
    "set_points": [
      {{
        "qtype": "六大题型之一：主旨大意 / 细节事实 / 推理判断 / 词义句意 / 态度观点 / 例证指代逻辑",
        "anchor_en": "被设题的原文原句（必须一字不差地引用，不许写「某句」）",
        "para": 1,
        "why_here": "为什么出题人爱在这里设题（要对应「设题位置偏好」，如「转折之后」「让步从句是干扰项重灾区」）",
        "stem": "真题里这类题的完整问法（英文，写成真正的题干）",
        "right_direction": "正确项该往哪个方向写、措辞留不留余地（中文）",
        "traps": [
          {{
            "trap": "六种干扰项套路里的哪一种（张冠李戴 / 偷换范围 / 过度推断 / 无中生有 / 反向干扰 / 断章取义）",
            "sample_option": "一个具体的、像真题那样的干扰项（英文，写得像真选项一样）",
            "why_wrong": "它为什么错（中文，一句话，要指出被骗在哪里）"
          }}
        ]
      }}
    ],
    "trap_summary": "本篇最需要提防的 1-2 种干扰项套路，说明为什么会在这里出现（中文）"
  }}
}}

数量要求：
- translation：与正文段落数完全一致，逐段对应
- vocab：10-14 个，优先选「考研高频 + 在该语境下容易误解」的词和固定搭配
- sentences：4 个真正值得分析的长难句（不要凑数）
- grammar：3-4 个，且必须能在文章中找到真实例句
- exam_focus.set_points：**4-5 条，且至少覆盖 4 种不同题型**；
  每条的 traps 至少 2 个，sample_option 要写成真题选项那个样子（长度、语气都要像）
- exam_focus 是这次分析里**最重要**的部分。如果它写得空泛，整篇解析就白做了。
"""

# ============================================================
# 3. 写作表达 + 仿写任务
# ============================================================
WRITING_SYSTEM = """你是一位考研英语一写作老师，负责把阅读材料转化为高质量的写作积累。
你挑选的表达必须是考研写作真正能用上的，拒绝生僻无用的词。
只输出 JSON，不要输出任何解释性文字。"""

WRITING_USER_TMPL = """基于今天的文章，设计今日写作积累任务。

【文章标题】{title}
【正文】
{body}

输出严格为如下 JSON：
{{
  "items": [
    {{
      "kind": "word 或 phrase 或 pattern",
      "content": "英文表达",
      "cn": "中文含义",
      "example": "从文章中提取或基于文章改写的例句",
      "usage_note": "使用场景提示：考研写作中什么时候用、注意什么（中文）",
      "position_hint": "这条表达最适合放在作文的哪里，只能填 opening / transition / body / ending / universal 之一",
      "position_usage": "一句话使用建议（中文，40 字以内）。必须明确说出用在开头段/过渡段/主体段/结尾段，以及具体怎么用",
      "genre": "这条表达属于什么文体，只能填 argumentative 议论文 / expository 说明文 / practical 应用文 / descriptive 描写文 / narrative 记叙文 / universal 通用文体 之一"
    }}
  ],
  "imitation": {{
    "prompt_cn": "给用户的中文句子，要求用户自己写出英文",
    "prompt_en": "该中文句子的参考英文表达（用于批改对照，用户看不到也可以）",
    "hint": "给用户的提示，引导用上今天的表达，但不要直接给答案",
    "reference_answer": "一版质量较高的参考译文"
  }}
}}

数量要求：items 共 5 个，其中 3 个是 word/phrase，2 个是 pattern（句型模板）。
position_hint 判断标准：能引出话题/描述现象填 opening；承上启下填 transition；
用于论证举例填 body；用于总结建议填 ending；多处可用填 universal。
position_usage 必须写清「用在作文的哪一段 + 怎么用」，不要写「视情况而定」。
genre 判断标准：观点论证类填 argumentative；客观陈述数据趋势填 expository；
书信通知类措辞填 practical；图画场景描写填 descriptive；讲经历填 narrative；看不出归属填 universal。
imitation 的中文句子必须能自然用上本次的至少 1 个表达，难度贴近考研英语一大作文句式。
"""

# ============================================================
# 4. 仿写批改
# ============================================================
GRADE_SYSTEM = """你是一位严格的考研英语一作文批改老师。
原则：你是教练，不是代练。不要替学生写，要指出问题、给出改法、解释原因。
只输出 JSON，不要输出任何解释性文字。"""

GRADE_USER_TMPL = """请批改学生的这次仿写。

【中文任务】{prompt_cn}
【要求使用的表达】{expressions}
【参考译文】{reference}
【学生作答】{answer}
{history_hint}

输出严格为如下 JSON：
{{
  "score": 0-10 的一位小数,
  "verdict": "一句话总体评价（中文）",
  "issues": [
    {{
      "type": "语法 / 拼写 / 词汇 / 搭配 / 自然度 / 句式重复 / 逻辑 / 结构 / 考研适用性",
      "original": "学生原文中的问题片段",
      "problem": "问题说明（中文）",
      "suggestion": "修改后的表达（英文）",
      "why": "为什么这样改（中文）"
    }}
  ],
  "polished": "在学生作答基础上润色后的完整英文句子（保留学生的思路，不要另起炉灶）",
  "recommended_expressions": [
    {{
      "en": "推荐的替代/升级表达",
      "cn": "中文含义",
      "position": "该表达最适合用在作文的哪里，只能填 opening / transition / body / ending / universal 之一",
      "genre": "该表达属于什么文体，只能填 argumentative / expository / practical / descriptive / narrative / universal 之一",
      "usage": "一句话使用建议（中文，40 字以内），要明确说出用在开头段/过渡段/主体段/结尾段"
    }}
  ],
  "dimensions": {{
    "语法": 0-10, "词汇": 0-10, "搭配": 0-10, "自然度": 0-10, "结构": 0-10, "考研适用性": 0-10
  }},
  "worth_saving": true 或 false（是否值得加入作文素材库）,
  "next_step": "给用户的下一步行动建议（中文，一句话）"
}}

要求：issues 若为空数组，说明学生写得没问题，此时鼓励要具体，不要空泛夸奖。
"""


# ============================================================
# 5. 素材库自动分类（收藏时触发）
# ============================================================
CLASSIFY_SYSTEM = """你是一位考研英语一写作教研员，负责把学生收藏的素材归入正确的素材库，并告诉他「这条素材该用在作文的哪个位置」。
你的判断必须具体、可执行：能一句话说清放在开头段还是结尾段，以及怎么用。
严禁模棱两可（不要写「视情况而定」这类废话）。只输出 JSON，不要输出任何解释性文字。"""

CLASSIFY_USER_TMPL = """请把下面这条素材归类。

【素材英文】{content}
【中文含义】{cn}
【例句 / 上下文】{example}
【素材类型】{item_type}（vocab 词汇 / phrase 短语搭配 / pattern 句型模板 / sentence 长难句 / expression 句子表达）
【来源文章】{source_title}
【来源主题】{source_topic}

【库（library）二选一】
- writing：作文素材库 —— 能直接写进作文的句型、搭配、观点表达、金句
- reading：阅读积累库 —— 主要为读懂服务的词汇释义、长难句拆解

【主题分类（category）九选一】
教育学习 / 科技发展 / 社会现象 / 环境保护 / 文化交流 / 人生哲理 / 经济就业 / 品质精神 / 通用表达

【作文位置（position）五选一 —— 最重要的一维】
- opening：开头段（引出话题、描述现象、点出争议）
- transition：过渡段（承上启下，然而 / 而且 / 相反 类）
- body：主体段（举例、对比、因果、让步论证）
- ending：结尾段（总结观点、提出建议、呼吁、升华）
- universal：通用（开头、主体、结尾都能用）

【文体（genre）六选一 —— 这条素材适合写进哪一类文章】
- argumentative：议论文（大作文主体）—— 立论、分析原因、论证观点、提出建议
- expository：说明文 —— 客观陈述现象、数据、趋势
- practical：应用文（小作文）—— 书信、邮件、通知、告示、备忘录、纪要专用措辞
- descriptive：描写文 —— 图画、场景、人物描写
- narrative：记叙文 —— 讲述经历或案例
- universal：通用文体 —— 议论文、说明文、应用文都能用

判断文体的关键线索：
· 出现 I am writing to / Dear Sir or Madam / Yours sincerely / 敬启者 → practical
· 出现 According to the chart / the figure / account for / 数据显示 → expository
· 出现 As is vividly depicted / the picture shows / 如图所示 → descriptive
· 出现 once / when I was / my experience / 有一次 → narrative
· 属于观点论证、原因分析、建议呼吁 → argumentative
· 看不出明确归属 → universal

【功能（function）选项】
引出话题 / 描述现象 / 提出观点 / 举例论证 / 对比论证 / 因果分析 / 让步转折 / 总结归纳 / 提出建议 / 呼吁行动 / 情感升华 / 精准释义 / 句子结构

【等级（level）】A＝亮点表达（能显著提分） B＝常用表达 C＝理解型（了解即可）

输出严格为如下 JSON：
{{
  "library": "writing 或 reading",
  "category": "上面的主题分类之一",
  "position": "上面的位置之一",
  "genre": "上面的文体之一",
  "function": "上面的功能之一",
  "level": "A 或 B 或 C",
  "usage": "一句话使用建议（中文，40 字以内）。必须明确说出用在开头段/过渡段/主体段/结尾段，并说明具体怎么用",
  "tags": ["2-4 个中文标签"],
  "confidence": 0.0 到 1.0 之间的小数,
  "reason": "判断依据，一句话（中文）"
}}
"""


# ============================================================
# 6. 批量标注「写作表达的位置 + 文体」（一次调用标注整篇）
# ============================================================
LABEL_MATERIALS_SYSTEM = """你是一位考研英语一写作老师。给你一组写作表达，请判断每一条：
① 最适合放在作文的哪个位置；② 属于什么文体。
只输出 JSON，不要输出任何解释性文字。"""

LABEL_MATERIALS_USER_TMPL = """请判断下面每一条写作表达的位置与文体。

【文章标题】{title}
【列表】
{items}

【位置 position 只能填】opening（开头段：引出话题/描述现象）、transition（过渡段：承上启下）、
body（主体段：举例/对比/因果/让步论证）、ending（结尾段：总结/建议/升华）、universal（通用）

【文体 genre 只能填】argumentative（议论文，大作文主体）、expository（说明文，客观陈述数据趋势）、
practical（应用文，书信/通知/告示/纪要专用措辞）、descriptive（描写文，图画场景描写）、
narrative（记叙文，讲述经历）、universal（通用文体）

输出严格为如下 JSON：
{{
  "labels": [
    {{"index": 0,
      "position": "上面的位置之一",
      "genre": "上面的文体之一",
      "usage": "一句话使用建议（中文，40字以内），要明确说出用在开头段/过渡段/主体段/结尾段"}}
  ]
}}

数量要求：labels 的数量和顺序必须与列表完全一致。
"""


# ------------------------------------------------------------
# 组装函数
# ------------------------------------------------------------
def _band(difficulty: int) -> tuple[int, int]:
    """按难度档位给出目标词数区间。

    英一阅读单篇约 400-450 词；所以下限档（3）直接抬到 430 起——
    「可以比英一难，不许比英一简单」这条在词数上也要落地。
    1/2 档默认不可达（见 config.EXAM_MIN_DIFFICULTY），只作兜底。
    """
    return {
        1: (300, 360),
        2: (360, 430),
        3: (430, 500),
        4: (500, 580),
        5: (580, 680),
    }.get(difficulty, (430, 500))


def word_band(difficulty: int) -> tuple[int, int]:
    """公开别名：按难度档位取目标词数区间（pipeline 做长度校验时用）。"""
    return _band(difficulty)


def build_expand_prompt(
    title: str,
    body: str,
    have: int,
    difficulty: int,
) -> str:
    lo, hi = _band(difficulty)
    return EXPAND_USER_TMPL.format(
        title=title or "（无标题）",
        body=body,
        have=have,
        min_words=lo,
        max_words=hi,
        difficulty=difficulty,
    )


def build_article_prompt(
    topic: str,
    difficulty: int,
    *,
    directives: str = "",
    recent_titles: list[str] | None = None,
    stage: str | None = None,
) -> str:
    """stage 由调用方传入（每个账号可以有自己的备考阶段），不传时才回退到 .env。"""
    lo, hi = _band(difficulty)
    extra = ""
    if recent_titles:
        extra += "\n【近期已写过，请勿重复主题角度】" + "；".join(recent_titles[:8])
    if directives:
        extra += "\n【本篇特别要求】" + directives
    return ARTICLE_USER_TMPL.format(
        topic=topic,
        difficulty=difficulty,
        difficulty_label=DIFFICULTY_LABEL.get(difficulty, "标准 · 考研英语一"),
        min_words=lo,
        max_words=hi,
        stage=stage or settings.stage,
        directives=extra,
        knowledge=ek.article_knowledge_text(topic),
    )


def build_analysis_prompt(
    title: str, body: str, weak_words: list[dict] | None = None, *, exam_mode: bool = False
) -> str:
    hint = ""
    if weak_words:
        words = "、".join(w["word"] for w in weak_words if w.get("word"))
        if words:
            hint = (
                f"\n【用户此前反复不认识的词（若本篇出现，请务必收进 vocab 并优先排序）】{words}\n"
            )
    if exam_mode:
        hint += (
            "\n【真题精读模式】本文是考研英语一真题原文，请在常规精读之外额外注意：\n"
            "- vocab 优先收「真题高频、且常被用来设置正确选项或干扰项」的词与搭配\n"
            "- sentences 挑「真题最爱设题的句子」：观点句、转折句、因果句、例证定位句\n"
            "- logic.reading_tips 结合考研阅读出题套路给建议（例证题看例子前后的观点句、"
            "态度题重点看转折之后、细节题先定位再比对选项等）\n"
        )
    knowledge = ek.analysis_knowledge_text()
    if exam_mode:
        knowledge += (
            "\n【注意】本文是**真题原文**，你上面的分析就等于在给真题做验尸："
            "请把「这段话出题人实际是怎么设题的」讲透，"
            "并明确指出这篇的设题重心落在哪几段。\n"
        )
    return ANALYSIS_USER_TMPL.format(
        title=title, body=body, weak_hint=hint, knowledge=knowledge
    )


def build_writing_prompt(title: str, body: str) -> str:
    return WRITING_USER_TMPL.format(title=title, body=body)


def build_grade_prompt(
    prompt_cn: str,
    reference: str,
    answer: str,
    expressions: list[str] | None = None,
    history: list[dict] | None = None,
) -> str:
    exp = "、".join(expressions) if expressions else "（无特别要求）"
    history_hint = ""
    if history:
        history_hint = "\n【学生此前的作答与批改记录（请据此判断是否真的改进了）】\n" + json.dumps(
            history, ensure_ascii=False, indent=2
        )
    return GRADE_USER_TMPL.format(
        prompt_cn=prompt_cn,
        expressions=exp,
        reference=reference or "（无）",
        answer=answer,
        history_hint=history_hint,
    )


def build_classify_prompt(
    content: str,
    cn: str = "",
    example: str = "",
    item_type: str = "phrase",
    source_title: str = "",
    source_topic: str = "",
) -> str:
    return CLASSIFY_USER_TMPL.format(
        content=content or "",
        cn=cn or "（无）",
        example=example or "（无）",
        item_type=item_type or "phrase",
        source_title=source_title or "（无）",
        source_topic=source_topic or "（无）",
    )


def build_label_materials_prompt(title: str, items: list[dict]) -> str:
    lines = []
    for i, w in enumerate(items):
        parts = [f"[{i}] {w.get('content', '')}"]
        if w.get("cn"):
            parts.append(f"中文：{w['cn']}")
        if w.get("usage_note"):
            parts.append(f"用法：{w['usage_note']}")
        lines.append("　".join(parts))
    return LABEL_MATERIALS_USER_TMPL.format(
        title=title or "（无）",
        items="\n".join(lines) if lines else "（空）",
    )
