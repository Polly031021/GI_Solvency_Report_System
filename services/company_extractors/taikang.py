"""泰康在线专属提取后处理：重建通用规则无法覆盖的 5 张目标表。

泰康在线（互联网财险）2022–2025 年偿付能力报告版式高度一致，但通用规则
在以下位置会丢行或串表：

1. 主要指标表（第 10 页）：2025 年表格被竖线切成两个网格，通用提取把金额
   列拆成“2”“,605,069.85”“2”…；2022–2024 年虽可用，但为保证四年口径
   一致，统一按文本行重建。
2. 主要经营指标（第 12 页）：Step1 的页内区域裁剪只保留到“综合成本率/费用率”，
   通用结果只有 13 行；2025 年 5.x 渠道签单保费还跨到第 13 页。
3. 最低资本 S05（第 25/26 页）：主表跨两页，通用结果只识别首页前几行，
   续页的行次列还会与三列表头错位。
4. 流动性风险监测指标（第 10–11 页）：标题在 10 页底部、数据行跨页，
   通用结果把上方偿付能力主表当作监测表，或只抓到 11 页的近三年收益率表。
5. 近三年（综合）投资收益率（第 11 页）：通用结果只有两行且没有表头。

专属逻辑只重写 ``table.rows`` / ``table.table_blocks`` / ``table.unit``，
数据全部从 PDF 文本重新解析，按标题和行标签定位，不写死年份与页码；
对同一份 PDF 重复执行结果一致。2022 年报告确实没有近三年收益率表，
此时不补建。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import fitz

from ..batch_extraction import FlexibleTable
from . import register

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_CODE_RE = re.compile(r"^\d+(?:\.\d+)*(?:\*)?$")
_CODE_LABEL_RE = re.compile(r"^(\d+(?:\.\d+)*(?:\*)?)\s+(\S.*)$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_SPACE_RE = re.compile(r"\s+")

_MAIN_HEADER = ["项目", "本季度末数", "上季度末数", "下季度末预测数"]
_OPER_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_MIN_HEADER = ["行次", "项目", "本季度末数", "上季度末数"]
_LIQ_HEADER = ["指标名称", "本季度数", "上季度数"]
_THREE_HEADER = ["项目", "数值"]

_DASH_TOKENS = {"-", "--", "—", "–", "－"}
_LIQ_HEADER_TOKENS = {
    "序号",
    "指标名称",
    "本期数",
    "上期数",
    "本季度数",
    "上季度数",
    "项目",
}


def _clean(text: Any) -> str:
    return _SPACE_RE.sub("", str(text or ""))


def _is_value(text: Any) -> bool:
    token = _clean(text)
    if not token:
        return False
    return bool(_NUMBER_RE.fullmatch(token)) or token in _DASH_TOKENS


def _is_row_code(text: Any) -> bool:
    return bool(_ROW_CODE_RE.fullmatch(_clean(text)))


def _page_lines(doc: Any, page: int) -> list[str]:
    """取一页文本行：去掉空行与页首页码。"""
    raw = doc[page - 1].get_text("text") or ""
    lines = [line.strip() for line in raw.splitlines()]
    lines = [line for line in lines if line]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return lines


def _find_page(doc: Any, needles: Sequence[str]) -> int | None:
    for page in range(1, doc.page_count + 1):
        text = "\n".join(_page_lines(doc, page))
        if all(needle in text for needle in needles):
            return page
    return None


def _sync(
    table: Any,
    rows: Sequence[Sequence[str]],
    blocks: Sequence[dict[str, Any]],
    *,
    unit: str | None = None,
    strategy: str = "",
) -> None:
    table.rows = [list(row) for row in rows]
    table.table_blocks = [
        {
            "page": int(block["page"]),
            "rows": [list(row) for row in block["rows"]],
            **({"unit": block["unit"]} if block.get("unit") else {}),
        }
        for block in blocks
    ]
    pages = sorted({int(block["page"]) for block in blocks})
    if pages:
        table.source_pages = pages
        table.page = pages[0]
    if unit:
        table.unit = unit
    if strategy:
        table.strategy = strategy


def _group_blocks(
    collected: Sequence[tuple[int, list[str]]],
    header: Sequence[str],
) -> list[dict[str, Any]]:
    """把 (页码, 行) 按页聚成 table_blocks，首页补表头。"""
    blocks: list[dict[str, Any]] = []
    current_page: int | None = None
    current_rows: list[list[str]] = []
    for page, row in collected:
        if current_page is None:
            current_page = page
        if page != current_page:
            blocks.append({"page": current_page, "rows": current_rows})
            current_page = page
            current_rows = []
        current_rows.append(list(row))
    if current_page is not None and current_rows:
        blocks.append({"page": current_page, "rows": current_rows})
    if blocks:
        blocks[0]["rows"] = [list(header), *blocks[0]["rows"]]
    return blocks


# ---------------------------------------------------------------- 主要指标（偿付能力）

def _parse_main(
    doc: Any,
) -> tuple[list[list[str]], list[dict[str, Any]]] | None:
    """从第 10 页“三、主要指标表”重建偿付能力充足率指标。

    2025 年该表被一条竖线切成两个网格，通用提取把金额列拆成
    “2”“,605,069.85”“2”…，只能按文本行重建；2022–2024 版式相同，
    一并重建以保持四年口径一致。
    """
    page = _find_page(doc, ["主要指标", "本季度末数"])
    if page is None:
        return None
    lines = _page_lines(doc, page)
    start = None
    for index, line in enumerate(lines):
        if _clean(line) in {"下季度预测数", "下季度末预测数", "基本情景下的下季度预测数"}:
            start = index + 1
            break
    if start is None:
        return None

    collected: list[tuple[int, list[str]]] = []
    label: list[str] = []
    values: list[str] = []

    def flush() -> None:
        nonlocal label, values
        text = "".join(_clean(part) for part in label)
        if text:
            padded = list(values) + [""] * max(0, 3 - len(values))
            collected.append((page, [text, padded[0], padded[1], padded[2]]))
        label = []
        values = []

    for line in lines[start:]:
        token = _clean(line)
        if not token:
            continue
        if (
            token.startswith("（二）")
            or token.startswith("二、")
            or "流动性风险监管指标" in token
        ):
            break
        if _is_value(token):
            if len(values) < 3:
                values.append(line)
                if len(values) >= 3:
                    flush()
            continue
        if values:
            flush()
        label.append(line)
    flush()
    if len(collected) < 10:
        return None
    blocks = _group_blocks(collected, _MAIN_HEADER)
    rows = [_MAIN_HEADER, *[row for _, row in collected]]
    return rows, blocks


# ---------------------------------------------------------------- 主要经营指标


def _is_group_label(text: str) -> bool:
    token = _clean(text)
    if not token:
        return True
    if token.endswith("指标"):
        return True
    if re.match(r"^[一二三四五六七八九十]+、", token):
        return True
    return token in {"主要经营指标", "规模类指标", "效益类指标", "品质类指标"}


def _scan_metric_rows(
    lines: Sequence[str],
    *,
    value_columns: int = 2,
) -> list[tuple[int, list[str]]]:
    """按“标签行 + 1~2 个数值行”解析经营指标表体。

    返回 (行, 该行结束后的文本索引) 不在这里维护；调用方只关心行内容。
    """
    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []

    def flush() -> None:
        nonlocal label, values
        text = "".join(_clean(part) for part in label)
        if text and not _is_group_label(text):
            padded = list(values) + [""] * max(0, value_columns - len(values))
            rows.append([text, *padded[:value_columns]])
        label = []
        values = []

    for line in lines:
        token = _clean(line)
        if not token:
            continue
        if token.startswith("注：") or re.match(r"^\d+注", token):
            break
        if _is_value(token):
            if not label:
                # 2025 年“（十二）规模类指标”下面单独一行的脚注编号 2。
                continue
            if len(values) < value_columns:
                values.append(line)
                if len(values) >= value_columns:
                    flush()
            continue
        if values:
            flush()
        elif label and _is_group_label("".join(_clean(part) for part in label)):
            # 2025 年“（十二）规模类指标”下面先出现脚注编号 2 和两个占位符，
            # 这些值被跳过；再遇到“1.签单保费”时要丢掉分组标题，不能拼接。
            label = []
        label.append(line)
    flush()
    return [(0, row) for row in rows]


def _parse_operating(
    doc: Any,
) -> tuple[list[list[str]], list[dict[str, Any]]] | None:
    page = _find_page(doc, ["财产保险公司主要经营指标"])
    if page is None:
        return None
    collected: list[tuple[int, list[str]]] = []
    parsed_pages: list[int] = []
    for offset in range(2):
        current = page + offset
        if current > doc.page_count:
            break
        lines = _page_lines(doc, current)
        if offset == 0:
            try:
                start = lines.index("指标名称") + 1
            except ValueError:
                return None
            body = [line for line in lines[start:] if line not in {"本季度数", "本年度累计数", "本季度", "本年累计"}]
        else:
            body = lines
            if not body or not body[0].startswith("5."):
                break
        parsed = _scan_metric_rows(body)
        if not parsed:
            if offset == 0:
                return None
            break
        collected.extend((current, row) for _, row in parsed)
        parsed_pages.append(current)
    if not collected:
        return None
    blocks = _group_blocks(collected, _OPER_HEADER)
    rows = [_OPER_HEADER, *[row for _, row in collected]]
    return rows, blocks


# ---------------------------------------------------------------- 最低资本 S05


def _parse_minimum(
    doc: Any,
) -> tuple[list[list[str]], list[dict[str, Any]]] | None:
    page = _find_page(doc, ["最低资本表", "行次"])
    if page is None:
        return None
    collected: list[tuple[int, list[str]]] = []
    current_code: str | None = None
    label: list[str] = []
    values: list[str] = []
    finished = False

    def flush(current_page: int) -> None:
        nonlocal current_code, label, values
        if current_code is None:
            return
        text = "".join(_clean(part) for part in label)
        if text:
            padded = list(values) + [""] * max(0, 2 - len(values))
            collected.append((current_page, [current_code, text, padded[0], padded[1]]))
        current_code = None
        label = []
        values = []

    for current in range(page, min(page + 2, doc.page_count + 1)):
        lines = _page_lines(doc, current)
        start = 0
        if current == page:
            for index, line in enumerate(lines):
                if _clean(line) in {"上季度末数", "期初数", "期末数"}:
                    start = index + 1
                    break
        for line in lines[start:]:
            token = _clean(line)
            if not token:
                continue
            if "明细" in token and "保险风险" in token:
                finished = True
                break
            match = _CODE_LABEL_RE.match(token)
            if match:
                flush(current)
                current_code = match.group(1)
                label = [match.group(2)]
                values = []
            elif _is_row_code(token):
                flush(current)
                current_code = token
                label = []
                values = []
            elif _is_value(token):
                if current_code and len(values) < 2:
                    values.append(line)
            elif current_code:
                label.append(line)
            if current_code == "4" and len(values) >= 2:
                flush(current)
                finished = True
                break
        flush(current)
        if finished:
            break
    if not collected:
        return None
    blocks = _group_blocks(collected, _MIN_HEADER)
    rows = [_MIN_HEADER, *[row for _, row in collected]]
    return rows, blocks


# ---------------------------------------------------------------- 流动性监测


def _parse_liquidity(
    doc: Any,
) -> tuple[list[list[str]], list[dict[str, Any]]] | None:
    page = _find_page(doc, ["流动性风险监测指标"])
    if page is None:
        return None
    collected: list[tuple[int, list[str]]] = []
    label: list[str] = []
    values: list[str] = []
    finished = False

    def flush(current_page: int) -> None:
        nonlocal label, values
        text = "".join(_clean(part) for part in label)
        if text:
            padded = list(values) + [""] * max(0, 2 - len(values))
            collected.append((current_page, [text, padded[0], padded[1]]))
        label = []
        values = []

    for offset in range(2):
        current = page + offset
        if current > doc.page_count:
            break
        lines = _page_lines(doc, current)
        if offset == 0:
            anchor = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if "流动性风险监测指标" in line
                ),
                len(lines),
            )
            body = lines[anchor + 1 :]
        else:
            body = lines
        for line in body:
            token = _clean(line)
            if not token:
                continue
            if "流动性风险监测指标" in token:
                continue
            if token.startswith("（四）") or token.startswith("（五）"):
                finished = True
                break
            if "近三年" in token:
                finished = True
                break
            if token in _LIQ_HEADER_TOKENS:
                continue
            if _is_row_code(token) and not label and not values:
                continue
            if _is_value(token):
                if label and len(values) < 2:
                    values.append(line)
                    if len(values) >= 2:
                        flush(current)
                continue
            if values:
                flush(current)
            label.append(line)
        flush(current)
        if finished:
            break
    data_rows = [row for _, row in collected]
    if len(data_rows) < 5:
        return None
    blocks = _group_blocks(collected, _LIQ_HEADER)
    rows = [_LIQ_HEADER, *data_rows]
    return rows, blocks


# ---------------------------------------------------------------- 近三年收益率


def _parse_three_year(
    doc: Any,
) -> tuple[int, list[list[str]]] | None:
    page = _find_page(doc, ["近三年平均投资收益率"])
    if page is None:
        return None
    lines = _page_lines(doc, page)
    rows: list[list[str]] = []
    for label in ("近三年平均投资收益率", "近三年平均综合投资收益率"):
        index = next(
            (i for i, line in enumerate(lines) if _clean(line) == label),
            None,
        )
        if index is None:
            continue
        for line in lines[index + 1 :]:
            if _is_value(line):
                rows.append([label, line])
                break
    if not rows:
        return None
    return page, rows


# ---------------------------------------------------------------- 写回与补建


def _rebuild_main(doc: Any, table: Any) -> None:
    parsed = _parse_main(doc)
    if parsed is None:
        return
    rows, blocks = parsed
    _sync(
        table,
        rows,
        blocks,
        unit="万元",
        strategy="专属提取·泰康在线·主要指标表文本重建",
    )


def _rebuild_operating(doc: Any, table: Any) -> None:
    parsed = _parse_operating(doc)
    if parsed is None:
        return
    rows, blocks = parsed
    _sync(
        table,
        rows,
        blocks,
        unit="元",
        strategy="专属提取·泰康在线·主要经营指标文本重建",
    )


def _rebuild_minimum(doc: Any, table: Any) -> None:
    parsed = _parse_minimum(doc)
    if parsed is None:
        return
    rows, blocks = parsed
    _sync(
        table,
        rows,
        blocks,
        unit="元",
        strategy="专属提取·泰康在线·最低资本 S05 文本重建",
    )


def _rebuild_liquidity(doc: Any, table: Any) -> None:
    parsed = _parse_liquidity(doc)
    if parsed is None:
        return
    rows, blocks = parsed
    for block in blocks:
        # 监测指标里只有“经营活动净现金流”是金额（万元），其余为百分比；
        # 百分比指标不会走万元换算，块级单位只影响金额行。
        block["unit"] = "万元"
    _sync(
        table,
        rows,
        blocks,
        unit="万元",
        strategy="专属提取·泰康在线·流动性风险监测指标文本重建",
    )


def _rebuild_three_year(doc: Any, table: Any) -> None:
    parsed = _parse_three_year(doc)
    if parsed is None:
        return
    page, data_rows = parsed
    rows = [_THREE_HEADER, *data_rows]
    _sync(
        table,
        rows,
        [{"page": page, "rows": rows}],
        unit="%",
        strategy="专属提取·泰康在线·近三年收益率文本重建",
    )


def _append_three_year(doc: Any, tables: list[Any]) -> None:
    if any(
        str(getattr(table, "table_id", "") or "") == "THREE_YEAR_INVESTMENT_RETURN"
        for table in tables
    ):
        return
    parsed = _parse_three_year(doc)
    if parsed is None:
        return
    page, data_rows = parsed
    rows = [_THREE_HEADER, *data_rows]
    tables.append(
        FlexibleTable(
            table_id="THREE_YEAR_INVESTMENT_RETURN",
            table_name="近三年（综合）投资收益率",
            page=page,
            source_pages=[page],
            rows=rows,
            table_blocks=[{"page": page, "rows": [list(row) for row in rows]}],
            table_config={
                "table_id": "THREE_YEAR_INVESTMENT_RETURN",
                "table_name": "近三年（综合）投资收益率",
                "canonical_headers": list(_THREE_HEADER),
            },
            unit="%",
            strategy="专属提取·泰康在线·补建近三年收益率",
            quality_score=1.0,
            evidence="定位成功但同页首个 match（经营表/监测表）整页提取未覆盖该表，由专属提取器从 PDF 文本补建",
        )
    )


@register("taikang")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """泰康在线专属后处理入口；异常由调用方捕获。"""
    if not tables or not pdf_bytes:
        return tables
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        updated = list(tables)
        by_id = {
            str(getattr(table, "table_id", "") or ""): table
            for table in updated
        }
        if "SOLVENCY_MAIN" in by_id:
            _rebuild_main(doc, by_id["SOLVENCY_MAIN"])
        if "NON_LIFE_OPERATING_METRICS" in by_id:
            _rebuild_operating(doc, by_id["NON_LIFE_OPERATING_METRICS"])
        if "MINIMUM_CAPITAL" in by_id:
            _rebuild_minimum(doc, by_id["MINIMUM_CAPITAL"])
        if "LIQUIDITY_MONITORING" in by_id:
            _rebuild_liquidity(doc, by_id["LIQUIDITY_MONITORING"])
        if "THREE_YEAR_INVESTMENT_RETURN" in by_id:
            _rebuild_three_year(doc, by_id["THREE_YEAR_INVESTMENT_RETURN"])
        _append_three_year(doc, updated)
        return updated


from ._document import make_standard_document_hooks

locate_company_tables, extract_company_tables = make_standard_document_hooks(
    "泰康在线", postprocess
)
