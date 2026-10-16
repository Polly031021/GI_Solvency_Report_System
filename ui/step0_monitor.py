"""STEP0 报告监控：公司清单、单家检查与批量并发检查。"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from services.report_profiles import ReportProfile
from services.solvency_pdf_locator import extract_report_metadata

from .cache import dataframe_to_xlsx
from .helpers import finalize_company_frame, target_period_terms


def _load_monitor_companies() -> pd.DataFrame:
    """读取本地财险公司清单并规范列名。"""
    try:
        companies = pd.read_excel("财险公司列表.xlsx")
    except Exception:
        companies = pd.DataFrame(columns=["公司", "公司类别", "报告披露地址"])
        st.warning("未找到 财险公司列表.xlsx，STEP0 公司清单为空。")

    # 兼容“公司名称 / 披露地址”等历史列名；其余清理逻辑与其他入口共用
    return finalize_company_frame(
        companies,
        {
            "公司名称": "公司",
            "公司类别": "公司类别",
            "披露地址": "报告披露地址",
        },
        fill_missing=False,
    )


def _render_monitor_single_company(
    active_profile: ReportProfile,
    filtered_companies: pd.DataFrame,
    target_year: int,
    target_period: str,
) -> None:
    """STEP0：逐个公司打开披露页面并检查是否更新。"""
    st.markdown("#### 逐个查看")
    if filtered_companies.empty:
        st.warning("当前类别筛选下没有公司。")
    else:
        selected_company = st.selectbox(
            "选择需要检查的公司",
            filtered_companies["公司"].tolist(),
            key="monitor_selected_company",
        )
        selected_row = filtered_companies[filtered_companies["公司"] == selected_company].iloc[0]

        link_col, check_col = st.columns(2)
        link_col.link_button(
            "打开该公司披露页面",
            selected_row["报告披露地址"],
            width="stretch",
        )
        if check_col.button(
            "检查该公司是否更新",
            key="monitor_one",
            type="primary",
            width="stretch",
        ):
            with st.spinner(f"正在检查 {selected_company}..."):
                st.session_state.monitor_single_result = check_company_report(
                    selected_row,
                    target_year,
                    target_period,
                    frequency=active_profile.frequency,
                    report_name=active_profile.profile_name,
                    report_terms=tuple(active_profile.monitoring.get("report_terms", [])),
                )

        if st.session_state.monitor_single_result:
            single_result = st.session_state.monitor_single_result
            status = single_result["检查结果"]
            if status == "已更新":
                st.success(f"{single_result['公司']}：{status}")
            elif "失败" in status or "异常" in status:
                st.error(f"{single_result['公司']}：{status}")
            else:
                st.warning(f"{single_result['公司']}：{status}")
            st.dataframe(
                pd.DataFrame([single_result]),
                width="stretch",
                hide_index=True,
                column_config={
                    "披露地址": st.column_config.LinkColumn(
                        "披露地址",
                        display_text="打开页面",
                    )
                },
            )


def _render_monitor_batch_check(
    active_profile: ReportProfile,
    filtered_companies: pd.DataFrame,
    target_year: int,
    target_period: str,
) -> None:
    """STEP0：并发检查当前筛选范围内的全部公司。"""
    st.markdown("#### 所有公司检查")
    with st.expander("批量检查设置"):
        worker_count = st.slider(
            "并发检查数量",
            min_value=1,
            max_value=8,
            value=4,
            help="数量越大速度越快，但部分保险公司网站可能触发访问限制。",
        )
        request_timeout = st.slider(
            "单个网站读取超时（秒）",
            min_value=5,
            max_value=30,
            value=15,
        )

    if st.button(
        f"检查当前筛选的全部 {len(filtered_companies)} 家公司",
        key="monitor_all",
        disabled=filtered_companies.empty,
        width="stretch",
    ):
        progress = st.progress(0, text="正在准备批量检查...")
        result_rows = []
        records = list(filtered_companies.iterrows())

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(
                    check_company_report,
                    row,
                    target_year,
                    target_period,
                    frequency=active_profile.frequency,
                    report_name=active_profile.profile_name,
                    report_terms=tuple(active_profile.monitoring.get("report_terms", [])),
                    timeout=request_timeout,
                ): order
                for order, (_, row) in enumerate(records)
            }
            for completed, future in enumerate(as_completed(future_map), start=1):
                result = future.result()
                result["_排序"] = future_map[future]
                result_rows.append(result)
                progress.progress(
                    completed / len(future_map),
                    text=f"已检查 {completed}/{len(future_map)}：{result['公司']}",
                )

        st.session_state.monitor_results = (
            pd.DataFrame(result_rows)
            .sort_values("_排序")
            .drop(columns="_排序")
            .reset_index(drop=True)
        )
        progress.empty()

    if not st.session_state.monitor_results.empty:
        results = st.session_state.monitor_results
        summary = results["检查结果"].value_counts()
        summary_columns = st.columns(min(len(summary), 4))
        for index, (status, count) in enumerate(summary.items()):
            summary_columns[index % len(summary_columns)].metric(status, int(count))

        result_filter = st.multiselect(
            "筛选检查结果",
            results["检查结果"].drop_duplicates().tolist(),
            default=results["检查结果"].drop_duplicates().tolist(),
            key="monitor_result_filter",
        )
        result_view = results[results["检查结果"].isin(result_filter)]
        st.dataframe(
            result_view,
            width="stretch",
            hide_index=True,
            column_config={
                "披露地址": st.column_config.LinkColumn(
                    "披露地址",
                    display_text="打开页面",
                )
            },
        )
        st.download_button(
            "下载本次检查结果",
            dataframe_to_xlsx(results, "报告更新检查"),
            f"{active_profile.profile_id}_报告更新检查_"
            f"{target_year}{'' if target_period == 'ANNUAL' else target_period}.xlsx",
            width="stretch",
        )


def render_monitor_tab(active_profile: ReportProfile) -> pd.DataFrame:
    """STEP0 报告监控；返回公司清单，供 STEP5 映射公司类型。"""
    st.subheader(f"{active_profile.profile_name}监控")
    companies = _load_monitor_companies()
    year_col, quarter_col = st.columns(2)
    target_year = int(year_col.number_input("报告年度", 2020, 2050, 2026))
    if active_profile.frequency.upper() == "ANNUAL":
        target_period = "ANNUAL"
        quarter_col.text_input("报告期间", value="年度", disabled=True)
    else:
        target_period = quarter_col.selectbox(
            "报告季度",
            ["Q1", "Q2", "Q3", "Q4"],
        )

    all_categories = sorted(companies["公司类别"].dropna().unique().tolist())
    selected_categories = st.multiselect(
        "公司类别",
        all_categories,
        default=all_categories,
        key="monitor_company_categories",
    )
    filtered_companies = companies[companies["公司类别"].isin(selected_categories)].reset_index(
        drop=True
    )

    st.caption(
        f"系统公司范围共 {len(companies)} 家；当前筛选 {len(filtered_companies)} 家。"
        f"运行时使用 {active_profile.profile_id} 的公司来源配置。"
    )
    st.dataframe(
        filtered_companies,
        width="stretch",
        hide_index=True,
        column_config={
            "报告披露地址": st.column_config.LinkColumn(
                "报告披露地址",
                display_text="打开页面",
            )
        },
    )
    _render_monitor_single_company(active_profile, filtered_companies, target_year, target_period)
    _render_monitor_batch_check(active_profile, filtered_companies, target_year, target_period)
    return companies


def check_company_report(
    row: pd.Series,
    year: int,
    period: str,
    *,
    frequency: str = "QUARTERLY",
    report_name: str = "目标报告",
    report_terms: tuple[str, ...] = (),
    timeout: int = 15,
) -> dict:
    company = str(row.get("公司", "")).strip()
    category = str(row.get("公司类别", "")).strip()
    url = str(row.get("报告披露地址", "")).strip()
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target_period = str(year) if frequency.upper() == "ANNUAL" else f"{year}{period}"
    base = {
        "公司": company,
        "公司类别": category,
        "报告类型": report_name,
        "目标报告期": target_period,
        "检查结果": "",
        "HTTP状态": "",
        "匹配关键词": "",
        "披露地址": url,
        "检查时间": checked_at,
        "说明": "",
    }
    if not url.lower().startswith(("http://", "https://")):
        return {**base, "检查结果": "未配置有效链接", "说明": "请维护系统公司网址配置。"}

    try:
        response = requests.get(
            url,
            timeout=(5, timeout),
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
        )
        base["HTTP状态"] = response.status_code
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "application/pdf" in content_type or response.content[:4] == b"%PDF":
            metadata = extract_report_metadata(response.content)
            matched = metadata.get("报告年度") == year
            if frequency.upper() != "ANNUAL":
                matched = matched and metadata.get("报告季度") == period
            return {
                **base,
                "检查结果": "已更新" if matched else "PDF报告期不匹配",
                "匹配关键词": str(metadata.get("报告期", "")),
                "说明": "链接直接返回PDF，已按PDF首页报告期判断。",
            }

        soup = BeautifulSoup(response.text, "html.parser")
        visible_text = soup.get_text(" ", strip=True)
        link_text = " ".join(
            f"{tag.get_text(' ', strip=True)} {tag.get('href', '')}" for tag in soup.find_all("a")
        )
        searchable = re.sub(r"\s+", "", f"{visible_text} {link_text}")
        terms = target_period_terms(year, period, frequency)
        matched_terms = [term for term in terms if term.lower() in searchable.lower()]
        normalized_report_terms = tuple(item for item in report_terms if str(item).strip())
        has_report_term = (
            any(str(term).lower() in searchable.lower() for term in normalized_report_terms)
            if normalized_report_terms
            else True
        )

        if matched_terms and has_report_term:
            result = "已更新"
            note = "页面中同时发现目标报告期和报告类型关键词。"
        elif matched_terms:
            result = "疑似已更新，需人工核对"
            note = "发现目标报告期，但未在静态页面文本中发现报告类型关键词。"
        else:
            result = "未发现目标报告"
            note = "静态页面未发现目标报告期；动态加载页面或反爬虫网站需人工打开核对。"
        return {
            **base,
            "检查结果": result,
            "匹配关键词": "、".join(matched_terms),
            "说明": note,
        }
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "")
        return {
            **base,
            "HTTP状态": status,
            "检查结果": "访问失败，需人工核对",
            "说明": str(exc)[:300],
        }
    except Exception as exc:
        return {**base, "检查结果": "检查异常，需人工核对", "说明": str(exc)[:300]}
