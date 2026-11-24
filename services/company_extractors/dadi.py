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
from typing import Any, Callable, Mapping, Sequence

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
_TAG_LCR_RE = re.compile(r"表\s*3\.2\s*[：:]?\s*流动性风险监管指标")
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


def _contract_failure(table: Any, rows: list[list[str]]) -> str:
    config = getattr(table, "table_config", None)
    contract = (
        config.get("_layout_contract")
        if isinstance(config, dict)
        else None
    )
    if not isinstance(contract, dict):
        return ""
    if not rows:
        return "版式契约：重建结果为空"
    def contract_values(value: Any) -> list[str]:
        if isinstance(value, str):
            values = re.split(r"[、|,，\n\r]+", value)
        elif isinstance(value, (list, tuple)):
            values = value
        else:
            values = []
        return [_clean(item) for item in values if _clean(item)]

    expected_headers = contract_values(contract.get("headers"))
    actual_headers = [_clean(value) for value in rows[0]]
    header_aliases = contract.get("header_aliases") or {}
    candidate_headers = [
        expected_headers,
        *[
            contract_values(value)
            for value in (contract.get("header_variants") or [])
        ],
    ]
    matched_headers: list[str] | None = None
    for candidate in candidate_headers:
        if len(actual_headers) == len(candidate) and all(
            actual == expected
            or actual in {
                _clean(alias)
                for alias in (header_aliases.get(expected) or [])
            }
            for expected, actual in zip(candidate, actual_headers)
        ):
            matched_headers = candidate
            break
    headers_match = not expected_headers or matched_headers is not None
    if not headers_match:
        return (
            "版式契约：表头不匹配，"
            f"预期 {expected_headers}，实际 {actual_headers}"
        )
    min_rows = int(contract.get("min_rows", 1) or 1)
    expected_width = len(matched_headers or expected_headers)
    if expected_headers and any(len(row) != expected_width for row in rows):
        return "版式契约：数据列数与表头不一致"
    if len(rows) < min_rows:
        return f"版式契约：仅 {len(rows)} 行，少于最低要求 {min_rows} 行"
    max_rows = contract.get("max_rows")
    if max_rows is not None and len(rows) > int(max_rows):
        return f"版式契约：{len(rows)} 行，超过上限 {max_rows} 行"
    required_rows = contract_values(contract.get("required_rows"))
    flattened_rows = {_clean("".join(row)) for row in rows[1:]}
    missing_rows = [
        label for label in required_rows
        if not any(label in row for row in flattened_rows)
    ]
    if missing_rows:
        return f"版式契约：缺少必要行 {missing_rows}"
    forbidden_rows = contract_values(contract.get("forbidden_rows"))
    forbidden = [
        label for label in forbidden_rows
        if any(label in row for row in flattened_rows)
    ]
    if forbidden:
        return f"版式契约：出现禁止行 {forbidden}"
    if contract.get("unique_rows"):
        labels = [_clean(row[0]) for row in rows[1:] if row]
        duplicates = sorted({
            label for label in labels
            if label and labels.count(label) > 1
        })
        if duplicates:
            return f"版式契约：指标行重复 {duplicates}"
    return ""


def _accept_rebuilt(
    table: Any,
    rows: list[list[str]],
    pages: Sequence[int],
    *,
    blocks: Sequence[dict[str, Any]] | None = None,
) -> bool:
    failure = _contract_failure(table, rows)
    config = dict(getattr(table, "table_config", None) or {})
    if failure:
        header = rows[0] if rows else []
        table.rows = [list(header)] if header else []
        table.table_blocks = []
        config["_layout_contract_failure"] = failure
        table.table_config = config
        return False
    config.pop("_layout_contract_failure", None)
    table.table_config = config
    if blocks:
        table.rows = [list(row) for row in rows]
        table.table_blocks = [
            {
                "page": int(block["page"]),
                "rows": [list(row) for row in block["rows"]],
            }
            for block in blocks
        ]
    else:
        _sync_blocks(table, rows, pages)
    return True


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
def _rebuild_lcr_rows(lines: Sequence[str]) -> list[list[str]]:
    start = next(
        (index for index, line in enumerate(lines) if _TAG_LCR_RE.search(line)),
        None,
    )
    if start is None:
        return []
    body = _header_end(lines, start, "上季度数")
    if body is None:
        return []
    parsed = _parse_body(
        lines[body:],
        n_values=2,
        keep_row_no=True,
        stop=_stop_heading,
    )
    rows: list[list[str]] = []
    active_lcr = ""
    for row_no, label, value_current, value_previous in parsed:
        if row_no in {"1", "2", "3"}:
            match = re.search(r"LCR\s*([123])", label, flags=re.IGNORECASE)
            active_lcr = match.group(1) if match else ""
            continue
        if row_no in {"4", "5", "6", "7"}:
            # 4/5 是自测压力情景，不能映射到必测 LCR2/LCR3。
            active_lcr = ""
            continue
        if not active_lcr:
            continue
        compact_label = label.replace(" ", "").replace("\u3000", "")
        if "未来3个月" in compact_label:
            month = 3
        elif "未来12个月" in compact_label:
            month = 12
        else:
            continue
        rows.append([
            f"LCR{active_lcr}未来{month}个月",
            value_current,
            value_previous,
        ])
    return rows


def _rebuild_liquidity_parts(
    texts: Sequence[str],
    pages: Sequence[int],
) -> tuple[list[list[str]], list[dict[str, Any]]]:
    lcr_rows: list[list[str]] = []
    lcr_source_pages: list[int] = []
    for page in _pages_with(texts, _TAG_LCR_RE, pages):
        parsed = _rebuild_lcr_rows(_split_row_prefix(_lines(texts[page - 1])))
        if parsed:
            lcr_rows.extend(parsed)
            lcr_source_pages.append(page)

    monitoring_rows: list[list[str]] = []
    monitoring_blocks: list[dict[str, Any]] = []
    for page in _pages_with(texts, _TAG_LIQUIDITY_RE, pages):
        lines = _split_row_prefix(_lines(texts[page - 1]))
        start = _first_tag_index(lines, _TAG_LIQUIDITY_RE)
        if start is None:
            continue
        body = _header_end(lines, start, "上季度数")
        if body is None:
            continue
        page_rows = _parse_body(lines[body:], n_values=2, stop=_stop_heading)
        monitoring_rows.extend(page_rows)
        if page_rows:
            monitoring_blocks.append(
                {
                    "page": page,
                    "rows": [_LIQUIDITY_HEADER, *page_rows],
                }
            )

    unique_lcr = list({
        row[0]: row for row in lcr_rows
    }.values())
    unique_monitoring = list({
        row[0]: row for row in monitoring_rows
    }.values())
    rows = [_LIQUIDITY_HEADER, *unique_lcr, *unique_monitoring]
    blocks: list[dict[str, Any]] = []
    if unique_lcr:
        blocks.append(
            {
                "page": min(lcr_source_pages) if lcr_source_pages else pages[0],
                "rows": [_LIQUIDITY_HEADER, *unique_lcr],
            }
        )
    blocks.extend(monitoring_blocks)
    return rows, blocks


def _rebuild_liquidity(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    rows, _blocks = _rebuild_liquidity_parts(texts, pages)
    return rows


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


def locate_company_tables(
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, list[int]]:
    """Locate Dadi target tables directly from company-specific table captions."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        texts = [page.get_text() or "" for page in doc]
    finally:
        doc.close()

    operating_pages = [
        page
        for page in _pages_with_terms(
            texts,
            ("指标名称", "本年度累计数"),
            [],
        )
        if any(
            term in texts[page - 1]
            for term in ("主要经营指标", "效益类指标", "规模类指标")
        )
    ]
    actual_pages = sorted(
        set(_pages_with(texts, _TAG_ACTUAL_1_RE, []))
        | set(_pages_with(texts, _TAG_ACTUAL_2_RE, []))
    )
    liquidity_pages = sorted(
        set(_pages_with(texts, _TAG_LCR_RE, []))
        | set(_pages_with(texts, _TAG_LIQUIDITY_RE, []))
    )
    three_year_pages = [
        page
        for page in _pages_with_terms(texts, ("近三年平均投资收益率",), [])
    ]
    mapping = {
        "SOLVENCY_MAIN": _pages_with(texts, _TAG_MAIN_RE, []),
        "NON_LIFE_OPERATING_METRICS": operating_pages,
        "ACTUAL_CAPITAL": actual_pages,
        "MINIMUM_CAPITAL": _pages_with(texts, _TAG_MINIMUM_RE, []),
        "LIQUIDITY_MONITORING": liquidity_pages,
        "THREE_YEAR_INVESTMENT_RETURN": three_year_pages,
    }
    requested = {
        str(config.get("table_id") or "")
        for config in table_configs
        if str(config.get("table_id") or "")
    }
    if requested:
        mapping = {
            table_id: pages
            for table_id, pages in mapping.items()
            if table_id in requested
        }
    return {
        table_id: pages
        for table_id, pages in mapping.items()
        if pages
    }


def extract_company_tables(
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Extract all Dadi target tables without invoking the generic table path."""
    import fitz
    import pandas as pd
    from services.batch_extraction import FlexibleTable

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        texts = [page.get_text() or "" for page in doc]
    finally:
        doc.close()
    pages_by_table = locate_company_tables(pdf_bytes, table_configs)
    tables: list[Any] = []
    logs: list[dict[str, Any]] = []

    for config in table_configs:
        table_id = str(config.get("table_id") or "").strip()
        table_name = str(config.get("table_name") or table_id).strip()
        pages = list(pages_by_table.get(table_id) or [])
        if not pages:
            undisclosed = table_id == "THREE_YEAR_INVESTMENT_RETURN" and not any("近三年" in text for text in texts)
            logs.append(
                {
                    "目标表": table_name,
                    "物理页码": "",
                    "识别方式": "公司专属规则",
                    "状态": "未披露" if undisclosed else "失败",
                    "说明": "原文未披露近三年投资收益率" if undisclosed else "公司专属定位未找到目标表",
                }
            )
            continue

        blocks: list[dict[str, Any]] | None = None
        if table_id == "SOLVENCY_MAIN":
            rows = _rebuild_solvency_main(texts, pages)
        elif table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
            rows = _rebuild_operating(texts, pages)
        elif table_id == "ACTUAL_CAPITAL":
            rows = _rebuild_actual_capital(texts, pages)
        elif table_id == "MINIMUM_CAPITAL":
            rows = _rebuild_minimum_capital(texts, pages)
        elif table_id == "LIQUIDITY_MONITORING":
            rows, blocks = _rebuild_liquidity_parts(texts, pages)
        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            rows = _rebuild_three_year(texts, pages)
        else:
            continue

        if not rows:
            logs.append(
                {
                    "目标表": table_name,
                    "物理页码": "、".join(map(str, pages)),
                    "识别方式": "公司专属规则",
                    "状态": "失败",
                    "说明": "公司专属解析未取得表体",
                }
            )
            continue

        frame = pd.DataFrame([list(row) for row in rows])
        table = FlexibleTable(
            table_id=table_id,
            table_name=table_name,
            page=pages[0],
            source_pages=pages,
            rows=[list(row) for row in rows],
            to_frame=lambda *args, _frame=frame, **kwargs: _frame,
            strategy="公司专属规则",
            quality_score=1.0,
            evidence="公司专属页码与表体解析",
            table_config=dict(config),
            unit="万元",
            unit_source="万元",
            source_grid_raw=[list(row) for row in rows],
            table_blocks=(
                blocks
                if blocks
                else [{"page": pages[0], "rows": [list(row) for row in rows]}]
            ),
        )
        failure = _contract_failure(table, rows)
        if failure:
            logs.append(
                {
                    "目标表": table_name,
                    "物理页码": "、".join(map(str, pages)),
                    "识别方式": "公司专属规则",
                    "状态": "失败",
                    "说明": failure,
                }
            )
            continue
        tables.append(table)
        logs.append(
            {
                "目标表": table_name,
                "物理页码": "、".join(map(str, pages)),
                "识别方式": "公司专属规则",
                "状态": "成功",
                "说明": f"公司专属解析 {len(rows) - 1} 条数据行",
            }
        )
    return {
        "tables": tables,
        "logs": logs,
        "pages_by_table": pages_by_table,
    }


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
                    _accept_rebuilt(table, rows, pages)
            elif table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                rows = _rebuild_operating(texts, pages)
                if len(rows) > 10:
                    _accept_rebuilt(table, rows, pages)
            elif table_id == "ACTUAL_CAPITAL":
                rows = _rebuild_actual_capital(texts, pages)
                if len(rows) > 10:
                    _accept_rebuilt(table, rows, pages)
            elif table_id == "MINIMUM_CAPITAL":
                rows = _rebuild_minimum_capital(texts, pages)
                if len(rows) > 10:
                    _accept_rebuilt(table, rows, pages)
            elif table_id == "LIQUIDITY_MONITORING":
                rows, blocks = _rebuild_liquidity_parts(texts, pages)
                if len(rows) > 5:
                    _accept_rebuilt(table, rows, pages, blocks=blocks)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                rows = _rebuild_three_year(texts, pages)
                if rows:
                    _accept_rebuilt(table, rows, pages)
    finally:
        doc.close()
    return tables
