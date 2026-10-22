"""STEP1 智能页码定位：目标表选择、定位执行、结果核对与 PDF 原页预览。"""

from __future__ import annotations

import fitz
import streamlit as st
from services.report_profiles import ReportProfile
from services.workflow_settings import apply_pending_single_settings
from services.solvency_hybrid_pipeline import locate_tables_hybrid
from services.solvency_gold_standard import evaluate_gold_case, find_gold_case
from services.solvency_pdf_locator import (
    PageMatch,
    extract_report_metadata,
    report_identity_warning,
)

from .cache import cached_analyze_document_type, read_gold_manifest, render_pdf_page
from .helpers import parse_page_numbers
from .session import llm_credentials_ready
from .step2_extraction import clear_extraction_results


def _mark_pages_dirty() -> None:
    """只有用户真正修改页码输入框时，才取消“已确认”状态。"""
    st.session_state.pages_confirmed = False


def _render_locator_review_panel(total_pages: int, gold_case: dict | None) -> None:
    """STEP1：核对自动定位结果，并提供人工修改物理页码的入口。"""
    if st.session_state.auto_page_matches:
        st.markdown("---")
        st.markdown("#### 结果核对")
        st.caption("若表格跨页，请用逗号分隔物理页码，例如：26, 27。未找到时可直接手工输入。")

        edited_pages: dict[str, list[int]] = {}
        updated_matches: list[PageMatch] = []
        conflict_matches = [
            item for item in st.session_state.auto_page_matches if item.review_required
        ]
        if conflict_matches:
            st.warning(
                "以下目标的定位证据存在冲突，已隔离等待人工核对："
                + "、".join(item.table_name for item in conflict_matches)
            )
        for match in st.session_state.auto_page_matches:
            input_key = f"page_edit_{match.table_id}"
            if input_key not in st.session_state:
                saved_pages = st.session_state.edited_pages.get(
                    match.table_id,
                    match.pages,
                )
                st.session_state[input_key] = ", ".join(map(str, saved_pages))
            raw_value = st.text_input(
                match.table_name,
                key=input_key,
                on_change=_mark_pages_dirty,
            )
            valid_pages, invalid_values = parse_page_numbers(raw_value, total_pages)
            edited_pages[match.table_id] = valid_pages
            updated_matches.append(
                PageMatch(
                    table_id=match.table_id,
                    table_name=match.table_name,
                    pages=valid_pages,
                    score=match.score,
                    evidence=match.evidence,
                    review_required=match.review_required,
                    review_reason=match.review_reason,
                    sources=match.sources,
                    strategy_id=match.strategy_id,
                    table_config=match.table_config,
                    confidence=match.confidence,
                    confidence_basis=match.confidence_basis,
                )
            )
            auto_text = ", ".join(map(str, match.pages)) if match.pages else "未自动找到"
            evidence_text = match.evidence or "无明确关键词证据"
            confidence = int(getattr(match, "confidence", 0) or 0)
            confidence_basis = getattr(match, "confidence_basis", "") or "未评估"
            manual_override = bool(valid_pages) and valid_pages != match.pages
            confidence_status = (
                f"置信度 {confidence}%（{confidence_basis}）"
                if confidence
                else "置信度 0%（无自动定位证据，请手动填写）"
            )
            if manual_override:
                confidence_status += "｜已手动修改页码"
            st.caption(f"自动定位：{auto_text}　|　识别依据：{evidence_text}")
            if confidence >= 85:
                st.success(confidence_status)
            elif confidence >= 65:
                st.info(confidence_status)
            elif confidence > 0:
                st.warning(confidence_status + "，建议人工确认。")
            else:
                st.error(confidence_status)
            if match.review_required:
                st.error(f"定位证据冲突：{match.review_reason}")
            if invalid_values:
                st.warning(f"以下页码或内容无效，已忽略：{', '.join(invalid_values)}")

        st.session_state.edited_pages = edited_pages
        st.session_state.page_matches = updated_matches

        if gold_case:
            st.markdown("##### 真实PDF金标准 - 页码定位")
            st.dataframe(
                evaluate_gold_case(
                    gold_case,
                    matches=updated_matches,
                ),
                width="stretch",
                hide_index=True,
            )

        if st.button(
            "确认页码，进入下一步",
            key="confirm_solvency_pages",
            width="stretch",
        ):
            st.session_state.pages_confirmed = True
            valid_count = sum(bool(item.pages) for item in updated_matches)
            conflict_note = "；定位冲突已由人工确认" if conflict_matches else ""
            st.success(
                f"页码已确认：{len(updated_matches)}类目标中有"
                f" {valid_count} 类配置了有效页码{conflict_note}。"
                "请前往 STEP2 提取表格。"
            )


def _render_locator_target_panel(
    active_profile: ReportProfile,
    feature_config: dict,
    table_configs: list[dict],
    table_config_by_name: dict[str, dict],
    incoming: bytes,
    total_pages: int,
    gold_case: dict | None,
) -> None:
    """STEP1：目标表选择、定位方法选择与定位执行。"""
    st.markdown("#### 检索目标设定")
    all_table_names = [item["table_name"] for item in table_configs]
    required_table_names = [
        item["table_name"] for item in table_configs if item.get("required", True)
    ]
    selected_table_names = st.multiselect(
        "请选择需要定位的报表：",
        all_table_names,
        default=required_table_names,
        key="solvency_target_tables",
    )
    selected_configs = [
        table_config_by_name[name] for name in selected_table_names if name in table_config_by_name
    ]

    # --- 文档类型识别与定位方法推荐 ---
    doc_profile = st.session_state.get("document_type_profile")
    if doc_profile is not None:
        method_hint = (
            "规则法（离线，零模型调用）"
            if doc_profile.recommended_method == "RULE"
            else "大模型法（需要API Key）"
        )
        st.info(
            f"**文档类型：{doc_profile.label}**（可检索文本页 "
            f"{doc_profile.text_pages}/{doc_profile.total_pages}）\n\n"
            f"**推荐识别方法：{method_hint}**\n\n"
            f"{doc_profile.notes}"
        )
    else:
        st.caption("未能识别文档类型，请手动选择定位方法。")

    locator_method = st.radio(
        "定位方法",
        [
            "规则法（离线，零模型调用）",
            "混合法：文字页规则法 + 图片页视觉大模型",
            "大模型法（语义+视觉混合）",
        ],
        index=(0 if doc_profile is None or doc_profile.recommended_method == "RULE" else 1),
        key=f"locator_method_{active_profile.profile_id}",
        help=(
            "规则法只使用本地边界分析、表格雷达和目录换算，结果确定可复现；"
            "混合法只把图片页交给视觉大模型；大模型法会对文字页也调用文本模型，"
            "适合版式异常或规则法无法处理的报告。"
        ),
    )
    use_rule_mode = locator_method.startswith("规则法")
    use_rule_vision_mode = locator_method.startswith("混合法")
    use_full_llm_mode = locator_method.startswith("大模型法")
    credentials_ready = llm_credentials_ready()
    llm_verify = False
    if use_rule_mode:
        llm_verify = st.checkbox(
            "规则法完成后用大模型复核",
            value=False,
            key=f"locator_llm_verify_{active_profile.profile_id}",
            disabled=not credentials_ready,
            help=(
                "规则法先独立出结果，再调用一次大模型交叉验证：一致则提升置信度，"
                "不一致则标记人工确认。未配置API Key时不可用。"
            ),
        )
        if not credentials_ready:
            st.caption("当前未配置AI接口：规则法可正常使用，大模型复核不可用。")
    elif use_rule_vision_mode:
        st.info(
            "混合定位会先对可检索文字页执行规则法；只有无文字/低文字页面才调用视觉大模型。"
            "如果当前没有配置 AI 接口，文字页仍可定位，但图片页会记录失败。",
            icon=":material/layers:",
        )

    configured_provider = st.session_state.get("llm_provider", "自定义节点")
    configured_model = st.session_state.get("llm_model", "") or "未配置"
    st.caption(
        f"AI 配置来自登录页：{configured_provider} / {configured_model}。"
        "Step1、Step2 与 Step7 共用该配置；如需更改，请退出后重新登录。"
    )

    method_needs_llm = use_full_llm_mode or llm_verify
    locate_clicked = st.button(
        "启动智能定位",
        type="primary",
        key="locate_tables",
        width="stretch",
    )
    rescan_clicked = st.button(
        "重新扫描",
        key="rescan_locator",
        width="stretch",
        disabled=not st.session_state.auto_page_matches,
        help=(
            "清除当前定位结果并按上方设置重新扫描PDF；"
            "手工修改过的页码会被新结果覆盖，请重新核对。"
        ),
    )
    if rescan_clicked:
        _clear_page_locations()
        _drop_page_edit_widgets(table_configs)

    if locate_clicked or rescan_clicked:
        if not selected_configs:
            st.error("请至少选择一张报表。")
        elif method_needs_llm and not credentials_ready:
            st.error(
                "当前定位方法需要AI配置。请退出登录，在登录页填写 "
                "Base URL、模型名称和 API Key 后重新进入；"
                "或切换为规则法（离线）。"
            )
        else:
            spinner_text = (
                "正在执行规则法定位（本地边界分析+表格雷达+目录换算）..."
                if use_rule_mode
                else "正在检测PDF文字层并执行语义、结构与图片页码定位..."
            )
            if use_rule_mode and llm_verify:
                spinner_text = "规则法定位完成，正在调用大模型复核..."
            elif use_rule_vision_mode:
                spinner_text = "正在执行混合定位：文字页规则法 + 图片页视觉大模型..."
            elif use_full_llm_mode:
                spinner_text = "正在执行大模型定位：文字页语义定位 + 图片页视觉定位..."
            with st.spinner(spinner_text):
                selected_feature_config = {
                    **feature_config,
                    "tables": selected_configs,
                }
                matches, locator_message = locate_tables_hybrid(
                    incoming,
                    selected_feature_config,
                    api_key=st.session_state.llm_api_key,
                    base_url=st.session_state.llm_base_url,
                    model=st.session_state.llm_model,
                    profile_context=active_profile.locator_context(),
                    mode=(
                        "rule"
                        if use_rule_mode
                        else "rule_vision"
                        if use_rule_vision_mode
                        else "hybrid"
                    ),
                    llm_verify=llm_verify,
                )
                st.session_state.auto_page_matches = matches
                st.session_state.page_matches = matches
                st.session_state.pages_confirmed = False
                st.session_state.edited_pages = {
                    item.table_id: list(item.pages) for item in matches
                }
                for item in matches:
                    st.session_state[f"page_edit_{item.table_id}"] = ", ".join(map(str, item.pages))
            located_count = sum(bool(item.pages) for item in matches)
            if located_count:
                st.success(
                    f"定位完成：{located_count}类目标已找到候选页。"
                    "请结合右侧页面预览和置信度提示进行校准。"
                )
            else:
                st.warning("尚未自动找到目标页，请查看定位说明或直接填写物理页码。")
            if "失败" in locator_message:
                st.warning(locator_message)
            else:
                st.info(locator_message)
    _render_locator_review_panel(total_pages, gold_case)


def _render_locator_preview_panel(incoming: bytes, total_pages: int) -> None:
    """STEP1：右侧 PDF 原页预览。"""
    st.markdown("#### 页面预览")
    if st.session_state.auto_page_matches and st.session_state.page_matches:
        preview_matches = st.session_state.page_matches
        preview_ids = [item.table_id for item in preview_matches]
        preview_name_map = {item.table_id: item.table_name for item in preview_matches}
        selected_preview_id = st.selectbox(
            "选择要预览的报表：",
            preview_ids,
            format_func=lambda table_id: preview_name_map[table_id],
            key="solvency_preview_table",
        )
        selected_match = next(
            item for item in preview_matches if item.table_id == selected_preview_id
        )
        pages_to_preview = selected_match.pages

        if not pages_to_preview:
            st.info("尚未配置有效页码，请在左侧输入物理页码。")
        else:
            if len(pages_to_preview) > 1:
                current_page = st.radio(
                    "该报表包含多页，请切换预览：",
                    pages_to_preview,
                    horizontal=True,
                    key=f"preview_page_{selected_preview_id}",
                )
            else:
                current_page = pages_to_preview[0]

            try:
                preview_image = render_pdf_page(incoming, current_page)
                st.image(
                    preview_image,
                    caption=f"当前预览：第 {current_page} 页 / 共 {total_pages} 页",
                    width="stretch",
                )
            except Exception as exc:
                st.warning(f"页面预览失败：{exc}")
    else:
        st.info("启动智能定位后，此处将显示对应PDF页面。")


def _clear_page_locations() -> None:
    """清空页码定位结果（保留已上传的 PDF）。"""
    for key, value in (
        ("auto_page_matches", []),
        ("page_matches", []),
        ("edited_pages", {}),
        ("pages_confirmed", False),
    ):
        st.session_state[key] = value


def _drop_page_edit_widgets(table_configs: list[dict]) -> None:
    """移除逐表页码输入框的会话状态，避免残留上一次的页码。"""
    for item in table_configs:
        st.session_state.pop(f"page_edit_{item['table_id']}", None)


def _render_locator_single_tab(active_profile: ReportProfile) -> None:
    """STEP1 智能页码定位。"""
    st.subheader("📑 智能页码定位")
    st.caption(
        "系统结合页面语义推断与表格结构雷达定位目标表；跨页页码可在左侧人工修改，右侧同步显示PDF原页。"
    )

    feature_config = dict(active_profile.feature_config)
    table_configs = feature_config.get("tables", [])
    config_version = active_profile.runtime_version
    if st.session_state.locator_config_version != config_version:
        st.session_state.locator_config_version = config_version
        _clear_page_locations()
        clear_extraction_results()
        _drop_page_edit_widgets(table_configs)
    table_config_by_name = {item["table_name"]: item for item in table_configs}

    uploaded_pdf = st.file_uploader(
        f"拖拽或选择一份{active_profile.profile_name} PDF",
        type=["pdf"],
        key=f"report_pdf_{active_profile.profile_id}",
    )

    if uploaded_pdf is None:
        st.info("上传PDF并启动智能定位后，此处将显示可编辑页码和对应页面预览。")
    else:
        incoming = uploaded_pdf.getvalue()
        if incoming != st.session_state.pdf_bytes:
            st.session_state.pdf_bytes = incoming
            st.session_state.pdf_name = uploaded_pdf.name
            st.session_state.metadata = extract_report_metadata(incoming)
            try:
                st.session_state.document_type_profile = cached_analyze_document_type(incoming)
            except Exception:
                st.session_state.document_type_profile = None
            # --- 提取整份 PDF 文本，用于抓取注册资本等公司信息 ---
            # ★ 双栏 PDF 的文本层会把标签和值分开排，注册资本常出现在第 3 页，
            #   为兼容各种排版，这里一次性读全文档文本；对几十页的 PDF 也就
            #   几百 KB，本地内存无压力。
            try:
                doc = fitz.open(stream=incoming, filetype="pdf")
                st.session_state.metadata["first_page_text"] = "\n".join(
                    (doc.load_page(_i).get_text() or "") for _i in range(len(doc))
                )
                doc.close()
            except Exception:
                st.session_state.metadata["first_page_text"] = ""
            # ----------------------------------------------------------
            _clear_page_locations()
            clear_extraction_results(clear_caches=True)
            _drop_page_edit_widgets(table_configs)
            if apply_pending_single_settings(
                st.session_state,
                pdf_name=uploaded_pdf.name,
                pdf_bytes=incoming,
            ):
                st.success("已从工作流配置文件恢复该 PDF 的页码。")
                for item in st.session_state.page_matches:
                    st.session_state[f"page_edit_{item.table_id}"] = ", ".join(
                        map(str, item.pages)
                    )

        total_pages = int(st.session_state.metadata.get("页数", 0) or 0)
        st.caption(f"当前文件：{uploaded_pdf.name}　|　文档共 {total_pages} 页")
        identity_warning = report_identity_warning(
            uploaded_pdf.name,
            st.session_state.metadata,
        )
        if identity_warning:
            st.warning(identity_warning, icon=":material/warning:")
        gold_case = find_gold_case(incoming, read_gold_manifest())
        if gold_case:
            st.success(
                f"已匹配真实PDF金标准：{gold_case['case_id']}。"
                "定位和提取结果会自动显示基准对比。"
            )
        with st.expander("查看自动识别的报告基本信息"):
            st.json(st.session_state.metadata)
        col_left, col_spacer, col_right = st.columns([1, 0.05, 1.2])
        with col_left:
            _render_locator_target_panel(
                active_profile,
                feature_config,
                table_configs,
                table_config_by_name,
                incoming,
                total_pages,
                gold_case,
            )
        with col_right:
            _render_locator_preview_panel(incoming, total_pages)


def _render_locator_batch_tab(active_profile: ReportProfile) -> None:
    """STEP1 batch: one uploader, per-company page review and PDF switching."""
    import dataclasses
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from services.batch_processor import (
        build_context_from_profile,
        locate_one_pdf_bytes,
    )
    from services.batch_workflow import (
        document_pdf_bytes,
        ensure_document_metadata,
        reset_document_after_pages,
    )

    from .batch_workspace import (
        batch_documents,
        failed_batch_companies,
        render_batch_status,
        render_batch_uploader,
        replace_document,
        selected_batch_document,
    )

    st.markdown("#### 多文件页码定位")
    st.caption(
        "一次上传多家公司 PDF。定位后在“当前核对文件”中切换公司，"
        "分别修改页码并预览原页；切换文件不会丢失已保存的页码。"
    )
    documents = render_batch_uploader(active_profile)
    if not documents:
        return

    feature_config = dict(active_profile.feature_config)
    table_configs = feature_config.get("tables", [])
    table_config_by_name = {
        item["table_name"]: item for item in table_configs
    }
    selected_names = st.multiselect(
        "批量定位的报表范围",
        list(table_config_by_name),
        default=list(table_config_by_name),
        key=f"batch_locate_targets_{active_profile.profile_id}",
    )
    selected_configs = [
        table_config_by_name[name]
        for name in selected_names
        if name in table_config_by_name
    ]
    locate_mode = st.radio(
        "批量定位方法",
        [
            "规则法（离线，零模型调用）",
            "混合法：文字页规则法 + 图片页视觉大模型",
            "大模型法（语义+视觉混合）",
        ],
        horizontal=True,
        index=0,
        key=f"batch_locate_mode_{active_profile.profile_id}",
        help=(
            "规则法完全离线；混合法只对图片/低文字页调用视觉模型；"
            "大模型法会让文字页也调用文本模型。"
        ),
    )
    use_rule_mode = locate_mode.startswith("规则法")
    use_rule_vision_mode = locate_mode.startswith("混合法")
    use_full_llm_mode = locate_mode.startswith("大模型法")
    credentials_ready = llm_credentials_ready()
    llm_verify = False
    if use_rule_mode:
        llm_verify = st.checkbox(
            "规则法完成后用大模型复核（会增加 token 消耗）",
            value=False,
            key=f"batch_locate_verify_{active_profile.profile_id}",
            disabled=not credentials_ready,
        )
    locate_logic = (
        "规则法" + (" + 大模型复核" if llm_verify else "（零模型调用）")
        if use_rule_mode
        else "文字页规则法 + 图片页视觉大模型"
        if use_rule_vision_mode
        else "大模型语义 + 视觉混合"
    )
    st.info(
        f"当前批量定位逻辑：**{locate_logic}**。"
        f"共 {len(documents)} 份 PDF，系统会按当前页签独立处理，"
        "未激活的 STEP 不再重复读取整个文件池。"
    )
    locate_workers = st.slider(
        "定位并发数",
        min_value=1,
        max_value=8,
        key="batch_locate_workers",
        help="建议 2；规则法定位受 CPU 和磁盘读取限制，过高并发可能反而更慢。",
    )

    def run_locate(target_documents: list[tuple[str, dict]]) -> None:
        nonlocal documents
        if use_rule_vision_mode and not credentials_ready:
            st.warning(
                "混合法会保留文字页规则定位；图片页因未配置视觉模型将无法识别。"
            )
        if use_full_llm_mode and not credentials_ready:
            st.error("大模型定位需要 AI 配置；批量模式建议直接使用规则法。")
            return

        context = build_context_from_profile(
            active_profile,
            api_key=st.session_state.llm_api_key,
            base_url=st.session_state.llm_base_url,
            model=st.session_state.llm_model,
            use_vision=False,
        )
        context = dataclasses.replace(
            context,
            feature_config={
                **context.feature_config,
                "tables": selected_configs,
            },
        )
        progress = st.progress(0, text="正在批量定位…")
        updated_documents = dict(documents)

        def locate_document(company: str, document: dict):
            pdf_bytes = document_pdf_bytes(document)
            result = locate_one_pdf_bytes(
                company,
                pdf_bytes,
                context,
                mode=(
                    "rule"
                    if use_rule_mode
                    else "rule_vision"
                    if use_rule_vision_mode
                    else "hybrid"
                ),
                llm_verify=llm_verify,
            )
            return company, document, pdf_bytes, result

        with ThreadPoolExecutor(max_workers=int(locate_workers)) as pool:
            futures = {
                pool.submit(locate_document, company, document): company
                for company, document in target_documents
            }
            for index, future in enumerate(as_completed(futures), 1):
                company = futures[future]
                try:
                    company, document, pdf_bytes, result = future.result()
                except Exception as exc:
                    result = {
                        "状态": "异常",
                        "说明": str(exc)[:300],
                        "matches": [],
                        "metadata": {},
                    }
                    document = documents[company]
                    pdf_bytes = b""
                updated = reset_document_after_pages(document)
                updated.update(
                    {
                        "pdf_bytes": pdf_bytes,
                        "metadata": result.get("metadata")
                        or document.get("metadata", {}),
                        "metadata_loaded": bool(result.get("metadata")),
                        "matches": result.get("matches") or [],
                        "edited_pages": {
                            match.table_id: list(match.pages)
                            for match in (result.get("matches") or [])
                        },
                        "locate_status": result.get("状态", ""),
                        "locate_note": result.get("说明", ""),
                        "locate_method": locate_logic,
                    }
                )
                updated_documents[company] = updated
                progress.progress(
                    index / max(len(target_documents), 1),
                    text=(
                        f"已定位 {index}/{len(target_documents)}：{company}"
                        f"｜{result.get('状态', '')}｜{locate_logic}"
                    ),
                )
        st.session_state.batch_documents = updated_documents
        no_page_names = [
            company
            for company, document in updated_documents.items()
            if document.get("locate_status")
            and not any(
                bool(getattr(match, "pages", []))
                for match in (document.get("matches") or [])
            )
        ]
        st.session_state.batch_last_operation = (
            f"批量定位完成：{len(target_documents)} 份 PDF"
        )
        if no_page_names:
            st.error(
                "未提取到任何目标表页码（"
                + f"{len(no_page_names)} 家）："
                + "、".join(no_page_names)
            )
        else:
            st.success(
                f"批量定位完成：{len(target_documents)} 份 PDF 均取得候选页码。"
            )
        documents = batch_documents(active_profile)

    if st.button(
        "开始批量定位",
        type="primary",
        key="step1_batch_locate_run",
        disabled=not selected_configs,
    ):
        run_locate(list(documents.items()))

    render_batch_status(active_profile, detail_key="locator")
    no_page_names = [
        company
        for company, document in documents.items()
        if document.get("locate_status")
        and not any(
            bool(getattr(match, "pages", []))
            for match in (document.get("matches") or [])
        )
    ]
    failed_locate = list(
        dict.fromkeys([*failed_batch_companies(documents, "locate"), *no_page_names])
    )
    retry_locate = st.multiselect(
        "指定公司重新定位（提取页码）",
        list(documents),
        default=[name for name in failed_locate if name in documents],
        key=f"batch_relocate_companies_{active_profile.profile_id}",
        help="只重跑选中公司的页码定位，不会清除其他公司的结果。",
    )
    if st.button(
        f"重新定位选中公司（{len(retry_locate)} 家）",
        key="batch_relocate_selected",
        disabled=not retry_locate or not selected_configs,
    ):
        run_locate(
            [
                (company, documents[company])
                for company in retry_locate
                if company in documents
            ]
        )

    current = selected_batch_document(
        active_profile,
        documents,
        key_prefix="locator",
    )
    if current is None:
        return
    ensure_document_metadata(current)
    matches = current.get("matches") or []
    if not matches:
        if current.get("locate_status"):
            st.warning(
                f"{current['company']} 定位状态：{current['locate_status']}。"
                f"{current.get('locate_note', '')}"
            )
        return

    col_review, col_preview = st.columns([1, 1.15], gap="large")
    edited_pages: dict[str, list[int]] = {}
    updated_matches: list[PageMatch] = []
    changed = False
    with col_review:
        st.markdown(f"##### 页码核对：{current['company']}")
        st.caption(
            f"{current.get('pdf_name', '')}　|　"
            f"共 {current.get('metadata', {}).get('页数', '')} 页"
        )
        for match in matches:
            input_key = (
                f"batch_page_{active_profile.profile_id}_"
                f"{current['company']}_{match.table_id}"
            )
            saved_pages = current.get("edited_pages", {}).get(
                match.table_id,
                match.pages,
            )
            if input_key not in st.session_state:
                st.session_state[input_key] = ", ".join(map(str, saved_pages))
            raw = st.text_input(
                match.table_name,
                key=input_key,
                help="跨页表格用逗号分隔物理页码，例如 26, 27。",
            )
            pages, invalid = parse_page_numbers(
                raw,
                int(current.get("metadata", {}).get("页数", 0) or 9999),
            )
            if invalid:
                st.warning(f"无效页码已忽略：{', '.join(invalid)}")
            edited_pages[match.table_id] = pages
            changed = changed or pages != list(match.pages)
            evidence = getattr(match, "evidence", "") or "无明确关键词证据"
            st.caption(
                f"自动定位：{', '.join(map(str, match.pages)) or '未找到'}"
                f"　|　依据：{evidence}"
            )
            updated_matches.append(
                PageMatch(
                    table_id=match.table_id,
                    table_name=match.table_name,
                    pages=pages,
                    score=match.score,
                    evidence=match.evidence,
                    review_required=match.review_required,
                    review_reason=match.review_reason,
                    sources=match.sources,
                    strategy_id=match.strategy_id,
                    table_config=match.table_config,
                    confidence=match.confidence,
                    confidence_basis=(
                        "人工确认页码"
                        if pages != list(match.pages)
                        else match.confidence_basis
                    ),
                )
            )

        updated = dict(current)
        updated["matches"] = updated_matches
        updated["edited_pages"] = edited_pages
        if changed:
            updated["pages_confirmed"] = False
            updated = reset_document_after_pages(updated)
            updated["matches"] = updated_matches
            updated["edited_pages"] = edited_pages
        replace_document(active_profile, current["company"], updated)
        current = updated

        save_col, confirm_col = st.columns(2)
        with save_col:
            if st.button(
                "保存当前 PDF 页码",
                key=f"batch_save_pages_{active_profile.profile_id}_{current['company']}",
                width="stretch",
            ):
                st.success(f"{current['company']} 页码已保存。")
        with confirm_col:
            if st.button(
                "确认全部 PDF 页码",
                type="primary",
                key=f"batch_confirm_all_pages_{active_profile.profile_id}",
                width="stretch",
            ):
                all_documents = batch_documents(active_profile)
                for company, document in all_documents.items():
                    if document.get("matches"):
                        document["pages_confirmed"] = True
                st.session_state.batch_documents = all_documents
                st.success("全部已定位 PDF 的页码已确认，可前往 STEP2 批量提取。")

    with col_preview:
        st.markdown("##### PDF 原页预览")
        table_names = [match.table_name for match in updated_matches]
        selected_name = st.selectbox(
            "预览目标表",
            table_names,
            key=(
                f"batch_preview_table_{active_profile.profile_id}_"
                f"{current['company']}"
            ),
        )
        selected_match = next(
            match
            for match in updated_matches
            if match.table_name == selected_name
        )
        if not selected_match.pages:
            st.info("该目标表尚未填写有效页码。")
        else:
            preview_page = (
                st.radio(
                    "切换页",
                    selected_match.pages,
                    horizontal=True,
                    key=(
                        f"batch_preview_page_{active_profile.profile_id}_"
                        f"{current['company']}_{selected_match.table_id}"
                    ),
                )
                if len(selected_match.pages) > 1
                else selected_match.pages[0]
            )
            try:
                st.image(
                    render_pdf_page(
                        current["pdf_bytes"],
                        int(preview_page),
                    ),
                    caption=f"{current['company']} — 第 {preview_page} 页",
                    width="stretch",
                )
            except Exception as exc:
                st.warning(f"页面预览失败：{exc}")


def render_locator_tab(active_profile: ReportProfile) -> None:
    """STEP1: single-file and multi-file page locating."""
    single_tab, batch_tab = st.tabs(
        ["📄 单份处理", "🗂️ 批量处理（多公司）"]
    )
    with single_tab:
        _render_locator_single_tab(active_profile)
    with batch_tab:
        _render_locator_batch_tab(active_profile)
