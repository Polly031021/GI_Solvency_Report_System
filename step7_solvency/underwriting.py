"""承保效益散点图：综合成本率/赔付率/费用率与保费排名分布。"""

from __future__ import annotations

import math

import altair as alt
import pandas as pd
import streamlit as st


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
    df["数值"] = (
        df["数值"].astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False)
    )
    df["数值"] = pd.to_numeric(df["数值"], errors="coerce")

    # 3. 找出所有包含所需指标的报告期
    required_codes = [
        "COMBINED_RATIO",
        "LOSS_RATIO",
        "EXPENSE_RATIO",
        "SIGNED_PREMIUM",
        "PREMIUM_INCOME",
    ]
    valid_periods = df[df["指标编码"].isin(required_codes)]["报告期"].unique().tolist()

    if not valid_periods:
        st.warning(
            f"当前 STEP7 数据中未找到任何所需指标（{required_codes}）。请检查 STEP5/STEP6 是否包含这些数据。"
        )
        return pd.DataFrame()

    valid_periods.sort(reverse=True)
    latest_period = valid_periods[0]

    # 4. 计算保费排名（优先 SIGNED_PREMIUM，缺失则回退 PREMIUM_INCOME）
    premium_df = (
        df[
            (df["报告期"] == latest_period)
            & (df["指标编码"].isin(["SIGNED_PREMIUM", "PREMIUM_INCOME"]))
        ]
        .drop_duplicates(subset=["公司"], keep="last")
        .copy()
    )

    if premium_df.empty:
        st.warning(f"报告期 {latest_period} 未找到 SIGNED_PREMIUM 或 PREMIUM_INCOME 数据。")
        return pd.DataFrame()

    premium_df = premium_df.sort_values("数值", ascending=False).reset_index(drop=True)
    premium_df["排名"] = range(1, len(premium_df) + 1)
    rank_map = premium_df.set_index("公司")["排名"].to_dict()

    # 5. 提取三个比率指标
    metrics = ["COMBINED_RATIO", "LOSS_RATIO", "EXPENSE_RATIO"]
    metric_df = (
        df[(df["报告期"] == latest_period) & (df["指标编码"].isin(metrics))]
        .drop_duplicates(subset=["公司", "指标编码"], keep="last")
        .copy()
    )

    if metric_df.empty:
        st.warning(
            f"报告期 {latest_period} 未找到 COMBINED_RATIO / LOSS_RATIO / EXPENSE_RATIO 数据。"
        )
        return pd.DataFrame()

    # 6. 合并并计算排名
    metric_df["排名"] = metric_df["公司"].map(rank_map)
    metric_df = metric_df.dropna(subset=["排名", "数值"])

    if metric_df.empty:
        st.warning(
            f"公司名称无法匹配：保费数据中有 {len(rank_map)} 家公司，比率数据中有 {metric_df['公司'].nunique()} 家公司。"
        )
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
    data: pd.DataFrame, chart_config: dict | None = None, key_prefix: str = ""
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
        "EXPENSE_RATIO": "综合费用率",
    }

    for code in ["COMBINED_RATIO", "LOSS_RATIO", "EXPENSE_RATIO"]:
        sub_df = df[df["指标编码"] == code].copy()
        if sub_df.empty:
            continue

        label = label_map.get(code, code)
        median_val = sub_df["中位数"].iloc[0]  # 中位数还是按全体样本算

        # ★ 改动：纵轴范围改为"自动适配 + 合理的异常值护栏"。
        #   思路：
        #   1) 先用一个宽的"合理性区间"筛掉明显异常值（异常值不进图，只进下方附表）；
        #   2) 剩下的正常值，用它们的 min/max 加上 8% padding 作为纵轴范围；
        #   3) 但纵轴不能小于一个最小可视跨度（防止所有点完全重合时纵轴过窄）；
        #   4) 综合成本率的下限不能低于 40（否则失去业务含义）。
        if code == "COMBINED_RATIO":
            OUTLIER_LOWER = 40.0  # 异常值下限（低于此视为异常）
            OUTLIER_UPPER = 150.0  # 异常值上限
            MIN_DOMAIN_LOWER = 40.0  # 纵轴最小下限
        else:
            OUTLIER_LOWER = 0.0
            OUTLIER_UPPER = 150.0
            MIN_DOMAIN_LOWER = 0.0

        # ---- 先用"合理性区间"筛掉异常值 ----
        normal_df = sub_df[
            (sub_df["数值"] >= OUTLIER_LOWER) & (sub_df["数值"] <= OUTLIER_UPPER)
        ].copy()
        outlier_df = sub_df[
            (sub_df["数值"] < OUTLIER_LOWER) | (sub_df["数值"] > OUTLIER_UPPER)
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
            ""
            if _has_real_variance
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
                size=90,  # ★ 稍大一点
                color="#A52A2A",
                opacity=0.9,
                stroke="#FFFFFF",  # ★ 白色描边
                strokeWidth=1.2,  # ★ 让点更醒目
            )
            .encode(
                x=alt.X(
                    "排名:Q",
                    title="保费收入排名",
                    scale=alt.Scale(zero=False, padding=10),
                    axis=alt.Axis(tickMinStep=1),
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
                    alt.Tooltip("数值:Q", title=label, format=".2f"),
                ],
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

        chart = chart_layers.properties(
            title=alt.TitleParams(
                text=f"{latest_period} 财险公司{label}分布",
                anchor="middle",
                fontSize=14,
                color="#333333",
            ),
            width=700,
            height=300,
        ).configure_view(stroke=None)

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
