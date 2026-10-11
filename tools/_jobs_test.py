"""后台任务机制（app/jobs.py）的回归测试 —— 不碰真库、不花模型额度。

为什么单独测它：
    524 这个坑的根因就是「让 HTTP 请求干等 1~2 分钟的 AI 流程」。
    改成后台任务后，最容易出错的地方变成了「任务状态」本身：
    重复点击会不会重复跑、错误有没有传出来、用户上下文有没有跟着线程走。
    这三条都在这里钉住。

跑法（在 项目/ 目录下）：
    python tools/_jobs_test.py
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 控制台默认是 GBK，直接 print 中文/符号会 UnicodeEncodeError。
# 这个测试是要给人看的，所以自己把输出流切成 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from app import jobs  # noqa: E402


class FakeDB:
    """替换掉真 db：只记录「任务体有没有跑在正确的用户上下文里」。"""

    UNCLAIMED = 0

    def __init__(self, uid: int = 7):
        self.uid = uid
        self.contexts: list[int] = []

    def require_user(self) -> int:
        return self.uid

    @contextmanager
    def as_user(self, uid: int):
        self.contexts.append(int(uid))
        yield int(uid)


def wait_done(kind: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.status(kind)
        if not st["running"]:
            return st
        time.sleep(0.02)
    raise AssertionError(f"{kind} 任务超时未结束")


def main() -> int:
    fake = FakeDB(uid=7)
    jobs.db = fake
    checks: list[tuple[str, bool, str]] = []

    # ---- 1) 正常跑完：状态能从 running 走到 ok，结果带得回来 ----
    runs = {"n": 0}

    def slow_ok():
        runs["n"] += 1
        time.sleep(0.25)
        return {"ok": True, "bundle": {"article": {"date": "2026-10-10"}}}

    first = jobs.start("generate", slow_ok, label="测试")
    checks.append(("开始时 running=True", first["running"] is True, str(first)))
    time.sleep(0.1)
    mid = jobs.status("generate")
    checks.append(("进行中 elapsed 是数字", isinstance(mid["elapsed"], int) and mid["elapsed"] >= 0, str(mid["elapsed"])))
    checks.append(("进行中拿不到 result", mid["result"] is None, str(mid["result"])))

    # ---- 2) 同一用户重复点击：不会重复启动 ----
    again = jobs.start("generate", slow_ok, label="测试")
    checks.append(("重复点击不重复启动", again["running"] is True and runs["n"] == 1, f"runs={runs['n']}"))

    done = wait_done("generate")
    checks.append(("跑完 ok=True", done["ok"] is True, str(done["ok"])))
    checks.append(("跑完带回 bundle", bool((done["result"] or {}).get("bundle")), str(done["result"])))
    checks.append(("只跑了一次", runs["n"] == 1, f"runs={runs['n']}"))
    checks.append(("任务体跑在用户上下文里", fake.contexts == [7, 7][: len(fake.contexts)] and 7 in fake.contexts, str(fake.contexts)))
    checks.append(("finished_at 已填", bool(done["finished_at"]), str(done["finished_at"])))

    # ---- 3) 失败要如实传出来（对应 AIError / 任何异常）----
    def boom():
        raise RuntimeError("模型输出格式连续两次不正确")

    jobs.start("import", boom, label="测试失败")
    st = wait_done("import")
    checks.append(("失败时 running=False", st["running"] is False, str(st)))
    checks.append(("失败时 ok=False", st["ok"] is False, str(st["ok"])))
    checks.append(("失败原因能拿到", "格式连续两次不正确" in (st["error"] or ""), str(st["error"])))

    # ---- 4) 不同任务类型互不干扰 ----
    checks.append(("另一类任务状态独立", jobs.status("exam")["exists"] is False, str(jobs.status("exam"))))

    ok = True
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"), flush=True)
        ok = ok and passed
    print("全部通过" if ok else "有失败项", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())