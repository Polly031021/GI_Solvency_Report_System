"""恒邦财险专属后处理：文字版沿用既有规则，扫描版走本地 OCR。"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_VALUE_RE = re.compile(
    r"^(?:"
    r"--|—|-|－"
    r"|-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
    r"|-?\d+\.\d{2,}%?"
    r"|-?\d{4,}(?:\.\d+)?%?"
    r")$"
)
_ROW_RE = re.compile(r"^(?:\d+(?:\.\d+)*\*?|\(\d+\)|\(\+\))$")


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _is_value(value: str) -> bool:
    return bool(_VALUE_RE.fullmatch(str(value).strip()))


def _find_line(
    lines: Sequence[str],
    needle: str,
    *,
    start: int = 0,
) -> int | None:
    key = _compact(needle)
    for index in range(start, len(lines)):
        if key in _compact(lines[index]):
            return index
    return None


def _stop_index(
    lines: Sequence[str],
    start: int,
    terms: Sequence[str],
) -> int:
    keys = tuple(_compact(term) for term in terms if str(term).strip())
    for index in range(start, len(lines)):
        text = _compact(lines[index])
        if any(key and key in text for key in keys):
            return index
    return len(lines)


def _parse_labeled(lines: Sequence[str], width: int) -> list[list[str]]:
    rows: list[list[str]] = []
    index = 0
    while index < len(lines):
        label = str(lines[index]).strip()
        if not label or _is_value(label) or _ROW_RE.fullmatch(label):
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while (
            cursor < len(lines)
            and len(values) < width
            and _is_value(str(lines[cursor]).strip())
        ):
            values.append(str(lines[cursor]).strip())
            cursor += 1
        if values:
            rows.append([label, *values])
            index = cursor
        else:
            index += 1
    return rows


def _parse_numbered(lines: Sequence[str], width: int) -> list[list[str]]:
    rows: list[list[str]] = []
    index = 0
    while index < len(lines):
        number = str(lines[index]).strip()
        if not _ROW_RE.fullmatch(number):
            index += 1
            continue
        index += 1
        labels: list[str] = []
        values: list[str] = []
        while index < len(lines):
            token = str(lines[index]).strip()
            if _ROW_RE.fullmatch(token) and not values:
                break
            if _is_value(token) and labels:
                values.append(token)
                index += 1
                if len(values) >= width:
                    break
                continue
            if values:
                break
            if _ROW_RE.fullmatch(token):
                break
            labels.append(token)
            index += 1
        if labels:
            rows.append([number, "".join(labels), *values])
    return rows


def _cut_after_label(
    rows: list[list[str]],
    label: str,
    *,
    exact: bool = False,
) -> list[list[str]]:
    key = _compact(label)
    for index, row in enumerate(rows):
        if len(row) <= 1:
            continue
        value = _compact(row[1])
        if (exact and value == key) or (not exact and key in value):
            return rows[: index + 1]
    return rows


def _ocr_lines(pdf_bytes: bytes, pages: Sequence[int]) -> list[str]:
    from ._ocr import page_cell_lines

    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        text_lines = [
            line.strip()
            for page in pages
            if 1 <= int(page) <= len(document)
            for line in (document.load_page(int(page) - 1).get_text() or "").splitlines()
            if line.strip()
        ]
    if len(text_lines) >= 20:
        return text_lines
    lines: list[str] = []
    for page in pages:
        lines.extend(page_cell_lines(pdf_bytes, int(page)))
    return lines


def _rebuild_main(lines: Sequence[str]) -> list[list[str]] | None:
    header = ["项目", "本季度数", "上季度数", "基本情景下下季度预测数"]
    header_at = _find_line(lines, "上季度数")
    if header_at is None:
        return None
    body_at = _find_line(lines, "认可资产", start=header_at + 1)
    if body_at is None:
        return None
    stop = _stop_index(
        lines,
        body_at,
        ("2.流动性风险监管指标", "流动性风险监测指标"),
    )
    rows = _parse_labeled(lines[body_at:stop], 3)
    return [header, *rows] if rows else None


def _rebuild_operating(lines: Sequence[str]) -> list[list[str]] | None:
    header = ["指标名称", "本季度数", "本年度累计数"]
    title_at = _find_line(lines, "3.主要经营指标")
    if title_at is None:
        title_at = _find_line(lines, "主要经营指标")
    if title_at is None:
        return None
    header_at = _find_line(lines, "本年度累计数", start=title_at)
    if header_at is None:
        return None
    stop = _stop_index(
        lines,
        header_at + 1,
        ("4.近三年平均投资收益率", "备注：效益类指标"),
    )
    rows = _parse_labeled(lines[header_at + 1:stop], 2)
    rows = [
        row
        for row in rows
        if len(row) > 1 and any(_is_value(value) for value in row[1:])
    ]
    return [header, *rows] if rows else None


def _rebuild_returns(lines: Sequence[str]) -> list[list[str]] | None:
    header = ["指标名称", "指标额"]
    rows = []
    for label in ("近三年平均投资收益率", "近三年平均综合投资收益率"):
        index = _find_line(lines, label)
        if index is None:
            continue
        value = next(
            (
                _compact(item)
                for item in lines[index + 1:index + 5]
                if _is_value(_compact(item))
            ),
            "",
        )
        if value:
            rows.append([label, value])
    return [header, *rows] if rows else None


def _rebuild_capital(
    lines: Sequence[str],
    *,
    minimum: bool,
) -> list[list[str]] | None:
    header = ["行次", "项目", "期末数", "期初数"]
    title = "最低资本表" if minimum else "实际资本表"
    title_at = _find_line(lines, title)
    if title_at is None:
        return None
    header_at = _find_line(lines, "期初数", start=title_at)
    if header_at is None:
        return None
    stop = _stop_index(
        lines,
        header_at + 1,
        ("认可资产明细表", "认可负债明细表"),
    )
    rows = _parse_numbered(lines[header_at + 1:stop], 2)
    if not rows:
        return None
    rows = _cut_after_label(
        rows,
        "最低资本" if minimum else "实际资本合计",
        exact=minimum,
    )
    return [header, *rows]


def _assign_columns(
    tokens: Sequence[Any],
    anchors: Sequence[float],
) -> list[str]:
    if not anchors:
        return []
    boundaries = [
        (anchors[index] + anchors[index + 1]) / 2
        for index in range(len(anchors) - 1)
    ]
    cells = [""] * len(anchors)
    for token in sorted(tokens, key=lambda item: item.x0):
        column = 0
        while column < len(boundaries) and token.x_center > boundaries[column]:
            column += 1
        cells[column] += token.text
    return cells


def _rebuild_liquidity(
    pdf_bytes: bytes,
    pages: Sequence[int],
) -> list[list[str]] | None:
    from ._ocr import group_tokens_into_lines, ocr_page_tokens

    rows: list[list[str]] = []
    for page in pages:
        lines = group_tokens_into_lines(
            ocr_page_tokens(pdf_bytes, int(page))
        )
        header_index = None
        anchors: list[float] = []
        for index, line in enumerate(lines):
            text = _compact("".join(token.text for token in line))
            if (
                "监测指标" in text
                and "项目" in text
                and "本季度数" in text
                and "上季度数" in text
            ):
                header_index = index
                anchors = [
                    next(token.x_center for token in line if "监测指标" in token.text),
                    next(token.x_center for token in line if "项目" in token.text),
                    next(token.x_center for token in line if "本季度数" in token.text),
                    next(token.x_center for token in line if "上季度数" in token.text),
                ]
                break
        if header_index is None:
            continue
        category = ""
        for line in lines[header_index + 1:]:
            cells = _assign_columns(line, anchors)
            if len(cells) != 4:
                continue
            if cells[0]:
                category = cells[0]
            elif category:
                cells[0] = category
            if not any(_is_value(value) for value in cells[2:]):
                continue
            rows.append(cells)
    header = ["监测指标", "项目", "本季度数", "上季度数"]
    return [header, *rows] if rows else None


def _sync(table: Any, rows: list[list[str]], pages: Sequence[int]) -> None:
    table.rows = rows
    table.table_blocks = [
        {
            "page": int(pages[0]) if pages else int(getattr(table, "page", 1) or 1),
            "rows": [list(row) for row in rows],
            "unit": getattr(table, "unit", ""),
        }
    ]
    table.strategy = "公司专属OCR·单元格重建"


@register("hengbang")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if int(page) > 0
        ]
        if not pages:
            continue
        rows: list[list[str]] | None = None
        if table_id == "LIQUIDITY_MONITORING":
            rows = _rebuild_liquidity(pdf_bytes, pages)
        else:
            lines = _ocr_lines(pdf_bytes, pages)
            if table_id == "SOLVENCY_MAIN":
                rows = _rebuild_main(lines)
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                rows = _rebuild_operating(lines)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                rows = _rebuild_returns(lines)
            elif table_id == "ACTUAL_CAPITAL":
                rows = _rebuild_capital(lines, minimum=False)
            elif table_id == "MINIMUM_CAPITAL":
                rows = _rebuild_capital(lines, minimum=True)
        if rows:
            _sync(table, rows, pages)
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("hengbang", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "hengbang",
        pdf_bytes,
        table_configs,
        mode="document",
    )
