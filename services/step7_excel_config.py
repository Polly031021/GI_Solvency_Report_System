from __future__ import annotations
from dataclasses import dataclass, field
import json
import pandas as pd

# ★ 直接引用 chart_plans 里的常量，两处字符串永远保持一致
from .solvency_step7_chart_plans import (
    SINGLE_METRIC_TREND,
    TREND_WITH_COMPANY_BARS,
    COMPANY_BAR_TREND,
    COMPANY_PERIOD_BAR,
    SOLVENCY_RATIO_COMBO,
    CAPITAL_AMOUNT_COMBO,
    CAPITAL_RATIO_COMBO,
    CAPITAL_STRUCTURE_COMBO,
    CAPITAL_EFFICIENCY_BUBBLE,
    COMPONENT_STACK,
    EFFECT_DIVERGING,
    SOLVENCY_MATRIX,
    MARKET_CREDIT_MATRIX,
    RISK_RATIO_SCATTER,
    KEY_METRICS_TABLE,
    FINANCING_TABLE,
    PENDING_DEFINITION,
    CAPABILITY_RADAR,          # ★ 新增
    UNDERWRITING_EFFICIENCY_SCATTER,
    STRUCTURE_CHANGE_CHART,
    PREMIUM_GROWTH_STRUCTURE,
    PERFORMANCE_PATTERN_GRID,
    INDUSTRY_PREMIUM_STRUCTURE,  # ★ 新增：行业整体保费规模与结构
    INDUSTRY_MARKET_SHARE,  # ★ 新增：行业市场份额
    COMPANY_LINE_TREND,        # ★ 新增：公司折线趋势图
)


CHART_KIND_LABELS = {
    "单指标趋势图": SINGLE_METRIC_TREND,
    "公司柱状图": COMPANY_BAR_TREND,
    "趋势+公司柱状": TREND_WITH_COMPANY_BARS,
    "关键指标表": KEY_METRICS_TABLE,
    "重大融资表": FINANCING_TABLE,
    "偿付能力组合图": SOLVENCY_RATIO_COMBO,
    "资本充足率组合图": CAPITAL_RATIO_COMBO,
    "资本结构组合图": CAPITAL_STRUCTURE_COMBO,
    "构成堆叠图": COMPONENT_STACK,
    "资本金额堆叠": CAPITAL_AMOUNT_COMBO,
    "资本效率气泡图": CAPITAL_EFFICIENCY_BUBBLE,
    "偿付能力矩阵": SOLVENCY_MATRIX,
    "市场信用矩阵": MARKET_CREDIT_MATRIX,
    "风险散点图": RISK_RATIO_SCATTER,
    "效应发散图": EFFECT_DIVERGING,
    "公司分期柱": COMPANY_PERIOD_BAR,
    "能力雷达图": CAPABILITY_RADAR,     # ★ 新增
    "承保效益散点图": UNDERWRITING_EFFICIENCY_SCATTER,
    "结构变化组合图": STRUCTURE_CHANGE_CHART,
    "保费增速结构图": PREMIUM_GROWTH_STRUCTURE,
    "保费增速组合图": PREMIUM_GROWTH_STRUCTURE,
    "业绩模式分列图": PERFORMANCE_PATTERN_GRID,
    "行业保费规模与结构图": INDUSTRY_PREMIUM_STRUCTURE,  # ★ 新增
    "行业整体保费规模与结构图": INDUSTRY_PREMIUM_STRUCTURE,
    "行业市场份额图": INDUSTRY_MARKET_SHARE,
    "公司折线趋势图": COMPANY_LINE_TREND,   # ★ 新增
    "待定": PENDING_DEFINITION,
}


@dataclass
class Step7Module:
    module_id: str
    level_one: str
    level_two: str  # "" 表示没有二级，直接展示图表
    chart_name: str
    chart_kind: str          # 对应上面的英文枚举
    metric_codes: tuple[str, ...] = ()
    chart_config: dict = field(default_factory=dict)
    requires_all: bool = False       # ★ 新增
    analysis_default: str = ""
    analysis_custom: str = ""
    note: str = ""
    image_file: str = ""


def _clean(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _parse_codes(v) -> tuple[str, ...]:
    s = _clean(v)
    if not s:
        return ()
    parts = [x.strip() for x in s.replace("；", ",").replace("，", ",").split(",")]
    return tuple(x for x in parts if x)


def _parse_json(v) -> dict:
    s = _clean(v)
    if not s or s.lower() in ("nan", "none", "null"):
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def _parse_bool(v) -> bool:
    """把 Excel 里的 TRUE/是/Y/1 解析为 True；其余为 False。"""
    s = _clean(v).upper()
    return s in ("TRUE", "1", "是", "Y", "YES")


def load_step7_config(source) -> list[Step7Module]:
    """source: 文件路径 / bytes / file-like / URL。返回按 Excel 行顺序的模块列表。"""
    df = pd.read_excel(source, dtype=str).fillna("")
    modules: list[Step7Module] = []
    for _, r in df.iterrows():
        module_id = _clean(r.get("模块ID"))
        if not module_id:
            continue
        level_one = _clean(r.get("一级分类"))
        level_two = _clean(r.get("二级分类"))
        if level_two in ("全部", ""):
            level_two = ""
        kind_label = _clean(r.get("图表类型"))
        chart_kind = CHART_KIND_LABELS.get(kind_label, "")
        modules.append(Step7Module(
            module_id=module_id,
            level_one=level_one,
            level_two=level_two,
            chart_name=_clean(r.get("对应图表名称")) or module_id,
            chart_kind=chart_kind,
            metric_codes=_parse_codes(r.get("指标编码")),
            chart_config=_parse_json(r.get("图表配置(JSON)")),
            requires_all=_parse_bool(r.get("必须全部指标")),    # ★ 新增
            analysis_default=_clean(r.get("分析内容-默认")),
            analysis_custom=_clean(r.get("分析内容-自定义")),
            note=_clean(r.get("注释内容")),
            image_file=_clean(r.get("图片文件名")),
        ))
    return modules
