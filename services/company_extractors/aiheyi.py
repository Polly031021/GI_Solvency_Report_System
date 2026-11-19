"""爱和谊（爱和谊日生同和财产保险（中国）有限公司）专属提取后处理。

四年报告都是“偿付能力数据补充/更正公告”，正文表格有三种通用规则处理不好的
版式：

1. ``SOLVENCY_MAIN``：2023 年主表跨第 10-11 页，第 10 页上方还有薪酬区间表；
   pdfplumber 把首列“行次+项目”合并、把本季度数拆成 ``2 | 81,327.94``，
   第 11 页又按 11 列错位输出。这里按页面文字层逐行重建，只保留
   “认可资产 … 综合偿付能力充足率”区间。
2. ``NON_LIFE_OPERATING_METRICS``：经营表没有表格线，文字对齐识别会把
   标签拆成单字列、把数值拆成多段；续页还混入“（五）近三年收益率”表头。
   这里按“（四）主要经营指标”到“（五）/五、风险管理能力”的文字段重建。
3. ``LIQUIDITY_MONITORING``：监测表与监管指标、经营指标同页，通用规则输出
   整页混合表，且本公司没有“综合退保率”。这里按“（三）流动性风险监测指标”
   到“（四）”的文字段重建，仅保留 1-10 行。

约定：只改 ``rows`` 与 ``table_blocks``；解析不到内容时对经营表清空（避免
把“近三年收益率”续页块混进来），其余表保持原样；重复执行结果不变。
"""

from __future__ import annotations

import io
import re
from typing import Any, Sequence

from . import register

_SOLVENCY_HEADER = ["指标名称", "本季度数", "上季度数", "下季度末预测数"]
_OPERATING_HEADER = ["指标名称", "本季度数", "本年累计数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_RE = re.compile(r"^(\d+(?:[.．]\d+)*)\s+(.+)$")
_DASHES = {"-", "--", "—", "–", "－"}
_STOP_SOLVENCY = ("（二", "（三", "（四", "五、")
_STOP_OPERATING = ("（五", "五、", "近三年")
_STOP_LIQUIDITY = ("（四", "（五", "五、")


def _page_texts(pdf_bytes: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _is_number(token: str) -> bool:
    return bool(_NUMBER_RE.match(token)) or token in _DASHES


def _split_row(line: str) -> tuple[str, list[str]] | None:
    """把 ``1 认可资产 281,327.94 271,115.56`` 拆成行号、标签和数值。"""
    match = _ROW_RE.match(line)
    if not match:
        return None
    number, rest = match.group(1), match.group(2)
    tokens = rest.split()
    values: list[str] = []
    while tokens and _is_number(tokens[-1]) and len(values) < 3:
        values.insert(0, tokens.pop())
    label = "".join(tokens).strip()
    if not label:
        return None
    return number, [label, *values]


def _parse_solvency(text: str) -> list[list[str]]:
    lines = _lines(text)
    start: int | None = None
    for index, line in enumerate(lines):
        # 只认偿付能力表表头；同页的流动性监管表表头是“行次 指标名称…”。
        if (
            line.startswith("行次")
            and "本季度数" in line
            and "指标名称" not in line
        ):
            start = index + 1
            break
    if start is None:
        # 2023 年第 11 页是续页，没有表头，从 4.1.3 市场风险开始。
        for index, line in enumerate(lines):
            if re.match(r"^\d+(?:[.．]\d+)*\s+", line) and any(
                key in line
                for key in (
                    "市场风险最低资本",
                    "认可资产",
                    "核心偿付能力充足率",
                )
            ):
                start = index
                break
    if start is None:
        return []
    rows: list[list[str]] = []
    for line in lines[start:]:
        if line.startswith(_STOP_SOLVENCY):
            break
        parsed = _split_row(line)
        if not parsed:
            continue
        _, cells = parsed
        if not cells or not _is_number(cells[1] if len(cells) > 1 else ""):
            # 该表所有数据行至少有一个数值或占位符。
            if len(cells) < 2 or not any(_is_number(value) for value in cells[1:]):
                continue
        values = (cells[1:] + ["", "", ""])[:3]
        rows.append([cells[0], *values])
    return rows


def _parse_operating(text: str) -> list[list[str]]:
    lines = _lines(text)
    start: int | None = None
    for index, line in enumerate(lines):
        if "主要经营指标" in line:
            start = index + 1
            break
        if re.match(r"^\d+(?:[.．]\d+)*\s+", line) and any(
            key in line for key in ("净资产收益率", "保险业务收入", "签单保费")
        ):
            start = index
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    for line in lines[start:]:
        if line.startswith(_STOP_OPERATING):
            break
        if line.startswith("行次") or "指标名称" in line:
            continue
        parsed = _split_row(line)
        if not parsed:
            continue
        _, cells = parsed
        # 经营表每行固定“本季度数 + 本年累计数”；分组标题没有数值。
        if len(cells) < 3 or not _is_number(cells[1]) or not _is_number(cells[2]):
            continue
        rows.append([cells[0], cells[1], cells[2]])
    return rows


def _parse_liquidity(text: str) -> list[list[str]]:
    lines = _lines(text)
    start: int | None = None
    for index, line in enumerate(lines):
        if "流动性风险监测指标" in line:
            start = index + 1
            break
        if re.match(r"^10\s+持有关联方资产占比", line):
            start = index
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    for line in lines[start:]:
        if line.startswith(_STOP_LIQUIDITY):
            break
        if line.startswith("行次") or "指标名称" in line:
            continue
        parsed = _split_row(line)
        if not parsed:
            continue
        number, cells = parsed
        if number not in {str(value) for value in range(1, 11)}:
            continue
        if len(cells) < 3 or not _is_number(cells[1]) or not _is_number(cells[2]):
            continue
        rows.append([cells[0], cells[1], cells[2]])
    return rows


def _table_blocks(table: Any) -> list[dict[str, Any]]:
    """取表格的逐页分块；单页表也要归一成 block 列表。"""
    blocks: list[dict[str, Any]] = []
    for raw in getattr(table, "table_blocks", None) or []:
        blocks.append(
            {
                "page": int(raw.get("page") or 0),
                "rows": [list(row) for row in (raw.get("rows") or [])],
                "unit": str(raw.get("unit") or ""),
            }
        )
    if not blocks:
        blocks.append(
            {
                "page": int(getattr(table, "page", 0) or 0),
                "rows": [list(row) for row in (getattr(table, "rows", None) or [])],
                "unit": str(getattr(table, "unit", "") or ""),
            }
        )
    return blocks


_PARSERS = {
    "SOLVENCY_MAIN": (_parse_solvency, _SOLVENCY_HEADER, "万元"),
    "NON_LIFE_OPERATING_METRICS": (_parse_operating, _OPERATING_HEADER, "万元"),
    "LIQUIDITY_MONITORING": (_parse_liquidity, _LIQUIDITY_HEADER, ""),
}


@register("aiheyi")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    texts = _page_texts(pdf_bytes)
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        config = _PARSERS.get(table_id)
        if config is None:
            continue
        parser, header, unit = config
        new_blocks: list[dict[str, Any]] = []
        for block in _table_blocks(table):
            page = block["page"]
            text = texts[page - 1] if 1 <= page <= len(texts) else ""
            rows = parser(text) if text else []
            if not rows:
                # 经营表续页可能只剩“（五）近三年”表头，清掉该块避免污染；
                # 其余表解析失败时保留原块作为兜底。
                if table_id == "NON_LIFE_OPERATING_METRICS":
                    continue
                new_blocks.append(block)
                continue
            new_blocks.append(
                {
                    "page": page,
                    "rows": [list(header), *rows],
                    "unit": unit or block["unit"],
                }
            )
        if table_id == "NON_LIFE_OPERATING_METRICS":
            # 首段（1-6 行）与续段（7 行以后）分处两页；定位器只选中其中一页时，
            # 对尚未覆盖、但能解析出经营指标行的页面补块（2024 年补首段，
            # 2025 年补第 12 页 12.1-12.5.4 续段）。
            covered = {block["page"] for block in new_blocks}
            for page, text in enumerate(texts, start=1):
                if page in covered:
                    continue
                rows = parser(text)
                if rows:
                    new_blocks.append(
                        {
                            "page": page,
                            "rows": [list(header), *rows],
                            "unit": unit,
                        }
                    )
            new_blocks.sort(key=lambda block: block["page"])
        if not new_blocks:
            table.rows = []
            table.table_blocks = []
            continue
        merged: list[list[str]] = [list(header)]
        for block in new_blocks:
            for row in block["rows"]:
                if row == header:
                    continue
                merged.append(row)
        if table_id == "LIQUIDITY_MONITORING" and not any(
            "持有关联方资产占比" in row[0] for row in merged[1:]
        ):
            # 2023 年第 10 行落在下一页页首，整页识别时被丢掉，单独补回。
            next_page = new_blocks[-1]["page"] + 1
            if 1 <= next_page <= len(texts):
                extra = [
                    row
                    for row in _parse_liquidity(texts[next_page - 1])
                    if "持有关联方资产占比" in row[0]
                ]
                if extra:
                    new_blocks.append(
                        {
                            "page": next_page,
                            "rows": [list(header), *extra],
                            "unit": "",
                        }
                    )
                    merged.extend(extra)
        table.page = new_blocks[0]["page"]
        table.rows = merged
        table.table_blocks = new_blocks
    return tables
