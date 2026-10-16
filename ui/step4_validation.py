"""STEP4 勾稽检查：规则装载、结果汇总、筛选展示与工作簿导出。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from services.report_profiles import ReportProfile
from services.solvency_validator import validate_standard_data

from .cache import read_rules, validation_results_to_xlsx


def render_validation_tab(
    active_profile: ReportProfile,
    rules_path: Path,
    taxonomy: pd.DataFrame,
) -> None:
    """STEP4 勾稽检查。"""
    st.subheader(f"{active_profile.profile_name}勾稽检查")
    st.caption(
        "检查 STEP3 标准窄表的数据规范、六项关键指标完整性、公式勾稽和跨表一致性；"
        "检查过程只读，不会自动修改标准化结果。"
    )
    if st.session_state.standard_data.empty:
        st.info("请先完成 STEP3 标准化。")
    elif st.button(
        "执行勾稽检查",
        type="primary",
        key="validate_data",
        icon=":material/fact_check:",
    ):
        st.session_state.validation_results = validate_standard_data(
            st.session_state.standard_data,
            read_rules(str(rules_path)),
            taxonomy,
        )
    validation_results = st.session_state.validation_results
    required_result_columns = {"check_type", "rule_name", "status", "severity"}
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
                "请根据处理建议返回 STEP2/STEP3 修正后重新检查。"
            )
        elif status_counts.get("需复核", 0):
            st.info("公式勾稽未发现阻断项，但仍有重复记录等事项需要人工复核。")
        else:
            st.success("第一阶段勾稽检查已通过，可以继续进入 STEP5 数据集成。")

        available_statuses = [
            status
            for status in ("未通过", "缺失", "需复核", "执行错误", "通过", "不适用")
            if status_counts.get(status, 0)
        ]
        issue_statuses = [
            status
            for status in ("未通过", "缺失", "需复核", "执行错误")
            if status in available_statuses
        ]
        filter_columns = st.columns(2)
        selected_statuses = filter_columns[0].multiselect(
            "状态筛选",
            available_statuses,
            default=issue_statuses or available_statuses,
            key="step4_status_filter",
        )
        available_types = list(dict.fromkeys(validation_results["check_type"].astype(str)))
        selected_types = filter_columns[1].multiselect(
            "检查类别",
            available_types,
            default=available_types,
            key="step4_type_filter",
        )
        filtered_results = validation_results[
            validation_results["status"].astype(str).isin(selected_statuses)
            & validation_results["check_type"].astype(str).isin(selected_types)
        ].copy()
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
            "status": "状态",
            "notes": "检查说明",
            "involved_metrics": "涉及指标编码",
            "source_pages": "来源页码",
            "suggestion": "处理建议",
        }
        st.dataframe(
            filtered_results[list(display_columns)].rename(columns=display_columns),
            width="stretch",
            hide_index=True,
            column_config={
                "披露值/实际数": st.column_config.NumberColumn(format="%.4f"),
                "系统计算值/应有数": st.column_config.NumberColumn(format="%.4f"),
                "差异": st.column_config.NumberColumn(format="%.4f"),
                "容差": st.column_config.NumberColumn(format="%.4f"),
            },
        )
        st.download_button(
            "下载勾稽检查工作簿",
            validation_results_to_xlsx(validation_results),
            "偿付能力_STEP4勾稽检查结果.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
        )
