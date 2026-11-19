"""泰山财险专属提取后处理：重建偿付能力主表、经营指标表与流动性表。

泰山财险 2022–2025 的偿付能力报告版式逐年一致，通用规则在三处失手：

1. 偿付能力主表：PDF 把「项目 / 本季度末数 / 上季度末数 / 基本情景下的
   下季度预测数」排成 4 列，但标签与数值常常只差 1–7pt 的基线偏移（不再
   同一行），通用表结构会把「认可资产」的预测数拆到独立列、把「核心偿付
   能力充足率」的标签与数值拆成三行，最终得到 5–8 列错位表。
2. 主要经营指标：页内区域裁剪从「保险服务收入」起，把表头行裁掉（2022），
   缺表头后期间只能按默认口径猜，本季度数与本年度累计数互相顶替；跨页
   拼接还会把「【第N页表格分块】」标记行留在表体里。
3. 流动性风险监管/监测指标：LCR1 落在上一页（主表页下半段），监管表跨页；
   监测表是「组名 / 项目 / 数值」三列，项目列固定为「指标值」，通用规则
   把「指标值」当成数值列，导致十项监测指标全部取不到值；LCR 标签被
   断行加空格后与指标库别名只有 0.06 分差，落入“歧义待确认”。

专属逻辑只重写 ``table.rows`` / ``table.table_blocks`` / ``table.source_pages``，
数据全部从 PDF 文本按标题、行标签与数值列横坐标重新解析，不写死年份和
页码，重复执行结果一致。任何一步解析失败都保留通用结果，不抛异常。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

import fitz

from . import register

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_SPACE_RE = re.compile(r"\s+")
_UNIT_SUFFIX_RE = re.compile(r"[（(][^（）()]{0,10}[）)]$")
_GROUP_RE = re.compile(r"^[一二三四五六七八九十]+、")
_LCR_RE = re.compile(r"^(LCR[123]).*?未来\s*(3|12)\s*个?\s*月")

_MAIN_HEADER = ["项目", "本季度末数", "上季度末数", "基本情景下的下季度预测数"]
_MAIN_LABELS = [
    "认可资产",
    "认可负债",
    "实际资本",
    "核心一级资本",
    "核心二级资本",
    "附属一级资本",
    "附属二级资本",
    "量化风险最低资本",
    "控制风险最低资本",
    "附加资本",
    "最低资本",
    "核心偿付能力溢额",
    "核心偿付能力充足率",
    "综合偿付能力溢额",
    "综合偿付能力充足率",
]
_MAIN_LABEL_SET = set(_MAIN_LABELS)
_MAIN_SKIP_WORDS = {
    "项目",
    "本季度末数",
    "上季度末数",
    "基本情景下的下季度预",
    "测数",
}
_OPER_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_LIQ_HEADER = ["指标名称", "本季度数", "上季度数"]
_REG_SKIP = {"指标名称", "本季度数", "上季度数", "项目", "监测指标", "数值"}
_BLOCK_MARK_RE = re.compile(r"^【第\d+页表格分块】$")
_ROWNO_RE = re.compile(r"\d+(?:\.\d+)*")
_ACT_HEADER = ["行次", "项目", "期末数", "期初数"]
_ACT_STOP = {"实际资本合计"}


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def _cell(value: Any) -> str:
    return _SPACE_RE.sub("", str(value or "")).strip()


def _is_number(value: Any) -> bool:
    text = str(value or "").strip()
    if text in {"-", "—", "–", "--"}:
        return True
    return bool(_NUMBER_RE.fullmatch(text))


def _is_value(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text not in {"-", "—", "–", "--"} and _is_number(text)


def _strip_unit(label: str) -> str:
    text = _cell(label)
    for _ in range(2):
        stripped = _UNIT_SUFFIX_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text


def _page_lines(doc: Any, page: int) -> list[str]:
    """取一页的文本行；去掉页首纯数字页码。"""
    lines = [
        line.strip()
        for line in doc[int(page) - 1].get_text("text").splitlines()
        if line.strip()
    ]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return lines


def _find_page(doc: Any, pages: Iterable[int], needles: Sequence[str]) -> int | None:
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


def _cluster(edges: Sequence[float], gap: float = 25.0) -> list[float]:
    """把一组右端坐标按间距聚成列，返回每列代表坐标（取簇内最大值）。"""
    ordered = sorted(float(edge) for edge in edges)
    clusters: list[list[float]] = []
    for edge in ordered:
        if clusters and edge - clusters[-1][-1] <= gap:
            clusters[-1].append(edge)
        else:
            clusters.append([edge])
    return [max(cluster) for cluster in clusters]


# ---------------------------------------------------------------------------
# 一、偿付能力充足率主表
# ---------------------------------------------------------------------------
def _word_lines(words: Sequence[Sequence[Any]], limit: float = 215.0) -> list[tuple[float, str]]:
    """把非数字词按基线聚成行，返回 (y, 文本)。"""
    items = [
        (float(word[1]), float(word[0]), float(word[2]), str(word[4]).strip())
        for word in words
        if str(word[4]).strip()
        and float(word[2]) <= limit
        and not _is_number(str(word[4]).strip())
        and str(word[4]).strip() not in _MAIN_SKIP_WORDS
    ]
    items.sort(key=lambda item: (item[0], item[1]))
    lines: list[list[Any]] = []
    for y0, x0, x1, text in items:
        if lines and abs(lines[-1][0] - y0) <= 2.5:
            lines[-1][2].append((x0, text))
            lines[-1][0] = min(lines[-1][0], y0)
        else:
            lines.append([y0, y0, [(x0, text)]])
    result: list[tuple[float, str]] = []
    for y0, _y1, parts in lines:
        parts.sort()
        result.append((y0, "".join(part[1] for part in parts)))
    return result


def _rebuild_main(doc: Any, table: Any) -> None:
    page = int(getattr(table, "page", 1) or 1)
    candidates = [p for p in (page - 1, page, page + 1) if 1 <= p <= doc.page_count]
    target = _find_page(doc, candidates, ["认可资产", "本季度末数"])
    if target is None:
        target = _find_page(doc, candidates, ["认可资产"])
    if target is None:
        return
    words = list(doc[target - 1].get_text("words"))
    header_y = min(
        (
            float(word[1])
            for word in words
            if str(word[4]).strip() == "本季度末数"
        ),
        default=None,
    )
    if header_y is None:
        return

    labels: list[tuple[float, str]] = []
    for y0, text in _word_lines(words):
        canonical = _strip_unit(text)
        if canonical in _MAIN_LABEL_SET and y0 >= header_y:
            labels.append((y0, canonical))
    if len(labels) < 10:
        return
    labels.sort()
    last_y = labels[-1][0]

    numbers = [
        (float(word[1]), float(word[2]), str(word[4]).strip())
        for word in words
        if _is_value(str(word[4]).strip())
        and float(word[0]) >= 200.0
        and header_y < float(word[1]) < last_y + 14.0
    ]
    if not numbers:
        return
    columns = _cluster([x1 for _x0, x1, _text in numbers])
    if len(columns) < 2:
        return

    grid: dict[str, dict[int, str]] = {label: {} for _y, label in labels}
    for y0, x1, text in numbers:
        nearest = min(labels, key=lambda item: abs(item[0] - y0))
        if abs(nearest[0] - y0) > 14.0:
            continue
        column = min(range(len(columns)), key=lambda index: abs(columns[index] - x1))
        grid.setdefault(nearest[1], {}).setdefault(column, text)

    rows: list[list[str]] = [list(_MAIN_HEADER)]
    filled = 0
    for label in _MAIN_LABELS:
        cells = grid.get(label) or {}
        row = [
            label,
            cells.get(0, ""),
            cells.get(1, ""),
            cells.get(2, ""),
        ]
        if any(row[1:]):
            filled += 1
        rows.append(row)
    if filled < 10:
        return
    _sync(table, rows, [{"page": target, "rows": rows}])
    table.strategy = "专属提取·泰山·偿付能力主表按列坐标重建"


# ---------------------------------------------------------------------------
# 二、主要经营指标
# ---------------------------------------------------------------------------
def _clean_oper_row(row: Sequence[Any]) -> list[str] | None:
    cells = [str(cell or "").strip() for cell in row]
    if not cells:
        return None
    first = _cell(cells[0])
    if _BLOCK_MARK_RE.match(first) or first.startswith("【第") and "表格分块" in first:
        return None
    cells[0] = first
    return cells


def _is_oper_header(row: Sequence[str]) -> bool:
    text = "".join(_cell(cell) for cell in row[:3])
    return "指标名称" in text and "本季度" in text


def _fix_operating(table: Any) -> None:
    blocks_in = [
        block
        for block in (getattr(table, "table_blocks", None) or [])
        if block.get("rows")
    ]
    if not blocks_in:
        rows = [list(row) for row in (getattr(table, "rows", None) or [])]
        if not rows:
            return
        blocks_in = [{"page": int(getattr(table, "page", 1) or 1), "rows": rows}]

    blocks: list[dict[str, Any]] = []
    for block in blocks_in:
        cleaned = [
            cleaned
            for cleaned in (_clean_oper_row(row) for row in block["rows"])
            if cleaned is not None
        ]
        if not cleaned:
            continue
        blocks.append({"page": int(block.get("page") or 1), "rows": cleaned})
    if not blocks:
        return

    first_rows = blocks[0]["rows"]
    if not _is_oper_header(first_rows[0]):
        while first_rows and not any(_is_value(cell) for cell in first_rows[0][1:]):
            first_rows.pop(0)
        first_rows.insert(0, list(_OPER_HEADER))

    flat: list[list[str]] = []
    for block in blocks:
        kept = [
            row
            for row in block["rows"]
            if _is_oper_header(row) or any(_is_value(cell) for cell in row[1:])
        ]
        block["rows"] = kept
        flat.extend(kept)
    data_rows = [row for row in flat if not _is_oper_header(row)]
    if len(data_rows) < 20:
        return
    _sync(table, flat, blocks)
    table.strategy = "专属提取·泰山·经营指标补表头与分块清理"


# ---------------------------------------------------------------------------
# 三、最低资本表：险种保费/准备金二维子表拆行
# ---------------------------------------------------------------------------
def _fix_minimum(table: Any) -> None:
    blocks_in = [
        block
        for block in (getattr(table, "table_blocks", None) or [])
        if block.get("rows")
    ]
    if not blocks_in:
        rows = [list(row) for row in (getattr(table, "rows", None) or [])]
        if not rows:
            return
        blocks_in = [{"page": int(getattr(table, "page", 1) or 1), "rows": rows}]

    blocks: list[dict[str, Any]] = []
    split_count = 0
    for block in blocks_in:
        rows = [list(row) for row in block["rows"]]
        out: list[list[Any]] = []
        index = 0
        while index < len(rows):
            row = rows[index]
            head = _cell(row[0]) if row else ""
            if head == "类型" and len(row) >= 5:
                cursor = index + 1
                if cursor < len(rows) and "保费风险" in "".join(
                    _cell(cell) for cell in rows[cursor]
                ):
                    cursor += 1
                while cursor < len(rows):
                    item = rows[cursor]
                    name = _cell(item[0]) if item else ""
                    if not name or _BLOCK_MARK_RE.match(name) or name == "指标名称":
                        break
                    split_count += 1
                    cursor += 1
                index = cursor
                continue
            out.append(row)
            index += 1
        blocks.append({"page": int(block.get("page") or 1), "rows": out})

    if not split_count:
        return
    flat = [row for block in blocks for row in block["rows"]]
    _sync(table, flat, blocks)
    table.strategy = (
        "专属提取·泰山·最低资本（剔除险种级保费/准备金二维子表"
        f"{split_count} 行，避免同期间重复取值）"
    )


# ---------------------------------------------------------------------------
# 二、实际资本表：文本重建（补回被通用提取丢掉的“行次”列）
# ---------------------------------------------------------------------------
def _parse_actual_page(lines: Sequence[str]) -> list[list[str]]:
    """把一页“行次/项目/期末数/期初数”文本解析成行；缺行次的续页同样适用。"""
    start = 0
    for index, line in enumerate(lines):
        if line in {"期初数", "期初余额"}:
            start = index + 1
            break
    rows: list[list[str]] = []
    rowno = ""
    label: list[str] = []
    values: list[str] = []
    for line in lines[start:]:
        if not label and not values and _ROWNO_RE.fullmatch(line):
            rowno = line
            continue
        if _is_value(line):
            values.append(line)
            if len(values) >= 2:
                rows.append([rowno, "".join(label), values[0], values[1]])
                rowno, label, values = "", [], []
            continue
        label.append(line)
    if label and values:
        padded = [*values, ""][:2]
        rows.append([rowno, "".join(label), padded[0], padded[1]])
    return rows


def _refine_capital_sections(blocks: Sequence[dict[str, Any]]) -> None:
    """把“减：超限额应扣除的部分”等同名行补上所在节名。

    指标库对核心二级 / 附属一级 / 附属二级资本下的同名明细分别编码（如
    “附属一级资本超限额应扣除部分”），而标准化去行次后三条同名行无法区分；
    这里按行次首段（2/3/4）判断所属节，只在节名可用时补全，其余保持原样。
    """
    section = ""
    for block in blocks:
        for row in block["rows"]:
            rowno = str(row[0] or "").strip()
            label = str(row[1] or "").strip()
            if not rowno:
                continue
            if rowno.isdigit():
                section = label
                continue
            if not section or "." not in rowno:
                continue
            if label == "减：超限额应扣除的部分":
                row[1] = f"{section}超限额应扣除部分"
            elif label.startswith("递延所得税资产"):
                row[1] = f"{section}-{label}"


def _rebuild_actual_capital(doc: Any, table: Any) -> None:
    pages = [int(page) for page in (getattr(table, "source_pages", None) or []) if page]
    if not pages:
        pages = [int(getattr(table, "page", 0) or 0)]
    pages = [page for page in pages if 1 <= page <= doc.page_count][:4]
    if len(pages) < 2:
        base = pages[0] if pages else 0
        pages = [base, base + 1] if base else []
    pages = [page for page in pages if 1 <= page <= doc.page_count]
    if not pages:
        return

    blocks: list[dict[str, Any]] = []
    for position, page in enumerate(pages):
        lines = _page_lines(doc, page)
        if position == 0 and ("行次" not in lines or "期末数" not in lines):
            return
        rows = _parse_actual_page(lines)
        if not rows:
            continue
        blocks.append({"page": page, "rows": rows})
    if not blocks:
        return

    labels = [row[1] for block in blocks for row in block["rows"]]
    if len(labels) < 20 or "核心一级资本" not in labels:
        return
    if not any(label in _ACT_STOP for label in labels):
        return

    _refine_capital_sections(blocks)
    blocks[0]["rows"] = [list(_ACT_HEADER), *blocks[0]["rows"]]
    flat = [row for block in blocks for row in block["rows"]]
    _sync(table, flat, blocks)
    table.strategy = "专属提取·泰山·实际资本表文本重建（补回行次列）"


# ---------------------------------------------------------------------------
# 三、流动性风险监管指标 + 监测指标
# ---------------------------------------------------------------------------
def _lcr_label(text: str) -> str:
    joined = _cell(text)
    match = _LCR_RE.match(joined)
    if match:
        return f"{match.group(1)}未来{match.group(2)}个月"
    return joined


def _parse_regulatory(lines: Sequence[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    label: list[str] = []
    values: list[str] = []
    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        if text.startswith("（三）") or text.startswith("备注"):
            break
        if text in _REG_SKIP or _PAGE_NO_RE.fullmatch(text):
            continue
        if "公司名称" in text or "单位：" in text or "流动性风险监管指标" in text:
            continue
        if _is_value(text):
            if not label:
                continue
            values.append(text)
            if len(values) == 2:
                rows.append([_lcr_label("".join(label)), values[0], values[1]])
                label, values = [], []
            continue
        if values:
            label, values = [text], []
        else:
            label.append(text)
    return [
        row
        for row in rows
        if row[0].startswith("LCR") or "回溯" in row[0] or row[0] == "净现金流"
    ]


def _parse_monitoring(lines: Sequence[str]) -> list[list[str]]:
    body = list(lines)
    start = next((i for i, line in enumerate(body) if line.strip() == "监测指标"), None)
    if start is None:
        start = next(
            (i for i, line in enumerate(body) if "流动性风险监测指标" in line),
            None,
        )
    if start is not None:
        body = body[start + 1 :]

    rows: list[list[str]] = []
    group = ""
    pending = False
    wraps = 0
    for raw in body:
        text = raw.strip()
        if not text:
            continue
        if (
            text.startswith("备注")
            or text.startswith("（四）")
            or text.startswith("近三年平均投资收益率")
        ):
            break
        if _GROUP_RE.match(text):
            group, pending, wraps = text, True, 0
            continue
        if not group:
            continue
        if text == "指标值":
            continue
        if _is_value(text):
            if not pending:
                continue
            rows.append([_cell(group), text, ""])
            group, pending = "", False
            if len(rows) >= 10:
                break
            continue
        if pending and wraps < 1 and len(_cell(group)) <= 40:
            group += text
            wraps += 1
    return rows


def _rebuild_liquidity(doc: Any, table: Any) -> None:
    page = int(getattr(table, "page", 1) or 1)
    span = [
        p
        for p in range(max(1, page - 2), min(doc.page_count, page + 3) + 1)
    ]
    regulatory: list[list[str]] = []
    reg_page = _find_page(doc, span, ["LCR1"])
    if reg_page is not None:
        for offset in (0, 1):
            target = reg_page + offset
            if target > doc.page_count:
                break
            regulatory.extend(_parse_regulatory(_page_lines(doc, target)))
            if len(regulatory) >= 8:
                break
    monitoring: list[list[str]] = []
    mon_page = _find_page(doc, span, ["流动性风险监测指标"])
    if mon_page is None:
        mon_page = _find_page(doc, span, ["监测指标", "指标值"])
    if mon_page is not None:
        for offset in (0, 1):
            target = mon_page + offset
            if target > doc.page_count:
                break
            monitoring.extend(_parse_monitoring(_page_lines(doc, target)))
            if len(monitoring) >= 10:
                break

    if len(regulatory) < 6 or len(monitoring) < 8:
        return

    reg_rows = [list(_LIQ_HEADER), *regulatory]
    mon_rows = monitoring
    blocks = [
        {"page": reg_page, "rows": reg_rows},
        {"page": mon_page, "rows": mon_rows},
    ]
    flat = [*reg_rows, *mon_rows]
    _sync(table, flat, blocks)
    table.strategy = "专属提取·泰山·流动性监管+监测指标文本重建"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
@register("taishan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """按 PDF 文本重建三处通用规则解析不了的表（幂等，只改 rows/table_blocks）。"""
    if not tables or not pdf_bytes:
        return tables
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "").strip()
            if table_id == "SOLVENCY_MAIN":
                _rebuild_main(doc, table)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_actual_capital(doc, table)
            elif table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                _fix_operating(table)
            elif table_id == "MINIMUM_CAPITAL":
                _fix_minimum(table)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(doc, table)
    finally:
        doc.close()
    return tables
