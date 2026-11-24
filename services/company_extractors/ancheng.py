"""安诚财险专属提取后处理：按文本层重建五张表。

安诚偿付能力报告的正文表格由 Word 导出，文本层把一行指标压成
“项目 期末值 期初值”一条；长项目名会被拆成“项目头 / 数值 / 项目尾”三行
（例如“非寿险业务保险风险-保费及准备金风险最低 / 546,588,938.46 … / 资本”）。

通用线框规则在安诚身上有两处必然串票：

1. ``十、实际资本``：一页挤三张表（（一）实际资本 /（二）认可资产 /（三）认可负债）。
   6 列（账面价值/非认可价值/认可价值 ×2）的列线被套到 3 列的实际资本表上，
   金额被拆成两半（``认可资产（元）|8,400,924,|981.32``）。
2. ``四、主要指标`` 第 2 页：流动性风险监测指标 + 主要经营指标 + 效益类指标同页。
   整页帧把监测表 10 行拼到经营表表头之前，并夹进“指标名称/本季度数/…”伪行。

本模块直接读 pdfplumber 的词坐标重建 ``rows``：

* 数值列按**右边缘聚类**归列（Word 表格金额右对齐，期末/期初两列的右边缘稳定）；
* 无数值的碎行按**上下最近数值行**归行，因此三行式换行标签能拼回一条；
* 表头行取页面上的真实表头词，首块 rows 必带表头（STEP3 依赖）。

安全约定：解析结果不满阈值时直接 return，保留通用提取结果；不写死年份与页码
（页码只由 ``table.page`` / ``source_pages`` 与章节标题定位），不吞异常。
"""

from __future__ import annotations

import io
import re
from statistics import median
from typing import Any, Sequence

from . import register

_CAP_HEADER = ("项目", "期末数", "期初数")
_OPER_HEADER = ("指标名称", "本季度数", "本年度累计数")
_LIQ_HEADER = ("指标名称", "本季度数", "上季度数")
_THREE_YEAR_HEADER = ("项目", "数值")

_VALUE_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_DASHES = frozenset({"-", "--", "—", "–", "——", "－", "－", "．"})
_HEADER_TOKENS = frozenset(
    {
        "项目",
        "指标名称",
        "行次",
        "序号",
        "期末数",
        "期初数",
        "本季度数",
        "上季度数",
        "本季度末",
        "上季度末",
        "本季度末数",
        "上季度末数",
        "本年度累计数",
        "本年累计数",
        "数值",
        "排序",
        "险种",
        "渠道类型",
    }
)
_SECTION_RE = re.compile(r"^(?:[（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+、)")
_RANK_RE = re.compile(r"^\d{1,2}\s*[.．、]?\s*(\S.*)$")
# 以这些词收尾的碎行是“完整指标名”（该期无数值，如 2023 起的
# “寿险业务保险风险最低资本合计”），不能当成换行标签的头部拼到下一行。
_COMPLETE_SUFFIXES = ("合计", "总计", "小计")
_CALIBER_RE = re.compile(r"[（(]\s*已赚口径\s*[）)]")
_CJK_GAP_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")

_THREE_RETURN_RE = re.compile(
    r"近三年平均投资收益率\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*%"
)
_THREE_COMP_RE = re.compile(
    r"(?:近三年)?平均综合投资收益率\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*%"
)

# 各表重建后的最少行数（不含表头）：不达标就保留通用结果。
_MIN_CAPITAL_ROWS = 6
_MIN_OPERATING_ROWS = 15
_MIN_LIQUIDITY_ROWS = 8
_MIN_THREE_YEAR_ROWS = 2


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _is_value(token: str) -> bool:
    text = str(token or "").strip()
    if not text:
        return False
    if text in _DASHES:
        return True
    return bool(_VALUE_RE.match(text))


def _cell(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    return "--" if text in _DASHES else text


def _clean_label(text: str) -> str:
    value = _CALIBER_RE.sub("", str(text or ""))
    value = re.sub(r"\s+", " ", value).strip()
    value = _CJK_GAP_RE.sub("", value)
    return value.strip()


def _page_lines(page: Any) -> list[dict[str, Any]]:
    """把一页的 words 聚成“视觉行”（同一基线的词归一行）。"""
    words = page.extract_words(x_tolerance=2, y_tolerance=3) or []
    lines: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (round(float(item["top"]), 1), item["x0"])):
        top = float(word["top"])
        if lines and abs(top - lines[-1]["top"]) <= 3.0:
            lines[-1]["words"].append(word)
        else:
            lines.append({"top": top, "words": [word]})
    for line in lines:
        line["words"].sort(key=lambda item: item["x0"])
        line["text"] = " ".join(str(item["text"]) for item in line["words"])
    return lines


def _line_tokens(line: dict[str, Any]) -> list[tuple[float, float, str]]:
    return [
        (float(word["x0"]), float(word["x1"]), str(word["text"]))
        for word in line["words"]
    ]


def _find_page(pdf: Any, pages: Sequence[int], markers: Sequence[str], span: int = 2) -> int | None:
    """在 pages 附近的页里找第一个含任一 marker 的页（物理页，从 1 开始）。"""
    total = len(pdf.pages)
    candidates: list[int] = []
    for page in pages:
        for delta in range(0, span + 1):
            for candidate in (page - delta, page + delta):
                if 1 <= candidate <= total and candidate not in candidates:
                    candidates.append(candidate)
    for candidate in candidates:
        text = pdf.pages[candidate - 1].extract_text(x_tolerance=2, y_tolerance=3) or ""
        if any(marker in text for marker in markers):
            return candidate
    return None


def _table_pages(table: Any) -> list[int]:
    pages: list[int] = []
    for value in list(getattr(table, "source_pages", None) or []) + [getattr(table, "page", 0)]:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page >= 1 and page not in pages:
            pages.append(page)
    return pages


def _sync(table: Any, blocks: list[tuple[int, list[list[str]]]], *, unit: str = "") -> None:
    """同步写 table.rows 与 table.table_blocks（STEP3 依赖 table_blocks）。"""
    flat: list[list[str]] = []
    for page, rows in blocks:
        for row in rows:
            flat.append(list(row))
    table.rows = flat
    table.table_blocks = [
        {"page": int(page), "rows": [list(row) for row in rows]}
        for page, rows in blocks
        if rows
    ]
    if unit:
        table.unit = unit


# ---------------------------------------------------------------------------
# 通用解析：按词坐标把“标签 + 两个数值列”还原成三列表格
# ---------------------------------------------------------------------------
def _collect_records(lines: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    header: list[str] = []
    for line in lines:
        tokens = _line_tokens(line)
        labels = [text for _, _, text in tokens if not _is_value(text)]
        values = [(x1, text) for _, x1, text in tokens if _is_value(text)]
        if not values:
            if not labels:
                continue
            if all(text in _HEADER_TOKENS for text in labels):
                if not header:
                    header = labels
                continue
            records.append(
                {"top": line["top"], "parts": [(line["top"], " ".join(labels))], "values": None}
            )
            continue
        parts = [(line["top"], " ".join(labels))] if labels else []
        records.append({"top": line["top"], "parts": parts, "values": values})
    return records, header


def _attach_fragments(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """无数值的碎行：章节标题丢弃、完整指标名保留为空值行、其余按最近数值行归行。"""
    numeric = [item for item in records if item["values"]]
    keep: list[dict[str, Any]] = []
    for item in records:
        if item["values"]:
            continue
        text = _clean_label(item["parts"][0][1]) if item["parts"] else ""
        if not text:
            continue
        if _SECTION_RE.match(text):
            # “（五）效益类指标”“十二、……”这类结构标题不是指标行。
            continue
        rank_match = _RANK_RE.match(text)
        if rank_match and rank_match.group(1).strip():
            # “4. 非车险前五大险种的签单保费”这类分组标题：保留为无值行，
            # 不参与标签拼接（否则会黏到下一行“健康险”上）。
            keep.append(
                {
                    "top": item["top"],
                    "parts": [(item["top"], rank_match.group(1).strip())],
                    "values": [],
                }
            )
            continue
        if text.endswith(_COMPLETE_SUFFIXES):
            keep.append({"top": item["top"], "parts": [(item["top"], text)], "values": []})
            continue
        below = next((row for row in numeric if row["top"] > item["top"]), None)
        above = next(
            (row for row in reversed(numeric) if row["top"] < item["top"]), None
        )
        gap_below = (below["top"] - item["top"]) if below is not None else float("inf")
        gap_above = (item["top"] - above["top"]) if above is not None else float("inf")
        if below is not None and gap_below <= gap_above:
            below["parts"].append((item["top"], text))
        elif above is not None:
            above["parts"].append((item["top"], text))
        else:
            keep.append(item)
    merged = [item for item in records if item["values"]] + keep
    merged.sort(key=lambda item: item["top"])
    return merged


def _value_clusters(rights: Sequence[float], *, gap: float = 25.0) -> list[list[float]]:
    clusters: list[list[float]] = []
    for value in sorted(float(item) for item in rights):
        if clusters and value - clusters[-1][-1] <= gap:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def _column_edges(records: Sequence[dict[str, Any]], columns: int) -> dict[int, float]:
    """数值列参考边：按右边缘聚类，取成员最多的两簇（左=第一数值列）。

    量表的“排序”列（1/2/3/5）也是数字，会形成一个小簇；只取最大的两簇
    可避免把金额列判成第二列的从属簇。
    """
    rights = [
        round(float(x1), 1)
        for item in records
        for x1, _ in (item["values"] or [])
    ]
    if not rights:
        return {}
    clusters = _value_clusters(rights)
    if columns <= 1 or len(clusters) < 2:
        centre = median(rights)
        return {0: centre}
    clusters.sort(key=len, reverse=True)
    main = sorted(clusters[:2], key=lambda item: median(item))
    return {0: median(main[0]), 1: median(main[1])}


def _finalize(
    records: Sequence[dict[str, Any]],
    header: Sequence[str],
    *,
    columns: int = 2,
) -> list[list[str]]:
    edges = _column_edges(records, columns)
    rows: list[list[str]] = [list(header)]
    for item in records:
        label = _clean_label(
            "".join(text for _, text in sorted(item["parts"], key=lambda pair: pair[0]))
        )
        if not label:
            continue
        cells = ["" for _ in range(columns)]
        for x1, text in item["values"] or []:
            if not edges:
                continue
            column = min(edges, key=lambda index: abs(float(x1) - edges[index]))
            if not cells[column]:
                cells[column] = _cell(text)
        rows.append([label, *cells])
    return rows


def _parse_section(
    pdf: Any,
    page_number: int,
    *,
    start_markers: Sequence[str],
    stop_markers: Sequence[str] = (),
    columns: int = 2,
    header: Sequence[str],
) -> list[list[str]]:
    """在 page_number 上截取 [start_markers, stop_markers) 区段并解析成三列表。"""
    page = pdf.pages[page_number - 1]
    lines = _page_lines(page)
    texts = [line["text"] for line in lines]

    start = None
    for index, text in enumerate(texts):
        if any(marker in text for marker in start_markers):
            start = index + 1
            break
    if start is None:
        return []
    stop = len(lines)
    for index in range(start, len(lines)):
        if any(marker in texts[index] for marker in stop_markers):
            stop = index
            break
    body = lines[start:stop]
    if not body:
        return []
    records, found_header = _collect_records(body)
    if not records:
        return []
    records = _attach_fragments(records)
    rows = _finalize(records, found_header or header, columns=columns)
    return rows if len(rows) > 1 else []


# ---------------------------------------------------------------------------
# 逐表重建
# ---------------------------------------------------------------------------
def _rebuild_capital(pdf: Any, table: Any, *, markers: Sequence[str], stop: Sequence[str]) -> bool:
    pages = _table_pages(table)
    page = _find_page(pdf, pages, markers) or (pages[0] if pages else None)
    if page is None:
        return False
    rows = _parse_section(
        pdf,
        page,
        start_markers=("期末数",),
        stop_markers=stop,
        header=_CAP_HEADER,
    )
    if len(rows) - 1 < _MIN_CAPITAL_ROWS:
        return False
    _sync(table, [(page, rows)], unit="元")
    return True


def _rebuild_operating(pdf: Any, table: Any) -> bool:
    pages = _table_pages(table)
    first = _find_page(pdf, pages, ("主要经营指标", "效益类指标"))
    if first is None:
        return False
    total = len(pdf.pages)
    blocks: list[tuple[int, list[list[str]]]] = []
    body_rows: list[list[str]] = []
    page = first
    while page <= total:
        text = pdf.pages[page - 1].extract_text(x_tolerance=2, y_tolerance=3) or ""
        if page == first:
            block = _parse_section(
                pdf,
                page,
                start_markers=("主要经营指标",),
                stop_markers=("（六）", "（七）", "十二、"),
                header=_OPER_HEADER,
            )
        else:
            if "规模类指标" not in text:
                break
            block = _parse_section(
                pdf,
                page,
                start_markers=("规模类指标",),
                stop_markers=("（七）", "（八）", "（九）", "十二、"),
                header=_OPER_HEADER,
            )
        if not block:
            break
        body = [row for row in block[1:] if row and str(row[0]).strip()]
        body_rows.extend(body)
        blocks.append((page, body))
        if page != first or "规模类指标" in text:
            break
        page += 1
    if len(body_rows) < _MIN_OPERATING_ROWS:
        return False
    # 首块必须带表头（STEP3 的 _expand_table_blocks 依赖首块表头行）。
    head_page, head_rows = blocks[0]
    blocks[0] = (head_page, [list(_OPER_HEADER), *head_rows])
    _sync(table, blocks, unit="元")
    return True


def _rebuild_liquidity(pdf: Any, table: Any) -> bool:
    pages = _table_pages(table)
    page = _find_page(pdf, pages, ("流动性风险监测指标",))
    if page is None:
        return False
    rows = _parse_section(
        pdf,
        page,
        start_markers=("流动性风险监测指标",),
        stop_markers=("（四）", "（五）", "主要经营指标"),
        header=_LIQ_HEADER,
    )
    if len(rows) - 1 < _MIN_LIQUIDITY_ROWS:
        return False
    _sync(table, [(page, rows)], unit="元")
    return True


def _rebuild_three_year(pdf: Any, table: Any) -> bool:
    pages = _table_pages(table)
    search: list[int] = []
    total = len(pdf.pages)
    for page in pages or [1]:
        for delta in (0, -1, 1, -2, 2):
            candidate = page + delta
            if 1 <= candidate <= total and candidate not in search:
                search.append(candidate)
    found: tuple[int, float, float] | None = None
    for page in search:
        text = pdf.pages[page - 1].extract_text(x_tolerance=2, y_tolerance=3) or ""
        value = _THREE_RETURN_RE.search(text)
        if not value:
            continue
        comp = _THREE_COMP_RE.search(text, value.end())
        if not comp:
            comp = _THREE_COMP_RE.search(text)
        if not comp:
            continue
        found = (page, float(value.group(1)), float(comp.group(1)))
        break
    if found is None:
        return False
    page, plain, comprehensive = found
    rows = [
        list(_THREE_YEAR_HEADER),
        ["近三年平均投资收益率", f"{plain:g}%"],
        ["近三年平均综合投资收益率", f"{comprehensive:g}%"],
    ]
    if len(rows) - 1 < _MIN_THREE_YEAR_ROWS:
        return False
    _sync(table, [(page, rows)])
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
@register("ancheng")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """按 table_id 分派到各表重建逻辑；未覆盖的表保持通用提取结果。"""
    targets = {
        "ACTUAL_CAPITAL": lambda pdf, table: _rebuild_capital(
            pdf, table, markers=("实际资本",), stop=("（二）认可资产",)
        ),
        "MINIMUM_CAPITAL": lambda pdf, table: _rebuild_capital(
            pdf, table, markers=("十一、最低资本", "最低资本"), stop=()
        ),
        "NON_LIFE_OPERATING_METRICS": _rebuild_operating,
        "OPERATING_METRICS": _rebuild_operating,
        "LIQUIDITY_MONITORING": _rebuild_liquidity,
        "THREE_YEAR_INVESTMENT_RETURN": _rebuild_three_year,
    }
    if not tables or not pdf_bytes:
        return tables
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for table in tables:
            handler = targets.get(str(getattr(table, "table_id", "") or ""))
            if handler is None:
                continue
            handler(pdf, table)
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("ancheng", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "ancheng",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
