"""全局配置：从 .env 读取，所有敏感信息不写进代码。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    def __init__(self) -> None:
        # ---- AI Provider（OpenAI 兼容协议，DeepSeek / 智谱 / 通义 / Kimi / OpenAI 均可）----
        self.ai_base_url = os.getenv("AI_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.ai_api_key = (os.getenv("AI_API_KEY") or "").strip()
        # 日常内容生成用便宜模型
        self.ai_model = os.getenv("AI_MODEL", "deepseek-chat").strip()
        # 质量敏感环节（长难句分析、作文批改）可用更强模型，留空则复用上面
        self.ai_model_strong = (os.getenv("AI_MODEL_STRONG") or "").strip() or self.ai_model
        # 拍照识图（数学/信号错题）单独指定模型：留空则复用 ai_model。
        # 为什么要单独一项：能识图的模型未必是最适合生成文章的，反过来也一样 ——
        # 实测 deepseek-flash 能读图，而 deepseek-v4-pro / 旧的 deepseek-chat 会回
        # 「无法读取图片」。以后接口里出现更强的视觉模型，改这一行即可，不用动代码。
        self.ai_model_vision = (os.getenv("AI_MODEL_VISION") or "").strip() or self.ai_model
        self.ai_timeout = int(os.getenv("AI_TIMEOUT", "240"))
        self.ai_temperature = float(os.getenv("AI_TEMPERATURE", "0.8"))

        # ---- 服务 ----
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8848"))

        # ---- 数据 ----
        self.db_path = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "app.db")))

        # ---- 访问口令 ----
        # 还没有任何账号时，这个东西就是整站的口令门（沿用最早那套设计）。
        # 留空 = 不设防，本地自用行为不变。
        self.access_password = (os.getenv("ACCESS_PASSWORD") or "").strip()
        # ---- 初始化码 ----
        # 创建「第一个账号（管理员）」时需要它，防止公网上被别人抢先占掉管理员。
        # 留空则退而使用 ACCESS_PASSWORD；两者都空就自动生成一个（写 data/setup_code.txt
        # 并在启动日志里打印）。本机直连（127.0.0.1 且无任何反代头）免码。
        self.setup_code = (os.getenv("SETUP_CODE") or "").strip()

        # ---- 自助注册 ----
        # 别人可以自己在 /register 填表申请，但建出来的是「待批准」状态，
        # 管理员在「设置 → 用户管理」里点头之后才能真正登录。
        # 想关掉注册入口就设 ALLOW_SELF_REGISTER=0（已存在的账号不受影响）。
        self.allow_self_register = (os.getenv("ALLOW_SELF_REGISTER", "1") or "1").strip().lower() not in (
            "0", "false", "no", "off",
        )

        # ---- 学习策略 ----
        # 每天几点自动准备当天内容（24 小时制）
        self.daily_hour = int(os.getenv("DAILY_GENERATE_HOUR", "6"))
        # 当前阶段：基础期 / 强化期 / 冲刺期，影响内容配比
        self.stage = os.getenv("STAGE", "基础期").strip()
        # ★ 难度下限：英一标准档。可以更难，绝不允许比英一简单（用户硬要求）。
        # 想临时放宽就把这个值调小，但正常不该动。
        self.exam_min_difficulty = int(os.getenv("EXAM_MIN_DIFFICULTY", "3"))
        # 初始难度基准 1-5 —— 被下限夹住，填 1 或 2 也上不来低于英一
        self.base_difficulty = max(
            self.exam_min_difficulty, int(os.getenv("BASE_DIFFICULTY", "3"))
        )
        # 目标考试年份（12 月考试）。真题的红线提示、年份下拉都从这里推，别在别处写死。
        self.exam_target_year = int(os.getenv("EXAM_TARGET_YEAR", "2027"))

    @property
    def ai_ready(self) -> bool:
        return bool(self.ai_api_key)

    @property
    def exam_latest_year(self) -> int:
        """能拿到的最新一套真题 = 目标年份的前一年（今年 12 月那套还没考）。"""
        return self.exam_target_year - 1

    @property
    def exam_redline_from(self) -> int:
        """最近 3 年的真题：留到最后做全真模考，平时别精读掉。"""
        return self.exam_latest_year - 2

    def exam_year_range(self) -> list[int]:
        """年份下拉的可选项：最新一套 → 最早一套。"""
        return list(range(self.exam_latest_year, 1997, -1))


settings = Settings()

# 真题篇目（导入真题用）。键会拼进 articles.date（exam-2010-text2 这种伪日期），
# 所以要稳定、可读、不含空格。
EXAM_SLOTS = {
    "cloze": "完形填空",
    "text1": "阅读 Text 1",
    "text2": "阅读 Text 2",
    "text3": "阅读 Text 3",
    "text4": "阅读 Text 4",
    "new": "新题型",
    "trans": "翻译",
}

# 外刊常见题材池
TOPIC_POOL = [
    "科技与人工智能", "教育", "社会环境", "经济与就业", "气候与环境",
    "心理学与人类行为", "医疗与公共健康", "科学研究", "文化与历史",
    "工作方式变化", "青年成长", "媒体与信息传播",
]

DIFFICULTY_LABEL = {
    1: "入门 · 低于英一（默认已禁用）",
    2: "偏易 · 低于英一（默认已禁用）",
    3: "标准 · 考研英语一（难度下限）",
    4: "偏难 · 英语一高分段",
    5: "挑战 · 英语一最难档",
}


# ============================================================
# 素材文库分类体系（V2：双库 + 主题 + 位置 + 功能 + 文体）
# ------------------------------------------------------------
# 收藏一条素材时，AI 会按这套体系自动归类，并明确告诉用户
# 「这条素材该用在作文的开头、过渡、主体还是结尾」，
# 以及「它属于什么文体」。
# ============================================================

# 一级：两个库
MATERIAL_LIBRARIES = {
    "writing": "作文素材库",
    "reading": "阅读积累库",
}

MATERIAL_LIBRARY_DESC = {
    "writing": "能直接写进作文的句型、搭配、观点表达与金句",
    "reading": "为读懂服务的词汇释义与长难句拆解",
}

# 二级：作文中的位置（用户最关心的一维）
MATERIAL_POSITIONS = {
    "opening": "开头段",
    "transition": "过渡段",
    "body": "主体段",
    "ending": "结尾段",
    "universal": "通用",
}

# 每个位置的固定使用口径——文案直接回答「用在开头还是结尾」
MATERIAL_POSITION_USAGE = {
    "opening": "用在开头段：引出话题 / 描述现象 / 点出争议",
    "transition": "用在过渡段：承上启下，衔接前后两段观点",
    "body": "用在主体段：举例、对比、因果或让步论证",
    "ending": "用在结尾段：总结观点、提出建议或升华主题",
    "universal": "通用：开头、主体、结尾都能用",
}

MATERIAL_POSITIONS_ORDER = ["opening", "transition", "body", "ending", "universal"]

# 三级：主题分类
MATERIAL_CATEGORIES = [
    "教育学习", "科技发展", "社会现象", "环境保护", "文化交流",
    "人生哲理", "经济就业", "品质精神", "通用表达",
]

# 三级：修辞功能
MATERIAL_FUNCTIONS = [
    "引出话题", "描述现象", "提出观点", "举例论证", "对比论证", "因果分析",
    "让步转折", "总结归纳", "提出建议", "呼吁行动", "情感升华",
    "精准释义", "句子结构",
]

# 四级：文体（体裁）—— 这条素材适合写进哪一类文章
MATERIAL_GENRES = {
    "argumentative": "议论文",
    "expository": "说明文",
    "practical": "应用文",
    "descriptive": "描写文",
    "narrative": "记叙文",
    "universal": "通用文体",
}

MATERIAL_GENRES_ORDER = [
    "argumentative", "expository", "practical", "descriptive", "narrative", "universal",
]

# 每种文体的一句话说明——直接回答「什么时候能拿它出来用」
MATERIAL_GENRE_HINT = {
    "argumentative": "议论文（大作文主体）：立论、分析原因、论证观点、提出建议",
    "expository": "说明文：客观陈述现象、数据、趋势，如「According to the chart…」",
    "practical": "应用文（小作文）：书信、邮件、通知、告示、备忘录、纪要专用措辞",
    "descriptive": "描写文：图画、场景、人物描写，常用于大作文首段「As is vividly depicted…」",
    "narrative": "记叙文：讲述经历或案例，可放进举例论证里",
    "universal": "通用文体：议论文、说明文、应用文都能用",
}

# 文体给人的「能不能用在大作文里」直觉
MATERIAL_GENRE_TONE = {
    "argumentative": "formal",
    "expository": "formal",
    "practical": "practical",
    "descriptive": "formal",
    "narrative": "neutral",
    "universal": "neutral",
}

# 提分等级
MATERIAL_LEVELS = {
    "A": "亮点表达",
    "B": "常用表达",
    "C": "理解型",
}

# 素材条目类型
MATERIAL_ITEM_TYPES = {
    "vocab": "词汇",
    "phrase": "短语搭配",
    "pattern": "句型模板",
    "sentence": "长难句",
    "expression": "句子表达",
}
