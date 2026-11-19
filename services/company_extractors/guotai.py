"""国泰产险专属提取后处理。

通用规则在国泰四份季报上能定位到全部目标表，残留问题都来自版式细节：

1. 实际资本表、最低资本表跨页时，续页没有表头，通用网格保留 PDF 的
   四列（行次/项目/期末数/期初数），而首块是“指标名称/期末数/期初数”
   三列；STEP3 把首块三列表头补到四列后，行次会被当成指标名，数值整
   行错位。这里把续块四列压成三列，并同步 ``table_blocks``（STEP3 只
   优先读 blocks）。
2. 跨页合并会在 ``table.rows`` 插入 ``【第N页表格分块】`` 占位行，显示
   与标准化都不需要，统一删除。
3. 2023 年经营指标页没有“单位：元”字样，通用嗅探得到“未识别”；该表
   数值仍为元，这里显式标注为元，避免诊断噪音（数值换算仍由 STEP3
   按元→万元处理）。
4. 近三年投资收益率只有两行且不带表头，通用嗅探把 ``%`` 值标成“元”；
   这里按单元格中的百分号改为 ``%``。

所有修复都是幂等的：重复执行不会改变行数、数值与单位。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_BLOCK_MARK_RE = re.compile(r"^【第\d+页表格分块】$")
_HEADER_LABELS = {"指标名称", "项目", "行次"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).replace("\r", " ").replace("\n", " ").strip()


def _is_block_marker(row: Sequence[Any]) -> bool:
    cells = [_text(cell) for cell in row if _text(cell)]
    return len(cells) == 1 and bool(_BLOCK_MARK_RE.match(cells[0]))


def _is_header_row(row: Sequence[Any]) -> bool:
    cells = {_text(cell) for cell in row if _text(cell)}
    return bool(cells & _HEADER_LABELS) and len(cells) <= 4


def _compact_row(row: Sequence[Any]) -> list[str]:
    """把续页的 [行次, 项目, 期末, 期初] 压成 [项目, 期末, 期初]。"""
    cells = [_text(cell) for cell in row]
    while cells and not cells[-1]:
        cells.pop()
    while cells and not cells[0]:
        cells.pop(0)
    if (
        len(cells) == 4
        and _ROW_NO_RE.match(cells[0])
        and cells[1]
        and not _ROW_NO_RE.match(cells[1])
    ):
        return cells[1:]
    return cells


def _normalize_capital_table(table: Any) -> None:
    blocks = getattr(table, "table_blocks", None)
    if not isinstance(blocks, list) or not blocks:
        return

    normalized: list[tuple[Any, list[list[str]]]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        rows = block.get("rows")
        if not isinstance(rows, list):
            continue
        cleaned: list[list[str]] = []
        for row in rows:
            if _is_block_marker(row):
                continue
            cells = _compact_row(row)
            if any(cells):
                cleaned.append(cells)
        normalized.append((block, cleaned))

    if not normalized:
        return

    flat: list[list[str]] = []
    for index, (_block, rows) in enumerate(normalized):
        data = rows
        if index:
            # 续块若自带表头，平面表只保留首块表头一份。
            while data and _is_header_row(data[0]):
                data = data[1:]
        flat.extend(data)

    for block, rows in normalized:
        block["rows"] = rows
    if flat:
        table.rows = flat


def _fix_operating_unit(table: Any) -> None:
    unit = str(getattr(table, "unit", "") or "").strip()
    if unit and unit != "未识别":
        return
    for row in getattr(table, "rows", []) or []:
        for cell in row:
            number = re.sub(r"[,，\s]", "", _text(cell))
            if re.fullmatch(r"-?\d+(?:\.\d+)?", number):
                try:
                    if abs(float(number)) >= 10000:
                        table.unit = "元"
                        table.unit_source = "元"
                        return
                except ValueError:
                    continue


def _fix_three_year_unit(table: Any) -> None:
    rows = getattr(table, "rows", []) or []
    if any(_text(cell).endswith("%") for row in rows if row for cell in row):
        table.unit = "%"
        table.unit_source = "%"


@register("guotai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """国泰产险专属后处理入口。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}:
            _normalize_capital_table(table)
        elif table_id == "NON_LIFE_OPERATING_METRICS":
            _fix_operating_unit(table)
        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            _fix_three_year_unit(table)
    return tables
