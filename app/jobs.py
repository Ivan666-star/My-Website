"""后台长任务：把「要跑 1~2 分钟的 AI 流程」从 HTTP 请求里搬出来。

为什么必须有这一层
------------------
Cloudflare 代理对**源站响应**有硬上限（官方文档：默认 125 秒 Proxy Read Timeout，
免费 / Pro / Business 都不能调，只有 Enterprise 能提到 6000 秒）。超时就返回
错误码 **524** 并掐断连接 —— 手机用户看到的是「请求失败（524）」。

而「生成今天这一套」要连续调 3 次模型（文章 → 精读解析 → 写作素材），通常
1~2 分钟，正好压在这条线上。更要命的是：**连接被掐断后，服务器其实还在继续跑**，
最后文章确实写进了库，但用户永远收不到那个回应，页面就一直停在「今天的内容还没准备好」，
只好再点一次 → 又超时 → 又把文章重写一遍（白烧模型额度）。

所以改成这里的做法：
    POST /api/generate        → 立刻返回「已开始」，真正的活在后台线程里跑
    GET  /api/generate/status → 前端每 2 秒问一次（毫秒级请求，永远碰不到 125 秒）
    done                      → 前端拿到结果渲染上屏

约定
----
- 一个用户同一类任务同时只跑一个：重复点击只会拿到 `running=True`，不会重复烧额度。
- 任务体在 `db.as_user(uid)` 里跑：后台线程不继承请求的 ContextVar，
  必须自己带上用户上下文，否则会撞上 db 的隔离检查（或更糟：写错用户的数据）。
- 只保留「每个用户每类任务的最近一次」状态，够前端用，也不会无限涨。
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime
from typing import Any, Callable

from . import db

_lock = threading.RLock()
# key = (user_id, kind) -> 最近一次任务的状态
_jobs: dict[tuple[int, str], dict] = {}


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _snapshot(key: tuple[int, str]) -> dict:
    """把状态拷一份出去。**调用方必须已经持有 _lock**（避免嵌套加锁）。"""
    state = _jobs.get(key)
    if not state:
        return {"kind": key[1], "running": False, "ok": None, "error": "", "result": None,
                "started_at": "", "finished_at": "", "elapsed": 0, "exists": False}
    cur = dict(state)
    if cur.get("running"):
        cur["elapsed"] = int((datetime.now() - state["_started_dt"]).total_seconds())
    cur.pop("_started_dt", None)
    cur["exists"] = True
    return cur


def _run(uid: int, kind: str, work: Callable[[], Any]) -> None:
    key = (uid, kind)
    try:
        with db.as_user(uid):
            result = work()
        with _lock:
            _jobs[key].update(ok=True, error="", result=result)
    except Exception as exc:  # noqa: BLE001 —— 任何异常都要落进状态里给前端看
        detail = str(exc) or exc.__class__.__name__
        print(f"[jobs] {kind} 失败：{detail}")
        traceback.print_exc()
        with _lock:
            _jobs[key].update(ok=False, error=detail, result=None)
    finally:
        with _lock:
            state = _jobs[key]
            state["running"] = False
            state["finished_at"] = _now()
            state["elapsed"] = int((datetime.now() - state["_started_dt"]).total_seconds())


def start(kind: str, work: Callable[[], Any], *, label: str = "") -> dict:
    """开始一个后台任务。已在跑就原样返回当前状态（不会重复启动）。"""
    uid = db.require_user()
    key = (uid, kind)
    with _lock:
        cur = _jobs.get(key)
        if cur and cur.get("running"):
            return _snapshot(key)      # 已在跑：不重复启动，也不重复烧模型额度
        _jobs[key] = {
            "kind": kind,
            "label": label,
            "running": True,
            "ok": None,
            "error": "",
            "result": None,
            "started_at": _now(),
            "finished_at": "",
            "elapsed": 0,
            "_started_dt": datetime.now(),
            "user_id": uid,
        }
    threading.Thread(target=_run, args=(uid, kind, work), name=f"job-{kind}-{uid}", daemon=True).start()
    return status(kind)


def status(kind: str) -> dict:
    uid = db.require_user()
    with _lock:
        return _snapshot((uid, kind))


def clear(kind: str) -> None:
    """把最近一次结果清掉（前端取走内容后可调，避免重复渲染旧结果）。"""
    uid = db.require_user()
    with _lock:
        _jobs.pop((uid, kind), None)