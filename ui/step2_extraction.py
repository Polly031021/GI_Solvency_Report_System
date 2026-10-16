"""STEP2 表格智能提取：逐页并发提取、跨页表头对齐、单位换算与结果导出。"""

from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import fitz
import pandas as pd
import streamlit as st
from services.report_profiles import ReportProfile
from services.solvency_ai_table_extractor import extraction_logs_frame
from services.solvency_gold_standard import evaluate_gold_case, find_gold_case
from services.solvency_normalizer import STANDARD_COLUMNS
from services.batch_extraction import (
    FlexibleTable,
    convert_table_rows_to_wan,
    detect_table_unit,
    extract_single_page,
    extract_single_page_vision,
    sanitize_scalar,
)

from .cache import read_gold_manifest, render_pdf_page
from .helpers import dedupe_column_names
from .session import llm_credentials_ready


# ---- 跨页表格合并：表头识别与单位换算 ----
_HEADER_ALIAS = {
    "本期金额": "本季度数",
    "本期数": "本季度数",
    "上期金额": "本年度累计数",
    "上期数": "本年度累计数",
    "本期": "本季度数",
    "上期": "本年度累计数",
    "本年累计": "本年度累计数",
}
_HEADER_HINT_TOKENS = (
    "指标名称", "项目", "行次", "本季度", "上季度",
    "本年累计", "期末", "期初", "下季度", "本期", "上期",
)
_HEADER_HINTS = (
    "指标名称", "项目", "行次", "本季度", "上季度",
    "本年累计", "期末", "期初", "下季度",
    "保费风险", "准备金风险", "本期", "上期",
)
def canonical_header_cell(cell: object) -> str:
    """把表头别名（本期/上期/本年累计等）归一到统一写法。"""
    text = str(cell).strip()
    return _HEADER_ALIAS.get(text, text)
def looks_like_header(row) -> bool:
    """判断某一行是否像表头；纯括号说明行（如“（审计后）”）不算表头。"""
    text = "".join(str(c) for c in row)
    if not text.strip():
        return False
    stripped = re.sub(r"[（(][^）)]*[）)]", "", text).strip()
    if not stripped:
        return False
    return any(token in text for token in _HEADER_HINT_TOKENS)
def header_richness(row: list) -> int:
    """表头丰富度 = 命中关键词数 × 10 + 非空单元格数，越大越像表头。"""
    text = "".join(str(c) for c in row)
    hits = sum(1 for token in _HEADER_HINT_TOKENS if token in text)
    nonempty = sum(1 for cell in row if str(cell).strip())
    return hits * 10 + nonempty
def locate_header_row_index(raw_rows: list[list]) -> int:
    """在合并后的原始行中定位表头行。

    优先取同时含“本季度 + 上季度”或“本季度 + 本年累计”的行；
    组合没找到时取关键词命中最多的行，全部未命中才回退到第 0 行。
    """
    header_index = 0
    best_hits = 0
    for index, row in enumerate(raw_rows[:30]):
        row_text = "".join(str(c) for c in row)
        if "本季度" in row_text and ("上季度" in row_text or "本年累计" in row_text):
            return index
        hits = sum(1 for hint in _HEADER_HINTS if hint in row_text)
        if hits > best_hits:
            best_hits = hits
            header_index = index
    return header_index if best_hits else 0
def normalize_table_unit(
    grid: list[list],
    combined_raw_text: str,
    table_id: str,
    pages: list[int],
) -> tuple[list[list], str]:
    """嗅探表格单位并换算为万元；单位不可识别时按数值量级兜底。"""
    table_unit = detect_table_unit(grid)
    # ★ 仅在原逻辑失败（table_unit 为空）时扩大到全文，避免被其他页的“万元”误命中
    if not table_unit and combined_raw_text:
        raw_lines = [
            line for line in str(combined_raw_text).splitlines() if line.strip()
        ]
        fallback_unit = detect_table_unit([[line] for line in raw_lines])
        if fallback_unit:
            table_unit = fallback_unit
            st.session_state.setdefault("_unit_fallback_events", []).append({
                "table_id": table_id,
                "page": pages,
                "detected": table_unit,
            })

    if table_unit and table_unit != "万元":
        grid = convert_table_rows_to_wan(grid, table_unit)
        if table_unit in {"元", "千元"}:
            table_unit = "万元"
    elif grid and len(grid) > 1:
        value_cols = [
            index
            for index, header in enumerate(grid[0])
            if isinstance(header, str)
            and any(
                key in header
                for key in ["本季度末", "上季度末", "期末", "期初", "本季度数", "本年累计"]
            )
        ]
        for row in range(1, len(grid)):
            for column in value_cols:
                if column < len(grid[row]) and grid[row][column]:
                    try:
                        raw = str(grid[row][column]).replace(",", "")
                        value = float(raw)
                        if abs(value) > 1e12:
                            grid[row][column] = str(value / 10000)
                    except (ValueError, TypeError):
                        pass
    return grid, table_unit
def build_tables_from_page_results(
    results: dict[str, list[tuple]], use_vision: bool
) -> list[FlexibleTable]:
    """把逐页提取结果按目标表拼接，并做表头归一、单位换算。"""
    raw_tables = []
    for table_id, page_dfs in results.items():
        if not page_dfs:
            continue
        # 按页码排序
        page_dfs.sort(key=lambda x: x[0])

        # ============================================================
        # ★ 修复：不再用 pd.concat 依赖列名自动对齐。
        # 原因：同一张表跨页时，AI 可能对每一页返回不同的表头文本
        #      （如第 14 页"本季度数/本年度累计数"、第 15 页"本期金额/上期金额"），
        #      pd.concat 会把列名做并集，导致各页真实数据被错位或丢失。
        # 新策略：按"行"直接拼接，以第一页第一行为统一表头，
        #       后续页的重复表头行自动丢弃，剩余全部保留。
        # ============================================================
        _all_rows: list[list] = []
        for _page, _df, *_ in page_dfs:
            if _df is None or _df.empty:
                continue
            _all_rows.extend(_df.fillna("").astype(str).values.tolist())

        if not _all_rows:
            continue

        # ★ 跨页时挑选"最像表头"的那一行作为统一表头，而不是固定第一页。
        # 原因：财险的"主要经营指标"表跨 14、15 页，14 页表头是
        #      "监测指标 | 本季度 | 上季度"，15 页表头是
        #      "指标名称 | 本季度数 | 本年度累计数"。如果选 14 页表头，
        #      15 页的"本年度累计数"会被误解释为"上季度"。
        _header_candidates: list[tuple[int, list]] = []
        for _i, _r in enumerate(_all_rows[:20]):
            _candidate = [canonical_header_cell(c) for c in _r]
            if looks_like_header(_candidate):
                _header_candidates.append((_i, _candidate))

        if _header_candidates:
            # 选"丰富度最高"的作为统一表头
            _start_idx, _header_row = max(
                _header_candidates, key=lambda item: header_richness(item[1])
            )
        else:
            _start_idx = 0
            _header_row = [canonical_header_cell(c) for c in _all_rows[0]]

        # ---- 拼 body：跳过表头行本身 + 丢弃所有"与主表头完全相同"的后续表头 ----
        _header_row_key = tuple(str(c).strip() for c in _header_row)
        _body_rows: list[list] = []
        for _i, _r in enumerate(_all_rows):
            if _i == _start_idx:
                continue
            _r_key = tuple(str(c).strip() for c in _r)
            if _r_key == _header_row_key:
                # 后续页的重复表头行，直接丢弃
                continue
            # 后续页数据行的列名不作归一化处理（只对表头行归一）
            _body_rows.append([str(c).strip() for c in _r])

        merged_df = pd.DataFrame([_header_row] + _body_rows)

        # ★ 先提取页码信息（供下方单位嗅探使用）
        extracted_pages_int = [item[0] for item in page_dfs]

        # ★ 修复：item[2] 是 AI 提取日志，不是 PDF 原文。
        raw_texts = []
        _pdf_bytes_for_unit = st.session_state.get("pdf_bytes")
        if _pdf_bytes_for_unit and extracted_pages_int:
            try:
                _doc = fitz.open(
                    stream=_pdf_bytes_for_unit,
                    filetype="pdf",
                )
                for _p in extracted_pages_int:
                    if 1 <= _p <= len(_doc):
                        raw_texts.append(
                            _doc.load_page(_p - 1).get_text() or ""
                        )
                _doc.close()
            except Exception:
                raw_texts = []
        combined_raw_text = "\n".join(t for t in raw_texts if t)

        # 查找预期页码（用于缺失提示）
        expected_pages = []
        for match in st.session_state.page_matches:
            if match.table_id == table_id:
                expected_pages = list(match.pages)
                break

        # 如果缺页，添加备注行
        if expected_pages and len(extracted_pages_int) < len(expected_pages):
            missing_pages = [p for p in expected_pages if p not in extracted_pages_int]
            note_row = pd.DataFrame([{
                "注意": f"仅提取了 {len(extracted_pages_int)}/{len(expected_pages)} 页，缺失页码：{missing_pages}"
            }])
            merged_df = pd.concat([merged_df, note_row], ignore_index=True).copy()

        # 查找对应的 table 配置
        table_match = next((m for m in st.session_state.page_matches if m.table_id == table_id), None)

        safe_table_config = (
            {k: sanitize_scalar(v) for k, v in table_match.table_config.items()}
            if table_match and isinstance(table_match.table_config, dict)
            else None
        )
        # ★ 修复：merged_df 的列名是 0,1,2...，第一行才是真表头
        raw_rows = merged_df.fillna("").astype(str).values.tolist()
        if not raw_rows:
            continue

        _header_idx = locate_header_row_index(raw_rows)


        header_row = [str(c).strip() for c in raw_rows[_header_idx]]
        # 表头行之前的内容（说明文字、章节标题）不进入数据区
        data_rows = raw_rows[_header_idx + 1:]
        # 去掉后续页重复的表头行
        data_rows = [row for row in data_rows if row != raw_rows[_header_idx]]

        # ★ 表头去重（空列名补“列N”，重名追加 _2/_3…）
        header_row = dedupe_column_names(header_row)

        # ★ 修复：AI 把"本季末"/"上季末"丢成"（过渡期政策）"时，按列位置回填
        #   适用条件：至少 1 列仍是未知（空或纯括号），且列数 >= 3
        _header_text = "".join(str(c) for c in header_row)
        _has_quarter_hint = any(
            tok in _header_text
            for tok in ("本季度", "本季", "上季度", "上季", "下季度")
        )
        if (
            not _has_quarter_hint
            and len(header_row) >= 3
        ):
            # 只在第 1/2/3 列按经验回填（其他列保持原样）
            _period_by_index = {
                1: "本季度末",
                2: "上季度末",
                3: "下季度预测数",
            }
            for _pos, _fallback in _period_by_index.items():
                if _pos >= len(header_row):
                    break
                _cell = str(header_row[_pos]).strip()
                _stripped = re.sub(
                    r"[（(][^）)]*[）)]", "", _cell
                ).strip()
                if not _stripped:
                    header_row[_pos] = _fallback

        grid = [header_row] + data_rows

        grid_raw = [list(r) for r in grid]  # 留存原始网格备查

        # ---------- 单位嗅探与换算 ----------
        grid, table_unit = normalize_table_unit(
            grid,
            combined_raw_text,
            table_id,
            extracted_pages_int,
        )

        # ---------- 生成 DataFrame 副本（供 to_frame 使用） ----------
        # ★ 列名去重：空列名补“列N”，重复列名追加 _2/_3…
        deduped_columns = dedupe_column_names(grid[0])
        grid[0] = deduped_columns
        df_captured = pd.DataFrame(
            [list(r) for r in grid[1:]],
            columns=deduped_columns,
        )

        # ---------- 创建 FlexibleTable ----------
        table_obj = FlexibleTable(
            table_id=table_id,
            table_name=next((m.table_name for m in st.session_state.page_matches if m.table_id == table_id), table_id),
            page=extracted_pages_int[0] if extracted_pages_int else 1,
            source_pages=list(extracted_pages_int),
            rows=grid,
            to_frame=lambda *args, df=df_captured, **kwargs: df,   # 使用 df_captured
            strategy="并发" + ("视觉" if use_vision else "文本"),
            quality_score=1.0,
            evidence="",
            table_config=safe_table_config,
            unit_records=[],
            unit=table_unit,
            unit_source=table_unit or "未识别",
            source_grid_raw=grid_raw,
        )
        raw_tables.append(table_obj)
    return raw_tables
def _store_extraction_result(
    logs: list[dict], raw_tables: list[FlexibleTable]
) -> None:
    """保存提取结果并生成 STEP2 标准化提取工作簿。"""
    st.session_state.raw_tables = raw_tables
    st.session_state.ai_extraction_logs = logs

    # 生成 Excel 数据（确保每个表独立写入）
    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # 1. 写入提取日志
            log_df = pd.DataFrame(logs)
            if not log_df.empty:
                log_df.to_excel(writer, sheet_name="提取日志", index=False)

            # 2. 写入每个表格（独立 Sheet）
            for table in raw_tables:
                df = table.to_frame()
                if df.empty:
                    continue
                # 清理表名
                sheet_name = re.sub(r'[\\/*?:\[\]]', '', table.table_name)[:31]
                if not sheet_name:
                    sheet_name = "Sheet"
                # 避免重名
                idx = 1
                original_name = sheet_name
                while sheet_name in writer.sheets:
                    sheet_name = f"{original_name[:27]}_{idx}"
                    idx += 1
                df.to_excel(writer, sheet_name=sheet_name, index=False)

            # 3. 如果没有任何表格，写入提示
            if not raw_tables:
                pd.DataFrame({"提示": ["没有提取到任何表格数据"]}).to_excel(
                    writer, sheet_name="提示", index=False
                )

        st.session_state.ai_workbook_bytes = output.getvalue()
    except Exception as e:
        st.warning(f"生成 Excel 文件失败：{e}")
        st.session_state.ai_workbook_bytes = b""
def extraction_log_entry(
    task: dict, use_vision: bool, status: str, note: str
) -> dict:
    """构造一行逐页提取日志。"""
    return {
        "目标表": task["table_name"],
        "物理页码": task["page"],
        "识别方式": "视觉" if use_vision else "文本",
        "状态": status,
        "说明": note,
    }
def _run_extraction(active_profile: ReportProfile, tasks: list[dict]) -> None:
    """STEP2：并发提取各页、合并跨页表格并生成工作簿。"""
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(tasks)
    results = {}  # table_id -> list of DataFrames
    logs = []
    with st.status("正在并发提取各页面...", expanded=True):
        # 根据是否强制图片模式选择引擎
        use_vision = st.session_state.force_vision_mode
        max_workers = 4  # 可调节

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {}
            for task in tasks:
                if use_vision:
                    future = executor.submit(
                        extract_single_page_vision,
                        st.session_state.pdf_bytes,
                        task["page"],
                        task["table_name"],
                        st.session_state.llm_api_key,
                        st.session_state.llm_base_url,
                        st.session_state.llm_model
                    )
                else:
                    future = executor.submit(
                        extract_single_page,
                        st.session_state.pdf_bytes,
                        task["page"],
                        task["table_name"],
                        st.session_state.llm_api_key,
                        st.session_state.llm_base_url,
                        st.session_state.llm_model
                    )
                future_to_task[future] = task

            completed = 0
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                df, log = future.result()
                completed += 1
                progress_bar.progress(completed / total)
                status_text.text(f"已处理 {completed}/{total} 页：{task['table_name']} 第 {task['page']} 页")

                if df is not None and not df.empty:
                    table_id = task["table_id"]
                    if table_id not in results:
                        results[table_id] = []
                    # ★ 把 raw_text 一起存进去，用于单位嗅探
                    results[table_id].append((task["page"], df, log))
                    logs.append(
                        extraction_log_entry(
                            task, use_vision, "成功", f"提取到 {len(df)} 行数据"
                        )
                    )
                else:
                    logs.append(
                        extraction_log_entry(
                            task, use_vision, "失败", log[:200] if log else "无数据返回"
                        )
                    )
        raw_tables = build_tables_from_page_results(results, use_vision)
        _store_extraction_result(logs, raw_tables)
    failed_count = sum(1 for log in logs if log["状态"] == "失败")
    if failed_count:
        st.warning(f"有 {failed_count} 页提取失败，请查看下方日志。")
    else:
        st.success("所有页面提取成功！")
def _render_extraction_launcher(active_profile: ReportProfile) -> None:
    """STEP2：提取按钮、前置校验与任务编排。"""
    if st.button("开始智能提取", type="primary", key="extract_tables"):
        if not llm_credentials_ready():
            st.error(
                "当前登录会话的 AI 配置不完整。请退出登录，在登录页填写 "
                "Base URL、模型名称和 API Key 后重新进入。"
            )
        else:
            # 准备任务列表
            tasks = []
            for match in st.session_state.page_matches:
                for page_num in match.pages:
                    tasks.append({
                        "table_id": match.table_id,
                        "table_name": match.table_name,
                        "page": page_num
                    })

            if not tasks:
                st.warning("没有需要提取的页面，请确认页码配置。")
            else:
                _run_extraction(active_profile, tasks)
def _render_extraction_results() -> None:
    """STEP2：中间表预览、金标准对比与下载。"""
    if st.session_state.raw_tables:
        tables = st.session_state.raw_tables
        gold_case = find_gold_case(
            st.session_state.pdf_bytes,
            read_gold_manifest(),
        )
        if gold_case:
            with st.expander("真实PDF金标准 - 表格提取结果", expanded=True):
                st.dataframe(
                    evaluate_gold_case(
                        gold_case,
                        matches=st.session_state.page_matches,
                        tables=tables,
                    ),
                    width="stretch",
                    hide_index=True,
                )

        def format_reconstructed_table(index: int) -> str:
            item = tables[index]
            pages = item.source_pages or [item.page]
            page_label = "、".join(map(str, pages))
            return f"{item.table_name} - 第{page_label}页 - {item.strategy}"

        selected_table_index = st.selectbox(
            "标准化中间表预览",
            range(len(tables)),
            format_func=format_reconstructed_table,
            key="ai_table_preview",
        )
        selected_table = tables[selected_table_index]
        source_pages = selected_table.source_pages or [selected_table.page]

        metric_cols = st.columns(3)
        metric_cols[0].metric("处理方式", selected_table.strategy)
        metric_cols[1].metric("来源页数", len(source_pages))
        metric_cols[2].metric("质量评分", f"{selected_table.quality_score:.1f}")
        if selected_table.evidence:
            st.caption(f"自动校验依据：{selected_table.evidence}")

        table_col, pdf_col = st.columns([1.15, 1], gap="large")
        with table_col:
            st.markdown("##### 网格化重构结果")
            st.dataframe(selected_table.to_frame(include_unit_footer=True), width="stretch", hide_index=True)
        with pdf_col:
            # 将 source_pages 从字符串还原为整数列表
            source_pages_raw = selected_table.source_pages
            if isinstance(source_pages_raw, str):
                source_pages_list = [int(p.strip()) for p in source_pages_raw.split(',') if p.strip().isdigit()]
            else:
                source_pages_list = source_pages_raw if isinstance(source_pages_raw, list) else [source_pages_raw]

            selected_source_page = st.selectbox(
                "PDF原页核实",
                options=source_pages_list,
                format_func=lambda value: f"第 {value} 页",
                key=f"ai_source_page_{selected_table.table_id}",
            )
            try:
                st.image(
                    render_pdf_page(st.session_state.pdf_bytes, selected_source_page),
                    caption=f"PDF物理第 {selected_source_page} 页",
                    width="stretch",
                )
            except Exception as exc:
                st.warning(f"PDF页面预览失败：{exc}")

        if st.session_state.ai_workbook_bytes:
            st.download_button(
                "下载STEP2标准化提取Excel",
                st.session_state.ai_workbook_bytes,
                "偿付能力报告_STEP2标准化提取.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
def render_extraction_tab(active_profile: ReportProfile) -> None:
    """STEP2 表格智能转换。"""
    st.subheader("表格智能转换")
    st.caption(
        "采用逐页结构化提取：每个物理页独立处理，失败页单独进行图片扫描或网格纠错，"
        "所有页面成功后再按人工确认的页码顺序确定性拼接；任何一页失败都不会输出残缺跨页表。"
    )
    if not st.session_state.page_matches or not st.session_state.pages_confirmed:
        st.info("请先在 STEP1 上传报告、完成页码定位并人工确认物理页码。")
    else:
        _render_extraction_settings()
        _render_extraction_launcher(active_profile)
    if st.session_state.ai_extraction_logs:
        st.markdown("##### 提取与重试日志")
        logs = st.session_state.ai_extraction_logs
        if logs and isinstance(logs[0], dict):
            # 自定义列顺序，与原始日志列名保持一致
            columns = ["目标表", "物理页码", "识别方式", "状态", "说明"]
            df = pd.DataFrame(logs, columns=columns)
        else:
            # 兼容旧格式（对象列表）
            df = extraction_logs_frame(logs)
        st.dataframe(df, width="stretch", hide_index=True)
        _render_extraction_results()
def clear_extraction_results(*, clear_caches: bool = False) -> None:
    """清空提取结果与下游标准化结果；clear_caches 同时重置提取缓存。"""
    values = {
        "raw_tables": [],
        "ai_extraction_logs": [],
        "ai_workbook_bytes": b"",
        "standard_data": pd.DataFrame(columns=STANDARD_COLUMNS),
        "validation_results": pd.DataFrame(),
        "normalization_diagnostics": pd.DataFrame(),
    }
    if clear_caches:
        values.update({
            "table_candidates": {},
            "selected_table_candidates": {},
            "extraction_grid_cache": {},
            "extraction_image_cache": {},
            "extraction_cache_lock": Lock(),
        })
    for key, value in values.items():
        st.session_state[key] = value
def _render_extraction_settings() -> None:
    """STEP2：逐页提取与重试设置。"""
    with st.expander("逐页提取与重试设置", expanded=not bool(st.session_state.raw_tables)):
        configured_model = st.session_state.get("llm_model", "") or "未设置"
        st.caption(f"使用登录页中的 AI 设置，当前模型：{configured_model}")
        option_cols = st.columns(2)
        with option_cols[0]:
            st.toggle(
                "单页提取失败时自动图片扫描重试",
                key="auto_vision_retry",
                help="只重试失败的物理页；模型不支持图片时会改用该页的加强版文本纠错。",
            )
        with option_cols[1]:
            st.toggle(
                "所有页面直接使用图片扫描模式",
                key="force_vision_mode",
                help=(
                    "系统已会自动识别无文字层页面；此开关用于文字层存在但排版异常、"
                    "仍希望所有已选页强制走图片识别的情况。"
                ),
            )
        st.caption("API Key仅保存在当前浏览器会话内，不会写入配置文件或导出的Excel。")
