"""国寿财险专属提取后处理（2022-2025 版式）。

覆盖通用规则无法处理、但数据确实印在 PDF 上的四处缺口：

1. ``THREE_YEAR_INVESTMENT_RETURN``：近三年（综合）投资收益率只是经营指标页
   末尾"（四）"小节的两行正文（没有独立线框表），通用流程按表特征词选帧时会
   把同页的经营指标表整张当成本表。这里按正文文本重建两行。
2. ``SOLVENCY_MAIN``：2022/2023 年第 22/23 页表头是两行（"本季末 上季末 下季度预测"
   与"指标名称/（过渡期政策）"），网格只保留了下半行，首列变空、三个期间列只剩
   "（过渡期政策）"。这里用正文行把表头补回。
3. ``NON_LIFE_OPERATING_METRICS``：2022/2023 年"本季度非车险前五大险种的签单保费"
   的标签是竖排文字，被拆成"本季度非车 / 险前五大险 / 种的签单保 / 费（万元）"
   若干碎片行，并与五个险种名、金额混排。这里按正文行重排该段。
4. ``LIQUIDITY_MONITORING``：表被拆成两页块，拼接时产生"【第N页表格分块】"伪行；
   表头"本季度指标值/上季度指标值"与标准口径不一致，且"流动性覆盖率"是
   未来3个月/未来12个月的两级表头。这里合并为一张三列表，把 LCR 行拆成
   "LCRx未来3个月"/"LCRx未来12个月"、回溯不利偏差率按本季度/上季度对齐；
   "公司净现金流"三行是年度口径（本年度累计/上一会计年度/上上会计年度），
   标准库 NET_CASH_FLOW 只有季度期间，保留会按本季度数错配，故不输出。

文本读取统一用 pdfplumber：fitz 会把同一行的"标签 + 两个金额"拆成三行，
pdfplumber 的 ``extract_text`` 才是"一行一条指标"的版式。
只改 ``table.rows`` 与 ``table.table_blocks``（两者同步），不写死年份/页码。
"""

from __future__ import annotations

import io
import re
from typing import Any, Callable, Sequence

from . import register

_AMOUNT = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_THREE_YEAR_HEADER = ("项目", "数值")
_LIQUIDITY_HEADER = ("指标名称", "本季度数", "上季度数")
_TOP5_GROUP = "本季度非车险前五大险种的签单保费（万元）"
_POLICY_MARK = "过渡期政策"
_ADVERSE_DEVIATION = "经营活动净现金流回溯不利偏差率"
_CASH_FLOW_UNIT_LABEL = "公司净现金流（单位：万元）"

_RETURN_PAIR_RE = re.compile(
    rf"近三年.{{0,24}}?平均(?P<kind>综合)?投资收益率\s*(?P<value>-?{_AMOUNT}%)"
)
_MAIN_PERIOD_RE = re.compile(r"本季末\s+上季末\s+下季度(?:末)?预测")
_VEHICLE_PREMIUM_RE = re.compile(r"^车险签单保费")
_VEHICLE_AVG_RE = re.compile(r"^车险车均保费")
_TOP5_GROUP_RE = re.compile(r"^本季度非车险前五大险种")
_TOP5_VALUE_RE = re.compile(
    rf"^(?P<label>.*?)\s+(?P<cur>{_AMOUNT}|-)\s+(?P<ytd>{_AMOUNT}|-)\s*$"
)
_NUMBER_RE = re.compile(rf"^{_AMOUNT}%?$")
_PSEUDO_BLOCK_RE = re.compile(r"^【第\s*\d+\s*页表格分块】$")
_INLINE_YUAN_RE = re.compile(r"[（(]\s*元\s*[）)]")
_LCR_RE = re.compile(r"^LCR([123])")
# 净现金流三行按"本年度累计/上一会计年度/上上会计年度"披露，标准库
# NET_CASH_FLOW 只接受本季度数/上季度数；保留会按季度口径错配（实测把
# "上一会计年度净现金流"记成了本季度数），因此不输出，见 plans/company-国寿财险.md。
_CASH_FLOW_LABELS = (
    "本年度累计",
    "上一会计年度净现金流",
    "上一会计年度之前的会计年度净现金流",
)

PageText = Callable[[int], str]


def _table_pages(table: Any) -> list[int]:
    pages: list[int] = []
    raw = list(getattr(table, "source_pages", None) or [])
    raw.append(getattr(table, "page", None))
    for value in raw:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return pages


def _sync_blocks(
    table: Any,
    rows: list[list[str]],
    pages: Sequence[int],
    unit: str = "",
) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。

    ``unit`` 写到表块上，供 STEP3 的 ``_block_unit`` 识别“（元）”段落；
    不传时沿用整表单位。
    """
    table.rows = rows
    page = int(pages[0]) if pages else 1
    block: dict[str, Any] = {"page": page, "rows": [list(row) for row in rows]}
    if unit:
        block["unit"] = unit
    table.table_blocks = [block]


def _rebuild_three_year(table: Any, page_text: PageText, pages: Sequence[int]) -> None:
    found: dict[str, str] = {}
    for page in pages:
        for match in _RETURN_PAIR_RE.finditer(page_text(page)):
            key = "综合" if match.group("kind") else "普通"
            found.setdefault(key, match.group("value"))
    if len(found) < 2:
        return
    rows = [
        list(_THREE_YEAR_HEADER),
        ["近三年平均投资收益率", found["普通"]],
        ["近三年平均综合投资收益率", found["综合"]],
    ]
    _sync_blocks(table, rows, pages)


def _fix_main_header(table: Any, page_text: PageText, pages: Sequence[int]) -> None:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    if not rows:
        return
    head = [str(cell or "").strip() for cell in rows[0]]
    if head[0] and head[0] != "指标名称":
        return
    if not any(_POLICY_MARK in cell for cell in head):
        return
    periods: list[str] | None = None
    for page in pages:
        for line in page_text(page).splitlines():
            match = _MAIN_PERIOD_RE.search(line)
            if match:
                periods = match.group(0).split()
                break
        if periods:
            break
    if not periods or len(periods) != len(head) - 1:
        return
    rows[0] = ["指标名称", *periods]
    _sync_blocks(table, rows, pages)


def _operating_section(rows: Sequence[Sequence[str]]) -> tuple[int, int] | None:
    start = end = None
    for index, row in enumerate(rows):
        cells = [str(cell or "").strip() for cell in row]
        label = cells[0] if cells else ""
        if start is None and _VEHICLE_PREMIUM_RE.match(label):
            start = index
        elif start is not None and _VEHICLE_AVG_RE.match(label):
            end = index
            break
    if start is None or end is None or end <= start + 1:
        return None
    return start, end


def _section_is_clean(rows: Sequence[Sequence[str]], start: int, end: int) -> bool:
    """2024/2025 的该段是"分组标题 + 5 个险种 + 两个金额"，无需重排。"""
    section = [list(row) for row in rows[start + 1:end]]
    if len(section) != 6:
        return False
    head = [str(cell or "").strip() for cell in section[0]]
    if not head or not _TOP5_GROUP_RE.match(head[0]):
        return False
    for row in section[1:]:
        cells = [str(cell or "").strip() for cell in row]
        if len(cells) < 2 or not _NUMBER_RE.match(cells[1]):
            return False
    return True


def _parse_top5_from_text(page_text: PageText, pages: Sequence[int]) -> list[list[str]] | None:
    for page in pages:
        lines = [line.strip() for line in page_text(page).splitlines()]
        start = next(
            (i for i, line in enumerate(lines) if _VEHICLE_PREMIUM_RE.match(line)),
            None,
        )
        if start is None:
            continue
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if _VEHICLE_AVG_RE.match(lines[index]):
                end = index
                break
        remaining = _TOP5_GROUP
        name = ""
        parsed: list[list[str]] = []
        for line in lines[start + 1:end]:
            if not line:
                continue
            match = _TOP5_VALUE_RE.match(line)
            if match:
                label_part = match.group("label")
                cur, ytd = match.group("cur"), match.group("ytd")
            else:
                label_part, cur, ytd = line, "", ""
            for token in label_part.split():
                if remaining.startswith(token):
                    remaining = remaining[len(token):]
                else:
                    name = token
            if cur and cur != "-" and name:
                parsed.append([name, cur, ytd if ytd and ytd != "-" else ""])
                name = ""
        if len(parsed) >= 5:
            return parsed
    return None


def _fix_operating_top5(table: Any, page_text: PageText, pages: Sequence[int]) -> None:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    section = _operating_section(rows)
    if section is None:
        return
    start, end = section
    if _section_is_clean(rows, start, end):
        return
    parsed = _parse_top5_from_text(page_text, pages)
    if parsed is None:
        return
    # 2024/2025 的正文把"非车险前五大险种的签单保费"与金额写在同一行，
    # 解析结果自带分组行；2022/2023 只有"险种名 + 两个金额"的 5 行，
    # 报告本身不给合计值，补空分组行会被标准库判成"空白按 0 处理"，故不补。
    _sync_blocks(table, [*rows[: start + 1], *parsed, *rows[end:]], pages)


def _rebuild_liquidity(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    source: list[list[str]] = []
    for row in rows:
        cells = [str(cell or "").strip() for cell in row]
        if not any(cells) or _PSEUDO_BLOCK_RE.match(cells[0]):
            continue
        source.append(cells)

    # 已经是本函数产出的形态（3 列、首行即目标表头）就直接返回，
    # 保证 postprocess 重复执行不会把 LCR 行再拆一遍（幂等）。
    if source and tuple(source[0]) == _LIQUIDITY_HEADER:
        return [list(cells) for cells in source]

    output: list[list[str]] = [list(_LIQUIDITY_HEADER)]
    quarter_value = ""
    for cells in source:
        label = cells[0]
        values = [cell for cell in cells[1:] if cell]
        if label in ("指标名称", "流动性覆盖率") or label.startswith("未来"):
            continue
        match = _LCR_RE.match(label)
        if match:
            number = match.group(1)
            if "自测" in label:
                # 指标库没有“自测压力情景”的编码，且标准库会把这类标签模糊匹配到
                # 同期限的必测编码上（实测 2023/2025 的 LCR2/LCR3 12 个月被污染），
                # 故不输出；必测 6 个编码已逐年与 PDF 核对。
                continue
            padded = [*values, "", "", ""][:4]
            for suffix, quarter, prior in (
                ("未来3个月", 0, 2),
                ("未来12个月", 1, 3),
            ):
                output.append(
                    [f"LCR{number}{suffix}", padded[quarter], padded[prior]]
                )
            continue
        if label == _ADVERSE_DEVIATION and not values:
            continue  # 分组标题，由下面两行合并
        if label == f"{_ADVERSE_DEVIATION}（本季度）":
            quarter_value = values[0] if values else ""
            continue
        if label == f"{_ADVERSE_DEVIATION}（上一季度）":
            output.append(
                [_ADVERSE_DEVIATION, quarter_value, values[0] if values else ""]
            )
            quarter_value = ""
            continue
        if label == _CASH_FLOW_UNIT_LABEL and not values:
            continue
        if label in _CASH_FLOW_LABELS:
            continue
        output.append(
            [label, values[0] if values else "", values[1] if len(values) > 1 else ""]
        )
    return output


@register("guoshou")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """国寿财险专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import pdfplumber

    cache: dict[int, str] = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:

        def page_text(page: int) -> str:
            number = int(page)
            if number not in cache:
                try:
                    cache[number] = pdf.pages[number - 1].extract_text() or ""
                except IndexError:  # 页码越界按"没有文本"处理
                    cache[number] = ""
            return cache[number]

        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            pages = _table_pages(table)
            if not pages:
                continue
            if table_id == "THREE_YEAR_INVESTMENT_RETURN":
                _rebuild_three_year(table, page_text, pages)
            elif table_id == "SOLVENCY_MAIN":
                _fix_main_header(table, page_text, pages)
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                _fix_operating_top5(table, page_text, pages)
            elif table_id == "LIQUIDITY_MONITORING":
                rows = _rebuild_liquidity(list(getattr(table, "rows", None) or []))
                block_unit = "元" if any(_INLINE_YUAN_RE.search(row[0]) for row in rows) else ""
                _sync_blocks(table, rows, pages, unit=block_unit)
    return tables
