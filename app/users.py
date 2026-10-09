"""账号与会话：开号 / 登录 / 改密 / 停用，以及老数据的认领。

为什么手写而不用现成库：
    标准库的 hashlib.pbkdf2_hmac 就够用，不额外引入 passlib / bcrypt，
    部署时少一个依赖、少一类装不上的问题。

密码怎么存：
    每人一个独立 salt，PBKDF2-HMAC-SHA256 迭代 20 万轮，只存派生结果。
    就算数据库被拿走，也无法反推出口令。

会话怎么存：
    cookie 里放 32 字节随机令牌；**库里只存它的 SHA-256**。
    这样即便数据库泄露，也不能直接拿库里的东西登录 —— 少一层转发风险。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from . import db

ITERATIONS = 200_000
SALT_BYTES = 16
MIN_PASSWORD = 6
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,24}$")
RESERVED = {"admin", "root", "system", "setup", "login", "logout", "api", "static", "me"}


# ============================================================
# 口令
# ============================================================
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), ITERATIONS)
    return dk.hex(), salt


def verify_password(user: dict, password: str) -> bool:
    stored = (user or {}).get("password_hash") or ""
    salt = (user or {}).get("salt") or ""
    if not stored or not salt:
        return False
    got, _ = hash_password(password or "", salt)
    # 用 compare_digest 而不是 == ：避免按字符比较带来的时间侧信道
    return hmac.compare_digest(got, stored)


# ============================================================
# 校验
# ============================================================
def check_username(username: str) -> str:
    """返回清洗后的用户名；不合法时抛 ValueError（消息可直接给用户看）。"""
    u = (username or "").strip()
    if not u:
        raise ValueError("请填用户名")
    if not USERNAME_RE.match(u):
        raise ValueError("用户名只能填 3-24 位的字母、数字、下划线、点或短横线")
    if u.lower() in RESERVED:
        raise ValueError(f"「{u}」是保留字，请换一个")
    return u


def check_password(password: str) -> str:
    p = password or ""
    if len(p) < MIN_PASSWORD:
        raise ValueError(f"密码至少 {MIN_PASSWORD} 位（建议 8 位以上，混字母和数字）")
    if len(p) > 128:
        raise ValueError("密码太长了，请控制在 128 位以内")
    return p


# ============================================================
# 用户
# ============================================================
def count_users() -> int:
    row = db.query_one("SELECT COUNT(*) c FROM users")
    return int(row["c"]) if row else 0


def count_approved() -> int:
    """已批准的账号数。

    判定「系统里有没有账号」必须用这个，不能用 count_users()：
    否则第一个自助申请的人（还没人批准他）会让系统以为账号体系已经建立，
    /setup 被关掉，而他本人又登不进去 —— 结果是谁都进不来。
    """
    row = db.query_one("SELECT COUNT(*) c FROM users WHERE approved=1")
    return int(row["c"]) if row else 0


def count_pending() -> int:
    """等着管理员点头的账号数。"""
    row = db.query_one("SELECT COUNT(*) c FROM users WHERE approved=0")
    return int(row["c"]) if row else 0


def has_users() -> bool:
    return count_approved() > 0


def list_users() -> list[dict]:
    """给管理页用的列表（不含任何口令字段）。"""
    rows = db.query(
        """SELECT id, username, display_name, is_admin, active, approved,
                  created_at, last_login_at
           FROM users ORDER BY is_admin DESC, id ASC"""
    )
    for row in rows:
        with db.as_user(int(row["id"])):
            stats = db.query_one(
                "SELECT (SELECT COUNT(*) FROM articles) article_count, "
                "(SELECT COUNT(*) FROM favorites) favorite_count"
            )
        row.update(stats or {"article_count": 0, "favorite_count": 0})
    return rows


def get_user(user_id: int) -> dict | None:
    return db.query_one("SELECT * FROM users WHERE id=?", (int(user_id),))


def get_user_by_name(username: str) -> dict | None:
    return db.query_one("SELECT * FROM users WHERE username=?", ((username or "").strip(),))


def public_user(user: dict | None) -> dict | None:
    """剥掉口令相关字段，只留能下发给前端的部分。"""
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "is_admin": bool(user.get("is_admin")),
        "active": bool(user.get("active")),
        "approved": bool(user.get("approved", 1)),
    }


def create_user(
    username: str,
    password: str,
    *,
    display_name: str = "",
    is_admin: bool = False,
) -> dict:
    u = check_username(username)
    p = check_password(password)
    if get_user_by_name(u):
        raise ValueError("这个用户名已经被占用了，换一个吧")
    pwd_hash, salt = hash_password(p)
    uid = db.execute(
        "INSERT INTO users(username,display_name,password_hash,salt,is_admin) VALUES(?,?,?,?,?)",
        (u, (display_name or "").strip(), pwd_hash, salt, 1 if is_admin else 0),
    )
    return public_user(get_user(uid))


def register_user(username: str, password: str, *, display_name: str = "") -> dict:
    """自助注册：建出来的是「待批准」账号，管理员批准前登不进去。

    管理员在后台直接开的号不走这里（那种是他信得过的人，建好即用）。
    """
    u = check_username(username)
    p = check_password(password)
    if get_user_by_name(u):
        raise ValueError("这个用户名已经被占用了，换一个吧")
    pwd_hash, salt = hash_password(p)
    uid = db.execute(
        "INSERT INTO users(username,display_name,password_hash,salt,is_admin,active,approved) "
        "VALUES(?,?,?,?,0,1,0)",
        (u, (display_name or "").strip()[:40], pwd_hash, salt),
    )
    return public_user(get_user(uid))


def set_approved(user_id: int, approved: bool) -> None:
    db.execute(
        "UPDATE users SET approved=? WHERE id=?", (1 if approved else 0, int(user_id))
    )
    if not approved:
        # 撤回批准 = 立刻不能再用，所以把他在线的会话一并清掉
        revoke_user_sessions(int(user_id))


def delete_user(user_id: int) -> None:
    """删除账号。只用来清理「还没批准、也没任何数据」的空账号，
    调用方必须先确认过这一点（已批准的账号里有全部学习数据，不该被删）。"""
    revoke_user_sessions(int(user_id))
    db.execute("DELETE FROM users WHERE id=?", (int(user_id),))


def set_password(user_id: int, password: str) -> None:
    p = check_password(password)
    pwd_hash, salt = hash_password(p)
    db.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (pwd_hash, salt, int(user_id)))
    # 改密后把这个人的所有设备踢下线（旧令牌一律作废）
    revoke_user_sessions(int(user_id))


def set_active(user_id: int, active: bool) -> None:
    db.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, int(user_id)))
    if not active:
        revoke_user_sessions(int(user_id))


def set_display_name(user_id: int, name: str) -> None:
    db.execute("UPDATE users SET display_name=? WHERE id=?", ((name or "").strip()[:40], int(user_id)))


def set_admin(user_id: int, is_admin: bool) -> None:
    db.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin else 0, int(user_id)))


def touch_login(user_id: int) -> None:
    db.execute("UPDATE users SET last_login_at=datetime('now','localtime') WHERE id=?", (int(user_id),))


# ============================================================
# 会话
# ============================================================
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int, user_agent: str = "") -> str:
    tok = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,user_agent,last_seen_at) "
        "VALUES(?,?,?,datetime('now','localtime'))",
        (_hash_token(tok), int(user_id), (user_agent or "")[:200]),
    )
    return tok


def session_user(token: str) -> dict | None:
    """用 cookie 里的令牌换用户。停用 / 已删除的用户一律当作未登录。"""
    if not token:
        return None
    row = db.query_one("SELECT * FROM sessions WHERE token_hash=?", (_hash_token(token),))
    if not row:
        return None
    user = get_user(row["user_id"])
    # 已停用 / 还没批准 / 已删除的账号，一律当作未登录
    if not user or not user.get("active") or not user.get("approved", 1):
        return None
    # 每小时才刷新一次 last_seen，避免每个请求都写库
    last = (row.get("last_seen_at") or "").replace(" ", "T")
    if last:
        from datetime import datetime, timedelta

        try:
            if datetime.now() - datetime.fromisoformat(last) < timedelta(hours=1):
                return user
        except ValueError:
            pass
    db.execute(
        "UPDATE sessions SET last_seen_at=datetime('now','localtime') WHERE token_hash=?",
        (_hash_token(token),),
    )
    return user


def revoke_session(token: str) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))


def revoke_user_sessions(user_id: int) -> int:
    with db.connect(central=True) as conn:
        cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (int(user_id),))
        return int(cur.rowcount or 0)


def session_count(user_id: int) -> int:
    row = db.query_one("SELECT COUNT(*) c FROM sessions WHERE user_id=?", (int(user_id),))
    return int(row["c"]) if row else 0


# ============================================================
# 老数据认领（只在创建首个管理员时调用一次）
# ============================================================
def claim_orphan_data(user_id: int) -> dict:
    """把升级前留下的数据（0 号桶）划给这个用户。

    为什么要留到这一步才认领：
        迁移那一刻系统里还没有任何账号，随便填一个 user_id 会指向不存在的账号。
        所以老数据先统一挂在 db.UNCLAIMED(0) 上，那是谁都不属于的一个桶；
        等到第一个账号建好，才把它整桶交出去。
    """
    if int(user_id) == db.UNCLAIMED:
        raise ValueError("0 号桶是未认领数据的保留归属，不能把数据划给它自己")
    return db.claim_unclaimed_data(int(user_id))


def orphan_counts() -> dict:
    out: dict[str, int] = {}
    for table in ("articles", "study_log", "favorites"):
        row = db.query_one(f"SELECT COUNT(*) c FROM {table} WHERE user_id=?", (db.UNCLAIMED,))
        out[table] = int(row["c"]) if row else 0
    return out
