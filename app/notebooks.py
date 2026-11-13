"""两个错题本共用功能实现，但科目范围、任务和进度分别保存。"""

MODULES = {
    "math": ["高等数学", "线性代数", "概率论与数理统计", ""],
    "sig": ["信号与系统"],
}


def subjects_for(module: str) -> list[str]:
    if module not in MODULES:
        raise ValueError("请选择数学或信号与系统题库")
    return MODULES[module]
