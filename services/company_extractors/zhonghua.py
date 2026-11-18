"""中华联合专属提取后处理。

通用规则（pdfplumber 网格 + 页内区域裁剪）在本公司四份季报上存在四类缺口：

1. ``主要经营指标``：区域定位只保留前 12-13 行，2025 年签单保费渠道行续到下一页；
2. ``流动性风险监测指标``：与“（四）近三年投资收益率”同页，选 frame 时错拿近三年两行；
3. ``实际资本表``：4/4.1/4.2/4.3/5 合计行在次页，通用续页识别失败（blocks=1）；
4. ``最低资本表``：附加资本 3.x 行多一列，且混入【第N页表格分块】占位行。

这里统一按 PDF 文本行重建这四张表：保留行次/项目/期末数/期初数四列结构，
rows 与 table_blocks 同步，单位沿用表内“（万元）”“单位：元”标注。
任何解析异常都由调用方兜底，不阻断通用流程。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_PAGE_NO_RE = re.compile(r"^\d{1,3}\s*$")
_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_NUM_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_DASH_RE = re.compile(r"^(?:--|—|–|-|——)$")
_HEADER_WORDS = {
    "指标名称", "项目", "行次",
    "本季度数", "上季度数", "本年度累计数", "本年累计数",
    "本季度末数", "上季度末数", "期末数", "期初数",
}
_STOP_PREFIXES = ("S01-", "S02-", "S03-", "S04-", "S05-", "公司分类")
_OP_HEADER = ("指标名称", "本季度数", "本年度累计数")
_LIQ_HEADER = ("指标名称", "本季度数", "上季度数")
_CAP_HEADER = ("行次", "项目", "期末数", "期初数")
_PREFIX_GROUP_RE = re.compile(r"^[（(][一二三四五六七八九十]+[）)]")
_PREFIX_NUM_RE = re.compile(r"^\d+(?:\.\d+)*[.．、]?\s*")
_CJK_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_CJK_PUNCT_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[（）()、，,])|(?<=[（）()、，,])\s+(?=[\u4e00-\u9fff])")


def _page_lines(doc: Any, page_number: int) -> list[str]:
    """取一页文本行：压空行、去掉页首页码行。"""
    if page_number < 1 or page_number > len(doc):
        return []
    raw = doc.load_page(page_number - 1).get_text() or ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    # 页眉页码一般为两位以上（本册正文页码=物理页-2，如第 27 页印“25”）；
    # 表格首行行次可能是 1~5，不能当页码剥掉。
    while lines and _PAGE_NO_RE.match(lines[0]) and len(lines[0].strip()) >= 2:
        lines.pop(0)
    return lines


def _clean_label(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = _CJK_SPACE_RE.sub("", text)
    text = _CJK_PUNCT_SPACE_RE.sub("", text)
    return text.strip()


def _metric_label(text: str) -> str:
    """经营表标签：去掉“（一）”“1.”“3.1 ”等结构性前缀。"""
    text = _PREFIX_GROUP_RE.sub("", text)
    text = _PREFIX_NUM_RE.sub("", text)
    return text.strip()


def _is_value(line: str) -> bool:
    return bool(_NUM_RE.match(line) or _DASH_RE.match(line))


def _value_text(line: str) -> str:
    return "--" if _DASH_RE.match(line) else line.replace(" ", "")


def _lines_after(lines: list[str], marker: str) -> list[str]:
    for index, line in enumerate(lines):
        if marker in line:
            return lines[index + 1:]
    return []


def _parse_metric_rows(lines: list[str]) -> list[list[str]]:
    """无行次表（经营/流动性）：标签行 + 两个数值行。"""
    rows: list[list[str]] = []
    label_parts: list[str] = []
    values: list[str] = []
    for line in lines:
        if line in _HEADER_WORDS:
            continue
        if line.startswith(("单位：", "公司名称", "注：", "编制单位")):
            label_parts = []
            values = []
            continue
        if _is_value(line):
            if not label_parts:
                continue
            values.append(_value_text(line))
            if len(values) == 2:
                rows.append(["".join(label_parts), values[0], values[1]])
                label_parts = []
                values = []
            continue
        if values:
            values = []
            label_parts = []
        label_parts.append(line)
    return rows


def _parse_numbered_rows(lines: list[str]) -> list[list[str]]:
    """有行次表（实际资本/最低资本）：行次行开始，标签行 + 两个数值行。

    注意 "0.00" 这类数值也能匹配行次正则，必须先按“当前行是否已有标签”
    区分：无标签时的数字串才是行次，有标签时的数字串是数值。
    """
    rows: list[list[str]] = []
    rowno = ""
    label_parts: list[str] = []
    values: list[str] = []
    for line in lines:
        if line in _HEADER_WORDS:
            continue
        if not label_parts and not values and _ROW_NO_RE.match(line):
            rowno = line
            continue
        if rowno and label_parts and _is_value(line):
            values.append(_value_text(line))
            if len(values) < 2:
                continue
            label = _clean_label("".join(label_parts))
            if label:
                rows.append([rowno, label, values[0], values[1]])
            rowno, label_parts, values = "", [], []
            continue
        if rowno:
            label_parts.append(line)
    return rows


def _table_pages(table: Any) -> list[int]:
    pages = [int(p) for p in (getattr(table, "source_pages", None) or []) if p]
    if not pages:
        page = getattr(table, "page", None)
        pages = [int(page)] if page else []
    return sorted({p for p in pages if p > 0})


def _set_blocks(table: Any, block_rows: list[tuple[int, list[list[str]]]]) -> list[list[str]]:
    rows = [row for _, block in block_rows for row in block]
    table.rows = rows
    table.table_blocks = [
        {"page": int(page), "rows": [list(row) for row in block]}
        for page, block in block_rows
    ]
    return rows


def _ensure_metadata(table: Any, unit: str = "") -> None:
    if unit and str(getattr(table, "unit", "") or "") in {"", "未识别"}:
        table.unit = unit


def _drop_group_and_dup(rows: list[list[str]]) -> list[list[str]]:
    output: list[list[str]] = []
    for row in rows:
        label = str(row[0]).strip()
        if not label:
            continue
        if row[1] == "--" and row[2] == "--":  # 分组标题行（--、--）
            continue
        output.append(row)
    return output


def _rebuild_operating(doc: Any, table: Any) -> None:
    pages = _table_pages(table)

    def parse(page: int, first: bool = False) -> list[list[str]]:
        lines = _page_lines(doc, page)
        body = _lines_after(lines, "指标名称") if first else lines
        body = [line for line in body if not line.startswith(_STOP_PREFIXES)]
        rows = _parse_metric_rows(body)
        cleaned = []
        for row in rows:
            label = _metric_label(str(row[0]))
            if label:
                cleaned.append([label, row[1], row[2]])
        return _drop_group_and_dup(cleaned)

    chunks: list[tuple[int, list[list[str]]]] = []
    for index, page in enumerate(pages):
        rows = parse(page, first=index == 0)
        if rows:
            chunks.append((page, rows))
    all_rows = [row for _, rows in chunks for row in rows]
    if all_rows and all_rows[-1][0] != "其他渠道签单保费" and chunks:
        extra = chunks[-1][0] + 1
        if extra <= len(doc):
            rows = parse(extra)
            if rows:
                chunks.append((extra, rows))
                all_rows = [row for _, rows_in in chunks for row in rows_in]

    if len(all_rows) < 29:
        return
    labels = [row[0] for row in all_rows]
    if not any("签单保费" in label for label in labels):
        return
    if not any("综合成本率" in label for label in labels):
        return

    blocks: list[tuple[int, list[list[str]]]] = []
    for index, (page, rows) in enumerate(chunks):
        block = ([list(_OP_HEADER)] if index == 0 else []) + rows
        blocks.append((page, block))
    _set_blocks(table, blocks)
    _ensure_metadata(table, "元")


def _rebuild_liquidity(doc: Any, table: Any) -> None:
    pages = _table_pages(table)
    if not pages:
        return
    page = pages[0]
    body = _lines_after(_page_lines(doc, page), "流动性风险监测指标")
    trimmed: list[str] = []
    for line in body:
        if line.startswith(("（四）", "(四)")) or line.startswith(_STOP_PREFIXES):
            break
        trimmed.append(line)
    rows = _parse_metric_rows(trimmed)
    rows = _drop_group_and_dup(rows)
    # taxonomy 只有“经营活动净现金流（万元）”，没有“百元保费经营活动净现金流”。
    # 后者若保留原名，会被归一化模糊匹配到同一编码并在去重时顶掉真值，
    # 这里改用同义标签，确保它只作为 RAW 行保留。
    rows = [["百元保费现金净流量" if str(row[0]).strip() == "百元保费经营活动净现金流" else row[0],
             row[1], row[2]] for row in rows]
    if len(rows) != 10 or not any("经营活动净现金流" in str(row[0]) for row in rows):
        return
    _set_blocks(table, [(page, [list(_LIQ_HEADER), *rows])])
    _ensure_metadata(table, "万元")


def _rebuild_capital(doc: Any, table: Any, minimum: bool) -> None:
    pages = _table_pages(table)
    if not pages:
        return
    marker = "最低资本表" if minimum else "实际资本表"
    terminal = "最低资本" if minimum else "实际资本合计"

    def parse_pages(pages_in: list[int]) -> list[tuple[int, list[list[str]]]]:
        chunks: list[tuple[int, list[list[str]]]] = []
        for index, page in enumerate(pages_in):
            lines = _page_lines(doc, page)
            if index == 0:
                lines = _lines_after(lines, marker)
            if not lines:
                continue
            rows = _parse_numbered_rows(lines)
            if rows:
                chunks.append((page, rows))
        return chunks

    chunks = parse_pages(pages)
    rows = [row for _, chunk in chunks for row in chunk]
    if not rows or rows[-1][1] != terminal:
        extra = pages[-1] + 1
        if extra <= len(doc):
            chunks = parse_pages(pages + [extra])
            rows = [row for _, chunk in chunks for row in chunk]
    if not rows or rows[-1][1] != terminal:
        return
    if minimum:
        if len(rows) < 33 or rows[-1][0] != "4":
            return
    else:
        if len(rows) < 30:
            return
        chunks = [(page, _canonical_actual_labels(chunk)) for page, chunk in chunks]
        rows = [row for _, chunk in chunks for row in chunk]

    blocks: list[tuple[int, list[list[str]]]] = []
    for index, (page, chunk) in enumerate(chunks):
        block = ([list(_CAP_HEADER)] if index == 0 else []) + chunk
        blocks.append((page, block))
    if not blocks:
        return
    _set_blocks(table, blocks)
    _ensure_metadata(table, "元")


def _canonical_actual_labels(rows: list[list[str]]) -> list[list[str]]:
    """实际资本表里 2.4/3.4/3.8/4.3 四行按规范名输出。

    归一化器会剥掉前导行次，导致“2.4 减：超限额应扣除的部分”与
    “3.8 …”“4.3 …”共用同一别名而无法区分；改用各编码的规范名称后
    可唯一命中（1.2.4 递延所得税与附属一级资本下的同名项仍歧义，保持原文）。
    """
    canonical = {
        "2.4": "核心二级资本超限额应扣除部分",
        "3.8": "附属一级资本超限额应扣除部分",
        "4.3": "附属二级资本超限额应扣除部分",
    }
    output: list[list[str]] = []
    for row in rows:
        rowno = str(row[0]).strip()
        label = str(row[1]).strip()
        if rowno in canonical and "超限额" in label:
            row = [rowno, canonical[rowno], row[2], row[3]]
        elif rowno == "3.4" and label.startswith("递延所得税资产"):
            row = [rowno, "附属一级资本-" + label, row[2], row[3]]
        output.append(row)
    return output


@register("zhonghua")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """中华联合专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "NON_LIFE_OPERATING_METRICS":
                _rebuild_operating(doc, table)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(doc, table)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_capital(doc, table, minimum=False)
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_capital(doc, table, minimum=True)
    finally:
        doc.close()
    return tables
