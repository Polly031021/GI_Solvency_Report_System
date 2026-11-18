"""太平财险专属提取后处理：按页内线框网格重建 6 张目标表。

太平财险 2022–2025 的报告版式高度一致，但一页通常并存 2–4 张表
（偿付能力主表 / 流动性风险监管指标 / 流动性风险监测指标 / 主要经营指标）。
Profile 的 ``start_item_terms``/``end_item_terms`` 区域定位会把目标表裁短：
最低资本只剩前 3 行、实际资本截断在“银保监会规定的其他调整项目”、
主表缺表头。通用版式规则拿到的是裁短后的 rows，但 pdfplumber 的页内线框
网格本身是完整的，因此这里不再信任组装后的 rows，而是：

1. 按 ``table.source_pages``（±1 页）定位目标表所在页面；
2. 用 line 策略取整页线框网格（一页可能返回多张表）；
3. 按表头结构 / 行标签把属于该表的表块按页序拼回整张表；
4. 同步写回 ``table.rows`` 与 ``table.table_blocks``（STEP3 优先读 blocks）。

不写死年份和页码，重复执行结果一致。
"""

from __future__ import annotations

import io
import re
from typing import Any, Iterable, Sequence

import pdfplumber

from . import register

# 与 Profile 的“线框识别”策略保持一致（config/extraction/tables.yaml）。
_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 4,
    "join_tolerance": 4,
    "intersection_tolerance": 5,
}

_ROW_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_SPACE_RE = re.compile(r"\s+")

_MAIN_CONTINUATION_LABELS = ("核心偿付能力溢额", "综合偿付能力溢额")
_MAIN_HEADER_TERMS = ("期末数", "期初数")

# 流动性风险监测指标的固定行标签（四年稳定）；主表/监管指标表不含这些词。
_MONITOR_LABELS = (
    "经营活动净现金流",
    "百元保费",
    "特定业务现金流支出占比",
    "规模保费同比增速",
    "现金及流动性管理工具占比",
    "融资杠杆比例",
    "应收款项占比",
    "持有关联方资产占比",
    "AA级",
    "持股比例大于5%",
    "综合退保率",
    "退保率",
)

_COMPANY_INFO_LABELS = ("公司类型", "成立日期", "省级分支机构数量")


def _cell(value: Any) -> str:
    """单元格转字符串：去掉 pdfplumber 折行插入的换行和首尾空白。"""
    text = str(value or "")
    text = text.replace("\r", "").replace("\n", "")
    return text.strip()


def _norm(value: Any) -> str:
    return _SPACE_RE.sub("", _cell(value))


def _grid_width(rows: Sequence[Sequence[str]]) -> int:
    return max((len(row) for row in rows), default=0)


def _is_header_row(row: Sequence[str]) -> bool:
    """表头行：含“指标名称”或“行次/项目”。"""
    cells = [_norm(cell) for cell in row]
    if "指标名称" in cells:
        return True
    return "行次" in cells and "项目" in cells


def _header_has(rows: Sequence[Sequence[str]], term: str) -> bool:
    if not rows:
        return False
    return any(term in _norm(cell) for cell in rows[0])


def _all_rows_are_row_numbered(rows: Sequence[Sequence[str]]) -> bool:
    if not rows or _grid_width(rows) != 4:
        return False
    for row in rows:
        if len(row) < 3:
            return False
        if not _ROW_NUMBER_RE.match(_norm(row[0])):
            return False
    return True


def _page_grids(pdf: Any, page_number: int, cache: dict[int, list[list[list[str]]]]) -> list[list[list[str]]]:
    """取某物理页的全部线框网格（同一页只解析一次）。"""
    if page_number in cache:
        return cache[page_number]
    if page_number < 1 or page_number > len(pdf.pages):
        cache[page_number] = []
        return []
    grids: list[list[list[str]]] = []
    for rows in pdf.pages[page_number - 1].extract_tables(_TABLE_SETTINGS):
        cleaned = [[_cell(cell) for cell in row] for row in rows if row]
        if cleaned and _grid_width(cleaned) > 1:
            grids.append(cleaned)
    cache[page_number] = grids
    return grids


def _table_frames(
    table: Any,
    pdf: Any,
    cache: dict[int, list[list[list[str]]]],
) -> list[tuple[int, int, list[list[str]]]]:
    """目标表页面窗口内的表块，按（物理页，页内顺序）排序。"""
    pages: Iterable[Any] = getattr(table, "source_pages", None) or [
        getattr(table, "page", 1)
    ]
    page_set = sorted({int(page) for page in pages if isinstance(page, int)})
    if not page_set:
        return []
    window = sorted(
        {
            neighbor
            for page in page_set
            for neighbor in (page - 1, page, page + 1)
            if 1 <= neighbor <= len(pdf.pages)
        }
    )
    frames: list[tuple[int, int, list[list[str]]]] = []
    for page in window:
        for index, rows in enumerate(_page_grids(pdf, page, cache)):
            frames.append((page, index, rows))
    return frames


def _find_frame(
    frames: Sequence[tuple[int, int, list[list[str]]]],
    predicate,
) -> int:
    for position, (_, _, rows) in enumerate(frames):
        if predicate(rows):
            return position
    return -1


def _rebuild_solvency_main(frames: Sequence[tuple[int, int, list[list[str]]]]) -> list[list[str]] | None:
    """主表：4 列表头（期末数/期初数/下季度预测数）+ 跨页无表头续段。"""
    start = _find_frame(
        frames,
        lambda rows: len(rows) > 1
        and _grid_width(rows) == 4
        and _is_header_row(rows[0])
        and all(_header_has(rows, term) for term in _MAIN_HEADER_TERMS)
        and any("认可资产" == _norm(row[0]) for row in rows[1:]),
    )
    if start < 0:
        return None
    page, index, base = frames[start]
    rows = [list(row) for row in base]
    for later_page, later_index, later in frames[start + 1 :]:
        if (later_page, later_index) <= (page, index):
            continue
        if _grid_width(later) != 4 or _is_header_row(later[0]):
            break
        first = _norm(later[0][0])
        if not first.startswith(_MAIN_CONTINUATION_LABELS):
            break
        rows.extend(list(row) for row in later)
        if any("综合偿付能力充足率" in _norm(row[0]) for row in later):
            break
    return rows


def _rebuild_operating(frames: Sequence[tuple[int, int, list[list[str]]]]) -> list[list[str]] | None:
    """经营表：本季度（末）/本年累计（末）表头 + 跨页无表头续段。"""
    start = _find_frame(
        frames,
        lambda rows: len(rows) > 1
        and _is_header_row(rows[0])
        and _header_has(rows, "本年累计"),
    )
    if start < 0:
        return None
    page, index, base = frames[start]
    rows = [list(row) for row in base]
    for later_page, later_index, later in frames[start + 1 :]:
        if (later_page, later_index) <= (page, index):
            continue
        if _grid_width(later) != 3:
            break
        labels = [_norm(row[0]) for row in later if row]
        if any(label.startswith(_COMPANY_INFO_LABELS) for label in labels):
            break
        if _is_header_row(later[0]):
            break
        rows.extend(list(row) for row in later)
    return rows


def _rebuild_actual_capital(frames: Sequence[tuple[int, int, list[list[str]]]]) -> list[list[str]] | None:
    """实际资本明细表：行次|项目|本季度数|上季度数，跨页续段无表头。"""
    start = _find_frame(
        frames,
        lambda rows: len(rows) > 1
        and _grid_width(rows) == 4
        and _is_header_row(rows[0])
        and _header_has(rows, "行次")
        and _header_has(rows, "项目"),
    )
    if start < 0:
        return None
    page, index, base = frames[start]
    rows = [list(row) for row in base]
    finished = any("实际资本合计" in _norm(row[1]) for row in base[1:] if len(row) > 1)
    for later_page, later_index, later in frames[start + 1 :]:
        if (later_page, later_index) <= (page, index) or finished:
            continue
        if not _all_rows_are_row_numbered(later):
            break
        rows.extend(list(row) for row in later)
        if any("实际资本合计" in _norm(row[1]) for row in later if len(row) > 1):
            finished = True
    return rows


def _rebuild_minimum_capital(frames: Sequence[tuple[int, int, list[list[str]]]]) -> list[list[str]] | None:
    """最低资本表：单页 3 列，表头 指标名称|本季度数|上季度数，含最低资本合计行。"""
    best: list[list[str]] | None = None
    for _, _, rows in frames:
        if len(rows) < 10 or _grid_width(rows) != 3 or not _is_header_row(rows[0]):
            continue
        labels = [_norm(row[0]) for row in rows]
        if "最低资本" not in labels:
            continue
        if not any("量化风险最低资本" in label for label in labels):
            continue
        if best is None or len(rows) > len(best):
            best = [list(row) for row in rows]
    return best


def _rebuild_three_year(frames: Sequence[tuple[int, int, list[list[str]]]]) -> list[list[str]] | None:
    """近三年投资收益率：2 列表，行标签含“近三年平均投资收益率”。"""
    for _, _, rows in frames:
        if not any("近三年平均投资收益率" in _norm(row[0]) for row in rows):
            continue
        if _is_header_row(rows[0]):
            return [list(row) for row in rows]
        return [["指标名称", "本季度数"], *[list(row) for row in rows]]
    return None


def _looks_like_monitoring(rows: Sequence[Sequence[str]]) -> bool:
    return any(
        any(label in _norm(row[0]) for label in _MONITOR_LABELS)
        for row in rows
        if row and str(row[0]).strip()
    )


def _rebuild_liquidity(frames: Sequence[tuple[int, int, list[list[str]]]]) -> list[list[str]] | None:
    """流动性监测指标：3 列表（指标名称|本季度数|上季度数），排除 LCR 监管指标表。"""
    start = _find_frame(
        frames,
        lambda rows: len(rows) > 1
        and _grid_width(rows) == 3
        and _is_header_row(rows[0])
        and _header_has(rows, "本季度")
        and _header_has(rows, "上季度")
        and not _header_has(rows, "本年累计")
        and _looks_like_monitoring(rows[1:]),
    )
    if start < 0:
        return None
    page, index, base = frames[start]
    rows = [list(row) for row in base]
    for later_page, later_index, later in frames[start + 1 :]:
        if (later_page, later_index) <= (page, index):
            continue
        if _grid_width(later) != 3:
            break
        if _header_has(later, "本年累计"):
            break
        if _is_header_row(later[0]) and not _looks_like_monitoring(later):
            break
        rows.extend(list(row) for row in later)
    return rows


_REBUILDERS = {
    "SOLVENCY_MAIN": _rebuild_solvency_main,
    "NON_LIFE_OPERATING_METRICS": _rebuild_operating,
    "ACTUAL_CAPITAL": _rebuild_actual_capital,
    "MINIMUM_CAPITAL": _rebuild_minimum_capital,
    "THREE_YEAR_INVESTMENT_RETURN": _rebuild_three_year,
    "LIQUIDITY_MONITORING": _rebuild_liquidity,
}


def _sync_blocks(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


@register("taiping")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """太平财险专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    if not tables:
        return tables
    cache: dict[int, list[list[list[str]]]] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            rebuild = _REBUILDERS.get(table_id)
            if rebuild is None:
                continue
            frames = _table_frames(table, pdf, cache)
            rows = rebuild(frames)
            if not rows:
                continue
            page = int(getattr(table, "page", 1) or 1)
            _sync_blocks(table, rows, page)
            table.source_grid_raw = [list(row) for row in rows]
    return tables
