"""阳光产险专属提取后处理（2022-2025 版式）。

已覆盖的通用规则缺口：

1. ``主要经营指标``：2022-2025 的效益类/规模类指标普遍落在续页，
   续页没有表格线框，pdfplumber 只能取到“指标名称~投资收益率”半张表。
   这里按 PDF 文本行重建“指标名 + 本季度数 + 本年度累计数”。
2. ``实际资本表``：续页（3.6/3.7 → 5 实际资本合计）缺“核心一级资本”
   签名，被 tables.yaml 的 signature 过滤丢掉。这里读取续页整页线框表，
   按行次列拆出“项目/期末数/期初数”续到主表末尾。
3. ``最低资本表`` S05：Profile 终止词“最低资本合计”会先命中
   “寿险业务保险风险最低资本合计”，导致主表只剩表头 4 行。这里按
   整页线框表重建，止于行次为 4 的“最低资本”行。
4. ``近三年投资收益率``：2023 年该表与经营表同页，整页帧会被拼成
   16 行。这里只保留“近三年”开头的行（2 行）。
5. ``流动性风险监测指标``：续页（经营表所在页）的整页帧也含“本季度”
   等表头词，会被当成第二个表块拼进流动性表。该表四年都只占一页
   （11 行），这里截断到第一个分块标记之前。
6. 数值笔误：2024 年报告原文把总资产写成 ``5,890.140.20``（两个小数点，
   结构上不可能是小数）。按 ``services/table_number_repair.py`` 同一判据
   还原千分位为 ``5,890,140.20``，否则该值会被标准化当成文本丢弃。

只改 ``table.rows`` 与 ``table.table_blocks``，不改 table_id / source_pages。
"""

from __future__ import annotations

import io
import re
from typing import Any, Sequence

from . import register

# 与 config/extraction/tables.yaml 的“线框识别”策略保持一致。
_LINE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 4,
    "join_tolerance": 4,
    "intersection_tolerance": 5,
}

_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_MINIMUM_HEADER = ["指标名称", "期末数", "期初数"]

# 数值行：金额（千分位）、百分比、负数、占位符“-”“--”“—”。
_VALUE_RE = re.compile(r"^(?:[-—–]{1,2}|[-+]?\d[\d,.]*%?)$")
# 行次列：1、1.1、1.1.1、1*、3.6。
_ROWNO_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3})*\*?$")
# 千分位逗号被写成小数点：5,890.140.20（结构上不可能是合法小数）。
_MULTI_DOT_RE = re.compile(r"^(\d{1,3}(?:,\d{3})*)\.(\d{3})\.(\d{1,2})$")
_TITLE_STOP = ("注：", "注:", "五、", "（四）近三年", "（三）流动性")
_GROUP_TITLE_TAIL = ("类指标", "主要经营指标")


def _strip(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _is_value(line: str) -> bool:
    return bool(_VALUE_RE.match(_compact(line)))


def _repair_separators(text: str) -> str:
    """把 5,890.140.20 这类千分位笔误还原成 5,890,140.20。"""
    match = _MULTI_DOT_RE.match(text)
    if not match:
        return text
    return f"{match.group(1)},{match.group(2)}.{match.group(3)}"


def _page_text(doc: Any, page_number: int) -> str:
    if page_number < 1 or page_number > len(doc):
        return ""
    return doc.load_page(page_number - 1).get_text() or ""


def _source_pages(table: Any) -> list[int]:
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).strip().isdigit()
    ]
    if not pages:
        pages = [int(getattr(table, "page", 1) or 1)]
    return pages


def _sync_blocks(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


# --------------------------------------------------------------------------
# 经营表
# --------------------------------------------------------------------------


def _is_group_title(label: str) -> bool:
    compact = _compact(label)
    return compact.endswith(_GROUP_TITLE_TAIL) or compact in {"四、主要经营指标"}


def _parse_operating_rows(texts: Sequence[str]) -> list[list[str]]:
    """按“指标名称/本季度数/本年度累计数”逐行重构经营表数据。

    跨页续表时，表头只出现一次；页码行、重复表头、分组标题都要跳过。
    """
    rows: list[list[str]] = []
    started = False
    current: list[str] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and len(current) == 3 and not _is_group_title(current[0]):
            rows.append(list(current))
        current = None

    for text in texts:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not started:
            start = next(
                (index for index, line in enumerate(lines) if line.startswith("指标名称")),
                None,
            )
            if start is None:
                continue
            started = True
            lines = lines[start + 1:]
        else:
            # 续页页眉只有页码；表头不会重复。
            cursor = 0
            while cursor < len(lines) and re.fullmatch(r"\d{1,3}", lines[cursor]):
                cursor += 1
            lines = lines[cursor:]

        for line in lines:
            if line.startswith(_TITLE_STOP):
                flush()
                return rows
            if line in {"本季度数", "本年度累计数"}:
                continue
            if _is_value(line):
                if current is not None and len(current) < 3:
                    current.append(_repair_separators(_compact(line)))
                continue
            # 新标签行：先结算上一指标。
            if current is not None:
                flush()
            current = [line]
    flush()
    return rows


def _complete_operating(table: Any, doc: Any) -> list[list[str]] | None:
    pages = _source_pages(table)
    parsed = _parse_operating_rows([_page_text(doc, page) for page in pages])
    if len(parsed) < 5:
        return None
    return [list(_OPERATING_HEADER), *parsed]


# --------------------------------------------------------------------------
# 整页线框表（实际资本续页 / S05 最低资本表）
# --------------------------------------------------------------------------


def _grid_rows(pdf: Any, page_number: int) -> list[list[str]]:
    """取某页行数最多的线框表；只保留单元格文本，None 归一为空串。"""
    if page_number < 1 or page_number > len(pdf.pages):
        return []
    try:
        tables = pdf.pages[page_number - 1].extract_tables(
            table_settings=_LINE_SETTINGS
        ) or []
    except Exception:
        return []
    best: list[list[str]] = []
    for raw in tables:
        rows = [[_strip(cell) for cell in row] for row in raw]
        if len(rows) > len(best):
            best = rows
    return best


def _split_row(row: Sequence[str]) -> tuple[str, str, str] | None:
    """把整页线框表的一行拆成（项目, 期末数, 期初数），丢弃行次列。"""
    cells = [cell for cell in row if cell]
    if len(cells) >= 4 and _ROWNO_RE.match(cells[0]):
        return cells[1], cells[2], cells[3]
    if len(cells) == 3:
        return cells[0], cells[1], cells[2]
    return None


def _complete_actual(table: Any, pdf: Any) -> tuple[int, list[list[str]]] | None:
    rows = [list(row) for row in (table.rows or [])]
    labels = {_compact(row[0]) for row in rows if row and _compact(row[0])}
    if any("实际资本合计" in label for label in labels):
        return None

    pages = _source_pages(table)
    candidates = [pages[-1], pages[-1] + 1, *reversed(pages)]
    grid: list[list[str]] = []
    page = pages[-1]
    for candidate in candidates:
        grid = _grid_rows(pdf, candidate)
        if not grid:
            continue
        flat = "".join(_compact(cell) for row in grid for cell in row)
        if "实际资本合计" in flat:
            page = candidate
            break
        grid = []
    if not grid:
        return None

    output = list(rows)
    for row in grid:
        split = _split_row(row)
        if split is None:
            continue
        label, current, previous = split
        key = _compact(label)
        if not key or key in labels:
            continue
        output.append([label, current, previous])
        labels.add(key)
        if "实际资本合计" in key:
            break
    if len(output) == len(rows):
        return None
    return page, output


def _rebuild_minimum(table: Any, pdf: Any) -> tuple[int, list[list[str]]] | None:
    for page in _source_pages(table):
        grid = _grid_rows(pdf, page)
        header = next(
            (
                index
                for index, row in enumerate(grid)
                if len(row) >= 4
                and _compact(row[0]) == "行次"
                and "项目" in (_compact(row[1]), _compact(row[2]))
            ),
            None,
        )
        if header is None:
            continue
        output: list[list[str]] = [list(_MINIMUM_HEADER)]
        for row in grid[header + 1:]:
            cells = [*row, "", "", "", ""]
            rowno, label, current, previous = (
                _strip(cells[0]),
                _strip(cells[1]),
                _strip(cells[2]),
                _strip(cells[3]),
            )
            if not label:
                continue
            output.append([label, current, previous])
            # S05 的汇总行：行次 4 = 最低资本，必须包含在表内。
            if rowno == "4" and label == "最低资本":
                break
        if len(output) >= 10:
            return page, output
    return None


# --------------------------------------------------------------------------
# 近三年投资收益率
# --------------------------------------------------------------------------


def _trim_three_year(table: Any, doc: Any) -> list[list[str]] | None:
    rows = [list(row) for row in (table.rows or [])]
    kept = [row for row in rows if row and "近三年" in _compact(row[0])]
    if len(kept) >= 2:
        return kept

    fallback: list[list[str]] = []
    for page in _source_pages(table):
        lines = [
            line.strip()
            for line in _page_text(doc, page).splitlines()
            if line.strip()
        ]
        for index, line in enumerate(lines):
            if not line.startswith("近三年") or index + 1 >= len(lines):
                continue
            if _is_value(lines[index + 1]):
                fallback.append([line, _compact(lines[index + 1])])
    return fallback if len(fallback) >= 2 else None


# --------------------------------------------------------------------------
# 流动性风险监测指标
# --------------------------------------------------------------------------


def _trim_liquidity(table: Any) -> list[list[str]] | None:
    rows = [list(row) for row in (table.rows or [])]
    trimmed: list[list[str]] = []
    for row in rows:
        if row and "表格分块" in str(row[0]):
            break
        trimmed.append(row)
    if len(trimmed) >= 5 and len(trimmed) < len(rows):
        return trimmed
    return None


@register("yangguang")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """阳光产险专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz
    import pdfplumber

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for table in tables:
                table_id = str(getattr(table, "table_id", "") or "")
                pages = _source_pages(table)
                if table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                    rows = _complete_operating(table, doc)
                    if rows:
                        _sync_blocks(table, rows, pages[0])
                elif table_id == "ACTUAL_CAPITAL":
                    result = _complete_actual(table, pdf)
                    if result:
                        _sync_blocks(table, result[1], pages[0])
                elif table_id == "MINIMUM_CAPITAL":
                    result = _rebuild_minimum(table, pdf)
                    if result:
                        _sync_blocks(table, result[1], result[0])
                elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                    rows = _trim_three_year(table, doc)
                    if rows:
                        _sync_blocks(table, rows, pages[0])
                elif table_id == "LIQUIDITY_MONITORING":
                    rows = _trim_liquidity(table)
                    if rows:
                        _sync_blocks(table, rows, pages[0])
    finally:
        doc.close()
    return tables
