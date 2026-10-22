"""STEP7 组合图的输入准备：指标编码解析、阶梯打分与能力五边形打分。"""

from __future__ import annotations

import pandas as pd
from services.solvency_navigation import metric_codes_for_chart
from services.solvency_step7_chart_plans import CAPITAL_RATIO_COMBO, COMPONENT_STACK
from .specs import (
    COMPONENT_STACK_DENOMINATOR_CODES,
    QUANT_RISK_STACK_RATIO_CODES,
    CAPABILITY_STEP_SCORE_FALLBACK,
)


def chart_input_metric_codes(
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

    if plan_kind == CAPITAL_RATIO_COMBO and chart_name in {
        "核心充足率变化",
        "核心偿付能力充足率",
    }:
        codes.append("ACTUAL_CAPITAL")

    return tuple(dict.fromkeys(codes))


def score_with_steps(value: float, steps: list[dict]) -> float:
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


def capability_scores(
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
                score_row[f"{label}_得分"] = score_with_steps(value, steps)
                continue

            # ② 保费增速：全行业统一百分位（89家一起比，最好100、最低0）
            if code == "PREMIUM_GROWTH":
                pool = baseline_pool_all.get(label, [])
                n = len(pool)
                if n == 0:
                    score_row[f"{label}_得分"] = 0.0
                elif n == 1:
                    score_row[f"{label}_得分"] = 100.0
                else:
                    below = sum(1 for v in pool if v < value)
                    equal = sum(1 for v in pool if v == value)
                    rank = below + (equal + 1) / 2.0
                    score = (rank - 1.0) / (n - 1.0) * 100.0
                    score_row[f"{label}_得分"] = max(0.0, min(100.0, score))
                continue

            # ③ 总资产、实际资本：组内线性比例（组内最高=100，其他=值/最高值×100）
            if code in {"TOTAL_ASSETS", "ACTUAL_CAPITAL"}:
                pool = baseline_pool_by_group.get((company_group, label), [])
                if not pool:
                    pool = baseline_pool_all.get(label, [])
                if not pool:
                    score_row[f"{label}_得分"] = 0.0
                else:
                    max_val = max(pool)
                    if max_val <= 0:
                        score_row[f"{label}_得分"] = 0.0
                    else:
                        score_row[f"{label}_得分"] = max(
                            0.0, min(100.0, value / max_val * 100.0)
                        )
                continue

            # ④ 其他顶点：同分类内百分位排名（原默认逻辑）
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
