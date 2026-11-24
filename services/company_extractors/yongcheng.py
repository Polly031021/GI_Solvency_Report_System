"""永诚保险专属提取后处理（2022-2025 版式）。

永诚季报的公开表在通用网格提取上有四处系统性缺口：

1. ``主要经营指标`` 与 ``流动性风险监测指标`` 同页，整页网格把监测表、
   偿付能力监管指标与经营表拼在一起；金额还被列线切成 ``1,`` +
   ``420,001,430.14`` 两格。2025 年经营表续页（代理/直销/经纪/其他渠道
   4 行）没有被定位。这里按 PDF 文本行重建经营表。
2. ``实际资本表``(S02) / ``最低资本表``(S05) 的第二页是无表头续页，通用
   流程把 ``【第N页表格分块】`` 占位行混进 rows 且丢掉后页；S05 还会被
   Profile 的终止词 ``最低资本合计`` 误截断在子行 ``寿险业务保险风险最低
   资本合计``。这里按“行次 / 标签 / 期末 / 期初”文本行重建整表，并在
   “（二）认可资产…”或“非寿险业务保险风险-保费和准备金…”标题处停止。
3. ``近三年平均投资收益率`` 表与经营表续页同页，通用网格只取到渠道行，
   两个收益率数值落在第 4 列；2023/2024 还因为整页提取在续页上失败，表在组装
   阶段被整个丢弃（无行的表不会进入 tables）。这里按标题重建两行指标，表若
   已不存在则补建。
4. 流动性监测表把章节号（一、……十、）和“指标值”写进指标名列，标签还会
   被换行拆开（``七、AA 级（含）以下境内固定收益类资`` + ``产占比（%）``）。
   这里合并标签行并去掉章节号与“指标值”。

所有重建都同时写 ``table.rows``（供 STEP2 标准化）与 ``table.table_blocks``
（供 STEP3 分页/单位处理），且不依赖具体年份与页码——只认表标题与行次。
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from . import register

_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_VALUE_RE = re.compile(r"^-$|^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_SECTION_NO_RE = re.compile(r"^[一二三四五六七八九十]{1,3}\s*[、.．]\s*")
_SUB_SECTION_RE = re.compile(r"^[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)]")
_CN_CHAPTER_RE = re.compile(r"^[一二三四五六七八九十]{1,3}\s*、")
# 表头被拆成多行时逐行出现的碎词，重建时跳过。
_CAPITAL_SKIP = {
    "行次", "行", "次", "项目", "项目名称", "期末数", "期初数", "续表",
}
_OPERATING_HEADER = ["指标名称", "本季度数", "本年累计数"]
_OPERATING_HEADER_TOKENS = {"指标名称", "监测指标", "指标值"}
_CAPITAL_HEADER = ["指标名称", "期末数", "期初数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度（末）数", "上季度（末）数"]
_THREE_YEAR_HEADER = ["指标名称", "数值"]
_LIQ_SUFFIX = "指标值"
# S02 到“（二）认可资产”表为止；S05 到非寿险保费准备金/巨灾表为止。
_S02_STOP = ("认可资产各项指标表", "认可负债表", "（三）认可资产", "(三)认可资产")
_S05_STOP = (
    "非寿险业务保险风险-保费和准备金风险最低资本表",
    "非寿险业务保险风险-巨灾风险最低资本表",
    "巨灾风险最低资本表",
    "保费和准备金风险最低资本表",
)


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _is_value(cell: str) -> bool:
    return bool(_VALUE_RE.match(cell))


def _page_lines(doc: Any, page_no: int) -> list[str]:
    """取一页的文本行；第一行是印刷页码，按与物理页差 <=3 的规则去掉。"""
    if not 1 <= page_no <= doc.page_count:
        return []
    lines = [_clean(line) for line in doc[page_no - 1].get_text("text").splitlines()]
    lines = [line for line in lines if line]
    if lines and _PAGE_NO_RE.match(lines[0]):
        try:
            printed = int(lines[0])
        except ValueError:
            return lines
        if abs(printed - page_no) <= 3:
            return lines[1:]
    return lines


def _find_page(
    doc: Any,
    finder: Callable[[Sequence[str]], int | None],
    start_page: int = 1,
) -> tuple[int | None, list[str], int | None]:
    """从 start_page 起找第一个满足 finder 的页，返回 (页码, 文本行, 行号)。"""
    for page_no in range(max(1, int(start_page)), doc.page_count + 1):
        lines = _page_lines(doc, page_no)
        index = finder(lines)
        if index is not None:
            return page_no, lines, index
    return None, [], None


# ---------------------------------------------------------------------------
# 主要经营指标
# ---------------------------------------------------------------------------
def _operating_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        head = line.replace(" ", "")
        if head.endswith("主要经营指标") and len(head) >= 6:
            return index
    return None


def _is_operating_header(line: str) -> bool:
    head = line.replace(" ", "")
    if head in _OPERATING_HEADER_TOKENS:
        return True
    return head.startswith("本季度") or head.startswith("上季度") or head.startswith("本年")


def _operating_stop(line: str) -> bool:
    head = line.replace(" ", "")
    if _SUB_SECTION_RE.match(head) or _CN_CHAPTER_RE.match(head):
        return True
    if head.startswith("注：") or head.startswith("注:"):
        return True
    if "非寿险业务保险风险" in head or "巨灾风险最低资本表" in head:
        return True
    return False


def _parse_operating_page(
    lines: Sequence[str], start: int
) -> tuple[list[list[str]], bool]:
    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    stopped = False
    for line in lines[start:]:
        if _operating_stop(line):
            stopped = True
            break
        if _is_operating_header(line):
            continue
        if label and _is_value(line):
            values.append(line)
            if len(values) >= 2:
                rows.append(["".join(label), values[0], values[1]])
                label, values = [], []
            continue
        if values:  # 上一行只有 1 个数值就遇到新标签，按残缺行收尾
            rows.append(["".join(label), values[0], values[1] if len(values) > 1 else ""])
            label, values = [], []
        label.append(line)
    if label and values:
        rows.append(["".join(label), values[0], values[1] if len(values) > 1 else ""])
    return rows, stopped


def _rebuild_operating(table: Any, doc: Any) -> bool:
    page_no, lines, index = _find_page(doc, _operating_heading_index)
    if page_no is None or index is None:
        return False
    blocks: list[dict[str, Any]] = []
    current = page_no
    start = index + 1
    first = True
    while current <= doc.page_count:
        page_lines = _page_lines(doc, current) if not first else lines
        if not first:
            if not any(_is_operating_header(line) for line in page_lines[:4]):
                break
            start = 0
        rows, stopped = _parse_operating_page(page_lines, start)
        if rows:
            blocks.append(
                {"page": current, "rows": [list(_OPERATING_HEADER), *rows]}
            )
        if stopped:
            break
        first = False
        current += 1
    if not blocks:
        return False
    _apply_blocks(table, blocks, _OPERATING_HEADER)
    return True


# ---------------------------------------------------------------------------
# 近三年平均投资收益率
# ---------------------------------------------------------------------------
def _three_year_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        head = line.replace(" ", "")
        if head.startswith("（") and "近三年" in head and "投资收益率" in head:
            return index
    return None


def _parse_three_year_rows(lines: Sequence[str], start: int) -> list[list[str]]:
    rows: list[list[str]] = []
    label: list[str] = []
    for line in lines[start:]:
        head = line.replace(" ", "")
        if _SUB_SECTION_RE.match(head) or _CN_CHAPTER_RE.match(head):
            break
        if head.startswith("注：") or head.startswith("注:"):
            break
        if head in {"指标名称", "数值"} or _is_operating_header(line):
            continue
        if label and _is_value(line):
            rows.append(["".join(label), line])
            label = []
            continue
        label.append(line)
    return rows


def _rebuild_three_year(table: Any, doc: Any) -> bool:
    page_no, lines, index = _find_page(doc, _three_year_heading_index)
    if page_no is None or index is None:
        # 2022 年报确实没有近三年投资收益率表，清空以免误把经营表续页当本表。
        table.rows = []
        table.table_blocks = []
        return False
    rows = _parse_three_year_rows(lines, index + 1)
    for row in rows:
        label = str(row[0] if row else "")
        if "综合收益率" in label and "综合投资收益率" not in label:
            row[0] = label.replace("综合收益率", "综合投资收益率")
        elif "平均投资收益率" not in label and "收益率" in label:
            row[0] = label.replace("收益率", "投资收益率")
    if not rows:
        table.rows = []
        table.table_blocks = []
        return False
    block = {"page": page_no, "rows": [list(_THREE_YEAR_HEADER), *rows]}
    _apply_blocks(table, [block], _THREE_YEAR_HEADER)
    # 两行都是百分数指标，单位按 % 记，避免标准化按“元”换算。
    table.unit = "%"
    table.table_name = "近三年平均投资收益率"
    return True


def _build_three_year_table(doc: Any) -> Any | None:
    """定位成功但整页提取在“经营表续页”上失败时，补建本表。"""
    page_no, lines, index = _find_page(doc, _three_year_heading_index, start_page=1)
    if page_no is None or index is None:
        return None
    rows = _parse_three_year_rows(lines, index + 1)
    if not rows:
        return None
    block = {"page": page_no, "rows": [list(_THREE_YEAR_HEADER), *rows]}
    table = SimpleNamespace(
        table_id="THREE_YEAR_INVESTMENT_RETURN",
        table_name="近三年平均投资收益率",
        page=page_no,
        table_index=0,
        rows=_flatten([block], _THREE_YEAR_HEADER),
        table_blocks=[block],
        source_pages=[page_no],
        unit="%",
        strategy="永诚文本重建",
        quality_score=0.0,
        evidence="近三年平均投资收益率表（文本行重建）",
        profile_strategy_id="",
    )
    return table


# ---------------------------------------------------------------------------
# 流动性风险监测指标
# ---------------------------------------------------------------------------
def _liquidity_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        head = line.replace(" ", "")
        if "流动性风险监测指标" in head:
            return index
        if "流动性风险" in head and "监测指标" in head:
            return index
    # 2022 年报把这一节写作“2、监测指标”，表头是“监测指标 / 指标名称”。
    for index, line in enumerate(lines):
        head = line.replace(" ", "")
        if head == "监测指标" or re.fullmatch(r"\d+\s*[、.．]\s*监测指标", head):
            window = lines[index + 1 : index + 8]
            if any(_SECTION_NO_RE.match(item) for item in window):
                return index
    return None


def _liquidity_first_row(lines: Sequence[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        if _SECTION_NO_RE.match(lines[index]):
            return index
    return None


def _clean_liquidity_label(value: str) -> str:
    text = _SECTION_NO_RE.sub("", _clean(value))
    if text.endswith(_LIQ_SUFFIX):
        text = text[: -len(_LIQ_SUFFIX)]
    return text.strip()


def _parse_liquidity_rows(lines: Sequence[str], start: int) -> list[list[str]]:
    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    for line in lines[start:]:
        head = line.replace(" ", "")
        if (
            _SUB_SECTION_RE.match(head)
            or "主要经营指标" in head
            or "风险管理能力" in head
        ):
            break
        if head == _LIQ_SUFFIX:
            continue
        if head.endswith(_LIQ_SUFFIX):
            label.append(head[: -len(_LIQ_SUFFIX)].strip())
            continue
        if label and _is_value(line):
            values.append(line)
            if len(values) >= 2:
                rows.append(
                    [_clean_liquidity_label("".join(label)), values[0], values[1]]
                )
                label, values = [], []
            continue
        if values:
            rows.append(
                [
                    _clean_liquidity_label("".join(label)),
                    values[0],
                    values[1] if len(values) > 1 else "",
                ]
            )
            label, values = [], []
        label.append(line)
    if label and values:
        rows.append(
            [
                _clean_liquidity_label("".join(label)),
                values[0],
                values[1] if len(values) > 1 else "",
            ]
        )
    return rows


def _rebuild_liquidity(table: Any, doc: Any) -> bool:
    page_no, lines, index = _find_page(doc, _liquidity_heading_index)
    if page_no is None or index is None:
        return False
    # 2024 年这一节的标题印在上一页末尾，表格本身从下一页页首开始。
    for offset in range(3):
        current = page_no + offset
        if current > doc.page_count:
            break
        page_lines = lines if offset == 0 else _page_lines(doc, current)
        start = _liquidity_first_row(page_lines, index + 1 if offset == 0 else 0)
        if start is None:
            continue
        rows = _parse_liquidity_rows(page_lines, start)
        if not rows:
            continue
        block = {"page": current, "rows": [list(_LIQUIDITY_HEADER), *rows]}
        _apply_blocks(table, [block], _LIQUIDITY_HEADER)
        return True
    return False


# ---------------------------------------------------------------------------
# 实际资本表(S02) / 最低资本表(S05)
# ---------------------------------------------------------------------------
def _s02_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        head = line.replace(" ", "")
        if "实际资本各项指标对比表" in head:
            return index
        if head.endswith("实际资本明细表") or head.endswith("实际资本表"):
            return index
    return None


def _s05_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        head = line.replace(" ", "")
        if re.fullmatch(r"[一二三四五六七八九十]{1,3}、最低资本", head):
            return index
    return None


def _capital_stop(line: str, stop_terms: Sequence[str]) -> bool:
    head = line.replace(" ", "")
    if any(term in head for term in stop_terms):
        return True
    if _SUB_SECTION_RE.match(head):
        return True
    return bool(_CN_CHAPTER_RE.match(head))


def _has_capital_header(lines: Sequence[str]) -> bool:
    head = [line.replace(" ", "") for line in lines[:8]]
    return "项目" in head and any(token in head for token in ("行次", "行", "次"))


def _parse_capital_rows(
    lines: Sequence[str], start: int, stop_terms: Sequence[str]
) -> tuple[list[list[str]], bool]:
    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    started = False
    stopped = False
    for line in lines[start:]:
        if _capital_stop(line, stop_terms):
            stopped = True
            break
        if line.replace(" ", "") in _CAPITAL_SKIP:
            continue
        if _ROW_NO_RE.match(line):
            if started and label:
                rows.append(_capital_row(label, values))
            label, values = [], []
            started = True
            continue
        if not started:
            continue
        if _is_value(line) and len(values) < 2:
            values.append(line)
        elif not values:
            label.append(line)
    if started and label:
        rows.append(_capital_row(label, values))
    return rows, stopped


def _capital_row(label: Sequence[str], values: Sequence[str]) -> list[str]:
    return [
        "".join(label),
        values[0] if values else "",
        values[1] if len(values) > 1 else "",
    ]


def _rebuild_capital_table(
    table: Any,
    doc: Any,
    finder: Callable[[Sequence[str]], int | None],
    stop_terms: Sequence[str],
) -> bool:
    pages = [int(page) for page in (getattr(table, "source_pages", None) or []) if page]
    hint = min(pages) if pages else int(getattr(table, "page", 1) or 1)
    page_no, lines, index = _find_page(doc, finder, start_page=max(1, hint - 2))
    if page_no is None or index is None:
        return False
    blocks: list[dict[str, Any]] = []
    current = page_no
    start = index + 1
    first = True
    while current <= doc.page_count:
        page_lines = _page_lines(doc, current) if not first else lines
        if not first:
            if not _has_capital_header(page_lines):
                break
            start = 0
        rows, stopped = _parse_capital_rows(page_lines, start, stop_terms)
        if rows:
            block_rows = rows if not first else [list(_CAPITAL_HEADER), *rows]
            blocks.append({"page": current, "rows": block_rows})
        if stopped:
            break
        first = False
        current += 1
    if not blocks:
        return False
    _apply_blocks(table, blocks, _CAPITAL_HEADER)
    return True


# ---------------------------------------------------------------------------
def _apply_blocks(
    table: Any, blocks: Sequence[dict[str, Any]], header: Sequence[str]
) -> None:
    table.rows = _flatten(blocks, header)
    table.table_blocks = [dict(block) for block in blocks]
    table.source_pages = [int(block["page"]) for block in blocks]


def _flatten(blocks: Sequence[dict[str, Any]], header: Sequence[str]) -> list[list[str]]:
    """合并各页块：表头只保留一次，续页重复表头去掉。"""
    flat: list[list[str]] = []
    for block in blocks:
        for row in block.get("rows") or []:
            cells = [str(cell or "") for cell in row]
            if cells == list(header):
                if not flat:
                    flat.append(list(cells))
                continue
            flat.append(cells)
    return flat


@register("yongcheng")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """永诚专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        rebuilt: list[Any] = list(tables)
        has_three_year = False
        for table in rebuilt:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                _rebuild_operating(table, doc)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(table, doc)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                has_three_year = True
                _rebuild_three_year(table, doc)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_capital_table(table, doc, _s02_heading_index, _S02_STOP)
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_capital_table(table, doc, _s05_heading_index, _S05_STOP)
        if not has_three_year:
            # 2023/2024 的该表与经营表续页同页，整页提取在那一页会整页失败，
            # 表在组装阶段被丢弃；这里按标题补建（2022 无此表，自然补不出来）。
            extra = _build_three_year_table(doc)
            if extra is not None:
                rebuilt.append(extra)
    finally:
        doc.close()
    return rebuilt


from ._document import STANDARD_NON_LIFE_LAYOUT, make_document_hooks

_YONGCHENG_LAYOUT = dict(STANDARD_NON_LIFE_LAYOUT)
_YONGCHENG_LAYOUT["SOLVENCY_MAIN"] = {
    "title": "(?:主要)?偿付能力(?:充足率)?指标",
    "required": ["认可资产", "综合偿付能力充足率"],
    "unit": "元",
}
_YONGCHENG_LAYOUT["ACTUAL_CAPITAL"] = {
    "title": "实际资本(?:各项指标对比表|明细表|表)",
    "required": ["核心一级资本"],
    "continuation": 3,
    "tail_terms": ["附属一级资本", "实际资本合计", "核心一级资本"],
    "stop": "(?:认可资产表|最低资本)",
    "unit": "元",
}
_YONGCHENG_LAYOUT["MINIMUM_CAPITAL"] = {
    "title": "(?:S05[-—－]?最低资本表|[一二三四五六七八九十]+、最低资本)",
    "required": ["量化风险最低资本"],
    "continuation": 2,
    "tail_terms": ["风险最低资本", "最低资本"],
    "stop": "(?:保费风险最低资本表|巨灾风险最低资本表)",
    "unit": "元",
}
locate_company_tables, extract_company_tables = make_document_hooks(
    _YONGCHENG_LAYOUT, "永诚保险", postprocess
)

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("yongcheng", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "yongcheng",
        pdf_bytes,
        table_configs,
        mode="document",
    )
