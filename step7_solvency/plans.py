"""多指标组合图的调度中心：按图表类型分派到具体构建函数。"""

from __future__ import annotations

from html import escape
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from dashboard_components import render_key_solvency_overview
from dashboard_components import render_major_financing
from services.solvency_navigation import KPMG_CAPITAL_COMBO_COLORS
from services.solvency_navigation import MARKET_RISK_ASSET_SCATTER
from services.solvency_navigation import metric_codes_for_chart
from services.step7_excel_config import Step7Module
from services.solvency_step7_chart_plans import COMPANY_PERIOD_BAR
from services.solvency_step7_chart_plans import CAPITAL_AMOUNT_COMBO
from services.solvency_step7_chart_plans import CAPITAL_EFFICIENCY_BUBBLE
from services.solvency_step7_chart_plans import CAPITAL_RATIO_COMBO
from services.solvency_step7_chart_plans import CAPITAL_STRUCTURE_COMBO
from services.solvency_step7_chart_plans import CAPABILITY_RADAR
from services.solvency_step7_chart_plans import UNDERWRITING_EFFICIENCY_SCATTER
from services.solvency_step7_chart_plans import STRUCTURE_CHANGE_CHART
from services.solvency_step7_chart_plans import COMPONENT_STACK
from services.solvency_step7_chart_plans import EFFECT_DIVERGING
from services.solvency_step7_chart_plans import FINANCING_TABLE
from services.solvency_step7_chart_plans import KEY_METRICS_TABLE
from services.solvency_step7_chart_plans import MARKET_CREDIT_MATRIX
from services.solvency_step7_chart_plans import PENDING_DEFINITION
from services.solvency_step7_chart_plans import RISK_RATIO_SCATTER
from services.solvency_step7_chart_plans import SOLVENCY_MATRIX
from services.solvency_step7_chart_plans import SOLVENCY_RATIO_COMBO
from services.solvency_step7_chart_plans import chart_plan_for
from services.solvency_step7_chart_plans import PERFORMANCE_PATTERN_GRID
from services.solvency_step7_charts import CAPITAL_STRUCTURE_COLORS
from services.solvency_step7_charts import build_capability_radar_chart
from services.solvency_step7_charts import build_structure_change_chart
from services.solvency_step7_charts import build_capital_amount_combo
from services.solvency_step7_charts import build_capital_ratio_combo_chart
from services.solvency_step7_charts import capital_ratio_amount_axis_domain
from services.solvency_step7_charts import build_company_period_bar_chart
from services.solvency_step7_charts import build_component_stack_chart
from services.solvency_step7_charts import build_effect_diverging_chart
from services.solvency_step7_charts import build_matrix_chart
from services.solvency_step7_charts import build_solvency_ratio_combo_chart
from services.solvency_step7_charts import component_stack_axis_domain
from services.solvency_step7_charts import component_stack_proportion_axis_domain
from services.solvency_step7_charts import metric_bar_axis_domain
from services.solvency_step7_charts import report_period_color_map
from services.solvency_step7_charts import solvency_ratio_axis_domain
from services.solvency_step7_charts import PERFORMANCE_PATTERN_ORDER
from services.solvency_step7_charts import classify_performance_pattern
from services.solvency_step7_charts import build_performance_pattern_trajectory

from .fragments import (
    render_capital_efficiency_bubble_fragment,
    render_risk_ratio_scatter_fragment,
)
from .rendering import (
    chart_without_internal_title,
    convert_unit,
    render_chart_legend,
    render_company_chart_grid,
)
from .selection import company_color_map, ordered_companies
from .specs import (
    COMPONENT_STACK_DENOMINATOR_CODES,
    COMPONENT_STACK_SPECS,
    QUANT_RISK_STACK_RATIO_CODES,
    RISK_RATIO_SCATTER_SPECS,
    RISK_SCATTER_DISPLAY_WIDTH,
    RISK_SCATTER_DUAL_VIEW_CHARTS,
    CAPABILITY_STEP_SCORE_FALLBACK,
    DEFAULT_CAPABILITY_AXES,
)
from .underwriting import render_underwriting_efficiency_scatter


def _chart_input_metric_codes(
    chart_name: str,
    plan_kind: str,
    *,
    metric_codes_override: tuple[str, ...] | None = None,
    chart_config: dict | None = None,
) -> tuple[str, ...]:
    """Keep calculation inputs that are not navigation display metrics.

    优先使用 Excel 提供的 metric_codes_override；否则回退到
    COMPANY_NAVIGATION 里的 metric_codes_for_chart(chart_name)。
    """
    if metric_codes_override:
        codes = list(metric_codes_override)
    else:
        codes = list(metric_codes_for_chart(chart_name))

    cfg = chart_config or {}

    if plan_kind == COMPONENT_STACK:
        # Excel 里可以填 {"denominator_code": "...", "ratio_codes": {...}}
        denominator_code = cfg.get("denominator_code") or COMPONENT_STACK_DENOMINATOR_CODES.get(
            chart_name, ""
        )
        if denominator_code:
            codes.append(denominator_code)
        ratio_codes = cfg.get("ratio_codes") or (
            QUANT_RISK_STACK_RATIO_CODES if chart_name == "量化风险最低资本构成" else {}
        )
        codes.extend(ratio_codes.values())

    if plan_kind == CAPITAL_RATIO_COMBO and chart_name == "核心充足率变化":
        codes.append("ACTUAL_CAPITAL")

    return tuple(dict.fromkeys(codes))


def _score_with_steps(value: float, steps: list[dict]) -> float:
    """按阶梯配置对单个数值打分。

    参数
    ----
    value : 原始指标值（例如偿付能力充足率 185.3 表示 185.3%）
    steps : [{"min": 200, "score": 100}, {"min": 150, "score": 70}, ...]

    规则
    ----
    把 steps 按 min 从大到小排序，命中第一个 value >= min 的档位，就返回该档 score。
    所有档位都没命中（value 比最小档的 min 还小）时，返回最后一档的 score。
    """
    if pd.isna(value):
        return float("nan")
    numeric = float(value)
    sorted_steps = sorted(
        steps,
        key=lambda s: float(s.get("min", float("-inf"))),
        reverse=True,
    )
    for step in sorted_steps:
        threshold = float(step.get("min", float("-inf")))
        if numeric >= threshold:
            return float(step.get("score", 0.0))
    return 0.0


def _capability_scores(
    frame: pd.DataFrame,
    axes_config: list[dict],
    periods: list[str],
    baseline_frame: pd.DataFrame | None = None,
    *,
    group_column: str = "同业分类",
    step_score_fallback: dict[str, list[dict]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """能力五边形打分。

    与旧版不同之处：
    1. 若顶点配置里带 `steps`（或该编码在 step_score_fallback 里），
       则按阶梯打分。例如偿付能力：≥200%=100, 150~200%=70,
       120~150%=50, 100~120%=30, <100%=0。
    2. 其余顶点按「公司所在分类」（同业分类）在同分类内做百分位排名。
       即：大型险企只跟大型险企比，中小险企只跟中小险企比。
    3. 若同分类内有效样本不足 2 家（无法排名），自动回退到全量池，
       避免出现"整个分类所有公司都是 50 分"的情况。

    参数
    ----
    frame            : 当前筛选范围（含 公司 / 指标编码 / 报告期 / 期间口径 / 数值 / 同业分类）
    axes_config      : 每个顶点的配置，可含 steps=[{"min":200,"score":100}, ...]
    periods          : 可用报告期，函数内取最后一个作为"最新期"
    baseline_frame   : 用于构建基准池的数据源（默认用 frame 自身）
    group_column     : 用于分组的列名，默认"同业分类"
    step_score_fallback : 阶梯打分兜底配置，默认用模块级 CAPABILITY_STEP_SCORE_FALLBACK

    返回
    ----
    (score_frame, raw_frame, latest_period)
        score_frame: 列 = 公司 + "<label>_得分"
        raw_frame  : 列 = 公司 + "<label>"（原始值）
    """
    latest_period = str(periods[-1]) if periods else ""
    if frame.empty or not periods or not axes_config:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    # ---------- 公司名归一化（幂等） ----------
    frame = frame.copy()
    frame["公司"] = (
        frame["公司"]
        .astype(str)
        .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
        .str.strip()
    )
    if baseline_frame is not None:
        baseline_frame = baseline_frame.copy()
        baseline_frame["公司"] = (
            baseline_frame["公司"]
            .astype(str)
            .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
            .str.strip()
        )
    else:
        baseline_frame = frame

    period_frame = frame[frame["报告期"].astype(str).eq(latest_period)].copy()
    if period_frame.empty:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    companies = list(
        dict.fromkeys(
            str(c).strip() for c in period_frame["公司"].fillna("").astype(str) if str(c).strip()
        )
    )
    if not companies:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    # ---------- 记录每家公司的分类（用于分组排名） ----------
    group_map: dict[str, str] = {}
    if group_column and group_column in period_frame.columns:
        group_series = (
            period_frame[["公司", group_column]]
            .assign(
                **{
                    group_column: lambda d: (
                        d[group_column]
                        .fillna("未分类")
                        .astype(str)
                        .str.strip()
                        .replace("", "未分类")
                    )
                }
            )
            .drop_duplicates(subset=["公司"], keep="last")
        )
        group_map = dict(zip(group_series["公司"], group_series[group_column]))

    # ---------- 规范化 axes（含 steps） ----------
    normalized_axes: list[dict] = []
    for ax in axes_config:
        code = str(ax.get("code", "")).strip()
        if not code:
            continue
        label = str(ax.get("label", "")).strip() or code
        target_period = str(ax.get("period", "")).strip()
        steps = ax.get("steps")
        # 兼容从 Excel JSON 读进来时 steps 是字符串的情况
        if isinstance(steps, str):
            try:
                import json

                steps = json.loads(steps)
            except Exception:
                steps = None
        normalized_axes.append(
            {
                "code": code,
                "label": label,
                "period": target_period,
                "steps": list(steps) if steps else None,
            }
        )
    if not normalized_axes:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    fallback_steps = (
        step_score_fallback if step_score_fallback is not None else CAPABILITY_STEP_SCORE_FALLBACK
    )

    # ---------- 第 1 遍：收集待展示公司的原始值 ----------
    raw_cache: dict[str, dict[str, float]] = {}
    for company in companies:
        company_rows = period_frame[period_frame["公司"].astype(str).eq(company)]
        raw: dict[str, float] = {}
        for ax in normalized_axes:
            match = company_rows[company_rows["指标编码"].astype(str).eq(ax["code"])].copy()
            if not match.empty and ax["period"]:
                target_period = str(ax["period"]).strip()
                match["_优先级"] = match["期间口径"].astype(str).str.strip().eq(target_period)
                match = match.sort_values(
                    "_优先级",
                    ascending=False,
                    kind="stable",
                ).drop(columns="_优先级")
            value = float("nan")
            if not match.empty:
                numeric = pd.to_numeric(match.iloc[0]["数值"], errors="coerce")
                if pd.notna(numeric):
                    value = float(numeric)
            raw[ax["label"]] = value
        raw_cache[company] = raw

    # ---------- 构建基准池：分「全量池」和「按分类分组池」 ----------
    baseline_period_frame = baseline_frame[
        baseline_frame["报告期"].astype(str).eq(latest_period)
    ].copy()

    # baseline 里每家公司的分类
    baseline_group_map: dict[str, str] = {}
    if group_column and group_column in baseline_period_frame.columns:
        bg = (
            baseline_period_frame[["公司", group_column]]
            .assign(
                **{
                    group_column: lambda d: (
                        d[group_column]
                        .fillna("未分类")
                        .astype(str)
                        .str.strip()
                        .replace("", "未分类")
                    )
                }
            )
            .drop_duplicates(subset=["公司"], keep="last")
        )
        baseline_group_map = dict(zip(bg["公司"], bg[group_column]))

    baseline_pool_all: dict[str, list[float]] = {}
    baseline_pool_by_group: dict[tuple[str, str], list[float]] = {}

    for ax in normalized_axes:
        label = ax["label"]
        rows = baseline_period_frame[
            baseline_period_frame["指标编码"].astype(str).eq(ax["code"])
        ].copy()
        if rows.empty:
            baseline_pool_all[label] = []
            continue

        # 期间口径优先级排序（与原逻辑保持一致）
        if ax["period"]:
            target_period = str(ax["period"]).strip()
            rows["_优先级"] = rows["期间口径"].astype(str).str.strip().eq(target_period)
            rows = (
                rows.sort_values(
                    ["公司", "_优先级"],
                    ascending=[True, False],
                    kind="stable",
                )
                .drop_duplicates(subset=["公司"], keep="first")
                .drop(columns="_优先级")
            )
        else:
            rows = rows.drop_duplicates(subset=["公司"], keep="last")

        # 全量池
        pool_all = pd.to_numeric(rows["数值"], errors="coerce").dropna().tolist()
        baseline_pool_all[label] = pool_all

        # 按分类拆分池
        for _, r in rows.iterrows():
            name = str(r["公司"]).strip()
            if not name:
                continue
            value = pd.to_numeric(pd.Series([r["数值"]]), errors="coerce").iloc[0]
            if pd.isna(value):
                continue
            group_name = baseline_group_map.get(name, "未分类")
            baseline_pool_by_group.setdefault((group_name, label), []).append(float(value))

    # ---------- 第 2 遍：逐公司逐顶点打分 ----------
    raw_rows: list[dict] = []
    score_rows: list[dict] = []
    for company in companies:
        raw = raw_cache[company]
        company_group = group_map.get(company, "未分类")
        raw_row: dict = {"公司": company}
        score_row: dict = {"公司": company}

        for ax in normalized_axes:
            label = ax["label"]
            code = ax["code"]
            value = raw.get(label, float("nan"))
            raw_row[label] = value

            if pd.isna(value):
                # ★ 用 NaN 表示"缺失"，而不是 0.0，让雷达图区分缺数据顶点
                score_row[f"{label}_得分"] = float("nan")
                continue

            # ① 阶梯打分优先（如偿付能力）
            steps = ax.get("steps") or fallback_steps.get(code)
            if steps:
                score_row[f"{label}_得分"] = _score_with_steps(value, steps)
                continue

            # ② 同分类内百分位排名
            pool = baseline_pool_by_group.get((company_group, label), [])
            if len(pool) < 2:
                # 组内不足 2 家（无法排名），退回到全量池
                pool = baseline_pool_all.get(label, [])
            n = len(pool)
            if n == 0:
                score_row[f"{label}_得分"] = 0.0
            elif n == 1:
                score_row[f"{label}_得分"] = 50.0
            else:
                below = sum(1 for v in pool if v < value)
                equal = sum(1 for v in pool if v == value)
                # 平均排名法（1-based），并列取平均名次
                rank = below + (equal + 1) / 2.0
                score = (rank - 1.0) / (n - 1.0) * 100.0
                score_row[f"{label}_得分"] = max(0.0, min(100.0, score))

        raw_rows.append(raw_row)
        score_rows.append(score_row)

    # ---------- 排序与组装结果 ----------
    raw_df = pd.DataFrame(raw_rows)
    score_df = pd.DataFrame(score_rows)
    score_cols = [c for c in score_df.columns if c.endswith("_得分")]
    if score_cols:
        score_df["_axes_with_data"] = score_df[score_cols].apply(
            lambda r: int((r > 0).sum()), axis=1
        )
        score_df = (
            score_df.sort_values(
                ["_axes_with_data", *score_cols],
                ascending=False,
            )
            .drop(columns=["_axes_with_data"])
            .reset_index(drop=True)
        )
    raw_df = raw_df.set_index("公司").reindex(score_df["公司"]).reset_index()
    return score_df, raw_df, latest_period


def render_combination_analysis(
    frame: pd.DataFrame,
    chart_name: str,
    *,
    periods: list[str],
    unit_mode: str,
    highlight_company: str,
    key_prefix: str,
    financing_data: pd.DataFrame | None = None,
    module_override: Step7Module | None = None,  # ★ 新增
) -> None:
    """Render an approved multi-metric plan selected by chart name.

    如果传入了 module_override，则用它携带的 chart_kind / metric_codes /
    chart_config 覆盖 COMPANY_NAVIGATION 里的默认配置。
    """
    if module_override is not None and module_override.chart_kind:
        plan = SimpleNamespace(kind=module_override.chart_kind)
    else:
        plan = chart_plan_for(chart_name)

    # ---- 应用 Excel chart_config 覆盖硬编码 spec ----
    if module_override is not None and module_override.chart_config:
        cfg = module_override.chart_config
        if plan.kind == COMPONENT_STACK and "components" in cfg:
            COMPONENT_STACK_SPECS[chart_name] = tuple(
                (c["code"], c["label"], c["color"]) for c in cfg["components"]
            )
        if plan.kind == RISK_RATIO_SCATTER and "scatter" in cfg:
            s = cfg["scatter"]
            RISK_RATIO_SCATTER_SPECS[chart_name] = (
                s["x_code"],
                s["y_code"],
                s["bubble_size_code"],
                s.get("denominator_label", "认可资产"),
            )

    if plan.kind == STRUCTURE_CHANGE_CHART:
        companies = ordered_companies(frame)
        if not companies:
            st.warning("当前没有公司数据。")
            return

        if chart_name == "承保规模":
            category_map = {"SIGNED_PREMIUM_MOTOR": "车险", "SIGNED_PREMIUM": "非车险"}
            total_code = "SIGNED_PREMIUM"
            line_color_map = {"车险": "#E36C0A", "非车险": "#9B3FB5"}
        elif chart_name == "经营效益":
            category_map = {"NET_PROFIT": "净利润率"}
            total_code = "SIGNED_PREMIUM"
            line_color_map = {"净利润率": "#E36C0A"}
        elif chart_name == "资本规模":
            category_map = {"CORE_T1_CAPITAL": "核心资本", "ANC_T1_CAPITAL": "附属资本"}
            total_code = "ACTUAL_CAPITAL"
            line_color_map = {"核心资本": "#E36C0A", "附属资本": "#9B3FB5"}
        else:
            return

        periods = sorted(frame["报告期"].dropna().unique().tolist())
        columns_per_row = 2

        for i in range(0, len(companies), columns_per_row):
            batch = companies[i : i + columns_per_row]
            cols = st.columns(len(batch), gap="large")
            for col, company in zip(cols, batch):
                with col:
                    try:
                        chart = build_structure_change_chart(
                            frame,
                            category_map=category_map,
                            total_code=total_code,
                            period_order=periods,
                            company_name=company,
                            line_color_map=line_color_map,
                            show_legend=True,
                        )
                        st.altair_chart(chart, use_container_width=True)
                    except Exception as e:
                        # ★ 详细报错 + 打印堆栈，便于排查
                        import traceback

                        st.error(f"❌ {company} 图表渲染失败：{e}")
                        with st.expander("查看详细错误", expanded=False):
                            st.code(traceback.format_exc())
        return

    # ★ 新增：公司业绩展示（收入排名—利润排名，按模式分列）
    if plan.kind == PERFORMANCE_PATTERN_GRID:
        companies = ordered_companies(frame)
        if not companies:
            st.warning("当前没有公司数据。")
            return

        cfg = module_override.chart_config if module_override else {}
        revenue_code = str(cfg.get("revenue_code", "SIGNED_PREMIUM")).strip()
        profit_code = str(cfg.get("profit_code", "NET_PROFIT")).strip()
        pattern_overrides: dict = cfg.get("pattern_overrides") or {}

        periods_list = sorted(frame["报告期"].dropna().astype(str).unique().tolist())

        # ---------- 1. 计算每家公司、每个报告期的收入/利润排名 ----------
        _df = frame.copy()
        _df["数值"] = pd.to_numeric(_df["数值"], errors="coerce")
        _df["报告期"] = _df["报告期"].astype(str)
        _df["公司"] = _df["公司"].astype(str).str.strip()

        all_companies = list(
            dict.fromkeys(c for c in _df["公司"].dropna().astype(str) if c and c != "nan")
        )
        n_companies = len(all_companies)
        if n_companies == 0:
            st.warning("无公司数据。")
            return

        rank_rows: list[dict] = []
        for period in periods_list:
            period_df = _df[_df["报告期"].eq(period)]
            revenue = (
                period_df[period_df["指标编码"].astype(str).eq(revenue_code)][["公司", "数值"]]
                .dropna(subset=["数值"])
                .drop_duplicates("公司", keep="last")
            )
            profit = (
                period_df[period_df["指标编码"].astype(str).eq(profit_code)][["公司", "数值"]]
                .dropna(subset=["数值"])
                .drop_duplicates("公司", keep="last")
            )
            if revenue.empty or profit.empty:
                continue
            revenue["收入排名"] = revenue["数值"].rank(ascending=False, method="min")
            profit["利润排名"] = profit["数值"].rank(ascending=False, method="min")
            merged = revenue[["公司", "收入排名"]].merge(
                profit[["公司", "利润排名"]], on="公司", how="inner"
            )
            merged["报告期"] = period
            rank_rows.append(merged)

        if not rank_rows:
            st.warning(
                f"缺少 {revenue_code} 或 {profit_code} 数据，"
                "无法计算排名。请检查 STEP5/STEP6 集成数据。"
            )
            return

        rank_df = pd.concat(rank_rows, ignore_index=True)

        # ---------- 2. 对每家公司做模式分类（人工覆盖优先） ----------
        pattern_map: dict[str, str] = {}
        for company in all_companies:
            if company in pattern_overrides:
                pattern_map[company] = str(pattern_overrides[company]).strip()
                continue
            series = rank_df[rank_df["公司"].eq(company)].sort_values("报告期")["利润排名"]
            if series.empty:
                continue
            pattern_map[company] = classify_performance_pattern(series)

        # ---------- 3. 按模式分组 ----------
        grouped: dict[str, list[str]] = {p: [] for p in PERFORMANCE_PATTERN_ORDER}
        for company in all_companies:
            pattern = pattern_map.get(company, "其他")
            if pattern not in grouped:
                grouped[pattern] = []
            grouped[pattern].append(company)

        active_patterns = [p for p in PERFORMANCE_PATTERN_ORDER if grouped.get(p)]
        if not active_patterns:
            st.warning("没有公司能被归类到任何业绩模式。")
            return

        # ---------- 4. 展示说明 ----------
        st.caption(
            f"横轴为「{revenue_code}」在样本内的排名，纵轴为「{profit_code}」排名；"
            "反向坐标（第 1 名在右上角）。"
            "公司按利润排名的年度轨迹自动归类到以下模式："
            "逐年提升 / 稳健经营 / V形反转 / 大起大落 / 持续恶化。"
        )
        # ★ 新增：数据诊断展开区，帮用户排查"某年缺失"的具体原因
        with st.expander("🔍 数据诊断：查看每家公司的收入/利润数据覆盖情况", expanded=False):
            st.caption(
                f"下表显示每家公司每个报告期是否有「{revenue_code}」和「{profit_code}」数据。"
                "❌ 表示该期该指标缺失（未披露或未集成）。"
                "若发现大量 ❌，请到 STEP5/STEP6 检查原始数据是否完整。"
            )
            _diag_rows = []
            for _c in all_companies:
                _c_df = _df[_df["公司"].eq(_c)]
                for _p in periods_list:
                    _p_df = _c_df[_c_df["报告期"].eq(_p)]
                    _has_revenue = (
                        not _p_df[_p_df["指标编码"].astype(str).eq(revenue_code)]
                        .dropna(subset=["数值"])
                        .empty
                    )
                    _has_profit = (
                        not _p_df[_p_df["指标编码"].astype(str).eq(profit_code)]
                        .dropna(subset=["数值"])
                        .empty
                    )
                    _diag_rows.append(
                        {
                            "公司": _c,
                            "报告期": _p,
                            f"有{revenue_code}": "✅" if _has_revenue else "❌",
                            f"有{profit_code}": "✅" if _has_profit else "❌",
                        }
                    )
            st.dataframe(
                pd.DataFrame(_diag_rows),
                width="stretch",
                hide_index=True,
            )

        # ---------- 5. 分列并排渲染 ----------
        _pattern_cols = st.columns(len(active_patterns), gap="small")
        for _col, _pattern in zip(_pattern_cols, active_patterns):
            with _col:
                # 列标题（模仿参考图的橙红色横幅）
                st.markdown(
                    f"<div style='background:#D94330; color:#FFFFFF; "
                    f"text-align:center; padding:6px 0; border-radius:3px; "
                    f"font-weight:700; font-size:13px; margin-bottom:10px;'>"
                    f"{escape(_pattern)}</div>",
                    unsafe_allow_html=True,
                )
                # 该列下的公司，纵向堆叠
                for _company in grouped[_pattern]:
                    # ★ 新增：在公司图上方显示公司名（红色小标题）
                    st.markdown(
                        f"<div style='text-align:center; "
                        f"font-size:11px; font-weight:700; color:#C8102E; "
                        f"margin:6px 0 2px; white-space:nowrap; "
                        f"overflow:hidden; text-overflow:ellipsis;' "
                        f"title='{escape(_company)}'>"
                        f"{escape(_company)}</div>",
                        unsafe_allow_html=True,
                    )
                    try:
                        _chart = build_performance_pattern_trajectory(
                            frame,
                            _company,
                            periods_list,
                            revenue_code=revenue_code,
                            profit_code=profit_code,
                        )
                        st.altair_chart(
                            _chart,  # ★ 改动：不再清空 title
                            width="content",
                            key=f"{key_prefix}_pattern_{_pattern}_{_company}",
                        )
                    except ValueError as _exc:
                        st.warning(f"{_company}：{_exc}")
        return

    if plan.kind == KEY_METRICS_TABLE:
        render_key_solvency_overview(frame, highlight_company=highlight_company)
        return
    if plan.kind == FINANCING_TABLE:
        render_major_financing(financing_data)
        return
    if plan.kind == PENDING_DEFINITION:
        st.warning(f"“{chart_name}”在新版 Excel 中标记为“待定”，目前没有定义可绘图的明细组成指标。")
        return
    required_codes = _chart_input_metric_codes(
        chart_name,
        plan.kind,
        metric_codes_override=(module_override.metric_codes if module_override else None),
        chart_config=(module_override.chart_config if module_override else None),
    )
    chart_frame = (
        frame[frame["指标编码"].fillna("").astype(str).isin(required_codes)].copy()
        if required_codes
        else frame
    )
    # ★ 二次保险：主流程 show_step_7_solvency 已经在入口处清洗过公司名，
    #   这里再做一次同样的归一化，防止其他调用方绕过主流程导致名字不一致。
    chart_frame["公司"] = (
        chart_frame["公司"]
        .astype(str)
        .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
        .str.strip()
    )
    converted = convert_unit(chart_frame, unit_mode)
    company_colors = company_color_map(converted["公司"], highlight_company)
    company_panel_count = converted["公司"].nunique()
    dense_company_panels = company_panel_count >= 10
    if plan.kind == SOLVENCY_RATIO_COMBO:
        shared_y_domain = solvency_ratio_axis_domain(converted, periods)
        period_colors = report_period_color_map(periods)
        st.caption(
            "图表说明：柱状图为综合偿付能力充足率；粉色折线为核心偿付能力充足率。"
            "各公司统一使用同一百分比纵轴范围，柱形颜色对应不同报告期。"
        )
        render_chart_legend(
            [
                *[(period_colors[period], period, "square") for period in periods],
                ("#FD349C", "核心偿付能力充足率（%）", "line"),
            ]
        )
        render_company_chart_grid(
            converted,
            lambda company_frame: build_solvency_ratio_combo_chart(
                company_frame,
                periods,
                shared_y_domain=shared_y_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_solvency_ratio_combo",
            highlight_company=highlight_company,
        )
    elif plan.kind == COMPANY_PERIOD_BAR:
        codes = (
            list(module_override.metric_codes)
            if module_override and module_override.metric_codes
            else list(metric_codes_for_chart(chart_name))
        )
        if codes:
            shared_y_domain = metric_bar_axis_domain(
                converted,
                codes[0],
                periods,
            )
            period_colors = report_period_color_map(periods)
            render_chart_legend([(period_colors[period], period, "square") for period in periods])
            render_company_chart_grid(
                converted,
                lambda company_frame: build_company_period_bar_chart(
                    company_frame,
                    codes[0],
                    periods,
                    shared_y_domain=shared_y_domain,
                    dense_layout=dense_company_panels,
                    panel_count=company_panel_count,
                ),
                key_prefix=f"{key_prefix}_{codes[0]}_bar",
                highlight_company=highlight_company,
            )

    elif plan.kind == CAPITAL_RATIO_COMBO:
        cfg = module_override.chart_config if module_override else {}
        if chart_name == "综合充足率变化":
            amount_groups = cfg.get("amount_groups") or {
                "实际资本": ("ACTUAL_CAPITAL",),
                "最低资本": ("MINIMUM_CAPITAL",),
            }
            ratio_code = "COMBINED_SOLVENCY_RATIO"
            ratio_label = "综合偿付能力充足率（%）"
        else:
            amount_groups = cfg.get("amount_groups") or {
                "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
                "最低资本": ("MINIMUM_CAPITAL",),
            }
            ratio_code = "CORE_SOLVENCY_RATIO"
            ratio_label = "核心偿付能力充足率（%）"
        grouped_amount_groups = cfg.get("grouped_amount_groups") or {
            "实际资本": ("ACTUAL_CAPITAL",),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        shared_amount_domain = capital_ratio_amount_axis_domain(
            converted,
            amount_groups,
            periods,
        )
        grouped_amount_domain = capital_ratio_amount_axis_domain(
            converted,
            grouped_amount_groups,
            periods,
            stacked=False,
        )
        st.caption(
            "上方 KPMG Pink 折线展示对应充足率，下方堆叠柱展示资本规模；"
            "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。"
        )
        st.markdown("**堆叠柱组合图**")
        render_chart_legend(
            [
                *[
                    (KPMG_CAPITAL_COMBO_COLORS[index], label, "square")
                    for index, label in enumerate(amount_groups)
                ],
                ("#FD349C", ratio_label, "line"),
            ]
        )
        render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_ratio_combo_chart(
                company_frame,
                amount_groups,
                periods,
                chart_name,
                ratio_code=ratio_code,
                ratio_label=ratio_label,
                shared_amount_domain=shared_amount_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_{ratio_code}_combo",
            highlight_company=highlight_company,
        )
        st.markdown("**并列柱组合图**")
        st.caption("新增视图：每个报告期的两项资本各自成柱，" "上方仍保留对应充足率折线。")
        render_chart_legend(
            [
                *[
                    (KPMG_CAPITAL_COMBO_COLORS[index], label, "square")
                    for index, label in enumerate(grouped_amount_groups)
                ],
                ("#FD349C", ratio_label, "line"),
            ]
        )
        render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_ratio_combo_chart(
                company_frame,
                grouped_amount_groups,
                periods,
                chart_name,
                ratio_code=ratio_code,
                ratio_label=ratio_label,
                shared_amount_domain=grouped_amount_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
                bar_layout="grouped",
            ),
            key_prefix=f"{key_prefix}_{ratio_code}_grouped_combo",
            highlight_company=highlight_company,
        )
    elif plan.kind == CAPITAL_STRUCTURE_COMBO:
        cfg = module_override.chart_config if module_override else {}
        capital_structure_groups = cfg.get("amount_groups") or {
            "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
            "附属资本": ("ANC_T1_CAPITAL", "ANC_T2_CAPITAL"),
        }
        shared_amount_domain = capital_ratio_amount_axis_domain(
            converted,
            capital_structure_groups,
            periods,
        )
        st.caption(
            "上方 KPMG Pink 折线展示核心资本占实际资本的比例，下方堆叠柱展示核心资本与附属资本；"
            "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。"
        )
        render_chart_legend(
            [
                (KPMG_CAPITAL_COMBO_COLORS[0], "核心资本", "square"),
                (KPMG_CAPITAL_COMBO_COLORS[1], "附属资本", "square"),
                ("#FD349C", "核心资本占实际资本的比例", "line"),
            ]
        )
        render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_ratio_combo_chart(
                company_frame,
                capital_structure_groups,
                periods,
                "核心资本与附属资本结构",
                ratio_label="核心资本占实际资本的比例（%）",
                share_line=True,
                shared_amount_domain=shared_amount_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_core_capital_structure",
            highlight_company=highlight_company,
        )
    elif plan.kind == COMPONENT_STACK:
        specs = COMPONENT_STACK_SPECS.get(chart_name, ())
        if not specs:
            st.warning(f"“{chart_name}”尚未配置组成指标。")
            return
        if chart_name == "计入各级资本的保单未来盈余构成占比":
            st.caption(
                "各色块表示计入相应资本层级的保单未来盈余占该公司已披露保单未来盈余明细合计的比例；"
                "100%表示该公司披露的保单未来盈余全部计入了同一个资本层级，其他资本层级没有披露这个明细。"
            )
        elif chart_name == "量化风险最低资本构成":
            st.caption(
                "柱高按各构成项目/量化风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "数据标签与对应比例折线图一致，负向抵减项目位于零线下方。"
            )
        elif chart_name == "各类市场风险占比":
            st.caption(
                "柱高按各构成项目/市场风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "负向风险分散效应位于零线下方。"
            )
        elif chart_name == "各类信用风险占比":
            st.caption(
                "柱高按各构成项目/信用风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "负向风险分散效应位于零线下方。"
            )
        else:
            st.caption("按公司分面展示构成；负向抵减项目位于零线下方，所有颜色均来自 KPMG 色卡。")
        negative_component_codes = (
            (
                "QUANT_RISK_DIVERSIFICATION_EFFECT",
                "CONTRACT_LOSS_ABSORPTION_EFFECT",
            )
            if chart_name == "量化风险最低资本构成"
            else (
                ("MARKET_RISK_DIVERSIFICATION_EFFECT",)
                if chart_name == "各类市场风险占比"
                else (
                    ("CREDIT_RISK_DIVERSIFICATION_EFFECT",)
                    if chart_name == "各类信用风险占比"
                    else ()
                )
            )
        )
        proportion_stack = chart_name in COMPONENT_STACK_DENOMINATOR_CODES
        shared_stack_domain = (
            component_stack_proportion_axis_domain(
                converted,
                specs,
                periods,
                label_ratio_codes=(
                    QUANT_RISK_STACK_RATIO_CODES if chart_name == "量化风险最低资本构成" else None
                ),
                label_denominator_code=COMPONENT_STACK_DENOMINATOR_CODES[chart_name],
                negative_component_codes=negative_component_codes,
            )
            if proportion_stack
            else component_stack_axis_domain(
                converted,
                specs,
                periods,
                negative_component_codes=negative_component_codes,
            )
        )
        render_chart_legend([(color, label, "square") for _, label, color in specs])
        render_company_chart_grid(
            converted,
            lambda company_frame: build_component_stack_chart(
                company_frame,
                specs,
                periods,
                chart_name,
                label_ratio_codes=(
                    QUANT_RISK_STACK_RATIO_CODES if chart_name == "量化风险最低资本构成" else None
                ),
                label_denominator_code=(COMPONENT_STACK_DENOMINATOR_CODES.get(chart_name, "")),
                value_labels=False,
                negative_component_codes=negative_component_codes,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
                shared_y_domain=shared_stack_domain,
                plot_proportions=proportion_stack,
                min_label_share=(0.10 if chart_name == "各类市场风险占比" else None),
            ),
            key_prefix=f"{key_prefix}_component_stack",
            highlight_company=highlight_company,
        )

    elif plan.kind == CAPITAL_AMOUNT_COMBO:
        st.caption(
            "100%堆叠柱显示核心一级、核心二级、附属一级和附属二级资本构成；"
            "所有公司和报告期的柱高一致，色块高度代表结构占比，悬浮可查看占比和金额。"
        )
        capital_codes = [
            "CORE_T1_CAPITAL",
            "CORE_T2_CAPITAL",
            "ANC_T1_CAPITAL",
            "ANC_T2_CAPITAL",
        ]
        expected_count = converted["公司"].nunique() * len(periods) * len(capital_codes)
        disclosed_count = (
            converted[converted["指标编码"].astype(str).isin(capital_codes)]
            .drop_duplicates(["公司", "报告期", "指标编码"])
            .shape[0]
        )
        capital_legend = [
            (color, label, "square") for label, color in CAPITAL_STRUCTURE_COLORS.items()
        ]
        if disclosed_count < expected_count:
            capital_legend.append(("#B8BDC7", "未披露", "square"))
        render_chart_legend(capital_legend)
        render_company_chart_grid(
            converted,
            lambda company_frame: build_capital_amount_combo(
                company_frame,
                periods,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_capital_amount",
            highlight_company=highlight_company,
        )
    elif plan.kind == EFFECT_DIVERGING:
        codes = (
            list(module_override.metric_codes)
            if module_override and module_override.metric_codes
            else list(metric_codes_for_chart(chart_name))
        )
        if codes:
            st.caption("按公司展示抵减效应的跨期方向与幅度，零线用于区分正向增加和负向抵减。")
            st.altair_chart(
                chart_without_internal_title(
                    build_effect_diverging_chart(converted, codes[0], periods)
                ),
                width="stretch",
                key=f"{key_prefix}_{codes[0]}_effect",
            )
    elif plan.kind == CAPITAL_EFFICIENCY_BUBBLE:
        render_capital_efficiency_bubble_fragment(
            converted,
            periods,
            company_colors,
            highlight_company,
            key_prefix,
        )
    elif plan.kind == SOLVENCY_MATRIX:
        chart, _ = build_matrix_chart(
            converted,
            "CORE_SOLVENCY_RATIO",
            "COMBINED_SOLVENCY_RATIO",
            periods,
            "偿付能力矩阵",
            highlight_company,
            regulatory_lines=True,
            company_colors=company_colors,
        )
        st.altair_chart(
            chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_solvency_matrix",
        )
        st.caption("横轴50%、纵轴100%的红色虚线分别为核心和综合偿付能力充足率监管下限。")
    elif plan.kind == MARKET_CREDIT_MATRIX:
        chart, _ = build_matrix_chart(
            converted,
            "MARKET_RISK_TO_QUANT_CAPITAL",
            "CREDIT_RISK_TO_QUANT_CAPITAL",
            periods,
            "市场—信用风险矩阵",
            highlight_company,
            company_colors=company_colors,
        )
        st.altair_chart(
            chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_market_credit_matrix",
        )
        st.caption("深蓝虚线为样本中位数，用于识别市场风险和信用风险同时偏高的公司。")
    elif plan.kind == RISK_RATIO_SCATTER:
        # 坐标轴口径来自注册表（Excel 可在"图表配置(JSON)"的 scatter 字段覆盖）。
        # 未注册的自定义图表名回退到"市场风险/认可资产"口径，避免 KeyError。
        x_code, y_code, bubble_size_code, denominator_label = (
            RISK_RATIO_SCATTER_SPECS.get(chart_name)
            or RISK_RATIO_SCATTER_SPECS[MARKET_RISK_ASSET_SCATTER]
        )
        if chart_name in RISK_SCATTER_DUAL_VIEW_CHARTS:
            render_risk_ratio_scatter_fragment(
                converted,
                periods,
                company_colors,
                highlight_company,
                key_prefix,
                chart_name,
                x_code,
                y_code,
                bubble_size_code,
                denominator_label,
            )
        else:
            chart, latest_period = build_matrix_chart(
                converted,
                x_code,
                y_code,
                periods,
                chart_name,
                highlight_company,
                company_colors=company_colors,
                percentage_axes=True,
                bubble_size_code=bubble_size_code,
                bubble_size_label=denominator_label,
            )
            centered_chart = chart_without_internal_title(chart).properties(
                width=RISK_SCATTER_DISPLAY_WIDTH
            )
            st.caption(
                f"展示 {latest_period}；每个气泡代表一家公司，横纵轴均为占{denominator_label}的比例，"
                f"气泡面积代表{denominator_label}规模；"
                "深蓝虚线为样本横纵指标中位数。"
            )
            with st.container(horizontal_alignment="center"):
                st.altair_chart(
                    centered_chart,
                    width=RISK_SCATTER_DISPLAY_WIDTH,
                    key=f"{key_prefix}_risk_asset_scatter",
                )

    # ★ 新增：能力五边形雷达图
    elif plan.kind == CAPABILITY_RADAR:
        cfg = module_override.chart_config if module_override else {}
        axes_config = cfg.get("axes") or DEFAULT_CAPABILITY_AXES
        axes_labels = [ax["label"] for ax in axes_config]

        latest_period_for_anchor = str(periods[-1]) if periods else ""
        baseline_full = chart_frame[
            chart_frame["报告期"].astype(str).eq(latest_period_for_anchor)
        ].copy()

        score_all, raw_all, latest_period = _capability_scores(
            chart_frame,
            axes_config,
            periods,
            baseline_frame=baseline_full,
        )

        if score_all.empty:
            st.warning(
                "能力五边形需要以下五个指标："
                + "、".join(axes_labels)
                + "。当前筛选范围缺少部分数据。"
            )
            return

        # ---------- 取得每家公司的同业分类 ----------
        category_column = "同业分类" if "同业分类" in chart_frame.columns else None
        if category_column:
            cat_map = (
                chart_frame[["公司", category_column]]
                .dropna()
                .assign(**{category_column: lambda d: d[category_column].astype(str).str.strip()})
                .drop_duplicates(subset=["公司"], keep="last")
                .set_index("公司")[category_column]
                .to_dict()
            )
        else:
            cat_map = {}
        score_all["_分类"] = score_all["公司"].astype(str).map(cat_map).fillna("未分类")

        # ---------- 分类顺序 ----------
        ordered_categories = list(dict.fromkeys(str(category) for category in score_all["_分类"]))
        # 把 "未分类" 放到最后
        if "未分类" in ordered_categories:
            ordered_categories.remove("未分类")
            ordered_categories.append("未分类")

        # ---------- 样式提示 ----------
        st.caption(
            f"展示 {latest_period}；每家公司单独一张能力五边形，"
            "按“同业分类”分组排列。"
            "顶点依次为：" + "、".join(axes_labels) + "。打分基准 = 全量 "
            f"{len(score_all)} 家（百分位排名），"
            "50 分 ≈ 行业中位数水平（灰色虚线五边形）。"
        )

        # ---------- 按分类逐组渲染 ----------
        columns_per_row = 5  # 每行 5 家
        panel_size = 230  # 单张小图尺寸
        tracked_company = str(highlight_company or "").strip()

        for category in ordered_categories:
            cat_scores = score_all[score_all["_分类"].astype(str).eq(category)].reset_index(
                drop=True
            )
            if cat_scores.empty:
                continue

            # ---- 组标题 ----
            st.markdown(
                f"<div style='color:#00338D;font-size:15px;font-weight:700;"
                f"border-bottom:1.5px solid #00338D;margin:18px 0 8px;padding-bottom:4px;'>"
                f"{escape(str(category))}"
                f"<span style='color:#7A8496;font-size:12px;font-weight:400;margin-left:8px;'>"
                f"（{len(cat_scores)} 家）</span></div>",
                unsafe_allow_html=True,
            )

            # ---- 网格布局 ----
            companies = list(cat_scores["公司"].astype(str))
            for start in range(0, len(companies), columns_per_row):
                batch = companies[start : start + columns_per_row]
                cols = st.columns(len(batch), gap="small")
                for col, company in zip(cols, batch):
                    with col:
                        is_tracked = (
                            tracked_company
                            and tracked_company != "无"
                            and company == tracked_company
                        )
                        title_color = "#ED2124" if is_tracked else "#00338D"
                        st.markdown(
                            f"<div style='text-align:center;font-size:12px;"
                            f"font-weight:700;color:{title_color};"
                            f"margin:4px 0 0;white-space:nowrap;overflow:hidden;"
                            f"text-overflow:ellipsis;' title='{escape(company)}'>"
                            f"{escape(company)}</div>",
                            unsafe_allow_html=True,
                        )

                        one_company_scores = cat_scores[cat_scores["公司"].astype(str).eq(company)]
                        # 单公司图：统一用 KPMG 蓝，追踪公司用红色
                        single_color = "#ED2124" if is_tracked else "#00338D"
                        single_colors = {company: single_color}

                        try:
                            chart = build_capability_radar_chart(
                                one_company_scores,
                                axes_labels,
                                single_colors,
                                panel_width=panel_size,
                                panel_height=panel_size,
                                show_legend=False,
                                label_font_size=9,
                                median_color="#94A3B8",
                            )
                        except Exception as exc:
                            st.warning(f"⚠️ {company} 的雷达图渲染失败：{exc}")
                            import traceback

                            st.code(traceback.format_exc())
                            continue

                        st.altair_chart(
                            chart_without_internal_title(chart),
                            width="content",
                            key=f"{key_prefix}_radar_{category}_{company}",
                        )

        # ---------- 明细展开 ----------
        with st.expander("查看原始数值与得分明细"):
            st.dataframe(
                raw_all.style.format(
                    {col: "{:,.2f}" for col in raw_all.columns if col != "公司"},
                    na_rep="—",
                ),
                width="stretch",
                hide_index=True,
            )
            st.dataframe(
                score_all.drop(columns=["_分类"]).style.format(
                    {col: "{:.0f}" for col in score_all.columns if col not in {"公司", "_分类"}},
                    na_rep="0",
                ),
                width="stretch",
                hide_index=True,
            )
        return

    # ★ 新增：承保效益散点图
    elif plan.kind == UNDERWRITING_EFFICIENCY_SCATTER:
        render_underwriting_efficiency_scatter(
            frame,  # ← 直接把当前页面的 frame 传进去
            chart_config=(module_override.chart_config if module_override else {}),
            key_prefix=key_prefix,
        )
        return
