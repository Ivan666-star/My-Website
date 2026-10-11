"""SQLite 存储层：表结构 + 幂等迁移 + 用户隔离的读写。

============================================================
★ 多用户隔离的核心约定（改这个文件前先读这一段）
============================================================
这个系统支持多个账号，每个人有自己独立的文章、词汇、素材库、复习进度和统计。

**归属怎么传递的（重要）**：
    请求进来时中间件把 user_id 放进一个 ContextVar（`db.as_user`）；
    `db.py` 里所有碰「用户数据」的函数都从那个 ContextVar 取 user_id 并写进 SQL。
    这样就不必给每个接口、每个函数都加一个 user_id 参数 —— 那种写法一定会有人漏，
    而漏掉的表现是「A 看到 B 的数据」，属于最不能接受的 bug。

**四种上下文**：
    未设置（None）  → `scope()` 直接抛错。这是故意的：忘了设上下文应该立刻报错，
                      而不是静默地把所有人的数据都查出来。
    UNCLAIMED(0)   → 未认领的老数据 / 还没建账号时的本地自用模式。
    ALL_USERS(-1)  → 显式声明「这是全库操作」（迁移、启动回填、调度器遍历用户时用）。
    具体 user_id    → 正常请求。

**哪些表带 user_id**：
    articles / study_log / favorites / events / user_profile / annotations
    其余（vocab / sentences / grammar / writing_items / imitation_tasks / submissions）
    不带 —— 它们全部通过 article_id 挂到某篇文章上，那篇文章归谁，它们就归谁。
    所以只要 articles 的查询是隔离的，下游自动就隔离了。

**为什么「未认领」用 0 而不是 NULL**：
    SQLite 的 UNIQUE 索引认为多个 NULL 互不相同，所以 UNIQUE(user_id,date) 在
    user_id 为 NULL 时等于没约束 —— 反复写同一天会写出好几行。
    用 0 当哨兵（账号 id 从 1 开始自增，永远撞不上），约束才真正生效。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from functools import lru_cache

from .config import settings

# ============================================================
# 用户上下文
# ============================================================
UNCLAIMED = 0   # 未认领的老数据 / 还没建账号时的本地自用模式
ALL_USERS = -1  # 全库上下文：不按用户过滤（迁移 / 启动回填 / 调度器遍历用户）

_ctx: ContextVar[int | None] = ContextVar("kaoyan_current_user", default=None)


@contextmanager
def as_user(user_id: int):
    """把当前上下文切成某个具体用户。调度器、脚本、认领老数据时用。"""
    tok = _ctx.set(int(user_id))
    try:
        if int(user_id) > UNCLAIMED:
            ensure_user_db(int(user_id))
        yield int(user_id)
    finally:
        _ctx.reset(tok)


@contextmanager
def as_system():
    """全库上下文：明确表示「我知道我在跨用户操作」。"""
    tok = _ctx.set(ALL_USERS)
    try:
        yield ALL_USERS
    finally:
        _ctx.reset(tok)


@contextmanager
def as_local():
    """未认领上下文：只看 / 只写 user_id=0 的数据（还没建账号时的本地自用模式）。"""
    tok = _ctx.set(UNCLAIMED)
    try:
        yield UNCLAIMED
    finally:
        _ctx.reset(tok)


def use_local_context() -> None:
    """给独立脚本用：把当前上下文本地固定成 0 号桶（未认领 / 本地自用）。

    只针对「没有请求上下文、一个进程跑完就退」的工具脚本。
    正常请求请老老实实用 `with as_user(uid):` —— 别在服务里调这个。
    """
    _ctx.set(UNCLAIMED)


def current_user_id(required: bool = True) -> int | None:
    """当前用户 id。未设置时按 `required` 决定抛错还是返回 None。"""
    uid = _ctx.get()
    if uid is None and required:
        raise RuntimeError(
            "没有用户上下文：访问用户数据前必须先进 db.as_user(uid)"
            "（全库操作 db.as_system() / 未认领模式 db.as_local()）。"
            "这是隔离机制的一部分，不要绕过。"
        )
    return uid


def require_user() -> int:
    """必须是「某个具体用户」（含未认领的 0 号桶），不接受全库上下文。"""
    uid = current_user_id()
    if uid == ALL_USERS:
        raise RuntimeError("这个操作必须针对具体用户，不能在 db.as_system() 里执行")
    return int(uid)


def owner_id() -> int | None:
    """写入时该填的归属值。全库上下文返回 None（系统事件，不属于任何人）。"""
    uid = current_user_id(required=False)
    if uid is None or uid == ALL_USERS:
        return None
    return int(uid)


def scope(col: str = "user_id") -> tuple[str, tuple]:
    """返回 (WHERE 片段, 参数)。

    col 允许带表别名，例如 scope("a.user_id") → ("a.user_id=?", (uid,))。
    """
    uid = current_user_id()
    if uid == ALL_USERS:
        return "1=1", ()
    return f"{col}=?", (int(uid),)


# ============================================================
# 表结构
# ============================================================
# articles / study_log 的列定义单独拎出来：老库要把 UNIQUE 约束从「日期唯一」
# 改成「每人每天唯一」，而 SQLite 改不了约束，只能重建表 ——
# 重建时要复用的就是下面这两段，保证「新建的库」和「重建后的老库」结构一模一样。
_ARTICLES_COLS = """
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,                       -- 归属用户（老数据为 NULL，首个管理员认领）
    date          TEXT NOT NULL,                 -- YYYY-MM-DD，真题是 exam-2010-text2
    title         TEXT NOT NULL,
    subtitle      TEXT,
    source        TEXT,                          -- 来源标注
    source_type   TEXT DEFAULT 'ai',             -- ai | real | exam | demo
    topic         TEXT,
    difficulty    INTEGER DEFAULT 3,             -- 1-5
    word_count    INTEGER DEFAULT 0,
    est_minutes   INTEGER DEFAULT 20,
    body          TEXT NOT NULL,                 -- 英文正文，段落之间空行分段
    translation   TEXT DEFAULT '[]',             -- JSON: [{en, zh, note}]
    logic         TEXT DEFAULT '{}',             -- JSON: 阅读逻辑分析
    exam_focus    TEXT DEFAULT '{}',             -- JSON: 命题视角（出题人爱在哪设题/怎么骗人）
    status        TEXT DEFAULT 'ready',
    model         TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
"""
_ARTICLES_DDL = f"CREATE TABLE IF NOT EXISTS articles ({_ARTICLES_COLS}, UNIQUE(user_id, date))"

_STUDY_LOG_COLS = """
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER,                    -- 归属用户
    date             TEXT NOT NULL,
    article_id       INTEGER,
    read_seconds     INTEGER DEFAULT 0,
    analysis_opened  INTEGER DEFAULT 0,
    vocab_unknown    INTEGER DEFAULT 0,
    vocab_focus      INTEGER DEFAULT 0,
    vocab_known      INTEGER DEFAULT 0,
    imitation_rounds INTEGER DEFAULT 0,
    imitation_score  REAL,
    favorites_added  INTEGER DEFAULT 0,
    updated_at       TEXT DEFAULT (datetime('now','localtime'))
"""
_STUDY_LOG_DDL = f"CREATE TABLE IF NOT EXISTS study_log ({_STUDY_LOG_COLS}, UNIQUE(user_id, date))"

SCHEMA = (
    "PRAGMA journal_mode=WAL;\n"
    "\n-- 账号（密码只存 PBKDF2 派生值 + 每人独立 salt）\n"
    "CREATE TABLE IF NOT EXISTS users (\n"
    "    id            INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    username      TEXT NOT NULL COLLATE NOCASE UNIQUE,\n"
    "    display_name  TEXT DEFAULT '',\n"
    "    password_hash TEXT NOT NULL DEFAULT '',\n"
    "    salt          TEXT NOT NULL DEFAULT '',\n"
    "    is_admin      INTEGER DEFAULT 0,\n"
    "    active        INTEGER DEFAULT 1,\n"
    "    approved      INTEGER NOT NULL DEFAULT 1,\n"
    "    created_at    TEXT DEFAULT (datetime('now','localtime')),\n"
    "    last_login_at TEXT\n"
    ");\n"
    "\n-- 登录会话（只存令牌的哈希，库被看到也无法拿来登录）\n"
    "CREATE TABLE IF NOT EXISTS sessions (\n"
    "    token_hash   TEXT PRIMARY KEY,\n"
    "    user_id      INTEGER NOT NULL,\n"
    "    user_agent   TEXT DEFAULT '',\n"
    "    created_at   TEXT DEFAULT (datetime('now','localtime')),\n"
    "    last_seen_at TEXT\n"
    ");\n"
    "\n-- 每日文章\n"
    + _ARTICLES_DDL + ";\n"
    "\n-- 词汇\n"
    "CREATE TABLE IF NOT EXISTS vocab (\n"
    "    id                INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    article_id        INTEGER NOT NULL,\n"
    "    word              TEXT NOT NULL,\n"
    "    pos               TEXT,\n"
    "    meaning_in_context TEXT,\n"
    "    common_meanings   TEXT,\n"
    "    exam_note         TEXT,                       -- 这个词在真题里怎么考\n"
    "    cn_definition     TEXT,\n"
    "    example           TEXT,\n"
    "    importance        INTEGER DEFAULT 3,          -- 考研重要程度 1-5\n"
    "    mastery           INTEGER DEFAULT 1,          -- 1不认识 2重点学习 3已掌握（默认就是「不认识」，\n"
    "                                                  -- 由用户自己挑哪些已掌握 —— 2026-10-11 用户口径）\n"
    "    favorite          INTEGER DEFAULT 0,\n"
    "    occurrence        INTEGER DEFAULT 1,          -- 累计出现次数（跨文章累加）\n"
    "    last_review_at    TEXT,\n"
    "    UNIQUE(article_id, word)\n"
    ");\n"
    "\n-- 长难句\n"
    "CREATE TABLE IF NOT EXISTS sentences (\n"
    "    id           INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    article_id   INTEGER NOT NULL,\n"
    "    en           TEXT NOT NULL,\n"
    "    zh           TEXT,\n"
    "    skeleton     TEXT,                            -- 句子主干\n"
    "    main_clause  TEXT,\n"
    "    subject      TEXT,\n"
    "    predicate    TEXT,\n"
    "    object       TEXT,\n"
    "    clauses      TEXT DEFAULT '[]',               -- JSON 从句列表\n"
    "    modifiers    TEXT DEFAULT '[]',               -- JSON 修饰成分\n"
    "    non_finite   TEXT,                            -- 非谓语\n"
    "    insertion    TEXT,                            -- 插入结构\n"
    "    appositive   TEXT,                            -- 同位语\n"
    "    logic        TEXT,                            -- 逻辑关系\n"
    "    why_hard     TEXT,                            -- 为什么难 / 训练价值\n"
    "    order_index  INTEGER DEFAULT 0\n"
    ");\n"
    "\n-- 语法点\n"
    "CREATE TABLE IF NOT EXISTS grammar (\n"
    "    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    article_id  INTEGER NOT NULL,\n"
    "    name        TEXT NOT NULL,\n"
    "    category    TEXT,\n"
    "    explanation TEXT,\n"
    "    pattern     TEXT,\n"
    "    examples    TEXT DEFAULT '[]',\n"
    "    order_index INTEGER DEFAULT 0\n"
    ");\n"
    "\n-- 写作表达（词/搭配/句型）\n"
    "CREATE TABLE IF NOT EXISTS writing_items (\n"
    "    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    article_id  INTEGER NOT NULL,\n"
    "    kind        TEXT,                             -- word | phrase | pattern\n"
    "    content     TEXT NOT NULL,\n"
    "    cn          TEXT,\n"
    "    example     TEXT,\n"
    "    usage_note  TEXT,\n"
    "    position_hint TEXT,                           -- opening/transition/body/ending/universal\n"
    "    position_usage TEXT,                          -- 该条的位置使用建议（中文，AI 生成）\n"
    "    genre       TEXT,                             -- argumentative/expository/practical/...\n"
    "    order_index INTEGER DEFAULT 0\n"
    ");\n"
    "\n-- 仿写任务\n"
    "CREATE TABLE IF NOT EXISTS imitation_tasks (\n"
    "    id               INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    article_id       INTEGER NOT NULL,\n"
    "    prompt_cn        TEXT NOT NULL,\n"
    "    prompt_en        TEXT,\n"
    "    hint             TEXT,\n"
    "    reference_answer TEXT\n"
    ");\n"
    "\n-- 仿写提交记录（支持 修改→再提交→再检查）\n"
    "CREATE TABLE IF NOT EXISTS submissions (\n"
    "    id         INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    task_id    INTEGER NOT NULL,\n"
    "    round      INTEGER DEFAULT 1,\n"
    "    answer     TEXT NOT NULL,\n"
    "    feedback   TEXT DEFAULT '{}',\n"
    "    score      REAL,\n"
    "    created_at TEXT DEFAULT (datetime('now','localtime'))\n"
    ");\n"
    "\n-- 每日学习记录\n"
    + _STUDY_LOG_DDL + ";\n"
    "\n-- 原始行为事件流（长期个性化训练的原料）\n"
    "CREATE TABLE IF NOT EXISTS events (\n"
    "    id      INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    user_id INTEGER,\n"
    "    ts      TEXT DEFAULT (datetime('now','localtime')),\n"
    "    kind    TEXT,\n"
    "    ref_id  INTEGER,\n"
    "    payload TEXT DEFAULT '{}'\n"
    ");\n"
    "\n-- 收藏 / 素材库（V2：双库 + 主题分类 + 作文位置 + 功能 + 文体 + 使用建议）\n"
    "CREATE TABLE IF NOT EXISTS favorites (\n"
    "    id                INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    user_id           INTEGER,                     -- 归属用户\n"
    "    item_type         TEXT,                        -- vocab | phrase | pattern | sentence | expression\n"
    "    content           TEXT NOT NULL,\n"
    "    cn                TEXT,\n"
    "    example           TEXT,\n"
    "    tags              TEXT DEFAULT '[]',\n"
    "    source_article_id INTEGER,\n"
    "    library           TEXT DEFAULT 'reading',      -- writing 作文素材库 | reading 阅读积累库\n"
    "    category          TEXT DEFAULT '',             -- 主题分类：教育学习 / 科技发展 / ...\n"
    "    position          TEXT DEFAULT 'universal',    -- opening | transition | body | ending | universal\n"
    "    genre             TEXT DEFAULT 'universal',    -- 文体\n"
    "    function          TEXT DEFAULT '',             -- 修辞功能\n"
    "    level             TEXT DEFAULT 'B',            -- A 亮点表达 | B 常用表达 | C 理解型\n"
    "    usage             TEXT DEFAULT '',             -- 一句话使用建议\n"
    "    auto              INTEGER DEFAULT 0,           -- 1 = AI 自动归类，0 = 手动/规则\n"
    "    confidence        REAL DEFAULT 0,              -- 分类置信度 0-1\n"
    "    created_at        TEXT DEFAULT (datetime('now','localtime')),\n"
    "    updated_at        TEXT\n"
    ");\n"
    "\n-- 手写批注（每人 / 每篇 / 每区块一条；data 是向量笔迹 JSON）\n"
    "CREATE TABLE IF NOT EXISTS annotations (\n"
    "    id         INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    user_id    INTEGER NOT NULL,\n"
    "    doc_key    TEXT NOT NULL,          -- \"<文档前缀>:<区块名>\"，如 2026-10-05:body\n"
    "    data       TEXT DEFAULT '{}',      -- {v,w,strokes:[{c,s,hl,p:[[x,y],…]}]}\n"
    "    created_at TEXT DEFAULT (datetime('now','localtime')),\n"
    "    updated_at TEXT,\n"
    "    UNIQUE(user_id, doc_key)\n"
    ");\n"
    "\n-- 阅读笔记（划选填充高亮 → 自动记一条；独立成库，且有复习阶梯）\n"
    "CREATE TABLE IF NOT EXISTS reading_notes (\n"
    "    id           INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    user_id      INTEGER,\n"
    "    article_id   INTEGER,                     -- 来自哪篇（每日内容 / 真题）\n"
    "    doc_date     TEXT,                        -- 日期或 exam-2010-text2，便于按篇回看\n"
    "    kind         TEXT DEFAULT 'vocab',        -- vocab | phrase | sentence | other\n"
    "    content      TEXT NOT NULL,               -- 高亮选中的原文\n"
    "    cn           TEXT,                        -- 中文意思（双击查词/AI 补的，可空）\n"
    "    note         TEXT,                        -- 我自己的批注\n"
    "    color        TEXT DEFAULT 'yellow',        -- 填充颜色\n"
    "    context      TEXT,                        -- 所在句子：复习时给回忆线索\n"
    "    para_index   INTEGER,                     -- 第几段（重新打开文章时按它精确画回高亮）\n"
    "    char_start   INTEGER,                     -- 段内起始字符位置\n"
    "    created_at   TEXT DEFAULT (datetime('now','localtime')),\n"
    "    updated_at   TEXT,\n"
    "    review_count INTEGER DEFAULT 0,\n"
    "    next_review  TEXT DEFAULT '',              -- 空 = 不在复习队列\n"
    "    last_result  TEXT\n"
    ");\n"
    "\n-- 全局设置（跟具体用户无关的东西）\n"
    "CREATE TABLE IF NOT EXISTS profile (\n"
    "    key   TEXT PRIMARY KEY,\n"
    "    value TEXT\n"
    ");\n"
    "\n-- 每个用户自己的偏好（学习阶段 / 难度基线）\n"
    "CREATE TABLE IF NOT EXISTS user_profile (\n"
    "    user_id INTEGER NOT NULL,\n"
    "    key     TEXT NOT NULL,\n"
    "    value   TEXT,\n"
    "    PRIMARY KEY (user_id, key)\n"
    ");\n"
    "\n"
    # ============================================================
    # 数学一错题库（业务逻辑见 app/math.py）
    #   层级：数一错题总库 → 科目 → 普通错题 / 考研真题错题 → 专题 → 错题
    #   只保存「题干 + 原图 + AI 解析结果」；按用户明确要求**不设「我的答案」字段**。
    #   next_review / review_count 是 3/7/14/30 天复习阶梯的状态位。
    # ============================================================
    "-- 数学一错题（题干是唯一必填项）\n"
    "CREATE TABLE IF NOT EXISTS math_problems (\n"
    "    id             INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    user_id        INTEGER,\n"
    "    subject        TEXT DEFAULT '',                          -- 高等数学 / 线性代数 / 概率论与数理统计\n"
    "    kind           TEXT DEFAULT 'normal',                    -- normal 普通错题 / exam 考研真题错题\n"
    "    topic          TEXT DEFAULT '',                          -- 专题\n"
    "    stem           TEXT NOT NULL,                            -- 题干（只录题干，不含答案与解析）\n"
    "    image_path     TEXT DEFAULT '',                          -- 原题照片（相对 data/ 的路径）\n"
    "    source_note    TEXT DEFAULT '',                          -- 来源备注（哪本书 / 哪套卷）\n"
    "    knowledge      TEXT DEFAULT '',                          -- AI：核心知识点\n"
    "    key_steps      TEXT DEFAULT '[]',                        -- AI：关键步骤 JSON 数组\n"
    "    solution       TEXT DEFAULT '',                          -- AI：推荐解法\n"
    "    alt_solution   TEXT DEFAULT '',                          -- AI：另一种解法\n"
    "    pitfalls       TEXT DEFAULT '',                          -- AI：易错点\n"
    "    ai_model       TEXT DEFAULT '',\n"
    "    analyzed_at    TEXT DEFAULT '',                          -- 非空 = 解析已就绪\n"
    "    review_count   INTEGER DEFAULT 0,                        -- 已连续做对次数（推进阶梯用）\n"
    "    next_review    TEXT DEFAULT '',                          -- YYYY-MM-DD；空 = 不在复习队列\n"
    "    last_result    TEXT DEFAULT '',\n"
    "    last_review_at TEXT DEFAULT '',\n"
    "    created_at     TEXT DEFAULT (datetime('now','localtime')),\n"
    "    updated_at     TEXT DEFAULT (datetime('now','localtime'))\n"
    ");\n"
    "\n-- 每次重做的历史（长期保留，用于看「这道题反复错的次数」）\n"
    "CREATE TABLE IF NOT EXISTS math_review_log (\n"
    "    id                 INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    user_id            INTEGER,\n"
    "    problem_id         INTEGER NOT NULL,\n"
    "    result             TEXT DEFAULT '',      -- right / wrong / skip / drop / readd\n"
    "    review_count_after INTEGER DEFAULT 0,\n"
    "    created_at         TEXT DEFAULT (datetime('now','localtime'))\n"
    ");\n"
    "\n"
    "CREATE INDEX IF NOT EXISTS idx_vocab_word ON vocab(word);\n"
    "CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);\n"
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);\n"
    # ⚠️ 别在这里建带 user_id 的索引：老库的 articles/study_log 此刻还没有这一列，
    #    会直接 no such column 启动崩。它们统一放到 _migrate() 里、补完列之后再建。
)


# ------------------------------------------------------------
# 幂等迁移：老库自动补列 + 改约束 + 历史数据回填
#
# 设计要点：
#   1. 只在「该列本次才被加上」时回填它，已存在的列一律不碰 ——
#      否则每次启动都会把用户手动改过的分类冲掉。
#   2. 索引必须放在补列之后建，否则旧库会报 no such column。
#   3. **改 UNIQUE 约束只能重建表**（SQLite 没有 DROP CONSTRAINT），
#      重建时按列名搬运数据，所以 article_id 不会变，下游引用全部有效。
#   4. 老数据的 user_id 一律留 NULL，由「首个管理员」在初始化时认领
#      （见 users.claim_orphan_data）。不在这里回填，是因为此刻还没有任何用户，
#      随便填一个 id 会指向不存在的账号。
# ------------------------------------------------------------
_FAV_ADD_COLUMNS = [
    ("user_id", "INTEGER"),
    ("library", "TEXT"),
    ("category", "TEXT"),
    ("position", "TEXT"),
    ("genre", "TEXT"),
    ("function", "TEXT"),
    ("level", "TEXT"),
    ("usage", "TEXT"),
    ("auto", "INTEGER DEFAULT 0"),
    ("confidence", "REAL DEFAULT 0"),
    ("updated_at", "TEXT"),
    ("review_count", "INTEGER DEFAULT 0"),
    ("next_review", "TEXT"),
    ("last_result", "TEXT"),
    # 积累闭环 ①（10-07）：批改时比对作文正文，命中的素材就 +1 —— 用来回答
    # 「攒了这么多，到底用没用上」。used_count>0 即视为「用过」。
    ("used_count", "INTEGER DEFAULT 0"),
    ("last_used_at", "TEXT"),
]
_WI_ADD_COLUMNS = [
    ("position_hint", "TEXT"),
    ("position_usage", "TEXT"),
    ("genre", "TEXT"),
]
# 后续新增的增量列（老库升级用）
_ARTICLE_ADD_COLUMNS: dict[str, str] = {
    "exam_focus": "TEXT DEFAULT '{}'",
}
_VOCAB_ADD_COLUMNS: dict[str, str] = {
    "exam_note": "TEXT",
}
_EVENTS_ADD_COLUMNS: dict[str, str] = {
    "user_id": "INTEGER",
}

# 列 → 回填用的 SQL 表达式
_BACKFILL = {
    "position": "'universal'",
    "genre": "'universal'",
    "level": "'B'",
    "category": "'通用表达'",
    "function": "''",
    "usage": "''",
    "auto": "0",
    "confidence": "0",
    "updated_at": "created_at",
    "used_count": "0",
}
# 数值型用 IS NULL 判断，文本型还要带上 ='' 才能补上非空默认值
_BACKFILL_NUMERIC = {"auto", "confidence", "used_count"}


def _add_columns(conn: sqlite3.Connection, table: str, adds: dict[str, str]) -> set[str]:
    """幂等补列：PRAGMA 探测缺列 → ALTER TABLE，返回本次真正补上的列名。"""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    added: set[str] = set()
    for name, ddl in adds.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.add(name)
    return added


def _rebuild_table(conn: sqlite3.Connection, table: str, create_sql: str) -> None:
    """重建表以更换约束（SQLite 改不了 UNIQUE）。

    按列名交集搬运，所以主键 id 原样保留 —— 下游用 article_id 关联的数据不会错位。
    这里没有外键约束，也没有视图/触发器引用这两张表，所以换个名字再建最省事。
    """
    old_cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    tmp = f"{table}__migrating"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(f"ALTER TABLE {table} RENAME TO {tmp}")
    conn.execute(create_sql)  # rename 之后原表名已空出来，IF NOT EXISTS 会正常建
    new_cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    keep = [c for c in new_cols if c in old_cols]
    cols = ",".join(keep)
    conn.execute(f"INSERT INTO {table}({cols}) SELECT {cols} FROM {tmp}")
    conn.execute(f"DROP TABLE {tmp}")


def _migrate(conn: sqlite3.Connection) -> None:
    # ---- 1) 改约束：articles / study_log 从「日期全局唯一」变成「每人每天唯一」 ----
    for table, ddl in (("articles", _ARTICLES_DDL), ("study_log", _STUDY_LOG_DDL)):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "user_id" not in cols:
            _rebuild_table(conn, table, ddl)

    # ---- 2) 补列（索引 / 回填都依赖列已经存在） ----
    art_added = _add_columns(conn, "articles", _ARTICLE_ADD_COLUMNS)
    _add_columns(conn, "vocab", _VOCAB_ADD_COLUMNS)
    _add_columns(conn, "events", _EVENTS_ADD_COLUMNS)
    # users.approved：老库里的账号本来就能用，补列时默认 1（已批准）；
    # 只有自助注册新建的账号显式写 0，等管理员点头。
    _add_columns(conn, "users", {"approved": "INTEGER NOT NULL DEFAULT 1"})
    if "exam_focus" in art_added:
        # ALTER TABLE 的常量默认值本会填上，这里再兜一次 NULL / 空串
        conn.execute(
            "UPDATE articles SET exam_focus='{}' WHERE exam_focus IS NULL OR exam_focus=''"
        )

    fav_cols = {r["name"] for r in conn.execute("PRAGMA table_info(favorites)")}
    added: set[str] = set()
    for name, ddl in _FAV_ADD_COLUMNS:
        if name not in fav_cols:
            conn.execute(f"ALTER TABLE favorites ADD COLUMN {name} {ddl}")
            added.add(name)

    wi_cols = {r["name"] for r in conn.execute("PRAGMA table_info(writing_items)")}
    for name, ddl in _WI_ADD_COLUMNS:
        if name not in wi_cols:
            conn.execute(f"ALTER TABLE writing_items ADD COLUMN {name} {ddl}")

    # ---- 3) 补列之后再建索引 ----
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_library ON favorites(library)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_position ON favorites(position)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_genre ON favorites(genre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_user ON favorites(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_user_date ON articles(user_id, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_log_user_date ON study_log(user_id, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anno_user_doc ON annotations(user_id, doc_key)")
    # 数学错题库：这两张表是本次新增的（SCHEMA 里建、天生带 user_id），
    # 所以索引可以直接建，不像 articles/study_log 要等补列。
    conn.execute("CREATE INDEX IF NOT EXISTS idx_math_user ON math_problems(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_math_user_due ON math_problems(user_id, next_review)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_math_log ON math_review_log(user_id, problem_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON reading_notes(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_article ON reading_notes(user_id, article_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_due ON reading_notes(user_id, next_review)")

    # ---- 4) 只回填本次新加的列 ----
    # library 是特例：要按 item_type 推断，而不是填统一默认值
    if "library" in added:
        conn.execute(
            "UPDATE favorites SET library='writing' "
            "WHERE item_type IN ('phrase','pattern','expression')"
        )
        conn.execute("UPDATE favorites SET library='reading' WHERE library IS NULL")

    for col, expr in _BACKFILL.items():
        if col not in added:
            continue
        if col in _BACKFILL_NUMERIC:
            conn.execute(f"UPDATE favorites SET {col}={expr} WHERE {col} IS NULL")
        else:
            conn.execute(f"UPDATE favorites SET {col}={expr} WHERE {col} IS NULL OR {col}=''")

    # 复习列是特例：next_review 要按「收藏时间 + 1 天」补，让存量素材自然进入复习周期
    if "next_review" in added:
        conn.execute(
            "UPDATE favorites SET next_review=date(created_at,'+1 day') WHERE next_review IS NULL"
        )

    # ---- 5) NULL 归属 → 未认领桶 ----
    # 老库重建 articles/study_log 时新表的 user_id 是空的；favorites/events 补列也一样。
    # 统一写成 0 之后 UNIQUE(user_id,date) 才真正生效，本地自用模式也能查到这批数据。
    # 等第一个账号建好，由 users.claim_orphan_data() 整体划给它。
    for _t in ("articles", "study_log", "favorites", "events"):
        conn.execute(f"UPDATE {_t} SET user_id=? WHERE user_id IS NULL", (UNCLAIMED,))

    # ---- 6) 词汇默认「不认识」（2026-10-11 用户口径）----
    # 以前 mastery=0 表示「未标记」，界面上是一堆没态度的条目；现在默认就该是不认识，
    # 由用户自己去点「已掌握」。0 不再是合法状态，所以把存量 0 一次性并到 1。
    # 幂等：再跑一次也只会命中 0，不会有副作用。
    conn.execute("UPDATE vocab SET mastery=1 WHERE mastery IS NULL OR mastery=0")


_CENTRAL_TABLES = {"users", "sessions", "profile"}
_TABLE_REF_RE = re.compile(
    r"\b(?:(?:FROM|JOIN|INTO|TABLE)\s+|UPDATE\s+(?!SET\b))"
    r"[`\"]?([A-Za-z_][A-Za-z0-9_]*)",
    re.I,
)


def user_data_dir(user_id: int) -> Path:
    """返回该账号的数据目录；目录名不使用显示名，避免路径注入。"""
    with connect(central=True) as conn:
        row = conn.execute("SELECT username FROM users WHERE id=?", (int(user_id),)).fetchone()
    username = str(row["username"] if row else f"user{int(user_id)}")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", username).strip("._-") or f"user{int(user_id)}"
    return settings.db_path.parent / "users" / f"{int(user_id)}_{safe}"


def _sql_uses_central_tables(sql: str) -> bool:
    """账号表只存在于中心库；学习表只存在于当前用户库。"""
    refs = {name.lower() for name in _TABLE_REF_RE.findall(sql)}
    if refs & _CENTRAL_TABLES and refs - _CENTRAL_TABLES:
        raise ValueError("SQL 不能跨中心账号库和个人学习库查询")
    return bool(refs & _CENTRAL_TABLES)


def connect(*, central: bool | None = None) -> sqlite3.Connection:
    uid = current_user_id(required=False)
    if central is None:
        central = uid in (None, ALL_USERS, UNCLAIMED)
    path = settings.db_path if central else user_data_dir(int(uid)) / "learning.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if is_new and not central:
        conn.executescript(SCHEMA)
        _migrate(conn)
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


_USER_OWNED_TABLES = (
    "articles", "study_log", "events", "favorites", "annotations",
    "user_profile", "math_problems", "math_review_log",
)
_ARTICLE_CHILD_TABLES = ("vocab", "sentences", "grammar", "writing_items", "imitation_tasks")
_initialized_user_dbs: dict[int, Path] = {}


def ensure_user_db(user_id: int) -> Path:
    """确保个人学习库存在且已升级到当前表结构。"""
    path = user_data_dir(user_id) / "learning.db"
    if _initialized_user_dbs.get(int(user_id)) == path and path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
    _initialized_user_dbs[int(user_id)] = path
    return path


def claim_unclaimed_data(user_id: int) -> dict[str, int]:
    """将中心库的 0 号桶搬进首个账号的个人库，校验后才清理原记录。"""
    uid = int(user_id)
    ensure_user_db(uid)
    with connect(central=True) as source:
        old_images = settings.db_path.parent / "math_images" / str(UNCLAIMED)
        has_data = any(source.execute(f"SELECT 1 FROM {table} WHERE user_id=? LIMIT 1", (UNCLAIMED,)).fetchone()
                       for table in _USER_OWNED_TABLES)
        has_data = has_data or bool(source.execute(
            "SELECT 1 FROM articles WHERE user_id=? LIMIT 1", (UNCLAIMED,)
        ).fetchone())
        if has_data or (old_images.exists() and any(old_images.iterdir())):
            backup_dir = _backup_central("pre_claim")
            if old_images.exists():
                shutil.copytree(old_images, backup_dir / "math_images_0")
        target_path = user_data_dir(uid) / "learning.db"
        with sqlite3.connect(target_path, timeout=30) as target:
            target.row_factory = sqlite3.Row
            target.executescript(SCHEMA)
            _migrate(target)
            counts = _copy_user_data(source, target, UNCLAIMED, uid)
            if old_images.exists():
                image_dir = user_data_dir(uid) / "math_images"
                image_dir.mkdir(parents=True, exist_ok=True)
                _copy_images_verified(old_images, image_dir)
                for row in target.execute("SELECT id,image_path FROM math_problems WHERE user_id=?", (uid,)):
                    prefix = f"math_images/{UNCLAIMED}/"
                    if (row["image_path"] or "").startswith(prefix):
                        target.execute(
                            "UPDATE math_problems SET image_path=? WHERE id=?",
                            (_user_image_rel(uid, row["image_path"][len(prefix):]), row["id"]),
                        )
        with source:
            source.execute("DELETE FROM submissions WHERE task_id IN (SELECT id FROM imitation_tasks WHERE article_id IN (SELECT id FROM articles WHERE user_id=0))")
            for table in (*_ARTICLE_CHILD_TABLES, *_USER_OWNED_TABLES):
                if table in _ARTICLE_CHILD_TABLES:
                    source.execute(f"DELETE FROM {table} WHERE article_id IN (SELECT id FROM articles WHERE user_id=0)")
                elif table == "submissions":
                    continue
                else:
                    source.execute(f"DELETE FROM {table} WHERE user_id=0")
    return counts


def _user_image_rel(user_id: int, filename: str) -> str:
    rel_dir = user_data_dir(user_id).relative_to(settings.db_path.parent).as_posix()
    return f"{rel_dir}/math_images/{filename}"


def _backup_central(prefix: str) -> Path:
    backup_dir = settings.db_path.parent / "backups" / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    with connect(central=True) as source:
        with sqlite3.connect(backup_dir / settings.db_path.name) as backup:
            source.backup(backup)
            result = backup.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite 备份校验失败：{result}")
    return backup_dir


def _copy_images_verified(source_dir: Path, target_dir: Path) -> int:
    """复制图片并逐个核对字节；出错时让调用方中止数据库清理。"""
    copied = 0
    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(source_dir)
        target = target_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if source.stat().st_size != target.stat().st_size or source.read_bytes() != target.read_bytes():
            raise RuntimeError(f"图片迁移校验失败：{source}")
        copied += 1
    return copied


def _copy_owned_rows(source: sqlite3.Connection, target: sqlite3.Connection,
                     table: str, owner_id: int, target_uid: int) -> int:
    rows = source.execute(f"SELECT * FROM {table} WHERE user_id=?", (owner_id,)).fetchall()
    if not rows:
        return 0
    cols = [r["name"] for r in target.execute(f"PRAGMA table_info({table})")]
    names = [name for name in rows[0].keys() if name in cols]
    marks = ",".join("?" for _ in names)
    sql = f"INSERT OR IGNORE INTO {table}({','.join(names)}) VALUES({marks})"
    for row in rows:
        expected = dict(row)
        expected["user_id"] = target_uid
        if table == "math_problems":
            prefix = f"math_images/{owner_id}/"
            image_path = expected.get("image_path") or ""
            if image_path.startswith(prefix):
                expected["image_path"] = _user_image_rel(
                    target_uid, image_path[len(prefix):]
                )
        target.execute(sql, tuple(expected[name] for name in names))
        key_cols = ["user_id", "key"] if table == "user_profile" else ["id"]
        key_values = [expected[k] for k in key_cols]
        got = target.execute(
            f"SELECT {','.join(names)} FROM {table} WHERE "
            + " AND ".join(f"{key}= ?" for key in key_cols), key_values,
        ).fetchone()
        if got is None or dict(got) != {name: expected[name] for name in names}:
            raise RuntimeError(f"迁移校验失败：{table} 中的记录 {key_values}")
    return len(rows)


def _copy_user_data(source: sqlite3.Connection, target: sqlite3.Connection,
                    source_uid: int, target_uid: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    article_ids = [r[0] for r in source.execute(
        "SELECT id FROM articles WHERE user_id=?", (source_uid,)
    )]
    problem_ids = [r[0] for r in source.execute(
        "SELECT id FROM math_problems WHERE user_id=?", (source_uid,)
    )]
    for table in _USER_OWNED_TABLES:
        counts[table] = _copy_owned_rows(source, target, table, source_uid, target_uid)
    if article_ids:
        marks = ",".join("?" for _ in article_ids)
        for table in _ARTICLE_CHILD_TABLES:
            rows = source.execute(f"SELECT * FROM {table} WHERE article_id IN ({marks})", article_ids).fetchall()
            cols = [r["name"] for r in target.execute(f"PRAGMA table_info({table})")]
            names = [name for name in rows[0].keys() if name in cols] if rows else []
            if names:
                insert = f"INSERT OR IGNORE INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})"
                for row in rows:
                    target.execute(insert, tuple(row[name] for name in names))
                for row in rows:
                    key = row["id"]
                    got = target.execute(f"SELECT {','.join(names)} FROM {table} WHERE id=?", (key,)).fetchone()
                    if got is None or dict(got) != dict(row):
                        raise RuntimeError(f"迁移校验失败：{table} 中的记录 {key}")
            counts[table] = len(rows)
        task_ids = [r[0] for r in source.execute(
            f"SELECT id FROM imitation_tasks WHERE article_id IN ({marks})", article_ids
        )]
        if task_ids:
            task_marks = ",".join("?" for _ in task_ids)
            rows = source.execute(f"SELECT * FROM submissions WHERE task_id IN ({task_marks})", task_ids).fetchall()
            cols = [r["name"] for r in target.execute("PRAGMA table_info(submissions)")]
            names = [name for name in rows[0].keys() if name in cols] if rows else []
            if names:
                insert = f"INSERT OR IGNORE INTO submissions({','.join(names)}) VALUES({','.join('?' for _ in names)})"
                for row in rows:
                    target.execute(insert, tuple(row[name] for name in names))
                for row in rows:
                    got = target.execute(f"SELECT {','.join(names)} FROM submissions WHERE id=?", (row["id"],)).fetchone()
                    if got is None or dict(got) != dict(row):
                        raise RuntimeError(f"迁移校验失败：submissions 中的记录 {row['id']}")
            counts["submissions"] = len(rows)
        else:
            counts.update({"submissions": 0})
    else:
        counts.update({name: 0 for name in (*_ARTICLE_CHILD_TABLES, "submissions")})
    if problem_ids:
        marks = ",".join("?" for _ in problem_ids)
        rows = source.execute(f"SELECT * FROM math_review_log WHERE problem_id IN ({marks}) AND user_id=?",
                              (*problem_ids, source_uid)).fetchall()
        # math_review_log is already copied from the user_id tables above.
        counts["math_review_log"] = len(rows)
    return counts


def migrate_legacy_user_data() -> dict[int, dict[str, int]]:
    """备份并把中心库中各账号的学习数据幂等拆分到个人库。"""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(central=True) as source:
        users_rows = source.execute("SELECT id,username FROM users WHERE id>0 ORDER BY id").fetchall()
        if not users_rows:
            return {}
        pending = any(source.execute(
            f"SELECT 1 FROM {table} WHERE user_id>0 LIMIT 1"
        ).fetchone() for table in _USER_OWNED_TABLES)
        if not pending:
            return {}
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = _backup_central("pre_user_split")
        old_images = settings.db_path.parent / "math_images"
        if old_images.exists():
            shutil.copytree(old_images, backup_dir / "math_images")
        result: dict[int, dict[str, int]] = {}
        for user in users_rows:
            uid = int(user["id"])
            with as_user(uid):
                target_path = user_data_dir(uid) / "learning.db"
                with sqlite3.connect(target_path, timeout=30) as target:
                    target.row_factory = sqlite3.Row
                    target.execute("PRAGMA foreign_keys=ON")
                    target.executescript(SCHEMA)
                    _migrate(target)
                    result[uid] = _copy_user_data(source, target, uid, uid)
                old_user_images = old_images / str(uid)
                if old_user_images.exists():
                    new_user_images = user_data_dir(uid) / "math_images"
                    new_user_images.mkdir(parents=True, exist_ok=True)
                    _copy_images_verified(old_user_images, new_user_images)
        # 只有所有用户的复制和记录逐条校验都成功后，才从中心库清理重复行。
        with source:
            for table in ("submissions", *_ARTICLE_CHILD_TABLES, *_USER_OWNED_TABLES):
                if table in _ARTICLE_CHILD_TABLES or table == "submissions":
                    source.execute(
                        f"DELETE FROM {table} WHERE " +
                        ("article_id IN (SELECT id FROM articles WHERE user_id>0)" if table != "submissions"
                         else "task_id IN (SELECT id FROM imitation_tasks WHERE article_id IN "
                              "(SELECT id FROM articles WHERE user_id>0))")
                    )
                else:
                    source.execute(f"DELETE FROM {table} WHERE user_id>0")
    return result


# ------------------------------------------------------------
# 底层执行（不带隔离逻辑的地方用；业务读写请用下面的封装）
# ------------------------------------------------------------
def query(sql: str, args: tuple = ()) -> list[dict]:
    central = _sql_uses_central_tables(sql)
    with connect(central=True if central else None) as conn:
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    central = _sql_uses_central_tables(sql)
    with connect(central=True if central else None) as conn:
        cur = conn.execute(sql, args)
        return cur.lastrowid


def executemany(sql: str, seq: list[tuple]) -> None:
    central = _sql_uses_central_tables(sql)
    with connect(central=True if central else None) as conn:
        conn.executemany(sql, seq)


# ------------------------------------------------------------
# 文章
# ------------------------------------------------------------
def get_article_by_date(d: str) -> dict | None:
    w, a = scope()
    return query_one(f"SELECT * FROM articles WHERE date=? AND {w}", (d, *a))


def get_article(article_id: int) -> dict | None:
    w, a = scope()
    return query_one(f"SELECT * FROM articles WHERE id=? AND {w}", (int(article_id), *a))


def article_exists(d: str) -> bool:
    return get_article_by_date(d) is not None


def latest_article() -> dict | None:
    w, a = scope()
    return query_one(f"SELECT * FROM articles WHERE {w} ORDER BY date DESC LIMIT 1", a)


def recent_articles(limit: int = 14) -> list[dict]:
    """每日内容的历史列表。**刻意排除真题**：真题有自己的入口，
    不该混进「某月某日学了什么」的时间线里。"""
    w, a = scope()
    return query(
        f"SELECT date,title,topic,difficulty,word_count,source_type FROM articles "
        f"WHERE {w} AND COALESCE(source_type,'ai') != 'exam' "
        f"ORDER BY date DESC LIMIT ?",
        (*a, limit),
    )


def exam_articles() -> list[dict]:
    w, a = scope()
    return query(
        f"SELECT id,date,title,source,topic,difficulty,word_count,est_minutes,created_at, "
        f"(SELECT COUNT(*) FROM vocab v WHERE v.article_id=articles.id) AS vocab_count, "
        f"(SELECT COUNT(*) FROM sentences s WHERE s.article_id=articles.id) AS sent_count "
        f"FROM articles WHERE source_type='exam' AND {w} ORDER BY date DESC, id DESC",
        a,
    )


def get_exam_article(exam_id: int) -> dict | None:
    w, a = scope()
    return query_one(
        f"SELECT * FROM articles WHERE id=? AND source_type='exam' AND {w}", (int(exam_id), *a)
    )


# ------------------------------------------------------------
# 事件流
# ------------------------------------------------------------
def log_event(kind: str, ref_id: int | None = None, payload: dict | None = None) -> None:
    execute(
        "INSERT INTO events(user_id,kind,ref_id,payload) VALUES(?,?,?,?)",
        (owner_id(), kind, ref_id, json.dumps(payload or {}, ensure_ascii=False)),
    )


# ------------------------------------------------------------
# 学习记录
# ------------------------------------------------------------
def touch_study_log(d: str, article_id: int | None = None, **fields) -> None:
    """upsert 当天学习记录。"""
    uid = require_user()
    execute(
        "INSERT OR IGNORE INTO study_log(user_id,date,article_id) VALUES(?,?,?)",
        (uid, d, article_id),
    )
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(
        f"UPDATE study_log SET {sets}, updated_at=datetime('now','localtime') "
        f"WHERE user_id=? AND date=?",
        (*fields.values(), uid, d),
    )


def bump_study_log(d: str, key: str, delta: int = 1) -> None:
    uid = require_user()
    execute(
        f"UPDATE study_log SET {key}=COALESCE({key},0)+?, "
        f"updated_at=datetime('now','localtime') WHERE user_id=? AND date=?",
        (delta, uid, d),
    )


def get_study_log(d: str) -> dict | None:
    w, a = scope()
    return query_one(f"SELECT * FROM study_log WHERE date=? AND {w}", (d, *a))


def streak_days() -> int:
    """连续学习天数（按 study_log 中 analysis_opened=1 或 有阅读记录 计算）。"""
    w, a = scope()
    rows = query(
        f"SELECT date FROM study_log WHERE {w} AND (read_seconds>0 OR analysis_opened=1) "
        f"ORDER BY date DESC LIMIT 400",
        a,
    )
    dates = {r["date"] for r in rows}
    n = 0
    cur = date.today()
    while cur.isoformat() in dates:
        n += 1
        cur -= timedelta(days=1)
    return n


# ------------------------------------------------------------
# 词汇 / 语法（通过 articles 判归属）
# ------------------------------------------------------------
def get_vocab(vocab_id: int) -> dict | None:
    w, a = scope("a.user_id")
    return query_one(
        f"SELECT v.*, a.user_id AS _uid, a.date AS _date, a.source_type AS _source_type "
        f"FROM vocab v JOIN articles a ON a.id=v.article_id WHERE v.id=? AND {w}",
        (int(vocab_id), *a),
    )


def weak_words(limit: int = 40) -> list[dict]:
    """反复不认识的词：按 mastery 低 + 出现次数高排序。"""
    w, a = scope("a.user_id")
    return query(
        f"""SELECT v.word, MAX(v.mastery) AS mastery, SUM(v.occurrence) AS occ
            FROM vocab v JOIN articles a ON a.id=v.article_id
            WHERE {w}
            GROUP BY v.word
            HAVING mastery IN (1,2)
            ORDER BY occ DESC, mastery ASC LIMIT ?""",
        (*a, limit),
    )


def active_grammar_weak(limit: int = 6) -> list[dict]:
    """长期出现的语法点（用于个性化复现）。"""
    w, a = scope("a.user_id")
    return query(
        f"""SELECT g.name, COUNT(*) AS c FROM grammar g JOIN articles a ON a.id=g.article_id
            WHERE {w}
            GROUP BY g.name ORDER BY c DESC LIMIT ?""",
        (*a, limit),
    )


# ------------------------------------------------------------
# 复习（间隔重复，SM-2 的极简版）
#
# 设计取舍：
#   - 不做「难度系数」那套。间隔序列是固定的 [1,2,4,7,15,30,60] 天，
#     记得越多次间隔越长，忘一次就退回 1 天。够用，且用户一眼能看懂。
#   - 收藏即入复习节奏（next_review = 收藏次日），不需要用户额外开启。
#   - 「不再复习」= next_review 置空串，这条素材就退出循环（素材还在，只是不提醒）。
# ------------------------------------------------------------
REVIEW_INTERVALS = [1, 2, 4, 7, 15, 30, 60]

# 走完整条间隔阶梯（1→2→4→7→15→30→60 天）视为「已巩固」。
# 注意：第 n 次「记住了」用的是 REVIEW_INTERVALS[n-1]，所以 rc=7 才落到 60 天档。
MASTERED_AT = len(REVIEW_INTERVALS)

_NOT_IN_CYCLE = "next_review IS NULL OR next_review=''"


def _load_tags(rows: list[dict]) -> list[dict]:
    for r in rows:
        try:
            r["tags"] = json.loads(r.get("tags") or "[]")
        except Exception:  # noqa: BLE001
            r["tags"] = []
    return rows


def review_due(limit: int = 200) -> list[dict]:
    """今天（含逾期）该复习的素材，最久没碰的排前面。"""
    w, a = scope()
    rows = query(
        f"SELECT * FROM favorites WHERE {w} AND NOT ({_NOT_IN_CYCLE}) AND next_review<=? "
        f"ORDER BY next_review ASC, id ASC LIMIT ?",
        (*a, today_str(), limit),
    )
    return _load_tags(rows)


def review_stats() -> dict:
    today = today_str()
    w, a = scope()
    due = query_one(
        f"SELECT COUNT(*) c FROM favorites WHERE {w} AND NOT ({_NOT_IN_CYCLE}) AND next_review<=?",
        (*a, today),
    )
    in_cycle = query_one(f"SELECT COUNT(*) c FROM favorites WHERE {w} AND NOT ({_NOT_IN_CYCLE})", a)
    mastered = query_one(
        f"SELECT COUNT(*) c FROM favorites WHERE {w} AND NOT ({_NOT_IN_CYCLE}) AND review_count>=?",
        (*a, MASTERED_AT),
    )
    total = query_one(f"SELECT COUNT(*) c FROM favorites WHERE {w}", a)
    return {
        "due": due["c"] if due else 0,
        "in_cycle": in_cycle["c"] if in_cycle else 0,
        "mastered": mastered["c"] if mastered else 0,
        "total": total["c"] if total else 0,
        "today": today,
    }


def review_apply(fid: int, result: str) -> dict | None:
    """推进一条素材的复习进度。返回更新后的行；素材不存在/不属于自己时返回 None。

    result：
      known  记住了  → 间隔往后走一档
      forgot 忘记了  → 退回 1 天后重来
      skip   稍后再说 → 推到明天，进度不变
      drop   不再复习 → 退出循环（素材保留）
      readd  重新加入 → 回到 1 天后的节奏
    """
    row = get_favorite(fid)
    if not row:
        return None
    uid = require_user()

    today = date.today()
    rc = row.get("review_count") or 0

    if result == "forgot":
        rc, nxt = 0, (today + timedelta(days=1)).isoformat()
    elif result == "known":
        rc = rc + 1
        interval = REVIEW_INTERVALS[min(rc - 1, len(REVIEW_INTERVALS) - 1)]
        nxt = (today + timedelta(days=interval)).isoformat()
    elif result == "skip":
        nxt = (today + timedelta(days=1)).isoformat()
    elif result == "drop":
        nxt = ""
    elif result == "readd":
        rc, nxt = 0, (today + timedelta(days=1)).isoformat()
    else:
        raise ValueError(f"未知的复习结果：{result}")

    execute(
        "UPDATE favorites SET review_count=?, next_review=?, last_result=?, "
        "updated_at=datetime('now','localtime') WHERE id=? AND user_id=?",
        (rc, nxt, result, fid, uid),
    )
    log_event(f"review_{result}", fid, {"count": rc, "next": nxt})
    return get_favorite(fid)


# ------------------------------------------------------------
# 统计
# ------------------------------------------------------------
def weekly_stats(days: int = 7) -> dict:
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    wl, al = scope()
    wa, aa = scope("a.user_id")
    logs = query(
        f"SELECT * FROM study_log WHERE {wl} AND date>=? ORDER BY date", (*al, since)
    )
    arts = query(
        f"SELECT COUNT(*) c, COALESCE(SUM(word_count),0) w FROM articles "
        f"WHERE {wl} AND date>=? AND COALESCE(source_type,'ai') != 'exam'",
        (*al, since),
    )
    v_total = query_one(
        f"""SELECT COUNT(*) c FROM vocab v JOIN articles a ON a.id=v.article_id
            WHERE {wa} AND a.date>=?""",
        (*aa, since),
    )
    v_unknown = query_one(
        f"""SELECT COUNT(*) c FROM vocab v JOIN articles a ON a.id=v.article_id
            WHERE {wa} AND a.date>=? AND v.mastery IN (1,2)""",
        (*aa, since),
    )
    v_known = query_one(
        f"""SELECT COUNT(*) c FROM vocab v JOIN articles a ON a.id=v.article_id
            WHERE {wa} AND a.date>=? AND v.mastery=3""",
        (*aa, since),
    )
    sents = query_one(
        f"""SELECT COUNT(*) c FROM sentences s JOIN articles a ON a.id=s.article_id
            WHERE {wa} AND a.date>=?""",
        (*aa, since),
    )
    grams = query(
        f"""SELECT g.name, COUNT(*) c FROM grammar g JOIN articles a ON a.id=g.article_id
            WHERE {wa} AND a.date>=? GROUP BY g.name ORDER BY c DESC LIMIT 5""",
        (*aa, since),
    )
    wr = query_one(
        f"""SELECT COUNT(*) c, COALESCE(AVG(sub.score),0) s FROM submissions sub
            JOIN imitation_tasks t ON t.id=sub.task_id
            JOIN articles a ON a.id=t.article_id WHERE {wa} AND a.date>=?""",
        (*aa, since),
    )
    favs = query_one(f"SELECT COUNT(*) c FROM favorites WHERE {wl}", al)
    return {
        "days": days,
        "articles": arts[0]["c"],
        "words_read": arts[0]["w"],
        "read_minutes": round(sum((l["read_seconds"] or 0) for l in logs) / 60),
        "new_vocab": v_total["c"] if v_total else 0,
        "unknown_vocab": v_unknown["c"] if v_unknown else 0,
        "known_vocab": v_known["c"] if v_known else 0,
        "sentences": sents["c"] if sents else 0,
        "grammar_top": grams,
        "writing_attempts": wr["c"] if wr else 0,
        "writing_avg_score": round(wr["s"], 1) if wr and wr["s"] else 0,
        "favorites_total": favs["c"] if favs else 0,
        "active_days": len(logs),
        "streak": streak_days(),
        "logs": logs,
        # 积累闭环 ①：素材使用率。**全量口径**，不随 days 变 —— 界面上单独一组，别混进周统计
        "material_usage": usage_stats(),
        # 顺手给学习数据页一份"已巩固但一直没用过"的清单（最多 8 条），页面才可行动
        "material_unused": mastered_unused(8),
    }


# ------------------------------------------------------------
# 素材库
# ------------------------------------------------------------
def get_favorite(fid: int) -> dict | None:
    w, a = scope()
    return query_one(f"SELECT * FROM favorites WHERE id=? AND {w}", (int(fid), *a))


def favorites_all(
    limit: int = 500,
    *,
    library: str | None = None,
    position: str | None = None,
    category: str | None = None,
    genre: str | None = None,
) -> list[dict]:
    """素材库列表（tags 已解析为数组）。"""
    w, a = scope()
    sql = f"SELECT * FROM favorites WHERE {w}"
    args: list = list(a)
    if library:
        sql += " AND library=?"
        args.append(library)
    if position:
        sql += " AND position=?"
        args.append(position)
    if category:
        sql += " AND category=?"
        args.append(category)
    if genre:
        sql += " AND genre=?"
        args.append(genre)
    sql += " ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT ?"
    args.append(limit)
    return _load_tags(query(sql, tuple(args)))


def favorites_summary() -> dict:
    """素材库统计：按库 / 位置 / 文体 / 类别 / 等级 分组计数（基于本人的全量，不受筛选影响）。"""
    w, a = scope()
    rows = query(
        f"SELECT library, position, category, genre, level, auto FROM favorites WHERE {w}", a
    )
    by_library: dict[str, int] = {}
    by_position: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_genre: dict[str, int] = {}
    by_level: dict[str, int] = {}
    auto_n = 0
    for r in rows:
        lib = r["library"] or "reading"
        pos = r["position"] or "universal"
        cat = r["category"] or "通用表达"
        gen = r["genre"] or "universal"
        lv = r["level"] or "B"
        by_library[lib] = by_library.get(lib, 0) + 1
        by_position[pos] = by_position.get(pos, 0) + 1
        by_category[cat] = by_category.get(cat, 0) + 1
        by_genre[gen] = by_genre.get(gen, 0) + 1
        by_level[lv] = by_level.get(lv, 0) + 1
        if r["auto"]:
            auto_n += 1
    return {
        "total": len(rows),
        "by_library": by_library,
        "by_position": by_position,
        "by_category": by_category,
        "by_genre": by_genre,
        "by_level": by_level,
        "auto_classified": auto_n,
    }


# ------------------------------------------------------------
# 素材使用率（积累闭环 ①：批改点名）—— 10-07 新增
# ------------------------------------------------------------
#   要解决的问题：积累库攒了一堆表达，但「到底用没用上」从来没被统计过。
#   攒了不用 = 白攒。所以批改完作文后，把作文正文和积累库做**本地字符串比对**：
#     · 命中的素材 → used_count +1、刷 last_used_at，界面报「这次用上了」
#     · 「已巩固但一次没用过」的 → 挑 N 条提示「这些可以考虑用上」
#   刻意**不额外调模型**：本地比对免费、稳定、可回归；比让 AI 自己数更可靠。
#   「已巩固」的判定与复习队列**同源**（都用 MASTERED_AT），两边不会各说一套。
# ------------------------------------------------------------
_MATCH_MIN_LEN = 4                       # 太短的素材（如 "in"）不参与匹配，否则满是误报
_ASCII_WORD = re.compile(r"[a-z]{3}")     # 至少含一个 3 字母英文词才认为是英文素材
_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


def _norm_for_match(s: str) -> str:
    """归一化：统一各种引号 / 破折号、压掉多余空白、转小写、剥掉首尾标点。"""
    if not s:
        return ""
    t = (s.replace("\u2019", "'").replace("\u2018", "'")
           .replace("\u201c", '"').replace("\u201d", '"')
           .replace("\u2014", "-").replace("\u2013", "-"))
    t = re.sub(r"\s+", " ", t.lower())
    return t.strip(" \t\r\n.,;:!?\"'()[]")


@lru_cache(maxsize=4096)
def _pattern_for(content: str):
    """把素材编译成「容忍词形变化」的匹配式；不适合匹配的返回 None。

    ⚠️ 为什么不能只做 `content in 作文` 的子串判断：
       素材存的是词典形（`play a crucial role in`），学生写出来是 plays / played ——
       子串判定会漏掉，然后界面还告诉他「这条你一次都没用过」，属**假否定**，最伤信任。
    做法：按非字母数字切成词，词之间允许任意分隔符，每个词尾允许少量后缀；
       长度 ≥4 的词允许 0~3 个字母后缀（play→plays / played / playing），
       短词（in / the / a）只允许 0~1 个，免得 "in" 把 "into" 也算进来。
    结果缓存在 lru_cache 里：每次批改要对整个素材库建式，不能每次重编译。
    """
    k = _norm_for_match(content)
    if len(k) < _MATCH_MIN_LEN or not _ASCII_WORD.search(k):
        return None
    toks = [t for t in _TOKEN_SPLIT.split(k) if t]
    if not toks:
        return None
    body = r"[\W_]+".join(
        re.escape(t) + (r"\w{0,3}" if len(t) >= 4 else r"\w{0,1}") for t in toks
    )
    return re.compile(r"(?<!\w)" + body + r"(?!\w)")


def _matchable(content: str) -> bool:
    """这条素材能不能参与匹配（太短 / 纯中文的不参与）。"""
    return bool(_pattern_for(content or ""))


def materials_matched(text: str) -> list[dict]:
    """这次写作里**真的用上**了哪些已积累素材（长的排前面，长的更具体）。"""
    norm = _norm_for_match(text)
    if not norm:
        return []
    hits = []
    for r in _load_tags(favorites_all(limit=5000)):
        pat = _pattern_for(r.get("content") or "")
        if pat is not None and pat.search(norm):
            hits.append(r)
    hits.sort(key=lambda r: len(r.get("content") or ""), reverse=True)
    return hits


def mark_materials_used(fids: list[int]) -> int:
    """把命中的素材记为「用过」：used_count +1、刷 last_used_at。返回改动条数。"""
    if not fids:
        return 0
    w, a = scope()
    n = 0
    for fid in fids:
        cur = query_one(f"SELECT used_count FROM favorites WHERE id=? AND {w}", (int(fid), *a))
        if not cur:
            continue
        execute(
            "UPDATE favorites SET used_count=?, last_used_at=datetime('now','localtime'), "
            f"updated_at=datetime('now','localtime') WHERE id=? AND {w}",
            ((cur.get("used_count") or 0) + 1, int(fid), *a),
        )
        n += 1
    return n


def mastered_unused(limit: int = 5, exclude: set | None = None) -> list[dict]:
    """「已巩固」但一次都没用过的素材 —— 批改时点名提示用。"""
    w, a = scope()
    rows = _load_tags(query(
        f"SELECT * FROM favorites WHERE {w} AND NOT ({_NOT_IN_CYCLE}) "
        f"AND review_count>=? AND COALESCE(used_count,0)=0",
        (*a, MASTERED_AT),
    ))
    if exclude:
        rows = [r for r in rows if r["id"] not in exclude]
    rows.sort(key=lambda r: (-(r.get("review_count") or 0), r["id"]))
    return rows[:limit] if limit else rows


def usage_stats() -> dict:
    """素材使用率：分母 =「已巩固」（与复习队列同源），分子 = 其中真正用过的。"""
    w, a = scope()
    cyc = f"NOT ({_NOT_IN_CYCLE})"

    def _count(extra: str = "", args: tuple = ()) -> int:
        r = query_one(f"SELECT COUNT(*) c FROM favorites WHERE {w} AND {cyc} {extra}", args)
        return r["c"] if r else 0

    mastered = _count("AND review_count>=?", (*a, MASTERED_AT))
    mastered_used = _count("AND review_count>=? AND COALESCE(used_count,0)>0", (*a, MASTERED_AT))
    total = query_one(f"SELECT COUNT(*) c FROM favorites WHERE {w}", a)
    used_any = query_one(
        f"SELECT COUNT(*) c FROM favorites WHERE {w} AND COALESCE(used_count,0)>0", a
    )
    return {
        "mastered": mastered,
        "mastered_used": mastered_used,
        "mastered_unused": max(0, mastered - mastered_used),
        "total": total["c"] if total else 0,
        "used_any": used_any["c"] if used_any else 0,
        "mastered_ratio": round(mastered_used / mastered * 100) if mastered else 0,
    }


def annotate_material_usage(text: str, unused_limit: int = 5) -> dict:
    """批改收尾：算「这次用上了哪些 / 哪些一直没用过」，并落 used_count。

    返回值直接挂到批改反馈 fb 上（前端 renderFeedback 渲染成两个名单）。
    """
    hits = materials_matched(text)
    mark_materials_used([h["id"] for h in hits])
    hit_ids = {h["id"] for h in hits}
    used = [
        {
            "id": h["id"],
            "content": h["content"],
            "cn": h.get("cn") or "",
            "position": h.get("position") or "universal",
            "review_count": h.get("review_count") or 0,
            "used_count": (h.get("used_count") or 0) + 1,
        }
        for h in hits
    ]
    unused = [
        {
            "id": r["id"],
            "content": r["content"],
            "cn": r.get("cn") or "",
            "position": r.get("position") or "universal",
            "usage": r.get("usage") or "",
            "review_count": r.get("review_count") or 0,
        }
        for r in mastered_unused(unused_limit, exclude=hit_ids)
    ]
    return {"used": used, "unused": unused, "stats": usage_stats()}


# ------------------------------------------------------------
# 仿写任务归属
# ------------------------------------------------------------
def get_task(task_id: int) -> dict | None:
    w, a = scope("a.user_id")
    return query_one(
        f"""SELECT t.* FROM imitation_tasks t JOIN articles a ON a.id=t.article_id
            WHERE t.id=? AND {w}""",
        (int(task_id), *a),
    )


# ------------------------------------------------------------
# 全局设置 / 用户偏好
# ------------------------------------------------------------
def get_profile(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM profile WHERE key=?", (key,))
    return row["value"] if row else default


def set_profile(key: str, value: str) -> None:
    execute(
        "INSERT INTO profile(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def get_user_pref(key: str, default: str = "") -> str:
    uid = current_user_id(required=False)
    if uid in (None, ALL_USERS):
        return default
    row = query_one("SELECT value FROM user_profile WHERE user_id=? AND key=?", (uid, key))
    return row["value"] if row else default


def set_user_pref(key: str, value: str) -> None:
    uid = require_user()
    execute(
        """INSERT INTO user_profile(user_id,key,value) VALUES(?,?,?)
           ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value""",
        (uid, key, str(value)),
    )


def user_prefs() -> dict:
    uid = current_user_id(required=False)
    if uid in (None, ALL_USERS):
        return {}
    return {r["key"]: r["value"] for r in query("SELECT key,value FROM user_profile WHERE user_id=?", (uid,))}


def today_str() -> str:
    return date.today().isoformat()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------
# 手写批注
#
# 每「用户 + 文档 + 区块」一行：doc_key = "<文档前缀>:<区块名>"，
# 例如 "2026-10-05:body" / "exam-2010-text2:trans"。
#
# 为什么用「前缀:名称」这种扁平字符串，而不是 (article_id, area) 两列：
#   前端换一篇内容时只要一次 LIKE 就能把整篇的批注全捞回来（少一轮往返），
#   而且真题（伪日期 exam-2010-text2）和每日文章走的是完全相同的代码路径。
# 前缀一定不含 ':'，所以 "前缀:%" 不会误伤别人。
# ------------------------------------------------------------
def _like_escape(s: str) -> str:
    """转义 LIKE 通配符，免得文档前缀里的 % _ 把别人的批注也捞进来。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def annotations_for(prefix: str) -> dict[str, dict]:
    """取当前用户在某篇文档下的全部批注：{doc_key: data}。

    前缀为空时返回空字典 —— 宁可什么都不给，也不要变成"把全库倒出来"。
    """
    p = (prefix or "").strip()
    if not p:
        return {}
    w, a = scope()
    rows = query(
        f"SELECT doc_key, data FROM annotations WHERE doc_key LIKE ? ESCAPE '\\' AND {w}",
        (_like_escape(p) + ":%", *a),
    )
    out: dict[str, dict] = {}
    for r in rows:
        try:
            out[r["doc_key"]] = json.loads(r["data"] or "{}")
        except (TypeError, ValueError):
            out[r["doc_key"]] = {}
    return out


def save_annotation(doc_key: str, data: dict) -> None:
    """写一条批注。笔迹被擦空时直接删行，别让表里堆一堆没内容的空记录。"""
    key = (doc_key or "").strip()
    if not key or ":" not in key:
        raise ValueError("批注 key 必须是「文档前缀:区块名」")
    uid = require_user()
    strokes = (data or {}).get("strokes") or []
    if not strokes:
        execute("DELETE FROM annotations WHERE user_id=? AND doc_key=?", (uid, key))
        return
    payload = json.dumps(data, ensure_ascii=False)
    execute(
        "INSERT INTO annotations(user_id, doc_key, data, updated_at) "
        "VALUES(?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(user_id, doc_key) DO UPDATE SET "
        "data=excluded.data, updated_at=excluded.updated_at",
        (uid, key, payload),
    )


def clear_annotations(prefix: str) -> int:
    """清掉某篇文档的全部批注（暂未接界面入口，留给「重置这篇」用）。"""
    p = (prefix or "").strip()
    if not p:
        return 0
    uid = require_user()
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM annotations WHERE user_id=? AND doc_key LIKE ? ESCAPE '\\'",
            (uid, _like_escape(p) + ":%"),
        )
        return cur.rowcount
