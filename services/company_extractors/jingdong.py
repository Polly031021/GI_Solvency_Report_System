"""京东安联财产保险有限公司（京东安联）专属提取后处理。

版式特点（2022 Q4 – 2025 Q4，报告 28–30 页）：

* ``四、主要指标`` 一页内连续排布 4 张表（1 偿付能力充足率指标 / 2 流动性风险
  监管指标 / 3 流动性风险监测指标 / 4 经营指标），pdfplumber 的线框网格会把
  相邻表格并成一张，且金额列常被文字块拆成两列（``1,230, | 305,825.97``）。
* ``十、实际资本`` 表的章节标题写作 ``1. 实际资本明细``（没有"…明细表"字样），
  跨页时续页直接从 ``核心二级资本`` 开始，通用锚点会落到正文页。
* ``十一、最低资本`` 表标题同样只是 ``1. 最低资本明细``，且金额会被拆成
  ``438,574,87 | 5.81`` 这种跨列碎片。
* 2024 起偿付能力主表本身跨 p13→p14，主表被切成两张线框图。
* 2022 年没有独立的"近三年投资收益率"表。

因此这里统一按 PyMuPDF 的文本行序重建：京东安联的报告是**标签行 + 紧随若干
数值行**的稳定排版，逐行读取比线框网格可靠得多。所有重建都做内容校验，校验
不过就保留通用提取结果（不破坏已有可用数据）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator, Sequence

from . import register

_LOGGER = logging.getLogger(__name__)

_FOOTER_RE = re.compile(r"^第\s*\d+\s*页")
_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_DASH_TOKENS = frozenset({"——", "--", "—", "-", "－", "–", "/", "不适用"})

# 表头/分组标题：出现在数据行之间，必须丢弃
_HEADER_TOKENS = frozenset(
    {
        "项目",
        "指标名称",
        "本季度末",
        "上季度末",
        "下季度末",
        "下季度预测数",
        "本季度数",
        "上季度数",
        "本季度数（审计后）",
        "本年度累计",
        "本年度累计数",
        "本期数",
        "上期数",
        "数值",
    }
)
_GROUP_TOKENS = frozenset(
    {"基本指标", "效益类指标", "规模类指标", "品质类指标", "规模指标", "效益指标"}
)

# 章节序号后面必须紧跟中文，避免把数值 "5.18" / "2.35" 误判成下一节
_SECTION_NUMBER_RE = re.compile(r"^(\d{1,2})\s*[.．、]\s*(?=[\u4e00-\u9fff])")
_SECTION_END_RE = re.compile(r"^(?:五|5)\s*[、.．]\s*(?=[\u4e00-\u9fff])")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


_HEADER_NORMS = frozenset(_norm(item) for item in _HEADER_TOKENS)
_GROUP_NORMS = frozenset(_norm(item) for item in _GROUP_TOKENS)


def _is_number(text: str) -> bool:
    token = _norm(text)
    if not token:
        return False
    if token in _DASH_TOKENS:
        return True
    return bool(_NUMBER_RE.match(token))


def _split_numbers(text: str) -> list[str]:
    """一行里挤了多个数值（PDF 把两个单元格合成一行）时拆开。"""
    parts = str(text or "").split()
    if len(parts) < 2:
        return []
    if all(_NUMBER_RE.match(part) and _is_number(part) for part in parts):
        return parts
    return []


_FULL_PAREN_RE = re.compile(r"^[（(][^）)]{0,20}[）)]$")
_PAREN_RE = re.compile(r"[（(][^）)]{0,20}[）)]")


def _is_noise(text: str) -> bool:
    """纯括注行，如 ``（审计后）`` / ``（单位：人民币元）``。"""
    return bool(_FULL_PAREN_RE.match(_norm(text)))


def _header_key(text: str) -> str:
    return _norm(_PAREN_RE.sub("", str(text or "")))


def _iter_lines(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """整份 PDF → ``(物理页, 文本行)``，过滤页脚/单位注释/空行。"""
    import fitz

    lines: list[tuple[int, str]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index, page in enumerate(doc, start=1):
            for raw in page.get_text().splitlines():
                text = _squash(raw)
                if not text or _FOOTER_RE.match(text):
                    continue
                if text.startswith("（单位") or text.startswith("(单位"):
                    continue
                lines.append((index, text))
    return lines


def _find_marker(
    lines: Sequence[tuple[int, str]],
    predicate,
    anchor_page: int,
    *,
    min_page: int = 4,
) -> int:
    """在锚点页附近找章节标题行；优先取页号最接近且不晚于锚点+1 的那处。"""

    best = -1
    best_key: tuple[int, int] | None = None
    for index, (page, text) in enumerate(lines):
        if page < min_page or not predicate(text):
            continue
        distance = abs(page - anchor_page)
        if page > anchor_page + 1:
            distance += 1
        key = (distance, index)
        if best_key is None or key < best_key:
            best_key = key
            best = index
    return best


def _collect(
    lines: Sequence[tuple[int, str]],
    index: int,
    *,
    stop,
    n_values: int,
    keep_last: str = "",
) -> list[list[str]]:
    """从 ``index`` 起按"标签 + 最多 n_values 个数值行"重建行。"""

    rows: list[list[str]] = []
    total = len(lines)
    cursor = index
    while cursor < total:
        text = lines[cursor][1]
        if stop(text):
            break
        norm = _norm(text)
        if _header_key(text) in _HEADER_NORMS or norm in _GROUP_NORMS or _is_noise(text):
            cursor += 1
            continue
        if _is_number(text) or _split_numbers(text):
            cursor += 1
            continue
        values: list[str] = []
        following = cursor + 1
        while following < total and len(values) < n_values:
            candidate = lines[following][1]
            if stop(candidate):
                break
            if _is_number(candidate):
                values.append(candidate)
                following += 1
                continue
            merged = _split_numbers(candidate)
            if not merged:
                break
            values.extend(merged[: n_values - len(values)])
            following += 1
        rows.append([text, *values, *([""] * (n_values - len(values)))])
        if keep_last and norm == keep_last:
            break
        cursor = following
    return rows


def _has_all(rows: Sequence[Sequence[str]], labels: Sequence[str]) -> bool:
    """行标签里是否都出现了（允许带后缀，如"经营活动净现金流（年累计）"）。"""
    joined = "\n".join(_norm(row[0]) for row in rows if row)
    return all(_norm(label) in joined for label in labels)


def _sync(table: Any, rows: Sequence[Sequence[str]]) -> None:
    clean = [[str(cell) for cell in row] for row in rows]
    page = int(getattr(table, "page", 0) or 0) or 1
    table.rows = clean
    table.table_blocks = [{"page": page, "rows": [list(row) for row in clean]}]


def _hint_page(table: Any) -> int:
    pages = [int(page) for page in (getattr(table, "source_pages", []) or []) if page]
    if not pages:
        page = int(getattr(table, "page", 0) or 0)
        pages = [page] if page else [1]
    return pages[0]


# ------------------------------------------------------------------ 重建函数


def _heading(text: str, body: str) -> bool:
    """章节标题行，如 ``1. 实际资本明细`` / ``4. 经营指标``。"""
    return bool(re.match(rf"^\d{{1,2}}\s*[.．、]\s*{body}$", _norm(text)))


def _rebuild_main(table: Any, lines: Sequence[tuple[int, str]]) -> bool:
    start = _find_marker(
        lines, lambda t: _heading(t, "偿付能力充足率指标"), _hint_page(table)
    )
    if start < 0:
        return False

    def stop(text: str) -> bool:
        return "流动性风险监管指标" in text or "流动性风险监测指标" in text

    rows = _collect(lines, start + 1, stop=stop, n_values=3, keep_last="综合偿付能力充足率")
    if len(rows) < 15 or not _has_all(
        rows, ["认可资产", "认可负债", "实际资本", "最低资本", "综合偿付能力充足率"]
    ):
        return False
    header = ["指标名称", "本季度末", "上季度末", "下季度预测数"]
    _sync(table, [header, *rows])
    return True


def _rebuild_operating(table: Any, lines: Sequence[tuple[int, str]]) -> bool:
    start = _find_marker(
        lines,
        lambda t: _heading(t, "经营指标"),
        _hint_page(table),
    )
    if start < 0:
        return False

    def stop(text: str) -> bool:
        norm = _norm(text)
        if "近三年" in norm:
            return True
        return bool(_SECTION_END_RE.match(norm)) or "风险管理能力" in norm

    rows = _collect(lines, start + 1, stop=stop, n_values=2)
    if len(rows) < 10 or not _has_all(rows, ["保险业务收入", "净利润"]):
        return False
    header = ["指标名称", "本季度数", "本年度累计数"]
    _sync(table, [header, *rows])
    return True


def _rebuild_monitoring(table: Any, lines: Sequence[tuple[int, str]]) -> bool:
    start = _find_marker(
        lines, lambda t: "流动性风险监测指标" in t and _norm(t).startswith("3"), _hint_page(table)
    )
    if start < 0:
        return False

    def stop(text: str) -> bool:
        norm = _norm(text)
        return _heading(norm, "经营指标") or bool(_SECTION_END_RE.match(norm))

    rows = _collect(lines, start + 1, stop=stop, n_values=2)
    if len(rows) < 8 or not _has_all(rows, ["经营活动净现金流", "季均融资杠杆比例"]):
        return False
    header = ["项目", "本季度数", "上季度数"]
    _sync(table, [header, *rows])
    return True


def _rebuild_actual_capital(table: Any, lines: Sequence[tuple[int, str]]) -> bool:
    start = _find_marker(lines, lambda t: _heading(t, "实际资本明细"), _hint_page(table))
    if start < 0:
        return False

    def stop(text: str) -> bool:
        norm = _norm(text)
        if "认可资产明细" in norm or "认可负债明细" in norm:
            return True
        match = _SECTION_NUMBER_RE.match(norm)
        return bool(match and int(match.group(1)) >= 2)

    rows = _collect(lines, start + 1, stop=stop, n_values=2, keep_last="实际资本")
    if len(rows) < 5 or not _has_all(rows, ["实际资本", "核心一级资本"]):
        return False
    header = ["项目", "本季度末", "上季度末"]
    _sync(table, [header, *rows])
    return True


def _rebuild_minimum_capital(table: Any, lines: Sequence[tuple[int, str]]) -> bool:
    start = _find_marker(lines, lambda t: _heading(t, "最低资本明细"), _hint_page(table))
    if start < 0:
        return False

    def stop(text: str) -> bool:
        norm = _norm(text)
        if "保费及准备金风险最低资本明细" in norm or "巨灾风险最低资本明细" in norm:
            return True
        match = _SECTION_NUMBER_RE.match(norm)
        return bool(match and int(match.group(1)) >= 2)

    rows = _collect(lines, start + 1, stop=stop, n_values=2, keep_last="最低资本")
    if len(rows) < 8 or not _has_all(
        rows,
        ["保险风险最低资本合计", "市场风险最低资本合计", "控制风险最低资本", "最低资本"],
    ):
        return False
    header = ["项目", "本季度末", "上季度末"]
    _sync(table, [header, *rows])
    return True


def _rebuild_three_year(table: Any, lines: Sequence[tuple[int, str]]) -> bool:
    labels = ("近三年平均投资收益率", "近三年平均综合投资收益率")
    rows: list[list[str]] = []
    for label in labels:
        for index, (_, text) in enumerate(lines):
            if _norm(text) != _norm(label):
                continue
            for following in range(index + 1, min(index + 4, len(lines))):
                candidate = lines[following][1]
                if _is_number(candidate):
                    rows.append([label, candidate])
                    break
            break
    if len(rows) < 2:
        return False
    _sync(table, rows)
    return True


_REBUILDERS = {
    "SOLVENCY_MAIN": _rebuild_main,
    "NON_LIFE_OPERATING_METRICS": _rebuild_operating,
    "OPERATING_METRICS": _rebuild_operating,
    "ACTUAL_CAPITAL": _rebuild_actual_capital,
    "MINIMUM_CAPITAL": _rebuild_minimum_capital,
    "THREE_YEAR_INVESTMENT_RETURN": _rebuild_three_year,
    "LIQUIDITY_MONITORING": _rebuild_monitoring,
    "LIQUIDITY_RISK_MONITORING": _rebuild_monitoring,
}


@register("jingdong")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """京东安联专属后处理入口。

    单表重建失败只记录告警并保留该表的通用结果，不影响其他表；
    整体异常（PDF 打不开等）不捕获，交由调用方记入批量日志。
    """

    if not pdf_bytes:
        return list(tables)
    lines = _iter_lines(pdf_bytes)
    if not lines:
        return list(tables)
    rebuilt: list[Any] = list(tables)
    for table in rebuilt:
        table_id = str(getattr(table, "table_id", "") or "")
        rebuilder = _REBUILDERS.get(table_id)
        if rebuilder is None:
            continue
        try:
            rebuilder(table, lines)
        except Exception as exc:  # noqa: BLE001 - 单表失败不影响其他表
            _LOGGER.warning("京东安联 extractor 重建 %s 失败：%s", table_id, exc)
    return rebuilt
