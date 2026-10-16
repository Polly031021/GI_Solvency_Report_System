"""STEP2 逐页提取引擎路由：文字版页面走规则法，扫描版页面走大模型。

设计目标与 STEP1 定位一致——能用确定性规则解决的部分不调用模型：

* **规则法**：用 pdfplumber 的线框识别 / 文字对齐识别离线重建目标表，
  零 token、结果可复现；随后复用 STEP1 已有的逐页完整性校验器做质量门禁。
* **大模型法**：仅扫描版页面（无文字层）或规则法未通过校验的页面调用，
  与原有提示词、重试链和返回格式完全一致。

本模块不依赖 Streamlit，可被界面层与批处理脚本共用。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Mapping

import pandas as pd
import pdfplumber

from .batch_extraction import (
    extract_single_page,
    extract_single_page_vision,
)
from .solvency_ai_table_extractor import (
    ExtractionQualityError,
    PageGrid,
)
from .solvency_hybrid_pipeline.page_extract import _validate_single_page
from .solvency_table_region import TableRegion, locate_table_region
from .solvency_table_boundaries import enforce_output_boundaries
from .solvency_table_extractor import (
    TABLE_HEADERS,
    TABLE_SIGNATURES,
    page_table_candidates,
)
from .table_number_repair import NumberRepairOutcome, repair_thousand_separators
from .table_strategy_handlers import BoundaryRequest
from .table_strategy_registry import resolve_table_strategy


# ---------- 提取方式（界面层下拉选项） ----------
MODE_AUTO = "智能：文字版页走规则法，扫描版页走大模型"
MODE_RULE = "全部规则法（离线，零模型调用）"
MODE_LLM = "全部大模型法（文本优先，失败转图片）"
MODE_VISION = "全部图片视觉法"
METHOD_OPTIONS = (MODE_RULE, MODE_AUTO, MODE_LLM, MODE_VISION)
DEFAULT_METHOD = MODE_RULE

# ---------- 引擎标识与日志用标签 ----------
ENGINE_RULE = "rule"
ENGINE_LLM_TEXT = "llm_text"
ENGINE_LLM_VISION = "llm_vision"
ENGINE_LABELS = {
    ENGINE_RULE: "规则法",
    ENGINE_LLM_TEXT: "大模型文本",
    ENGINE_LLM_VISION: "大模型视觉",
}

# 规则法未产出结果时的统一前缀，界面层据此区分“规则法未命中”与“模型调用失败”。
RULE_FAILURE_PREFIX = "规则法未能完成本页提取："

# 表头最多出现在表格前 8 行内（章节标题、续表标记之后）。
_HEADER_SEARCH_LIMIT = 8


@dataclass
class PageExtractionOutcome:
    """一页的提取结果：数据、所用引擎、状态与日志说明。"""

    frame: pd.DataFrame | None = None
    engine: str = ""
    status: str = "失败"
    note: str = ""
    repairs: tuple[str, ...] = ()
    reviews: tuple[str, ...] = ()
    region: TableRegion | None = None

    @property
    def mode_label(self) -> str:
        return ENGINE_LABELS.get(self.engine, "未识别")

    @property
    def succeeded(self) -> bool:
        return self.frame is not None and not self.frame.empty


def credentials_ready(api_key: str, base_url: str, model: str) -> bool:
    """判断模型接口三要素是否齐全。"""
    return all(str(value or "").strip() for value in (api_key, base_url, model))


def page_source_text(pdf_bytes: bytes, page_number: int) -> str:
    """取单页原文；用于千分位修复的佐证。"""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if 1 <= page_number <= len(pdf.pages):
                return pdf.pages[page_number - 1].extract_text() or ""
    except Exception:
        return ""
    return ""


def page_has_searchable_text(grid: PageGrid | None, minimum_words: int = 6) -> bool:
    """文字层是否足以支撑规则法（与既有低文字量阈值口径一致）。"""
    if grid is None:
        return False
    return int(getattr(grid, "word_count", 0) or 0) >= minimum_words


def _target_terms(
    table_id: str,
    table_config: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """目标表的内容关键词：优先 Profile 配置，其次内置表特征词。"""
    config = dict(table_config or {})
    configured = tuple(
        str(item)
        for item in (
            config.get("content_terms")
            or config.get("completeness_terms")
            or ()
        )
        if str(item).strip()
    )
    if configured:
        return configured
    return tuple(TABLE_SIGNATURES.get(table_id, ()))


def _matches_target_terms(
    table_id: str,
    rows: list[list[str]],
    table_config: Mapping[str, object] | None,
) -> tuple[bool, str]:
    """确认规则法选中的表确实是本目标表（同页可能存在多张表）。"""
    terms = _target_terms(table_id, table_config)
    if not terms:
        return True, ""
    flat = "".join(str(cell) for row in rows for cell in row).replace(" ", "")
    hits = [term for term in terms if term.replace(" ", "") in flat]
    if hits:
        return True, "、".join(dict.fromkeys(hits))[:60]
    return False, f"规则法选中的表不含目标表关键词（{ '、'.join(terms[:5]) }）"


def _header_hints(
    table_id: str,
    table_config: Mapping[str, object] | None,
) -> tuple[str, ...]:
    config = dict(table_config or {})
    configured = tuple(str(item) for item in config.get("header_terms", ()) or ())
    if configured:
        return configured
    return TABLE_HEADERS.get(table_id, ()) or (
        "指标名称",
        "项目",
        "行次",
        "序号",
        "本季度",
        "上季度",
        "期末",
        "期初",
        "本年",
    )


def _trim_leading_title_rows(
    table_id: str,
    rows: list[list[str]],
    table_config: Mapping[str, object] | None = None,
) -> list[list[str]]:
    """丢掉表头之上的章节标题行，避免标题被当成第一条数据。"""
    hints = _header_hints(table_id, table_config)
    for index, row in enumerate(rows[:_HEADER_SEARCH_LIMIT]):
        text = "".join(str(cell) for cell in row)
        if sum(1 for hint in hints if hint and hint in text) >= 2:
            return rows[index:]
    return rows


def _trim_to_boundary(
    table_id: str,
    rows: list[list[str]],
    table_config: Mapping[str, object] | None = None,
) -> tuple[list[list[str]], str]:
    """按目标表首尾业务项裁掉同页相邻表的内容；失败不阻断。"""
    try:
        strategy = resolve_table_strategy(table_id, None, table_config)
        return strategy.enforce_boundaries(
            BoundaryRequest(
                mode="output_rows",
                table_id=table_id,
                rows=rows,
                require_complete=False,
                engine=enforce_output_boundaries,
            )
        )
    except Exception as exc:
        return rows, f"边界裁剪已跳过（{exc}）"


def _apply_number_repair(
    frame: pd.DataFrame,
    source_text: str,
) -> tuple[pd.DataFrame, tuple[str, ...], tuple[str, ...]]:
    """对大模型返回的数值做千分位修复，并回传修复/待核对说明。"""
    if frame is None or frame.empty:
        return frame, (), ()
    outcome = repair_thousand_separators(
        frame.fillna("").astype(str).values.tolist(),
        source_text,
    )
    if not outcome.changed:
        return frame, outcome.repairs, outcome.reviews
    return pd.DataFrame(outcome.rows), outcome.repairs, outcome.reviews


@dataclass
class RuleCandidate:
    """一个通过结构准备（裁切/边界裁剪/关键词核对/数值校正）的规则法候选表。"""

    frame: pd.DataFrame
    note: str
    repair: NumberRepairOutcome
    region: TableRegion | None = None

    @property
    def rows(self) -> list[list[str]]:
        return self.frame.fillna("").astype(str).values.tolist()


def _prepare_candidate(
    table_id: str,
    table_name: str,
    rows: list[list[str]],
    strategy: str,
    quality_score: float,
    evidence: str,
    table_config: Mapping[str, object] | None,
    source_text: str,
    region: TableRegion | None,
) -> tuple[RuleCandidate | None, str]:
    """把原始候选行整理成可用候选：去标题行 → 边界裁剪 → 关键词核对 → 数值校正。"""
    if len(rows) < 2:
        return None, "规则法仅识别到表头，未取得数据行"
    rows = _trim_leading_title_rows(table_id, rows, table_config)
    rows, boundary_note = _trim_to_boundary(table_id, rows, table_config)
    if len(rows) < 2:
        return None, "规则法结果在边界裁剪后没有数据行"
    matched, match_note = _matches_target_terms(table_id, rows, table_config)
    if not matched:
        return None, match_note
    repair = repair_thousand_separators(rows, source_text)
    note = (
        f"规则法（{strategy}｜结构评分{quality_score:.1f}｜{evidence}）"
        f"识别到{len(repair.rows) - 1}条数据行"
    )
    if match_note:
        note += f"；命中目标表关键词：{match_note}"
    if boundary_note:
        note += f"；{boundary_note}"
    if region is not None:
        note = f"{region.note}；{note}"
    return (
        RuleCandidate(
            frame=pd.DataFrame(repair.rows),
            note=note,
            repair=repair,
            region=region,
        ),
        "",
    )


def prepare_rule_candidates(
    pdf_bytes: bytes,
    page_number: int,
    table_id: str,
    table_name: str,
    *,
    table_config: Mapping[str, object] | None = None,
    source_text: str = "",
) -> tuple[list[RuleCandidate], str]:
    """准备本页所有可用的规则法候选（页内区域优先，整页兜底）。

    一页多表时，优先用 Profile 配置的首尾项目把页面裁切到目标表区域，
    可以彻底避免把相邻表读进来；区域定位失败时退回整页候选。
    """
    config = dict(table_config or {})
    region = locate_table_region(pdf_bytes, page_number, table_id, config)

    prepared: list[RuleCandidate] = []
    reasons: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                return [], "页码超出 PDF 范围"
            page = pdf.pages[page_number - 1]
            # 同页原文只在需要时取一次，复用已打开的页面对象，避免重复解析 PDF
            text = source_text
            if not text:
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
            attempts: list[tuple[str, list]] = []
            if region is not None:
                attempts.append(("页内区域", page.crop(region.bbox)))
            attempts.append(("整页", page))
            for scope, target_page in attempts:
                candidates = page_table_candidates(
                    target_page,
                    table_id,
                    table_name,
                    page_number,
                    config,
                )
                for candidate in candidates:
                    scoped_region = region if scope == "页内区域" else None
                    entry, reason = _prepare_candidate(
                        table_id,
                        table_name,
                        [list(row) for row in candidate.rows],
                        f"{scope}·{candidate.strategy}",
                        float(candidate.quality_score),
                        str(candidate.evidence or ""),
                        config,
                        text,
                        scoped_region,
                    )
                    if entry is not None:
                        prepared.append(entry)
                    else:
                        reasons.append(f"{scope}：{reason}")
    except Exception as exc:
        return [], f"规则法解析本页失败：{exc}"

    if prepared:
        return prepared, ""
    if reasons:
        return [], "；".join(dict.fromkeys(reasons))[:300]
    return [], "规则法在本页未识别到符合目标表特征的表结构"


def extract_single_page_rule(
    pdf_bytes: bytes,
    page_number: int,
    table_id: str,
    table_name: str,
    *,
    table_config: Mapping[str, object] | None = None,
    source_text: str = "",
) -> tuple[pd.DataFrame | None, str, NumberRepairOutcome]:
    """规则法提取单页目标表；返回 ``(数据框, 说明, 数值修复结果)``。"""
    candidates, reason = prepare_rule_candidates(
        pdf_bytes,
        page_number,
        table_id,
        table_name,
        table_config=table_config,
        source_text=source_text,
    )
    if not candidates:
        return None, reason, NumberRepairOutcome(rows=[])
    best = candidates[0]
    return best.frame, best.note, best.repair


def validate_rule_page_rows(
    table_id: str,
    rows: list[list[str]],
    grid: PageGrid | None,
    *,
    table_config: Mapping[str, object] | None = None,
    strategy_id: str | None = None,
) -> tuple[bool, str]:
    """用 STEP1 的逐页完整性校验器给规则法结果做质量门禁。"""
    if grid is None or not page_has_searchable_text(grid):
        return True, "本页无可用文字网格，规则法结果未做完整性校验"
    try:
        strategy = resolve_table_strategy(table_id, strategy_id, table_config)
        strategy.run(
            "extract_page",
            _validate_single_page,
            rows,
            grid,
            0.0,
            inject_table_id="positional",
        )
    except ExtractionQualityError as exc:
        return False, str(exc)
    except Exception as exc:  # 校验器自身异常不应误伤规则法结果
        return True, f"完整性校验未完成，已按规则结果放行：{exc}"
    return True, "已通过逐页完整性校验"


def _rule_attempt(
    *,
    pdf_bytes: bytes,
    page_number: int,
    table_id: str,
    table_name: str,
    table_config: Mapping[str, object] | None,
    strategy_id: str | None,
    grid: PageGrid | None,
) -> PageExtractionOutcome:
    candidates, reason = prepare_rule_candidates(
        pdf_bytes,
        page_number,
        table_id,
        table_name,
        table_config=table_config,
        source_text="",
    )
    if not candidates:
        return PageExtractionOutcome(
            engine=ENGINE_RULE,
            status="失败",
            note=f"{RULE_FAILURE_PREFIX}{reason}",
        )
    first_gate = ""
    for candidate in candidates:
        passed, validation_note = validate_rule_page_rows(
            table_id,
            candidate.rows,
            grid,
            table_config=table_config,
            strategy_id=strategy_id,
        )
        if passed:
            return PageExtractionOutcome(
                frame=candidate.frame,
                engine=ENGINE_RULE,
                status="成功",
                note=f"{candidate.note}；{validation_note}",
                repairs=candidate.repair.repairs,
                reviews=candidate.repair.reviews,
                region=candidate.region,
            )
        if not first_gate:
            first_gate = validation_note
    return PageExtractionOutcome(
        engine=ENGINE_RULE,
        status="失败",
        note=(
            f"{RULE_FAILURE_PREFIX}{len(candidates)}个候选表均未通过完整性校验"
            f"（{first_gate or '无有效候选'}）"
        ),
        repairs=candidates[0].repair.repairs,
        reviews=candidates[0].repair.reviews,
    )


def _llm_text_attempt(
    *,
    pdf_bytes: bytes,
    page_number: int,
    table_name: str,
    api_key: str,
    base_url: str,
    model: str,
    source_text: str = "",
) -> PageExtractionOutcome:
    frame, log = extract_single_page(
        pdf_bytes,
        page_number,
        table_name,
        api_key,
        base_url,
        model,
    )
    if frame is None or frame.empty:
        return PageExtractionOutcome(
            engine=ENGINE_LLM_TEXT,
            status="失败",
            note=f"文本模式未返回数据：{str(log)[:180] or '无返回'}",
        )
    frame, repairs, reviews = _apply_number_repair(frame, source_text)
    return PageExtractionOutcome(
        frame=frame,
        engine=ENGINE_LLM_TEXT,
        status="成功",
        note=f"文本模式提取到{len(frame)}行",
        repairs=repairs,
        reviews=reviews,
    )


def _llm_vision_attempt(
    *,
    pdf_bytes: bytes,
    page_number: int,
    table_id: str = "",
    table_name: str,
    api_key: str,
    base_url: str,
    model: str,
    source_text: str = "",
    region: TableRegion | None = None,
    grid: PageGrid | None = None,
    table_config: Mapping[str, object] | None = None,
    strategy_id: str | None = None,
) -> PageExtractionOutcome:
    def call(bbox, region_note: str) -> PageExtractionOutcome:
        frame, log = extract_single_page_vision(
            pdf_bytes,
            page_number,
            table_name,
            api_key,
            base_url,
            model,
            bbox=bbox,
            region_note=region_note,
        )
        if frame is None or frame.empty:
            return PageExtractionOutcome(
                engine=ENGINE_LLM_VISION,
                status="失败",
                note=f"图片模式未返回数据：{str(log)[:180] or '无返回'}",
            )
        frame, repairs, reviews = _apply_number_repair(frame, source_text)
        return PageExtractionOutcome(
            frame=frame,
            engine=ENGINE_LLM_VISION,
            status="成功",
            note=f"图片模式提取到{len(frame)}行",
            repairs=repairs,
            reviews=reviews,
        )

    fallback_reason = ""
    if region is not None and table_id:
        cropped = call(region.bbox, region.note)
        if cropped.succeeded:
            passed, gate_note = validate_rule_page_rows(
                table_id,
                cropped.frame.fillna("").astype(str).values.tolist(),
                grid,
                table_config=table_config,
                strategy_id=strategy_id,
            )
            if passed:
                cropped.note = f"{region.note}；已按区域裁切识别（{cropped.note}）；{gate_note}"
                cropped.region = region
                return cropped
            fallback_reason = f"页内区域裁切结果未通过完整性校验（{gate_note}），已回退整页识别"
        else:
            fallback_reason = "页内区域裁切识别失败，已回退整页识别"

    full = call(None, "")
    if fallback_reason and full.succeeded:
        full.note = f"{fallback_reason}；{full.note}"
    return full


def extract_page_with_method(
    *,
    pdf_bytes: bytes,
    page_number: int,
    table_id: str,
    table_name: str,
    method: str = DEFAULT_METHOD,
    grid: PageGrid | None = None,
    has_text: bool | None = None,
    table_config: Mapping[str, object] | None = None,
    strategy_id: str | None = None,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    auto_vision_retry: bool = True,
    allow_llm_fallback: bool = True,
) -> PageExtractionOutcome:
    """按选定方式处理单页，必要时在规则法与大模型之间自动切换。"""
    ready = credentials_ready(api_key, base_url, model)
    if has_text is None:
        has_text = page_has_searchable_text(grid)
    cached_source: list[str] = []
    cached_region: list[TableRegion | None] = []

    def source() -> str:
        if not cached_source:
            cached_source.append(page_source_text(pdf_bytes, page_number))
        return cached_source[0]

    def region() -> TableRegion | None:
        """目标表的页内区域（最多定位一次）；区域用于裁切图片，避免读到相邻表。"""
        if not cached_region:
            cached_region.append(
                locate_table_region(
                    pdf_bytes,
                    page_number,
                    table_id,
                    dict(table_config or {}),
                )
            )
        return cached_region[0]

    def vision() -> PageExtractionOutcome:
        return _llm_vision_attempt(
            pdf_bytes=pdf_bytes,
            page_number=page_number,
            table_id=table_id,
            table_name=table_name,
            api_key=api_key,
            base_url=base_url,
            model=model,
            source_text=source(),
            region=region(),
            grid=grid,
            table_config=table_config,
            strategy_id=strategy_id,
        )

    if method == MODE_VISION:
        if not ready:
            return PageExtractionOutcome(
                engine=ENGINE_LLM_VISION,
                note="图片识别需要 AI 配置：请在登录页填写 Base URL、模型与 API Key。",
            )
        return vision()

    if method == MODE_LLM:
        if not ready:
            return PageExtractionOutcome(
                engine=ENGINE_LLM_TEXT,
                note="大模型法需要 AI 配置：请在登录页填写 Base URL、模型与 API Key。",
            )
        attempts: list[str] = []
        if has_text:
            text_outcome = _llm_text_attempt(
                pdf_bytes=pdf_bytes,
                page_number=page_number,
                table_name=table_name,
                api_key=api_key,
                base_url=base_url,
                model=model,
                source_text=source(),
            )
            if text_outcome.succeeded:
                return text_outcome
            attempts.append(text_outcome.note)
        else:
            attempts.append("本页无可检索文字层")
        if auto_vision_retry:
            vision_outcome = vision()
            if vision_outcome.succeeded:
                vision_outcome.note = "；".join([*attempts, vision_outcome.note])
                return vision_outcome
            attempts.append(vision_outcome.note)
        return PageExtractionOutcome(
            engine=ENGINE_LLM_TEXT if has_text else ENGINE_LLM_VISION,
            note="；".join(attempts) or "未提取到数据",
        )

    if method == MODE_RULE:
        if not has_text:
            return PageExtractionOutcome(
                engine=ENGINE_RULE,
                note="本页无可检索文字层，规则法不适用于扫描版页面。",
            )
        return _rule_attempt(
            pdf_bytes=pdf_bytes,
            page_number=page_number,
            table_id=table_id,
            table_name=table_name,
            table_config=table_config,
            strategy_id=strategy_id,
            grid=grid,
        )

    # ---------- 智能模式：文字版优先规则法，扫描版直接走视觉大模型 ----------
    if has_text:
        rule_outcome = _rule_attempt(
            pdf_bytes=pdf_bytes,
            page_number=page_number,
            table_id=table_id,
            table_name=table_name,
            table_config=table_config,
            strategy_id=strategy_id,
            grid=grid,
        )
        if rule_outcome.succeeded:
            return rule_outcome
        if not allow_llm_fallback or not ready:
            reason = "已关闭模型兜底" if not allow_llm_fallback else "未配置AI接口"
            rule_outcome.note += f"；未调用大模型（{reason}）"
            return rule_outcome
        notes = [rule_outcome.note]
        text_outcome = _llm_text_attempt(
            pdf_bytes=pdf_bytes,
            page_number=page_number,
            table_name=table_name,
            api_key=api_key,
            base_url=base_url,
            model=model,
            source_text=source(),
        )
        if text_outcome.succeeded:
            text_outcome.note = f"{rule_outcome.note}→已转文本大模型；{text_outcome.note}"
            return text_outcome
        notes.append(text_outcome.note)
        if auto_vision_retry:
            vision_outcome = vision()
            if vision_outcome.succeeded:
                vision_outcome.note = "→".join(notes) + f"→已转图片大模型；{vision_outcome.note}"
                return vision_outcome
            notes.append(vision_outcome.note)
        return PageExtractionOutcome(
            engine=ENGINE_LLM_TEXT,
            note="；".join(notes),
            repairs=rule_outcome.repairs,
            reviews=rule_outcome.reviews,
        )

    if not ready:
        return PageExtractionOutcome(
            engine=ENGINE_LLM_VISION,
            note="扫描版页面需要 AI 配置：请在登录页填写 Base URL、模型与 API Key。",
        )
    vision_outcome = vision()
    if vision_outcome.succeeded:
        vision_outcome.note = "扫描版页面直接使用图片识别；" + vision_outcome.note
    return vision_outcome


def preview_page_engine(method: str, *, has_text: bool) -> str:
    """预估某页将使用的引擎，供界面层展示模型调用量预估。"""
    if method == MODE_VISION:
        return ENGINE_LLM_VISION
    if method == MODE_LLM:
        return ENGINE_LLM_TEXT if has_text else ENGINE_LLM_VISION
    if method == MODE_RULE:
        return ENGINE_RULE
    return ENGINE_RULE if has_text else ENGINE_LLM_VISION
