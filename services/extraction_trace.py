"""提取链路缺失原因（R4）：把定位/提取阶段的问题归因到可读原因码。

原则：
* 只解释“为什么没取到”，不修改数据；
* 原因码稳定，供 STEP2 展示、STEP3 诊断和后续 STEP7 缺口复用；
* 未配置规则的公司只提示，不阻断。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

REASON_LABELS: dict[str, str] = {
    "RULE_NOT_FOUND": "未找到公司规则",
    "YEAR_UNKNOWN": "未识别报告年度",
    "PAGE_DRIFT": "页码偏移（容差内）",
    "PAGE_OUT_OF_TOLERANCE": "页码超出容差，已回退全文",
    "ANCHOR_NOT_FOUND": "未找到表标题锚点",
    "SIGNATURE_MISSING": "页面未识别到目标表结构",
    "REQUIRED_ITEM_MISSING": "缺少必填行",
    "UNIT_UNKNOWN": "未识别单位",
    "OK": "",
}

# 与日志/诊断表对齐的列。
TRACE_COLUMNS = ["目标表", "物理页码", "状态", "规则来源", "未命中原因", "原因码"]


@dataclass(frozen=True)
class ExtractionTrace:
    table_id: str
    table_name: str
    pages: tuple[int, ...]
    status: str
    reason_code: str
    reason_text: str
    rule_source: str = ""

    def to_row(self) -> dict[str, str]:
        return {
            "目标表": self.table_name or self.table_id,
            "物理页码": "、".join(str(page) for page in self.pages),
            "状态": self.status,
            "规则来源": self.rule_source,
            "未命中原因": self.reason_text,
            "原因码": self.reason_code,
        }


def _rule_label(resolved_rule: Any) -> str:
    if resolved_rule is None:
        return ""
    label = getattr(resolved_rule, "label", "")
    source = getattr(resolved_rule, "source", "")
    if label and source:
        return f"{source}（{label}）"
    return str(label or source or "")


def _page_status(pages: Iterable[int], logs: Sequence[Mapping[str, Any]], table_name: str = "") -> tuple[str, str]:
    """根据日志判断这些页的提取状态，返回 (状态, 失败说明)。"""
    page_set = {int(page) for page in pages}
    failed: list[str] = []
    for log in logs or []:
        if table_name and str(log.get("目标表") or "") not in {table_name, "", "整页全表"}:
            continue
        log_pages = {int(value) for value in re.findall(r"\d+", str(log.get("物理页码") or ""))}
        if not log_pages.intersection(page_set):
            continue
        if str(log.get("状态") or "") == "失败":
            failed.append(str(log.get("说明") or "")[:120])
    if failed:
        return ("部分成功" if len(failed) < len(page_set) else "缺失"), failed[0]
    return "成功", ""


def _table_rows(tables: Sequence[Any] | None, table_id: str) -> list[list[str]]:
    for table in tables or []:
        if str(getattr(table, "table_id", "") or "") == table_id:
            return [list(row) for row in getattr(table, "rows", []) or []]
    return []


def _missing_required_items(
    rows: Sequence[Sequence[str]],
    required_items: Sequence[str],
) -> list[str]:
    if not rows or not required_items:
        return []
    labels = "".join(str(row[0]) if row else "" for row in rows)
    return [item for item in required_items if str(item) and str(item) not in labels]


def _primary_table_id(table_id: str) -> str:
    """把 Profile 的 table_id 映射到内置主键（如 NON_LIFE_OPERATING_METRICS）。"""
    try:
        from .extraction_config import load_table_maps

        return load_table_maps()["aliases"].get(table_id, table_id)
    except Exception:
        return table_id


def build_traces(
    *,
    resolved_rule: Any = None,
    matches: Sequence[Any] | None = None,
    logs: Sequence[Mapping[str, Any]] | None = None,
    tables: Sequence[Any] | None = None,
) -> list[dict[str, str]]:
    """为每张目标表生成一条状态记录（成功/部分成功/缺失 + 原因）。"""
    logs = list(logs or [])
    rule_label = _rule_label(resolved_rule)
    warnings = tuple(getattr(resolved_rule, "warnings", ()) or ())
    year_unknown = any("YEAR_UNKNOWN" in str(item) for item in warnings)

    traces: list[ExtractionTrace] = []
    for match in matches or []:
        table_id = str(getattr(match, "table_id", "") or "")
        table_name = str(getattr(match, "table_name", "") or table_id)
        pages = tuple(int(page) for page in (getattr(match, "pages", []) or []))
        rule_note = str(getattr(match, "rule_note", "") or "")

        reason_code = "OK"
        reason_text = ""
        status = "成功"

        if not pages:
            status = "缺失"
            if resolved_rule is None:
                reason_code = "RULE_NOT_FOUND"
            elif "未命中锚点" in rule_note or "未命中" in rule_note:
                reason_code = "ANCHOR_NOT_FOUND"
            else:
                reason_code = "ANCHOR_NOT_FOUND"
            reason_text = rule_note or REASON_LABELS.get(reason_code, "")
            if year_unknown:
                reason_text = f"{reason_text}；{REASON_LABELS['YEAR_UNKNOWN']}".strip("；")
        else:
            status, failure_note = _page_status(pages, logs, table_name)
            if status != "成功":
                reason_code = "SIGNATURE_MISSING"
                reason_text = failure_note or REASON_LABELS[reason_code]

            rule_config: dict[str, Any] = {}
            if resolved_rule is not None:
                rule_tables = getattr(resolved_rule, "tables", {}) or {}
                rule_config = dict(
                    rule_tables.get(table_id)
                    or rule_tables.get(_primary_table_id(table_id))
                    or {}
                )
            required_items = [
                str(item)
                for item in (rule_config.get("required_items") or [])
                if str(item).strip()
            ]
            missing_items = _missing_required_items(
                _table_rows(tables, table_id), required_items
            )
            if missing_items:
                item_text = "、".join(missing_items)
                if status == "成功":
                    status = "部分成功"
                reason_code = "REQUIRED_ITEM_MISSING"
                reason_text = (
                    f"{reason_text}；缺少必填行：{item_text}"
                    if reason_text
                    else f"缺少必填行：{item_text}"
                )

        traces.append(
            ExtractionTrace(
                table_id=table_id,
                table_name=table_name,
                pages=pages,
                status=status,
                reason_code=reason_code,
                reason_text=reason_text,
                rule_source=rule_label,
            )
        )
    return [trace.to_row() for trace in traces]


def trace_reason_map(traces: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """table_name → 原因，供 STEP3 诊断按表名挂原因。"""
    return {
        str(trace.get("目标表") or ""): str(trace.get("未命中原因") or "")
        for trace in traces or []
        if str(trace.get("状态") or "") != "成功"
        and str(trace.get("未命中原因") or "").strip()
    }


def build_diagnostics(traces: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """把未成功的 trace 转成 STEP3“匹配诊断”行。"""
    rows: list[dict[str, Any]] = []
    for trace in traces or []:
        status = str(trace.get("状态") or "成功")
        if status == "成功":
            continue
        rows.append(
            {
                "目标表": str(trace.get("目标表") or ""),
                "来源页码": str(trace.get("物理页码") or ""),
                "原始项目": "",
                "指标编码": "",
                "状态": "来源缺失",
                "原因": str(trace.get("未命中原因") or "未提取到该表"),
                "最佳候选": "",
                "最佳得分": 0,
                "第二候选": "",
                "第二得分": 0,
            }
        )
    return rows
