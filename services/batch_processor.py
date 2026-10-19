"""批量处理核心逻辑：从内存 bytes 处理 PDF，可被 Streamlit 或命令行共用。"""
from __future__ import annotations

import traceback
from dataclasses import dataclass

import pandas as pd

from .batch_extraction import (
    FlexibleTable,
    convert_table_rows_to_wan,
    detect_table_unit,
    extract_single_page,
    extract_single_page_vision,
    sanitize_tree,
    sniff_unit_from_text,
)
from .solvency_company_identity import (
    load_company_master_workbook,
    load_peer_group_config,
)
from .solvency_hybrid_pipeline import locate_tables_hybrid
from .solvency_metric_registry import extend_taxonomy
from .solvency_normalizer import load_taxonomy
from .solvency_pdf_locator import PageMatch, extract_report_metadata
from .solvency_step3_standardizer import (
    result_workbook_bytes,
    standardize_to_target,
    step3_metric_catalog,
)

_SOURCE_PRIORITY = {
    "偿付能力充足率指标": 100,
    "主要经营指标（财险）": 100,
    "主要经营指标": 100,
    "近三年（综合）投资收益率": 100,
    "实际资本表": 50,
    "认可资产表": 50,
    "认可负债表": 50,
    "最低资本表": 50,
    "S02-实际资本明细表": 10,
    "S05-最低资本表": 10,
}


def _source_priority(sheet_name) -> int:
    if not isinstance(sheet_name, str):
        return 0
    name = sheet_name.strip()
    if name in _SOURCE_PRIORITY:
        return _SOURCE_PRIORITY[name]
    for key, score in _SOURCE_PRIORITY.items():
        if name.startswith(key):
            return score
    return 0


def _dedupe_by_source_priority(frame):
    if frame is None or frame.empty:
        return frame
    required = {"公司", "指标编码", "期间口径", "来源工作表"}
    if not required.issubset(frame.columns):
        return frame
    work = frame.copy()
    work["_priority"] = work["来源工作表"].map(_source_priority)
    work["_page_sort"] = pd.to_numeric(
        work.get("来源页码", pd.Series([0] * len(work), index=work.index))
        .astype(str).str.extract(r"(\d+)")[0],
        errors="coerce",
    ).fillna(9999)
    work = work.sort_values(
        ["公司", "指标编码", "期间口径", "_priority", "_page_sort"],
        ascending=[True, True, True, False, True],
    )
    work = work.drop_duplicates(
        subset=["公司", "指标编码", "期间口径"],
        keep="first",
    )
    work = work.drop(columns=["_priority", "_page_sort"])
    return work.reset_index(drop=True)


@dataclass
class BatchContext:
    profile: object
    feature_config: dict
    table_configs: list
    taxonomy: pd.DataFrame
    target_catalog: pd.DataFrame
    include_derived: bool
    company_type_map: dict
    default_company_type: str
    peer_group_map: dict
    default_peer_group: str
    api_key: str
    base_url: str
    model: str
    use_vision: bool


def build_context_from_profile(
    profile,
    *,
    api_key: str,
    base_url: str,
    model: str,
    use_vision: bool = False,
    company_type_map: dict | None = None,
) -> BatchContext:
    """从 ReportProfile 构造批处理上下文（只读，可多线程共享）。"""
    taxonomy = (
        profile.taxonomy_frame()
        if profile.field_dictionary
        else load_taxonomy(str(profile.resource_path("normalization", "taxonomy_file")))
    )
    include_derived = profile.profile_id == "LIFE_SOLVENCY"
    if include_derived:
        taxonomy = extend_taxonomy(taxonomy)
    target_catalog = step3_metric_catalog(taxonomy, include_derived=include_derived)

    feature_config = dict(profile.feature_config)
    table_configs = feature_config.get("tables", [])
    # ★ 修复：批量处理也要处理 required=False 的表（如"流动性风险监测指标"）。
    #   required=False 表示"允许缺失"，不是"不要处理"：
    #   - locate_tables_hybrid 对找不到的表返回空 pages
    #   - process_one_pdf_bytes 只在"一张表都没定位到"时才判为失败
    #   所以把全部表传给批量定位是安全的。
    scoped_feature_config = {**feature_config, "tables": table_configs}

    # peer group 配置路径（找不到不报错，用空配置）
    try:
        if profile.profile_id == "NON_LIFE_SOLVENCY":
            master_path = profile.project_root / "财险公司列表.xlsx"
            alias_path = (
                profile.project_root
                / "config"
                / "non_life_solvency_company_urls.xlsx"
            )
            company_master = load_company_master_workbook(
                master_path,
                alias_workbook=alias_path,
            )
            peer_group_map = dict(company_master.peer_group_map)
            default_peer_group = company_master.default_peer_group
            company_type_map = {
                **dict(company_master.company_type_map),
                **dict(company_type_map or {}),
            }
        else:
            peer_cfg = profile.project_root / "config" / "solvency_peer_groups.json"
            peer_group_map, default_peer_group = load_peer_group_config(peer_cfg)
    except Exception:
        peer_group_map, default_peer_group = {}, ""

    return BatchContext(
        profile=profile,
        feature_config=scoped_feature_config,
        table_configs=table_configs,
        taxonomy=taxonomy,
        target_catalog=target_catalog,
        include_derived=include_derived,
        company_type_map=company_type_map or {},
        default_company_type=profile.company_types[0],
        peer_group_map=peer_group_map,
        default_peer_group=default_peer_group,
        api_key=api_key,
        base_url=base_url,
        model=model,
        use_vision=use_vision,
    )


# ---------- 表头/数据行对齐（复用 batch_run.py 中的逻辑） ----------
# batch_processor.py
def _detect_header_index(raw_rows) -> int:
    _HEADER_HINTS = (
        "指标名称", "项目", "行次", "本季度", "上季度",
        "本年累计", "本年度累计",          # ← 新增 "本年度累计"
        "期末", "期初", "下季度", "下季末",   # ← 顺手加 "下季末"
        "保费风险", "准备金风险", "本期", "上期",
    )
    best_idx, best_hits = 0, 0
    for i, row in enumerate(raw_rows[:30]):
        text = "".join(str(c) for c in row)
        if "本季度" in text and ("上季度" in text or "本年累计" in text):
            return i
        hits = sum(1 for h in _HEADER_HINTS if h in text)
        if hits > best_hits:
            best_hits, best_idx = hits, i
    return best_idx if best_hits > 0 else 0


def _dedup_columns(cols) -> list[str]:
    seen, out = {}, []
    for i, c in enumerate(cols):
        name = str(c).strip() or f"列{i+1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(name)
    return out


def _merge_pages_to_table(table_match, page_dfs, table_config):
    """把同一张表的多页 DataFrame 合并（与 batch_run.py 一致的逻辑）。"""
    if not page_dfs:
        return None
    page_dfs.sort(key=lambda x: x[0])
    merged = pd.concat([df for _, df, _ in page_dfs], ignore_index=True).fillna("").astype(str)
    raw_rows = merged.values.tolist()
    if not raw_rows:
        return None

    header_idx = _detect_header_index(raw_rows)
    header_row = [str(c).strip() for c in raw_rows[header_idx]]

    # ★ 众安修复：表头首列是"本季度数/上季度数"时，左移一位对齐数据行
    _PERIOD_HINTS = ("本季度", "上季度", "下季度", "期末", "期初", "本年累计", "本年度累计", "近三年")
    if header_row and any(k in header_row[0] for k in _PERIOD_HINTS):
        header_row = [""] + header_row[:-1]

    data_rows = [r for r in raw_rows[header_idx + 1:] if r != raw_rows[header_idx]]
    header_row = _dedup_columns(header_row)
    grid = [header_row] + data_rows
    grid_raw = [list(r) for r in grid]

    # 单位嗅探 + 万元换算
    unit = detect_table_unit(grid)
    if not unit:
        text = " ".join(str(log) for _, _, log in page_dfs if isinstance(log, str))
        if text:
            unit = sniff_unit_from_text(text)
    if unit and unit != "万元":
        grid = convert_table_rows_to_wan(grid, unit)
        if unit in {"元", "千元"}:
            unit = "万元"

    pages = [p for p, _, _ in page_dfs]
    df_captured = pd.DataFrame([list(r) for r in grid[1:]], columns=grid[0])

    return FlexibleTable(
        table_id=table_match.table_id,
        table_name=table_match.table_name,
        page=pages[0] if pages else 1,
        source_pages=pages,
        rows=grid,
        to_frame=lambda *a, _df=df_captured, **kw: _df,
        strategy="批量",
        quality_score=1.0,
        evidence="",
        table_config=sanitize_tree(table_config) if isinstance(table_config, dict) else None,
        unit=unit or "未识别",
        unit_source=unit or "未识别",
        source_grid_raw=grid_raw,
        unit_records=[],
    )


def locate_one_pdf_bytes(
    company_name: str,
    pdf_bytes: bytes,
    ctx: BatchContext,
) -> dict:
    """只跑定位（STEP1），不提取也不标准化，返回结果供人工确认。"""
    result = {
        "公司": company_name,
        "状态": "",
        "matches": [],
        "说明": "",
    }
    try:
        matches, locator_message = locate_tables_hybrid(
            pdf_bytes,
            ctx.feature_config,
            api_key=ctx.api_key,
            base_url=ctx.base_url,
            model=ctx.model,
            profile_context=ctx.profile.locator_context(),
        )
        result["matches"] = list(matches)
        result["状态"] = "成功"
        result["说明"] = locator_message
    except Exception as exc:
        result["状态"] = "异常"
        result["说明"] = str(exc)[:300]
        result["traceback"] = traceback.format_exc()
    return result


def process_one_pdf_bytes(
    company_name: str,
    pdf_bytes: bytes,
    ctx: BatchContext,
    page_overrides: dict[str, list[int]] | None = None,
) -> dict:
    """处理单份 PDF，返回结果字典（含 workbook_bytes）。"""
    result = {
        "公司": company_name,
        "状态": "",
        "定位表数": 0,
        "提取表数": 0,
        "窄表记录数": 0,
        "workbook_bytes": b"",
        "说明": "",
    }

    try:
        # STEP1：定位
        matches, _ = locate_tables_hybrid(
            pdf_bytes,
            ctx.feature_config,
            api_key=ctx.api_key, base_url=ctx.base_url, model=ctx.model,
            profile_context=ctx.profile.locator_context(),
        )
        # ★ 新增：用外部传入的页码覆盖自动定位结果（批量两阶段流程）
        if page_overrides:
            _new_matches = []
            for _m in matches:
                _ovr = page_overrides.get(_m.table_name)
                if _ovr is not None:
                    _new_matches.append(PageMatch(
                        table_id=_m.table_id,
                        table_name=_m.table_name,
                        pages=list(_ovr),
                        score=_m.score,
                        evidence="人工确认",
                        review_required=False,
                        review_reason="",
                        sources=_m.sources,
                        strategy_id=_m.strategy_id,
                        table_config=_m.table_config,
                        # 人工确认页码是最高优先级证据，保留自动定位的
                        # 原始置信度供结果表展示，依据注明为人工确认。
                        confidence=_m.confidence,
                        confidence_basis="人工确认页码",
                    ))
                else:
                    _new_matches.append(_m)
            matches = _new_matches
        located = [m for m in matches if m.pages]
        result["定位表数"] = len(located)
        conflicts = [m for m in matches if m.review_required]
        if conflicts:
            result["说明"] += f"定位冲突 {len(conflicts)} 张；"
        if not located:
            result["状态"] = "失败"
            result["说明"] += "定位不到任何目标表。"
            return result

        # STEP2：逐表逐页提取
        config_by_id = {item["table_id"]: item for item in ctx.table_configs}
        raw_tables = []
        for m in located:
            page_dfs = []
            for page in m.pages:
                if ctx.use_vision:
                    df, log = extract_single_page_vision(
                        pdf_bytes, page, m.table_name,
                        ctx.api_key, ctx.base_url, ctx.model,
                    )
                else:
                    df, log = extract_single_page(
                        pdf_bytes, page, m.table_name,
                        ctx.api_key, ctx.base_url, ctx.model,
                    )
                if df is not None and not df.empty:
                    page_dfs.append((page, df, log))
            table = _merge_pages_to_table(m, page_dfs, config_by_id.get(m.table_id))
            if table is not None:
                raw_tables.append(table)
        result["提取表数"] = len(raw_tables)
        if not raw_tables:
            result["状态"] = "失败"
            result["说明"] += "未提取到任何表。"
            return result

        # STEP3：标准化
        company_type = ctx.company_type_map.get(company_name, ctx.default_company_type)
        metadata = sanitize_tree({
            **extract_report_metadata(pdf_bytes),
            "来源文件": f"{company_name}.pdf",
            "公司": company_name,
            "导入批次": f"{company_name}:batch",
        })
        std_result = standardize_to_target(
            raw_tables,
            ctx.taxonomy,
            metadata,
            company_type,
            ctx.target_catalog,
            report_profile_id=ctx.profile.profile_id,
            allowed_company_types=ctx.profile.company_types,
            peer_group_map=ctx.peer_group_map,
            default_peer_group=ctx.default_peer_group,
            company_type_map=ctx.company_type_map,
            include_derived=ctx.include_derived,
        )

        # ★ 去重：消除 S02/S05 明细表对核心指标的重复
        try:
            import dataclasses
            if dataclasses.is_dataclass(std_result):
                std_result = dataclasses.replace(
                    std_result,
                    data=_dedupe_by_source_priority(std_result.data),
                )
            else:
                std_result.data = _dedupe_by_source_priority(std_result.data)
        except Exception:
            pass  # 保底：去重失败不阻断流程

        # ================================================================
        # ★ 新增：自动补注册资本（批量）
        # ================================================================
        try:
            # ★ 改成相对导入：不管 services 是不是顶层包都能找到。
            #   绝对导入 "services.registered_capital_extractor" 在 Streamlit
            #   多线程运行时可能失败，报 "No module named ..."。
            from .registered_capital_extractor import append_registered_capital
            _pdf_text = ""
            try:
                import fitz as _fitz
                _doc = _fitz.open(stream=pdf_bytes, filetype="pdf")
                try:
                    _pdf_text = "\n".join((p.get_text() or "") for p in _doc)
                finally:
                    _doc.close()
            except Exception:
                _pdf_text = ""

            _report_period = str(metadata.get("报告期", "")).strip()
            if not _report_period:
                _report_period = (
                    f"{metadata.get('报告年度', '')}{metadata.get('报告季度', '')}"
                    or "2025Q4"
                )

            _current_data = getattr(std_result, "data", None)
            _patched_data = append_registered_capital(
                _current_data,
                pdf_text=_pdf_text,
                pdf_bytes=pdf_bytes,
                pdf_name=f"{company_name}.pdf",
                company_name=company_name,
                report_period=_report_period,
                company_type=company_type,
            )

            # 把补好的数据写回 std_result
            try:
                import dataclasses
                if dataclasses.is_dataclass(std_result):
                    std_result = dataclasses.replace(std_result, data=_patched_data)
                else:
                    std_result.data = _patched_data
            except Exception:
                std_result.data = _patched_data

            # ★ 新增：重算 target_summary，让 REGISTERED_CAPITAL 出现在“填报汇总”里。
            #   原因：_target_summary 只按 target_catalog 里的行去统计，
            #   REGISTERED_CAPITAL 不在 target_catalog 里；不重算的话，
            #   即使 data 里已经有这条记录，填报汇总也依然显示“未填报”。
            try:
                import dataclasses as _dc
                from services.solvency_step3_standardizer import _target_summary
                _new_summary = _target_summary(
                    std_result.target_catalog,
                    _patched_data,
                )
                if _dc.is_dataclass(std_result):
                    std_result = _dc.replace(std_result, target_summary=_new_summary)
                else:
                    std_result.target_summary = _new_summary
            except Exception as _sum_exc:
                result["说明"] += f"（填报汇总重算失败：{_sum_exc}）"
        except Exception as _cap_exc:
            # 注册资本补录失败不阻断流程，仅记录到说明里
            result["说明"] += f"（注册资本自动补录失败：{_cap_exc}）"

        result["状态"] = "成功"
        result["窄表记录数"] = len(std_result.data)
        result["workbook_bytes"] = result_workbook_bytes(std_result)
        result["说明"] += f"窄表 {len(std_result.data)} 条。"
    except Exception as exc:
        result["状态"] = "异常"
        result["说明"] = str(exc)[:300]
        result["traceback"] = traceback.format_exc()
    return result
