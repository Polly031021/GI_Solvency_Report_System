"""公司/指标筛选与图表选择：控件状态维护、排序、配色映射与模块解析。"""

from __future__ import annotations

from collections.abc import Iterable

import colorsys
import pandas as pd
import streamlit as st
from services.solvency_navigation import COMPANY_NAVIGATION
from services.solvency_navigation import KPMG_CHART_COLORS
from services.solvency_navigation import PRINT_ALL_LABEL
from services.solvency_navigation import resolve_chart_selection
from services.step7_excel_config import Step7Module

from .specs import (
    ALL_COMPANY_TYPES,
    BUBBLE_DISTINCT_COLORS,
    DEFAULT_SORT_LABEL,
    TRACKED_COMPANY_COLOR,
)


def valid_state(key: str, options: Iterable[str], *, multiple: bool = False) -> None:
    values = list(options)
    if key not in st.session_state:
        return
    if multiple:
        st.session_state[key] = [value for value in st.session_state[key] if value in values]
    elif st.session_state[key] not in values:
        st.session_state.pop(key, None)


def metric_title(frame: pd.DataFrame, code: str) -> str:
    rows = frame[frame["指标编码"].astype(str).eq(code)]
    return code if rows.empty else str(rows.iloc[0]["指标名称"])


def metric_sort_options(frame: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
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


def sort_companies_by_metric(
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


def company_color_map(companies: Iterable[str], highlight_company: str = "无") -> dict[str, str]:
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
    colors = {company: palette[index % len(palette)] for index, company in enumerate(ordered)}
    if has_highlight:
        colors[highlight] = TRACKED_COMPANY_COLOR
    return colors


def bubble_company_color_map(
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


def ordered_companies(frame: pd.DataFrame) -> list[str]:
    """Keep companies in the integrated table's original display order."""
    if frame.empty or "公司" not in frame.columns:
        return []
    return list(
        dict.fromkeys(value for value in frame["公司"].fillna("").astype(str).str.strip() if value)
    )


def company_scope_for_types(
    frame: pd.DataFrame,
    selected_types: Iterable[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Return the exact company scope implied by the company-type widget."""
    selected = [str(value).strip() for value in selected_types if str(value).strip()]
    specific_types = [value for value in selected if value != ALL_COMPANY_TYPES]
    if not specific_types:
        company_source = frame
    else:
        company_source = frame[
            frame["同业分类"].fillna("").astype(str).str.strip().isin(specific_types)
        ].copy()
    return company_source, ordered_companies(company_source)


def sync_company_selection_state(
    selected_types: Iterable[str],
    available_companies: Iterable[str],
    *,
    signature_key: str = "_s7_company_scope_signature",
) -> str:
    """Return a fresh downstream widget key whenever its upstream scope changes."""
    companies = list(
        dict.fromkeys(
            str(company).strip() for company in available_companies if str(company).strip()
        )
    )
    selected = tuple(
        sorted(
            {
                str(value).strip()
                for value in selected_types
                if str(value).strip() and str(value).strip() != ALL_COMPANY_TYPES
            }
        )
    )
    signature = (selected or (ALL_COMPANY_TYPES,), tuple(companies))
    revision_key = "_s7_company_selection_revision"
    st.session_state.setdefault(revision_key, 0)
    if st.session_state.get(signature_key) != signature:
        if signature_key in st.session_state:
            st.session_state[revision_key] += 1
        st.session_state[signature_key] = signature
    selection_key = f"s7_companies_scope_v3_{st.session_state[revision_key]}"
    current = st.session_state.get(selection_key, companies)
    st.session_state[selection_key] = [company for company in current if company in companies]
    return selection_key


def reset_company_selection_for_types(frame: pd.DataFrame) -> None:
    """Apply a company-type change before Streamlit redraws downstream widgets."""
    selected_types = st.session_state.get("s7_company_types", [ALL_COMPANY_TYPES])
    _, companies = company_scope_for_types(frame, selected_types)
    selected = tuple(
        sorted(
            {
                str(value).strip()
                for value in selected_types
                if str(value).strip() and str(value).strip() != ALL_COMPANY_TYPES
            }
        )
    )
    revision_key = "_s7_company_selection_revision"
    st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
    selection_key = f"s7_companies_scope_v3_{st.session_state[revision_key]}"
    st.session_state[selection_key] = list(companies)
    st.session_state["_s7_company_scope_signature"] = (
        selected or (ALL_COMPANY_TYPES,),
        tuple(companies),
    )


def step7_modules_from_state() -> list[Step7Module]:
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


def selected_chart_names(
    frame: pd.DataFrame, modules: list[Step7Module] | None = None
) -> list[str]:
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


def module_by_chart_name(modules: list[Step7Module], chart_name: str) -> Step7Module | None:
    for m in modules:
        if m.chart_name == chart_name:
            return m
    return None
