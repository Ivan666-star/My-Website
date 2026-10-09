# -*- coding: utf-8 -*-
"""英语侧「示例每日文章」的数据文件（给 tools/_seed_english.py 用）。

为什么要有这个文件：
    库里已有的每日文章都是**真实 AI 生成**的（10-03 ~ 10-07 五天）。
    「学习数据」页的 7 天窗口是 10-01 ~ 10-07，缺开头两天；
    补上这两天，窗口才是满的，热力/趋势看起来才完整。

内容来源说明（重要，别改成假装真题）：
    这两篇是**我按考研英语一标准原创**的样例，不是任何一年的真题原文
    （真题必须用户自己贴原文导入，编造原文等于教错东西）。
    所以 source_type 用 'demo'、model 用 '内置示例内容'，
    并且 source 字段统一以 `[示例] ` 开头 —— 既能被 --clear 精确删掉，
    也能让用户一眼看出这不是真题。

结构完全对齐 app/ai/pipeline.py 的 store_package：
    {article, analysis{translation,vocab,sentences,grammar,logic,exam_focus}, writing{items,imitation}}
"""

# ============================================================
# 文章 1 · 教育学习 · 2026-10-01
# ============================================================
ART_MEASURE = {
    "date": "2026-10-01",
    "topic": "教育学习",
    "difficulty": 4,
    "pkg": {
        "article": {
            "title": "When the Measure Becomes the Mission",
            "subtitle": "Schools are asked to be accountable, and accountability has quietly "
                        "redefined what schools believe they are for.",
            "source": "本站自撰样刊 · 教育评论",
            "topic": "教育学习",
            "difficulty": "4",
            "body": (
                "For a decade, the secondary school in the north of the city advertised a single "
                "number on its front gate: the proportion of its pupils who went on to university. "
                "Parents read it as a promise and the school worked hard to keep it. Nobody involved "
                "was acting in bad faith. The number was easy to understand, it was easy to compare "
                "with neighbouring schools, and it did, in a rough way, track something that parents "
                "genuinely wanted.\n\n"
                "Then the teaching began to change. Optional subjects that no examination rewarded "
                "were quietly dropped; the long, unstructured projects that had once occupied the "
                "last weeks of the summer term were replaced by revision sessions. Teachers were not "
                "told to abandon the things they valued. They were simply told what would be "
                "measured, and they responded as sensible people do. Within a few years the school "
                "had the best figure in the district and a curriculum that had been reshaped, almost "
                "without anyone deciding to reshape it, around a single question: what can be "
                "counted?\n\n"
                "Economists have a name for this. When a measure becomes a target, it ceases to be a "
                "good measure. Not because the number is wrong, but because the behaviour it was "
                "supposed to summarise starts to bend towards it. Hospitals judged on waiting times "
                "shorten waiting times, and sometimes do so by keeping patients in ambulances. The "
                "pattern is not evidence of dishonesty. It is evidence that people are responsive, "
                "and that a system which rewards one visible thing will get more of that thing, "
                "whatever else it quietly gives up.\n\n"
                "None of this argues against measurement. Without it, the school's real problems -- "
                "the pupils who were falling behind, the departments that had lost their way -- would "
                "have stayed invisible, and the families who depended on the school would have had no "
                "way to know. The difficulty is that measurement is a tool for answering a question, "
                "and it is easy to forget which question. A figure that describes how well a school "
                "is doing is useful. A figure that has quietly become what a school is for is "
                "something else altogether.\n\n"
                "The practical lesson is not to stop counting but to count more than one thing, and "
                "to keep asking what the numbers were meant to stand for. The school in question now "
                "publishes four figures instead of one, including one that no parent had ever asked "
                "about: how many of its leavers say, a year later, that they would choose the same "
                "subjects again. It is a clumsy and rather subjective measure. That, in a sense, is "
                "the point."
            ),
        },
        "analysis": {
            "translation": [
                {
                    "en": "For a decade, the secondary school in the north of the city advertised a "
                          "single number on its front gate: the proportion of its pupils who went on "
                          "to university.",
                    "zh": "十年来，这座城市北边的那所中学一直在大门上公布一个数字：本校升入大学的学生比例。",
                    "note": "advertise 在这里不是「做广告」，而是「公开展示、明示」，是考研里常见的熟词僻义。冒号后是对 a single number 的解释，阅读时先抓主干。",
                },
                {
                    "en": "Parents read it as a promise and the school worked hard to keep it.",
                    "zh": "家长把它当作一种承诺来读，学校也竭力去兑现它。",
                    "note": "read A as B = 把 A 理解为 B。keep a promise 的宾语被代词 it 替换，指前面那个数字。",
                },
                {
                    "en": "Teachers were not told to abandon the things they valued. They were simply "
                          "told what would be measured, and they responded as sensible people do.",
                    "zh": "没有人要求教师放弃他们看重的东西。他们只是被告知什么会被考核，于是他们做出了明理人都会做出的反应。",
                    "note": "这是全文最关键的一句转折：**不动声色**地重定义（quietly redefine）。两个句子构成「否定—肯定」的对照，作者把责任从个人品德转移到了制度激励上。",
                },
                {
                    "en": "When a measure becomes a target, it ceases to be a good measure. Not "
                          "because the number is wrong, but because the behaviour it was supposed to "
                          "summarise starts to bend towards it.",
                    "zh": "当一种衡量方式本身变成了要追逐的目标，它就不再是一种好的衡量方式。不是因为数字错了，而是因为它本该概括的行为开始向它靠拢。",
                    "note": "这是著名的「古德哈特定律」的口语版。not because ... but because ... 是考研高频对举结构，重点永远落在 but 之后。",
                },
                {
                    "en": "A figure that describes how well a school is doing is useful. A figure "
                          "that has quietly become what a school is for is something else altogether.",
                    "zh": "一个描述学校办得好不好的数字是有用的；一个已经悄悄变成了「学校存在的意义」的数字，就完全是另一回事了。",
                    "note": "两个 A figure 平行起句，第二句用 quietly become 把差别点出来。此类「同头异尾」的平行结构常用来下判断，也是主旨题的落点。",
                },
            ],
            "vocab": [
                {
                    "word": "in bad faith",
                    "pos": "phr.",
                    "meaning_in_context": "出于不诚实的动机；居心不良",
                    "common_meanings": "bad faith 指「不守信用、不怀好意」；反义是 in good faith（出于善意、诚心）",
                    "cn_definition": "存心欺诈地；不诚实地",
                    "exam_note": "常与 negotiate / act / bargain 搭配，法律与商业文本里高频。",
                    "example": "Nobody involved was acting in bad faith.",
                    "importance": 4,
                },
                {
                    "word": "track",
                    "pos": "v.",
                    "meaning_in_context": "（大致）反映、与……同步变动",
                    "common_meanings": "常见义是「追踪、跟踪」（track a parcel）；名词是「小径、轨道」",
                    "cn_definition": "（数据、指标）反映；与……保持一致",
                    "exam_note": "熟词僻义。track 作「反映/吻合」讲时，宾语多是趋势、指标、收入这类抽象名词。",
                    "example": "It did, in a rough way, track something that parents genuinely wanted.",
                    "importance": 4,
                },
                {
                    "word": "drop",
                    "pos": "v.",
                    "meaning_in_context": "取消、停止开设（课程、项目）",
                    "common_meanings": "常见义是「掉落、下降」；也可指「不再与某人往来」（drop a friend）",
                    "cn_definition": "取消；砍掉（课程、栏目、项目）",
                    "exam_note": "和 axe（砍掉）、scrap（废弃）同义。看到 subject 被 drop 要反应成「这门课没了」，不是「成绩掉了」。",
                    "example": "Optional subjects that no examination rewarded were quietly dropped.",
                    "importance": 4,
                },
                {
                    "word": "revision",
                    "pos": "n.",
                    "meaning_in_context": "复习（课）；备考温习",
                    "common_meanings": "常见义是「修改、修订」（make a revision to the plan）；英式英语里特指「考前复习」",
                    "cn_definition": "复习；温课",
                    "exam_note": "英式用法。revision session = 复习课，常带一点应试暗示，与后文的 project 形成「应试/素养」对照。",
                    "example": "The long projects were replaced by revision sessions.",
                    "importance": 3,
                },
                {
                    "word": "reshape",
                    "pos": "v.",
                    "meaning_in_context": "重塑；从根本上改造（往往是无形的、渐进的）",
                    "common_meanings": "re- 表「重新」，shape 作动词是「塑造、决定……的形态」",
                    "cn_definition": "重塑；重新塑造",
                    "exam_note": "搭配 quietly / fundamentally / irrevocably reshape，副词是理解作者评价的关键。",
                    "example": "A curriculum that had been reshaped without anyone deciding to reshape it.",
                    "importance": 4,
                },
                {
                    "word": "cease to be",
                    "pos": "phr.",
                    "meaning_in_context": "不再是；失去……的身份或性质",
                    "common_meanings": "cease 是「停止」（cease fire 停火），比 stop 正式",
                    "cn_definition": "不再是……；不再具有……性质",
                    "exam_note": "cease to be + 名词/形容词，用来点出「性质已经变了」，是判断作者立场的信号。",
                    "example": "When a measure becomes a target, it ceases to be a good measure.",
                    "importance": 4,
                },
                {
                    "word": "bend towards",
                    "pos": "phr.",
                    "meaning_in_context": "向……倾斜、趋附",
                    "common_meanings": "bend 是「使弯曲」；bend over backwards 是「不惜一切地帮忙」",
                    "cn_definition": "（为迎合而）向……靠拢",
                    "exam_note": "本句是比喻用法：行为向指标「弯腰」。别理解成物理上的弯曲。",
                    "example": "The behaviour it was supposed to summarise starts to bend towards it.",
                    "importance": 4,
                },
                {
                    "word": "fall behind",
                    "pos": "phr.",
                    "meaning_in_context": "（学业上）落在后面、跟不上",
                    "common_meanings": "也可指「拖欠」（fall behind on rent 拖欠房租）",
                    "cn_definition": "落后；跟不上进度",
                    "exam_note": "教育类文章中 fall behind 的主语几乎总是学生或班级。",
                    "example": "The pupils who were falling behind would have stayed invisible.",
                    "importance": 3,
                },
                {
                    "word": "lose one's way",
                    "pos": "phr.",
                    "meaning_in_context": "迷失方向；偏离本来的目标",
                    "common_meanings": "字面是「迷路」；引申为「（组织、人）忘了初衷」",
                    "cn_definition": "迷失方向；走偏了",
                    "exam_note": "此处主语是 departments（教研组），是拟人化的抽象用法 —— 考研很爱考这类隐喻。",
                    "example": "The departments that had lost their way would have stayed invisible.",
                    "importance": 3,
                },
                {
                    "word": "stand for",
                    "pos": "phr.",
                    "meaning_in_context": "代表、象征、（缩写）是……的意思",
                    "common_meanings": "还有「容忍、忍受」义（常用在否定句中：I won't stand for it.）",
                    "cn_definition": "代表；表示……的含义",
                    "exam_note": "本句 what the numbers were meant to stand for = 这些数字本来要代表什么，是理解全文立场的关键短语。",
                    "example": "Keep asking what the numbers were meant to stand for.",
                    "importance": 5,
                },
            ],
            "sentences": [
                {
                    "en": "When a measure becomes a target, it ceases to be a good measure -- not "
                          "because the number is wrong, but because the behaviour it was supposed to "
                          "summarise starts to bend towards it.",
                    "zh": "当一种衡量方式本身变成了要追逐的目标，它就不再是一种好的衡量方式 —— 不是因为数字错了，而是因为它本该概括的行为开始向它靠拢。",
                    "skeleton": "When A becomes B, it ceases to be C -- not because X, but because Y.",
                    "main_clause": "it ceases to be a good measure",
                    "subject": "it（指代 a measure）",
                    "predicate": "ceases to be",
                    "object": "a good measure",
                    "clauses": [
                        "时间状语从句：When a measure becomes a target —— 提出条件，主句才是结论",
                        "not because ... but because ... 引导两个原因状语从句，构成对举",
                        "定语从句：it was supposed to summarise 修饰 the behaviour，关系代词 that 被省略",
                    ],
                    "modifiers": [
                        "破折号后的 not because ... but because ... 是对前面结论的解释，阅读时应先跳过，读完主句再回来看",
                        "it was supposed to summarise 是插入在 the behaviour 之后的定语从句，把主语和谓语开始隔开",
                    ],
                    "non_finite": "to summarise —— 不定式作定语，修饰 the behaviour（= 它本该用来概括的东西）",
                    "insertion": "not because ... but because ... 整体是插入性的解释成分",
                    "appositive": "",
                    "logic": "先给判断（不再是好指标），再给两层原因。两层原因一否一肯，重点落在 but because 之后 —— 这是考研阅读里「找作者真正想说」的固定突破口。",
                    "why_hard": "主句很短（it ceases to be a good measure），但破折号后面拖了两个并列的原因从句，中间还夹了一个省略了 that 的定语从句。考试时最常见的是读完前半句就以为结束了，结果没抓住真正的解释在哪。",
                },
                {
                    "en": "It is evidence that people are responsive, and that a system which rewards one "
                          "visible thing will get more of that thing, whatever else it quietly gives up.",
                    "zh": "它说明的是人会对激励作出反应，也说明一个只奖励某一件看得见的事的制度，会得到更多这件东西 —— 无论它为此悄悄放弃了什么。",
                    "skeleton": "It is evidence that X, and that Y, whatever Z.",
                    "main_clause": "It is evidence",
                    "subject": "It（指前一句描述的现象）",
                    "predicate": "is",
                    "object": "evidence",
                    "clauses": [
                        "that 引导同位语从句（不是定语从句）：that people are responsive",
                        "第二个 that 从句与第一个并列：that a system ... will get more of that thing",
                        "定语从句：which rewards one visible thing 修饰 a system（关系代词 which 作主语，不可省）",
                        "让步状语从句：whatever else it quietly gives up",
                    ],
                    "modifiers": [
                        "whatever else ... gives up 放在句尾，是对主句结论的补充限制，语气上相当于 even if it gives up other things",
                    ],
                    "non_finite": "",
                    "insertion": "",
                    "appositive": "that people are responsive 与 that a system ... 同作 evidence 的同位语",
                    "logic": "先下判断（这是关于「人会响应激励」的证据），再用第二个同位语从句把机制说透，最后用让步从句指出代价。三个层次依次是：结论 → 机制 → 代价。",
                    "why_hard": "evidence 后面挂了两个并列的 that 从句，容易只读到一个；中间又插了一个 which 定语从句；句尾 whatever 引导的让步从句离主句很远，常被漏读，而这一小句正是作者保留态度的地方。",
                },
                {
                    "en": "A figure that describes how well a school is doing is useful. A figure that has "
                          "quietly become what a school is for is something else altogether.",
                    "zh": "一个描述学校办得好不好的数字是有用的；一个已经悄悄变成了「学校存在的意义」的数字，就完全是另一回事了。",
                    "skeleton": "A figure that X is useful. A figure that Y is something else altogether.",
                    "main_clause": "A figure ... is useful / A figure ... is something else altogether",
                    "subject": "A figure（两句同形主语）",
                    "predicate": "is",
                    "object": "useful / something else altogether",
                    "clauses": [
                        "定语从句一：that describes how well a school is doing（描述有没有用）",
                        "定语从句二：that has quietly become what a school is for（描述性质已变）",
                        "宾语从句：how well a school is doing",
                        "表语从句：what a school is for",
                    ],
                    "modifiers": [
                        "副词 quietly 是第二句里唯一的「评价性」词，作者的态度几乎全压在它身上；阅读时它常被当成虚词跳过",
                    ],
                    "non_finite": "",
                    "insertion": "",
                    "appositive": "",
                    "logic": "两句结构完全平行，只在第一个定语从句上做了替换，用同形结构逼出对比：有用的工具 vs 变成了目的本身。这是议论文下判断的典型写法。",
                    "why_hard": "两句话各自都带两层从句（定语从句里再套 how / what 从句），词汇并不难，但句子壳子很像，容易读成同义反复。必须抓住 quietly 和 something else altogether 才能看出是转折。",
                },
            ],
            "grammar": [
                {
                    "name": "not because ... but because ...（原因对举）",
                    "category": "句法",
                    "explanation": "先用 not because 排除一个人们通常会想到的原因，再用 but because 给出作者真正认可的原因。信息重心在 but 之后，这也是阅读时判断作者立场的固定突破口。",
                    "pattern": "..., not because + 从句, but because + 从句",
                    "examples": [
                        "When a measure becomes a target, it ceases to be a good measure -- not because the number is wrong, but because the behaviour it was supposed to summarise starts to bend towards it.",
                        "The policy failed not because it was badly designed, but because nobody had been asked to carry it out.",
                    ],
                },
                {
                    "name": "whatever 引导的让步状语从句",
                    "category": "句法",
                    "explanation": "whatever 相当于 no matter what，既可以在从句中作主语或宾语，也表示「无论什么」。它放句尾时对主句结论加一层限制，往往是作者留余地的位置。",
                    "pattern": "主句 + whatever (+ 名词) + 主语 + 谓语",
                    "examples": [
                        "... a system which rewards one visible thing will get more of that thing, whatever else it quietly gives up.",
                        "The committee approved the plan, whatever the cost to the smaller sites.",
                    ],
                },
            ],
            "logic": {
                "overall": "文章借一所中学「大门上只挂一个数字」的故事，说明一个更一般的道理：当衡量指标变成追逐目标，它就不再是好指标，被衡量的行为会反过来向指标靠拢。但作者并未否定测量本身 —— 测量是回答问题的工具，问题在于人们忘了它本来要回答什么。结论是：不是停止计数，而是多计几件、并且记得数字代表什么。",
                "structure": [
                    {"para": "1", "role": "引出案例", "main_idea": "学校用「升学率」这一个数字对外说明自己，家长也认这个数字 —— 没有任何人出于恶意"},
                    {"para": "2", "role": "揭示变化", "main_idea": "教学悄悄变了：没考试价值的选修课停开，项目制学习被复习课取代，课程被重塑成「什么可以被计算」"},
                    {"para": "3", "role": "给出机制", "main_idea": "古德哈特定律：指标一旦成为目标就不再是好指标；用医院缩短等待时间作类比，指出这是激励的结果而非诚信问题"},
                    {"para": "4", "role": "让步辩证", "main_idea": "但这不构成反对测量的理由 —— 没有测量，落后生和走偏的教研组会永远隐身；问题是别忘记测量是为了回答哪个问题"},
                    {"para": "5", "role": "给出结论", "main_idea": "不是停止计数，而是多计几件并追问数字代表什么；学校现在公布四项指标，包括一项笨拙而主观的「一年后是否还愿选同样的科目」"},
                ],
                "author_attitude": "作者的态度是**谨慎的批判与建设性提议**（critical but constructive）：反复强调「没人出于恶意」（in bad faith）「不是不诚实的证据」，把矛头指向激励结构而不是人；同时明确「不构成反对测量的理由」。最后用 clumsy / subjective 形容新指标却肯定它 —— 说明作者宁可要一个不完美但贴近本意的数字，也不要一个精确却错位的数字。",
                "reading_tips": [
                    "第 1 段的作用只是**建立案例**，不是论点，别在这里找主旨；真正的论点在第 3 段那句古德哈特定律。",
                    "看到「Nobody involved was acting in bad faith」「The pattern is not evidence of dishonesty」这两处否定句要警觉 —— 作者在为下文把责任归于制度铺路，主旨题若选项把问题归因于「教师不诚实/学校欺骗」即属张冠李戴。",
                    "第 4 段以 None of this argues against ... 开头，是标准的**让步段**：让步段之后的第 5 段才是结论，主旨题优先看第 5 段。",
                    "态度题的答题依据集中在 quietly、clumsy、subjective 这几个带评价色彩的副词/形容词上，不要凭整体印象选「批评/赞扬」这类过大的词。",
                ],
            },
            "exam_focus": {
                    "pattern": "案例—机制—让步—结论（评论型议论文）",
                    "main_thread": "全文主线是：一个看似合理的单一指标，如何在不改变任何人的品德的前提下，悄悄重塑了一所学校的教学目标；以及为什么正确的应对不是放弃测量，而是测量更多元、并记住测量的初衷。第 3 段的机制和第 5 段的建议是主旨题的两端，第 4 段的让步是干扰项最爱出没的地方。",
                    "set_points": [
                        {
                            "qtype": "细节事实",
                            "anchor_en": "Optional subjects that no examination rewarded were quietly dropped; the long, unstructured projects ... were replaced by revision sessions.",
                            "para": 2,
                            "why_here": "列举了具体变化的句子，是细节题的高频设题处；两处被动语态（were dropped / were replaced）说明变化并非由某个人决定。",
                            "stem": "According to Paragraph 2, the change in teaching at the school was characterised by ______.",
                            "right_direction": "正确项应落在「课程向可考核内容收缩」上，措辞留有余地，如 a narrowing of the curriculum towards what could be assessed。",
                            "traps": [
                                {
                                    "trap": "张冠李戴",
                                    "sample_option": "It was ordered by the local education authority.",
                                    "why_wrong": "原文说 Teachers were not told to abandon the things they valued，变化并非上级指令所致，而是对考核信号的自发反应。",
                                },
                                {
                                    "trap": "曲解原意",
                                    "sample_option": "It was driven by teachers' desire for higher salaries.",
                                    "why_wrong": "原文完全未提薪酬，这是把常识推入原文。",
                                },
                            ],
                        },
                        {
                            "qtype": "推理判断",
                            "anchor_en": "Hospitals judged on waiting times shorten waiting times, and sometimes do so by keeping patients in ambulances.",
                            "para": 3,
                            "why_here": "举例处（Hospitals ...）。考研几乎不在举例本身出题，而是考这个例子**要说明什么**。",
                            "stem": "The hospital example in Paragraph 3 is used to show that ______.",
                            "right_direction": "正确项应指向「对单一指标的追求会使行为变形（甚至产生荒谬做法）」，如 people adapt to what is measured rather than to what matters。",
                            "traps": [
                                {
                                    "trap": "以例代论",
                                    "sample_option": "Hospitals are generally less honest than schools.",
                                    "why_wrong": "把例子的表层内容当成结论，且原文明确否认这是诚信问题。",
                                },
                                {
                                    "trap": "过度推断",
                                    "sample_option": "Waiting-time targets should be abolished.",
                                    "why_wrong": "作者在第 4 段明确说自己不反对测量，废止指标不是作者立场。",
                                },
                            ],
                        },
                        {
                            "qtype": "态度观点",
                            "anchor_en": "It is a clumsy and rather subjective measure. That, in a sense, is the point.",
                            "para": 5,
                            "why_here": "句末的评价句 + 一句短判断，是态度题的落点。clumsy / subjective 看似贬义，后一句却肯定它。",
                            "stem": "The author's attitude towards the school's newly published figures can best be described as ______.",
                            "right_direction": "正确项应体现「不完美但方向正确、值得肯定」，如 approving of the change in principle。",
                            "traps": [
                                {
                                    "trap": "断章取义",
                                    "sample_option": "Dismissive, because the new measure is subjective.",
                                    "why_wrong": "只抓住 clumsy / subjective 的表层贬义，忽略了 That, in a sense, is the point 的肯定。",
                                },
                                {
                                    "trap": "态度过强",
                                    "sample_option": "Enthusiastic about an ideal solution.",
                                    "why_wrong": "作者承认新指标笨拙主观，谈不上热情，也不认为是理想方案。",
                                },
                            ],
                        },
                    ],
                    "trap_summary": "本篇最容易设的三类陷阱：① 把问题归因于个人品德（in bad faith / dishonesty），与原文反复强调的「激励结构」相反；② 把第 4 段的让步误读成作者主张放弃测量；③ 态度题只看 clumsy / subjective 得出「否定」结论，而忽略紧随其后的肯定。",
                },
        },
        "writing": {
            "items": [
                {
                    "kind": "phrase",
                    "content": "in bad faith",
                    "cn": "出于不良动机；不诚实地",
                    "example": "Nobody involved was acting in bad faith.",
                    "usage_note": "适合在「反驳对方把问题归因于道德」时使用，比写 The company lied 更克制、更学术。常与 in good faith 对举使用。",
                    "position_hint": "transition",
                    "position_usage": "用在过渡段：承上启下，衔接前后两段观点",
                    "genre": "argumentative",
                },
                {
                    "kind": "pattern",
                    "content": "not because ... but because ...",
                    "cn": "不是因为……，而是因为……",
                    "example": "It ceased to be a good measure, not because the number is wrong, but because the behaviour it summarised began to bend towards it.",
                    "usage_note": "议论文里最好用的「辨因」结构：先排除一个大家都会想到的原因，再给出你真正想说的原因。比 the reason is 有辨析力得多，且天然带出层次。",
                    "position_hint": "body",
                    "position_usage": "用在主体段：举例、对比、因果或让步论证",
                    "genre": "argumentative",
                },
                {
                    "kind": "pattern",
                    "content": "When a measure becomes a target, it ceases to be a good measure.",
                    "cn": "当衡量标准本身变成追逐的目标，它就不再是好的衡量标准。",
                    "example": "When a measure becomes a target, it ceases to be a good measure.",
                    "usage_note": "可直接引用的判断句，适用于一切「考核 / 排名 / KPI 变形」类话题（教育评价、科研产出、企业绩效）。引述知名判断是议论文里性价比很高的加分方式，但只引一句、不炫技。",
                    "position_hint": "body",
                    "position_usage": "用在主体段：举例、对比、因果或让步论证",
                    "genre": "argumentative",
                },
                {
                    "kind": "phrase",
                    "content": "lose one's way",
                    "cn": "迷失方向；偏离初衷",
                    "example": "The departments that had lost their way would have stayed invisible.",
                    "usage_note": "写组织、行业、文化「走偏了」时好用，主语可以是人或机构。文中用来形容教研组，属于拟人化的抽象用法，用在自己作文里很显得有观察力。",
                    "position_hint": "body",
                    "position_usage": "用在主体段：举例、对比、因果或让步论证",
                    "genre": "argumentative",
                },
                {
                    "kind": "pattern",
                    "content": "The practical lesson is not to ... but to ...",
                    "cn": "实际的经验教训不是……，而是……",
                    "example": "The practical lesson is not to stop counting but to count more than one thing.",
                    "usage_note": "结尾段提出建议时的固定句式：先否定一种极端做法，再给出平衡的替代方案。比 We should 高级，而且自带「理性不极端」的阅卷好感。",
                    "position_hint": "ending",
                    "position_usage": "用在结尾段：总结观点、提出建议或升华主题",
                    "genre": "argumentative",
                },
            ],
            "imitation": {
                "prompt_cn": "请把下面这句话翻译成英文，尽量用上今天积累的表达（提示：可以用 not because ... but because ... 和 stand for）：\n\n人们批评这套排名体系，并不是因为数字本身算错了，而是因为大学渐渐忘了这些数字本该代表什么。",
                "prompt_en": "The criticisms of the ranking system are not that the figures are miscalculated, but that universities have slowly forgotten what the figures were meant to stand for.",
                "hint": "1)「并不是因为……而是因为……」优先用 not because ... but because ...，注意两个原因要对举；2)「本该代表什么」用 be meant to stand for + 宾语从句 what ...；3) 前半句可用 the criticism is not that ... but that ... 的名词性对举，比 because 从句更书面；4) 别忘了「渐渐」要译出 gradually / slowly 这个渐变感。",
                "reference_answer": "The criticism is not that the figures are wrong, but that universities have gradually forgotten what those figures were meant to stand for.",
            },
        },
        "source_type": "demo",
        "model": "内置示例内容",
    },
}


# ============================================================
# 文章 2 · 环境保护 · 2026-10-02
# ============================================================
ART_VERGES = {
    "date": "2026-10-02",
    "topic": "环境保护",
    "difficulty": 3,
    "pkg": {
        "article": {
            "title": "The Wilderness in the Cracks",
            "subtitle": "Big restoration projects get the headlines. Much of the biodiversity we still "
                        "have is being kept alive somewhere far less photogenic.",
            "source": "本站自撰样刊 · 环境评论",
            "topic": "环境保护",
            "difficulty": "3",
            "body": (
                "Conservation has a taste for the dramatic. The announcements that make the news "
                "involve million-hectare parks, new corridors for migrating elephants, and pledges to "
                "plant a billion trees. These are worth celebrating; nobody who has stood at the edge "
                "of a restored wetland would call them empty gestures.\n\n"
                "Yet a growing body of evidence suggests that a great deal of the biodiversity we "
                "still possess is being held in place by places too small and too ordinary to appear "
                "on any map of importance: the grass verge beside a motorway, the strip of weeds "
                "behind a car park, the unbuilt corner at the end of a housing estate. Such ground is "
                "rarely protected, rarely surveyed, and almost never named after anyone.\n\n"
                "A survey of roadside verges across several European countries found that they "
                "supported a wider range of flowering plants than the farmland around them, largely "
                "because they had never been ploughed, sprayed or fertilised. The explanation is not "
                "romantic. It is that these strips have been left alone long enough to become "
                "complicated, and complexity is what living systems need. The same logic applies to "
                "the humble garden wall, which in some cities holds more species of moss and lichen "
                "than the nature reserve a bus ride away.\n\n"
                "None of this is an argument for giving up on grand projects. Large, connected "
                "habitats are irreplaceable, and the species that depend on them cannot be rescued by "
                "a thousand roadside margins. The point is rather that the small places are cheap, "
                "quick and already here. Nothing has to be bought, drained or flooded. What is "
                "required is mainly restraint -- and restraint is politically awkward, because an "
                "unmown verge looks to most voters like neglect.\n\n"
                "So the obstacle is less financial than aesthetic, which may explain why the most "
                "effective campaigns have not talked about species at all. They have argued about "
                "tidiness, and quietly changed the standard of what a cared-for street looks like. "
                "That is a slower kind of persuasion than planting a billion trees, and harder to "
                "photograph. It may also be the part that decides whether anything else survives."
            ),
        },
        "analysis": {
            "translation": [
                {
                    "en": "Conservation has a taste for the dramatic. The announcements that make the "
                          "news involve million-hectare parks, new corridors for migrating elephants, "
                          "and pledges to plant a billion trees.",
                    "zh": "自然保护偏爱戏剧性的场面。能上新闻的公告，往往涉及上百万公顷的公园、为迁徙大象新辟的通道，以及「种下十亿棵树」的承诺。",
                    "note": "have a taste for = 偏爱、喜好。第二句用三个并列宾语 (parks / corridors / pledges) 铺出「宏大叙事」的群像，为下段转折做反衬。",
                },
                {
                    "en": "Yet a growing body of evidence suggests that a great deal of the biodiversity "
                          "we still possess is being held in place by places too small and too ordinary "
                          "to appear on any map of importance.",
                    "zh": "然而越来越多的证据表明，我们如今仍然拥有的生物多样性，有相当大一部分其实是靠一些小得、普通得根本不会出现在任何「重要地图」上的地方维持着的。",
                    "note": "本句是全文的**转折与论点句**。hold ... in place = 把……维系在原位；too ... to ... 结构里嵌了定语从句，是长难句的重点。",
                },
                {
                    "en": "The explanation is not romantic. It is that these strips have been left "
                          "alone long enough to become complicated, and complexity is what living "
                          "systems need.",
                    "zh": "解释并不浪漫：这些狭长地带只是被长期放着没人管，时间久到足以变得复杂起来 —— 而复杂恰恰是生命系统所需要的。",
                    "note": "两个短句一否一肯，是作者「反浪漫、讲机制」的典型笔法。is that ... 引出名词性表语从句。",
                },
                {
                    "en": "The point is rather that the small places are cheap, quick and already here. "
                          "Nothing has to be bought, drained or flooded.",
                    "zh": "重点毋宁说在于：这些小地方便宜、见效快，而且本来就在那里。什么都不用买、不用排水、也不用引水淹没。",
                    "note": "rather 这里不是「相当」，而是「更确切地说、毋宁」，用来修正上一句可能被误读的方向，是考研的常见用法。",
                },
                {
                    "en": "What is required is mainly restraint -- and restraint is politically awkward, "
                          "because an unmown verge looks to most voters like neglect.",
                    "zh": "所需要的主要是克制 —— 而克制在政治上很不好办，因为在多数选民眼里，一块没修剪的草地就等同于疏于管理。",
                    "note": "what 引导的主语从句 + 破折号后追加一层。awkward 在此为「难办、不好处理」，不是「尴尬」。此句点出政策障碍的性质，是主旨题的落点之一。",
                },
            ],
            "vocab": [
                {
                    "word": "have a taste for",
                    "pos": "phr.",
                    "meaning_in_context": "偏爱、喜好（常含轻微批评意味）",
                    "common_meanings": "taste 作名词是「味道、品味」；to one's taste 是「合某人口味」",
                    "cn_definition": "对……情有独钟；偏好",
                    "exam_note": "主语多为机构、媒体、公众等集合名词，用来解释某种取向，比 like 更有评述味。",
                    "example": "Conservation has a taste for the dramatic.",
                    "importance": 4,
                },
                {
                    "word": "hold in place",
                    "pos": "phr.",
                    "meaning_in_context": "把……维系住、使其不散掉",
                    "common_meanings": "in place 还表示「在适当位置、准备就绪」（The arrangements are in place.）",
                    "cn_definition": "维持……（的存在）；使……得以延续",
                    "exam_note": "被动式 be held in place by ... 是「归因」句型，看到它就要问「靠什么维持」。",
                    "example": "Biodiversity is being held in place by places too small to appear on any map.",
                    "importance": 5,
                },
                {
                    "word": "verge",
                    "pos": "n.",
                    "meaning_in_context": "（道路旁的）草边、路肩绿化带",
                    "common_meanings": "常见义是「边缘」，on the verge of = 濒临、即将",
                    "cn_definition": "路旁草地；边缘地带",
                    "exam_note": "本义（边缘）与引申义（濒临）都要掌握。grass verge 是英式说法，美式多说 roadside shoulder。",
                    "example": "The grass verge beside a motorway.",
                    "importance": 4,
                },
                {
                    "word": "plough",
                    "pos": "v.",
                    "meaning_in_context": "犁地、翻耕",
                    "common_meanings": "plough 也写作 plow（美式）；plough through 是「吃力地读完」",
                    "cn_definition": "耕（地）；翻土",
                    "exam_note": "与 spray（喷药）、fertilise（施肥）并列出现时，是在列举「农业干预」，理解成一组比逐个翻译更高效。",
                    "example": "They had never been ploughed, sprayed or fertilised.",
                    "importance": 3,
                },
                {
                    "word": "humble",
                    "pos": "adj.",
                    "meaning_in_context": "不起眼的、寻常朴素的",
                    "common_meanings": "常见义是「谦逊的」（a humble man）；也可表示「地位低微的」",
                    "cn_definition": "不起眼的；平凡的",
                    "exam_note": "the humble + 名词 是英语评论里很地道的写法（the humble garden wall = 那堵不起眼的院墙），修饰物时表示「看似普通却重要」。",
                    "example": "The same logic applies to the humble garden wall.",
                    "importance": 4,
                },
                {
                    "word": "lichen",
                    "pos": "n.",
                    "meaning_in_context": "地衣（与苔藓一同作为「空气质量/生态复杂度」的指示物种）",
                    "common_meanings": "不可数名词，无复数",
                    "cn_definition": "地衣",
                    "exam_note": "与 moss（苔藓）常成对出现于环境类文章。专有名词只需认识，不必会拼写。",
                    "example": "A wall that holds more species of moss and lichen than the nature reserve.",
                    "importance": 2,
                },
                {
                    "word": "irreplaceable",
                    "pos": "adj.",
                    "meaning_in_context": "不可替代的、无可取代的",
                    "common_meanings": "ir- 为否定前缀 + replaceable",
                    "cn_definition": "不可替代的",
                    "exam_note": "让步段里常见的「抬高对方」用词：先承认大项目 irreplaceable，再转折说出自己的重点，逻辑才立得住。",
                    "example": "Large, connected habitats are irreplaceable.",
                    "importance": 4,
                },
                {
                    "word": "restraint",
                    "pos": "n.",
                    "meaning_in_context": "克制、不出手干预",
                    "common_meanings": "也可指「约束、限制措施」（wage restraint 工资管制）",
                    "cn_definition": "克制；（主动的）不加干预",
                    "exam_note": "本文的核心对策词。restraint 作「什么都不做」讲时，是环保/医政类文章的高频概念。",
                    "example": "What is required is mainly restraint.",
                    "importance": 5,
                },
                {
                    "word": "awkward",
                    "pos": "adj.",
                    "meaning_in_context": "难办的、不好处理的",
                    "common_meanings": "常见义是「尴尬的」（an awkward silence）；也可指「笨拙的」",
                    "cn_definition": "（事情）棘手的、难办的",
                    "exam_note": "熟词僻义。politically awkward = 在政治上不好交代，主语是「事」而不是「人」时，一律理解为「难办」。",
                    "example": "Restraint is politically awkward.",
                    "importance": 5,
                },
                {
                    "word": "aesthetic",
                    "pos": "adj.",
                    "meaning_in_context": "审美上的、外观层面的",
                    "common_meanings": "也可作名词 aesthetics（美学）；形容词拼作 aesthetic（英）/ esthetic（美）",
                    "cn_definition": "审美的；关乎观感的",
                    "exam_note": "本文用它把障碍从「钱」转移到「观感」，是主旨句里的关键对照词（financial vs aesthetic）。",
                    "example": "The obstacle is less financial than aesthetic.",
                    "importance": 5,
                },
            ],
            "sentences": [
                {
                    "en": "Yet a growing body of evidence suggests that a great deal of the biodiversity we "
                          "still possess is being held in place by places too small and too ordinary to "
                          "appear on any map of importance.",
                    "zh": "然而越来越多的证据表明，我们如今仍然拥有的生物多样性，有相当大一部分其实是靠一些小得、普通得根本不会出现在任何「重要地图」上的地方维持着的。",
                    "skeleton": "Yet evidence suggests that A is being held in place by B.",
                    "main_clause": "a growing body of evidence suggests that ...",
                    "subject": "a growing body of evidence",
                    "predicate": "suggests",
                    "object": "that 引导的宾语从句（整个 that 从句作 suggests 的宾语）",
                    "clauses": [
                        "宾语从句：that a great deal of the biodiversity ... is being held in place by places ...",
                        "定语从句一：we still possess（修饰 biodiversity，关系代词 that/which 被省略）",
                        "定语从句二：too small and too ordinary to appear ...（修饰 places，此处是「too ... to ...」结构作后置修饰，不是定语从句）",
                    ],
                    "modifiers": [
                        "Yet 置于句首，是全文的转折信号 —— 读到它就要把上一段的态度翻转过来",
                        "a growing body of 是量词短语，等于「越来越多的」，不要拆成 body（身体）",
                    ],
                    "non_finite": "to appear on any map —— 不定式，与前面的 too 呼应，构成 too ... to ...（太……以至于不能……）",
                    "insertion": "we still possess 是插在 biodiversity 之后的省略关系代词的定语从句",
                    "appositive": "",
                    "logic": "先用 Yet 反转上一段的「宏大叙事」，再用 suggests that 引出论点：真正在维系生物多样性的是不起眼的小地方。句末的 too ... to ... 用「连地图都上不了」来强化「不起眼」，是评价性成分。",
                    "why_hard": "宾语从句里套了两个定语结构（一个省略关系代词，一个是 too...to...），而且宾语从句本身用了被动语态进行时 is being held，主语和施动者离得很远。常见误读是把 places 当成主语，读成「这些地方维持了……」，其实 places 前面有介词 by，是施动者。",
                },
                {
                    "en": "A survey of roadside verges across several European countries found that they "
                          "supported a wider range of flowering plants than the farmland around them, "
                          "largely because they had never been ploughed, sprayed or fertilised.",
                    "zh": "一项横跨多个欧洲国家、针对路旁草带的调查发现：这些草带所支持的开花植物种类比它们周围的农田更多，这主要因为它们从未被翻耕、喷洒农药或施过肥。",
                    "skeleton": "A survey found that X supported more A than B, because Y.",
                    "main_clause": "A survey ... found that ...",
                    "subject": "A survey of roadside verges across several European countries",
                    "predicate": "found",
                    "object": "that 引导的宾语从句",
                    "clauses": [
                        "宾语从句：that they supported a wider range of flowering plants than the farmland around them",
                        "比较结构：more ... than ...（同类比较：verges 上的植物种类 vs 农田上的植物种类）",
                        "原因状语从句：because they had never been ploughed, sprayed or fertilised（过去完成时，强调「从未被干预过」这一延续状态）",
                    ],
                    "modifiers": [
                        "largely 修饰 because 从句，表示「主要是因为这个原因」，是作者留有余地的措辞（还有其他原因）",
                        "three 个过去分词 ploughed / sprayed / fertilised 并列，用「农业干预」的三个代表动作解释「为什么复杂」",
                    ],
                    "non_finite": "",
                    "insertion": "",
                    "appositive": "",
                    "logic": "这是典型的「研究证据」句：先给主体（调查）+ 结论（草带物种更多），再给原因（没被干预）。论证链是「反直觉的发现 → 朴素的机制」，正是本文主张的证据支撑。",
                    "why_hard": "比较结构省略较多：than the farmland around them 后面省掉了 did / supported，要自己补出「农田所支持的植物种类」。另外过去完成时 had never been 表示「一直都没有」，若读成一般过去时会丢掉「从未」这层意思。",
                },
                {
                    "en": "So the obstacle is less financial than aesthetic, which may explain why the most "
                          "effective campaigns have not talked about species at all.",
                    "zh": "所以说，真正的障碍与其说是钱的问题，不如说是观感的问题 —— 这或许也解释了为什么最有效的那些行动压根没谈物种。",
                    "skeleton": "So the obstacle is less A than B, which may explain why ...",
                    "main_clause": "the obstacle is less financial than aesthetic",
                    "subject": "the obstacle",
                    "predicate": "is",
                    "object": "less financial than aesthetic（表语）",
                    "clauses": [
                        "非限定性定语从句：which may explain why ...（which 指代前面整个主句）",
                        "宾语从句：why the most effective campaigns have not talked about species at all",
                    ],
                    "modifiers": [
                        "So 承接上文给出推论，标志这是作者自己的判断而不是别人的观点",
                        "at all 在否定句中加强语气，相当于「一点也（没）」",
                    ],
                    "non_finite": "",
                    "insertion": "",
                    "appositive": "",
                    "logic": "先用 less A than B 把障碍从「钱」改判为「观感」，再用 which 从句推出一个合理的解释（有效行动不谈物种而谈整洁）。逻辑是「重新定性 → 解释现象」，也是全文结论的入口。",
                    "why_hard": "less A than B 的意思是「与其说是 A，不如说是 B」，重心在 B —— 极易读反成「不如 A」。另外 which 指代的是整个主句而非某个名词，学生常误以为它指 aesthetic。",
                },
            ],
            "grammar": [
                {
                    "name": "less A than B（否定比较：与其说 A，不如说 B）",
                    "category": "句法",
                    "explanation": "less ... than ... 连接的两个成分是「被否定的那个在前、被肯定的那个在后」。它和 not so much A as B 基本同义，是考研里判断作者重心的关键结构：重心永远在 than 之后。",
                    "pattern": "X is less + A + than + B  ⇒  与其说是 A，不如说是 B",
                    "examples": [
                        "So the obstacle is less financial than aesthetic.",
                        "The problem is less a shortage of money than a shortage of political will.",
                    ],
                },
                {
                    "name": "which 引导的非限定性定语从句（指代整句）",
                    "category": "句法",
                    "explanation": "which 前面有逗号时，它可以指代前面整个主句的内容，用来对上一句作出评价、推论或解释。这时 which 不能用 that 替换，也不能省略。",
                    "pattern": "主句, + which + 谓语 ...  ⇒  这一点……",
                    "examples": [
                        "... which may explain why the most effective campaigns have not talked about species at all.",
                        "The plan was shelved, which disappointed nobody.",
                    ],
                },
            ],
            "logic": {
                "overall": "文章先承认大型保护项目的价值，再指出：真正维系着大量生物多样性的，是路边草带、车场后的小片杂草、小区尽头那块没盖房子的地这类毫不起眼的地方。理由是它们长期未被干预，因而足够复杂 —— 而复杂正是生命系统需要的。作者不主张放弃大项目，而是指出小地方便宜、见效快、本来就存在，所需的主要是「克制」；真正的障碍不是钱而是观感，因为没修剪的草地看起来像疏于管理。",
                "structure": [
                    {"para": "1", "role": "先扬（承认对方）", "main_idea": "保护事业偏爱宏大叙事：百万公顷公园、大象迁徙通道、十亿棵树；这些值得称赞，不是空姿态"},
                    {"para": "2", "role": "转折提出论点", "main_idea": "但大量现存的生物多样性其实靠极小、极普通的地方维系 —— 这些地方很少被保护、很少被调查，也几乎没人给它们命名"},
                    {"para": "3", "role": "给出证据与机制", "main_idea": "欧洲路旁草带的调查显示其开花植物种类多于周围农田，原因是不曾被翻耕/喷药/施肥，因而积累了复杂性；同理，不起眼的院墙也能承载更多苔藓与地衣"},
                    {"para": "4", "role": "让步并界定主张", "main_idea": "这不是要放弃大项目（连通的大栖息地不可替代），而是小地方便宜、见效快、已在眼前；需要的主要是克制，而克制在政治上难办"},
                    {"para": "5", "role": "结论与升华", "main_idea": "障碍与其说是钱，不如说是观感；因此最有效的行动不谈物种而谈整洁，悄悄改变了「什么叫被照料好的街道」的标准 —— 这比种十亿棵树更慢、更不上镜，却可能决定其他一切能否存活"},
                ],
                "author_attitude": "作者的态度是**谨慎赞赏 + 务实呼吁**（appreciative of grand projects, but insistent on the modest and the political）：一方面用 worth celebrating / irreplaceable 明确肯定大项目，另一方面把论证重心压在小地方上，并坦诚承认对策的政治难度（politically awkward）。结尾用 slower / harder to photograph 形容自己的主张，是克制的自我评价，而非宣传口吻。",
                "reading_tips": [
                    "全文结构是「先扬—后转—让步—结论」，第 1 段的作用是**建立对立方**，不是论点；主旨看第 2 段（too small and too ordinary）和第 5 段（less financial than aesthetic）。",
                    "第 4 段开头 None of this is an argument for giving up on grand projects 是标准让步句，跟随其后的 The point is rather that ... 才是作者主张 —— 凡是选项说「作者反对大项目」的，一律错。",
                    "注意两类「熟词僻义」：awkward（难办，不是尴尬）、rather（毋宁、更确切地说，不是相当）。这两处常是细节题或语义题的直接考点。",
                    "本篇最可能出的态度题落点是「务实 / 谨慎肯定」一类；若选项是 enthusiastic support 或 outright rejection，都属于态度过强。",
                ],
            },
            "exam_focus": {
                    "pattern": "先扬后抑—证据支持—让步—结论（环境评论型）",
                    "main_thread": "文章用「宏大项目 vs 边角小地」的对照推进：承认大项目的不可替代，但论证真正在维持生物多样性的是那些毫不起眼、长期未受干预因而足够复杂的小地方；结论把障碍从资金重新定性为观感，并指出有效行动因此改变了话语方式。第 2 段是论点，第 3 段是证据，第 4 段是让步，第 5 段是结论 —— 主旨题几乎必定落在「小地方的价值 + 障碍是观感不在钱」这一组合上。",
                    "set_points": [
                        {
                            "qtype": "主旨大意",
                            "anchor_en": "a great deal of the biodiversity we still possess is being held in place by places too small and too ordinary to appear on any map of importance",
                            "para": 2,
                            "why_here": "全文论点句所在，Yet 转折之后的重心句，主旨题的正确项几乎都由它改写而来。",
                            "stem": "Which of the following best expresses the main idea of the text?",
                            "right_direction": "正确项应同时包含「不起眼的小地方对生物多样性至关重要」与「所需主要是克制/改变观感标准」两层，措辞如 Small, unmanaged spaces play an underrated role in conservation。",
                            "traps": [
                                {
                                    "trap": "以偏概全",
                                    "sample_option": "Large restoration projects are a waste of public money.",
                                    "why_wrong": "原文第 1、4 段都明确肯定大项目（worth celebrating / irreplaceable），并未否定其价值。",
                                },
                                {
                                    "trap": "偷换重心",
                                    "sample_option": "Roadside verges are richer in plant species than farmland.",
                                    "why_wrong": "这只是第 3 段的证据细节，不是全文主旨，属于「以细节充主旨」。",
                                },
                            ],
                        },
                        {
                            "qtype": "推理判断",
                            "anchor_en": "It is that these strips have been left alone long enough to become complicated, and complexity is what living systems need.",
                            "para": 3,
                            "why_here": "冒号/表语从句给出的机制解释，作者用它替代「浪漫化」的解释，是推理题最爱设题的地方（问作者为何强调「未被干预」）。",
                            "stem": "According to Paragraph 3, roadside verges are species-rich mainly because ______.",
                            "right_direction": "正确项应落在「长期无人干预使其生态关系足够复杂」上，如 they were left undisturbed long enough to develop complexity。",
                            "traps": [
                                {
                                    "trap": "因果倒置",
                                    "sample_option": "They are deliberately sown with wildflower seeds.",
                                    "why_wrong": "原文恰恰强调它们从未被翻耕、喷洒、施肥，即没有任何人为播种管理。",
                                },
                                {
                                    "trap": "过度推断",
                                    "sample_option": "They receive more sunlight than farmland.",
                                    "why_wrong": "原文未提光照，属于用常识补入原文未给的信息。",
                                },
                            ],
                        },
                        {
                            "qtype": "细节事实",
                            "anchor_en": "So the obstacle is less financial than aesthetic ... They have argued about tidiness, and quietly changed the standard of what a cared-for street looks like.",
                            "para": 5,
                            "why_here": "结论段用 less A than B 作出定性，紧接一句给出对应事实（不谈物种、谈整洁），是细节题与语义题的双重设题处。",
                            "stem": "It can be inferred from the last paragraph that successful campaigns have ______.",
                            "right_direction": "正确项应指出它们改变的是「何为整洁/被照料」的公共标准，如 reshaped public expectations about how a well-kept street should look。",
                            "traps": [
                                {
                                    "trap": "曲解原意",
                                    "sample_option": "avoided mentioning biodiversity because it is not important.",
                                    "why_wrong": "不谈物种是策略选择（因为障碍在观感），并非认为生物多样性不重要。",
                                },
                                {
                                    "trap": "反向干扰",
                                    "sample_option": "focused on raising funds for larger reserves.",
                                    "why_wrong": "原文说障碍 less financial than aesthetic，且重点是改造观感，方向相反。",
                                },
                            ],
                        },
                    ],
                    "trap_summary": "本篇三类高频陷阱：① 把作者的「先扬」读成「反对大项目」（忽略 worth celebrating / irreplaceable / None of this is an argument for giving up）；② 把第 3 段的证据细节（路旁草带物种更多）当成全文主旨；③ 把 less A than B 读反，得出「障碍是钱」而不是「障碍是观感」。",
                },
        },
        "writing": {
            "items": [
                {
                    "kind": "phrase",
                    "content": "a growing body of evidence suggests that ...",
                    "cn": "越来越多的证据表明……",
                    "example": "Yet a growing body of evidence suggests that a great deal of the biodiversity we still possess is being held in place by places too small to appear on any map.",
                    "usage_note": "给论点挂证据时的稳妥开场，比 Many studies show 更书面、更像英文学术评论。后面接 that 从句，主语要写清。注意 body 这里是量词「（一大批）」，不是「身体」。",
                    "position_hint": "body",
                    "position_usage": "用在主体段：举例、对比、因果或让步论证",
                    "genre": "argumentative",
                },
                {
                    "kind": "pattern",
                    "content": "None of this is an argument for ... The point is rather that ...",
                    "cn": "这一切并不构成主张……的理由。重点毋宁说在于……",
                    "example": "None of this is an argument for giving up on grand projects. The point is rather that the small places are cheap, quick and already here.",
                    "usage_note": "让步段的标准写法：先主动排除「对方可能以为你在主张的极端立场」，再用 rather 把重点扳回来。能显著减少「非此即彼」式论证的失分。",
                    "position_hint": "transition",
                    "position_usage": "用在过渡段：承上启下，衔接前后两段观点",
                    "genre": "argumentative",
                },
                {
                    "kind": "pattern",
                    "content": "less A than B",
                    "cn": "与其说是 A，不如说是 B",
                    "example": "The obstacle is less financial than aesthetic.",
                    "usage_note": "下判断、纠正常识误区时极好用：先把大家默认的原因放在 A 位置，再把真正的原因放在 B。重心在 B，作文里用它就是在明确告诉阅卷人你的判断。",
                    "position_hint": "body",
                    "position_usage": "用在主体段：举例、对比、因果或让步论证",
                    "genre": "argumentative",
                },
                {
                    "kind": "phrase",
                    "content": "the humble + 名词",
                    "cn": "不起眼的……（看似普通却重要）",
                    "example": "The same logic applies to the humble garden wall.",
                    "usage_note": "把平凡事物「抬」起来的写法。写环保、传统、日常技艺类话题时，用 the humble bicycle / the humble notebook 起句，一句话就带出「被忽视的重要」这层意思。",
                    "position_hint": "body",
                    "position_usage": "用在主体段：举例、对比、因果或让步论证",
                    "genre": "argumentative",
                },
            ],
            "imitation": {
                "prompt_cn": "请把下面这句话翻译成英文，尽量用上今天积累的表达（提示：可以用 less ... than ... 和 a growing body of evidence）：\n\n越来越多的证据表明，城市里真正的问题与其说是空间不够，不如说是我们不允许任何地方保持凌乱。",
                "prompt_en": "A growing body of evidence suggests that the real problem in cities is less a shortage of space than our refusal to let anything stay untidy.",
                "hint": "1)「越来越多的证据表明」直接用 a growing body of evidence suggests that ...；2)「与其说 A 不如说 B」用 less A than B，注意 A、B 要词性对称（都是名词短语）；3)「不允许任何地方保持凌乱」可译作 our refusal to let anything stay untidy，用动词名词化比 we do not allow ... to be messy 更书面；4) 别把 shortage 写成 lack of，二者皆可但 shortage 与 less 搭配更自然。",
                "reference_answer": "A growing body of evidence suggests that the real problem in cities is less a shortage of space than our unwillingness to let anything stay untidy.",
            },
        },
        "source_type": "demo",
        "model": "内置示例内容",
    },
}


ARTICLES = [ART_MEASURE, ART_VERGES]
