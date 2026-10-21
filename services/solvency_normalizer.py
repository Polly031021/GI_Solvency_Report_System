from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .solvency_table_extractor import ExtractedTable
from .solvency_company_identity import (
    apply_company_identities,
    resolve_company_identity,
    resolve_peer_group,
)
from .solvency_navigation import apply_navigation_labels


STANDARD_COLUMNS = [
    "公司", "原始公司名称", "标准公司名称", "公司统一编码", "公司类型", "同业分类",
    "报告类型", "报告年度", "报告季度", "报告期", "披露日期",
    "一级模块", "二级模块", "行次", "指标编码", "指标名称", "期间口径",
    "数值", "单位", "数据类型", "是否预测", "来源页码", "原始披露值", "备注",
    "来源类型", "指标属性", "来源文件", "来源工作表", "导入批次", "计算逻辑",
]

# User-facing STEP3/STEP5 exchange schema.  The wider STANDARD_COLUMNS schema
# remains the internal source of truth so identity, provenance and profile
# fields stay available to validation and downstream report logic.
NARROW_TABLE_COLUMNS = [
    "公司",
    "同业分类",
    "报告期",
    "一级模块",
    "二级模块",
    "指标编码",
    "指标名称",
    "期间口径",
    "数值",
    "单位",
    "数据类型",
    "来源类型",
    "指标属性",
    "计算逻辑",
]

LIFE_COMPANY_TYPES = ("寿险", "健康险", "养老险")

_COMPANY_TYPE_ALIASES = {
    "寿险": "寿险",
    "健康": "健康险",
    "健康险": "健康险",
    "养老": "养老险",
    "养老险": "养老险",
    "财险": "财险",
    "财产险": "财险",
    "财产保险": "财险",
    "非寿险": "财险",
}

_CURRENCY_UNIT_IN_YUAN = {
    "元": 1.0,
    "千元": 1_000.0,
    "万元": 10_000.0,
    "亿元": 100_000_000.0,
}

_INLINE_UNIT_PATTERN = re.compile(
    r"[（(]\s*(亿元|万元|千元|元|%|％|百分比|百分点|人|户|件|次|级)\s*[）)]"
)

# ★ 单位换算豁免名单
# 这些指标在 taxonomy 里"数据类型"标为"金额"，但原始单位是"元"（或"元/股"、"元/车"），
# 不是整表声明的"万元"。Step2 提取时已通过 _RATIO_LABEL_MARKERS 跳过换算，
# Step3 兜底换算必须同样跳过，否则会被误乘 10000。
_UNIT_CONVERT_EXEMPT_CODES = {
    "EPS",                # 基本每股收益（元/股）
    "MOTOR_AVG_PREMIUM",  # 车险车均保费（元/车）
}

# 资本构成表的四个层级明细：核心一级、核心二级、附属一级、附属二级。
# 部分公司在原表中用空白表示该层级没有余额（而不是写 0 或 "-"）。
# 只在这两类资本表的对应行内启用空白=0，避免把其他表的空白误当成零。
CAPITAL_COMPONENT_ZERO_CODES = frozenset({
    "CORE_T1_CAPITAL",
    "CORE_T2_CAPITAL",
    "ANC_T1_CAPITAL",
    "ANC_T2_CAPITAL",
})
CAPITAL_COMPONENT_ZERO_TABLE_IDS = frozenset({
    "ACTUAL_CAPITAL",
    "SOLVENCY_MAIN",
})

# 已识别指标但数值单元为空、短横线或“未披露”等占位符时，统一按 0 标准化。
# 该规则覆盖全部数值型指标，而不只限于资本明细；备注会保留原始值，
# 便于分析师回到来源页核对“披露为零”与“确实未填报”的差异。
MISSING_NUMERIC_ZERO_MARKER = "原始值空白或占位符，按0处理"


PERIOD_TERMS = [
    # 偿付能力主表/实际资本表/最低资本表常用
    "本季度末数", "上季度末数", "下季度末预测数", "下季度预测数",
    "基本情景下的下季度预测数",
    "本季度末", "上季度末", "下季度末", "下季度预测",
    "基本情景下的下季度预测",
    # 人保/太保常用的季度预测
    "季度预测数", "季度预测", "下季度预测数",
    # 经营指标常用
    "本季度数", "上季度数", "本季度", "上季度",
    "本年累计数", "本年累计", "本年度累计数", "本年度累计",
    "期末数", "期初数", "期末", "期初",
    # 其他
    "未来3个月", "未来12个月",
    "账面价值", "非认可", "认可价值",
    "本季末", "上季末", "下季末",
    "本期末", "上期末",
    "本期", "上期",
]
PERIOD_ALIASES = {
    "下季度预测数": "下季度末预测数",
    "基本情景下的下季度预测数": "下季度末预测数",
    "下季度预测": "下季度末预测数",
    "基本情景下的下季度预测": "下季度末预测数",
    # ★ 新增：人保用“季度预测数”
    "季度预测数": "下季度末预测数",
    "季度预测": "下季度末预测数",
    "本季度末": "本季度末数",
    "上季度末": "上季度末数",
    "下季度末": "下季度末预测数",
    "本季度": "本季度数",
    "上季度": "上季度数",
    "本期": "本季度数",
    "上期": "上季度数",
    "上年同期": "上季度数",
    "上年度": "上季度末数",
    "上年末": "上季度末数",
    "上年度末": "上季度末数",
    "本年累计": "本年累计数",
    "本年度累计": "本年累计数",
    "本年度": "本年累计数",
    "年度累计": "本年累计数",
    "全年累计": "本年累计数",
    "全年": "本年累计数",
    "本期累计": "本年累计数",
    "累计": "本年累计数",
    "期末": "期末数",
    "期初": "期初数",
    "本季末": "本季度末数",
    "上季末": "上季度末数",
    "下季末": "下季度末预测数",
    "本期末": "本季度末数",
    "上期末": "上季度末数",
}

# ================================================================
# ★ 精确锁定字典：这些标签在 PDF 里足够唯一，一旦归一化后相等，
#   直接锁定编码，跳过模糊匹配。
# 原因：S05 的"量化风险分散效应"因为与"可资本化风险分散效应"、
#      "市场风险分散效应"相似，模糊匹配分差不够被判为"歧义"而丢弃。
# ================================================================
_EXACT_LABEL_LOCKS: dict[str, str] = {
    "量化风险分散效应":          "QUANT_RISK_DIVERSIFICATION_EFFECT",
    "量化风险分散效应期末数":     "QUANT_RISK_DIVERSIFICATION_EFFECT",
    "量化风险分散效应期初数":     "QUANT_RISK_DIVERSIFICATION_EFFECT",
    "风险分散效应":              "QUANT_RISK_DIVERSIFICATION_EFFECT",
    # ★ 资本层级常见别名：部分公司写“合计/金额”，归一化后直接锁定，
    #   避免与“资本工具合计”等相邻项目模糊匹配。
    "核心一级资本合计":          "CORE_T1_CAPITAL",
    "核心一级资本金额":          "CORE_T1_CAPITAL",
    "核心二级资本合计":          "CORE_T2_CAPITAL",
    "核心二级资本金额":          "CORE_T2_CAPITAL",
    "附属一级资本合计":          "ANC_T1_CAPITAL",
    "附属一级资本金额":          "ANC_T1_CAPITAL",
    "附属二级资本合计":          "ANC_T2_CAPITAL",
    "附属二级资本金额":          "ANC_T2_CAPITAL",
    # ★ 新增：流动性风险监测表里的"规模保费同比增速"
    "规模保费同比增速":          "PREMIUM_GROWTH",
    "规模保费增速":              "PREMIUM_GROWTH",
}

TABLE_ALLOWED_CODES = {
    "SOLVENCY_MAIN": {
        "RECOGNIZED_ASSETS",
        "RECOGNIZED_LIABILITIES",
        "ACTUAL_CAPITAL",
        "CORE_T1_CAPITAL",
        "CORE_T2_CAPITAL",
        "ANC_T1_CAPITAL",
        "ANC_T2_CAPITAL",
        "MINIMUM_CAPITAL",
        "QUANT_RISK_CAPITAL",
        "CONTROL_RISK_CAPITAL",
        # ★ 新增：财险偿付能力主表常直接列出这几行明细
        "MARKET_RISK_CAPITAL",
        "CREDIT_RISK_CAPITAL",
        "NON_LIFE_INSURANCE_RISK_CAPITAL",
        "NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL",
        "NON_LIFE_CATASTROPHE_RISK_CAPITAL",
        "NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT",
        "INTEREST_RATE_RISK_CAPITAL",
        "EQUITY_RISK_CAPITAL",
        "SPREAD_RISK_CAPITAL",
        "COUNTERPARTY_RISK_CAPITAL",
        "QUANT_RISK_DIVERSIFICATION_EFFECT",
        "ADDITIONAL_CAPITAL",
        "CORE_SOLVENCY_SURPLUS",
        "COMBINED_SOLVENCY_SURPLUS",
        "CORE_SOLVENCY_RATIO",
        "COMBINED_SOLVENCY_RATIO",
    },
    "ACTUAL_CAPITAL": {
        "RECOGNIZED_ASSETS",
        "RECOGNIZED_LIABILITIES",
        "ACTUAL_CAPITAL",
        "CORE_T1_CAPITAL",
        "CORE_T2_CAPITAL",
        "ANC_T1_CAPITAL",
        "ANC_T2_CAPITAL",
        "NET_ASSETS",
        "POLICY_SURPLUS_CORE_T1",
        "POLICY_SURPLUS_CORE_T2",
        "POLICY_SURPLUS_ANC_T1",
        "POLICY_SURPLUS_ANC_T2",
        "FINANCIAL_STATEMENT_ASSETS",
        "FINANCIAL_STATEMENT_LIABILITIES",
        "FINANCIAL_STATEMENT_NET_ASSETS",
        "NET_ASSET_ADJUSTMENT",
        "NON_RECOGNIZED_ASSET_BOOK_VALUE",
        "LONG_TERM_EQUITY_VALUATION_DIFFERENCE",
        "DEFERRED_TAX_ASSET_ADJUSTMENT",
        "OTHER_CORE_T1_ADJUSTMENT",
        "CORE_T1_INVESTMENT_PROPERTY_FAIR_VALUE_ADJUSTMENT",
        "AGRICULTURAL_CATASTROPHE_RISK_RESERVE",
        "QUALIFYING_CORE_T1_LIABILITY_CAPITAL",
        "CORE_T2_PREFERRED_SHARES",
        "OTHER_CORE_T2_CAPITAL",
        "CORE_T2_EXCESS_DEDUCTION",
        "ANC_T1_SUBORDINATED_TERM_DEBT",
        "ANC_T1_CAPITAL_SUPPLEMENTARY_BONDS",
        "ANC_T1_CONVERTIBLE_SUBORDINATED_DEBT",
        "ANC_T1_DEFERRED_TAX_ASSET",
        "ANC_T1_INVESTMENT_PROPERTY_FAIR_VALUE",
        "OTHER_ANC_T1_CAPITAL",
        "ANC_T1_EXCESS_DEDUCTION",
        "EMERGENCY_OTHER_ANC_T2_CAPITAL",
        "ANC_T2_EXCESS_DEDUCTION",
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "INVESTMENT_RETURN",
        "COMPREHENSIVE_INVESTMENT_RETURN",
    },
}

# 标签排除规则：在指定指标编码下，遇到这些“归一化后完全等于”的标签就直接跳过。
# 目的：防止“资产合计”“非认可资产”“可资本化风险最低资本”等被
# 误挂到 RECOGNIZED_ASSETS / RECOGNIZED_LIABILITIES / MINIMUM_CAPITAL 上。
METRIC_EXCLUDE_LABELS: dict[str, tuple[str, ...]] = {
    "RECOGNIZED_ASSETS": (
        "资产合计",
        "资产总额",
        "财务报表资产总额",
        "非认可资产",
        "非认可资产合计",
        "非认可资产的账面价值",
        "其他认可资产",
        "投资资产",
        "再保险资产",
        "现金及流动性管理工具",
        "应收及预付款项",
        "固定资产",
        "土地使用权",
        "独立账户资产",
    ),
    "RECOGNIZED_LIABILITIES": (
        "负债合计",
        "负债总额",
        "财务报表负债总额",
        "非认可负债",
        "非认可负债合计",
        "准备金负债",
        "金融负债",
        "其他应付及预收款项",
    ),
    "MINIMUM_CAPITAL": (
        "可资本化风险最低资本",
        "量化风险最低资本",
        "保险风险最低资本",
        "市场风险最低资本",
        "信用风险最低资本",
        "控制风险最低资本",
        "附加资本",
    ),
    "ANC_T1_CAPITAL": (
        "资本工具合计",
        "资本工具一览表",
        "债券发行金额",
    ),
    # ★ 新增：防止"现金及流动性管理工具占比"（百分比）被误挂到
    #   "现金及流动性管理工具"（金额，万元）上。
    #   PDF 里这两个标签只差"占比"两个字，模糊匹配会误命中。
    "CASH_LIQUID_ASSETS": (
        "现金及流动性管理工具占比",
        "流动性资产占比",
    ),
    "DEFERRED_TAX_ASSET_ADJUSTMENT": (
        # 短名称来自认可资产表（正数），必须排除；
        # 调整项在 S02 的完整名称是“……（由经营性亏损引起的递延所得税资产除外）”
        "递延所得税资产",
    ),
    "SIGNED_PREMIUM": (
        "车险签单保费",
        "非车险前五大险种的签单保费",
        "各渠道签单保费",
        "代理渠道签单保费",
        "直销渠道签单保费",
        "经纪渠道签单保费",
        "其他渠道签单保费",
        "第一大险种的签单保费",
        "第二大险种的签单保费",
        "第三大险种的签单保费",
        "第四大险种的签单保费",
        "第五大险种的签单保费",
         # ★ 新增：防止"规模保费同比增速"被误映射到"签单保费"
        "规模保费同比增速",
        "保费同比增速",
        "规模保费增速",
        "保费增速",
    ),
}


@dataclass(frozen=True)
class MetricMatchDecision:
    metric: pd.Series | None
    score: float
    runner_up_score: float
    ambiguous: bool
    reason: str
    candidate_name: str = ""
    runner_up_name: str = ""


def load_taxonomy(path: str | Path) -> pd.DataFrame:
    """读指标字典；自动定位含'指标编码'的表头行，避免硬编码 header=2 在
    用户追加/插入行后失效。"""
    # 先无表头读取前 30 行，找到"指标编码"所在行
    preview = pd.read_excel(
        path, sheet_name="指标字典", header=None, nrows=30,
    )
    header_row: int | None = None
    for index, row in preview.iterrows():
        values = {str(value).strip() for value in row if not pd.isna(value)}
        if "指标编码" in values:
            header_row = int(index)
            break
    if header_row is None:
        raise ValueError(
            f"{path} 的'指标字典'sheet 在前 30 行内未找到'指标编码'表头。"
            "请检查 Excel 结构：第一行应为标题，第二行留空，"
            "第三行必须包含'指标编码/指标名称/别名/一级模块/二级模块/标准单位/数据类型'等列名。"
        )

    frame = pd.read_excel(path, sheet_name="指标字典", header=header_row)
    frame = frame.fillna("")
    for column in frame.columns:
        frame[column] = frame[column].astype(str).str.strip()
    return frame


def parse_numeric(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip().replace(",", "").replace("，", "")
    if text in {"", "-", "—", "--", "不适用", "未披露"}:
        return np.nan
    text = text.replace("％", "%")
    if text.endswith("%"):
        text = text[:-1].strip()
    if (text.startswith("(") and text.endswith(")")) or (text.startswith("（") and text.endswith("）")):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return np.nan


# ★ 新增：剥掉形如 "(=1+2+3)" 或 "（=1.1+1.2-1.3）" 的公式后缀
_FORMULA_SUFFIX_RE = re.compile(r"[（(]\s*=\s*[^）)]*[）)]")

# ★ 剥掉前导行号，同时支持阿拉伯数字（1、1.2、3.4.1）
#   和中文数字（一、二、三、……、十、十一、）
_LEADING_ROW_NUMBER_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"\d+(?:[.．]\d+)*\s*[.．、]?"                    # 阿拉伯数字，分隔符可选
    r"|[一二三四五六七八九十百千]{1,4}\s*[.．、]"     # 中文数字，必须跟分隔符
    r"|[（(]\s*(?:\d+|[一二三四五六七八九十百千]{1,4})\s*[）)]\s*[.．、]?"  # （一）/（1）
    r")"
    r"\s*"
)


def _normalize_label(value: str) -> str:
    text = _FORMULA_SUFFIX_RE.sub("", str(value or ""))   # ★ 先剥公式后缀
    # “未考虑特征系数前”是量化风险最低资本的独立口径；保留该后缀，
    # 避免与基础“量化风险最低资本”合并成同一个指标。
    text = _INLINE_UNIT_PATTERN.sub("", text)
    text = _LEADING_ROW_NUMBER_RE.sub("", text)           # ★ 再剥前导行号
    text = re.sub(r"^\s*其中\s*[:：]?", "", text)          # ★ 剥“其中：”前缀
    text = re.sub(r"[\s：:（）()、，,。·—\-_/]", "", text)
    # LCR 期限口径统一：部分报告写“三个月/一年”，部分写“未来3个月/未来12个月”。
    # 只对 LCR/流动性覆盖率相关标签做转换，避免影响其他指标的文本。
    if "LCR" in text.upper() or "流动性覆盖率" in text:
        text = re.sub(r"未来\s*(3|12)\s*个月", r"\1个月", text)
        text = re.sub(r"十二\s*个月", "12个月", text)
        text = re.sub(r"三\s*个月", "3个月", text)
        text = re.sub(r"一\s*年", "12个月", text)
    return text


def normalize_company_type(
    company_type: str,
    allowed_company_types: Sequence[str] | None = None,
) -> str:
    raw = str(company_type or "").strip()
    normalized = _COMPANY_TYPE_ALIASES.get(raw, raw)
    supported_types = tuple(allowed_company_types or LIFE_COMPANY_TYPES)
    if normalized not in supported_types:
        supported = "、".join(supported_types)
        raise ValueError(f"公司类型仅支持：{supported}")
    return normalized


def _canonical_unit(value: str) -> str:
    text = re.sub(r"[\s：:（）()]", "", str(value or "")).replace("％", "%")
    if "百分比" in text:
        return "%"
    if "百分点" in text:
        return "百分点"
    for unit in ("亿元", "万元", "千元", "元", "%", "人", "户", "件", "次", "级"):
        if text.endswith(unit):
            return unit
    return text


def _unit_from_text(value: str) -> str:
    matches = list(_INLINE_UNIT_PATTERN.finditer(str(value or "")))
    return _canonical_unit(matches[-1].group(1)) if matches else ""


def _select_unit(records, target_unit: str) -> str:
    units = list(dict.fromkeys(
        _canonical_unit(record.normalized_unit)
        for record in records
        if _canonical_unit(record.normalized_unit)
    ))
    if target_unit in _CURRENCY_UNIT_IN_YUAN:
        units = [unit for unit in units if unit in _CURRENCY_UNIT_IN_YUAN]
    elif target_unit == "%":
        units = [unit for unit in units if unit == "%"]
    return units[0] if len(units) == 1 else ""


def _source_unit(
    table: ExtractedTable,
    label: str,
    header: str,
    target_unit: str,
) -> str:
    # ★ 第一步：优先信任 app.py / batch_processor 已经嗅探并写入的 table.unit。
    #   app.py 已经按该单位换算过 grid；如果这里再扫描表头里的"（元）"，
    #   会把已换算的数值再除一次。所以这里必须最先判断。
    _declared_unit = _canonical_unit(getattr(table, "unit", "") or "")
    if _declared_unit and _declared_unit != "未识别":
        if target_unit in _CURRENCY_UNIT_IN_YUAN and _declared_unit in _CURRENCY_UNIT_IN_YUAN:
            return _declared_unit
        if target_unit == "%" and _declared_unit == "%":
            return _declared_unit
        if target_unit not in _CURRENCY_UNIT_IN_YUAN and target_unit != "%":
            return _declared_unit

    # ★ 第二步：查 unit_records（行级/列级/表级）
    normalized_label = _normalize_label(label)
    header_parts = [part for part in str(header or "").split("/") if part]
    normalized_headers = {_normalize_label(part) for part in header_parts}

    row_records = [
        record
        for record in table.unit_records
        if record.scope == "行级" and _normalize_label(record.target) == normalized_label
    ]
    selected = _select_unit(row_records, target_unit)
    if selected:
        return selected
    inline_row_unit = _unit_from_text(label)
    if inline_row_unit:
        return inline_row_unit

    column_records = [
        record
        for record in table.unit_records
        if record.scope == "列级" and _normalize_label(record.target) in normalized_headers
    ]
    selected = _select_unit(column_records, target_unit)
    if selected:
        return selected
    for part in reversed(header_parts):
        inline_column_unit = _unit_from_text(part)
        if inline_column_unit:
            return inline_column_unit

    table_records = [record for record in table.unit_records if record.scope == "表级"]
    selected = _select_unit(table_records, target_unit)
    if selected:
        return selected

    # ★ 第三步：兜底 —— 只有 table.unit 为空（或"未识别"）时才走这里
    if target_unit in _CURRENCY_UNIT_IN_YUAN:
        for row in getattr(table, "rows", [])[:5]:
            for cell in row:
                text = str(cell or "")
                m = re.search(r"单位\s*[：:]\s*(亿元|万元|千元|元)", text)
                if m:
                    return _canonical_unit(m.group(1))
                if "(元)" in text or "（元）" in text:
                    return "元"
                if "(千元)" in text or "（千元）" in text:
                    return "千元"
                if "(万元)" in text or "（万元）" in text:
                    return "万元"
                if "(亿元)" in text or "（亿元）" in text:
                    return "亿元"

    return ""


def _convert_unit(value: float, source_unit: str, target_unit: str) -> tuple[float, str]:
    if (
        source_unit not in _CURRENCY_UNIT_IN_YUAN
        or target_unit not in _CURRENCY_UNIT_IN_YUAN
        or source_unit == target_unit
    ):
        return value, ""
    converted = (
        value
        * _CURRENCY_UNIT_IN_YUAN[source_unit]
        / _CURRENCY_UNIT_IN_YUAN[target_unit]
    )
    return converted, f"原单位：{source_unit}；已换算为{target_unit}"


def _taxonomy_candidates(taxonomy: pd.DataFrame) -> list[tuple[int, list[str]]]:
    candidates = []
    for index, row in taxonomy.iterrows():
        aliases = [row.get("指标名称", "")]
        aliases += [item.strip() for item in str(row.get("别名", "")).split("|") if item.strip()]
        candidates.append((index, [_normalize_label(alias) for alias in aliases if alias]))
    return candidates
    

def _should_exclude_metric(normalized_label: str, code: str) -> bool:
    """按归一化标签与指标编码做精确排除。"""
    excludes = METRIC_EXCLUDE_LABELS.get(code)
    if not excludes:
        return False
    return normalized_label in {_normalize_label(term) for term in excludes}
    

def resolve_metric_match(
    label: str,
    taxonomy: pd.DataFrame,
    candidates=None,
    *,
    table_id: str = "",
    allow_all_codes: bool = False,
    minimum_score: float = 0.72,
    ambiguity_margin: float = 0.08,
) -> MetricMatchDecision:
    normalized = _normalize_label(label)
    if not normalized:
        return MetricMatchDecision(None, 0.0, 0.0, False, "项目名称为空")
    # ★ 精确锁定：归一化标签完全命中 `_EXACT_LABEL_LOCKS` 时直接返回
    _locked_code = _EXACT_LABEL_LOCKS.get(normalized)
    if _locked_code:
        _lock_rows = taxonomy.loc[
            taxonomy["指标编码"].astype(str).str.strip() == _locked_code
        ]
        if not _lock_rows.empty:
            return MetricMatchDecision(
                _lock_rows.iloc[0],
                2.0,
                0.0,
                False,
                "精确锁定",
            )
    candidates = candidates or _taxonomy_candidates(taxonomy)
    allowed_codes = None if allow_all_codes else TABLE_ALLOWED_CODES.get(table_id)
    if not allow_all_codes and table_id in {"OPERATING_METRICS", "NON_LIFE_OPERATING_METRICS"}:
        allowed_codes = set(
            taxonomy.loc[taxonomy["一级模块"] == "经营指标", "指标编码"].astype(str)
        )
    elif not allow_all_codes and table_id == "MINIMUM_CAPITAL":
        allowed_codes = set(
            taxonomy.loc[taxonomy["一级模块"] == "最低资本", "指标编码"].astype(str)
        )
    elif not allow_all_codes and table_id == "SOLVENCY_MAIN":
        # 财险主表包含经营口径的认可资产等，不再额外扩范围
        allowed_codes = TABLE_ALLOWED_CODES["SOLVENCY_MAIN"]
        
    ranked: list[tuple[float, bool, int]] = []
    for index, aliases in candidates:
        code = str(taxonomy.loc[index].get("指标编码", "")).strip()
        if allowed_codes is not None and code not in allowed_codes:
            continue
        # ★ 新增：命中排除词直接跳过
        if _should_exclude_metric(normalized, code):
            continue
        candidate_score = 0.0
        exact = False
        for alias in aliases:
            if not alias:
                continue
            if alias == normalized:
                score = 2.0
                exact = True
            elif alias in normalized or normalized in alias:
                score = min(len(alias), len(normalized)) / max(len(alias), len(normalized)) + 0.5
            else:
                score = SequenceMatcher(None, normalized, alias).ratio()
            candidate_score = max(candidate_score, score)
        if candidate_score:
            ranked.append((candidate_score, exact, index))
            
    ranked.sort(key=lambda item: (-item[0], -int(item[1]), item[2]))

    # ================================================================
    # ★ 寿险 / 非寿险 硬互斥
    # 原因：S05 表里"寿险业务保险风险最低资本合计"(0.00) 会被模糊匹配
    #      到 NON_LIFE_INSURANCE_RISK_CAPITAL，导致去重时把 0 值当成
    #      "非寿险业务保险风险最低资本合计"，真值 594,557.27 被顶掉。
    # 规则：标签里带"寿险"（且不带"非寿险"）→ 排除所有 NON_LIFE_* 编码；
    #      标签里带"非寿险"→ 排除所有 LIFE_*（但保留 NON_LIFE_*）。
    # ================================================================
    _has_non_life_token = "非寿险" in normalized
    _has_life_token = ("寿险" in normalized) and not _has_non_life_token
    if _has_non_life_token or _has_life_token:
        _filtered_ranked: list[tuple[float, bool, int]] = []
        for _s, _e, _idx in ranked:
            _code = str(taxonomy.loc[_idx].get("指标编码", "")).strip()
            _is_non_life_code = _code.startswith("NON_LIFE_")
            _is_life_code = _code.startswith("LIFE_") and not _is_non_life_code
            if _has_non_life_token and _is_life_code:
                continue
            if _has_life_token and _is_non_life_code:
                continue
            _filtered_ranked.append((_s, _e, _idx))
        # 过滤后为空时，保留原始候选（避免误杀；只在确实有 LIFE/NON_LIFE 冲突时才生效）
        if _filtered_ranked:
            ranked = _filtered_ranked

    if not ranked:
        return MetricMatchDecision(None, 0.0, 0.0, False, "目标表范围内没有候选指标")

    best_score, exact, best_index = ranked[0]
    runner_score = ranked[1][0] if len(ranked) > 1 else 0.0
    runner_exact = ranked[1][1] if len(ranked) > 1 else False
    best_name = str(taxonomy.loc[best_index].get("指标名称", ""))
    runner_name = (
        str(taxonomy.loc[ranked[1][2]].get("指标名称", ""))
        if len(ranked) > 1 else ""
    )
    if best_score < minimum_score:
        return MetricMatchDecision(
            None,
            best_score,
            runner_score,
            False,
            f"最佳匹配得分低于{minimum_score:.2f}",
            best_name,
            runner_name,
        )
    ambiguous = (
        len(ranked) > 1
        and runner_score >= minimum_score
        and best_score - runner_score < ambiguity_margin
        and (not exact or runner_exact)
    )
    if ambiguous:
        return MetricMatchDecision(
            None,
            best_score,
            runner_score,
            True,
            f"前两候选分差小于{ambiguity_margin:.2f}",
            best_name,
            runner_name,
        )
    return MetricMatchDecision(
        taxonomy.loc[best_index],
        best_score,
        runner_score,
        False,
        "精确别名匹配" if exact else "唯一高置信度匹配",
        best_name,
        runner_name,
    )


_AUTO_COLUMN_RE = re.compile(r"^列\d+$")

def _period_header(row: list[str], index: int) -> str:
    current = str(row[index]).strip() if index < len(row) else ""
    if _AUTO_COLUMN_RE.match(current):   # ★ 新增
        return ""

    # ★ 新增：剥掉表头里的括号补充说明，让
    #   "本季度(期末)数" → "本季度数"
    #   "上季度(期初)数" → "上季度数"
    #   "本季度（期末）" → "本季度"
    # 这样 _PERIOD_TERMS_SORTED 就能匹配到完整的"本季度数"/"本季度末数"。
    current = re.sub(
        r"[（(]\s*(期末|期初|末|初|审计后|未经审计|预测)\s*[）)]",
        "",
        current,
    ).strip()
    if (
        any(
            token in current
            for token in ("本年度", "年度累计", "全年", "本期累计")
        )
        and "预测" not in current
    ):
        return "本年累计数"
    if any(token in current for token in ("上年度", "上年末")):
        return "上季度末数"
    if current == "累计" or current.endswith("累计"):
        return "本年累计数"

    _PERIOD_TERMS_SORTED = sorted(PERIOD_TERMS, key=len, reverse=True)
    
    for term in _PERIOD_TERMS_SORTED:    # ★ 改
        if term in current:
            return PERIOD_ALIASES.get(term, term)
    if re.search(r"20[0-9]{2}", current):
        if re.search(r"[0-9]{1,2}\s*月", current):
            return "期末数"
        return "本年累计数"
    return current

# 存量类指标（资本/资产/负债类）的“本季度数”与“本季度末数”语义相同，
# 统一归一到“本季度末数”，避免太平（本季度数）与平安/人保/太保（本季度末数）
# 在 Step7 表格里分列显示成两套。
# 经营指标（保险业务收入、净利润等）不在白名单内，保留“本季度数”。
_STOCK_PERIOD_INDICATORS = {
    # 资本与偿付能力
    "RECOGNIZED_ASSETS", "RECOGNIZED_LIABILITIES", "ACTUAL_CAPITAL",
    "CORE_T1_CAPITAL", "CORE_T2_CAPITAL", "ANC_T1_CAPITAL", "ANC_T2_CAPITAL",
    "MINIMUM_CAPITAL", "QUANT_RISK_CAPITAL", "CONTROL_RISK_CAPITAL",
    "QUANT_RISK_CAPITAL_BEFORE_FEATURE_FACTOR",
    "ADDITIONAL_CAPITAL",
    "CORE_SOLVENCY_SURPLUS", "COMBINED_SOLVENCY_SURPLUS",
    "CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO",
    "NET_ASSETS", "NET_ASSET_ADJUSTMENT",
    "NON_RECOGNIZED_ASSET_BOOK_VALUE", "DEFERRED_TAX_ASSET_ADJUSTMENT",
    "AGRICULTURAL_CATASTROPHE_RISK_RESERVE",
    "LONG_TERM_EQUITY_VALUATION_DIFFERENCE",
    # ★ 本次新增：S05 最低资本明细（S05 表头也是“本季度数”）
    "NON_LIFE_INSURANCE_RISK_CAPITAL",
    "NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL",
    "NON_LIFE_CATASTROPHE_RISK_CAPITAL",
    "NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT",
    "MARKET_RISK_CAPITAL",
    "INTEREST_RATE_RISK_CAPITAL",
    "EQUITY_RISK_CAPITAL",
    "REAL_ESTATE_RISK_CAPITAL",
    "OVERSEAS_FIXED_INCOME_RISK_CAPITAL",
    "OVERSEAS_EQUITY_RISK_CAPITAL",
    "FOREIGN_EXCHANGE_RISK_CAPITAL",
    "MARKET_RISK_DIVERSIFICATION_EFFECT",
    "CREDIT_RISK_CAPITAL",
    "SPREAD_RISK_CAPITAL",
    "COUNTERPARTY_RISK_CAPITAL",
    "CREDIT_RISK_DIVERSIFICATION_EFFECT",
    # 认可资产/负债明细（若报告披露）
    "CASH_LIQUID_ASSETS", "INVESTMENT_ASSETS", "REINSURANCE_ASSETS",
    "RESERVE_LIABILITIES",
    # 保单未来盈余
    "POLICY_SURPLUS_CORE_T1", "POLICY_SURPLUS_CORE_T2",
    "POLICY_SURPLUS_ANC_T1", "POLICY_SURPLUS_ANC_T2",
    "QUANT_RISK_DIVERSIFICATION_EFFECT",
}

# ★ 品质类指标：PDF 里通常只披露"本年累计数"这一列有意义。
#   由于跨页合并表头可能错位，把"上季度数"归一到"本年累计数"作为兜底。
_QUALITY_PERIOD_CODES = {
    "COMBINED_RATIO",
    "LOSS_RATIO",
    "EXPENSE_RATIO",
    "COMMISSION_EXPENSE_RATIO",
    "ADMIN_EXPENSE_RATIO",
}


def _normalize_period_alias(period: str, metric_code: str = "") -> str:
    """把不同叫法归一；存量类指标额外把本季度数/上季度数归一到末数。"""
    mapping = {
        "期末数": "本季度末数",
        "期初数": "上季度末数",
        "本年累计数": "本年累计数",
        "本年度累计数": "本年累计数",
        "本年度": "本年累计数",
        "本年度末": "本年累计数",
        "年度累计": "本年累计数",
        "全年累计": "本年累计数",
        "全年": "本年累计数",
        "本期累计": "本年累计数",
        "累计": "本年累计数",
        "本季度末数": "本季度末数",
        "上季度末数": "上季度末数",
        "上年度": "上季度末数",
        "上年末": "上季度末数",
        "上年度末": "上季度末数",
        "下季度末预测数": "下季度末预测数",
        "本季末": "本季度末数",
        "上季末": "上季度末数",
        "下季末": "下季度末预测数",
        "本期": "本季度数",
        "上期": "上季度数",
    }
    if metric_code in _STOCK_PERIOD_INDICATORS:
        mapping["本季度数"] = "本季度末数"
        mapping["上季度数"] = "上季度末数"
        # ★ 新增：兜底覆盖 _period_header 可能返回的"本季度"/"上季度"
        mapping["本季度"] = "本季度末数"
        mapping["上季度"] = "上季度末数"
        mapping["本期"] = "本季度末数"
        mapping["上期"] = "上季度末数"
        # ★ 新增
        mapping["本季末"] = "本季度末数"
        mapping["上季末"] = "上季度末数"
    else:
        mapping["本季度数"] = "本季度数"
        mapping["上季度数"] = "上季度数"
        # ★ 新增
        mapping["本季度"] = "本季度数"
        mapping["上季度"] = "上季度数"
        mapping["本期"] = "本季度数"
        mapping["上期"] = "上季度数"
        # ★ 新增
        mapping["本季末"] = "本季度末数"
        mapping["上季末"] = "上季度末数"
    # ★ 品质类兜底：PDF 里 C 列通常是"本年度累计数"，
    #   若因表头错位被识别为"上季度数"，这里再纠正一次。
    if metric_code in _QUALITY_PERIOD_CODES and period == "上季度数":
        return "本年累计数"

    return mapping.get(period, period)


def infer_period_label(value: object) -> str:
    """Infer a canonical period from a human-readable header label.

    Returns an empty string when local semantic rules cannot decide. The
    optional LLM fallback only receives these unresolved labels.
    """
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return ""
    if any(
        token in text
        for token in ("近三年", "近3年", "三年平均", "3年平均")
    ) or re.search(r"20\d{2}年(?:至|到|-|—)20\d{2}年", text):
        return "近三年平均"
    if any(token in text for token in ("下季度", "下季末", "预测")):
        return "下季度末预测数"
    if any(
        token in text
        for token in ("本年度", "年度累计", "全年", "本期累计")
    ):
        return "本年累计数"
    if text == "累计" or text.endswith("累计"):
        return "本年累计数"
    if any(token in text for token in ("上年度", "上年末")):
        return "上季度末数"
    if "本年累计" in text:
        return "本年累计数"
    if any(token in text for token in ("本季度末", "本季末")):
        return "本季度末数"
    if any(token in text for token in ("上季度末", "上季末", "期初")):
        return "上季度末数"
    if any(token in text for token in ("本季度", "本期")):
        return "本季度数"
    if any(token in text for token in ("上季度", "上期")):
        return "上季度数"
    if "期末" in text:
        return "期末数"
    return ""


_HEADER_LABEL_TERMS = (
    "指标名称",
    "项目",
    "行次",
    "序号",
    "指标",
    "监测指标",
)
_HEADER_PERIOD_TERMS = tuple(PERIOD_TERMS) + (
    "审计后",
    "审计前",
    "账面价值",
    "非认可",
    "认可价值",
)
_TABLE_DEFAULT_PERIODS = {
    "SOLVENCY_MAIN": ("本季度末数", "上季度末数", "下季度末预测数"),
    "ACTUAL_CAPITAL": ("本季度末数", "上季度末数"),
    "MINIMUM_CAPITAL": ("本季度末数", "上季度末数"),
    "OPERATING_METRICS": ("本季度数", "本年累计数"),
    "NON_LIFE_OPERATING_METRICS": ("本季度数", "本年累计数"),
    "LIQUIDITY_MONITORING": ("本季度数", "本年累计数"),
}


def _is_header_numeric(value: object) -> bool:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    if not text:
        return False
    text = text.strip("()（）").replace("%", "").replace("％", "")
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def _header_row_signal(row: Sequence[str]) -> tuple[int, int, int, int]:
    text = "".join(str(cell or "") for cell in row)
    label_hits = sum(term in text for term in _HEADER_LABEL_TERMS)
    period_hits = sum(term in text for term in _HEADER_PERIOD_TERMS)
    numeric = sum(_is_header_numeric(cell) for cell in row)
    score = label_hits * 10 + min(period_hits, 5) * 4 - numeric * 3
    return score, label_hits, period_hits, numeric


def _is_table_subheader(row: Sequence[str]) -> bool:
    text = "".join(str(cell or "") for cell in row)
    hits = sum(
        term in text
        for term in (
            "审计后",
            "审计前",
            "账面价值",
            "非认可",
            "认可价值",
            "预测",
            "本季度",
            "上季度",
            "期末",
            "期初",
        )
    )
    numeric = sum(_is_header_numeric(cell) for cell in row)
    return hits >= 1 and numeric <= 1


def _locate_table_header(
    rows: Sequence[Sequence[str]],
) -> tuple[int, bool] | None:
    best: tuple[int, int] | None = None
    for index, row in enumerate(rows[:8]):
        score, label_hits, period_hits, numeric = _header_row_signal(row)
        nonempty = sum(bool(str(cell or "").strip()) for cell in row)
        if numeric > 1:
            continue
        if (
            index <= 2
            and nonempty >= 2
            and numeric == 0
            and label_hits >= 1
            and period_hits == 0
        ):
            if best is None or score > best[0]:
                best = (score, index)
        elif label_hits >= 1 and (period_hits >= 1 or label_hits >= 2):
            if best is None or score > best[0]:
                best = (score, index)
        elif period_hits >= 2 and numeric == 0:
            if best is None or score > best[0]:
                best = (score, index)
    if best is None or best[0] < 6:
        return None
    index = best[1]
    multi = (
        index + 1 < len(rows)
        and _is_table_subheader(rows[index + 1])
    )
    return index, multi


def normalize_tables(
    tables: list[ExtractedTable],
    taxonomy: pd.DataFrame,
    metadata: dict,
    company_type: str = "寿险",
    report_profile_id: str = "LIFE_SOLVENCY",
    diagnostics: list[dict] | None = None,
    allowed_company_types: Sequence[str] | None = None,
    peer_group: str = "",
    peer_group_map: Mapping[str, str] | None = None,
    default_peer_group: str = "",
    company_type_map: Mapping[str, str] | None = None,
    period_overrides: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    company_type = normalize_company_type(company_type, allowed_company_types)
    company_identity = resolve_company_identity(
        metadata.get("公司", ""),
        company_type_map,
        fallback_company_type=company_type,
    )
    resolved_peer_group = str(peer_group or "").strip()
    if not resolved_peer_group and peer_group_map:
        resolved_peer_group = resolve_peer_group(
            metadata.get("公司", ""),
            peer_group_map,
            fallback_peer_group=default_peer_group,
        )
    records: list[dict] = []
    candidates = _taxonomy_candidates(taxonomy)
    for table in tables:
        whole_page_block = bool(getattr(table, "whole_page_block", False))
        rows = table.rows
        if len(rows) < 2:
            continue
        located = _locate_table_header(rows)
        if located is None:
            header_index = None
            is_multiheader = False
            header_rows = []
            data_start = 0
        else:
            header_index, is_multiheader = located
            header_rows = (
                rows[header_index:header_index + 2]
                if is_multiheader
                else [rows[header_index]]
            )
            data_start = header_index + len(header_rows)
            if is_multiheader:
                filled_first_header = []
                last_header = ""
                for value in rows[header_index]:
                    last_header = value or last_header
                    filled_first_header.append(last_header)
                header_rows = [filled_first_header, rows[header_index + 1]]
        max_width = max(len(row) for row in rows)
        headers = []
        for column_index in range(max_width):
            parts = []
            for header_row in header_rows:
                value = header_row[column_index] if column_index < len(header_row) else ""
                if value and value not in parts:
                    parts.append(value)
            headers.append("/".join(parts))

        has_header_row_column = any(
            ("行次" in str(h)) or ("序号" in str(h)) for h in headers[:2]
        )
        if diagnostics is not None and "偿付能力充足率" in str(table.table_name):
            diagnostics.append({
                "目标表": table.table_name,
                "来源页码": "、".join(map(str, table.source_pages or [table.page])),
                "原始项目": f"[DEBUG] headers={headers[:8]}",
                "状态": "调试",
                "原因": f"max_width={max_width}, "
                        f"data_start={data_start}, "
                        f"is_multiheader={is_multiheader}, "
                        f"has_header_row_column={has_header_row_column}, "
                        f"header_rows_len={[len(h) for h in header_rows]}",
                "最佳候选": "",
                "最佳得分": 0,
                "第二候选": "",
                "第二得分": 0,
            })
            # 额外 dump 前 3 行原始行，看行列数是否匹配
            for _i, _row_dbg in enumerate(rows[:3]):
                diagnostics.append({
                    "目标表": table.table_name,
                    "来源页码": f"行{_i}",
                    "原始项目": f"[DEBUG] row{_i} len={len(_row_dbg)}: "
                                f"{[str(c)[:20] for c in _row_dbg[:8]]}",
                    "状态": "调试",
                    "原因": "",
                    "最佳候选": "", "最佳得分": 0,
                    "第二候选": "", "第二得分": 0,
                })
        for row in rows[data_start:]:
            row = row + [""] * (max_width - len(row))
            row_number = row[0] if re.fullmatch(r"\d+(?:\.\d+)*\*?", row[0].strip()) else ""
            label_index = 1 if row_number and max_width > 1 else 0
            header_offset = 1 if (row_number and not has_header_row_column) else 0  # ★ 新增
            label = row[label_index].strip()
            decision = None
            if table.table_id == "ACTUAL_CAPITAL" and row_number:
                contextual = resolve_metric_match(
                    f"{row_number} {label}",
                    taxonomy,
                    candidates,
                    table_id=table.table_id,
                    allow_all_codes=whole_page_block,
                )
                if contextual.metric is not None and contextual.score == 2.0:
                    decision = contextual
            if decision is None:
                decision = resolve_metric_match(
                    label,
                    taxonomy,
                    candidates,
                    table_id=table.table_id,
                    allow_all_codes=whole_page_block,
                )
            label_index_used = label_index
            if decision.metric is None and label_index + 1 < max_width:
                secondary = str(row[label_index + 1]).strip()
                if (
                    secondary
                    and not _is_header_numeric(secondary)
                    and any('\u4e00' <= ch <= '\u9fff' for ch in secondary)
                ):
                    combined = label + secondary
                    combined_decision = resolve_metric_match(
                        combined,
                        taxonomy,
                        candidates,
                        table_id=table.table_id,
                        allow_all_codes=whole_page_block,
                    )
                    if combined_decision.metric is not None:
                        decision = combined_decision
                        label_index_used = label_index + 1
            if decision.metric is None and not whole_page_block:
                exact_any = resolve_metric_match(
                    label,
                    taxonomy,
                    candidates,
                    table_id=table.table_id,
                    allow_all_codes=True,
                )
                if exact_any.metric is not None and exact_any.score >= 2.0:
                    decision = exact_any
            metric = decision.metric
            if metric is None:
                if diagnostics is not None and label:
                    diagnostics.append({
                        "目标表": table.table_name,
                        "来源页码": "、".join(map(str, table.source_pages or [table.page])),
                        "原始项目": label,
                        "状态": "歧义" if decision.ambiguous else "未匹配",
                        "原因": decision.reason,
                        "最佳候选": decision.candidate_name,
                        "最佳得分": round(decision.score, 3),
                        "第二候选": decision.runner_up_name,
                        "第二得分": round(decision.runner_up_score, 3),
                    })
                continue
            value_start = label_index_used + 1
            for column_index in range(value_start, max_width):
                raw_value = row[column_index]
                numeric_value = parse_numeric(raw_value)
                data_type = str(metric.get("数据类型", "金额"))
                target_unit = str(metric.get("标准单位", "")).strip()
                note = ""
                blank_capital_zero = False
                placeholder_zero = False
                if data_type in {"文本", "评级", "布尔"}:
                    value = raw_value.strip()
                    if not value:
                        continue
                else:
                    if pd.isna(numeric_value):
                        _metric_code = str(metric.get("指标编码", "")).strip()
                        _table_id = str(getattr(table, "table_id", "")).strip()
                        if (
                            not str(raw_value).strip()
                            and _metric_code in CAPITAL_COMPONENT_ZERO_CODES
                            and _table_id in CAPITAL_COMPONENT_ZERO_TABLE_IDS
                        ):
                            numeric_value = 0.0
                            blank_capital_zero = True
                        else:
                            numeric_value = 0.0
                            placeholder_zero = True
                    value = numeric_value
                    _metric_code_for_unit = str(metric.get("指标编码", "")).strip()
                    if _metric_code_for_unit in _UNIT_CONVERT_EXEMPT_CODES:
                        note = ""
                    else:
                        source_unit = _source_unit(
                            table,
                            label,
                            headers[column_index],
                            target_unit,
                        )
                        if target_unit in _CURRENCY_UNIT_IN_YUAN and not source_unit:
                            note = "未识别原始单位，数值未换算"
                        else:
                            value, conversion_note = _convert_unit(
                                value,
                                source_unit,
                                target_unit,
                            )
                            note = conversion_note

                    # ★ 新增：百分比类指标的合理性兜底校验
                    # 某些 PDF 表格（如"流动性风险监测指标"）里，"占比/增速"
                    # 这类百分比行的下方紧跟"分子/分母金额"明细行，AI 容易把
                    # 金额误挂到百分比指标上。这里用绝对阈值做简单防护：
                    #   - 单位是 "%"，数值绝对值 > 1000 → 明显异常，丢弃
                    #   - 单位是 "倍"，数值绝对值 > 1000 → 丢弃
                    _is_ratio_unit = str(target_unit).strip() in {"%", "百分比", "倍"}
                    if _is_ratio_unit and abs(float(value)) > 1000:
                        if diagnostics is not None and label:
                            diagnostics.append({
                                "目标表": table.table_name,
                                "来源页码": "、".join(map(str, table.source_pages or [table.page])),
                                "原始项目": label,
                                "状态": "未匹配",
                                "原因": (
                                    f"百分比指标数值异常（{value}），"
                                    "疑似误把金额明细行当作百分比值，已丢弃"
                                ),
                                "最佳候选": str(metric.get("指标名称", "")),
                                "最佳得分": 0,
                                "第二候选": "",
                                "第二得分": 0,
                            })
                        continue

                    if blank_capital_zero:
                        note = "资本明细空白，按0处理" + (
                            f"；{note}" if note else ""
                        )
                    elif placeholder_zero:
                        original = str(raw_value).strip() or "空值"
                        note = (
                            f"{MISSING_NUMERIC_ZERO_MARKER}"
                            f"（原始值：{original}）"
                            + (f"；{note}" if note else "")
                        )

                header_index = max(0, column_index - header_offset)
                period = _period_header(headers, header_index)
                if period in {"金额", "金额（万元）", "金额(万元)", "数值"}:
                    period = ""
                if period_overrides:
                    raw_period_header = str(headers[header_index]).strip()
                    override = period_overrides.get(raw_period_header)
                    if not override:
                        override = next(
                            (
                                mapped
                                for label, mapped in period_overrides.items()
                                if label and label in raw_period_header
                            ),
                            "",
                        )
                    if override:
                        period = override
                
                # ★ 近三年投资收益率表强制期间 = "近三年平均"
                if "近三年" in str(getattr(table, "table_name", "")):
                    period = "近三年平均"
                
                # ★ 新增：从行标签推断"近三年平均"（优先级高于列头）
                # 原因：财险主表把"近三年平均投资收益率 2.34%"放在"本季度数"列里，
                #      如果只看列头会被识别成本季度数，导致真实的季度值被顶掉。
                _label_text = str(label or "")
                if ("近三年" in _label_text) and ("平均" in _label_text):
                    period = "近三年平均"

                period_default_note = ""
                if not period:
                    _defaults = tuple(getattr(table, "default_periods", ()) or ())
                    _header_label = str(headers[header_index]).strip()
                    _generic_header = _header_label in {
                        "", "金额", "金额（万元）", "金额(万元)", "数值",
                    }
                    _can_fallback = (not header_rows) or _generic_header
                    if header_index < len(_defaults) and _defaults[header_index]:
                        period = str(_defaults[header_index])
                    elif _can_fallback:
                        _ordinal = max(0, column_index - value_start)
                        _table_defaults = _TABLE_DEFAULT_PERIODS.get(
                            str(getattr(table, "table_id", "")).strip(),
                            ("本季度末数", "上季度末数", "下季度末预测数"),
                        )
                        if _ordinal < len(_table_defaults):
                            period = str(_table_defaults[_ordinal])
                        else:
                            period = "本季度末数"
                    else:
                        # 表头存在但期间确实无法识别（例如“列2”“项目”），
                        # 不臆造期间；留给补漏匹配按行标签兜底。
                        continue
                    period_default_note = (
                        f"期间未识别，按默认口径'{period}'处理"
                    )

                if (
                    period == "期末数"
                    and re.search(r"20[0-9]{2}", str(headers[header_index]))
                ):
                    _date_ordinal = max(0, column_index - value_start)
                    period = (
                        "本季度末数",
                        "上季度末数",
                        "下季度末预测数",
                    )[min(_date_ordinal, 2)]
                period = _normalize_period_alias(
                    period,
                    metric_code=str(metric.get("指标编码", "")).strip(),
                )
                if period_default_note:
                    note = (
                        f"{note}；{period_default_note}"
                        if note
                        else period_default_note
                    )
                if blank_capital_zero and "预测" in period:
                    # 空白不自动解释为“下季度预测为0”，只对本期/上期资本明细生效。
                    continue
                
                if period.endswith("/认可价值"):
                    period = period.removesuffix("/认可价值")
                elif period.endswith("/账面价值"):
                    period = period.removesuffix("/账面价值") + "-账面价值"
                elif period.endswith("/非认可"):
                    period = period.removesuffix("/非认可") + "-非认可"
                records.append({
                    "公司": company_identity.standard_name,
                    "原始公司名称": company_identity.original_name,
                    "标准公司名称": company_identity.standard_name,
                    "公司统一编码": company_identity.company_code,
                    "公司类型": company_identity.company_type,
                    "同业分类": resolved_peer_group,
                    "报告类型": report_profile_id,
                    "报告年度": metadata.get("报告年度"),
                    "报告季度": metadata.get("报告季度", ""),
                    "报告期": metadata.get("报告期", ""),
                    "披露日期": metadata.get("披露日期", ""),
                    "一级模块": metric.get("一级模块", table.table_name),
                    "二级模块": metric.get("二级模块", ""),
                    "行次": row_number,
                    "指标编码": metric.get("指标编码", ""),
                    "指标名称": metric.get("指标名称", label),
                    "期间口径": period,
                    "数值": value,
                    "单位": target_unit,
                    "数据类型": data_type,
                    "是否预测": "是" if "预测" in period else "否",
                    "来源页码": "、".join(map(str, table.source_pages)) if table.source_pages else table.page,
                    "原始披露值": raw_value,
                    "备注": note,
                    "来源类型": "报告提取",
                    "指标属性": "披露",
                    "来源文件": metadata.get("来源文件", ""),
                    "来源工作表": table.table_name,
                    "导入批次": metadata.get("导入批次", ""),
                    "计算逻辑": "",
                })
    return apply_navigation_labels(pd.DataFrame(records, columns=STANDARD_COLUMNS))


def standardize_uploaded_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in STANDARD_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    return apply_navigation_labels(result[STANDARD_COLUMNS])


def narrow_table_view(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Return the compact STEP3/STEP5 display and workbook exchange schema."""
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame(columns=NARROW_TABLE_COLUMNS)
    result = frame.copy()
    for column in NARROW_TABLE_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    return result.reindex(columns=NARROW_TABLE_COLUMNS)


def upgrade_standard_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Upgrade a saved/session DataFrame to the current standard schema."""
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    return apply_company_identities(standardize_uploaded_frame(frame))

