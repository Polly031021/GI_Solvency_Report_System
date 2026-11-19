"""中银保险专属提取后处理。

通用规则（pdfplumber 网格 + 页内区域裁剪）在本公司四份季报上有三类缺口：

1. ``主要经营指标``：页内区域只保留前 13 行（到“1.综合成本率”），
   后续效益类、规模类、签单保费与渠道行全部丢失；
2. ``流动性风险监测指标``：与“（二）监管指标”“（四）近三年投资收益率”
   同页，整页提取按表配置顺序只拿到近三年两行；且监管指标末行
   “上一会计年度之前的会计年度净现金流”的两个数值排在同一文本行；
3. ``最低资本表``：跨页，续页 3.1-3.4 在 PDF 表格里多一列，通用结果
   还混入【第N页表格分块】占位行，列数与首块不一致。

这里按 PDF 文本行重建这三张表：经营表输出 29 个数据行；流动性拆成
“监管指标 10 行（原 11 行，剔除回溯偏差率的上季度复述行）”“监测指标
10 行”两个 block，各自带表头；最低资本表按行次+项目+期末数+期初数
三列输出，首块带表头、续块继承。rows 与
table_blocks 同步，单位沿用表内“单位：元/单位：元，%”标注。
解析不依赖年份和固定页码，只认文本锚点；结构校验不通过时保持通用
结果不动，由调用方统一记录，不在这里捕获异常。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_PAGE_NO_RE = re.compile(r"^\d{1,3}\s*$")
_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_NUM_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_DASH_RE = re.compile(r"^(?:--|—|–|-|——)$")
_VALUE_PATTERN = r"(?:-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?|--|—|–|-|——)"
_TWO_VALUES_RE = re.compile(rf"^({_VALUE_PATTERN})\s+({_VALUE_PATTERN})$")
_LCR_LABEL_RE = re.compile(r"^LCR\s*([123])\s*[（(]?\s*未来\s*(3|12)\s*个月")
_HEADER_WORDS = {
    "指标名称", "项目", "行次", "监管指标", "监测指标",
    "本季度数", "上季度数", "本年度累计数", "本年累计数",
    "本季度末数", "上季度末数", "期末数", "期初数",
}
_STOP_PREFIXES = ("S01-", "S02-", "S03-", "S04-", "S05-", "公司分类")
_OP_HEADER = ("指标名称", "本季度数", "本年度累计数")
_LIQ_HEADER = ("指标名称", "本季度数", "上季度数")
_MIN_HEADER = ("指标名称", "期末数", "期初数")
_OPER_GROUP_LABELS = {"主要经营指标", "效益类指标", "规模类指标"}
_PREFIX_GROUP_RE = re.compile(r"^[（(][一二三四五六七八九十]+[）)]|^[一二三四五六七八九十]+、")
_PREFIX_NUM_RE = re.compile(r"^\d+(?:\.\d+)*[.．、]?\s*")


def _page_lines(doc: Any, page_number: int) -> list[str]:
    """取一页文本行：压空行、去掉页首印刷页码。"""
    if page_number < 1 or page_number > len(doc):
        return []
    raw = doc.load_page(page_number - 1).get_text() or ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    # 印刷页码可能从一位数开始；正文表格首行行次只有 1~5，
    # 所以只把“两位及以上”的首行当页码剥掉，避免误删行次 1。
    while lines and _PAGE_NO_RE.match(lines[0]) and len(lines[0].strip()) >= 2:
        lines.pop(0)
    return lines


def _clean_label(text: str) -> str:
    """表格标签里常有换行/排版空格（如“差 额”“AA 级”），统一压掉。"""
    return re.sub(r"\s+", "", str(text or ""))


def _metric_label(text: str) -> str:
    """经营/流动性标签：去掉“（一）”“1.”“3.1 ”“一、”等结构性前缀。"""
    text = _clean_label(text)
    text = _PREFIX_GROUP_RE.sub("", text)
    text = _PREFIX_NUM_RE.sub("", text)
    return text.strip()


def _is_value(line: str) -> bool:
    return bool(_NUM_RE.match(line) or _DASH_RE.match(line))


def _value_text(line: str) -> str:
    return "--" if _DASH_RE.match(line) else line.replace(" ", "")


def _value_tokens(line: str) -> list[str]:
    """返回一行里的数值 token。

    正常情况下一个数值占一行；监管指标末行会把“本季度/上季度”两个
    数值印在同一行（如 ``-150,388,945.07 -150,388,945.07``），这里拆开。
    """
    if _is_value(line):
        return [_value_text(line)]
    match = _TWO_VALUES_RE.match(line)
    if match:
        return [_value_text(match.group(1)), _value_text(match.group(2))]
    return []


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
        if line.startswith(("单位：", "公司名称", "注：", "编制单位", "有关指标详见")):
            label_parts = []
            values = []
            continue
        tokens = _value_tokens(line)
        if tokens:
            if not label_parts:
                continue
            values.extend(tokens)
            if len(values) >= 2:
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
    """有行次表（最低资本）：行次行开始，项目行 + 两个数值行。"""
    rows: list[list[str]] = []
    rowno = ""
    label_parts: list[str] = []
    values: list[str] = []
    for line in lines:
        if line in _HEADER_WORDS:
            continue
        if line.startswith(("单位：", "公司名称", "注：", "编制单位")):
            continue
        if not label_parts and not values and _ROW_NO_RE.match(line):
            rowno = line
            continue
        if rowno and label_parts and _value_tokens(line):
            values.extend(_value_tokens(line))
            if len(values) < 2:
                continue
            label = _clean_label("".join(label_parts))
            if label:
                rows.append([label, values[0], values[1]])
            rowno, label_parts, values = "", [], []
            continue
        if rowno:
            label_parts.append(line)
    return rows


def _drop_group_and_dup(
    rows: list[list[str]], group_labels: set[str] | None = None
) -> list[list[str]]:
    output: list[list[str]] = []
    for row in rows:
        label = str(row[0]).strip()
        if not label:
            continue
        if row[1] == "--" and row[2] == "--":
            # 经营表的分组标题两列都是 --；但“基本每股收益”这类
            # 真实数据行也可能两列都是 --，只在指定分组名时删除。
            if group_labels is None or label in group_labels:
                continue
        output.append([label, row[1], row[2]])
    return output


def _table_pages(table: Any) -> list[int]:
    pages = [int(p) for p in (getattr(table, "source_pages", None) or []) if p]
    if not pages:
        page = getattr(table, "page", None)
        pages = [int(page)] if page else []
    return sorted({p for p in pages if p > 0})


def _locate_page(doc: Any, needles: Sequence[str], preferred: Sequence[int] = ()) -> int:
    """优先用定位结果的页，否则全册按文本锚点找。"""
    for page in list(preferred) + list(range(1, len(doc) + 1)):
        if page < 1 or page > len(doc):
            continue
        text = doc.load_page(page - 1).get_text() or ""
        if all(needle in text for needle in needles):
            return page
    return 0


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


def _rebuild_operating(doc: Any, table: Any) -> None:
    preferred = _table_pages(table)
    page = _locate_page(doc, ("财产保险公司主要经营指标", "指标名称"), preferred)
    if not page:
        return
    lines = _page_lines(doc, page)
    body = _lines_after(lines, "指标名称")
    body = [line for line in body if not line.startswith(_STOP_PREFIXES)]
    rows = _parse_metric_rows(body)
    cleaned: list[list[str]] = []
    for row in rows:
        label = _metric_label(str(row[0]))
        if label:
            cleaned.append([label, row[1], row[2]])
    rows = _drop_group_and_dup(cleaned, _OPER_GROUP_LABELS)
    if len(rows) < 29:
        return
    labels = [row[0] for row in rows]
    if labels[0] != "保险业务收入":
        return
    if labels[-1] != "其他渠道签单保费":
        return
    if not any("综合成本率" in label for label in labels):
        return
    if not any("签单保费" in label for label in labels):
        return
    _set_blocks(table, [(page, [[*_OP_HEADER], *rows])])
    _ensure_metadata(table, "元")


def _canonical_lcr_label(row: list[str]) -> list[str]:
    """把 LCR 长标签收敛成 taxonomy 的标准短标签。"""
    match = _LCR_LABEL_RE.match(str(row[0]).strip())
    if not match:
        return row
    return [f"LCR{match.group(1)}未来{match.group(2)}个月", row[1], row[2]]


def _rebuild_liquidity(doc: Any, table: Any) -> None:
    preferred = _table_pages(table)
    page = _locate_page(doc, ("流动性风险监测指标", "监测指标"), preferred)
    if not page:
        return
    lines = _page_lines(doc, page)

    regulatory = _lines_after(lines, "监管指标")
    regulatory = _take_until(regulatory, ("（三）", "(三)"))
    monitoring = _lines_after(lines, "监测指标")
    monitoring = _take_until(monitoring, ("（四）", "(四)", "近三年"))

    reg_rows = [
        _canonical_lcr_label(row)
        for row in _drop_group_and_dup(_parse_metric_rows(regulatory))
    ]
    mon_rows = _drop_group_and_dup(_parse_metric_rows(monitoring))
    if len(reg_rows) != 11 or not str(reg_rows[0][0]).startswith("LCR1"):
        return
    if len(mon_rows) != 10 or not any("经营活动净现金流" in row[0] for row in mon_rows):
        return
    # 监管表的列已经是“本季度数/上季度数”；回溯偏差率的两行分别
    # 复述了这两个期间（本季度行再带一个上季度值），四值压成两期间
    # 会互相覆盖，这里只保留“（本季度）”那一行。
    reg_rows = [
        row
        for row in reg_rows
        if not ("回溯不利偏差率" in row[0] and "上季度" in row[0])
    ]
    if len(reg_rows) < 10:
        return

    rows = [[*_LIQ_HEADER], *reg_rows, [*_LIQ_HEADER], *mon_rows]
    table.rows = rows
    table.table_blocks = [
        {"page": int(page), "rows": [[*_LIQ_HEADER], *reg_rows]},
        {"page": int(page), "rows": [[*_LIQ_HEADER], *mon_rows]},
    ]
    _ensure_metadata(table, "元")


def _take_until(lines: list[str], stop_prefixes: Sequence[str]) -> list[str]:
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in stop_prefixes):
            break
        output.append(line)
    return output


def _rebuild_minimum(doc: Any, table: Any) -> None:
    preferred = _table_pages(table)
    first = _locate_page(doc, ("S05-最低资本表", "行次"), preferred)
    if not first:
        return
    pages = [first]
    # 续页正文以 3.1 起；优先沿用定位结果里的页，避免把下一张 S06 当续页。
    for candidate in preferred:
        if candidate > first and candidate not in pages:
            pages.append(candidate)
    if len(pages) == 1 and first + 1 <= len(doc):
        pages.append(first + 1)

    chunks: list[tuple[int, list[list[str]]]] = []
    for index, page in enumerate(sorted(pages)):
        lines = _page_lines(doc, page)
        if index == 0:
            lines = _lines_after(lines, "S05-最低资本表")
        if not lines:
            continue
        rows = _parse_numbered_rows(lines)
        if rows:
            chunks.append((page, rows))

    rows = [row for _, chunk in chunks for row in chunk]
    # 末页不是“4 最低资本”时，按物理顺序再补一页（应对定位结果漏续页）。
    if (not rows or rows[-1][0] != "最低资本") and pages:
        extra = max(pages) + 1
        if extra <= len(doc):
            extra_rows = _parse_numbered_rows(_page_lines(doc, extra))
            if extra_rows:
                chunks.append((extra, extra_rows))
                rows = [row for _, chunk in chunks for row in chunk]
    if len(rows) < 33 or rows[-1][0] != "最低资本":
        return
    if not any("量化风险最低资本" in row[0] for row in rows):
        return
    if not any("附加资本" in row[0] for row in rows):
        return

    blocks: list[tuple[int, list[list[str]]]] = []
    for index, (page, chunk) in enumerate(chunks):
        block = ([[*_MIN_HEADER]] if index == 0 else []) + chunk
        blocks.append((page, block))
    _set_blocks(table, blocks)
    _ensure_metadata(table, "元")


@register("zhongyin")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """中银保险专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "NON_LIFE_OPERATING_METRICS":
                _rebuild_operating(doc, table)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(doc, table)
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_minimum(doc, table)
    finally:
        doc.close()
    return tables
