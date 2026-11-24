# -*- coding: utf-8 -*-
"""国任保险（国任财产保险）专属提取后处理。

本报告版式的共同特征：

* 主表、实际资本表、最低资本表都是"行次 + 项目 + 两/三列数值"的带框表格，
  但 PDF 里长标签会折成 2-3 个物理行（"市场风险最低资 / 198,398.04 … / 本"），
  2022 年更会出现行次与标签分处不同行的画法，pdfplumber 的线框识别会把一行拆成
  三行、或把标签丢到别的列；
* 同一页往往并排/顺排多张表（流动性监管、流动性监测、近三年投资收益率、经营指标），
  整页提取会把相邻表混进来；
* 2024 起"主要偿付能力充足率指标"跨页（p12-p13），续页没有表头。

这里不依赖通用行网格：先按 PDF 横线把页面切成"行带"，再**以表头行的竖线为准**统一
每一行的列归属（表头竖线才是这张表的真实列边界，数据行的竖线因为合并单元格残缺不齐），
最后用语义规则把被拆开的行并回去（标签在前/行次单独一行/折行补字）。
"""

from __future__ import annotations

import io
import re
from typing import Any, Callable, Iterable, Sequence

import pdfplumber

from . import register

# 横线/竖线聚类容差（pt）：PDF 里同一条框线常被画成 2-3 条紧邻的线。
_EDGE_TOLERANCE = 2.5
_MIN_HORIZONTAL_WIDTH = 60.0  # 过滤正文里的短下划线
_AMOUNT = re.compile(r"^-?[\d,，]+(?:\.\d+)?%?$")
_CODE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_UNIT = re.compile(r"单位\s*[：:]\s*(亿元|万元|千元|元)")

_MAIN_HEADER = ["项目", "本季度数", "上季度可比数", "下季度预测数"]
_CAPITAL_HEADER = ["行次", "项目", "期末数", "期初数"]
_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_RATIO_HEADER = ["项目", "本季度数"]

_HEADERS = {
    ("main", 4): _MAIN_HEADER,
    ("main", 3): ["项目", "本季度数", "上季度可比数"],
    ("capital", 4): _CAPITAL_HEADER,
    ("capital", 3): ["项目", "期末数", "期初数"],
    ("capital", 2): ["项目", "期末数"],
    ("operating", 3): _OPERATING_HEADER,
    ("operating", 2): ["指标名称", "本年度累计数"],
    ("ratio", 2): _RATIO_HEADER,
}


class _Band:
    """页内一条表格行带：上下两条横线之间的内容。"""

    __slots__ = ("top", "bottom", "words", "bounds", "row")

    def __init__(self, top: float, bottom: float, words: list[Any], bounds: list[float]) -> None:
        self.top = top
        self.bottom = bottom
        self.words = words
        self.bounds = bounds
        self.row = _cells(words, bounds)

    def set_bounds(self, bounds: Sequence[float]) -> None:
        self.bounds = list(bounds)
        self.row = _cells(self.words, bounds)


class _Grid:
    """一页的表格行带。"""

    __slots__ = ("number", "text", "unit", "bands")

    def __init__(self, number: int, text: str, bands: list[_Band]) -> None:
        self.number = number
        self.text = text
        match = _UNIT.search(text or "")
        self.unit = match.group(1) if match else ""
        self.bands = bands


def _cluster(values: Iterable[float], tolerance: float = _EDGE_TOLERANCE) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _cells(words: Sequence[Any], bounds: Sequence[float]) -> list[str]:
    """按列边界把词放进单元格；同格内按 (top, x0) 顺序拼接，天然修复折行标签。"""
    if not bounds:
        return []
    buckets: dict[int, list[Any]] = {}
    for word in words:
        x0 = float(word["x0"])
        column = sum(1 for bound in bounds if x0 >= bound)
        buckets.setdefault(column, []).append(word)
    row = []
    for column in range(max(buckets) + 1):
        ordered = sorted(
            buckets.get(column, []),
            key=lambda item: (round(float(item["top"]), 1), float(item["x0"])),
        )
        row.append("".join(str(item["text"]) for item in ordered).strip())
    return row


def _page_bands(page: Any) -> list[_Band]:
    horizontal = [
        float(edge["top"])
        for edge in page.horizontal_edges
        if float(edge["x1"]) - float(edge["x0"]) >= _MIN_HORIZONTAL_WIDTH
    ]
    if len(horizontal) < 2:
        return []
    # 首条横线以上的行（如续页第一行）也要成为行带；页脚不纳入，避免混入页码/注释
    tops = [0.0] + [top for top in _cluster(horizontal) if top > 1.0]
    if len(tops) < 2:
        return []
    words = page.extract_words()
    verticals = [
        (float(edge["x0"]), float(edge["top"]), float(edge["bottom"]))
        for edge in page.vertical_edges
    ]
    bands: list[_Band] = []
    for top, bottom in zip(tops, tops[1:]):
        if bottom - top < 2.0:
            continue
        inside = [
            word
            for word in words
            if top <= (float(word["top"]) + float(word["bottom"])) / 2 < bottom
        ]
        if not inside:
            continue
        bounds = _cluster(
            [
                x
                for x, y0, y1 in verticals
                if y0 <= bottom + 0.6 and y1 >= top - 0.6
            ],
            3.0,
        )
        bands.append(_Band(top, bottom, inside, bounds))
    return bands


def _grids(pdf_bytes: bytes) -> list[_Grid]:
    grids: list[_Grid] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            grids.append(_Grid(index, page.extract_text() or "", _page_bands(page)))
    return grids


def _text(row: Sequence[str]) -> str:
    return "".join(cell for cell in row if cell)


def _has(row: Sequence[str], needle: str) -> bool:
    return any(needle in cell for cell in row)


def _numeric(row: Sequence[str]) -> bool:
    return any(_AMOUNT.match(cell.strip()) for cell in row)


def _width(rows: Sequence[Sequence[str]]) -> int:
    return max((len(row) for row in rows), default=0)


def _pad(rows: Sequence[Sequence[str]], width: int) -> list[list[str]]:
    return [
        [str(cell).strip() for cell in row] + [""] * (width - len(row))
        for row in rows
    ]


def _is_header(row: Sequence[str]) -> bool:
    text = _text(row)
    if "行次" in text or "序号" in text:
        return True
    return any(term in text for term in ("本季度数", "期末数", "本年度累计数")) and (
        "项目" in text or "指标名称" in text
    )


def _header_for(width: int, kind: str) -> list[str]:
    known = _HEADERS.get((kind, width))
    if known is not None:
        return list(known)
    base = {
        "main": _MAIN_HEADER,
        "capital": _CAPITAL_HEADER,
        "operating": _OPERATING_HEADER,
        "ratio": _RATIO_HEADER,
    }[kind]
    return (base + [""] * width)[:width]


def _parse(row: Sequence[str], has_code: bool) -> tuple[str, str, list[str]]:
    """把一行拆成 (行次, 标签, 数值)。"""
    cells = [str(cell).strip() for cell in row]
    code = ""
    if has_code and cells and _CODE.match(cells[0]):
        code = cells[0]
        cells = cells[1:]
    label = ""
    values: list[str] = []
    for cell in cells:
        if not cell:
            continue
        if _AMOUNT.match(cell) or cell in ("-", "--", "—"):
            values.append(cell)
        elif not label:
            label = cell
    return code, label, values


def _merge(rows: Sequence[Sequence[str]], has_code: bool) -> list[list[str]]:
    """把被 PDF 拆开的物理行并回逻辑行。"""
    parsed = [_parse(row, has_code) for row in rows]
    merged: list[dict[str, Any]] = []
    for index, (code, label, values) in enumerate(parsed):
        upcoming = parsed[index + 1] if index + 1 < len(parsed) else None
        if code and not label and not values:
            # 行次单独占一行：附到上一条"只有标签"的行
            if merged and merged[-1]["label"] and not merged[-1]["code"] and not merged[-1]["values"]:
                merged[-1]["code"] = code
            else:
                merged.append({"code": code, "label": "", "values": []})
            continue
        if label and not code and not values:
            # 标签单独占一行：可能是下一行的首行，也可能是上一行的折行
            if upcoming is not None and upcoming[0] and not upcoming[1] and not upcoming[2]:
                merged.append({"code": "", "label": label, "values": []})
            elif len(label) <= 6 and merged and merged[-1]["label"] and not merged[-1]["values"]:
                merged[-1]["label"] += label
            elif len(label) <= 4 and merged and merged[-1]["values"]:
                merged[-1]["label"] += label
            else:
                merged.append({"code": "", "label": label, "values": []})
            continue
        if values and not label:
            # 数值单独占一行（可带行次）：接到上一条只有标签的行
            if merged and merged[-1]["label"] and not merged[-1]["values"] and not merged[-1]["code"]:
                merged[-1]["values"] = values
                merged[-1]["code"] = code
            else:
                merged.append({"code": code, "label": "", "values": values})
            continue
        merged.append({"code": code, "label": label, "values": values})
    return merged


def _assemble(
    table: Any,
    blocks: Sequence[tuple[int, str, list[list[str]]]],
    kind: str,
    *,
    has_code: bool,
) -> bool:
    width = max((_width(rows) for _, _, rows in blocks), default=0)
    if not width:
        return False
    matrix = [_pad(rows, width) for _, _, rows in blocks]
    flat = [row for part in matrix for row in part]
    if not flat:
        return False
    left, right = 0, width
    while left < right and all(not row[left].strip() for row in flat):
        left += 1
    while right > left and all(not row[right - 1].strip() for row in flat):
        right -= 1
    if right <= left:
        return False
    width = right - left
    table_blocks: list[dict[str, Any]] = []
    normalised: list[list[str]] = []
    last_row: list[str] | None = None
    label_pos = 1 if has_code else 0
    for (page, unit, _), rows in zip(blocks, matrix):
        trimmed = [row[left:right] for row in rows]
        merged = _merge(trimmed, has_code)
        # 续页首行若是上一页末行标签的折行残段，并回上一块
        if last_row is not None and merged:
            head = merged[0]
            if (
                head["label"]
                and not head["values"]
                and not head["code"]
                and len(head["label"]) <= 6
            ):
                last_row[label_pos] += head["label"]
                merged = merged[1:]
        data: list[list[str]] = []
        for item in merged:
            if not item["label"]:
                continue
            row = (
                [item["code"], item["label"], *item["values"]]
                if has_code
                else [item["label"], *item["values"]]
            )
            last_row = (row + [""] * width)[:width]
            data.append(last_row)
        if not data:
            continue
        block_rows = [_header_for(width, kind), *data]
        table_blocks.append({"page": page, "rows": block_rows, "unit": unit})
        normalised.extend(block_rows if not normalised else block_rows[1:])
    if not table_blocks:
        return False
    table.rows = normalised
    table.table_blocks = table_blocks
    table.source_pages = [block["page"] for block in table_blocks]
    table.page = table_blocks[0]["page"]
    units = {block["unit"] for block in table_blocks if block["unit"]}
    if len(units) == 1:
        table.unit = units.pop()
    return True


def _find_band(grid: _Grid, predicate: Callable[[Sequence[str]], bool]) -> int | None:
    for index, band in enumerate(grid.bands):
        if predicate(band.row):
            return index
    return None


def _header_index(grid: _Grid, start: int) -> int:
    for index in range(start, max(-1, start - 6), -1):
        if _is_header(grid.bands[index].row):
            return index
    return start


def _header_bounds(grid: _Grid, start: int) -> list[float]:
    """取表头行的列边界；不足两条竖线时返回空（调用方回退到行带自带边界）。"""
    if start >= len(grid.bands):
        return []
    bounds = list(grid.bands[start].bounds)
    return bounds if len(bounds) >= 2 else []


def _collect(
    grids: Sequence[_Grid],
    start_grid: int,
    start_band: int,
    stop: Callable[[Sequence[str]], bool],
    *,
    bounds: Sequence[float],
    max_pages: int = 3,
) -> list[tuple[int, str, list[list[str]]]]:
    """从 (grid, band) 起按行带收集，命中 stop 或越界即止；返回按页分块。

    列边界始终取自首块表头，不就地改写行带，避免不同表互相污染。
    """
    blocks: list[tuple[int, str, list[list[str]]]] = []
    for offset, grid in enumerate(grids[start_grid:start_grid + max_pages]):
        first = start_band if offset == 0 else 0
        rows: list[list[str]] = []
        for band in grid.bands[first:]:
            row = _cells(band.words, bounds) if bounds else list(band.row)
            if _is_header(row):
                continue
            rows.append(row)
            if stop(row):
                blocks.append((grid.number, grid.unit, rows))
                return blocks
        blocks.append((grid.number, grid.unit, rows))
    return blocks


def _rebuild(
    grids: Sequence[_Grid],
    page_probe: Callable[[_Grid], bool],
    start: Callable[[Sequence[str]], bool],
    stop: Callable[[Sequence[str]], bool],
    *,
    max_pages: int = 3,
) -> list[tuple[int, str, list[list[str]]]] | None:
    for index, grid in enumerate(grids):
        if not page_probe(grid):
            continue
        band = _find_band(grid, start)
        if band is None:
            continue
        begin = _header_index(grid, band)
        bounds = _header_bounds(grid, begin)
        return _collect(grids, index, begin, stop, bounds=bounds, max_pages=max_pages)
    return None


def _main(grids: Sequence[_Grid]) -> list[tuple[int, str, list[list[str]]]] | None:
    return _rebuild(
        grids,
        lambda grid: "认可资产" in grid.text and "偿付能力充足率" in grid.text,
        lambda row: any(cell.strip() == "认可资产" for cell in row),
        lambda row: _has(row, "综合偿付能力充足率"),
        max_pages=2,
    )


def _actual_capital(grids: Sequence[_Grid]) -> list[tuple[int, str, list[list[str]]]] | None:
    return _rebuild(
        grids,
        lambda grid: "S02" in grid.text,
        lambda row: any(cell.strip() == "核心一级资本" for cell in row),
        lambda row: _has(row, "实际资本合计"),
    )


def _minimum_capital(grids: Sequence[_Grid]) -> list[tuple[int, str, list[list[str]]]] | None:
    return _rebuild(
        grids,
        lambda grid: "S05" in grid.text or "最低资本表" in grid.text,
        lambda row: any(cell.strip() == "量化风险最低资本" for cell in row),
        lambda row: any(cell.strip() == "最低资本" for cell in row) and _numeric(row),
    )


def _operating(grids: Sequence[_Grid]) -> list[tuple[int, str, list[list[str]]]] | None:
    return _rebuild(
        grids,
        lambda grid: "保险业务收入" in grid.text and "主要经营指标" in grid.text,
        lambda row: any(cell.strip().startswith("（一）保险业务收入") for cell in row),
        lambda row: _has(row, "其他渠道"),
    )


def _liquidity(grids: Sequence[_Grid]) -> list[tuple[int, str, list[list[str]]]] | None:
    return _rebuild(
        grids,
        lambda grid: (
            "流动性风险监测指标" in grid.text
            and "经营活动净现金流" in grid.text
        ),
        lambda row: _has(row, "经营活动净现金流"),
        lambda row: _has(row, "持有关联方资产"),
        max_pages=2,
    )


def _three_year(grids: Sequence[_Grid]) -> list[tuple[int, str, list[list[str]]]] | None:
    for grid in grids:
        matches = [
            band
            for band in grid.bands
            if "近三年平均" in _text(band.row) and "投资收益率" in _text(band.row)
        ]
        if len(matches) >= 2:
            return [(grid.number, "", [list(band.row) for band in matches[:2]])]
    return None


_BUILDERS = {
    "SOLVENCY_MAIN": (_main, False, "main"),
    "ACTUAL_CAPITAL": (_actual_capital, True, "capital"),
    "MINIMUM_CAPITAL": (_minimum_capital, True, "capital"),
    "NON_LIFE_OPERATING_METRICS": (_operating, False, "operating"),
    "OPERATING_METRICS": (_operating, False, "operating"),
    "LIQUIDITY_MONITORING": (_liquidity, False, "ratio"),
    "THREE_YEAR_INVESTMENT_RETURN": (_three_year, False, "ratio"),
}


@register("guoren")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """按页内行带重建国任保险六张目标表。"""
    grids = _grids(pdf_bytes)
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "").strip()
        spec = _BUILDERS.get(table_id)
        if spec is None:
            continue
        builder, has_code, kind = spec
        blocks = builder(grids)
        if not blocks:
            continue
        if not _assemble(table, blocks, kind, has_code=has_code):
            continue
        if table_id == "THREE_YEAR_INVESTMENT_RETURN":
            table.unit = ""
    return tables


from ._document import STANDARD_NON_LIFE_LAYOUT, make_document_hooks

_GUOREN_LAYOUT = dict(STANDARD_NON_LIFE_LAYOUT)
_GUOREN_LAYOUT["SOLVENCY_MAIN"] = {
    "title": "(?:主要)?偿付能力充足率指标",
    "required": ["认可资产"],
    "continuation": 1,
    "tail_terms": ["综合偿付能力充足率", "核心偿付能力充足率"],
    "stop": "(?:流动性风险|主要经营指标)",
    "unit": "万元",
}
locate_company_tables, extract_company_tables = make_document_hooks(
    _GUOREN_LAYOUT, "国任财险", postprocess
)

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("guoren", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "guoren",
        pdf_bytes,
        table_configs,
        mode="document",
    )
