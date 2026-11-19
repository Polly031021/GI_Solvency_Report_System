"""华泰财险专属提取后处理：重建经营指标、流动性与近三年投资收益率。

华泰财险 2022-2025 四份 Q4 报告版式一致，但通用规则有三处取错：

1. 主要经营指标表跨两页：首页只有“保险业务收入/净利润”2 行，续页无表头；
   首页同时含“流动性风险监测指标”，整页提取会把 11 行监测表当成经营表。
2. 流动性页把“监管指标”“LCR1/2/3 必测+自测”“监测指标”混在一页，
   通用结果会并进主表或 `【第N页表格分块】` 占位行。
3. 近三年（综合）投资收益率 2023-2025 只有正文一句话，没有表格；2022 全册
   没有该披露。表对象缺失时由本模块补建。

专属逻辑只改 ``table.rows`` / ``table.table_blocks`` / 单位，数据全部从 PDF
文本重新解析；按标题和行标签定位，不写死年份与页码，重复执行结果一致。
流动性按国寿口径丢弃 3 行年度净现金流（标准库只有季度期间）和 6 行自测压力
情景 LCR2/LCR3（标准库无自测编码，避免污染必测编码）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

import fitz

from ..batch_extraction import FlexibleTable
from . import register

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_SPACE_RE = re.compile(r"\s+")
_LCR_TERM_RE = re.compile(r"未来\s*(3|12)\s*个月")
_LCR_BASE_RE = re.compile(r"^(LCR[123])")
_THREE_YEAR_RE = re.compile(
    r"近三年平均(综合)?投资收益率(?:为)?\s*([\d.]+)\s*%"
)

_OPER_HEADER = ["指标名称", "本季度数", "本年度数"]
_MAIN_HEADER = ["指标名称", "本季度数", "上季度数", "下季度预测数"]
_LIQ_HEADER = ["指标名称", "本季度数", "上季度数"]
_THREE_YEAR_HEADER = ["指标名称", "本年累计"]

_OPER_HEADER_TOKENS = {
    "指标名称",
    "本季度数",
    "本年度数",
    "本季度",
    "本年度",
    "本年累计数",
    "本年累计",
    "单位：万元",
}
_LIQ_HEADER_TOKENS = {
    "指标名称",
    "项目",
    "本季度",
    "上季度",
    "本季度数",
    "上季度数",
    "单位：万元",
}
_OPER_GROUP_RE = re.compile(r"^（十[一二]）(?:效益类|规模类)指标$")


def _cell(value: Any) -> str:
    return _SPACE_RE.sub("", str(value or "")).strip()


def _is_number(value: Any) -> bool:
    text = str(value or "").strip()
    if text in {"-", "—", "–", "--", "－"}:
        return True
    return bool(_NUMBER_RE.fullmatch(text))


def _join_label(parts: Iterable[str]) -> str:
    return "".join(str(part or "").strip() for part in parts)


def _page_lines(doc: Any, page: int) -> list[str]:
    lines = [
        line.strip()
        for line in doc[int(page) - 1].get_text("text").splitlines()
        if line.strip()
    ]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return lines


def _find_page(doc: Any, needles: Sequence[str]) -> int | None:
    for page in range(1, doc.page_count + 1):
        text = doc[page - 1].get_text("text")
        if all(needle in text for needle in needles):
            return page
    return None


def _sync(table: Any, rows: list[list[str]], blocks: list[dict[str, Any]]) -> None:
    table.rows = [list(row) for row in rows]
    table.table_blocks = [
        {"page": int(block["page"]), "rows": [list(row) for row in block["rows"]]}
        for block in blocks
    ]
    table.source_pages = [int(block["page"]) for block in blocks]


# ---------------------------------------------------------------------------
# 偿付能力主表
# ---------------------------------------------------------------------------
def _rebuild_main(table: Any) -> None:
    """把区域裁剪出的 5 列（空首列 + 标签 + 三个期间）规整为 4 列。

    主表两行表头被 PDF 网格压成半句，整页提取又会丢标签列，因此保留
    Profile 的区域定位，只在后处理里修列与表头。
    """
    rows = [[_cell(cell) for cell in row] for row in (table.rows or [])]
    if not rows:
        return
    if rows[0] == _MAIN_HEADER and all(len(row) == 4 for row in rows):
        return
    body = rows[1:]
    if len(body) < 12:
        return
    width = max(len(row) for row in body)
    num_start = None
    for column in range(width):
        sample = [row[column] for row in body if len(row) > column]
        numeric = sum(1 for cell in sample if _is_number(cell) or cell == "-")
        if sample and numeric >= 3 and numeric >= 0.6 * len(sample):
            num_start = column
            break
    if num_start is None or num_start < 1:
        return
    label_index = num_start - 1
    data: list[list[str]] = []
    for row in body:
        label = row[label_index] if len(row) > label_index else ""
        if not label:
            continue
        values = [
            row[index] if len(row) > index else ""
            for index in range(num_start, num_start + 3)
        ]
        data.append([label, *values])
    if len(data) < 12:
        return
    full = [list(_MAIN_HEADER), *data]
    _sync(table, full, [{"page": int(table.page), "rows": [list(row) for row in full]}])
    table.strategy = "专属提取·华泰·主表列/表头规整"


# ---------------------------------------------------------------------------
# 主要经营指标
# ---------------------------------------------------------------------------
def _oper_stop(line: str) -> bool:
    # 注意：“（四）净资产（万元）”“（五）保险合同负债（万元）”是经营表数据行，
    # 不能按节号前缀截断；只有“（四）近三年（综合）投资收益率”才是下一节。
    return (
        line.startswith("备注")
        or line.startswith("四、")
        or line.startswith("五、")
        or ("（四）" in line and "近三年" in line)
        or "近三年（综合）投资收益率" in line
        or "近三年平均投资收益率" in line
    )


def _parse_operating_page(
    lines: Sequence[str], *, has_header: bool
) -> list[list[str]]:
    start = 0
    if has_header:
        index = next(
            (i for i, line in enumerate(lines) if "经营指标" in _cell(line)),
            None,
        )
        if index is None:
            return []
        start = index + 1
    body = list(lines[start:])
    while body and _cell(body[0]) in _OPER_HEADER_TOKENS:
        body.pop(0)

    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    for line in body:
        clean = _cell(line)
        if not clean:
            continue
        if _oper_stop(clean):
            break
        if _OPER_GROUP_RE.fullmatch(clean):
            # 分组标题自身没有数值；其后的 -- 占位会因 label 为空被忽略。
            label, values = [], []
            continue
        if values:
            if _is_number(clean):
                values.append(clean)
                if len(values) == 2:
                    rows.append([_join_label(label), values[0], values[1]])
                    label, values = [], []
            else:
                label.append(clean)
            continue
        if _is_number(clean):
            if label:
                values = [clean]
            continue
        label.append(clean)
    return rows


def _rebuild_operating(doc: Any, table: Any) -> None:
    start = _find_page(doc, ["经营指标", "保险业务收入"])
    if start is None:
        return
    blocks: list[dict[str, Any]] = []
    flat: list[list[str]] = []
    for offset in (0, 1):
        page = start + offset
        if page > doc.page_count:
            break
        rows = _parse_operating_page(
            _page_lines(doc, page), has_header=(offset == 0)
        )
        if not rows:
            continue
        block_rows = [list(_OPER_HEADER), *rows] if offset == 0 else rows
        blocks.append({"page": page, "rows": block_rows})
        flat.extend(block_rows)

    data_rows = [row for row in flat if len(row) > 1 and _is_number(row[1])]
    if len(data_rows) < 25:
        return
    _sync(table, flat, blocks)
    table.unit = "未识别"
    table.strategy = "专属提取·华泰·经营指标跨页文本重建"


# ---------------------------------------------------------------------------
# 流动性：监管指标 + 必测 LCR + 监测指标
# ---------------------------------------------------------------------------
def _parse_regulatory(lines: Sequence[str]) -> list[list[str]]:
    index = next(
        (i for i, line in enumerate(lines) if _cell(line) == "1.监管指标"),
        None,
    )
    if index is None:
        return []
    body = list(lines[index + 1 :])
    while body and _cell(body[0]) in _LIQ_HEADER_TOKENS:
        body.pop(0)

    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    base = ""
    slot = ""
    for line in body:
        clean = _cell(line)
        if not clean:
            continue
        if clean.startswith("LCR") or clean.startswith("2.监测指标"):
            break
        if _is_number(clean):
            if not label:
                continue
            values.append(clean)
            if len(values) == 2:
                text = _join_label(label)
                # 三行年度现金流没有季度期间，丢弃；“过去一季度”行的第二个值
                # 是上上季度，标准库无对应期间，只保留“当季度”行。
                if text.startswith("经营活动现金流回溯不利偏差率") and slot == "当季度":
                    rows.append([text, values[0], values[1]])
                elif "净现金流" not in text and slot != "过去一季度":
                    rows.append([text, values[0], values[1]])
                label, values = [], []
                slot = ""
            continue
        if clean in {"当季度", "过去一季度"} and base:
            slot = clean
            label = [base]
            continue
        if "回溯不利偏差率" in clean:
            base = clean
            label = [clean]
            continue
        if values:
            label.append(clean)
            continue
        label.append(clean)
    return rows


def _parse_lcr(lines: Sequence[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    values: list[str] = []
    base = ""
    term = ""
    for line in lines:
        clean = _cell(line)
        if not clean:
            continue
        if clean.startswith("2.监测指标") or clean.startswith("（三）"):
            break
        match = _LCR_TERM_RE.search(clean)
        if match:
            term = match.group(1)
            continue
        if _is_number(clean):
            if base and term:
                values.append(clean)
                if len(values) == 2:
                    code_match = _LCR_BASE_RE.match(base)
                    code = code_match.group(1) if code_match else base
                    if "自测" not in base:
                        rows.append([code + f"未来{term}个月", values[0], values[1]])
                    values = []
            continue
        if _LCR_BASE_RE.match(clean):
            base = clean
            term = ""
            continue
        # LCR 标签在 2023/2025 会被换行拆开（如“流动性覆”+“盖率）”）。
        if base and not values:
            base += clean
            continue
    return rows


def _parse_monitoring(lines: Sequence[str]) -> list[list[str]]:
    index = next(
        (i for i, line in enumerate(lines) if _cell(line).startswith("2.监测指标")),
        None,
    )
    if index is None:
        return []
    body = list(lines[index + 1 :])
    while body and _cell(body[0]) in _LIQ_HEADER_TOKENS:
        body.pop(0)

    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    token = ""
    for line in body:
        clean = _cell(line)
        if not clean:
            continue
        if clean.startswith("（三）") or clean.startswith("经营指标"):
            break
        if clean in {"指标值", "指标值（元）", "指标值(元)"}:
            token = clean
            continue
        if _is_number(clean):
            if not label:
                continue
            values.append(clean)
            if len(values) == 2:
                text = _join_label(label)
                if "元" in token and "元" not in text:
                    text += "（元）"
                rows.append([text, values[0], values[1]])
                label, values, token = [], [], ""
            continue
        if values:
            label.append(clean)
            continue
        label.append(clean)
    return rows


def _liquidity_blocks(
    doc: Any,
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    reg_page = _find_page(doc, ["监管指标", "本年度累计净现金流"])
    lcr_page = _find_page(doc, ["LCR1", "监测指标"])
    if reg_page is None or lcr_page is None:
        return [], []
    regulatory = _parse_regulatory(_page_lines(doc, reg_page))
    lcr = _parse_lcr(_page_lines(doc, lcr_page))
    monitoring = _parse_monitoring(_page_lines(doc, lcr_page))
    if len(regulatory) != 1 or len(lcr) != 6 or len(monitoring) != 10:
        return [], []
    blocks = [
        {"page": reg_page, "rows": [list(_LIQ_HEADER), *regulatory]},
        {"page": lcr_page, "rows": [list(_LIQ_HEADER), *lcr]},
        {"page": lcr_page, "rows": [list(_LIQ_HEADER), *monitoring]},
    ]
    flat = [
        list(_LIQ_HEADER),
        *[list(row) for row in regulatory],
        *[list(row) for row in lcr],
        *[list(row) for row in monitoring],
    ]
    return blocks, flat


def _rebuild_liquidity(doc: Any, table: Any) -> None:
    blocks, flat = _liquidity_blocks(doc)
    if not blocks:
        return
    _sync(table, flat, blocks)
    table.unit = "未识别"
    table.strategy = "专属提取·华泰·流动性监管/LCR/监测三段重建"


def _append_liquidity(doc: Any, tables: list[Any]) -> None:
    blocks, flat = _liquidity_blocks(doc)
    if not blocks:
        return
    tables.append(
        FlexibleTable(
            table_id="LIQUIDITY_MONITORING",
            table_name="流动性风险指标",
            page=int(blocks[0]["page"]),
            source_pages=[int(block["page"]) for block in blocks],
            rows=[list(row) for row in flat],
            table_blocks=[
                {"page": int(block["page"]), "rows": [list(row) for row in block["rows"]]}
                for block in blocks
            ],
            table_config={
                "table_id": "LIQUIDITY_MONITORING",
                "table_name": "流动性风险指标",
                "canonical_headers": list(_LIQ_HEADER),
            },
            unit="未识别",
            strategy="专属提取·华泰·补建流动性三段",
            quality_score=1.0,
            evidence="通用提取未产出流动性表，由专属提取器按页文本补建",
        )
    )


# ---------------------------------------------------------------------------
# 近三年投资收益率（正文一句话，无表格）
# ---------------------------------------------------------------------------
def _parse_three_year(doc: Any) -> tuple[int | None, list[list[str]]]:
    page = _find_page(doc, ["近三年平均投资收益率"])
    if page is None:
        return None, []
    text = _SPACE_RE.sub("", doc[page - 1].get_text("text"))
    simple = _THREE_YEAR_RE.search(text)
    # 先匹配“综合”，避免与“近三年平均投资收益率”互相干扰。
    comprehensive = re.search(
        r"近三年平均综合投资收益率(?:为)?([\d.]+)%", text
    )
    if simple is None or comprehensive is None:
        return page, []
    rows = [
        list(_THREE_YEAR_HEADER),
        ["近三年平均投资收益率（%）", simple.group(2)],
        ["近三年平均综合投资收益率（%）", comprehensive.group(1)],
    ]
    return page, rows


def _rebuild_three_year(doc: Any, table: Any) -> bool:
    page, rows = _parse_three_year(doc)
    if not rows or page is None:
        return False
    _sync(table, rows, [{"page": page, "rows": [list(row) for row in rows]}])
    table.unit = "%"
    table.strategy = "专属提取·华泰·近三年收益率正文重建"
    return True


def _append_three_year(doc: Any, tables: list[Any]) -> None:
    page, rows = _parse_three_year(doc)
    if not rows or page is None:
        return
    tables.append(
        FlexibleTable(
            table_id="THREE_YEAR_INVESTMENT_RETURN",
            table_name="近三年（综合）投资收益率",
            page=page,
            source_pages=[page],
            rows=[list(row) for row in rows],
            table_blocks=[{"page": page, "rows": [list(row) for row in rows]}],
            table_config={
                "table_id": "THREE_YEAR_INVESTMENT_RETURN",
                "table_name": "近三年（综合）投资收益率",
                "canonical_headers": list(_THREE_YEAR_HEADER),
            },
            unit="%",
            strategy="专属提取·华泰·补建近三年收益率",
            quality_score=1.0,
            evidence="正文披露近三年平均（综合）投资收益率，PDF 无表格；由专属提取器补建",
        )
    )


@register("huatai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """华泰财险专属后处理入口；异常由调用方捕获。"""
    if not tables or not pdf_bytes:
        return tables
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        updated = list(tables)
        seen: set[str] = set()
        drop: list[Any] = []
        for table in updated:
            table_id = str(getattr(table, "table_id", "") or "")
            seen.add(table_id)
            if table_id == "NON_LIFE_OPERATING_METRICS":
                _rebuild_operating(doc, table)
            elif table_id == "SOLVENCY_MAIN":
                _rebuild_main(table)
                table.unit = "万元"
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(doc, table)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                # 2022 全册没有“近三年平均投资收益率”，锚点却会误吸附到经营续页；
                # 无正文可重建时丢弃该表，避免经营行被重复计入近三年指标。
                if not _rebuild_three_year(doc, table):
                    drop.append(table)
            elif table_id in {
                "SOLVENCY_MAIN",
                "ACTUAL_CAPITAL",
                "MINIMUM_CAPITAL",
            }:
                table.unit = "万元"
        for table in drop:
            updated.remove(table)
        if "LIQUIDITY_MONITORING" not in seen:
            _append_liquidity(doc, updated)
        if "THREE_YEAR_INVESTMENT_RETURN" not in seen:
            _append_three_year(doc, updated)
        return updated
