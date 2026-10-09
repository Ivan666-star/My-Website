# -*- coding: utf-8 -*-
"""给某个账号灌一批「数一错题」示例数据，方便手动点着试功能。

用法（在项目根目录执行）：
    python tools/_seed_math.py                 # 灌进账号 1
    python tools/_seed_math.py --user 2        # 指定账号
    python tools/_seed_math.py --clear         # 只删本脚本灌的，不动你自己录的

为什么标成「示例」而不是假装真题：
    题干 / 解析都按真实题的标准写，数学上是站得住的，但**不是我抄自某年卷子**的原文，
    所以备注统一写 `[示例] …`。要清的时候一条命令全清，不会误伤自己录的题。

覆盖到的状态（就是拿来试那一排筛选的）：
    3 个科目（高数 6 / 线代 4 / 概率 3）· 普通错题 6 + 考研真题错题 7
    今天该复习 5（含 1 道逾期）· 已掌握 1 · 已移出复习 1 · 未解析 1
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


# ============================================================
# 题目本身：题干 / 知识点 / 关键步骤 / 解法 / 易错点
# ------------------------------------------------------------
# cycle: due=今天该复习 · overdue=已逾期 · future=在周期里 · mastered=已掌握 · out=已移出
# ============================================================
ITEMS = [
    # ---------------- 高等数学 ----------------
    {
        "subject": "高等数学", "topic": "函数与极限", "kind": "exam",
        "note": "极限 0/0 型——泰勒展开还是洛必达",
        "cycle": "due",
        "stem": r"求极限 $\lim\limits_{x\to0}\dfrac{e^{x}-1-x}{x^{2}}$。",
        "knowledge": r"$\dfrac{0}{0}$ 型极限：等价无穷小与泰勒展开的阶数选择",
        "key_steps": [
            r"先定型：$x\to0$ 时分子分母都趋于 $0$，属 $\dfrac{0}{0}$ 型。",
            r"把 $e^{x}$ 展开到二阶：$e^{x}=1+x+\dfrac{x^{2}}{2}+o(x^{2})$。",
            r"分子 $=\dfrac{x^{2}}{2}+o(x^{2})$，与分母 $x^{2}$ 同阶。",
            r"相除后取极限，得 $\dfrac{1}{2}$。",
        ],
        "solution": r"原式 $=\lim\limits_{x\to0}\dfrac{\frac{x^{2}}{2}+o(x^{2})}{x^{2}}=\dfrac{1}{2}$。",
        "alt_solution": r"也可用洛必达两次：$\lim\limits_{x\to0}\dfrac{e^{x}-1}{2x}=\lim\limits_{x\to0}\dfrac{e^{x}}{2}=\dfrac{1}{2}$。",
        "pitfalls": r"展开阶数不够。只展到一阶 $(e^{x}=1+x+o(x))$ 时分子是 $o(x)$，除不下 $x^{2}$，会得到错误的 $0$。分母是 $x^{2}$，分子就至少展到二阶。",
    },
    {
        "subject": "高等数学", "topic": "一元函数微分学", "kind": "normal",
        "note": "中值定理证明题：结论里出现导数",
        "cycle": "future",
        "stem": r"设 $f(x)$ 在 $[0,1]$ 上连续、在 $(0,1)$ 内可导，且 $f(0)=0$、$f(1)=1$。证明：存在 $\xi\in(0,1)$，使 $f'(\xi)=1$。",
        "knowledge": r"拉格朗日中值定理（见到「结论含导数」先想它）",
        "key_steps": [
            r"条件给的是**两个端点值**，结论是导数等于一个常数 → 直接试拉格朗日中值定理。",
            r"在 $[0,1]$ 上对 $f$ 用定理：存在 $\xi\in(0,1)$ 使 $f(1)-f(0)=f'(\xi)(1-0)$。",
            r"代入 $f(0)=0$、$f(1)=1$，即得 $f'(\xi)=1$。",
        ],
        "solution": r"由拉格朗日中值定理 $f(1)-f(0)=f'(\xi)\cdot1$，代入端点值即得 $f'(\xi)=1$。",
        "alt_solution": r"构造辅助函数 $\varphi(x)=f(x)-x$，则 $\varphi(0)=\varphi(1)=0$，用罗尔定理得 $\varphi'(\xi)=0$，同样得到 $f'(\xi)=1$。",
        "pitfalls": r"别养成「一提中值定理就构造辅助函数」的习惯。结论是常数时用拉格朗日最短；只有结论里带了 $\xi$（如 $f'(\xi)=\xi$）才非构造不可。",
    },
    {
        "subject": "高等数学", "topic": "一元函数积分学", "kind": "exam",
        "note": "分部积分 + 假分式先做除法",
        "cycle": "due",
        "stem": r"计算定积分 $\displaystyle\int_{0}^{1}x\ln(1+x)\,\mathrm{d}x$。",
        "knowledge": r"分部积分法（$u$ 取对数、$\mathrm{d}v$ 取幂函数）与假分式的多项式除法",
        "key_steps": [
            r"取 $u=\ln(1+x)$、$\mathrm{d}v=x\,\mathrm{d}x$，则 $\mathrm{d}u=\dfrac{\mathrm{d}x}{1+x}$、$v=\dfrac{x^{2}}{2}$。",
            r"原式 $=\left[\dfrac{x^{2}}{2}\ln(1+x)\right]_{0}^{1}-\dfrac{1}{2}\displaystyle\int_{0}^{1}\dfrac{x^{2}}{1+x}\,\mathrm{d}x$。",
            r"前一项 $=\dfrac{1}{2}\ln2$。被积函数是假分式，先做除法：$\dfrac{x^{2}}{1+x}=x-1+\dfrac{1}{1+x}$。",
            r"$\displaystyle\int_{0}^{1}\left(x-1+\dfrac{1}{1+x}\right)\mathrm{d}x=\left[\dfrac{x^{2}}{2}-x+\ln(1+x)\right]_{0}^{1}=\ln2-\dfrac{1}{2}$。",
        ],
        "solution": r"原式 $=\dfrac{1}{2}\ln2-\dfrac{1}{2}\left(\ln2-\dfrac{1}{2}\right)=\dfrac{1}{4}$。",
        "pitfalls": r"分子次数 ≥ 分母次数时**必须先做多项式除法**再积分 —— $\dfrac{x^{2}}{1+x}$ 直接硬积是积不出来的（这一步是本轮扣分最多的地方）。",
    },
    {
        "subject": "高等数学", "topic": "多元函数微分学", "kind": "normal",
        "note": "抽象复合函数的链式法则：记号别写错",
        "cycle": "future",
        "stem": r"设 $z=f(x^{2}-y^{2},\,e^{xy})$，其中 $f(u,v)$ 具有连续的一阶偏导数，求 $\dfrac{\partial z}{\partial x}$ 与 $\dfrac{\partial z}{\partial y}$。",
        "knowledge": r"多元复合函数求导（链式法则）+ 抽象函数偏导记号的规范写法",
        "key_steps": [
            r"把中间变量记清楚：$u=x^{2}-y^{2}$，$v=e^{xy}$，于是 $z=f(u,v)$。",
            r"$\dfrac{\partial z}{\partial x}=f'_{u}\dfrac{\partial u}{\partial x}+f'_{v}\dfrac{\partial v}{\partial x}$。",
            r"$\dfrac{\partial u}{\partial x}=2x$，$\dfrac{\partial v}{\partial x}=y e^{xy}$。",
            r"对 $y$ 同理：$\dfrac{\partial u}{\partial y}=-2y$，$\dfrac{\partial v}{\partial y}=x e^{xy}$。",
        ],
        "solution": r"$\dfrac{\partial z}{\partial x}=2x f'_{u}+y e^{xy}f'_{v}$，$\quad\dfrac{\partial z}{\partial y}=-2y f'_{u}+x e^{xy}f'_{v}$。",
        "pitfalls": r"抽象函数的偏导必须写在**中间变量**上（$f'_{u}$、$f'_{v}$），不能写成 $\dfrac{\partial f}{\partial x}$；也不能把 $u,v$ 回代成 $x,y$ 之后再求导。",
    },
    {
        "subject": "高等数学", "topic": "无穷级数", "kind": "exam",
        "note": "幂级数收敛域：两个端点要分别判",
        "cycle": "overdue",
        "stem": r"求幂级数 $\displaystyle\sum_{n=1}^{\infty}\dfrac{(x-1)^{n}}{n\cdot 3^{n}}$ 的收敛半径与收敛域。",
        "knowledge": r"幂级数的收敛半径（比值法）与端点收敛性的分别判别",
        "key_steps": [
            r"这是 $(x-1)$ 的幂级数，先换元 $t=x-1$，化为 $\displaystyle\sum_{n=1}^{\infty}\dfrac{t^{n}}{n\cdot3^{n}}$。",
            r"系数 $a_n=\dfrac{1}{n\cdot3^{n}}$，用比值法：$\lim\limits_{n\to\infty}\left|\dfrac{a_{n+1}}{a_n}\right|=\dfrac{1}{3}$，故收敛半径 $R=3$。",
            r"由 $|t|<3$ 得 $|x-1|<3$，即 $-2<x<4$。",
            r"查端点：$t=3$ 时级数为 $\displaystyle\sum\dfrac{1}{n}$，调和级数**发散**；$t=-3$ 时为 $\displaystyle\sum\dfrac{(-1)^{n}}{n}$，**条件收敛**。",
        ],
        "solution": r"收敛半径 $R=3$；收敛域为 $\left[-2,\,4\right)$。",
        "pitfalls": r"换元之后忘了把端点换回 $x$；以及两个端点一起下结论 —— 必须**分别**代进去判。",
    },
    {
        "subject": "高等数学", "topic": "微分方程", "kind": "normal",
        "note": "一阶线性非齐次 + 初值",
        "cycle": "future",
        "stem": r"求微分方程 $y'+2xy=2x e^{-x^{2}}$ 满足 $y(0)=1$ 的特解。",
        "knowledge": r"一阶线性非齐次微分方程的通解公式（常数变易法）",
        "key_steps": [
            r"化成标准形 $y'+P(x)y=Q(x)$：$P(x)=2x$，$Q(x)=2xe^{-x^{2}}$。",
            r"齐次方程 $y'+2xy=0$ 的解为 $y_h=Ce^{-x^{2}}$（这里 $\int P\,\mathrm{d}x=x^{2}$）。",
            r"常数变易：设 $y=C(x)e^{-x^{2}}$ 代回原方程，整理得 $C'(x)=2x$，故 $C(x)=x^{2}+C_1$。",
            r"通解 $y=(x^{2}+C_1)e^{-x^{2}}$；由 $y(0)=1$ 得 $C_1=1$。",
        ],
        "solution": r"特解 $y=(x^{2}+1)e^{-x^{2}}$。",
        "pitfalls": r"常数变易时会自动消掉一个 $y_h$ 项，若把那一步漏写就会多出一个无法消掉的项而卡住。另外 $y(0)=1$ 要最后代入，别提前。",
    },

    # ---------------- 线性代数 ----------------
    {
        "subject": "线性代数", "topic": "行列式", "kind": "exam",
        "note": "三对角行列式的递推",
        "cycle": "due",
        "stem": r"计算 $n$ 阶行列式 $D_n=\begin{vmatrix}2&1&0&\cdots&0\\1&2&1&\cdots&0\\0&1&2&\cdots&0\\\vdots&\vdots&\vdots&\ddots&\vdots\\0&0&0&\cdots&2\end{vmatrix}$。",
        "knowledge": r"三对角行列式的递推法：$D_n=2D_{n-1}-D_{n-2}$",
        "key_steps": [
            r"按第一行展开：$D_n=2D_{n-1}-\begin{vmatrix}1&1&0&\cdots\\0&2&1&\cdots\\\vdots&\vdots&\ddots\end{vmatrix}$，第二个行列式再按第一列展开恰为 $D_{n-2}$。",
            r"于是得递推式 $D_n=2D_{n-1}-D_{n-2}$，初值 $D_1=2$，$D_2=\begin{vmatrix}2&1\\1&2\end{vmatrix}=3$。",
            r"解递推：特征方程 $r^{2}-2r+1=0$ 有**二重根** $r=1$，通解写 $D_n=(A+Bn)\cdot1^{n}$。",
            r"代入初值：$A+B=2$，$A+2B=3$，解得 $A=B=1$。",
        ],
        "solution": r"$D_n=n+1$。",
        "pitfalls": r"特征方程有重根时，通解必须写成 $(A+Bn)r^{n}$；写成 $Ar^{n}+Br^{n}$ 等于只有一个待定系数，解不出两个初值。",
    },
    {
        "subject": "线性代数", "topic": "特征值与特征向量", "kind": "exam",
        "note": "行和相等型矩阵 + 能否对角化",
        "cycle": "future",
        "stem": r"设 $A=\begin{pmatrix}2&1&1\\1&2&1\\1&1&2\end{pmatrix}$，求 $A$ 的全部特征值，并判断 $A$ 能否对角化。",
        "knowledge": r"特征多项式的计算（行和相等型矩阵的一个特征值）+ 可对角化的判定",
        "key_steps": [
            r"观察**每一行元素之和都是 $4$** → $\lambda=4$ 必是特征值，对应特征向量 $(1,1,1)^{T}$。",
            r"再按定义算 $|\lambda I-A|=(\lambda-1)^{2}(\lambda-4)$。",
            r"故特征值为 $\lambda_1=\lambda_2=1$（二重），$\lambda_3=4$。",
            r"对 $\lambda=1$ 解 $(I-A)x=0$：由 $x_1+x_2+x_3=0$ 得自由未知量 $2$ 个 → 几何重数 $=2$。",
            r"几何重数 $2$ = 代数重数 $2$，且三个特征值全为实数 ⇒ 有 $3$ 个线性无关特征向量。",
        ],
        "solution": r"特征值 $1,\;1,\;4$；$\lambda=1$ 的几何重数等于代数重数，故 $A$ **可以**对角化。",
        "pitfalls": r"看到「有重根」就写「不可对角化」是错的。判据是**几何重数是否等于代数重数**，不是有没有重根。",
    },
    {
        "subject": "线性代数", "topic": "线性方程组", "kind": "normal",
        "note": "含参数的方程组：三种情形都要讨论到",
        "cycle": "mastered",
        "stem": r"讨论 $\lambda$ 取何值时，方程组 $\begin{cases}\lambda x_1+x_2+x_3=1\\x_1+\lambda x_2+x_3=\lambda\\x_1+x_2+\lambda x_3=\lambda^{2}\end{cases}$ 有唯一解、无解、有无穷多解。",
        "knowledge": r"含参数线性方程组解的判定：系数行列式是否为零，以及 $r(A)$ 与 $r(\bar{A})$ 的比较",
        "key_steps": [
            r"先算系数行列式：$|A|=(\lambda-1)^{2}(\lambda+2)$。",
            r"$\lambda\ne1$ 且 $\lambda\ne-2$ 时 $|A|\ne0$ → 有**唯一解**。",
            r"$\lambda=1$ 时把增广矩阵化行最简，得 $r(A)=r(\bar{A})=1<3$ → 有**无穷多解**。",
            r"$\lambda=-2$ 时 $r(A)=2$ 而 $r(\bar{A})=3$ → **无解**。",
        ],
        "solution": r"$\lambda\ne1$ 且 $\lambda\ne-2$：唯一解；$\lambda=1$：无穷多解；$\lambda=-2$：无解。",
        "pitfalls": r"算出 $|A|=0$ 就说「无穷多解」是最常见的错 —— 必须再比 $r(A)$ 与 $r(\bar{A})$，本题 $\lambda=-2$ 就是无解。",
    },
    {
        "subject": "线性代数", "topic": "二次型", "kind": "normal",
        "note": "正定性：必须用**顺序**主子式",
        "cycle": "out",
        "stem": r"判断二次型 $f(x_1,x_2,x_3)=x_1^{2}+x_2^{2}+5x_3^{2}+4x_1x_2$ 的正定性。",
        "knowledge": r"正定二次型的判定（顺序主子式全大于零）",
        "key_steps": [
            r"写出对称矩阵：$A=\begin{pmatrix}1&2&0\\2&1&0\\0&0&5\end{pmatrix}$（交叉项系数要除以 $2$）。",
            r"一阶顺序主子式 $|A_1|=1>0$。",
            r"二阶顺序主子式 $|A_2|=\begin{vmatrix}1&2\\2&1\end{vmatrix}=1-4=-3<0$。",
            r"顺序主子式不满足「全大于零」⇒ $f$ **不**正定。",
        ],
        "solution": r"不正定。因为二阶顺序主子式 $=-3<0$，正定的必要条件已被破坏（事实上 $f=(x_1+x_2)^{2}+4x_2^{2}+5x_3^{2}-4x_2^{2}$ 可看出存在负值方向）。",
        "pitfalls": r"两个坑：① 写矩阵时交叉项 $4x_1x_2$ 对应 $a_{12}=2$，不是 $4$；② 必须算**从左上角开始的**全部顺序主子式，随便挑一个主子式算是不成立的判据。",
    },

    # ---------------- 概率论与数理统计 ----------------
    {
        "subject": "概率论与数理统计", "topic": "多维随机变量", "kind": "exam",
        "note": "联合密度定常数 + 独立性必须验证矩形域",
        "cycle": "due",
        "stem": r"设 $(X,Y)$ 的联合概率密度为 $f(x,y)=\begin{cases}ke^{-(2x+y)},&x>0,\;y>0\\0,&\text{其他}\end{cases}$。求常数 $k$，并判断 $X$ 与 $Y$ 是否相互独立。",
        "knowledge": r"联合密度的归一性（求常数）与用边缘密度判定独立性",
        "key_steps": [
            r"由归一性 $\displaystyle\iint_{\mathbb{R}^{2}}f=1$：$\displaystyle\int_{0}^{+\infty}\!\!\!\int_{0}^{+\infty}ke^{-2x}e^{-y}\,\mathrm{d}x\,\mathrm{d}y=k\cdot\dfrac{1}{2}\cdot1=1$，得 $k=2$。",
            r"求边缘密度：$f_X(x)=\displaystyle\int_{0}^{+\infty}2e^{-2x}e^{-y}\mathrm{d}y=2e^{-2x}$（$x>0$）。",
            r"同理 $f_Y(y)=\displaystyle\int_{0}^{+\infty}2e^{-2x}e^{-y}\mathrm{d}x=e^{-y}$（$y>0$）。",
            r"验证：$f_X(x)f_Y(y)=2e^{-(2x+y)}=f(x,y)$ 对一切 $(x,y)$ 成立。",
        ],
        "solution": r"$k=2$；且 $f(x,y)=f_X(x)f_Y(y)$，故 $X$ 与 $Y$ **相互独立**。",
        "pitfalls": r"只看「密度里 $x$、$y$ 是分离开的」就断言独立 —— 还得确认**定义域也是矩形**。本题定义域 $x>0,y>0$ 是矩形，才成立。",
    },
    {
        "subject": "概率论与数理统计", "topic": "数字特征", "kind": "exam",
        "note": "线性变换的期望方差与相关系数",
        "cycle": "future",
        "stem": r"设随机变量 $X\sim N(1,4)$，$Y=2X-3$。求 $E(Y)$、$D(Y)$ 以及 $X$ 与 $Y$ 的相关系数 $\rho_{XY}$。",
        "knowledge": r"期望与方差的线性性质、相关系数的含义",
        "key_steps": [
            r"由 $X\sim N(1,4)$ 知 $E(X)=1$，$D(X)=4$。",
            r"$E(Y)=2E(X)-3=2\times1-3=-1$。",
            r"$D(Y)=2^{2}D(X)+D(3)=4\times4+0=16$（常数项方差为 $0$，但系数要平方）。",
            r"$Y$ 是 $X$ 的线性函数且斜率 $2>0$，两端同向变化，故 $\rho_{XY}=1$。",
        ],
        "solution": r"$E(Y)=-1$，$D(Y)=16$，$\rho_{XY}=1$。",
        "pitfalls": r"$D(2X-3)$ 里漏掉平方写成 $2D(X)$；以及以为相关系数要算 $\operatorname{Cov}$ 才行 —— 严格线性且同向时直接就是 $1$。",
    },
    {
        "subject": "概率论与数理统计", "topic": "数理统计", "kind": "normal",
        "note": "矩估计 + 最大似然（这条故意不给解析，用来试「AI 解析」按钮）",
        "cycle": "future",
        "analyzed": False,
        "stem": r"设总体 $X$ 的概率密度为 $f(x;\theta)=\begin{cases}\theta x^{\theta-1},&0<x<1\\0,&\text{其他}\end{cases}$，其中 $\theta>0$ 为未知参数，$X_1,X_2,\dots,X_n$ 是来自该总体的简单随机样本。求 $\theta$ 的矩估计量与最大似然估计量。",
    },
]


def clear(uid: int) -> int:
    with db.as_user(uid):
        rows = db.query(
            "SELECT id FROM math_problems WHERE user_id=? AND source_note LIKE ?",
            (uid, TAG + "%"),
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
        for i, it in enumerate(ITEMS, 1):
            p = math.create_problem(
                it["stem"],
                subject=it["subject"], topic=it["topic"], kind=it["kind"],
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
            rc = 5 if it["cycle"] == "mastered" else (2 if it["cycle"] == "future" else 0)
            db.execute(
                "UPDATE math_problems SET next_review=?, review_count=? WHERE id=? AND user_id=?",
                (plan[it["cycle"]], rc, pid, uid),
            )
            added.append((pid, it["subject"], it["topic"], it["kind"], it["cycle"]))
        return {"added": added, "stats": math.stats()}


def main() -> None:
    args = sys.argv[1:]
    uid = 1
    if "--user" in args:
        uid = int(args[args.index("--user") + 1])

    if "--clear" in args:
        n = clear(uid)
        print(f"已删除账号 {uid} 下带 `{TAG}` 备注的错题：{n} 条")
        with db.as_user(uid):
            print("剩余：", math.stats())
        return

    # 表结构由正在跑的服务负责，这里**不动 schema**（免得和线上进程抢写）
    with db.as_user(uid):
        before = math.stats()
    if before["total"]:
        print(f"⚠️ 账号 {uid} 已有 {before['total']} 条错题，本次是**追加**（不会覆盖）。\n")

    # 库先备份：这是正式库，写坏了要能退回去。
    # ⚠️ 别用 shutil.copy2 —— 库跑在 WAL 模式下，刚提交的数据还在 `-wal` 里，
    #    单拷 app.db 会得到一份**不含最新写入的旧库**（而且看着"备份成功"）。
    #    用 SQLite 自带的在线备份接口，一次拿到一致的单文件快照。
    src = ROOT / "data" / "app.db"
    if src.exists():
        from datetime import datetime as _dt
        bd = ROOT / "data" / ("backup_pre_seedmath_" + _dt.now().strftime("%Y%m%d_%H%M%S"))
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
    for pid, sub, top, kind, cyc in out["added"]:
        print(f"  #{pid:<4} {sub:<12} {top:<14} {math.KINDS[kind]:<8} {cyc}")
    s = out["stats"]
    print(f"\n错题总量 {s['total']} ｜ 今天该复习 {s['due']} ｜ 已掌握 {s['mastered']}"
          f" ｜ 按科目 {s['by_subject']}")
    print(f"（今天 = {s['today']}；清空示例数据：python tools/_seed_math.py --clear）")


if __name__ == "__main__":
    main()
