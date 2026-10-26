"""能力五边形雷达图。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import altair as alt
import pandas as pd

from .primitives import apply_report_theme


def _angles(count: int) -> list[float]:
    # θ=0 在 12 点方向：x=r·sin(θ)，y=r·cos(θ)
    return [2.0 * math.pi * index / count for index in range(count)]


def _to_xy(score: float, angle: float, max_score: float) -> tuple[float, float]:
    radius = max(0.0, min(float(max_score), float(score)))
    return radius * math.sin(angle), radius * math.cos(angle)


def _reference_values(
    reference_scores: Sequence[float] | None,
    axis_count: int,
    max_score: float,
) -> list[float]:
    if reference_scores is not None and len(reference_scores) == axis_count:
        return [
            max_score * 0.5
            if value is None or pd.isna(value)
            else float(value)
            for value in reference_scores
        ]
    return [max_score * 0.5] * axis_count


def _radar_data(
    score_frame: pd.DataFrame,
    axis_labels: list[str],
    *,
    max_score: float,
    reference_scores: Sequence[float] | None,
    facet_titles: Mapping[str, str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Build all geometry for one or more companies in a single pass."""
    axis_count = len(axis_labels)
    angles = _angles(axis_count)
    reference_values = _reference_values(reference_scores, axis_count, max_score)
    facet_titles = facet_titles or {}

    ring_rows: list[dict] = []
    axis_rows: list[dict] = []
    median_rows: list[dict] = []
    poly_rows: list[dict] = []
    vertex_rows: list[dict] = []
    label_rows: list[dict] = []

    for _, row in score_frame.iterrows():
        company = str(row["公司"])
        facet_label = str(facet_titles.get(company, company))

        for tick in (20.0, 40.0, 60.0, 80.0, 100.0):
            if tick > max_score:
                continue
            coords = [_to_xy(tick, angle, max_score) for angle in angles]
            coords.append(coords[0])
            for order, (x, y) in enumerate(coords):
                ring_rows.append(
                    {
                        "公司": company,
                        "facet": facet_label,
                        "tick": str(int(tick)),
                        "order": order,
                        "x": x,
                        "y": y,
                    }
                )

        for angle in angles:
            x2, y2 = _to_xy(max_score, angle, max_score)
            axis_rows.append(
                {
                    "公司": company,
                    "facet": facet_label,
                    "x": 0.0,
                    "y": 0.0,
                    "x2": x2,
                    "y2": y2,
                }
            )

        median_coords = [
            _to_xy(score, angle, max_score)
            for score, angle in zip(reference_values, angles)
        ]
        median_coords.append(median_coords[0])
        for order, (x, y) in enumerate(median_coords):
            median_rows.append(
                {"公司": company, "facet": facet_label, "order": order, "x": x, "y": y}
            )

        coords = [
            _to_xy(row.get(f"{label}_得分", 0.0), angles[index], max_score)
            for index, label in enumerate(axis_labels)
        ]
        closed = coords + [coords[0]]
        for idx, (x, y) in enumerate(closed):
            poly_rows.append(
                {
                    "公司": company,
                    "facet": facet_label,
                    "idx": idx,
                    "x": x,
                    "y": y,
                }
            )
        for label, (x, y) in zip(axis_labels, coords):
            vertex_rows.append(
                {
                    "公司": company,
                    "facet": facet_label,
                    "顶点": label,
                    "x": x,
                    "y": y,
                    "得分": float(row.get(f"{label}_得分", 0.0)),
                }
            )

        for label, angle in zip(axis_labels, angles):
            x, y = _to_xy(max_score * 1.22, angle, max_score)
            label_rows.append(
                {
                    "公司": company,
                    "facet": facet_label,
                    "label": label,
                    "x": x,
                    "y": y,
                }
            )

    return {
        "ring": pd.DataFrame(ring_rows),
        "axis": pd.DataFrame(axis_rows),
        "median": pd.DataFrame(median_rows),
        "poly": pd.DataFrame(poly_rows),
        "vertex": pd.DataFrame(vertex_rows),
        "label": pd.DataFrame(label_rows),
    }


def _radar_layers(
    data: Mapping[str, pd.DataFrame],
    company_colors: Mapping[str, str],
    *,
    max_score: float,
    label_font_size: int,
    ring_color: str,
    median_color: str,
    show_legend: bool,
    legend_name: str,
    shared_data: pd.DataFrame | None = None,
) -> alt.LayerChart:
    scale_limit = max_score * 1.30
    domain = [-scale_limit, scale_limit]
    color_scale = alt.Scale(
        domain=list(company_colors.keys()),
        range=list(company_colors.values()),
    )

    def _source(layer: str) -> alt.Chart:
        """One shared DataFrame keeps Altair faceting valid for layered charts."""
        if shared_data is None:
            return alt.Chart(data[layer])
        return alt.Chart(shared_data).transform_filter(
            alt.datum["_layer"] == layer
        )

    # 点击图例切换显示/隐藏该公司；单公司小图不会显示图例。
    legend_select = alt.selection_point(
        name=legend_name,
        fields=["公司"],
        bind="legend",
        toggle="event.shiftKey",
        clear=False,
        empty=True,
    )

    x_enc = alt.X("x:Q", scale=alt.Scale(domain=domain), axis=None)
    y_enc = alt.Y("y:Q", scale=alt.Scale(domain=domain), axis=None)

    ring_layer = (
        _source("ring")
        .mark_line(color=ring_color, strokeWidth=0.9, opacity=0.9)
        .encode(
            x=x_enc,
            y=y_enc,
            detail="tick:N",
            order=alt.Order("order:Q"),
        )
    )
    axis_layer = (
        _source("axis")
        .mark_rule(color=ring_color, strokeWidth=0.9, opacity=0.9)
        .encode(x=x_enc, y=y_enc, x2="x2:Q", y2="y2:Q")
    )
    median_layer = (
        _source("median")
        .mark_line(
            color=median_color,
            strokeWidth=1.6,
            strokeDash=[6, 4],
            opacity=0.95,
        )
        .encode(x=x_enc, y=y_enc, order=alt.Order("order:Q"))
    )

    poly_layer = (
        _source("poly")
        .mark_line(strokeWidth=2.2)
        .encode(
            x=x_enc,
            y=y_enc,
            color=alt.Color(
                "公司:N",
                scale=color_scale,
                legend=(
                    alt.Legend(
                        title="公司",
                        orient="top",
                        direction="horizontal",
                        symbolType="square",
                        symbolSize=90,
                        labelFontSize=10,
                        titleFontSize=11,
                        columns=0,
                        labelLimit=140,
                        offset=6,
                        symbolLimit=0,
                    )
                    if show_legend
                    else None
                ),
            ),
            detail="公司:N",
            order=alt.Order("idx:Q"),
            opacity=alt.condition(
                legend_select,
                alt.value(0.9),
                alt.value(0.05),
            ),
        )
        .add_params(legend_select)
    )

    vertex_layer = (
        _source("vertex")
        .mark_circle(size=70)
        .encode(
            x=x_enc,
            y=y_enc,
            color=alt.Color("公司:N", scale=color_scale, legend=None),
            tooltip=[
                "公司:N",
                "顶点:N",
                alt.Tooltip("得分:Q", format=".0f"),
            ],
            opacity=alt.condition(
                legend_select,
                alt.value(0.95),
                alt.value(0.05),
            ),
        )
    )

    label_layer = (
        _source("label")
        .mark_text(
            fontSize=label_font_size,
            fontWeight="bold",
            color="#0C233C",
            align="center",
        )
        .encode(x=x_enc, y=y_enc, text="label:N")
    )

    return (
        ring_layer
        + axis_layer
        + median_layer
        + poly_layer
        + vertex_layer
        + label_layer
    )


def build_capability_radar_chart(
    score_frame: pd.DataFrame,
    axis_labels: list[str],
    company_colors: Mapping[str, str],
    *,
    max_score: float = 100.0,
    panel_width: int = 560,
    panel_height: int = 560,
    show_legend: bool = True,  # ★ 单公司小图时关掉
    label_font_size: int = 13,  # ★ 小图用 9
    ring_color: str = "#CBD5E1",  # ★ 网格颜色可调
    median_color: str = "#64748B",  # ★ 中位线颜色可调
    reference_scores: Sequence[float] | None = None,  # ★ 参考五边形（默认 50 分）
) -> alt.Chart:
    """能力五边形雷达图（Altair 手绘极坐标→笛卡尔）。

    参数
    ----
    score_frame: 列 = 公司 + "<label>_得分"，每行一家公司
    axis_labels: 5 个顶点名称。**顺序决定顶点顺序**：
                 第 0 个在 12 点方向，之后顺时针每 72° 一个。
    company_colors: 公司 → 颜色（16 进制）
    max_score:     分数上限（默认 100）
    """
    if score_frame.empty or not axis_labels:
        raise ValueError("能力雷达图缺少输入数据。")

    data = _radar_data(
        score_frame,
        axis_labels,
        max_score=max_score,
        reference_scores=reference_scores,
    )
    layers = _radar_layers(
        data,
        company_colors,
        max_score=max_score,
        label_font_size=label_font_size,
        ring_color=ring_color,
        median_color=median_color,
        show_legend=show_legend,
        legend_name="capability_company_legend_select",
    )
    return apply_report_theme(
        layers.properties(
            width=panel_width,
            height=panel_height,
            padding={"top": 0, "bottom": 0, "left": 5, "right": 5},
        ).configure_legend(
            # ★ 图例容器不裁剪，允许超出默认框
            clipHeight=220,
        )
    )


def build_capability_radar_grid(
    score_frame: pd.DataFrame,
    axis_labels: list[str],
    company_colors: Mapping[str, str],
    *,
    facet_titles: Mapping[str, str] | None = None,
    reference_scores: Sequence[float] | None = None,
    columns: int = 5,
    max_score: float = 100.0,
    panel_width: int = 230,
    panel_height: int = 230,
    label_font_size: int = 9,
    ring_color: str = "#CBD5E1",
    median_color: str = "#94A3B8",
    spacing: int = 10,
) -> alt.Chart:
    """Render several company pentagons as one faceted chart.

    One Streamlit chart element per row is far cheaper than one element per
    company (the report can contain ~90 companies).  The facet header is the
    company name, so the name and the pentagon are guaranteed to share the same
    centre line — the previous layout centred the HTML name over the whole
    column while Vega left-aligned the chart.

    参数
    ----
    facet_titles: 公司 → 面板标题（默认公司名；追踪公司可加 ★ 前缀）
    reference_scores: 该组各顶点中位数，所有面板共用
    columns: 每行公司数（默认 5）
    """
    if score_frame.empty or not axis_labels:
        raise ValueError("能力雷达图缺少输入数据。")

    companies = [str(value) for value in score_frame["公司"]]
    titles = {company: str((facet_titles or {}).get(company, company)) for company in companies}
    data = _radar_data(
        score_frame,
        axis_labels,
        max_score=max_score,
        reference_scores=reference_scores,
        facet_titles=titles,
    )
    # Altair 要求分面的图层共享同一个 data 对象；把六类几何合并为长表，
    # 再用 _layer 过滤，既满足分面要求，也避免重复构造 DataFrame。
    shared_data = pd.concat(
        [frame.assign(_layer=name) for name, frame in data.items()],
        ignore_index=True,
        sort=False,
    )
    layers = _radar_layers(
        data,
        company_colors,
        max_score=max_score,
        label_font_size=label_font_size,
        ring_color=ring_color,
        median_color=median_color,
        show_legend=False,
        legend_name="capability_company_legend_select_grid",
        shared_data=shared_data,
    )
    column_count = max(1, min(int(columns), len(companies)))
    facet = alt.Facet(
        "facet:N",
        sort=[titles[company] for company in companies],
        header=alt.Header(
            title=None,
            labelOrient="top",
            labelAnchor="middle",
            labelFontSize=12,
            labelFontWeight="bold",
            labelColor="#00338D",
            labelPadding=2,
            labelLimit=150,
        ),
    )
    faceted = layers.properties(width=panel_width, height=panel_height).facet(
        facet, columns=column_count
    )
    return apply_report_theme(
        faceted.properties(
            padding={"top": 4, "bottom": 0, "left": 4, "right": 4}
        )
    )
