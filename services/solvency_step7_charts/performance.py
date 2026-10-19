"""公司业绩模式：收入/利润排名轨迹与模式分类。"""

from __future__ import annotations

import altair as alt
import pandas as pd

from .primitives import apply_report_theme
from .structure import display_period_label

# 展示顺序（和参考图一致）
PERFORMANCE_PATTERN_ORDER = (
    "逐年提升",
    "稳健经营",
    "V形反转",
    "大起大落",
    "持续恶化",
    "倒V形反转",  # 参考图没有，但保留兜底
    "其他",
)


def classify_performance_pattern(
    rank_series: pd.Series,
    *,
    improvement_threshold: int = 2,
    fluctuation_threshold: int = 3,
    stable_threshold: int = 2,
) -> str:
    """根据利润排名序列判断业绩模式。

    参数
    ----
    rank_series            : 按报告期排序的利润排名（越小越好）
    improvement_threshold  : 首尾名次变化达到多少才算"趋势类"
    fluctuation_threshold  : 振幅（max-min）达到多少视为剧烈波动
    stable_threshold       : 振幅不超过多少视为"稳健经营"
    """
    values = [float(v) for v in rank_series.dropna().tolist()]
    n = len(values)
    if n < 3:
        return "其他"

    p_first, p_last = values[0], values[-1]
    p_min, p_max = min(values), max(values)
    amplitude = p_max - p_min
    net_change = p_first - p_last  # 正数 = 名次改善（变小）

    # 1. 稳健经营：整体振幅很小（常年在头部小幅波动，如人保 1-2 名）
    if amplitude <= stable_threshold:
        return "稳健经营"

    # 相邻差分方向序列（去掉持平的 0）：+ = 名次变差，- = 名次变好
    signs: list[int] = []
    for i in range(1, n):
        d = values[i] - values[i - 1]
        if d > 0:
            signs.append(1)
        elif d < 0:
            signs.append(-1)
    if not signs:
        return "稳健经营"

    # 方向转折次数：相邻方向不同即记一次（降→升、升→降都算）
    switches = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])

    # 2. 无转折 = 单调趋势
    if switches == 0:
        if signs[0] < 0 and net_change >= improvement_threshold:
            return "逐年提升"
        if signs[0] > 0 and net_change <= -improvement_threshold:
            return "持续恶化"
        # 单调但首尾变化不足：振幅大算大起大落，否则仍算稳健
        if amplitude >= fluctuation_threshold:
            return "大起大落"
        return "稳健经营"

    # 3. 一次转折：V 形 / 倒V 形
    if switches == 1:
        # 先变差(+)后变好(-)：中间是谷底（最差名次），即 V 形
        if signs[0] > 0 and signs[-1] < 0:
            return "V形反转"
        # 先变好(-)后变差(+)：中间是峰顶（最好名次），即 倒V 形
        if signs[0] < 0 and signs[-1] > 0:
            return "倒V形反转"

    # 4. 两次及以上转折：方向反复（如降→升→降）= 大起大落
    if switches >= 2:
        return "大起大落"

    # 兜底
    if amplitude >= fluctuation_threshold:
        return "大起大落"
    return "其他"


def build_performance_pattern_trajectory(
    frame: pd.DataFrame,
    company_name: str,
    period_order: list[str],
    *,
    revenue_code: str = "SIGNED_PREMIUM",
    profit_code: str = "NET_PROFIT",
    line_color: str = "#C8102E",
    panel_width: int = 190,
    panel_height: int = 190,
    compact: bool = True,
) -> alt.Chart:
    """构建单家公司的收入—利润排名轨迹图（分列展示版）。

    compact=True 时隐藏轴标题（因为列头已经表达），
    只保留刻度标签。
    """
    df = frame.copy()
    df["数值"] = pd.to_numeric(df["数值"], errors="coerce")
    df["报告期"] = df["报告期"].astype(str)
    df["公司"] = df["公司"].astype(str).str.strip()

    all_companies = list(
        dict.fromkeys(c for c in df["公司"].dropna().astype(str) if c and c != "nan")
    )
    n_companies = len(all_companies)
    if n_companies == 0:
        raise ValueError("无公司数据。")

    # ---------- 0. 辅助函数：优先选"本季度数/本季度末数"口径 ----------
    #   同一报告期同一指标可能有多个"期间口径"（本季度数 / 本年度累计数 / 期末数等），
    #   直接 drop_duplicates(keep="last") 可能选到不想要的那条，导致某些年份"丢失"。
    #   这里显式按优先级选：季度口径优先，其次累计口径，最后才按原始顺序取第一条。
    _PREFERRED_SCOPE_KEYWORDS = ("本季度", "期末", "本季末", "当期")

    def _pick_company_value(sub_df: pd.DataFrame) -> pd.DataFrame:
        """从同一 (公司, 报告期) 的多条记录中，选一条最能代表当期水平的值。"""
        if sub_df.empty:
            return sub_df
        if "期间口径" not in sub_df.columns:
            return sub_df.dropna(subset=["数值"]).drop_duplicates("公司", keep="first")

        def _scope_priority(scope: object) -> int:
            text = str(scope or "").strip()
            # 优先级：本季度类 = 0，其他 = 1（越小越优先）
            for keyword in _PREFERRED_SCOPE_KEYWORDS:
                if keyword in text:
                    return 0
            return 1

        work = sub_df.dropna(subset=["数值"]).copy()
        work["_scope_priority"] = work["期间口径"].map(_scope_priority)
        # 先按优先级排序，再按原始顺序保留第一条
        work = work.sort_values(
            ["公司", "_scope_priority"],
            kind="stable",
        )
        return work.drop_duplicates("公司", keep="first")

    rank_rows: list[dict] = []

    for period in period_order:
        period_df = df[df["报告期"].eq(str(period))]
        revenue = _pick_company_value(
            period_df[period_df["指标编码"].astype(str).eq(revenue_code)][
                ["公司", "数值", "期间口径"]
            ]
            if "期间口径" in period_df.columns
            else period_df[period_df["指标编码"].astype(str).eq(revenue_code)][["公司", "数值"]]
        )
        profit = _pick_company_value(
            period_df[period_df["指标编码"].astype(str).eq(profit_code)][
                ["公司", "数值", "期间口径"]
            ]
            if "期间口径" in period_df.columns
            else period_df[period_df["指标编码"].astype(str).eq(profit_code)][["公司", "数值"]]
        )
        if revenue.empty or profit.empty:
            continue
        revenue["收入排名"] = revenue["数值"].rank(ascending=False, method="min")
        profit["利润排名"] = profit["数值"].rank(ascending=False, method="min")
        # ★ 改为 how="outer"，让"只有收入没利润"或反之的年份也能保留（后续在绘图里区分）
        merged = revenue[["公司", "收入排名"]].merge(
            profit[["公司", "利润排名"]], on="公司", how="outer"
        )
        merged["报告期"] = str(period)
        merged["显示年"] = merged["报告期"].map(display_period_label)
        rank_rows.append(merged)

    if not rank_rows:
        raise ValueError(f"{company_name} 缺少收入/利润数据。")

    rank_df = pd.concat(rank_rows, ignore_index=True)
    company_df = (
        rank_df[rank_df["公司"].eq(company_name)]
        .sort_values("报告期")
        .dropna(subset=["收入排名", "利润排名"], how="all")  # ★ 至少一个存在
        .reset_index(drop=True)
    )
    if company_df.empty:
        raise ValueError(f"{company_name} 无收入/利润排名数据。")

    # ---------- 自适应坐标轴：按该公司自身排名范围缩放 ----------
    #   固定 [N,1] 时，排名小幅波动在小图里几乎看不出移动；
    #   这里取该公司收入/利润排名的最小-最大值，上下留 0.5 边距，
    #   让轨迹波动在小图里更明显；刻度数随范围自适应。
    rev_vals = company_df["收入排名"].dropna().astype(float)
    prof_vals = company_df["利润排名"].dropna().astype(float)
    if not rev_vals.empty:
        rev_lo, rev_hi = float(rev_vals.min()), float(rev_vals.max())
    else:
        rev_lo, rev_hi = 1.0, 2.0
    if not prof_vals.empty:
        prof_lo, prof_hi = float(prof_vals.min()), float(prof_vals.max())
    else:
        prof_lo, prof_hi = 1.0, 2.0
    # 若所有年份排名相同，向外扩 ±1，避免退化成单点
    if rev_lo == rev_hi:
        rev_lo -= 1.0
        rev_hi += 1.0
    if prof_lo == prof_hi:
        prof_lo -= 1.0
        prof_hi += 1.0
    # 反向坐标：排名 1 在右上角；domain 左/下为大值
    x_domain = [rev_hi + 0.5, rev_lo - 0.5]
    y_domain = [prof_hi + 0.5, prof_lo - 0.5]
    x_ticks = max(2, int(rev_hi - rev_lo + 1))
    y_ticks = max(2, int(prof_hi - prof_lo + 1))

    x_axis = alt.Axis(
        title=None,
        labelFontSize=7,
        tickCount=x_ticks,
        tickMinStep=1,
        grid=False,
        domain=False,
        ticks=False,
    )
    y_axis = alt.Axis(
        title=None,
        labelFontSize=7,
        tickCount=y_ticks,
        tickMinStep=1,
        grid=False,
        domain=False,
        ticks=False,
    )

    base = alt.Chart(company_df)

    line = base.mark_line(
        color=line_color,
        strokeWidth=1.4,
        opacity=0.9,
    ).encode(
        x=alt.X(
            "收入排名:Q", scale=alt.Scale(domain=x_domain, zero=False, nice=False), axis=x_axis
        ),
        y=alt.Y(
            "利润排名:Q", scale=alt.Scale(domain=y_domain, zero=False, nice=False), axis=y_axis
        ),
        order=alt.Order("报告期:N"),
    )

    points = base.mark_circle(
        size=40,
        color=line_color,
        opacity=0.95,
    ).encode(
        x="收入排名:Q",
        y="利润排名:Q",
        tooltip=[
            "公司:N",
            "报告期:N",
            alt.Tooltip("收入排名:Q", format=".0f"),
            alt.Tooltip("利润排名:Q", format=".0f"),
        ],
    )

    # ---------- 3. 标签：把同坐标的年份合并成一个文本，避免重叠 ----------
    #   原因：只有 8 家公司时，同一家公司相邻年份的排名很容易完全相同
    #   （如平安产险 2024、2025 的利润排名都是 2），位置重合会导致
    #   "看起来只有 1 个年份"，用户无法区分。
    #   这里把同一 (收入排名, 利润排名) 上的所有年份合并为 "2024/2025"，
    #   一个坐标只画一个标签。line 和 points 仍用原始数据，连线轨迹不变。
    _label_df = (
        company_df.assign(显示年=company_df["显示年"].astype(str))
        .groupby(["收入排名", "利润排名"], as_index=False)
        .agg(
            {
                "显示年": lambda series: "/".join(sorted(set(series.dropna().astype(str)))),
                "报告期": "min",
            }
        )
    )

    labels = (
        alt.Chart(_label_df)
        .mark_text(
            dy=-8,
            fontSize=7,
            color="#333333",
            fontWeight="bold",
        )
        .encode(
            x=alt.X(
                "收入排名:Q",
                scale=alt.Scale(domain=x_domain, zero=False, nice=False),
            ),
            y=alt.Y(
                "利润排名:Q",
                scale=alt.Scale(domain=y_domain, zero=False, nice=False),
            ),
            text=alt.Text("显示年:N"),
        )
    )

    # ★ 点大小按"该坐标有几个年份"微调：重合越多，点画得越大，
    #   让用户能直观感受到"这里其实堆了好几年"。
    _point_df = company_df.merge(
        company_df.groupby(["收入排名", "利润排名"]).size().rename("同坐标年份数").reset_index(),
        on=["收入排名", "利润排名"],
        how="left",
    )
    _point_df["点大小"] = 30 + _point_df["同坐标年份数"].clip(upper=5) * 12

    points = (
        alt.Chart(_point_df)
        .mark_circle(
            color=line_color,
            opacity=0.95,
        )
        .encode(
            x=alt.X(
                "收入排名:Q",
                scale=alt.Scale(domain=x_domain, zero=False, nice=False),
            ),
            y=alt.Y(
                "利润排名:Q",
                scale=alt.Scale(domain=y_domain, zero=False, nice=False),
            ),
            size=alt.Size(
                "点大小:Q",
                scale=None,
                legend=None,
            ),
            tooltip=[
                "公司:N",
                "报告期:N",
                alt.Tooltip("收入排名:Q", format=".0f"),
                alt.Tooltip("利润排名:Q", format=".0f"),
                alt.Tooltip("同坐标年份数:Q", title="同坐标年份数", format=".0f"),
            ],
        )
    )

    return apply_report_theme(
        (line + points + labels).properties(
            width=panel_width,
            height=panel_height,
        )
    )
