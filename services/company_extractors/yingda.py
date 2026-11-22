"""英大泰和财产保险专属提取后处理（2022–2025 版式）。

已覆盖的通用规则缺口（4 年共有或部分年份出现）：

1. ``NON_LIFE_OPERATING_METRICS``：pdfplumber 只识别到“1.综合成本率”就
   截断（rows=13），效益类其余行、规模类、渠道类全部丢失。这里按 PDF
   文本行重建 27 行“指标名 + 本季度数 + 本年度累计数”。
2. ``ACTUAL_CAPITAL``：表格跨页，通用规则读到续页前的“4.1 应急资本”就
   停止（2022/2023/2025 缺 4.2/4.3/5 实际资本合计，2024 缺 3.7 以后），
   导致“实际资本合计”缺失、实际资本只靠主表口径。这里按 source_pages
   重建整表（含续页）。
3. ``THREE_YEAR_INVESTMENT_RETURN``：2023 年该子表在经营指标页页尾，
   通用规则把整页经营表当成近三年表返回；2024/2025 只有 2 行、无表头。
   这里统一从页文本抽取“近三年平均（综合）投资收益率”两行并补表头。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
CAP_CODE_RE = re.compile(r"^\d+(?:\.\d+)*$")
PLACEHOLDERS = {"--", "-", "—", "–", "－"}

_OPERATING_HEADER = ("指标名称", "本季度数", "本年度累计数")
_OPERATING_SKIP = {
    "主要经营指标",
    "3.非车险前五大险种的签单保费",
    "5.各渠道签单保费",
}
_GROUP_TITLE_RE = re.compile(r"^[（(](?:十一|十二)[）)]\s*(?:效益|规模)类指标")
_NEW_LABEL_RE = re.compile(
    r"^(?:[（(]|[0-9]+[.．]|[一二三四五六七八九十]+、|其中[:：]|减[:：]|加[:：])"
)
_OPERATING_STOP = ("填表说明", "（四）近三年", "(四)近三年", "五、")

_CAPITAL_HEADER = ("指标名称", "期末数", "期初数")

_THREE_YEAR_LABELS = ("近三年平均投资收益率", "近三年平均综合投资收益率")


def _page_text(doc: Any, page_number: int) -> str:
    index = int(page_number) - 1
    if index < 0 or index >= doc.page_count:
        return ""
    return doc.load_page(index).get_text() or ""


def _table_pages(table: Any) -> list[int]:
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).strip().isdigit()
    ]
    if not pages:
        pages = [int(getattr(table, "page", 1) or 1)]
    return pages


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _is_number(token: str) -> bool:
    token = (token or "").strip()
    return bool(NUMBER_RE.match(token)) or token in PLACEHOLDERS


def _parse_operating_rows(text: str) -> list[list[str]]:
    """按文本行解析“（三）主要经营指标”，返回三列行（不含表头）。"""
    lines = _split_lines(text)
    try:
        start = lines.index("本年度累计数") + 1
    except ValueError:
        return []

    rows: list[list[str]] = []
    label = ""
    values: list[str] = []

    def flush() -> None:
        nonlocal label, values
        if not label:
            values = []
            return
        cells = [label, *values[:2]]
        while len(cells) < 3:
            cells.append("")
        rows.append(cells)
        label, values = "", []

    for line in lines[start:]:
        if line.startswith(_OPERATING_STOP):
            break
        if line in _OPERATING_SKIP or _GROUP_TITLE_RE.match(line):
            flush()
            continue
        if _is_number(line):
            if not label:
                continue
            values.append(line)
            continue
        if label and (values or _NEW_LABEL_RE.match(line)):
            flush()
        label += line
    flush()
    return rows


def _parse_capital_rows(page_texts: Sequence[str]) -> list[list[str]]:
    """按文本行 + 行次列解析实际资本表，跨页合并为一个表体。"""
    lines: list[str] = []
    for text in page_texts:
        page_lines = _split_lines(text)
        # 每页首行是印刷页码（与物理页号不同），跳过。
        if page_lines and page_lines[0].isdigit():
            page_lines = page_lines[1:]
        lines.extend(page_lines)

    try:
        header_index = lines.index("行次")
    except ValueError:
        return []
    cursor = header_index + 1
    while cursor < len(lines) and lines[cursor] not in {"项目", "期末数", "期初数"}:
        cursor += 1
    while cursor < len(lines) and lines[cursor] in {"项目", "期末数", "期初数"}:
        cursor += 1

    rows: list[list[str]] = []
    label = ""
    values: list[str] = []

    def flush() -> None:
        nonlocal label, values
        if label:
            cells = [label, *values[:2]]
            while len(cells) < 3:
                cells.append("")
            rows.append(cells)
        label, values = "", []

    expects_label = True
    for index in range(cursor, len(lines)):
        line = lines[index]
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        next_is_label = (
            bool(next_line)
            and not _is_number(next_line)
            and not CAP_CODE_RE.match(next_line)
        )
        is_code = (
            bool(CAP_CODE_RE.match(line))
            and not _is_number(line)
            and next_is_label
            and len(values) != 1
        )
        if is_code:
            flush()
            expects_label = True
            continue
        if _is_number(line):
            if not label:
                continue
            values.append(line)
            continue
        if expects_label or (label and values):
            flush()
            label = line
            expects_label = False
        else:
            label += line
    flush()
    return rows


def _parse_three_year_rows(page_texts: Sequence[str]) -> list[list[str]]:
    """从页文本抽取近三年平均（综合）投资收益率两行。"""
    rows: list[list[str]] = []
    seen: set[str] = set()
    for text in page_texts:
        lines = _split_lines(text)
        for index, line in enumerate(lines):
            if line not in _THREE_YEAR_LABELS or line in seen:
                continue
            if index + 1 >= len(lines) or "%" not in lines[index + 1]:
                continue
            rows.append([line, lines[index + 1]])
            seen.add(line)
    return rows


_LIQUID_GROUP_RE = re.compile(r"^[一二三四五六七八九十]+、")
_LIQUID_WRAP_SUFFIX = ("（万", "(万", "（元", "(元", "（", "(", "，", ",")
_LIQUID_GROUP_SUFFIX = ("占比", "比例", "现金流", "增速")


def _parse_liquidity_rows(page_texts: Sequence[str]) -> list[list[str]]:
    """按文本行重建“4．流动性风险监测指标”表（十组监测指标）。"""
    lines: list[str] = []
    for text in page_texts:
        page_lines = _split_lines(text)
        if page_lines and page_lines[0].isdigit():
            page_lines = page_lines[1:]
        lines.extend(page_lines)
    try:
        start = lines.index("监测指标")
        item_header = lines.index("项目", start)
    except ValueError:
        return []

    columns = 3
    cursor = item_header + 1
    if cursor < len(lines) and lines[cursor] == "数值":
        cursor += 1
    elif cursor + 1 < len(lines) and lines[cursor] in {"本季度", "上季度"}:
        columns = 4
        while cursor < len(lines) and lines[cursor] in {"本季度", "上季度"}:
            cursor += 1

    rows: list[list[str]] = []
    group = ""
    group_first = True
    item = ""
    values: list[str] = []

    def flush() -> None:
        nonlocal item, values, group_first
        if item:
            cells = [group if group_first else "", item, *values[: columns - 2]]
            while len(cells) < columns:
                cells.append("")
            rows.append(cells)
            group_first = False
        item, values = "", []

    for line in lines[cursor:]:
        if _LIQUID_GROUP_RE.match(line):
            flush()
            group = line
            group_first = True
            continue
        if _is_number(line):
            if item:
                values.append(line)
            continue
        if (
            not item
            and group
            and not group.endswith(_LIQUID_GROUP_SUFFIX)
            and not line.startswith("指标值")
        ):
            # 七、AA 级（含）以下境内固定收益类资产占比 等组名跨行，先拼回组名。
            group += line
            continue
        if item and not values and item.endswith(_LIQUID_WRAP_SUFFIX):
            item += line
            continue
        if item and values:
            flush()
        item += line
    flush()
    return rows


def _find_liquidity_page(doc: Any, pages: Sequence[int]) -> int:
    """定位“流动性风险监测指标”表头所在页；窗口页优先，其次向后探两页。"""
    for page_number in pages:
        if "流动性风险监测指标" in _page_text(doc, page_number):
            return int(page_number)
    last = max(pages) if pages else 1
    for page_number in range(last + 1, min(doc.page_count, last + 2) + 1):
        if "流动性风险监测指标" in _page_text(doc, page_number):
            return int(page_number)
    return 0


def _sync_blocks(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


@register("yingda")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """英大专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            pages = _table_pages(table)
            if table_id == "NON_LIFE_OPERATING_METRICS":
                parsed: list[list[str]] = []
                for page_number in pages:
                    parsed.extend(_parse_operating_rows(_page_text(doc, page_number)))
                if parsed:
                    rows = [list(_OPERATING_HEADER), *parsed]
                    _sync_blocks(table, rows, pages[0])
            elif table_id == "ACTUAL_CAPITAL":
                parsed = _parse_capital_rows(
                    [_page_text(doc, page_number) for page_number in pages]
                )
                if parsed:
                    rows = [list(_CAPITAL_HEADER), *parsed]
                    _sync_blocks(table, rows, pages[0])
            elif table_id == "LIQUIDITY_MONITORING":
                page = _find_liquidity_page(doc, pages)
                if page:
                    parsed = _parse_liquidity_rows(
                        [_page_text(doc, page), _page_text(doc, page + 1)]
                    )
                    if parsed:
                        columns = len(parsed[0])
                        header = ["监测指标", "项目"]
                        if columns == 4:
                            header += ["本季度", "上季度"]
                        else:
                            header.append("数值")
                        _sync_blocks(table, [header, *parsed], page)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                parsed = _parse_three_year_rows(
                    [_page_text(doc, page_number) for page_number in pages]
                )
                if parsed:
                    rows = [["监测指标", "数值"], *parsed]
                    _sync_blocks(table, rows, pages[0])
    finally:
        doc.close()
    return tables


_DOCUMENT_LAYOUT = {
    "SOLVENCY_MAIN": {
        "title": "认可资产",
        "required": [
            "认可资产",
            "认可负债",
            "综合偿付能力充足率"
        ],
        "unit": "万元"
    },
    "NON_LIFE_OPERATING_METRICS": {
        "title": "主要经营指标",
        "required": [
            "保险业务收入",
            "净利润"
        ],
        "continuation": 2,
        "tail_terms": [
            "签单保费",
            "综合成本率",
            "车险车均保费"
        ],
        "stop": "(?:实际资本表|偿付能力状况表)",
        "unit": "万元"
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
        "unit": "万元"
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
        "unit": "万元"
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
        "unit": "万元"
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
    return extract_document(pdf_bytes, table_configs, _DOCUMENT_LAYOUT, postprocess, "英大财产")
