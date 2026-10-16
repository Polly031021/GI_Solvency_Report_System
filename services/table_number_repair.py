"""数值格式修复：按同一页原文校正金额的千分位写法。

背景：模型（尤其是图片模式）经常破坏数字的分隔符——把千分位逗号写成小数点
（``1,234,567.89`` → ``1.234.567.89``）、丢掉千分位逗号、或把全角逗号写成点。
金额一旦被改写，数值会差若干个数量级，属于下游无法察觉的静默错误。

本模块以**同一页 PDF 原文**为准绳做确定性校正，而不是靠猜：

1. 从原页文本建立"数字指纹"索引：``(整数位数字, 小数位数字, 正负号, 是否百分号)``
   → 原文中实际出现的写法集合；
2. 单元格数字若能在索引中唯一对应到原文写法，就采用原文的分隔符版式
   （原文用千分位逗号就输出逗号；原文是全角逗号则统一为标准半角逗号）；
3. 多组三位分段（``1.234.567``）在结构上不可能是合法小数，即使原文无证据也
   直接还原为千分位逗号；
4. 单一三位分段（``1.234``）有歧义：只有原文出现过对应写法才改写，否则原样保留
   并进入待人工核对清单；
5. 百分比、倍数、负号一律按原文与单元格自身写法保留，不猜测符号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd


# 提示词中的统一数值格式要求：文字模式与图片模式共用。
THOUSAND_SEPARATOR_PROMPT_RULE = (
    "数值格式：原文里的千分位分隔符必须原样保留（如 1,234,567.89），"
    "严禁把千分位逗号改写成小数点（禁止输出 1.234.567.89 或 1234.567.89）；"
    "也不得漏写或自行添加千分位逗号。"
    "一个数字里最多只能有一个小数点，小数点后请按原文位数保留；"
    "负数用括号或负号按原文保留。"
)


# 原文中的数字候选项：不含内部空白，避免把相邻两列数字粘成一个数。
_SOURCE_NUMBER_RE = re.compile(r"[（(]?[-－−]?\d[\d,，.．]*[）)]?[%％]?")
# 单一三位分段（1.234）：有歧义，需要原文佐证
_AMBIGUOUS_SINGLE_GROUP_RE = re.compile(r"^\d{1,3}\.\d{3}$")
# 整数位较长、小数位恰好三位（12345.678）：疑似把千分位误读成小数点，
# 原文无佐证时进入待核对清单，而不是静默放行。
_SUSPECT_LARGE_DOT_RE = re.compile(r"^\d{4,}\.\d{3}$")
# 欧式写法：点号做千分位、逗号做小数点（12.345.678,90）。
_EUROPEAN_NUMBER_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+),(\d{1,2})")

_DIGIT_ONLY_RE = re.compile(r"^\d+$")
_SEPARATORS = {",", "，", ".", "．"}


@dataclass(frozen=True)
class ParsedNumber:
    """一个数字字符串的结构化拆解。"""

    int_digits: str
    frac_digits: str
    negative: bool
    percent: bool
    grouped: bool
    raw: str

    @property
    def key(self) -> tuple[str, str, bool, bool]:
        return (self.int_digits, self.frac_digits, self.negative, self.percent)


def _strip_wrappers(text: str) -> tuple[str, bool, bool, bool]:
    """剥离括号/负号/百分号，返回 (数字主体, 负数, 百分号, 使用了括号)。"""
    value = str(text or "").strip()
    percent = value.endswith("%") or value.endswith("％")
    if percent:
        value = value[:-1].strip()
    parenthesised = False
    if len(value) >= 2 and value[0] in "（(" and value[-1] in "）)":
        parenthesised = True
        value = value[1:-1].strip()
    negative = parenthesised
    if value[:1] in {"-", "－", "−"}:
        negative = True
        value = value[1:].strip()
    return value, negative, percent, parenthesised


def parse_number_token(text: str) -> ParsedNumber | None:
    """解析数字写法；无法确定结构时返回 None。

    ``grouped=True`` 表示整数位存在千分位分组（如 ``1,234`` 或 ``1.234.567``）。
    """
    body, negative, percent, _ = _strip_wrappers(text)
    if not body:
        return None
    normalized = body.replace("，", ",").replace("．", ".")
    if not re.fullmatch(r"\d[\d,.]*", normalized):
        return None

    # 欧式写法：1.234.567,89 → 1234567.89（千分位点、小数逗号）。
    european = _EUROPEAN_NUMBER_RE.fullmatch(normalized)
    if european:
        return ParsedNumber(
            int_digits="".join(european.group(1).split(".")),
            frac_digits=european.group(2),
            negative=negative,
            percent=percent,
            grouped=True,
            raw=str(text),
        )

    if "," in normalized:
        int_part, _, frac_part = normalized.partition(".")
        groups = int_part.split(",")
        # 千分位分组的判据：首组 1-3 位，其余各组恰好 3 位
        valid_grouping = (
            _DIGIT_ONLY_RE.match(groups[0]) is not None
            and 1 <= len(groups[0]) <= 3
            and all(len(group) == 3 and group.isdigit() for group in groups[1:])
        )
        frac_digits = frac_part.replace(",", "")
        if frac_digits and not _DIGIT_ONLY_RE.match(frac_digits):
            return None
        if valid_grouping:
            return ParsedNumber(
                int_digits="".join(groups),
                frac_digits=frac_digits,
                negative=negative,
                percent=percent,
                grouped=True,
                raw=str(text),
            )
        # 逗号出现在小数位置（欧式写法或误写，如 102,49%）：按小数点理解，
        # 但只在形态明确（两位一组、无小数点）时接受，避免把 1,234 误判。
        if (
            not frac_part
            and len(groups) == 2
            and _DIGIT_ONLY_RE.match(groups[0]) is not None
            and 1 <= len(groups[1]) <= 2
            and groups[1].isdigit()
        ):
            return ParsedNumber(
                int_digits=groups[0],
                frac_digits=groups[1],
                negative=negative,
                percent=percent,
                grouped=False,
                raw=str(text),
            )
        return None

    parts = normalized.split(".")
    if len(parts) == 1:
        return ParsedNumber(
            int_digits=parts[0],
            frac_digits="",
            negative=negative,
            percent=percent,
            grouped=False,
            raw=str(text),
        )
    head, *tail = parts
    if len(tail) == 1:
        # 单一小数点：按普通小数处理；1.234 这类歧义写法由上层按原文佐证判断
        return ParsedNumber(
            int_digits=head,
            frac_digits=tail[0],
            negative=negative,
            percent=percent,
            grouped=False,
            raw=str(text),
        )
    # 多个小数点：只有全部分组为三位时才是千分位写法
    if any(len(group) != 3 for group in tail[:-1]):
        return None
    if len(tail[-1]) == 3:
        return ParsedNumber(
            int_digits="".join([head, *tail]),
            frac_digits="",
            negative=negative,
            percent=percent,
            grouped=True,
            raw=str(text),
        )
    if 1 <= len(tail[-1]) <= 2:
        return ParsedNumber(
            int_digits="".join([head, *tail[:-1]]),
            frac_digits=tail[-1],
            negative=negative,
            percent=percent,
            grouped=True,
            raw=str(text),
        )
    return None


def format_number(parsed: ParsedNumber, *, grouped: bool) -> str:
    """按指定分组方式输出数字主体（不含符号与百分号）。"""
    int_text = parsed.int_digits
    if grouped and len(int_text) > 3:
        int_text = _group_digits(int_text)
    return f"{int_text}.{parsed.frac_digits}" if parsed.frac_digits else int_text


def _group_digits(digits: str) -> str:
    """从右往左按三位插逗号；不做数值转换，避免丢失前导零。"""
    chunks: list[str] = []
    remaining = digits
    while len(remaining) > 3:
        chunks.insert(0, remaining[-3:])
        remaining = remaining[:-3]
    chunks.insert(0, remaining)
    return ",".join(chunks)


@dataclass
class NumberIndex:
    """同一页原文的数字索引：指纹 → 原文写法是否带千分位分组。"""

    grouped: dict[tuple[str, str, bool, bool], bool] = field(default_factory=dict)
    grouped_integers: set[str] = field(default_factory=set)

    def has(self, parsed: ParsedNumber) -> bool:
        return parsed.key in self.grouped

    def prefers_grouping(self, parsed: ParsedNumber) -> bool:
        """原文中同一数值只要出现过千分位写法，就按千分位输出。"""
        return bool(self.grouped.get(parsed.key, False))

    def has_grouped_integer(self, int_digits: str) -> bool:
        """原文是否出现过“该整数位、且无小数”的千分位整数。

        用于判定 12345.678 这类写法：若原文出现 12,345,678，说明小数点
        位置就是千分位，可按整数还原。
        """
        return int_digits in self.grouped_integers

    def __len__(self) -> int:  # pragma: no cover - 便于调试
        return len(self.grouped)


def build_number_index(source_text: str) -> NumberIndex:
    """从原页文本建立数字索引。"""
    index = NumberIndex()
    if not source_text:
        return index
    for token in _SOURCE_NUMBER_RE.findall(str(source_text)):
        parsed = parse_number_token(token)
        if parsed is None:
            continue
        index.grouped[parsed.key] = bool(index.grouped.get(parsed.key, False) or parsed.grouped)
        if parsed.grouped and not parsed.frac_digits:
            index.grouped_integers.add(parsed.int_digits)
    return index


def _comparable(text: str) -> str:
    """比较用归一化：全角逗号/句点按半角处理，忽略空白。"""
    return (
        str(text or "")
        .replace("，", ",")
        .replace("．", ".")
        .replace("％", "%")
        .replace("（", "(")
        .replace("）", ")")
        .replace("\u00a0", "")
        .replace(" ", "")
        .strip()
    )


def repair_number_text(
    text: str,
    index: NumberIndex | None = None,
) -> tuple[str, str, str]:
    """修复单个单元格文本。

    返回 ``(修复后的文本, 修复说明, 待核对说明)``；未发生时后两项为空串。
    """
    raw = "" if text is None else str(text)
    stripped_raw = raw.strip()
    if not stripped_raw:
        return raw, "", ""
    parsed = parse_number_token(stripped_raw)
    if parsed is None:
        return raw, "", ""

    index = index if index is not None else NumberIndex()
    if parsed.grouped:
        # 多组三位分段在结构上不可能是合法小数 → 直接还原为千分位逗号
        target_body = format_number(parsed, grouped=True)
    elif index.has(parsed):
        target_body = format_number(parsed, grouped=index.prefers_grouping(parsed))
    elif (
        parsed.frac_digits
        and not parsed.percent
        and index.has_grouped_integer(f"{parsed.int_digits}{parsed.frac_digits}")
    ):
        # 12345.678 这类写法：原文存在 12,345,678 时，小数点即千分位。
        merged = ParsedNumber(
            int_digits=f"{parsed.int_digits}{parsed.frac_digits}",
            frac_digits="",
            negative=parsed.negative,
            percent=parsed.percent,
            grouped=True,
            raw=parsed.raw,
        )
        target_body = format_number(merged, grouped=True)
    elif _AMBIGUOUS_SINGLE_GROUP_RE.match(_comparable(stripped_raw)):
        # 单一三位分段（1.234）：只有原文明确以"1,234"这类千分位整数出现时才还原
        merged = ParsedNumber(
            int_digits=f"{parsed.int_digits}{parsed.frac_digits}",
            frac_digits="",
            negative=parsed.negative,
            percent=parsed.percent,
            grouped=True,
            raw=parsed.raw,
        )
        if index.grouped.get(merged.key) is True:
            target_body = format_number(merged, grouped=True)
        else:
            return raw, "", f"{stripped_raw}（原文未见对应千分位写法，无法判断是金额还是小数）"
    elif _SUSPECT_LARGE_DOT_RE.match(_comparable(stripped_raw)):
        # 12345.678 这类写法：位数较大，疑似千分位被误读成小数点。
        return raw, "", f"{stripped_raw}（整数位较长且小数位恰为三位，疑似千分位被误读为小数点，请核对原文）"
    else:
        return raw, "", ""

    prefix = "(" if parsed.negative and stripped_raw.startswith(("（", "(")) else (
        "-" if parsed.negative else ""
    )
    suffix = ")" if prefix == "(" else ""
    target = f"{prefix}{target_body}{'%' if parsed.percent else ''}{suffix}"
    if _comparable(target) == _comparable(stripped_raw):
        return raw, "", ""
    repaired = raw.replace(stripped_raw, target)
    return repaired, f"{stripped_raw}→{target}", ""


@dataclass
class NumberRepairOutcome:
    """一次修复的结果：修好的行、已修复项与待人工核对的疑点。"""

    rows: list[list[str]]
    repairs: tuple[str, ...] = ()
    reviews: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.repairs)

    @property
    def note(self) -> str:
        """拼成一行日志说明；无问题时返回空串。"""
        parts: list[str] = []
        if self.repairs:
            preview = "、".join(dict.fromkeys(self.repairs))[:140]
            parts.append(f"千分位等数值格式校正{len(self.repairs)}处（{preview}）")
        if self.reviews:
            preview = "、".join(dict.fromkeys(self.reviews))[:140]
            parts.append(f"待人工核对：{preview}")
        return "；".join(parts)


def repair_thousand_separators(
    rows: list[list],
    source_text: str = "",
) -> NumberRepairOutcome:
    """对整张表逐单元格做数值格式校正；``source_text`` 为同页原文。"""
    index = build_number_index(source_text)
    repaired_rows: list[list[str]] = []
    repairs: list[str] = []
    reviews: list[str] = []
    for row in rows or []:
        cells: list[str] = []
        for cell in row or []:
            fixed, repair_note, review_note = repair_number_text(
                "" if cell is None else str(cell),
                index,
            )
            cells.append(fixed)
            if repair_note:
                repairs.append(repair_note)
            if review_note:
                reviews.append(review_note)
        repaired_rows.append(cells)
    return NumberRepairOutcome(
        rows=repaired_rows,
        repairs=tuple(repairs),
        reviews=tuple(reviews),
    )


def repair_frame(
    frame: pd.DataFrame,
    source_text: str = "",
) -> tuple[pd.DataFrame, NumberRepairOutcome]:
    """DataFrame 版本的修复入口，供逐页提取结果直接复用。"""
    if frame is None or frame.empty:
        return frame, NumberRepairOutcome(rows=[])
    outcome = repair_thousand_separators(
        frame.fillna("").astype(str).values.tolist(),
        source_text,
    )
    if not outcome.changed:
        return frame, outcome
    return pd.DataFrame(outcome.rows), outcome


__all__ = [
    "THOUSAND_SEPARATOR_PROMPT_RULE",
    "NumberIndex",
    "NumberRepairOutcome",
    "ParsedNumber",
    "build_number_index",
    "format_number",
    "parse_number_token",
    "repair_frame",
    "repair_number_text",
    "repair_thousand_separators",
]
