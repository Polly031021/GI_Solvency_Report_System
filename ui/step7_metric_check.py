"""STEP3 页面上的 STEP7 指标完整性检查与补录合并入口。"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
import streamlit as st

from services.solvency_dataset_adapter import standard_workbook_bytes
from services.solvency_step3_standardizer import (
    _target_summary,
    batch_result_workbook_bytes,
)
from services.solvency_step7_metric_catalog import (
    build_step7_metric_catalog_workbook_bytes,
)
from services.step3_metric_gap import (
    build_step7_metric_gap,
    merge_missing_metric_fills,
    missing_metric_template_workbook_bytes,
    read_missing_metric_fills,
)


def batch_company_periods(documents: dict) -> list[dict]:
    """Build the company/report-period list for companies that produced no rows."""
    rows: list[dict] = []
    for company, document in (documents or {}).items():
        metadata = document.get("metadata") or {}
        item = {
            "公司": company,
            "报告期": metadata.get("报告期", ""),
            "报告年度": metadata.get("报告年度", ""),
            "报告季度": metadata.get("报告季度", ""),
            "公司统一编码": "",
            "公司类型": metadata.get("公司类型", ""),
            "同业分类": metadata.get("同业分类", ""),
            "报告类型": metadata.get("报告类型", ""),
            "来源文件": document.get("pdf_name", ""),
        }
        standard = document.get("standard_data")
        if isinstance(standard, pd.DataFrame) and not standard.empty:
            first = standard.iloc[0]
            for column in (
                "原始公司名称",
                "标准公司名称",
                "公司统一编码",
                "公司类型",
                "同业分类",
                "报告类型",
                "报告年度",
                "报告季度",
                "报告期",
                "披露日期",
                "导入批次",
            ):
                value = str(first.get(column, "") or "").strip()
                if value:
                    item[column] = value
        rows.append(item)
    return rows


def _merged_workbook_bytes(data: pd.DataFrame) -> bytes:
    catalog = st.session_state.get("standard_target_catalog")
    if isinstance(catalog, pd.DataFrame) and not catalog.empty:
        diagnostics = st.session_state.get("normalization_diagnostics")
        if not isinstance(diagnostics, pd.DataFrame):
            diagnostics = pd.DataFrame()
        summary = _target_summary(catalog, data, diagnostics)
        st.session_state.standard_target_summary = summary
        return batch_result_workbook_bytes(data, summary, diagnostics)
    return standard_workbook_bytes(data)


def render_step7_metric_check(
    data: pd.DataFrame,
    *,
    taxonomy: pd.DataFrame,
    key_prefix: str,
    company_periods: Sequence[dict] | None = None,
) -> None:
    """Render the STEP7 requirement check, export and merge workflow."""
    with st.container(border=True):
        st.markdown("#### STEP7 指标完整性检查")
        st.caption(
            "以《Step7_指标需求清单.xlsx》为准，检查本次标准化结果是否覆盖 STEP7 "
            "公司报告所需的全部基础指标。缺失指标可导出标准补录表，填数后直接合并。"
        )

        report = build_step7_metric_gap(
            data,
            taxonomy=taxonomy,
            company_periods=company_periods,
        )
        if report.requirement_catalog.empty:
            st.error("STEP7 指标需求目录为空，请检查模板或指标字典。")
            return

        metric_columns = st.columns(4)
        metric_columns[0].metric(
            "STEP7 基础指标",
            report.required_base_count,
            border=True,
        )
        metric_columns[1].metric(
            "公司×报告期",
            report.company_count,
            border=True,
        )
        metric_columns[2].metric(
            "缺失指标组合",
            report.missing_count,
            border=True,
        )
        metric_columns[3].metric(
            "基础指标覆盖率",
            f"{report.coverage_ratio:.1%}",
            border=True,
        )

        for warning in report.warnings:
            st.warning(warning, icon=":material/warning:")

        if report.missing_count:
            st.warning(
                f"STEP7 所需基础指标还有 **{report.missing_count}** 个"
                f"“公司×指标”缺口，涉及 **{report.missing_code_count}** 个指标。"
                "建议先导出补录表，补齐后再进入 STEP4/STEP5。",
                icon=":material/rule:",
            )
            st.markdown("**缺口最多的指标**")
            st.dataframe(
                report.missing_summary().rename(
                    columns={
                        "缺失公司数": "涉及公司数",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
            with st.expander("查看各公司覆盖情况", expanded=False):
                st.dataframe(
                    report.coverage,
                    width="stretch",
                    hide_index=True,
                )
            with st.expander("查看缺失指标明细", expanded=False):
                detail_columns = [
                    column
                    for column in (
                        "公司",
                        "报告期",
                        "指标编码",
                        "指标名称",
                        "一级模块",
                        "二级模块",
                        "单位",
                        "期间口径",
                        "补录优先级",
                    )
                    if column in report.missing.columns
                ]
                st.dataframe(
                    report.missing[detail_columns],
                    width="stretch",
                    hide_index=True,
                )
        else:
            st.success(
                "STEP7 所需基础指标均已覆盖，无需补录。",
                icon=":material/check_circle:",
            )

        if report.derived_gaps is not None and not report.derived_gaps.empty:
            with st.expander(
                f"派生指标暂无法计算（{len(report.derived_gaps)} 条）",
                expanded=False,
            ):
                st.caption(
                    "派生指标本身不需要手工填数；补录其依赖的基础指标后，"
                    "系统会在合并时自动补算。"
                )
                st.dataframe(
                    report.derived_gaps,
                    width="stretch",
                    hide_index=True,
                )

        download_left, download_right = st.columns(2)
        with download_left:
            st.download_button(
                "下载缺失指标补录表",
                missing_metric_template_workbook_bytes(report),
                f"STEP7缺失指标补录表_{key_prefix}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                key=f"{key_prefix}_download_missing_template",
                disabled=report.missing_count == 0,
                help="数值列留空，按单位列口径填写后可在下方上传合并。",
            )
        with download_right:
            st.download_button(
                "下载 STEP7 指标需求清单",
                build_step7_metric_catalog_workbook_bytes(taxonomy),
                "Step7_指标需求清单.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                key=f"{key_prefix}_download_step7_catalog",
                help="用于长期维护 STEP7 需要的指标、图表映射与单位口径。",
            )

        uploaded = st.file_uploader(
            "上传已填写的缺失指标补录表",
            type=["xlsx"],
            key=f"{key_prefix}_missing_fill_upload",
            help="只读取“缺失指标清单”工作表；空白行会被忽略。",
        )
        if uploaded is not None:
            if st.button(
                "应用补录并合并到标准数据",
                type="primary",
                icon=":material/merge:",
                key=f"{key_prefix}_apply_missing_fill",
            ):
                try:
                    parsed = read_missing_metric_fills(uploaded.getvalue())
                except Exception as exc:
                    st.error(f"补录表读取失败：{exc}")
                    return
                if parsed.rows.empty:
                    st.warning("补录表中没有填写任何数值，请先填写“数值”列。")
                    if not parsed.skipped.empty:
                        st.dataframe(
                            parsed.skipped,
                            width="stretch",
                            hide_index=True,
                        )
                    return
                result = merge_missing_metric_fills(
                    st.session_state.standard_data,
                    parsed.rows,
                    taxonomy=taxonomy,
                )
                st.session_state.standard_data = result.data
                st.session_state.standard_workbook_bytes = _merged_workbook_bytes(
                    result.data
                )
                # 批量页面会在下一次 rerun 用该覆盖值刷新合并结果。
                st.session_state["step3_gap_merged_data"] = result.data
                for warning in (*result.warnings, *parsed.warnings):
                    st.warning(warning)
                if result.added_count:
                    st.success(
                        f"已合并 {result.added_count} 条补录记录，"
                        f"跳过 {result.skipped_count} 条。"
                    )
                else:
                    st.warning(
                        "没有新增记录：填写项与现有数据重复，或数值无法解析。"
                    )
                if not parsed.skipped.empty:
                    with st.expander(
                        f"未合并的行（{len(parsed.skipped)} 条）",
                        expanded=False,
                    ):
                        st.dataframe(
                            parsed.skipped,
                            width="stretch",
                            hide_index=True,
                        )
                st.rerun()
