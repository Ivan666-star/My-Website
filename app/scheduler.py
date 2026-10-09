"""每日自动生成调度：进程内后台任务，电脑开机服务常驻时即可每天自动备好内容。

多用户之后的一个关键变化：
    内容是**每人各自一份**（只有各人生成各自的，难度自适应和个人画像才有意义），
    所以这里要遍历所有在职账号，逐个进 db.as_user(uid) 之后生成。
    缺谁的补谁 —— 某个人已经生成了就跳过，不会重复消耗模型额度。

为什么要 in 进程内做：
    规格书第二十七节把定时任务交给 Harness，这里提供一个「不依赖外部编排」的兜底：
    服务只要在跑，每天 daily_hour 点后就会自动补齐当天内容。
    （同时保留 POST /api/generate 接口，Harness / 手动也能随时触发。）
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime

from . import db
from .ai.pipeline import generate_daily
from .config import settings

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "last_error": "",
    "last_date": "",
    "last_at": "",
    "last_users": [],
}


def daily_hour() -> int:
    """自动生成的时间点。设置页改过就用改过的（存 profile），否则用 .env。"""
    raw = db.get_profile("daily_hour", "")
    try:
        return int(raw) if raw else settings.daily_hour
    except (TypeError, ValueError):
        return settings.daily_hour


def active_user_ids() -> list[int]:
    """需要每天生成内容的账号。还没建账号时 = 本地自用的未认领桶。"""
    with db.as_system():
        # 只给「已批准 + 未停用」的账号生成：还没批准的人压根没在用，
        # 给他生成等于白烧模型调用。
        rows = db.query("SELECT id FROM users WHERE active=1 AND approved=1 ORDER BY id")
        total = db.query_one("SELECT COUNT(*) c FROM users")
    ids = [int(r["id"]) for r in rows]
    if ids:
        return ids
    # 一个已批准的账号都没有，分两种情况：
    #   库里压根没账号   → 本地自用，生成进未认领桶（老行为不变）
    #   只有待批准的账号 → 谁都还用不上，别生成（白烧模型调用）
    return [] if (total and int(total["c"]) > 0) else [db.UNCLAIMED]


def _try_generate_today() -> None:
    with _lock:
        if _state["running"]:
            return
        _state["running"] = True
    try:
        today = db.today_str()
        done: list[int] = []
        errs: list[str] = []
        for uid in active_user_ids():
            try:
                with db.as_user(uid):
                    if db.get_article_by_date(today):
                        continue  # 这个人今天已经有了
                    generate_daily(today)
                done.append(uid)
            except Exception as exc:  # noqa: BLE001  —— 一个人失败不能拖累其他账号
                errs.append(f"#{uid} {exc}")
        _state["last_date"] = today
        _state["last_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _state["last_users"] = done
        _state["last_error"] = "；".join(errs)[:300]
    except Exception as exc:  # noqa: BLE001
        _state["last_error"] = str(exc)[:300]
    finally:
        _state["running"] = False


async def _loop() -> None:
    # 启动后稍等，避免和启动流程抢资源
    await asyncio.sleep(15)
    while True:
        try:
            if datetime.now().hour >= daily_hour():
                # 已在 asyncio.to_thread 里跑：它自带一份上下文副本，
                # 所以在里面 set/reset ContextVar 不会污染主线程。
                await asyncio.to_thread(_try_generate_today)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(600)  # 每 10 分钟检查一次


def start_scheduler() -> asyncio.Task | None:
    if not settings.ai_ready:
        return None
    return asyncio.create_task(_loop())


def state() -> dict:
    return dict(_state)
