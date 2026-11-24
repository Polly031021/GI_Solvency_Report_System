"""太平科技专属提取后处理：用文本行重建 6 张偿付能力披露表。

太平科技 2022–2024 年报的目标表在版式上有三个通用提取器处理不好的点：

1. 偿付能力主表第 1–8 行的行次与项目名在 PDF 文本层里粘成
   ``1认可资产``，通用规则按“行次/项目”两列解析时会把金额当成项目名；
2. 第 15 页同时排了“流动性风险监测指标”“投资收益率指标”“主要经营
   指标”三张表，pdfplumber 的网格策略会按文本行把它们裂成 6 列；
3. 实际资本明细表跨页（2023/2024），最低资本表被 Profile 区域裁剪
   截断（只剩前 3 行）。

这些页的文字层非常规整：每个单元格独立成行（行次、项目、金额各占一行），
因此这里直接用 PyMuPDF 的 ``get_text`` 文本行重建表格，不再依赖线框网格。
标题在全文档范围内按“标题 + 后续若干行内出现表头”定位，避免命中目录；
所有页码、年份均来自 PDF 本身，不写死。

约定：只改 ``table.rows`` / ``table.table_blocks``，不改 table_id 与 pages；
找不到目标段落时保持原样；重复执行结果一致。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import fitz

from . import register

_ROW_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")

# 金额/比率单元格。刻意排除 `1.2`、`2.4` 这类“一位小数”的写法，因为它们在
# 明细表里正是下一行的行次；本报告的目标表金额均带千分位或至少两位小数/百分号。
# 2023/2024 的“基本每股收益”写成 -0.0148 / 0.0012 这类四位小数，故小数点后
# 允许 2 位以上，否则整行会被当成标签吞掉。
_VALUE_RE = re.compile(
    r"^(?:"
    r"--|—|-|－"
    r"|-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
    r"|-?\d+\.\d{2,}%?"
    r"|-?\d{4,}(?:\.\d+)?%?"
    r")$"
)

_HEADER_HINTS = ("行次", "指标名称", "监测指标")

_SECTIONS: dict[str, dict[str, Any]] = {
    "SOLVENCY_MAIN": {
        "marker": "偿付能力充足率指标",
        "header": ["行次", "项目", "本季度末数", "上季度末数", "下季度末预测数"],
        "width": 3,
        "stop": ("流动性风险监管指标",),
        "max_pages": 1,
    },
    "ACTUAL_CAPITAL": {
        "marker": "核心一级资本调整明细表",
        "header": ["行次", "项目", "本季度末数", "上季度末数"],
        "width": 2,
        "stop": ("最低资本",),
        "max_pages": 3,
        "tail": ("5", "实际资本合计"),
    },
    "MINIMUM_CAPITAL": {
        "marker": "十一、最低资本",
        "header": ["行次", "项目", "本季度末数", "上季度末数"],
        "width": 2,
        "stop": (),
        "max_pages": 2,
        "tail": ("4", "最低资本"),
    },
    "NON_LIFE_OPERATING_METRICS": {
        "marker": "主要经营指标",
        "header": ["指标名称", "本季度数", "本年度累计数"],
        "width": 2,
        "stop": ("五、风险管理能力", "注："),
        "max_pages": 3,
    },
    "LIQUIDITY_MONITORING": {
        "marker": "流动性风险监测指标",
        "header": ["指标名称", "本季度末数", "上季度末数"],
        "width": 2,
        "stop": ("投资收益率指标", "主要经营指标"),
        "max_pages": 1,
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "marker": "投资收益率指标",
        "header": ["指标名称", "指标值"],
        "width": 1,
        "stop": ("主要经营指标",),
        "max_pages": 1,
    },
}


def _compact(text: object) -> str:
    return re.sub(r"[\s·…\.]+", "", str(text or ""))


def _is_value(text: str) -> bool:
    return bool(_VALUE_RE.fullmatch(str(text).strip()))


def _is_row_number(text: str) -> bool:
    """行次形如 1 / 1.2 / 1.2.3 / 1*。

    ``718.69`` 这类两位小数金额也满足数字正则，因此先排除金额形态，
    否则最低资本表里的“汇率风险最低资本 718.69”会被当成一行行次。
    """
    token = str(text).strip()
    return bool(_ROW_NUMBER_RE.fullmatch(token)) and not _is_value(token)


def _page_lines(
    doc: fitz.Document,
    index: int,
    *,
    pdf_bytes: bytes | None = None,
) -> list[str]:
    """取一页的非空文本行，并去掉页首的页码行。"""
    raw = doc.load_page(index).get_text("text")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) < 3 and pdf_bytes:
        from ._ocr import page_cell_lines

        lines = page_cell_lines(pdf_bytes, index + 1)
    if lines and re.fullmatch(r"\d{1,3}", lines[0]):
        following = _compact(lines[1]) if len(lines) > 1 else ""
        if int(lines[0]) == index + 1 or following in {
            "行次", "项目", "指标名称", "监测指标",
        }:
            lines = lines[1:]
    return lines


def _find_section(
    doc: fitz.Document,
    marker: str,
    *,
    pdf_bytes: bytes | None = None,
    preferred_pages: Sequence[int] = (),
) -> tuple[int, int] | None:
    """返回 (页索引, 标题后第一行的索引)。

    目录页也会出现标题文字，因此要求标题后 6 行内出现表头词（行次/项目/
    指标名称/监测指标），目录行的下一行是另一个目录条目，不会满足。
    """
    needle = _compact(marker)
    ordered: list[int] = []
    for page in preferred_pages:
        center = int(page)
        for candidate in range(max(1, center - 2), min(doc.page_count, center + 2) + 1):
            if candidate not in ordered:
                ordered.append(candidate)
    ordered.extend(page for page in range(1, doc.page_count + 1) if page not in ordered)
    for page_number in ordered:
        index = page_number - 1
        lines = _page_lines(doc, index, pdf_bytes=pdf_bytes)
        for position, line in enumerate(lines):
            if needle not in _compact(line):
                continue
            window = [_compact(item) for item in lines[position + 1:position + 7]]
            if any(hint in cell for hint in _HEADER_HINTS for cell in window):
                return index, position + 1
    return None


def _collect(
    doc: fitz.Document,
    page: int,
    start: int,
    stop_terms: Sequence[str],
    max_pages: int,
    *,
    pdf_bytes: bytes | None = None,
) -> list[str]:
    stops = tuple(_compact(term) for term in stop_terms)
    collected: list[str] = []
    for current in range(page, min(doc.page_count, page + max_pages)):
        lines = _page_lines(doc, current, pdf_bytes=pdf_bytes)
        begin = start if current == page else 0
        for line in lines[begin:]:
            compact = _compact(line)
            if any(term and term in compact for term in stops):
                return collected
            collected.append(line)
    return collected


def _parse_numbered(lines: Sequence[str], width: int) -> list[list[str]]:
    """解析带“行次”列的表：行次一行、项目若干行、金额至多 width 个。"""
    rows: list[list[str]] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if not _is_row_number(line):
            index += 1
            continue
        number = line
        index += 1
        labels: list[str] = []
        values: list[str] = []
        while index < total:
            token = lines[index]
            if _is_row_number(token) and not values:
                break
            if _is_value(token) and labels:
                values.append(token)
                index += 1
                if len(values) >= width:
                    break
                continue
            if values:
                break
            if _is_row_number(token):
                break
            labels.append(token)
            index += 1
        if not labels:
            continue
        rows.append([number, "".join(labels), *values])
    return rows


def _parse_labeled(lines: Sequence[str], width: int) -> list[list[str]]:
    """解析无行次列的表：项目一行，紧跟至多 width 个数值行。"""
    rows: list[list[str]] = []
    index = 0
    total = len(lines)
    while index < total:
        label = lines[index]
        if _is_value(label):
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while (
            cursor < total
            and len(values) < width
            and _is_value(lines[cursor])
        ):
            values.append(lines[cursor])
            cursor += 1
        if values:
            rows.append([label, *values])
            index = cursor
        else:
            index += 1
    return rows


def _cut_after(
    rows: list[list[str]], number: str, label: str
) -> list[list[str]]:
    for position, row in enumerate(rows):
        if row[0] == number and row[1].strip() == label:
            return rows[: position + 1]
    return rows


def _rebuild(
    doc: fitz.Document,
    spec: dict[str, Any],
    *,
    pdf_bytes: bytes | None = None,
    preferred_pages: Sequence[int] = (),
) -> tuple[list[list[str]], int] | None:
    found = _find_section(
        doc,
        str(spec["marker"]),
        pdf_bytes=pdf_bytes,
        preferred_pages=preferred_pages,
    )
    if found is None:
        return None
    page, start = found
    lines = _collect(
        doc,
        page,
        start,
        spec.get("stop") or (),
        int(spec.get("max_pages") or 1),
        pdf_bytes=pdf_bytes,
    )
    if spec["marker"] in {"偿付能力充足率指标", "十一、最低资本", "核心一级资本调整明细表"}:
        rows = _parse_numbered(lines, int(spec["width"]))
    else:
        rows = _parse_labeled(lines, int(spec["width"]))
    tail = spec.get("tail")
    if tail:
        rows = _cut_after(rows, str(tail[0]), str(tail[1]))
    if not rows:
        return None
    return [list(spec["header"]), *rows], page + 1


def _sync_blocks(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


@register("taipingkj")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """太平科技专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    if not tables:
        return tables
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            spec = _SECTIONS.get(table_id)
            if spec is None:
                continue
            pages = [
                int(page)
                for page in (getattr(table, "source_pages", None) or [])
                if int(page) > 0
            ]
            rebuilt = _rebuild(
                document,
                spec,
                pdf_bytes=pdf_bytes,
                preferred_pages=pages,
            )
            if rebuilt is None:
                continue
            rows, page = rebuilt
            _sync_blocks(table, rows, page)
            table.source_text_rebuilt = True
    finally:
        document.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("taipingkj", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "taipingkj",
        pdf_bytes,
        table_configs,
        mode="document",
    )
