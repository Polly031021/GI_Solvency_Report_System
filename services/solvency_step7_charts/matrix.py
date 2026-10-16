"""矩阵/散点图：偿付能力矩阵、市场—信用矩阵与风险比率散点联动。"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

import altair as alt
import pandas as pd

from .primitives import clean_numeric, company_scale, metric_title, apply_report_theme


def compact_scatter_axis(values: pd.Series) -> alt.Axis:
    """Use sparse, range-aware ticks so rounded scatter labels never repeat."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return alt.Axis(format=".2~f", tickCount=6)
    span = float(numeric.max() - numeric.min())
    if span > 0:
        target_step = span / 5
        decimal_places = max(
            0,
            min(6, int(math.ceil(-math.log10(target_step)))),
        )
    else:
        magnitude = abs(float(numeric.iloc[0]))
        decimal_places = 2 if magnitude < 1 else 1
    return alt.Axis(format=f".{decimal_places}~f", tickCount=6)


def ratio_axis_display_scale(values: pd.Series) -> tuple[float, str]:
    """Choose a readable display unit for a raw 0-1 ratio series."""
    numeric = pd.to_numeric(values, errors="coerce").dropna().abs()
    maximum = float(numeric.max()) if not numeric.empty else 0.0
    if maximum >= 0.01:
        return 100.0, "%"
    if maximum >= 0.001:
        return 1_000.0, "‰"
    if maximum > 0:
        return 10_000.0, "bp"
    return 100.0, "%"


def scatter_zoom_source(
    pivot: pd.DataFrame,
    x_code: str,
    y_code: str,
    highlight_company: str = "",
) -> tuple[pd.DataFrame, bool]:
    """Choose a dense local window, prioritizing the tracked company."""
    if len(pivot) < 2:
        return pivot, False
    x_span = max(float(pivot[x_code].max() - pivot[x_code].min()), 1e-9)
    y_span = max(float(pivot[y_code].max() - pivot[y_code].min()), 1e-9)
    row_indices = list(pivot.index)

    def display_distance(left_index: object, right_index: object) -> float:
        x_distance = float(pivot.at[left_index, x_code] - pivot.at[right_index, x_code]) / x_span
        y_distance = float(pivot.at[left_index, y_code] - pivot.at[right_index, y_code]) / y_span
        return math.sqrt(x_distance**2 + (y_distance * 0.65) ** 2)

    highlight = str(highlight_company or "").strip()
    highlight_indices = list(pivot.index[pivot["公司"].astype(str).eq(highlight)])
    if highlight_indices:
        seed_index = highlight_indices[0]
        nearby = sorted(
            (display_distance(row_index, seed_index), row_index) for row_index in row_indices
        )
        selected = [row_index for _, row_index in nearby[: min(len(pivot), 4)]]
        return pivot.loc[selected], True

    closest_pair: tuple[object, object] | None = None
    closest_distance = math.inf
    for left_position, left_index in enumerate(row_indices[:-1]):
        for right_index in row_indices[left_position + 1 :]:
            distance = display_distance(left_index, right_index)
            if distance < closest_distance:
                closest_distance = distance
                closest_pair = (left_index, right_index)
    if closest_pair is None or closest_distance > 0.12:
        return pivot, False

    cluster_radius = min(max(closest_distance * 2.2, 0.06), 0.16)
    nearby = sorted(
        (
            min(display_distance(row_index, seed_index) for seed_index in closest_pair),
            row_index,
        )
        for row_index in row_indices
    )
    dense_count = sum(1 for distance, _ in nearby if distance <= cluster_radius)
    context_count = min(len(pivot), min(6, max(3, dense_count)))
    selected = [row_index for _, row_index in nearby[:context_count]]
    return pivot.loc[selected], True


def scatter_padded_domain(
    full_sample: pd.DataFrame,
    domain_source: pd.DataFrame,
    field: str,
    *,
    zoomed: bool,
    protect_bubbles: bool = False,
) -> list[float]:
    lower = float(domain_source[field].min())
    upper = float(domain_source[field].max())
    span = upper - lower
    if zoomed:
        center = (lower + upper) / 2
        if span > 1e-12:
            domain_span = span * (1.55 if protect_bubbles else 1.35)
        else:
            full_span = float(full_sample[field].max() - full_sample[field].min())
            domain_span = (
                full_span * (0.20 if protect_bubbles else 0.12)
                if full_span > 1e-12
                else max(
                    abs(center) * (0.15 if protect_bubbles else 0.08),
                    0.01,
                )
            )
        return [center - domain_span / 2, center + domain_span / 2]
    padding = max(
        span * (0.18 if protect_bubbles else 0.08),
        max(abs(lower), abs(upper)) * (0.06 if protect_bubbles else 0.025),
        0.01,
    )
    return [lower - padding, upper + padding]


def scatter_domain_override(
    domain_override: Mapping[str, Iterable[float]] | None,
    key: str,
    fallback: list[float],
) -> list[float]:
    if not domain_override or key not in domain_override:
        return fallback
    values = [float(value) for value in domain_override[key]]
    if (
        len(values) != 2
        or not all(math.isfinite(value) for value in values)
        or values[0] >= values[1]
    ):
        return fallback
    return values


def build_matrix_chart(
    frame: pd.DataFrame,
    x_code: str,
    y_code: str,
    period_order: Iterable[str],
    title: str,
    highlight_company: str = "",
    regulatory_lines: bool = False,
    company_colors: Mapping[str, str] | None = None,
    percentage_axes: bool = False,
    zoom_to_overlap_region: bool = False,
    panel_label: str = "",
    chart_width: int = 820,
    chart_height: int = 540,
    show_company_legend: bool = True,
    show_axis_titles: bool = True,
    linked_selection_name: str = "",
    zoom_domain_override: Mapping[str, Iterable[float]] | None = None,
    apply_theme: bool = True,
    bubble_size_code: str = "",
    bubble_size_label: str = "",
    show_size_legend: bool = True,
) -> tuple[alt.Chart, str]:
    size_code = str(bubble_size_code or "").strip()
    required_codes = [x_code, y_code]
    if size_code:
        required_codes.append(size_code)
    rows = clean_numeric(frame[frame["指标编码"].astype(str).isin(required_codes)])
    periods = list(period_order)
    latest_period = periods[-1]
    latest = rows[rows["报告期"].astype(str).eq(latest_period)]
    size_units = (
        [
            str(value).strip()
            for value in latest.loc[latest["指标编码"].astype(str).eq(size_code), "单位"]
            .dropna()
            .unique()
            if str(value).strip()
        ]
        if size_code
        else []
    )
    size_unit = size_units[0] if size_units else ""
    pivot = latest.pivot_table(
        index=["公司", "同业分类"], columns="指标编码", values="数值", aggfunc="first"
    ).reset_index()
    missing_columns = [code for code in required_codes if code not in pivot.columns]
    if missing_columns:
        raise ValueError(f"{latest_period} 缺少可绘制的完整公司数据。")
    pivot = pivot.dropna(subset=required_codes).copy()
    if size_code:
        pivot["气泡大小"] = pd.to_numeric(pivot[size_code], errors="coerce").abs()
        finite_size = pivot["气泡大小"].map(lambda value: math.isfinite(float(value)))
        pivot = pivot[finite_size & pivot["气泡大小"].gt(0)].copy()
    x_unit = ""
    y_unit = ""
    if percentage_axes:
        if size_code:
            x_multiplier, x_unit = ratio_axis_display_scale(pivot[x_code])
            y_multiplier, y_unit = ratio_axis_display_scale(pivot[y_code])
        else:
            x_multiplier, y_multiplier = 100.0, 100.0
            x_unit, y_unit = "%", "%"
        pivot[x_code] = pd.to_numeric(pivot[x_code], errors="coerce") * x_multiplier
        pivot[y_code] = pd.to_numeric(pivot[y_code], errors="coerce") * y_multiplier
    if pivot.empty:
        raise ValueError(f"{latest_period} 缺少可绘制的完整公司数据。")
    full_sample_x_median = float(pivot[x_code].median())
    full_sample_y_median = float(pivot[y_code].median())
    zoom_source, zoom_applied = (
        scatter_zoom_source(pivot, x_code, y_code, highlight_company)
        if zoom_to_overlap_region
        else (pivot, False)
    )
    x_domain = scatter_domain_override(
        zoom_domain_override,
        "x",
        scatter_padded_domain(
            pivot,
            zoom_source,
            x_code,
            zoomed=zoom_applied,
            protect_bubbles=bool(size_code),
        ),
    )
    y_domain = scatter_domain_override(
        zoom_domain_override,
        "y",
        scatter_padded_domain(
            pivot,
            zoom_source,
            y_code,
            zoomed=zoom_applied,
            protect_bubbles=bool(size_code),
        ),
    )
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
    elif zoom_to_overlap_region or zoom_domain_override or size_code:
        x_scale = alt.Scale(domain=x_domain, zero=False, nice=False)
        y_scale = alt.Scale(domain=y_domain, zero=False, nice=False)
    else:
        x_scale = alt.Scale(zero=False)
        y_scale = alt.Scale(zero=False)
    highlight = str(highlight_company or "").strip()
    pivot["是否追踪"] = pivot["公司"].astype(str).eq(highlight)
    companies = list(dict.fromkeys(pivot["公司"].astype(str)))
    scale = company_scale(companies, company_colors or {})
    legend = (
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
    x_title = metric_title(rows, x_code)
    y_title = metric_title(rows, y_code)
    if percentage_axes:
        x_title = f"{x_title}（{x_unit}）"
        y_title = f"{y_title}（{y_unit}）"
    if not show_axis_titles:
        x_title = ""
        y_title = ""
    axis_source = zoom_source if zoom_to_overlap_region and zoom_applied else pivot
    x_axis = compact_scatter_axis(axis_source[x_code]) if percentage_axes else alt.Axis()
    y_axis = compact_scatter_axis(axis_source[y_code]) if percentage_axes else alt.Axis()
    tooltip_format = ".2f" if percentage_axes else ",.2f"
    tooltip = [
        "公司:N",
        "同业分类:N",
        alt.Tooltip(f"{x_code}:Q", title=x_title, format=tooltip_format),
        alt.Tooltip(f"{y_code}:Q", title=y_title, format=tooltip_format),
    ]
    if size_code:
        size_label = str(bubble_size_label or "").strip() or metric_title(rows, size_code)
        size_title = size_label if not size_unit else f"{size_label}（{size_unit}）"
        size_legend_title = (
            ["气泡大小代表", f"{size_label}金额"]
            if not size_unit
            else ["气泡大小代表", f"{size_label}金额（{size_unit}）"]
        )
        size_encoding = alt.Size(
            "气泡大小:Q",
            scale=alt.Scale(range=[180, 1800]),
            legend=(
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
            ),
        )
        tooltip.append(alt.Tooltip(f"{size_code}:Q", title=size_title, format=",.2f"))
    else:
        size_encoding = alt.condition(alt.datum["是否追踪"], alt.value(230), alt.value(115))
    points = (
        alt.Chart(pivot)
        .mark_circle(
            opacity=0.96,
            clip=bool(size_code) or zoom_to_overlap_region,
        )
        .encode(
            x=alt.X(
                f"{x_code}:Q",
                title=x_title,
                scale=x_scale,
                axis=x_axis,
            ),
            y=alt.Y(
                f"{y_code}:Q",
                title=y_title,
                scale=y_scale,
                axis=y_axis,
            ),
            color=alt.Color("公司:N", scale=scale, legend=legend),
            size=size_encoding,
            stroke=alt.condition(alt.datum["是否追踪"], alt.value("#00338D"), alt.value("#FFFFFF")),
            strokeWidth=alt.condition(alt.datum["是否追踪"], alt.value(3.0), alt.value(1.0)),
            tooltip=tooltip,
        )
    )
    border = None
    if selection_name:
        border = (
            alt.Chart(pd.DataFrame({"边框": [1]}))
            .mark_rect(
                fillOpacity=0,
                stroke="#D9DEE7",
                strokeWidth=1,
            )
            .encode(
                x=alt.value(0),
                x2=alt.value(chart_width),
                y=alt.value(0),
                y2=alt.value(chart_height),
            )
        )
    if zoom_to_overlap_region and selection_name:
        # Match the working bubble-chart structure exactly: the local panel has
        # one dynamic-domain data layer plus a pixel border. Independent median
        # rule datasets would otherwise participate in scale merging and pin the
        # local axes instead of following the interval selection.
        layers: list[alt.Chart] = [border, points]
    else:
        # Keep points first so the interval selection inherits the actual x/y
        # fields rather than a synthetic border or reference-line field.
        layers = [points]
        if border is not None:
            layers.append(border)
    if regulatory_lines and not zoom_to_overlap_region:
        layers.extend(
            [
                alt.Chart(pd.DataFrame({"核心监管线": [50.0]}))
                .mark_rule(color="#ED2124", strokeDash=[5, 4])
                .encode(x="核心监管线:Q"),
                alt.Chart(pd.DataFrame({"综合监管线": [100.0]}))
                .mark_rule(color="#ED2124", strokeDash=[5, 4])
                .encode(y="综合监管线:Q"),
            ]
        )
    elif not zoom_to_overlap_region:
        reference_color = "#C9CED6" if panel_label else "#0C233C"
        layers.extend(
            [
                alt.Chart(pd.DataFrame({"横轴中位数": [full_sample_x_median]}))
                .mark_rule(color=reference_color, strokeDash=[6, 4])
                .encode(x="横轴中位数:Q"),
                alt.Chart(pd.DataFrame({"纵轴中位数": [full_sample_y_median]}))
                .mark_rule(color=reference_color, strokeDash=[6, 4])
                .encode(y="纵轴中位数:Q"),
            ]
        )
    chart_title: str | alt.TitleParams = f"{latest_period} {title}"
    if panel_label:
        chart_title = alt.TitleParams(
            text=panel_label,
            anchor="start",
            color="#667085",
            fontSize=12,
            fontWeight="bold",
            offset=8,
        )
    chart = alt.layer(*layers).properties(
        title=chart_title,
        width=chart_width,
        height=chart_height,
    )
    return (apply_report_theme(chart) if apply_theme else chart), latest_period


def matrix_chart_domain(
    chart: alt.Chart,
    x_code: str,
    y_code: str,
) -> dict[str, list[float]]:
    """Read the fixed x/y domain from a locally zoomed matrix chart."""
    spec = chart.to_dict(validate=True)
    points = next(layer for layer in spec["layer"] if layer.get("mark", {}).get("type") == "circle")
    return {
        "x": list(points["encoding"]["x"]["scale"]["domain"]),
        "y": list(points["encoding"]["y"]["scale"]["domain"]),
    }


def risk_ratio_scatter_default_domain(
    frame: pd.DataFrame,
    x_code: str,
    y_code: str,
    period_order: Iterable[str],
    title: str,
    highlight_company: str = "",
    company_colors: Mapping[str, str] | None = None,
    bubble_size_code: str = "",
    bubble_size_label: str = "",
) -> tuple[dict[str, list[float]], str]:
    """Return the initial linked local window for a risk-ratio bubble chart."""
    chart, latest_period = build_matrix_chart(
        frame,
        x_code,
        y_code,
        period_order,
        title,
        highlight_company,
        company_colors=company_colors,
        percentage_axes=True,
        zoom_to_overlap_region=True,
        show_company_legend=False,
        show_axis_titles=False,
        apply_theme=False,
        bubble_size_code=bubble_size_code,
        bubble_size_label=bubble_size_label,
        show_size_legend=False,
    )
    return matrix_chart_domain(chart, x_code, y_code), latest_period


def combine_linked_matrix_charts(
    full_chart: alt.Chart,
    local_chart: alt.Chart,
    *,
    selection_name: str,
    selection_domain: Mapping[str, Iterable[float]],
    spacing: int = 20,
) -> alt.HConcatChart:
    """Combine full and local risk panels with a browser-side brush."""
    x_domain = [float(value) for value in selection_domain.get("x", ())]
    y_domain = [float(value) for value in selection_domain.get("y", ())]
    if len(x_domain) == 2 and len(y_domain) == 2:
        brush = alt.selection_interval(
            name=selection_name,
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
    return apply_report_theme(
        alt.hconcat(full_chart, local_chart, spacing=spacing).resolve_scale(
            x="independent",
            y="independent",
            color="shared",
            size="shared",
        )
    )
