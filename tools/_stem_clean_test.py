"""识图结果清理（math.clean_extracted_stem）的回归测试 —— 不花钱、不碰真库。

用户反馈的原话是「识别出来的题目带有数学代码」，实测有三个来源：
  1. 模型把整段包进 ``` 围栏；
  2. 模型用 \\( \\) / \\[ \\] 定界 —— 前端只认 $ 与 $$，于是原样显示成源码；
  3. 混进 <br>、&nbsp;、**加粗** 这类标记，或者写了没闭合的 \\begin{...}。
这三类都在这里钉住；同时确认「无损」：普通公式一个字符都不能被改掉。

跑法（在 项目/ 目录下）：
    python tools/_stem_clean_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from app.math import clean_extracted_stem  # noqa: E402


CASES = [
    (
        "去掉 markdown 围栏",
        "```latex\n7. 设 $f(x)=x^2$，求 $f'(1)$。\n```",
        lambda s: "```" not in s and "f'(1)" in s,
    ),
    (
        "\\( \\) 统一成 $",
        r"求 \(\lim\limits_{x\to 0}\dfrac{\sin x}{x}\) 的值。",
        lambda s: "$" in s and "\\(" not in s and "\\lim" in s,
    ),
    (
        "\\[ \\] 统一成 $$",
        r"\[ \int_0^1 x\,dx \]",
        lambda s: s.startswith("$$") and s.endswith("$$") and "\\int" in s,
    ),
    (
        "去掉 HTML 标记与实体",
        "求 <b>极限</b>：<br>lim&nbsp;x→0 f(x)",
        lambda s: "<br>" not in s and "<b>" not in s and "&nbsp;" not in s and "极限" in s,
    ),
    (
        "去掉成对加粗标记",
        "**题干**：设 $a>0$",
        lambda s: "**" not in s and "题干" in s and "$a>0$" in s,
    ),
    (
        "未闭合的 \\begin 被剔除（不让 KaTeX 整段报错）",
        r"\begin{cases} x>0 \\ x<1",
        lambda s: "\\begin{cases}" not in s,
    ),
    (
        "闭合环境原样保留",
        r"$$\begin{cases} x>0 \\ x<1 \end{cases}$$",
        lambda s: "\\begin{cases}" in s and "\\end{cases}" in s,
    ),
    (
        "无损：公式字符一个不改",
        r"$f'(a)=\lim\limits_{x\to a}\dfrac{f(x)-f(a)}{x-a}$",
        lambda s: s == r"$f'(a)=\lim\limits_{x\to a}\dfrac{f(x)-f(a)}{x-a}$",
    ),
    (
        "无损：看不清的 □ 占位保留",
        "设 $a=□$，求 $a^2$。",
        lambda s: "□" in s,
    ),
]


def main() -> int:
    ok = True
    for name, raw, check in CASES:
        out = clean_extracted_stem(raw)
        passed = bool(out) and check(out)
        detail = "" if passed else f"输入={raw!r} 输出={out!r}"
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + ("" if passed else f"   <- {detail}"), flush=True)
        ok = ok and passed
    print("全部通过" if ok else "有失败项", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())