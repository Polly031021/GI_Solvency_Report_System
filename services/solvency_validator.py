from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from .solvency_normalizer import STANDARD_COLUMNS


PERIOD_GROUP_ALIASES: dict[str, tuple[str, ...]] = {
    "当前期末": ("本季度末数", "期末数", "本季度（末）数"),
    "上期末/期初": ("上季度末数", "期初数"),
    "下季度预测": (
        "下季度末预测数",
        "下季度预测数",
        "基本情景下的下季度预测数",
    ),
    "本季度": ("本季度数",),
    "上季度": ("上季度数",),
    "本年累计": ("本年累计数", "年度累计数"),
}
PERIOD_GROUP_ORDER = {name: index for index, name in enumerate(PERIOD_GROUP_ALIASES)}
PERIOD_ALIAS_TO_GROUP = {
    alias: group
    for group, aliases in PERIOD_GROUP_ALIASES.items()
    for alias in aliases
}


REQUIRED_CORE_METRICS: tuple[str, ...] = (
    "RECOGNIZED_ASSETS",
    "RECOGNIZED_LIABILITIES",
    "ACTUAL_CAPITAL",
    "MINIMUM_CAPITAL",
    "CORE_SOLVENCY_RATIO",
    "COMBINED_SOLVENCY_RATIO",
)


RESULT_COLUMNS = [
    "result_id",
    "check_type",
    "severity",
    "rule_id",
    "rule_name",
    "company",
    "report_period",
    "period",
    "actual",
    "expected",
    "difference",
    "tolerance",
    "tolerance_unit",
    "status",
    "failure_reason",
    "formula",
    "notes",
    "involved_metrics",
    "source_pages",
    "suggestion",
    "evidence_count",
]


EVIDENCE_COLUMNS = [
    "result_id",
    "record_id",
    "metric_code",
    "metric_name",
    "role",
    "period",
    "status",
    "actual_value",
    "expected_value",
    "unit",
    "source_file",
    "source_sheet",
    "source_page",
    "message",
]


@dataclass
class ValidationResult:
    check_type: str
    severity: str
    rule_id: str
    rule_name: str
    company: str
    report_period: str
    period: str
    actual: float | None
    expected: float | None
    difference: float | None
    tolerance: float
    status: str
    notes: str
    involved_metrics: str
    source_pages: str
    suggestion: str
    failure_reason: str = ""
    formula: str = ""
    tolerance_unit: str = ""
    evidence_count: int = 0
    result_id: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("evidence", None)
        return data


@dataclass
class MetricSnapshot:
    """One metric's values for a company/report period/period group."""

    code: str
    name: str
    value: float | None
    rows: pd.DataFrame
    numeric_rows: pd.DataFrame
    unit: str


@dataclass
class RuleEvaluation:
    """Detailed outcome of one formula/cross-table rule."""

    actual: float | None
    expected: float | None
    failure_reason: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    applicable: bool = True


def load_validation_rules(path: str | Path) -> pd.DataFrame:
    """Load the editable rule workbook, with no built-in rule additions."""
    preview = pd.read_excel(path, sheet_name="勾稽规则", header=None, nrows=30)
    header_row: int | None = None
    for index, row in preview.iterrows():
        values = {str(value).strip() for value in row if not pd.isna(value)}
        if "规则ID" in values:
            header_row = int(index)
            break
    if header_row is None:
        raise ValueError(
            f"{path} 的“勾稽规则”工作表前 30 行内未找到“规则ID”表头。"
        )
    frame = pd.read_excel(
        path,
        sheet_name="勾稽规则",
        header=header_row,
    ).fillna("")
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def validation_period_group(value: Any) -> str:
    period = str(value or "").strip()
    return PERIOD_ALIAS_TO_GROUP.get(period, period)


def _numeric(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _record_id(row: pd.Series) -> int | None:
    value = row.get("_记录号")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _evidence(
    *,
    record_id: int | None = None,
    metric_code: str = "",
    metric_name: str = "",
    role: str = "",
    period: str = "",
    status: str = "",
    actual_value: Any = None,
    expected_value: Any = None,
    unit: str = "",
    source_file: str = "",
    source_sheet: str = "",
    source_page: str = "",
    message: str = "",
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "metric_code": _text(metric_code),
        "metric_name": _text(metric_name),
        "role": _text(role),
        "period": _text(period),
        "status": _text(status),
        "actual_value": actual_value,
        "expected_value": expected_value,
        "unit": _text(unit),
        "source_file": _text(source_file),
        "source_sheet": _text(source_sheet),
        "source_page": _text(source_page),
        "message": _text(message),
    }


def _row_evidence(
    row: pd.Series,
    *,
    role: str,
    status: str,
    message: str,
    actual_value: Any = None,
    expected_value: Any = None,
) -> dict[str, Any]:
    return _evidence(
        record_id=_record_id(row),
        metric_code=_text(row.get("指标编码")),
        metric_name=_text(row.get("指标名称")),
        role=role,
        period=_text(row.get("_验证期间组")) or _text(row.get("期间口径")),
        status=status,
        actual_value=actual_value,
        expected_value=expected_value,
        unit=_text(row.get("单位")),
        source_file=_text(row.get("来源文件")),
        source_sheet=_text(row.get("来源工作表")),
        source_page=_text(row.get("来源页码")),
        message=message,
    )


def _safe_tolerance(value: Any) -> float:
    number = _numeric(value)
    return 0.0 if number is None else number


def _format_number(value: Any, unit: str = "") -> str:
    if value is None:
        return "缺失"
    number = _numeric(value)
    if number is None:
        return _text(value)
    use_small_precision = number != 0 and abs(number) < 0.01
    if unit == "%":
        return (
            f"{number:,.4f}%"
            if use_small_precision
            else f"{number:,.2f}%"
        )
    if unit == "百分点":
        return (
            f"{number:,.4f} 百分点"
            if use_small_precision
            else f"{number:,.2f} 百分点"
        )
    if unit == "倍":
        return (
            f"{number:,.4f} 倍"
            if use_small_precision
            else f"{number:,.2f} 倍"
        )
    suffix = f" {unit}" if unit else ""
    formatted = f"{number:,.4f}" if use_small_precision else f"{number:,.2f}"
    return f"{formatted}{suffix}"


def _source_pages(
    frame: pd.DataFrame,
    codes: Iterable[str] = (),
    period_group: str = "",
) -> str:
    scoped = frame
    code_values = {str(code) for code in codes if str(code).strip()}
    if code_values:
        scoped = scoped[scoped["指标编码"].astype(str).str.strip().isin(code_values)]
    if period_group:
        scoped = scoped[scoped["_验证期间组"].astype(str).str.strip().eq(period_group)]
    pages = [
        _text(value)
        for value in scoped.get("来源页码", pd.Series(dtype=str))
        if _text(value) and _text(value).lower() != "nan"
    ]
    return "、".join(dict.fromkeys(pages))


def _metric_rows(frame: pd.DataFrame, code: str, period_group: str) -> pd.DataFrame:
    if frame.empty:
        return frame.iloc[0:0]
    codes = frame["指标编码"].astype(str).str.strip()
    periods = frame["_验证期间组"].astype(str).str.strip()
    return frame[codes.eq(str(code)) & periods.eq(str(period_group))]


def _numeric_metric_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    numbers = pd.to_numeric(rows["数值"], errors="coerce")
    valid = numbers.notna().to_numpy() & np.isfinite(numbers.to_numpy(dtype=float, na_value=np.nan))
    return rows.loc[valid].copy()


def _preferred_numeric_row(numeric_rows: pd.DataFrame) -> pd.Series | None:
    if numeric_rows.empty:
        return None
    attributes = numeric_rows.get("指标属性", pd.Series("", index=numeric_rows.index)).astype(str).str.strip()
    source_types = numeric_rows.get("来源类型", pd.Series("", index=numeric_rows.index)).astype(str).str.strip()
    disclosed = numeric_rows[
        ~attributes.isin({"计算", "校验"}) & ~source_types.eq("系统计算")
    ]
    target = disclosed if not disclosed.empty else numeric_rows
    if "来源工作表" in target.columns:
        def _sheet_priority(value: Any) -> int:
            sheet = _text(value)
            if "主要指标" in sheet:
                return 0
            if "偿付能力" in sheet:
                return 1
            if "实际资本" in sheet or "最低资本" in sheet:
                return 2
            return 3

        target = target.assign(
            _来源表优先=target["来源工作表"].map(_sheet_priority)
        ).sort_values("_来源表优先", kind="stable")
    return target.iloc[0]


def _metric_snapshot(
    frame: pd.DataFrame,
    code: str,
    period_group: str,
    taxonomy_names: dict[str, str],
) -> MetricSnapshot:
    rows = _metric_rows(frame, code, period_group)
    numeric_rows = _numeric_metric_rows(rows)
    preferred = _preferred_numeric_row(numeric_rows)
    value = _numeric(preferred.get("数值")) if preferred is not None else None
    unit = _text(preferred.get("单位")) if preferred is not None else ""
    name = taxonomy_names.get(str(code), "")
    if not name and not rows.empty:
        name = _text(rows.iloc[0].get("指标名称"))
    return MetricSnapshot(
        code=str(code),
        name=name or str(code),
        value=value,
        rows=rows,
        numeric_rows=numeric_rows,
        unit=unit,
    )


def _snapshot_evidence(
    frame: pd.DataFrame,
    snapshot: MetricSnapshot,
    period_group: str,
    role: str,
    taxonomy_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Build row-level STEP3 evidence for one metric and period group."""
    if snapshot.value is not None:
        distinct_values = sorted(
            {
                round(float(value), 6)
                for value in pd.to_numeric(snapshot.numeric_rows["数值"], errors="coerce").dropna()
            }
        )
        evidence: list[dict[str, Any]] = []
        for _, row in snapshot.numeric_rows.iterrows():
            attributes = _text(row.get("指标属性"))
            source_type = _text(row.get("来源类型"))
            derived = attributes in {"计算", "校验"} or source_type == "系统计算"
            note = _text(row.get("备注"))
            zero_filled = "按0处理" in note
            if derived:
                message = "STEP3 派生计算值"
                record_status = "派生计算"
            elif zero_filled:
                message = "STEP3 原始值为空白/占位符，已按0标准化"
                record_status = "占位符按0"
            else:
                message = "STEP3 披露值"
                record_status = "已披露"
            if note:
                message += f"；备注：{note}"
            if len(distinct_values) > 1:
                message += (
                    f"；同一期间口径存在 {len(distinct_values)} 个不同数值，"
                    f"本次勾稽取用 {_format_number(snapshot.value, snapshot.unit)}"
                )
            evidence.append(
                _row_evidence(
                    row,
                    role=role,
                    status=record_status,
                    actual_value=_numeric(row.get("数值")),
                    message=message,
                )
            )
        return evidence

    if not snapshot.rows.empty:
        return [
            _row_evidence(
                row,
                role=role,
                status="数值无法解析",
                actual_value=_text(row.get("数值")),
                message=f"{period_group}的{snapshot.name}（{snapshot.code}）存在记录，但数值无法解析为数字",
            )
            for _, row in snapshot.rows.iterrows()
        ]

    unit = snapshot.unit or _text(taxonomy_names.get(f"{snapshot.code}__unit", ""))
    evidence = [
        _evidence(
            metric_code=snapshot.code,
            metric_name=snapshot.name,
            role=role,
            period=period_group,
            status="缺失",
            unit=unit,
            message=f"{period_group}缺少{snapshot.name}（{snapshot.code}）",
        )
    ]
    other_periods = frame[
        frame["指标编码"].astype(str).str.strip().eq(snapshot.code)
        & frame["_验证期间组"].astype(str).str.strip().ne(period_group)
    ]
    for _, row in other_periods.iterrows():
        evidence.append(
            _row_evidence(
                row,
                role="其他期间",
                status="期间口径不一致",
                actual_value=_numeric(row.get("数值"))
                if _numeric(row.get("数值")) is not None
                else _text(row.get("数值")),
                message=(
                    f"该指标仅在期间口径「{_text(row.get('_验证期间组')) or _text(row.get('期间口径'))}」"
                    f"中找到，本次检查期间为「{period_group}」，请核对期间映射"
                ),
            )
        )
    return evidence


def _missing_reason(
    period_group: str,
    snapshots: Iterable[MetricSnapshot],
    label: str = "公式依赖指标",
) -> str:
    missing = [snapshot for snapshot in snapshots if snapshot.value is None]
    names = "、".join(f"{snapshot.name}（{snapshot.code}）" for snapshot in missing)
    return (
        f"{period_group}无法完成勾稽：缺少{label} {names}。"
        "请回到 STEP3 核对这些指标是否存在、期间口径是否一致、数值是否可解析。"
    )


def _comparison_reason(
    actual_label: str,
    actual: float,
    actual_unit: str,
    expected_label: str,
    expected: float,
    expected_unit: str,
    tolerance: float,
    tolerance_unit: str,
) -> str:
    difference = float(actual - expected)
    direction = "高于" if difference > 0 else "低于"
    return (
        f"{actual_label}为 {_format_number(actual, actual_unit)}，"
        f"{expected_label}为 {_format_number(expected, expected_unit)}，"
        f"相差 {_format_number(abs(difference), tolerance_unit)}"
        f"（{direction}应有值，带符号差异 {_format_number(difference, tolerance_unit)}），"
        f"超过容差 {_format_number(tolerance, tolerance_unit)}。"
    )


def _snapshots(
    frame: pd.DataFrame,
    codes: Iterable[str],
    period_group: str,
    taxonomy_names: dict[str, str],
) -> dict[str, MetricSnapshot]:
    return {
        str(code): _metric_snapshot(frame, str(code), period_group, taxonomy_names)
        for code in codes
    }


def _collect_evidence(
    frame: pd.DataFrame,
    snapshots: dict[str, MetricSnapshot],
    roles: dict[str, str],
    period_group: str,
    taxonomy_names: dict[str, str],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for code, snapshot in snapshots.items():
        evidence.extend(
            _snapshot_evidence(
                frame,
                snapshot,
                period_group,
                roles.get(code, "公式组成"),
                taxonomy_names,
            )
        )
    return evidence


def _evaluate_duplicate_rule(
    frame: pd.DataFrame,
    rule_id: str,
    metric_code: str,
    period_group: str,
    formula: str,
    tolerance: float,
    tolerance_unit: str,
    taxonomy_names: dict[str, str],
) -> RuleEvaluation:
    """Compare the same metric across source worksheets, not just max/min."""
    del rule_id
    code = metric_code
    snapshot = _metric_snapshot(frame, code, period_group, taxonomy_names)
    evidence = _snapshot_evidence(
        frame, snapshot, period_group, "跨表核对", taxonomy_names
    )
    if snapshot.value is None or snapshot.numeric_rows.empty:
        return RuleEvaluation(
            actual=None,
            expected=None,
            failure_reason=_missing_reason(period_group, [snapshot], "可比较的披露值"),
            evidence=evidence,
            notes="未找到可比较的有效披露值，无法执行跨表一致性检查。",
            applicable=False,
        )

    numeric = snapshot.numeric_rows.copy()
    numeric["_来源表"] = numeric["来源工作表"].map(_text).replace("", "(未标注工作表)")
    by_sheet: dict[str, list[tuple[float, pd.Series]]] = {}
    for sheet, rows in numeric.groupby("_来源表", sort=False):
        values: list[tuple[float, pd.Series]] = []
        for _, row in rows.iterrows():
            value = _numeric(row.get("数值"))
            if value is not None:
                values.append((value, row))
        if values:
            by_sheet[str(sheet)] = values

    if len(by_sheet) < 2:
        sheet = next(iter(by_sheet), "(未标注工作表)")
        evidence = [
            _row_evidence(
                row,
                role="待核对",
                status="已披露",
                actual_value=value,
                message=f"同一期间组仅在来源工作表「{sheet}」中发现有效披露",
            )
            for value, row in next(iter(by_sheet.values()), [])
        ]
        return RuleEvaluation(
            actual=None,
            expected=None,
            failure_reason="",
            evidence=evidence,
            notes="同一期间组仅发现 1 个来源工作表的有效披露，无法执行跨表一致性检查。",
            applicable=False,
        )

    sheets = list(by_sheet)
    current_period = frame[
        frame["_验证期间组"].astype(str).str.strip().eq(period_group)
    ]

    def sheet_priority(sheet: str) -> int:
        sheet_codes = set(
            current_period.loc[
                current_period["来源工作表"].map(_text).eq(sheet), "指标编码"
            ]
            .astype(str)
            .str.strip()
        )
        score = len(sheet_codes & set(REQUIRED_CORE_METRICS)) * 10
        if "主要指标" in sheet:
            score += 5
        if "偿付能力" in sheet:
            score += 2
        return score

    reference_sheet = max(
        sheets,
        key=lambda sheet: (sheet_priority(sheet), -sheets.index(sheet)),
    )
    reference_value = by_sheet[reference_sheet][0][0]
    comparisons: list[tuple[float, pd.Series, str]] = []
    for sheet, values in by_sheet.items():
        if sheet == reference_sheet:
            continue
        for value, row in values:
            comparisons.append((value, row, sheet))
    if not comparisons:
        comparisons = [
            (value, row, reference_sheet)
            for value, row in by_sheet[reference_sheet]
        ]
    compared_value, compared_row, compared_sheet = max(
        comparisons, key=lambda item: abs(item[0] - reference_value)
    )

    unit = snapshot.unit or _text(taxonomy_names.get(f"{code}__unit", ""))
    evidence = []
    for sheet, values in by_sheet.items():
        for value, row in values:
            is_reference = sheet == reference_sheet
            difference = value - reference_value
            within_tolerance = _within_tolerance(difference, tolerance)
            if is_reference:
                status = "基准值"
            elif within_tolerance:
                status = "已披露"
            else:
                status = "跨表差异"
            evidence.append(
                _row_evidence(
                    row,
                    role="基准值" if is_reference else "对比值",
                    status=status,
                    actual_value=value,
                    expected_value=reference_value,
                    message=(
                        f"来源工作表「{sheet}」；与基准来源的差异为 "
                        f"{_format_number(difference, tolerance_unit)}"
                    ),
                )
            )

    difference = compared_value - reference_value
    if _within_tolerance(difference, tolerance):
        return RuleEvaluation(
            actual=compared_value,
            expected=reference_value,
            failure_reason="",
            evidence=evidence,
            notes="各来源工作表披露值在容差范围内一致。",
        )

    sources = "；".join(
        f"{sheet}：{_format_number(values[0][0], unit)}"
        for sheet, values in by_sheet.items()
    )
    failure_reason = (
        f"跨表数值不一致：{sources}。"
        f"基准来源「{reference_sheet}」为 {_format_number(reference_value, unit)}，"
        f"对比来源「{compared_sheet}」为 {_format_number(compared_value, unit)}，"
        f"相差 {_format_number(abs(difference), tolerance_unit)}，"
        f"超过容差 {_format_number(tolerance, tolerance_unit)}。"
    )
    if formula:
        failure_reason += f"核对口径：{formula}。"
    return RuleEvaluation(
        actual=compared_value,
        expected=reference_value,
        failure_reason=failure_reason,
        evidence=evidence,
        notes=failure_reason,
    )


RULE_REQUIRED_COLUMNS = (
    "规则ID",
    "规则名称",
    "适用期间",
    "容差",
    "容差单位",
    "启用",
)


def _rule_value(rule: pd.Series, *names: str) -> str:
    for name in names:
        value = _text(rule.get(name))
        if value:
            return value
    return ""


def _expression_names(expression: str) -> list[str]:
    """Return metric identifiers used by a restricted arithmetic formula."""
    tree = ast.parse(str(expression), mode="eval")
    names: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            visit(node.body)
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                raise ValueError(f"不支持的运算符：{type(node.op).__name__}")
            visit(node.left)
            visit(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                raise ValueError(f"不支持的一元运算符：{type(node.op).__name__}")
            visit(node.operand)
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError("公式常量必须是数字。")
        elif isinstance(node, ast.Name):
            name = str(node.id).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"无效的指标编码：{name}")
            names.append(name)
        else:
            raise ValueError(f"公式包含不支持的语法：{type(node).__name__}")

    visit(tree)
    return list(dict.fromkeys(names))


def _evaluate_expression(expression: str, values: dict[str, float]) -> float:
    tree = ast.parse(str(expression), mode="eval")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise KeyError(f"公式指标未提供数值：{node.id}")
            return float(values[node.id])
        if isinstance(node, ast.UnaryOp):
            operand = evaluate(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ZeroDivisionError("公式分母为0")
                return left / right
        raise ValueError(f"公式包含不支持的语法：{type(node).__name__}")

    result = float(evaluate(tree))
    if not np.isfinite(result):
        raise ValueError("公式计算结果不是有限数值。")
    return result


def _formula_codes(rule: pd.Series) -> tuple[str, list[str]]:
    left_code = _rule_value(rule, "左值指标", "左侧指标", "校验指标")
    expression = _rule_value(rule, "右值表达式", "右侧公式", "校验公式")
    if not left_code:
        raise ValueError("未配置“左值指标”。")
    if _text(rule.get("校验方式")) == "跨表一致":
        return left_code, []
    if not expression:
        raise ValueError("未配置“右值表达式”。")
    return left_code, _expression_names(expression)


def _evaluate_formula_rule(
    frame: pd.DataFrame,
    *,
    left_code: str,
    expression: str,
    dependency_codes: Sequence[str],
    period_group: str,
    rule_name: str,
    formula: str,
    tolerance: float,
    tolerance_unit: str,
    taxonomy_names: dict[str, str],
) -> RuleEvaluation:
    codes = list(dict.fromkeys([left_code, *dependency_codes]))
    snapshots = _snapshots(frame, codes, period_group, taxonomy_names)
    roles = {
        code: ("披露值/左值" if code == left_code else "公式组成")
        for code in codes
    }
    evidence = _collect_evidence(
        frame,
        snapshots,
        roles,
        period_group,
        taxonomy_names,
    )
    missing = [snapshots[code] for code in codes if snapshots[code].value is None]
    if missing:
        reason = _missing_reason(period_group, missing, f"“{rule_name}”公式依赖指标")
        return RuleEvaluation(None, None, reason, evidence, notes=reason)

    actual = snapshots[left_code].value
    values = {
        code: float(snapshots[code].value)
        for code in dependency_codes
        if snapshots[code].value is not None
    }
    try:
        expected = _evaluate_expression(expression, values)
    except ZeroDivisionError as exc:
        reason = f"{period_group}无法执行“{rule_name}”：{exc}。"
        return RuleEvaluation(actual, None, reason, evidence, notes=reason)
    except Exception as exc:
        reason = f"{period_group}无法执行“{rule_name}”公式：{exc}"
        return RuleEvaluation(actual, None, reason, evidence, notes=reason)

    left_unit = snapshots[left_code].unit
    expected_unit = left_unit
    difference = float(actual - expected)
    if _within_tolerance(difference, tolerance):
        return RuleEvaluation(
            actual,
            expected,
            "",
            evidence,
            notes="检查通过",
        )
    reason = _comparison_reason(
        f"披露的{snapshots[left_code].name}",
        actual,
        left_unit,
        f"按配置公式计算的{snapshots[left_code].name}",
        expected,
        expected_unit,
        tolerance,
        tolerance_unit or left_unit,
    )
    reason += (
        f" 配置公式：{expression}。"
        f" 代入值：{_formula_substitution(values)}。"
    )
    if formula and formula != expression:
        reason += f" 规则说明：{formula}"
    return RuleEvaluation(actual, expected, reason, evidence, notes=reason)


def _formula_substitution(values: Mapping[str, float]) -> str:
    return "；".join(
        f"{code}={_format_number(value, '')}"
        for code, value in values.items()
    )


def _within_tolerance(difference: float, tolerance: float) -> bool:
    if tolerance > 0:
        return abs(float(difference)) < float(tolerance)
    return abs(float(difference)) == 0


def validate_rule_configuration(
    rules: pd.DataFrame,
    taxonomy: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Validate formula syntax, metric references and duplicate rule IDs."""
    rows: list[dict[str, str]] = []
    if not isinstance(rules, pd.DataFrame):
        return pd.DataFrame(
            [{"规则ID": "", "状态": "错误", "说明": "规则配置不是表格。"}]
        )
    missing_columns = [
        column for column in RULE_REQUIRED_COLUMNS if column not in rules.columns
    ]
    for column in missing_columns:
        rows.append(
            {
                "规则ID": "",
                "状态": "缺列",
                "说明": f"规则表缺少必需列：{column}",
            }
        )
    known_codes: set[str] = set()
    if isinstance(taxonomy, pd.DataFrame) and "指标编码" in taxonomy.columns:
        known_codes = set(taxonomy["指标编码"].astype(str).str.strip())
    duplicate_ids = set(
        rules.get("规则ID", pd.Series(dtype=str))
        .astype(str)
        .str.strip()
        .loc[lambda values: values.ne("") & values.duplicated(keep=False)]
    )
    for _, rule in rules.iterrows():
        rule_id = _text(rule.get("规则ID"))
        if not rule_id:
            rows.append({"规则ID": "", "状态": "错误", "说明": "规则ID为空。"})
            continue
        if rule_id in duplicate_ids:
            rows.append(
                {"规则ID": rule_id, "状态": "错误", "说明": "规则ID重复。"}
            )
        mode = _text(rule.get("校验方式"))
        try:
            left_code, dependency_codes = _formula_codes(rule)
            if mode == "跨表一致":
                codes = [left_code]
            else:
                codes = [left_code, *dependency_codes]
            unknown = [
                code
                for code in codes
                if known_codes and code not in known_codes
            ]
            if unknown:
                rows.append(
                    {
                        "规则ID": rule_id,
                        "状态": "错误",
                        "说明": "指标编码未登记：" + "、".join(unknown),
                    }
                )
        except Exception as exc:
            rows.append(
                {"规则ID": rule_id, "状态": "错误", "说明": str(exc)}
            )
    if not rows:
        rows.append(
            {
                "规则ID": "全部规则",
                "状态": "通过",
                "说明": f"共 {len(rules)} 条规则，语法和指标引用有效。",
            }
        )
    return pd.DataFrame(rows)


def _evaluate_business_rule(
    frame: pd.DataFrame,
    rule_id: str,
    period_group: str,
    formula: str = "",
    tolerance: float = 0.0,
    tolerance_unit: str = "",
    taxonomy_names: dict[str, str] | None = None,
    rule: pd.Series | None = None,
) -> RuleEvaluation:
    taxonomy_names = taxonomy_names or {}
    if rule is None:
        raise ValueError(f"规则 {rule_id} 缺少配置行。")
    left_code, dependency_codes = _formula_codes(rule)
    if _text(rule.get("校验方式")) == "跨表一致":
        return _evaluate_duplicate_rule(
            frame,
            rule_id,
            left_code,
            period_group,
            formula,
            tolerance,
            tolerance_unit,
            taxonomy_names,
        )
    return _evaluate_formula_rule(
        frame,
        left_code=left_code,
        expression=_rule_value(rule, "右值表达式", "右侧公式", "校验公式"),
        dependency_codes=dependency_codes,
        period_group=period_group,
        rule_name=_text(rule.get("规则名称")) or rule_id,
        formula=formula,
        tolerance=tolerance,
        tolerance_unit=tolerance_unit,
        taxonomy_names=taxonomy_names,
    )

def _entity_groups(frame: pd.DataFrame):
    working = frame.copy()
    for column in STANDARD_COLUMNS:
        if column not in working.columns:
            working[column] = ""
    if "_记录号" not in working.columns:
        working["_记录号"] = np.arange(1, len(working) + 1)
    working["_验证公司"] = (
        working["标准公司名称"].fillna("").astype(str).str.strip()
        .mask(lambda values: values.eq(""), working["公司"].fillna("").astype(str).str.strip())
    )
    working["_验证公司键"] = (
        working["公司统一编码"].fillna("").astype(str).str.strip()
        .mask(lambda values: values.eq(""), working["_验证公司"])
    )
    working["_验证报告期"] = working["报告期"].fillna("").astype(str).str.strip()
    fallback_period = (
        working["报告年度"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
        + working["报告季度"].fillna("").astype(str).str.strip()
    )
    working["_验证报告期"] = working["_验证报告期"].mask(
        working["_验证报告期"].eq(""), fallback_period
    )
    working["_验证报告类型"] = working["报告类型"].fillna("").astype(str).str.strip()
    working["_验证期间组"] = working["期间口径"].map(validation_period_group)
    grouping = ["_验证报告类型", "_验证公司键", "_验证报告期"]
    for _, group in working.groupby(grouping, dropna=False, sort=False):
        yield group.copy()


def _result(
    *,
    check_type: str,
    severity: str,
    rule_id: str,
    rule_name: str,
    group: pd.DataFrame,
    period: str = "",
    actual: Any = None,
    expected: Any = None,
    difference: Any = None,
    tolerance: float = 0.0,
    status: str,
    notes: str = "",
    involved_metrics: Iterable[str] = (),
    source_pages: str = "",
    suggestion: str = "",
    failure_reason: str = "",
    formula: str = "",
    tolerance_unit: str = "",
    evidence: Iterable[dict[str, Any]] = (),
) -> ValidationResult:
    company = str(group["_验证公司"].iloc[0]).strip() if not group.empty else ""
    report_period = str(group["_验证报告期"].iloc[0]).strip() if not group.empty else ""
    evidence_rows = list(evidence)
    return ValidationResult(
        check_type=check_type,
        severity=severity,
        rule_id=rule_id,
        rule_name=rule_name,
        company=company,
        report_period=report_period,
        period=period,
        actual=_numeric(actual),
        expected=_numeric(expected),
        difference=_numeric(difference),
        tolerance=float(tolerance),
        status=status,
        notes=notes,
        involved_metrics="、".join(
            dict.fromkeys(str(code) for code in involved_metrics if str(code).strip())
        ),
        source_pages=source_pages,
        suggestion=suggestion,
        failure_reason=failure_reason,
        formula=formula,
        tolerance_unit=tolerance_unit,
        evidence_count=len(evidence_rows),
        evidence=evidence_rows,
    )


def _canonical_data_type(value: Any) -> str:
    text = _text(value).lower()
    aliases = {
        "比率": "百分比",
        "比例": "百分比",
        "百分数": "百分比",
        "percent": "百分比",
        "%": "百分比",
    }
    return aliases.get(text, text)


def _canonical_metric_name(value: Any) -> str:
    return re.sub(
        r"[\s：:（）()、，,。·—\-_/的]",
        "",
        _text(value),
    )


def _schema_period_key(value: Any) -> str:
    """Equivalent period key used by the metric dictionary check.

    ``本季度数``, ``本季度末数``, ``本季度（末）数`` and ``期末数`` describe the
    same current-period bucket for this validation. Other rules retain their
    existing period-group semantics.
    """
    text = re.sub(r"\s+", "", _text(value))
    if not text:
        return ""
    if "预测" in text or "下季度" in text or "下季末" in text:
        return "下季度预测"
    if (
        text.startswith("上季度")
        or text.startswith("上季末")
        or text.startswith("期初")
    ):
        return "上期末"
    if (
        text.startswith("本季度")
        or text.startswith("本季末")
        or text.startswith("期末")
    ):
        return "本期末"
    if "本年累计" in text or "本年度累计" in text:
        return "本年累计"
    return validation_period_group(text)


def _metric_name_equivalent(
    actual: Any,
    expected: Any,
    definition: pd.Series,
) -> bool:
    actual_key = _canonical_metric_name(actual)
    candidates = [
        expected,
        *re.split(r"[|｜]", _text(definition.get("别名"))),
    ]
    return bool(actual_key) and any(
        actual_key == _canonical_metric_name(candidate)
        for candidate in candidates
        if _canonical_metric_name(candidate)
    )


def _schema_checks(group: pd.DataFrame, taxonomy: pd.DataFrame) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    taxonomy_frame = taxonomy.copy().fillna("")
    taxonomy_frame["指标编码"] = taxonomy_frame["指标编码"].astype(str).str.strip()
    taxonomy_frame = taxonomy_frame[taxonomy_frame["指标编码"] != ""].drop_duplicates(
        subset="指标编码", keep="last"
    )
    taxonomy_by_code = taxonomy_frame.set_index("指标编码", drop=False)
    taxonomy_units = {
        str(code): _text(row.get("标准单位"))
        for code, row in taxonomy_by_code.iterrows()
    }

    required_fields = (
        "公司", "标准公司名称", "公司统一编码", "公司类型", "同业分类",
        "报告类型", "报告年度", "报告季度", "报告期", "指标编码",
        "指标名称", "期间口径", "单位", "数据类型",
    )
    missing_by_field: dict[str, int] = {}
    missing_rows: list[tuple[pd.Series, list[str]]] = []
    for _, row in group.iterrows():
        missing = [field for field in required_fields if not _text(row.get(field))]
        if missing:
            missing_rows.append((row, missing))
            for field in missing:
                missing_by_field[field] = missing_by_field.get(field, 0) + 1
    missing_total = sum(missing_by_field.values())
    field_text = "；".join(
        f"{field}缺失{count}条" for field, count in missing_by_field.items()
    )
    failure_reason = (
        f"必填字段缺失：{field_text}。涉及 {len(missing_rows)} 条 STEP3 记录。"
        if missing_total
        else ""
    )
    evidence = [
        _row_evidence(
            row,
            role="必填字段",
            status="字段缺失",
            message="缺少字段：" + "、".join(fields),
        )
        for row, fields in missing_rows
    ]
    results.append(_result(
        check_type="数据规范", severity="错误", rule_id="SCHEMA_REQUIRED_FIELDS",
        rule_name="标准窄表必填字段完整性", group=group, actual=missing_total,
        expected=0, difference=missing_total,
        status="通过" if missing_total == 0 else "未通过",
        notes=failure_reason or "所有必填字段均已填报",
        failure_reason=failure_reason,
        evidence=evidence,
        suggestion="返回 Step3 补齐公司、报告期或指标元数据。" if missing_total else "",
    ))

    codes = group["指标编码"].fillna("").astype(str).str.strip()
    system_generated = (
        group["指标属性"].fillna("").astype(str).str.strip().isin({"计算", "校验"})
        & group["来源类型"].fillna("").astype(str).str.strip().eq("系统计算")
    )
    unknown_rows = group.loc[
        ~codes.isin(taxonomy_by_code.index) & ~system_generated
    ]
    unknown_codes = list(dict.fromkeys(unknown_rows["指标编码"].astype(str).str.strip()))
    failure_reason = (
        f"发现 {len(unknown_rows)} 条记录的指标编码不在正式指标字典中："
        + "、".join(unknown_codes)
        if not unknown_rows.empty
        else ""
    )
    evidence = [
        _row_evidence(
            row,
            role="指标编码",
            status="未登记指标",
            actual_value=_text(row.get("指标编码")),
            message=f"指标编码「{_text(row.get('指标编码'))}」不在正式指标字典中",
        )
        for _, row in unknown_rows.iterrows()
    ]
    results.append(_result(
        check_type="数据规范", severity="错误", rule_id="SCHEMA_KNOWN_METRIC",
        rule_name="指标编码存在于正式指标字典", group=group, actual=len(unknown_rows),
        expected=0, difference=len(unknown_rows),
        status="通过" if unknown_rows.empty else "未通过",
        notes=failure_reason or "全部指标编码均已登记",
        failure_reason=failure_reason,
        involved_metrics=unknown_codes,
        source_pages=_source_pages(unknown_rows),
        evidence=evidence,
        suggestion="返回 Step3 修正指标映射；新指标须先进入正式指标字典。" if not unknown_rows.empty else "",
    ))

    known_rows = group.loc[codes.isin(taxonomy_by_code.index)].copy()
    comparisons = (
        ("SCHEMA_METRIC_NAME", "指标编码与指标名称一致", "指标名称", "指标名称"),
        ("SCHEMA_UNIT_MATCH", "指标单位符合标准单位", "单位", "标准单位"),
        ("SCHEMA_DATA_TYPE", "指标数据类型符合字典", "数据类型", "数据类型"),
    )
    for rule_id, rule_name, source_column, taxonomy_column in comparisons:
        mismatch_records: list[tuple[pd.Series, str, str]] = []
        for _, row in known_rows.iterrows():
            definition = taxonomy_by_code.loc[str(row["指标编码"]).strip()]
            actual_value = _text(row.get(source_column))
            expected_value = _text(definition.get(taxonomy_column))
            if rule_id == "SCHEMA_DATA_TYPE":
                matches = (
                    _canonical_data_type(actual_value)
                    == _canonical_data_type(expected_value)
                )
            elif rule_id == "SCHEMA_METRIC_NAME":
                matches = _metric_name_equivalent(
                    actual_value,
                    expected_value,
                    definition,
                )
            else:
                matches = actual_value == expected_value
            if not matches:
                mismatch_records.append((row, actual_value, expected_value))
        mismatches = (
            pd.DataFrame([row for row, _, _ in mismatch_records])
            if mismatch_records
            else known_rows.iloc[0:0]
        )
        evidence = [
            _row_evidence(
                row,
                role=source_column,
                status="与指标字典不一致",
                actual_value=actual_value,
                expected_value=expected_value,
                message=(
                    f"STEP3的{source_column}为「{actual_value}」，"
                    f"指标字典要求「{expected_value}」"
                ),
            )
            for row, actual_value, expected_value in mismatch_records
        ]
        examples = "；".join(
            f"{_text(row.get('指标编码'))}：STEP3为「{actual_value}」，字典为「{expected_value}」"
            for row, actual_value, expected_value in mismatch_records[:5]
        )
        failure_reason = (
            f"{rule_name}未通过：{examples}"
            + ("等" if len(mismatch_records) > 5 else "")
            if mismatch_records
            else ""
        )
        results.append(_result(
            check_type="数据规范", severity="错误", rule_id=rule_id,
            rule_name=rule_name, group=group, actual=len(mismatches), expected=0,
            difference=len(mismatches), status="通过" if mismatches.empty else "未通过",
            notes=failure_reason or "检查通过",
            failure_reason=failure_reason,
            involved_metrics=[
                _text(row.get("指标编码")) for row, _, _ in mismatch_records
            ],
            source_pages=_source_pages(mismatches),
            evidence=evidence,
            suggestion="返回 Step3 按指标字典统一名称、单位和数据类型。" if not mismatches.empty else "",
        ))

    invalid_period_records: list[tuple[pd.Series, str, str]] = []
    for _, row in known_rows.iterrows():
        definition = taxonomy_by_code.loc[str(row["指标编码"]).strip()]
        allowed = {
            _schema_period_key(item.strip())
            for item in str(definition.get("允许期间口径", "")).split("|")
            if item.strip()
        }
        actual_period = _text(row.get("期间口径"))
        actual_period_group = _schema_period_key(actual_period)
        if allowed and actual_period_group not in allowed:
            invalid_period_records.append(
                (
                    row,
                    actual_period,
                    "|".join(
                        sorted(
                            {
                                item.strip()
                                for item in str(
                                    definition.get("允许期间口径", "")
                                ).split("|")
                                if item.strip()
                            }
                        )
                    ),
                )
            )
    invalid_periods = (
        pd.DataFrame([row for row, _, _ in invalid_period_records])
        if invalid_period_records
        else known_rows.iloc[0:0]
    )
    evidence = [
        _row_evidence(
            row,
            role="期间口径",
            status="期间口径不允许",
            actual_value=actual_period,
            expected_value=allowed_text,
            message=(
                f"期间口径「{actual_period}」不在指标字典允许范围"
                f"（{allowed_text}）内"
            ),
        )
        for row, actual_period, allowed_text in invalid_period_records
    ]
    examples = "；".join(
        f"{_text(row.get('指标编码'))}：{actual_period}"
        for row, actual_period, _ in invalid_period_records[:5]
    )
    failure_reason = (
        f"期间口径不在指标字典允许范围：{examples}"
        + ("等" if len(invalid_period_records) > 5 else "")
        if invalid_period_records
        else ""
    )
    results.append(_result(
        check_type="数据规范", severity="错误", rule_id="SCHEMA_ALLOWED_PERIOD",
        rule_name="期间口径符合指标字典", group=group, actual=len(invalid_periods),
        expected=0, difference=len(invalid_periods),
        status="通过" if invalid_periods.empty else "未通过",
        notes=failure_reason or "全部期间口径均在允许范围内",
        failure_reason=failure_reason,
        involved_metrics=[
            _text(row.get("指标编码")) for row, _, _ in invalid_period_records
        ],
        source_pages=_source_pages(invalid_periods),
        evidence=evidence,
        suggestion="返回 Step2 核对表头列位，或在 Step3 修正期间口径。" if not invalid_periods.empty else "",
    ))

    numeric_types = {"金额", "百分比", "比率", "数量", "倍数"}
    invalid_numeric_records: list[tuple[pd.Series, str, str]] = []
    for _, row in known_rows.iterrows():
        definition = taxonomy_by_code.loc[str(row["指标编码"]).strip()]
        if str(definition.get("数据类型", "")).strip() in numeric_types and _numeric(row.get("数值")) is None:
            unit = _text(row.get("单位")) or taxonomy_units.get(
                _text(row.get("指标编码")), ""
            )
            invalid_numeric_records.append((row, _text(row.get("数值")), unit))
    invalid_numeric = (
        pd.DataFrame([row for row, _, _ in invalid_numeric_records])
        if invalid_numeric_records
        else known_rows.iloc[0:0]
    )
    evidence = [
        _row_evidence(
            row,
            role="数值",
            status="数值无法解析",
            actual_value=actual_value,
            expected_value="可解析的数值",
            message=(
                f"原始值「{actual_value}」无法解析为数字"
                + (f"（单位：{unit}）" if unit else "")
            ),
        )
        for row, actual_value, unit in invalid_numeric_records
    ]
    examples = "；".join(
        f"{_text(row.get('指标编码'))}：{actual_value or '空值'}"
        for row, actual_value, _ in invalid_numeric_records[:5]
    )
    failure_reason = (
        f"金额、百分比、数量或倍数类指标存在无法解析的数值：{examples}"
        + ("等" if len(invalid_numeric_records) > 5 else "")
        if invalid_numeric_records
        else ""
    )
    results.append(_result(
        check_type="数据规范", severity="错误", rule_id="SCHEMA_NUMERIC_VALUE",
        rule_name="数值字段可按指标类型解析", group=group, actual=len(invalid_numeric),
        expected=0, difference=len(invalid_numeric),
        status="通过" if invalid_numeric.empty else "未通过",
        notes=failure_reason or "金额、百分比、数量和倍数均可正常解析",
        failure_reason=failure_reason,
        involved_metrics=[
            _text(row.get("指标编码")) for row, _, _ in invalid_numeric_records
        ],
        source_pages=_source_pages(invalid_numeric),
        evidence=evidence,
        suggestion="返回 Step2 核对原始值和列错位，再重新执行 Step3。" if not invalid_numeric.empty else "",
    ))

    source_key = ["来源文件", "来源工作表", "指标编码", "_验证期间组"]
    exact_duplicate_count = int(group.duplicated(subset=[*source_key, "数值"], keep="first").sum())
    duplicate_mask = group.duplicated(subset=[*source_key, "数值"], keep=False)
    duplicate_rows = group.loc[duplicate_mask]
    evidence = [
        _row_evidence(
            row,
            role="重复记录",
            status="重复记录",
            actual_value=_numeric(row.get("数值"))
            if _numeric(row.get("数值")) is not None
            else _text(row.get("数值")),
            message=(
                "同一来源文件、工作表、指标和期间口径出现完全相同的数值，"
                "请核对是否重复提取同一页或同一表块"
            ),
        )
        for _, row in duplicate_rows.iterrows()
    ]
    failure_reason = (
        f"发现 {exact_duplicate_count} 条完全重复记录，涉及 {len(duplicate_rows)} 条 STEP3 记录。"
        if exact_duplicate_count
        else ""
    )
    results.append(_result(
        check_type="数据规范", severity="警告", rule_id="SCHEMA_EXACT_DUPLICATE",
        rule_name="同一来源不存在完全重复记录", group=group,
        actual=exact_duplicate_count, expected=0, difference=exact_duplicate_count,
        status="通过" if exact_duplicate_count == 0 else "需复核",
        notes=failure_reason or "未发现重复记录",
        failure_reason=failure_reason,
        involved_metrics=[
            _text(row.get("指标编码")) for _, row in duplicate_rows.iterrows()
        ],
        source_pages=_source_pages(duplicate_rows),
        evidence=evidence,
        suggestion="核对 Step2 是否重复提取了同一表格或同一页。" if exact_duplicate_count else "",
    ))

    conflicts: list[pd.DataFrame] = []
    conflict_details: list[tuple[str, list[float]]] = []
    for keys, rows in group.groupby(source_key, dropna=False, sort=False):
        values = pd.to_numeric(rows["数值"], errors="coerce").dropna().unique()
        if len(values) > 1:
            conflicts.append(rows)
            key_values = keys if isinstance(keys, tuple) else (keys,)
            conflict_details.append(
                (
                    " / ".join(_text(value) for value in key_values),
                    [float(value) for value in values],
                )
            )
    conflict_rows = pd.concat(conflicts, ignore_index=True) if conflicts else group.iloc[0:0]
    evidence = [
        _row_evidence(
            row,
            role="同源记录",
            status="同源冲突值",
            actual_value=_numeric(row.get("数值"))
            if _numeric(row.get("数值")) is not None
            else _text(row.get("数值")),
            message=(
                f"同一来源同一指标出现多个数值："
                f"{'、'.join(_format_number(value, '') for value in values)}"
            ),
        )
        for rows, (_, values) in zip(conflicts, conflict_details)
        for _, row in rows.iterrows()
    ]
    examples = "；".join(
        f"{key}：{'、'.join(_format_number(value, '') for value in values)}"
        for key, values in conflict_details[:5]
    )
    failure_reason = (
        f"同一来源文件、工作表、指标和期间口径存在 {len(conflicts)} 组冲突值：{examples}"
        + ("等" if len(conflict_details) > 5 else "")
        if conflicts
        else ""
    )
    results.append(_result(
        check_type="数据规范", severity="错误", rule_id="SCHEMA_CONFLICTING_DUPLICATE",
        rule_name="同一来源同一指标不存在冲突值", group=group,
        actual=len(conflicts), expected=0, difference=len(conflicts),
        status="通过" if not conflicts else "未通过",
        notes=failure_reason or "未发现同源冲突值",
        failure_reason=failure_reason,
        involved_metrics=conflict_rows.get("指标编码", pd.Series(dtype=str)).astype(str).tolist(),
        source_pages=_source_pages(conflict_rows),
        evidence=evidence,
        suggestion="返回 Step2 检查重复行、跨页拼接或列错位。" if conflicts else "",
    ))
    return results


def _completeness_checks(group: pd.DataFrame, taxonomy: pd.DataFrame) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    taxonomy_frame = (
        taxonomy.fillna("").drop_duplicates(subset="指标编码", keep="last")
        .set_index("指标编码")
    )
    taxonomy_names = {
        str(code): _text(row.get("指标名称"))
        for code, row in taxonomy_frame.iterrows()
    }
    taxonomy_units = {
        str(code): _text(row.get("标准单位"))
        for code, row in taxonomy_frame.iterrows()
    }
    present_groups = {str(value).strip() for value in group["_验证期间组"] if str(value).strip()}
    core_rows = group[group["指标编码"].astype(str).isin(REQUIRED_CORE_METRICS)]
    core_groups = set(core_rows["_验证期间组"].astype(str))
    target_groups: list[str] = ["当前期末"] if present_groups else []
    for period_group in ("上期末/期初", "下季度预测"):
        if period_group in core_groups:
            target_groups.append(period_group)

    for period_group in target_groups:
        for code in REQUIRED_CORE_METRICS:
            snapshot = _metric_snapshot(group, code, period_group, taxonomy_names)
            snapshot.unit = snapshot.unit or taxonomy_units.get(code, "")
            available = snapshot.value is not None
            metric_name = snapshot.name or taxonomy_names.get(code, code)
            if available:
                failure_reason = ""
                evidence: list[dict[str, Any]] = []
                notes = "已取得可用数值"
            else:
                evidence = _snapshot_evidence(
                    group, snapshot, period_group, "核心指标", taxonomy_names
                )
                other_periods = [
                    _text(value)
                    for value in dict.fromkeys(
                        group.loc[
                            group["指标编码"].astype(str).str.strip().eq(code)
                            & group["_验证期间组"].astype(str).str.strip().ne(period_group),
                            "_验证期间组",
                        ]
                    )
                    if _text(value)
                ]
                if not snapshot.rows.empty:
                    failure_reason = (
                        f"{period_group}的{metric_name}（{code}）存在 "
                        f"{len(snapshot.rows)} 条记录，但数值均无法解析为数字。"
                    )
                elif other_periods:
                    failure_reason = (
                        f"{period_group}缺少{metric_name}（{code}）；"
                        f"STEP3 仅在期间口径「{'、'.join(other_periods)}」中找到该指标，"
                        "可能期间口径映射不一致，请核对 STEP3 的期间口径。"
                    )
                else:
                    failure_reason = (
                        f"{period_group}缺少{metric_name}（{code}），"
                        "STEP3 标准窄表中没有该指标记录。"
                    )
                notes = failure_reason
            source_pages = "、".join(
                dict.fromkeys(
                    _text(item.get("source_page"))
                    for item in evidence
                    if _text(item.get("source_page"))
                )
            )
            results.append(_result(
                check_type="完整性", severity="错误",
                rule_id=f"REQUIRED_CORE_METRIC:{code}",
                rule_name=f"关键指标完整性：{metric_name}", group=group,
                period=period_group, actual=1 if available else 0, expected=1,
                difference=0 if available else -1, status="通过" if available else "缺失",
                notes=notes,
                failure_reason=failure_reason,
                involved_metrics=(code,),
                source_pages=source_pages or _source_pages(group, (code,), period_group),
                evidence=evidence,
                suggestion="返回 Step2 检查是否漏提，或返回 Step3 检查指标映射。" if not available else "",
            ))
    return results


def _business_checks(
    group: pd.DataFrame,
    rules: pd.DataFrame,
    taxonomy: pd.DataFrame,
) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    taxonomy_names: dict[str, str] = {}
    if (
        isinstance(taxonomy, pd.DataFrame)
        and not taxonomy.empty
        and "指标编码" in taxonomy.columns
    ):
        taxonomy_names = {
            str(code): _text(row.get("指标名称"))
            for code, row in taxonomy.fillna("").drop_duplicates(
                subset="指标编码", keep="last"
            ).set_index("指标编码").iterrows()
        }
    for _, row in group.iterrows():
        code = _text(row.get("指标编码"))
        if code and code not in taxonomy_names:
            taxonomy_names[code] = _text(row.get("指标名称"))
    periods = list(dict.fromkeys(
        str(value).strip() for value in group["_验证期间组"] if str(value).strip()
    ))
    periods.sort(key=lambda value: (PERIOD_GROUP_ORDER.get(value, 999), value))
    for _, rule in rules.iterrows():
        rule_id = str(rule.get("规则ID", "")).strip()
        if not rule_id or str(rule.get("启用", "是")).strip() == "否":
            continue
        try:
            left_code, formula_codes = _formula_codes(rule)
        except Exception:
            left_code, formula_codes = "", []
        configured_metrics = [
            code.strip()
            for code in re.split(r"[、,，|;；\s]+", _text(rule.get("涉及指标")))
            if code.strip()
        ]
        metrics = tuple(
            dict.fromkeys(
                configured_metrics
                or ([left_code, *formula_codes] if left_code else [])
            )
        )
        allowed_periods = {
            validation_period_group(item.strip())
            for item in str(rule.get("适用期间", "")).split("|")
            if item.strip()
        }
        target_periods = [period for period in periods if not allowed_periods or period in allowed_periods]
        tolerance = _safe_tolerance(rule.get("容差", 0))
        tolerance_unit = _text(rule.get("容差单位"))
        rule_name = _text(rule.get("规则名称")) or rule_id
        formula = _text(rule.get("规则说明"))
        duplicate_rule = (
            _text(rule.get("校验方式")) == "跨表一致"
            or rule_id.startswith("DUPLICATE_")
        )
        for period_group in target_periods:
            try:
                evaluation = _evaluate_business_rule(
                    group,
                    rule_id,
                    period_group,
                    formula=formula,
                    tolerance=tolerance,
                    tolerance_unit=tolerance_unit,
                    taxonomy_names=taxonomy_names,
                    rule=rule,
                )
                actual, expected = evaluation.actual, evaluation.expected
                if not evaluation.applicable:
                    status = "不适用"
                    difference = None
                    failure_reason = ""
                    notes = evaluation.notes or "当前数据不满足该规则的执行条件"
                    suggestion = ""
                elif actual is None or expected is None:
                    status = "缺失"
                    difference = None
                    failure_reason = evaluation.failure_reason or (
                        "缺少勾稽所需指标，或公式分母为零"
                    )
                    notes = evaluation.notes or failure_reason
                    suggestion = "返回 Step2/Step3 补齐公式依赖指标并核对数值。"
                else:
                    difference = float(actual - expected)
                    status = (
                        "通过"
                        if _within_tolerance(difference, tolerance)
                        else "未通过"
                    )
                    if status == "通过":
                        failure_reason = ""
                        notes = "检查通过"
                        suggestion = ""
                    else:
                        failure_reason = evaluation.failure_reason or (
                            "披露值与系统计算值的差异超过容差"
                        )
                        notes = evaluation.notes or failure_reason
                        suggestion = "核对来源页中的披露值、单位和资本构成明细。"
            except Exception as exc:
                actual, expected, difference = None, None, None
                status = "执行错误"
                failure_reason = str(exc)
                notes = str(exc)
                suggestion = "检查规则配置或联系系统维护人员。"
                evaluation = RuleEvaluation(
                    actual=None,
                    expected=None,
                    failure_reason=failure_reason,
                    evidence=[],
                    notes=notes,
                    applicable=False,
                )
            source_pages = "、".join(
                dict.fromkeys(
                    _text(item.get("source_page"))
                    for item in evaluation.evidence
                    if _text(item.get("source_page"))
                )
            )
            results.append(_result(
                check_type="跨表一致性" if duplicate_rule else "公式勾稽",
                severity="错误", rule_id=rule_id,
                rule_name=rule_name, group=group,
                period=period_group, actual=actual, expected=expected,
                difference=difference, tolerance=tolerance, status=status,
                notes=notes, involved_metrics=metrics,
                source_pages=source_pages or _source_pages(group, metrics, period_group),
                suggestion=suggestion,
                failure_reason=failure_reason,
                formula=formula,
                tolerance_unit=tolerance_unit,
                evidence=evaluation.evidence,
            ))
    return results


def validate_standard_data(
    frame: pd.DataFrame,
    rules: pd.DataFrame,
    taxonomy: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Run first-stage STEP4 checks against canonical STEP3 narrow-table data."""
    results, _ = validate_standard_data_detailed(frame, rules, taxonomy)
    return results


def validate_standard_data_detailed(
    frame: pd.DataFrame,
    rules: pd.DataFrame,
    taxonomy: pd.DataFrame | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run STEP4 checks and return both summary results and row-level evidence.

    The evidence table keeps one row per STEP3 record that explains a failure:
    which metric was used, what value was found, whether it was missing or
    unparsable, and which source worksheet/page it came from.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return (
            pd.DataFrame(columns=RESULT_COLUMNS),
            pd.DataFrame(columns=EVIDENCE_COLUMNS),
        )
    rule_frame = rules if isinstance(rules, pd.DataFrame) else pd.DataFrame()
    taxonomy_frame = taxonomy if isinstance(taxonomy, pd.DataFrame) else pd.DataFrame()
    results: list[ValidationResult] = []
    working = frame.reset_index(drop=True).copy()
    working["_记录号"] = np.arange(1, len(working) + 1)
    groups = list(_entity_groups(working))
    for index, group in enumerate(groups, 1):
        if not taxonomy_frame.empty:
            results.extend(_schema_checks(group, taxonomy_frame))
            results.extend(_completeness_checks(group, taxonomy_frame))
        if not rule_frame.empty:
            results.extend(_business_checks(group, rule_frame, taxonomy_frame))
        if progress_callback is not None:
            company = (
                _text(group["_验证公司"].iloc[0])
                if not group.empty and "_验证公司" in group.columns
                else ""
            )
            progress_callback(index, len(groups), company)
    for index, item in enumerate(results, 1):
        item.result_id = f"VAL{index:05d}"
    result_frame = pd.DataFrame(
        [item.to_dict() for item in results], columns=RESULT_COLUMNS
    )
    evidence_rows = [
        {"result_id": item.result_id, **row}
        for item in results
        for row in item.evidence
    ]
    evidence_frame = pd.DataFrame(evidence_rows)
    if evidence_frame.empty:
        evidence_frame = pd.DataFrame(columns=EVIDENCE_COLUMNS)
    else:
        evidence_frame = evidence_frame.reindex(columns=EVIDENCE_COLUMNS)
    return result_frame, evidence_frame


def validation_status_summary(results: pd.DataFrame) -> pd.DataFrame:
    order = ("通过", "未通过", "缺失", "需复核", "不适用", "执行错误")
    counts = (
        results.get("status", pd.Series(dtype=str)).fillna("").astype(str).value_counts().to_dict()
    )
    return pd.DataFrame([{"状态": status, "数量": int(counts.get(status, 0))} for status in order])
