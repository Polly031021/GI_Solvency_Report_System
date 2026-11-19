"""瑞再企商（Swiss Re Corporate Solutions）专属提取后处理（2022-2025 版式）。

四份报告的通用规则缺口同构：

1. ``主要经营指标``：pdfplumber 只识别到“1.综合成本率”为止，且把首行分组标题
   “六、主要经营指标”当成表头，导致本季度/本年度累计两列丢失、效益类和规模类
   （费用率、赔付率、签单保费、前五大险种与四类渠道保费）整段缺失。PDF 文本里
   每个逻辑行固定占三行（标签 / 本季度值 / 本年度累计值，空值也保留空行），
   这里按三行一组重建为“指标名称 + 本季度数 + 本年度累计数”，并跳过三个
   分组标题（“X、主要经营指标”“（十一）效益类指标”“（十二）规模类指标”）。
2. ``偿付能力充足率指标``：末行“偿付能力充足率”是标准名“综合偿付能力充足率”
   的简写，通用匹配只能落到 RAW 编码，这里改写标签。
3. ``S02 实际资本明细表 / S05 最低资本表``：续页表块首列多出“行次”
   （``4``、``4.1``…），与 3 列表头错列，并夹着“【第N页表格分块】”伪行；
   统一去伪行、去行次列。S05 在“4 最低资本”之后还接保险风险子类/巨灾风险
   子表（均非标准编码），需在该行截断，后续块整块丢弃。
4. ``流动性风险监管指标``：监管表 5 列、LCR 行首列为空，标准化直接跳过；
   监测表 34 行里只有每节“指标值”行对应标准指标，组件明细不进入标准表。
   这里把监管表重建为 8 行（净现金流 / 回溯偏差率 / 必测情景 3 档 × 2 个
   期间），监测表重建为“指标名称 + 本季度数”的 10 行。LCR2/LCR3 的自测
   压力情景与必测情景共用同一编码，保留会造成同码不同值，故丢弃。

约定：只改 ``rows`` 与 ``table_blocks``（两者必须同步，STEP3 按块展开）；
保持幂等；任何异常由调用方兜底，不在此吞掉。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]
_MONITOR_PREFIX_RE = re.compile(r"^[一二三四五六七八九十]+、\s*")
_MONITOR_HEADER = ["指标名称", "本季度数"]

_OPERATING_ANCHOR_RE = re.compile(r"^[一二三四五六七八九十]+、主要经营指标$")
_GROUP_TITLE_RE = re.compile(r"^[（(]?[一二三四五六七八九十]+[）)、]?.*类指标$")
_ACTUAL_TOTAL_LABELS = frozenset({"核心一级资本", "实际资本合计"})
_ROW_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*$")
_LCR_TAG_RE = re.compile(r"LCR\s*([123])")
_LCR_WINDOW_RE = re.compile(r"^未来\s*(\d{1,2})\s*个月$")
_VALUE_RE = re.compile(r"-?\d[\d,，]*(?:\.\d+)?%?")

_STOP_LABELS = ("公司名称", "单位：", "单位:", "指标名称", "S01", "S02", "S05")


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _doc_texts(pdf_bytes: bytes) -> list[str]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [doc.load_page(index).get_text() or "" for index in range(len(doc))]
    finally:
        doc.close()


def _raw_lines(texts: Sequence[str], page: int) -> list[str]:
    """保留空行：经营表每行固定占三行，空值行是占位符，不能过滤。"""
    try:
        text = texts[int(page) - 1]
    except (IndexError, TypeError, ValueError):
        return []
    return [line.strip() for line in str(text).splitlines()]


def _sync(table: Any, rows: list[list[str]], page: int) -> None:
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


def _sync_blocks(table: Any, blocks: list[dict[str, Any]]) -> None:
    table.table_blocks = blocks
    table.rows = [list(row) for block in blocks for row in block["rows"]]


def _block_rows(block: Any) -> list[list[str]]:
    if isinstance(block, dict):
        rows = block.get("rows") or []
    else:
        rows = block or []
    return [list(row) for row in rows]


def _block_page(block: Any, default: int) -> int:
    if isinstance(block, dict):
        try:
            return int(block.get("page") or default)
        except (TypeError, ValueError):
            return default
    return default


def _tail_values(cells: Sequence[str], count: int = 2) -> list[str]:
    """取行内最后 count 个数值单元格；LCR/净现金流行的数值总在行末。"""
    values = [
        _clean(cell) for cell in cells if _VALUE_RE.fullmatch(_clean(cell))
    ]
    values = values[-count:]
    while len(values) < count:
        values.insert(0, "")
    return values


def _is_group_title(label: str) -> bool:
    if not label:
        return False
    if "主要经营指标" in label:
        return True
    return bool(_GROUP_TITLE_RE.match(label))


def _parse_operating(lines: Sequence[str]) -> list[list[str]]:
    start = None
    for index, line in enumerate(lines):
        if _OPERATING_ANCHOR_RE.match(_clean(line)):
            start = index
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    index = start + 3  # 跳过标题行及其 “--/--” 占位行
    while index + 2 < len(lines):
        label = _clean(lines[index])
        if not label:
            break
        if label.startswith(_STOP_LABELS) or re.fullmatch(r"\d{1,3}", label):
            break
        if _is_group_title(label):
            index += 3
            continue
        rows.append([label, _clean(lines[index + 1]), _clean(lines[index + 2])])
        index += 3
    return rows


def _rebuild_operating(table: Any, texts: Sequence[str]) -> None:
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).strip().isdigit()
    ] or [int(getattr(table, "page", 1) or 1)]
    for page in pages:
        rows = _parse_operating(_raw_lines(texts, page))
        if rows:
            _sync(table, [_OPERATING_HEADER, *rows], page)
            return


def _rename_combined_ratio(table: Any) -> None:
    """“偿付能力充足率”（末行）实为“综合偿付能力充足率”，精确名替换。"""
    def _fixed(rows: Sequence[Sequence[Any]]) -> list[list[str]]:
        output: list[list[str]] = []
        for row in rows:
            cells = [str(cell) for cell in row]
            if cells and _clean(cells[0]) == "偿付能力充足率":
                cells[0] = "综合偿付能力充足率"
            output.append(cells)
        return output

    table.rows = _fixed(list(getattr(table, "rows", None) or []))
    blocks = list(getattr(table, "table_blocks", None) or [])
    for block in blocks:
        if isinstance(block, dict):
            block["rows"] = _fixed(_block_rows(block))
    table.table_blocks = [
        {
            "page": _block_page(block, int(getattr(table, "page", 1) or 1)),
            "rows": _fixed(_block_rows(block)),
        }
        for block in blocks
    ]


def _is_lowest_capital_row(cells: Sequence[str]) -> bool:
    return bool(cells) and _clean(cells[0]) == "最低资本"


def _clean_capital(table: Any, table_id: str) -> None:
    default_page = int(getattr(table, "page", 1) or 1)
    raw_blocks = list(getattr(table, "table_blocks", None) or [])
    if not raw_blocks:
        raw_blocks = [{"page": default_page, "rows": list(getattr(table, "rows", None) or [])}]

    cleaned: list[dict[str, Any]] = []
    truncated = False
    for block in raw_blocks:
        if truncated:
            break
        page = _block_page(block, default_page)
        output: list[list[str]] = []
        for row in _block_rows(block):
            cells = [_clean(cell) for cell in row]
            if not any(cells):
                continue
            if any("表格分块" in cell for cell in cells):
                continue
            if len(cells) >= 4 and _ROW_NUMBER_RE.fullmatch(cells[0]):
                cells = cells[1:]
            if not any(cells):
                continue
            if table_id == "ACTUAL_CAPITAL" and cells[0] in _ACTUAL_TOTAL_LABELS:
                # “核心一级资本/实际资本合计”是 S02 表内合计行，与主表
                # 同码同期；2023 年 S02 该两块合计比主表低 183.52 万元，
                # 主表口径才与 332.0% 的充足率自洽，故合计行统一以主表为准。
                continue
            output.append(cells)
            if table_id == "MINIMUM_CAPITAL" and _is_lowest_capital_row(cells):
                truncated = True
                break
        if output:
            cleaned.append({"page": page, "rows": output})

    if cleaned:
        _sync_blocks(table, cleaned)


def _rebuild_regulatory(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    output: list[list[str]] = [_LIQUIDITY_HEADER]
    tag = ""
    scenario = ""
    for row in rows:
        cells = [_clean(cell) for cell in row]
        if not any(cells):
            continue
        flat = "".join(cells)
        tag_cell = next((cell for cell in cells if _LCR_TAG_RE.search(cell)), "")
        if tag_cell:
            match = _LCR_TAG_RE.search(tag_cell)
            tag = match.group(1) if match else tag
            scenario = tag_cell

        if "净现金流" in cells[0].replace(" ", "") and "本年度累计" in flat:
            output.append(["净现金流", *_tail_values(cells)])
            continue
        if "上一会计年度" in flat:
            continue
        if "回溯不利偏差率" in flat:
            output.append(
                ["经营活动净现金流回溯不利偏差率", *_tail_values(cells)]
            )
            continue

        window_cell = next(
            (cell for cell in cells if _LCR_WINDOW_RE.match(cell)), ""
        )
        if window_cell and tag:
            if "自测" in scenario:
                continue
            window = _LCR_WINDOW_RE.match(window_cell)
            output.append(
                [f"LCR{tag}未来{window.group(1)}个月", *_tail_values(cells)]
            )
    return output


def _rebuild_monitor(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    output: list[list[str]] = [_MONITOR_HEADER]
    for row in rows:
        cells = [_clean(cell) for cell in row]
        if any("表格分块" in cell for cell in cells):
            continue
        if "指标值" not in cells:
            continue
        label = _MONITOR_PREFIX_RE.sub("", cells[0])
        label = re.sub(r"\s+", "", label)
        if not label:
            continue
        index = cells.index("指标值")
        value = next((cell for cell in cells[index + 1 :] if cell), "")
        output.append([label, value])
    return output


def _rebuild_liquidity(table: Any) -> None:
    default_page = int(getattr(table, "page", 1) or 1)
    blocks = list(getattr(table, "table_blocks", None) or [])
    if not blocks:
        return
    regulatory = _rebuild_regulatory(_block_rows(blocks[0]))
    outputs: list[dict[str, Any]] = [
        {"page": _block_page(blocks[0], default_page), "rows": regulatory}
    ]
    if len(blocks) > 1:
        monitor = _rebuild_monitor(_block_rows(blocks[1]))
        if len(monitor) > 1:
            outputs.append(
                {"page": _block_page(blocks[1], default_page), "rows": monitor}
            )
    if len(outputs[0]["rows"]) > 1:
        _sync_blocks(table, outputs)


@register("ruizai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """瑞再企商专属后处理入口；异常由调用方捕获并保留通用结果。"""
    needs_text = any(
        str(getattr(table, "table_id", "") or "")
        in {"NON_LIFE_OPERATING_METRICS", "LIQUIDITY_MONITORING"}
        for table in tables
    )
    texts = _doc_texts(pdf_bytes) if needs_text else []
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id == "SOLVENCY_MAIN":
            _rename_combined_ratio(table)
        elif table_id == "NON_LIFE_OPERATING_METRICS":
            _rebuild_operating(table, texts)
        elif table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}:
            _clean_capital(table, table_id)
        elif table_id == "LIQUIDITY_MONITORING":
            _rebuild_liquidity(table)
    return tables
