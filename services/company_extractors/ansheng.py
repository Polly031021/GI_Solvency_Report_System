# -*- coding: utf-8 -*-
"""安盛天平专属提取后处理。

覆盖的版式缺口（详见 plans/company-安盛天平-extraction.md）：

1. SOLVENCY_MAIN：2022/2024 表格行内夹空占位列（如
   ``['1','','认可资产','','11,634,360,895.49',...]``），2025 又把行次与
   项目合并成两格，导致表头与数据列错位、关键指标被记成 0。统一成
   ``行次/项目/本季度末数/上季度末数[/下季度末预测数]``。
2. ACTUAL_CAPITAL：2022 第 20 页 8 列、第 21 页 4 列混排，且 1.1 净资产
   行只存在于 PDF 文本层（pdfplumber 丢行），从文本补回。
3. MINIMUM_CAPITAL：2024/2025 末页“4 最低资本”合计行落在表格块之外，
   从 PDF 文本补回；行首“3.4/4”行次列去掉，避免与指标名混排。
4. NON_LIFE_OPERATING_METRICS：2023/2024 表头被“主要经营指标”分组行
   顶掉、后半页无框线只能识别 13 行；2024 表头还是“本期/本期期末”。
   各年份统一按 PDF 文本重建标准三列表。
5. LIQUIDITY_MONITORING：跨页表块标记行、同一主标签下“指标值/分子/
   分母”多行会造成未映射噪声与同指标多值冲突；回溯偏差率表去掉空列，
   监测指标压成“主标签→数值”，分子/分母明细拼进标签并跳过空值
   （不把“不适用”写成 0）。

只改 table.rows / table.table_blocks，不改 table_id、source_pages、unit。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import fitz

from . import register

# 行次：允许 1、1.2、1.2.3、1* 等写法
_ROW_NUMBER_RE = re.compile(r"^\d+(?:[.．]\d+)*\*?$")
# 数值：允许空格千分位（2023）、逗号千分位、负号、百分号
_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_DASHES = frozenset({"-", "--", "—", "–", "－"})
_MARKER_RE = re.compile(r"^【第\d+页表格分块】$")

_MAIN_HEADER = ["行次", "项目", "本季度末数", "上季度末数", "下季度末预测数"]
_ACTUAL_HEADER = ["行次", "项目", "期末数", "期初数"]
_MIN_HEADER = ["指标名称", "期末数", "期初数"]
_OPER_HEADER = ["指标名称", "本季度数", "本年度累计数"]

_PERIOD_LINES = frozenset({
    "本季度数", "本年度累计数", "本期", "本期期末", "上季度数",
    "本季度", "上季度", "期末数", "期初数", "数值",
})
_GROUP_WORDS = ("主要经营指标", "效益类指标", "规模类指标", "品质类指标")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_value(text: str) -> bool:
    s = _clean(text).replace(" ", "").replace("\u3000", "")
    if not s:
        return False
    if s in _DASHES:
        return True
    return bool(_NUMBER_RE.match(s))


def _is_header(cells: Sequence[str]) -> bool:
    words = {_clean(cell) for cell in cells if _clean(cell)}
    if words & {"指标名称", "行次", "序号", "监测指标"}:
        return True
    return bool(
        "项目" in words
        and words & {"本季度", "上季度", "期末数", "期初数", "数值", "本期"}
    )


def _split_row(row: Sequence[Any]) -> tuple[str, str, list[str]]:
    """拆成 (行次, 标签, 数值列表)。

    行内空占位列直接删除；数值列之前出现的非数值文本并入标签（少数行
    标签被折成两格）。
    """
    cells = [_clean(cell) for cell in row]
    while cells and not cells[-1]:
        cells.pop()
    cells = [cell for cell in cells if cell]
    if not cells:
        return "", "", []

    if _ROW_NUMBER_RE.match(cells[0]):
        number, rest = cells[0], cells[1:]
    else:
        number, rest = "", cells
    if not rest:
        return number, "", []

    label = rest[0]
    values: list[str] = []
    started = False
    for cell in rest[1:]:
        if _is_value(cell):
            values.append(cell)
            started = True
        elif not started:
            label = f"{label} {cell}".strip()
    return number, label, values


def _blocks(table: Any) -> list[tuple[int, list[list[str]]]]:
    out: list[tuple[int, list[list[str]]]] = []
    for block in list(getattr(table, "table_blocks", None) or []):
        if isinstance(block, dict):
            page = int(block.get("page") or 0)
            rows = list(block.get("rows") or [])
        else:
            page = int(getattr(block, "page", 0) or 0)
            rows = list(getattr(block, "rows", []) or [])
        out.append((page, [[_clean(cell) for cell in row] for row in rows]))
    if not out:
        page = int(getattr(table, "page", 0) or 0)
        out = [(page, [[_clean(cell) for cell in row] for row in list(getattr(table, "rows", None) or [])])]
    return out


def _first_page(table: Any) -> int:
    page = int(getattr(table, "page", 0) or 0)
    if page:
        return page
    pages = list(getattr(table, "source_pages", None) or [])
    return int(pages[0]) if pages else 1


def _sync(table: Any, blocks: list[list[list[str]]], pages: list[int]) -> None:
    """写回 table_blocks（STEP3 优先读 blocks），并同步 table.rows。"""
    merged: list[list[str]] = []
    payload = []
    for index, block_rows in enumerate(blocks):
        page = pages[index] if index < len(pages) else _first_page(table)
        payload.append({"page": int(page or 1), "rows": [list(r) for r in block_rows]})
        if not merged:
            merged.extend([list(r) for r in block_rows])
        else:
            merged.extend([list(r) for r in block_rows[1:]])
    table.table_blocks = payload
    table.rows = merged


def _page_text(doc: Any, page: int) -> str:
    if page < 1 or page > doc.page_count:
        return ""
    return doc[page - 1].get_text("text")


def _search(doc: Any, pages: Sequence[int], pattern: str) -> tuple[str, ...] | None:
    regex = re.compile(pattern)
    for page in pages or ():
        match = regex.search(_page_text(doc, int(page)))
        if match:
            return match.groups()
    return None


# ---------------------------------------------------------------------------
# 各表修补
# ---------------------------------------------------------------------------


def _fix_main(table: Any) -> None:
    data: list[tuple[str, str, list[str]]] = []
    max_values = 0
    for _page, rows in _blocks(table):
        for row in rows:
            cells = [_clean(cell) for cell in row]
            if not cells:
                continue
            if _MARKER_RE.match(cells[0]) or _is_header(cells):
                continue
            number, label, values = _split_row(row)
            if not label:
                continue
            max_values = max(max_values, len(values))
            data.append((number, label, values))

    count = 3 if max_values >= 3 else 2
    header = _MAIN_HEADER[: 2 + count]
    out: list[list[str]] = [header]
    for number, label, values in data:
        # 主表的“-”表示空白（资本项按 0 处理），不能当成无法解析的文本，
        # 否则 2023 的核心二级资本等行会丢记录。
        vals = ["" if value in _DASHES else value for value in values[:count]]
        vals += [""] * max(0, count - len(vals))
        out.append([number, label, *vals])
    _sync(table, [out], [_first_page(table)])


def _fix_actual(table: Any, doc: Any) -> None:
    data: list[list[str]] = []
    for _page, rows in _blocks(table):
        for row in rows:
            cells = [_clean(cell) for cell in row]
            if not cells:
                continue
            if _MARKER_RE.match(cells[0]) or _is_header(cells):
                continue
            number, label, values = _split_row(row)
            if not label:
                continue
            vals = list(values[:2]) + [""] * max(0, 2 - len(values))
            data.append([number, label, *vals])

    labels = {row[1].strip() for row in data}
    if "净资产" not in labels:
        pages = list(getattr(table, "source_pages", None) or [_first_page(table)])
        found = _search(
            doc,
            pages,
            r"1\.1\s*净资产\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})",
        )
        if found:
            index = next(
                (i for i, row in enumerate(data) if "对净资产的调整额" in row[1]),
                len(data),
            )
            data.insert(index, ["1.1", "净资产", found[0], found[1]])

    header = list(_ACTUAL_HEADER)
    _sync(table, [ [header, *data] ], [_first_page(table)])


def _fix_minimum(table: Any, doc: Any) -> None:
    data: list[list[str]] = []
    for _page, rows in _blocks(table):
        for row in rows:
            cells = [_clean(cell) for cell in row]
            if not cells:
                continue
            if _MARKER_RE.match(cells[0]) or _is_header(cells):
                continue
            _number, label, values = _split_row(row)
            if not label:
                continue
            vals = list(values[:2]) + [""] * max(0, 2 - len(values))
            data.append([label, *vals])

    if not any(row[0].strip() == "最低资本" for row in data):
        pages = list(getattr(table, "source_pages", None) or [_first_page(table)])
        found = _search(
            doc,
            pages,
            r"(?<![\d.])4\s*最低资本\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})",
        )
        if found:
            data.append(["最低资本", found[0], found[1]])

    header = list(_MIN_HEADER)
    _sync(table, [[header, *data]], [_first_page(table)])


def _page_lines(doc: Any, page: int) -> list[str]:
    lines = [_clean(line) for line in _page_text(doc, page).splitlines()]
    lines = [line for line in lines if line]
    # PDF 页脚/页眉的纯页码（通常等于物理页或物理页-1），不能当成数值。
    if lines and lines[0].isdigit() and abs(int(lines[0]) - page) <= 1:
        lines = lines[1:]
    if lines and lines[-1].isdigit() and abs(int(lines[-1]) - page) <= 1:
        lines = lines[:-1]
    return lines


def _is_group_label(label: str) -> bool:
    return len(label) <= 24 and any(word in label for word in _GROUP_WORDS)


def _parse_operating(doc: Any, pages: Sequence[int]) -> list[list[str]]:
    lines: list[str] = []
    for page in pages:
        lines.extend(_page_lines(doc, int(page)))

    start = next(
        (i for i, line in enumerate(lines) if line.startswith("指标名称")),
        None,
    )
    if start is None:
        return []

    i = start + 1
    while i < len(lines) and lines[i] in _PERIOD_LINES:
        i += 1

    rows: list[list[str]] = []
    while i < len(lines):
        line = lines[i]
        if (
            "近三年" in line
            or "风险管理能力" in line
            or line.startswith("五、")
        ):
            break
        if line in _PERIOD_LINES or line in _DASHES:
            i += 1
            continue
        if _is_group_label(line):
            i += 1
            while i < len(lines) and lines[i] in _DASHES:
                i += 1
            continue
        if _is_value(line):
            i += 1
            continue

        label = line
        i += 1
        values: list[str] = []
        while i < len(lines) and len(values) < 2 and _is_value(lines[i]):
            values.append(lines[i])
            i += 1
        if not values:
            continue
        rows.append([label, *values])
    return rows


def _rebuild_operating(table: Any, doc: Any) -> None:
    pages = list(getattr(table, "source_pages", None) or [_first_page(table)])
    rows = _parse_operating(doc, pages)
    if len(rows) < 10:
        return
    header = list(_OPER_HEADER)
    out = [header]
    for row in rows:
        values = list(row[1:3]) + [""] * max(0, 2 - len(row[1:3]))
        out.append([row[0], *values])
    _sync(table, [out], [int(pages[0])])


def _fix_liquidity(table: Any) -> None:
    blocks = _blocks(table)
    if not blocks:
        return

    first_page = blocks[0][0] or _first_page(table)
    backtest: list[list[str]] = []
    for row in blocks[0][1]:
        cells = [_clean(cell) for cell in row]
        if not cells or _MARKER_RE.match(cells[0]) or _is_header(cells):
            continue
        _number, label, values = _split_row(row)
        if not label:
            continue
        backtest.append([label, *values[:2]])

    monitor: list[list[str]] = []
    monitor_page = blocks[1][0] if len(blocks) > 1 else first_page
    last_main = ""
    for _page, rows in blocks[1:]:
        for row in rows:
            cells = [_clean(cell) for cell in row]
            if not cells or _MARKER_RE.match(cells[0]) or _is_header(cells):
                continue
            # 监测指标是跨页合并单元格：续行的主标签为空，需要沿用上一行。
            if len(cells) >= 5:
                main = cells[0] or last_main
                sub = cells[2]
                value = cells[4]
            elif len(cells) == 3:
                main = cells[0] or last_main
                sub = cells[1]
                value = cells[2]
            else:
                parts = [cell for cell in cells if cell]
                if len(parts) < 2:
                    continue
                main = parts[0] or last_main
                sub = parts[1] if len(parts) >= 3 else ""
                value = parts[-1]
            if main:
                last_main = main
            if not _is_value(value):
                # 空值/“不适用”不写 0，直接不落行。
                continue
            label = main if sub in {"", "指标值"} else f"{main}（{sub}）"
            monitor.append([label, value])

    block_backtest = [["项目", "本季度数", "上季度数"], *backtest]
    block_monitor = [["监测指标", "数值"], *monitor]
    _sync(table, [block_backtest, block_monitor], [first_page, monitor_page])


@register("ansheng")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """安盛天平专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                _fix_main(table)
            elif table_id == "ACTUAL_CAPITAL":
                _fix_actual(table, doc)
            elif table_id == "MINIMUM_CAPITAL":
                _fix_minimum(table, doc)
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                _rebuild_operating(table, doc)
            elif table_id == "LIQUIDITY_MONITORING":
                _fix_liquidity(table)
    finally:
        doc.close()
    return tables
