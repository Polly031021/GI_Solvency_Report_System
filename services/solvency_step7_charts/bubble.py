"""资本使用效率气泡图（全样本 + 局部放大联动）。"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

import altair as alt
import pandas as pd

from .primitives import clean_numeric, company_scale, apply_report_theme


def build_capital_efficiency_bubble_chart(
    frame: pd.DataFrame,
    period_order: Iterable[str],
    company_colors: Mapping[str, str],
    highlight_company: str = "",
    *,
    zoom_to_overlap_region: bool = False,
    panel_label: str = "全样本图",
    chart_width: int = 650,
    chart_height: int = 430,
    show_company_legend: bool = True,
    show_size_legend: bool = True,
    show_axis_titles: bool = True,
    apply_theme: bool = True,
    linked_selection_name: str = "",
    zoom_domain_override: Mapping[str, Iterable[float]] | None = None,
) -> tuple[alt.Chart, str]:
    """Plot latest-period actual/registered and core/registered capital with asset size."""
    periods = list(dict.fromkeys(str(period) for period in period_order))
    if not periods:
        raise ValueError("缺少可绘制的报告期。")
    latest_period = periods[-1]
    required_codes = (
        "ACTUAL_CAPITAL",
        "RECOGNIZED_ASSETS",
        "REGISTERED_CAPITAL",
        "CORE_T1_CAPITAL",
        "CORE_T2_CAPITAL",
    )
    rows = frame[
        frame["指标编码"].astype(str).isin(required_codes)
        & frame["报告期"].astype(str).eq(latest_period)
    ].copy()
    if "同业分类" not in rows.columns:
        rows["同业分类"] = ""
    rows = clean_numeric(rows)
    asset_units = [
        str(value).strip()
        for value in rows.loc[rows["指标编码"].astype(str).eq("RECOGNIZED_ASSETS"), "单位"]
        .dropna()
        .unique()
        if str(value).strip()
    ]
    asset_unit = asset_units[0] if asset_units else ""
    pivot = rows.pivot_table(
        index=["公司", "同业分类"],
        columns="指标编码",
        values="数值",
        aggfunc="first",
    ).reset_index()
    missing_columns = [code for code in required_codes if code not in pivot.columns]
    if missing_columns:
        raise ValueError("最新报告期缺少气泡图所需指标。")
    pivot = pivot.dropna(subset=list(required_codes)).copy()
    pivot = pivot[pivot["REGISTERED_CAPITAL"].ne(0) & pivot["RECOGNIZED_ASSETS"].ne(0)].copy()
    pivot["实际资本/注册资本"] = pivot["ACTUAL_CAPITAL"] / pivot["REGISTERED_CAPITAL"]
    pivot["核心资本/注册资本"] = (pivot["CORE_T1_CAPITAL"] + pivot["CORE_T2_CAPITAL"]) / pivot[
        "REGISTERED_CAPITAL"
    ]
    pivot["认可资产"] = pivot["RECOGNIZED_ASSETS"]
    pivot["气泡大小"] = pivot["RECOGNIZED_ASSETS"].abs()
    pivot["报告期"] = latest_period
    finite_columns = ["实际资本/注册资本", "核心资本/注册资本", "气泡大小"]
    finite_mask = (
        pivot[finite_columns]
        .apply(lambda column: column.map(lambda value: math.isfinite(float(value))))
        .all(axis=1)
    )
    pivot = pivot[finite_mask & pivot["气泡大小"].gt(0)].copy()
    if pivot.empty:
        raise ValueError(f"{latest_period} 缺少可绘制的完整公司数据。")
    full_sample_x_median = float(pivot["实际资本/注册资本"].median())
    full_sample_y_median = float(pivot["核心资本/注册资本"].median())
    zoom_domain_source = pivot
    zoom_applied = False
    highlight = str(highlight_company or "").strip()

    if zoom_to_overlap_region and len(pivot) >= 2:
        x_field = "实际资本/注册资本"
        y_field = "核心资本/注册资本"
        x_span = max(float(pivot[x_field].max() - pivot[x_field].min()), 1e-9)
        y_span = max(float(pivot[y_field].max() - pivot[y_field].min()), 1e-9)
        row_indices = list(pivot.index)

        def display_distance_between(left_index: object, right_index: object) -> float:
            x_distance = (
                float(pivot.at[left_index, x_field] - pivot.at[right_index, x_field]) / x_span
            )
            y_distance = (
                float(pivot.at[left_index, y_field] - pivot.at[right_index, y_field]) / y_span
            )
            return math.sqrt(x_distance**2 + (y_distance * 0.65) ** 2)

        highlight_indices = list(pivot.index[pivot["公司"].astype(str).eq(highlight)])
        if highlight_indices:
            seed_index = highlight_indices[0]
            nearby = sorted(
                (
                    display_distance_between(row_index, seed_index),
                    row_index,
                )
                for row_index in row_indices
            )
            context_count = min(len(pivot), 4)
            selected_indices = [row_index for _, row_index in nearby[:context_count]]
            zoom_domain_source = pivot.loc[selected_indices]
            zoom_applied = True
        else:
            closest_pair: tuple[object, object] | None = None
            closest_distance = math.inf
            for left_position, left_index in enumerate(row_indices[:-1]):
                for right_index in row_indices[left_position + 1 :]:
                    display_distance = display_distance_between(
                        left_index,
                        right_index,
                    )
                    if display_distance < closest_distance:
                        closest_distance = display_distance
                        closest_pair = (left_index, right_index)
            if closest_pair is None or closest_distance > 0.12:
                closest_pair = None
            cluster_radius = min(max(closest_distance * 2.2, 0.06), 0.16)
            nearby = []
            for row_index in row_indices:
                distance_to_seed = (
                    min(
                        display_distance_between(row_index, seed_index)
                        for seed_index in closest_pair
                    )
                    if closest_pair is not None
                    else math.inf
                )
                nearby.append((distance_to_seed, row_index))
            nearby.sort(key=lambda item: item[0])
            dense_count = sum(1 for distance, _ in nearby if distance <= cluster_radius)
            if closest_pair is not None:
                context_count = min(
                    len(pivot),
                    min(6, max(3, dense_count)),
                )
                selected_indices = [row_index for _, row_index in nearby[:context_count]]
                zoom_domain_source = pivot.loc[selected_indices]
                zoom_applied = True

    pivot = pivot.sort_values(
        ["实际资本/注册资本", "核心资本/注册资本"],
        kind="stable",
    ).reset_index(drop=True)

    pivot["是否追踪"] = pivot["公司"].astype(str).eq(highlight)
    companies = list(dict.fromkeys(pivot["公司"].astype(str)))
    color_scale = company_scale(companies, company_colors)
    company_legend = (
        alt.Legend(
            title="公司",
            orient="right",
            direction="vertical",
            columns=1,
            symbolSize=110,
            offset=24,
            padding=8,
        )
        if show_company_legend
        else None
    )
    size_title = "认可资产" if not asset_unit else f"认可资产（{asset_unit}）"
    size_legend_title = (
        ["气泡大小代表", "认可资产金额"]
        if not asset_unit
        else ["气泡大小代表", f"认可资产金额（{asset_unit}）"]
    )
    size_legend = (
        alt.Legend(
            title=size_legend_title,
            orient="right",
            direction="vertical",
            tickCount=3,
            format=",.1f",
            offset=24,
            padding=8,
        )
        if show_size_legend
        else None
    )

    def padded_domain(field: str) -> list[float]:
        source = zoom_domain_source if zoom_applied else pivot
        lower = float(source[field].min())
        upper = float(source[field].max())
        span = upper - lower
        if zoom_applied:
            center = (lower + upper) / 2
            if span > 1e-12:
                domain_span = span * 1.35
            else:
                full_span = float(pivot[field].max() - pivot[field].min())
                domain_span = (
                    full_span * 0.12 if full_span > 1e-12 else max(abs(center) * 0.08, 1e-4)
                )
            return [center - domain_span / 2, center + domain_span / 2]
        padding = max(
            span * 0.08,
            max(abs(lower), abs(upper)) * 0.025,
            0.005,
        )
        return [lower - padding, upper + padding]

    x_domain = padded_domain("实际资本/注册资本")
    y_domain = padded_domain("核心资本/注册资本")

    def overridden_domain(key: str, fallback: list[float]) -> list[float]:
        if not zoom_domain_override or key not in zoom_domain_override:
            return fallback
        values = [float(value) for value in zoom_domain_override[key]]
        if (
            len(values) != 2
            or not all(math.isfinite(value) for value in values)
            or values[0] >= values[1]
        ):
            return fallback
        return values

    x_domain = overridden_domain("x", x_domain)
    y_domain = overridden_domain("y", y_domain)
    selection_name = str(linked_selection_name or "").strip()
    if zoom_to_overlap_region and selection_name:
        x_scale = alt.Scale(
            domain={"param": selection_name, "encoding": "x"},
            zero=False,
            nice=False,
        )
        y_scale = alt.Scale(
            domain={"param": selection_name, "encoding": "y"},
            zero=False,
            nice=False,
        )
    else:
        x_scale = alt.Scale(domain=x_domain, zero=False, nice=False)
        y_scale = alt.Scale(domain=y_domain, zero=False, nice=False)
    border_color = "#D9DEE7"
    reference_color = "#C9CED6"
    x_title = "实际资本/注册资本（%）" if show_axis_titles else ""
    y_title = "核心资本/注册资本（%）" if show_axis_titles else ""
    x_axis = alt.Axis(
        title=x_title,
        labelExpr="format(datum.value * 100, '.1f')",
        tickCount=6,
        labelOverlap=True,
        labels=True,
        domain=True,
        domainColor=border_color,
        domainWidth=1,
        ticks=True,
        tickColor=border_color,
    )
    y_axis = alt.Axis(
        title=y_title,
        labelExpr=(
            "format(datum.value * 100, '.1f')"
            if zoom_to_overlap_region
            else "format(datum.value * 100, '.0f')"
        ),
        tickCount=7,
        labels=True,
        domain=True,
        domainColor=border_color,
        domainWidth=1,
        ticks=True,
        tickColor=border_color,
    )
    base = alt.Chart(pivot)
    bubbles = base.mark_circle(
        opacity=0.78,
        clip=zoom_to_overlap_region,
    ).encode(
        x=alt.X(
            "实际资本/注册资本:Q",
            title=x_title,
            axis=x_axis,
            scale=x_scale,
        ),
        y=alt.Y(
            "核心资本/注册资本:Q",
            title=y_title,
            axis=y_axis,
            scale=y_scale,
        ),
        size=alt.Size(
            "气泡大小:Q",
            scale=alt.Scale(range=[180, 1800]),
            legend=size_legend,
        ),
        color=alt.Color("公司:N", scale=color_scale, legend=company_legend),
        stroke=alt.condition(
            alt.datum["是否追踪"],
            alt.value("#00338D"),
            alt.value("#FFFFFF"),
        ),
        strokeWidth=alt.condition(
            alt.datum["是否追踪"],
            alt.value(3.0),
            alt.value(1.0),
        ),
        tooltip=[
            "公司:N",
            "同业分类:N",
            "报告期:N",
            alt.Tooltip("实际资本/注册资本:Q", format=".1%"),
            alt.Tooltip("核心资本/注册资本:Q", format=".1%"),
            alt.Tooltip("认可资产:Q", title=size_title, format=",.2f"),
        ],
    )
    if selection_name:
        border = (
            alt.Chart(pd.DataFrame({"边框": [1]}))
            .mark_rect(
                fillOpacity=0,
                stroke=border_color,
                strokeWidth=1,
            )
            .encode(
                x=alt.value(0),
                x2=alt.value(chart_width),
                y=alt.value(0),
                y2=alt.value(chart_height),
            )
        )
    else:
        border = (
            alt.Chart(
                pd.DataFrame(
                    {
                        "横轴起点": [x_domain[0]],
                        "横轴终点": [x_domain[1]],
                        "纵轴起点": [y_domain[0]],
                        "纵轴终点": [y_domain[1]],
                    }
                )
            )
            .mark_rect(
                fillOpacity=0,
                stroke=border_color,
                strokeWidth=1,
            )
            .encode(
                x=alt.X("横轴起点:Q", axis=x_axis, scale=x_scale),
                x2="横轴终点:Q",
                y=alt.Y("纵轴起点:Q", axis=y_axis, scale=y_scale),
                y2="纵轴终点:Q",
            )
        )
    reference_values = pd.DataFrame(
        {
            "横轴中位数": [full_sample_x_median],
            "纵轴中位数": [full_sample_y_median],
        }
    )
    vertical_reference = (
        alt.Chart(reference_values)
        .mark_rule(
            color=reference_color,
            strokeDash=[5, 4],
            strokeWidth=1,
        )
        .encode(
            x=alt.X("横轴中位数:Q", axis=x_axis, scale=x_scale),
            y=alt.Y(
                datum=y_domain[0],
                type="quantitative",
                axis=y_axis,
                scale=y_scale,
            ),
            y2=alt.Y2(datum=y_domain[1]),
        )
    )
    horizontal_reference = (
        alt.Chart(reference_values)
        .mark_rule(
            color=reference_color,
            strokeDash=[5, 4],
            strokeWidth=1,
        )
        .encode(
            x=alt.X(
                datum=x_domain[0],
                type="quantitative",
                axis=x_axis,
                scale=x_scale,
            ),
            x2=alt.X2(datum=x_domain[1]),
            y=alt.Y("纵轴中位数:Q", axis=y_axis, scale=y_scale),
        )
    )
    if selection_name and not zoom_to_overlap_region:
        # The interval selection must inherit the bubble fields instead of the
        # border layer's synthetic start/end fields so Streamlit can persist
        # the selected data-domain for print locking.
        chart_layers = [bubbles, border, vertical_reference, horizontal_reference]
    else:
        chart_layers = [border]
        if not zoom_to_overlap_region:
            chart_layers.extend([vertical_reference, horizontal_reference])
        chart_layers.append(bubbles)
    chart = alt.layer(*chart_layers).properties(
        title=alt.TitleParams(
            text=panel_label,
            anchor="start",
            color="#667085",
            fontSize=12,
            fontWeight="bold",
            offset=8,
        ),
        width=chart_width,
        height=chart_height,
    )
    return (apply_report_theme(chart) if apply_theme else chart), latest_period


def capital_efficiency_bubble_chart_domain(
    chart: alt.Chart,
) -> dict[str, list[float]]:
    """Read the linked x/y window from an already-built zoom chart."""
    spec = chart.to_dict(validate=True)
    bubble_layer = next(
        layer for layer in spec["layer"] if layer.get("mark", {}).get("type") == "circle"
    )
    return {
        "x": list(bubble_layer["encoding"]["x"]["scale"]["domain"]),
        "y": list(bubble_layer["encoding"]["y"]["scale"]["domain"]),
    }


def capital_efficiency_bubble_default_domain(
    frame: pd.DataFrame,
    period_order: Iterable[str],
    company_colors: Mapping[str, str],
    highlight_company: str = "",
) -> tuple[dict[str, list[float]], str]:
    """Return the initial linked-zoom window around the tracked company."""
    chart, latest_period = build_capital_efficiency_bubble_chart(
        frame,
        period_order,
        company_colors,
        highlight_company,
        zoom_to_overlap_region=True,
        show_company_legend=False,
        show_size_legend=False,
        show_axis_titles=False,
        apply_theme=False,
    )
    return capital_efficiency_bubble_chart_domain(chart), latest_period


def combine_capital_efficiency_bubble_charts(
    full_chart: alt.Chart,
    overlap_chart: alt.Chart,
    *,
    spacing: int = 20,
    selection_name: str = "",
    selection_domain: Mapping[str, Iterable[float]] | None = None,
) -> alt.HConcatChart:
    """Place both bubble panels on one canvas so their plot baselines align."""
    linked_selection = str(selection_name or "").strip()
    if linked_selection and selection_domain:
        x_domain = [float(value) for value in selection_domain.get("x", ())]
        y_domain = [float(value) for value in selection_domain.get("y", ())]
        if len(x_domain) == 2 and len(y_domain) == 2:
            brush = alt.selection_interval(
                name=linked_selection,
                encodings=["x", "y"],
                value={"x": x_domain, "y": y_domain},
                clear=False,
                translate=True,
                zoom=True,
                mark=alt.BrushConfig(
                    fill="#D9DEE7",
                    fillOpacity=0.12,
                    stroke="#98A2B3",
                    strokeDash=[4, 3],
                    strokeOpacity=0.95,
                    strokeWidth=1.2,
                ),
            )
            full_chart = full_chart.add_params(brush)
    combined = alt.hconcat(
        full_chart,
        overlap_chart,
        spacing=spacing,
    ).resolve_scale(
        x="independent",
        y="independent",
        color="shared",
        size="shared",
    )
    return apply_report_theme(combined)
