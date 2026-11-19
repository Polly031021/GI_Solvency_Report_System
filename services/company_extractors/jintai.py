"""锦泰保险专属提取：按 PDF 文字坐标重建 6 张目标表。

锦泰偿付能力报告的文字层是“每个单元格独立定位”的（不是按阅读顺序
输出整行），表格线框也经常横穿合并单元格，通用网格提取会串列：

* 2022 年文字流先输出整列数值、再输出标签，网格把标签列与数值列错位；
* 2023 年主表被并成 2 列（``项目 期末数 期初数`` | ``下季度预测数``），
  实际资本表被列线切成 12 列碎片，流动性数值被拆成多个单元格；
* 2025 年实际资本表标签居中、数值右对齐，标签整体跨在两条列线之间。

本模块不依赖 pdfplumber 的网格，而是按坐标重建：同一页内 y 中心相差
不超过 4pt 的文字归为一行，行内 x 间隙大于 8pt 的切成单元格；再用
表头行的列序把“非中文单元格”（数值/``-``/``--``）落到对应列，中文
单元格拼接成行标签。这样对 2022-2025 四种版式共用一套逻辑，不写死
年份、页码与行数。

重建只改 table.rows / table.table_blocks / table.source_pages，每次从
PDF 重新解析，重复执行结果一致。
"""

from __future__ import annotations

import re
import statistics
from types import SimpleNamespace
from typing import Any, Sequence

from . import register

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_Y_TOL = 4.0
_CELL_GAP = 8.0
_MAX_ROWS = 90

# table_id -> 重建参数
#   needle        正文里唯一出现的目标行文字（且该行必须带数值，避免命中叙述段落）
#   header_tokens 表头行必须同时出现的文字
#   label_header  表头第一列改写成的名称
#   value_headers 可选：固定数值列表头（近三年表 PDF 写“数值”，系统需“近三年平均”）
_SPECS: dict[str, dict[str, Any]] = {
    "SOLVENCY_MAIN": {
        "needle": "综合偿付能力充足率",
        "header_tokens": ("项目", "期末数", "期初数"),
        "label_header": "项目",
        "unit": "元",
    },
    "NON_LIFE_OPERATING_METRICS": {
        "needle": "其他渠道签单保费",
        "header_tokens": ("主要经营指标", "本季度数"),
        "label_header": "指标名称",
        "unit": "元",
    },
    "ACTUAL_CAPITAL": {
        "needle": "非认可资产",
        "header_tokens": ("本季度末", "上季度末"),
        "label_header": "项目",
        "unit": "元",
    },
    "MINIMUM_CAPITAL": {
        "needle": "量化风险分散效应",
        "header_tokens": ("项目", "本季度末", "上季度末"),
        "label_header": "项目",
        "unit": "元",
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "needle": "近三年平均综合投资收益率",
        "header_tokens": ("指标名称", "数值"),
        "label_header": "指标名称",
        "value_headers": ("近三年平均",),
        "unit": "%",
    },
    "LIQUIDITY_MONITORING": {
        "needle": "百元保费经营活动净现金流",
        "header_tokens": ("本季度数", "上季度数"),
        "label_header": "流动性风险监测指标",
        "unit": "元",
    },
}

_OPERATING_ALIASES = {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}

# 无“（单位”等标记、但明显不是数据行的表头/标题
_STOP_HEADS = ("指标名称", "项目", "流动性风险监测指标", "实际资本", "主要经营指标")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _cell_text(words: Sequence[tuple]) -> str:
    parts: list[str] = []
    for index, word in enumerate(words):
        if index and float(word[0]) - float(words[index - 1][2]) > 2.6:
            left = parts[-1][-1:] if parts else ""
            right = str(word[4])[:1]
            # 只在两个 ASCII 字母/数字之间补空格，避免“AA 级”被拆开
            if left.isascii() and left.isalnum() and right.isascii() and right.isalnum():
                parts.append(" ")
        parts.append(str(word[4]))
    return "".join(parts).strip()


class _Page:
    """一页文字按 y 聚类成行、行内按 x 间隙切成单元格。"""

    __slots__ = ("page_no", "lines")

    def __init__(self, page_no: int, lines: list[dict[str, Any]]) -> None:
        self.page_no = page_no
        self.lines = lines

    @classmethod
    def parse(cls, page_no: int, page: Any) -> "_Page":
        words = [w for w in page.get_text("words") if str(w[4]).strip()]
        height = float(page.rect.height)
        # 去掉页脚印刷页码
        words = [w for w in words if float(w[1]) < height - 55]
        buckets: list[list[Any]] = []
        for word in sorted(
            words, key=lambda w: ((float(w[1]) + float(w[3])) / 2.0, float(w[0]))
        ):
            center = (float(word[1]) + float(word[3])) / 2.0
            if buckets and abs(buckets[-1][0] - center) <= _Y_TOL:
                buckets[-1][1].append(word)
            else:
                buckets.append([center, [word]])
        lines: list[dict[str, Any]] = []
        for center, items in buckets:
            items.sort(key=lambda w: float(w[0]))
            cells: list[list[tuple]] = []
            for word in items:
                if cells and float(word[0]) - float(cells[-1][-1][2]) <= _CELL_GAP:
                    cells[-1].append(word)
                else:
                    cells.append([word])
            built = [
                {
                    "text": _cell_text(cell),
                    "x0": float(cell[0][0]),
                    "x1": float(cell[-1][2]),
                }
                for cell in cells
            ]
            built = [cell for cell in built if cell["text"]]
            if built:
                lines.append({"y": center, "cells": built})
        return cls(page_no, lines)

    def value_cells(self, line: dict[str, Any]) -> list[dict[str, Any]]:
        return [cell for cell in line["cells"] if not _has_cjk(cell["text"])]

    def label_of(self, line: dict[str, Any]) -> str:
        return "".join(cell["text"] for cell in line["cells"] if _has_cjk(cell["text"]))

    def find_anchor(self, needle: str) -> int | None:
        for index, line in enumerate(self.lines):
            joined = "".join(cell["text"] for cell in line["cells"])
            if needle in joined and self.value_cells(line):
                return index
        return None

    def find_header(self, anchor: int, tokens: Sequence[str]) -> int | None:
        needles = [_norm(token) for token in tokens]
        for index in range(anchor - 1, -1, -1):
            cells = self.lines[index]["cells"]
            joined = "".join(_norm(cell["text"]) for cell in cells)
            if len(cells) >= len(needles) and all(token in joined for token in needles):
                return index
        return None


def _looks_like_stop(head: str) -> bool:
    text = _norm(head)
    if not text:
        return True
    if re.match(r"^[一二三四五六七八九十]+、", text):
        return True
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]", text):
        return True
    if "（单位" in text or "(单位" in text:
        return True
    if text.startswith(("注：", "注:", "说明：")):
        return True
    if text in _STOP_HEADS:
        return True
    return False


def _column_centers(rows: Sequence[list[dict[str, Any]]], count: int) -> list[float] | None:
    centers: list[list[float]] = [[] for _ in range(count)]
    for cells in rows:
        if len(cells) != count:
            continue
        for index, cell in enumerate(cells):
            centers[index].append((cell["x0"] + cell["x1"]) / 2.0)
    if any(not bucket for bucket in centers):
        return None
    return [statistics.median(bucket) for bucket in centers]


def _assign(values: Sequence[dict[str, Any]], centers: Sequence[float] | None, count: int) -> list[str]:
    if centers is None:
        slots = [cell["text"] for cell in values][:count]
        return slots + [""] * (count - len(slots))
    slots = [""] * count
    for cell in values:
        center = (cell["x0"] + cell["x1"]) / 2.0
        index = min(range(count), key=lambda i: abs(centers[i] - center))
        if slots[index]:
            # 数值跨列线被切成两格时并回同一列
            slots[index] = slots[index] + cell["text"]
        else:
            slots[index] = cell["text"]
    return slots


def _header_row(header_cells: Sequence[dict[str, Any]], spec: dict[str, Any]) -> list[str]:
    values = [_norm(cell["text"]) for cell in header_cells[1:]]
    if spec.get("value_headers"):
        values = [str(v) for v in spec["value_headers"]]
    return [str(spec.get("label_header") or "项目"), *values]


def _rebuild(page: _Page, spec: dict[str, Any]) -> tuple[list[list[str]], int] | None:
    anchor = page.find_anchor(str(spec["needle"]))
    if anchor is None:
        return None
    header_index = page.find_header(anchor, spec["header_tokens"])
    if header_index is None:
        return None
    header = _header_row(page.lines[header_index]["cells"], spec)
    value_count = max(1, len(header) - 1)

    parsed: list[tuple[str, list[dict[str, Any]]]] = []
    for line in page.lines[header_index + 1 :][:_MAX_ROWS]:
        values = page.value_cells(line)
        label = page.label_of(line)
        if not values:
            if _looks_like_stop(label):
                break
            if parsed and label:
                # 长标签换行：并入上一行标签
                previous_label, previous_values = parsed[-1]
                parsed[-1] = (previous_label + label, previous_values)
            continue
        if not label:
            continue
        parsed.append((label, values))
    if not parsed:
        return None

    centers = _column_centers([values for _, values in parsed], value_count)
    rows: list[list[str]] = [list(header)]
    for label, values in parsed:
        rows.append([label, *_assign(values, centers, value_count)])
    return rows, page.page_no


def _apply(table: Any, rows: list[list[str]], page_no: int, unit: str) -> None:
    table.rows = [list(row) for row in rows]
    table.table_blocks = [{"page": page_no, "rows": [list(row) for row in rows]}]
    table.source_pages = [page_no]
    table.page = page_no
    table.unit = unit
    table.strategy = "锦泰坐标重建"
    table.evidence = f"锦泰 {page_no} 页按文字坐标重建"
    table.quality_score = 0.0


@register("jintai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """锦泰专属后处理入口；异常由调用方捕获，不在这里吞掉。"""
    import pymupdf as fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages: dict[int, _Page] = {}

        def page_of(page_no: int) -> _Page:
            if page_no not in pages:
                pages[page_no] = _Page.parse(page_no, doc[page_no - 1])
            return pages[page_no]

        def locate(needle: str, tokens: Sequence[str]) -> _Page | None:
            for page_no in range(1, doc.page_count + 1):
                if needle not in doc[page_no - 1].get_text("text"):
                    continue
                page = page_of(page_no)
                anchor = page.find_anchor(needle)
                if anchor is None:
                    continue
                # 同一指标可能先出现在主表（如“量化风险分散效应”），
                # 必须整页表头也对得上才认。
                if page.find_header(anchor, tokens) is None:
                    continue
                return page
            return None

        rebuilt: list[Any] = []
        seen: set[str] = set()
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            key = "NON_LIFE_OPERATING_METRICS" if table_id in _OPERATING_ALIASES else table_id
            spec = _SPECS.get(key)
            if spec is None or key in seen:
                continue
            seen.add(key)
            page = locate(str(spec["needle"]), spec["header_tokens"])
            result = _rebuild(page, spec) if page is not None else None
            if result is None:
                # 保留通用结果；若能定位到页说明版式已变，记录在案
                rebuilt.append(table)
                continue
            rows, page_no = result
            _apply(table, rows, page_no, str(spec["unit"]))
            rebuilt.append(table)

        # 定位阶段漏掉的表（例如 2022 年主表被并成 2 列、近三年表整页漏提）
        for key, spec in _SPECS.items():
            if key in seen:
                continue
            page = locate(str(spec["needle"]), spec["header_tokens"])
            result = _rebuild(page, spec) if page is not None else None
            if result is None:
                continue
            rows, page_no = result
            rebuilt.append(
                SimpleNamespace(
                    table_id=key,
                    table_name=str(spec["needle"]),
                    page=page_no,
                    table_index=0,
                    rows=rows,
                    table_blocks=[{"page": page_no, "rows": rows}],
                    source_pages=[page_no],
                    unit=str(spec["unit"]),
                    strategy="锦泰坐标重建",
                    evidence=f"锦泰 {page_no} 页按文字坐标重建（补建）",
                    quality_score=0.0,
                    profile_strategy_id="",
                    unit_records=[],
                    header_records=[],
                )
            )
        return rebuilt
    finally:
        doc.close()
