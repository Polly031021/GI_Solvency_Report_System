"""STEP7 报告的中英文展示层。

本模块只负责“展示文案”，不改动任何指标编码、公司名称或底层数据：
- 中文/英文语言状态与切换；
- 指标、图表、模块、期间口径、单位、同业分类的英文名称；
- Altair / Plotly 图表的标题、坐标轴、图例、悬浮提示翻译；
- 表格列名与常见枚举值翻译；
- Excel 默认分析内容的英文对照。

语言状态由 Step7 页面上的“展示语言 / Display language”控件写入
st.session_state['s7_language']；没有 Streamlit 运行时（例如单元测试）时
默认返回中文，保证原有行为不变。
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

import pandas as pd

try:  # pragma: no cover - Streamlit is available in the app, not necessarily in tests
    import streamlit as st
except Exception:  # pragma: no cover
    st = None


LANG_ZH = "zh"
LANG_EN = "en"
LANGUAGE_LABELS = {"中文": LANG_ZH, "English": LANG_EN}
LANGUAGE_OPTIONS = ("中文", "English")
LANGUAGE_STATE_KEY = "s7_language"


# ---------------------------------------------------------------------------
# 语言状态
# ---------------------------------------------------------------------------
def get_language() -> str:
    if st is None:
        return LANG_ZH
    try:
        value = st.session_state.get(LANGUAGE_STATE_KEY, LANG_ZH)
    except Exception:
        return LANG_ZH
    return LANG_EN if str(value).lower().startswith("en") else LANG_ZH


def set_language(language: str) -> None:
    if st is None:
        return
    value = LANG_EN if str(language).lower().startswith("en") else LANG_ZH
    st.session_state[LANGUAGE_STATE_KEY] = value


def is_english(language: str | None = None) -> bool:
    return (language or get_language()) == LANG_EN


def t(zh: str, en: str, language: str | None = None) -> str:
    """Return the English text when English display is active."""
    return en if is_english(language) else zh


def language_label(language: str | None = None) -> str:
    return "English" if is_english(language) else "中文"


# ---------------------------------------------------------------------------
# 指标名称
# ---------------------------------------------------------------------------
# 只覆盖 Step7 常用指标；未列出的编码使用编码本身按英文单词展开，避免回退成中文。
ENGLISH_METRIC_NAMES: dict[str, str] = {
    "RECOGNIZED_ASSETS": "Recognised assets",
    "RECOGNIZED_LIABILITIES": "Recognised liabilities",
    "ACTUAL_CAPITAL": "Actual capital",
    "CORE_T1_CAPITAL": "Core tier 1 capital",
    "CORE_T2_CAPITAL": "Core tier 2 capital",
    "ANC_T1_CAPITAL": "Ancillary tier 1 capital",
    "ANC_T2_CAPITAL": "Ancillary tier 2 capital",
    "MINIMUM_CAPITAL": "Minimum capital",
    "QUANT_RISK_CAPITAL": "Quantitative risk minimum capital",
    "CONTROL_RISK_CAPITAL": "Control risk minimum capital",
    "ADDITIONAL_CAPITAL": "Additional capital",
    "CORE_SOLVENCY_SURPLUS": "Core solvency surplus",
    "COMBINED_SOLVENCY_SURPLUS": "Combined solvency surplus",
    "CORE_SOLVENCY_RATIO": "Core solvency ratio",
    "COMBINED_SOLVENCY_RATIO": "Combined solvency ratio",
    "NET_PROFIT": "Net profit",
    "NET_ASSETS": "Net assets",
    "TOTAL_ASSETS": "Total assets",
    "ROE": "Return on equity (ROE)",
    "ROA": "Return on assets (ROA)",
    "PREMIUM_GROWTH": "Gross premium growth",
    "SIGNED_PREMIUM": "Signed premium",
    "SIGNED_PREMIUM_MOTOR": "Motor signed premium",
    "SIGNED_PREMIUM_NON_MOTOR_TOP5": "Top 5 non-motor signed premium",
    "INSURANCE_REVENUE": "Insurance revenue",
    "COMBINED_RATIO": "Combined ratio",
    "EXPENSE_RATIO": "Expense ratio",
    "LOSS_RATIO": "Loss ratio",
    "REGISTERED_CAPITAL": "Registered capital",
    "INSURANCE_RISK_CAPITAL": "Life insurance risk minimum capital",
    "NON_LIFE_INSURANCE_RISK_CAPITAL": "Non-life insurance risk minimum capital",
    "MARKET_RISK_CAPITAL": "Market risk minimum capital",
    "CREDIT_RISK_CAPITAL": "Credit risk minimum capital",
    "SPREAD_RISK_CAPITAL": "Spread risk minimum capital",
    "COUNTERPARTY_RISK_CAPITAL": "Counterparty default risk minimum capital",
    "SURRENDER_RISK_CAPITAL": "Surrender risk minimum capital",
    "LOSS_OCCURRENCE_RISK_CAPITAL": "Loss occurrence risk minimum capital",
    "EXPENSE_RISK_CAPITAL": "Expense risk minimum capital",
    "LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT": "Life insurance risk diversification effect",
    "QUANT_RISK_DIVERSIFICATION_EFFECT": "Quantitative risk diversification effect",
    "CONTRACT_LOSS_ABSORPTION_EFFECT": "Contractual loss absorption effect",
    "REAL_ESTATE_RISK_CAPITAL": "Real estate price risk minimum capital",
    "OVERSEAS_FIXED_INCOME_RISK_CAPITAL": "Overseas fixed-income price risk minimum capital",
    "OVERSEAS_EQUITY_RISK_CAPITAL": "Overseas equity price risk minimum capital",
    "FOREIGN_EXCHANGE_RISK_CAPITAL": "Foreign exchange risk minimum capital",
    "MARKET_RISK_DIVERSIFICATION_EFFECT": "Market risk diversification effect",
    "CREDIT_RISK_DIVERSIFICATION_EFFECT": "Credit risk diversification effect",
    "INTEREST_RATE_RISK_CAPITAL": "Interest rate risk minimum capital",
    "EQUITY_RISK_CAPITAL": "Equity price risk minimum capital",
    "POLICY_SURPLUS_CORE_T1": "Policy future surplus included in core tier 1 capital",
    "POLICY_SURPLUS_CORE_T2": "Policy future surplus included in core tier 2 capital",
    "POLICY_SURPLUS_ANC_T1": "Policy future surplus included in ancillary tier 1 capital",
    "POLICY_SURPLUS_ANC_T2": "Policy future surplus included in ancillary tier 2 capital",
    "POLICY_SURPLUS_CORE_TO_CORE_CAPITAL": "Policy future surplus / core capital",
    "ACTUAL_CAPITAL_TO_RECOGNIZED_ASSETS": "Actual capital / recognised assets",
    "CORE_CAPITAL_TO_REGISTERED_CAPITAL": "Core capital / registered capital",
    "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL": "Life insurance risk / quantitative risk capital",
    "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL": "Non-life insurance risk / quantitative risk capital",
    "MARKET_RISK_TO_QUANT_CAPITAL": "Market risk / quantitative risk capital",
    "CREDIT_RISK_TO_QUANT_CAPITAL": "Credit risk / quantitative risk capital",
    "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL": "Diversification effect / quantitative risk capital",
    "LOSS_ABSORPTION_TO_QUANT_CAPITAL": "Loss absorption / quantitative risk capital",
    "LIFE_INSURANCE_RISK_TO_LIABILITIES": "Life insurance risk / recognised liabilities",
    "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES": "Non-life insurance risk / recognised liabilities",
    "INTEREST_RATE_RISK_TO_ASSETS": "Interest rate risk / recognised assets",
    "EQUITY_RISK_TO_ASSETS": "Equity price risk / recognised assets",
    "SPREAD_RISK_TO_ASSETS": "Spread risk / recognised assets",
    "COUNTERPARTY_RISK_TO_ASSETS": "Counterparty risk / recognised assets",
    "PREMIUM_INCOME": "Premium income",
    "UNDERWRITING_PROFIT": "Underwriting profit",
    "UNDERWRITING_PROFIT_AMOUNT": "Underwriting profit",
}

# 同一中文指标名在不同来源中的兜底对照。
ENGLISH_METRIC_NAME_BY_ZH: dict[str, str] = {
    "认可资产": "Recognised assets",
    "认可负债": "Recognised liabilities",
    "实际资本": "Actual capital",
    "最低资本": "Minimum capital",
    "核心一级资本": "Core tier 1 capital",
    "核心二级资本": "Core tier 2 capital",
    "附属一级资本": "Ancillary tier 1 capital",
    "附属二级资本": "Ancillary tier 2 capital",
    "核心偿付能力充足率": "Core solvency ratio",
    "综合偿付能力充足率": "Combined solvency ratio",
    "净利润": "Net profit",
    "总资产": "Total assets",
    "净资产": "Net assets",
    "签单保费": "Signed premium",
    "车险签单保费": "Motor signed premium",
    "综合成本率": "Combined ratio",
    "注册资本": "Registered capital",
    "保费增速": "Premium growth",
    "保费收入": "Premium income",
    "收入": "Revenue",
    "利润": "Profit",
    "净利润率": "Net profit margin",
    "核心资本": "Core capital",
    "附属资本": "Ancillary capital",
}


def english_from_code(code: str) -> str:
    """Build a readable English label from a metric code."""
    words = [word for word in str(code or "").split("_") if word]
    if not words:
        return ""
    acronyms = {"ROE", "ROA", "EPS", "LCR"}
    rendered = []
    for word in words:
        if word in acronyms:
            rendered.append(word)
        elif word.isdigit():
            rendered.append(word)
        else:
            rendered.append(word.capitalize())
    return " ".join(rendered)


def metric_name(code: str, zh_name: str = "", language: str | None = None) -> str:
    if not is_english(language):
        return str(zh_name or code)
    key = str(code or "").strip()
    if key in ENGLISH_METRIC_NAMES:
        return ENGLISH_METRIC_NAMES[key]
    name = str(zh_name or "").strip()
    if name in ENGLISH_METRIC_NAME_BY_ZH:
        return ENGLISH_METRIC_NAME_BY_ZH[name]
    if key:
        return english_from_code(key)
    return name


def translate_metric_frame(frame: pd.DataFrame, language: str | None = None) -> pd.DataFrame:
    """Return a copy of the analysis frame with English metric names for display."""
    if not is_english(language) or not isinstance(frame, pd.DataFrame) or frame.empty:
        return frame
    result = frame.copy()
    if "指标名称" not in result.columns:
        return result
    if "指标编码" in result.columns:
        result["指标名称"] = [
            metric_name(code, name, language)
            for code, name in zip(result["指标编码"], result["指标名称"])
        ]
    else:
        result["指标名称"] = [
            ENGLISH_METRIC_NAME_BY_ZH.get(str(name).strip(), str(name))
            for name in result["指标名称"]
        ]
    return result


# ---------------------------------------------------------------------------
# 图表、模块、分类名称
# ---------------------------------------------------------------------------
CHART_NAMES_EN: dict[str, str] = {
    "行业整体保费规模与结构": "Industry premium scale and structure",
    "分险种保费收入": "Premium by line of business",
    "行业市场份额": "Industry market share",
    "承保效益": "Underwriting efficiency",
    "偿付能力披露整体情况": "Solvency disclosure overview",
    "能力五边形": "Capability pentagon",
    "公司业绩展示": "Company performance",
    "综合偿付能力充足率": "Combined solvency ratio",
    "核心偿付能力充足率": "Core solvency ratio",
    "核心资本占比": "Core capital share",
    "保费增速": "Premium growth",
    "关键偿付数据概览": "Key solvency data overview",
    "承保规模": "Underwriting scale",
    "经营效益": "Operating efficiency",
    "资本规模": "Capital scale",
    "资本规模与结构": "Capital scale and structure",
    "核心资本明细占比": "Core capital composition",
    "附属资本明细占比": "Ancillary capital composition",
    "量化风险最低资本构成": "Quantitative risk minimum capital composition",
    "保险风险（非寿）/认可负债率": "Non-life insurance risk / recognised liabilities",
    "各类市场风险占比": "Market risk composition",
    "利率风险/认可资产率": "Interest rate risk / recognised assets",
    "权益价格风险/认可资产率": "Equity price risk / recognised assets",
    "各类信用风险占比": "Credit risk composition",
    "利差风险/认可资产率": "Spread risk / recognised assets",
    "对手违约风险/认可资产率": "Counterparty risk / recognised assets",
    "增资发债信息统计": "Capital raising and bond issuance",
    "资本使用效率与核心资本占比气泡图": "Capital efficiency and core capital share bubble chart",
    "核心资本/注册资本": "Core capital / registered capital",
    "实际资本/认可资产率": "Actual capital / recognised assets",
    "核心及综合充足率": "Core and combined solvency ratios",
    "综合充足率变化": "Combined solvency ratio change",
    "核心充足率变化": "Core solvency ratio change",
    "资本使用效率": "Capital efficiency",
    "行业资本分级": "Industry capital tiers",
    "核心资本": "Core capital",
    "附属资本": "Ancillary capital",
    "风险构成情况": "Risk composition",
    "保险风险": "Insurance risk",
    "市场风险": "Market risk",
    "信用风险": "Credit risk",
    "重大融资信息": "Major financing",
    "公司分期柱": "Company period bars",
    "效应发散": "Effect divergence",
    "市场信用矩阵": "Market–credit matrix",
    "自定义风险散点": "Custom risk scatter",
    "核心资本明细占比-待定": "Core capital composition – to be defined",
    "附属资本明细占比-待定": "Ancillary capital composition – to be defined",
    "利率与权益价格风险占认可资产率气泡图": "Interest rate and equity price risk / recognised assets bubble chart",
    "利差与对手违约风险占认可资产率气泡图": "Spread and counterparty risk / recognised assets bubble chart",
    "寿险与非寿险保险风险占认可负债率气泡图": "Life and non-life insurance risk / recognised liabilities bubble chart",
    "综合偿付能力充足率变化": "Combined solvency ratio change",
    "核心偿付能力充足率变化": "Core solvency ratio change",
}

LEVEL_NAMES_EN: dict[str, str] = {
    "行业概览": "Industry overview",
    "行业对标与趋势研判": "Peer benchmarking and trends",
    "偿付能力水平深度拆解": "Solvency deep dive",
    "经营与偿付能力联动分析": "Operating and solvency linkage",
    "公司规模与结构": "Company scale and structure",
    "行业画像": "Industry profile",
    "附录": "Appendix",
    "规模与发展": "Scale and development",
    "分险种保费收入": "Premium by line of business",
    "市场份额": "Market share",
    "承保效益": "Underwriting efficiency",
    "偿付能力披露整体情况": "Solvency disclosure overview",
    "能力框架-横向对标": "Capability framework – peer benchmarking",
    "经营表现-横向对标": "Operating performance – peer benchmarking",
    "核心指标速览": "Key metrics at a glance",
    "实际资本变动": "Actual capital movement",
    "实际资本/认可资产率": "Actual capital / recognised assets",
    "最低资本变动": "Minimum capital movement",
    "保费增速": "Premium growth",
    "关键偿付数据概览": "Key solvency data overview",
    "实际资本数据对比-行业资本分级": "Actual capital comparison – industry capital tiers",
    "实际资本数据对比-核心资本": "Actual capital comparison – core capital",
    "实际资本数据对比-附属资本": "Actual capital comparison – ancillary capital",
    "最低资本数据对比-风险构成情况": "Minimum capital comparison – risk composition",
    "实际资本数据对比-保险风险": "Actual capital comparison – insurance risk",
    "实际资本数据对比-市场风险": "Actual capital comparison – market risk",
    "实际资本数据对比-信用风险": "Actual capital comparison – credit risk",
    "重大融资信息": "Major financing",
    "全部": "All",
    "一键显示全部（打印/导出）": "Show all (print/export)",
    "行业整体偿付能力概览": "Industry solvency overview",
    "实际资本数据对比": "Actual capital comparison",
    "最低资本数据对比": "Minimum capital comparison",
    "附录": "Appendix",
}

CHART_KIND_NAMES_EN: dict[str, str] = {
    "行业保费规模与结构图": "Industry premium scale and structure",
    "分险种保费收入图": "Premium by line of business",
    "分险种保费收入组合图": "Premium by line of business",
    "行业市场份额图": "Industry market share",
    "承保效益散点图": "Underwriting efficiency scatter",
    "偿付能力整体情况卡片": "Solvency overview cards",
    "能力雷达图": "Capability radar",
    "业绩模式分列图": "Performance pattern grid",
    "资本充足率组合图": "Capital adequacy combination",
    "资本结构组合图": "Capital structure combination",
    "公司折线趋势图": "Company line trend",
    "保费增速结构图": "Premium growth structure",
    "关键指标表": "Key metrics table",
    "结构变化组合图": "Structural change combination",
    "资本金额堆叠": "Capital amount stack",
    "构成堆叠图": "Composition stack",
    "单指标趋势图": "Single metric trend",
    "重大融资表": "Major financing table",
    "资本效率气泡图": "Capital efficiency bubble",
    "公司柱状图": "Company bar chart",
    "趋势+公司柱状": "Trend + company bars",
    "偿付能力组合图": "Solvency combination",
    "市场信用矩阵": "Market–credit matrix",
    "风险散点图": "Risk scatter",
    "效应发散图": "Effect divergence",
    "公司分期柱": "Company period bars",
    "待定": "To be defined",
}

CATEGORY_NAMES_EN: dict[str, str] = {
    "全部": "All",
    "前十大": "Top 10",
    "前三大": "Top 3",
    "前4-10": "Top 4–10",
    "大型公司": "Large companies",
    "中型公司": "Medium companies",
    "小型公司": "Small companies",
    "农业保险": "Agricultural insurance",
    "机动车辆保险": "Motor vehicle insurance",
    "责任险": "Liability insurance",
    "健康险": "Health insurance",
    "意外险": "Accident insurance",
    "企业财产保险": "Commercial property insurance",
    "家庭财产保险": "Home property insurance",
    "工程保险": "Engineering insurance",
    "保证保险": "Guarantee insurance",
    "外资系": "Foreign-invested",
    "自保公司": "Captive insurers",
    "其他": "Other",
    "未分类": "Unclassified",
    "行业合计": "Industry total",
    "头部": "Top tier",
    "中小": "Small and medium",
    "大型": "Large",
    "中型": "Medium",
    "小型": "Small",
}

PERFORMANCE_NAMES_EN: dict[str, str] = {
    "逐年提升": "Consistent improvement",
    "稳健经营": "Steady performance",
    "V形反转": "V-shaped recovery",
    "大起大落": "Highly volatile",
    "持续恶化": "Continuous deterioration",
    "倒V形反转": "Inverted V-shaped reversal",
    "其他": "Other",
}

DISCLOSURE_NAMES_EN: dict[str, str] = {
    "已披露": "Disclosed",
    "未披露": "Not disclosed",
    "有": "Yes",
    "无": "No",
    "完整": "Complete",
    "异常数": "Anomalies",
    "合计": "Total",
    "车险": "Motor",
    "非车险": "Non-motor",
    "未拆分": "Unallocated",
    "保费": "Premium",
    "净利润": "Net profit",
    "承保利润": "Underwriting profit",
    "年度": "Year",
    "来源": "Source",
    "公司类别": "Company category",
    "盈亏": "Profit / loss",
    "盈亏平衡": "Break-even",
    "盈利": "Profit",
    "亏损": "Loss",
    "固定行业数据": "Built-in industry data",
    "上传数据": "Uploaded data",
    "本年累计数": "Year-to-date",
    "本季度数": "Quarterly",
    "本季度末数": "Quarter-end",
    "上季度末数": "Prior quarter-end",
    "期末数": "Period-end",
    "期初数": "Period-start",
    "合并": "Merged",
    "拆分": "Split",
    "核心一级资本": "Core tier 1 capital",
    "核心二级资本": "Core tier 2 capital",
    "附属一级资本": "Ancillary tier 1 capital",
    "附属二级资本": "Ancillary tier 2 capital",
    "计入核心一级资本": "Included in core tier 1 capital",
    "计入核心二级资本": "Included in core tier 2 capital",
    "计入附属一级资本": "Included in ancillary tier 1 capital",
    "计入附属二级资本": "Included in ancillary tier 2 capital",
    "风险分散效应": "Diversification effect",
    "损失吸收效应": "Loss absorption effect",
    "保险风险（寿）": "Insurance risk (life)",
    "保险风险（非寿）": "Insurance risk (non-life)",
    "非寿险业务保险风险": "Non-life insurance risk",
    "保费及准备金风险": "Premium and reserve risk",
    "巨灾风险": "Catastrophe risk",
    "风险分散": "Diversification",
    "赔付率": "Loss ratio",
    "费用率": "Expense ratio",
    "保险风险": "Insurance risk",
    "市场风险": "Market risk",
    "信用风险": "Credit risk",
    "利率风险": "Interest rate risk",
    "权益价格风险": "Equity price risk",
    "房地产价格风险": "Real estate price risk",
    "境外固定收益类资产价格风险": "Overseas fixed-income asset price risk",
    "境外权益类资产价格风险": "Overseas equity asset price risk",
    "汇率风险": "Foreign exchange risk",
    "利差风险": "Spread risk",
    "交易对手违约风险": "Counterparty default risk",
    "损失发生风险": "Loss occurrence risk",
    "退保风险": "Surrender risk",
    "费用风险": "Expense risk",
}

UNIT_NAMES_EN: dict[str, str] = {
    "百亿元": "RMB 10bn",
    "十亿元": "RMB bn",
    "亿元": "RMB 100 mn",
    "百万元": "RMB mn",
    "十万元": "RMB 100k",
    "万元": "RMB 10k",
    "元": "RMB",
    "%": "%",
    "％": "%",
    "‰": "‰",
    "倍": "x",
}

FIELD_NAMES_EN: dict[str, str] = {
    "公司": "Company",
    "公司名称": "Company",
    "报告期": "Reporting period",
    "指标名称": "Metric",
    "指标编码": "Metric code",
    "数值": "Value",
    "单位": "Unit",
    "披露状态": "Disclosure status",
    "同业分类": "Peer group",
    "公司类型": "Company type",
    "年度": "Year",
    "期间口径": "Period basis",
    "数据类型": "Data type",
    "监管下限": "Regulatory minimum",
    "保费": "Premium",
    "净利润": "Net profit",
    "承保利润": "Underwriting profit",
    "保费占比": "Premium share",
    "净利润占比": "Net profit share",
    "承保利润占比": "Underwriting profit share",
    "车险": "Motor",
    "非车险": "Non-motor",
    "险种": "Line of business",
    "原保险保费收入": "Original premium income",
    "同比增速": "YoY growth",
    "基期年度": "Base year",
    "合计": "Total",
    "来源": "Source",
    "收入排名": "Revenue rank",
    "利润排名": "Profit rank",
    "收入": "Revenue",
    "利润": "Profit",
    "类别": "Category",
    "盈亏": "Profit / loss",
    "标签": "Label",
    "公司数": "Company count",
    "有效公司数": "Valid companies",
    "综合成本率": "Combined ratio",
    "签单保费排名": "Signed premium rank",
    "盈亏平衡": "Break-even",
    "保费单位": "Premium unit",
    "状态": "Status",
    "原始公司名称": "Original company name",
    "标准公司名称": "Standard company name",
    "公司统一编码": "Company identifier",
    "报告类型": "Report type",
    "报告年度": "Reporting year",
    "报告季度": "Reporting quarter",
    "披露日期": "Disclosure date",
    "一级模块": "Level-one module",
    "二级模块": "Level-two module",
    "行次": "Line number",
    "是否预测": "Forecast",
    "来源页码": "Source page",
    "原始披露值": "Original disclosed value",
    "备注": "Notes",
    "来源类型": "Source type",
    "指标属性": "Metric attribute",
    "来源文件": "Source file",
    "来源工作表": "Source worksheet",
    "导入批次": "Import batch",
    "计算逻辑": "Calculation logic",
}
FIELD_NAMES_EN["类型"] = "Type"

DEFAULT_ANALYSIS_EN: dict[str, str] = {
    "近五年行业保费合计及车险/非车险结构变化": "Industry premium and motor/non-motor mix over the past five years",
    "最新年度按公司类别的保费、净利润、承保利润市场份额": "Latest-year market share by company category for premium, net profit and underwriting profit",
    "分险种展示原保险保费收入与同比增速（单位：亿元），仅列示机动车辆保险、健康险、农业保险、责任险和意外险，保留最近四个年度；每个险种一张保费柱状图与同比增速折线图的组合图，每行两个险种，同比按相邻有数年度计算。": "Original premium income and YoY growth by line of business (RMB 100 mn), limited to motor, health, agricultural, liability and accident insurance for the latest four years; each line has one combo chart with premium bars and a YoY growth line, two lines per row, with YoY calculated from the nearest year with data.",
    "近五年综合成本率与签单保费排名散点图（100% 盈亏平衡线），并汇总盈利/亏损公司数": "Five-year scatter of combined ratio against signed premium rank (100% break-even line), with profitable/loss-making company counts",
    "按行业合计口径计算综合/核心偿付能力充足率，并展示实际资本与最低资本": "Industry aggregate combined/core solvency ratios, with actual and minimum capital",
    "上方 KPMG Pink 折线展示综合偿付能力充足率，下方堆叠柱展示实际资本与最低资本；按年度口径展示。": "The KPMG Pink line shows the combined solvency ratio; stacked bars below show actual and minimum capital (annual basis).",
    "上方 KPMG Pink 折线展示核心偿付能力充足率，下方堆叠柱展示核心资本与最低资本；按年度口径展示。": "The KPMG Pink line shows the core solvency ratio; stacked bars below show core and minimum capital (annual basis).",
    "各公司实际资本年度变动走势，每家公司一个折线小图": "Annual actual capital trend for each company, shown as one small line chart per company",
    "各公司实际资本/认可资产率年度走势，每家公司一个折线小图": "Annual actual capital / recognised assets ratio for each company, shown as one small line chart per company",
    "各公司最低资本年度变动走势，每家公司一个折线小图": "Annual minimum capital trend for each company, shown as one small line chart per company",
    "每家公司近五年车险/非车险结构占比与总保费同比增速": "Five-year motor/non-motor mix and total premium growth for each company",
    "多公司实际资本/认可资产率跨期趋势对比。": "Actual capital / recognised assets ratio trend comparison across companies.",
    "100%堆叠柱展示核心一级、核心二级、附属一级和附属二级资本构成。": "100% stacked bars show the composition of core tier 1, core tier 2, ancillary tier 1 and ancillary tier 2 capital.",
    "非寿险业务保险风险构成：巨灾风险、准备金风险、保费风险和风险分散。": "Non-life insurance risk composition: catastrophe risk, reserve risk, premium risk and diversification.",
    "多公司保险风险/认可负债率跨期趋势对比。": "Insurance risk / recognised liabilities ratio trend comparison across companies.",
    "每家公司近五年赔付率、费用率堆叠及综合成本率变化。": "Five-year loss ratio, expense ratio stack and combined ratio for each company.",
    "各公司近五年投资收益率年度变动情况。": "Annual investment return movements for each company over the past five years.",
}


# 长句或高频短语，按长度从长到短替换，避免先替换短词破坏长句。
PHRASE_EN: dict[str, str] = {
    "公司级偿付能力对标报告": "Company Solvency Benchmarking Report",
    "当前没有公司数据。": "No company data.",
    "当前没有同时具备赔付率和费用率的年度数据。": "No annual data with both loss ratio and expense ratio is available.",
    "核心资本是指在持续经营状态下和破产清算状态下均可以吸收损失的资本。": "Core capital is capital that can absorb losses both on a going-concern basis and in liquidation.",
    "附属资本是指在破产清算状态下可以吸收损失的资本。": "Ancillary capital is capital that can absorb losses in liquidation.",
    "小型、外资系、农业保险和自保公司的核心资本占比较为相似，处于80%至100%区间；资本补充债券等进行资本补充，以支持偿付能力提升和业务持续健康发展。": "Small companies, foreign-invested insurers, agricultural insurers and captive insurers have broadly similar core capital shares, within the 80%–100% range. These companies use instruments such as capital supplementary bonds to support solvency and sustainable business development.",
    "部分集团子公司、大型公司及中型公司的附属资本占比较高，通常是在保司偿付能力充足率持续承压的情况下，通过发行次级定期债务、资本补充债券等进行资本补充，以支持偿付能力提升和业务持续健康发展。": "Some group subsidiaries, large companies and medium-sized companies have relatively high ancillary capital shares. When solvency ratios are under pressure, they commonly issue subordinated term debt or capital supplementary bonds to support solvency and sustainable business development.",
    "每家公司平铺展示最近 ": "Each company displays the latest ",
    " 个年度：下方为赔付率，上方为费用率，柱高合计为综合成本率；红色虚线为 100% 盈亏平衡线。柱顶数字为综合成本率，*表示按赔付率加费用率测算的值。": " years side by side. Loss ratio is below and expense ratio above; their combined height is the combined ratio. The red dashed line marks 100% break-even. Values above the bars show the combined ratio, with * indicating a calculated loss-plus-expense ratio.",
    "赔付率": "Loss ratio",
    "费用率": "Expense ratio",
    "100% 盈亏平衡线": "100% break-even line",
    "非寿险业务": "Non-life insurance",
    "保费及准备金风险": "Premium and reserve risk",
    "准备金风险": "Reserve risk",
    "巨灾风险": "Catastrophe risk",
    "风险分散": "Diversification",
    "风险分散率": "Diversification rate",
    "特征系数影响": "Feature-factor impact",
    "市场竞争": "Market competition",
    "财险公司列表": "the property insurance company list",
    "配置": "configuration",
    "保险公司偿付能力公司对标报告": "Insurance Company Solvency Benchmarking Report",
    "公司级图表设置与图片覆盖": "Company chart settings and image overrides",
    "公司内容分析与注释输入": "Company analysis and note inputs",
    "手动上传图片（PNG/JPG）": "Manual image upload (PNG/JPG)",
    "图表类型：按指标特征自动匹配（系统内置）": "Chart type: automatically matched to metric characteristics (built in)",
    "请在侧边栏公司报告导航中选择具体图表。": "Select a chart from the company report navigation in the sidebar.",
    "请在报告配置中至少选择一家展示公司。": "Select at least one company in the report settings.",
    "请先在 Step5 确认集成数据，或在 Step6 上传集成表。": "Confirm integrated data in Step 5, or upload an integrated workbook in Step 6.",
    "当前数据没有公司明细记录。": "No company detail records are available.",
    "当前数据没有可用报告期。": "No reporting periods are available.",
    "查看报告底层数据": "View underlying report data",
    "缺失指标编码：": "Missing metric codes: ",
    "一键AI分析": "One-click AI commentary",
    "一键 AI 分析": "One-click AI commentary",
    "特定追踪": "Track company",
    "排序方向": "Sort direction",
    "展示公司": "Companies",
    "显示单位": "Display unit",
    "公司类型": "Company type",
    "图表展示顺序依据": "Chart sort metric",
    "选择一家或多家公司": "Select one or more companies",
    "同业分类": "Peer group",
    "图片覆盖：": "Image override: ",
    "公司按“": "Companies grouped by “",
    "”分组，每家公司展示最近 ": "”; each company shows the latest ",
    " 个年度：左轴为车险/非车险的 100% 结构占比，右轴折线为总保费同比增速；非车险按“总签单保费 - 车险”计算。首年若缺少上一年度基数，则不显示折线点。": " years: the left axis shows the 100% motor/non-motor mix, and the right-axis line shows total premium growth. Non-motor premium is total signed premium less motor. No growth point is shown for the first year without a prior-year base.",
    "当前各公司都没有可比的连续年度保费基数，因此图内暂不显示同比增速折线。": "No company has a comparable consecutive-year premium base, so growth lines are not shown.",
    "保费增速图渲染失败：": "premium growth chart failed: ",
    "图表渲染失败：": "chart failed: ",
    "查看详细错误": "View detailed error",
    "横轴为「": "The x-axis is “",
    "」，纵轴为「": "”, and the y-axis is “",
    "」；数值越小表示排名越靠前，第 1 名位于右上角。每家小图的坐标轴按该公司自身排名范围自适应缩放，便于看出排名波动；刻度数字仍为真实名次。公司按利润排名年度轨迹归类为：逐年提升 / 稳健经营 / V形反转 / 大起大落 / 持续恶化。": "”. Lower values indicate a better rank, with rank 1 in the top-right corner. Each panel is scaled to the company’s own rank range; tick labels remain actual ranks. Companies are grouped by their annual profit-rank trajectory: Consistent improvement / Steady performance / V-shaped recovery / Highly volatile / Continuous deterioration.",
    "🔍 数据诊断：查看每家公司的收入/利润数据覆盖情况": "Data diagnostics: revenue/profit coverage by company",
    "下表显示每家公司每个报告期是否有「": "The table shows whether each company has “",
    "」和「": "” and “",
    "」数据。❌ 表示该期该指标缺失（未披露或未集成）。若发现大量 ❌，请到 STEP5/STEP6 检查原始数据是否完整。": "” data in each period. ❌ means the metric is missing (not disclosed or not integrated). If many ❌ appear, check the source data in STEP5/STEP6.",
    "无公司数据。": "No company data.",
    "没有公司能被归类到任何业绩模式。": "No company could be classified into a performance pattern.",
    "按年度口径展示：上方 KPMG Pink 折线为对应充足率，下方堆叠柱为资本规模；同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。": "Annual view: the KPMG Pink line shows the solvency ratio, and stacked bars below show capital scale. All companies in the group share the same amount axis for direct comparison.",
    "图表说明：柱状图为综合偿付能力充足率；粉色折线为核心偿付能力充足率。各公司统一使用同一百分比纵轴范围，柱形颜色对应不同报告期。": "Chart note: bars show the combined solvency ratio and the pink line shows the core solvency ratio. All companies share the same percentage axis; bar colours identify reporting periods.",
    "核心偿付能力充足率（%）": "Core solvency ratio (%)",
    "综合偿付能力充足率（%）": "Combined solvency ratio (%)",
    "核心资本充足率": "Core capital adequacy ratio",
    "综合资本充足率": "Combined capital adequacy ratio",
    "保单未来盈余/核心资本比例": "Policy future surplus / core capital",
    "未来保单盈余": "Future policy surplus",
    "认可负债余额": "Recognised liabilities",
    "市场风险占比": "Market risk share",
    "保险风险占比": "Insurance risk share",
    "上年同期": "Prior-year period",
    "本期": "Current period",
    "堆叠柱组合图": "Stacked bar combination",
    "并列柱组合图": "Grouped bar combination",
    "新增视图：每个报告期的两项资本各自成柱，上方仍保留对应充足率折线。": "New view: the two capital items are shown as separate bars for each reporting period, with the corresponding solvency ratio line above.",
    "上方 KPMG Pink 折线展示核心资本占实际资本的比例，下方堆叠柱展示核心资本与附属资本；同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。": "The KPMG Pink line shows core capital as a share of actual capital; stacked bars below show core and ancillary capital. All companies in the group share the same amount axis.",
    "核心资本占实际资本的比例": "Core capital / actual capital",
    "核心资本占实际资本的比例（%）": "Core capital / actual capital (%)",
    "核心资本与附属资本结构": "Core and ancillary capital structure",
    "计入各级资本的保单未来盈余构成占比": "Policy future surplus by capital tier",
    "各色块表示计入相应资本层级的保单未来盈余占该公司已披露保单未来盈余明细合计的比例；100%表示该公司披露的保单未来盈余全部计入了同一个资本层级，其他资本层级没有披露这个明细。": "Each segment shows policy future surplus in a capital tier as a share of the company’s total disclosed policy future surplus detail. 100% means all disclosed surplus was allocated to one tier; no detail was disclosed for other tiers.",
    "量化风险最低资本构成": "Quantitative risk minimum capital composition",
    "各类市场风险占比": "Market risk composition",
    "各类信用风险占比": "Credit risk composition",
    "柱高按各构成项目/量化风险最低资本的比例展示，实际金额仅保留在悬浮信息中；数据标签与对应比例折线图一致，负向抵减项目位于零线下方。": "Bar heights show each component as a share of quantitative risk minimum capital; amounts are available on hover. Negative offsets sit below the zero line.",
    "柱高按各构成项目/市场风险最低资本的比例展示，实际金额仅保留在悬浮信息中；负向风险分散效应位于零线下方。": "Bar heights show each component as a share of market risk minimum capital; amounts are available on hover. The negative diversification effect sits below the zero line.",
    "柱高按各构成项目/信用风险最低资本的比例展示，实际金额仅保留在悬浮信息中；负向风险分散效应位于零线下方。": "Bar heights show each component as a share of credit risk minimum capital; amounts are available on hover. The negative diversification effect sits below the zero line.",
    "按公司分面展示构成；负向抵减项目位于零线下方，所有颜色均来自 KPMG 色卡。": "Composition by company; negative offsets sit below the zero line. All colours come from the KPMG palette.",
    "100%堆叠柱显示核心一级、核心二级、附属一级和附属二级资本构成；所有公司和报告期的柱高一致，色块高度代表结构占比，悬浮可查看占比和金额。": "100% stacked bars show core tier 1, core tier 2, ancillary tier 1 and ancillary tier 2 capital. Bar heights are equal across companies and periods; segment heights show the mix, with shares and amounts on hover.",
    "未披露": "Not disclosed",
    "按公司展示抵减效应的跨期方向与幅度，零线用于区分正向增加和负向抵减。": "Offsetting effects by company over time; the zero line separates positive additions from negative offsets.",
    "偿付能力矩阵": "Solvency matrix",
    "市场—信用风险矩阵": "Market–credit risk matrix",
    "横轴50%、纵轴100%的红色虚线分别为核心和综合偿付能力充足率监管下限。": "Red dashed lines at 50% on the x-axis and 100% on the y-axis are the regulatory minima for the core and combined solvency ratios.",
    "深蓝虚线为样本中位数，用于识别市场风险和信用风险同时偏高的公司。": "The dark-blue dashed line is the sample median, highlighting companies with both high market and credit risk.",
    "展示 ": "Showing ",
    "；每个气泡代表一家公司，横纵轴均为占": "; each bubble is one company; both axes are shown as a share of ",
    "的比例，气泡面积代表": ". Bubble size represents ",
    "规模；深蓝虚线为样本横纵指标中位数。": " scale; the dark-blue dashed line is the sample median of both axes.",
    "每家公司一个年度小折线图，纵轴为 %；蓝色折线为该公司综合偿付能力充足率走势，红色虚线为监管下限 100%；仅标注首末年数值，悬停查看各年明细。": "One annual line chart per company; the y-axis is in %. The blue line is the company’s combined solvency ratio and the red dashed line is the 100% regulatory minimum. Only first/last-year values are labelled; hover for annual detail.",
    "能力五边形当前无法打分，诊断如下：": "The capability pentagon cannot be scored. Diagnostics:",
    "筛选后总记录数：": "Filtered records: ",
    "最新报告期「": "Latest reporting period “",
    "」在筛选后仅剩 ": "” has only ",
    " 行、": " rows and ",
    " 家公司；": " companies after filtering;",
    "要求的指标编码：": "Required metric codes: ",
    "数据中缺失的编码：": "Missing codes: ",
    "数据中实际存在的编码：": "Codes present: ",
    "无（5 个编码都在）": "None (all 5 codes present)",
    "常见原因：89 家数据的「指标编码」列与标准编码不一致（如用了中文名），或最新报告期这一期缺少上述 5 个指标。": "Common causes: the metric-code column for the 89 companies does not match the standard codes (for example, Chinese names were used), or the latest period lacks the five metrics above.",
    "查看原始数值与得分明细": "View raw values and scores",
    "每家公司一个年度折线小图，按公司类型分组分节展示。": "One annual line chart per company, grouped by company type.",
    "当前筛选范围没有可绘制的数据。": "No plottable data in the current selection.",
    "规模与结构": "Scale and structure",
    "行业签单保费": "industry signed premium",
    "保费规模与同比增速": "Premium scale and year-on-year growth",
    "左：签单保费规模与同比增速　右：车险 / 非车险占比与同比增速": "Left: signed premium scale and YoY growth   Right: motor / non-motor mix and YoY growth",
    "签单保费规模与同比增速": "Signed premium scale and YoY growth",
    "柱：年度签单保费　折线：同比增速（右轴，%）": "Bars: annual signed premium   Line: YoY growth (right axis, %)",
    "车险 / 非车险占比与同比增速": "Motor / non-motor mix and YoY growth",
    "柱：100% 堆叠占比　折线：车险 / 非车险同比增速（右轴，%）": "Bars: 100% stacked mix   Lines: motor / non-motor YoY growth (right axis, %)",
    "签单保费（柱）": "Signed premium (bars)",
    "车险（占比）": "Motor (share)",
    "非车险（占比）": "Non-motor (share)",
    "保费规模与结构": "Premium scale and structure",
    "柱：车险 / 非车险金额　折线：同比增速（右轴，%）": "Bars: motor / non-motor premium   Line: YoY growth (right axis, %)",
    "柱：车险 / 非车险金额": "Bars: motor / non-motor premium",
    "车险（柱）": "Motor (bars)",
    "非车险（柱）": "Non-motor (bars)",
    "车险同比": "Motor YoY",
    "非车险同比": "Non-motor YoY",
    "合计同比": "Total YoY",
    "0% 基准线": "0% baseline",
    "基期（无同比）": "Base year (no YoY)",
    "同比不可比": "YoY not comparable",
    "市场份额与集中度": "Market share and concentration",
    "样本覆盖 ": "Sample coverage: ",
    "保费集中度：": "Premium concentration: ",
    "公司类别市场份额（": "Company category market share (",
    " 年）": ")",
    "公司类别金额对比（": "Company category amounts (",
    "按公司类别汇总（": "Aggregated by company category (",
    "饼图展示各指标正值占比，负值不计入占比。": "Pies show positive values only; negative values are excluded from shares.",
    "利润面板以灰色零轴区分正值与负值。": "The profit panel uses a grey zero axis to separate positive and negative values.",
    "查看分类与公司明细": "View category and company detail",
    "承保利润 = 签单保费 ×（1 - 综合成本率）的测算值。": "Underwriting profit is estimated as signed premium × (1 − combined ratio).",
    "查看行业年度明细": "View annual industry detail",
    "年度汇总": "Annual summary",
    "公司级明细（上传数据）": "Company detail (uploaded data)",
    "数据不完整的年度：": "Years with incomplete data: ",
    "缺少年度累计": "missing year-to-date",
    "年度累计覆盖不足": "insufficient year-to-date coverage",
    "车险拆分覆盖不足": "insufficient motor split coverage",
    "数据不完整": "incomplete data",
    "检测到 ": "Detected ",
    " 条公司年度值疑似单位/量级异常，图表未自动改写原始数值；请核对明细。": " company-year values with suspected unit/scale anomalies; charts do not overwrite the source values. Please review the detail.",
    "偿付能力披露整体情况": "Solvency disclosure overview",
    "行业合计口径": "industry aggregate basis",
    "实际资本合计": "Total actual capital",
    "最低资本合计": "Total minimum capital",
    "核心资本占实际资本比例暂不可用": "Core capital / actual capital is not available",
    "查看合计口径公司与公式明细": "View aggregate companies and formulas",
    "综合 = Σ实际资本 ": "Combined = Σ actual capital ",
    " ÷ Σ最低资本 ": " ÷ Σ minimum capital ",
    "核心 = Σ核心资本 ": "Core = Σ core capital ",
    "核心资本占实际资本 ": "Core capital / actual capital ",
    "合计口径仅纳入同时具备相应资本与正的最低资本的公司；卡片中的比率不是各公司充足率的简单平均。": "The aggregate basis includes only companies with the relevant capital and positive minimum capital; the ratios are not simple averages of company ratios.",
    "公式：综合偿付能力充足率 = Σ所有公司实际资本 / Σ所有公司最低资本；核心偿付能力充足率 = Σ（核心一级资本 + 核心二级资本）/ Σ所有公司最低资本。监管口径下，核心资本对应核心一级与核心二级资本之和。": "Formula: combined solvency ratio = Σ actual capital / Σ minimum capital; core solvency ratio = Σ (core tier 1 + core tier 2 capital) / Σ minimum capital. Under the regulatory basis, core capital equals core tier 1 plus core tier 2 capital.",
    "综合成本率（%）": "Combined ratio (%)",
    "签单保费排名（1 = 最高）": "Signed premium rank (1 = highest)",
    "公司数（家）": "Company count",
    "低于纵轴下限": "Below y-axis lower bound",
    "高于纵轴上限": "Above y-axis upper bound",
    "显示范围": "Display range",
    "盈亏状态": "Profit / loss status",
    "全样本图": "Full sample",
    "局部样本放大": "Zoomed sample",
    "总保费同比增速": "Total premium growth",
    "所选公司平均投资收益率": "Selected-company average return",
    "所选公司平均投资收益率（%）": "Selected-company average return (%)",
    "所选公司平均": "Selected-company average",
    "展开此模块": "Show module",
    "偿付能力充足率指标": "Solvency ratio metrics",
    "主要经营指标（财险）": "Key operating metrics (P&C)",
    "S02-实际资本明细表": "S02 – Actual capital detail",
    "近三年（综合）投资收益率": "Three-year (combined) investment return",
    "S05-最低资本表": "S05 – Minimum capital",
    "流动性风险监测指标": "Liquidity risk monitoring metrics",
    "纳入综合口径": "Included in combined basis",
    "纳入核心口径": "Included in core basis",
    "Yes数据": "Yes",
    "_得分": " score",
    "财险": "Property & casualty",
    "资本充足率": "Capital adequacy ratio",
    "资本": "Capital",
    "具体图表（全部展示）": "Charts (show all)",
    "前十大展示口径": "Top 10 grouping",
    "实际资本/注册资本": "Actual capital / registered capital",
    "核心资本/注册资本": "Core capital / registered capital",
    "认可资产规模": "Recognised asset scale",
    "签单保费排名": "Signed premium rank",
    "综合成本率与签单保费排名": "Combined ratio and signed premium rank",
    "有效样本": "Valid sample",
    "样本公司数": "Sample companies",
    "盈利能力": "Profitability",
    "资本实力": "Capital strength",
    "承保风控能力": "Underwriting risk control",
    "业务拓展": "Business expansion",
    "基期": "Base period",
    "同比基期（无上年数据）": "YoY base period (no prior-year data)",
    "同比类别": "YoY category",
    "同比增速": "YoY growth",
    "保费规模": "Premium scale",
    "保费收入": "Premium income",
    "利率风险": "Interest rate risk",
    "权益价格风险": "Equity price risk",
    "房地产价格风险": "Real estate price risk",
    "境外固定收益类资产价格风险": "Overseas fixed-income asset price risk",
    "境外权益类资产价格风险": "Overseas equity asset price risk",
    "汇率风险": "Foreign exchange risk",
    "利差风险": "Spread risk",
    "交易对手违约风险": "Counterparty default risk",
    "损失发生风险": "Loss occurrence risk",
    "退保风险": "Surrender risk",
    "费用风险": "Expense risk",
    "缺少可绘制的完整公司数据": "lacks complete company data for plotting",
    "缺少气泡图所需指标": "is missing metrics required for the bubble chart",
    "最新报告期缺少气泡图所需指标。": "The latest reporting period is missing metrics required for the bubble chart.",
    "最新报告期": "Latest reporting period",
    "计入核心一级资本": "Included in core tier 1 capital",
    "计入核心二级资本": "Included in core tier 2 capital",
    "计入附属一级资本": "Included in ancillary tier 1 capital",
    "计入附属二级资本": "Included in ancillary tier 2 capital",
    "核心一级资本": "Core tier 1 capital",
    "核心二级资本": "Core tier 2 capital",
    "附属一级资本": "Ancillary tier 1 capital",
    "附属二级资本": "Ancillary tier 2 capital",
    "实际资本": "Actual capital",
    "最低资本": "Minimum capital",
    "认可资产": "Recognised assets",
    "认可负债": "Recognised liabilities",
    "风险分散效应": "Diversification effect",
    "损失吸收效应": "Loss absorption effect",
    "保险风险（寿）": "Insurance risk (life)",
    "保险风险（非寿）": "Insurance risk (non-life)",
    "保险风险": "Insurance risk",
    "市场风险": "Market risk",
    "信用风险": "Credit risk",
    "综合偿付能力充足率": "Combined solvency ratio",
    "核心偿付能力充足率": "Core solvency ratio",
    "偿付能力": "Solvency",
    "总资产": "Total assets",
    "保费增速": "Premium growth",
    "签单保费": "Signed premium",
    "净利润率": "Net profit margin",
    "注册资本": "Registered capital",
    "报告期": "Reporting period",
    "年度": "Year",
    "数值": "Value",
    "单位": "Unit",
    "车险": "Motor",
    "非车险": "Non-motor",
    "合计": "Total",
    "保费": "Premium",
    "净利润": "Net profit",
    "承保利润": "Underwriting profit",
    "公司类别": "Company category",
    "来源": "Source",
    "数据来源": "Data source",
    "构成": "Component",
    "金额": "Amount",
    "占合计": "Share of total",
    "占比": "Share",
    "增长率": "Growth rate",
    "当年金额": "Current-year amount",
    "上年金额": "Prior-year amount",
    "基期年度": "Base year",
    "未拆分": "Unallocated",
    "监管下限": "Regulatory minimum",
    "已披露": "Disclosed",
    "未披露": "Not disclosed",
    "盈亏平衡": "Break-even",
    "盈利": "Profit",
    "亏损": "Loss",
    "其他": "Other",
    "未分类": "Unclassified",
}


def translate_text(text: str, language: str | None = None) -> str:
    """Translate a display string with exact and phrase-level lookup."""
    if not is_english(language) or not isinstance(text, str) or not text:
        return text
    if text in PHRASE_EN:
        return PHRASE_EN[text]
    if text in DEFAULT_ANALYSIS_EN:
        return DEFAULT_ANALYSIS_EN[text]
    if text in CHART_NAMES_EN:
        return CHART_NAMES_EN[text]
    if text in LEVEL_NAMES_EN:
        return LEVEL_NAMES_EN[text]
    if text in CATEGORY_NAMES_EN:
        return CATEGORY_NAMES_EN[text]
    if text in PERFORMANCE_NAMES_EN:
        return PERFORMANCE_NAMES_EN[text]
    if text in DISCLOSURE_NAMES_EN:
        return DISCLOSURE_NAMES_EN[text]
    if text in UNIT_NAMES_EN:
        return UNIT_NAMES_EN[text]
    if text in FIELD_NAMES_EN:
        return FIELD_NAMES_EN[text]
    value_unit = re.fullmatch(r"数值（(.+)）", text)
    if value_unit:
        return f"Value ({unit_name(value_unit.group(1), language)})"
    unit_suffix = re.fullmatch(r"(.+)（([^）]+)）", text)
    if unit_suffix and (
        unit_suffix.group(2) in UNIT_NAMES_EN
        or unit_suffix.group(2) in {"%", "％", "‰", "倍"}
    ):
        return (
            f"{translate_text(unit_suffix.group(1), language)}"
            f" ({unit_name(unit_suffix.group(2), language)})"
        )
    result = text
    for source in sorted(PHRASE_EN, key=len, reverse=True):
        if source and source in result:
            result = result.replace(source, PHRASE_EN[source])
    for source in sorted(CATEGORY_NAMES_EN, key=len, reverse=True):
        if source and source in result:
            result = result.replace(source, CATEGORY_NAMES_EN[source])
    for source in sorted(PERFORMANCE_NAMES_EN, key=len, reverse=True):
        if source and source in result:
            result = result.replace(source, PERFORMANCE_NAMES_EN[source])
    for source in sorted(DISCLOSURE_NAMES_EN, key=len, reverse=True):
        if source and source in result:
            result = result.replace(source, DISCLOSURE_NAMES_EN[source])
    result = re.sub(
        r" · 第 (\d+)/(\d+) 组（(\d+) 家公司）",
        r" · Group \1/\2 (\3 companies)",
        result,
    )
    result = re.sub(r"（(\d+) 家公司）", r" (\1 companies)", result)
    result = re.sub(r"（(\d+) 家）", r" (\1 companies)", result)
    result = re.sub(r"数值（([^）]+)）", r"Value (\1)", result)
    result = result.replace("跨期趋势", " trend over periods")
    result = result.replace("公司跨期对标", " company benchmark")
    result = result.replace("公司年度走势", " annual trend")
    result = result.replace("（单位）", " (unit)")
    return result


def chart_name(name: str, language: str | None = None) -> str:
    if not is_english(language):
        return str(name)
    value = str(name or "")
    return CHART_NAMES_EN.get(value, LEVEL_NAMES_EN.get(value, translate_text(value, language)))


def level_name(name: str, language: str | None = None) -> str:
    if not is_english(language):
        return str(name)
    value = str(name or "")
    return LEVEL_NAMES_EN.get(value, translate_text(value, language))


def chart_kind_name(name: str, language: str | None = None) -> str:
    if not is_english(language):
        return str(name)
    value = str(name or "")
    return CHART_KIND_NAMES_EN.get(value, translate_text(value, language))


def category_name(name: str, language: str | None = None) -> str:
    if not is_english(language):
        return str(name)
    value = str(name or "")
    return CATEGORY_NAMES_EN.get(value, PERFORMANCE_NAMES_EN.get(value, translate_text(value, language)))


def translate_mapping(mapping: dict, language: str | None = None) -> dict:
    if not is_english(language):
        return dict(mapping)
    return {category_name(key, language): value for key, value in mapping.items()}


# ---------------------------------------------------------------------------
# DataFrame 展示
# ---------------------------------------------------------------------------
COLUMN_NAMES_EN: dict[str, str] = {
    "公司": "Company",
    "公司名称": "Company",
    "报告期": "Reporting period",
    "指标编码": "Metric code",
    "指标名称": "Metric",
    "数值": "Value",
    "单位": "Unit",
    "期间口径": "Period basis",
    "数据类型": "Data type",
    "同业分类": "Peer group",
    "公司类型": "Company type",
    "年度": "Year",
    "保费": "Premium",
    "净利润": "Net profit",
    "承保利润": "Underwriting profit",
    "保费占比": "Premium share",
    "净利润占比": "Net profit share",
    "承保利润占比": "Underwriting profit share",
    "车险": "Motor",
    "非车险": "Non-motor",
    "险种": "Line of business",
    "原保险保费收入": "Original premium income",
    "同比增速": "YoY growth",
    "基期年度": "Base year",
    "合计": "Total",
    "来源": "Source",
    "收入排名": "Revenue rank",
    "利润排名": "Profit rank",
    "收入": "Revenue",
    "利润": "Profit",
    "类别": "Category",
    "盈亏": "Profit / loss",
    "标签": "Label",
    "公司数": "Company count",
    "有效公司数": "Valid companies",
    "综合成本率": "Combined ratio",
    "签单保费排名": "Signed premium rank",
    "盈亏平衡": "Break-even",
    "保费单位": "Premium unit",
    "公司类别": "Company category",
    "类型": "Type",
    "承保利润测算": "Estimated underwriting profit",
    "综合成本率口径": "Combined ratio basis",
    "保费口径": "Premium basis",
    "综合成本率报告期": "Combined ratio period",
    "保费报告期": "Premium period",
    "车险占比": "Motor share",
    "异常数": "Anomalies",
    "完整": "Complete",
    "公司数（家）": "Company count",
}
COLUMN_NAMES_EN.update(FIELD_NAMES_EN)


def translate_dataframe(
    frame: pd.DataFrame,
    language: str | None = None,
    *,
    translate_values: bool = True,
) -> pd.DataFrame:
    """Return a display copy with English column names and known category values."""
    if not is_english(language) or not isinstance(frame, pd.DataFrame):
        return frame
    result = frame.copy()
    rename = {}
    for column in result.columns:
        text = str(column)
        if text in COLUMN_NAMES_EN:
            rename[column] = COLUMN_NAMES_EN[text]
        elif "（" in text or "(" in text:
            base = text.split("（")[0].split("(")[0]
            if base in COLUMN_NAMES_EN:
                suffix = text[len(base):]
                rename[column] = f"{COLUMN_NAMES_EN[base]}{suffix}"
        if column not in rename:
            translated_column = translate_text(text, language)
            if translated_column != text:
                rename[column] = translated_column
    result = result.rename(columns=rename)
    if translate_values and is_english(language):
        # Translate units inside parenthetical column suffixes, e.g. 车险（亿元）.
        unit_pattern = re.compile(r"[（(]([^）)]+)[）)]\s*$")
        result = result.rename(
            columns={
                column: unit_pattern.sub(
                    lambda match: f" ({unit_name(match.group(1), language)})",
                    str(column),
                )
                for column in result.columns
                if unit_pattern.search(str(column))
            }
        )
    if translate_values:
        for column in result.columns:
            if pd.api.types.is_string_dtype(
                result[column].dtype
            ) or pd.api.types.is_object_dtype(result[column].dtype):
                if str(column) in {"Company", "公司", "公司名称"}:
                    continue
                result[column] = result[column].map(
                    lambda value: (
                        translate_text(value, language)
                        if isinstance(value, str)
                        else value
                    )
                )
    return result


# ---------------------------------------------------------------------------
# Altair / Plotly 图表
# ---------------------------------------------------------------------------
def _field_display_name(field: str, language: str | None) -> str:
    if not is_english(language):
        return field
    text = str(field or "")
    if text in FIELD_NAMES_EN:
        return FIELD_NAMES_EN[text]
    return translate_text(text, language)


def _translate_value(value: Any, language: str | None) -> Any:
    if not is_english(language) or not isinstance(value, str):
        return value
    if value in DISCLOSURE_NAMES_EN:
        return DISCLOSURE_NAMES_EN[value]
    if value in CATEGORY_NAMES_EN:
        return CATEGORY_NAMES_EN[value]
    if value in PERFORMANCE_NAMES_EN:
        return PERFORMANCE_NAMES_EN[value]
    if value in UNIT_NAMES_EN:
        return UNIT_NAMES_EN[value]
    if value == "高于纵轴上限":
        return "Above upper range"
    if value == "低于纵轴下限":
        return "Below lower range"
    # Never run free-text phrase replacement over data values: company names
    # such as 国元农险 must remain unchanged.
    return value


def _translate_chart_node(node: Any, language: str | None, *, in_values: bool = False) -> Any:
    if isinstance(node, list):
        return [_translate_chart_node(item, language, in_values=in_values) for item in node]
    if not isinstance(node, dict):
        if isinstance(node, str) and in_values:
            return _translate_value(node, language)
        return node

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in {"field", "field2", "name", "repeat"}:
            result[key] = value
            continue
        if key in {"domain", "sort"} and isinstance(value, list):
            result[key] = [_translate_value(item, language) for item in value]
            continue
        if key in {"values", "datasets"}:
            result[key] = _translate_chart_node(value, language, in_values=True)
            continue
        if key in {"title", "labelExpr", "description"} and isinstance(value, str):
            result[key] = translate_text(value, language)
            continue
        if key in {"title", "labelExpr", "description"} and isinstance(value, dict):
            translated = _translate_chart_node(value, language)
            if isinstance(translated, dict) and isinstance(translated.get("text"), str):
                translated["text"] = translate_text(translated["text"], language)
            result[key] = translated
            continue
        if key == "tooltip" and isinstance(value, list):
            tooltips = []
            for item in value:
                if isinstance(item, dict):
                    item = deepcopy(item)
                    field = str(item.get("field", ""))
                    if field and "title" not in item:
                        item["title"] = _field_display_name(field, language)
                    else:
                        item = _translate_chart_node(item, language)
                    tooltips.append(item)
                elif isinstance(item, str) and ":" in item:
                    field = item.split(":", 1)[0]
                    tooltips.append(
                        {
                            "field": field,
                            "type": "nominal" if item.endswith(":N") else "quantitative",
                            "title": _field_display_name(field, language),
                        }
                    )
                else:
                    tooltips.append(item)
            result[key] = tooltips
            continue
        if key in {"axis", "legend", "header"} and isinstance(value, dict):
            translated = _translate_chart_node(value, language)
            if isinstance(translated, dict) and isinstance(translated.get("title"), str):
                translated["title"] = translate_text(translated["title"], language)
            result[key] = translated
            continue
        if key == "mark" and isinstance(value, dict):
            translated = _translate_chart_node(value, language)
            if isinstance(translated, dict) and isinstance(translated.get("text"), str):
                translated["text"] = translate_text(translated["text"], language)
            result[key] = translated
            continue
        result[key] = _translate_chart_node(value, language, in_values=in_values)

    # Give unlabelled encoding fields a readable title.
    if "field" in node and isinstance(node.get("field"), str) and "title" not in node:
        if "axis" not in node and "legend" not in node:
            field = str(node["field"])
            if field not in {"数值", "值"}:
                result["title"] = _field_display_name(field, language)
    return result


class _LocalizedChart:
    """Minimal chart object that exposes a translated Vega-Lite spec.

    Streamlit only needs to_dict() from the chart object. Returning the
    translated spec this way avoids reconstructing the Altair object graph,
    which can lose mark-level properties such as dy on layered charts.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec

    def to_dict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(self._spec)


def localize_chart(chart: Any, language: str | None = None) -> Any:
    """Return a chart object with English display titles and category values."""
    if not is_english(language) or chart is None:
        return chart
    try:
        spec = chart.to_dict()
        translated = _translate_chart_node(spec, language)
        return _LocalizedChart(translated)
    except Exception:
        return chart


def translate_hover_text(text: str, language: str | None = None) -> str:
    """Translate a hover template without touching company/category data."""
    if not is_english(language) or not isinstance(text, str):
        return text
    result = translate_text(text, language)
    for source in sorted(UNIT_NAMES_EN, key=len, reverse=True):
        if source and source in result:
            result = result.replace(source, UNIT_NAMES_EN[source])
    return result.replace("：", ": ")


def localize_plotly(figure: Any, language: str | None = None) -> Any:
    """Translate Plotly titles, annotations, legend and trace labels in place."""
    if not is_english(language) or figure is None:
        return figure
    try:
        for annotation in list(getattr(figure.layout, "annotations", ()) or ()):
            if getattr(annotation, "text", None):
                annotation.text = translate_text(str(annotation.text), language)
        for axis_name in ("xaxis", "yaxis"):
            axis = getattr(figure.layout, axis_name, None)
            if axis is not None and getattr(axis, "title", None) is not None:
                axis.title.text = translate_text(str(axis.title.text), language)
        if getattr(figure.layout, "title", None) is not None:
            title = figure.layout.title
            if getattr(title, "text", None):
                title.text = translate_text(str(title.text), language)
        for trace in figure.data:
            for attribute in ("name", "labels", "text", "x"):
                values = getattr(trace, attribute, None)
                if values is None:
                    continue
                if isinstance(values, str):
                    setattr(trace, attribute, translate_text(values, language))
                elif hasattr(values, "__iter__"):
                    setattr(
                        trace,
                        attribute,
                        [
                            translate_text(str(value), language)
                            if isinstance(value, str)
                            else value
                            for value in list(values)
                        ],
                    )
            hovertemplate = getattr(trace, "hovertemplate", None)
            if isinstance(hovertemplate, str) and hovertemplate:
                trace.hovertemplate = translate_hover_text(hovertemplate, language)
    except Exception:
        return figure
    return figure


# ---------------------------------------------------------------------------
# 报告注释
# ---------------------------------------------------------------------------
def translate_default_analysis(text: str, language: str | None = None) -> str:
    if not is_english(language):
        return text
    return DEFAULT_ANALYSIS_EN.get(str(text).strip(), str(text))


def unit_name(unit: str, language: str | None = None) -> str:
    if not is_english(language):
        return str(unit)
    return UNIT_NAMES_EN.get(str(unit), str(unit))
