"""STEP7 公司级偿付能力对标报告包。

对外只暴露 show_step_7_solvency；其余模块按职责拆分：
specs 常量、theme 样式、selection 筛选、rendering 单指标图表、
plans 多指标组合图、fragments 联动气泡图、ai_analysis AI 点评、
underwriting 承保效益散点图。
"""

from .report import show_step_7_solvency

__all__ = ["show_step_7_solvency"]
