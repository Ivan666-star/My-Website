# -*- coding: utf-8 -*-
"""手写批注的回归：db 层隔离 + HTTP 接口端到端。

为什么这么测：
    批注是"存进账号、按文章保存"的——最不能接受的 bug 是「A 看到 B 的笔迹」。
    所以第一层直接切 as_user(1) / as_user(2)，验隔离、覆盖、前缀匹配和 LIKE 转义；
    第二层自己起临时实例打真实端口，验落库 / 取回 / 空笔迹删行 / 非法 key 被拒 / 未登录被挡。

绝不碰 data/app.db，绝不调模型（AI_API_KEY 置空、生成时间挪到 23 点），跑完自己收掉。

用法：
    python tools/_pen_test.py
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qa_tmp import cleanup  # noqa: E402  （删临时目录要重试，见该模块注释）

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PLAIN_PORT = 8896     # 免登录实例（MODE_OPEN）：跑主流程
GUARDED_PORT = 8897   # 带口令实例：验"没登录不给看"
PWD = "pen1234"

ok_n = 0
bad_n = 0


def check(label, cond, detail=""):
    global ok_n, bad_n
    if cond:
        ok_n += 1
        print(f"  [OK]   {label}")
    else:
        bad_n += 1
        print(f"  [FAIL] {label}   {detail}")


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) != 0


@contextmanager
def instance(port: int, password: str | None):
    """起一个临时实例，退出上下文时保证被杀掉。"""
    if not port_free(port):
        raise RuntimeError(f"端口 {port} 被占用。这是本测试专用端口，请先关掉占用它的程序。")
    tmp = Path(tempfile.mkdtemp(prefix=f"pentest{port}_"))
    env = dict(os.environ)
    env.update({
        "ACCESS_PASSWORD": password or "",
        "DB_PATH": str(tmp / "app.db").replace("\\", "/"),
        "AI_API_KEY": "",                 # 临时实例绝不调模型
        "DAILY_GENERATE_HOUR": "23",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        ready = False
        for _ in range(60):
            if proc.poll() is not None:
                err = (proc.stderr.read() or b"").decode("utf-8", "replace")
                raise RuntimeError(f"实例 {port} 启动即退出：\n{err[-800:]}")
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                ready = True
                break
            except urllib.error.HTTPError:
                ready = True   # 401 也算活着
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        if not ready:
            raise RuntimeError(f"实例 {port} 在 24 秒内没起来")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        cleanup(tmp)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """别自动跟着跳：要看状态码本身。"""

    def redirect_request(self, *a, **k):  # noqa: D102
        return None


def opener_with(jar=None):
    jar = jar if jar is not None else http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),          # 本机 http_proxy 会劫持 127.0.0.1
        NoRedirect(),
        urllib.request.HTTPCookieProcessor(jar),
    )
    return op, jar


def lower(hd) -> dict:
    """uvicorn 下发的响应头名是小写的，统一归一化。"""
    return {str(k).lower(): v for k, v in hd.items()}


def call(base, path, method="GET", body=None, accept=None, op=None):
    """返回 (status, headers, body)。HTTP 错误也当正常结果返回。"""
    headers = {}
    if accept:
        headers["Accept"] = accept
    data = None
    if body is not None:
        if isinstance(body, bytes):
            data = body
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    op = op or opener_with()[0]
    try:
        with op.open(req, timeout=20) as r:
            return r.status, lower(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, lower(e.headers), e.read()
    except Exception as e:  # noqa: BLE001
        return -1, {}, str(e).encode("utf-8")


def form(pairs) -> bytes:
    return urllib.parse.urlencode(pairs).encode("utf-8")


def js(bd) -> dict:
    try:
        return json.loads(bd.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return {}


STROKE = {"c": "#c0392b", "s": 3.5, "hl": 0, "p": [[10, 12], [14, 18], [20, 30]]}
DATA = {"v": 1, "w": 640, "strokes": [STROKE]}


# ============================================================
# A. db 层：隔离 / 覆盖 / 前缀 / LIKE 转义
# ============================================================
def part_a() -> None:
    # ⚠️ 必须在 import app.db 之前把 DB_PATH 指到临时库，否则会往线上的 data/app.db 里写测试数据
    tmp = Path(tempfile.mkdtemp(prefix="pentest_db_"))
    os.environ["DB_PATH"] = str(tmp / "app.db").replace("\\", "/")
    os.environ.setdefault("AI_API_KEY", "")
    try:
        _part_a(tmp)
    finally:
        cleanup(tmp)


def _part_a(tmp: Path) -> None:
    from app import db
    db.init_db()
    print("=== A. db 层：每人独立 + 前缀精确 + 转义 ===")

    with db.as_user(1):
        db.save_annotation("2026-10-05:body", DATA)
        db.save_annotation("2026-10-05:trans", {"v": 1, "w": 640, "strokes": [{"c": "#1f6fd0", "s": 2, "p": [[5, 6]]}]})
        got = db.annotations_for("2026-10-05")
        check("自己存的能取回（2 个区块）", len(got) == 2, list(got))
        check("区块键是「文档:区块」", "2026-10-05:body" in got and "2026-10-05:trans" in got, list(got))
        check("坐标原样回（没被截断/取整）",
              got["2026-10-05:body"]["strokes"][0]["p"] == [[10, 12], [14, 18], [20, 30]],
              got.get("2026-10-05:body"))
        check("基准宽 w 存下来了", got["2026-10-05:body"]["w"] == 640, got.get("2026-10-05:body"))
        check("换一天取不到", db.annotations_for("2026-10-06") == {}, db.annotations_for("2026-10-06"))
        check("前缀必须整段匹配（2026 不等于 2026-10-05）", db.annotations_for("2026") == {}, "不该匹配")
        check("空前缀返回空（绝不倒库）", db.annotations_for("") == {})
        check("纯空白前缀同样返回空", db.annotations_for("   ") == {})

    with db.as_user(2):
        check("B 看不到 A 的批注", db.annotations_for("2026-10-05") == {}, db.annotations_for("2026-10-05"))
        db.save_annotation("2026-10-05:body", {"v": 1, "w": 640, "strokes": [{"c": "#1f6fd0", "s": 2, "p": [[9, 9]]}]})
        got2 = db.annotations_for("2026-10-05")
        check("B 只看到自己那一条", len(got2) == 1, list(got2))
        check("B 的笔迹是蓝色那条",
              got2["2026-10-05:body"]["strokes"][0]["c"] == "#1f6fd0", got2.get("2026-10-05:body"))

    with db.as_user(1):
        a = db.annotations_for("2026-10-05")
        check("A 的数据没被 B 覆盖（仍是黑笔）",
              a["2026-10-05:body"]["strokes"][0]["c"] == "#c0392b", a.get("2026-10-05:body"))
        check("A 的笔数没变少", len(a) == 2, list(a))

    with db.as_user(1):
        db.save_annotation("2026-10-05:body", DATA)
        n = db.query("SELECT COUNT(*) AS n FROM annotations WHERE user_id=1")[0]["n"]
        check("同一 key 重复写是覆盖、不新增行", n == 2, n)

    with db.as_user(1):
        db.save_annotation("2026-10-05:trans", {"v": 1, "w": 640, "strokes": []})
        got = db.annotations_for("2026-10-05")
        check("笔迹擦空 -> 这条记录被删掉", "2026-10-05:trans" not in got, list(got))
        n = db.query("SELECT COUNT(*) AS n FROM annotations WHERE user_id=1")[0]["n"]
        check("擦空后库里只剩 1 行", n == 1, n)

    with db.as_user(1):
        for bad in ("", "   ", "nocolon"):
            try:
                db.save_annotation(bad, {"strokes": [{"p": [[1, 1]]}]})
                check(f"非法 key {bad!r} 应当被拒", False, "居然通过了")
            except ValueError:
                check(f"非法 key {bad!r} 被拒", True)

    with db.as_user(3):
        db.save_annotation("exam-2010-text2:body", {"v": 1, "w": 700, "strokes": [{"p": [[1, 1], [2, 2]]}]})
        check("真题伪日期前缀正常取回", len(db.annotations_for("exam-2010-text2")) == 1, "取不到")
        check("_ 被转义（e_am 不该匹配 exam）", db.annotations_for("e_am-2010-text2") == {}, "通配符漏了")
        check("% 被转义（% 不该匹配一切）", db.annotations_for("%") == {}, "通配符漏了")
        check("exam% 不该匹配 exam-2010-text2", db.annotations_for("exam%") == {}, "通配符漏了")

    try:
        db.annotations_for("2026-10-05")
        check("没设用户上下文时应当抛错", False, "居然返回了数据（可能串数据）")
    except RuntimeError:
        check("没设用户上下文时抛错（不会静默倒库）", True)

    with db.as_system():
        got = db.annotations_for("2026-10-05")
        check("全库上下文能同时看到 A 和 B", "2026-10-05:body" in got, list(got))


# ============================================================
# B / C. HTTP 层
# ============================================================
def part_b() -> None:
    print("=== B. 免登录实例：落库 / 取回 / 校验 / 擦空 ===")
    with instance(PLAIN_PORT, None) as base:
        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "2026-10-05:body", "data": DATA})
        check("PUT 批注 -> 200", st == 200, f"got {st} {bd[:120]}")
        check("返回 ok=true", js(bd).get("ok") is True, bd[:120])

        st, _, bd = call(base, "/api/annotations?prefix=2026-10-05")
        items = js(bd).get("items", {})
        check("GET 能取回这条批注", "2026-10-05:body" in items, list(items))
        st0 = items.get("2026-10-05:body", {}).get("strokes", [{}])[0]
        check("点数一致（坐标没丢）", len(st0.get("p", [])) == 3, st0)
        check("颜色 / 粗细原样", st0.get("c") == "#c0392b" and st0.get("s") == 3.5, st0)
        check("基准宽 w 也存了", items.get("2026-10-05:body", {}).get("w") == 640, items)

        st, _, bd = call(base, "/api/annotations?prefix=2026-10-06")
        check("换一篇取不到", js(bd).get("items") == {}, bd[:120])
        st, _, bd = call(base, "/api/annotations?prefix=")
        check("空前缀不倒库", js(bd).get("items") == {}, bd[:120])

        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "nocolon", "data": DATA})
        check("key 里没有冒号 -> 400", st == 400, f"got {st}")
        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "2026-10-05:body", "data": "我是个字符串"})
        check("data 不是对象 -> 400", st == 400, f"got {st}")
        st, _, bd = call(base, "/api/annotations", method="PUT", body={"data": DATA})
        check("缺 key -> 400", st == 400, f"got {st}")
        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "x" * 200 + ":body", "data": DATA})
        check("超长 key -> 400", st == 400, f"got {st}")

        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "2026-10-05:body", "data": {"v": 1, "w": 640, "strokes": []}})
        check("笔迹擦空 PUT -> 200", st == 200, f"got {st}")
        st, _, bd = call(base, "/api/annotations?prefix=2026-10-05")
        check("擦空之后取不到这条", js(bd).get("items") == {}, bd[:120])

        big = {"v": 1, "w": 700, "strokes": [{"c": "#1b1a17", "s": 2, "p": [[i, i + 1] for i in range(1500)]}]}
        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "exam-2010-text2:body", "data": big})
        check("1500 点的大笔画能存", st == 200, f"got {st}")
        st, _, bd = call(base, "/api/annotations?prefix=exam-2010-text2")
        n = len(js(bd).get("items", {}).get("exam-2010-text2:body", {}).get("strokes", [{}])[0].get("p", []))
        check("1500 点原样取回", n == 1500, n)


def part_c() -> None:
    print("=== C. 带口令实例：没登录不给看批注 ===")
    with instance(GUARDED_PORT, PWD) as base:
        st, _, bd = call(base, "/api/annotations?prefix=2026-10-05")
        check("未登录 GET 批注 -> 401", st == 401, f"got {st}")
        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "2026-10-05:body", "data": DATA})
        check("未登录 PUT 批注 -> 401", st == 401, f"got {st}")

        op, _ = opener_with()
        st, _, _ = call(base, "/login", method="POST",
                        body=form({"password": PWD, "next": "/"}), op=op)
        check("口令登录 -> 303", st == 303, f"got {st}")
        st, _, bd = call(base, "/api/annotations", method="PUT",
                         body={"key": "2026-10-05:body", "data": DATA}, op=op)
        check("登录后 PUT -> 200", st == 200, f"got {st} {bd[:120]}")
        st, _, bd = call(base, "/api/annotations?prefix=2026-10-05", op=op)
        check("登录后能取回自己刚写的",
              "2026-10-05:body" in js(bd).get("items", {}), bd[:120])
        st, _, _ = call(base, "/api/annotations?prefix=2026-10-05")
        check("另一个没登录的会话仍被挡", st == 401, f"got {st}")


def main() -> int:
    part_a()
    part_b()
    part_c()
    print(f"\n=== 结果：{ok_n} 通过 / {bad_n} 失败 ===")
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())
