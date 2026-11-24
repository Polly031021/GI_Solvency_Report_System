"""诚泰财险专属提取后处理（2022-2025 版式）。

通用规则在诚泰报告上的缺口：

1. ``SOLVENCY_MAIN``：pdfplumber 线框表把“行次”列保留下来，且充足率/溢额
   两个指标名带公式后缀（如“综合偿付能力充足率（核心资本÷最低资本）”），
   标准库相似度 < 0.72 无法映射。
2. ``NON_LIFE_OPERATING_METRICS``：2023/2024 经营表跨两页且续页无表头，
   通用整页提取只选中第一页的前 13 行，规模类指标（签单保费等）全部丢失。
3. ``ACTUAL_CAPITAL``：共享层线框识别把“标签+期末数+期初数”合并进第 1 列，
   后两列留空（2023 年起四列表均如此），合计行也常落在下一页。
4. ``MINIMUM_CAPITAL``：S05 主表与“保费和准备金风险最低资本”子表同章，
   通用提取会把子表并进来；2024/2025 合计行同样在下一页。
5. ``THREE_YEAR_INVESTMENT_RETURN``：2023 该表与经营表同页，通用提取把
   整张经营表并入近三年表。
6. ``LIQUIDITY_MONITORING``：2024 通用定位落到 LCR 表；正文监测表的
   行标签在 PDF 文本流末尾、数值在前，且 2024/2025 跨页、无表头。

这里统一用 pdfplumber 的 lines 线框策略按页重建目标表，rows 与
table_blocks 同步，保持幂等；不写死年份和页码，按表头/首行特征扫描。
"""

from __future__ import annotations

import io
import re
from typing import Any, Iterable, Sequence

from . import register

_MAIN_HEADER = ["指标名称", "本季度数", "上季度可比数", "基本情景下的下季度预测数"]
_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_ACTUAL_HEADER = ["指标名称", "期末数", "期初数"]
_MINIMUM_HEADER = ["指标名称", "期末数", "期初数"]
_THREE_YEAR_HEADER = ["指标名称", "近三年平均"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]

_FORMULA_SUFFIX_RE = re.compile(r"[（(][^（）()]*(?:÷|/|－|-|—)[^（）()]*[）)]\s*$")
_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_GROUP_PREFIX_RE = re.compile(r"^[一二三四五六七八九十]+[、.．]\s*")
_CONTINUATION_LABEL_RE = re.compile(
    r"^(?:\d+(?:\.\d+)+|\d+[.．、]|（[一二三四五六七八九十]+）)"
)


def _text(cell: Any) -> str:
    """去掉单元格内换行/空白；数值里的千分位空格也要清掉。"""
    return re.sub(r"\s+", "", str(cell or "").replace("\u00a0", " "))


def _number(cell: Any) -> str:
    text = _text(cell)
    if text in {"", "-", "--", "—", "–", "－", "/"}:
        return text
    return text


def _is_numeric(text: str) -> bool:
    return bool(_NUMBER_RE.match(text.replace(" ", "")))


def _label_col(row: Sequence[str]) -> int:
    """四列表首列是行次时，标签在第 2 列；否则第 1 列。"""
    if len(row) >= 2 and re.fullmatch(r"\d+(?:\.\d+)*", row[0] or ""):
        return 1
    return 0


def _strip_formula(label: str) -> str:
    return _FORMULA_SUFFIX_RE.sub("", label).strip()


def _read_pages(pdf_bytes: bytes) -> dict[int, list[list[list[str]]]]:
    """返回 {物理页: [表, ...]}，单元格已去空白。"""
    import pdfplumber

    pages: dict[int, list[list[list[str]]]] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            raw_tables = page.extract_tables(
                {
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                }
            )
            pages[index] = [
                [[_text(cell) for cell in row] for row in table]
                for table in (raw_tables or [])
                if table
            ]
    return pages


def _read_texts(pdf_bytes: bytes) -> dict[int, str]:
    """返回 {物理页: 纯文本}，用于补表格线框漏掉的跨页分组标题。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return {index: doc[index - 1].get_text() for index in range(1, len(doc) + 1)}
    finally:
        doc.close()


def _iter_tables(
    pages: dict[int, list[list[list[str]]]], start: int = 1
) -> Iterable[tuple[int, list[list[str]]]]:
    for page_no in sorted(pages):
        if page_no < start:
            continue
        for rows in pages[page_no]:
            yield page_no, rows


def _header_index(rows: list[list[str]], needles: Sequence[str]) -> int | None:
    for index, row in enumerate(rows[:5]):
        joined = "".join(row)
        if all(needle in joined for needle in needles):
            return index
    return None


# ---------------------------------------------------------------------------
# SOLVENCY_MAIN
# ---------------------------------------------------------------------------
def _build_main(pages) -> tuple[list[list[str]], int] | None:
    for page_no, rows in _iter_tables(pages):
        header_at = _header_index(rows, ("本季度",))
        if header_at is None:
            continue
        header = rows[header_at]
        name_at = next(
            (
                i
                for i, c in enumerate(header)
                if c in {"项目", "指标名称"}
            ),
            None,
        )
        if name_at is None or not any("综合偿付能力充足率" in "".join(r) for r in rows):
            continue
        value_start = name_at + 1
        out: list[list[str]] = [list(_MAIN_HEADER)]
        started = False
        for row in rows[header_at + 1 :]:
            cells = list(row) + [""] * 5
            label = _strip_formula(cells[name_at])
            if "认可资产" in label and not started:
                started = True
            if not started:
                continue
            values = [cells[value_start + i] for i in range(3)]
            if not label:
                continue
            if not any(_is_numeric(value) for value in values):
                continue
            out.append([label, *values])
            if "综合偿付能力充足率" in label:
                return out, page_no
    return None


# ---------------------------------------------------------------------------
# 主要经营指标（跨页拼接）
# ---------------------------------------------------------------------------
def _operating_table(rows: list[list[str]]) -> tuple[int, int, int, int] | None:
    for header_at in range(min(3, len(rows))):
        header = rows[header_at]
        if not any("指标名称" in cell for cell in header):
            continue
        q_at = next((i for i, c in enumerate(header) if "本季度" in c), None)
        y_at = next(
            (
                i
                for i, c in enumerate(header)
                if "本年度累计" in c or "1-12月" in c
            ),
            None,
        )
        if q_at is None or y_at is None:
            continue
        name_at = next((i for i, c in enumerate(header) if "指标名称" in c), 0)
        return header_at, name_at, q_at, y_at
    return None


def _is_operating_continuation(rows: list[list[str]]) -> bool:
    if not rows:
        return False
    if any("指标名称" in "".join(row) for row in rows[:2]):
        return False
    widths = [len(row) for row in rows]
    if not widths or max(widths) < 3:
        return False
    first = rows[0]
    # 续页表标签固定在第 1 列；数值列可能比标签更长，不能按最长单元格取。
    label = first[0] if first else ""
    return bool(_CONTINUATION_LABEL_RE.match(label))


_INLINE_UNIT_RE = re.compile(r"[（(]\s*(亿元|万元|千元|元|%)\s*[）)]\s*$")


def _operating_unit_records(
    rows: list[list[str]],
    page: int,
) -> list[Any]:
    """2022 经营表单位写在行标签里（元/万元/%）。

    把这些单位拆成行级 unit_records，并把标签里的括号单位去掉。原因是
    STEP3 的 _unit_from_block_rows 会把首个“（元）”当成整块单位，压过
    后续“（万元）”行，导致 56,662 万元被当成 56,662 元。
    """
    from services.solvency_table_extractor import UnitRecord

    records: list[Any] = []
    current = ""
    for row in rows[1:]:
        label = str(row[0] or "")
        match = _INLINE_UNIT_RE.search(label)
        if match:
            current = match.group(1)
            row[0] = _INLINE_UNIT_RE.sub("", label).strip()
        elif not current:
            continue
        records.append(
            UnitRecord(
                scope="行级",
                target=str(row[0]),
                raw_unit=current,
                normalized_unit=current,
                source_page=page,
                source_type="行级单位",
                source_text=label,
            )
        )
    return records


def _build_operating(pages) -> tuple[list[list[str]], int, list[int], list[Any]] | None:
    for page_no, rows in _iter_tables(pages):
        layout = _operating_table(rows)
        if layout is None:
            continue
        header_at, name_at, q_at, y_at = layout
        out: list[list[str]] = [list(_OPERATING_HEADER)]
        for row in rows[header_at + 1 :]:
            cells = list(row) + [""] * 6
            label = cells[name_at]
            q_value, y_value = cells[q_at], cells[y_at]
            if not label:
                continue
            if not _is_numeric(q_value) and not _is_numeric(y_value):
                continue
            out.append([label, q_value, y_value])
        pages_used = [page_no]
        # 续页：下一页开头是“2.综合费用率 / 5.3 经纪渠道”之类无表头行。
        for next_page in (page_no + 1, page_no + 2):
            continuation = None
            for candidate in pages.get(next_page, []):
                if _is_operating_continuation(candidate):
                    continuation = candidate
                    break
            if continuation is None:
                break
            added = False
            for row in continuation:
                cells = list(row) + [""] * 6
                label = cells[0]
                q_value, y_value = cells[1], cells[2]
                if not label or label.startswith("三、"):
                    break
                if not _is_numeric(q_value) and not _is_numeric(y_value):
                    continue
                out.append([label, q_value, y_value])
                added = True
            if added:
                pages_used.append(next_page)
            else:
                break
        return out, pages_used[0], pages_used, _operating_unit_records(out, pages_used[0])
    return None


# ---------------------------------------------------------------------------
# 实际资本 / 最低资本（行次表，跨页合计）
# ---------------------------------------------------------------------------
def _build_capital(
    pages,
    *,
    start_label: str,
    stop_label: str,
) -> tuple[list[list[str]], int, list[int]] | None:
    for page_no, rows in _iter_tables(pages):
        header_at = _header_index(rows, ("项目", "期末数", "期初数"))
        if header_at is None:
            continue
        header = rows[header_at]
        name_at = next((i for i, c in enumerate(header) if c == "项目"), 0)
        out: list[list[str]] = [list(_ACTUAL_HEADER)]
        started = False
        pages_used = [page_no]
        for row in rows[header_at + 1 :]:
            cells = list(row) + [""] * 4
            label = cells[name_at]
            if label == start_label:
                started = True
            if not started:
                continue
            values = [cells[name_at + 1], cells[name_at + 2]]
            if not any(_is_numeric(value) for value in values):
                continue
            out.append([label, values[0], values[1]])
            if label == stop_label:
                return out, page_no, pages_used
        # 合计行在下一页且续页无表头。
        for next_page in (page_no + 1, page_no + 2):
            added = False
            for candidate in pages.get(next_page, []):
                if any("项目" in "".join(row) for row in candidate[:2]):
                    continue
                for row in candidate:
                    cells = list(row) + [""] * 4
                    label_at = _label_col(cells)
                    label = cells[label_at]
                    values = [cells[label_at + 1], cells[label_at + 2]]
                    if not label or not any(_is_numeric(v) for v in values):
                        continue
                    out.append([label, values[0], values[1]])
                    added = True
                    if label == stop_label:
                        pages_used.append(next_page)
                        return out, page_no, pages_used
            if added:
                pages_used.append(next_page)
            else:
                break
    return None


# ---------------------------------------------------------------------------
# 近三年平均投资收益率（正文两行）
# ---------------------------------------------------------------------------
def _build_three_year(pages, pdf_bytes: bytes) -> tuple[list[list[str]], int] | None:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for index, page in enumerate(doc, start=1):
            text = page.get_text()
            match = re.search(
                r"近三年平均投资收益率\s*[:：]?\s*(-?\d[\d,]*(?:\.\d+)?%)",
                text,
            )
            match_all = re.search(
                r"近三年平均综合投资收益率\s*[:：]?\s*(-?\d[\d,]*(?:\.\d+)?%)",
                text,
            )
            if not match:
                continue
            rows = [
                list(_THREE_YEAR_HEADER),
                ["近三年平均投资收益率", match.group(1)],
                [
                    "近三年平均综合投资收益率",
                    match_all.group(1) if match_all else "",
                ],
            ]
            return rows, index
    finally:
        doc.close()
    return None


# ---------------------------------------------------------------------------
# 流动性风险监测指标
# ---------------------------------------------------------------------------
def _liquidity_rows(rows: list[list[str]], header_at: int) -> list[list[str]]:
    header = rows[header_at]
    v1_at = next((i for i, c in enumerate(header) if "1-12月" in c), 2)
    v2_at = next((i for i, c in enumerate(header) if "1-9月" in c), 3)
    out: list[list[str]] = []
    for row in rows[header_at + 1 :]:
        cells = list(row) + [""] * (v2_at + 1)
        item = cells[1] if len(cells) > 1 else ""
        if item != "指标值":
            continue
        label = _GROUP_PREFIX_RE.sub("", cells[0])
        if not label:
            continue
        if label in {"百元保费经营活动现金净流量", "百元保费经营活动净现金流"}:
            label = "百元保费现金净流量"
        out.append([label, cells[v1_at], cells[v2_at]])
    return out


def _liquidity_text_groups(text: str) -> list[tuple[str, str, str]]:
    """从页文本补出线框表漏掉的分组（如 2024 p19 的“七、AA级…”）。

    文本顺序是“组名（可能折行）/指标值/本期/上期”。只在“指标值”紧邻
    组名时采用，避免把明细行误当分组。
    """
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    found: list[tuple[str, str, str]] = []
    for index, line in enumerate(lines):
        if line != "指标值" or index < 1 or index + 2 >= len(lines):
            continue
        heading = ""
        for back in (1, 2):
            if index - back < 0:
                continue
            candidate = lines[index - back]
            if re.match(r"^[一二三四五六七八九十]+、", candidate):
                heading = "".join(lines[index - back : index])
                break
        if not heading:
            continue
        first, second = lines[index + 1], lines[index + 2]
        if _is_numeric(first) and _is_numeric(second):
            found.append((_text(_GROUP_PREFIX_RE.sub("", heading)), first, second))
    return found


def _build_liquidity(
    pages,
    texts: dict[int, str] | None = None,
) -> tuple[list[list[str]], int, list[int]] | None:
    for page_no, rows in _iter_tables(pages):
        header_at = _header_index(rows, ("监测指标", "1-12月"))
        if header_at is None:
            continue
        body = _liquidity_rows(rows, header_at)
        out = [list(_LIQUIDITY_HEADER), *body]
        pages_used = [page_no]
        present = {_text(row[0]) for row in body}
        # 续页：无表头，仍按“指标值”行取组名。
        for next_page in (page_no + 1, page_no + 2):
            added = False
            for candidate in pages.get(next_page, []):
                for row in candidate:
                    cells = list(row) + [""] * 4
                    if len(cells) < 2 or cells[1] != "指标值":
                        continue
                    label = _GROUP_PREFIX_RE.sub("", cells[0])
                    if not label:
                        continue
                    if label in {
                        "百元保费经营活动现金净流量",
                        "百元保费经营活动净现金流",
                    }:
                        label = "百元保费现金净流量"
                    key = _text(label)
                    if key in present:
                        continue
                    out.append([label, cells[2], cells[3]])
                    present.add(key)
                    added = True
            for label, first, second in _liquidity_text_groups(
                (texts or {}).get(next_page, "")
            ):
                key = _text(label)
                if key in present:
                    continue
                out.append([label, first, second])
                present.add(key)
                added = True
            if added:
                pages_used.append(next_page)
            else:
                break
        return out, pages_used[0], pages_used
    return None


# ---------------------------------------------------------------------------
# 后处理入口
# ---------------------------------------------------------------------------
def _report_year(pdf_bytes: bytes) -> int | None:
    """从封面“YYYY 年第N季度”识别报告年度，用于屏蔽 2022 图片表。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        head = "".join(doc[index].get_text() for index in range(min(2, len(doc))))
    finally:
        doc.close()
    match = re.search(r"(\d{4})\s*年第\s*[1-4一二三四]\s*季度", head)
    return int(match.group(1)) if match else None


def _rebuild(pdf_bytes: bytes) -> dict[str, dict[str, Any]]:
    pages = _read_pages(pdf_bytes)
    texts = _read_texts(pdf_bytes)
    rebuilt: dict[str, dict[str, Any]] = {}

    main = _build_main(pages)
    if main:
        rows, page = main
        rebuilt["SOLVENCY_MAIN"] = {
            "rows": rows,
            "pages": [page],
            "unit": "元",
            "note": "按项目/本季度列表头重建；已去掉行次列与充足率公式后缀",
        }

    operating = _build_operating(pages)
    if operating:
        rows, page, used, unit_records = operating
        rebuilt["NON_LIFE_OPERATING_METRICS"] = {
            "rows": rows,
            "pages": used,
            "unit": "",
            "unit_records": unit_records,
            "note": "按主要经营指标表重建并拼接无表头续页",
        }

    actual = _build_capital(
        pages, start_label="核心一级资本", stop_label="实际资本合计"
    )
    if actual:
        rows, page, used = actual
        rebuilt["ACTUAL_CAPITAL"] = {
            "rows": rows,
            "pages": used,
            "unit": "元",
            "note": "只取“2.实际资本表”，从核心一级资本拼到实际资本合计",
        }

    minimum = _build_capital(
        pages, start_label="量化风险最低资本", stop_label="最低资本"
    )
    if minimum:
        rows, page, used = minimum
        rebuilt["MINIMUM_CAPITAL"] = {
            "rows": rows,
            "pages": used,
            "unit": "元",
            "note": "只取 S05 主表，从量化风险最低资本拼到最低资本，排除子表",
        }

    three = _build_three_year(pages, pdf_bytes)
    if three:
        rows, page = three
        rebuilt["THREE_YEAR_INVESTMENT_RETURN"] = {
            "rows": rows,
            "pages": [page],
            "unit": "%",
            "note": "正文“近三年平均（综合）投资收益率”两行",
        }

    liquidity = _build_liquidity(pages, texts)
    if liquidity:
        rows, page, used = liquidity
        rebuilt["LIQUIDITY_MONITORING"] = {
            "rows": rows,
            "pages": used,
            "unit": "",
            "note": "只保留各分组“指标值”行，跨页拼接",
        }

    return rebuilt


@register("chengtai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """诚泰专属后处理；异常由调用方捕获，不在这里吞掉。"""
    rebuilt = _rebuild(pdf_bytes)
    if not rebuilt:
        return tables

    # 2022 年实际资本/最低资本/流动性为图片页，正文没有可提取的文字表；
    # 通用规则会把 p14 主表误指给这些 table_id，这里直接丢弃，避免重复记录。
    year = _report_year(pdf_bytes)
    image_only = {
        "ACTUAL_CAPITAL",
        "MINIMUM_CAPITAL",
        "LIQUIDITY_MONITORING",
        "THREE_YEAR_INVESTMENT_RETURN",
    }
    if year is not None and year <= 2022:
        tables = [
            table
            for table in tables
            if str(getattr(table, "table_id", "") or "") not in image_only
            or str(getattr(table, "table_id", "") or "") in rebuilt
        ]

    existing = {
        str(getattr(table, "table_id", "") or ""): table for table in tables
    }
    for table_id, payload in rebuilt.items():
        rows = payload["rows"]
        if not rows or len(rows) <= 1:
            continue
        pages = list(payload["pages"] or [1])
        table = existing.get(table_id)
        if table is None:
            from services.batch_extraction import FlexibleTable

            table = FlexibleTable(
                table_id=table_id,
                table_name=table_id,
                page=pages[0],
                source_pages=pages,
                rows=rows,
                table_blocks=[
                    {"page": pages[0], "rows": [list(row) for row in rows]}
                ],
                strategy="专属提取·诚泰·按线框表重建",
                quality_score=1.0,
                evidence=payload["note"],
                unit=payload["unit"],
                unit_records=list(payload.get("unit_records") or []),
            )
            tables.append(table)
        else:
            table.rows = rows
            table.table_blocks = [
                {"page": pages[0], "rows": [list(row) for row in rows]}
            ]
            table.source_pages = pages
            table.page = pages[0]
            table.unit = payload["unit"]
            table.unit_records = list(payload.get("unit_records") or [])
            table.note = payload["note"]
            table.strategy = "专属提取·诚泰·按线框表重建"
    return tables


from ._document import STANDARD_NON_LIFE_LAYOUT, make_document_hooks

_CHENGTAI_LAYOUT = dict(STANDARD_NON_LIFE_LAYOUT)
_CHENGTAI_LAYOUT["LIQUIDITY_MONITORING"] = {
    "title": "流动性风险监测指标",
    "required": [],
    "continuation": 2,
    "tail_terms": ["监测指标", "持有关联方资产", "应收款项占比"],
    "stop": "(?!)",
    "unit": "万元",
}
locate_company_tables, extract_company_tables = make_document_hooks(
    _CHENGTAI_LAYOUT, "诚泰财产", postprocess
)

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("chengtai", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "chengtai",
        pdf_bytes,
        table_configs,
        mode="document",
    )
