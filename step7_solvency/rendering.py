"""单指标图表的通用渲染：单位换算、图例、公司面板网格与 AI 点评挂载。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from html import escape

import altair as alt
import pandas as pd
import streamlit as st
from services.solvency_step6_analysis import (
    filter_analysis_frame,
    format_chart_value,
    sort_report_periods,
)
from services.solvency_step7_charts import (
    annual_period_order,
    build_company_bar_trend_chart,
    build_company_period_bar_chart,
    build_single_metric_trend_charts,
    insurance_risk_liability_pressure_sentence,
    convert_multiple_units_to_percent,
    metric_bar_axis_domain,
    report_period_color_map,
    display_period_label,
)

from services.report_i18n import (
    is_english,
    localize_chart,
    t,
    translate_text,
)

from .ai_analysis import ai_analysis_for_latest_period
from .selection import company_color_map, metric_title, ordered_companies


def render_image_override_controls(
    chart_names: list[str],
    notes: dict[str, dict[str, str]],
) -> dict[str, dict[str, object]]:
    overrides = st.session_state.setdefault("s7_image_overrides", {})
    st.caption(
        t(
            "手动上传图片（PNG/JPG）；选择对应图表后，图片将替代系统生成图表进入网页和打印报告。",
            "Upload chart images (PNG/JPG). Once mapped to a chart, the image replaces the generated chart on screen and in the printed report.",
        )
    )
    uploads = st.file_uploader(
        t("上传图表截图", "Upload chart image"),
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
                t(f"{upload.name} 对应图表", f"Chart for {upload.name}"),
                ["不覆盖/跳过", *chart_names],
                key=state_key,
                format_func=lambda value: (
                    t("不覆盖/跳过", "Do not override / skip")
                    if value == "不覆盖/跳过"
                    else translate_text(value)
                ),
            )
        with c2:
            st.image(upload, width="stretch")
        if target != "不覆盖/跳过":
            overrides[target] = {
                "name": upload.name,
                "data": upload.getvalue(),
            }
    if overrides:
        st.caption(
            t("当前已覆盖：", "Current overrides: ")
            + t("、", ", ").join(translate_text(name) for name in overrides)
        )
        disabled = st.multiselect(
            t("本次暂不使用的图片覆盖", "Image overrides to disable"),
            list(overrides),
            key="s7_disabled_image_overrides",
            placeholder=t(
                "如需恢复系统图表，可在此选择",
                "Select to restore the generated chart",
            ),
            format_func=translate_text,
        )
        return {name: value for name, value in overrides.items() if name not in disabled}
    return {}


def convert_unit(frame: pd.DataFrame, target: str) -> pd.DataFrame:
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
        numeric_values.loc[amount_mask] * source_scales.loc[amount_mask] / target_yuan
    )
    result.loc[amount_mask, "单位"] = target
    return result


def prepare_annual_metric_frame(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Keep one observation per year and replace report periods with years."""
    annual_periods = annual_period_order(frame["报告期"])
    if not annual_periods:
        return frame.iloc[0:0].copy(), []
    period_label_map = {
        period: display_period_label(period) for period in annual_periods
    }
    result = frame[frame["报告期"].astype(str).isin(annual_periods)].copy()
    result["报告期"] = result["报告期"].astype(str).map(period_label_map)
    return result, [period_label_map[period] for period in annual_periods]


def render_report_metric(
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
    annual_labels: bool = False,
) -> None:
    metric_frame = filter_analysis_frame(frame, metric_code=code, periods=periods)
    if metric_frame.empty:
        st.caption(
            t(
                f"{metric_title(frame, code)}：当前筛选范围无数据。",
                f"{metric_title(frame, code)}: no data in the current selection.",
            )
        )
        return
    metric_frame = convert_unit(metric_frame, unit_mode)
    metric_frame = convert_multiple_units_to_percent(metric_frame)
    if code == "ACTUAL_CAPITAL_TO_RECOGNIZED_ASSETS":
        insight = (
            "实际资本/认可资产率较高的公司，说明目前的综合偿付能力充足率较高，"
            "但也说明还有提升资本利用率的空间。"
        )
        st.markdown(
            '<div class="solvency-report-analysis">'
            f'<p class="solvency-note-default">{escape(insight)}</p>'
            "</div>",
            unsafe_allow_html=True,
        )
    if annual_labels:
        metric_frame, ordered_periods = prepare_annual_metric_frame(metric_frame)
        if not ordered_periods:
            st.caption("当前没有可用于年度趋势图的报告期数据。")
            return
    else:
        ordered_periods = sort_report_periods(metric_frame["报告期"])
    if code == "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES":
        risk_pressure_text = insurance_risk_liability_pressure_sentence(
            metric_frame,
            code,
            ordered_periods,
        )
        st.markdown(
            '<div class="solvency-report-analysis">'
            '<p class="solvency-note-default">'
            "以该指标体现公司保险风险最低资本相对认可负债的压力因子值，"
            "比率越高说明负债对应的保险风险水平越高。"
            "</p>"
            + (
                '<p class="solvency-note-default">'
                f"{escape(risk_pressure_text)}</p>"
                if risk_pressure_text
                else ""
            )
            + "</div>",
            unsafe_allow_html=True,
        )
    metric_name = str(metric_frame.iloc[0]["指标名称"])
    st.caption(
        t(
            f"公司 {metric_frame['公司'].nunique()} 家　｜　"
            f"报告期 {metric_frame['报告期'].nunique()} 个　｜　"
            f"记录 {len(metric_frame):,} 条",
            f"{metric_frame['公司'].nunique()} companies  |  "
            f"{metric_frame['报告期'].nunique()} periods  |  "
            f"{len(metric_frame):,} records",
        )
    )
    latest_period = ordered_periods[-1]
    latest = metric_frame[metric_frame["报告期"].astype(str).eq(latest_period)].copy()
    latest["数值"] = pd.to_numeric(latest["数值"], errors="coerce")
    latest = latest.dropna(subset=["数值"]).drop_duplicates(subset=["公司"], keep="last")
    if not latest.empty:
        max_row = latest.loc[latest["数值"].idxmax()]
        min_row = latest.loc[latest["数值"].idxmin()]
        ai_html = ""
        if enable_ai:
            ai_text, ai_failed = ai_analysis_for_latest_period(latest, metric_name, latest_period)
            ai_class = (
                "solvency-analysis-ai solvency-analysis-ai--error"
                if ai_failed
                else "solvency-analysis-ai"
            )
            ai_html = (
                f"<div class='{ai_class}'><b>"
                + t("AI 分析：", "AI commentary: ")
                + f"</b>{escape(ai_text)}</div>"
            )
        max_value = format_chart_value(
            max_row['数值'], max_row.get('单位', ''), max_row.get('数据类型', ''), decimals
        )
        min_value = format_chart_value(
            min_row['数值'], min_row.get('单位', ''), min_row.get('数据类型', ''), decimals
        )
        summary_text = t(
            f"最新报告期 {latest_period}：最高为 {max_row['公司']} "
            f"（{max_value}），最低为 {min_row['公司']}（{min_value}）。",
            f"Latest reporting period {latest_period}: highest is {max_row['公司']} "
            f"({max_value}); lowest is {min_row['公司']} ({min_value}).",
        )
        st.markdown(
            "<div class='solvency-analysis-note'>"
            f"{summary_text}"
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
        legend_items = [(period_colors[period], period, "square") for period in ordered_periods]
        legend_items.append(("#0C233C", t("趋势折线", "Trend line"), "line"))
        if highlight_company in metric_frame["公司"].astype(str).unique():
            legend_items.append(("#B8BDC7", t("特定追踪公司", "Tracked company"), "outline"))
        if disclosed_count < expected_count:
            legend_items.append(("#B8BDC7", t("未披露", "Not disclosed"), "square"))
        if code in {"CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO"}:
            legend_items.append(("#ED2124", t("监管下限", "Regulatory minimum"), "dash"))
        render_chart_legend(legend_items)
        chart = build_company_bar_trend_chart(
            metric_frame,
            code,
            ordered_periods,
            highlight_company,
        )
        render_altair_chart(
            chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_{code}_company_panels",
        )
    else:
        expected_count = metric_frame["公司"].nunique() * len(ordered_periods)
        disclosed_count = metric_frame.drop_duplicates(["公司", "报告期"]).shape[0]
        external_legend = []
        if disclosed_count < expected_count:
            external_legend.append(("#B8BDC7", t("未披露", "Not disclosed"), "square"))
        if code in {"CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO"}:
            external_legend.append(("#ED2124", t("监管下限", "Regulatory minimum"), "dash"))
        if external_legend:
            render_chart_legend(external_legend)
        charts = build_single_metric_trend_charts(
            metric_frame,
            code,
            ordered_periods,
            company_color_map(metric_frame["公司"], highlight_company),
            highlight_company,
        )
        if len(charts) > 1:
            st.markdown(
                "<div class='solvency-grouped-trend' aria-hidden='true'></div>",
                unsafe_allow_html=True,
            )
        for group_index, chart in enumerate(charts, start=1):
            with st.container(key=f"s7_trend_group_{key_prefix}_{code}_{group_index}"):
                render_altair_chart(
                    chart_without_internal_title(chart),
                    width="stretch",
                    key=f"{key_prefix}_{code}_trend_{group_index}",
                )
    if company_period_bars:
        with st.container(key=f"s7_company_bars_page_{key_prefix}_{code}"):
            company_panel_count = metric_frame["公司"].nunique()
            shared_y_domain = metric_bar_axis_domain(metric_frame, code, ordered_periods)
            period_colors = report_period_color_map(ordered_periods)
            st.caption(
                t(
                    "以下按公司展示各报告期柱状小图；所有公司统一使用同一纵轴范围，"
                    "不同报告期使用不同颜色。",
                    "Small bar charts by company and reporting period. All companies share the same y-axis range; colours identify periods.",
                )
            )
            render_chart_legend(
                [(period_colors[period], period, "square") for period in ordered_periods]
            )
            render_company_chart_grid(
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


def render_chart_legend(
    items: Iterable[tuple[str, str, str]],
    *,
    align: str = "right",
) -> None:
    """Render a standalone legend with a consistent report alignment."""
    justify_content = {
        "left": "flex-start",
        "center": "center",
        "right": "flex-end",
    }.get(str(align).strip().lower(), "flex-end")
    margin = (
        "2px 0 8px"
        if justify_content == "flex-start"
        else "2px 10px 8px 0"
    )
    legend_html = "".join(
        (
            f"<span class='solvency-chart-legend__item'>"
            f"<i class='solvency-chart-legend__symbol solvency-chart-legend__symbol--{shape}' "
            f"style='--legend-color:{color}'></i>{escape(translate_text(label))}</span>"
        )
        for color, label, shape in items
    )
    st.markdown(
        f"<div class='solvency-chart-legend' "
        f"style='justify-content:{justify_content};margin:{margin}'>{legend_html}</div>",
        unsafe_allow_html=True,
    )


def render_company_chart_grid(
    frame: pd.DataFrame,
    chart_factory: Callable[[pd.DataFrame], object],
    *,
    key_prefix: str,
    highlight_company: str = "无",
    panel_width: int | None = None,
) -> None:
    """Render equal company panels in one page-width row without scrolling."""
    companies = ordered_companies(frame)
    if not companies:
        st.warning(t("当前筛选范围没有可绘制的公司数据。", "No company data to plot in the current selection."))
        return
    company_count = len(companies)
    title_font_size = (
        9 if company_count >= 12 else 10 if company_count >= 8 else 11 if company_count >= 5 else 13
    )
    column_gap = None if company_count >= 8 else "small"
    with st.container(key=f"annual_company_grid_{key_prefix}"):
        panel_columns = st.columns(
            company_count,
            gap=column_gap,
            vertical_alignment="top",
        )
        tracked_company = str(highlight_company or "").strip()
        for index, (column, company) in enumerate(zip(panel_columns, companies)):
            company_frame = frame[frame["公司"].fillna("").astype(str).eq(company)].copy()
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
                        st.warning(
                            t(
                                f"该公司的图表暂不可用：{exc}",
                                f"Chart unavailable for this company: {exc}",
                            )
                        )
                        continue
                    render_altair_chart(
                        chart_without_internal_title(chart),
                        width="stretch",
                        key=f"{key_prefix}_{index}",
                    )


def render_altair_chart(chart: object, **kwargs) -> None:
    """Render an Altair chart with English display labels when enabled."""
    st.altair_chart(localize_chart(chart), **kwargs)


def chart_without_internal_title(chart: object) -> object:
    """Keep company titles outside Vega so Streamlit's chart toolbar cannot cover them."""
    copied = chart.copy(deep=True)
    copied.title = alt.Undefined
    return copied
