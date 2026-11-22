"""人保产险专属提取后处理（2022-2025 版式）。

通用规则在人保报告上的四个缺口：

1. 偿付能力主表表头被版面拆成 ``基本情景下的下一季 度预测数``，
   ``solvency_normalizer._period_header`` 按子串匹配时认不出“季度预测数”，
   2022 年 16 条下季度预测记录被当作非法期间丢弃。这里去掉表头空白。
2. ``实际资本表`` / ``最低资本表`` 的第二页是无表头续页，通用流程只
   组装第一页，2022 年最低资本表甚至只留下 2 行。这里按 PDF 文本行
   重建整表（行次 + 标签 + 期末 + 期初），并按页保留 table_blocks。
3. ``主要经营指标`` 跨页处把“5.2 直销渠道签单保费”拆成两行，且夹杂
   “【第 N 页表格分块】”与分组标题行。这里删标记/分组行并合并标签。
4. ``流动性风险监测指标`` 的定位页同时命中偿付能力主表，rows 前 16 行
   是主表副本。这里只保留“监测指标”表头及其后的监测明细。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_VALUE_RE = re.compile(r"^-$|^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_MARKER_RE = re.compile(r"^【第\d+页表格分块】$")
_GROUP_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+、)?主要经营指标$"
    r"|^（(?:十一|十二)）(?:效益类|规模类)指标$"
)
_CAPITAL_HEADER = ["指标名称", "期末数", "期初数"]
# 流动性表把章节号（一、二、……十、）和“指标值”字样写进了指标名称列，
# 例如“一、经营活动净 现金流指标值”；清洗后才是可匹配的指标名。
_LIQ_SECTION_RE = re.compile(r"^\s*[一二三四五六七八九十]{1,3}\s*[、.．]\s*")
_LIQ_METRIC_SUFFIX_RE = re.compile(r"\s*指标值\s*$")


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _clean_liquidity_label(value: Any) -> str:
    """去掉流动性表指标名称里的章节号和“指标值”后缀。"""
    text = _clean(value)
    text = _LIQ_SECTION_RE.sub("", text)
    text = _LIQ_METRIC_SUFFIX_RE.sub("", text)
    return text


def _page_lines(page: Any) -> list[str]:
    return [line.strip() for line in page.get_text("text").splitlines() if line.strip()]


def _parse_numbered_rows(lines: Sequence[str]) -> list[list[str]]:
    """把“行次 / 标签 / 期末 / 期初”逐行文本还原成 3 列数据行。"""
    parsed: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        if _ROW_NO_RE.match(line):
            if current is not None:
                parsed.append(current)
            current = {"label": [], "values": []}
            continue
        if current is None:
            continue
        if _VALUE_RE.match(line) and len(current["values"]) < 2:
            current["values"].append(line)
        elif not current["values"]:
            current["label"].append(line)
    if current is not None:
        parsed.append(current)

    rows: list[list[str]] = []
    for item in parsed:
        label = "".join(item["label"]).strip()
        if not label:
            continue
        values = list(item["values"]) + [""] * (2 - len(item["values"]))
        rows.append([label, values[0], values[1]])
    return rows


def _header_start(lines: Sequence[str]) -> int:
    for index, line in enumerate(lines[:14]):
        if line.replace(" ", "") == "期初数":
            return index + 1
    return 0


def _rebuild_numbered_table(table: Any, doc: Any) -> None:
    """按 source_pages 重建实际资本/最低资本表，rows 与 blocks 同步。"""
    pages = [int(page) for page in (getattr(table, "source_pages", None) or [])]
    if not pages:
        pages = [int(getattr(table, "page", 1) or 1)]
    blocks: list[dict[str, Any]] = []
    for index, page_no in enumerate(pages):
        if not 1 <= page_no <= doc.page_count:
            continue
        lines = _page_lines(doc[page_no - 1])
        if index == 0:
            start = _header_start(lines)
        else:
            start = 0
            if lines and lines[0] == str(page_no):
                start = 1
        rows = _parse_numbered_rows(lines[start:])
        if index == 0:
            rows = [list(_CAPITAL_HEADER)] + rows
        if rows:
            blocks.append({"page": page_no, "rows": rows})
    if not blocks:
        return
    flat: list[list[str]] = []
    for block in blocks:
        for row in block["rows"]:
            if not flat and row == _CAPITAL_HEADER:
                flat.append(list(row))
            elif row != _CAPITAL_HEADER:
                flat.append(list(row))
    table.rows = flat
    table.table_blocks = blocks


def _fix_solvency_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    header = [_clean(cell) for cell in rows[0]]
    if any("预测数" in cell for cell in header):
        rows[0] = [re.sub(r"\s+", "", cell) for cell in header]
    return rows


def _merge_operating_rows(rows: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    index = 0
    while index < len(rows):
        cells = [_clean(cell) for cell in rows[index]]
        label = cells[0] if cells else ""
        if (
            label.startswith("5.2")
            and "签单保" in label
            and not label.endswith("费")
            and index + 1 < len(rows)
            and _clean(rows[index + 1][0] if rows[index + 1] else "") == "费"
        ):
            cells[0] = label + "费"
            index += 2
            merged.append(cells)
            continue
        merged.append(cells)
        index += 1
    return merged


def _is_group_row(cells: Sequence[str]) -> bool:
    if not cells:
        return False
    if not _GROUP_RE.match(cells[0]):
        return False
    rest = [cell for cell in cells[1:] if cell]
    return all(cell in {"--", "-", "—", "–"} for cell in rest) or not rest


def _fix_operating(table: Any) -> None:
    blocks = [
        block
        for block in (getattr(table, "table_blocks", None) or [])
        if isinstance(block, dict) and block.get("rows")
    ]
    cleaned: list[dict[str, Any]] = []
    for block in blocks:
        rows: list[list[str]] = []
        for row in block["rows"]:
            cells = [_clean(cell) for cell in row]
            if not cells or _MARKER_RE.match(cells[0]):
                continue
            if _is_group_row(cells):
                continue
            rows.append(cells)
        if rows:
            cleaned.append({"page": int(block.get("page", 1) or 1), "rows": rows})
    if not cleaned:
        return

    # 跨块合并 5.2：上一块末尾是“5.2…签单保”，下一块首行是“费”。
    for index in range(len(cleaned) - 1):
        left = cleaned[index]["rows"]
        right = cleaned[index + 1]["rows"]
        if not left or not right:
            continue
        if (
            left[-1][0].startswith("5.2")
            and "签单保" in left[-1][0]
            and not left[-1][0].endswith("费")
            and right[0][0] == "费"
        ):
            left[-1][0] = left[-1][0] + "费"
            right.pop(0)
    cleaned = [block for block in cleaned if block["rows"]]

    for block in cleaned:
        block["rows"] = _merge_operating_rows(block["rows"])

    flat: list[list[str]] = []
    for block in cleaned:
        flat.extend(list(row) for row in block["rows"])
    table.rows = flat
    table.table_blocks = cleaned


def _fix_liquidity(table: Any) -> None:
    blocks = [
        block
        for block in (getattr(table, "table_blocks", None) or [])
        if isinstance(block, dict) and block.get("rows")
    ]
    start = None
    for index, block in enumerate(blocks):
        for row in block["rows"]:
            if row and "监测指标" in _clean(row[0]):
                start = index
                break
        if start is not None:
            break
    if start is None:
        return
    kept: list[dict[str, Any]] = []
    for block in blocks[start:]:
        rows = [
            [_clean_liquidity_label(row[0])] + [_clean(cell) for cell in row[1:]]
            for row in block["rows"]
            if not (row and _MARKER_RE.match(_clean(row[0])))
        ]
        if rows:
            kept.append({"page": int(block.get("page", 1) or 1), "rows": rows})
    if not kept:
        return
    flat: list[list[str]] = []
    for block in kept:
        flat.extend(list(row) for row in block["rows"])
    table.rows = flat
    table.table_blocks = kept


@register("renbao")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """人保专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                rows = _fix_solvency_header(
                    [list(row) for row in (getattr(table, "rows", None) or [])]
                )
                table.rows = rows
                for block in list(getattr(table, "table_blocks", None) or []):
                    if isinstance(block, dict) and block.get("rows"):
                        block["rows"] = _fix_solvency_header(
                            [list(row) for row in block["rows"]]
                        )
            elif table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                _fix_operating(table)
            elif table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}:
                _rebuild_numbered_table(table, doc)
            elif table_id == "LIQUIDITY_MONITORING":
                _fix_liquidity(table)
    finally:
        doc.close()
    return tables


_DOCUMENT_LAYOUT = {
    "SOLVENCY_MAIN": {
        "title": "偿付能力充足率指标",
        "required": [
            "认可资产",
            "认可负债",
            "综合偿付能力充足率"
        ],
        "unit": "元"
    },
    "NON_LIFE_OPERATING_METRICS": {
        "title": "财产保险公司主要经营指标",
        "required": [
            "保险业务收入",
            "净利润"
        ],
        "continuation": 2,
        "tail_terms": [
            "签单保费",
            "经纪渠道",
            "其他渠道",
            "综合成本率",
            "车险车均保费"
        ],
        "stop": "(?:实际资本表|偿付能力状况表)",
        "unit": "元"
    },
    "ACTUAL_CAPITAL": {
        "title": "实际资本表",
        "required": [
            "核心一级资本"
        ],
        "continuation": 3,
        "tail_terms": [
            "附属一级资本",
            "附属二级资本",
            "实际资本合计",
            "核心一级资本"
        ],
        "stop": "(?:认可资产表|认可资产明细表)",
        "unit": "元"
    },
    "MINIMUM_CAPITAL": {
        "title": "最低资本表",
        "required": [
            "量化风险最低资本"
        ],
        "continuation": 2,
        "tail_terms": [
            "附加资本",
            "风险最低资本",
            "最低资本"
        ],
        "stop": "(?:保费风险最低资本表|巨灾风险最低资本表)",
        "unit": "元"
    },
    "LIQUIDITY_MONITORING": {
        "title": "流动性风险监测指标",
        "required": [
            "经营活动净现金流"
        ],
        "continuation": 1,
        "tail_terms": [
            "融资杠杆",
            "持有关联方资产",
            "经营活动现金流",
            "AA级"
        ],
        "stop": "(?:实际资本表|偿付能力状况表)",
        "unit": "元"
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "title": "近三年.{0,35}平均(?:综合)?投资收益率",
        "required": [],
        "unit": "%"
    }
}


def locate_company_tables(pdf_bytes, table_configs=()):
    from ._document import locate_document
    return locate_document(pdf_bytes, _DOCUMENT_LAYOUT)


def extract_company_tables(pdf_bytes, table_configs):
    from ._document import extract_document
    return extract_document(pdf_bytes, table_configs, _DOCUMENT_LAYOUT, postprocess, "人保产险")
