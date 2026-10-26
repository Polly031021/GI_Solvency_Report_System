"""STEP7 图表前的数据型结论句。

每个函数返回一句可直接展示的中文/英文结论；数据不足时返回空字符串。
调用方通过 render_chart_summary 渲染，确保每张图先给结论、再给图。
"""

from __future__ import annotations

import re

import pandas as pd

from services.report_i18n import t


def _period_label(period: object) -> str:
    """Display an annual report period as its year in summaries."""
    match = re.search(r"(20\d{2})", str(period))
    return match.group(1) if match else str(period)


def _rows_for_period(frame: pd.DataFrame, codes: tuple[str, ...], period: str) -> pd.DataFrame:
    if frame is None or frame.empty or "指标编码" not in frame.columns:
        return pd.DataFrame()
    rows = frame[
        frame["指标编码"].fillna("").astype(str).isin(codes)
        & frame["报告期"].fillna("").astype(str).eq(str(period))
    ].copy()
    if rows.empty:
        return rows
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    return rows.dropna(subset=["数值"])


def _latest_period(frame: pd.DataFrame, codes: tuple[str, ...], periods) -> tuple[str, pd.DataFrame]:
    for period in reversed([str(item) for item in periods]):
        rows = _rows_for_period(frame, codes, period)
        if not rows.empty:
            return period, rows
    return "", pd.DataFrame()


def _scope_priority(scope: pd.Series) -> pd.Series:
    """Rank period bases, preferring current-quarter/period-end over prior quarter."""
    text = scope.fillna("").astype(str).str.strip()
    priority = pd.Series(9, index=text.index, dtype=int)
    priority[text.str.contains("本年累计|年度累计|全年累计", na=False)] = 0
    priority[
        text.str.contains("本季度末数|本季末|期末数|期末$|当期|时点", na=False)
    ] = 0
    priority[text.str.contains("本季度数", na=False)] = 1
    priority[text.str.contains("上季度", na=False)] = 20
    priority[text.str.contains("下季度|预测", na=False)] = 99
    return priority


def _pivot(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    work = rows.copy()
    if "期间口径" in work.columns:
        scope = work["期间口径"].fillna("").astype(str).str.strip()
        work["_口径优先级"] = _scope_priority(scope)
        work = work.sort_values(["公司", "指标编码", "_口径优先级"], kind="stable")
    work = work.drop_duplicates(["公司", "指标编码"], keep="first")
    return work.pivot_table(
        index="公司",
        columns="指标编码",
        values="数值",
        aggfunc="first",
    )


def solvency_ratio_summary(
    frame: pd.DataFrame,
    code: str,
    periods,
    ratio_label: str,
    *,
    threshold: float,
) -> str:
    """Summarise the latest annual distribution of a solvency ratio."""
    period, rows = _latest_period(frame, (str(code),), periods)
    if rows.empty:
        return ""
    pivot = _pivot(rows)
    if pivot.empty or str(code) not in pivot.columns:
        return ""
    values = pd.to_numeric(pivot[str(code)], errors="coerce").dropna()
    if values.empty:
        return ""
    median = float(values.median())
    above = int((values >= float(threshold)).sum())
    high_company = str(values.idxmax())
    low_company = str(values.idxmin())

    change_zh = ""
    change_en = ""
    ordered = [str(item) for item in periods]
    if period in ordered:
        index = ordered.index(period)
        for previous in reversed(ordered[:index]):
            previous_rows = _rows_for_period(frame, (str(code),), previous)
            previous_values = pd.to_numeric(previous_rows.get("数值"), errors="coerce").dropna()
            if previous_values.empty:
                continue
            delta = median - float(previous_values.median())
            if abs(delta) < 0.05:
                change_zh = f"与{previous}中位数基本持平；"
                change_en = f"Median is broadly unchanged versus {previous}; "
            else:
                change_zh = f"较{previous}中位数{delta:+.1f}个百分点；"
                change_en = f"Median {delta:+.1f} pp versus {previous}; "
            break

    spread = float(values.max() - values.min())
    if spread < 0.05:
        detail_zh = "各公司水平接近，极差不足 0.1 个百分点。"
        detail_en = "Dispersion is minimal (range below 0.1 pp)."
    else:
        detail_zh = (
            f"最高为{high_company}（{float(values.max()):.1f}%），"
            f"最低为{low_company}（{float(values.min()):.1f}%）。"
        )
        detail_en = (
            f"Highest: {high_company} ({float(values.max()):.1f}%); "
            f"lowest: {low_company} ({float(values.min()):.1f}%)."
        )

    period_display = _period_label(period)
    return t(
        f"{period_display}，{len(values)} 家样本公司{ratio_label}中位数为 {median:.1f}%，"
        f"{above} 家达到 {threshold:.0f}% 监管下限；"
        + change_zh
        + detail_zh,
        f"As of {period_display}, the median {ratio_label} across {len(values)} companies is "
        f"{median:.1f}%; {above} meet the {threshold:.0f}% regulatory floor. "
        + change_en
        + detail_en,
    )


def capital_structure_summary(
    frame: pd.DataFrame,
    core_codes,
    ancillary_codes,
    periods,
    *,
    label: str,
    denominator_code: str = "ACTUAL_CAPITAL",
) -> str:
    """Summarise the latest company distribution of core-capital share."""
    core_codes = tuple(str(code) for code in core_codes)
    ancillary_codes = tuple(str(code) for code in ancillary_codes)
    codes = (*core_codes, *ancillary_codes, str(denominator_code))
    period, rows = _latest_period(frame, core_codes, periods)
    if rows.empty:
        return ""
    pivot = _pivot(
        pd.concat(
            [
                rows,
                _rows_for_period(frame, ancillary_codes, period),
                _rows_for_period(frame, (str(denominator_code),), period),
            ],
            ignore_index=True,
        )
    )
    if pivot.empty:
        return ""
    for code in codes:
        if code not in pivot.columns:
            pivot[code] = 0.0
    core = pivot[list(core_codes)].sum(axis=1)
    ancillary = pivot[list(ancillary_codes)].sum(axis=1)
    actual = pd.to_numeric(pivot[denominator_code], errors="coerce")
    actual = actual.where(actual.notna() & actual.gt(0), core + ancillary)
    valid = actual.gt(0)
    if not valid.any():
        return ""
    share = (core.loc[valid] / actual.loc[valid] * 100.0).dropna()
    if share.empty:
        return ""
    median = float(share.median())
    above = int((share >= 80.0).sum())
    high_company = str(share.idxmax())
    low_company = str(share.idxmin())
    spread = float(share.max() - share.min())
    if spread < 0.05:
        detail_zh = "各公司比例接近，极差不足 0.1 个百分点。"
        detail_en = "Dispersion is minimal (range below 0.1 pp)."
    else:
        detail_zh = (
            f"最高为{high_company}（{float(share.max()):.1f}%），"
            f"最低为{low_company}（{float(share.min()):.1f}%）。"
        )
        detail_en = (
            f"Highest: {high_company} ({float(share.max()):.1f}%); "
            f"lowest: {low_company} ({float(share.min()):.1f}%)."
        )
    period_display = _period_label(period)
    return t(
        f"{period_display}，{len(share)} 家公司{label}占实际资本比例的中位数为 {median:.1f}%，"
        f"其中 {above} 家达到或高于 80%；" + detail_zh,
        f"As of {period_display}, the median {label} / actual capital ratio across {len(share)} "
        f"companies is {median:.1f}%, with {above} at or above 80%. " + detail_en,
    )


def capital_amount_summary(
    frame: pd.DataFrame,
    periods,
    *,
    core_code: str = "CORE_T1_CAPITAL",
    ancillary_code: str = "ANC_T1_CAPITAL",
) -> str:
    """Summarise the latest core tier-1 share of tier-1 capital."""
    codes = (str(core_code), str(ancillary_code))
    period, rows = _latest_period(frame, (str(core_code),), periods)
    if rows.empty:
        return ""
    pivot = _pivot(
        pd.concat(
            [rows, _rows_for_period(frame, (str(ancillary_code),), period)],
            ignore_index=True,
        )
    )
    if pivot.empty:
        return ""
    for code in codes:
        if code not in pivot.columns:
            pivot[code] = 0.0
    core = pd.to_numeric(pivot[core_code], errors="coerce")
    ancillary = pd.to_numeric(pivot[ancillary_code], errors="coerce")
    total = core.fillna(0.0) + ancillary.fillna(0.0)
    valid = total.gt(0)
    if not valid.any():
        return ""
    share = (core.loc[valid] / total.loc[valid] * 100.0).dropna()
    if share.empty:
        return ""
    median = float(share.median())
    above = int((share >= 80.0).sum())
    spread = float(share.max() - share.min())
    if spread < 0.05:
        detail_zh = "各公司比例接近，极差不足 0.1 个百分点。"
        detail_en = "Dispersion is minimal (range below 0.1 pp)."
    else:
        detail_zh = (
            f"最高为{share.idxmax()}（{float(share.max()):.1f}%），"
            f"最低为{share.idxmin()}（{float(share.min()):.1f}%）。"
        )
        detail_en = (
            f"Highest: {share.idxmax()} ({float(share.max()):.1f}%); "
            f"lowest: {share.idxmin()} ({float(share.min()):.1f}%)."
        )
    period_display = _period_label(period)
    return t(
        f"{period_display}，{len(share)} 家公司披露一级资本结构，核心一级资本占一级资本合计的"
        f"中位数为 {median:.1f}%，其中 {above} 家达到或高于 80%；" + detail_zh,
        f"As of {period_display}, {len(share)} companies disclose tier-1 capital. The median core "
        f"tier-1 share is {median:.1f}%, with {above} at or above 80%. " + detail_en,
    )


def premium_growth_summary(
    panels,
    *,
    unit_label: str = "",
    frame: pd.DataFrame | None = None,
) -> str:
    """Summarise the latest-year premium growth of the displayed companies."""
    latest_years: list[str] = []
    for panel in panels:
        line = getattr(panel, "line_frame", None)
        if line is None or line.empty:
            continue
        latest_years.append(_period_label(line["年度"].astype(str).max()))
    if not latest_years:
        return ""
    target_year = max(latest_years)

    growths: list[float] = []
    latest_total = 0.0
    previous_total = 0.0
    for panel in panels:
        line = getattr(panel, "line_frame", None)
        if line is None or line.empty:
            continue
        work = line.copy()
        work["年度"] = work["年度"].astype(str)
        work = work[work["年度"].map(_period_label).eq(target_year)]
        if work.empty:
            continue
        row = work.iloc[-1]
        growth = pd.to_numeric(row.get("同比增速"), errors="coerce")
        if pd.isna(growth):
            continue
        growths.append(float(growth))
        current_premium = pd.to_numeric(row.get("本年保费"), errors="coerce")
        previous_premium = pd.to_numeric(row.get("上年保费"), errors="coerce")
        if pd.notna(current_premium):
            latest_total += float(current_premium)
        if pd.notna(previous_premium):
            previous_total += float(previous_premium)
    if not growths:
        return ""
    up = sum(1 for value in growths if value > 0)
    flat = sum(1 for value in growths if value == 0)
    down = sum(1 for value in growths if value < 0)
    median = float(pd.Series(growths).median())
    total_text = f"{latest_total:,.0f} {unit_label}".strip()
    weighted_text_zh = ""
    weighted_text_en = ""
    if previous_total > 0:
        weighted = (latest_total / previous_total - 1.0) * 100.0
        weighted_text_zh = f"合计签单保费 {total_text}，加权同比 {weighted:+.1f}%；"
        weighted_text_en = (
            f"Aggregate signed premium reached {total_text}, up {weighted:+.1f}% year on year; "
        )
    solvency_pairs: list[tuple[float, float, float]] = []
    growth_by_company: dict[str, float] = {}
    for panel in panels:
        line = getattr(panel, "line_frame", None)
        solvency = getattr(panel, "solvency_frame", None)
        if line is None or line.empty or solvency is None or solvency.empty:
            continue
        line_work = line.copy()
        line_work["年度"] = line_work["年度"].astype(str).map(_period_label)
        line_work = line_work[line_work["年度"].eq(target_year)]
        if line_work.empty:
            continue
        growth_value = pd.to_numeric(
            line_work.iloc[-1].get("同比增速"), errors="coerce"
        )
        if pd.isna(growth_value):
            continue
        solvency_work = solvency.copy()
        solvency_work["年度"] = (
            solvency_work["年度"].astype(str).map(_period_label)
        )
        current = solvency_work[solvency_work["年度"].eq(target_year)]
        previous = solvency_work[
            solvency_work["年度"].eq(str(int(target_year) - 1))
        ]
        if current.empty or previous.empty:
            continue
        current_value = pd.to_numeric(
            current.iloc[-1].get("综合偿付能力充足率"), errors="coerce"
        )
        previous_value = pd.to_numeric(
            previous.iloc[-1].get("综合偿付能力充足率"), errors="coerce"
        )
        if pd.isna(current_value) or pd.isna(previous_value):
            continue
        growth_by_company[str(panel.company)] = float(growth_value)
        solvency_pairs.append(
            (
                float(growth_value),
                float(current_value),
                float(current_value) - float(previous_value),
            )
        )

    solvency_text_zh = ""
    solvency_text_en = ""
    if solvency_pairs:
        solvency_values = [item[1] for item in solvency_pairs]
        solvency_deltas = [item[2] for item in solvency_pairs]
        median_solvency = float(pd.Series(solvency_values).median())
        median_delta = float(pd.Series(solvency_deltas).median())
        solvency_down = sum(1 for value in solvency_deltas if value < 0)
        correlation = None
        if len(solvency_pairs) >= 3:
            correlation = float(
                pd.Series([item[0] for item in solvency_pairs]).corr(
                    pd.Series(solvency_deltas)
                )
            )
        if correlation is not None and pd.notna(correlation):
            if correlation >= 0.3:
                relation_zh = "呈一定正相关"
                relation_en = "a moderate positive correlation"
            elif correlation <= -0.3:
                relation_zh = "呈一定负相关"
                relation_en = "a moderate negative correlation"
            else:
                relation_zh = "相关性较弱"
                relation_en = "a weak correlation"
            relation_text_zh = (
                f"保费增速与偿付能力变动的相关系数为 {correlation:+.2f}，"
                f"{relation_zh}。"
            )
            relation_text_en = (
                f"The correlation between premium growth and solvency change is "
                f"{correlation:+.2f}, indicating {relation_en}."
            )
        else:
            relation_text_zh = ""
            relation_text_en = ""
        solvency_text_zh = (
            f"综合偿付能力充足率中位数为 {median_solvency:.1f}%，"
            f"较上年 {median_delta:+.1f} 个百分点，"
            f"{solvency_down}/{len(solvency_pairs)} 家下降；" + relation_text_zh
        )
        solvency_text_en = (
            f"The median combined solvency ratio is {median_solvency:.1f}%, "
            f"{median_delta:+.1f} pp versus the prior year, with "
            f"{solvency_down} of {len(solvency_pairs)} companies declining. "
            + relation_text_en
        )
    capital_text_zh = ""
    capital_text_en = ""
    if (
        isinstance(frame, pd.DataFrame)
        and not frame.empty
        and growth_by_company
    ):
        def _capital_year_map(code: str, year: str) -> dict[str, float]:
            rows = frame[
                frame["指标编码"].fillna("").astype(str).eq(code)
                & frame["报告期"]
                .fillna("")
                .astype(str)
                .str.contains(year, regex=False, na=False)
            ].copy()
            if rows.empty:
                return {}
            rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
            rows = rows.dropna(subset=["数值"])
            if "期间口径" in rows.columns:
                rows["_口径优先级"] = _scope_priority(
                    rows["期间口径"].fillna("").astype(str).str.strip()
                )
                rows = rows.sort_values(
                    ["公司", "_口径优先级"], kind="stable"
                )
            rows = rows.drop_duplicates("公司", keep="first")
            return dict(
                zip(
                    rows["公司"].astype(str),
                    pd.to_numeric(rows["数值"], errors="coerce"),
                )
            )

        minimum_now = _capital_year_map("MINIMUM_CAPITAL", str(target_year))
        minimum_prev = _capital_year_map(
            "MINIMUM_CAPITAL", str(int(target_year) - 1)
        )
        actual_now = _capital_year_map("ACTUAL_CAPITAL", str(target_year))
        actual_prev = _capital_year_map(
            "ACTUAL_CAPITAL", str(int(target_year) - 1)
        )
        capital_pairs: list[tuple[float, float]] = []
        for company, company_growth in growth_by_company.items():
            if company_growth <= 0:
                continue
            if not all(
                company in source and pd.notna(source[company])
                for source in (
                    minimum_now,
                    minimum_prev,
                    actual_now,
                    actual_prev,
                )
            ):
                continue
            min_base = float(minimum_prev[company])
            actual_base = float(actual_prev[company])
            if min_base <= 0 or actual_base <= 0:
                continue
            capital_pairs.append(
                (
                    (float(minimum_now[company]) / min_base - 1.0) * 100.0,
                    (float(actual_now[company]) / actual_base - 1.0) * 100.0,
                )
            )
        if capital_pairs:
            minimum_changes = [item[0] for item in capital_pairs]
            actual_changes = [item[1] for item in capital_pairs]
            median_minimum = float(pd.Series(minimum_changes).median())
            median_actual = float(pd.Series(actual_changes).median())
            minimum_down = sum(1 for value in minimum_changes if value < 0)
            actual_down = sum(1 for value in actual_changes if value < 0)
            if median_minimum > median_actual:
                driver_zh = (
                    "最低资本增速高于实际资本增速，是偿付能力承压的主要来源"
                )
                driver_en = (
                    "minimum capital grew faster than actual capital, which is the "
                    "main source of solvency pressure"
                )
            else:
                driver_zh = (
                    "实际资本增速不低于最低资本增速，对偿付能力形成支撑"
                )
                driver_en = (
                    "actual capital grew at least as fast as minimum capital, "
                    "supporting solvency"
                )
            capital_text_zh = (
                f"在{len(capital_pairs)}家保费正增长公司中，"
                f"{minimum_down}家最低资本下降、{actual_down}家实际资本下降；"
                f"最低资本与实际资本中位数分别{median_minimum:+.1f}%、"
                f"{median_actual:+.1f}%，{driver_zh}。"
            )
            capital_text_en = (
                f"Among {len(capital_pairs)} companies with premium growth, "
                f"{minimum_down} saw minimum capital fall and {actual_down} saw actual "
                f"capital fall; median minimum and actual capital changed "
                f"{median_minimum:+.1f}% and {median_actual:+.1f}% respectively, and "
                f"{driver_en}."
            )
    return t(
        f"{target_year} 年，样本公司{weighted_text_zh}"
        f"{up} 家实现正增长、{flat} 家持平、{down} 家下降，"
        f"公司增速中位数为 {median:+.1f}%。" + solvency_text_zh + capital_text_zh,
        f"In {target_year}, {weighted_text_en}"
        f"{up} companies grew, {flat} were flat and {down} declined; the median company "
        f"growth was {median:+.1f}%. " + solvency_text_en + capital_text_en,
    )


def loss_expense_summary(panels, frame: pd.DataFrame | None = None) -> str:
    """Summarise the latest-year combined ratio of the displayed companies."""
    years = [
        _period_label(panel.years[-1])
        for panel in panels
        if getattr(panel, "years", ())
    ]
    if not years:
        return ""
    target_year = max(years)
    entries: list[tuple[str, float]] = []
    for panel in panels:
        totals = getattr(panel, "total_frame", None)
        if totals is None or totals.empty:
            continue
        work = totals[totals["年度"].astype(str).map(_period_label).eq(target_year)]
        if work.empty:
            continue
        value = pd.to_numeric(work.iloc[-1]["综合成本率"], errors="coerce")
        if pd.isna(value):
            continue
        entries.append((str(panel.company), float(value)))
    if not entries:
        return ""
    values = [value for _, value in entries]
    median = float(pd.Series(values).median())
    profitable = sum(1 for value in values if value < 100.0)
    loss_making = sum(1 for value in values if value > 100.0)
    high_company, high_value = max(entries, key=lambda item: item[1])
    low_company, low_value = min(entries, key=lambda item: item[1])

    solvency_text_zh = ""
    solvency_text_en = ""
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        solvency_rows = frame[
            frame["指标编码"].fillna("").astype(str).eq("COMBINED_SOLVENCY_RATIO")
            & frame["报告期"].fillna("").astype(str).str.contains(target_year, na=False)
        ].copy()
        if not solvency_rows.empty:
            solvency_rows["数值"] = pd.to_numeric(
                solvency_rows["数值"], errors="coerce"
            )
            solvency_rows = solvency_rows.dropna(subset=["数值"])
            if "期间口径" in solvency_rows.columns:
                scope = (
                    solvency_rows["期间口径"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )
                solvency_rows["_口径优先级"] = scope.str.contains(
                    "本季度末|期末|本季末|当期", na=False
                ).map({True: 0, False: 1})
                solvency_rows = solvency_rows.sort_values(
                    ["公司", "_口径优先级"], kind="stable"
                )
            solvency_rows = solvency_rows.drop_duplicates("公司", keep="first")
            solvency_map = dict(
                zip(
                    solvency_rows["公司"].astype(str),
                    pd.to_numeric(solvency_rows["数值"], errors="coerce"),
                )
            )
            linked = [
                (value, float(solvency_map[company]))
                for company, value in entries
                if company in solvency_map and pd.notna(solvency_map[company])
            ]
            if len(linked) >= 5:
                linked_ratios = [item[0] for item in linked]
                linked_solvency = [item[1] for item in linked]
                profit_values = [
                    value for ratio, value in linked if ratio < 100.0
                ]
                loss_values = [
                    value for ratio, value in linked if ratio > 100.0
                ]
                correlation = float(
                    pd.Series(linked_ratios).corr(pd.Series(linked_solvency))
                )
                if pd.notna(correlation):
                    if correlation >= 0.3:
                        relation_zh = "呈一定正相关"
                        relation_en = "a moderate positive correlation"
                    elif correlation <= -0.3:
                        relation_zh = "呈一定负相关"
                        relation_en = "a moderate negative correlation"
                    else:
                        relation_zh = "相关性较弱"
                        relation_en = "a weak correlation"
                    profit_median_text = (
                        f"{float(pd.Series(profit_values).median()):.1f}%"
                        if profit_values
                        else "—"
                    )
                    loss_median_text = (
                        f"{float(pd.Series(loss_values).median()):.1f}%"
                        if loss_values
                        else "—"
                    )
                    solvency_text_zh = (
                        f"与偿付能力联动看，承保盈利公司综合偿付能力中位数为"
                        f"{profit_median_text}，承保亏损公司为{loss_median_text}；"
                        f"综合成本率与偿付能力的相关系数为 {correlation:+.2f}，"
                        f"{relation_zh}，承保亏损尚未普遍转化为偿付能力压力。"
                    )
                    solvency_text_en = (
                        f"Linked to solvency, profitable companies have a median "
                        f"combined solvency ratio of {profit_median_text}, versus "
                        f"{loss_median_text} for loss-making companies; the correlation "
                        f"between the combined ratio and solvency is {correlation:+.2f}, "
                        f"indicating {relation_en}. Underwriting losses have not yet "
                        f"broadly translated into solvency pressure."
                    )

    return t(
        f"{target_year} 年，{len(values)} 家样本公司综合成本率中位数为 {median:.1f}%；"
        f"{profitable} 家低于 100% 实现承保盈利，{loss_making} 家高于 100%。"
        f"最高为{high_company}（{high_value:.1f}%），最低为{low_company}（{low_value:.1f}%）。"
        + solvency_text_zh,
        f"In {target_year}, the median combined ratio across {len(values)} companies is "
        f"{median:.1f}%; {profitable} are below 100% (underwriting profit) and "
        f"{loss_making} are above 100%. Highest: {high_company} ({high_value:.1f}%); "
        f"lowest: {low_company} ({low_value:.1f}%). " + solvency_text_en,
    )