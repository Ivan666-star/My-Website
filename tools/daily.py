"""命令行入口：方便 Harness / 计划任务 / 手动直接调用，不必依赖网页。

用法：
    python -m tools.daily generate            生成今天的内容（已有则跳过）
    python -m tools.daily generate --force    强制重新生成今天的内容
    python -m tools.daily weekly              打印最近 7 天学习数据
    python -m tools.daily check               健康检查（进程内，不依赖服务已启动）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, seed  # noqa: E402
from app.ai.pipeline import generate_daily  # noqa: E402
from app.config import settings  # noqa: E402


def cmd_generate(args) -> int:
    db.init_db()
    seed.ensure_seed()
    if not settings.ai_ready:
        print("[x] 还没有配置 AI 模型，请在 .env 里填写 AI_API_KEY / AI_BASE_URL / AI_MODEL")
        return 2
    row = generate_daily(force=args.force, topic=args.topic)
    print(f"[ok] {row['date']} 《{row['title']}》 {row['word_count']} 词 难度 {row['difficulty']}")
    return 0


def cmd_weekly(_args) -> int:
    db.init_db()
    s = db.weekly_stats(7)
    print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_check(_args) -> int:
    db.init_db()
    today = db.get_article_by_date(db.today_str())
    print(f"今天日期      : {db.today_str()}")
    print(f"今日内容      : {'已就绪 → ' + today['title'] if today else '尚未生成'}")
    print(f"AI 模型       : {settings.ai_model if settings.ai_ready else '未配置'}")
    print(f"数据库        : {settings.db_path}")
    print(f"连续学习天数  : {db.streak_days()}")
    print(f"累计文章数    : {db.query_one('SELECT COUNT(*) c FROM articles')['c']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="daily", description="Prism · AI 考研英语一训练系统 · 命令行工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="生成每日内容")
    g.add_argument("--force", action="store_true", help="已有内容时强制重新生成")
    g.add_argument("--topic", default=None, help="指定主题（中文）")
    g.set_defaults(func=cmd_generate)

    sub.add_parser("weekly", help="输出最近 7 天学习数据").set_defaults(func=cmd_weekly)
    sub.add_parser("check", help="健康检查").set_defaults(func=cmd_check)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
