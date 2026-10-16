"""图表底层原语：配色/阈值常量、标签与格式化、主题、分面与坐标轴工具。"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

import altair as alt
import pandas as pd
from ..solvency_navigation import (
    KPMG_BRIGHT_CHART_COLORS,
    KPMG_CAPITAL_TIER_COLORS,
    KPMG_CHART_COLORS,
    KPMG_PERIOD_CHART_COLORS,
)

TRANSPARENT = "transparent"
ANNUAL_REPORT_FONT = "Microsoft YaHei"
REGULATORY_LIMITS = {
    "CORE_SOLVENCY_RATIO": 50.0,
    "COMBINED_SOLVENCY_RATIO": 100.0,
}
CAPITAL_AMOUNT_CODES = (
    "CORE_T1_CAPITAL",
    "CORE_T2_CAPITAL",
    "ANC_T1_CAPITAL",
    "ANC_T2_CAPITAL",
)
COMPONENT_LABELS = {
    "CORE_T1_CAPITAL": "核心一级资本",
    "CORE_T2_CAPITAL": "核心二级资本",
    "ANC_T1_CAPITAL": "附属一级资本",
    "ANC_T2_CAPITAL": "附属二级资本",
    "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL": "风险分散效应",
    "LOSS_ABSORPTION_TO_QUANT_CAPITAL": "损失吸收效应",
}
COMPONENT_COLORS = {
    "核心一级资本": KPMG_CAPITAL_TIER_COLORS[0],
    "核心二级资本": KPMG_CAPITAL_TIER_COLORS[1],
    "附属一级资本": KPMG_CAPITAL_TIER_COLORS[2],
    "附属二级资本": KPMG_CAPITAL_TIER_COLORS[3],
    "风险分散效应": KPMG_BRIGHT_CHART_COLORS[4],
    "损失吸收效应": KPMG_BRIGHT_CHART_COLORS[5],
}
CAPITAL_STRUCTURE_COLORS = {
    "核心一级资本": "#00B8F5",
    "核心二级资本": "#ACEAFF",
    "附属一级资本": "#B497FF",
    "附属二级资本": "#7213EA",
}
CAPITAL_COMPONENT_ORDER = {label: index for index, label in enumerate(list(COMPONENT_COLORS)[:4])}
MIN_INSIDE_LABEL_SHARE = 0.045
TREND_LABEL_FORMATS = {
    "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES": ",.5f",
    "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL": ",.1f",
    "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL": ",.1f",
    "MARKET_RISK_TO_QUANT_CAPITAL": ",.1f",
    "CREDIT_RISK_TO_QUANT_CAPITAL": ",.1f",
    "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL": ",.1f",
    "LOSS_ABSORPTION_TO_QUANT_CAPITAL": ",.1f",
}
TREND_AXIS_FORMATS = {
    code: ",.1f"
    for code in (
        "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
        "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
        "MARKET_RISK_TO_QUANT_CAPITAL",
        "CREDIT_RISK_TO_QUANT_CAPITAL",
        "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL",
        "LOSS_ABSORPTION_TO_QUANT_CAPITAL",
    )
}
TREND_LABEL_THRESHOLDS = {
    "POLICY_SURPLUS_CORE_TO_CORE_CAPITAL": 40.0,
}
COMPACT_TREND_SCALE_CODES = {
    "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
}


def report_period_color_map(period_order: Iterable[str]) -> dict[str, str]:
    """Assign stable annual-report-style KPMG colors to report periods."""
    periods = list(dict.fromkeys(str(period) for period in period_order))
    return {
        period: KPMG_PERIOD_CHART_COLORS[index % len(KPMG_PERIOD_CHART_COLORS)]
        for index, period in enumerate(periods)
    }


def label_color_for_fill(color: str) -> str:
    """Use white labels only on the darker official KPMG fill colors."""
    dark_fills = {"#00338D", "#1E49E2", "#0C233C", "#7213EA", "#510DBC"}
    return "#FFFFFF" if str(color).upper() in dark_fills else "#0C233C"


def whole_number_label(value: float) -> str:
    """Format chart labels as integers using conventional half-up rounding."""
    number = float(value)
    rounded = math.floor(abs(number) + 0.5)
    prefix = "-" if number < 0 and rounded else ""
    return f"{prefix}{rounded:,}"


def whole_percent_label(value: float) -> str:
    return f"{whole_number_label(float(value) * 100)}%"


def clean_numeric(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["数值"] = pd.to_numeric(result["数值"], errors="coerce")
    return result.dropna(subset=["数值"])


def apply_report_theme(chart: alt.Chart) -> alt.Chart:
    return (
        chart.properties(background=TRANSPARENT)
        .configure(
            font=ANNUAL_REPORT_FONT,
        )
        .configure_view(
            fill=TRANSPARENT,
            strokeOpacity=0,
        )
        .configure_axis(
            domain=True,
            domainColor="#D0D5DD",
            domainWidth=1,
            grid=False,
            labelFont=ANNUAL_REPORT_FONT,
            labelFontSize=10,
            labelColor="#0C233C",
            ticks=True,
            tickColor="#D0D5DD",
            titleFont=ANNUAL_REPORT_FONT,
            titleFontSize=11,
            titleColor="#0C233C",
        )
        .configure_axisY(
            domain=False,
            ticks=False,
        )
        .configure_axisX(
            domain=False,
            ticks=False,
            labels=True,
        )
        .configure_legend(
            labelFont=ANNUAL_REPORT_FONT,
            labelFontSize=10,
            labelColor="#0C233C",
            titleFont=ANNUAL_REPORT_FONT,
            titleFontSize=10,
            titleColor="#0C233C",
            symbolOpacity=1,
        )
        .configure_header(
            labelColor="#00338D",
            labelFont=ANNUAL_REPORT_FONT,
            labelFontSize=13,
            labelFontWeight="bold",
            labelPadding=10,
            titleFont=ANNUAL_REPORT_FONT,
            titleColor="#0C233C",
        )
        .configure_title(
            anchor="middle",
            color="#00338D",
            font=ANNUAL_REPORT_FONT,
            fontSize=13,
            fontWeight="bold",
            offset=12,
        )
    )


def metric_title(frame: pd.DataFrame, code: str) -> str:
    rows = frame[frame["指标编码"].astype(str).eq(code)]
    return code if rows.empty else str(rows.iloc[0].get("指标名称", code))


def _axis_title(frame: pd.DataFrame) -> str:
    units = [
        str(value).strip()
        for value in frame.get("单位", pd.Series(dtype=str)).dropna().unique()
        if str(value).strip()
    ]
    return "数值" if not units else f"数值（{'、'.join(units)}）"


def convert_multiple_units_to_percent(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert ratio multiples to their Step 7 display units without mutating source data."""
    result = frame.copy()
    if "数值" not in result or "单位" not in result:
        return result
    units = result["单位"].fillna("").astype(str).str.strip()
    multiple_mask = units.eq("倍")
    metric_codes = (
        result.get("指标编码", pd.Series("", index=result.index)).fillna("").astype(str).str.strip()
    )
    non_life_liability_mask = metric_codes.eq("NON_LIFE_INSURANCE_RISK_TO_LIABILITIES")
    permille_from_multiple = non_life_liability_mask & multiple_mask
    permille_from_percent = non_life_liability_mask & units.isin({"%", "％"})
    percent_mask = multiple_mask & ~non_life_liability_mask
    if not (percent_mask | permille_from_multiple | permille_from_percent).any():
        return result
    numeric = pd.to_numeric(result["数值"], errors="coerce")
    result["数值"] = numeric
    result.loc[percent_mask, "数值"] = numeric.loc[percent_mask] * 100.0
    result.loc[percent_mask, "单位"] = "%"
    result.loc[permille_from_multiple, "数值"] = numeric.loc[permille_from_multiple] * 1000.0
    result.loc[permille_from_percent, "数值"] = numeric.loc[permille_from_percent] * 10.0
    result.loc[permille_from_multiple | permille_from_percent, "单位"] = "‰"
    return result


def with_metric_value_labels(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """Add visible value labels, including a percent sign for percentage rows."""
    result = frame.copy()
    numeric = pd.to_numeric(result.get("数值", pd.Series(dtype=float)), errors="coerce")
    precision = (
        0
        if code == "SOLVENCY_RATIO_COMBO"
        else 5 if code in COMPACT_TREND_SCALE_CODES else 1 if code in TREND_AXIS_FORMATS else 2
    )
    units = result.get("单位", pd.Series("", index=result.index)).fillna("").astype(str).str.strip()
    result["数值标签"] = [
        (
            ""
            if pd.isna(value)
            else f"{float(value):,.{precision}f}{'%' if unit in {'%', '％'} else '‰' if unit == '‰' else ''}"
        )
        for value, unit in zip(numeric, units)
    ]
    return result


def company_scale(
    companies: Iterable[str],
    colors: Mapping[str, str],
) -> alt.Scale:
    domain = list(dict.fromkeys(str(company) for company in companies))
    palette = [
        colors.get(company, KPMG_CHART_COLORS[index % len(KPMG_CHART_COLORS)])
        for index, company in enumerate(domain)
    ]
    return alt.Scale(domain=domain, range=palette)


def facet_layout(company_count: int) -> tuple[int, int, int]:
    """Keep annual-report-style company facets on one fixed-height row.

    More companies only reduce panel width and horizontal spacing. The chart
    builders retain their normal panel height so dense print pages do not
    become vertically sparse.
    """
    if company_count <= 1:
        return 1, 760, 22
    spacing = (
        5 if company_count >= 10 else 8 if company_count >= 6 else 12 if company_count >= 4 else 22
    )
    available_width = 1120
    panel_width = max(
        52,
        (available_width - spacing * (company_count - 1)) // company_count,
    )
    return company_count, panel_width, spacing


def _panel_density_count(panel_count: int, dense_layout: bool = False) -> int:
    return max(int(panel_count or 1), 10 if dense_layout else 1)


def panel_typography(
    panel_count: int,
    dense_layout: bool = False,
) -> tuple[int, int, int]:
    """Scale period labels, values, and company headings for one-row panels."""
    count = _panel_density_count(panel_count, dense_layout)
    if count >= 12:
        return 6, 7, 8
    if count >= 8:
        return 7, 8, 10
    if count >= 5:
        return 8, 9, 11
    return 9, 10, 12


def responsive_bar_width(
    panel_count: int,
    dense_layout: bool = False,
) -> alt.RelativeBandSize:
    """Narrow bars gradually as more company panels share the same row."""
    count = _panel_density_count(panel_count, dense_layout)
    fraction = 0.56 if count >= 12 else 0.64 if count >= 8 else 0.68 if count >= 5 else 0.72
    return alt.RelativeBandSize(fraction)


def _responsive_grouped_bar_width(
    panel_count: int,
    dense_layout: bool = False,
) -> alt.RelativeBandSize:
    """Keep paired bars broad and nearly touching within each report period."""
    count = _panel_density_count(panel_count, dense_layout)
    fraction = 0.8 if count >= 12 else 0.86 if count >= 8 else 0.9 if count >= 5 else 0.94
    return alt.RelativeBandSize(fraction)


def compact_period_scale(
    panel_count: int = 1,
    dense_layout: bool = False,
) -> alt.Scale:
    """Keep report-period columns closer together inside every chart panel."""
    count = _panel_density_count(panel_count, dense_layout)
    padding = 0.58 if count >= 12 else 0.54 if count >= 8 else 0.5
    return alt.Scale(paddingInner=padding, paddingOuter=0.5)


def hidden_value_axis() -> alt.Axis:
    """Keep the shared y-scale geometry while hiding its numeric tick labels."""
    return alt.Axis(title=None, labels=False, ticks=False)


def _capital_stack_positions(
    rows: pd.DataFrame,
    value_column: str,
) -> pd.DataFrame:
    """Calculate deterministic stack bounds and label positions per bar."""
    result = rows.copy()
    result["_资本顺序"] = result["资本类别"].map(CAPITAL_COMPONENT_ORDER)
    result = result.sort_values(["公司", "报告期", "_资本顺序"])
    groups = result.groupby(["公司", "报告期"], sort=False)[value_column]
    result["堆叠终点"] = groups.cumsum()
    result["堆叠起点"] = result["堆叠终点"] - result[value_column]
    result["标签位置"] = (result["堆叠起点"] + result["堆叠终点"]) / 2
    return result


def _signed_stack_positions(
    rows: pd.DataFrame,
    value_column: str,
    order_column: str,
) -> pd.DataFrame:
    """Calculate stack bounds while keeping negative effects below zero."""
    result = rows.sort_values(["公司", "报告期", order_column]).copy()
    values = pd.to_numeric(result[value_column], errors="coerce")
    result["_正值"] = values.clip(lower=0)
    result["_负值"] = values.clip(upper=0)
    groups = result.groupby(["公司", "报告期"], sort=False)
    positive_end = groups["_正值"].cumsum()
    negative_end = groups["_负值"].cumsum()
    positive_start = positive_end - result["_正值"]
    negative_start = negative_end - result["_负值"]
    positive = values.ge(0)
    result["堆叠终点"] = positive_end.where(positive, negative_end)
    result["堆叠起点"] = positive_start.where(positive, negative_start)
    result["标签位置"] = (result["堆叠起点"] + result["堆叠终点"]) / 2
    totals = groups[value_column].transform(lambda series: series.abs().sum())
    result["构成占比"] = values.abs() / totals.where(totals.ne(0))
    result["占比标签"] = result["构成占比"].map(
        lambda value: (
            "" if pd.isna(value) or value < MIN_INSIDE_LABEL_SHARE else whole_percent_label(value)
        )
    )
    return result.drop(columns=["_正值", "_负值"])


def metric_rows(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
) -> tuple[pd.DataFrame, list[str], str]:
    metric = convert_multiple_units_to_percent(
        clean_numeric(frame[frame["指标编码"].astype(str).eq(code)])
    )
    periods = list(period_order)
    if metric.empty:
        raise ValueError(f"指标 {code} 没有可绘制数据。")
    metric = metric.drop_duplicates(
        subset=["公司", "报告期", "指标编码"],
        keep="last",
    ).copy()
    companies = list(dict.fromkeys(frame["公司"].dropna().astype(str)))
    grid = pd.MultiIndex.from_product(
        [companies, periods],
        names=["公司", "报告期"],
    ).to_frame(index=False)
    metric = grid.merge(metric, on=["公司", "报告期"], how="left", sort=False)
    metric["指标编码"] = metric["指标编码"].fillna(code)
    metric_name = metric_title(frame, code)
    metric["指标名称"] = metric["指标名称"].fillna(metric_name)
    if "单位" in metric.columns:
        disclosed_units = metric["单位"].dropna()
        default_unit = "" if disclosed_units.empty else disclosed_units.iloc[0]
        metric["单位"] = metric["单位"].fillna(default_unit)
    metric["披露状态"] = metric["数值"].notna().map({True: "已披露", False: "未披露"})
    metric = with_metric_value_labels(metric, code)
    return metric, periods, metric_title(metric, code)
