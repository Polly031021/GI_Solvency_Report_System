"""阳光信保专属提取后处理（2022-2025 版式）。

已覆盖的通用规则缺口：

1. 跨页表在页边界插入 ``【第N页表格分块】`` 占位行（实际资本表、最低资本表、
   经营指标表、流动性监测表都有）。该行既不是表头也不是项目，标准化时会被
   当成“无值项目”记一条警告，这里整行删除。
2. 实际资本表 / 最低资本表续页：续页线框是 ``行次 项目 期末数 期初数`` 四列，
   主表是 ``指标名称 期末数 期初数`` 三列。同一张表混排两种列宽，标准化按
   首列表头取宽度后会把“项目”当数值列读。这里统一回主表列宽：行次列丢弃，
   其余标签列合并成一个项目名。
3. 流动性风险监测指标：2022/2023 表体是合并单元格四列
   （``段名 项目 本季度数 上季度数``），段名只在每段首行出现；2022 年第十段的
   “指标值”行还被分页切到第 12 页页尾，段名落在第 13 页首行。这里把
   ``一、经营活动净现金流`` + ``指标值`` 还原成 ``经营活动净现金流``，
   孤立的“指标值”行按后文出现的段名补齐，避免 RAW 指标名只剩“指标值”。
4. 整表项目名被 PDF 折行拆开（``经营活动现金流入本年累 计数``、
   ``可资本化风险最低资 本``），标签内部空白统一去掉，RAW 指标名不再带空格。

只改 ``table.rows`` 与 ``table.table_blocks``（两者同步），不改 table_id /
page / source_pages；重复执行结果不变；异常不在这里吞掉。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

from . import register

# 合并表里跨页分块的占位行。
_BLOCK_MARK = "表格分块"
# 行次列：1、1.1、1.1.1、3.6、4*。
_ROWNO_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3})*\*?$")
# 流动性监测表的段名：一、经营活动净现金流 / 十、持有关联方资产占比。
_SECTION_RE = re.compile(r"^([一二三四五六七八九十]+)、(.+)$")
# 段内的指标行标签。
_INDICATOR_VALUE = "指标值"
_SPACE_RE = re.compile(r"\s+")

_LIQUIDITY_HEADER = ["监测指标", "本季度数", "上季度数"]
_PDF_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]
_RETURN_HEADER = ["项目", "数值"]
_PDF_LIQUIDITY_LABELS = (
    "经营活动净现金流",
    "百元保费经营活动净现金流",
    "特定业务现金流支出占比",
    "规模保费同比增速",
    "现金及流动性管理工具占比",
    "季均融资杠杆比例",
    "AA级（含）以下境内固定收益类资产占比",
    "持股比例大于5%的上市股票投资占比",
    "应收款项占比",
    "持有关联方资产占比",
)
_PDF_VALUE_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?%?|[-−–—]{1,2}")
_PDF_VALUE_LINE_RE = re.compile(
    r"^(?:[-+]?\d[\d,]*(?:\.\d+)?%?|[-−–—]{1,2})"
    r"(?:\s+(?:[-+]?\d[\d,]*(?:\.\d+)?%?|[-−–—]{1,2}))*$"
)
_RETURN_RE = re.compile(
    r"(?:近三年|三年)(?:的)?平均(?P<kind>综合)?投资收益率(?:为)?"
    r"(?P<value>[-−–—]{1,2}|[-+]?\d[\d,]*(?:\.\d+)?%?)"
)

_HANDLED = {
    "SOLVENCY_MAIN",
    "NON_LIFE_OPERATING_METRICS",
    "OPERATING_METRICS",
    "ACTUAL_CAPITAL",
    "MINIMUM_CAPITAL",
    "LIQUIDITY_MONITORING",
    "THREE_YEAR_INVESTMENT_RETURN",
}


def _strip(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _compact(value: Any) -> str:
    return _SPACE_RE.sub("", _strip(value))


def _cells(raw: Any) -> list[str]:
    cells = [_strip(cell) for cell in (raw or [])]
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _is_block_mark(cells: Sequence[str]) -> bool:
    return bool(cells) and _BLOCK_MARK in _compact(cells[0])


def _blocks_of(table: Any) -> list[dict[str, Any]]:
    blocks = list(getattr(table, "table_blocks", None) or [])
    if blocks:
        return [
            {"page": block.get("page") or getattr(table, "page", 1), "rows": list(block.get("rows") or [])}
            if isinstance(block, dict)
            else {"page": getattr(table, "page", 1), "rows": list(block or [])}
            for block in blocks
        ]
    return [{"page": getattr(table, "page", 1), "rows": list(getattr(table, "rows", None) or [])}]


def _table_width(blocks: Sequence[dict[str, Any]]) -> int:
    """主表列宽以第一块的首行（表头）为准。"""
    for block in blocks:
        for raw in block["rows"]:
            cells = _cells(raw)
            if not cells:
                continue
            return len(cells)
    return 0


def _clean_block(block: dict[str, Any], width: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in block["rows"]:
        cells = _cells(raw)
        if not cells or _is_block_mark(cells):
            continue
        if width and len(cells) > width:
            # 续页多出的是“行次”列：行次本身无信息量，直接丢掉；
            # 合并单元格拆出的多个标签列则拼回一个项目名。
            extra = len(cells) - width + 1
            label = "".join(cell for cell in cells[:extra] if cell and not _ROWNO_RE.match(cell))
            cells = [label, *cells[extra:]]
        elif width and len(cells) < width:
            cells = [*cells, *([""] * (width - len(cells)))]
        cells[0] = _compact(cells[0])
        rows.append(cells)
    return rows


def _liquidity_rows(blocks: Sequence[dict[str, Any]]) -> list[list[str]]:
    entries: list[list[Any]] = []  # [label, values, section]
    header: list[str] | None = None
    for block in blocks:
        for raw in block["rows"]:
            cells = _cells(raw)
            if not cells or _is_block_mark(cells):
                continue
            if header is None and _compact(cells[0]) == _LIQUIDITY_HEADER[0]:
                header = [_compact(cell) for cell in cells]
                continue
            if len(cells) >= 3:
                lead, values = cells[:-2], cells[-2:]
            else:
                lead, values = cells[:1], cells[1:]
            lead = [cell for cell in lead if cell]
            section = ""
            if len(lead) >= 2 and _SECTION_RE.match(_compact(lead[0])):
                section = _compact(lead[0])
                label = _compact("".join(lead[1:]))
                if label == _INDICATOR_VALUE:
                    # 段名与“指标值”分属两个单元格：这一段的值就是段指标值。
                    label = section + _INDICATOR_VALUE
            else:
                label = _compact("".join(lead))
            entries.append([label, list(values), section])

    for index, entry in enumerate(entries):
        label = entry[0]
        matched = _SECTION_RE.match(label)
        if matched:
            name = matched.group(2)
            entry[0] = name[: -len(_INDICATOR_VALUE)] if name.endswith(_INDICATOR_VALUE) else name
            continue
        if label != _INDICATOR_VALUE:
            continue
        # 分页把段名切到下一页时，页尾只剩一个孤立的“指标值”行。
        for later in entries[index + 1 :]:
            later_match = _SECTION_RE.match(str(later[2] or ""))
            if not later_match:
                continue
            name = later_match.group(2)
            entry[0] = name[: -len(_INDICATOR_VALUE)] if name.endswith(_INDICATOR_VALUE) else name
            break

    rows = [list(header or _LIQUIDITY_HEADER)]
    for label, values, _ in entries:
        if not label:
            continue
        rows.append([label, *values])
    return rows


def _pdf_compact(value: Any) -> str:
    return _SPACE_RE.sub("", unicodedata.normalize("NFKC", str(value or "")))


def _pdf_page_text(pdf_bytes: bytes, pages: Sequence[int]) -> str:
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return "\n".join(
            pdf[int(page) - 1].get_text()
            for page in pages
            if 1 <= int(page) <= len(pdf)
        )


def _pdf_monitoring_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts = [
        index
        for index, line in enumerate(lines)
        if "流动性风险监测指标" in _pdf_compact(line)
    ]
    if not starts:
        return []
    output: list[str] = []
    for line in lines[max(starts) :]:
        normalized = _pdf_compact(line)
        if output and any(
            marker in normalized
            for marker in ("近三年", "主要经营指标", "财产保险公司主要经营指标")
        ):
            break
        output.append(line)
    return output


def _pdf_values(line: str) -> list[str]:
    stripped = line.strip()
    if not _PDF_VALUE_LINE_RE.fullmatch(stripped):
        return []
    return _PDF_VALUE_RE.findall(stripped)


def _pdf_label_values(
    lines: Sequence[str], start: int, label_start: int, label_end: int
) -> list[str]:
    post: list[str] = []
    saw_indicator = False
    for line in lines[label_end + 1 :]:
        normalized = _pdf_compact(line)
        values = _pdf_values(line)
        if values and saw_indicator:
            post.extend(values)
            if len(post) >= 2:
                return post[:2]
            continue
        if values:
            break
        saw_indicator = saw_indicator or "指标值" in normalized
    for index in range(label_start - 1, start - 1, -1):
        if "指标值" not in _pdf_compact(lines[index]):
            continue
        before: list[str] = []
        for line in lines[index + 1 : label_start]:
            before.extend(_pdf_values(line))
            if len(before) >= 2:
                return before[:2]
        break
    return []


def _liquidity_rows_from_pdf(text: str) -> list[list[str]]:
    lines = _pdf_monitoring_lines(text)
    if not lines:
        return []
    rows: list[list[str]] = []
    cursor = 0
    for label in _PDF_LIQUIDITY_LABELS:
        target = _pdf_compact(label)
        buffer: list[str] = []
        run_start = cursor
        for index in range(cursor, len(lines)):
            if _pdf_values(lines[index]):
                run_start = index + 1
                continue
            buffer.append(_pdf_compact(lines[index]))
            if target not in "".join(buffer):
                continue
            label_start = run_start
            values = _pdf_label_values(lines, cursor, label_start, index)
            if len(values) >= 2:
                rows.append([label, values[0], values[1]])
            cursor = index + 1
            break
    return rows


def _return_rows_from_pdf(text: str) -> list[list[str]]:
    found: dict[str, str] = {}
    for match in _RETURN_RE.finditer(_pdf_compact(text)):
        kind = "综合" if match.group("kind") else ""
        found.setdefault(kind, match.group("value"))
    return [
        [f"近三年平均{kind}投资收益率", found[kind]]
        for kind in ("", "综合")
        if kind in found
    ]


@register("ygxb")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """阳光信保专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id not in _HANDLED:
            continue
        blocks = _blocks_of(table)
        if table_id in {"LIQUIDITY_MONITORING", "THREE_YEAR_INVESTMENT_RETURN"}:
            pages = [
                int(page)
                for page in (getattr(table, "source_pages", None) or [])
                if int(page) > 0
            ]
            if not pages:
                pages = list(
                    (getattr(table, "table_config", {}) or {}).get("_confirmed_pages") or []
                )
            text = _pdf_page_text(pdf_bytes, pages) if pages else ""
            if table_id == "LIQUIDITY_MONITORING":
                body = _liquidity_rows_from_pdf(text)
                rows = [_PDF_LIQUIDITY_HEADER, *body] if body else _liquidity_rows(blocks)
            else:
                body = _return_rows_from_pdf(text)
                rows = [_RETURN_HEADER, *body] if body else []
        elif table_id == "LIQUIDITY_MONITORING":
            rows = _liquidity_rows(blocks)
        else:
            width = _table_width(blocks)
            rows = []
            for block in blocks:
                rows.extend(_clean_block(block, width))
        if not rows:
            continue
        page = int(blocks[0].get("page") or getattr(table, "page", 1) or 1)
        # rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。
        table.rows = [list(row) for row in rows]
        table.table_blocks = [{"page": page, "rows": [list(row) for row in rows]}]
    return tables
