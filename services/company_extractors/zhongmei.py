"""中煤财险专属后处理：文字版重建主表，扫描版使用本地 OCR。"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register
from .hengbang import (
    _cut_after_label,
    _is_value,
    _parse_labeled,
    _parse_numbered,
)


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


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


_ROW_FRAGMENT_RE = re.compile(r"^\d+(?:\.\d+)*\.$")
_AMOUNT_FRAGMENT_RE = re.compile(
    r"^(?:-?\d{1,3}(?:,\d{3})+|-?\d{4,})\.\d{0,2}$"
)


def _normalize_ocr_lines(lines: Sequence[str]) -> list[str]:
    """Rejoin row numbers and amounts split by the OCR line detector."""
    normalized: list[str] = []
    index = 0
    while index < len(lines):
        text = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", str(lines[index]).strip())
        following = (
            str(lines[index + 1]).strip()
            if index + 1 < len(lines)
            else ""
        )
        if _ROW_FRAGMENT_RE.fullmatch(text) and re.fullmatch(r"\d", following):
            normalized.append(f"{text}{following}")
            index += 2
            continue
        if (
            _AMOUNT_FRAGMENT_RE.fullmatch(text)
            and re.fullmatch(r"\d{1,2}", following)
        ):
            normalized.append(f"{text}{following}")
            index += 2
            continue
        if text in {"-", "—", "－"} and _is_value(following):
            normalized.append(f"-{following}")
            index += 2
            continue
        normalized.append(text)
        index += 1
    return normalized


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
        return _normalize_ocr_lines(text_lines)
    lines: list[str] = []
    for page in pages:
        lines.extend(page_cell_lines(pdf_bytes, int(page)))
    return _normalize_ocr_lines(lines)


def _rebuild_main(lines: Sequence[str]) -> list[list[str]] | None:
    header = ["指标名称", "本季度末数", "上季度末数", "基本情景下下季度预测数"]
    header_at = _find_line(lines, "本季度末数")
    if header_at is None:
        return None
    body_at = _find_line(lines, "认可资产", start=header_at + 1)
    if body_at is None:
        return None
    stop = _stop_index(
        lines,
        body_at,
        ("2.流动性风险监管", "流动性风险监管及监测指标"),
    )
    rows = _parse_labeled(lines[body_at:stop], 3)
    return [header, *rows] if rows else None


def _rebuild_operating(lines: Sequence[str]) -> list[list[str]] | None:
    header = ["指标名称", "本季度数", "本年度累计数"]
    header_at = _find_line(lines, "本年累计数")
    if header_at is None:
        return None
    body_at = _find_line(lines, "保险业务收入", start=header_at + 1)
    if body_at is None:
        return None
    rows = _parse_labeled(lines[body_at:], 2)
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
                str(item).strip()
                for item in lines[index + 1:index + 5]
                if _is_value(str(item).strip())
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
    header_at = next(
        (
            found
            for label in ("期初数", "上季度末数", "上季度数")
            if (found := _find_line(lines, label, start=title_at)) is not None
        ),
        None,
    )
    if header_at is None:
        return None
    stop = _stop_index(
        lines,
        header_at + 1,
        ("保费风险和准备金风险最低资本明细表",),
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


def _rebuild_liquidity(lines: Sequence[str]) -> list[list[str]] | None:
    header = ["指标名称", "本季度数", "上季度数"]
    title_at = _find_line(lines, "流动性风险监测指标")
    if title_at is None:
        return None
    header_at = _find_line(lines, "上季度末数", start=title_at)
    if header_at is None:
        return None
    stop = _stop_index(
        lines,
        header_at + 1,
        ("近三年平均投资收益率", "近三年平均综合投资收益率"),
    )
    rows = _parse_labeled(lines[header_at + 1:stop], 2)
    rows = [
        row
        for row in rows
        if len(row) > 1 and any(_is_value(value) for value in row[1:])
    ]
    return [header, *rows] if rows else None


def _capital_total_row(
    pdf_bytes: bytes,
    pages: Sequence[int],
    *,
    label: str,
    row_number: str,
) -> list[str] | None:
    from ._ocr import ocr_page_tokens

    needle = _compact(label)
    for page in pages:
        tokens = list(ocr_page_tokens(pdf_bytes, int(page)))
        label_token = next(
            (
                token
                for token in tokens
                if needle in _compact(token.text)
            ),
            None,
        )
        if label_token is None:
            continue
        row_token = next(
            (
                token
                for token in tokens
                if _compact(token.text) == row_number
                and abs(token.y_center - label_token.y_center) <= 30
                and token.x_center < label_token.x0
            ),
            None,
        )
        numeric_tokens = [
            token
            for token in tokens
            if token.x_center > label_token.x1
            and label_token.y_center - 40
            <= token.y_center
            <= label_token.y_center + 40
            and re.search(r"\d", token.text)
        ]
        if not numeric_tokens:
            continue
        split_x = (
            min(token.x_center for token in numeric_tokens)
            + max(token.x_center for token in numeric_tokens)
        ) / 2
        current = "".join(
            token.text
            for token in sorted(numeric_tokens, key=lambda item: item.x_center)
            if token.x_center <= split_x
        )
        previous = "".join(
            token.text
            for token in sorted(numeric_tokens, key=lambda item: item.x_center)
            if token.x_center > split_x
        )
        if _is_value(current) and _is_value(previous):
            return [
                str(row_token.text).strip() if row_token else row_number,
                label,
                current,
                previous,
            ]
    return None


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


@register("zhongmei")
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
        lines = _ocr_lines(pdf_bytes, pages)
        rows: list[list[str]] | None = None
        if table_id == "SOLVENCY_MAIN":
            rows = _rebuild_main(lines)
        elif table_id == "NON_LIFE_OPERATING_METRICS":
            rows = _rebuild_operating(lines)
        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            rows = _rebuild_returns(lines)
        elif table_id == "ACTUAL_CAPITAL":
            rows = _rebuild_capital(lines, minimum=False)
            total = _capital_total_row(
                pdf_bytes,
                pages,
                label="实际资本合计",
                row_number="5",
            )
            if rows and total:
                rows = [
                    row
                    for row in rows
                    if "实际资本合计" not in _compact(row[1] if len(row) > 1 else "")
                ]
                rows.append(total)
        elif table_id == "MINIMUM_CAPITAL":
            rows = _rebuild_capital(lines, minimum=True)
            total = _capital_total_row(
                pdf_bytes,
                pages,
                label="最低资本",
                row_number="4",
            )
            if rows and total:
                rows = [
                    row
                    for row in rows
                    if _compact(row[1] if len(row) > 1 else "") != "最低资本"
                ]
                rows.append(total)
        elif table_id == "LIQUIDITY_MONITORING":
            rows = _rebuild_liquidity(lines)
        if rows:
            _sync(table, rows, pages)
    return tables
