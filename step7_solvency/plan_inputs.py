"""STEP7 组合图的输入准备：指标编码解析、阶梯打分与能力五边形打分。"""

from __future__ import annotations

import pandas as pd
from services.solvency_navigation import metric_codes_for_chart
from services.solvency_step7_chart_plans import CAPITAL_RATIO_COMBO, COMPONENT_STACK
from .specs import (
    COMPONENT_STACK_DENOMINATOR_CODES,
    QUANT_RISK_STACK_RATIO_CODES,
    CAPABILITY_STEP_SCORE_FALLBACK,
    CAPABILITY_PERCENTILE_FLOOR,
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
    step_score_fallback: dict[str, list[dict]] | None = None,
    score_floor: float = CAPABILITY_PERCENTILE_FLOOR,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """能力五边形打分。

    与旧版不同之处：
    1. 若顶点配置里带 `steps`（或该编码在 step_score_fallback 里），
       则按阶梯打分。例如偿付能力：≥200%=100, 150~200%=70,
       120~150%=50, 100~120%=30, <100%=0。
    2. 其余顶点统一按全行业样本的相对位次打分。所有公司使用同一基准，
       不再按「同业分类」拆分成组内排名。
    3. 百分位不再硬映射到 0–100，而是映射到 [score_floor, 100]
       （默认 30）。行业末位仍有 30 分，避免顶点塌陷到圆心。

    参数
    ----
    frame            : 当前筛选范围（含 公司 / 指标编码 / 报告期 / 期间口径 / 数值 / 同业分类）
    axes_config      : 每个顶点的配置，可含 steps=[{"min":200,"score":100}, ...]
    periods          : 可用报告期，函数内取最后一个作为"最新期"
    baseline_frame   : 用于构建基准池的数据源（默认用 frame 自身）
    step_score_fallback : 阶梯打分兜底配置，默认用模块级 CAPABILITY_STEP_SCORE_FALLBACK
    score_floor      : 百分位得分下限（默认 30），仅作用于排名类顶点

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
                "scoring": str(ax.get("scoring", "") or "").strip().lower(),
                "lower_is_better": bool(ax.get("lower_is_better", False)),
                "average_center": float(ax.get("average_center", 60.0) or 60.0),
                "average_span": float(ax.get("average_span", 0.10) or 0.10),
            }
        )
    if not normalized_axes:
        return pd.DataFrame(), pd.DataFrame(), latest_period

    fallback_steps = (
        step_score_fallback if step_score_fallback is not None else CAPABILITY_STEP_SCORE_FALLBACK
    )
    floor = max(0.0, min(100.0, float(score_floor)))
    def _rank_score(
        value: float,
        pool: list[float],
        *,
        axis_floor: float | None = None,
    ) -> float:
        """把数值在基准池中的位次映射到 [floor, 100]。

        平均排名法处理并列；最低分不再是 0，而是 floor。
        """
        n = len(pool)
        effective_floor = floor if axis_floor is None else axis_floor
        effective_floor = max(0.0, min(100.0, float(effective_floor)))
        if n == 0:
            return 0.0
        if n == 1:
            return effective_floor + (100.0 - effective_floor) * 0.5
        below = sum(1 for v in pool if v < value)
        equal = sum(1 for v in pool if v == value)
        rank = below + (equal + 1) / 2.0
        position = (rank - 1.0) / (n - 1.0)  # 0 = 组内最低，1 = 组内最高
        return max(
            0.0,
            min(
                100.0,
                effective_floor + (100.0 - effective_floor) * position,
            ),
        )

    def _average_score(
        value: float,
        pool: list[float],
        *,
        center: float = 60.0,
        span: float = 0.10,
        lower_is_better: bool = True,
        filter_ratio: bool = False,
    ) -> float:
        """Score a value around the peer-category average.

        center is the peer-category-average score (default 60).  A company that is
        10% better than the average (span=0.10) reaches 100; 10% worse reaches
        20.  For combined ratio, lower is better and invalid outliers are
        removed before the average is calculated.
        """
        clean: list[float] = []
        for item in pool:
            numeric = pd.to_numeric(item, errors="coerce")
            if pd.isna(numeric):
                continue
            numeric = float(numeric)
            if filter_ratio and not (0.0 < numeric < 1000.0):
                continue
            clean.append(numeric)
        if not clean:
            return float("nan")
        average = sum(clean) / len(clean)
        if average == 0:
            return float("nan")
        effective_center = max(0.0, min(100.0, float(center)))
        effective_span = max(1e-6, abs(float(span)))
        deviation = (
            (average - float(value)) / abs(average)
            if lower_is_better
            else (float(value) - average) / abs(average)
        )
        score = effective_center + (deviation / effective_span) * (
            100.0 - effective_center
        )
        return max(0.0, min(100.0, score))

    # 归一一次，后续所有筛选/排名都基于同一份数值列。
    period_frame["指标编码"] = period_frame["指标编码"].fillna("").astype(str).str.strip()
    period_frame["期间口径"] = period_frame["期间口径"].fillna("").astype(str).str.strip()
    period_frame["数值"] = pd.to_numeric(period_frame["数值"], errors="coerce")

    def _values_by_company(
        rows: pd.DataFrame,
        preferred_period: str,
        *,
        keep: str,
    ) -> dict[str, float]:
        """Return one numeric value per company, preferring the requested period."""
        if rows.empty:
            return {}
        work = rows
        if preferred_period:
            work = work.assign(
                _priority=work["期间口径"].eq(str(preferred_period).strip())
            ).sort_values("_priority", ascending=False, kind="stable")
        work = work.drop_duplicates(subset=["公司"], keep=keep)
        return dict(
            zip(
                work["公司"].astype(str),
                pd.to_numeric(work["数值"], errors="coerce"),
            )
        )

    # ---------- 第 1 遍：收集待展示公司的原始值 ----------
    raw_maps: dict[str, dict[str, float]] = {}
    for ax in normalized_axes:
        label = ax["label"]
        raw_maps[label] = _values_by_company(
            period_frame[period_frame["指标编码"].eq(ax["code"])],
            str(ax.get("period") or ""),
            keep="first",
        )
    raw_cache: dict[str, dict[str, float]] = {
        company: {
            ax["label"]: float(raw_maps[ax["label"]].get(company, float("nan")))
            for ax in normalized_axes
        }
        for company in companies
    }

    # ---------- 构建基准池：分「全量池」和「按分类分组池」 ----------
    baseline_period_frame = baseline_frame[
        baseline_frame["报告期"].astype(str).eq(latest_period)
    ].copy()
    baseline_period_frame["指标编码"] = (
        baseline_period_frame["指标编码"].fillna("").astype(str).str.strip()
    )
    baseline_period_frame["期间口径"] = (
        baseline_period_frame["期间口径"].fillna("").astype(str).str.strip()
    )
    baseline_period_frame["数值"] = pd.to_numeric(
        baseline_period_frame["数值"], errors="coerce"
    )

    baseline_pool_all: dict[str, list[float]] = {}

    for ax in normalized_axes:
        label = ax["label"]
        values = _values_by_company(
            baseline_period_frame[baseline_period_frame["指标编码"].eq(ax["code"])],
            str(ax.get("period") or ""),
            keep="first" if ax["period"] else "last",
        )
        clean_values = {
            str(name).strip(): float(value)
            for name, value in values.items()
            if str(name).strip() and pd.notna(value)
        }
        baseline_pool_all[label] = list(clean_values.values())

    # ---------- 第 2 遍：逐公司逐顶点打分 ----------
    raw_rows: list[dict] = []
    score_rows: list[dict] = []
    for company in companies:
        raw = raw_cache[company]
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

            if code == "COMBINED_RATIO" and not (0.0 < float(value) < 1000.0):
                score_row[f"{label}_得分"] = float("nan")
                continue

            # ① 阶梯打分优先（如偿付能力）
            steps = ax.get("steps") or fallback_steps.get(code)
            if steps:
                score_row[f"{label}_得分"] = score_with_steps(value, steps)
                continue

            # ② 综合成本率（承保风控能力）按行业平均值打分：
            #    行业平均=60 分，低于平均越多分越高；不再做单纯名次比较。
            scoring = str(ax.get("scoring", "") or "").lower()
            if scoring == "average" or (
                code == "COMBINED_RATIO" and scoring in {"", "rank"}
            ):
                industry_pool = baseline_pool_all.get(label, [])
                score_row[f"{label}_得分"] = _average_score(
                    value,
                    industry_pool,
                    center=float(ax.get("average_center", 60.0)),
                    span=float(ax.get("average_span", 0.10)),
                    lower_is_better=bool(
                        ax.get("lower_is_better", code == "COMBINED_RATIO")
                    ),
                    filter_ratio=(code == "COMBINED_RATIO"),
                )
                continue

            # ③ 其余顶点：所有公司统一按全行业池的相对位次打分。
            #    位次映射到 [floor, 100]，行业末位不再被判 0 分。
            pool = baseline_pool_all.get(label, [])
            score_row[f"{label}_得分"] = _rank_score(
                value,
                pool,
                axis_floor=floor,
            )

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
