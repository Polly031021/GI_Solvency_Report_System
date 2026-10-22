"""STEP2 表格智能提取：逐页并发提取、跨页表头对齐、单位换算与结果导出。"""

from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from types import SimpleNamespace

import fitz
import pandas as pd
import streamlit as st
from services.report_profiles import ReportProfile
from services.solvency_ai_table_extractor import PageGrid, build_pdf_grids, extraction_logs_frame
from services.solvency_gold_standard import evaluate_gold_case, find_gold_case
from services.solvency_normalizer import STANDARD_COLUMNS
from services.batch_extraction import (
    FlexibleTable,
    detect_table_unit,
    sanitize_scalar,
)
from services.solvency_rule_table_extractor import (
    DEFAULT_METHOD,
    ENGINE_LLM_TEXT,
    ENGINE_LLM_VISION,
    ENGINE_LABELS,
    ENGINE_RULE,
    METHOD_OPTIONS,
    MODE_AUTO,
    MODE_LLM,
    MODE_RULE,
    MODE_VISION,
    extract_page_with_method,
    page_has_searchable_text,
    preview_page_engine,
)
from services.batch_workflow import document_pdf_bytes, extract_document_tables
from services.solvency_table_extractor import TABLE_SIGNATURES

from .batch_workspace import (
    batch_documents,
    failed_batch_companies,
    render_batch_status,
    replace_document,
    selected_batch_document,
)
from .cache import read_gold_manifest, render_pdf_page
from .helpers import dedupe_column_names
from .session import llm_credentials_ready


# 逐页并发提取的线程数：规则法为纯本地解析，大模型法受平台限流约束。
EXTRACTION_MAX_WORKERS = 4


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
    "指标名称",
    "项目",
    "行次",
    "本季度",
    "上季度",
    "本年累计",
    "期末",
    "期初",
    "下季度",
    "本期",
    "上期",
)
_HEADER_HINTS = (
    "指标名称",
    "项目",
    "行次",
    "本季度",
    "上季度",
    "本年累计",
    "期末",
    "期初",
    "下季度",
    "保费风险",
    "准备金风险",
    "本期",
    "上期",
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
    """嗅探并记录表格单位；数值保持原文不变，单位换算统一交给 STEP3。

    STEP2 只忠实还原原文数值与单位：8,393,334,151.18 元就保留为
    8,393,334,151.18（元），不再除以 10000。否则会把千分位丢光、小数点
    左移四位，出现 839333.415118 这种看起来像识别错误的数字。
    """
    table_unit = detect_table_unit(grid)
    # ★ 仅在原逻辑失败（table_unit 为空）时扩大到全文，避免被其他页的“万元”误命中
    if not table_unit and combined_raw_text:
        raw_lines = [line for line in str(combined_raw_text).splitlines() if line.strip()]
        fallback_unit = detect_table_unit([[line] for line in raw_lines])
        if fallback_unit:
            table_unit = fallback_unit
            st.session_state.setdefault("_unit_fallback_events", []).append(
                {
                    "table_id": table_id,
                    "page": pages,
                    "detected": table_unit,
                }
            )
    return grid, table_unit


def _page_table_key_parts(table_id: str) -> tuple[str, int | None]:
    """整页全表结果键 ``BASE__PAGE_3__ALL0`` → ``(BASE, 0)``。"""
    match = re.match(
        r"^(?P<base>.+?)__PAGE_(?P<page>\d+)__ALL(?P<index>\d+)$",
        table_id,
    )
    if match is None:
        return table_id, None
    return match.group("base"), int(match.group("index"))


def _target_frame_terms(table_match, base_table_id: str) -> tuple[str, ...]:
    """目标表用于挑选页内表块的关键词；无配置时回退内置签名。"""
    config = (
        dict(table_match.table_config)
        if table_match is not None and isinstance(table_match.table_config, dict)
        else {}
    )
    terms: list[str] = []
    for key in (
        "title_terms",
        "content_terms",
        "completeness_terms",
    ):
        value = config.get(key) or ()
        if isinstance(value, str):
            terms.extend(
                item.strip()
                for item in re.split(r"[|\n\r]+", value)
                if item.strip()
            )
        else:
            terms.extend(str(item).strip() for item in value if str(item).strip())
    if table_match is not None:
        terms.append(str(getattr(table_match, "table_name", "") or "").strip())
    terms.extend(TABLE_SIGNATURES.get(base_table_id, ()))
    return tuple(dict.fromkeys(term for term in terms if term))


def _frame_matches_target(rows: list[list[str]], terms: tuple[str, ...]) -> bool:
    if not terms:
        return True
    flat = re.sub(r"\s+", "", "".join(str(cell) for row in rows for cell in row))
    return any(term.replace(" ", "") in flat for term in terms)


def _frame_contains_all_terms(
    rows: list[list[str]],
    terms: tuple[str, ...],
) -> bool:
    if not terms:
        return False
    flat = re.sub(r"\s+", "", "".join(str(cell) for row in rows for cell in row))
    return all(term.replace(" ", "") in flat for term in terms)


def build_tables_from_page_results(
    results: dict[str, list[tuple]],
    mode_by_page: dict[int, str] | None = None,
    *,
    whole_page: bool = False,
) -> list[FlexibleTable]:
    """把逐页提取结果按目标表拼接，并做表头归一、单位换算。

    ``mode_by_page`` 记录每个物理页实际使用的引擎，仅用于中间表说明。
    """
    mode_by_page = dict(mode_by_page or {})
    raw_tables = []
    for table_id, page_dfs in results.items():
        if not page_dfs:
            continue
        base_table_id, _ = _page_table_key_parts(table_id)
        table_match = next(
            (
                m
                for m in st.session_state.page_matches
                if m.table_id == base_table_id
            ),
            None,
        )
        target_terms = _target_frame_terms(table_match, base_table_id)
        table_config = (
            dict(table_match.table_config)
            if table_match is not None and isinstance(table_match.table_config, dict)
            else {}
        )
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
        table_blocks: list[dict] = []
        for _page, _payload, *_ in page_dfs:
            if _payload is None:
                continue
            _frames = (
                list(_payload)
                if isinstance(_payload, (list, tuple))
                else [_payload]
            )
            _frame_rows: list[list[list[str]]] = []
            for _frame in _frames:
                if _frame is None or getattr(_frame, "empty", True):
                    continue
                _rows = [
                    [str(cell).strip() for cell in row]
                    for row in _frame.fillna("").astype(str).values.tolist()
                ]
                if not _rows:
                    continue
                _frame_rows.append(_rows)
            # 整页全表模式会返回本页所有表块；每个目标表只挑与自身
            # 关键词/内置签名匹配的块。内置签名要求全部命中，优先于
            # 泛化的内容词，避免“实际资本”等词把相邻表也吸进来。
            _signature_terms = TABLE_SIGNATURES.get(base_table_id, ())
            _strong_frames = [
                rows for rows in _frame_rows
                if _frame_contains_all_terms(rows, _signature_terms)
            ]
            if _strong_frames:
                _page_frames = _strong_frames
            else:
                _matched_frames = [
                    rows for rows in _frame_rows
                    if _frame_matches_target(rows, target_terms)
                ]
                _page_frames = _matched_frames or _frame_rows
            # 无论是否开启“整页全表”，都为每个页内帧保留独立表块；
            # STEP3 只消费 table_blocks，不再依赖合并表的全局表头。
            table_blocks.extend(
                {"page": int(_page), "rows": rows}
                for rows in _page_frames
            )
            if not _page_frames:
                continue
            # 一页多表时仍合成同一张中间表展示，只在表与表之间插入
            # 一行“整页第N张表”分隔，避免结果列表被拆得过碎。
            _page_width = max(
                (len(row) for rows in _page_frames for row in rows),
                default=0,
            )
            for _frame_index, _rows in enumerate(_page_frames):
                if _frame_index:
                    _all_rows.append(
                        [f"【整页第{_frame_index + 1}张表】"]
                        + [""] * max(0, _page_width - 1)
                    )
                _all_rows.extend(_rows)

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

        _canonical_headers = [
            str(item).strip()
            for item in (table_config.get("canonical_headers") or ())
            if str(item).strip()
        ]
        if _header_candidates:
            # 选"丰富度最高"的作为统一表头
            _start_idx, _header_row = max(
                _header_candidates, key=lambda item: header_richness(item[1])
            )
        elif _canonical_headers:
            # Some non-life reports put the section title at the top of the
            # extracted grid and omit the visible header.  Do not promote that
            # title to the header: use the profile's canonical header and keep
            # every extracted row as data.
            _width = max((len(row) for row in _all_rows), default=0)
            _header_row = [
                canonical_header_cell(
                    _canonical_headers[index]
                    if index < len(_canonical_headers)
                    else f"列{index + 1}"
                )
                for index in range(_width)
            ]
            _start_idx = -1
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
                        raw_texts.append(_doc.load_page(_p - 1).get_text() or "")
                _doc.close()
            except Exception:
                raw_texts = []
        combined_raw_text = "\n".join(t for t in raw_texts if t)

        # 查找预期页码（用于缺失提示）
        expected_pages = []
        for match in st.session_state.page_matches:
            if match.table_id == base_table_id:
                expected_pages = list(match.pages)
                break

        # 如果缺页，添加备注行
        if expected_pages and len(extracted_pages_int) < len(expected_pages):
            missing_pages = [p for p in expected_pages if p not in extracted_pages_int]
            note_row = pd.DataFrame(
                [
                    {
                        "注意": f"仅提取了 {len(extracted_pages_int)}/{len(expected_pages)} 页，缺失页码：{missing_pages}"
                    }
                ]
            )
            merged_df = pd.concat([merged_df, note_row], ignore_index=True).copy()

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
        data_rows = raw_rows[_header_idx + 1 :]
        # 去掉后续页重复的表头行
        data_rows = [row for row in data_rows if row != raw_rows[_header_idx]]

        # ★ 表头去重（空列名补“列N”，重名追加 _2/_3…）
        header_row = dedupe_column_names(header_row)

        # ★ 修复：AI 把"本季末"/"上季末"丢成"（过渡期政策）"时，按列位置回填
        #   适用条件：至少 1 列仍是未知（空或纯括号），且列数 >= 3
        _header_text = "".join(str(c) for c in header_row)
        _has_quarter_hint = any(
            tok in _header_text for tok in ("本季度", "本季", "上季度", "上季", "下季度")
        )
        if not _has_quarter_hint and len(header_row) >= 3:
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
                _stripped = re.sub(r"[（(][^）)]*[）)]", "", _cell).strip()
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
        # ★ 中间表说明：如实标注本表各页用到的引擎（规则法/大模型文本/大模型视觉）
        table_modes = [
            mode_by_page.get(page, "")
            for page in extracted_pages_int
            if mode_by_page.get(page)
        ]
        unique_modes = tuple(dict.fromkeys(table_modes))
        if not unique_modes:
            strategy_label = "并发提取"
        elif len(unique_modes) == 1:
            strategy_label = f"并发·{unique_modes[0]}"
        else:
            strategy_label = "并发·" + "+".join(unique_modes)

        def captured_frame(
            *_args,
            _df=df_captured,
            _unit=table_unit,
            **_kwargs,
        ) -> pd.DataFrame:
            """STEP2 快照：可选附带单位备注行，避免数值换了单位却无标注。"""
            if not _kwargs.get("include_unit_footer") or not _unit or _unit == "未识别":
                return _df
            width = _df.shape[1]
            footer = ["【单位备注】", f"单位：{_unit}"] + [""] * max(0, width - 2)
            footer_frame = pd.DataFrame([footer[:width]], columns=_df.columns)
            return pd.concat([_df, footer_frame], ignore_index=True)

        table_obj = FlexibleTable(
            table_id=table_id,
            table_name=next(
                (
                    m.table_name
                    for m in st.session_state.page_matches
                    if m.table_id == base_table_id
                ),
                base_table_id,
            ),
            page=extracted_pages_int[0] if extracted_pages_int else 1,
            source_pages=list(extracted_pages_int),
            rows=grid,
            to_frame=captured_frame,
            strategy=strategy_label,
            quality_score=1.0,
            evidence="",
            table_config=safe_table_config,
            unit_records=[],
            unit=table_unit,
            unit_source=table_unit or "未识别",
            source_grid_raw=grid_raw,
            table_blocks=table_blocks,
        )
        raw_tables.append(table_obj)
    return raw_tables


def _store_extraction_result(logs: list[dict], raw_tables: list[FlexibleTable]) -> None:
    """保存提取结果并生成 STEP2 标准化提取工作簿。"""
    st.session_state.raw_tables = raw_tables
    st.session_state.ai_extraction_logs = logs

    # 生成 Excel 数据（确保每个表独立写入）
    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            # 1. 写入提取日志
            log_df = pd.DataFrame(logs)
            if not log_df.empty:
                log_df.to_excel(writer, sheet_name="提取日志", index=False)

            # 2. 写入每个表格（独立 Sheet）
            for table in raw_tables:
                df = table.to_frame(include_unit_footer=True)
                if df.empty:
                    continue
                # 清理表名
                sheet_name = re.sub(r"[\\/*?:\[\]]", "", table.table_name)[:31]
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


def extraction_log_entry(task: dict, mode_label: str, status: str, note: str) -> dict:
    """构造一行逐页提取日志。"""
    return {
        "目标表": task["table_name"],
        "物理页码": task["page"],
        "识别方式": mode_label,
        "状态": status,
        "说明": note,
    }


def _cached_page_grids(pages: list[int]) -> dict[int, PageGrid]:
    """按页取文字网格（带会话缓存）；网格既是规则法的质量门禁依据，
    也是判断该页能否走规则法的信号。"""
    cache: dict = st.session_state.extraction_grid_cache
    lock: Lock = st.session_state.extraction_cache_lock
    ordered = sorted({int(page) for page in pages})
    with lock:
        missing = [page for page in ordered if page not in cache]
        if missing:
            try:
                for grid in build_pdf_grids(st.session_state.pdf_bytes, missing):
                    cache.setdefault(grid.page_number, grid)
            except Exception:
                pass
        return {page: cache[page] for page in ordered if page in cache}


def _extraction_tasks(matches: list | None = None) -> list[dict]:
    """把已确认页码展开成逐页任务；任务携带表的策略与配置供质量门禁使用。"""
    tasks: list[dict] = []
    for match in matches if matches is not None else st.session_state.page_matches:
        table_config = match.table_config if isinstance(match.table_config, dict) else {}
        for page_number in sorted({int(page) for page in match.pages}):
            tasks.append(
                {
                    "table_id": match.table_id,
                    "table_name": match.table_name,
                    "page": page_number,
                    "table_config": table_config,
                    "strategy_id": getattr(match, "strategy_id", "") or "",
                }
            )
    return tasks


def page_text_flags(pages: list[int]) -> dict[int, bool]:
    """判断每个物理页能否走规则法。

    优先复用 STEP1 的文档类型结论（基于全文档文字层字符数）；没有结论时
    退回本地文字网格的字数判断，保持与提取引擎同一口径。
    """
    ordered = sorted({int(page) for page in pages})
    flags: dict[int, bool] = {}
    profile = st.session_state.get("document_type_profile")
    if profile is not None and int(getattr(profile, "total_pages", 0) or 0) > 0:
        low_text = {int(page) for page in getattr(profile, "low_text_page_numbers", []) or []}
        for page in ordered:
            flags[page] = page not in low_text
        return flags
    grids = _cached_page_grids(ordered)
    for page in ordered:
        flags[page] = page_has_searchable_text(grids.get(page))
    return flags


def run_page_tasks(
    tasks: list[dict],
    method: str,
    *,
    progress_callback=None,
    allow_llm_fallback: bool = True,
    whole_page: bool = True,
) -> tuple[dict, list[dict], dict[int, str]]:
    """并发执行逐页提取。

    返回 ``(按目标表分组的结果, 日志行, 页码→引擎标签)``；结果结构保持与
    历史实现一致，供 ``build_tables_from_page_results`` 直接消费。
    """
    grids = _cached_page_grids([task["page"] for task in tasks])
    text_flags = page_text_flags([task["page"] for task in tasks])
    regions: dict[str, tuple[float, float, float, float]] = {}
    results: dict[str, list[tuple]] = {}
    logs: list[dict] = []
    mode_by_page: dict[int, str] = {}
    total = len(tasks)
    completed = 0
    with ThreadPoolExecutor(max_workers=EXTRACTION_MAX_WORKERS) as executor:
        future_to_task = {
            executor.submit(
                extract_page_with_method,
                pdf_bytes=st.session_state.pdf_bytes,
                page_number=task["page"],
                table_id=task["table_id"],
                table_name=task["table_name"],
                method=method,
                grid=grids.get(task["page"]),
                has_text=text_flags.get(task["page"]),
                table_config=task.get("table_config") or {},
                strategy_id=task.get("strategy_id") or None,
                api_key=st.session_state.llm_api_key,
                base_url=st.session_state.llm_base_url,
                model=st.session_state.llm_model,
                auto_vision_retry=bool(st.session_state.auto_vision_retry),
                allow_llm_fallback=allow_llm_fallback,
                whole_page=whole_page,
            ): task
            for task in tasks
        }
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            completed += 1
            if progress_callback:
                progress_callback(completed, total, task)
            try:
                outcome = future.result()
            except Exception as exc:
                logs.append(
                    extraction_log_entry(task, "未识别", "失败", f"逐页任务异常：{exc}")
                )
                continue
            label = outcome.mode_label
            mode_by_page[task["page"]] = label
            if outcome.region is not None:
                regions[f"{task['table_id']}:{task['page']}"] = outcome.region.bbox
            if outcome.succeeded:
                frames = outcome.frames or (outcome.frame,)
                if whole_page:
                    # 同一目标表的所有页内表/续页仍归到同一个结果键。
                    # 下游会把它们拼成一张可读的中间表，同时在
                    # table_blocks 中保留每张页内表的独立表头。
                    results.setdefault(task["table_id"], []).append(
                        (task["page"], list(frames), outcome.note)
                    )
                    log_note = outcome.note
                    if len(frames) > 1:
                        log_note += f"；已合并为同一张中间表（含{len(frames)}个表块）"
                else:
                    results.setdefault(task["table_id"], []).append(
                        (task["page"], outcome.frame, outcome.note)
                    )
                    log_note = outcome.note
                logs.append(
                    extraction_log_entry(task, label, "成功", log_note[:400])
                )
            else:
                logs.append(extraction_log_entry(task, label, "失败", outcome.note[:400]))
            if outcome.repairs:
                st.session_state.extraction_number_repairs.extend(
                    {
                        "目标表": task["table_name"],
                        "物理页码": task["page"],
                        "修补": note,
                    }
                    for note in outcome.repairs
                )
            if outcome.reviews:
                st.session_state.extraction_number_reviews.extend(
                    {
                        "目标表": task["table_name"],
                        "物理页码": task["page"],
                        "待核对": note,
                    }
                    for note in outcome.reviews
                )
    if regions:
        st.session_state.extraction_regions.update(regions)
    return results, logs, mode_by_page


def _engine_summary(mode_by_page: dict[int, str]) -> str:
    """把各页使用的引擎汇总成一句说明，突出节省的模型调用。"""
    counts: dict[str, int] = {}
    for label in mode_by_page.values():
        counts[label] = counts.get(label, 0) + 1
    parts = [f"{label} {count} 页" for label, count in counts.items()]
    rule_pages = counts.get(ENGINE_LABELS[ENGINE_RULE], 0)
    if rule_pages:
        parts.append(f"规则法已为本次提取节省 {rule_pages} 次模型调用")
    return "；".join(parts)


def _run_extraction(active_profile: ReportProfile, tasks: list[dict]) -> None:
    """STEP2：并发提取各页、合并跨页表格并生成工作簿。"""
    del active_profile  # Profile 已通过 task 内的表配置传入
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(tasks)
    method = st.session_state.extraction_method or DEFAULT_METHOD
    allow_llm_fallback = bool(st.session_state.allow_llm_fallback)
    whole_page = bool(st.session_state.get("extract_whole_page", True))
    st.session_state.extraction_number_repairs = []
    st.session_state.extraction_number_reviews = []
    st.session_state.extraction_regions = {}

    def report(completed: int, total_pages: int, task: dict) -> None:
        progress_bar.progress(completed / max(total_pages, 1))
        status_text.text(
            f"已处理 {completed}/{total_pages} 页：{task['table_name']} 第 {task['page']} 页"
        )

    with st.status("正在并发提取各页面...", expanded=True) as status_box:
        status_box.write(f"提取方式：{method}")
        results, logs, mode_by_page = run_page_tasks(
            tasks,
            method,
            progress_callback=report,
            allow_llm_fallback=allow_llm_fallback,
            whole_page=whole_page,
        )
        raw_tables = build_tables_from_page_results(
            results,
            mode_by_page,
            whole_page=whole_page,
        )
        _store_extraction_result(logs, raw_tables)
        status_box.write(_engine_summary(mode_by_page))
    failed_count = sum(1 for log in logs if log["状态"] == "失败")
    if failed_count:
        st.warning(
            f"有 {failed_count} 页提取失败，请查看下方日志；"
            "可在“中间表编辑与重新提取”中单独重跑失败的表。"
        )
    else:
        st.success("所有页面提取成功！")


def method_requires_credentials(method: str) -> bool:
    """纯大模型/纯图片方式必须有 AI 配置；规则法与智能模式可离线启动。"""
    return method in {MODE_LLM, MODE_VISION}


def _render_extraction_launcher(active_profile: ReportProfile) -> None:
    """STEP2：提取按钮、前置校验与任务编排。"""
    if st.button("开始智能提取", type="primary", key="extract_tables"):
        method = st.session_state.extraction_method or DEFAULT_METHOD
        credentials_ready = llm_credentials_ready()
        if method_requires_credentials(method) and not credentials_ready:
            st.error(
                "当前提取方式需要 AI 配置。请退出登录，在登录页填写 "
                "Base URL、模型名称和 API Key 后重新进入；"
                "或改用“智能模式/全部规则法”，文字版报告可完全离线提取。"
            )
            return
        if method == MODE_AUTO and not credentials_ready:
            st.info(
                "当前未配置 AI 接口：文字版页面将用规则法提取，"
                "扫描版页面会被跳过并写入日志。"
            )
        tasks = _extraction_tasks()
        if not tasks:
            st.warning("没有需要提取的页面，请确认页码配置。")
        else:
            _run_extraction(active_profile, tasks)


def _normalized_rows(rows) -> list[list[str]]:
    """把任意二维数据规整为等宽字符串行，便于与编辑器结果比较。"""
    width = max((len(row) for row in rows), default=0)
    return [
        ["" if cell is None else str(cell).strip() for cell in row]
        + [""] * (width - len(row))
        for row in rows
    ]


def rows_to_frame(rows) -> pd.DataFrame:
    """首行作表头的网格 → DataFrame（列名沿用去重规则）。"""
    grid = _normalized_rows(rows)
    if not grid:
        return pd.DataFrame()
    columns = dedupe_column_names(grid[0])
    return pd.DataFrame([list(row) for row in grid[1:]], columns=columns)


def _frame_factory(rows: list[list[str]], unit: str = ""):
    """为编辑后的中间表生成 to_frame 闭包，保持下游接口不变。"""
    frame = rows_to_frame(rows)

    def to_frame(*_args, _frame=frame, _unit=unit, **kwargs) -> pd.DataFrame:
        if kwargs.get("include_unit_footer") and _unit and _unit != "未识别":
            width = _frame.shape[1]
            footer = ["【单位备注】", f"单位：{_unit}"] + [""] * max(0, width - 2)
            footer_frame = pd.DataFrame([footer[:width]], columns=_frame.columns)
            return pd.concat([_frame, footer_frame], ignore_index=True)
        return _frame

    return to_frame


_BLOCK_SEPARATOR_RE = re.compile(
    r"^【.*(?:整页第\d+张表|第\d+页表格分块|表格分块).*】$"
)


def _rebuild_table_blocks(
    rows: list[list[str]],
    page: int,
    unit: str = "",
) -> list[dict]:
    """人工编辑后重建可定位表头的数据块，保留单位与分块分隔行信息。

    旧的 table_blocks 对应的是编辑前网格，不能继续使用；但也不能直接
    清空，否则 STEP3 会退回旧合并表、忽略人工修正后的表头。这里按
    “【整页第N张表】/【第N页表格分块】”分隔行重建；没有分隔行时整张表
    作为一个块。分隔行本身不写入数据，避免污染表头识别。
    """
    blocks: list[dict] = []
    current: list[list[str]] = []
    current_page = int(page or 1)

    def flush() -> None:
        if not current:
            return
        block = {
            "page": current_page,
            "rows": [list(row) for row in current],
        }
        if unit and unit != "未识别":
            block["unit"] = unit
        blocks.append(block)

    for row in rows:
        cells = [str(cell) for cell in row]
        first = next((cell.strip() for cell in cells if cell.strip()), "")
        if _BLOCK_SEPARATOR_RE.match(first):
            flush()
            current = []
            page_match = re.search(r"第(\d+)页", first)
            if page_match:
                current_page = int(page_match.group(1))
            continue
        current.append(cells)
    flush()
    if not blocks:
        blocks = [{
            "page": int(page or 1),
            "rows": [list(row) for row in rows],
        }]
    return blocks


def persist_table_rows(table, rows: list[list[str]]) -> None:
    """把编辑后的网格写回中间表对象，并刷新表格快照。"""
    # 注意：FlexibleTable 对未知字段一律返回默认值，因此这里只能用 vars() 判断，
    # 不能依赖 hasattr/getattr。
    if "original_rows" not in vars(table):
        table.original_rows = [list(row) for row in table.rows]
    table.rows = [list(row) for row in rows]
    table.to_frame = _frame_factory(table.rows, str(getattr(table, "unit", "") or ""))
    # ★ P0-4：保留可定位表头的数据块；按编辑后的内容重建，既尊重人工修改，
    #   又不让 STEP3 完全回退到旧的合并表。
    table.table_blocks = _rebuild_table_blocks(
        table.rows,
        int(getattr(table, "page", 1) or 1),
        str(getattr(table, "unit", "") or ""),
    )
    table.edited = True


def _table_snapshot(table) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(row) for row in _normalized_rows(table.rows))


def _editor_frame(rows) -> tuple[pd.DataFrame, int]:
    """把网格（含表头行）转成等宽的编辑用 DataFrame。

    列标签使用稳定的“列1/列2/…”，而不是把表头当列名：这样表头行本身也能在
    网格里直接编辑，而且空/重复表头不会再引起列名变化、把编辑器状态重置掉。
    """
    grid = _normalized_rows(rows)
    if not grid:
        return pd.DataFrame(), 0
    width = max(len(row) for row in grid)
    columns = [f"列{index + 1}" for index in range(width)]
    return pd.DataFrame(grid, columns=columns), width


def _rows_from_editor(frame: pd.DataFrame) -> list[list[str]]:
    """把编辑器的返回结果还原成字符串网格（列标签只用于定位，不写入数据）。"""
    return [
        ["" if value is None else str(value).strip() for value in row]
        for row in frame.fillna("").astype(str).values.tolist()
    ]


def _add_column(rows: list[list[str]]) -> list[list[str]]:
    """在表格最右侧追加一列空列。"""
    return [list(row) + [""] for row in rows]


def _delete_columns(rows: list[list[str]], indexes) -> list[list[str]]:
    """删除指定列；至少保留一列，避免表格变成零列。"""
    selected = {int(index) for index in indexes}
    if not selected:
        return rows
    width = max((len(row) for row in rows), default=0)
    keep = [index for index in range(width) if index not in selected]
    if not keep:
        return [[""] for _ in rows]
    return [
        [row[index] if index < len(row) else "" for index in keep]
        for row in rows
    ]


def _record_edit_event(table, action: str, result: str) -> None:
    """记录一次中间表人工操作，供下方日志展开查看。"""
    st.session_state.extraction_edit_events.append(
        {
            "时间": pd.Timestamp.now().strftime("%H:%M:%S"),
            "目标表": table.table_name,
            "操作": action,
            "结果": result,
        }
    )


def _render_table_editor(tables: list, selected_index: int) -> None:
    """中间表实时编辑：单元格、表头、行列都可改，改动立即写回会话状态。"""
    table = tables[selected_index]
    st.markdown("##### ✏️ 中间表编辑与重新提取")
    st.caption(
        "第一行是表头，可直接在表格里修改任意单元格、增删行；列数用下方的“添加一列/删除列”调整。"
        "改动会立即保存并同步到下载文件；STEP3 标准填报请在本步修改完成后重新执行。"
    )
    if not st.session_state.standard_data.empty:
        st.warning(
            "检测到已经执行过 STEP3 标准填报：中间表修改后会与既有标准窄表不一致，"
            "请回到 STEP3 重新执行标准填报。",
            icon=":material/warning:",
        )

    editor_frame, _ = _editor_frame(table.rows)
    if editor_frame.empty:
        st.info("该表当前没有可编辑内容，可尝试“重新提取该表”。")
        return

    edited_frame = st.data_editor(
        editor_frame,
        key=f"table_editor_{table.table_id}",
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
    )
    edited_rows = _rows_from_editor(edited_frame)
    if _table_snapshot(table) != tuple(tuple(row) for row in _normalized_rows(edited_rows)):
        persist_table_rows(table, edited_rows)
        _record_edit_event(
            table,
            "人工编辑中间表",
            f"当前{max(len(edited_rows) - 1, 0)}条数据行",
        )
        _store_extraction_result(st.session_state.ai_extraction_logs, tables)
        st.success("已保存修改，下载文件已同步更新。", icon=":material/edit:")

    # ---- 列管理：数据编辑器本身不支持增删列，这里提供独立入口 ----
    current_rows = [list(row) for row in table.rows]
    current_width = max((len(row) for row in current_rows), default=0)
    header_labels = [
        (
            str(current_rows[0][index]).strip()
            if current_rows and index < len(current_rows[0]) and str(current_rows[0][index]).strip()
            else "（空）"
        )
        for index in range(current_width)
    ]

    col_actions = st.columns([1, 1.4, 1.2])
    with col_actions[0]:
        if st.button(
            "添加一列",
            key=f"add_col_{table.table_id}",
            width="stretch",
            help="在表格最右侧追加一列空列。",
        ):
            persist_table_rows(table, _add_column(current_rows))
            _record_edit_event(table, "添加一列", f"当前{current_width + 1}列")
            _store_extraction_result(st.session_state.ai_extraction_logs, tables)
            st.session_state.pop(f"table_editor_{table.table_id}", None)
            st.rerun()
    with col_actions[1]:
        delete_targets = st.multiselect(
            "选择要删除的列",
            options=list(range(current_width)),
            format_func=lambda index: f"第{index + 1}列 {header_labels[index]}",
            key=f"del_cols_{table.table_id}",
        )
    with col_actions[2]:
        if st.button(
            "删除选中列",
            key=f"del_col_btn_{table.table_id}",
            width="stretch",
            disabled=not delete_targets,
            help="删除左侧选中的列，至少保留一列。",
        ):
            remaining = _delete_columns(current_rows, delete_targets)
            persist_table_rows(table, remaining)
            _record_edit_event(
                table,
                "删除列",
                f"剩余{max((len(row) for row in remaining), default=0)}列",
            )
            _store_extraction_result(st.session_state.ai_extraction_logs, tables)
            st.session_state.pop(f"table_editor_{table.table_id}", None)
            st.session_state.pop(f"del_cols_{table.table_id}", None)
            st.rerun()

    action_cols = st.columns([1.1, 1, 1.4])
    with action_cols[1]:
        if st.button(
            "撤销全部修改",
            key=f"revert_edits_{table.table_id}",
            disabled="original_rows" not in vars(table),
            help="把该中间表还原为最近一次自动提取的结果。",
            width="stretch",
        ):
            table.rows = [list(row) for row in table.original_rows]
            table.to_frame = _frame_factory(
                table.rows,
                str(getattr(table, "unit", "") or ""),
            )
            table.edited = False
            del table.original_rows
            st.session_state.pop(f"table_editor_{table.table_id}", None)
            st.session_state.pop(f"del_cols_{table.table_id}", None)
            _record_edit_event(table, "撤销修改", "已还原为自动提取结果")
            _store_extraction_result(st.session_state.ai_extraction_logs, tables)
            st.rerun()
    with action_cols[2]:
        reextract_method = st.selectbox(
            "重新提取方式",
            ("跟随全局设置", MODE_RULE, MODE_LLM, MODE_VISION),
            key=f"reextract_method_{table.table_id}",
            label_visibility="collapsed",
        )
        if st.button(
            "重新提取该表",
            key=f"reextract_table_{table.table_id}",
            width="stretch",
            help="按该表在 STEP1 确认的页码重新执行逐页提取，覆盖当前中间表（含人工修改）。",
        ):
            _reextract_table(tables, selected_index, reextract_method)


def _reextract_table(tables: list, index: int, method_option: str) -> None:
    """按 STEP1 确认的页码重新提取一张目标表并替换中间表。"""
    table = tables[index]
    match = next(
        (item for item in st.session_state.page_matches if item.table_id == table.table_id),
        None,
    )
    if match is None or not match.pages:
        st.error("该目标表在 STEP1 没有确认页码，无法重新提取。")
        return
    if method_option == "跟随全局设置":
        method = st.session_state.extraction_method or DEFAULT_METHOD
    else:
        method = method_option
    if method_requires_credentials(method) and not llm_credentials_ready():
        st.error("该提取方式需要 AI 配置，请改用规则法或先在登录页补齐 Base URL / 模型 / API Key。")
        return
    tasks = _extraction_tasks([match])
    if not tasks:
        st.error("该目标表没有有效页码。")
        return
    whole_page = bool(st.session_state.get("extract_whole_page", True))
    with st.spinner(f"正在用“{method}”重新提取 {table.table_name}…"):
        results, logs, mode_by_page = run_page_tasks(
            tasks,
            method,
            allow_llm_fallback=bool(st.session_state.allow_llm_fallback),
            whole_page=whole_page,
        )
        new_tables = build_tables_from_page_results(
            results,
            mode_by_page,
            whole_page=whole_page,
        )
    if not new_tables:
        st.error(
            "重新提取未获得数据，已保留原中间表；请核对页码，或改用其他提取方式（例如图片识别）。"
        )
        st.session_state.ai_extraction_logs.extend(logs)
        return
    replacement = new_tables[0]
    tables[index : index + 1] = new_tables
    st.session_state.raw_tables = tables
    st.session_state.pop(f"table_editor_{table.table_id}", None)
    st.session_state.extraction_edit_events.append(
        {
            "时间": pd.Timestamp.now().strftime("%H:%M:%S"),
            "目标表": table.table_name,
            "操作": f"重新提取（{method}）",
            "结果": f"{replacement.strategy}，{max(len(replacement.rows) - 1, 0)}条数据行",
        }
    )
    st.session_state.ai_extraction_logs.extend(
        {"目标表": row["目标表"], "物理页码": row["物理页码"], "识别方式": row["识别方式"],
         "状态": row["状态"], "说明": f"重新提取：{row['说明']}"}
        for row in logs
    )
    _store_extraction_result(st.session_state.ai_extraction_logs, tables)
    st.success(
        f"已重新提取 {replacement.table_name}（{replacement.strategy}，"
        f"共 {len(new_tables)} 张表）。"
    )
    st.rerun()


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
            edited_mark = "✏️ " if getattr(item, "edited", False) else ""
            return f"{edited_mark}{item.table_name} - 第{page_label}页 - {item.strategy}"

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

        _render_table_editor(tables, selected_table_index)

        table_col, pdf_col = st.columns([1.15, 1], gap="large")
        with table_col:
            st.markdown("##### 网格化重构结果")
            st.dataframe(
                selected_table.to_frame(include_unit_footer=True), width="stretch", hide_index=True
            )
        with pdf_col:
            # 将 source_pages 从字符串还原为整数列表
            source_pages_raw = selected_table.source_pages
            if isinstance(source_pages_raw, str):
                source_pages_list = [
                    int(p.strip()) for p in source_pages_raw.split(",") if p.strip().isdigit()
                ]
            else:
                source_pages_list = (
                    source_pages_raw if isinstance(source_pages_raw, list) else [source_pages_raw]
                )

            selected_source_page = st.selectbox(
                "PDF原页核实",
                options=source_pages_list,
                format_func=lambda value: f"第 {value} 页",
                key=f"ai_source_page_{selected_table.table_id}",
            )
            try:
                region_bbox = st.session_state.extraction_regions.get(
                    f"{selected_table.table_id}:{selected_source_page}"
                )
                st.image(
                    render_pdf_page(
                        st.session_state.pdf_bytes,
                        selected_source_page,
                        highlight=region_bbox,
                    ),
                    caption=(
                        f"PDF物理第 {selected_source_page} 页"
                        + ("（红框=本次提取的表区域）" if region_bbox else "")
                    ),
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


def _render_extraction_single_tab(active_profile: ReportProfile) -> None:
    """STEP2 表格智能转换。"""
    st.subheader("表格智能转换")
    st.caption(
        "默认使用智能模式：文字版页面先用规则法离线重建，规则未通过或扫描版页面"
        "自动转大模型。默认同时开启“整页全表提取”，保留本页所有表格，避免目标表"
        "边界裁剪造成指标缺失。"
        "所有页面成功后再按人工确认的页码顺序确定性拼接；提取结果支持直接修改或单独重提。"
    )
    if not st.session_state.page_matches or not st.session_state.pages_confirmed:
        if st.session_state.raw_tables:
            st.warning(
                "当前页码状态尚未重新确认，但已有 STEP2 提取结果已保留。"
                "可直接查看/下载/进入 STEP3；只有需要重新提取时才回到 STEP1 确认页码，"
                "无需重复提取。",
                icon=":material/info:",
            )
        else:
            st.info("请先在 STEP1 上传报告、完成页码定位并人工确认物理页码。")
    else:
        _render_extraction_settings()
        _render_extraction_launcher(active_profile)
    if st.session_state.ai_extraction_logs:
        st.markdown("##### 提取与重试日志")
        if st.session_state.get("extract_whole_page", True):
            st.caption(
                "当前为整页全表模式：每页识别到的所有表格都会保留，并合并成同一张"
                "中间表展示；内部按页内表块保留独立表头，Step3 会分块识别。"
            )
        else:
            st.caption(
                "识别方式列写明每页实际使用的引擎；“说明”列如出现“页内区域定位”，"
                "表示该页存在多张表、系统已按目标表的首尾项目裁切到目标区域，"
                "可在下方 PDF 原页核实中看到红框标记。"
            )
        logs = st.session_state.ai_extraction_logs
        if logs and isinstance(logs[0], dict):
            # 自定义列顺序，与原始日志列名保持一致
            columns = ["目标表", "物理页码", "识别方式", "状态", "说明"]
            df = pd.DataFrame(logs, columns=columns)
        else:
            # 兼容旧格式（对象列表）
            df = extraction_logs_frame(logs)
        st.dataframe(df, width="stretch", hide_index=True)
        _render_number_quality_panel()
        _render_extraction_results()


def _render_extraction_batch_tab(active_profile: ReportProfile) -> None:
    """STEP2 batch: rule extraction by default, shared results for STEP3."""
    st.subheader("批量表格提取")
    st.caption(
        "批量默认让文字页走规则法，图片型页面自动转视觉大模型；"
        "可在下方关闭图片页视觉兜底。所有公司共用 STEP1 已确认的页码。"
    )
    documents = render_batch_status(active_profile, detail_key="extraction")
    if not documents:
        st.info("请先在 STEP1 的“批量处理（多公司）”中上传 PDF 并确认页码。")
        return

    with st.expander("批量提取设置", expanded=True):
        method = st.selectbox(
            "提取方式",
            [MODE_RULE, MODE_AUTO, MODE_LLM, MODE_VISION],
            key="batch_extraction_method",
            help=(
                "默认“全部规则法”。智能模式仅对规则法失败的文字页或扫描页调用模型；"
                "全部大模型/图片模式会让 token 消耗随文件数和页数线性增长。"
            ),
        )
        allow_llm_fallback = st.toggle(
            "规则法失败时允许大模型兜底",
            key="batch_allow_llm_fallback",
            disabled=method not in {MODE_AUTO, MODE_LLM},
            help="默认关闭。批量开启前请评估文件数、页数和模型单价。",
        )
        scan_vision_fallback = st.toggle(
            "图片型页面自动使用视觉大模型",
            key="batch_scan_vision_fallback",
            help=(
                "仅对没有可检索文字的页面调用视觉模型；文字页仍走规则法。"
                "这是批量处理扫描版 PDF 的必要兜底。"
            ),
        )
        if scan_vision_fallback and not llm_credentials_ready():
            st.warning(
                "当前未配置完整 AI 接口；图片型页面将无法识别，并在提取日志中标记失败。",
                icon=":material/warning:",
            )
        whole_page = st.toggle(
            "整页全表提取",
            key="batch_whole_page",
        )
        concurrency_columns = st.columns(2)
        with concurrency_columns[0]:
            document_workers = st.slider(
                "公司级并发数",
                min_value=1,
                max_value=4,
                key="batch_document_workers",
                help="同时处理多少家公司的 PDF；建议 2–3，视觉模型接口限流时降低。",
            )
        with concurrency_columns[1]:
            workers = st.slider(
                "每份 PDF 的页级并发数",
                min_value=1,
                max_value=12,
                key="batch_workers",
                help="单份 PDF 内部同时处理多少页；规则法可提高，视觉模型接口限流时降低。",
            )
        if method != MODE_RULE:
            st.warning(
                "当前不是纯规则法。批量运行会逐页调用大模型，token 消耗可能显著增加。",
                icon=":material/warning:",
            )
        method_explanation = {
            MODE_RULE: (
                "规则法：文字页用 pdfplumber 线框/文字对齐；"
                + ("图片页自动转视觉大模型。" if scan_vision_fallback else "图片页不调用模型。")
            ),
            MODE_AUTO: (
                "混合方法：文字版页面先用规则法，规则失败后"
                + ("允许" if allow_llm_fallback else "不调用")
                + "大模型兜底；扫描版页面走视觉模型。"
            ),
            MODE_LLM: "大模型文本优先，必要时图片重试，Token 消耗较高。",
            MODE_VISION: "全部页面转图片交给视觉模型，Token 消耗最高。",
        }[method]
        st.info(
            f"当前批量提取逻辑：**{method}**\n\n{method_explanation}"
        )

    confirmed = [
        (company, document)
        for company, document in documents.items()
        if document.get("pages_confirmed")
    ]
    def run_extraction(target_documents: list[tuple[str, dict]]) -> None:
        nonlocal documents
        if method in {MODE_LLM, MODE_VISION} and not llm_credentials_ready():
            st.error("当前提取方式需要 AI 配置；请改用默认规则法。")
            return
        method_label = method + (
            " + 图片页视觉兜底" if scan_vision_fallback else ""
        )
        progress = st.progress(0, text="正在批量提取…")
        updated_documents = dict(documents)
        api_key = st.session_state.llm_api_key
        base_url = st.session_state.llm_base_url
        model_name = st.session_state.llm_model
        def extract_document(company: str, document: dict):
            outcome = extract_document_tables(
                company=company,
                pdf_bytes=document_pdf_bytes(document),
                matches=document.get("matches") or [],
                method=method,
                allow_llm_fallback=allow_llm_fallback,
                vision_for_scanned_pages=scan_vision_fallback,
                whole_page=whole_page,
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                max_workers=workers,
            )
            return company, document, outcome

        with ThreadPoolExecutor(
            max_workers=max(1, int(document_workers))
        ) as pool:
            futures = {
                pool.submit(extract_document, company, document): company
                for company, document in target_documents
            }
            for index, future in enumerate(as_completed(futures), 1):
                company = futures[future]
                try:
                    company, document, outcome = future.result()
                except Exception as exc:
                    document = documents[company]
                    outcome = SimpleNamespace(
                        tables=[],
                        logs=[],
                        status="异常",
                        note=str(exc)[:300],
                    )
                updated = dict(document)
                updated.update(
                    {
                        "raw_tables": outcome.tables,
                        "extraction_logs": outcome.logs,
                        "extraction_status": outcome.status,
                        "extraction_note": outcome.note,
                        "extraction_method": method_label,
                        "standard_data": pd.DataFrame(columns=STANDARD_COLUMNS),
                        "standard_summary": pd.DataFrame(),
                        "standard_diagnostics": pd.DataFrame(),
                        "standard_workbook_bytes": b"",
                        "standard_status": "",
                        "standard_note": "",
                    }
                )
                updated_documents[company] = updated
                replace_document(active_profile, company, updated)
                progress.progress(
                    index / max(len(target_documents), 1),
                    text=(
                        f"已提取 {index}/{len(target_documents)}：{company}"
                        f"｜{outcome.status}｜{method_label}"
                    ),
                )
        st.session_state.batch_documents = updated_documents
        success_count = sum(
            bool(document.get("raw_tables"))
            for document in updated_documents.values()
        )
        st.session_state.batch_last_operation = (
            f"批量提取完成：{success_count}/{len(updated_documents)} 份，逻辑：{method_label}"
        )
        failed_names = [
            company
            for company in (name for name, _ in target_documents)
            if not updated_documents.get(company, {}).get("raw_tables")
        ]
        if failed_names:
            st.error(
                f"提取失败（{len(failed_names)} 家）：" + "、".join(failed_names)
            )
        else:
            st.success(
                f"批量提取完成：{success_count}/{len(updated_documents)} 份 PDF 取得中间表。"
            )
        documents = batch_documents(active_profile)

    if st.button(
        f"批量提取全部已确认文件（{len(confirmed)} 份）",
        type="primary",
        key="batch_extract_all_documents",
        disabled=not confirmed,
    ):
        run_extraction(confirmed)

    failed_documents = [
        company
        for company in failed_batch_companies(documents, "extraction")
        if company in documents
    ]
    retry_names = st.multiselect(
        "指定公司重新处理",
        list(documents),
        default=[name for name in failed_documents if name in documents],
        key=f"batch_retry_companies_{active_profile.profile_id}",
        help="可只选择一家失败公司重跑，不必重新处理整个批次。",
    )
    if st.button(
        f"重新处理选中公司（{len(retry_names)} 家）",
        key="batch_retry_selected_documents",
        disabled=not retry_names,
    ):
        run_extraction(
            [
                (company, documents[company])
                for company in retry_names
                if company in documents
            ]
        )

    current = selected_batch_document(
        active_profile,
        documents,
        key_prefix="extraction",
    )
    if current is None:
        return
    tables = current.get("raw_tables") or []
    if not tables:
        st.info(
            f"{current['company']} 尚无提取结果。"
            f"{current.get('extraction_note', '')}"
        )
        return
    st.markdown(f"#### 中间表预览：{current['company']}")
    selected_index = st.selectbox(
        "选择目标表",
        range(len(tables)),
        format_func=lambda index: (
            f"{tables[index].table_name}｜第"
            f"{','.join(map(str, tables[index].source_pages or [tables[index].page]))}页"
        ),
        key=f"batch_table_preview_{active_profile.profile_id}_{current['company']}",
    )
    table = tables[selected_index]
    st.caption(
        f"提取方式：{table.strategy}｜单位：{getattr(table, 'unit', '未识别')}"
    )
    st.dataframe(
        table.to_frame(),
        width="stretch",
        hide_index=True,
    )
    logs = current.get("extraction_logs") or []
    if logs:
        with st.expander(f"提取日志（{len(logs)} 页）", expanded=False):
            st.dataframe(
                pd.DataFrame(logs),
                width="stretch",
                hide_index=True,
            )


def render_extraction_tab(active_profile: ReportProfile) -> None:
    """STEP2: single-file editor and multi-file extraction workspace."""
    single_tab, batch_tab = st.tabs(
        ["📄 单份处理（当前 PDF）", "🗂️ 批量处理（多公司）"]
    )
    with single_tab:
        _render_extraction_single_tab(active_profile)
    with batch_tab:
        _render_extraction_batch_tab(active_profile)


def _render_number_quality_panel() -> None:
    """展示千分位修复记录与待人工核对的疑似误识别。"""
    reviews = st.session_state.extraction_number_reviews
    repairs = st.session_state.extraction_number_repairs
    if reviews:
        with st.expander(
            f"疑似千分位误识别待核对（{len(reviews)}处）",
            expanded=bool(reviews),
        ):
            st.caption(
                "以下数值只出现一个三位小数段（如 1.234），原文中没有对应的千分位写法，"
                "无法自动判定是金额还是小数，请在“中间表编辑”中核对后修改。"
            )
            st.dataframe(pd.DataFrame(reviews), width="stretch", hide_index=True)
    if repairs:
        with st.expander(f"千分位自动修复记录（{len(repairs)}处）", expanded=False):
            st.caption(
                "模型把千分位逗号写成了小数点（例如 1.234.567.89），"
                "已按同一页原文证据确定为千分位并还原为逗号。"
            )
            st.dataframe(pd.DataFrame(repairs), width="stretch", hide_index=True)
    events = st.session_state.extraction_edit_events
    if events:
        with st.expander(f"中间表修改与重新提取记录（{len(events)}条）", expanded=False):
            st.dataframe(pd.DataFrame(events), width="stretch", hide_index=True)


def clear_extraction_results(*, clear_caches: bool = False) -> None:
    """清空提取结果与下游标准化结果；clear_caches 同时重置提取缓存。"""
    values = {
        "raw_tables": [],
        "ai_extraction_logs": [],
        "ai_workbook_bytes": b"",
        "extraction_edit_events": [],
        "extraction_number_repairs": [],
        "extraction_number_reviews": [],
        "extraction_regions": {},
        "standard_data": pd.DataFrame(columns=STANDARD_COLUMNS),
        "validation_results": pd.DataFrame(),
        "validation_evidence": pd.DataFrame(),
        "step3_focus": {},
        "normalization_diagnostics": pd.DataFrame(),
    }
    # 编辑器的控件状态与旧中间表绑定，必须一并清空，否则会串到新结果。
    for key in [
        key for key in st.session_state if str(key).startswith("table_editor_")
    ]:
        del st.session_state[key]
    if clear_caches:
        values.update(
            {
                "table_candidates": {},
                "selected_table_candidates": {},
                "extraction_grid_cache": {},
                "extraction_image_cache": {},
                "extraction_cache_lock": Lock(),
            }
        )
    for key, value in values.items():
        st.session_state[key] = value


def _render_extraction_settings() -> None:
    """STEP2：逐页提取方式与重试设置。"""
    if st.session_state.extraction_method not in METHOD_OPTIONS:
        st.session_state.extraction_method = DEFAULT_METHOD
    with st.expander("逐页提取方式与重试设置", expanded=not bool(st.session_state.raw_tables)):
        profile = st.session_state.get("document_type_profile")
        if profile is not None:
            st.info(
                f"**文档类型：{profile.label}**"
                f"（可检索文本页 {profile.text_pages}/{profile.total_pages}）\n\n{profile.notes}"
            )
        method = st.radio(
            "提取方式",
            METHOD_OPTIONS,
            key="extraction_method",
            help=(
                "默认“智能”模式：文字版页面先走规则法，未通过质量校验或扫描版"
                "页面才调用大模型；“全部规则法”可完全离线，“全部大模型法”适合"
                "版式特别复杂的报告。"
            ),
        )
        _render_extraction_plan(method)
        st.toggle(
            "整页全表提取（推荐）",
            key="extract_whole_page",
            help=(
                "开启后不再按目标表首尾项目裁切页面，而是保留该页识别到的全部表格，"
                "同一目标表的页内多表和跨页内容合并到一张中间表；可避免边界裁剪导致"
                "指标缺失，同时保持中间表可读性。"
            ),
        )
        option_cols = st.columns(2)
        if method in {MODE_AUTO, MODE_LLM}:
            with option_cols[0]:
                st.toggle(
                    "单页提取失败时自动图片扫描重试",
                    key="auto_vision_retry",
                    help="只重试失败的物理页；模型不支持图片时会改用该页的加强版文本纠错。",
                )
        if method == MODE_AUTO:
            with option_cols[1]:
                st.toggle(
                    "规则法未通过或未识别到表格时改用大模型",
                    key="allow_llm_fallback",
                    help=(
                        "关闭后完全离线：规则法未通过的页面只记录失败，不调用模型，"
                        "适合批量试跑或统计规则法覆盖率。"
                    ),
                )
        configured_model = st.session_state.get("llm_model", "") or "未设置"
        st.caption(
            f"使用登录页中的 AI 设置，当前模型：{configured_model}。"
            "API Key 仅保存在当前浏览器会话内，不会写入配置文件或导出的 Excel。"
        )


def _render_extraction_plan(method: str) -> None:
    """按当前提取方式预估每页会用到的引擎，直观展示节省的模型调用。"""
    tasks = _extraction_tasks()
    if not tasks:
        st.caption("尚未确认页码：请在 STEP1 完成定位并确认后再提取。")
        return
    pages = [task["page"] for task in tasks]
    text_flags = page_text_flags(pages)
    engine_pages: dict[str, list[int]] = {}
    for page in sorted(set(pages)):
        engine = preview_page_engine(method, has_text=bool(text_flags.get(page)))
        engine_pages.setdefault(engine, []).append(page)
    parts = [
        f"{ENGINE_LABELS.get(engine, engine)} {len(engine_pages[engine])} 页"
        for engine in (ENGINE_RULE, ENGINE_LLM_TEXT, ENGINE_LLM_VISION)
        if engine_pages.get(engine)
    ]
    rule_pages = len(engine_pages.get(ENGINE_RULE, []))
    caption = f"本次共 {len(set(pages))} 页：{'；'.join(parts) or '无'}。"
    if rule_pages and method == MODE_AUTO:
        caption += f" 规则法预计节省 {rule_pages} 次模型调用。"
    st.caption(caption)
