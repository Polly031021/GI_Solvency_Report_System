"""渤海财险专属提取后处理（2022-2025 版式）。

渤海财险第四季度摘要的公开表在通用网格提取上有四处系统性缺口：

1. ``主要偿付能力指标``（SOLVENCY_MAIN）从 p11 跨到 p12，但 2023-2025 的
   p11/p12 线框只识别出“纯数值帧”，标签列整列丢失；2022 虽有标签，却把
   ``特定类保险合同损`` + ``失吸收效应`` 拆成两行、且没有表头。这里按 PDF
   正文行重建为 ``项目/本季度末数/上季度末数/下季度预测数`` 四列：
   从 p11 的“认可资产”开始，按“标签行 + 3 个数值行”收行，空值行
   （核心二级资本/附属二级资本）按标签补空，到 p12 的
   “（三）流动性风险指标”停止。

2. ``实际资本表``(S02) / ``最低资本表``(S05) 的第二页是无表头续页，通用
   网格把续页行排成“行次+标签+2 值”四列、首页排成三列，还混入
   ``【第N页表格分块】`` 占位行；S05 还会被 Profile 的终止词
   “最低资本合计/最低资本”截断在前几行（2023 年只剩 10 行）。这里按
   “行次 / 项目 / 期末数 / 期初数”正文行重建整表，跨页直到下一页不再是
   行次续页为止。

3. ``近三年（综合）投资收益率`` 在 2023-2025 只是一句话（p13），不是网格
   表；通用流程定位到 p13 后取到的是流动性监测表续页。这里按正文正则合成
   两行指标；2022 年报告没有该节，保持缺失（不补建）。

4. ``流动性监测指标`` 在 2022/2023 被列线切出第 5 个空列，LCR 行的本季度值
   落在第 4 列；2024/2025 正常。这里把每行归一为
   ``指标名称/本季度/上季度`` 三列，并去掉分块占位行与分块小表头。

所有重建都同时写 ``table.rows``（供 STEP2 标准化）与 ``table.table_blocks``
（供 STEP3 分页/单位处理），且不依赖具体年份与页码——只认表标题、行次与
章节标题。经营指标表（NON_LIFE_OPERATING_METRICS）通用提取四年均正确，
这里不改动。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_VALUE_RE = re.compile(r"^(?:-|--|—|[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)$")
_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")

_MAIN_HEADER = ["项目", "本季度末数", "上季度末数", "下季度预测数"]
_CAPITAL_HEADER = ["行次", "项目", "期末数", "期初数"]
_THREE_HEADER = ["指标名称", "数值"]
_LIQUIDITY_HEADER = ["指标名称", "本季度", "上季度"]

_MAIN_LABELS = {
    "认可资产",
    "认可负债",
    "实际资本",
    "其中:核心一级资本",
    "核心二级资本",
    "附属一级资本",
    "附属二级资本",
    "最低资本",
    "其中:量化风险最低资本",
    "保险风险最低资本",
    "市场风险最低资本",
    "信用风险最低资本",
    "风险分散效应",
    "特定类保险合同损失吸收效应",
    "控制风险最低资本",
    "附加资本",
    "核心偿付能力溢额",
    "核心偿付能力充足率",
    "综合偿付能力溢额",
    "综合偿付能力充足率",
}
_MAIN_HEADING_TERMS = ("主要偿付能力指标", "偿付能力充足率指标")
_CAPITAL_SKIP = {"行次", "项目", "期末数", "期初数", "项目名称", "续表"}
_ACTUAL_HEADINGS = ("实际资本表", "实际资本各项指标对比表", "实际资本明细表")
_MINIMUM_HEADINGS = ("最低资本表",)
_LIQUIDITY_TITLE_TOKENS = {"监管指标", "监测指标"}


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _label_text(label: Sequence[str]) -> str:
    return _compact("".join(label)).replace("：", ":")


def _is_value(cell: str) -> bool:
    return bool(_VALUE_RE.match(_compact(cell)))


def _page_lines(doc: Any, page_no: int) -> list[str]:
    """取一页正文行，去掉页眉“公司名 + 年第N季度…摘要”与印刷页码。"""
    if not 1 <= page_no <= doc.page_count:
        return []
    raw = [_clean(line) for line in doc[page_no - 1].get_text("text").splitlines()]
    raw = [line for line in raw if line]
    lines: list[str] = []
    for index, line in enumerate(raw):
        head = _compact(line)
        if "偿付能力季度报告摘要" in head:
            continue
        if head == "渤海财产保险股份有限公司":
            continue
        if index < 4 and _PAGE_NO_RE.fullmatch(head):
            if abs(int(head) - page_no) <= 3:
                continue
        lines.append(line)
    return lines


def _find_heading(
    doc: Any, terms: Sequence[str], start_page: int = 1
) -> tuple[int | None, list[str], int | None]:
    for page_no in range(max(1, int(start_page)), doc.page_count + 1):
        lines = _page_lines(doc, page_no)
        for index, line in enumerate(lines):
            head = _compact(line)
            if any(term in head for term in terms):
                return page_no, lines, index
    return None, [], None


def _sync(table: Any, rows: Sequence[Sequence[str]], pages: Sequence[int]) -> None:
    flat = [[str(cell or "") for cell in row] for row in rows]
    page = int(pages[0]) if pages else int(getattr(table, "page", 1) or 1)
    table.rows = flat
    table.table_blocks = [{"page": page, "rows": [list(row) for row in flat]}]
    table.source_pages = [int(item) for item in pages] or [page]


# ---------------------------------------------------------------------------
# 主要偿付能力指标（SOLVENCY_MAIN）
# ---------------------------------------------------------------------------
def _main_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        head = _compact(line)
        if any(term in head for term in _MAIN_HEADING_TERMS):
            return index
    return None


def _main_first_row(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        if _compact(line) == "认可资产":
            return index
    return None


def _main_stop(line: str) -> bool:
    head = _compact(line)
    if head.startswith("（三）") or head.startswith("(三)"):
        return True
    return "流动性风险指标" in head


def _append_main_row(
    rows: list[list[str]], label: Sequence[str], values: Sequence[str]
) -> None:
    text = _label_text(label)
    if not text:
        return
    vals = [str(item) for item in values[:3]]
    rows.append([text, *vals, *[""] * (3 - len(vals))])


def _rebuild_main(table: Any, doc: Any) -> bool:
    start_page: int | None = None
    for page_no in range(1, doc.page_count + 1):
        if _main_heading_index(_page_lines(doc, page_no)) is not None:
            start_page = page_no
            break
    if start_page is None:
        return False
    rows: list[list[str]] = []
    pages: list[int] = []
    label: list[str] = []
    values: list[str] = []
    started = False
    stopped = False
    for page_no in range(start_page, doc.page_count + 1):
        lines = _page_lines(doc, page_no)
        start = 0
        if not started:
            found = _main_first_row(lines)
            if found is None:
                continue
            start = found
            started = True
        pages.append(page_no)
        for line in lines[start:]:
            if _main_stop(line):
                stopped = True
                break
            if _is_value(line):
                if not label:
                    continue
                values.append(_clean(line))
                if len(values) >= 3:
                    _append_main_row(rows, label, values)
                    label, values = [], []
                continue
            # 标签行
            if values:
                _append_main_row(rows, label, values)
                label, values = [], []
            if label and _label_text(label) in _MAIN_LABELS:
                # 上一个标签已完整（如“核心二级资本”），本行是下一个标签，
                # 说明上一行没有数值，补空行。
                _append_main_row(rows, label, [])
                label = []
            label.append(line)
        if stopped:
            break
    if label:
        _append_main_row(rows, label, values)
    if not rows:
        return False
    _sync(table, [_MAIN_HEADER, *rows], pages or [start_page])
    table.unit = "万元"
    return True


# ---------------------------------------------------------------------------
# 实际资本表(S02) / 最低资本表(S05)
# ---------------------------------------------------------------------------
def _parse_capital_rows(
    lines: Sequence[str], start: int
) -> list[list[str]]:
    rows: list[list[str]] = []
    row_no: str | None = None
    label: list[str] = []
    values: list[str] = []

    def flush() -> None:
        nonlocal row_no, label, values
        if row_no is not None and label:
            text = _label_text(label)
            if text:
                rows.append(
                    [
                        row_no,
                        text,
                        values[0] if values else "",
                        values[1] if len(values) > 1 else "",
                    ]
                )
        row_no = None
        label = []
        values = []

    for line in lines[start:]:
        head = _compact(line)
        if head in _CAPITAL_SKIP:
            continue
        if head.startswith("单位") or head.startswith("公司名称"):
            continue
        if _ROW_NO_RE.fullmatch(head):
            flush()
            row_no = head
            continue
        if row_no is None:
            continue
        if _is_value(line) and len(values) < 2:
            values.append(_clean(line))
        elif not values:
            label.append(line)
    flush()
    return rows


def _continues_capital(doc: Any, page_no: int) -> bool:
    """续页必须“无新表头、首行是行次”，否则视为本表结束。"""
    lines = _page_lines(doc, page_no)
    heads = [_compact(line) for line in lines[:8]]
    if "项目" in heads or "期初数" in heads or "期末数" in heads:
        return False
    if any("认可资产" in head or "认可负债" in head for head in heads):
        return False
    return any(_ROW_NO_RE.fullmatch(head) for head in heads[:6])


def _rebuild_capital(table: Any, doc: Any, headings: Sequence[str]) -> bool:
    pages_hint = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).strip().isdigit()
    ]
    hint = min(pages_hint) if pages_hint else int(getattr(table, "page", 1) or 1)
    page_no, lines, index = _find_heading(doc, headings, start_page=max(1, hint - 2))
    if page_no is None or index is None:
        return False
    pages: list[int] = []
    rows: list[list[str]] = []
    current = page_no
    start = index + 1
    first = True
    while current <= doc.page_count:
        page_lines = lines if first else _page_lines(doc, current)
        page_rows = _parse_capital_rows(page_lines, start)
        if page_rows:
            rows.extend(page_rows)
            pages.append(current)
        if not first and not page_rows:
            break
        if not first:
            if not _continues_capital(doc, current + 1):
                break
        else:
            if not rows or not _continues_capital(doc, current + 1):
                break
        first = False
        current += 1
        start = 0
    if not rows:
        return False
    _sync(table, [_CAPITAL_HEADER, *rows], pages or [page_no])
    table.unit = "元"
    return True


# ---------------------------------------------------------------------------
# 近三年（综合）投资收益率
# ---------------------------------------------------------------------------
def _rebuild_three_year(table: Any, doc: Any) -> bool:
    text = _compact("".join(doc[page].get_text("text") for page in range(doc.page_count)))
    rows: list[list[str]] = []
    plain = re.search(r"近三年平均投资收益率为?(-?\d+(?:\.\d+)?)%", text)
    combined = re.search(r"近三年平均综合投资收益率为?(-?\d+(?:\.\d+)?)%", text)
    if plain:
        rows.append(["近三年平均投资收益率", f"{plain.group(1)}%"])
    if combined:
        rows.append(["近三年平均综合投资收益率", f"{combined.group(1)}%"])
    if not rows:
        table.rows = []
        table.table_blocks = []
        return False
    page = int(getattr(table, "page", 1) or 1)
    _sync(table, [_THREE_HEADER, *rows], [page])
    table.unit = "%"
    return True


# ---------------------------------------------------------------------------
# 流动性监测指标
# ---------------------------------------------------------------------------
def _complete_liquidity_values(rows: list[list[str]], doc: Any) -> bool:
    """补回被线框拆到相邻帧、因而丢值的流动性行。

    2024/2025 的“规模保费同比增速”在正文里单独成行、数值紧跟其后两行，但
    几何帧把它切到了流动性表之外，通用提取只留下空单元格。这里只在
    “整行两列都空”时按正文行序回填 1-2 个数值，不覆盖已有值，因此幂等。
    """
    missing = {_compact(row[0]): row for row in rows if not row[1] and not row[2]}
    if not missing:
        return False
    lines: list[str] = []
    for page_no in range(1, doc.page_count + 1):
        lines.extend(_page_lines(doc, page_no))
    filled = False
    for index, line in enumerate(lines):
        row = missing.get(_compact(line))
        if row is None or row[1] or row[2]:
            continue
        values: list[str] = []
        for follow in lines[index + 1 : index + 4]:
            if not _is_value(follow):
                break
            values.append(_clean(follow))
            if len(values) == 2:
                break
        if values:
            row[1] = values[0]
            row[2] = values[1] if len(values) > 1 else ""
            filled = True
    return filled


def _normalize_liquidity(table: Any, doc: Any) -> bool:
    rows: list[list[str]] = []
    for raw in getattr(table, "rows", None) or []:
        cells = [_clean(cell) for cell in (raw or [])]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        joined = _compact("".join(cells))
        if "【" in joined and "分块】" in joined:
            continue
        first = _compact(cells[0])
        if first in _LIQUIDITY_TITLE_TOKENS:
            # “监管指标/具体指标/本季度/上季度”表头或分块小表头
            continue
        if len(cells) >= 4:
            label = _compact(cells[1])
            values = cells[2:]
            if len(values) >= 3 and not values[1] and values[2]:
                pair = [values[0], values[2]]
            else:
                pair = [values[0], values[1] if len(values) > 1 else ""]
        elif len(cells) == 3:
            label = _compact(cells[0])
            pair = [cells[1], cells[2]]
        else:
            label = _compact(cells[0])
            pair = ["", ""]
        if not label:
            continue
        rows.append([label, _clean(pair[0]), _clean(pair[1]) if len(pair) > 1 else ""])
    if not rows:
        return False
    _complete_liquidity_values(rows, doc)
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).strip().isdigit()
    ]
    _sync(table, [_LIQUIDITY_HEADER, *rows], pages or [int(getattr(table, "page", 1) or 1)])
    return True


@register("bohai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """渤海财险专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        rebuilt: list[Any] = list(tables)
        for table in rebuilt:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                _rebuild_main(table, doc)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_capital(table, doc, _ACTUAL_HEADINGS)
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_capital(table, doc, _MINIMUM_HEADINGS)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                _rebuild_three_year(table, doc)
            elif table_id == "LIQUIDITY_MONITORING":
                _normalize_liquidity(table, doc)
    finally:
        doc.close()
    return rebuilt
