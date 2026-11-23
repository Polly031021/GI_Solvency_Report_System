"""申能财险专属提取后处理：重建 4 张通用规则无法正确提取的表。

申能财险 2024、2025 两期偿付能力报告版式基本一致（2025 页码整体前移），
通用规则在以下位置丢表、串表或丢行：

1. 主要经营指标（第 13/14 页下半页起）没有表格线框，pdfplumber 识别
   不到；同页上半页是流动性风险监测指标表，通用规则会把后者当成经营表
   或近三年投资收益率表。本模块从 PDF 文本按“主要经营指标”标题之后
   逐行重建，直到“注：”“四、”等下一节标记。
2. “近三年平均投资收益率/近三年平均综合投资收益率”不是独立表，而是
   主要经营指标表内的两行；本模块从经营表行中抽取并补建
   THREE_YEAR_INVESTMENT_RETURN。
3. 实际资本表（S02）与“实际调整资本表”分列相邻两页，行次列与项目列
   会被拆成多行，通用提取出现列错位；本模块按行次重建两张子表。
4. 最低资本（S05）整页有线框，但行次列会被并入项目列，且页后紧跟
   “资本工具表”；本模块按行次重建，到“4 最低资本”行为止。

专属逻辑只重写 ``table.rows`` 与 ``table.table_blocks``，数据全部从
PDF 文本重新解析，按标题/行次/行标签定位，不写死年份和页码；重复执行
结果一致。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import fitz

from ..batch_extraction import FlexibleTable
from . import register

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_CODE_RE = re.compile(r"^\d+(?:\.\d+)*(?:\.\*)?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")

_OPER_HEADER = ["指标名称", "本季度数", "本年累计数"]
_CODE_HEADER = ["行次", "项目", "本季度末", "上季度末"]
_THREE_YEAR_HEADER = ["指标名称", "数值"]

_SKIP_LINES = {
    "行次",
    "项目",
    "本季度末",
    "上季度末",
    "期末数",
    "期初数",
    "指标名称",
    "本季度数",
    "本年累计数",
    "本季度末数",
    "上季度末数",
    "实际资本表",
    "实际调整资本表",
    "单位：元",
    "单位:元",
}
_STOP_PREFIXES = ("注：", "注:", "备注", "四、", "（四）", "五、", "（五）")


def _cell(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _is_value(value: Any) -> bool:
    text = str(value or "").strip()
    if text in {"-", "—", "–", "--"}:
        return True
    return bool(_NUMBER_RE.fullmatch(text))


def _is_row_code(value: str) -> bool:
    return bool(_ROW_CODE_RE.fullmatch(value))


def _join_label(parts: Sequence[str]) -> str:
    return "".join(str(part or "").strip() for part in parts)


def _page_lines(doc: Any, page: int) -> list[str]:
    """取一页文本行；去掉页首 1–3 位页码。"""
    lines = [
        line.strip()
        for line in doc[int(page) - 1].get_text("text").splitlines()
        if line.strip()
    ]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return lines


def _find_page(doc: Any, needles: Sequence[str], start: int = 1) -> int | None:
    for page in range(max(1, int(start)), doc.page_count + 1):
        text = doc[page - 1].get_text("text")
        if all(needle in text for needle in needles):
            return page
    return None


def _sync(table: Any, blocks: list[dict[str, Any]], unit: str) -> None:
    """同步 rows / table_blocks / source_pages / unit。"""
    clean_blocks = [
        {
            "page": int(block["page"]),
            "rows": [list(row) for row in block["rows"]],
            "unit": unit,
        }
        for block in blocks
        if block.get("rows")
    ]
    flat: list[list[str]] = []
    for block in clean_blocks:
        flat.extend(block["rows"])
    table.rows = [list(row) for row in flat]
    table.table_blocks = clean_blocks
    table.source_pages = [block["page"] for block in clean_blocks]
    table.unit = unit


# ---------------------------------------------------------------------------
# 主要经营指标（无表格线，按文本重建）
# ---------------------------------------------------------------------------
def _parse_operating_page(lines: Sequence[str], *, has_title: bool) -> tuple[list[list[str]], bool]:
    """解析一页经营指标；返回 (数据行, 是否遇到下一节)。"""
    body = list(lines)
    if has_title:
        index = next(
            (i for i, line in enumerate(body) if "主要经营指标" in line),
            None,
        )
        if index is None:
            return [], False
        body = body[index + 1 :]

    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    stopped = False
    for line in body:
        if line in _SKIP_LINES:
            continue
        if line.startswith(_STOP_PREFIXES):
            stopped = True
            break
        if _is_value(line):
            values.append(line)
            if len(values) == 2:
                rows.append([_join_label(label), values[0], values[1]])
                label, values = [], []
            continue
        if values:
            # 值不足两个即出现新标签：补空单元格后成行，避免吞掉标签
            values.append("")
            rows.append([_join_label(label), values[0], values[1]])
            label, values = [], []
        label.append(line)
    return rows, stopped


def _operating_pages(doc: Any) -> list[tuple[int, list[list[str]]]]:
    start = _find_page(doc, ["主要经营指标"])
    if start is None:
        return []
    pages: list[tuple[int, list[list[str]]]] = []
    for page in range(start, doc.page_count + 1):
        rows, stopped = _parse_operating_page(
            _page_lines(doc, page),
            has_title=(page == start),
        )
        if rows:
            pages.append((page, rows))
        if stopped:
            break
    return pages


def _rebuild_operating(doc: Any, table: Any) -> None:
    pages = _operating_pages(doc)
    rows_total = sum(len(rows) for _, rows in pages)
    if len(pages) == 0 or rows_total < 20:
        return
    blocks: list[dict[str, Any]] = []
    for index, (page, rows) in enumerate(pages):
        block_rows = [list(_OPER_HEADER), *rows] if index == 0 else rows
        blocks.append({"page": page, "rows": block_rows})
    _sync(table, blocks, "元")
    table.strategy = "专属提取·申能·经营指标文本重建"


# ---------------------------------------------------------------------------
# 近三年（综合）投资收益率：取自经营表两行
# ---------------------------------------------------------------------------
def _three_year_rows(doc: Any) -> list[list[str]]:
    wanted = ("近三年平均投资收益率", "近三年平均综合投资收益率")
    found: dict[str, str] = {}
    for _, rows in _operating_pages(doc):
        for row in rows:
            label = _join_label([row[0]]) if row else ""
            for name in wanted:
                if label != name:
                    continue
                if len(row) >= 3 and _is_value(row[2]):
                    found[name] = row[2]
                elif len(row) >= 2 and _is_value(row[1]):
                    found[name] = row[1]
    if not found:
        return []
    records = [[name, found.get(name, "-")] for name in wanted]
    return [list(_THREE_YEAR_HEADER), *records]


def _three_year_page(doc: Any) -> int | None:
    return _find_page(doc, ["近三年平均投资收益率"])


def _rebuild_three_year(doc: Any, table: Any) -> None:
    rows = _three_year_rows(doc)
    if not rows:
        return
    page = _three_year_page(doc) or int(getattr(table, "page", 1) or 1)
    _sync(table, [{"page": page, "rows": rows}], "%")
    table.strategy = "专属提取·申能·近三年收益率抽取"


def _append_three_year(doc: Any, tables: list[Any]) -> None:
    if any(
        str(getattr(table, "table_id", "") or "") == "THREE_YEAR_INVESTMENT_RETURN"
        for table in tables
    ):
        return
    rows = _three_year_rows(doc)
    if not rows:
        return
    page = _three_year_page(doc) or 1
    tables.append(
        FlexibleTable(
            table_id="THREE_YEAR_INVESTMENT_RETURN",
            table_name="近三年（综合）投资收益率",
            page=page,
            source_pages=[page],
            rows=rows,
            table_blocks=[{"page": page, "rows": [list(row) for row in rows], "unit": "%"}],
            table_config={
                "table_id": "THREE_YEAR_INVESTMENT_RETURN",
                "table_name": "近三年（综合）投资收益率",
                "canonical_headers": list(_THREE_YEAR_HEADER),
            },
            unit="%",
            strategy="专属提取·申能·补建近三年收益率",
            quality_score=1.0,
            evidence="报告未单列近三年收益率表，数据位于主要经营指标表内，由专属提取器抽取补建",
        )
    )


# ---------------------------------------------------------------------------
# 实际资本 / 最低资本：按行次重建
# ---------------------------------------------------------------------------
def _parse_code_rows(lines: Sequence[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    code: str | None = None
    label_parts: list[str] = []
    values: list[str] = []
    pending: list[str] = []
    started = False

    def flush() -> None:
        nonlocal code, label_parts, values
        if code is None:
            return
        rows.append([code, _join_label(label_parts)] + values + [""] * (2 - len(values)))
        code, label_parts, values = None, [], []

    for line in lines:
        if line in _SKIP_LINES or line.startswith("单位："):
            continue
        if not started:
            if _is_row_code(line):
                started = True
            else:
                continue
        if _is_row_code(line):
            flush()
            code = line
            label_parts = list(pending)
            pending = []
            values = []
            continue
        if code is None:
            pending.append(line)
            continue
        if _is_value(line):
            if len(values) < 2:
                values.append(line)
            continue
        if values:
            flush()
            pending = [line]
            continue
        label_parts.append(line)
    flush()
    return rows


def _truncate_after(rows: list[list[str]], code: str, label_term: str) -> list[list[str]]:
    for index, row in enumerate(rows):
        if row[0] == code and label_term in row[1]:
            return rows[: index + 1]
    return rows


def _rebuild_actual(doc: Any, table: Any) -> None:
    start = _find_page(doc, ["实际资本表", "核心一级资本"])
    if start is None:
        return
    pages = [p for p in (start, start + 1) if p <= doc.page_count]
    blocks: list[dict[str, Any]] = []
    total = 0
    for index, page in enumerate(pages):
        rows = _parse_code_rows(_page_lines(doc, page))
        if not rows:
            continue
        total += len(rows)
        block_rows = [list(_CODE_HEADER), *rows] if index == 0 else rows
        blocks.append({"page": page, "rows": block_rows})
    if total < 20:
        return
    # 第二张子表止于“5 实际资本合计”，其后的页脚/下一节不并入
    last = blocks[-1]
    last["rows"] = _truncate_after(last["rows"], "5", "实际资本合计")
    _sync(table, blocks, "元")
    table.strategy = "专属提取·申能·实际资本行次重建"


def _rebuild_minimum(doc: Any, table: Any) -> None:
    page = _find_page(doc, ["十、最低资本", "量化风险最低资本"])
    if page is None:
        return
    rows = _parse_code_rows(_page_lines(doc, page))
    rows = _truncate_after(rows, "4", "最低资本")
    if len(rows) < 25:
        return
    _sync(table, [{"page": page, "rows": [list(_CODE_HEADER), *rows]}], "元")
    table.strategy = "专属提取·申能·最低资本行次重建"


@register("shenneng")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """申能财险专属后处理入口；异常由调用方捕获。"""
    if not tables or not pdf_bytes:
        return tables
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        updated = list(tables)
        for table in updated:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "NON_LIFE_OPERATING_METRICS":
                _rebuild_operating(doc, table)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                _rebuild_three_year(doc, table)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_actual(doc, table)
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_minimum(doc, table)
        _append_three_year(doc, updated)
        return updated


from ._document import STANDARD_NON_LIFE_LAYOUT, make_document_hooks

_SHENNENG_LAYOUT = dict(STANDARD_NON_LIFE_LAYOUT)
_SHENNENG_LAYOUT["SOLVENCY_MAIN"] = {
    "title": "主要(?:偿付能力)?指标",
    "required": ["认可资产", "综合偿付能力充足率"],
    "unit": "元",
}
_SHENNENG_LAYOUT["MINIMUM_CAPITAL"] = {
    "title": "(?:S05[-—－]?最低资本表|[一二三四五六七八九十]+、最低资本)",
    "required": ["量化风险最低资本"],
    "continuation": 2,
    "tail_terms": ["风险最低资本", "最低资本"],
    "stop": "(?:保费风险最低资本表|巨灾风险最低资本表)",
    "unit": "元",
}
locate_company_tables, extract_company_tables = make_document_hooks(
    _SHENNENG_LAYOUT, "申能财险", postprocess
)
