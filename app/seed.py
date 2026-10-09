"""首次启动时写入两天的完整示例内容。

目的：即使还没配置 AI 模型，也能立刻看到并走通「阅读 → 展开解析 → 词汇标记 → 仿写 → 数据记录」
的完整闭环，用于验收。这两篇内容是按考研英语一标准人工撰写的高质量样例，
你配置好模型后，可以在页面上点「重新生成」把它们换成 AI 实时生成的内容。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from . import db
from .config import BASE_DIR

SEEDS_DIR = BASE_DIR / "seeds"


def ensure_seed(force: bool = False) -> int:
    """只在「整个库一篇内容都没有」时灌示例。

    注意判重是全局的（跨所有账号），不是当前用户 —— 否则每建一个新账号都会灌一遍示例。
    写入时上下文应该是 db.as_local()，这样示例落在 0 号桶，第一个账号建好时认领走。
    """
    if not SEEDS_DIR.exists():
        return 0
    with db.as_system():
        has = db.query_one("SELECT COUNT(*) AS c FROM articles")
    if has and has["c"] and not force:
        return 0

    files = sorted(SEEDS_DIR.glob("*.json"))
    if not files:
        return 0

    today = date.today()
    n = 0
    for i, f in enumerate(files):
        try:
            pkg = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        d = (today - timedelta(days=i)).isoformat()
        pkg["date"] = d
        pkg["source_type"] = "demo"
        pkg["model"] = "内置示例内容"
        from .ai.pipeline import store_package

        store_package(pkg, replace=True)
        n += 1
    return n
