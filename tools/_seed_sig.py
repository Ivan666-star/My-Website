# -*- coding: utf-8 -*-
"""给某个账号灌一批「信号与系统错题」示例数据，方便手动点着试功能。

用法（在项目根目录执行）：
    python tools/_seed_sig.py                 # 灌进账号 1
    python tools/_seed_sig.py --user 2        # 指定账号
    python tools/_seed_sig.py --clear         # 只删本脚本灌的，不动你自己录的

为什么标成「示例」而不是假装真题：
    题干 / 解析都按真实题的标准写，信号上站得住，但**不是我抄自某年卷子**的原文，
    所以备注统一写 `[示例] …`。要清的时候一条命令全清，不会误伤自己录的题。
    和数学示例共用同一个错题库引擎（同一张表 / 同一条复习阶梯），只是科目不同。

覆盖到的状态（就是拿来试那一排筛选的）：
    8 个专题每个 1 条 · 普通错题 5 + 考研真题错题 3
    今天该复习 2 · 已逾期 1 · 在周期 3 · 已掌握 1 · 已移出复习 1 · 未解析 1
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app import db, math  # noqa: E402

TAG = "[示例]"
SIG = "信号与系统"


# ============================================================
# 题目本身：题干 / 知识点 / 关键步骤 / 解法 / 易错点
# ------------------------------------------------------------
# cycle: due=今天该复习 · overdue=已逾期 · future=在周期里 · mastered=已掌握 · out=已移出
# ============================================================
ITEMS = [
    # ---------------- 拉普拉斯变换与s域分析 ----------------
    {
        "topic": "拉普拉斯变换与s域分析", "kind": "exam",
        "note": "分段信号的拉氏变换——时移性质",
        "cycle": "due",
        "stem": r"已知 $f(t)=u(t)-u(t-2)$，求其拉普拉斯变换 $F(s)$ 并注明收敛域。",
        "knowledge": r"单位阶跃的拉氏变换 + 时移性质（$t_0$ 右移对应乘 $e^{-st_0}$）",
        "key_steps": [
            r"$u(t)\leftrightarrow \dfrac{1}{s}$（$\operatorname{Re}(s)>0$）。",
            r"$u(t-2)\leftrightarrow \dfrac{e^{-2s}}{s}$（时移性质：右移 $2$ 乘 $e^{-2s}$）。",
            r"线性相减：$F(s)=\dfrac{1-e^{-2s}}{s}$。",
            r"收敛域由 $u(t)$ 决定：$\operatorname{Re}(s)>0$（右边信号，ROC 不因加减改变）。",
        ],
        "solution": r"$F(s)=\dfrac{1-e^{-2s}}{s}$，收敛域 $\operatorname{Re}(s)>0$。",
        "pitfalls": r"① 时移因子方向写反（$e^{+2s}$）；② 收敛域不写——拉氏变换**必须**带 ROC 才完整；③ 以为 $u(t-2)$ 的变换是 $\dfrac{1}{s-2}$，把时移和 $s$ 域平移搞混。",
    },
    # ---------------- Z变换与z域分析 ----------------
    {
        "topic": "Z变换与z域分析", "kind": "normal",
        "note": "基本序列的 z 变换与收敛域",
        "cycle": "overdue",
        "stem": r"求序列 $x[n]=a^{n}u[n]$（$a$ 为实常数）的 $z$ 变换 $X(z)$ 及其收敛域，并指出 $a$ 取何值时该序列的傅里叶变换存在。",
        "knowledge": r"等比序列的 z 变换、收敛域由 $|az^{-1}|<1$ 决定、DTFT 存在条件（ROC 含单位圆）",
        "key_steps": [
            r"按定义：$X(z)=\sum\limits_{n=0}^{\infty}a^{n}z^{-n}=\sum\limits_{n=0}^{\infty}(az^{-1})^{n}$。",
            r"等比级数收敛条件 $|az^{-1}|<1$，即 $|z|>|a|$。",
            r"$X(z)=\dfrac{1}{1-az^{-1}}=\dfrac{z}{z-a}$，极点 $z=a$，收敛域 $|z|>|a|$。",
            r"DTFT 是 $z=e^{j\omega}$ 上的取值，要求收敛域包含单位圆，即 $|a|<1$。",
        ],
        "solution": r"$X(z)=\dfrac{z}{z-a}$（$|z|>|a|$）；当 $|a|<1$ 时收敛域包含单位圆，DTFT 存在，为 $X(e^{j\omega})=\dfrac{1}{1-ae^{-j\omega}}$。",
        "pitfalls": r"① 收敛域漏写——z 变换不加 ROC 是**没有定义**的；② 把 $a^n u[-n-1]$（左边序列）的 ROC $|z|<|a|$ 与右边序列混为一谈；③ 判断 DTFT 存在时只看序列衰减、忘了看单位圆。",
    },
    # ---------------- 傅里叶级数与傅里叶变换 ----------------
    {
        "topic": "傅里叶级数与傅里叶变换", "kind": "exam",
        "note": "单边指数信号的傅里叶变换",
        "cycle": "future",
        "stem": r"求 $g(t)=e^{-2t}u(t)$ 的傅里叶变换 $G(j\omega)$，并画出幅度谱 $|G(j\omega)|$ 随 $\omega$ 变化的趋势。",
        "knowledge": r"单边指数衰减信号的傅里叶变换对 $e^{-\alpha t}u(t)\leftrightarrow \dfrac{1}{\alpha+j\omega}$（$\alpha>0$）",
        "key_steps": [
            r"由定义：$G(j\omega)=\displaystyle\int_{0}^{\infty}e^{-2t}e^{-j\omega t}\,\mathrm{d}t$。",
            r"积分得 $\dfrac{1}{2+j\omega}$（$\alpha=2$ 直接套公式）。",
            r"幅度 $|G(j\omega)|=\dfrac{1}{\sqrt{4+\omega^{2}}}$：$\omega=0$ 时为 $\dfrac12$，随 $|\omega|$ 增大单调下降趋于 $0$——**低通**特性。",
        ],
        "solution": r"$G(j\omega)=\dfrac{1}{2+j\omega}$；幅度谱从 $\dfrac12$ 单调衰减到 $0$，相位 $-\arctan\dfrac{\omega}{2}$。",
        "pitfalls": r"① $j$ 的符号写反（分母是 $\alpha+j\omega$ 不是 $\alpha-j\omega$，差一个共轭）；② 公式只对 $\alpha>0$ 成立——$e^{+2t}u(t)$ 不绝对可积，**没有**傅里叶变换（只有拉氏变换）。",
    },
    # ---------------- 连续系统的时域分析 ----------------
    {
        "topic": "连续系统的时域分析", "kind": "normal",
        "note": "二阶系统零状态响应——部分分式反变换",
        "cycle": "due",
        "stem": r"已知 LTI 系统的微分方程为 $y''(t)+3y'(t)+2y(t)=x(t)$，起始状态为零，$x(t)=u(t)$。求系统的单位冲激响应 $h(t)$ 与零状态响应 $y(t)$。",
        "knowledge": r"系统函数 $H(s)=\dfrac{1}{s^{2}+3s+2}$、部分分式展开、$H(s)$ 与 $h(t)$ 的关系",
        "key_steps": [
            r"$H(s)=\dfrac{1}{(s+1)(s+2)}=\dfrac{1}{s+1}-\dfrac{1}{s+2}$。",
            r"$h(t)=\left(e^{-t}-e^{-2t}\right)u(t)$。",
            r"$X(s)=\dfrac1s$，$Y(s)=H(s)X(s)=\dfrac{1}{s(s+1)(s+2)}=\dfrac{\tfrac12}{s}-\dfrac{1}{s+1}+\dfrac{\tfrac12}{s+2}$。",
            r"反变换：$y(t)=\left(\dfrac12-e^{-t}+\dfrac12 e^{-2t}\right)u(t)$。",
        ],
        "solution": r"$h(t)=\left(e^{-t}-e^{-2t}\right)u(t)$；$y(t)=\left(\dfrac12-e^{-t}+\dfrac12e^{-2t}\right)u(t)$。",
        "pitfalls": r"① 部分分式系数用「通分比对」硬算时最容易算错，建议用遮盖法逐个求；② 忘记乘 $u(t)$——这是**因果**系统且输入在 $t=0$ 接入；③ 题目问零状态就别把零输入响应（起始状态为零时本为零）混进来。",
    },
    # ---------------- 离散系统的时域分析 ----------------
    {
        "topic": "离散系统的时域分析", "kind": "normal",
        "note": "二阶差分方程的齐次解",
        "cycle": "future",
        "stem": r"已知离散系统的差分方程为 $y[n]-\dfrac{5}{6}y[n-1]+\dfrac{1}{6}y[n-2]=0$，且 $y[0]=1$、$y[1]=\dfrac{5}{6}$。求 $y[n]$（$n\ge 0$）。",
        "knowledge": r"特征方程求根、齐次解的两种形式（互异实根）、由初始条件定系数",
        "key_steps": [
            r"特征方程 $\lambda^{2}-\dfrac56\lambda+\dfrac16=0$，即 $(\lambda-\tfrac12)(\lambda-\tfrac13)=0$，根 $\lambda_1=\dfrac12$、$\lambda_2=\dfrac13$。",
            r"齐次解形式 $y[n]=A\left(\tfrac12\right)^{n}+B\left(\tfrac13\right)^{n}$。",
            r"代 $y[0]=1$：$A+B=1$；代 $y[1]=\dfrac56$：$\dfrac A2+\dfrac B3=\dfrac56$，即 $3A+2B=5$。",
            r"解得 $A=3$、$B=-2$。",
        ],
        "solution": r"$y[n]=3\left(\tfrac12\right)^{n}-2\left(\tfrac13\right)^{n}$（$n\ge 0$）。",
        "pitfalls": r"① 特征方程的系数抄错（差分方程里 $-\dfrac56$ 对应特征方程里 $-\dfrac56$，别变号变两次）；② 两个初始条件**都要**代进去，只代 $y[0]$ 会把 $B$ 当成任意常数。",
    },
    # ---------------- 系统函数与稳定性 ----------------
    {
        "topic": "系统函数与稳定性", "kind": "exam",
        "note": "由极点位置判稳定 + 冲激响应",
        "cycle": "mastered",
        "stem": r"已知连续 LTI 系统的系统函数 $H(s)=\dfrac{s+1}{s^{2}+2s+2}$。判断系统是否稳定，并求单位冲激响应 $h(t)$。",
        "knowledge": r"极点分布与稳定性的关系（连续系统：极点全部落在 $s$ 左半平面 ⇔ 渐进稳定）、共轭复极点的部分分式",
        "key_steps": [
            r"分母 $s^{2}+2s+2=(s+1)^{2}+1$，极点 $s=-1\pm j$，实部全为负 → **稳定**。",
            r"把分子凑成与分母匹配：$s+1$ 恰是 $(s+1)^{2}+1$ 的实部。",
            r"$H(s)=\dfrac{(s+1)}{(s+1)^{2}+1}\leftrightarrow h(t)=e^{-t}\cos t\,u(t)$。",
        ],
        "solution": r"系统稳定；$h(t)=e^{-t}\cos t\,u(t)$（衰减振荡）。",
        "pitfalls": r"① 把「零点位置」当「极点位置」来判断稳定——只看**分母**；② 复极点配对时分子凑不出 $(s+1)$ 就硬拆成两个实系数分式，绕远路还容易错。",
    },
    # ---------------- 状态变量分析 ----------------
    {
        "topic": "状态变量分析", "kind": "normal",
        "note": "状态转移矩阵 e^{At}（已掌握，验证「已掌握」筛选）",
        "cycle": "mastered",
        "stem": r"已知系统 $\dot{x}(t)=\begin{bmatrix}0&1\\-2&-3\end{bmatrix}x(t)$，求状态转移矩阵 $\Phi(t)=e^{At}$。",
        "knowledge": r"$\Phi(t)=\mathcal{L}^{-1}\left[(sI-A)^{-1}\right]$、二阶系统的伴随矩阵法",
        "key_steps": [
            r"$sI-A=\begin{bmatrix}s&-1\\2&s+3\end{bmatrix}$，$\det(sI-A)=s^{2}+3s+2$。",
            r"$(sI-A)^{-1}=\dfrac{1}{(s+1)(s+2)}\begin{bmatrix}s+3&1\\-2&s\end{bmatrix}$。",
            r"逐元素反变换：$\dfrac{s+3}{(s+1)(s+2)}=\dfrac{2}{s+1}-\dfrac{1}{s+2}$，$\dfrac{1}{(s+1)(s+2)}=\dfrac{1}{s+1}-\dfrac{1}{s+2}$，$\dfrac{s}{(s+1)(s+2)}=-\dfrac{1}{s+1}+\dfrac{2}{s+2}$。",
            r"$\Phi(t)=\begin{bmatrix}2e^{-t}-e^{-2t}&e^{-t}-e^{-2t}\\-2e^{-t}+2e^{-2t}&-e^{-t}+2e^{-2t}\end{bmatrix}$。",
        ],
        "solution": r"$\Phi(t)=\begin{bmatrix}2e^{-t}-e^{-2t}&e^{-t}-e^{-2t}\\[2pt]-2e^{-t}+2e^{-2t}&-e^{-t}+2e^{-2t}\end{bmatrix}$（可代 $t=0$ 得 $I$ 自检）。",
        "pitfalls": r"① 伴随矩阵的符号错（副对角线要**变号**）；② 特征多项式写成 $\det(A-sI)$——差一个符号，后面的部分分式全错；③ 算完不验 $\Phi(0)=I$，错了自己都不知道。",
    },
    # ---------------- 信号与系统基本概念 ----------------
    {
        "topic": "信号与系统基本概念", "kind": "normal",
        "note": "系统性质判断（这条故意不给解析，用来试「AI 解析」按钮）",
        "cycle": "future",
        "analyzed": False,
        "stem": r"判断系统 $y(t)=t\,x(t)$ 是否具有线性、时不变性与因果性，并说明理由。",
    },
]


def clear(uid: int) -> int:
    with db.as_user(uid):
        rows = db.query(
            "SELECT id FROM math_problems WHERE user_id=? AND subject=? AND source_note LIKE ?",
            (uid, SIG, TAG + "%"),
        )
        n = 0
        for r in rows:
            if math.delete_problem(r["id"]):
                n += 1
        return n


def seed(uid: int) -> dict:
    today = date.today()
    plan = {
        "due": today.isoformat(),
        "overdue": (today - timedelta(days=2)).isoformat(),
        "future": (today + timedelta(days=7)).isoformat(),
        "mastered": (today + timedelta(days=30)).isoformat(),
        "out": "",
    }
    added = []
    with db.as_user(uid):
        for it in ITEMS:
            p = math.create_problem(
                it["stem"],
                subject=SIG, topic=it["topic"], kind=it["kind"],
                source_note=TAG + " " + it["note"],
            )
            pid = p["id"]
            if it.get("analyzed", True):
                math.update_problem(pid, {
                    "knowledge": it["knowledge"],
                    "key_steps": it["key_steps"],
                    "solution": it["solution"],
                    "alt_solution": it.get("alt_solution", ""),
                    "pitfalls": it["pitfalls"],
                })
            rc = 7 if it["cycle"] == "mastered" else (2 if it["cycle"] == "future" else 0)
            db.execute(
                "UPDATE math_problems SET next_review=?, review_count=? WHERE id=? AND user_id=?",
                (plan[it["cycle"]], rc, pid, uid),
            )
            added.append((pid, it["topic"], it["kind"], it["cycle"]))
        return {"added": added, "stats": math.stats(module="sig")}


def main() -> None:
    args = sys.argv[1:]
    uid = 1
    if "--user" in args:
        uid = int(args[args.index("--user") + 1])

    if "--clear" in args:
        n = clear(uid)
        print(f"已删除账号 {uid} 下科目为「{SIG}」且带 `{TAG}` 备注的错题：{n} 条")
        with db.as_user(uid):
            print("剩余：", math.stats(module="sig"))
        return

    # 表结构由正在跑的服务负责，这里**不动 schema**（免得和线上进程抢写）
    with db.as_user(uid):
        before = math.stats(module="sig")
    if before["total"]:
        print(f"⚠️ 账号 {uid} 的信号与系统已有 {before['total']} 道错题，本次是**追加**（不会覆盖）。\n")

    # 库先备份：正式库，写坏了要能退回去。
    # ⚠️ 别用 shutil.copy2 —— WAL 模式下刚提交的数据还在 -wal 里，单拷 app.db 会拿到旧库。
    #    用 SQLite 在线备份接口拿一致的单文件快照。
    src = ROOT / "data" / "app.db"
    if src.exists():
        from datetime import datetime as _dt
        bd = ROOT / "data" / ("backup_pre_seedsig_" + _dt.now().strftime("%Y%m%d_%H%M%S"))
        bd.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(src))
        try:
            dst = sqlite3.connect(str(bd / "app.db"))
            try:
                con.backup(dst)
            finally:
                dst.close()
        finally:
            con.close()
        print(f"备份 -> {bd}\\app.db")

    out = seed(uid)
    print(f"写入 {len(out['added'])} 条：")
    for pid, top, kind, cyc in out["added"]:
        print(f"  #{pid:<4} {top:<16} {math.KINDS[kind]:<8} {cyc}")
    s = out["stats"]
    print(f"\n信号与系统：总量 {s['total']} ｜ 今天该复习 {s['due']} ｜ 已掌握 {s['mastered']}"
          f" ｜ 按专题 {len(s['by_subject'])} 个科目")
    print(f"（今天 = {s['today']}；清空示例数据：python tools/_seed_sig.py --clear）")


if __name__ == "__main__":
    main()
