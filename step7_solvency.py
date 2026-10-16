from __future__ import annotations

import colorsys
import math                          # ★ 新增
from collections.abc import Callable, Iterable
from datetime import date
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import requests
import streamlit as st

from dashboard_components import (
    company_detail_rows,
    render_key_solvency_overview,
    render_major_financing,
    render_report_analysis,
    render_report_back_cover,
    render_report_cover,
    render_report_footnote,
    render_report_notes_editor,
)
from services.llm_config import model_request_parameters, normalize_model_id
from services.llm_http import post_json_with_retry
from types import SimpleNamespace

from services.solvency_navigation import (
    COMPANY_NAVIGATION,          # 保留：作为没有 Excel 时的兜底
    CREDIT_RISK_ASSET_SCATTER,
    INSURANCE_RISK_LIABILITY_SCATTER,
    KPMG_BRIGHT_CHART_COLORS,
    KPMG_CAPITAL_COMBO_COLORS,
    KPMG_CHART_COLORS,
    KPMG_QUANT_RISK_COLORS,
    MARKET_RISK_ASSET_SCATTER,
    PRINT_ALL_LABEL,
    metric_codes_for_chart,      # 保留：旧接口仍在使用
    resolve_chart_selection,     # 保留：兜底时使用
)
from services.step7_excel_config import Step7Module
from services.solvency_step6_analysis import (
    filter_analysis_frame,
    format_chart_value,
    nonblank_values,
    sort_report_periods,
)
from services.solvency_step7_chart_plans import (
    COMPANY_BAR_TREND,
    COMPANY_PERIOD_BAR,
    CAPITAL_AMOUNT_COMBO,
    CAPITAL_EFFICIENCY_BUBBLE,
    CAPITAL_RATIO_COMBO,
    CAPITAL_STRUCTURE_COMBO,
    CAPABILITY_RADAR,               # ★ 新增
    UNDERWRITING_EFFICIENCY_SCATTER, # ★ 新增：承保效益散点图
    STRUCTURE_CHANGE_CHART,          # ★ 新增：结构变化组合图（必须加这一行）
    COMPONENT_STACK,
    EFFECT_DIVERGING,
    FINANCING_TABLE,
    KEY_METRICS_TABLE,
    MARKET_CREDIT_MATRIX,
    PENDING_DEFINITION,
    RISK_RATIO_SCATTER,
    SINGLE_METRIC_TREND,
    SOLVENCY_MATRIX,
    SOLVENCY_RATIO_COMBO,
    TREND_WITH_COMPANY_BARS,
    chart_plan_for,
    PERFORMANCE_PATTERN_GRID,      # ★ 新增
)
from services.solvency_step7_charts import (
    CAPITAL_STRUCTURE_COLORS,
    build_capability_radar_chart,    # ★ 新增
    build_structure_change_chart,    # ★ 新增：结构变化组合图
    build_capital_amount_combo,
    build_capital_efficiency_bubble_chart,
    capital_efficiency_bubble_default_domain,
    combine_capital_efficiency_bubble_charts,
    combine_linked_matrix_charts,
    build_capital_ratio_combo_chart,
    capital_ratio_amount_axis_domain,
    build_company_bar_trend_chart,
    build_company_period_bar_chart,
    build_component_stack_chart,
    build_effect_diverging_chart,
    build_matrix_chart,
    build_single_metric_trend_charts,
    build_solvency_ratio_combo_chart,
    convert_multiple_units_to_percent,
    component_stack_axis_domain,
    component_stack_proportion_axis_domain,
    metric_bar_axis_domain,
    report_period_color_map,
    risk_ratio_scatter_default_domain,
    solvency_ratio_axis_domain,
    PERFORMANCE_PATTERN_ORDER,            # ★ 新增
    classify_performance_pattern,          # ★ 新增
    build_performance_pattern_trajectory,  # ★ 新增
)
from services.solvency_report_notes import company_notes_template


PICTURE_DIR = Path(__file__).resolve().parent / "picture"
ALL_COMPANY_TYPES = "全部"
DEFAULT_SORT_LABEL = "默认（按列表原始顺序）"
SORT_DESCENDING = "降序（从大到小）"
SORT_ASCENDING = "升序（从小到大）"
TRACKED_COMPANY_COLOR = "#00338D"
RISK_DIVERSIFICATION_EFFECT_COLOR = KPMG_BRIGHT_CHART_COLORS[6]
RISK_SCATTER_DISPLAY_WIDTH = 750
RISK_SCATTER_FULL_PANEL_WIDTH = 570
RISK_SCATTER_ZOOM_PANEL_WIDTH = 330
BUBBLE_FULL_PANEL_WIDTH = 570
BUBBLE_ZOOM_PANEL_WIDTH = 330
BUBBLE_DISTINCT_COLORS = (
    "#1FDB1F",
    "#DB651F",
    "#1FDBDB",
    "#DB1F89",
    "#84943E",
    "#A01FDB",
    "#3E7494",
    "#943E43",
    "#1FDB89",
    "#DBAC1F",
    "#DB1F42",
    "#A44DB8",
    "#29946C",
    "#259415",
    "#B8824D",
    "#5C84DB",
    "#1F42DB",
    "#9AB81A",
    "#DB1FC4",
    "#943E74",
    "#5CBCDB",
    "#944415",
    "#DB5C84",
    "#DB6C5C",
)

COMPONENT_STACK_SPECS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "计入各级资本的保单未来盈余构成占比": (
        ("POLICY_SURPLUS_CORE_T1", "计入核心一级资本", CAPITAL_STRUCTURE_COLORS["核心一级资本"]),
        ("POLICY_SURPLUS_CORE_T2", "计入核心二级资本", CAPITAL_STRUCTURE_COLORS["核心二级资本"]),
        ("POLICY_SURPLUS_ANC_T1", "计入附属一级资本", CAPITAL_STRUCTURE_COLORS["附属一级资本"]),
        ("POLICY_SURPLUS_ANC_T2", "计入附属二级资本", CAPITAL_STRUCTURE_COLORS["附属二级资本"]),
    ),
    "量化风险最低资本构成": (
        ("INSURANCE_RISK_CAPITAL", "保险风险（寿）", KPMG_QUANT_RISK_COLORS[0]),
        ("NON_LIFE_INSURANCE_RISK_CAPITAL", "保险风险（非寿）", KPMG_QUANT_RISK_COLORS[1]),
        ("MARKET_RISK_CAPITAL", "市场风险", KPMG_QUANT_RISK_COLORS[2]),
        ("CREDIT_RISK_CAPITAL", "信用风险", KPMG_QUANT_RISK_COLORS[3]),
        ("QUANT_RISK_DIVERSIFICATION_EFFECT", "风险分散效应", KPMG_QUANT_RISK_COLORS[4]),
        ("CONTRACT_LOSS_ABSORPTION_EFFECT", "损失吸收效应", KPMG_QUANT_RISK_COLORS[5]),
    ),
    "各类保险风险（寿）占比": (
        ("LOSS_OCCURRENCE_RISK_CAPITAL", "损失发生风险", KPMG_BRIGHT_CHART_COLORS[0]),
        ("SURRENDER_RISK_CAPITAL", "退保风险", KPMG_BRIGHT_CHART_COLORS[1]),
        ("EXPENSE_RISK_CAPITAL", "费用风险", KPMG_BRIGHT_CHART_COLORS[2]),
        ("LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT", "风险分散效应", KPMG_BRIGHT_CHART_COLORS[3]),
    ),
    "各类市场风险占比": (
        ("INTEREST_RATE_RISK_CAPITAL", "利率风险", KPMG_BRIGHT_CHART_COLORS[0]),
        ("EQUITY_RISK_CAPITAL", "权益价格风险", KPMG_BRIGHT_CHART_COLORS[1]),
        ("REAL_ESTATE_RISK_CAPITAL", "房地产价格风险", KPMG_BRIGHT_CHART_COLORS[2]),
        ("OVERSEAS_FIXED_INCOME_RISK_CAPITAL", "境外固定收益类资产价格风险", KPMG_BRIGHT_CHART_COLORS[3]),
        ("OVERSEAS_EQUITY_RISK_CAPITAL", "境外权益类资产价格风险", KPMG_BRIGHT_CHART_COLORS[4]),
        ("FOREIGN_EXCHANGE_RISK_CAPITAL", "汇率风险", KPMG_BRIGHT_CHART_COLORS[5]),
        ("MARKET_RISK_DIVERSIFICATION_EFFECT", "风险分散效应", RISK_DIVERSIFICATION_EFFECT_COLOR),
    ),
    "各类信用风险占比": (
        ("SPREAD_RISK_CAPITAL", "利差风险", KPMG_BRIGHT_CHART_COLORS[0]),
        ("COUNTERPARTY_RISK_CAPITAL", "交易对手违约风险", KPMG_BRIGHT_CHART_COLORS[1]),
        ("CREDIT_RISK_DIVERSIFICATION_EFFECT", "风险分散效应", RISK_DIVERSIFICATION_EFFECT_COLOR),
    ),
}

QUANT_RISK_STACK_RATIO_CODES = {
    "INSURANCE_RISK_CAPITAL": "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
    "NON_LIFE_INSURANCE_RISK_CAPITAL": "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
    "MARKET_RISK_CAPITAL": "MARKET_RISK_TO_QUANT_CAPITAL",
    "CREDIT_RISK_CAPITAL": "CREDIT_RISK_TO_QUANT_CAPITAL",
    "QUANT_RISK_DIVERSIFICATION_EFFECT": "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL",
    "CONTRACT_LOSS_ABSORPTION_EFFECT": "LOSS_ABSORPTION_TO_QUANT_CAPITAL",
}

COMPONENT_STACK_DENOMINATOR_CODES = {
    "量化风险最低资本构成": "QUANT_RISK_CAPITAL",
    "各类市场风险占比": "MARKET_RISK_CAPITAL",
    "各类信用风险占比": "CREDIT_RISK_CAPITAL",
}

RISK_RATIO_SCATTER_SPECS = {
    MARKET_RISK_ASSET_SCATTER: (
        "INTEREST_RATE_RISK_TO_ASSETS",
        "EQUITY_RISK_TO_ASSETS",
        "RECOGNIZED_ASSETS",
        "认可资产",
    ),
    CREDIT_RISK_ASSET_SCATTER: (
        "SPREAD_RISK_TO_ASSETS",
        "COUNTERPARTY_RISK_TO_ASSETS",
        "RECOGNIZED_ASSETS",
        "认可资产",
    ),
    INSURANCE_RISK_LIABILITY_SCATTER: (
        "LIFE_INSURANCE_RISK_TO_LIABILITIES",
        "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
        "RECOGNIZED_LIABILITIES",
        "认可负债",
    ),
}
RISK_SCATTER_DUAL_VIEW_CHARTS = frozenset({
    MARKET_RISK_ASSET_SCATTER,
    CREDIT_RISK_ASSET_SCATTER,
    INSURANCE_RISK_LIABILITY_SCATTER,
})


def _valid_state(key: str, options: Iterable[str], *, multiple: bool = False) -> None:
    values = list(options)
    if key not in st.session_state:
        return
    if multiple:
        st.session_state[key] = [value for value in st.session_state[key] if value in values]
    elif st.session_state[key] not in values:
        st.session_state.pop(key, None)


def _metric_title(frame: pd.DataFrame, code: str) -> str:
    rows = frame[frame["指标编码"].astype(str).eq(code)]
    return code if rows.empty else str(rows.iloc[0]["指标名称"])


def _metric_sort_options(frame: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    """Return annual-platform-style metric labels and their metric codes."""
    if frame.empty:
        return [DEFAULT_SORT_LABEL], {}
    metrics = frame[["指标编码", "指标名称"]].drop_duplicates()
    duplicated_names = metrics["指标名称"].astype(str).duplicated(keep=False)
    options = [DEFAULT_SORT_LABEL]
    lookup: dict[str, str] = {}
    for duplicated, row in zip(duplicated_names, metrics.itertuples(index=False)):
        name = str(row.指标名称).strip() or str(row.指标编码).strip()
        code = str(row.指标编码).strip()
        label = f"{name}（{code}）" if duplicated else name
        if label and label not in lookup:
            options.append(label)
            lookup[label] = code
    return [DEFAULT_SORT_LABEL, *sorted(options[1:])], lookup


def _sort_companies_by_metric(
    frame: pd.DataFrame,
    companies: Iterable[str],
    metric_code: str,
    latest_period: str,
    *,
    descending: bool,
) -> list[str]:
    """Sort companies by the selected metric while keeping missing values last."""
    original = list(dict.fromkeys(str(company) for company in companies if str(company).strip()))
    if not original or not metric_code:
        return original
    rows = frame[
        frame["公司"].astype(str).isin(original)
        & frame["指标编码"].astype(str).eq(str(metric_code))
        & frame["报告期"].astype(str).eq(str(latest_period))
    ].copy()
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    values = rows.dropna(subset=["数值"]).groupby("公司", sort=False)["数值"].first().to_dict()
    present = [company for company in original if company in values]
    missing = [company for company in original if company not in values]
    return [*sorted(present, key=lambda company: values[company], reverse=descending), *missing]


def _company_color_map(companies: Iterable[str], highlight_company: str = "无") -> dict[str, str]:
    """Match the annual platform: reserve KPMG Blue for the tracked company."""
    ordered = list(dict.fromkeys(str(company) for company in companies if str(company).strip()))
    highlight = str(highlight_company or "").strip()
    has_highlight = highlight in ordered
    palette = (
        [color for color in KPMG_CHART_COLORS if color.upper() != TRACKED_COMPANY_COLOR]
        if has_highlight
        else list(KPMG_CHART_COLORS)
    )
    if not palette:
        palette = list(KPMG_CHART_COLORS)
    colors = {
        company: palette[index % len(palette)]
        for index, company in enumerate(ordered)
    }
    if has_highlight:
        colors[highlight] = TRACKED_COMPANY_COLOR
    return colors


def _bubble_company_color_map(
    companies: Iterable[str],
    highlight_company: str = "无",
) -> dict[str, str]:
    """Give every bubble a distinct color without changing other chart palettes."""
    ordered = list(dict.fromkeys(str(company) for company in companies if str(company).strip()))
    highlight = str(highlight_company or "").strip()
    has_highlight = highlight in ordered
    palette = list(BUBBLE_DISTINCT_COLORS)
    if not has_highlight:
        palette.insert(0, TRACKED_COMPANY_COLOR)

    needed = len(ordered) - int(has_highlight)
    used = {TRACKED_COMPANY_COLOR, *(color.upper() for color in palette)}
    generated_index = 0
    while len(palette) < needed:
        hue = (0.137 + generated_index * 0.618033988749895) % 1.0
        saturation = (0.68, 0.82, 0.74)[generated_index % 3]
        value = (0.72, 0.84)[(generated_index // 3) % 2]
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
        color = f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"
        generated_index += 1
        if color.upper() not in used:
            palette.append(color)
            used.add(color.upper())

    palette_index = 0
    colors: dict[str, str] = {}
    for company in ordered:
        if has_highlight and company == highlight:
            colors[company] = TRACKED_COMPANY_COLOR
            continue
        colors[company] = palette[palette_index]
        palette_index += 1
    return colors


def _ordered_companies(frame: pd.DataFrame) -> list[str]:
    """Keep companies in the integrated table's original display order."""
    if frame.empty or "公司" not in frame.columns:
        return []
    return list(
        dict.fromkeys(
            value
            for value in frame["公司"].fillna("").astype(str).str.strip()
            if value
        )
    )


def _company_scope_for_types(
    frame: pd.DataFrame,
    selected_types: Iterable[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Return the exact company scope implied by the company-type widget."""
    selected = [
        str(value).strip()
        for value in selected_types
        if str(value).strip()
    ]
    specific_types = [
        value for value in selected if value != ALL_COMPANY_TYPES
    ]
    if not specific_types:
        company_source = frame
    else:
        company_source = frame[
            frame["同业分类"].fillna("").astype(str).str.strip().isin(specific_types)
        ].copy()
    return company_source, _ordered_companies(company_source)


def _sync_company_selection_state(
    selected_types: Iterable[str],
    available_companies: Iterable[str],
    *,
    signature_key: str = "_s7_company_scope_signature",
) -> str:
    """Return a fresh downstream widget key whenever its upstream scope changes."""
    companies = list(dict.fromkeys(
        str(company).strip()
        for company in available_companies
        if str(company).strip()
    ))
    selected = tuple(sorted({
        str(value).strip()
        for value in selected_types
        if str(value).strip() and str(value).strip() != ALL_COMPANY_TYPES
    }))
    signature = (selected or (ALL_COMPANY_TYPES,), tuple(companies))
    revision_key = "_s7_company_selection_revision"
    st.session_state.setdefault(revision_key, 0)
    if st.session_state.get(signature_key) != signature:
        if signature_key in st.session_state:
            st.session_state[revision_key] += 1
        st.session_state[signature_key] = signature
    selection_key = f"s7_companies_scope_v3_{st.session_state[revision_key]}"
    current = st.session_state.get(selection_key, companies)
    st.session_state[selection_key] = [
        company for company in current if company in companies
    ]
    return selection_key


def _reset_company_selection_for_types(frame: pd.DataFrame) -> None:
    """Apply a company-type change before Streamlit redraws downstream widgets."""
    selected_types = st.session_state.get("s7_company_types", [ALL_COMPANY_TYPES])
    _, companies = _company_scope_for_types(frame, selected_types)
    selected = tuple(sorted({
        str(value).strip()
        for value in selected_types
        if str(value).strip() and str(value).strip() != ALL_COMPANY_TYPES
    }))
    revision_key = "_s7_company_selection_revision"
    st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
    selection_key = f"s7_companies_scope_v3_{st.session_state[revision_key]}"
    st.session_state[selection_key] = list(companies)
    st.session_state["_s7_company_scope_signature"] = (
        selected or (ALL_COMPANY_TYPES,),
        tuple(companies),
    )


def _step7_modules_from_state() -> list[Step7Module]:
    """从 session_state 读取由 app.py 侧边栏解析好的 Excel 模块列表；
    如果没找到，就返回空列表（调用方回退到 COMPANY_NAVIGATION）。"""
    modules = st.session_state.get("step7_modules")
    if not modules:
        return []
    # 兼容：如果不是 Step7Module 实例（比如反序列化后丢了类型），尝试轻量修复
    fixed: list[Step7Module] = []
    for m in modules:
        if isinstance(m, Step7Module):
            fixed.append(m)
        elif isinstance(m, dict):
            fixed.append(Step7Module(**m))
    return fixed


def _selected_chart_names(frame: pd.DataFrame, modules: list[Step7Module] | None = None) -> list[str]:
    """优先用 Excel 模块列表决定要渲染哪些图表；
    没有 Excel 时回退到 COMPANY_NAVIGATION。"""
    # --- 有 Excel 配置：按 Excel 顺序 + 侧边栏选择 ---
    if modules:
        first = st.session_state.get("company_nav_level_one", "")
        if first == PRINT_ALL_LABEL:
            return [m.chart_name for m in modules]
        second = st.session_state.get("company_nav_level_two", "全部")
        active_chart = st.session_state.get("company_nav_chart", "")
        # 按 Excel 顺序筛选
        pool = [m for m in modules if m.level_one == first]
        if second and second != "全部":
            pool = [m for m in pool if m.level_two == second]
        # 若侧边栏选了某个具体图表，只渲染它；否则渲染该层级下全部
        if active_chart:
            matched = [m for m in pool if m.chart_name == active_chart]
            if matched:
                return [m.chart_name for m in matched]
        return [m.chart_name for m in pool]

    # --- 没 Excel：兜底用旧的 COMPANY_NAVIGATION ---
    first = st.session_state.get("company_nav_level_one", "")
    if first == PRINT_ALL_LABEL:
        return list(dict.fromkeys(entry.chart_name for entry in COMPANY_NAVIGATION))
    return resolve_chart_selection(
        first,
        st.session_state.get("company_nav_level_two", "全部"),
        st.session_state.get("company_nav_chart", ""),
    )


def _module_by_chart_name(modules: list[Step7Module], chart_name: str) -> Step7Module | None:
    for m in modules:
        if m.chart_name == chart_name:
            return m
    return None



def _report_style() -> None:
    st.markdown(
        """
        <style>
        [class*="st-key-s7_report_module_"],
        [class*="st-key-s7_trend_group_"],
        .solvency-report-title, .solvency-module-title,
        .solvency-report-analysis, .solvency-report-footnote,
        .solvency-chart-legend {
            font-family:Microsoft YaHei, 微软雅黑, sans-serif;
        }
        [class*="st-key-s7_report_module_"] {
            font-size:14px;
        }
        [class*="st-key-s7_report_module_"] p,
        [class*="st-key-s7_report_module_"] label,
        [class*="st-key-s7_report_module_"] button,
        [class*="st-key-s7_report_module_"] input {
            font-family:Microsoft YaHei, 微软雅黑, sans-serif!important;
            font-size:14px;
        }
        [data-testid="stMainBlockContainer"] {
            max-width:none!important; padding-left:10px!important; padding-right:10px!important;
        }
        .solvency-report-title {
            color:#00338D; font-size:30px; font-weight:900;
            border-bottom:2px solid #00338D; padding-bottom:8px; margin:6px 0 18px;
        }
        .solvency-module-title {
            color:#1E293B; font-size:17px; font-weight:600; letter-spacing:1px;
            background:rgba(255,255,255,.45); border:1px solid rgba(255,255,255,.8);
            border-radius:8px; padding:8px 18px; width:fit-content;
            box-shadow:0 4px 12px rgba(0,0,0,.03), inset 0 1px 0 rgba(255,255,255,.5);
            margin:10px 0 18px;
        }
        [class*="st-key-s7_report_module_"] h3 {
            color:#1E293B!important; font-size:17px!important; font-weight:600!important;
            letter-spacing:1px; background:rgba(255,255,255,.45)!important;
            border:1px solid rgba(255,255,255,.8); border-radius:8px!important;
            padding:8px 18px!important; width:fit-content; margin:10px 0 18px!important;
            box-shadow:0 4px 12px rgba(0,0,0,.03), inset 0 1px 0 rgba(255,255,255,.5)!important;
        }
        .solvency-analysis-note {
            background:#F2F6FC; border-left:4px solid #00338D;
            border-radius:4px; padding:9px 13px; margin:6px 0 0; color:#243B53;
        }
        .solvency-analysis-spacer {height:16px; width:100%;}
        .solvency-chart-legend {
            display:flex; flex-wrap:wrap; justify-content:flex-end;
            align-items:center; gap:10px 18px;
            margin:2px 10px 8px 0; color:#0C233C; font-size:10px;
        }
        .solvency-chart-legend__item {display:inline-flex; align-items:center; gap:6px;}
        .solvency-chart-legend__symbol {
            --legend-color:#0C233C; display:inline-block; flex:0 0 auto;
        }
        .solvency-chart-legend__symbol--square {
            width:11px; height:11px; border-radius:2px; background:var(--legend-color);
        }
        .solvency-chart-legend__symbol--outline {
            width:13px; height:11px; border-radius:3px;
            border:2px solid var(--legend-color); background:transparent;
            box-sizing:border-box;
        }
        .solvency-chart-legend__symbol--line,
        .solvency-chart-legend__symbol--dash {
            width:22px; height:0; border-top:3px solid var(--legend-color);
        }
        .solvency-chart-legend__symbol--dash {border-top-style:dashed;}
        .solvency-analysis-ai {color:#D84315; font-size:12px; margin-top:5px;}
        .solvency-analysis-ai--error {color:#C00000;}
        .solvency-report-analysis {
            background:#F4F7FC; border-left:4px solid #00338D;
            border-radius:3px; padding:7px 10px; margin:4px 0 10px;
        }
        .solvency-report-analysis p {margin:2px 0; line-height:1.45;}
        .solvency-note-default {color:#0A1F5C; font-size:14px;}
        .solvency-note-custom {color:#1E49E2; font-size:14px; font-weight:600;}
        .solvency-report-footnote {color:#777; font-size:12px; font-style:italic; margin:4px 0 16px;}
        [class*="st-key-annual_company_panel_"] {
            border:1px solid #EAEAEA!important; border-radius:0!important;
            padding:14px 4px 2px!important; box-shadow:none!important;
            box-sizing:border-box!important;
        }
        .annual-company-title {
            color:#00338D; font-size:13px; font-weight:700; line-height:1.25;
            text-align:center; white-space:nowrap; min-height:17px;
            margin:0 0 30px; padding:0 4px;
        }
        [class*="st-key-annual_company_panel_"] [data-testid="stMarkdownContainer"]:has(.annual-company-title) {
            margin:0!important; padding:0!important;
        }
        [class*="st-key-annual_company_panel_gray_"] {
            background:rgba(200,200,200,.12)!important;
        }
        [class*="st-key-annual_company_panel_white_"] {
            background:rgba(255,255,255,0)!important;
        }
        [class*="st-key-annual_company_panel_tracked_"] {
            background:rgba(0,51,141,.03)!important;
            border:1.5px solid rgba(0,51,141,.35)!important;
        }
        [class*="st-key-s7_report_module_"] [data-testid="stTable"] table,
        [class*="st-key-s7_report_module_"] [data-testid="stDataFrame"] {
            font-family:Microsoft YaHei, 微软雅黑, sans-serif!important;
            font-size:11px!important;
        }
        [class*="st-key-s7_report_module_"] [data-testid="stTable"] thead th {
            background:#00338D; color:#FFFFFF; font-size:11px;
            font-weight:700; padding:6px 4px; border:1px solid #FFFFFF;
        }
        [class*="st-key-s7_report_module_"] [data-testid="stTable"] tbody td {
            font-size:11px; padding:4px; border:1px solid #EAEAEA;
        }
        [class*="st-key-s7_report_module_"] {break-inside:avoid-page; page-break-inside:avoid;}
        [class*="st-key-s7_trend_group_"] {break-inside:avoid-page; page-break-inside:avoid;}
        .solvency-print-cover {
            position:relative; width:100%; aspect-ratio:16/9; overflow:hidden;
            margin:0; padding:0; background:#00338D;
            -webkit-print-color-adjust:exact; print-color-adjust:exact;
        }
        .solvency-print-cover img, .solvency-cover-fallback {
            position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
        }
        .solvency-cover-fallback {background:linear-gradient(135deg,#00338D,#1E49E2);}
        .solvency-print-cover__text {
            position:absolute; inset:0; z-index:2; display:flex; flex-direction:column;
            justify-content:center; align-items:flex-start; padding:0 8%; box-sizing:border-box;
            color:#fff; text-shadow:2px 2px 5px rgba(0,0,0,.45);
        }
        .solvency-print-cover__title {font-size:48px; font-weight:900; line-height:1.35;}
        .solvency-print-cover__subtitle {font-size:23px; font-weight:650; margin-top:14px;}
        .solvency-print-cover__date {font-size:19px; margin-top:18px;}
        .solvency-print-cover--front {page-break-after:always; break-after:page;}
        .solvency-print-cover--back {page-break-before:always; break-before:page;}
        @media print {
            [data-testid="stHeader"], [data-testid="stSidebar"],
            [data-testid="stToolbar"], [data-testid="collapsedControl"],
            [data-testid="stExpander"], div[role="tablist"], h1,
            .platform-page-heading, .solvency-report-title,
            .solvency-no-print,
            [class*="st-key-capital_bubble_print_controls_"],
            [class*="st-key-risk_scatter_print_controls_"] {display:none!important;}
            .capital_efficiency_zoom_brush,
            .capital_efficiency_zoom_brush_bg,
            [class*="capital_efficiency_zoom_"][class*="_brush"],
            .risk_ratio_zoom_brush,
            .risk_ratio_zoom_brush_bg,
            [class*="risk_ratio_zoom_"][class*="_brush"] {
                display:none!important;
            }
            [data-testid="stElementContainer"]:has(.platform-page-heading),
            [data-testid="stElementContainer"]:has(.solvency-report-title) {display:none!important;}
            [class*="st-key-s7_report_module_"] {
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-s7_report_module_"]:has(.solvency-grouped-trend) {
                break-inside:auto!important; page-break-inside:auto!important;
            }
            /* A dual-view metric deliberately spans two sheets: the trend stays
               on the first sheet and the complete company-bar group starts the next. */
            [class*="st-key-s7_report_module_"]:has([class*="st-key-s7_company_bars_page_"]) {
                break-inside:auto!important; page-break-inside:auto!important;
            }
            [class*="st-key-s7_company_bars_page_"] {
                break-before:page!important; page-break-before:always!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-s7_trend_group_"] {
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-s7_report_module_"] [data-testid="stVegaLiteChart"] {
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                max-width:100%!important;
            }
            /* Keep every company card intact.  The print-only grid packs cards
               tightly enough to avoid an otherwise nearly empty spill page. */
            [class*="st-key-annual_company_grid_"],
            [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] {
                display:grid!important;
                grid-template-columns:repeat(auto-fit,minmax(155px,1fr))!important;
                align-items:stretch!important; gap:8px!important;
                width:100%!important; min-width:0!important; max-width:100%!important;
                break-inside:auto!important; page-break-inside:auto!important;
            }
            [class*="st-key-annual_company_grid_"] > [data-testid="stLayoutWrapper"],
            [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] > [data-testid="stLayoutWrapper"],
            [class*="st-key-annual_company_grid_"] [data-testid="stElementContainer"]:has([class*="st-key-annual_company_panel_"]),
            [class*="st-key-annual_company_grid_"] [data-testid="stVerticalBlockBorderWrapper"]:has([class*="st-key-annual_company_panel_"]) {
                width:auto!important; min-width:0!important; max-width:100%!important;
                box-sizing:border-box!important;
            }
            /* st.columns keeps a flex-basis width after its parent becomes a
               print grid.  Fill the grid track instead of shrinking to it. */
            [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                justify-self:stretch!important; box-sizing:border-box!important;
            }
            [class*="st-key-annual_company_panel_"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                align-self:stretch!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-annual_company_panel_"] [data-testid="stVegaLiteChart"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                min-height:280px!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            [class*="st-key-annual_company_panel_"] [data-testid="stVegaLiteChart"] canvas,
            [class*="st-key-annual_company_panel_"] [data-testid="stVegaLiteChart"] svg {
                width:100%!important; min-width:0!important; max-width:100%!important;
                height:280px!important; min-height:280px!important; max-height:280px!important;
            }
            /* Follow the annual-report platform's centered full-sheet canvas.
               Covers use the whole 16:9 page; body modules provide their own
               symmetric 12 mm side margins and 8 mm top margin. */
            html.solvency-print-mode-widescreen {
                --solvency-print-page-width:338.67mm;
                --solvency-print-page-height:190.5mm;
                --solvency-print-content-width:314.67mm;
                --solvency-print-content-height:174.5mm;
            }
            html.solvency-print-mode-widescreen,
            html.solvency-print-mode-widescreen body,
            html.solvency-print-mode-widescreen [data-testid="stAppViewContainer"],
            html.solvency-print-mode-widescreen [data-testid="stMain"] {
                width:var(--solvency-print-page-width)!important;
                max-width:var(--solvency-print-page-width)!important;
                margin:0 auto!important; padding:0!important;
                overflow:visible!important; box-sizing:border-box!important;
                -webkit-print-color-adjust:exact!important;
                print-color-adjust:exact!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stMainBlockContainer"] {
                width:var(--solvency-print-page-width)!important;
                min-width:var(--solvency-print-page-width)!important;
                max-width:var(--solvency-print-page-width)!important;
                margin:0 auto!important; padding:0!important;
                box-sizing:border-box!important; overflow:visible!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_"],
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                margin-left:0!important; margin-right:0!important;
                grid-template-columns:none!important;
                grid-auto-flow:column!important;
                grid-auto-columns:minmax(0,1fr)!important;
                gap:8px!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_panel_"] {
                height:100%!important; padding:12px 3px 2px!important;
            }
            html.solvency-print-mode-widescreen .annual-company-title {
                margin:0 0 12px!important;
            }
            html.solvency-print-mode-widescreen .key-solvency-overview {
                width:100%!important; min-width:0!important; max-width:100%!important;
                margin-left:0!important; margin-right:0!important;
                table-layout:fixed!important;
            }
            html.solvency-print-mode-widescreen .key-solvency-overview th,
            html.solvency-print-mode-widescreen .key-solvency-overview td {
                min-width:0!important; max-width:none!important;
                white-space:normal!important; overflow-wrap:anywhere!important;
                word-break:break-word!important; box-sizing:border-box!important;
                font-size:9px!important; padding:3px 2px!important;
            }
            html.solvency-print-mode-widescreen .key-solvency-overview th:first-child,
            html.solvency-print-mode-widescreen .key-solvency-overview td:first-child {
                width:6.5%!important; white-space:nowrap!important;
                word-break:keep-all!important; overflow-wrap:normal!important;
            }
            .solvency-report-page-break {break-before:page; page-break-before:always;}
            html.solvency-print-mode-widescreen .solvency-print-cover {
                width:338.67mm!important; height:190.5mm!important; max-width:338.67mm!important;
                aspect-ratio:auto!important; margin:0 auto!important; padding:0!important;
                box-sizing:border-box!important; overflow:hidden!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            html.solvency-print-mode-widescreen .solvency-print-cover img {
                width:100%!important; height:100%!important; object-fit:contain!important;
                object-position:center!important; display:block!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover),
            html.solvency-print-mode-widescreen [data-testid="stMarkdownContainer"]:has(.solvency-print-cover),
            html.solvency-print-mode-widescreen .stMarkdown:has(.solvency-print-cover) {
                width:338.67mm!important; max-width:338.67mm!important;
                height:190.5mm!important; margin:0 auto!important; padding:0!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stVerticalBlock"]:has(.solvency-print-cover) {
                gap:0!important; margin:0!important; padding:0!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] {
                width:var(--solvency-print-content-width)!important;
                min-width:var(--solvency-print-content-width)!important;
                max-width:var(--solvency-print-content-width)!important;
                min-height:0!important; padding:8mm 0 0!important;
                margin:0 auto!important; box-sizing:border-box!important;
                overflow:visible!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] > [data-testid="stVerticalBlock"],
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] [data-testid="stElementContainer"],
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] [data-testid="stVegaLiteChart"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                box-sizing:border-box!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] > [data-testid="stVerticalBlock"] {
                gap:0.45rem!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] img {
                max-width:100%!important; height:auto!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] table {
                width:100%!important; max-width:100%!important;
                table-layout:fixed!important; box-sizing:border-box!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover--front) {
                break-after:page!important; page-break-after:always!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover--back) {
                break-before:page!important; page-break-before:always!important;
            }
            .solvency-module-title {
                color:#00338D!important; font-size:30px!important; font-weight:900!important;
                background:transparent!important; border:none!important; border-radius:0!important;
                box-shadow:none!important; padding:0!important; margin:10px 0 8px!important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_image_override_controls(
    chart_names: list[str],
    notes: dict[str, dict[str, str]],
) -> dict[str, dict[str, object]]:
    overrides = st.session_state.setdefault("s7_image_overrides", {})
    st.caption("手动上传图片（PNG/JPG）；选择对应图表后，图片将替代系统生成图表进入网页和打印报告。")
    uploads = st.file_uploader(
        "上传图表截图",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="s7_image_uploads",
    )
    filename_targets = {
        str(note.get("图片文件名", "")).strip(): chart_name
        for chart_name, note in notes.items()
        if str(note.get("图片文件名", "")).strip()
    }
    for index, upload in enumerate(uploads or []):
        state_key = f"s7_image_target_{index}"
        if state_key not in st.session_state:
            suggested_target = filename_targets.get(upload.name, "不覆盖/跳过")
            st.session_state[state_key] = (
                suggested_target if suggested_target in chart_names else "不覆盖/跳过"
            )
        c1, c2 = st.columns([1, 1.4], vertical_alignment="center")
        with c1:
            target = st.selectbox(
                f"{upload.name} 对应图表",
                ["不覆盖/跳过", *chart_names],
                key=state_key,
            )
        with c2:
            st.image(upload, width="stretch")
        if target != "不覆盖/跳过":
            overrides[target] = {
                "name": upload.name,
                "data": upload.getvalue(),
            }
    if overrides:
        st.caption("当前已覆盖：" + "、".join(overrides))
        disabled = st.multiselect(
            "本次暂不使用的图片覆盖",
            list(overrides),
            key="s7_disabled_image_overrides",
            placeholder="如需恢复系统图表，可在此选择",
        )
        return {name: value for name, value in overrides.items() if name not in disabled}
    return {}


def _convert_unit(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    result = frame.copy()
    target_yuan_map = {
        "十亿元": 1_000_000_000,
        "亿元": 100_000_000,
        "百万元": 1_000_000,
        "十万元": 100_000,
    }
    if target not in target_yuan_map:
        return result
    target_yuan = target_yuan_map[target]
    source_yuan = {
        "元": 1,
        "万元": 10_000,
        "十万元": 100_000,
        "百万元": 1_000_000,
        "亿元": 100_000_000,
        "十亿元": 1_000_000_000,
    }
    numeric_values = pd.to_numeric(result["数值"], errors="coerce")
    clean_units = result["单位"].fillna("").astype(str).str.strip()
    source_scales = clean_units.map(source_yuan)
    amount_mask = source_scales.notna()
    result["数值"] = numeric_values
    result.loc[amount_mask, "数值"] = (
        numeric_values.loc[amount_mask]
        * source_scales.loc[amount_mask]
        / target_yuan
    )
    result.loc[amount_mask, "单位"] = target
    return result

def _analysis_completion_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("模型接口地址不能为空。")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


@st.cache_data(show_spinner=False, ttl="12h", max_entries=256)
def _call_ai_analysis_cached(
    data_text: str,
    metric_name: str,
    latest_period: str,
    api_key: str,
    base_url: str,
    model: str,
) -> str:
    prompt = (
        "你是资深四大保险精算顾问。请根据以下偿付能力同业对标数据，"
        "用一句中文给出专业点评，指出最高、最低、均值及值得关注的差异；"
        "不要编造数据，不超过80字。\n"
        f"报告期：{latest_period}\n指标：{metric_name}\n数据：{data_text}"
    )
    payload = {
        "model": normalize_model_id(base_url, model),
        "messages": [{"role": "user", "content": prompt}],
    }
    payload.update(model_request_parameters(base_url, model))
    response = post_json_with_retry(
        requests.post,
        _analysis_completion_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except Exception:
            detail = response.text
        raise RuntimeError(detail or f"HTTP {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("模型接口返回结构中没有 choices/message/content。") from exc
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content).strip()


def _ai_analysis_for_latest_period(
    latest: pd.DataFrame,
    metric_name: str,
    latest_period: str,
) -> tuple[str, bool]:
    settings = {
        "api_key": str(st.session_state.get("llm_api_key", "")).strip(),
        "base_url": str(st.session_state.get("llm_base_url", "")).strip(),
        "model": str(st.session_state.get("llm_model", "")).strip(),
    }
    if not all(settings.values()):
        return (
            "当前登录会话的 AI 配置不完整。请退出登录，在登录页填写 "
            "Base URL、模型名称和 API Key 后重新进入。"
        ), True
    company_values = "、".join(
        f"{row['公司']}={format_chart_value(row['数值'], row.get('单位', ''), row.get('数据类型', ''))}"
        for _, row in latest.sort_values("数值", ascending=False).iterrows()
    )
    sample = latest.iloc[0]
    average_text = format_chart_value(
        latest["数值"].mean(),
        sample.get("单位", ""),
        sample.get("数据类型", ""),
    )
    data_text = f"{company_values}；样本均值={average_text}"
    try:
        return _call_ai_analysis_cached(
            data_text,
            metric_name,
            latest_period,
            settings["api_key"],
            settings["base_url"],
            settings["model"],
        ), False
    except Exception as exc:
        return f"AI 分析失败：{exc}", True


def _render_report_metric(
    frame: pd.DataFrame,
    code: str,
    *,
    periods: list[str],
    show_labels: bool,
    decimals: int,
    unit_mode: str,
    transparent: bool,
    show_average: bool,
    highlight_company: str,
    enable_ai: bool,
    key_prefix: str,
    company_panels: bool = False,
    company_period_bars: bool = False,
) -> None:
    metric_frame = filter_analysis_frame(frame, metric_code=code, periods=periods)
    if metric_frame.empty:
        st.caption(f"{_metric_title(frame, code)}：当前筛选范围无数据。")
        return
    metric_frame = _convert_unit(metric_frame, unit_mode)
    metric_frame = convert_multiple_units_to_percent(metric_frame)
    metric_name = str(metric_frame.iloc[0]["指标名称"])
    st.caption(
        f"公司 {metric_frame['公司'].nunique()} 家　｜　"
        f"报告期 {metric_frame['报告期'].nunique()} 个　｜　"
        f"记录 {len(metric_frame):,} 条"
    )
    ordered_periods = sort_report_periods(metric_frame["报告期"])
    latest_period = ordered_periods[-1]
    latest = metric_frame[metric_frame["报告期"].astype(str).eq(latest_period)].copy()
    latest["数值"] = pd.to_numeric(latest["数值"], errors="coerce")
    latest = latest.dropna(subset=["数值"]).drop_duplicates(subset=["公司"], keep="last")
    if not latest.empty:
        max_row = latest.loc[latest["数值"].idxmax()]
        min_row = latest.loc[latest["数值"].idxmin()]
        ai_html = ""
        if enable_ai:
            ai_text, ai_failed = _ai_analysis_for_latest_period(latest, metric_name, latest_period)
            ai_class = "solvency-analysis-ai solvency-analysis-ai--error" if ai_failed else "solvency-analysis-ai"
            ai_html = f"<div class='{ai_class}'><b>AI 分析：</b>{escape(ai_text)}</div>"
        st.markdown(
            "<div class='solvency-analysis-note'>"
            f"最新报告期 {latest_period}：最高为 {max_row['公司']} "
            f"（{format_chart_value(max_row['数值'], max_row.get('单位', ''), max_row.get('数据类型', ''), decimals)}），"
            f"最低为 {min_row['公司']} "
            f"（{format_chart_value(min_row['数值'], min_row.get('单位', ''), min_row.get('数据类型', ''), decimals)}）。"
            f"{ai_html}"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div class='solvency-analysis-spacer' aria-hidden='true'></div>",
            unsafe_allow_html=True,
        )
    if company_panels:
        expected_count = metric_frame["公司"].nunique() * len(ordered_periods)
        disclosed_count = metric_frame.drop_duplicates(["公司", "报告期"]).shape[0]
        period_colors = report_period_color_map(ordered_periods)
        legend_items = [
            (period_colors[period], period, "square")
            for period in ordered_periods
        ]
        legend_items.append(("#0C233C", "趋势折线", "line"))
        if highlight_company in metric_frame["公司"].astype(str).unique():
            legend_items.append(("#B8BDC7", "特定追踪公司", "outline"))
        if disclosed_count < expected_count:
            legend_items.append(("#B8BDC7", "未披露", "square"))
        if code in {"CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO"}:
            legend_items.append(("#ED2124", "监管下限", "dash"))
        _render_chart_legend(legend_items)
        chart = build_company_bar_trend_chart(
            metric_frame,
            code,
            ordered_periods,
            highlight_company,
        )
        st.altair_chart(
            _chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_{code}_company_panels",
        )
    else:
        expected_count = metric_frame["公司"].nunique() * len(ordered_periods)
        disclosed_count = metric_frame.drop_duplicates(["公司", "报告期"]).shape[0]
        external_legend = []
        if disclosed_count < expected_count:
            external_legend.append(("#B8BDC7", "未披露", "square"))
        if code in {"CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO"}:
            external_legend.append(("#ED2124", "监管下限", "dash"))
        if external_legend:
            _render_chart_legend(external_legend)
        charts = build_single_metric_trend_charts(
            metric_frame,
            code,
            ordered_periods,
            _company_color_map(metric_frame["公司"], highlight_company),
            highlight_company,
        )
        if len(charts) > 1:
            st.markdown(
                "<div class='solvency-grouped-trend' aria-hidden='true'></div>",
                unsafe_allow_html=True,
            )
        for group_index, chart in enumerate(charts, start=1):
            with st.container(key=f"s7_trend_group_{key_prefix}_{code}_{group_index}"):
                st.altair_chart(
                    _chart_without_internal_title(chart),
                    width="stretch",
                    key=f"{key_prefix}_{code}_trend_{group_index}",
                )
    if company_period_bars:
        with st.container(key=f"s7_company_bars_page_{key_prefix}_{code}"):
            company_panel_count = metric_frame["公司"].nunique()
            shared_y_domain = metric_bar_axis_domain(metric_frame, code, ordered_periods)
            period_colors = report_period_color_map(ordered_periods)
            st.caption(
                "以下按公司展示各报告期柱状小图；所有公司统一使用同一纵轴范围，"
                "不同报告期使用不同颜色。"
            )
            _render_chart_legend([
                (period_colors[period], period, "square")
                for period in ordered_periods
            ])
            _render_company_chart_grid(
                metric_frame,
                lambda company_frame: build_company_period_bar_chart(
                    company_frame,
                    code,
                    ordered_periods,
                    shared_y_domain=shared_y_domain,
                    dense_layout=company_panel_count >= 10,
                    panel_count=company_panel_count,
                ),
                key_prefix=f"{key_prefix}_{code}_period_bars",
                highlight_company=highlight_company,
            )


def _render_chart_legend(items: Iterable[tuple[str, str, str]]) -> None:
    legend_html = "".join(
        (
            f"<span class='solvency-chart-legend__item'>"
            f"<i class='solvency-chart-legend__symbol solvency-chart-legend__symbol--{shape}' "
            f"style='--legend-color:{color}'></i>{escape(label)}</span>"
        )
        for color, label, shape in items
    )
    st.markdown(
        f"<div class='solvency-chart-legend'>{legend_html}</div>",
        unsafe_allow_html=True,
    )


def _render_company_chart_grid(
    frame: pd.DataFrame,
    chart_factory: Callable[[pd.DataFrame], object],
    *,
    key_prefix: str,
    highlight_company: str = "无",
    panel_width: int | None = None,
) -> None:
    """Render equal company panels in one page-width row without scrolling."""
    companies = _ordered_companies(frame)
    if not companies:
        st.warning("当前筛选范围没有可绘制的公司数据。")
        return
    company_count = len(companies)
    title_font_size = 9 if company_count >= 12 else 10 if company_count >= 8 else 11 if company_count >= 5 else 13
    column_gap = None if company_count >= 8 else "small"
    with st.container(key=f"annual_company_grid_{key_prefix}"):
        panel_columns = st.columns(
            company_count,
            gap=column_gap,
            vertical_alignment="top",
        )
        tracked_company = str(highlight_company or "").strip()
        for index, (column, company) in enumerate(zip(panel_columns, companies)):
            company_frame = frame[
                frame["公司"].fillna("").astype(str).eq(company)
            ].copy()
            tone = (
                "tracked"
                if tracked_company and tracked_company != "无" and company == tracked_company
                else "gray" if index % 2 else "white"
            )
            with column:
                with st.container(
                    border=True,
                    width="stretch",
                    gap=None,
                    key=f"annual_company_panel_{tone}_{key_prefix}_{index}",
                ):
                    st.markdown(
                        "<div class='annual-company-title' "
                        f"style='font-size:{title_font_size}px'>{escape(company)}</div>",
                        unsafe_allow_html=True,
                    )
                    # ★ 把 try/except 移进容器内：图表失败时，公司标题列依然保留
                    try:
                        chart = chart_factory(company_frame)
                    except (ValueError, KeyError) as exc:
                        st.warning(f"该公司的图表暂不可用：{exc}")
                        continue
                    st.altair_chart(
                        _chart_without_internal_title(chart),
                        width="stretch",
                        key=f"{key_prefix}_{index}",
                    )

def _chart_without_internal_title(chart: object) -> object:
    """Keep company titles outside Vega so Streamlit's chart toolbar cannot cover them."""
    copied = chart.copy(deep=True)
    copied.title = alt.Undefined
    return copied


@st.fragment
def _render_capital_efficiency_bubble_fragment(
    converted: pd.DataFrame,
    periods: list[str],
    company_colors: dict[str, str],
    highlight_company: str,
    key_prefix: str,
) -> None:
    """Keep magnifier interaction entirely inside Vega without Python reruns."""
    bubble_panel_height = 430
    state_prefix = "capital_efficiency_bubble_zoom"
    signature_key = f"{state_prefix}_signature"
    revision_key = f"{state_prefix}_revision"
    available_companies = tuple(
        dict.fromkeys(converted["公司"].dropna().astype(str))
    )
    requested_highlight = str(highlight_company or "").strip()
    tracked_company = (
        requested_highlight
        if requested_highlight in available_companies
        else ""
    )
    company_colors = _bubble_company_color_map(
        available_companies,
        tracked_company,
    )
    try:
        default_domain, latest_period = capital_efficiency_bubble_default_domain(
            converted,
            periods,
            company_colors,
            tracked_company,
        )
    except ValueError as exc:
        st.warning(str(exc))
        return
    sample_signature = (
        latest_period,
        available_companies,
        tracked_company,
    )
    if (
        st.session_state.get(signature_key) != sample_signature
        or revision_key not in st.session_state
    ):
        st.session_state[signature_key] = sample_signature
        st.session_state[revision_key] = (
            int(st.session_state.get(revision_key, 0)) + 1
        )
    st.session_state.setdefault(revision_key, 0)
    # Keep the Vega selection entirely browser-side.  A revision-specific name
    # resets the brush without registering the chart as a Streamlit input.
    selection_name = (
        f"capital_efficiency_zoom_{st.session_state[revision_key]}"
    )
    try:
        full_chart, latest_period = build_capital_efficiency_bubble_chart(
            converted,
            periods,
            company_colors,
            tracked_company,
            panel_label="全样本图",
            chart_width=BUBBLE_FULL_PANEL_WIDTH,
            chart_height=bubble_panel_height,
            show_company_legend=True,
            apply_theme=False,
            linked_selection_name=selection_name,
        )
    except ValueError as exc:
        st.warning(str(exc))
        return

    with st.container(
        horizontal=True,
        vertical_alignment="center",
        key=f"capital_bubble_print_controls_{key_prefix}",
    ):
        if st.button(
            "定位追踪公司" if tracked_company else "恢复默认范围",
            icon=":material/my_location:",
            key=f"{state_prefix}_reset",
        ):
            st.session_state[revision_key] += 1
            st.rerun(scope="fragment")
        st.caption(
            "拖动主图灰色选区可移动观察范围，滚轮可缩放；"
            "当前局部视图会直接用于打印，打印时自动隐藏选区框。"
        )

    overlap_chart = None
    overlap_error = ""
    try:
        overlap_chart, _ = build_capital_efficiency_bubble_chart(
            converted,
            periods,
            company_colors,
            tracked_company,
            zoom_to_overlap_region=True,
            panel_label="局部样本放大",
            chart_width=BUBBLE_ZOOM_PANEL_WIDTH,
            chart_height=bubble_panel_height,
            show_company_legend=True,
            show_axis_titles=False,
            apply_theme=False,
            linked_selection_name=selection_name,
        )
        overlap_chart.to_dict(validate=True)
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        overlap_error = str(exc)
    st.caption(
        f"展示 {latest_period}：横轴为实际资本/注册资本，纵轴为核心资本/注册资本，"
        "气泡面积代表认可资产规模；浅灰虚线为全样本横纵指标中位数。"
    )
    if overlap_chart is not None:
        bubble_pair = combine_capital_efficiency_bubble_charts(
            full_chart,
            overlap_chart,
            selection_name=selection_name,
            selection_domain=default_domain,
        )
        st.altair_chart(
            bubble_pair,
            width="content",
        )
    else:
        st.altair_chart(
            full_chart,
            width="content",
            key=f"{key_prefix}_capital_efficiency_bubble_full_v8",
        )
    if overlap_error:
        st.caption(f"局部放大图暂不可用：{overlap_error}")
    st.caption(
        "局部图横纵轴与主图选区实时联动；设置追踪公司后，默认选区会围绕该公司及其邻近公司。"
    )


@st.fragment
def _render_risk_ratio_scatter_fragment(
    converted: pd.DataFrame,
    periods: list[str],
    company_colors: dict[str, str],
    highlight_company: str,
    key_prefix: str,
    chart_name: str,
    x_code: str,
    y_code: str,
    bubble_size_code: str,
    denominator_label: str,
) -> None:
    """Render linked full/local risk bubbles without Python selection reruns."""
    panel_height = 430
    state_prefix = f"risk_ratio_scatter_zoom_{x_code.lower()}_{y_code.lower()}"
    signature_key = f"{state_prefix}_signature"
    revision_key = f"{state_prefix}_revision"
    available_companies = tuple(
        dict.fromkeys(converted["公司"].dropna().astype(str))
    )
    requested_highlight = str(highlight_company or "").strip()
    tracked_company = (
        requested_highlight
        if requested_highlight in available_companies
        else ""
    )
    sample_signature = (
        "adaptive_risk_bubble_axes_v2",
        chart_name,
        tuple(periods),
        available_companies,
        tracked_company,
    )
    if (
        st.session_state.get(signature_key) != sample_signature
        or revision_key not in st.session_state
    ):
        st.session_state[signature_key] = sample_signature
        st.session_state[revision_key] = (
            int(st.session_state.get(revision_key, 0)) + 1
        )
    st.session_state.setdefault(revision_key, 0)
    selection_name = (
        f"risk_ratio_zoom_{x_code.lower()}_{st.session_state[revision_key]}"
    )
    try:
        default_domain, latest_period = risk_ratio_scatter_default_domain(
            converted,
            x_code,
            y_code,
            periods,
            chart_name,
            tracked_company,
            company_colors,
            bubble_size_code=bubble_size_code,
            bubble_size_label=denominator_label,
        )
        full_chart, _ = build_matrix_chart(
            converted,
            x_code,
            y_code,
            periods,
            chart_name,
            tracked_company,
            company_colors=company_colors,
            percentage_axes=True,
            panel_label="全样本图",
            chart_width=RISK_SCATTER_FULL_PANEL_WIDTH,
            chart_height=panel_height,
            linked_selection_name=selection_name,
            apply_theme=False,
            bubble_size_code=bubble_size_code,
            bubble_size_label=denominator_label,
        )
    except ValueError as exc:
        st.warning(str(exc))
        return

    with st.container(
        horizontal=True,
        vertical_alignment="center",
        key=f"risk_scatter_print_controls_{x_code.lower()}",
    ):
        if st.button(
            "定位追踪公司" if tracked_company else "恢复默认范围",
            icon=":material/my_location:",
            key=f"{state_prefix}_reset",
        ):
            st.session_state[revision_key] += 1
            st.rerun(scope="fragment")
        st.caption(
            "拖动全样本图灰色选区可移动观察范围，滚轮可缩放；"
            "当前局部视图会直接用于打印，打印时自动隐藏选区框。"
        )

    local_chart = None
    local_error = ""
    try:
        local_chart, _ = build_matrix_chart(
            converted,
            x_code,
            y_code,
            periods,
            chart_name,
            tracked_company,
            company_colors=company_colors,
            percentage_axes=True,
            zoom_to_overlap_region=True,
            panel_label="局部样本放大",
            chart_width=RISK_SCATTER_ZOOM_PANEL_WIDTH,
            chart_height=panel_height,
            show_axis_titles=False,
            linked_selection_name=selection_name,
            apply_theme=False,
            bubble_size_code=bubble_size_code,
            bubble_size_label=denominator_label,
            show_size_legend=False,
        )
        local_chart.to_dict(validate=True)
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        local_error = str(exc)

    st.caption(
        f"展示 {latest_period}；每个气泡代表一家公司，横纵轴均为占{denominator_label}的比例，"
        f"气泡面积代表{denominator_label}规模；"
        "横纵轴根据各指标量级自动采用%、‰或基点（bp），具体单位见坐标轴标题；"
        "浅灰虚线为全样本横纵指标中位数。"
    )
    if local_chart is not None:
        linked_chart = combine_linked_matrix_charts(
            full_chart,
            local_chart,
            selection_name=selection_name,
            selection_domain=default_domain,
        )
        st.altair_chart(linked_chart, width="content")
    else:
        st.altair_chart(
            full_chart,
            width="content",
            key=f"{key_prefix}_{x_code}_risk_scatter_full_v2",
        )
    if local_error:
        st.caption(f"局部放大图暂不可用：{local_error}")
    st.caption(
        "局部图横纵轴与全样本图选区实时联动；设置追踪公司后，默认选区会围绕该公司及其邻近公司。"
    )


def _chart_input_metric_codes(
    chart_name: str,
    plan_kind: str,
    *,
    metric_codes_override: tuple[str, ...] | None = None,
    chart_config: dict | None = None,
) -> tuple[str, ...]:
    """Keep calculation inputs that are not navigation display metrics.

    优先使用 Excel 提供的 metric_codes_override；否则回退到
    COMPANY_NAVIGATION 里的 metric_codes_for_chart(chart_name)。
    """
    if metric_codes_override:
        codes = list(metric_codes_override)
    else:
        codes = list(metric_codes_for_chart(chart_name))

    cfg = chart_config or {}

    if plan_kind == COMPONENT_STACK:
        # Excel 里可以填 {"denominator_code": "...", "ratio_codes": {...}}
        denominator_code = (
            cfg.get("denominator_code")
            or COMPONENT_STACK_DENOMINATOR_CODES.get(chart_name, "")
        )
        if denominator_code:
            codes.append(denominator_code)
        ratio_codes = cfg.get("ratio_codes") or (
            QUANT_RISK_STACK_RATIO_CODES
            if chart_name == "量化风险最低资本构成"
            else {}
        )
        codes.extend(ratio_codes.values())

    if plan_kind == CAPITAL_RATIO_COMBO and chart_name == "核心充足率变化":
        codes.append("ACTUAL_CAPITAL")

    return tuple(dict.fromkeys(codes))

# ★ 新增：偿付能力阶梯打分基准（默认兜底）
# 只用于雷达图顶点"偿付能力"（COMBINED_SOLVENCY_RATIO），不参与其他图表。
# 分数采用 100 分制，与 build_capability_radar_chart 的 max_score=100 一致。
# 若某天想从 Excel 里覆盖这些档位，把 steps 写进 chart_config.axes 即可，
# 此处仅作为"未配置时"的兜底。
_CAPABILITY_STEP_SCORE_FALLBACK: dict[str, list[dict]] = {
    "COMBINED_SOLVENCY_RATIO": [
        {"min": 200.0,          "score": 100.0},   # ≥200%        → 100 分
        {"min": 150.0,          "score": 70.0},    # 150% ~ 200%  → 70 分
        {"min": 120.0,          "score": 50.0},    # 120% ~ 150%  → 50 分
        {"min": 100.0,          "score": 30.0},    # 100% ~ 120%  → 30 分
        {"min": float("-inf"),  "score": 0.0},     # <100%        → 0 分
    ],
}


def _score_with_steps(value: float, steps: list[dict]) -> float:
    """按阶梯配置对单个数值打分。

    参数
    ----
    value : 原始指标值（例如偿付能力充足率 185.3 表示 185.3%）
    steps : [{"min": 200, "score": 100}, {"min": 150, "score": 70}, ...]

    规则
    ----
    把 steps 按 min 从大到小排序，命中第一个 value >= min 的档位，就返回该档 score。
    所有档位都没命中（value 比最小档的 min 还小）时，返回最后一档的 score。
    """
    if pd.isna(value):
        return float("nan")
    numeric = float(value)
    sorted_steps = sorted(
        steps,
        key=lambda s: float(s.get("min", float("-inf"))),
        reverse=True,
    )
    for step in sorted_steps:
        threshold = float(step.get("min", float("-inf")))
        if numeric >= threshold:
            return float(step.get("score", 0.0))
    return 0.0


# ★ 新增：能力五边形的默认打分基准
# 只在这些指标出现在雷达图 axes 里且 Excel 未提供 lower/upper 时兜底。
# 金额类基准使用"万元"（Step3 输出的标准单位是万元）。
_DEFAULT_CAPABILITY_AXES: list[dict] = [
    {"code": "PREMIUM_GROWTH",            "label": "保费增速",
     "period": "本季度数", "lower": -10.0,  "upper": 40.0},
    {"code": "ROE",                       "label": "ROE",
     "period": "本年累计数", "lower": 0.0,    "upper": 12.0},
    {"code": "TOTAL_ASSETS",              "label": "总资产",
     "period": "本季度数",   "lower": 500_000,   "upper": 30_000_000},
    {"code": "ACTUAL_CAPITAL",            "label": "实际资本",
     "period": "本季度末数", "lower": 200_000,   "upper": 15_000_000},
    # ★ 偿付能力改为"阶梯打分"：不再使用 lower/upper 线性映射，
    #   而是按 ≥200/150/120/100 的档位给分。
    {"code": "COMBINED_SOLVENCY_RATIO",   "label": "偿付能力",
     "period": "本季度末数",
     "steps": _CAPABILITY_STEP_SCORE_FALLBACK["COMBINED_SOLVENCY_RATIO"]},
]


def _capability_scores(
    frame: pd.DataFrame,
    axes_config: list[dict],
    periods: list[str],
    baseline_frame: pd.DataFrame | None = None,
    *,
    group_column: str = "同业分类",
    step_score_fallback: dict[str, list[dict]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """能力五边形打分。

    与旧版不同之处：
    1. 若顶点配置里带 `steps`（或该编码在 step_score_fallback 里），
       则按阶梯打分。例如偿付能力：≥200%=100, 150~200%=70,
       120~150%=50, 100~120%=30, <100%=0。
    2. 其余顶点按「公司所在分类」（同业分类）在同分类内做百分位排名。
       即：大型险企只跟大型险企比，中小险企只跟中小险企比。
    3. 若同分类内有效样本不足 2 家（无法排名），自动回退到全量池，
       避免出现"整个分类所有公司都是 50 分"的情况。

    参数
    ----
    frame            : 当前筛选范围（含 公司 / 指标编码 / 报告期 / 期间口径 / 数值 / 同业分类）
    axes_config      : 每个顶点的配置，可含 steps=[{"min":200,"score":100}, ...]
    periods          : 可用报告期，函数内取最后一个作为"最新期"
    baseline_frame   : 用于构建基准池的数据源（默认用 frame 自身）
    group_column     : 用于分组的列名，默认"同业分类"
    step_score_fallback : 阶梯打分兜底配置，默认用模块级 _CAPABILITY_STEP_SCORE_FALLBACK

    返回
    ----
    (score_frame, raw_frame, latest_period)
        score_frame: 列 = 公司 + "<label>_得分"
        raw_frame  : 列 = 公司 + "<label>"（原始值）
    """
    latest_period = str(periods[-1]) if periods else ""
    if frame.empty or not periods or not axes_config:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    # ---------- 公司名归一化（幂等） ----------
    frame = frame.copy()
    frame["公司"] = (
        frame["公司"].astype(str)
        .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
        .str.strip()
    )
    if baseline_frame is not None:
        baseline_frame = baseline_frame.copy()
        baseline_frame["公司"] = (
            baseline_frame["公司"].astype(str)
            .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
            .str.strip()
        )
    else:
        baseline_frame = frame

    period_frame = frame[
        frame["报告期"].astype(str).eq(latest_period)
    ].copy()
    if period_frame.empty:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    companies = list(dict.fromkeys(
        str(c).strip()
        for c in period_frame["公司"].fillna("").astype(str)
        if str(c).strip()
    ))
    if not companies:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    # ---------- 记录每家公司的分类（用于分组排名） ----------
    group_map: dict[str, str] = {}
    if group_column and group_column in period_frame.columns:
        group_series = (
            period_frame[["公司", group_column]]
            .assign(**{
                group_column: lambda d: (
                    d[group_column]
                    .fillna("未分类").astype(str).str.strip().replace("", "未分类")
                )
            })
            .drop_duplicates(subset=["公司"], keep="last")
        )
        group_map = dict(zip(group_series["公司"], group_series[group_column]))

    # ---------- 规范化 axes（含 steps） ----------
    normalized_axes: list[dict] = []
    for ax in axes_config:
        code = str(ax.get("code", "")).strip()
        if not code:
            continue
        label = str(ax.get("label", "")).strip() or code
        target_period = str(ax.get("period", "")).strip()
        steps = ax.get("steps")
        # 兼容从 Excel JSON 读进来时 steps 是字符串的情况
        if isinstance(steps, str):
            try:
                import json
                steps = json.loads(steps)
            except Exception:
                steps = None
        normalized_axes.append({
            "code": code,
            "label": label,
            "period": target_period,
            "steps": list(steps) if steps else None,
        })
    if not normalized_axes:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    fallback_steps = (
        step_score_fallback
        if step_score_fallback is not None
        else _CAPABILITY_STEP_SCORE_FALLBACK
    )

    # ---------- 第 1 遍：收集待展示公司的原始值 ----------
    raw_cache: dict[str, dict[str, float]] = {}
    for company in companies:
        company_rows = period_frame[
            period_frame["公司"].astype(str).eq(company)
        ]
        raw: dict[str, float] = {}
        for ax in normalized_axes:
            match = company_rows[
                company_rows["指标编码"].astype(str).eq(ax["code"])
            ].copy()
            if not match.empty and ax["period"]:
                target_period = str(ax["period"]).strip()
                match["_优先级"] = (
                    match["期间口径"].astype(str).str.strip().eq(target_period)
                )
                match = match.sort_values(
                    "_优先级", ascending=False, kind="stable",
                ).drop(columns="_优先级")
            value = float("nan")
            if not match.empty:
                numeric = pd.to_numeric(
                    match.iloc[0]["数值"], errors="coerce"
                )
                if pd.notna(numeric):
                    value = float(numeric)
            raw[ax["label"]] = value
        raw_cache[company] = raw

    # ---------- 构建基准池：分「全量池」和「按分类分组池」 ----------
    baseline_period_frame = baseline_frame[
        baseline_frame["报告期"].astype(str).eq(latest_period)
    ].copy()

    # baseline 里每家公司的分类
    baseline_group_map: dict[str, str] = {}
    if group_column and group_column in baseline_period_frame.columns:
        bg = (
            baseline_period_frame[["公司", group_column]]
            .assign(**{
                group_column: lambda d: (
                    d[group_column]
                    .fillna("未分类").astype(str).str.strip().replace("", "未分类")
                )
            })
            .drop_duplicates(subset=["公司"], keep="last")
        )
        baseline_group_map = dict(zip(bg["公司"], bg[group_column]))

    baseline_pool_all: dict[str, list[float]] = {}
    baseline_pool_by_group: dict[tuple[str, str], list[float]] = {}

    for ax in normalized_axes:
        label = ax["label"]
        rows = baseline_period_frame[
            baseline_period_frame["指标编码"].astype(str).eq(ax["code"])
        ].copy()
        if rows.empty:
            baseline_pool_all[label] = []
            continue

        # 期间口径优先级排序（与原逻辑保持一致）
        if ax["period"]:
            target_period = str(ax["period"]).strip()
            rows["_优先级"] = (
                rows["期间口径"].astype(str).str.strip().eq(target_period)
            )
            rows = (
                rows.sort_values(
                    ["公司", "_优先级"],
                    ascending=[True, False],
                    kind="stable",
                )
                .drop_duplicates(subset=["公司"], keep="first")
                .drop(columns="_优先级")
            )
        else:
            rows = rows.drop_duplicates(subset=["公司"], keep="last")

        # 全量池
        pool_all = pd.to_numeric(rows["数值"], errors="coerce").dropna().tolist()
        baseline_pool_all[label] = pool_all

        # 按分类拆分池
        for _, r in rows.iterrows():
            name = str(r["公司"]).strip()
            if not name:
                continue
            value = pd.to_numeric(pd.Series([r["数值"]]), errors="coerce").iloc[0]
            if pd.isna(value):
                continue
            group_name = baseline_group_map.get(name, "未分类")
            baseline_pool_by_group.setdefault(
                (group_name, label), []
            ).append(float(value))

    # ---------- 第 2 遍：逐公司逐顶点打分 ----------
    raw_rows: list[dict] = []
    score_rows: list[dict] = []
    for company in companies:
        raw = raw_cache[company]
        company_group = group_map.get(company, "未分类")
        raw_row: dict = {"公司": company}
        score_row: dict = {"公司": company}

        for ax in normalized_axes:
            label = ax["label"]
            code = ax["code"]
            value = raw.get(label, float("nan"))
            raw_row[label] = value

            if pd.isna(value):
                # ★ 用 NaN 表示"缺失"，而不是 0.0，让雷达图区分缺数据顶点
                score_row[f"{label}_得分"] = float("nan")
                continue

            # ① 阶梯打分优先（如偿付能力）
            steps = ax.get("steps") or fallback_steps.get(code)
            if steps:
                score_row[f"{label}_得分"] = _score_with_steps(value, steps)
                continue

            # ② 同分类内百分位排名
            pool = baseline_pool_by_group.get((company_group, label), [])
            if len(pool) < 2:
                # 组内不足 2 家（无法排名），退回到全量池
                pool = baseline_pool_all.get(label, [])
            n = len(pool)
            if n == 0:
                score_row[f"{label}_得分"] = 0.0
            elif n == 1:
                score_row[f"{label}_得分"] = 50.0
            else:
                below = sum(1 for v in pool if v < value)
                equal = sum(1 for v in pool if v == value)
                # 平均排名法（1-based），并列取平均名次
                rank = below + (equal + 1) / 2.0
                score = (rank - 1.0) / (n - 1.0) * 100.0
                score_row[f"{label}_得分"] = max(0.0, min(100.0, score))

        raw_rows.append(raw_row)
        score_rows.append(score_row)

    # ---------- 排序与组装结果 ----------
    raw_df = pd.DataFrame(raw_rows)
    score_df = pd.DataFrame(score_rows)
    score_cols = [c for c in score_df.columns if c.endswith("_得分")]
    if score_cols:
        score_df["_axes_with_data"] = score_df[score_cols].apply(
            lambda r: int((r > 0).sum()), axis=1
        )
        score_df = (
            score_df.sort_values(
                ["_axes_with_data", *score_cols],
                ascending=False,
            )
            .drop(columns=["_axes_with_data"])
            .reset_index(drop=True)
        )
    raw_df = (
        raw_df.set_index("公司")
        .reindex(score_df["公司"])
        .reset_index()
    )
    return score_df, raw_df, latest_period


def _render_combination_analysis(
    frame: pd.DataFrame,
    chart_name: str,
    *,
    periods: list[str],
    unit_mode: str,
    highlight_company: str,
    key_prefix: str,
    financing_data: pd.DataFrame | None = None,
    module_override: Step7Module | None = None,   # ★ 新增
) -> None:
    """Render an approved multi-metric plan selected by chart name.

    如果传入了 module_override，则用它携带的 chart_kind / metric_codes /
    chart_config 覆盖 COMPANY_NAVIGATION 里的默认配置。
    """
    if module_override is not None and module_override.chart_kind:
        plan = SimpleNamespace(kind=module_override.chart_kind)
    else:
        plan = chart_plan_for(chart_name)

    # ---- 应用 Excel chart_config 覆盖硬编码 spec ----
    if module_override is not None and module_override.chart_config:
        cfg = module_override.chart_config
        if plan.kind == COMPONENT_STACK and "components" in cfg:
            COMPONENT_STACK_SPECS[chart_name] = tuple(
                (c["code"], c["label"], c["color"]) for c in cfg["components"]
            )
        if plan.kind == RISK_RATIO_SCATTER and "scatter" in cfg:
            s = cfg["scatter"]
            RISK_RATIO_SCATTER_SPECS[chart_name] = (
                s["x_code"], s["y_code"],
                s["bubble_size_code"], s.get("denominator_label", "认可资产"),
            )
            
    if plan.kind == STRUCTURE_CHANGE_CHART:
        companies = _ordered_companies(frame)
        if not companies:
            st.warning("当前没有公司数据。")
            return

        if chart_name == "承保规模":
            category_map = {"SIGNED_PREMIUM_MOTOR": "车险", "SIGNED_PREMIUM": "非车险"}
            total_code = "SIGNED_PREMIUM"
            line_color_map = {"车险": "#E36C0A", "非车险": "#9B3FB5"}
        elif chart_name == "经营效益":
            category_map = {"NET_PROFIT": "净利润率"}
            total_code = "SIGNED_PREMIUM"
            line_color_map = {"净利润率": "#E36C0A"}
        elif chart_name == "资本规模":
            category_map = {"CORE_T1_CAPITAL": "核心资本", "ANC_T1_CAPITAL": "附属资本"}
            total_code = "ACTUAL_CAPITAL"
            line_color_map = {"核心资本": "#E36C0A", "附属资本": "#9B3FB5"}
        else:
            return

        periods = sorted(frame["报告期"].dropna().unique().tolist())
        columns_per_row = 2

        for i in range(0, len(companies), columns_per_row):
            batch = companies[i:i + columns_per_row]
            cols = st.columns(len(batch), gap="large")
            for col, company in zip(cols, batch):
                with col:
                    try:
                        chart = build_structure_change_chart(
                            frame, category_map=category_map, total_code=total_code,
                            period_order=periods, company_name=company,
                            line_color_map=line_color_map,
                            show_legend=True,
                        )
                        st.altair_chart(chart, use_container_width=True)
                    except Exception as e:
                        # ★ 详细报错 + 打印堆栈，便于排查
                        import traceback
                        st.error(f"❌ {company} 图表渲染失败：{e}")
                        with st.expander("查看详细错误", expanded=False):
                            st.code(traceback.format_exc())
        return
        
    # ★ 新增：公司业绩展示（收入排名—利润排名，按模式分列）
    if plan.kind == PERFORMANCE_PATTERN_GRID:
        companies = _ordered_companies(frame)
        if not companies:
            st.warning("当前没有公司数据。")
            return

        cfg = module_override.chart_config if module_override else {}
        revenue_code = str(cfg.get("revenue_code", "SIGNED_PREMIUM")).strip()
        profit_code = str(cfg.get("profit_code", "NET_PROFIT")).strip()
        pattern_overrides: dict = cfg.get("pattern_overrides") or {}

        periods_list = sorted(
            frame["报告期"].dropna().astype(str).unique().tolist()
        )

        # ---------- 1. 计算每家公司、每个报告期的收入/利润排名 ----------
        _df = frame.copy()
        _df["数值"] = pd.to_numeric(_df["数值"], errors="coerce")
        _df["报告期"] = _df["报告期"].astype(str)
        _df["公司"] = _df["公司"].astype(str).str.strip()

        all_companies = list(dict.fromkeys(
            c for c in _df["公司"].dropna().astype(str) if c and c != "nan"
        ))
        n_companies = len(all_companies)
        if n_companies == 0:
            st.warning("无公司数据。")
            return

        rank_rows: list[dict] = []
        for period in periods_list:
            period_df = _df[_df["报告期"].eq(period)]
            revenue = (
                period_df[period_df["指标编码"].astype(str).eq(revenue_code)]
                [["公司", "数值"]].dropna(subset=["数值"])
                .drop_duplicates("公司", keep="last")
            )
            profit = (
                period_df[period_df["指标编码"].astype(str).eq(profit_code)]
                [["公司", "数值"]].dropna(subset=["数值"])
                .drop_duplicates("公司", keep="last")
            )
            if revenue.empty or profit.empty:
                continue
            revenue["收入排名"] = revenue["数值"].rank(
                ascending=False, method="min"
            )
            profit["利润排名"] = profit["数值"].rank(
                ascending=False, method="min"
            )
            merged = revenue[["公司", "收入排名"]].merge(
                profit[["公司", "利润排名"]], on="公司", how="inner"
            )
            merged["报告期"] = period
            rank_rows.append(merged)

        if not rank_rows:
            st.warning(
                f"缺少 {revenue_code} 或 {profit_code} 数据，"
                "无法计算排名。请检查 STEP5/STEP6 集成数据。"
            )
            return

        rank_df = pd.concat(rank_rows, ignore_index=True)

        # ---------- 2. 对每家公司做模式分类（人工覆盖优先） ----------
        pattern_map: dict[str, str] = {}
        for company in all_companies:
            if company in pattern_overrides:
                pattern_map[company] = str(pattern_overrides[company]).strip()
                continue
            series = (
                rank_df[rank_df["公司"].eq(company)]
                .sort_values("报告期")["利润排名"]
            )
            if series.empty:
                continue
            pattern_map[company] = classify_performance_pattern(series)

        # ---------- 3. 按模式分组 ----------
        grouped: dict[str, list[str]] = {p: [] for p in PERFORMANCE_PATTERN_ORDER}
        for company in all_companies:
            pattern = pattern_map.get(company, "其他")
            if pattern not in grouped:
                grouped[pattern] = []
            grouped[pattern].append(company)

        active_patterns = [
            p for p in PERFORMANCE_PATTERN_ORDER if grouped.get(p)
        ]
        if not active_patterns:
            st.warning("没有公司能被归类到任何业绩模式。")
            return

        # ---------- 4. 展示说明 ----------
        st.caption(
            f"横轴为「{revenue_code}」在样本内的排名，纵轴为「{profit_code}」排名；"
            "反向坐标（第 1 名在右上角）。"
            "公司按利润排名的年度轨迹自动归类到以下模式："
            "逐年提升 / 稳健经营 / V形反转 / 大起大落 / 持续恶化。"
        )
        # ★ 新增：数据诊断展开区，帮用户排查"某年缺失"的具体原因
        with st.expander("🔍 数据诊断：查看每家公司的收入/利润数据覆盖情况", expanded=False):
            st.caption(
                f"下表显示每家公司每个报告期是否有「{revenue_code}」和「{profit_code}」数据。"
                "❌ 表示该期该指标缺失（未披露或未集成）。"
                "若发现大量 ❌，请到 STEP5/STEP6 检查原始数据是否完整。"
            )
            _diag_rows = []
            for _c in all_companies:
                _c_df = _df[_df["公司"].eq(_c)]
                for _p in periods_list:
                    _p_df = _c_df[_c_df["报告期"].eq(_p)]
                    _has_revenue = not _p_df[
                        _p_df["指标编码"].astype(str).eq(revenue_code)
                    ].dropna(subset=["数值"]).empty
                    _has_profit = not _p_df[
                        _p_df["指标编码"].astype(str).eq(profit_code)
                    ].dropna(subset=["数值"]).empty
                    _diag_rows.append({
                        "公司": _c,
                        "报告期": _p,
                        f"有{revenue_code}": "✅" if _has_revenue else "❌",
                        f"有{profit_code}": "✅" if _has_profit else "❌",
                    })
            st.dataframe(
                pd.DataFrame(_diag_rows),
                width="stretch",
                hide_index=True,
            )

        # ---------- 5. 分列并排渲染 ----------
        _pattern_cols = st.columns(len(active_patterns), gap="small")
        for _col, _pattern in zip(_pattern_cols, active_patterns):
            with _col:
                # 列标题（模仿参考图的橙红色横幅）
                st.markdown(
                    f"<div style='background:#D94330; color:#FFFFFF; "
                    f"text-align:center; padding:6px 0; border-radius:3px; "
                    f"font-weight:700; font-size:13px; margin-bottom:10px;'>"
                    f"{escape(_pattern)}</div>",
                    unsafe_allow_html=True,
                )
                # 该列下的公司，纵向堆叠
                for _company in grouped[_pattern]:
                    # ★ 新增：在公司图上方显示公司名（红色小标题）
                    st.markdown(
                        f"<div style='text-align:center; "
                        f"font-size:11px; font-weight:700; color:#C8102E; "
                        f"margin:6px 0 2px; white-space:nowrap; "
                        f"overflow:hidden; text-overflow:ellipsis;' "
                        f"title='{escape(_company)}'>"
                        f"{escape(_company)}</div>",
                        unsafe_allow_html=True,
                    )
                    try:
                        _chart = build_performance_pattern_trajectory(
                            frame,
                            _company,
                            periods_list,
                            revenue_code=revenue_code,
                            profit_code=profit_code,
                        )
                        st.altair_chart(
                            _chart,           # ★ 改动：不再清空 title
                            width="content",
                            key=f"{key_prefix}_pattern_{_pattern}_{_company}",
                        )
                    except ValueError as _exc:
                        st.warning(f"{_company}：{_exc}")
        return

    if plan.kind == KEY_METRICS_TABLE:
        render_key_solvency_overview(frame, highlight_company=highlight_company)
        return
    if plan.kind == FINANCING_TABLE:
        render_major_financing(financing_data)
        return
    if plan.kind == PENDING_DEFINITION:
        st.warning(
            f"“{chart_name}”在新版 Excel 中标记为“待定”，目前没有定义可绘图的明细组成指标。"
        )
        return
    required_codes = _chart_input_metric_codes(
        chart_name,
        plan.kind,
        metric_codes_override=(
            module_override.metric_codes if module_override else None
        ),
        chart_config=(
            module_override.chart_config if module_override else None
        ),
    )
    chart_frame = (
        frame[frame["指标编码"].fillna("").astype(str).isin(required_codes)].copy()
        if required_codes
        else frame
    )
    # ★ 二次保险：主流程 show_step_7_solvency 已经在入口处清洗过公司名，
    #   这里再做一次同样的归一化，防止其他调用方绕过主流程导致名字不一致。
    chart_frame["公司"] = (
        chart_frame["公司"].astype(str)
        .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
        .str.strip()
    )
    converted = _convert_unit(chart_frame, unit_mode)
    company_colors = _company_color_map(converted["公司"], highlight_company)
    company_panel_count = converted["公司"].nunique()
    dense_company_panels = company_panel_count >= 10
    if plan.kind == SOLVENCY_RATIO_COMBO:
        shared_y_domain = solvency_ratio_axis_domain(converted, periods)
        period_colors = report_period_color_map(periods)
        st.caption(
            "图表说明：柱状图为综合偿付能力充足率；粉色折线为核心偿付能力充足率。"
            "各公司统一使用同一百分比纵轴范围，柱形颜色对应不同报告期。"
        )
        _render_chart_legend([
            *[
                (period_colors[period], period, "square")
                for period in periods
            ],
            ("#FD349C", "核心偿付能力充足率（%）", "line"),
        ])
        _render_company_chart_grid(
            converted,
            lambda company_frame: build_solvency_ratio_combo_chart(
                company_frame,
                periods,
                shared_y_domain=shared_y_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_solvency_ratio_combo",
            highlight_company=highlight_company,
        )
    elif plan.kind == COMPANY_PERIOD_BAR:
        codes = (
            list(module_override.metric_codes)
            if module_override and module_override.metric_codes
            else list(metric_codes_for_chart(chart_name))
        )
        if codes:
            shared_y_domain = metric_bar_axis_domain(
                converted,
                codes[0],
                periods,
            )
            period_colors = report_period_color_map(periods)
            _render_chart_legend([
                (period_colors[period], period, "square")
                for period in periods
            ])
            _render_company_chart_grid(
                converted,
                lambda company_frame: build_company_period_bar_chart(
                    company_frame,
                    codes[0],
                    periods,
                    shared_y_domain=shared_y_domain,
                    dense_layout=dense_company_panels,
                    panel_count=company_panel_count,
                ),
                key_prefix=f"{key_prefix}_{codes[0]}_bar",
                highlight_company=highlight_company,
            )

    elif plan.kind == CAPITAL_RATIO_COMBO:
        cfg = module_override.chart_config if module_override else {}
        if chart_name == "综合充足率变化":
            amount_groups = cfg.get("amount_groups") or {
                "实际资本": ("ACTUAL_CAPITAL",),
                "最低资本": ("MINIMUM_CAPITAL",),
            }
            ratio_code = "COMBINED_SOLVENCY_RATIO"
            ratio_label = "综合偿付能力充足率（%）"
        else:
            amount_groups = cfg.get("amount_groups") or {
                "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
                "最低资本": ("MINIMUM_CAPITAL",),
            }
            ratio_code = "CORE_SOLVENCY_RATIO"
            ratio_label = "核心偿付能力充足率（%）"
        grouped_amount_groups = cfg.get("grouped_amount_groups") or {
            "实际资本": ("ACTUAL_CAPITAL",),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        shared_amount_domain = capital_ratio_amount_axis_domain(
            converted,
            amount_groups,
            periods,
        )
        grouped_amount_domain = capital_ratio_amount_axis_domain(
            converted,
            grouped_amount_groups,
            periods,
            stacked=False,
        )
        st.caption(
            "上方 KPMG Pink 折线展示对应充足率，下方堆叠柱展示资本规模；"
            "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。"
        )
        st.markdown("**堆叠柱组合图**")
        _render_chart_legend([
            *[
                (KPMG_CAPITAL_COMBO_COLORS[index], label, "square")
                for index, label in enumerate(amount_groups)
            ],
            ("#FD349C", ratio_label, "line"),
        ])
        _render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_ratio_combo_chart(
                company_frame,
                amount_groups,
                periods,
                chart_name,
                ratio_code=ratio_code,
                ratio_label=ratio_label,
                shared_amount_domain=shared_amount_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_{ratio_code}_combo",
            highlight_company=highlight_company,
        )
        st.markdown("**并列柱组合图**")
        st.caption(
            "新增视图：每个报告期的两项资本各自成柱，"
            "上方仍保留对应充足率折线。"
        )
        _render_chart_legend([
            *[
                (KPMG_CAPITAL_COMBO_COLORS[index], label, "square")
                for index, label in enumerate(grouped_amount_groups)
            ],
            ("#FD349C", ratio_label, "line"),
        ])
        _render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_ratio_combo_chart(
                company_frame,
                grouped_amount_groups,
                periods,
                chart_name,
                ratio_code=ratio_code,
                ratio_label=ratio_label,
                shared_amount_domain=grouped_amount_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
                bar_layout="grouped",
            ),
            key_prefix=f"{key_prefix}_{ratio_code}_grouped_combo",
            highlight_company=highlight_company,
        )
    elif plan.kind == CAPITAL_STRUCTURE_COMBO:
        cfg = module_override.chart_config if module_override else {}
        capital_structure_groups = cfg.get("amount_groups") or {
            "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
            "附属资本": ("ANC_T1_CAPITAL", "ANC_T2_CAPITAL"),
        }
        shared_amount_domain = capital_ratio_amount_axis_domain(
            converted,
            capital_structure_groups,
            periods,
        )
        st.caption(
            "上方 KPMG Pink 折线展示核心资本占实际资本的比例，下方堆叠柱展示核心资本与附属资本；"
            "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。"
        )
        _render_chart_legend([
            (KPMG_CAPITAL_COMBO_COLORS[0], "核心资本", "square"),
            (KPMG_CAPITAL_COMBO_COLORS[1], "附属资本", "square"),
            ("#FD349C", "核心资本占实际资本的比例", "line"),
        ])
        _render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_ratio_combo_chart(
                company_frame,
                capital_structure_groups,
                periods,
                "核心资本与附属资本结构",
                ratio_label="核心资本占实际资本的比例（%）",
                share_line=True,
                shared_amount_domain=shared_amount_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_core_capital_structure",
            highlight_company=highlight_company,
        )
    elif plan.kind == COMPONENT_STACK:
        specs = COMPONENT_STACK_SPECS.get(chart_name, ())
        if not specs:
            st.warning(f"“{chart_name}”尚未配置组成指标。")
            return
        if chart_name == "计入各级资本的保单未来盈余构成占比":
            st.caption(
                "各色块表示计入相应资本层级的保单未来盈余占该公司已披露保单未来盈余明细合计的比例；"
                "100%表示该公司披露的保单未来盈余全部计入了同一个资本层级，其他资本层级没有披露这个明细。"
            )
        elif chart_name == "量化风险最低资本构成":
            st.caption(
                "柱高按各构成项目/量化风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "数据标签与对应比例折线图一致，负向抵减项目位于零线下方。"
            )
        elif chart_name == "各类市场风险占比":
            st.caption(
                "柱高按各构成项目/市场风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "负向风险分散效应位于零线下方。"
            )
        elif chart_name == "各类信用风险占比":
            st.caption(
                "柱高按各构成项目/信用风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "负向风险分散效应位于零线下方。"
            )
        else:
            st.caption("按公司分面展示构成；负向抵减项目位于零线下方，所有颜色均来自 KPMG 色卡。")
        negative_component_codes = (
            (
                "QUANT_RISK_DIVERSIFICATION_EFFECT",
                "CONTRACT_LOSS_ABSORPTION_EFFECT",
            )
            if chart_name == "量化风险最低资本构成"
            else ("MARKET_RISK_DIVERSIFICATION_EFFECT",)
            if chart_name == "各类市场风险占比"
            else ("CREDIT_RISK_DIVERSIFICATION_EFFECT",)
            if chart_name == "各类信用风险占比"
            else ()
        )
        proportion_stack = chart_name in COMPONENT_STACK_DENOMINATOR_CODES
        shared_stack_domain = (
            component_stack_proportion_axis_domain(
                converted,
                specs,
                periods,
                label_ratio_codes=(
                    QUANT_RISK_STACK_RATIO_CODES
                    if chart_name == "量化风险最低资本构成"
                    else None
                ),
                label_denominator_code=COMPONENT_STACK_DENOMINATOR_CODES[chart_name],
                negative_component_codes=negative_component_codes,
            )
            if proportion_stack
            else component_stack_axis_domain(
                converted,
                specs,
                periods,
                negative_component_codes=negative_component_codes,
            )
        )
        _render_chart_legend([
            (color, label, "square")
            for _, label, color in specs
        ])
        _render_company_chart_grid(
            converted,
            lambda company_frame: build_component_stack_chart(
                company_frame,
                specs,
                periods,
                chart_name,
                label_ratio_codes=(
                    QUANT_RISK_STACK_RATIO_CODES
                    if chart_name == "量化风险最低资本构成"
                    else None
                ),
                label_denominator_code=(
                    COMPONENT_STACK_DENOMINATOR_CODES.get(chart_name, "")
                ),
                value_labels=False,
                negative_component_codes=negative_component_codes,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
                shared_y_domain=shared_stack_domain,
                plot_proportions=proportion_stack,
                min_label_share=(
                    0.10
                    if chart_name == "各类市场风险占比"
                    else None
                ),
            ),
            key_prefix=f"{key_prefix}_component_stack",
            highlight_company=highlight_company,
        )

    elif plan.kind == CAPITAL_AMOUNT_COMBO:
        st.caption(
            "100%堆叠柱显示核心一级、核心二级、附属一级和附属二级资本构成；"
            "所有公司和报告期的柱高一致，色块高度代表结构占比，悬浮可查看占比和金额。"
        )
        capital_codes = [
            "CORE_T1_CAPITAL", "CORE_T2_CAPITAL", "ANC_T1_CAPITAL",
            "ANC_T2_CAPITAL",
        ]
        expected_count = converted["公司"].nunique() * len(periods) * len(capital_codes)
        disclosed_count = converted[
            converted["指标编码"].astype(str).isin(capital_codes)
        ].drop_duplicates(["公司", "报告期", "指标编码"]).shape[0]
        capital_legend = [
            (color, label, "square")
            for label, color in CAPITAL_STRUCTURE_COLORS.items()
        ]
        if disclosed_count < expected_count:
            capital_legend.append(("#B8BDC7", "未披露", "square"))
        _render_chart_legend(capital_legend)
        _render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_amount_combo(
                company_frame,
                periods,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_capital_amount",
            highlight_company=highlight_company,
        )
    elif plan.kind == EFFECT_DIVERGING:
        codes = (
            list(module_override.metric_codes)
            if module_override and module_override.metric_codes
            else list(metric_codes_for_chart(chart_name))
        )
        if codes:
            st.caption("按公司展示抵减效应的跨期方向与幅度，零线用于区分正向增加和负向抵减。")
            st.altair_chart(
                _chart_without_internal_title(
                    build_effect_diverging_chart(converted, codes[0], periods)
                ),
                width="stretch",
                key=f"{key_prefix}_{codes[0]}_effect",
            )
    elif plan.kind == CAPITAL_EFFICIENCY_BUBBLE:
        _render_capital_efficiency_bubble_fragment(
            converted,
            periods,
            company_colors,
            highlight_company,
            key_prefix,
        )
    elif plan.kind == SOLVENCY_MATRIX:
        chart, _ = build_matrix_chart(
            converted,
            "CORE_SOLVENCY_RATIO",
            "COMBINED_SOLVENCY_RATIO",
            periods,
            "偿付能力矩阵",
            highlight_company,
            regulatory_lines=True,
            company_colors=company_colors,
        )
        st.altair_chart(
            _chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_solvency_matrix",
        )
        st.caption("横轴50%、纵轴100%的红色虚线分别为核心和综合偿付能力充足率监管下限。")
    elif plan.kind == MARKET_CREDIT_MATRIX:
        chart, _ = build_matrix_chart(
            converted,
            "MARKET_RISK_TO_QUANT_CAPITAL",
            "CREDIT_RISK_TO_QUANT_CAPITAL",
            periods,
            "市场—信用风险矩阵",
            highlight_company,
            company_colors=company_colors,
        )
        st.altair_chart(
            _chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_market_credit_matrix",
        )
        st.caption("深蓝虚线为样本中位数，用于识别市场风险和信用风险同时偏高的公司。")
    elif plan.kind == RISK_RATIO_SCATTER:
        # 坐标轴口径来自注册表（Excel 可在"图表配置(JSON)"的 scatter 字段覆盖）。
        # 未注册的自定义图表名回退到"市场风险/认可资产"口径，避免 KeyError。
        x_code, y_code, bubble_size_code, denominator_label = (
            RISK_RATIO_SCATTER_SPECS.get(chart_name)
            or RISK_RATIO_SCATTER_SPECS[MARKET_RISK_ASSET_SCATTER]
        )
        if chart_name in RISK_SCATTER_DUAL_VIEW_CHARTS:
            _render_risk_ratio_scatter_fragment(
                converted,
                periods,
                company_colors,
                highlight_company,
                key_prefix,
                chart_name,
                x_code,
                y_code,
                bubble_size_code,
                denominator_label,
            )
        else:
            chart, latest_period = build_matrix_chart(
                converted,
                x_code,
                y_code,
                periods,
                chart_name,
                highlight_company,
                company_colors=company_colors,
                percentage_axes=True,
                bubble_size_code=bubble_size_code,
                bubble_size_label=denominator_label,
            )
            centered_chart = _chart_without_internal_title(chart).properties(
                width=RISK_SCATTER_DISPLAY_WIDTH
            )
            st.caption(
                f"展示 {latest_period}；每个气泡代表一家公司，横纵轴均为占{denominator_label}的比例，"
                f"气泡面积代表{denominator_label}规模；"
                "深蓝虚线为样本横纵指标中位数。"
            )
            with st.container(horizontal_alignment="center"):
                st.altair_chart(
                    centered_chart,
                    width=RISK_SCATTER_DISPLAY_WIDTH,
                    key=f"{key_prefix}_risk_asset_scatter",
                )

    # ★ 新增：能力五边形雷达图
    elif plan.kind == CAPABILITY_RADAR:
        cfg = module_override.chart_config if module_override else {}
        axes_config = cfg.get("axes") or _DEFAULT_CAPABILITY_AXES
        axes_labels = [ax["label"] for ax in axes_config]

        latest_period_for_anchor = str(periods[-1]) if periods else ""
        baseline_full = chart_frame[
            chart_frame["报告期"].astype(str).eq(latest_period_for_anchor)
        ].copy()

        score_all, raw_all, latest_period = _capability_scores(
            chart_frame, axes_config, periods,
            baseline_frame=baseline_full,
        )

        if score_all.empty:
            st.warning(
                "能力五边形需要以下五个指标："
                + "、".join(axes_labels)
                + "。当前筛选范围缺少部分数据。"
            )
            return

        # ---------- 取得每家公司的同业分类 ----------
        category_column = "同业分类" if "同业分类" in chart_frame.columns else None
        if category_column:
            cat_map = (
                chart_frame[["公司", category_column]]
                .dropna()
                .assign(**{category_column: lambda d: d[category_column].astype(str).str.strip()})
                .drop_duplicates(subset=["公司"], keep="last")
                .set_index("公司")[category_column]
                .to_dict()
            )
        else:
            cat_map = {}
        score_all["_分类"] = (
            score_all["公司"].astype(str).map(cat_map).fillna("未分类")
        )

        # ---------- 分类顺序 ----------
        ordered_categories = list(dict.fromkeys(
            str(category) for category in score_all["_分类"]
        ))
        # 把 "未分类" 放到最后
        if "未分类" in ordered_categories:
            ordered_categories.remove("未分类")
            ordered_categories.append("未分类")

        # ---------- 样式提示 ----------
        st.caption(
            f"展示 {latest_period}；每家公司单独一张能力五边形，"
            "按“同业分类”分组排列。"
            "顶点依次为："
            + "、".join(axes_labels)
            + "。打分基准 = 全量 "
            f"{len(score_all)} 家（百分位排名），"
            "50 分 ≈ 行业中位数水平（灰色虚线五边形）。"
        )

        # ---------- 按分类逐组渲染 ----------
        columns_per_row = 5           # 每行 5 家
        panel_size = 230              # 单张小图尺寸
        tracked_company = str(highlight_company or "").strip()

        for category in ordered_categories:
            cat_scores = (
                score_all[score_all["_分类"].astype(str).eq(category)]
                .reset_index(drop=True)
            )
            if cat_scores.empty:
                continue

            # ---- 组标题 ----
            st.markdown(
                f"<div style='color:#00338D;font-size:15px;font-weight:700;"
                f"border-bottom:1.5px solid #00338D;margin:18px 0 8px;padding-bottom:4px;'>"
                f"{escape(str(category))}"
                f"<span style='color:#7A8496;font-size:12px;font-weight:400;margin-left:8px;'>"
                f"（{len(cat_scores)} 家）</span></div>",
                unsafe_allow_html=True,
            )

            # ---- 网格布局 ----
            companies = list(cat_scores["公司"].astype(str))
            for start in range(0, len(companies), columns_per_row):
                batch = companies[start:start + columns_per_row]
                cols = st.columns(len(batch), gap="small")
                for col, company in zip(cols, batch):
                    with col:
                        is_tracked = (
                            tracked_company
                            and tracked_company != "无"
                            and company == tracked_company
                        )
                        title_color = "#ED2124" if is_tracked else "#00338D"
                        st.markdown(
                            f"<div style='text-align:center;font-size:12px;"
                            f"font-weight:700;color:{title_color};"
                            f"margin:4px 0 0;white-space:nowrap;overflow:hidden;"
                            f"text-overflow:ellipsis;' title='{escape(company)}'>"
                            f"{escape(company)}</div>",
                            unsafe_allow_html=True,
                        )

                        one_company_scores = cat_scores[
                            cat_scores["公司"].astype(str).eq(company)
                        ]
                        # 单公司图：统一用 KPMG 蓝，追踪公司用红色
                        single_color = "#ED2124" if is_tracked else "#00338D"
                        single_colors = {company: single_color}

                        try:
                            chart = build_capability_radar_chart(
                                one_company_scores,
                                axes_labels,
                                single_colors,
                                panel_width=panel_size,
                                panel_height=panel_size,
                                show_legend=False,
                                label_font_size=9,
                                median_color="#94A3B8",
                            )
                        except Exception as exc:
                            st.warning(f"⚠️ {company} 的雷达图渲染失败：{exc}")
                            import traceback
                            st.code(traceback.format_exc())
                            continue

                        st.altair_chart(
                            _chart_without_internal_title(chart),
                            width="content",
                            key=f"{key_prefix}_radar_{category}_{company}",
                        )

        # ---------- 明细展开 ----------
        with st.expander("查看原始数值与得分明细"):
            st.dataframe(
                raw_all.style.format(
                    {col: "{:,.2f}" for col in raw_all.columns if col != "公司"},
                    na_rep="—",
                ),
                width="stretch",
                hide_index=True,
            )
            st.dataframe(
                score_all.drop(columns=["_分类"]).style.format(
                    {col: "{:.0f}" for col in score_all.columns
                     if col not in {"公司", "_分类"}},
                    na_rep="0",
                ),
                width="stretch",
                hide_index=True,
            )
        return

    # ★ 新增：承保效益散点图
    elif plan.kind == UNDERWRITING_EFFICIENCY_SCATTER:
        render_underwriting_efficiency_scatter(
            frame,  # ← 直接把当前页面的 frame 传进去
            chart_config=(
                module_override.chart_config if module_override else {}
            ),
            key_prefix=key_prefix,
        )
        return

def _underwriting_efficiency_data(data: pd.DataFrame) -> pd.DataFrame:
    """从传入的 STEP7 数据中提取承保效益相关数据，并计算保费排名"""
    if data is None or data.empty:
        st.warning("传入 STEP7 的数据为空，无法绘制承保效益图。")
        return pd.DataFrame()

    df = data.copy()
    
    # 1. 深度清理：去除列名和单元格里的空格
    for col in ["公司", "指标编码", "报告期"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            
    # 2. 数值清理：去掉 % 和逗号，再转成数字
    df["数值"] = df["数值"].astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False)
    df["数值"] = pd.to_numeric(df["数值"], errors="coerce")

    # 3. 找出所有包含所需指标的报告期
    required_codes = ["COMBINED_RATIO", "LOSS_RATIO", "EXPENSE_RATIO", "SIGNED_PREMIUM", "PREMIUM_INCOME"]
    valid_periods = df[df["指标编码"].isin(required_codes)]["报告期"].unique().tolist()
    
    if not valid_periods:
        st.warning(f"当前 STEP7 数据中未找到任何所需指标（{required_codes}）。请检查 STEP5/STEP6 是否包含这些数据。")
        return pd.DataFrame()
        
    valid_periods.sort(reverse=True)
    latest_period = valid_periods[0]

    # 4. 计算保费排名（优先 SIGNED_PREMIUM，缺失则回退 PREMIUM_INCOME）
    premium_df = df[
        (df["报告期"] == latest_period) & 
        (df["指标编码"].isin(["SIGNED_PREMIUM", "PREMIUM_INCOME"]))
    ].drop_duplicates(subset=["公司"], keep="last").copy()
    
    if premium_df.empty:
        st.warning(f"报告期 {latest_period} 未找到 SIGNED_PREMIUM 或 PREMIUM_INCOME 数据。")
        return pd.DataFrame()

    premium_df = premium_df.sort_values("数值", ascending=False).reset_index(drop=True)
    premium_df["排名"] = range(1, len(premium_df) + 1)
    rank_map = premium_df.set_index("公司")["排名"].to_dict()

    # 5. 提取三个比率指标
    metrics = ["COMBINED_RATIO", "LOSS_RATIO", "EXPENSE_RATIO"]
    metric_df = df[
        (df["报告期"] == latest_period) & 
        (df["指标编码"].isin(metrics))
    ].drop_duplicates(subset=["公司", "指标编码"], keep="last").copy()

    if metric_df.empty:
        st.warning(f"报告期 {latest_period} 未找到 COMBINED_RATIO / LOSS_RATIO / EXPENSE_RATIO 数据。")
        return pd.DataFrame()

    # 6. 合并并计算排名
    metric_df["排名"] = metric_df["公司"].map(rank_map)
    metric_df = metric_df.dropna(subset=["排名", "数值"])
    
    if metric_df.empty:
        st.warning(f"公司名称无法匹配：保费数据中有 {len(rank_map)} 家公司，比率数据中有 {metric_df['公司'].nunique()} 家公司。")
        return pd.DataFrame()

    # 7. 计算各指标行业中位数（★ 只按"合理范围"内的值计算）
    #   ★ 修复：之前用 groupby().apply().values 会导致赋值错位——
    #     apply 返回的 Series 是按"分组顺序"排列的，而不是按原 DataFrame 行顺序，
    #     导致 LOSS_RATIO / EXPENSE_RATIO 拿到了 COMBINED_RATIO 的中位数（99.55），
    #     中位数线被画到 99.55 的位置（远超真实数据范围），撑大 y domain，
    #     把真实的 8 个点全部压扁到图表底部。
    #   改用"先算 median_map，再 .map()"的方式，保证一一对应，不会错位。
    _median_map: dict[str, float] = {}
    for _code_key, _group_df in metric_df.groupby("指标编码"):
        _code_str = str(_code_key)
        if _code_str == "COMBINED_RATIO":
            _lo, _hi = 40.0, 150.0
        else:
            _lo, _hi = 0.0, 150.0
        _valid = _group_df.loc[
            (_group_df["数值"] >= _lo) & (_group_df["数值"] <= _hi),
            "数值",
        ]
        if not _valid.empty:
            _median_map[_code_str] = float(_valid.median())
        else:
            _median_map[_code_str] = float(_group_df["数值"].median())

    metric_df["中位数"] = metric_df["指标编码"].astype(str).map(_median_map)

    return metric_df


def render_underwriting_efficiency_scatter(
    data: pd.DataFrame,
    chart_config: dict | None = None,
    key_prefix: str = ""
) -> None:
    """渲染承保效益散点图（综合成本率、赔付率、费用率）"""
    df = _underwriting_efficiency_data(data)
    if df.empty:
        st.info(
            "当前 STEP7 数据中缺少承保效益相关指标（COMBINED_RATIO/LOSS_RATIO/EXPENSE_RATIO）"
            "或保费规模指标（SIGNED_PREMIUM），无法绘制散点图。"
        )
        return

    latest_period = df["报告期"].iloc[0]
    
    label_map = {
        "COMBINED_RATIO": "综合成本率",
        "LOSS_RATIO": "综合赔付率",
        "EXPENSE_RATIO": "综合费用率"
    }

    for code in ["COMBINED_RATIO", "LOSS_RATIO", "EXPENSE_RATIO"]:
        sub_df = df[df["指标编码"] == code].copy()
        if sub_df.empty:
            continue

        label = label_map.get(code, code)
        median_val = sub_df["中位数"].iloc[0] # 中位数还是按全体样本算

        # ★ 改动：纵轴范围改为"自动适配 + 合理的异常值护栏"。
        #   思路：
        #   1) 先用一个宽的"合理性区间"筛掉明显异常值（异常值不进图，只进下方附表）；
        #   2) 剩下的正常值，用它们的 min/max 加上 8% padding 作为纵轴范围；
        #   3) 但纵轴不能小于一个最小可视跨度（防止所有点完全重合时纵轴过窄）；
        #   4) 综合成本率的下限不能低于 40（否则失去业务含义）。
        if code == "COMBINED_RATIO":
            OUTLIER_LOWER = 40.0      # 异常值下限（低于此视为异常）
            OUTLIER_UPPER = 150.0     # 异常值上限
            MIN_DOMAIN_LOWER = 40.0   # 纵轴最小下限
        else:
            OUTLIER_LOWER = 0.0
            OUTLIER_UPPER = 150.0
            MIN_DOMAIN_LOWER = 0.0

        # ---- 先用"合理性区间"筛掉异常值 ----
        normal_df = sub_df[
            (sub_df["数值"] >= OUTLIER_LOWER) &
            (sub_df["数值"] <= OUTLIER_UPPER)
        ].copy()
        outlier_df = sub_df[
            (sub_df["数值"] < OUTLIER_LOWER) |
            (sub_df["数值"] > OUTLIER_UPPER)
        ].copy()

        # ---- 用正常值计算纵轴范围 ----
        if normal_df.empty:
            st.warning(f"{latest_period} 的{label}没有落在合理范围内的数据点。")
            continue

        # ★ 计算最小/最大值时，去掉 NaN 和 inf
        _values = pd.to_numeric(normal_df["数值"], errors="coerce").dropna()
        _values = _values[_values.map(lambda v: math.isfinite(float(v)))]
        if _values.empty:
            st.warning(f"{latest_period} 的{label}没有有效数值。")
            continue

        _vmin = float(_values.min())
        _vmax = float(_values.max())

        # ★ 自动检测"小数形式"：如果所有值都 < 2.0，很可能是 0.75 这种小数
        #   需要 ×100 转换成 75.0。这能修复"赔付率/费用率"图空白的常见原因。
        _is_decimal_form = (_vmax < 2.0) and (_vmin >= 0)
        if _is_decimal_form:
            st.warning(
                f"⚠️ {label} 的数值看起来是小数形式（最大 {_vmax:.4f}），"
                "已自动 ×100 转换为百分比显示。请检查 STEP3 标准化是否有单位问题。"
            )
            _values = _values * 100.0
            normal_df = normal_df.copy()
            normal_df["数值"] = normal_df["数值"] * 100.0
            if pd.notna(median_val):
                median_val = median_val * 100.0
            _vmin = float(_values.min())
            _vmax = float(_values.max())

        _raw_span = _vmax - _vmin
        if _raw_span < 1e-6:
            _raw_span = 0.0

        # ★ 最小可视跨度
        if code == "COMBINED_RATIO":
            MIN_SPAN = 8.0
        else:
            MIN_SPAN = 5.0

        _has_real_variance = _raw_span > 0.3

        if _has_real_variance:
            _pad = max(_raw_span * 0.20, MIN_SPAN * 0.15)
            y_lower = _vmin - _pad
            y_upper = _vmax + _pad
            if y_upper - y_lower < MIN_SPAN:
                _center = (y_lower + y_upper) / 2
                y_lower = _center - MIN_SPAN / 2
                y_upper = _center + MIN_SPAN / 2
            y_lower = max(MIN_DOMAIN_LOWER, y_lower)
        else:
            _center = (_vmin + _vmax) / 2
            y_lower = max(MIN_DOMAIN_LOWER, _center - MIN_SPAN / 2)
            y_upper = _center + MIN_SPAN / 2

        # ★ 稳妥性检查：保证 domain 是有效有限数
        if not (math.isfinite(y_lower) and math.isfinite(y_upper)):
            y_lower, y_upper = 0.0, 100.0
        if y_upper <= y_lower:
            y_upper = y_lower + MIN_SPAN

        # ★ 动态提示：根据是否有真实差异给不同说明
        _variance_hint = (
            "" if _has_real_variance
            else "  ⚠️ 注意：本报告期内所有公司的该比率几乎相同（差异 < 0.3 个百分点），"
                 "因此图上的点会聚集在同一条水平线上——这本身是一个值得关注的行业现象。"
        )

        st.caption(
            f"展示 {latest_period} 行业数据 ｜ 数据源：STEP7 当前数据 ｜ "
            f"X轴为保费收入规模排名，Y轴为对应比率"
            f"（已剔除偏离正常范围 [{OUTLIER_LOWER}%, {OUTLIER_UPPER}%] 的异常值；"
            f"纵轴根据数据自动适配）。灰色虚线为行业中位数。"
            f"{_variance_hint}"
        )

        # 画图用的点（只包含正常值）
        points = (
            alt.Chart(normal_df)
            .mark_circle(
                size=90,                 # ★ 稍大一点
                color="#A52A2A",
                opacity=0.9,
                stroke="#FFFFFF",        # ★ 白色描边
                strokeWidth=1.2,         # ★ 让点更醒目
            )
            .encode(
                x=alt.X(
                    "排名:Q", 
                    title="保费收入排名",
                    scale=alt.Scale(zero=False, padding=10),
                    axis=alt.Axis(tickMinStep=1)
                ),
                y=alt.Y(
                    "数值:Q", 
                    title=f"{label}（%）",
                    scale=alt.Scale(
                        domain=[y_lower, y_upper],
                        zero=False,
                        padding=10,
                        nice=False,
                    ),
                    axis=alt.Axis(
                        format=".1f",
                        tickCount=6,
                    ),
                ),
                tooltip=[
                    "公司:N",
                    alt.Tooltip("排名:Q", title="保费排名"),
                    alt.Tooltip("数值:Q", title=label, format=".2f")
                ]
            )
        )

        # ★ 防御性检查：如果中位数不在 y domain 范围内，
        #   说明中位数计算有误（或数据本身异常），不画中位数线，
        #   避免中位数线把 domain 撑大导致真实数据点被压扁。
        _draw_median = (
            pd.notna(median_val)
            and math.isfinite(float(median_val))
            and (y_lower - 1e-6) <= float(median_val) <= (y_upper + 1e-6)
        )

        if _draw_median:
            median_line = (
                alt.Chart(pd.DataFrame({"中位数": [float(median_val)]}))
                .mark_rule(color="#888888", strokeDash=[5, 5], strokeWidth=1.5)
                .encode(y="中位数:Q")
            )
            chart_layers = points + median_line
        else:
            st.caption(
                f"⚠️ {label} 的中位数（{median_val:.2f}）超出图表纵轴范围 "
                f"[{y_lower:.2f}, {y_upper:.2f}]，已隐藏中位数线。"
            )
            chart_layers = points

        chart = (
            chart_layers
            .properties(
                title=alt.TitleParams(
                    text=f"{latest_period} 财险公司{label}分布",
                    anchor="middle",
                    fontSize=14,
                    color="#333333"
                ),
                width=700,
                height=300
            )
            .configure_view(stroke=None)
        )

        st.altair_chart(chart, use_container_width=True)
        
        if not outlier_df.empty:
            with st.expander(
                f"⚠️ 查看 {label} 偏离正常范围的异常值（{len(outlier_df)} 家）",
                expanded=False,
            ):
                st.caption(
                    f"以下公司的{label}偏离了正常范围 "
                    f"[{OUTLIER_LOWER}%, {OUTLIER_UPPER}%]，"
                    "未在图表中显示，请关注其数据质量或业务异常："
                )
                st.dataframe(
                    outlier_df[["公司", "数值"]]
                    .sort_values("数值", ascending=False)
                    .rename(columns={"数值": f"{label}（%）"}),
                    hide_index=True,
                    use_container_width=True,
                )
        else:
            st.caption(f"✅ 当前 {label} 无异常值。")

        st.markdown("<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True)


def show_step_7_solvency(
    data: pd.DataFrame,
    financing_data: pd.DataFrame | None = None,
    step7_modules: list[Step7Module] | None = None,   # ★ 新增
) -> None:
    """Company report page following the annual-platform report workflow.

    图表清单、顺序、类型、指标编码全部来自 Step7 Excel 注释表（step7_modules）；
    未提供时回退到 COMPANY_NAVIGATION 的内置配置。
    """
    _report_style()
    st.markdown("<div class='solvency-report-title'>公司级偿付能力对标报告</div>", unsafe_allow_html=True)
    if data is None or data.empty:
        st.info("请先在 Step5 确认集成数据，或在 Step6 上传集成表。")
        return

    # ★ 从 session_state 兜底读取 Excel 模块列表
    if step7_modules is None:
        step7_modules = _step7_modules_from_state()
    
    frame = company_detail_rows(data).copy()
    if frame.empty:
        st.info("当前数据没有公司明细记录。")
        return

    # ★ 修复（新增）：公司名称归一化。
    #   数据源里同一家公司会以两种名字出现，例如：
    #       "平安产险"         —— 覆盖保费、承保、利润等经营类指标
    #       "平安产险（偿付能力）" —— 覆盖充足率、实际资本、最低资本等指标
    #   如果不去掉"（偿付能力）"尾缀，Step7 就会把同一家公司当成两家，
    #   导致公司筛选器显示 13 家（8 + 5 个带后缀的重复项）。
    frame["公司"] = (
        frame["公司"].astype(str)
        .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
        .str.strip()
    )

    frame["数值"] = pd.to_numeric(frame["数值"], errors="coerce")
    frame = frame.dropna(subset=["数值"])

    # ★ 修复（新增）：归一化后，同一公司/指标/报告期/期间口径可能出现重复行
    #   （一个来源叫"平安产险"，另一个来源叫"平安产险（偿付能力）"，合并后同粒度出现两行）。
    #   这里按"后出现优先"去重，避免图表出现重复点。
    #   注意：不同"指标编码"不会被误删——保费、充足率等指标编码不同，都会保留。
    _dedup_keys = [
        _key for _key in ("公司", "指标编码", "报告期", "期间口径")
        if _key in frame.columns
    ]
    if _dedup_keys:
        frame = frame.drop_duplicates(subset=_dedup_keys, keep="last")

    all_periods = sort_report_periods(frame["报告期"])
    if not all_periods:
        st.info("当前数据没有可用报告期。")
        return


    notes = render_report_notes_editor(
        title="公司内容分析与注释输入",
        key_prefix="step7",
        template=company_notes_template(),
    )

    # ★ 优先用 Excel 模块列表；没有则回退到 COMPANY_NAVIGATION
    if step7_modules:
        available_chart_names = [m.chart_name for m in step7_modules]
    else:
        available_chart_names = list(dict.fromkeys(
            entry.chart_name for entry in COMPANY_NAVIGATION
        ))
    image_overrides: dict[str, dict[str, object]] = {}
    with st.expander("公司级图表设置与图片覆盖", expanded=False, icon=":material/tune:"):
        c0, c1, c2, c3, c4 = st.columns([1, 2, 1, 1, 1])
        peer_groups = nonblank_values(frame, "同业分类")
        type_options = [ALL_COMPANY_TYPES, *peer_groups]
        st.session_state.setdefault("s7_company_types", [ALL_COMPANY_TYPES])
        _valid_state("s7_company_types", type_options, multiple=True)
        with c0:
            selected_types_raw = st.multiselect(
                "公司类型",
                type_options,
                key="s7_company_types",
                on_change=_reset_company_selection_for_types,
                args=(frame,),
                help="沿用偿付能力平台的‘同业分类’字段进行筛选。",
            )
        company_source, all_companies = _company_scope_for_types(
            frame,
            selected_types_raw,
        )
        company_selection_key = _sync_company_selection_state(
            selected_types_raw,
            all_companies,
        )
        with c1:
            selected_companies = st.multiselect(
                "展示公司",
                all_companies,
                key=company_selection_key,
                placeholder="选择一家或多家公司",
            )
        with c2:
            unit_options = ["十亿元", "亿元", "百万元", "十万元"]   
            _valid_state("s7_unit_mode", unit_options)
            unit_mode = st.selectbox(
                "显示单位",
                unit_options,
                key="s7_unit_mode",
            )
        with c3:
            highlight_options = ["无", *selected_companies]
            _valid_state("s7_highlight_company", highlight_options)
            highlight_company = st.selectbox(
                "特定追踪",
                highlight_options,
                key="s7_highlight_company",
            )
        with c4:
            enable_ai = st.toggle(
                "一键AI分析",
                value=False,
                key="s7_enable_ai",
                help="使用登录页配置的大模型接口，为当前图表生成一句同业对标点评。",
            )
            ai_data_consent = False
            if enable_ai:
                ai_data_consent = st.checkbox(
                    "确认将当前图表中的公司名称和指标数据发送至已配置的大模型服务",
                    value=False,
                    key="s7_ai_data_consent",
                )
                if not ai_data_consent:
                    st.caption("未确认前不会调用模型接口或发送数据。")

        st.divider()
        sort_options, sort_lookup = _metric_sort_options(company_source)
        _valid_state("s7_sort_field", sort_options)
        sc1, sc2, sc3 = st.columns([2, 2, 3])
        with sc1:
            sort_field = st.selectbox(
                "图表展示顺序依据",
                sort_options,
                key="s7_sort_field",
            )
        with sc2:
            sort_order = st.radio(
                "排序方向",
                [SORT_DESCENDING, SORT_ASCENDING],
                horizontal=True,
                key="s7_sort_order",
            )
        with sc3:
            st.caption("图表类型：按指标特征自动匹配（系统内置）")
        selected_companies = _sort_companies_by_metric(
            company_source,
            selected_companies,
            sort_lookup.get(sort_field, ""),
            all_periods[-1],
            descending=sort_order == SORT_DESCENDING,
        )

        st.markdown("#### 手动上传图片（PNG/JPG）")
        image_overrides = _render_image_override_controls(available_chart_names, notes)

    selected_periods = all_periods
    show_labels = True
    decimals = 2
    transparent = True
    show_average = False
    if not selected_companies:
        st.info("请在报告配置中至少选择一家展示公司。")
        return
    scoped = filter_analysis_frame(frame, companies=selected_companies, periods=selected_periods)
    scoped["_s7_company_order"] = pd.Categorical(
        scoped["公司"], categories=selected_companies, ordered=True
    )
    scoped = scoped.sort_values(["_s7_company_order", "报告期"]).drop(columns="_s7_company_order")

    chart_names = _selected_chart_names(scoped, modules=step7_modules)
    if not chart_names:
        st.info("请在侧边栏公司报告导航中选择具体图表。")
        return
    print_all = st.session_state.get("company_nav_level_one") == PRINT_ALL_LABEL
    if print_all:
        # ... 渲染封面（这段保持不变）...
        today = date.today()
        period_label = (
            selected_periods[0]
            if len(selected_periods) == 1
            else f"{selected_periods[0]}–{selected_periods[-1]}"
        )
        company_label = (
            "、".join(selected_companies)
            if len(selected_companies) <= 4
            else f"{len(selected_companies)} 家公司"
        )
        render_report_cover(
            title="保险公司偿付能力公司对标报告",
            subtitle=f"{period_label} · {company_label}",
            date_text=f"{today.year}年{today.month}月",
            picture_dir=PICTURE_DIR,
        )

    for chart_index, chart_name in enumerate(chart_names):
        if print_all and chart_index:
            st.markdown("<div class='solvency-report-page-break'></div>", unsafe_allow_html=True)

        # ★ 先尝试从 Excel 模块里找元数据
        module = _module_by_chart_name(step7_modules, chart_name) if step7_modules else None

        # ★ 兼容没有 Excel 的情形：沿用 COMPANY_NAVIGATION
        if module is None:
            entries = [entry for entry in COMPANY_NAVIGATION if entry.chart_name == chart_name]
            level_one = entries[0].level_one if entries else ""
            level_two = entries[0].level_two if entries else ""
            module_chart_kind = None
            module_metric_codes: tuple[str, ...] = ()
            module_requires_all = entries[0].requires_all if entries else False
            module_entry_metric_codes = entries[0].metric_codes if entries else ()
        else:
            level_one = module.level_one
            level_two = module.level_two
            module_chart_kind = module.chart_kind
            module_metric_codes = module.metric_codes
            module_requires_all = module.requires_all   # ★ 改用 Excel 的值
            module_entry_metric_codes = module.metric_codes

        # 标题：Excel 有二级就拼上，没有就省掉
        title_parts = [p for p in (level_one, level_two, chart_name) if p]
        title_text = " - ".join(title_parts)

        with st.container(key=f"s7_report_module_{chart_index}"):
            st.markdown(
                f"<div class='solvency-module-title'>{title_text}</div>",
                unsafe_allow_html=True,
            )

            # 分析/注释：优先用 Excel 的
            chart_note = notes.get(chart_name, {})
            render_report_analysis(chart_note)

            override = image_overrides.get(chart_name)
            if override:
                st.image(
                    override["data"],
                    caption=f"图片覆盖：{override['name']}",
                    width="stretch",
                )
            else:
                # ★ 决定图表类型：Excel 优先，其次回退到 chart_plan_for
                CHART_KIND_ALIASES = {
                    "能力五边形": CAPABILITY_RADAR,
                    "能力雷达图": CAPABILITY_RADAR,
                    "承保效益散点图": UNDERWRITING_EFFICIENCY_SCATTER,  # ★ 新增
                    "结构变化组合图": STRUCTURE_CHANGE_CHART,  # ★ 新增
                    "业绩模式分列图": PERFORMANCE_PATTERN_GRID,  # ★ 新增
                    "公司业绩轨迹图": PERFORMANCE_PATTERN_GRID,  # ★ 新增（兼容旧写法）
                }
                if module_chart_kind:
                    plan_kind = CHART_KIND_ALIASES.get(module_chart_kind, module_chart_kind)
                else:
                    plan_kind = chart_plan_for(chart_name).kind

                available_codes = set(scoped["指标编码"].fillna("").astype(str))
                missing_codes = [c for c in module_entry_metric_codes if c not in available_codes]

                if module_requires_all and missing_codes:
                    st.warning(
                        f"“{chart_name}”缺少 {len(missing_codes)} 个必要指标，暂无法按 Excel 指定格式完整绘图。"
                    )
                    st.caption("缺失指标编码：" + "、".join(missing_codes))
                elif plan_kind in {SINGLE_METRIC_TREND, TREND_WITH_COMPANY_BARS, COMPANY_BAR_TREND}:
                    # 指标编码：Excel 优先
                    codes_for_this_chart = (
                        module_metric_codes
                        or tuple(metric_codes_for_chart(chart_name))
                    )
                    for code in codes_for_this_chart:
                        _render_report_metric(
                            scoped,
                            code,
                            periods=selected_periods,
                            show_labels=show_labels,
                            decimals=decimals,
                            unit_mode=unit_mode,
                            transparent=transparent,
                            show_average=show_average,
                            highlight_company=highlight_company,
                            enable_ai=enable_ai and ai_data_consent,
                            key_prefix=f"s7_{chart_index}",
                            company_panels=(plan_kind == COMPANY_BAR_TREND),
                            company_period_bars=(plan_kind == TREND_WITH_COMPANY_BARS),
                        )
                else:
                    _render_combination_analysis(
                        scoped,
                        chart_name,
                        periods=selected_periods,
                        unit_mode=unit_mode,
                        highlight_company=highlight_company,
                        key_prefix=f"s7_{chart_index}",
                        financing_data=financing_data,
                        module_override=module,      # ★ 新增
                    )
            render_report_footnote(chart_note)

    if print_all:
        render_report_back_cover(picture_dir=PICTURE_DIR)

    with st.expander("查看报告底层数据"):
        st.dataframe(scoped, width="stretch", hide_index=True)
