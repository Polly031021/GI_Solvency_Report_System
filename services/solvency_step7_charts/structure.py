"""结构变化组合图：100% 堆叠柱 + 同比增速折线。"""

from __future__ import annotations

import re

import altair as alt
import pandas as pd

from .primitives import apply_report_theme


def display_period_label(period: object) -> str:
    """把 '2021Q4' / '2021年第4季度' 等报告期标签统一显示为 '2021'。"""
    text = str(period).strip()
    match = re.match(r"^(\d{4})", text)
    return match.group(1) if match else text


def build_structure_change_chart(
    frame: pd.DataFrame,
    *,
    category_map: dict[str, str],
    total_code: str,
    period_order: list[str],
    company_name: str,
    y_left_title: str = "占比（%）",
    y_right_title: str = "同比增速（%）",
    color_map: dict[str, str] = None,
    line_color_map: dict[str, str] = None,
    show_legend: bool = True,
) -> alt.Chart:
    """构建单家公司的结构变化组合图：100%堆叠柱 + 双轴同比折线。

    ★ 改进点（对照 KPMG 参考图）：
      1. 左轴范围智能判断：占比类固定 0-120%；率类（如净利润率）自适应
      2. 右轴 tick 数量限制为 5，避免刻度挤成一团
      3. 折线图例名改为"车险同比增速"这种全名，让用户一眼看懂折线含义
      4. 折线用暖色系（橙红/紫），和柱子的冷色系（深蓝/浅蓝）明显区分
      5. 两套图例（柱状 / 折线）在底部并排显示
    """
    # 1. 数据清洗与准备
    df = frame[frame["公司"] == company_name].copy()
    df = df[df["报告期"].isin(period_order)]
    if df.empty:
        # ★ 公司无任何数据时，也要生成占位数据（避免抛异常）
        df = pd.DataFrame(columns=["公司", "报告期", "指标编码", "数值"])
    df["数值"] = pd.to_numeric(df["数值"], errors="coerce")

    # 2. 提取总量数据（用于算占比）
    if total_code:
        _total_src = df[df["指标编码"] == total_code]
        if not _total_src.empty:
            total_df = _total_src.drop_duplicates(["报告期"]).set_index("报告期")["数值"]
        else:
            total_df = pd.Series(dtype=float)  # ★ 空 Series，避免索引报错
    else:
        total_df = None

    records = []
    for code, label in category_map.items():
        sub_df = df[df["指标编码"] == code]
        if not sub_df.empty:
            sub_df = sub_df.drop_duplicates(["报告期"]).set_index("报告期")
        else:
            sub_df = pd.DataFrame(columns=["数值"])  # ★ 空 DataFrame

        for period in period_order:
            val = float(sub_df.loc[period, "数值"]) if period in sub_df.index else 0.0
            if total_df is not None and period in total_df.index:
                total = float(total_df.loc[period])
            else:
                total = 0.0
            ratio = (val / total * 100) if total != 0 else 0.0
            records.append(
                {
                    "报告期": period,
                    "显示期": display_period_label(period),
                    "类别": label,
                    "数值": val,
                    "占比": ratio,
                }
            )

    plot_df = pd.DataFrame(records)
    if plot_df.empty:
        # ★ 最终兜底：连 records 都没生成，就构造最小空数据
        plot_df = pd.DataFrame(
            {
                "报告期": period_order,
                "显示期": [display_period_label(p) for p in period_order],
                "类别": [list(category_map.values())[0]] * len(period_order),
                "数值": [0.0] * len(period_order),
                "占比": [0.0] * len(period_order),
            }
        )

    # ---------- 3. 计算同比增速 ----------
    plot_df = plot_df.sort_values("报告期")
    _prev_values = plot_df.groupby("类别")["数值"].shift(1)
    _safe_prev = _prev_values.where(_prev_values.abs() > 1e-9)
    plot_df["同比增速"] = (plot_df["数值"] - _prev_values) / _safe_prev * 100
    plot_df["同比增速"] = pd.to_numeric(plot_df["同比增速"], errors="coerce")

    # ★ 新增：折线图例名 = 类别名 + "同比增速"
    #   例：类别人"车险" → 折线标签"车险同比增速"
    plot_df["折线标签"] = plot_df["类别"].astype(str) + "同比增速"

    # ---------- 4. 颜色配置 ----------
    if color_map is None:
        color_map = {
            "车险": "#00338D",
            "非车险": "#00B8F5",
            "核心资本": "#00338D",
            "附属资本": "#00B8F5",
            "净利润率": "#00338D",
            "其他": "#00B8F5",
        }

    # ★ 只保留实际出现的类别，避免图例显示未使用的项
    _used_categories = list(dict.fromkeys(plot_df["类别"].astype(str)))
    _effective_color_map = {k: v for k, v in color_map.items() if k in _used_categories}
    for _i, _cat in enumerate(_used_categories):
        if _cat not in _effective_color_map:
            _effective_color_map[_cat] = ["#00338D", "#00B8F5", "#B497FF", "#7213EA"][_i % 4]

    # 折线暖色系配色
    _line_palette = ["#E36C0A", "#9B3FB5", "#D9376E", "#2CA02C"]
    if line_color_map is None:
        line_color_map = {
            _key: _line_palette[i % len(_line_palette)]
            for i, _key in enumerate(_effective_color_map.keys())
        }

    # 折线的 color scale：key 用"车险同比增速"这种全名
    _line_domain = [f"{k}同比增速" for k in _effective_color_map.keys()]
    _line_range = [
        line_color_map.get(k, _line_palette[i % len(_line_palette)])
        for i, k in enumerate(_effective_color_map.keys())
    ]

    # ---------- 5. 左轴范围：智能判断 ----------
    #   - 占比类（最大值 > 80%）→ 固定 0-120%，和参考图一致
    #   - 中等（30%~80%）→ 0-100%
    #   - 率类（< 30%，如净利润率）→ 自适应到 [0, max*1.5]
    _ratio_values = pd.to_numeric(plot_df["占比"], errors="coerce").dropna()
    if _ratio_values.empty:
        _ratio_upper, _ratio_lower = 120.0, 0.0
    else:
        _ratio_max = float(_ratio_values.max())
        _ratio_min = float(_ratio_values.min())
        if _ratio_max > 80.0:
            _ratio_upper = 120.0
            _ratio_lower = 0.0
        elif _ratio_max > 30.0:
            _ratio_upper = 100.0
            _ratio_lower = min(0.0, _ratio_min * 1.15)
        else:
            _ratio_upper = max(_ratio_max * 1.5, 10.0)
            _ratio_lower = min(0.0, _ratio_min * 1.5)

    # ---------- 6. 右轴范围：优先固定 [-10, 35]，超出则扩展 ----------
    #   参考图右轴范围 -10% ~ 35%
    _growth_values = pd.to_numeric(plot_df["同比增速"], errors="coerce").dropna()
    # 剔除异常值（|值| > 500 视为噪声，不影响轴范围）
    _growth_values_clean = _growth_values[_growth_values.abs() < 500]
    if _growth_values_clean.empty:
        _growth_upper, _growth_lower = 35.0, -10.0
    else:
        _growth_max = float(_growth_values_clean.max())
        _growth_min = float(_growth_values_clean.min())
        _growth_upper = max(35.0, _growth_max * 1.15)
        _growth_lower = min(-10.0, _growth_min * 1.15)
        if _growth_upper <= _growth_lower:
            _growth_upper = _growth_lower + 1.0

    _ratio_scale = alt.Scale(domain=[_ratio_lower, _ratio_upper])
    _growth_scale = alt.Scale(domain=[_growth_lower, _growth_upper])

    # 横轴始终按"报告期顺序"排列，而不是按数值大小排序
    _display_period_order = [display_period_label(p) for p in period_order]

    # ---------- 7. 构建图表 ----------
    base = alt.Chart(plot_df)

    # 7.1 堆叠柱状图（左轴）
    bars = base.mark_bar(size=36).encode(
        x=alt.X(
            "显示期:N",
            sort=_display_period_order,
            title=None,
            axis=alt.Axis(labelAngle=0, labelFontSize=11),
        ),
        y=alt.Y(
            "占比:Q",
            title="占比(%)",  # ★ 简化标题
            scale=_ratio_scale,
            axis=alt.Axis(
                format=".0f",
                titleColor="#888",
                titleFontSize=10,
                titleAngle=0,  # ★ 水平摆放
                titleAnchor="middle",
                titlePadding=15,
                labelFontSize=9,
                labelColor="#888",
                tickCount=6,
                tickMinStep=10,
            ),
        ),
        color=alt.Color(
            "类别:N",
            scale=alt.Scale(
                domain=list(_effective_color_map.keys()),
                range=list(_effective_color_map.values()),
            ),
            legend=(
                alt.Legend(
                    title=None,
                    orient="bottom",
                    columns=2,
                    direction="horizontal",
                    labelFontSize=11,
                    symbolType="square",
                    symbolSize=120,
                    offset=10,
                )
                if show_legend
                else None
            ),
        ),
        tooltip=[
            "显示期",
            "类别",
            alt.Tooltip("数值:Q", format=",.0f"),
            alt.Tooltip("占比:Q", format=".2f"),
        ],
    )

    # 7.2 柱子内部的数据标签
    bar_text = base.mark_text(
        dy=0,
        fontSize=11,
        fontWeight="bold",
        baseline="middle",
    ).encode(
        x=alt.X("显示期:N", sort=_display_period_order),
        y=alt.Y("占比:Q", scale=_ratio_scale),
        text=alt.Text("占比:Q", format=".0f"),
        color=alt.value("#FFFFFF"),
    )

    # 7.3 同比增速折线图（右轴）
    #   ★ 关键：color 用"折线标签"（如"车险同比增速"），图例名一眼看懂
    lines = base.mark_line(
        point=alt.OverlayMarkDef(size=70, filled=True),
        strokeWidth=2.4,
    ).encode(
        x=alt.X("显示期:N", sort=_display_period_order),
        y=alt.Y(
            "同比增速:Q",
            # ★ 关键：右轴完全不显示
            #   原因：resolve_scale(y="independent") 后 Altair 会把两个 Y 轴
            #   都渲染到左侧，空间不足时溢出到右侧并与刻度叠加。
            #   折线每点已有数据标签（如 10.6 / 3.8 / 3.2），无需看轴。
            axis=None,  # ★ 隐藏右轴的标题和刻度
            scale=_growth_scale,
        ),
        color=alt.Color(
            "折线标签:N",
            scale=alt.Scale(domain=_line_domain, range=_line_range),
            legend=(
                alt.Legend(
                    title=None,
                    orient="bottom",
                    columns=2,
                    direction="horizontal",
                    labelFontSize=11,
                    symbolType="stroke",
                    symbolSize=120,
                    offset=10,
                )
                if show_legend
                else None
            ),
        ),
        tooltip=["显示期", "类别", alt.Tooltip("同比增速:Q", format=".2f")],
    )

    # 7.4 折线上的数据标签
    # ★ 让不同类别的折线标签在垂直方向错开，避免重叠
    #   给每个"折线标签"分配一个 dy 偏移：第一个 -14，第二个 -28，第三个 -42……
    _line_labels = list(dict.fromkeys(plot_df["折线标签"].astype(str)))
    _label_dy_map = {_lbl: -(14 + 14 * _i) for _i, _lbl in enumerate(_line_labels)}
    plot_df["_dy_offset"] = plot_df["折线标签"].map(_label_dy_map).fillna(-14)

    line_text = base.mark_text(
        fontSize=10,
        fontWeight="bold",
        baseline="bottom",
    ).encode(
        x=alt.X("显示期:N", sort=_display_period_order),
        y=alt.Y("同比增速:Q", scale=_growth_scale),
        # ★ 按类别动态偏移，第一条 -14，第二条 -28，错开两层
        dy=alt.Y("_dy_offset:Q", scale=None),
        text=alt.Text("同比增速:Q", format=".1f"),
        color=alt.Color(
            "折线标签:N",
            scale=alt.Scale(domain=_line_domain, range=_line_range),
            legend=None,
        ),
    )

    # ---------- 8. 组合图表 ----------
    #   ★ 关键：color="independent" 让柱子与折线各有独立颜色 scale
    #         （否则折线颜色会被柱子的颜色 scale 覆盖）
    chart = (
        alt.layer(bars, bar_text, lines, line_text)
        .resolve_scale(
            y="independent",
            color="independent",
        )
        .properties(
            title=alt.TitleParams(
                text=company_name,
                anchor="middle",
                fontSize=15,
                color="#00338D",
                offset=10,
            ),
            width=520,
            height=340,
        )
    )

    return apply_report_theme(chart)
