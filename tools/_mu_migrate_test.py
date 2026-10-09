"""验证多用户迁移：老库升级后「结构变了、数据没丢、幂等」三件事。

为什么自己现造老库、而不是拿线上库的副本：
    线上库早就迁移过了，而且第一个账号已经把老数据认领走 ——
    再跑一次迁移只是幂等自检，**验不到「老库第一次升级」这条真正的路径**。
    所以这里按老结构现造一个小库（没有 user_id、日期全局唯一、没有 users 表），
    数据量小、id 可控、每次跑结果一致，跑完就在临时目录里，不碰线上 8848。

跑法：python tools/_mu_migrate_test.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402  （删临时目录要重试+延后，见该模块注释）

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = 0
bad = 0


# ============================================================
# 老库结构（多用户之前）：articles / study_log 没有 user_id，UNIQUE 只约束日期；
# 没有 users / sessions / user_profile 表；vocab 没有 exam_note；
# writing_items 没有 position_hint / position_usage / genre；
# favorites 还是 V1 的样子（没有双库、主题、位置那一堆列）。
# 迁移要做的事，就是把这些差异补齐并保持数据不变。
# ============================================================
_LEGACY_DDL = """
CREATE TABLE articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    title         TEXT NOT NULL,
    subtitle      TEXT,
    source        TEXT,
    source_type   TEXT DEFAULT 'ai',
    topic         TEXT,
    difficulty    INTEGER DEFAULT 3,
    word_count    INTEGER DEFAULT 0,
    est_minutes   INTEGER DEFAULT 20,
    body          TEXT NOT NULL,
    translation   TEXT DEFAULT '[]',
    logic         TEXT DEFAULT '{}',
    status        TEXT DEFAULT 'ready',
    model         TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(date)
);
CREATE TABLE study_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
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
    updated_at       TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(date)
);
CREATE TABLE vocab (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id        INTEGER NOT NULL,
    word              TEXT NOT NULL,
    pos               TEXT,
    meaning_in_context TEXT,
    common_meanings   TEXT,
    cn_definition     TEXT,
    example           TEXT,
    importance        INTEGER DEFAULT 3,
    mastery           INTEGER DEFAULT 0,
    favorite          INTEGER DEFAULT 0,
    occurrence        INTEGER DEFAULT 1,
    last_review_at    TEXT,
    UNIQUE(article_id, word)
);
CREATE TABLE sentences (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL,
    en           TEXT NOT NULL,
    zh           TEXT,
    skeleton     TEXT,
    main_clause  TEXT,
    subject      TEXT,
    predicate    TEXT,
    object       TEXT,
    clauses      TEXT DEFAULT '[]',
    modifiers    TEXT DEFAULT '[]',
    non_finite   TEXT,
    insertion    TEXT,
    appositive   TEXT,
    logic        TEXT,
    why_hard     TEXT,
    order_index  INTEGER DEFAULT 0
);
CREATE TABLE grammar (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT,
    explanation TEXT,
    pattern     TEXT,
    examples    TEXT DEFAULT '[]',
    order_index INTEGER DEFAULT 0
);
CREATE TABLE writing_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL,
    kind        TEXT,
    content     TEXT NOT NULL,
    cn          TEXT,
    example     TEXT,
    usage_note  TEXT,
    order_index INTEGER DEFAULT 0
);
CREATE TABLE imitation_tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id       INTEGER NOT NULL,
    prompt_cn        TEXT NOT NULL,
    prompt_en        TEXT,
    hint             TEXT,
    reference_answer TEXT
);
CREATE TABLE submissions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    round      INTEGER DEFAULT 1,
    answer     TEXT NOT NULL,
    feedback   TEXT DEFAULT '{}',
    score      REAL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT DEFAULT (datetime('now','localtime')),
    kind    TEXT,
    ref_id  INTEGER,
    payload TEXT DEFAULT '{}'
);
CREATE TABLE favorites (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type         TEXT,
    content           TEXT NOT NULL,
    cn                TEXT,
    example           TEXT,
    tags              TEXT DEFAULT '[]',
    source_article_id INTEGER,
    created_at        TEXT DEFAULT (datetime('now','localtime'))
);
"""


def build_legacy_db(path: Path) -> None:
    """现造一个老库，塞入少量可数的样本数据。"""
    con = sqlite3.connect(str(path))
    con.executescript(_LEGACY_DDL)
    with con:
        con.executemany(
            "INSERT INTO articles(date,title,body,translation,logic) VALUES(?,?,?,?,?)",
            [
                ("2026-09-01", "Old One", "body one", '[{"en":"a","zh":"甲"}]', '{}'),
                ("2026-09-02", "Old Two", "body two", '[{"en":"b","zh":"乙"}]', '{}'),
                ("exam-2010-text2", "真题 Text2", "exam body", "[]", "{}"),
            ],
        )
        con.executemany(
            "INSERT INTO study_log(date,article_id,read_seconds) VALUES(?,?,?)",
            [("2026-09-01", 1, 600), ("2026-09-02", 2, 300)],
        )
        con.executemany(
            "INSERT INTO favorites(item_type,content) VALUES(?,?)",
            [("phrase", "in the long run"), ("sentence", "It is not that ... but that ...")],
        )
        con.execute("INSERT INTO events(kind,ref_id) VALUES('read',1)")
        con.execute("INSERT INTO vocab(article_id,word) VALUES(1,'prism')")
        con.execute("INSERT INTO sentences(article_id,en) VALUES(1,'A sentence.')")
        con.execute("INSERT INTO grammar(article_id,name) VALUES(1,'从句')")
        con.execute("INSERT INTO writing_items(article_id,content) VALUES(1,'the more ... the more')")
        con.execute("INSERT INTO imitation_tasks(article_id,prompt_cn) VALUES(1,'仿写一句')")
        con.execute("INSERT INTO submissions(task_id,answer) VALUES(1,'my answer')")
    con.close()


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        bad += 1
        print(f"  ✗ {label} {extra}")


def snapshot_counts(db: Path) -> dict:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    out = {}
    for t in ("articles", "vocab", "sentences", "grammar", "writing_items",
              "imitation_tasks", "submissions", "favorites", "study_log", "events"):
        try:
            out[t] = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        except sqlite3.Error:
            out[t] = -1
    con.close()
    return out


def dump_ids(db: Path) -> tuple[list, list]:
    con = sqlite3.connect(db)
    arts = [(r[0], r[1], r[2]) for r in con.execute("SELECT id,date,title FROM articles ORDER BY id")]
    favs = [(r[0], r[1]) for r in con.execute("SELECT id,content FROM favorites ORDER BY id")]
    con.close()
    return arts, favs


def main() -> int:
    # 这个脚本原来**没有任何收尾**：每跑一次就在 %TEMP% 里留一个 mu_mig_*（实测攒了 11 个）。
    # 包一层 try/finally，无论从哪个 return 出去都会收掉。
    tmp = Path(tempfile.mkdtemp(prefix="mu_mig_"))
    try:
        return _main(tmp)
    finally:
        cleanup(tmp)


def _main(tmp: Path) -> int:
    copy = tmp / "app.db"
    build_legacy_db(copy)
    print(f"老库副本：{copy}")

    before_arts, before_favs = dump_ids(copy)
    before_counts = snapshot_counts(copy)
    print("迁移前：" + "  ".join(f"{k}={v}" for k, v in before_counts.items() if v > 0))

    # ★ 必须在 import app 之前把 DB_PATH 指向副本（load_dotenv 不覆盖已有环境变量）
    os.environ["DB_PATH"] = str(copy)
    try:
        from app import db, users
    except Exception as exc:  # noqa: BLE001
        print("导入失败：", exc)
        return 1
    print("应用读取的库：", db.settings.db_path)
    if Path(db.settings.db_path) != copy:
        print("！！DB_PATH 没生效，拒绝继续")
        return 1

    print("\n[1] 第一次迁移")
    db.init_db()
    db.init_db()  # 幂等：再来一次不能出错、不能改变结果
    print("    连续 init_db() 两次未报错")

    con = sqlite3.connect(str(copy))
    con.row_factory = sqlite3.Row

    # ---- 结构 ----
    art_cols = {r["name"] for r in con.execute("PRAGMA table_info(articles)")}
    log_cols = {r["name"] for r in con.execute("PRAGMA table_info(study_log)")}
    check("articles 有 user_id 列", "user_id" in art_cols)
    check("study_log 有 user_id 列", "user_id" in log_cols)
    check("articles 补上 exam_focus 列", "exam_focus" in art_cols)
    vocab_cols = {r["name"] for r in con.execute("PRAGMA table_info(vocab)")}
    check("vocab 补上 exam_note 列", "exam_note" in vocab_cols)
    wi_cols = {r["name"] for r in con.execute("PRAGMA table_info(writing_items)")}
    check("writing_items 补上 position_hint / position_usage / genre",
          {"position_hint", "position_usage", "genre"} <= wi_cols)
    for t in ("users", "sessions", "user_profile"):
        got = con.execute(
            "SELECT COUNT(*) c FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()["c"]
        check(f"新表 {t} 已建好", got == 1)

    # UNIQUE 约束确实变成 (user_id, date)
    art_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='articles'"
    ).fetchone()["sql"]
    check("articles 唯一键换成 (user_id,date)", "UNIQUE(user_id, date)" in art_sql,
          f"实际：{art_sql[-120:]}")
    log_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='study_log'"
    ).fetchone()["sql"]
    check("study_log 唯一键换成 (user_id,date)", "UNIQUE(user_id, date)" in log_sql)

    # 临时表没留下
    left = con.execute(
        "SELECT COUNT(*) c FROM sqlite_master WHERE name LIKE '%__migrating'"
    ).fetchone()["c"]
    check("没有残留的 __migrating 临时表", left == 0)

    # ---- 数据一条不少，id 不变 ----
    after_counts = snapshot_counts(copy)
    for t, n in before_counts.items():
        if n >= 0:
            check(f"{t} 行数不变（{n}）", after_counts[t] == n,
                  f"迁移后 {after_counts[t]}")
    after_arts, after_favs = dump_ids(copy)
    check("articles 的 id/date/title 原样保留", after_arts == before_arts)
    check("favorites 的 id/content 原样保留", after_favs == before_favs)

    # ---- 归属统一成 0 号桶（不能留 NULL） ----
    for t in ("articles", "study_log", "favorites", "events"):
        n = con.execute(f"SELECT COUNT(*) c FROM {t} WHERE user_id IS NULL").fetchone()["c"]
        check(f"{t} 没有 user_id IS NULL 的行", n == 0, f"还剩 {n} 条")
    n0 = con.execute("SELECT COUNT(*) c FROM articles WHERE user_id=0").fetchone()["c"]
    check(f"articles 全部进了 0 号桶（{n0} 条）", n0 == before_counts["articles"])
    # favorites 的 V2 分类列在迁移里被回填了默认值
    lvl = [r["level"] for r in con.execute("SELECT DISTINCT level FROM favorites")]
    check("favorites 的 level 已回填", lvl == ["B"], str(lvl))
    lib = [r["library"] for r in con.execute("SELECT DISTINCT library FROM favorites")]
    check("favorites 的 library 已回填", all(x in ("reading", "writing") for x in lib), str(lib))
    con.close()

    # ---- 全新库：列名一个都不能少（DDL 里的转义注释吃掉列名，就栽在这） ----
    fresh = tmp / "fresh.db"
    os.environ["DB_PATH"] = str(fresh)
    # 直接把连接指向新库（db.settings 就是 config.settings 同一个对象，
    # 不要去 reload app.config —— 那会换出一个新对象，db 还指着旧的，白折腾）
    db.settings.db_path = fresh
    db.init_db()
    con2 = sqlite3.connect(str(fresh))
    con2.row_factory = sqlite3.Row
    want_articles = {
        "id", "user_id", "date", "title", "subtitle", "source", "source_type", "topic",
        "difficulty", "word_count", "est_minutes", "body", "translation", "logic",
        "exam_focus", "status", "model", "created_at",
    }
    got_articles = {r["name"] for r in con2.execute("PRAGMA table_info(articles)")}
    check("全新库 articles 列齐全", want_articles <= got_articles,
          f"缺：{sorted(want_articles - got_articles)}；多：{sorted(got_articles - want_articles)}")
    want_log = {
        "id", "user_id", "date", "article_id", "read_seconds", "analysis_opened",
        "vocab_unknown", "vocab_focus", "vocab_known", "imitation_rounds",
        "imitation_score", "favorites_added", "updated_at",
    }
    got_log = {r["name"] for r in con2.execute("PRAGMA table_info(study_log)")}
    check("全新库 study_log 列齐全", want_log == got_log,
          f"缺：{sorted(want_log - got_log)}；多：{sorted(got_log - want_log)}")
    # 光看列名还不够：真写一行、再读回来，才算这些列真的能用
    with con2:
        con2.execute(
            "INSERT INTO articles(user_id,date,title,body,translation,logic,exam_focus) "
            'VALUES(1,\'2020-01-01\',\'t\',\'b\',\'[{"en":"a"}]\',\'{}\',\'{}\')'
        )
        row2 = con2.execute(
            "SELECT translation,logic,exam_focus FROM articles WHERE date='2020-01-01'"
        ).fetchone()
    check("全新库写读 translation/logic/exam_focus 正常",
          row2 is not None and "en" in (row2["translation"] or ""),
          str(dict(row2) if row2 else None))
    con2.close()
    db.settings.db_path = copy  # 拨回副本，继续后面几节

    # ---- UNIQUE(user_id,date) 真的生效 ----
    con = sqlite3.connect(str(copy))
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute(
            "INSERT INTO articles(user_id,date,title,body) VALUES(0,'2099-01-01','t','b')"
        )
        con.execute(
            "INSERT INTO articles(user_id,date,title,body) VALUES(0,'2099-01-01','t','b')"
        )
        check("同一用户同一天写两篇会被拦住", False, "居然插入成功了")
    except sqlite3.IntegrityError:
        check("同一用户同一天写两篇会被拦住", True)
    con.rollback()
    try:
        con.execute(
            "INSERT INTO articles(user_id,date,title,body) VALUES(0,'2099-01-02','a','b')"
        )
        con.execute(
            "INSERT INTO articles(user_id,date,title,body) VALUES(7,'2099-01-02','a','b')"
        )
        con.rollback()
        check("不同用户可以在同一天各有一篇", True)
    except sqlite3.IntegrityError as exc:
        check("不同用户可以在同一天各有一篇", False, str(exc))
    con.close()

    # ---- 隔离上下文 ----
    print("\n[2] 上下文与隔离")
    try:
        db.scope()
        check("没有上下文时 scope() 会抛错", False, "竟然没抛")
    except RuntimeError:
        check("没有上下文时 scope() 会抛错", True)
    try:
        db.require_user()
        check("没有上下文时 require_user() 会抛错", False)
    except RuntimeError:
        check("没有上下文时 require_user() 会抛错", True)

    with db.as_local():
        check("as_local() 能看到 0 号桶的文章", db.get_article_by_date(
            before_arts[0][1]) is not None if before_arts else True)
    with db.as_user(1):
        check("切到 1 号用户后看不到 0 号桶的文章",
              db.latest_article() is None, "居然看到了别人的")
    with db.as_system():
        check("as_system() 能看到全库", db.latest_article() is not None)
    with db.as_system():
        try:
            db.require_user()
            check("as_system() 里 require_user() 会拒绝", False)
        except RuntimeError:
            check("as_system() 里 require_user() 会拒绝", True)
    # 上下文用完要复位
    try:
        db.current_user_id()
        check("退出 with 之后上下文已复位", False, "还留着值")
    except RuntimeError:
        check("退出 with 之后上下文已复位", True)

    # ---- 账号与认领 ----
    print("\n[3] 建号 + 认领老数据")
    with db.as_system():
        admin = users.create_user("tester", "abc12345", display_name="测试", is_admin=True)
    check("账号建好且返回的是公开字段（不含口令）",
          "password_hash" not in admin and admin["is_admin"] is True)
    check("管理员手动开的号默认就是已批准", admin["approved"] == 1)
    check("用户名重复会被拒绝", _raises(lambda: users.create_user("TESTER", "abc12345")))
    check("非法用户名会被拒绝", _raises(lambda: users.create_user("a b", "abc12345")))
    check("太短的密码会被拒绝", _raises(lambda: users.create_user("okname", "123")))
    check("保留字会被拒绝", _raises(lambda: users.create_user("admin", "abc12345")))

    u = users.get_user_by_name("tester")
    check("口令核对正确", users.verify_password(u, "abc12345"))
    check("错口令不通过", not users.verify_password(u, "abc1234"))
    check("大小写不敏感地找到用户", users.get_user_by_name("TESTER") is not None)

    left = users.orphan_counts()
    check("认领前 0 号桶里有文章", left["articles"] == before_counts["articles"],
          str(left))
    claimed = users.claim_orphan_data(u["id"])
    check("认领后 0 号桶清空", users.orphan_counts()["articles"] == 0, str(claimed))
    with db.as_user(u["id"]):
        check("认领后这个账号能看到老数据", db.latest_article() is not None)

    # ---- 会话 ----
    print("\n[4] 会话")
    tok = users.create_session(u["id"], "pytest")
    con = sqlite3.connect(str(copy))
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT token_hash FROM sessions").fetchone()
    check("库里存的是令牌哈希、不是令牌本身", row["token_hash"] != tok and len(row["token_hash"]) == 64)
    con.close()
    check("拿令牌能换回用户", users.session_user(tok)["id"] == u["id"])
    check("伪造令牌换不回用户", users.session_user("nope") is None)
    users.set_password(u["id"], "newpass99")
    check("改密后旧令牌立刻失效", users.session_user(tok) is None)
    check("改密后新口令可用", users.verify_password(users.get_user(u["id"]), "newpass99"))

    print(f"\n{'=' * 56}\n通过 {ok} 项，失败 {bad} 项\n{'=' * 56}")
    print(f"临时库仍保留在：{tmp}（确认无误后可删）")
    return 1 if bad else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


if __name__ == "__main__":
    sys.exit(main())
