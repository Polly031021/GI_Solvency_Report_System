"""STEP3 标准化：单份目标表填报与多家公司批处理（定位—核对—提取三阶段）。"""

from __future__ import annotations

import dataclasses
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from services.report_profiles import ReportProfile
from services.solvency_dataset_adapter import standard_workbook_bytes
from services.registered_capital_extractor import append_registered_capital
from services.solvency_step3_standardizer import (
    _target_summary,
    read_target_template,
    result_workbook_bytes,
    standardize_to_target,
    step3_metric_catalog,
)
from services.solvency_normalizer import STANDARD_COLUMNS, narrow_table_view
from services.batch_extraction import sanitize_scalar, sanitize_tree
from services.batch_processor import (
    build_context_from_profile,
    locate_one_pdf_bytes,
    process_one_pdf_bytes,
)

from .cache import (
    cached_step3_target_template,
    dataframe_to_xlsx,
    read_company_master,
    read_peer_groups,
    render_pdf_page,
)
from .config import (
    NON_LIFE_COMPANY_LIST,
    NON_LIFE_COMPANY_URLS,
    PEER_GROUP_CONFIG,
)
from .helpers import parse_page_numbers
from .session import llm_credentials_ready


def _render_step3_focus_panel() -> None:
    """Show the STEP3 rows that a STEP4 failure linked to."""
    focus = st.session_state.get("step3_focus")
    if not isinstance(focus, dict) or not focus:
        return
    standard_data = st.session_state.standard_data
    company = str(focus.get("company", "")).strip()
    report_period = str(focus.get("report_period", "")).strip()
    period = str(focus.get("period", "")).strip()
    metric_codes = [
        str(code).strip()
        for code in focus.get("metric_codes", [])
        if str(code).strip()
    ]
    record_ids: list[int] = []
    for value in focus.get("record_ids", []):
        try:
            record_ids.append(int(float(value)))
        except (TypeError, ValueError):
            continue

    with st.container(border=True):
        st.markdown("#### STEP4 勾稽定位")
        st.caption(
            f"来源检查：{focus.get('rule_name', '')}｜"
            f"{company or '未标注公司'}｜{report_period or '未标注报告期'}｜"
            f"{period or '未标注期间'}"
        )
        reason = str(focus.get("failure_reason", "")).strip()
        if reason:
            st.warning(f"失败原因：{reason}")

        rows = pd.DataFrame()
        record_numbers: list[int] = []
        if not standard_data.empty:
            valid_ids = [
                record_id
                for record_id in record_ids
                if 1 <= record_id <= len(standard_data)
            ]
            if valid_ids:
                candidate = standard_data.iloc[
                    [record_id - 1 for record_id in valid_ids]
                ].copy()
                if not company or candidate["公司"].astype(str).eq(company).any():
                    rows = candidate
                    record_numbers = valid_ids
            if rows.empty and metric_codes:
                mask = standard_data["指标编码"].astype(str).str.strip().isin(metric_codes)
                if company:
                    mask &= standard_data["公司"].astype(str).str.strip().eq(company)
                if report_period:
                    mask &= standard_data["报告期"].astype(str).str.strip().eq(report_period)
                record_numbers = [
                    position + 1
                    for position, keep in enumerate(mask.tolist())
                    if keep
                ]
                rows = standard_data.iloc[
                    [record_number - 1 for record_number in record_numbers]
                ].copy()

        if rows.empty:
            st.warning(
                "STEP3 当前标准窄表中没有找到这些指标记录，属于缺失项。"
                "请检查指标映射、期间口径或原始提取结果。"
            )
        else:
            rows = rows.reset_index(drop=True)
            rows.insert(0, "STEP3记录号", record_numbers)
            columns = [
                column
                for column in (
                    "STEP3记录号", "公司", "报告期", "指标编码", "指标名称",
                    "期间口径", "数值", "单位", "来源工作表", "来源页码",
                    "指标属性", "来源类型", "备注", "计算逻辑",
                )
                if column in rows.columns
            ]
            st.dataframe(rows[columns], width="stretch", hide_index=True)
            if metric_codes:
                present_codes = set(rows["指标编码"].astype(str).str.strip())
                missing_codes = [
                    code for code in metric_codes if code not in present_codes
                ]
                if missing_codes:
                    st.warning(
                        "以下检查涉及指标在 STEP3 中没有记录："
                        + "、".join(missing_codes)
                    )

        if st.button(
            "清除 STEP4 定位",
            key="clear_step3_focus",
            icon=":material/close:",
        ):
            st.session_state.pop("step3_focus", None)
            st.rerun()


def _render_step3_single_tab(active_profile: ReportProfile, taxonomy: pd.DataFrame) -> None:
    """STEP3：单份 PDF 目标表标准填报。"""
    st.subheader(f"{active_profile.profile_name}目标表标准填报")
    _render_step3_focus_panel()
    with st.container(border=True):
        st.markdown("#### 功能说明")
        st.write(
            "按目标指标清单将 STEP2 提取结果填入标准窄表，并自动完成单位换算与派生指标计算。"
            "输出字段和可用指标范围与 STEP5 的精确映射口径完全一致。"
        )

    include_derived = active_profile.profile_id == "LIFE_SOLVENCY"
    system_target_catalog = step3_metric_catalog(
        taxonomy,
        include_derived=include_derived,
    )

    use_default_target = st.toggle(
        "使用系统默认目标表",
        value=True,
        key="step3_use_default_target",
        help="默认目标表直接引用 STEP5 当前可映射指标目录。关闭后可上传自定义指标子集。",
    )
    target_upload = None
    if use_default_target:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.caption(
                f"当前默认目标表包含 {len(system_target_catalog):,} 个指标，"
                "可下载后将不需要的指标设为“不启用”。"
            )
            st.download_button(
                "下载系统目标表",
                cached_step3_target_template(
                    system_target_catalog,
                    active_profile.profile_name,
                ),
                f"{active_profile.profile_id}_STEP3目标表.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                key=f"download_step3_target_{active_profile.profile_id}",
            )
    else:
        target_upload = st.file_uploader(
            "上传自定义目标表",
            type=["xlsx"],
            key="step3_target_upload",
            help="建议使用系统目标表修改“启用”列；指标编码不得超出 STEP3 正式指标字典。",
        )

    company_type = active_profile.company_types[0]
    if (
        active_profile.profile_id == "NON_LIFE_SOLVENCY"
        and NON_LIFE_COMPANY_LIST.exists()
    ):
        try:
            company_master = read_company_master(
                NON_LIFE_COMPANY_LIST,
                NON_LIFE_COMPANY_LIST.stat().st_mtime_ns,
                NON_LIFE_COMPANY_URLS,
                NON_LIFE_COMPANY_URLS.stat().st_mtime_ns
                if NON_LIFE_COMPANY_URLS.exists()
                else 0,
            )
        except Exception as exc:
            st.warning(
                f"财险公司分类表读取失败，暂按“其他”处理：{exc}",
                icon=":material/warning:",
            )
            peer_group_map, default_peer_group = read_peer_groups(
                PEER_GROUP_CONFIG,
                PEER_GROUP_CONFIG.stat().st_mtime_ns,
            )
            company_type_map = {}
        else:
            peer_group_map = company_master.peer_group_map
            company_type_map = company_master.company_type_map
            default_peer_group = company_master.default_peer_group
    else:
        peer_group_map, default_peer_group = read_peer_groups(
            PEER_GROUP_CONFIG,
            PEER_GROUP_CONFIG.stat().st_mtime_ns,
        )
        company_type_map = {}
    standardize_submitted = st.button(
        "启动目标表标准填报",
        type="primary",
        icon=":material/auto_fix_high:",
        disabled=(
            not st.session_state.raw_tables or (not use_default_target and target_upload is None)
        ),
        key="step3_standardize_target",
    )

    if not st.session_state.raw_tables:
        st.info("请先在 STEP2 完成表格提取。")
    elif not use_default_target and target_upload is None:
        st.info("请上传自定义目标表，或切回系统默认目标表。")

    if standardize_submitted:
        st.session_state.standard_data = pd.DataFrame(columns=STANDARD_COLUMNS)
        st.session_state.validation_results = pd.DataFrame()
        st.session_state.validation_evidence = pd.DataFrame()
        st.session_state.step3_focus = {}
        st.session_state.standard_workbook_bytes = b""
        st.session_state.normalization_diagnostics = pd.DataFrame()
        st.session_state.standard_target_summary = pd.DataFrame()
        st.session_state.standardization_warnings = []
        with st.status("正在进行目标表标准填报…", expanded=True) as status_box:
            try:
                for table in st.session_state.raw_tables:
                    if isinstance(getattr(table, "table_config", None), dict):
                        table.table_config = sanitize_tree(table.table_config)
                    if not isinstance(getattr(table, "evidence", ""), str):
                        table.evidence = sanitize_scalar(table.evidence)
                    if not isinstance(getattr(table, "table_name", None), str):
                        table.table_name = sanitize_scalar(table.table_name)

                status_box.write("正在读取目标指标清单并校验 STEP3 正式指标范围…")
                if use_default_target:
                    target_catalog = system_target_catalog
                    template_warnings: tuple[str, ...] = ()
                    template_label = "系统默认目标表"
                else:
                    target_catalog, template_warnings = read_target_template(
                        target_upload.getvalue(),
                        target_upload.name,
                        system_target_catalog,
                    )
                    template_label = target_upload.name

                status_box.write("正在匹配披露指标、识别期间口径并统一计量单位…")
                _metadata_company = str(
                    st.session_state.metadata.get("公司", "") or ""
                ).strip()
                if not _metadata_company:
                    _metadata_company = Path(st.session_state.pdf_name).stem
                metadata = sanitize_tree(
                    {
                        **st.session_state.metadata,
                        "公司": _metadata_company,
                        "来源文件": st.session_state.pdf_name,
                        # ★ 兜底：把 first_page_text 显式带过来
                        "first_page_text": str(
                            st.session_state.metadata.get("first_page_text", "")
                        ),
                        "导入批次": (
                            f"{Path(st.session_state.pdf_name).stem}:"
                            f"{st.session_state.metadata.get('报告期', '')}"
                        ),
                    }
                )
                result = standardize_to_target(
                    st.session_state.raw_tables,
                    taxonomy,
                    metadata,
                    company_type,
                    target_catalog,
                    report_profile_id=active_profile.profile_id,
                    allowed_company_types=active_profile.company_types,
                    peer_group_map=peer_group_map,
                    default_peer_group=default_peer_group,
                    company_type_map=company_type_map,
                    include_derived=include_derived,
                    template_warnings=template_warnings,
                )

                status_box.write("正在生成 STEP5 可直接读取的标准窄表工作簿…")
                st.session_state.standard_data = result.data

                # ---------- 自动补注册资本（单份） ----------
                _company_name = ""
                if (
                    not st.session_state.standard_data.empty
                    and "公司" in st.session_state.standard_data.columns
                ):
                    _company_name = str(st.session_state.standard_data["公司"].iloc[0]).strip()
                if not _company_name:
                    _company_name = Path(st.session_state.pdf_name).stem

                _report_period = str(st.session_state.metadata.get("报告期", "")).strip()
                if not _report_period:
                    _report_period = "2025Q4"

                st.session_state.standard_data = append_registered_capital(
                    st.session_state.standard_data,
                    pdf_text=st.session_state.metadata.get("first_page_text", ""),
                    pdf_bytes=st.session_state.pdf_bytes,
                    pdf_name=st.session_state.pdf_name,
                    company_name=_company_name,
                    report_period=_report_period,
                    company_type=company_type,
                )

                # ★ 关键：把更新后的 standard_data 写回 result，再生成 workbook
                # frozen dataclass 不能直接赋值，需要用 dataclasses.replace 生成新对象
                result = dataclasses.replace(
                    result,
                    data=st.session_state.standard_data,
                    target_summary=_target_summary(
                        result.target_catalog,
                        st.session_state.standard_data,
                    ),
                )
                st.session_state.standard_workbook_bytes = result_workbook_bytes(result)
                st.session_state.normalization_diagnostics = result.diagnostics
                st.session_state.standard_target_catalog = result.target_catalog
                st.session_state.standard_target_summary = result.target_summary
                st.session_state.standardization_warnings = list(result.warnings)
                st.session_state.standard_template_label = template_label
                status_box.update(
                    label="目标表标准填报完成",
                    state="complete",
                    expanded=False,
                )
            except Exception as exc:
                status_box.update(
                    label="目标表标准填报失败",
                    state="error",
                    expanded=True,
                )
                st.error(f"标准填报失败：{exc}")
                import traceback

                st.exception(exc)
                st.code(traceback.format_exc())

    if not st.session_state.standard_target_summary.empty:
        summary = st.session_state.standard_target_summary
        filled_targets = int((summary["填报状态"] == "已填报").sum())
        missing_targets = int((summary["填报状态"] == "未填报").sum())
        with st.container(horizontal=True):
            st.metric("目标指标", len(summary), border=True)
            st.metric("已填报指标", filled_targets, border=True)
            st.metric("未填报指标", missing_targets, border=True)
            st.metric("窄表记录", len(st.session_state.standard_data), border=True)
        st.caption(f"本次使用：{st.session_state.standard_template_label}")
        for warning in st.session_state.standardization_warnings:
            st.warning(warning)
        if missing_targets:
            with st.expander("查看未填报目标指标", expanded=True):
                st.dataframe(
                    summary[summary["填报状态"] == "未填报"],
                    width="stretch",
                    hide_index=True,
                )

    if not st.session_state.normalization_diagnostics.empty:
        with st.expander("查看未匹配或歧义诊断"):
            st.dataframe(
                st.session_state.normalization_diagnostics,
                width="stretch",
                hide_index=True,
            )

    if not st.session_state.standard_data.empty:
        derived_count = int(st.session_state.standard_data["指标属性"].isin(["计算", "校验"]).sum())
        converted_rows = int(
            st.session_state.standard_data["备注"]
            .astype(str)
            .str.contains("已换算为", regex=False)
            .sum()
        )
        pending_unit_rows = int(
            st.session_state.standard_data["备注"]
            .astype(str)
            .str.contains("未识别原始单位", regex=False)
            .sum()
        )
        with st.container(horizontal=True):
            st.metric("派生及校验记录", derived_count, border=True)
            st.metric("单位换算记录", converted_rows, border=True)
            st.metric("单位待核对记录", pending_unit_rows, border=True)
        st.markdown("#### 标准窄表预览")
        step3_narrow_display = narrow_table_view(st.session_state.standard_data)
        step3_narrow_display.insert(
            0, "记录号", range(1, len(step3_narrow_display) + 1)
        )
        st.caption("记录号用于与 STEP4 勾稽检查的“STEP3记录号”逐行对应。")
        st.dataframe(step3_narrow_display, width="stretch", hide_index=True)
        st.download_button(
            "下载已填报的标准窄表",
            (
                st.session_state.standard_workbook_bytes
                or standard_workbook_bytes(st.session_state.standard_data)
            ),
            f"{Path(st.session_state.pdf_name).stem}_STEP3标准窄表.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
        )


def _render_batch_prerequisites(is_project_member: bool) -> bool:
    """STEP3 批处理：权限与 AI 配置检查。"""
    _batch_ready = True
    if not is_project_member:
        st.info("批量处理仅对项目组成员开放。", icon=":material/lock:")
        _batch_ready = False
    if not llm_credentials_ready():
        st.warning(
            "当前会话的 AI 配置不完整。请退出登录，在登录页填写 "
            "Base URL、模型名称和 API Key 后重新进入。",
            icon=":material/warning:",
        )
        _batch_ready = False
    return _batch_ready


def _render_batch_upload(active_profile: ReportProfile, ready: bool) -> list:
    """STEP3 批处理：多份 PDF 上传区。"""
    _batch_ready = ready
    # ---------- 上传区 ----------
    uploaded_pdfs = st.file_uploader(
        "一次拖拽或选择多份 PDF（文件名 = 公司名，例如「平安产险.pdf」）",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"batch_pdfs_{active_profile.profile_id}",
        disabled=not _batch_ready,
    )

    if uploaded_pdfs:
        _names = [Path(f.name).stem for f in uploaded_pdfs]
        _dup_names = sorted({n for n in _names if _names.count(n) > 1})
        if _dup_names:
            st.warning(
                "以下公司名重复，请确保文件名唯一：" + "、".join(_dup_names),
                icon=":material/warning:",
            )
        st.success(f"已选择 {len(uploaded_pdfs)} 份 PDF。")
    return uploaded_pdfs


def _render_batch_settings() -> SimpleNamespace:
    """STEP3 批处理：并发数、图片识别与跳过策略设置。"""
    with st.expander("批处理设置", expanded=True):
        _c1, _c2, _c3 = st.columns(3)
        with _c1:
            _batch_workers = st.slider(
                "并发数",
                min_value=1,
                max_value=5,
                value=3,
                key="batch_workers",
                help="越高越快，但可能触发平台限流。",
            )
        with _c2:
            _batch_vision = st.toggle(
                "强制图片识别",
                value=False,
                key="batch_force_vision",
                help="文字层异常时开启。",
            )
        with _c3:
            _batch_only_new = st.toggle(
                "跳过已成功过的公司",
                value=False,
                key="batch_skip_success",
                help="上一次批量结果里状态为「成功」的公司会跳过，只重跑失败/异常的。",
            )
    return SimpleNamespace(
        workers=int(_batch_workers),
        vision=bool(_batch_vision),
        only_new=bool(_batch_only_new),
    )


def _prepare_batch_context(active_profile: ReportProfile, use_vision: bool):
    """构建批处理上下文；失败时提示并中断本次渲染。"""
    with st.status("正在准备批处理上下文…", expanded=False) as status_box:
        try:
            context = build_context_from_profile(
                active_profile,
                api_key=st.session_state.llm_api_key,
                base_url=st.session_state.llm_base_url,
                model=st.session_state.llm_model,
                use_vision=use_vision,
                company_type_map={},
            )
            status_box.update(label="批处理上下文已就绪", state="complete")
        except Exception as exc:
            status_box.update(label="上下文准备失败", state="error")
            st.error(f"上下文准备失败：{exc}")
            st.stop()
    return context


def batch_summary_frame(results: list[dict], *, note_limit: int | None = None) -> pd.DataFrame:
    """把批处理结果整理成汇总表；note_limit 用于限制说明列长度。"""
    rows = []
    for item in results:
        note = str(item.get("说明", ""))
        rows.append(
            {
                "公司": item["公司"],
                "状态": item["状态"],
                "定位表数": item.get("定位表数", 0),
                "提取表数": item.get("提取表数", 0),
                "窄表记录数": item.get("窄表记录数", 0),
                "说明": note[:note_limit] if note_limit else note,
            }
        )
    return pd.DataFrame(rows)


def _render_batch_locate(
    active_profile: ReportProfile,
    uploaded_pdfs: list,
    settings: SimpleNamespace,
    ready: bool,
) -> None:
    """STEP3 批处理阶段 1：批量定位。"""
    _batch_ready = ready
    _batch_workers = settings.workers
    _batch_vision = settings.vision
    st.markdown("### 阶段 1：批量定位")
    st.caption("系统对每份 PDF 只跑页码定位，不调用大模型提取表格，结果供人工核对。")
    _locate_clicked = st.button(
        "🎯 开始批量定位",
        type="primary",
        key="batch_locate",
        disabled=not _batch_ready or not uploaded_pdfs,
        width="stretch",
    )

    if _locate_clicked and uploaded_pdfs:
        _ctx = _prepare_batch_context(active_profile, _batch_vision)

        _locate_progress = st.progress(0, text="正在定位…")
        _locate_results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=_batch_workers) as _pool:
            _futs = {
                _pool.submit(
                    locate_one_pdf_bytes,
                    Path(_f.name).stem,
                    _f.getvalue(),
                    _ctx,
                ): Path(_f.name).stem
                for _f in uploaded_pdfs
            }
            for _i, _fut in enumerate(as_completed(_futs), 1):
                _res = _fut.result()
                _locate_results[_res["公司"]] = _res
                _locate_progress.progress(
                    _i / len(_futs),
                    text=f"已定位 {_i}/{len(_futs)}：{_res['公司']}",
                )
        _locate_progress.empty()

        # ★ 新增：缓存每份 PDF 的原始 bytes，供阶段 2 页面预览使用
        st.session_state["batch_pdf_bytes"] = {
            Path(_f.name).stem: _f.getvalue() for _f in uploaded_pdfs
        }

        st.session_state["batch_located"] = _locate_results
        st.session_state.pop("batch_last_results", None)  # 清上一轮结果
        st.success(f"已完成 {len(_locate_results)} 家公司的定位，请核对页码。")


def _render_batch_page_review() -> None:
    """STEP3 批处理阶段 2：核对并修正页码（含页面预览）。"""
    # ============================================================
    # 阶段 2：页码确认与编辑
    # ============================================================
    _located = st.session_state.get("batch_located")
    _edited_overrides: dict[str, dict[str, list[int]]] = {}
    if _located:
        st.markdown("### 阶段 2：核对并修正页码")
        st.caption(
            "🔴 标红的行表示系统定位存在冲突或不确定，请重点核对。"
            "其他行如无问题，可直接进入下一步。"
        )

        _conflict_count = 0
        _no_match_count = 0
        for _company, _info in _located.items():
            if _info["状态"] != "成功":
                st.error(f"❌ {_company}：定位失败 — {_info['说明']}")
                continue

            _matches = _info.get("matches") or []
            if not _matches:
                st.warning(f"⚠️ {_company}：未定位到任何目标表")
                _no_match_count += 1
                continue

            _edited_overrides[_company] = {}
            with st.expander(f"📄 {_company}（{len(_matches)} 张表）", expanded=False):
                for _m in _matches:
                    _auto_pages = ", ".join(map(str, _m.pages)) if _m.pages else ""
                    _input_key = f"batch_page_{_company}_{_m.table_id}"
                    if _input_key not in st.session_state:
                        st.session_state[_input_key] = _auto_pages

                    _is_conflict = getattr(_m, "review_required", False)
                    if _is_conflict:
                        _conflict_count += 1

                    _col1, _col2 = st.columns([1, 2])
                    with _col1:
                        st.markdown(f"**{_m.table_name}**" + (" 🔴" if _is_conflict else ""))
                        if _is_conflict:
                            st.caption(f"⚠ {_m.review_reason}")
                    with _col2:
                        _raw = st.text_input(
                            "页码（逗号分隔）",
                            key=_input_key,
                            label_visibility="collapsed",
                            placeholder="例如：26,27",
                        )
                        _pages, _invalid = parse_page_numbers(_raw, 9999)
                        if _invalid:
                            st.warning(f"无效页码：{_invalid}")
                        _edited_overrides[_company][_m.table_name] = _pages
                        if _m.pages and _pages != list(_m.pages):
                            st.caption(f"原自动定位：{_auto_pages} → 已修改")

                # ============================================================
                # ★ 新增：页面预览区
                # 用当前输入框里的页码（非原始定位），用户改完立刻能看效果
                # ============================================================
                st.markdown("---")
                st.markdown("**📷 页面预览**")

                _preview_table_names = [m.table_name for m in _matches]
                _preview_table_map = {m.table_name: m for m in _matches}
                _selected_preview_table = st.selectbox(
                    "选择要预览的表",
                    _preview_table_names,
                    key=f"batch_preview_table_{_company}",
                )
                _sel_match = _preview_table_map[_selected_preview_table]

                # 优先用输入框当前值；若为空，回退到原始自动定位
                _current_raw = st.session_state.get(
                    f"batch_page_{_company}_{_sel_match.table_id}", ""
                )
                _current_pages, _ = parse_page_numbers(_current_raw, 9999)
                if not _current_pages and _sel_match.pages:
                    _current_pages = list(_sel_match.pages)

                if not _current_pages:
                    st.info("该表当前无有效页码，请在上方输入。")
                else:
                    if len(_current_pages) > 1:
                        _sel_page = st.radio(
                            "切换页",
                            _current_pages,
                            horizontal=True,
                            key=f"batch_preview_page_{_company}_{_sel_match.table_id}",
                        )
                    else:
                        _sel_page = _current_pages[0]

                    _pdf_bytes_cache = st.session_state.get("batch_pdf_bytes", {})
                    if _company in _pdf_bytes_cache:
                        try:
                            _img = render_pdf_page(_pdf_bytes_cache[_company], _sel_page)
                            st.image(
                                _img,
                                caption=f"{_company} — 第 {_sel_page} 页",
                                width="stretch",
                            )
                        except Exception as _e:
                            st.warning(f"预览失败：{_e}")
                    else:
                        st.warning(
                            "PDF 缓存不可用（可能未完成定位或已清空）。"
                            "请重新点击「🎯 开始批量定位」。"
                        )

        if _conflict_count:
            st.warning(f"⚠️ 共 {_conflict_count} 张表存在定位冲突，建议修正后再提取。")
        elif _no_match_count == 0:
            st.success("✅ 所有目标表定位无冲突，可直接提取。")

        st.session_state["batch_page_overrides"] = _edited_overrides


def _render_batch_extract(
    active_profile: ReportProfile,
    uploaded_pdfs: list,
    settings: SimpleNamespace,
    ready: bool,
) -> None:
    """STEP3 批处理阶段 3：执行提取。"""
    _batch_ready = ready
    _batch_workers = settings.workers
    _batch_vision = settings.vision
    _batch_only_new = settings.only_new
    _located = st.session_state.get("batch_located")
    # ============================================================
    # 阶段 3：执行提取
    # ============================================================
    st.markdown("### 阶段 3：执行提取")
    _extract_clicked = st.button(
        "🚀 确认页码，开始批量提取",
        type="primary",
        key="batch_extract",
        disabled=not _batch_ready or not uploaded_pdfs or not _located,
        width="stretch",
    )

    if _extract_clicked and uploaded_pdfs:
        _prev_results = st.session_state.get("batch_last_results", {})
        _prev_success_names = {n for n, r in _prev_results.items() if r.get("状态") == "成功"}

        _ctx2 = _prepare_batch_context(active_profile, _batch_vision)

        _todo = []
        _skipped = []
        for _f in uploaded_pdfs:
            _name = Path(_f.name).stem
            if _batch_only_new and _name in _prev_success_names:
                _skipped.append(_name)
                continue
            _todo.append((_name, _f.getvalue()))

        if _skipped:
            st.info(f"跳过 {len(_skipped)} 家已成功的公司：{'、'.join(_skipped)}")

        if not _todo:
            st.warning("没有需要处理的文件。")
        else:
            _progress = st.progress(0, text="准备中…")
            _progress_text = st.empty()
            _live_log = st.empty()

            _results: list[dict] = []
            _total = len(_todo)
            _overrides_all = st.session_state.get("batch_page_overrides", {})

            with ThreadPoolExecutor(max_workers=_batch_workers) as _pool:
                _futures = {
                    _pool.submit(
                        process_one_pdf_bytes,
                        _name,
                        _bytes,
                        _ctx2,
                        _overrides_all.get(_name, {}),
                    ): _name
                    for _name, _bytes in _todo
                }
                for _i, _fut in enumerate(as_completed(_futures), 1):
                    _name = _futures[_fut]
                    try:
                        _res = _fut.result()
                    except Exception as _e:
                        _res = {
                            "公司": _name,
                            "状态": "异常",
                            "定位表数": 0,
                            "提取表数": 0,
                            "窄表记录数": 0,
                            "说明": str(_e)[:200],
                            "workbook_bytes": b"",
                        }
                    _results.append(_res)

                    _progress.progress(
                        _i / _total,
                        text=f"已完成 {_i}/{_total}：{_res['公司']} — {_res['状态']}",
                    )
                    _progress_text.text(_res.get("说明", ""))

                    _live_log.dataframe(
                        batch_summary_frame(_results, note_limit=80),
                        width="stretch",
                        hide_index=True,
                    )

            _progress.empty()
            _progress_text.empty()

            st.session_state["batch_last_results"] = {_r["公司"]: _r for _r in _results}
            st.session_state["batch_last_active_profile"] = active_profile.profile_id


def _render_batch_results(active_profile: ReportProfile) -> None:
    """STEP3 批处理：结果汇总、打包下载与清空。"""
    # ============================================================
    # 结果展示与下载（保留原逻辑）
    # ============================================================
    _stored = st.session_state.get("batch_last_results", {})
    _stored_profile = st.session_state.get("batch_last_active_profile", "")
    if _stored and _stored_profile == active_profile.profile_id:
        _res_list = list(_stored.values())
        _ok = sum(1 for r in _res_list if r.get("状态") == "成功")
        _fail = len(_res_list) - _ok

        with st.container(border=True):
            st.markdown("#### 批处理结果")
            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("总数", len(_res_list))
            _m2.metric("✅ 成功", _ok)
            _m3.metric("⚠️ 需复核", _fail)

            _summary_df = batch_summary_frame(_res_list)
            st.dataframe(_summary_df, width="stretch", hide_index=True)

            if _ok > 0:
                _zip_buffer = io.BytesIO()
                with zipfile.ZipFile(_zip_buffer, "w", zipfile.ZIP_DEFLATED) as _zf:
                    for _r in _res_list:
                        if _r.get("workbook_bytes"):
                            _fname = f"{_r['公司']}_STEP3标准窄表.xlsx"
                            _zf.writestr(_fname, _r["workbook_bytes"])

                    _summary_xlsx = dataframe_to_xlsx(_summary_df, "批处理汇总")
                    _zf.writestr("_批处理汇总.xlsx", _summary_xlsx)

                _zip_bytes = _zip_buffer.getvalue()

                _dl1, _dl2 = st.columns(2)
                with _dl1:
                    st.download_button(
                        f"📦 下载全部结果（{_ok} 家，ZIP）",
                        _zip_bytes,
                        file_name=(
                            f"{active_profile.profile_id}_批量提取" f"_{len(_res_list)}家.zip"
                        ),
                        mime="application/zip",
                        type="primary",
                        width="stretch",
                        key="batch_dl_zip",
                    )
                with _dl2:
                    st.download_button(
                        "📊 下载汇总表",
                        dataframe_to_xlsx(_summary_df, "批处理汇总"),
                        file_name=f"{active_profile.profile_id}_批处理汇总.xlsx",
                        mime=(
                            "application/vnd.openxmlformats-officedocument" ".spreadsheetml.sheet"
                        ),
                        width="stretch",
                        key="batch_dl_summary",
                    )

            if _fail > 0:
                with st.expander(f"⚠️ {_fail} 家未成功，点开查看明细", expanded=False):
                    st.dataframe(
                        _summary_df[_summary_df["状态"] != "成功"],
                        width="stretch",
                        hide_index=True,
                    )

            if st.button("🗑️ 清空当前批处理结果", key="batch_clear"):
                st.session_state.pop("batch_last_results", None)
                st.session_state.pop("batch_last_active_profile", None)
                st.session_state.pop("batch_located", None)
                st.session_state.pop("batch_page_overrides", None)
                st.rerun()


def _render_step3_batch_tab(active_profile: ReportProfile, is_project_member: bool) -> None:
    """STEP3：批量处理多家公司。"""
    st.subheader("🗂️ 批量提取多家公司偿付能力数据")
    st.caption(
        "两阶段批处理：先定位预览（可人工修正页码），再执行提取与标准化，"
        "避免因页码定位错误导致整份报告数据缺失。"
    )
    batch_ready = _render_batch_prerequisites(is_project_member)
    st.caption(
        f"当前 Profile：{active_profile.profile_name}（{active_profile.profile_id}）｜"
        f"默认公司类型：{active_profile.company_types[0]}"
    )
    uploaded_pdfs = _render_batch_upload(active_profile, batch_ready)
    settings = _render_batch_settings()
    _render_batch_locate(active_profile, uploaded_pdfs, settings, batch_ready)
    _render_batch_page_review()
    _render_batch_extract(active_profile, uploaded_pdfs, settings, batch_ready)
    _render_batch_results(active_profile)


def render_standardization_tab(
    active_profile: ReportProfile,
    taxonomy: pd.DataFrame,
    is_project_member: bool,
) -> None:
    """STEP3 标准化：单份处理 + 批量处理。"""
    _step3_single_tab, _step3_batch_tab = st.tabs(
        ["📄 单份处理（当前 PDF）", "🗂️ 批量处理（多家公司）"]
    )
    with _step3_single_tab:
        _render_step3_single_tab(active_profile, taxonomy)
    with _step3_batch_tab:
        _render_step3_batch_tab(active_profile, is_project_member)
