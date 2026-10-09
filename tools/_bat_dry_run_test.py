"""启动脚本（启动.bat）的分支干跑测试 —— 跑完自删临时产物。

只验证分支逻辑、编码与换行，不真的启动服务。

两个真踩过的坑，这里都当断言守着：
  1. 【换行必须 CRLF】LF-only 时 cmd 执行多行 if(...) 块会解析错乱。
     旧版测试用 write_text，被 Windows 文本模式顺手补成 CRLF，
     于是"真文件是 LF"这个 bug 被测成了通过 —— 假通过。
  2. 【不能再出现 chcp 65001 + UTF-8】cmd 按当前代码页解码批处理，
     代码页中途切换会在读入缓冲边界上把中文拆坏：
     同一个文件两次运行结果不一样（A 跑挂 / B 跑好）。所以本测试会
     连跑 3 次，要求输出完全一致，把这种不确定性钉死。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "\u542f\u52a8.bat"

ASCII_ONLY_FORBIDDEN = ("chcp",)          # 代码页切换是万恶之源
BOX_CHARS = "\u2500\u00b7\u2502"          # ─ · │


def listening_ports() -> list[int]:
    """取真正在监听的【本地】端口。

    ⚠️ 只看第 2 列（本地地址）。netstat 里 LISTENING 行的对端地址是
    `0.0.0.0:0`，如果把所有含冒号的字段都收进来，会得到一个假端口 0，
    进而让 A 分支的构造变成假通过。
    """
    r = subprocess.run(["netstat", "-ano"], capture_output=True)
    out = r.stdout.decode("gbk", errors="replace")
    ports: list[int] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or "LISTENING" not in line.upper():
            continue
        local = parts[1]
        tail = local.rsplit(":", 1)[-1]
        if tail.isdigit():
            p = int(tail)
            if p and p not in ports:
                ports.append(p)
    return sorted(ports)


def has_chcp_command(text: str) -> bool:
    """只认真正的 chcp 命令，放过 rem 注释里提到 chcp 的情况。"""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("rem"):
            continue
        if s.lower().startswith("chcp") or "| chcp " in s.lower() or "&& chcp " in s.lower():
            return True
    return False


def make_dry(text: str, probe_port: int) -> str:
    t = text
    t = t.replace('start "" "http://127.0.0.1:8848"', "echo [DRY] open-browser")
    t = t.replace("ping -n 3 127.0.0.1 >nul", "echo [DRY] wait-2s")
    t = t.replace('start "" /min "%PY%" "%~dp0tools\\open_when_ready.py"',
                  "echo [DRY] spawn-open-helper PY=%PY%")
    t = t.replace('"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8848 '
                  '--no-use-colors > "%LOG%" 2>&1',
                  "echo [DRY] run-uvicorn")
    t = t.replace("pause", "echo [DRY] pause")
    if probe_port != 8848:
        t = t.replace('findstr /c:":8848"', f'findstr /c:":{probe_port}"')
    return t


def write_bat(path: Path, text: str) -> None:
    """显式 GBK + CRLF —— 不再依赖任何隐式转换。"""
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk"))


def run(path: Path, times: int = 3) -> tuple[str, bool]:
    outs = []
    for _ in range(times):
        p = subprocess.run(["cmd.exe", "/c", str(path)], capture_output=True, cwd=str(ROOT))
        outs.append(p.stdout.decode("gbk", errors="replace")
                    + p.stderr.decode("gbk", errors="replace"))
    return outs[0], all(o == outs[0] for o in outs)


def main() -> int:
    fails: list[str] = []

    # ── 先守源文件本身 ────────────────────────────────────────────
    raw = SRC.read_bytes()
    bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
    if bare_lf:
        fails.append(f"源文件有 {bare_lf} 处裸 LF（cmd 解析多行块会错乱），必须全部 CRLF")
    if raw[:3] == b"\xef\xbb\xbf":
        fails.append("源文件带 UTF-8 BOM，cmd 会把 BOM 当成命令")
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError as exc:
        fails.append(f"源文件不是 GBK 编码，cmd 读它必乱码：{exc}")
        text = raw.decode("utf-8", errors="replace")
    for kw in ASCII_ONLY_FORBIDDEN:
        if kw == "chcp":
            if has_chcp_command(text):
                fails.append("源文件里有 chcp 命令（代码页中途切换会拆坏中文，已踩过坑）")
            continue
        if kw in text:
            fails.append(f"源文件含 {kw!r}（代码页中途切换会拆坏中文，已踩过坑）")
    for ch in BOX_CHARS:
        if ch in text:
            fails.append(f"源文件含制表符/中点 {ch!r}，容易被代码页拆坏")

    # ⚠️ 服务日志必须重定向到文件。
    #    不重定向的话：有人在这个黑窗口里**点一下、框选一下文字**，
    #    Windows 控制台就会把进程挂起（写不进去日志 = 所有请求卡死）。
    #    本项目真踩过：窗口标题会变成「选择 xxx」，服务"看着在跑其实冻住了"。
    uvicorn_lines = [ln for ln in text.splitlines() if "-m uvicorn" in ln]
    if not uvicorn_lines:
        fails.append("源文件里找不到启动 uvicorn 的那一行")
    else:
        uv = uvicorn_lines[0]
        if ">" not in uv or "2>&1" not in uv:
            fails.append("启动 uvicorn 那一行没有把输出重定向到文件（控制台框选会冻住服务）")
        if "--no-use-colors" not in uv:
            fails.append("启动 uvicorn 那一行缺 --no-use-colors（日志文件里会有 ANSI 乱码）")

    # ⚠️ 路径被转义层改写过的痕迹：本项目真踩过 —— 源码写 `C:\\Users\\...`，
    #    落盘变成 `C://Users//...`（双斜杠）。Windows 能容错，但这类"看着能跑"的脏数据必须拦。
    for line in text.splitlines():
        if "VENV_PY" in line or "%~dp0" in line:
            if "//" in line or "\\\\" in line:
                fails.append(f"源文件路径里有重复分隔符（转义被改写）：{line.strip()!r}")

    ports = listening_ports()
    live = next((p for p in ports if p != 8848), None)
    if live is None:
        fails.append("找不到一个正在监听的端口，A 分支没法构造")

    tmp = Path(tempfile.mkdtemp(prefix="_dry_", dir=str(ROOT)))
    try:
        if live is not None:
            a = tmp / "a.bat"
            write_bat(a, make_dry(text, live))
            out_a, same_a = run(a)
            print(f"=== A) 探一个真在监听的端口 :{live} ===")
            print(out_a)
            if "已经在运行" not in out_a:
                fails.append("A 分支：没走『已经在运行』")
            if "[DRY] open-browser" not in out_a:
                fails.append("A 分支：没有打开页面动作")
            if "[DRY] run-uvicorn" in out_a:
                fails.append("A 分支：竟然还去启动 uvicorn（会端口冲突）")
            for need in ("服务已经在运行", "正在打开页面"):
                if need not in out_a:
                    fails.append(f"A 分支：中文输出不对/乱码，缺 {need!r}")
            if "\ufffd" in out_a:
                fails.append("A 分支：中文输出出现了替换字符")
            if "不是内部或外部命令" in out_a:
                fails.append("A 分支：cmd 报解析错误")
            if not same_a:
                fails.append("A 分支：连跑 3 次输出不一致（不确定性行为）")

        b = tmp / "b.bat"
        write_bat(b, make_dry(text, 999999))
        out_b, same_b = run(b)
        print("=== B) 探一个不存在的端口 :999999 ===")
        print(out_b)
        if "已经在运行" in out_b:
            fails.append("B 分支：误判成『已经在运行』")
        for need in ("[DRY] spawn-open-helper", "[DRY] run-uvicorn"):
            if need not in out_b:
                fails.append(f"B 分支：缺少 {need}")
        if "http://127.0.0.1:8848" not in out_b:
            fails.append("B 分支：没打印访问地址")
        for need in ("AI 考研英语一训练系统", "电脑访问：", "手机访问：",
                     "运行日志：", "停止服务：按 Ctrl+C 或直接关掉本窗口"):
            if need not in out_b:
                fails.append(f"B 分支：中文输出不对/乱码，缺 {need!r}")
        if "\ufffd" in out_b:
            fails.append("B 分支：中文输出出现了替换字符")
        if "不是内部或外部命令" in out_b:
            fails.append("B 分支：cmd 报解析错误")
        if not same_b:
            fails.append("B 分支：连跑 3 次输出不一致（不确定性行为）")

        # 真正验一次：脚本解析出来的解释器路径，必须真的存在
        m = re.search(r"PY=(\S.*?)\s*$", out_b, re.MULTILINE)
        if not m:
            fails.append("B 分支：没能从输出里读到 %PY% 的值")
        else:
            py = m.group(1).strip()
            print(f"   脚本算出的解释器：{py}")
            if not Path(py).exists():
                fails.append(f"B 分支：%PY% 指向的解释器不存在 → {py!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        for f in fails:
            print("\u2717", f)
        print(f"\n{len(fails)} 项待修")
        return 1
    print("\u2713 启动脚本：源文件格式 + 分支干跑（各 3 次取一致），全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
