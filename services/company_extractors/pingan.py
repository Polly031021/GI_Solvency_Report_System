"""平安产险专属提取后处理（2022 版式试点）。

已覆盖的通用规则缺口：

1. ``偿付能力充足率指标``：pdfplumber 把“本季度末数/上季度末数”两列
   合并成 ``50,456,00551,044,518`` 且首列留空，导致认可资产/认可负债/
   量化风险最低资本等被记成 0 或 1e15 量级错误值。这里按逗号分组拆分。
2. ``主要经营指标``：2022 年第 17 页的效益类/规模类/品质类指标不在
   pdfplumber 识别出的表格块里，导致综合成本率、费用率等缺失。这里用
   PDF 文本行重建“指标名 + 本季度数 + 本年度累计数”。
3. 签单保费不在经营表内，而在下一页“公司分类”表中；单独提取并追加。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

# 支持：金额粘连（50,456,00551,044,518）、负数粘连（-132,042-129,107）、
# 百分比粘连（177.6%191.2%）。
MERGED_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|-?\d+(?:\.\d+)?%?"
)
NUMBER_LINE_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
SIGNED_PREMIUM_RE = re.compile(
    r"签单保费[（(]万元[）)][^\d\-]*(-?[\d,]+(?:\.\d+)?)"
)

_OPERATING_HEADER = ("指标名称", "本季度数", "本年度累计数")

# 2023 版式把首列表头与期间粘在一起（“类型本季度末”），需要拆回两列。
_HEADER_PERIOD_TERMS = ("本季度末数", "本季度末", "本季度数", "本季度", "上季度末", "上季度数")


def _fix_solvency_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    header = [str(cell or "").strip() for cell in rows[0]]
    if len(header) >= 2 and header[0]:
        for term in _HEADER_PERIOD_TERMS:
            if term in header[0] and not header[0].startswith(term):
                prefix = header[0].replace(term, "", 1).strip()
                if prefix:
                    rows[0] = [prefix, term, *header[1:]]
                    break
    return rows


def _clean_cell(value: Any) -> str:
    return str(value or "").replace(" ", "").replace("\u3000", "").strip()


def _split_merged_numbers(text: Any) -> list[str] | str | None:
    compact = _clean_cell(text)
    if not compact:
        return None
    parts = MERGED_NUMBER_RE.findall(compact)
    if len(parts) >= 2 and "".join(parts) == compact:
        return parts
    return None


def _is_number_line(line: str) -> bool:
    return bool(NUMBER_LINE_RE.match(_clean_cell(line)))


def _looks_like_label(value: Any) -> bool:
    """中文标签：非空、无数字、不是占位符。"""
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"\d", text):
        return False
    return not text.startswith(("-", "—", "–"))


def _fix_solvency_main(rows: list[list[str]]) -> list[list[str]]:
    """修复平安偿付能力主表的两类版式问题。

    1. 行标签被换行拆成两列（``核心一 | 级资本``），先把前两列拼回标签；
    2. “本季度末/上季度末”两列金额粘连（``5,829,6865,700,074``），
       按数字模式重新拆开。
    """
    fixed: list[list[str]] = []
    for row_index, row in enumerate(rows):
        cells = [str(cell or "").strip() for cell in row]
        if row_index == 0:
            fixed.append(cells)
            continue
        if (
            len(cells) >= 3
            and _looks_like_label(cells[0])
            and _looks_like_label(cells[1])
            and not cells[0].startswith(("指标名称", "项目"))
        ):
            cells = [f"{cells[0]}{cells[1]}", *cells[2:]]
        for index in range(1, len(cells)):
            parts = _split_merged_numbers(cells[index])
            if isinstance(parts, list) and len(parts) >= 2:
                if index > 1 and not cells[index - 1]:
                    # 前一列是空占位（认可资产/认可负债行常见），
                    # 用拆出的第一个值填掉它，避免多出一列导致整体错位。
                    cells = [
                        *cells[: index - 1],
                        *parts,
                        *cells[index + 1:],
                    ]
                else:
                    cells = [*cells[:index], *parts, *cells[index + 1:]]
                break
        fixed.append(cells)
    return fixed


def _page_text(doc, page_number: int) -> str:
    if page_number < 1 or page_number > len(doc):
        return ""
    return doc.load_page(page_number - 1).get_text() or ""


def _parse_operating_rows(text: str) -> list[list[str]]:
    """从“指标名称/本季度数/本年度累计数”表重建数据行。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        start = next(
            index for index, line in enumerate(lines) if line.startswith("指标名称")
        )
    except StopIteration:
        return []
    rows: list[list[str]] = []
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith(("五、", "（四）", "（三）流动性")):
            break
        if line in {"本季度数", "本年度累计数"}:
            index += 1
            continue
        if _is_number_line(line):
            index += 1
            continue
        # 只接受像指标名的行：短、不以括号/冒号开头。
        if len(line) > 48 or line.startswith(("（", "(", "单位：", "注：")):
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and _is_number_line(lines[cursor]) and len(values) < 2:
            values.append(_clean_cell(lines[cursor]))
            cursor += 1
        if values:
            rows.append([line, *values])
            index = cursor
        else:
            index += 1
    return rows


def _complete_operating(table: Any, doc) -> list[list[str]]:
    rows = [list(row) for row in getattr(table, "rows", []) or []]
    pages = list(getattr(table, "source_pages", []) or [getattr(table, "page", 1)])
    parsed: list[list[str]] = []
    for page_number in pages:
        parsed.extend(_parse_operating_rows(_page_text(doc, int(page_number))))

    existing = {str(row[0]).strip() for row in rows if row and str(row[0]).strip()}
    for row in parsed:
        label = str(row[0]).strip()
        if not label or label in existing:
            continue
        rows.append(row)
        existing.add(label)

    # 签单保费在下一页“公司分类”表里，单独追加到“本年度累计数”列。
    if not any("签单保费" in str(row[0]) for row in rows[1:] if row):
        next_page = int(pages[-1] if pages else getattr(table, "page", 1)) + 1
        match = SIGNED_PREMIUM_RE.search(_page_text(doc, next_page))
        if match:
            rows.append(["签单保费（万元）", "", match.group(1)])
    return rows


def _sync_blocks(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


@register("pingan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """平安专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                fixed_rows = _fix_solvency_main(
                    _fix_solvency_header(list(table.rows or []))
                )
                # 页内表块同样需要拆分，否则 STEP3 仍会读到旧值。
                for block in list(getattr(table, "table_blocks", None) or []):
                    if isinstance(block, dict) and block.get("rows"):
                        block["rows"] = _fix_solvency_main(
                            _fix_solvency_header(
                                [list(row) for row in block["rows"]]
                            )
                        )
                table.rows = fixed_rows
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                rows = _complete_operating(table, doc)
                pages = list(
                    getattr(table, "source_pages", [])
                    or [getattr(table, "page", 1)]
                )
                _sync_blocks(table, rows, int(pages[0] if pages else 1))
    finally:
        doc.close()
    return tables
