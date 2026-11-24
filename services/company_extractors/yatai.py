"""亚太财险专属提取后处理（2022-2025 版式）。

通用规则在亚太财险报告上的四个缺口：

1. ``（三）主要经营指标`` 与 ``（二）主要流动性指标`` / ``非车险前五大`` /
   ``各渠道签单保费`` 同页或相邻页，整页提取会把几张表混在一起，金额列还会
   被 pdfplumber 按列边界切成两段（``1,182,885,884.`` | ``46``）。这里按
   PyMuPDF 词坐标聚行，只收“标签以（元）/（%）结尾 + 恰好两个数值”的行，
   跨页收集，续行即使落在“（四）（五）（六）”之后也不会漏。
2. ``十、最低资本`` 的 S05 表有三十余行，通用流程只保留表头附近 3-4 行。
   这里按“行次 + 项目 + 期末数 + 期初数”整表重建，并把 PDF 里拼在标签上的
   风险类别前缀（``市场风险利率风险最低资本``）还原成指标库口径
   （``利率风险最低资本``），否则标签覆盖率不足 0.8 无法匹配。
3. ``（二）主要流动性指标`` 的定位页同时命中偿付能力主表/经营表，通用结果
   常抓到别的表；这里只重建“主要流动性指标”到“主要经营指标”之间的一段。
4. 2025 年实际资本 S02、最低资本 S05 改为带行次的四列版式，且部分标签被
   拆成跨行片段（``投资性房地产（包括……`` / 行次行 / ``（扣除减值……）``）。

约定：只改 ``rows`` 与 ``table_blocks``（两者保持同步），不改
``table_id`` / ``page`` / ``source_pages``；幂等；不写死年份与页码。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PLACEHOLDER_RE = re.compile(r"^[-—–－]+$")
# 同一表格行内相邻词的水平间隙超过该值即视为换列（中文词间距通常 < 3pt）。
_COLUMN_GAP = 12.0

_OPERATING_HEADER = ["指标名称", "本季度（末）数", "本年累计数"]
_OPERATING_CHANNELS = ("代理渠道", "直销渠道", "经纪渠道", "其他渠道")
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]
_CAPITAL_HEADER = ["行次", "项目", "期末数", "期初数"]
_THREE_YEAR_HEADER = ["指标名称", "指标金额"]

# 实际资本表里值为“-”但属于口径明细的行（其余“-”行是寿险/附加资本，丢弃）。
_ACTUAL_ZERO_HINTS = (
    "递延所得税",
    "投资性房地产",
    "保单未来盈余",
    "负债类资本工具",
    "其他调整",
    "核心二级资本",
    "附属一级资本",
    "附属二级资本",
)
# 最低资本表里值为“-”但需要以 0 填报的行（亚太无寿险业务，寿险行全部丢弃）。
_MINIMUM_ZERO_HINTS = ("房地产", "境外固定收益", "损失吸收")

# 风险类别前缀：PDF 把子风险拼在类别后面（``市场风险利率风险最低资本``），
# 指标库口径是去掉类别前缀的子风险名；类别合计/分散效应保留原样。
_PREFIX_STRIP = (
    ("市场风险", ("利率", "权益价格", "房地产", "境外固定收益", "境外权益", "汇率")),
    ("信用风险", ("利差", "交易对手违约")),
    ("非寿险业务保险风险", ("保费及准备金", "巨灾")),
)


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _is_number(cell: Any) -> bool:
    return bool(_NUMBER_RE.match(_clean(cell)))


def _is_value(cell: Any) -> bool:
    text = _clean(cell)
    return bool(_NUMBER_RE.match(text) or _PLACEHOLDER_RE.match(text))


def _is_placeholder(cell: Any) -> bool:
    return bool(_PLACEHOLDER_RE.match(_clean(cell)))


def _page_rows(page: Any, tol: float = 5.0) -> list[list[str]]:
    """按基线聚行，再把同一行内间距过大的词拆成单元格。"""
    words = page.get_text("words")
    if not words:
        return []
    ordered = sorted(words, key=lambda item: (((item[1] + item[3]) / 2), item[0]))
    grouped: list[list[Any]] = []
    for x0, y0, x1, y1, text, *_rest in ordered:
        text = _clean(text)
        if not text:
            continue
        middle = (y0 + y1) / 2
        if grouped and abs(middle - grouped[-1][0]) <= tol:
            grouped[-1][1].append((x0, x1, text))
        else:
            grouped.append([middle, [(x0, x1, text)]])

    rows: list[list[str]] = []
    for _middle, cells in grouped:
        cells.sort(key=lambda item: item[0])
        merged: list[str] = []
        buffer: list[str] = []
        previous_x1: float | None = None
        for x0, x1, text in cells:
            if buffer and previous_x1 is not None and x0 - previous_x1 > _COLUMN_GAP:
                merged.append("".join(buffer))
                buffer = []
            buffer.append(text)
            previous_x1 = x1
        if buffer:
            merged.append("".join(buffer))
        rows.append([cell for cell in merged if cell])
    return rows


def _scan_pages(table: Any, doc: Any, pad: int = 1) -> list[int]:
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).strip().isdigit()
    ]
    if not pages:
        page = int(getattr(table, "page", 1) or 1)
        pages = [page]
    first = max(1, min(pages) - pad)
    last = min(doc.page_count, max(pages) + pad)
    return list(range(first, last + 1))


def _sync(table: Any, header: Sequence[str], records: Sequence[tuple[int, list[str]]]) -> None:
    """按页把记录写回 rows/table_blocks，每块都带表头。"""
    blocks: list[dict[str, Any]] = []
    flat: list[list[str]] = [list(header)]
    for page_no, row in records:
        if not blocks or blocks[-1]["page"] != page_no:
            blocks.append({"page": page_no, "rows": [list(header)]})
        blocks[-1]["rows"].append(list(row))
        flat.append(list(row))
    table.rows = flat
    table.table_blocks = blocks


def _first_value_index(cells: Sequence[str]) -> int | None:
    for index, cell in enumerate(cells):
        if _is_value(cell):
            return index
    return None


# ---------------------------------------------------------------------------
# 主要经营指标
# ---------------------------------------------------------------------------
def _rebuild_operating(table: Any, doc: Any) -> None:
    pages = _scan_pages(table, doc, pad=1)
    start = None
    for page_no in pages:
        if "主要经营指标" in doc[page_no - 1].get_text("text"):
            start = page_no
            break
    if start is None:
        return

    records: list[tuple[int, list[str]]] = []
    seen: set[str] = set()
    for page_no in range(start, pages[-1] + 1):
        for cells in _page_rows(doc[page_no - 1]):
            text = "".join(cells)
            if "四、风险管理能力" in text or "公司所属类型" in text:
                _sync(table, _OPERATING_HEADER, records)
                table.strategy = "专属提取·亚太·经营指标整表重建"
                return
            if any(cell == "实际数" for cell in cells):
                continue
            indexes = [index for index, cell in enumerate(cells) if _is_number(cell)]
            if len(indexes) != 2:
                continue
            label = "".join(cells[: indexes[0]]).strip()
            if not label:
                continue
            if not (
                label.endswith(("（元）", "（%）", "(元)", "(%)"))
                or label in _OPERATING_CHANNELS
            ):
                continue
            if label in seen:
                continue
            seen.add(label)
            records.append((page_no, [label, _clean(cells[indexes[0]]), _clean(cells[indexes[1]])]))

    if records:
        _sync(table, _OPERATING_HEADER, records)
        table.strategy = "专属提取·亚太·经营指标整表重建"


# ---------------------------------------------------------------------------
# 流动性监测指标
# ---------------------------------------------------------------------------
def _join_label(parts: Sequence[str]) -> str:
    return "".join(part.strip() for part in parts if part.strip())


def _rebuild_liquidity(table: Any, doc: Any) -> None:
    pages = _scan_pages(table, doc, pad=1)
    records: list[tuple[int, list[str]]] = []
    pending: list[str] = []
    started = False
    for page_no in pages:
        for cells in _page_rows(doc[page_no - 1]):
            text = _join_label(cells)
            if "主要经营指标" in text:
                if started:
                    _sync(table, _LIQUIDITY_HEADER, records)
                    table.strategy = "专属提取·亚太·流动性监测重建"
                    return
                continue
            if not started:
                if "主要流动性指标" in text:
                    started = True
                continue
            if "指标名称" in text or ("本季度数" in text and "上季度数" in text):
                # 表头行（“指标名称|项目|本季度数|上季度数”）不是指标。
                continue
            if any(cell in {"实际数", "项目", "指标名称"} for cell in cells):
                clean_cells = [cell for cell in cells if cell not in {"实际数", "项目"}]
            else:
                clean_cells = list(cells)
            values = [cell for cell in clean_cells if _is_number(cell)]
            if len(values) >= 2:
                label_cells = []
                for cell in clean_cells:
                    if _is_number(cell):
                        break
                    label_cells.append(cell)
                label = _join_label([*pending, *label_cells])
                pending = []
                if not label:
                    continue
                records.append((page_no, [label, _clean(values[0]), _clean(values[1])]))
                continue

            if not values:
                if not text:
                    continue
                if "流动性覆盖率" in text:
                    # 合并表头单元格，单独成行，不属于任何指标。
                    continue
                if records and _is_trailing_fragment(text):
                    records[-1][1][0] = f"{records[-1][1][0]}{text}"
                    continue
                pending.append(text)

    if records:
        _sync(table, _LIQUIDITY_HEADER, records)
        table.strategy = "专属提取·亚太·流动性监测重建"


def _is_trailing_fragment(text: str) -> bool:
    if text.startswith(("差率", "不利偏差率", "类资产", "股票投资", "个月", "RDR")):
        return True
    return text.endswith(("（%）", "(%)")) and len(text) <= 8


# ---------------------------------------------------------------------------
# 实际资本 / 最低资本（行次版）
# ---------------------------------------------------------------------------
def _normalize_capital_label(label: str) -> str:
    text = label.replace("市场风险-", "市场风险").replace("信用风险-", "信用风险")
    text = text.replace("非寿险业务保险风险-", "非寿险业务保险风险")
    text = text.replace("风险风险分散效应", "风险分散效应").replace("风险风险分散", "风险分散")
    for prefix, children in _PREFIX_STRIP:
        if not text.startswith(prefix):
            continue
        rest = text[len(prefix) :].lstrip("-－— ")
        if rest.startswith(children):
            return rest
    return text


def _parse_numbered_rows(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    """把“行次 + 项目 + 期末 + 期初”聚成逻辑行，跨行标签前后片段都归位。"""
    records: list[list[str]] = []
    pending: list[str] = []
    last: list[str] | None = None
    for cells in rows:
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if _ROW_NO_RE.match(cells[0]) and not (
            len(cells) == 1 and cells[0].isdigit() and len(cells[0]) <= 3
        ):
            # 单单元格的纯页码（如“24”）不是行次，跳过。
            rest = cells[1:]
            value_index = _first_value_index(rest)
            if value_index is None:
                label_parts, values = rest, []
            else:
                label_parts = rest[:value_index]
                values = [cell for cell in rest[value_index:] if _is_value(cell)]
            row = [_clean(cells[0]), _join_label([*pending, *label_parts]), *values]
            pending = []
            last = row
            records.append(row)
            continue

        if _first_value_index(cells) is not None:
            # 值单独成行：并入上一条逻辑行。
            values = [cell for cell in cells if _is_value(cell)]
            if last is not None and len(last) <= 2:
                last.extend(values)
            continue

        text = _join_label(cells)
        if not text:
            continue
        if last is not None and (_is_wrapped_tail(text) or last[1].endswith(("且按规定", "的", "与", "及", "和"))):
            last[1] = f"{last[1]}{text}"
        else:
            pending.append(text)
    return records


def _is_wrapped_tail(text: str) -> bool:
    return text.startswith(("（", "(")) or text.endswith("）") or len(text) <= 6


def _capital_records(
    table: Any, doc: Any, hints: Sequence[str], anchor: str
) -> list[tuple[int, list[str]]]:
    """在含锚点行的页面上，从该表表头起整表重建（同页上方/下方别的表不串）。"""
    records: list[tuple[int, list[str]]] = []
    for page_no in _scan_pages(table, doc, pad=0):
        rows = _page_rows(doc[page_no - 1])
        anchor_index = None
        for index, cells in enumerate(rows):
            if cells and _ROW_NO_RE.match(cells[0]) and anchor in _join_label(cells):
                anchor_index = index
                break
        if anchor_index is None:
            continue
        header_index = None
        for index in range(anchor_index, -1, -1):
            joined = _join_label(rows[index])
            if "行次" in joined and "项目" in joined:
                header_index = index
                break
        if header_index is None:
            continue
        for row in _parse_numbered_rows(rows[header_index + 1 :]):
            if len(row) < 4:
                continue
            values = row[2:]
            label = row[1]
            if not any(_is_number(value) for value in values):
                if not any(hint in label for hint in hints):
                    continue
            row[1] = _normalize_capital_label(label)
            records.append((page_no, row))
        break
    return records


def _rebuild_actual(table: Any, doc: Any) -> None:
    records = _capital_records(table, doc, _ACTUAL_ZERO_HINTS, "核心一级资本")
    if not records:
        return
    _sync(table, _CAPITAL_HEADER, records)
    table.strategy = "专属提取·亚太·实际资本整表重建"


def _rebuild_minimum(table: Any, doc: Any) -> None:
    records = _capital_records(table, doc, _MINIMUM_ZERO_HINTS, "量化风险最低资本")
    if not records:
        return
    _sync(table, _CAPITAL_HEADER, records)
    table.strategy = "专属提取·亚太·最低资本整表重建"


# ---------------------------------------------------------------------------
# 近三年投资收益率
# ---------------------------------------------------------------------------
def _rebuild_three_year(table: Any, doc: Any) -> None:
    pages = _scan_pages(table, doc, pad=1)
    records: list[tuple[int, list[str]]] = []
    for page_no in pages:
        for cells in _page_rows(doc[page_no - 1]):
            label = _join_label([cell for cell in cells if not _is_number(cell)])
            values = [cell for cell in cells if _is_number(cell)]
            if "近三年平均投资收益率" in label and values:
                records.append((page_no, ["近三年平均投资收益率", _clean(values[0])]))
            elif "近三年平均综合投资收益率" in label and values:
                records.append((page_no, ["近三年平均综合投资收益率", _clean(values[-1])]))
        if len(records) >= 2:
            break
    if len(records) < 2:
        return
    _sync(table, _THREE_YEAR_HEADER, records[:2])
    table.strategy = "专属提取·亚太·近三年投资收益率重建"


@register("yatai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """亚太财险专属后处理入口。异常交给调用方兜底，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "NON_LIFE_OPERATING_METRICS":
                _rebuild_operating(table, doc)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(table, doc)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_actual(table, doc)
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_minimum(table, doc)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                _rebuild_three_year(table, doc)
    finally:
        doc.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("yatai", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "yatai",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
