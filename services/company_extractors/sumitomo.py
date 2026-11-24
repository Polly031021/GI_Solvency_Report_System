"""三井住友（三井住友海上火灾保险（中国）有限公司）专属后处理。

该报告的表格线框把最左“指标名称/项目”标签列切掉或切碎：主要经营指标、
流动性风险监测指标提取后只剩数值列，最低资本表被拆成若干残行。这里不再
依赖线框网格，而是用 PyMuPDF 的文本行，按小节标题定位数据区，把
“行标签 + 数值列”重新拼回规范表。

只处理本公司在 YAML 中定位到的六张表；其余表原样返回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from . import register

_PAGE_NUMBER_RE = re.compile(r"^\d{1,3}$")
_NUMBERING_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_TOP_LEVEL_RE = re.compile(r"^[一二三四五六七八九十]{1,3}、")
_SECTION_RE = re.compile(r"^[（(][一二三四五六七八九十]{1,3}[）)]")
_VALUE_RE = re.compile(
    r"^(?:[-—–－]{1,3}|不适用|未披露|N/?A|[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)$",
    re.IGNORECASE,
)
_HEADER_CELLS = frozenset(
    {
        "指标名称",
        "项目",
        "行次",
        "序号",
        "本季度",
        "上季度",
        "期末数",
        "期初数",
        "下季度预测数",
        "本年度累计数",
        "本年累计数",
        "本季度数",
        "上季度数",
        "本季度末数",
        "上季度末数",
        "本季度（末）数",
        "下季度末预测数",
        "数值",
    }
)


@dataclass(frozen=True)
class _SectionSpec:
    """一张目标表在 PDF 文本里的重建规格。"""

    start_markers: tuple[str, ...]
    stop_markers: tuple[str, ...]
    header: tuple[str, ...]
    columns: int
    unit: str


_SECTIONS: dict[str, _SectionSpec] = {
    "SOLVENCY_MAIN": _SectionSpec(
        start_markers=("（一）偿付能力充足率", "(一)偿付能力充足率", "（一） 偿付能力充足率"),
        stop_markers=(
            "流动性风险监管指标",
            "流动性风险监测指标",
            "主要经营指标",
            "风险管理能力",
        ),
        header=("指标名称", "本季度末数", "上季度末数", "下季度末预测数"),
        columns=3,
        unit="元",
    ),
    "ACTUAL_CAPITAL": _SectionSpec(
        start_markers=("实际资本指标", "实际资本明细表", "实际资本汇总"),
        stop_markers=(
            "最低资本指标",
            "保费准备金风险最低资本指标",
            "认可资产指标",
            "认可负债指标",
        ),
        header=("指标名称", "本季度末数", "上季度末数"),
        columns=2,
        unit="元",
    ),
    "MINIMUM_CAPITAL": _SectionSpec(
        start_markers=("最低资本指标", "最低资本明细表", "最低资本汇总"),
        stop_markers=("保费准备金风险最低资本指标", "（二）保费准备金"),
        header=("指标名称", "本季度末数", "上季度末数"),
        columns=2,
        unit="元",
    ),
    "OPERATING_METRICS": _SectionSpec(
        start_markers=("主要经营指标",),
        stop_markers=(
            "近三年（综合）投资收益率",
            "近三年(综合)投资收益率",
            "近三年平均投资收益率",
            "风险管理能力",
        ),
        header=("指标名称", "本季度数", "本年累计数"),
        columns=2,
        unit="元",
    ),
    "THREE_YEAR_INVESTMENT_RETURN": _SectionSpec(
        start_markers=(
            "近三年（综合）投资收益率",
            "近三年(综合)投资收益率",
            "近三年平均投资收益率",
        ),
        stop_markers=("风险管理能力", "主要经营指标"),
        header=("指标名称", "近三年平均"),
        columns=1,
        unit="%",
    ),
    "LIQUIDITY_MONITORING": _SectionSpec(
        start_markers=("流动性风险监测指标",),
        stop_markers=("主要经营指标", "近三年（综合）投资收益率", "风险管理能力"),
        header=("指标名称", "本季度数", "上季度数"),
        columns=2,
        unit="元",
    ),
}
_SECTIONS["NON_LIFE_OPERATING_METRICS"] = _SECTIONS["OPERATING_METRICS"]


def _normalize(line: str) -> str:
    return re.sub(r"\s+", "", str(line or ""))


def _page_lines(document, page_number: int) -> list[str]:
    """返回某物理页的文本行，去掉页眉页码。"""

    text = document[page_number - 1].get_text("text") or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and _PAGE_NUMBER_RE.match(_normalize(lines[0])):
        lines = lines[1:]
    return lines


def _is_noise(normalized: str) -> bool:
    if not normalized:
        return True
    if normalized.startswith(("注", "注：", "注:", "＊", "*", "【")):
        return True
    if "偿付能力报告" in normalized and "摘要" in normalized:
        return True
    if normalized.startswith("三井住友海上火灾保险"):
        return True
    return False


def _is_table_meta(normalized: str) -> bool:
    if normalized in _HEADER_CELLS:
        return True
    if normalized.startswith("单位") or "（单位：" in normalized or "(单位：" in normalized:
        return True
    return False


def _parse_section(document, spec: _SectionSpec, first_page: int, last_page: int):
    """在 [first_page, last_page] 内重建一张表，返回 (rows, start_page, end_page)。"""

    rows: list[list[str]] = []
    label_parts: list[str] = []
    values: list[str] = []
    started = False
    start_page = 0
    end_page = 0

    for page_number in range(first_page, last_page + 1):
        for line in _page_lines(document, page_number):
            normalized = _normalize(line)
            if not normalized:
                continue

            if not started:
                if any(marker in normalized for marker in spec.start_markers):
                    started = True
                    start_page = page_number
                continue

            if any(marker in normalized for marker in spec.stop_markers) or _TOP_LEVEL_RE.match(
                normalized
            ):
                return rows, start_page, end_page or page_number

            if _is_noise(normalized):
                continue
            if _is_table_meta(normalized):
                label_parts, values = [], []
                continue
            if _SECTION_RE.match(normalized):
                # 小节标题（一）/（二）…：数据区继续，标签缓冲区清空
                label_parts, values = [], []
                continue
            if _NUMBERING_RE.match(normalized) and not label_parts:
                # 独立成行的“行次”列，丢弃
                continue
            if _VALUE_RE.match(normalized):
                if not label_parts:
                    # 行次/孤立数值（上一行已 flush 完）
                    continue
                values.append(normalized)
                if len(values) >= spec.columns:
                    rows.append(["".join(label_parts), *values[: spec.columns]])
                    label_parts, values = [], []
                    end_page = page_number
                continue

            if values:
                # 数值不足列数又出现文字：按原行列出的列数收下，再开新行
                rows.append(["".join(label_parts), *values])
                label_parts, values = [], []
            if label_parts:
                label_parts = [label_parts[0] + normalized]
            else:
                label_parts = [normalized]

    return rows, start_page, end_page or start_page


def _window(table, page_count: int) -> tuple[int, int]:
    pages = [int(p) for p in (getattr(table, "source_pages", None) or []) if p]
    page = getattr(table, "page", None)
    if page and not pages:
        pages = [int(page)]
    if not pages:
        return 1, page_count
    return max(1, min(pages) - 2), min(page_count, max(pages) + 2)


def _apply_table(table, rows: Sequence[Sequence[str]], start_page: int, end_page: int, unit: str) -> None:
    rebuilt = [list(rows[0]), *[list(row) for row in rows[1:]]]
    table.rows = rebuilt
    table.table_blocks = [
        {
            "page": int(start_page or 1),
            "rows": rebuilt,
            "unit": unit,
        }
    ]
    pages = sorted({int(start_page or 1), int(end_page or start_page or 1)})
    table.page = pages[0]
    table.source_pages = pages
    table.unit = unit
    table.unit_source = "PDF 原文单位"
    table.source_grid_raw = rebuilt


@register("sumitomo")
def postprocess(tables, *, pdf_bytes):
    """按 PDF 文本行重建三井住友六张目标表；失败的表保持通用提取结果。"""

    if not pdf_bytes:
        return tables
    if not any(getattr(table, "table_id", "") in _SECTIONS for table in tables):
        return tables

    import pymupdf as fitz

    document = None
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = document.page_count
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            spec = _SECTIONS.get(table_id)
            if spec is None:
                continue
            first, last = _window(table, page_count)
            data, start_page, end_page = _parse_section(document, spec, first, last)
            if len(data) < 2:
                data, start_page, end_page = _parse_section(document, spec, 1, page_count)
            if len(data) < 2:
                continue
            rebuilt = [[*spec.header], *data]
            _apply_table(table, rebuilt, start_page, end_page, spec.unit)
    finally:
        if document is not None:
            document.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("sumitomo", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "sumitomo",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
