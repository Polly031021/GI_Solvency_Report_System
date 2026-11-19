"""鼎和财险 2022-2025 年报版式修补。

四年版式一致，下列缺口属于通用网格流程的系统性行为，统一在这里按
PDF 文本层重建（不按年份写分支）：

* 经营表续页没有表头，通用流程不跨页，续页 5-6 行（各渠道签单保费等）丢失；
* 最低资本表(S05)含“行次”列、列线把标签切碎，通用提取只能取到 3-4 行；
* 2025 实际资本表表头“项目”被拆成“项/目”两行，通用提取丢“行次”列；
* 2024 流动性监测表标题在页尾、表体在下一页，通用提取把上一页主表拼进来；
* 近三年（综合）投资收益率是文本行（非表格），整页提取取不到（2022 年无此表）；
* 2022/2023 主表表头“预测下季度（末）数”被标准化器剥成“预测下季度数”，
  整列期间识别失败，需要改写成“下季度末预测数”。

重建只改 table.rows / table.table_blocks / table.source_pages，且每次从
PDF 重新解析，重复执行结果一致。
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from . import register

_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_VALUE_RE = re.compile(r"^-$|^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_SUB_SECTION_RE = re.compile(r"^[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)]")
_CN_CHAPTER_RE = re.compile(r"^[一二三四五六七八九十]{1,3}\s*、")

_OPERATING_HEADER = ["指标名称", "本季度(末)数", "本年度累计(末)数"]
_CAPITAL_HEADER = ["行次", "项目", "本季度（末）数", "上季度（末）数"]
_CAPITAL_HEADER_PERIOD = ["行次", "项目", "期末数", "期初数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度（末）数", "上季度（末）数"]
_THREE_YEAR_HEADER = ["指标名称", "近三年平均"]

_THREE_YEAR_LABELS = ("近三年平均投资收益率", "近三年平均综合投资收益率")

# 表头单元格（续页/拆行后可能单独成行），解析表格正文时跳过。
_HEADER_TOKENS = {
    "指标名称",
    "行次",
    "项目",
    "项",
    "目",
    "数值",
    "本季度(末)数",
    "上季度(末)数",
    "本年度累计(末)数",
    "本季度数",
    "上季度数",
    "期末数",
    "期初数",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _clean(value: str) -> str:
    return (value or "").replace("\u3000", " ").strip()


def _norm(value: str) -> str:
    return _clean(value).replace(" ", "").replace("（", "(").replace("）", ")")


def _is_value(line: str) -> bool:
    return bool(_VALUE_RE.fullmatch(_clean(line)))


def _is_row_no(line: str) -> bool:
    return bool(_ROW_NO_RE.fullmatch(_clean(line)))


def _is_header_token(line: str) -> bool:
    return _norm(line) in _HEADER_TOKENS


def _is_stop(line: str) -> bool:
    head = _clean(line).replace(" ", "")
    if not head:
        return False
    if _SUB_SECTION_RE.match(head) or _CN_CHAPTER_RE.match(head):
        return True
    if head.startswith("注：") or head.startswith("注:") or head.startswith("备注"):
        return True
    return False


def _is_meta_line(line: str) -> bool:
    """表外信息行（表名、公司名、单位），只在判定续页时使用。"""
    head = _clean(line).replace(" ", "")
    return head.startswith("单位") or head.startswith("公司名称") or head.endswith("表")


def _page_lines(doc: Any, page_no: int) -> list[str]:
    """取一页文本行，去掉页首印刷页码。"""
    lines = [_clean(line) for line in doc[page_no - 1].get_text("text").splitlines()]
    lines = [line for line in lines if line]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        printed = int(lines[0])
        if 1 <= printed and abs(printed - page_no) <= 3:
            lines = lines[1:]
    return lines


def _find_page(
    doc: Any,
    finder: Callable[[Sequence[str]], int | None],
    start_page: int = 1,
) -> tuple[int | None, list[str] | None, int | None]:
    for page_no in range(max(1, start_page), doc.page_count + 1):
        lines = _page_lines(doc, page_no)
        index = finder(lines)
        if index is not None:
            return page_no, lines, index
    return None, None, None


def _hint_page(table: Any) -> int:
    pages = getattr(table, "source_pages", None) or []
    if pages:
        try:
            return max(1, int(min(pages)) - 1)
        except (TypeError, ValueError):
            pass
    try:
        return max(1, int(getattr(table, "page", 1)))
    except (TypeError, ValueError):
        return 1


def _apply_blocks(
    table: Any, blocks: Sequence[dict[str, Any]], header: Sequence[str]
) -> None:
    table.rows = _flatten(blocks, header)
    table.table_blocks = [dict(block) for block in blocks]
    table.source_pages = [int(block["page"]) for block in blocks]


def _flatten(blocks: Sequence[dict[str, Any]], header: Sequence[str]) -> list[list[str]]:
    """合并各页块：表头只保留一次，续页重复表头去掉。"""
    flat: list[list[str]] = []
    for block in blocks:
        for row in block.get("rows") or []:
            cells = [str(cell or "") for cell in row]
            if cells == list(header):
                if not flat:
                    flat.append(list(cells))
                continue
            flat.append(cells)
    return flat


# ---------------------------------------------------------------------------
# 偿付能力充足率主表表头
# ---------------------------------------------------------------------------
# 2022/2023 年版表头写作“预测下季度（末）数”。solvency_normalizer._period_header
# 先剥掉表头里的“（末）”，得到“预测下季度数”，而 PERIOD_TERMS 只登记了
# “下季度预测数/下季度末预测数”，整列会被判为非法期间丢弃。这里把表头
# 改写成系统认识的口径，正文数值保持 PDF 原样。
_MAIN_PREDICT_HEADER = "下季度末预测数"


def _fix_main_header(table: Any) -> bool:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    if not rows:
        return False
    header = [str(cell or "") for cell in rows[0]]
    changed = False
    for index, cell in enumerate(header):
        text = _norm(cell)
        if "预测" in text and "下季度" in text and header[index] != _MAIN_PREDICT_HEADER:
            header[index] = _MAIN_PREDICT_HEADER
            changed = True
    if not changed:
        return False
    rows[0] = header
    table.rows = rows
    blocks = getattr(table, "table_blocks", None) or []
    if blocks and isinstance(blocks[0], dict):
        block_rows = [list(row) for row in (blocks[0].get("rows") or [])]
        if block_rows:
            block_rows[0] = header
            blocks[0] = {**blocks[0], "rows": block_rows}
        table.table_blocks = blocks
    return True


# ---------------------------------------------------------------------------
# 通用“标签 + 两列数值”解析（经营表 / 流动性监测表）
# ---------------------------------------------------------------------------
def _parse_pairs(
    lines: Sequence[str], start: int
) -> tuple[list[list[str]], bool]:
    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    started = False

    def flush() -> None:
        nonlocal label, values
        if not label:
            values = []
            return
        text = "".join(label).strip()
        if not text:
            label, values = [], []
            return
        cells = list(values) + [""] * (2 - len(values))
        rows.append([text, cells[0], cells[1]])
        label, values = [], []

    for line in lines[start:]:
        head = _clean(line)
        if not head:
            continue
        if _is_stop(head):
            flush()
            return rows, True
        if not started:
            if _is_header_token(head) or _is_value(head):
                continue
            started = True
        if _is_value(head):
            values.append(head)
            if len(values) >= 2:
                flush()
            continue
        if values:
            flush()
        label.append(head)
    flush()
    return rows, False


def _operating_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        head = line.replace(" ", "")
        if head.endswith("主要经营指标"):
            return index
    return None


def _monitoring_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        if "流动性风险监测指标" in line.replace(" ", ""):
            return index
    return None


def _rebuild_operating(table: Any, doc: Any, hint: int) -> bool:
    page_no, lines, index = _find_page(doc, _operating_heading_index, hint)
    if page_no is None or index is None or lines is None:
        return False
    blocks: list[dict[str, Any]] = []
    current = page_no
    start = index + 1
    first = True
    while current <= doc.page_count:
        page_lines = lines if first else _page_lines(doc, current)
        if not first:
            start = 0
            if not page_lines:
                break
            head = page_lines[0]
            if _is_stop(head) or _is_meta_line(head) or _is_header_token(head):
                break
            if head.startswith("（") or re.match(r"^[一二三四五六七八九十]{1,3}\s*、", head):
                break
        rows, stopped = _parse_pairs(page_lines, start)
        if rows:
            blocks.append(
                {"page": current, "rows": [list(_OPERATING_HEADER), *rows]}
            )
        if stopped:
            break
        if not rows and not first:
            break
        first = False
        current += 1
    if not blocks:
        return False
    _apply_blocks(table, blocks, _OPERATING_HEADER)
    return True


def _rebuild_monitoring(table: Any, doc: Any, hint: int) -> bool:
    page_no, lines, index = _find_page(doc, _monitoring_heading_index, hint)
    if page_no is None or index is None or lines is None:
        return False
    body = list(lines[index + 1 :])
    rows, _ = _parse_pairs(body, 0)
    used_page = page_no
    # 2024 年标题与表头在页尾，10 行表体在下一页页首。
    if len(rows) < 2 and page_no < doc.page_count:
        next_rows, _ = _parse_pairs(_page_lines(doc, page_no + 1), 0)
        if len(next_rows) > len(rows):
            rows, used_page = next_rows, page_no + 1
    if not rows:
        return False
    block = {"page": used_page, "rows": [list(_LIQUIDITY_HEADER), *rows]}
    _apply_blocks(table, [block], _LIQUIDITY_HEADER)
    return True


# ---------------------------------------------------------------------------
# 实际资本表(S02) / 最低资本表(S05)：行次 + 标签 + 两列数值
# ---------------------------------------------------------------------------
def _capital_finder(needle: str) -> Callable[[Sequence[str]], int | None]:
    def finder(lines: Sequence[str]) -> int | None:
        for index, line in enumerate(lines):
            if line.replace(" ", "").endswith(needle):
                return index
        return None

    return finder


def _detect_capital_header(
    lines: Sequence[str], start: int, default: Sequence[str]
) -> list[str]:
    for line in lines[start : start + 12]:
        head = line.replace(" ", "")
        if "期末数" in head or "期初数" in head:
            return list(_CAPITAL_HEADER_PERIOD)
    return list(default)


def _capital_row(code: str, label: Sequence[str], values: Sequence[str]) -> list[str]:
    text = "".join(label).strip()
    cells = [str(value) for value in values]
    if len(cells) == 1:
        # 2023/2024 部分行两列都是“-”，文本层只保留一个。
        cells = [cells[0], cells[0]] if cells[0] == "-" else [cells[0], ""]
    cells = (cells + ["", ""])[:2]
    return [code, text, cells[0], cells[1]]


def _parse_capital_rows(lines: Sequence[str], start: int) -> list[list[str]]:
    rows: list[list[str]] = []
    code: str | None = None
    label: list[str] = []
    values: list[str] = []
    started = False

    for line in lines[start:]:
        head = _clean(line)
        if not head:
            continue
        if _is_stop(head):
            break
        if _is_row_no(head):
            if started and code is not None:
                rows.append(_capital_row(code, label, values))
            code, label, values = head, [], []
            started = True
            continue
        if not started:
            continue
        if _is_value(head):
            values.append(head)
            continue
        if values:
            # 上一行数值不完整就开始新标签：先落一行，避免丢数据。
            if code is not None:
                rows.append(_capital_row(code, label, values))
            code, label, values = None, [], []
            started = False
            continue
        label.append(head)
    if started and code is not None:
        rows.append(_capital_row(code, label, values))
    return rows


def _rebuild_capital(table: Any, doc: Any, hint: int, needle: str) -> bool:
    page_no, lines, index = _find_page(doc, _capital_finder(needle), hint)
    if page_no is None or index is None or lines is None:
        return False
    header = _detect_capital_header(lines, index + 1, _CAPITAL_HEADER)
    rows = _parse_capital_rows(lines, index + 1)
    if not rows:
        return False
    block = {"page": page_no, "rows": [list(header), *rows]}
    _apply_blocks(table, [block], header)
    return True


# ---------------------------------------------------------------------------
# 近三年（综合）投资收益率：文本行，不是表格
# ---------------------------------------------------------------------------
def _three_year_heading_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        if "近三年平均投资收益率" in line.replace(" ", ""):
            return index
    return None


def _parse_three_year_rows(lines: Sequence[str], start: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for label in _THREE_YEAR_LABELS:
        for offset, line in enumerate(lines[start:]):
            head = line.replace(" ", "")
            if not head.startswith(label):
                continue
            # 2023/2024：近三年平均投资收益率为5.26%（数值同一行）。
            match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", head)
            if match:
                rows.append([label, match.group(1) + "%"])
                break
            # 2025：标签单独一行，数值在下一行（表头已带“（%）”）。
            for follow in lines[start + offset + 1 : start + offset + 4]:
                value = _clean(follow)
                if _is_value(value):
                    rows.append([label, value if "%" in value else value + "%"])
                    break
            break
    return rows


def _build_three_year(doc: Any) -> Any | None:
    page_no, lines, index = _find_page(doc, _three_year_heading_index, 10)
    if page_no is None or index is None or lines is None:
        return None
    rows = _parse_three_year_rows(lines, index)
    if not rows:
        return None
    block = {"page": page_no, "rows": [list(_THREE_YEAR_HEADER), *rows]}
    return SimpleNamespace(
        table_id="THREE_YEAR_INVESTMENT_RETURN",
        table_name="近三年投资收益率",
        page=page_no,
        table_index=0,
        rows=_flatten([block], _THREE_YEAR_HEADER),
        table_blocks=[block],
        source_pages=[page_no],
        unit="%",
        strategy="鼎和文本重建",
        quality_score=0.0,
        evidence="近三年平均（综合）投资收益率（文本行重建）",
        profile_strategy_id="",
    )


def _rebuild_three_year(table: Any, doc: Any) -> bool:
    page_no, lines, index = _find_page(doc, _three_year_heading_index, 10)
    if page_no is None or index is None or lines is None:
        return False
    rows = _parse_three_year_rows(lines, index)
    if not rows:
        return False
    block = {"page": page_no, "rows": [list(_THREE_YEAR_HEADER), *rows]}
    _apply_blocks(table, [block], _THREE_YEAR_HEADER)
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
@register("dinghe")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """鼎和专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        rebuilt: list[Any] = list(tables)
        has_three_year = False
        for table in rebuilt:
            table_id = str(getattr(table, "table_id", "") or "")
            hint = _hint_page(table)
            if table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                _rebuild_operating(table, doc, hint)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_monitoring(table, doc, hint)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_capital(table, doc, hint, "实际资本表")
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_capital(table, doc, hint, "最低资本总表")
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                has_three_year = True
                _rebuild_three_year(table, doc)
            elif table_id == "SOLVENCY_MAIN":
                _fix_main_header(table)
        if not has_three_year:
            extra = _build_three_year(doc)
            if extra is not None:
                rebuilt.append(extra)
        return rebuilt
    finally:
        doc.close()
