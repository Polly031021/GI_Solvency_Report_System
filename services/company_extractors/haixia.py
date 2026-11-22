"""海峡金桥财产保险股份有限公司专属提取后处理（2022-2025 版式）。

海峡金桥四年 PDF 的主体表都没有可用的线框，通用网格识别会出三类问题：

1. ``SOLVENCY_MAIN``：2022/2025 整表识别后表头丢失；2023 第 9 页取到的是
   “薪酬区间/董事人数”表、第 10 页整体左移一列且从“核心一级资本”起，
   认可资产/认可负债/实际资本全部丢失；2024 第 9 页前半段混入行政处罚正文，
   表头与数值被拆成多列。正文文本层（pdfplumber）则是干净的
   “指标名称 本季度数 上季度数 下季度预测数” + 15 行“标签 + 3 个金额”。
2. ``NON_LIFE_OPERATING_METRICS``：Profile 默认终止词“综合成本率”把表截到
   效益类指标第一行（2023 实测只剩 13 行）；2022/2025 的险种与渠道段落在
   第二页，网格识别后又跟着分块伪行与 SARMRA 正文。
3. ``MINIMUM_CAPITAL``：主表有完整线框，但跨页后通用流程只留下 3-8 行；
   正文重建会丢跨页行，故用 pdfplumber ``find_tables`` 取 4 列主表并拼接
   下一页的 3.3/3.4/4 行。
4. ``LIQUIDITY_MONITORING``：表尾跟着第 20 页鉴证表；2022/2023 的 LCR 标签
   把序号写在“未来 N 个月”之后（“流动性覆盖率LCR （未来3个月） 1”），
   与标准库“LCR1未来3个月”对不上，只有 LCR3_12M 靠模糊匹配命中。

只改 ``table.rows`` 与 ``table.table_blocks``（两者同步），不写死年份/页码；
每个重建函数只依赖 PDF 文本/线框、不读取旧 rows，因此重复执行结果一致（幂等）；
重建失败（行数明显不足）时返回 None，保留通用识别结果。
"""

from __future__ import annotations

import io
import re
from typing import Any, Callable, Sequence

from . import register

# 金额/百分比/占位符：允许千分位、负号、括号负数、"—/-" 占位。
_AMOUNT = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
_DASH = r"(?:-{1,2}|—|–|－|——)"
_CELL = rf"(?:{_AMOUNT}|{_DASH})"
# 标签里若吃到金额（例如多一列“增减变动”被回退进标签），说明切分错位。
_BAD_LABEL_TAIL = re.compile(
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?%|%)$"
)
_ROW3_RE = re.compile(
    rf"^(?P<label>.+?)\s+(?P<a>{_CELL})\s+(?P<b>{_CELL})\s+(?P<c>{_CELL})$"
)
_ROW2_RE = re.compile(rf"^(?P<label>.+?)\s+(?P<a>{_CELL})\s+(?P<b>{_CELL})$")
_ROW2C_RE = re.compile(
    rf"^(?P<label>.+?)\s+(?P<a>{_CELL})\s+(?P<b>{_CELL})\s+(?P<c>{_CELL})$"
)
_VALUES2_RE = re.compile(rf"^(?P<a>{_CELL})\s+(?P<b>{_CELL})$")
_VALUES_RE = re.compile(rf"^(?P<a>{_CELL})\s+(?P<b>{_CELL})(?:\s+{_CELL})?$")
_LCR_DIGIT_RE = re.compile(r"^[123]$")
_LCR_RE = re.compile(
    r"^流动性覆盖率\s*LCR\s*([123])?\s*[（(]\s*未来\s*(\d+)\s*个月\s*[）)]$"
)
_ROW_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")

_MAIN_HEADER = ("指标名称", "本季度数", "上季度数", "下季度预测数")
_MAIN_STOP_RE = re.compile(r"^(?:注[：:]|（二）|\(二\)|特别说明)")
_OPER_HEADER = ("指标名称", "本季度数", "本年累计数")
_OPER_STOP_RE = re.compile(
    r"^(?:四、风险管理能力|（四）近三年|\(四\)近三年|（四）风险管理|"
    r"注[：:]|六、偿付能力风险管理|（五）)"
)
_LIQ_HEADER = ("指标名称", "本季度数", "上季度数")

PageText = Callable[[int], str]


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


def _sync_blocks(
    table: Any,
    rows: list[list[str]],
    pages: Sequence[int],
    unit: str = "",
) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = rows
    page = int(pages[0]) if pages else 1
    block: dict[str, Any] = {"page": page, "rows": [list(row) for row in rows]}
    if unit:
        block["unit"] = unit
    table.table_blocks = [block]


def _lines(page_text: PageText, pages: Sequence[int]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        lines.extend(page_text(page).splitlines())
    return [line.strip() for line in lines]


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", "", label).replace("\n", "")


def _label_ok(label: str) -> bool:
    """拒绝“标签里还带着金额”的错位行，避免多出来的列污染标签/数值。"""
    if not label:
        return False
    return not _BAD_LABEL_TAIL.search(label)


def _row_number(cell: str) -> str:
    match = _ROW_NUMBER_RE.match(cell.strip())
    return match.group(0) if match else ""


# --------------------------------------------------------------------------- #
# SOLVENCY_MAIN
# --------------------------------------------------------------------------- #
def _rebuild_main(page_text: PageText, pages: Sequence[int]) -> list[list[str]] | None:
    rows: list[list[str]] = [list(_MAIN_HEADER)]
    started = False
    for line in _lines(page_text, pages):
        if not line:
            continue
        if not started:
            if line.startswith("指标名称") and "本季度数" in line and "下季度预测数" in line:
                started = True
            continue
        if _MAIN_STOP_RE.match(line):
            break
        match = _ROW3_RE.match(line)
        if not match:
            continue
        label = _clean_label(match.group("label"))
        if not _label_ok(label):
            continue
        rows.append([label, match.group("a"), match.group("b"), match.group("c")])
    if len(rows) < 12:  # 四年主表都是 15 行数据；缺到 12 行以下说明没对上版式
        return None
    return rows


# --------------------------------------------------------------------------- #
# NON_LIFE_OPERATING_METRICS
# --------------------------------------------------------------------------- #
def _rebuild_operating(page_text: PageText, pages: Sequence[int]) -> list[list[str]] | None:
    rows: list[list[str]] = [list(_OPER_HEADER)]
    started = False
    pending = ""
    for line in _lines(page_text, pages):
        if not line:
            continue
        if not started:
            if line.startswith("指标名称") and "本年累计数" in line:
                started = True
            continue
        if _OPER_STOP_RE.match(line):
            break
        match = _ROW2_RE.match(line)
        if match:
            label = _clean_label(match.group("label"))
            if not _label_ok(label):
                continue
            # pending 是“分组标题/被换行拆开的标签”：本行自带标签和金额时不并回。
            pending = ""
            rows.append([label, match.group("a"), match.group("b")])
            continue
        values = _VALUES2_RE.match(line)
        if values and pending:
            rows.append([pending, values.group("a"), values.group("b")])
            pending = ""
            continue
        if len(line) <= 32 and not _BAD_LABEL_TAIL.search(line):
            pending = _clean_label(line)
    if len(rows) < 20:  # 经营表四个年份都不少于 24 行；分组标题不计入
        return None
    return rows


# --------------------------------------------------------------------------- #
# MINIMUM_CAPITAL
# --------------------------------------------------------------------------- #
def _normalize_cells(row: Sequence[Any]) -> list[str]:
    cells = []
    for cell in row:
        text = "" if cell is None else str(cell)
        cells.append(text.replace("\n", "").strip())
    return cells


def _rebuild_minimum(pdf: Any, pages: Sequence[int]) -> list[list[str]] | None:
    chunks: list[list[list[str]]] = []
    for page in pages:
        try:
            found = pdf.pages[int(page) - 1].find_tables()
        except (IndexError, ValueError):
            continue
        for table in found:
            try:
                data = table.extract()
            except Exception:  # 单个格子解析失败不影响其余表
                continue
            if not data:
                continue
            cells = [_normalize_cells(row) for row in data]
            width = max(len(row) for row in cells)
            if width != 4:
                # 同页“保险风险最低资本”交叉子表（6 列）与巨灾子表（12 列）
                # 不属于六个目标表口径，跳过。
                continue
            head = cells[0]
            is_header = len(head) >= 2 and head[0] == "行次" and head[1] == "项目"
            is_chunk = bool(head and _ROW_NUMBER_RE.match(head[0]))
            if is_header or is_chunk:
                chunks.append([row + [""] * (4 - len(row)) for row in cells])
    if not chunks:
        return None
    rows: list[list[str]] = []
    for chunk in chunks:
        for row in chunk:
            if row[0] == "行次" and row[1] == "项目":
                if rows:  # 跨页后的重复表头
                    continue
            rows.append(row)
    if len(rows) < 25 or rows[0][:2] != ["行次", "项目"]:
        return None
    return rows


def _supplement_missing_rows(
    rows: list[list[str]], page_text: PageText, pages: Sequence[int]
) -> list[list[str]]:
    """补回没有落在表格线里、只在正文里单独成行的“行次 + 项目”行。

    2022 年第 27 页的“3.3 G-SII附加资本”就是这样一行：它紧贴表头之上，
    线框识别抓不到，但正文文本层有。只接受父级行次已存在、且标签很短
    且不含数字的正文行，避免把正文段落当行次。
    """
    known = {_row_number(row[0]) for row in rows if _row_number(row[0])}

    def key(number: str) -> tuple[int, ...]:
        return tuple(int(part) for part in number.rstrip("*").split("."))

    known_keys = sorted(key(number) for number in known)
    extra: dict[str, list[str]] = {}
    for line in _lines(page_text, pages):
        match = re.match(r"^(\d+(?:\.\d+){0,2})\s+(?P<label>[^\d，。；：、]{2,20})$", line)
        if not match:
            continue
        number, label = match.group(1), match.group("label").strip()
        if number in known or re.search(r"(?:--|[—–－])", label):
            continue
        target = key(number)
        # 只补“夹在两个已知行次之间”的行（如 3.2 与 3.4 之间的 3.3）：
        # 子表（保险风险明细/巨灾）的行次在 4 之后单列，不会被误收。
        lower = [item for item in known_keys if item < target]
        upper = [item for item in known_keys if item > target]
        if not lower or not upper:
            continue
        extra[number] = [number, _clean_label(label), "", ""]
    if not extra:
        return rows
    merged = list(rows)

    def sort_key(row: list[str]) -> tuple:
        number = _row_number(row[0])
        if not number:
            return (9, 9, 9)
        parts = [int(part) for part in number.rstrip("*").split(".")]
        return tuple(parts + [0] * (3 - len(parts)))

    merged.extend(extra.values())
    head, body = merged[0], sorted(merged[1:], key=sort_key)
    return [head, *body]


# --------------------------------------------------------------------------- #
# LIQUIDITY_MONITORING
# --------------------------------------------------------------------------- #
def _norm_lcr(label: str, digit: str = "") -> str:
    match = _LCR_RE.match(label)
    if not match:
        return label
    number = match.group(1) or digit
    if not number:
        return label
    return f"流动性覆盖率LCR{number}（未来{int(match.group(2))}个月）"


def _rebuild_liquidity(page_text: PageText, pages: Sequence[int]) -> list[list[str]] | None:
    rows: list[list[str]] = [list(_LIQ_HEADER)]
    started = False
    last = -1
    pending = ""
    for line in _lines(page_text, pages):
        if not line:
            continue
        if not started:
            if (
                line.startswith(("监管指标名称", "监测指标名称", "指标名称"))
                and "本季度数" in line
                and "上季度数" in line
            ):
                started = True
            continue
        if last >= 0 and _LCR_DIGIT_RE.match(line):
            rows[last][0] = _norm_lcr(rows[last][0], line)
            continue
        # 2024 把“经营活动净现金流回溯不利偏差率”与金额拆成三行：
        # 标签行 → “99.14% 51.31% 47.83%” → “（％）”。金额行自身也是
        # “标签 + 两个金额”的形状，必须先按“纯金额行 + 待用标签”处理，
        # 否则首列金额会被当成标签，整行被 _label_ok 丢掉。
        values = _VALUES_RE.match(line)
        if values and pending:
            label = _norm_lcr(pending)
            rows.append([label, values.group("a"), values.group("b")])
            last = len(rows) - 1
            pending = ""
            if "公司净现金流" in label:
                break
            continue
        # 报告带“增减变动”第三列，这里只保留本季度数/上季度数；
        # 先按三列切分，避免多出来的一列被回退进标签。
        match = _ROW2C_RE.match(line) or _ROW2_RE.match(line)
        if match:
            label = _clean_label(match.group("label"))
            if not _label_ok(label) or label.startswith(("增减变动", "注", "（二）")):
                continue
            label = _norm_lcr(label)
            rows.append([label, match.group("a"), match.group("b")])
            last = len(rows) - 1
            pending = ""
            if "公司净现金流" in label:
                break  # 再往后是第 20 页的鉴证表
            continue
        if (
            4 <= len(line) <= 40
            and not line.startswith(("（", "("))
            and not re.search(r"\d", line)
            and re.search(r"[\u4e00-\u9fff]", line)
        ):
            pending = _clean_label(line)
    if len(rows) < 6:
        return None
    return rows


# --------------------------------------------------------------------------- #
@register("haixia")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """海峡金桥专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import pdfplumber

    cache: dict[int, str] = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:

        def page_text(page: int) -> str:
            number = int(page)
            if number not in cache:
                try:
                    cache[number] = pdf.pages[number - 1].extract_text() or ""
                except IndexError:  # 页码越界按“没有文本”处理
                    cache[number] = ""
            return cache[number]

        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            pages = _table_pages(table)
            if not pages:
                continue
            if table_id == "SOLVENCY_MAIN":
                rows = _rebuild_main(page_text, pages)
                if rows:
                    _sync_blocks(table, rows, pages)
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                rows = _rebuild_operating(page_text, pages)
                if rows:
                    _sync_blocks(table, rows, pages)
            elif table_id == "MINIMUM_CAPITAL":
                rows = _rebuild_minimum(pdf, pages)
                if rows:
                    rows = _supplement_missing_rows(rows, page_text, pages)
                    _sync_blocks(table, rows, pages)
            elif table_id == "LIQUIDITY_MONITORING":
                rows = _rebuild_liquidity(page_text, pages)
                if not rows and getattr(table, "strategy", "") == "公司规则页码种子":
                    rows = [list(_LIQ_HEADER)]
                    section = "".join(re.sub(r"\s+", "", page_text(page)) for page in pages)
                    for label in ("经营活动净现金流", "规模保费同比增速"):
                        match = re.search(re.escape(label) + r"([-+]?\d[\d,]*(?:\.\d+)?%?)([-+]?\d[\d,]*(?:\.\d+)?%?)", section)
                        if match:
                            rows.append([label, match.group(1), match.group(2)])
                if not rows or len(rows) < 2:
                    continue
                unit = "元" if any("（元）" in row[0] for row in rows) else ""
                _sync_blocks(table, rows, pages, unit=unit)
    return tables
