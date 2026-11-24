"""富邦财险专属提取后处理。

通用规则在富邦的资本章节（十、实际资本 / 十一、最低资本）会把表格识别成
“文字对齐”网格，行标签被拆成单字列（``认可 | 资 | 产 | 134,935.15``），
数值列位置逐页漂移，无法直接标准化；经营表则带有跨页分块标记行、重复表头
和分组标题行。这里的处理全部基于 PDF 文字层重建，不写死年份与页码：

1. ``ACTUAL_CAPITAL``：按“十、实际资本”到“十一、最低资本”之间的正文重建
   7 行 × 3 列（核心二级/附属一级/附属二级在源表就是空白，保持空白）。
2. ``MINIMUM_CAPITAL``：按“十一、最低资本”到“十二、”之间的正文重建长表，
   续页重复的“指标名称/本季度末/上季度末”表头与页码行自动跳过。
3. ``NON_LIFE_OPERATING_METRICS``：清掉分块标记行、重复表头与分组标题，
   并给每个表块声明源表单位（富邦 2022/2023 经营表是“元”，2024/2025 是
   “万元”，单位行紧跟在“主要经营指标”标题之后）。
4. ``SOLVENCY_MAIN``：去掉行次前缀（``1认可资产`` → ``认可资产``），
   只影响展示，不影响标准化（标准化层本身也会剥行次）。

约定：只改 ``rows`` 与 ``table_blocks``；重复执行结果不变；异常不吞掉。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_ACTUAL_HEADER = ["指标名称", "本季度末", "上季度末"]
_MINIMUM_HEADER = ["指标名称", "本季度末", "上季度末"]
_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]

# 十、实际资本 的 7 个行标签（顺序即报告顺序）。
_ACTUAL_LABELS = (
    "认可资产",
    "认可负债",
    "实际资本",
    "其中：核心一级资本",
    "核心二级资本",
    "附属一级资本",
    "附属二级资本",
)

_HEADER_TERMS = {
    "指标名称",
    "项目",
    "本季度末",
    "上季度末",
    "本季度末数",
    "上季度末数",
    "本季度数",
    "本年累计数",
    "期末数",
    "期初数",
}
_DASHES = {"-", "--", "—", "–"}
_UNIT_RE = re.compile(r"^单位[：:]\s*(亿元|万元|千元|百元|元)$")
_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_NUMBER_RE = re.compile(r"^\s*(?:\d+(?:[.．]\d+)*\s*[.．、]?)\s*")
# 目录行：“十、实际资本…………22”/“十一、最低资本......22”，不能当章节起点。
_TOC_RE = re.compile(r"[…]{2,}|\.{4,}")


def _clean(value: Any) -> str:
    """去掉全角/半角空白；富邦表格内的标签与数值都没有有意义空格。"""
    return re.sub(r"\s+", "", str(value or "").replace("\u3000", " "))


def _is_number(text: str) -> bool:
    return bool(_NUMBER_RE.match(text)) or text in _DASHES


def _is_noise(text: str) -> bool:
    return (
        not text
        or text in _HEADER_TERMS
        or bool(_UNIT_RE.match(text))
    )


def _document_lines(page_texts: Sequence[str]) -> list[str]:
    lines: list[str] = []
    for text in page_texts:
        for raw in str(text or "").splitlines():
            cleaned = _clean(raw)
            if cleaned and not _TOC_RE.search(cleaned):
                lines.append(cleaned)
    return lines


def _section(
    lines: Sequence[str],
    start_predicate,
    stop_predicate,
) -> list[str]:
    start = next(
        (index for index, line in enumerate(lines) if start_predicate(line)),
        None,
    )
    if start is None:
        return []
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if stop_predicate(lines[index]):
            end = index
            break
    return list(lines[start + 1 : end])


def _section_unit(segment: Sequence[str]) -> str:
    for line in segment:
        match = _UNIT_RE.match(line)
        if match:
            return match.group(1)
    return ""


def _parse_actual_capital(segment: Sequence[str]) -> tuple[list[list[str]], str]:
    """重建实际资本表；源表空白单元格保持空白。"""
    rows: list[list[str]] = []
    current: list[str] | None = None
    for line in segment:
        if line in _ACTUAL_LABELS:
            current = [line, "", ""]
            rows.append(current)
            continue
        if current is None or not _is_number(line):
            continue
        if not current[1]:
            current[1] = line
        elif not current[2]:
            current[2] = line
    return [_ACTUAL_HEADER, *rows], _section_unit(segment)


def _parse_minimum_capital(segment: Sequence[str]) -> tuple[list[list[str]], str]:
    """重建最低资本长表：标签行（可跨行续写）+ 两个数值行。

    续页重复表头、页码行由 ``_is_noise`` 跳过；只有标签、还没读到数值时
    遇到非数值行，视为标签换行（如“交易对手违约风险-套期保值外汇远期和
    利率”+“互换”），直接拼接。
    """
    rows: list[list[str]] = []
    label = ""
    values: list[str] = []
    for line in segment:
        if _is_noise(line):
            continue
        if _is_number(line):
            if not label:
                continue  # 页脚页码等游离数字
            values.append(line)
            if len(values) >= 2:
                rows.append([label, values[0], values[1]])
                label, values = "", []
            continue
        if label and not values:
            label += line
            continue
        if label:
            # 上一行只读到 1 个数值（页边界），保留已有值，开始新标签。
            rows.append([label, *values, *[""] * (2 - len(values))])
            label, values = "", []
        label = line
    if label:
        rows.append([label, *values, *[""] * (2 - len(values))])
    return [_MINIMUM_HEADER, *rows], _section_unit(segment)


def _parse_operating_unit(page_texts: Sequence[str]) -> str:
    """从“主要经营指标”标题后的单位行取源表单位（元/万元）。"""
    for text in page_texts:
        lines = [_clean(row) for row in str(text or "").splitlines()]
        for index, line in enumerate(lines):
            if "主要经营指标" not in line:
                continue
            for follower in lines[index + 1 : index + 4]:
                match = _UNIT_RE.match(follower)
                if match:
                    return match.group(1)
    return ""


def _normalize_operating(rows: Sequence[Sequence[Any]]) -> list[list[str]]:
    """清掉分块标记/重复表头/空分组行，统一三列表头。"""
    output: list[list[str]] = [list(_OPERATING_HEADER)]
    for row in rows:
        cells = [_clean(cell) for cell in row]
        if not cells:
            continue
        label = cells[0]
        if not label:
            continue
        if label.startswith("【第") and "表格分块" in label:
            continue
        if label in {"主要经营指标", "指标名称", "项目"}:
            continue
        if len(label) > 48:
            continue  # 正文段落误识别成的“指标行”
        values = cells[1:]
        # 2024 的 p13 整页回退会把“1) 由风险管理部牵头……”这类正文段落
        # 当成表格行；指标行的数值列只能是数字/占位符，否则一律丢弃。
        if not all((not value) or _is_number(value) for value in values):
            continue
        if all((not value) or value in _DASHES for value in values):
            # 只丢“（十一）效益类指标 -- --”这类分组标题；
            # “5.4其他渠道签单保费 - -”是源表未披露的指标，保留为缺失。
            if label.endswith("类指标"):
                continue
        output.append(
            [
                label,
                values[0] if len(values) > 0 else "",
                values[1] if len(values) > 1 else "",
            ]
        )
    return output


def _strip_row_number(label: str) -> str:
    stripped = _ROW_NUMBER_RE.sub("", label, count=1)
    return stripped or label


def _sync_table(table: Any, rows: list[list[str]], *, unit: str = "") -> None:
    """rows 与 table_blocks 必须同步（STEP3 优先读 blocks）。"""
    table.rows = [list(row) for row in rows]
    block: dict[str, Any] = {
        "page": int(getattr(table, "page", 0) or 1),
        "rows": [list(row) for row in rows],
    }
    if unit:
        block["unit"] = unit
    table.table_blocks = [block]


@register("fubang")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_texts = [doc.load_page(index).get_text() or "" for index in range(len(doc))]
        lines = _document_lines(page_texts)
        operating_unit = _parse_operating_unit(page_texts)

        actual_segment = _section(
            lines,
            lambda line: line.startswith("十、实际资本"),
            lambda line: line.startswith("十一、"),
        )
        minimum_segment = _section(
            lines,
            lambda line: line.startswith("十一、最低资本"),
            lambda line: line.startswith("十二、"),
        )

        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "ACTUAL_CAPITAL" and actual_segment:
                rows, unit = _parse_actual_capital(actual_segment)
                if len(rows) > 1:
                    _sync_table(table, rows, unit=unit)
            elif table_id == "MINIMUM_CAPITAL" and minimum_segment:
                rows, unit = _parse_minimum_capital(minimum_segment)
                if len(rows) > 1:
                    _sync_table(table, rows, unit=unit)
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                rows = _normalize_operating(list(getattr(table, "rows", []) or []))
                _sync_table(table, rows, unit=operating_unit)
            elif table_id == "SOLVENCY_MAIN":
                rows = [
                    [str(cell or "").strip() for cell in row]
                    for row in (getattr(table, "rows", []) or [])
                ]
                if rows:
                    rows[0] = [str(cell or "").strip() for cell in rows[0]]
                    for row in rows[1:]:
                        if row:
                            row[0] = _strip_row_number(row[0])
                table.rows = rows
                for block in list(getattr(table, "table_blocks", None) or []):
                    if isinstance(block, dict) and block.get("rows"):
                        fixed = [
                            [str(cell or "").strip() for cell in row]
                            for row in block["rows"]
                        ]
                        for row in fixed[1:]:
                            if row:
                                row[0] = _strip_row_number(row[0])
                        block["rows"] = fixed
    finally:
        doc.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("fubang", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "fubang",
        pdf_bytes,
        table_configs,
        mode="document",
    )
