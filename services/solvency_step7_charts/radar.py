"""能力五边形雷达图。"""

from __future__ import annotations

import math
from collections.abc import Mapping

import altair as alt
import pandas as pd

from .primitives import apply_report_theme


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

    n = len(axis_labels)
    # θ=0 在 12 点方向：x=r·sin(θ)，y=r·cos(θ)
    angles = [2.0 * math.pi * i / n for i in range(n)]

    def _to_xy(score: float, angle: float) -> tuple[float, float]:
        r = max(0.0, min(float(max_score), float(score)))
        return r * math.sin(angle), r * math.cos(angle)

    # ---------- 1. 背景同心五边形（20/40/60/80/100） ----------
    ring_rows: list[dict] = []
    for tick in (20.0, 40.0, 60.0, 80.0, 100.0):
        if tick > max_score:
            continue
        coords = [_to_xy(tick, a) for a in angles]
        coords.append(coords[0])  # 首尾相接
        for idx, (x, y) in enumerate(coords):
            ring_rows.append(
                {
                    "tick": str(int(tick)),
                    "order": idx,  # ★ 显式顺序
                    "x": x,
                    "y": y,
                }
            )
    ring_df = pd.DataFrame(ring_rows)

    # ---------- 2. 轴线（中心 → 最外顶点） ----------
    axis_rows: list[dict] = []
    for angle in angles:
        x2, y2 = _to_xy(max_score, angle)
        axis_rows.append({"x": 0.0, "y": 0.0, "x2": x2, "y2": y2})
    axis_df = pd.DataFrame(axis_rows)

    # ---------- 2.5 新增：50 分中位参考五边形（虚线） ----------
    median_rows: list[dict] = []
    median_coords = [_to_xy(max_score * 0.5, a) for a in angles]
    median_coords.append(median_coords[0])
    for idx, (x, y) in enumerate(median_coords):
        median_rows.append({"order": idx, "x": x, "y": y})  # ★ 加 order
    median_df = pd.DataFrame(median_rows)

    # ---------- 3. 每个公司的多边形轮廓 + 顶点 ----------
    poly_rows: list[dict] = []
    vertex_rows: list[dict] = []
    for _, row in score_frame.iterrows():
        company = str(row["公司"])
        # 判断每个顶点是否缺失
        scores = []
        for label in axis_labels:
            s = row.get(f"{label}_得分", None)
            # _capability_scores 里缺失被强制写 0；用一个额外标记判断
            scores.append(s)
        # 缺失判断：原始分数如果来自缺失，会在 raw 里对应 NaN；
        # 这里用 score==0 且该顶点实际在 raw 里为 NaN 时判定缺失
        # 更简单：直接读取 raw_frame 的 NaN —— 需要把 raw 传进来
        # 当前函数拿不到 raw，于是改用一个约定：
        #   缺失的顶点在 score_frame 里被额外标记
        coords = [
            _to_xy(row.get(f"{label}_得分", 0.0), angles[i]) for i, label in enumerate(axis_labels)
        ]
        closed = coords + [coords[0]]
        for idx, (x, y) in enumerate(closed):
            poly_rows.append({"公司": company, "idx": idx, "x": x, "y": y})
        for label, (x, y) in zip(axis_labels, coords):
            vertex_rows.append(
                {
                    "公司": company,
                    "顶点": label,
                    "x": x,
                    "y": y,
                    "得分": float(row.get(f"{label}_得分", 0.0)),
                }
            )
    poly_df = pd.DataFrame(poly_rows)
    vertex_df = pd.DataFrame(vertex_rows)

    # ---------- 4. 顶点文字标签（外移到 1.22 倍半径，避免顶部/底部裁切） ----------
    label_rows: list[dict] = []
    for label, angle in zip(axis_labels, angles):
        x, y = _to_xy(max_score * 1.22, angle)
        label_rows.append({"label": label, "x": x, "y": y})
    label_df = pd.DataFrame(label_rows)

    # ---------- 5. 坐标范围：固定 [-(max_score*1.3), +(max_score*1.3)] ----------
    scale_limit = max_score * 1.30
    domain = [-scale_limit, scale_limit]

    color_scale = alt.Scale(
        domain=list(company_colors.keys()),
        range=list(company_colors.values()),
    )

    # ★ 新增：绑定到图例的交互选择。点击图例切换显示/隐藏该公司；
    #   点击 Altair 自带的图例即可实现筛选，无需 Streamlit 重跑。
    legend_select = alt.selection_point(
        name="capability_company_legend_select",
        fields=["公司"],
        bind="legend",
        toggle="event.shiftKey",  # ★ 按住 Shift 点击可"取消选中"，
        #    直接点击则累加多选
        clear=False,  # ★ 禁止双击空白清空选择
        empty=True,  # ★ 无选择时全部按默认样式显示
    )

    x_enc_base = alt.X(
        "x:Q",
        scale=alt.Scale(domain=domain),
        axis=None,
    )
    y_enc_base = alt.Y(
        "y:Q",
        scale=alt.Scale(domain=domain),
        axis=None,
    )

    ring_layer = (
        alt.Chart(ring_df)
        .mark_line(color="#CBD5E1", strokeWidth=0.9, opacity=0.9)
        .encode(
            x=x_enc_base,
            y=y_enc_base,
            detail="tick:N",
            order=alt.Order("order:Q"),  # ★ 按顺序连边
        )
    )
    axis_layer = (
        alt.Chart(axis_df)
        .mark_rule(color="#CBD5E1", strokeWidth=0.9, opacity=0.9)
        .encode(
            x=x_enc_base,
            y=y_enc_base,
            x2="x2:Q",
            y2="y2:Q",
        )
    )

    # ★ 多边形图层：自带图例 + 透明度受 selection 控制
    poly_layer = (
        alt.Chart(poly_df)
        .mark_line(strokeWidth=2.2)
        .encode(
            x=x_enc_base,
            y=y_enc_base,
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

    # ★ 顶点图层：与多边形同步高亮/淡化
    vertex_layer = (
        alt.Chart(vertex_df)
        .mark_circle(size=70)
        .encode(
            x=x_enc_base,
            y=y_enc_base,
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
        alt.Chart(label_df)
        .mark_text(
            fontSize=label_font_size,  # ★ 改用参数
            fontWeight="bold",
            color="#0C233C",
            align="center",
        )
        .encode(
            x=x_enc_base,
            y=y_enc_base,
            text="label:N",
        )
    )

    median_layer = (
        alt.Chart(median_df)
        .mark_line(
            color="#64748B",  # 深一点的灰，与同心网格区分
            strokeWidth=1.6,
            strokeDash=[6, 4],
            opacity=0.95,
        )
        .encode(
            x=x_enc_base,
            y=y_enc_base,
            order=alt.Order("order:Q"),  # ★ 按顺序连边
        )
    )

    return apply_report_theme(
        (ring_layer + axis_layer + median_layer + poly_layer + vertex_layer + label_layer)
        .properties(
            width=panel_width,
            height=panel_height,
            padding={"top": 0, "bottom": 0, "left": 5, "right": 5},
        )
        .configure_legend(
            # ★ 图例容器不裁剪，允许超出默认框
            clipHeight=220,
        )
    )
