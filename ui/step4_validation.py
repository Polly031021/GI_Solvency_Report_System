"""STEP4 勾稽检查：规则装载、失败原因、STEP3 依据与工作簿导出。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from services.report_profiles import ReportProfile
from services.solvency_validator import (
    validate_rule_configuration,
    validate_standard_data_detailed,
)

from .cache import read_rules, validation_results_to_xlsx
from .config import WORKFLOW_TAB_LABELS


ISSUE_STATUSES = ("未通过", "缺失", "执行错误", "需复核")
STATUS_ORDER = {
    "未通过": 0,
    "缺失": 1,
    "执行错误": 2,
    "需复核": 3,
    "通过": 4,
    "不适用": 5,
}


def _format_metric(value: Any, unit: str = "") -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    use_small_precision = number != 0 and abs(number) < 0.01
    if unit == "%":
        return f"{number:,.4f}%" if use_small_precision else f"{number:,.2f}%"
    if unit == "倍":
        return f"{number:,.4f} 倍" if use_small_precision else f"{number:,.2f} 倍"
    if unit:
        return (
            f"{number:,.4f} {unit}"
            if use_small_precision
            else f"{number:,.2f} {unit}"
        )
    return f"{number:,.4f}" if use_small_precision else f"{number:,.2f}"


def _actual_unit(row: pd.Series) -> str:
    rule_id = str(row.get("rule_id", ""))
    if "RATIO" in rule_id:
        return "%"
    return str(row.get("tolerance_unit", "") or "")


def _goto_step3(focus: dict[str, Any]) -> None:
    """Switch to STEP3 in a widget callback, then let Streamlit rerun."""
    st.session_state["step3_focus"] = focus
    st.session_state["workflow_main_tab"] = WORKFLOW_TAB_LABELS[3]


def _related_step3_rows(
    evidence: pd.DataFrame,
    standard_data: pd.DataFrame,
) -> pd.DataFrame:
    record_ids: list[int] = []
    for value in evidence.get("record_id", pd.Series(dtype=object)).tolist():
        try:
            record_id = int(float(value))
        except (TypeError, ValueError):
            continue
        if 1 <= record_id <= len(standard_data):
            record_ids.append(record_id)
    record_ids = list(dict.fromkeys(record_ids))
    if not record_ids or standard_data.empty:
        return pd.DataFrame()
    related = standard_data.iloc[[record_id - 1 for record_id in record_ids]].copy()
    related.insert(0, "STEP3记录号", record_ids)
    return related


def _render_issue_detail(
    row: pd.Series,
    evidence: pd.DataFrame,
    standard_data: pd.DataFrame,
) -> None:
    status = str(row.get("status", ""))
    reason = str(row.get("failure_reason", "") or row.get("notes", "") or "").strip()
    rule_name = str(row.get("rule_name", "")).strip()
    period = str(row.get("period", "")).strip() or "未标注期间"
    company = str(row.get("company", "")).strip()
    report_period = str(row.get("report_period", "")).strip()
    unit = _actual_unit(row)

    with st.container(border=True):
        st.markdown(
            f"#### {rule_name}｜{company}｜{report_period}｜{period}"
        )
        if status == "需复核":
            st.warning(f"状态：{status}", icon=":material/rate_review:")
        else:
            st.error(f"状态：{status}", icon=":material/error:")

        metric_columns = st.columns(4)
        metric_columns[0].metric(
            "披露值/实际数", _format_metric(row.get("actual"), unit)
        )
        metric_columns[1].metric(
            "系统计算值/应有数", _format_metric(row.get("expected"), unit)
        )
        metric_columns[2].metric(
            "差异",
            _format_metric(row.get("difference"), str(row.get("tolerance_unit", ""))),
        )
        metric_columns[3].metric(
            "容差",
            _format_metric(row.get("tolerance"), str(row.get("tolerance_unit", ""))),
        )

        st.markdown(f"**失败原因**：{reason or '未记录失败原因，请重新执行勾稽检查。'}")
        formula = str(row.get("formula", "")).strip()
        if formula:
            st.caption(f"校验口径：{formula}")

        result_id = str(row.get("result_id", ""))
        issue_evidence = evidence[
            evidence.get("result_id", pd.Series(dtype=str)).astype(str).eq(result_id)
        ].copy()
        if issue_evidence.empty:
            st.info(
                "本次检查没有可定位的 STEP3 指标记录。"
                "若刚升级过系统，请重新点击“执行勾稽检查”生成明细依据。"
            )
        else:
            issue_evidence["record_id"] = issue_evidence["record_id"].map(
                lambda value: int(float(value))
                if pd.notna(value) and str(value).strip() not in {"", "nan"}
                else None
            )
            evidence_display = issue_evidence.rename(
                columns={
                    "record_id": "STEP3记录号",
                    "metric_code": "指标编码",
                    "metric_name": "指标名称",
                    "role": "检查角色",
                    "period": "期间组",
                    "status": "记录状态",
                    "actual_value": "STEP3数值/披露值",
                    "expected_value": "应有值/标准值",
                    "unit": "单位",
                    "source_sheet": "来源工作表",
                    "source_page": "来源页码",
                    "message": "问题说明",
                }
            )
            evidence_columns = [
                column
                for column in (
                    "STEP3记录号", "指标编码", "指标名称", "检查角色", "期间组",
                    "记录状态", "STEP3数值/披露值", "应有值/标准值", "单位",
                    "来源工作表", "来源页码", "问题说明",
                )
                if column in evidence_display.columns
            ]
            st.markdown("**失败原因对应的 STEP3 指标记录**")
            st.caption(
                "“缺失”表示 STEP3 没有该指标记录；“期间口径不一致”表示指标存在但期间映射不同；"
                "“数值无法解析”表示 STEP3 有记录但数值不是数字。"
            )
            st.dataframe(
                evidence_display[evidence_columns],
                width="stretch",
                hide_index=True,
            )

            related = _related_step3_rows(issue_evidence, standard_data)
            if related.empty:
                st.warning(
                    "STEP3 中没有找到该指标的有效记录，属于缺失项。"
                    "请回到 STEP3 检查指标映射、期间口径或原始提取结果。"
                )
            else:
                related_columns = [
                    column
                    for column in (
                        "STEP3记录号", "公司", "报告期", "指标编码", "指标名称",
                        "期间口径", "数值", "单位", "来源工作表", "来源页码",
                        "指标属性", "来源类型", "备注", "计算逻辑",
                    )
                    if column in related.columns
                ]
                st.markdown("**STEP3 原始记录**")
                st.caption(
                    "STEP3记录号对应 STEP3 标准窄表的行序号（从 1 开始），"
                    "可直接与 STEP3“标准窄表预览”逐行核对。"
                )
                st.dataframe(
                    related[related_columns],
                    width="stretch",
                    hide_index=True,
                )

        record_ids: list[int] = []
        for value in issue_evidence.get("record_id", pd.Series(dtype=object)).tolist():
            try:
                record_id = int(float(value))
            except (TypeError, ValueError):
                continue
            record_ids.append(record_id)
        focus = {
            "rule_name": rule_name,
            "status": status,
            "company": company,
            "report_period": report_period,
            "period": period,
            "failure_reason": reason,
            "metric_codes": [
                code
                for code in str(row.get("involved_metrics", "")).split("、")
                if code.strip()
            ],
            "record_ids": list(dict.fromkeys(record_ids)),
        }
        st.button(
            "前往 STEP3 定位这些记录",
            key=f"step4_goto_step3_{result_id}",
            on_click=_goto_step3,
            args=(focus,),
            icon=":material/arrow_forward:",
        )


def render_validation_tab(
    active_profile: ReportProfile,
    rules_path: Path,
    taxonomy: pd.DataFrame,
) -> None:
    """STEP4 勾稽检查。"""
    st.subheader(f"{active_profile.profile_name}勾稽检查")
    st.caption(
        "对当前标准数据中的全部公司批量执行勾稽检查。公式、适用期间和容差均来自"
        " config/solvency_validation_rules.xlsx；检查过程只读，失败项可下钻到 STEP3 记录。"
    )
    rules = read_rules(
        str(rules_path),
        rules_path.stat().st_mtime_ns if rules_path.exists() else 0,
    )
    with st.expander("勾稽规则配置与语法校验", expanded=False):
        st.caption(
            f"配置文件：{rules_path}。修改 Excel 后点击“重新读取规则配置”即可生效，"
            "无需修改代码。右值表达式支持指标编码、数字、括号和 + - * /。"
        )
        config_check = validate_rule_configuration(rules, taxonomy)
        config_ok = bool(
            not config_check.empty
            and config_check["状态"].astype(str).eq("通过").all()
        )
        if config_ok:
            st.success(config_check.iloc[-1]["说明"])
        else:
            st.error("规则配置存在错误，请先修正后再执行勾稽检查。")
            st.dataframe(config_check, width="stretch", hide_index=True)
        with st.container(horizontal=True):
            st.download_button(
                "下载勾稽规则配置",
                rules_path.read_bytes(),
                "solvency_validation_rules.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                key="download_validation_rule_config",
            )
            if st.button(
                "重新读取规则配置",
                key="reload_validation_rule_config",
                icon=":material/refresh:",
            ):
                read_rules.clear()
                st.rerun()
        rule_columns = [
            column
            for column in (
                "规则ID", "规则名称", "适用期间", "容差", "容差单位",
                "启用", "校验方式", "左值指标", "右值表达式", "规则说明",
            )
            if column in rules.columns
        ]
        st.dataframe(
            rules[rule_columns],
            width="stretch",
            hide_index=True,
        )
    with st.expander("查看勾稽检查逻辑与失败项说明", expanded=False):
        st.markdown(
            """
            - **数据规范**：检查必填字段、指标编码是否登记、指标名称/单位/数据类型是否符合指标字典、
              期间口径是否允许、数值是否可解析，以及同一来源是否存在重复或冲突记录。
            - **完整性**：检查当前期末的六项核心指标（认可资产、认可负债、实际资本、最低资本、
              核心偿付能力充足率、综合偿付能力充足率）；若 STEP3 中存在上期或下期预测数据，也会一并检查。
            - **公式勾稽**：按配置文件校验充足率、溢额、认可资产、实际资本、最低资本及量化风险、
              非寿险保险风险、市场风险、信用风险的分项构成。
            - **容差**：默认要求披露值与公式计算值的差额绝对值小于 0.0001；可在规则表中逐条调整。
            - **跨表一致性**：同一指标在不同来源工作表披露时应一致。系统优先以主要指标表为基准，
              与实际资本表、最低资本表等其他来源比对，而不是简单取最大值和最小值。
            - **失败原因**：每条未通过、缺失、需复核或执行错误的检查项都会给出具体原因；
              “STEP3记录号”对应 STEP3 标准窄表的行序号，可据此回到 STEP3 核对原始数值、期间口径和来源页码。
            - **状态含义**：`未通过` 表示差异超过容差；`缺失` 表示公式依赖指标或披露值缺失；
              `需复核` 表示重复记录等非阻断问题；`执行错误` 表示规则本身无法执行。
            """
        )
    if st.session_state.standard_data.empty:
        st.info("请先完成 STEP3 标准化。")
    elif st.button(
        (
            f"批量执行勾稽检查"
            f"（{st.session_state.standard_data['公司'].nunique()} 家公司）"
        ),
        type="primary",
        key="validate_data",
        icon=":material/fact_check:",
        disabled=not config_ok,
    ):
        validation_progress = st.progress(
            0,
            text="正在批量执行数据规范、完整性与公式勾稽…",
        )
        results, evidence = validate_standard_data_detailed(
            st.session_state.standard_data,
            rules,
            taxonomy,
            progress_callback=lambda index, total, company: (
                validation_progress.progress(
                    index / max(total, 1),
                    text=(
                        f"已检查 {index}/{total} 家公司：{company}"
                        f"｜逻辑：配置化公式勾稽，容差 < 0.0001"
                    ),
                )
            ),
        )
        validation_progress.progress(
            1.0,
            text=f"勾稽检查完成：{len(results):,} 个检查项",
        )
        st.session_state.validation_results = results
        st.session_state.validation_evidence = evidence
        st.session_state.step3_focus = {}
        st.session_state.batch_last_operation = (
            f"批量勾稽检查完成：{len(results):,} 个检查项"
        )

    validation_results = st.session_state.validation_results
    validation_evidence = st.session_state.get("validation_evidence", pd.DataFrame())
    required_result_columns = {
        "result_id",
        "check_type",
        "rule_name",
        "status",
        "severity",
        "failure_reason",
        "evidence_count",
    }
    if not validation_results.empty and not required_result_columns.issubset(
        validation_results.columns
    ):
        st.info("Step4 规则已升级，请重新点击“执行勾稽检查”生成新版结果。")
    elif not validation_results.empty:
        status_counts = validation_results["status"].astype(str).value_counts().to_dict()
        summary_columns = st.columns(6)
        summary_columns[0].metric("检查项", f"{len(validation_results):,}")
        summary_columns[1].metric("通过", f"{status_counts.get('通过', 0):,}")
        summary_columns[2].metric("未通过", f"{status_counts.get('未通过', 0):,}")
        summary_columns[3].metric("缺失", f"{status_counts.get('缺失', 0):,}")
        summary_columns[4].metric("需复核", f"{status_counts.get('需复核', 0):,}")
        summary_columns[5].metric(
            "不适用/错误",
            f"{status_counts.get('不适用', 0) + status_counts.get('执行错误', 0):,}",
        )

        blocking_count = sum(
            status_counts.get(status, 0) for status in ("未通过", "缺失", "执行错误")
        )
        if blocking_count:
            st.warning(
                f"发现 {blocking_count:,} 项未通过、缺失或执行错误。"
                "请在下方“失败项排查”查看失败原因，并核对对应的 STEP3 记录。"
            )
        elif status_counts.get("需复核", 0):
            st.info("公式勾稽未发现阻断项，但仍有重复记录等事项需要人工复核。")
        else:
            st.success("第一阶段勾稽检查已通过，可以继续进入 STEP5 数据集成。")

        company_summary = (
            validation_results.groupby(["company", "status"], dropna=False)
            .size()
            .unstack(fill_value=0)
            .reset_index()
            .rename(columns={"company": "公司"})
        )
        st.markdown("#### 按公司汇总")
        st.dataframe(
            company_summary,
            width="stretch",
            hide_index=True,
        )

        available_statuses = [
            status
            for status in ("未通过", "缺失", "需复核", "执行错误", "通过", "不适用")
            if status_counts.get(status, 0)
        ]
        issue_statuses = [
            status
            for status in ISSUE_STATUSES
            if status in available_statuses
        ]
        available_types = list(dict.fromkeys(validation_results["check_type"].astype(str)))
        company_series = (
            validation_results["company"]
            .fillna("")
            .astype(str)
            .replace("", "（未标注公司）")
        )
        available_companies = list(dict.fromkeys(company_series.tolist()))
        for filter_key, options in (
            ("step4_status_filter", available_statuses),
            ("step4_type_filter", available_types),
            ("step4_company_filter", available_companies),
        ):
            current = st.session_state.get(filter_key)
            if current is not None and not set(current).issubset(set(options)):
                st.session_state.pop(filter_key, None)
        filter_columns = st.columns(3)
        selected_statuses = filter_columns[0].multiselect(
            "状态筛选",
            available_statuses,
            default=issue_statuses or available_statuses,
            key="step4_status_filter",
        )
        selected_types = filter_columns[1].multiselect(
            "检查类别",
            available_types,
            default=available_types,
            key="step4_type_filter",
        )
        selected_companies = filter_columns[2].multiselect(
            "公司",
            available_companies,
            default=available_companies,
            key="step4_company_filter",
        )
        filtered_results = validation_results[
            validation_results["status"].astype(str).isin(selected_statuses)
            & validation_results["check_type"].astype(str).isin(selected_types)
            & company_series.isin(selected_companies)
        ].copy()
        filtered_results["_状态顺序"] = (
            filtered_results["status"].map(STATUS_ORDER).fillna(99)
        )
        filtered_results = filtered_results.sort_values(
            ["_状态顺序", "company", "report_period", "period", "rule_name"],
            kind="stable",
        ).drop(columns="_状态顺序")

        display_columns = {
            "check_type": "检查类别",
            "severity": "严重程度",
            "rule_name": "规则名称",
            "company": "公司",
            "report_period": "报告期",
            "period": "期间组",
            "actual": "披露值/实际数",
            "expected": "系统计算值/应有数",
            "difference": "差异",
            "tolerance": "容差",
            "tolerance_unit": "容差单位",
            "status": "状态",
            "failure_reason": "失败原因",
            "suggestion": "处理建议",
            "involved_metrics": "涉及指标编码",
            "source_pages": "来源页码",
            "evidence_count": "STEP3依据条数",
        }
        st.dataframe(
            filtered_results[list(display_columns)].rename(columns=display_columns),
            width="stretch",
            hide_index=True,
            height=420,
            column_config={
                "披露值/实际数": st.column_config.NumberColumn(format="%.4f"),
                "系统计算值/应有数": st.column_config.NumberColumn(format="%.4f"),
                "差异": st.column_config.NumberColumn(format="%.4f"),
                "容差": st.column_config.NumberColumn(format="%.4f"),
                "失败原因": st.column_config.TextColumn(width="large"),
                "STEP3依据条数": st.column_config.NumberColumn(format="%d"),
            },
        )

        issue_results = filtered_results[
            filtered_results["status"].astype(str).isin(ISSUE_STATUSES)
        ].copy()
        st.divider()
        st.markdown("### 失败项排查（与 STEP3 对应）")
        if issue_results.empty:
            st.success("当前筛选范围内没有未通过、缺失、执行错误或需复核的检查项。")
        else:
            st.caption(
                "选择任一失败项，可查看失败原因、公式代入值、"
                "缺失或异常的具体指标，以及这些指标在 STEP3 中的原始记录。"
            )
            issue_labels = [
                (
                    f"{row.status}｜{row.rule_name}｜{row.company}｜"
                    f"{row.report_period}｜{row.period or '未标注期间'}"
                )
                for row in issue_results.itertuples()
            ]
            selector_key = "step4_issue_selector"
            if st.session_state.get(selector_key) not in issue_labels:
                st.session_state.pop(selector_key, None)
            selected_label = st.selectbox(
                "选择要查看的失败项",
                issue_labels,
                index=0,
                key=selector_key,
            )
            selected_issue = issue_results.iloc[
                issue_labels.index(selected_label)
            ]
            _render_issue_detail(
                selected_issue,
                validation_evidence,
                st.session_state.standard_data,
            )

        st.download_button(
            "下载勾稽检查工作簿",
            validation_results_to_xlsx(
                validation_results,
                validation_evidence,
                st.session_state.standard_data,
            ),
            "偿付能力_STEP4勾稽检查结果.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
        )
