"""太保产险专属提取后处理：重建 6 张目标表。

太保产险 2022–2025 的偿付能力报告版式稳定，但通用规则在三个位置会
丢行或串表：

1. 偿付能力主表：PDF 表头把“项目/基本情景下的下季度预测”拆成多格，
   通用提取得到 5 列且第 3 列全空，表头也错位。
2. 主要经营指标：2023/2024 从第 12 页下半页开始、第 13 页无表头续页；
   2022/2025 虽在第 12 页完整，但整页帧会混入流动性监测表或备注。
3. 最低资本 S05 跨两页，定位器只命中续页（第 25/26/27 页），首页
   整段缺失；流动性风险监管指标在 2023/2024 与监测指标分页，通用
   规则只取到监测块。

专属逻辑只重写 ``table.rows`` 与 ``table.table_blocks``，数据全部从
PDF 文本重新解析，按标题/行标签定位，不写死年份和页码；重复执行结果
一致。2025 年第 13 页的近三年投资收益率表会被同页首个 match（经营表）
挤掉，本模块在确认 PDF 中存在该表且 tables 中没有对应对象时补建。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

import fitz

from ..batch_extraction import FlexibleTable
from . import register

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_CODE_RE = re.compile(r"^\d+(?:\.\d+)?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_SPACE_RE = re.compile(r"\s+")

_OPER_HEADER = ["指标名称", "本季度", "本年累计"]
_OPER_HEADER_TOKENS = {
    "指标名称",
    "本季度",
    "本年累计",
    "本季度数",
    "本年度累计数",
}
_MAIN_HEADER = ["项目", "本季度末", "上季度末", "基本情景下的下季度预测"]
_S05_HEADER = ["行次", "项目", "本季度末", "上季度末"]
_THREE_YEAR_HEADER = ["指标名称", "本年累计"]


def _cell(value: Any) -> str:
    return _SPACE_RE.sub("", str(value or "")).strip()


def _is_number(value: Any) -> bool:
    text = str(value or "").strip()
    if text in {"-", "—", "–", "--"}:
        return True
    return bool(_NUMBER_RE.fullmatch(text))


def _join_label(parts: Iterable[str]) -> str:
    return "".join(str(part or "").strip() for part in parts)


def _page_lines(doc: Any, page: int) -> list[str]:
    """取一页的文本行；去掉页首 1–3 位页码。"""
    lines = [
        line.strip()
        for line in doc[int(page) - 1].get_text("text").splitlines()
        if line.strip()
    ]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return lines


def _find_page(doc: Any, pages: Sequence[int], needles: Sequence[str]) -> int | None:
    for page in pages:
        if not (1 <= int(page) <= doc.page_count):
            continue
        text = doc[int(page) - 1].get_text("text")
        if all(needle in text for needle in needles):
            return int(page)
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
def _fix_solvency_main(table: Any) -> None:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    if len(rows) < 8:
        return
    fixed: list[list[str]] = [_MAIN_HEADER]
    for row in rows[1:]:
        cells = [str(cell or "").strip() for cell in row]
        # 通用提取把“基本情景下的下季度预测”拆成两列，第 3 列是空列；
        # 删除空列后预测值回到第 4 列。
        if len(cells) >= 5 and not cells[3] and cells[4]:
            del cells[3]
        if not any(cells):
            continue
        cells = (cells + [""] * len(_MAIN_HEADER))[: len(_MAIN_HEADER)]
        if cells[0]:
            fixed.append(cells)
    if len(fixed) < 8:
        return
    page = int(getattr(table, "page", 1) or 1)
    _sync(table, fixed, [{"page": page, "rows": fixed}])
    table.strategy = "专属提取·太保·主表去空列"


# ---------------------------------------------------------------------------
# 主要经营指标
# ---------------------------------------------------------------------------
def _oper_stop(line: str) -> bool:
    return (
        line.startswith("备注")
        or line.startswith("（四）")
        or line.startswith("四、")
        or "近三年（综合）投资收益率" in line
    )


def _parse_oper_page(lines: Sequence[str], *, has_header: bool) -> tuple[list[list[str]], bool]:
    start = 0
    if has_header:
        index = next(
            (i for i, line in enumerate(lines) if "主要经营指标" in line),
            None,
        )
        if index is None:
            return [], False
        start = index + 1
    body = list(lines[start:])
    while body and body[0] in _OPER_HEADER_TOKENS:
        body.pop(0)

    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    stopped = False
    for line in body:
        if _oper_stop(line):
            stopped = True
            break
        if values:
            if _is_number(line):
                values.append(line)
                if len(values) == 2:
                    rows.append([_join_label(label), values[0], values[1]])
                    label, values = [], []
            else:
                label.append(line)
            continue
        if _is_number(line):
            if label:
                values = [line]
            continue
        label.append(line)
    return rows, stopped


def _rebuild_operating(doc: Any, table: Any) -> None:
    pages = [int(p) for p in (getattr(table, "source_pages", None) or [table.page])]
    start = _find_page(doc, pages, ["主要经营指标"]) or (pages[0] if pages else 1)
    if start not in pages:
        pages = [start]
    ordered = [p for p in pages if p >= start] or [start]

    blocks: list[dict[str, Any]] = []
    flat: list[list[str]] = []
    for index, page in enumerate(ordered):
        lines = _page_lines(doc, page)
        rows, stopped = _parse_oper_page(lines, has_header=(index == 0))
        if not rows:
            continue
        if index == 0:
            block_rows = [list(_OPER_HEADER), *rows]
        else:
            block_rows = rows
        blocks.append({"page": page, "rows": block_rows})
        flat.extend(block_rows)
        if stopped:
            break

    data_rows = [row for row in flat if row and _is_number(row[1] if len(row) > 1 else "")]
    if len(data_rows) < 20:
        return
    _sync(table, flat, blocks)
    table.strategy = "专属提取·太保·经营指标文本重建"


# ---------------------------------------------------------------------------
# 最低资本 S05
# ---------------------------------------------------------------------------
def _find_s05_start(doc: Any, page: int) -> tuple[int, int] | None:
    lower = max(1, int(page) - 2)
    for candidate in range(lower, int(page) + 1):
        for index, line in enumerate(_page_lines(doc, candidate)):
            if line.startswith("十、") and "最低资本" in line:
                return candidate, index
    return None


def _parse_s05(
    doc: Any,
    start_page: int,
    start_index: int,
    end_page: int,
) -> tuple[list[list[str]], int | None]:
    rows: list[list[str]] = []
    code: str | None = None
    label: list[str] = []
    values: list[str] = []
    detail_page: int | None = None

    for page in range(start_page, end_page + 1):
        lines = _page_lines(doc, page)
        if page == start_page:
            lines = lines[start_index + 1 :]
        for line in lines:
            if "保费准备金风险最低资本明细" in line:
                detail_page = page
                break
            if code is None:
                if _ROW_CODE_RE.fullmatch(line) and len(line) <= 6:
                    code, label, values = line, [], []
                continue
            if values:
                if _is_number(line):
                    values.append(line)
                    if len(values) == 2:
                        text = _join_label(label)
                        if text.endswith("最低资"):
                            text += "本"
                        elif text.endswith("最低"):
                            text += "资本"
                        rows.append([code, text, values[0], values[1]])
                        code, label, values = None, [], []
                else:
                    label.append(line)
                continue
            if _is_number(line):
                if label:
                    values = [line]
                continue
            if _ROW_CODE_RE.fullmatch(line) and len(line) <= 6:
                # 新行次先出现说明上一行没有数值（占位行），丢弃重来。
                code, label, values = line, [], []
                continue
            label.append(line)
        if detail_page is not None:
            break
    return rows, detail_page


def _rebuild_minimum(doc: Any, table: Any) -> None:
    page = int(getattr(table, "page", 1) or 1)
    start = _find_s05_start(doc, page)
    if start is None:
        return
    start_page, start_index = start
    main_rows, detail_page = _parse_s05(doc, start_page, start_index, page)
    if len(main_rows) < 25:
        return

    main_block = [list(_S05_HEADER), *main_rows]
    blocks: list[dict[str, Any]] = [{"page": start_page, "rows": main_block}]
    detail_rows: list[list[str]] = []

    existing = getattr(table, "table_blocks", None) or []
    if len(existing) >= 2:
        for block in existing[1:]:
            block_rows = [
                [str(cell or "").strip() for cell in row]
                for row in (block.get("rows") or [])
                if row
            ]
            if not block_rows:
                continue
            blocks.append(
                {
                    "page": int(block.get("page") or detail_page or page),
                    "rows": block_rows,
                }
            )
            detail_rows.extend(block_rows)
    elif detail_page:
        # 保留原表中位于主表之后的明细行，避免只改一半。
        rows = [list(row) for row in (getattr(table, "rows", None) or [])]
        if len(rows) > len(main_rows):
            tail = rows[len(main_rows) + 1 :]
            if tail:
                blocks.append({"page": detail_page, "rows": tail})
                detail_rows.extend(tail)

    flat = [*main_block, *detail_rows]
    _sync(table, flat, blocks)
    table.strategy = "专属提取·太保·S05 跨页重建"


# ---------------------------------------------------------------------------
# 流动性与近三年投资收益率
# ---------------------------------------------------------------------------
def _pair_after(
    lines: Sequence[str],
    anchor: str,
    start: int = 0,
) -> tuple[tuple[str, str] | None, int]:
    index = next(
        (i for i in range(start, len(lines)) if lines[i].strip() == anchor),
        None,
    )
    if index is None:
        return None, start
    values: list[str] = []
    for line in lines[index + 1 : index + 8]:
        if _is_number(line):
            values.append(line)
            if len(values) == 2:
                return (values[0], values[1]), index + 1
    return None, index + 1


def _parse_regulatory(lines: Sequence[str]) -> list[list[str]] | None:
    section = next(
        (
            i
            for i, line in enumerate(lines)
            if "流动性风险监管指标" in line
        ),
        None,
    )
    search_from = section if section is not None else 0
    header = next(
        (
            i
            for i in range(search_from, len(lines))
            if "本季度" in lines[i] and "上季度" in lines[i]
        ),
        None,
    )
    if header is None:
        header = next(
            (
                i
                for i in range(search_from, len(lines))
                if lines[i].strip() == "项目"
            ),
            None,
        )
    if header is None:
        return None
    body = list(lines[header + 1 :])
    stop = next(
        (
            i
            for i, line in enumerate(body)
            if line.startswith("2、流动性风险监测指标")
            or line.startswith("（三）")
            or line.startswith("备注")
        ),
        len(body),
    )
    body = body[:stop]

    records: list[list[str]] = []

    def add(prefix: str, sub: str, anchor: str, start: int = 0) -> int:
        pair, next_index = _pair_after(body, anchor, start)
        if pair is None:
            return -1
        records.append([prefix, sub, pair[0], pair[1]])
        return next_index

    if add("净现金流（万元）", "本年累计", "本年累计") < 0:
        return None
    if add("净现金流（万元）", "上一会计年度", "上一会计年度") < 0:
        return None
    if add(
        "净现金流（万元）",
        "上一会计年度之前的会计年度",
        "上一会计年度之前的会计年度",
    ) < 0:
        return None

    cursor = 0
    for index in range(1, 4):
        code = f"LCR{index}"
        code_index = next(
            (i for i in range(cursor, len(body)) if body[i].strip() == code),
            None,
        )
        if code_index is None:
            return None
        three, next_index = _pair_after(body, "未来三个月", code_index)
        if three is None:
            return None
        records.append([f"流动性覆盖率（%）", f"{code}·未来三个月", three[0], three[1]])
        twelve, next_index = _pair_after(body, "未来十二个月", next_index)
        if twelve is None:
            return None
        records.append([f"流动性覆盖率（%）", f"{code}·未来十二个月", twelve[0], twelve[1]])
        cursor = next_index

    if add(
        "经营活动净现金流回溯不利偏差率（%）",
        "最近两个季度",
        "最近两个季度",
    ) < 0:
        return None
    if add(
        "经营活动净现金流回溯不利偏差率（%）",
        "最近一个季度",
        "最近一个季度",
    ) < 0:
        return None

    if len(records) != 11:
        return None
    return [["项目", "子项/期限", "本季度末", "上季度末"], *records]


def _parse_monitoring(lines: Sequence[str]) -> list[list[str]] | None:
    start = next(
        (i for i, line in enumerate(lines) if "流动性风险监测指标" in line),
        None,
    )
    if start is None:
        return None
    body = list(lines[start + 1 :])
    while body and body[0] in {"项目", "本季度", "（累计）", "上季度", "本季度（累计）", "上季度（累计）"}:
        body.pop(0)

    records: list[list[str]] = []
    group = ""
    label: list[str] = []
    values: list[str] = []
    for line in body:
        if line in {"负债端", "资产端"}:
            group = line
            continue
        if line.startswith("特定业务现金流支出占比：") or line.startswith("（三）") or line.startswith("备注"):
            break
        if values:
            if _is_number(line):
                values.append(line)
                if len(values) == 2:
                    text = _join_label(label)
                    if text.endswith("占"):
                        text += "比（%）"
                    records.append([text, group, values[0], values[1]])
                    label, values = [], []
            else:
                label.append(line)
            continue
        if _is_number(line):
            if label:
                values = [line]
            continue
        label.append(line)

    if len(records) != 10:
        return None
    return [["项目", "分组", "本季度（累计）", "上季度（累计）"], *records]


def _rebuild_liquidity(doc: Any, table: Any) -> None:
    page = int(getattr(table, "page", 1) or 1)
    candidates = [p for p in (page - 1, page, page + 1) if 1 <= p <= doc.page_count]
    reg_page = _find_page(doc, candidates, ["LCR1", "未来三个月"])
    mon_page = _find_page(doc, candidates, ["经营活动净现金流", "流动性风险监测指标"])
    if reg_page is None or mon_page is None:
        return
    regulatory = _parse_regulatory(_page_lines(doc, reg_page))
    monitoring = _parse_monitoring(_page_lines(doc, mon_page))
    if regulatory is None or monitoring is None:
        return
    blocks = [
        {"page": reg_page, "rows": regulatory},
        {"page": mon_page, "rows": monitoring},
    ]
    flat = [*regulatory, *monitoring]
    _sync(table, flat, blocks)
    table.strategy = "专属提取·太保·流动性监管+监测重建"


def _parse_three_year(lines: Sequence[str]) -> list[list[str]] | None:
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if "近三年（综合）投资收益率" in line
            or "近三年(综合)投资收益率" in line
            or line.startswith("近三年平均投资收益率")
        ),
        None,
    )
    if start is None:
        return None
    body = list(lines[start:])
    records: list[list[str]] = []
    for label in ("近三年平均投资收益率", "近三年平均综合投资收益率"):
        index = next(
            (i for i, line in enumerate(body) if line.startswith(label)),
            None,
        )
        if index is None:
            return None
        value = next(
            (line for line in body[index + 1 : index + 4] if _is_number(line)),
            None,
        )
        if value is None:
            return None
        records.append([f"{label}（%）", value])
    if len(records) != 2:
        return None
    return [list(_THREE_YEAR_HEADER), *records]


def _three_year_page(doc: Any) -> int | None:
    for page in range(1, doc.page_count + 1):
        if "近三年平均投资收益率" in doc[page - 1].get_text("text"):
            return page
    return None


def _rebuild_three_year(doc: Any, table: Any) -> None:
    pages = [int(p) for p in (getattr(table, "source_pages", None) or [table.page])]
    page = _find_page(doc, pages, ["近三年平均投资收益率"]) or _three_year_page(doc)
    if page is None:
        return
    rows = _parse_three_year(_page_lines(doc, page))
    if not rows:
        return
    _sync(table, rows, [{"page": page, "rows": rows}])
    table.unit = "%"
    table.strategy = "专属提取·太保·近三年收益率文本重建"


def _append_three_year(doc: Any, tables: list[Any]) -> None:
    if any(
        str(getattr(table, "table_id", "") or "") == "THREE_YEAR_INVESTMENT_RETURN"
        for table in tables
    ):
        return
    page = _three_year_page(doc)
    if page is None:
        return
    rows = _parse_three_year(_page_lines(doc, page))
    if not rows:
        return
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
                "canonical_headers": list(_THREE_YEAR_HEADER),
            },
            unit="%",
            strategy="专属提取·太保·补建近三年收益率",
            quality_score=1.0,
            evidence="定位成功但同页首个 match（经营表）整页提取失败，由专属提取器从 PDF 文本补建",
        )
    )


@register("taibao")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """太保产险专属后处理入口；异常由调用方捕获。"""
    if not tables or not pdf_bytes:
        return tables
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        updated = list(tables)
        for table in updated:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                _fix_solvency_main(table)
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                _rebuild_operating(doc, table)
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_minimum(doc, table)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(doc, table)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                _rebuild_three_year(doc, table)
        _append_three_year(doc, updated)
        return updated
