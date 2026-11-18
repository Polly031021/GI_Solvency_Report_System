"""大地财产专属提取后处理（2022-2025 版式）。

大地四年版式一致，但通用“线框 + 页内区域”提取在下列几处会丢行/丢值，
这里统一按 PDF 正文行重建（fitz 的 get_text 对大地是“每个单元格一行”，
行次行 → 标签行（可多行换行）→ 期末/本季度值行 → 期初/上季度值行）：

1. ``SOLVENCY_MAIN``：表头被竖排拆散，pdfplumber 只留“下季度预测数”一个
   单元格；这里补回 4 列标准表头，并按正文重排 15 行。
2. ``NON_LIFE_OPERATING_METRICS``：通用规则只截到“综合成本率”。2022 年
   经营指标拆成表3.4/3.5/3.6 三张表、2023-2025 合并成一张；这里按页内
   所有“指标名称 + 本年度累计数”段落重建，兼容两种版式，并丢弃分组标题。
3. ``ACTUAL_CAPITAL``：表9.1（指标/期末数/期初数）与表9.2（行次/项目/期末数/
   期初数）分两页，通用规则拼成一块后换行标签（投资性房地产、递延所得税
   资产等）所在行丢值；这里用表9.2 全量重建，3.x 节的递延所得税行补
   “附属一级资本-”前缀以区分 1.2.4 的递延所得税资产，另取表9.1 的
   报表/认可口径 5 行（表9.2 没有这些科目）。
4. ``MINIMUM_CAPITAL``：通用规则的区域终止词把表截成 3 行；这里按
   “行次 + 项目 + 期末数 + 期初数”整表重建（2022 没有 1* 行，自然兼容）。
5. ``LIQUIDITY_MONITORING``：2023-2025 的“AA 级（含）以下境内固定收益类
   资产占比”标签被拆成两行、数值因此丢失；这里合并换行标签后重排。
6. ``THREE_YEAR_INVESTMENT_RETURN``：大地没有线框表，只有“（五）”小节
   两行正文；这里解析成“项目/数值”两行，避免同页经营表被误当成本表。

只改 ``table.rows`` 与 ``table.table_blocks``（两者同步），不改 table_id /
pages；全部按正文标签定位，不写死年份与页码；重复执行结果相同。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Sequence

from . import register

_VALUE_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*(?:\*)?$")
_GROUP_NO_RE = re.compile(r"^[（(][一二三四五六七八九十百]+[）)]$")
_GROUP_TITLE_RE = re.compile(r"^[（(][一二三四五六七八九十百]+[）)].*类指标")
_HEADING_RE = re.compile(r"^[（(][一二三四五六七八九十百]+[）)]\s*\S")
_SECTION_RE = re.compile(r"^[一二三四五六七八九十百]+、")
_TABLE_TAG_RE = re.compile(r"^表\s*\d+")
_PLACEHOLDERS = frozenset({"-", "--", "—", "–", "－"})

_MAIN_HEADER = ["项目", "本季度数", "上季度数", "基本情景下季度预测数"]
_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_ACTUAL_HEADER = ["指标名称", "期末数", "期初数"]
_MINIMUM_HEADER = ["行次", "项目", "期末数", "期初数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]
_THREE_YEAR_HEADER = ["项目", "数值"]

_TAG_MAIN_RE = re.compile(r"表\s*3\.1\s*[：:]?\s*偿付能力充足率")
_TAG_LIQUIDITY_RE = re.compile(r"表\s*3\.3\s*[：:]?\s*流动性风险监测指标")
_TAG_ACTUAL_1_RE = re.compile(r"表\s*9\.1\s*[：:]?\s*实际资本")
_TAG_ACTUAL_2_RE = re.compile(r"表\s*9\.2\s*[：:]?\s*实际资本")
_TAG_MINIMUM_RE = re.compile(r"表\s*10\.1\s*[：:]?\s*最低资本")
_RETURN_RE = re.compile(
    r"近三年平均(?P<kind>综合)?投资收益率为?\s*(?P<value>-?\d+(?:\.\d+)?%)"
)

Stop = Callable[[str], bool]


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _lines(text: str) -> list[str]:
    return [_clean(line) for line in (text or "").splitlines()]


def _is_placeholder(value: str) -> bool:
    return value in _PLACEHOLDERS


def _is_value(value: str) -> bool:
    if not value:
        return False
    if _is_placeholder(value):
        return True
    return bool(_VALUE_RE.fullmatch(value.replace(" ", "")))


def _is_row_marker(value: str) -> bool:
    return bool(_ROW_NO_RE.fullmatch(value)) or bool(_GROUP_NO_RE.fullmatch(value))


def _value_tokens(line: str) -> list[str]:
    """一行里可能并排了同一行的多个数值（2023 流动性表的 1.13 / 2.47）。"""
    tokens = [token for token in line.split() if token]
    if len(tokens) >= 2 and all(_is_value(token) for token in tokens):
        return tokens
    return [line] if _is_value(line) else []


def _split_row_prefix(lines: Sequence[str]) -> list[str]:
    """把“1* 量化风险最低资本”“4 最低资本”这类行拆成行次 + 标签。"""
    output: list[str] = []
    for line in lines:
        match = re.match(r"^(\d+(?:\.\d+)*(?:\*)?)\s+(\S.*)$", line)
        if match and not _is_value(match.group(2)):
            output.append(match.group(1))
            output.append(_clean(match.group(2)))
        else:
            output.append(line)
    return output


def _header_end(lines: Sequence[str], start: int, end_term: str, *, limit: int = 40) -> int | None:
    for index in range(start, min(len(lines), start + limit)):
        if lines[index] == end_term:
            return index + 1
    return None


def _parse_body(
    lines: Sequence[str],
    *,
    n_values: int,
    keep_row_no: bool = False,
    stop: Stop | None = None,
) -> list[list[str]]:
    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    row_no = ""
    for raw in lines:
        line = _clean(raw)
        if not line:
            continue
        if line.startswith("单位") or line.startswith("【"):
            continue
        if stop is not None and rows and stop(line):
            break
        if not label and not values and _is_row_marker(line):
            row_no = line if keep_row_no else ""
            continue
        tokens = _value_tokens(line)
        if tokens:
            if not label and not row_no:
                continue
            for token in tokens:
                values.append(token)
                if len(values) < n_values:
                    continue
                text = "".join(label).strip()
                if text and not _GROUP_TITLE_RE.match(text):
                    prefix = [row_no] if keep_row_no else []
                    rows.append(prefix + [text] + values[:n_values])
                label = []
                values = []
                row_no = ""
            continue
        if values:  # 标签行出现在半行数值之后：丢弃这半行
            values = []
        label.append(line)
    return rows


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


def _sync_blocks(table: Any, rows: list[list[str]], pages: Sequence[int]) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = [list(row) for row in rows]
    page = int(pages[0]) if pages else 1
    table.table_blocks = [{"page": page, "rows": [list(row) for row in rows]}]


def _pages_with(texts: Sequence[str], pattern: re.Pattern[str], preferred: Sequence[int]) -> list[int]:
    pages: list[int] = []
    for page in preferred:
        if page not in pages:
            pages.append(page)
    for index, text in enumerate(texts, start=1):
        if pattern.search(text or "") and index not in pages:
            pages.append(index)
    return sorted(pages)


def _pages_with_terms(texts: Sequence[str], terms: Sequence[str], preferred: Sequence[int]) -> list[int]:
    pages: list[int] = []
    for page in preferred:
        if page not in pages:
            pages.append(page)
    for index, text in enumerate(texts, start=1):
        if all(term in (text or "") for term in terms) and index not in pages:
            pages.append(index)
    return sorted(pages)


def _segments(lines: Sequence[str]) -> list[list[str]]:
    """按“表3.x”标题切段；没有表格标题时整页就是一段。"""
    segments: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _TABLE_TAG_RE.match(line) and current:
            segments.append(current)
            current = []
        current.append(line)
    if current:
        segments.append(current)
    return segments


def _stop_heading(line: str) -> bool:
    return bool(_TABLE_TAG_RE.match(line) or _HEADING_RE.match(line) or _SECTION_RE.match(line))


def _first_tag_index(lines: Sequence[str], tag: re.Pattern[str]) -> int | None:
    return next((index for index, line in enumerate(lines) if tag.search(line)), None)


# --------------------------------------------------------------------------- #
# SOLVENCY_MAIN
# --------------------------------------------------------------------------- #
def _rebuild_solvency_main(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    rows: list[list[str]] = []
    for page in _pages_with(texts, _TAG_MAIN_RE, pages):
        lines = _split_row_prefix(_lines(texts[page - 1]))
        start = _first_tag_index(lines, _TAG_MAIN_RE)
        if start is None:
            continue
        body = _header_end(lines, start, "下季度预测数")
        if body is None:
            continue
        rows.extend(_parse_body(lines[body:], n_values=3, stop=_stop_heading))
    return [_MAIN_HEADER, *rows]


# --------------------------------------------------------------------------- #
# NON_LIFE_OPERATING_METRICS
# --------------------------------------------------------------------------- #
def _operating_segment_rows(lines: Sequence[str]) -> list[list[str]]:
    for index, line in enumerate(lines):
        if line != "指标名称":
            continue
        if not any(
            candidate == "本年度累计数" for candidate in lines[index + 1:index + 4]
        ):
            continue
        body = _header_end(lines, index, "本年度累计数", limit=5)
        if body is None:
            continue
        return _parse_body(lines[body:], n_values=2)
    return []


def _rebuild_operating(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    rows: list[list[str]] = []
    for page in _pages_with_terms(
        texts, ("指标名称", "本年度累计数"), pages
    ):
        for segment in _segments(_lines(texts[page - 1])):
            if "本年度累计数" not in segment or "指标名称" not in segment:
                continue
            rows.extend(_operating_segment_rows(segment))
    return [_OPERATING_HEADER, *rows]


# --------------------------------------------------------------------------- #
# ACTUAL_CAPITAL
# --------------------------------------------------------------------------- #
def _parse_actual_piece(
    texts: Sequence[str],
    tag: re.Pattern[str],
    *,
    keep_row_no: bool,
    pages: Sequence[int],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for page in _pages_with(texts, tag, pages):
        lines = _split_row_prefix(_lines(texts[page - 1]))
        start = _first_tag_index(lines, tag)
        if start is None:
            continue
        body = _header_end(lines, start, "期初数")
        if body is None:
            continue
        rows.extend(
            _parse_body(
                lines[body:],
                n_values=2,
                keep_row_no=keep_row_no,
                stop=_stop_heading,
            )
        )
    return rows


def _rebuild_actual_capital(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    balance = _parse_actual_piece(
        texts, _TAG_ACTUAL_1_RE, keep_row_no=False, pages=pages
    )
    detail = _parse_actual_piece(
        texts, _TAG_ACTUAL_2_RE, keep_row_no=True, pages=pages
    )
    if not detail:
        return []
    # 表9.1 只取报表/认可口径的 5 行；资本口径行在表9.2 里有完整版本。
    extras = [
        row
        for row in balance
        if row and (row[0].startswith("报表") or row[0].startswith("认可"))
    ]
    fixed: list[list[str]] = []
    for row in extras:
        fixed.append([row[0], *row[1:]])
    for row_no, label, *values in detail:
        if row_no.startswith("3.") and label.startswith("递延所得税资产"):
            # 3.x 是附属一级资本的调整项，与 1.2.4 的核心一级资本递延所得税
            # 资产同名；taxonomy 里前者的名称带“附属一级资本-”前缀。
            label = f"附属一级资本-{label}"
        fixed.append([label, *values])
    return [_ACTUAL_HEADER, *fixed]


# --------------------------------------------------------------------------- #
# MINIMUM_CAPITAL
# --------------------------------------------------------------------------- #
def _rebuild_minimum_capital(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    rows: list[list[str]] = []
    for page in _pages_with(texts, _TAG_MINIMUM_RE, pages):
        lines = _split_row_prefix(_lines(texts[page - 1]))
        start = _first_tag_index(lines, _TAG_MINIMUM_RE)
        if start is None:
            continue
        body = _header_end(lines, start, "期初数")
        if body is None:
            continue
        rows.extend(
            _parse_body(
                lines[body:], n_values=2, keep_row_no=True, stop=_stop_heading
            )
        )
    return [_MINIMUM_HEADER, *rows]


# --------------------------------------------------------------------------- #
# LIQUIDITY_MONITORING
# --------------------------------------------------------------------------- #
def _rebuild_liquidity(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    rows: list[list[str]] = []
    for page in _pages_with(texts, _TAG_LIQUIDITY_RE, pages):
        lines = _split_row_prefix(_lines(texts[page - 1]))
        start = _first_tag_index(lines, _TAG_LIQUIDITY_RE)
        if start is None:
            continue
        body = _header_end(lines, start, "上季度数")
        if body is None:
            continue
        rows.extend(_parse_body(lines[body:], n_values=2, stop=_stop_heading))
    return [_LIQUIDITY_HEADER, *rows]


# --------------------------------------------------------------------------- #
# THREE_YEAR_INVESTMENT_RETURN
# --------------------------------------------------------------------------- #
def _rebuild_three_year(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    found: dict[str, str] = {}
    for page in pages or range(1, len(texts) + 1):
        text = texts[page - 1] if 1 <= page <= len(texts) else ""
        for match in _RETURN_RE.finditer(text or ""):
            key = "综合" if match.group("kind") else "普通"
            found.setdefault(key, match.group("value"))
    if "普通" not in found or "综合" not in found:
        return []
    return [
        _THREE_YEAR_HEADER,
        ["近三年平均投资收益率", found["普通"]],
        ["近三年平均综合投资收益率", found["综合"]],
    ]


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
@register("dadi")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """大地专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        texts = [
            doc.load_page(index).get_text() or "" for index in range(len(doc))
        ]
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            pages = _table_pages(table)
            if table_id == "SOLVENCY_MAIN":
                rows = _rebuild_solvency_main(texts, pages)
                if len(rows) > 5:
                    _sync_blocks(table, rows, pages)
            elif table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                rows = _rebuild_operating(texts, pages)
                if len(rows) > 10:
                    _sync_blocks(table, rows, pages)
            elif table_id == "ACTUAL_CAPITAL":
                rows = _rebuild_actual_capital(texts, pages)
                if len(rows) > 10:
                    _sync_blocks(table, rows, pages)
            elif table_id == "MINIMUM_CAPITAL":
                rows = _rebuild_minimum_capital(texts, pages)
                if len(rows) > 10:
                    _sync_blocks(table, rows, pages)
            elif table_id == "LIQUIDITY_MONITORING":
                rows = _rebuild_liquidity(texts, pages)
                if len(rows) > 5:
                    _sync_blocks(table, rows, pages)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                rows = _rebuild_three_year(texts, pages)
                if rows:
                    _sync_blocks(table, rows, pages)
    finally:
        doc.close()
    return tables
