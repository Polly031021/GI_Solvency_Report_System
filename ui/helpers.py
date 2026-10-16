"""与业务无关的通用小工具：列名清洗、页码解析、控件状态维护等。"""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st


def dedupe_column_names(columns) -> list[str]:
    """空列名补“列N”，重复列名追加 _2/_3…。"""
    seen: dict[str, int] = {}
    names: list[str] = []
    for index, column in enumerate(columns):
        name = str(column).strip() or f"列{index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        names.append(name)
    return names


def parse_page_numbers(raw_value: str, total_pages: int) -> tuple[list[int], list[str]]:
    """解析逗号、分号或空格分隔的物理页码。"""
    valid_pages: list[int] = []
    invalid_values: list[str] = []
    for token in re.split(r"[,，;；\s]+", str(raw_value).strip()):
        if not token:
            continue
        if not token.isdigit():
            invalid_values.append(token)
            continue
        page_number = int(token)
        if page_number == 0:
            continue
        if 1 <= page_number <= total_pages:
            if page_number not in valid_pages:
                valid_pages.append(page_number)
        else:
            invalid_values.append(token)
    return valid_pages, invalid_values


def keep_valid_widget_state(
    key: str,
    options: list[str],
    *,
    multiple: bool = False,
) -> None:
    """Discard stale selections when upstream Step6 filters change."""
    if key not in st.session_state:
        return
    if multiple:
        current = st.session_state.get(key) or []
        st.session_state[key] = [value for value in current if value in options]
    elif st.session_state.get(key) not in options:
        del st.session_state[key]


def navigation_metric_codes(frame: pd.DataFrame) -> list[str]:
    if "指标编码" not in frame.columns:
        return []
    return frame["指标编码"].dropna().astype(str).str.strip().tolist()


def finalize_company_frame(
    frame: pd.DataFrame,
    rename_map: dict[str, str] | None = None,
    *,
    fill_missing: bool = True,
) -> pd.DataFrame:
    """统一公司清单列名，清理空值并按公司名去重。"""
    companies = frame.rename(columns=rename_map) if rename_map else frame
    for column in ("公司", "公司类别", "报告披露地址"):
        series = companies[column]
        if fill_missing:
            series = series.fillna("")
        companies[column] = series.astype(str).str.strip()
    return (
        companies[companies["公司"] != ""]
        .drop_duplicates(subset=["公司"], keep="last")
        .reset_index(drop=True)
    )


def target_period_terms(
    year: int,
    period: str,
    frequency: str = "QUARTERLY",
) -> list[str]:
    if frequency.upper() == "ANNUAL":
        return [
            f"{year}年度",
            f"{year}年年度报告",
            f"{year}年年报",
            f"annualreport{year}",
        ]
    quarter = period
    quarter_number = int(quarter[-1])
    chinese_number = {1: "一", 2: "二", 3: "三", 4: "四"}[quarter_number]
    return [
        f"{year}{quarter}",
        f"{year}年第{quarter_number}季度",
        f"{year}年第{chinese_number}季度",
        f"{year}年{quarter_number}季度",
        f"{year}年{chinese_number}季度",
    ]
