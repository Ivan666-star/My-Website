# -*- coding: utf-8 -*-
"""后台任务接口的端到端验证（真 HTTP，不花模型额度）。

为什么还要测一遍：`tools/_jobs_test.py` 测的是 jobs 模块本身（内存里的状态机），
但这个坑真正发生在**HTTP 这一层**——前端拿到的是不是「立刻返回」、轮询能不能拿到
「跑完了没有 / 报了什么错」。这两条只有打真接口才算数。

做法：起一个临时实例（独立 DB、开放模式、故意不配 AI 密钥），于是：
  · POST /api/generate 必须**立刻**返回（而不是挂在那儿等模型）——这是 524 的根因；
  · 因为没配模型，后台任务会很快失败，轮询应当拿到 running=False 且 error 非空；
  · 整个过程里**没有任何一次请求耗时接近 125 秒**（Cloudflare 的那条线）。

跑法（在 项目/ 目录下）：
    python tools/_jobs_api_test.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PORT = 8898
BASE = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="jobsapi_"))

env = dict(os.environ)
env.update({
    "DB_PATH": str(TMP / "app.db").replace("\\", "/"),
    "AI_API_KEY": "",              # 故意不配：让后台任务走「失败也要把原因传回来」这条路
    "DAILY_GENERATE_HOUR": "23",
    "ACCESS_PASSWORD": "",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONPATH": str(ROOT),
})

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with OPENER.open(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")), time.time() - t0, r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw), time.time() - t0, e.code
        except Exception:  # noqa: BLE001
            return {"_raw": raw}, time.time() - t0, e.code


proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

checks: list[tuple[str, bool, str]] = []
try:
    for _ in range(80):
        try:
            OPENER.open(BASE + "/api/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        sys.exit("临时实例没起来")

    # ---- ① 没跑过任何任务时，状态查询应当是「干净的未开始」 ----
    st, dt, code = call("GET", "/api/generate/status")
    checks.append(("状态接口可用（免登录）", code == 200, f"{code} {st}"))
    checks.append(("未开始时 running=False", st.get("running") is False, str(st)))
    checks.append(("状态查询是毫秒级", dt < 2.0, f"{dt:.2f}s"))

    # ---- ② 发起生成：必须立刻返回，不能挂在那儿等模型 ----
    # 故意挑一个内置示例里没有的日期：这样才会真的去调模型，
    # 而本实例没配密钥 → 走的正是「后台失败也要把原因如实传回」这条路。
    r, dt, code = call("POST", "/api/generate", {"date": "2030-01-01", "force": True})
    checks.append(("发起生成返回 200", code == 200, f"{code} {str(r)[:120]}"))
    checks.append(("发起生成是立刻返回的", dt < 3.0, f"{dt:.2f}s"))
    checks.append(("返回值告诉前端「已在跑」", r.get("running") is True, str(r)[:160]))
    checks.append(("返回值里没有把长内容塞进响应", "bundle" not in r, str(list(r.keys()))))

    # ---- ③ 轮询：要能拿到结束状态与失败原因 ----
    final = None
    polls = 0
    max_poll_cost = 0.0
    deadline = time.time() + 40
    while time.time() < deadline:
        st, dt, code = call("GET", "/api/generate/status")
        polls += 1
        max_poll_cost = max(max_poll_cost, dt)
        if not st.get("running"):
            final = st
            break
        time.sleep(0.5)
    checks.append(("轮询能拿到结束状态", final is not None, str(final)[:200] if final else "超时仍在跑"))
    checks.append(("结束时 running=False", bool(final) and final.get("running") is False, str(final)[:120]))
    checks.append(("失败原因如实传回（没配模型）",
                   bool(final) and "AI" in (final.get("error") or ""), str((final or {}).get("error"))[:200]))
    checks.append(("结束时 ok=False", bool(final) and final.get("ok") is False, str((final or {}).get("ok"))))
    checks.append(("每次轮询都很快（远离 125 秒那条线）", max_poll_cost < 2.0, f"最慢 {max_poll_cost:.2f}s / {polls} 次"))

    # ---- ④ 另外两个长接口同样改造过 ----
    for path, kind in (("/api/import", "import"), ("/api/exam/import", "exam")):
        st, dt, code = call("GET", f"{path}/status")
        checks.append((f"{kind} 状态接口在", code == 200, f"{code}"))
    r, dt, code = call("POST", "/api/import", {"text": "x" * 300})
    checks.append(("粘贴材料：立刻返回且已在跑", code == 200 and dt < 3.0 and r.get("running") is True,
                   f"{code} {dt:.2f}s {str(r)[:120]}"))
    # 太短的正文仍然要当场报错（这是输入校验，不该进后台）
    r, dt, code = call("POST", "/api/exam/import", {"text": "short"})
    checks.append(("真题导入的输入校验仍在（未进后台）", code == 400, f"{code} {str(r)[:120]}"))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    cleanup(TMP)

ok = True
print("=== 断言 ===")
for name, passed, detail in checks:
    print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"))
    ok = ok and passed
print("全部通过" if ok else "有失败项")
sys.exit(0 if ok else 1)