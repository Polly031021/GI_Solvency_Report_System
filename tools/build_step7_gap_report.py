"""Build a STEP7 missing-metric report for an existing integrated standard-data workbook.

Usage:
    python tools/build_step7_gap_report.py <source.xlsx> <output.xlsx>

The source workbook is expected to contain a 标准数据 sheet with at least:
    公司, 报告期, 指标编码, 指标名称, 期间口径, 数值, 单位, 数据类型

It can also contain a _系统字段 sheet; company codes are read from it when available.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_normalizer import NARROW_TABLE_COLUMNS, STANDARD_COLUMNS  # noqa: E402
from services.solvency_step7_metric_catalog import (  # noqa: E402
    step7_metric_requirement_catalog,
)
from services.step3_metric_gap import build_step7_metric_gap  # noqa: E402


SHEET_INSTRUCTION = "检查结论"
SHEET_CORE_MISSING = "核心缺口明细_最新期"
SHEET_ALL_MISSING = "全部缺口明细_最新期"
SHEET_COMPANY = "公司缺口汇总_最新期"
SHEET_METRIC = "指标覆盖汇总_最新期"
SHEET_ALL_PERIOD = "全期间缺口汇总"
SHEET_PERIOD = "期间覆盖"
SHEET_QUALITY = "数据质量"
SHEET_FORMAT = "格式对照"
SHEET_FILL_HELP = "填写说明"

CORE_CODES = {
    "ACTUAL_CAPITAL",
    "MINIMUM_CAPITAL",
    "CORE_T1_CAPITAL",
    "CORE_T2_CAPITAL",
    "ANC_T1_CAPITAL",
    "ANC_T2_CAPITAL",
    "COMBINED_SOLVENCY_RATIO",
    "CORE_SOLVENCY_RATIO",
    "SIGNED_PREMIUM",
    "SIGNED_PREMIUM_MOTOR",
    "NET_PROFIT",
    "COMBINED_RATIO",
    "TOTAL_ASSETS",
    "RECOGNIZED_ASSETS",
    "RECOGNIZED_LIABILITIES",
    "REGISTERED_CAPITAL",
}

CONDITIONAL_CODES = {
    "POLICY_SURPLUS_CORE_T1",
    "POLICY_SURPLUS_CORE_T2",
    "POLICY_SURPLUS_ANC_T1",
    "POLICY_SURPLUS_ANC_T2",
    "INSURANCE_RISK_CAPITAL",
    "CONTRACT_LOSS_ABSORPTION_EFFECT",
    "OVERSEAS_FIXED_INCOME_RISK_CAPITAL",
    "OVERSEAS_EQUITY_RISK_CAPITAL",
    "FOREIGN_EXCHANGE_RISK_CAPITAL",
    "REAL_ESTATE_RISK_CAPITAL",
    "MARKET_RISK_DIVERSIFICATION_EFFECT",
    "CREDIT_RISK_DIVERSIFICATION_EFFECT",
    "INTEREST_RATE_RISK_CAPITAL",
    "EQUITY_RISK_CAPITAL",
    "SPREAD_RISK_CAPITAL",
    "COUNTERPARTY_RISK_CAPITAL",
}

DETAIL_BASE_COLUMNS = [
    "公司统一编码",
    "公司",
    "同业分类",
    "报告期",
    "指标编码",
    "指标名称",
    "一级模块",
    "二级模块",
    "数据类型",
    "单位",
    "期间口径",
    "数值",
    "指标类别",
    "备注",
]

PERIOD_ORDER = {"2022Q4": 0, "2023Q4": 1, "2024Q4": 2, "2025Q4": 3}


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _metric_category(code: str) -> str:
    if code in CORE_CODES:
        return "核心必备"
    if code in CONDITIONAL_CODES:
        return "明细/条件（非所有公司适用）"
    return "其他基础指标"


def _read_public_sheet(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="标准数据", dtype={"报告期": str, "指标编码": str})
    for column in (
        "公司",
        "同业分类",
        "报告期",
        "一级模块",
        "二级模块",
        "指标编码",
        "指标名称",
        "期间口径",
        "单位",
        "数据类型",
        "指标属性",
        "来源类型",
    ):
        if column in df.columns:
            df[column] = df[column].fillna("").astype(str).str.strip()
    if "数值" in df.columns:
        df["数值"] = pd.to_numeric(df["数值"], errors="coerce")
    return df


def _company_code_map(path: Path) -> dict[str, str]:
    try:
        internal = pd.read_excel(
            path,
            sheet_name="_系统字段",
            dtype=str,
            usecols=["公司", "公司统一编码"],
        )
    except Exception:
        return {}
    internal.columns = [str(column).strip() for column in internal.columns]
    internal["公司"] = internal["公司"].fillna("").astype(str).str.strip()
    internal["公司统一编码"] = internal["公司统一编码"].fillna("").astype(str).str.strip()
    internal = internal[(internal["公司"].ne("")) & (internal["公司统一编码"].ne(""))]
    return internal.drop_duplicates("公司", keep="first").set_index("公司")["公司统一编码"].to_dict()


def _latest_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    working = df.copy()
    working["_order"] = working["报告期"].map(PERIOD_ORDER).fillna(-1)
    latest = (
        working.sort_values(["公司", "_order"], kind="stable")
        .groupby("公司", as_index=False)
        .tail(1)[["公司", "报告期", "_order"]]
        .drop_duplicates(["公司", "报告期"])
    )
    latest_data = working.merge(
        latest[["公司", "报告期"]],
        on=["公司", "报告期"],
        how="inner",
    )
    return latest_data.drop(columns=["_order"]), latest


def _enrich_detail(frame: pd.DataFrame, code_map: dict[str, str]) -> pd.DataFrame:
    detail = frame.copy()
    if "公司统一编码" not in detail.columns:
        detail["公司统一编码"] = ""
    mapped = detail["公司"].map(code_map).fillna("")
    existing = detail["公司统一编码"].fillna("").astype(str).str.strip()
    detail["公司统一编码"] = existing.where(existing.ne(""), mapped)
    detail["指标类别"] = detail["指标编码"].map(_metric_category)
    for column in DETAIL_BASE_COLUMNS:
        if column not in detail.columns:
            detail[column] = ""
    return detail.reindex(columns=DETAIL_BASE_COLUMNS)


def _company_summary(
    report,
    df: pd.DataFrame,
    code_map: dict[str, str],
) -> pd.DataFrame:
    cov = report.coverage.copy()
    cov["公司统一编码"] = cov["公司统一编码"].fillna("").astype(str)
    mapped = cov["公司"].map(code_map).fillna("")
    cov["公司统一编码"] = cov["公司统一编码"].where(cov["公司统一编码"].str.strip().ne(""), mapped)
    periods = df.groupby("公司")["报告期"].nunique().to_dict()
    cov["历史期数"] = cov["公司"].map(periods).fillna(0).astype(int)
    core_missing = report.missing[report.missing["指标编码"].isin(CORE_CODES)]
    core_counts = core_missing.groupby(["公司", "报告期"]).size()
    cov["核心缺失数"] = [
        int(core_counts.get((company, period), 0))
        for company, period in zip(cov["公司"], cov["报告期"])
    ]
    cov["明细及条件缺失数"] = (
        cov["缺失基础指标数"].astype(int) - cov["核心缺失数"]
    )
    cov["覆盖率"] = (cov["覆盖率"].astype(float) * 100).round(1)
    cov = cov.rename(columns={"覆盖率": "基础指标覆盖率(%)"})
    columns = [
        "公司统一编码",
        "公司",
        "同业分类",
        "报告期",
        "历史期数",
        "必需基础指标数",
        "已提取基础指标数",
        "缺失基础指标数",
        "核心缺失数",
        "明细及条件缺失数",
        "基础指标覆盖率(%)",
        "派生指标待计算数",
        "缺失指标编码",
    ]
    return cov.reindex(columns=columns).sort_values(
        ["核心缺失数", "缺失基础指标数", "公司"],
        ascending=[False, False, True],
    )


def _metric_summary(report, total_companies: int) -> pd.DataFrame:
    catalog = step7_metric_requirement_catalog()
    base = catalog[catalog["补录方式"].eq("直接补录")].copy()
    missing = report.missing.copy()
    counts = (
        missing.groupby("指标编码")
        .agg(缺失公司数=("公司", "nunique"))
        .reset_index()
    )
    names = (
        missing.groupby("指标编码")["公司"]
        .agg(lambda values: "、".join(sorted(set(values.astype(str)))))
        .reset_index(name="缺失公司")
    )
    base = base.merge(counts, on="指标编码", how="left").merge(
        names,
        on="指标编码",
        how="left",
    )
    base["缺失公司数"] = base["缺失公司数"].fillna(0).astype(int)
    base["缺失公司"] = base["缺失公司"].fillna("")
    base["维护说明"] = base["维护说明"].fillna("")
    base["Step7使用图表"] = base["Step7使用图表"].fillna("")
    base["覆盖公司数"] = total_companies - base["缺失公司数"]
    base["覆盖率(%)"] = (base["覆盖公司数"] / total_companies * 100).round(1)
    base["指标类别"] = base["指标编码"].map(_metric_category)
    columns = [
        "指标编码",
        "指标名称",
        "指标类别",
        "标准单位",
        "Step7使用图表数",
        "Step7使用图表",
        "覆盖公司数",
        "缺失公司数",
        "覆盖率(%)",
        "缺失公司",
        "维护说明",
    ]
    return base.reindex(columns=columns).sort_values(
        ["指标类别", "缺失公司数", "指标编码"],
        ascending=[True, False, True],
    )


def _all_period_summary(report, code_map: dict[str, str]) -> pd.DataFrame:
    cov = report.coverage.copy()
    mapped = cov["公司"].map(code_map).fillna("")
    if "公司统一编码" not in cov.columns:
        cov["公司统一编码"] = ""
    existing = cov["公司统一编码"].fillna("").astype(str).str.strip()
    cov["公司统一编码"] = existing.where(existing.ne(""), mapped)
    core_missing = report.missing[report.missing["指标编码"].isin(CORE_CODES)]
    core_counts = core_missing.groupby(["公司", "报告期"]).size()
    cov["核心缺失数"] = [
        int(core_counts.get((company, period), 0))
        for company, period in zip(cov["公司"], cov["报告期"])
    ]
    cov["覆盖率"] = (cov["覆盖率"].astype(float) * 100).round(1)
    cov = cov.rename(columns={"覆盖率": "基础指标覆盖率(%)"})
    columns = [
        "公司统一编码",
        "公司",
        "同业分类",
        "报告期",
        "必需基础指标数",
        "已提取基础指标数",
        "缺失基础指标数",
        "核心缺失数",
        "基础指标覆盖率(%)",
        "派生指标待计算数",
        "缺失指标编码",
    ]
    return cov.reindex(columns=columns).sort_values(["公司", "报告期"])


def _period_summary(report, df: pd.DataFrame) -> pd.DataFrame:
    rows = (
        df.groupby("报告期")
        .agg(
            数据行数=("公司", "size"),
            公司数=("公司", "nunique"),
            指标编码数=("指标编码", "nunique"),
        )
        .reset_index()
    )
    coverage = (
        report.coverage.groupby("报告期")
        .agg(
            必需基础指标组合=("必需基础指标数", "sum"),
            已提取基础指标组合=("已提取基础指标数", "sum"),
            缺失基础指标组合=("缺失基础指标数", "sum"),
        )
        .reset_index()
    )
    rows = rows.merge(coverage, on="报告期", how="left")
    rows["基础指标覆盖率(%)"] = (
        rows["已提取基础指标组合"] / rows["必需基础指标组合"] * 100
    ).round(1)
    rows["年度"] = rows["报告期"].astype(str).str[:4]
    rows = rows.sort_values("报告期")
    # 2021 年整年缺失（Step7 五年图需要）
    if "2021Q4" not in set(rows["报告期"]):
        rows = pd.concat(
            [
                pd.DataFrame(
                    [
                        {
                            "报告期": "2021Q4",
                            "数据行数": 0,
                            "公司数": 0,
                            "指标编码数": 0,
                            "必需基础指标组合": 0,
                            "已提取基础指标组合": 0,
                            "缺失基础指标组合": 0,
                            "基础指标覆盖率(%)": None,
                            "年度": "2021",
                        }
                    ]
                ),
                rows,
            ],
            ignore_index=True,
        )
    for column in ("数据行数", "公司数", "指标编码数", "必需基础指标组合", "已提取基础指标组合", "缺失基础指标组合"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce").fillna(0).astype(int)
    return rows


def _quality_rows(path: Path, df: pd.DataFrame) -> list[tuple[str, str, str]]:
    units = df["单位"].fillna("").astype(str).value_counts().to_dict()
    duplicate_keys = int(
        df.duplicated(subset=["公司", "报告期", "指标编码", "期间口径"], keep=False).sum()
    )
    numeric_null = int(pd.to_numeric(df["数值"], errors="coerce").isna().sum())
    short = (
        df.groupby("公司")["报告期"]
        .nunique()
        .reset_index(name="期数")
        .query("期数 < 4")
        .sort_values(["期数", "公司"])
    )
    short_text = "、".join(f"{row.公司}({row.期数}期)" for row in short.itertuples())
    rows = [
        ("报告期范围", "2022Q4–2025Q4", "只有年度 Q4 数据，没有 Q1–Q3；STEP7 年度图可直接使用。"),
        ("2021 年度", "整年缺失", "STEP7 的“近五年”图实际只能展示 2022–2025 四年；若必须五年，需要补 2021Q4。"),
        ("不足 4 期的公司", short_text or "无", "这些公司历史趋势图会缺少早期年份。"),
        ("金额单位", "万元为主，另有 1,016 行“元”", "“元”行全部是 EPS / 车险车均保费，属于合理例外，不应当作万元。"),
        ("单位占位符", f"金额 {units.get('金额',0)} 行；空白 {units.get('',0)} 行；百分比 {units.get('百分比',0)} 行", "建议统一为“万元 / % / 倍”等标准单位后再进入 STEP5。"),
        ("数值为空", f"{numeric_null} 行", "数值为空或无法解析，不会参与指标覆盖判断。"),
        ("重复键", f"{duplicate_keys} 行", "同一 公司+报告期+指标+期间 有多行；STEP7 会按后出现优先去重，建议先确认保留哪条。"),
        ("公司名称", "标准数据 89 家；_系统字段 合并后 98 个身份", "直接上传整本工作簿到 STEP5 时，公司名会被拆分；建议只上传 14 列标准数据或先统一名称。"),
    ]
    return rows


def _format_rows(path: Path) -> pd.DataFrame:
    public = pd.read_excel(path, sheet_name="标准数据", nrows=0)
    internal = pd.read_excel(path, sheet_name="_系统字段", nrows=0)
    public_columns = [str(column).strip() for column in public.columns]
    internal_columns = [str(column).strip() for column in internal.columns]
    union = list(dict.fromkeys([*STANDARD_COLUMNS, *public_columns, *internal_columns]))
    rows = []
    for column in union:
        in_public = column in public_columns
        in_narrow = column in NARROW_TABLE_COLUMNS
        in_internal = column in internal_columns
        if column == "机构类型":
            note = "标准数据展示字段，对应内部公司类型"
        elif column == "名称核对":
            note = "标准数据展示字段，名称来源核对结果"
        elif column in ("公司全称", "业务特色"):
            note = "标准数据多出的展示字段"
        elif column == "公司统一编码":
            note = "标准数据没有；_系统字段有"
        elif column in NARROW_TABLE_COLUMNS:
            note = "STEP3 14 列标准字段"
        else:
            note = "内部溯源字段"
        rows.append(
            {
                "字段": column,
                "标准数据(18列)": "✓" if in_public else "",
                "STEP3导出(14列)": "✓" if in_narrow else "",
                "_系统字段(34列)": "✓" if in_internal else "",
                "说明": note,
            }
        )
    return pd.DataFrame(rows)


def _style_sheet(sheet, *, freeze: str = "A2", numeric_columns: tuple[int, ...] = ()) -> None:
    header_fill = PatternFill("solid", fgColor="00338D")
    header_font = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
    body_font = Font(name="Microsoft YaHei", size=10)
    thin = Side(style="thin", color="D9E2EC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    sheet.freeze_panes = freeze
    if sheet.max_row >= 1 and sheet.max_column >= 1:
        sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    sheet.row_dimensions[1].height = 34
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
    for column_cells in sheet.iter_cols(min_row=1, max_row=min(sheet.max_row, 400)):
        values = []
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            values.append(sum(2 if ord(char) > 127 else 1 for char in value))
        width = min(max(max(values, default=8) + 3, 10), 44)
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = width
    for column_index in numeric_columns:
        for row in range(2, sheet.max_row + 1):
            cell = sheet.cell(row=row, column=column_index)
            cell.fill = PatternFill("solid", fgColor="FFF2CC")
            cell.number_format = "#,##0.00####"
            cell.alignment = Alignment(horizontal="right", vertical="top")


def build_report(source: Path, output: Path) -> Path:
    df = _read_public_sheet(source)
    work = df[df["指标编码"].ne("")].copy()
    code_map = _company_code_map(source)
    latest_data, _ = _latest_data(work)
    report_latest = build_step7_metric_gap(latest_data)
    report_all = build_step7_metric_gap(work)

    total_companies = int(work["公司"].nunique())
    requirement = report_latest.requirement_catalog
    total_codes = int(len(requirement))
    base_codes = int(requirement["补录方式"].eq("直接补录").sum())
    derived_codes = int(requirement["是否派生"].eq("是").sum())
    core_missing = report_latest.missing[report_latest.missing["指标编码"].isin(CORE_CODES)]
    core_companies = int(core_missing["公司"].nunique())
    core_codes_count = int(core_missing["指标编码"].nunique())
    all_missing_companies = int(report_latest.missing["公司"].nunique())

    detail_all = _enrich_detail(report_latest.missing, code_map)
    detail_core = detail_all[detail_all["指标编码"].isin(CORE_CODES)].copy()
    company_summary = _company_summary(report_latest, work, code_map)
    metric_summary = _metric_summary(report_latest, total_companies)
    all_period = _all_period_summary(report_all, code_map)
    period_summary = _period_summary(report_all, work)
    quality = pd.DataFrame(
        _quality_rows(source, work),
        columns=["检查项", "现状", "说明"],
    )
    format_rows = _format_rows(source)

    conclusion_rows = [
        ("数据文件", source.name),
        ("检查工作表", "标准数据"),
        ("数据规模", f"{len(df):,} 行（有效指标行 {len(work):,}）；{total_companies} 家公司；{work['报告期'].nunique()} 个报告期；{work['指标编码'].nunique()} 个有效指标编码"),
        ("报告期", "、".join(sorted(work["报告期"].dropna().astype(str).unique()))),
        ("STEP7 指标范围", f"{total_codes} 个指标编码：{base_codes} 个基础指标可直接补录，{derived_codes} 个派生指标由系统计算"),
        ("检查口径", "每家公司取最后一个报告期（87 家 2025Q4、1 家 2023Q4、1 家 2022Q4）"),
        ("最新期基础指标覆盖率", f"{report_latest.coverage_ratio:.1%}"),
        ("最新期缺失", f"{report_latest.missing_count} 个“公司×指标”缺口；涉及 {report_latest.missing_code_count} 个指标、{all_missing_companies} 家公司"),
        ("其中核心必备缺口", f"{len(core_missing)} 个；涉及 {core_companies} 家公司、{core_codes_count} 个核心指标（详见“{SHEET_CORE_MISSING}”）"),
        ("全期间缺失", f"{report_all.missing_count} 个“公司×报告期×指标”缺口；覆盖率 {report_all.coverage_ratio:.1%}"),
        ("派生指标待计算", f"最新期 {len(report_latest.derived_gaps)} 条；全期间 {len(report_all.derived_gaps)} 条（补录依赖后自动计算）"),
        ("指标整体缺失", "没有“全行业完全缺失”的指标；缺失集中在个别公司或明细/条件指标"),
        ("格式判断", "标准数据 18 列 = STEP3 14 列标准字段 + 公司全称/机构类型/业务特色/名称核对，14 个核心字段齐全，可直接用于缺失检查；但整本上传 STEP5 时 _系统字段 会把 89 家拆成 98 个身份"),
        ("直接使用建议", "做缺失检查：直接用本报告。进入 STEP5/STEP6/STEP7：只上传 14 列标准数据，或先统一公司名称后再用整本工作簿"),
        ("优先补录", f"先补核心缺口（{len(core_missing)} 条），再按图表需要补明细/条件指标；2021 年度数据整体缺失，需要另行补期"),
    ]
    conclusion = pd.DataFrame(conclusion_rows, columns=["项目", "结论"])

    instructions = pd.DataFrame(
        [
            ("核心缺口明细", "优先处理：这些指标直接影响 STEP7 主图、关键偿付数据概览或资本充足率组合图。"),
            ("全部缺口明细", "包含核心与明细/条件指标；按公司、指标排序。条件指标并非每家公司都适用，按实际披露补录。"),
            ("数值列", "黄色列留空，直接填写数字。金额标准单位为万元；若原始数据是元，请把“单位”改为“元”，合并时自动除以 10000。"),
            ("期间口径", "默认按指标类型预填：存量指标本季度末数，流量指标本年累计数；如实际口径不同可直接修改。"),
            ("合并键", "公司统一编码 + 报告期 + 指标编码 + 期间口径；公司统一编码为空时使用公司名称。"),
            ("全期间缺口", "仅统计已有数据的公司×报告期组合，用于判断历史年度是否影响 STEP7 的五年趋势；完整期间覆盖见“期间覆盖”页。"),
            ("2021 年", "当前数据从 2022Q4 开始，STEP7 五年图实际只能画 4 年；如报告要求五年，需要补 2021Q4。"),
            ("格式", "本报告基于“标准数据”页生成；未改动源数据。"),
        ],
        columns=["项目", "说明"],
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        conclusion.to_excel(writer, sheet_name=SHEET_INSTRUCTION, index=False)
        detail_core.to_excel(writer, sheet_name=SHEET_CORE_MISSING, index=False)
        detail_all.to_excel(writer, sheet_name=SHEET_ALL_MISSING, index=False)
        company_summary.to_excel(writer, sheet_name=SHEET_COMPANY, index=False)
        metric_summary.to_excel(writer, sheet_name=SHEET_METRIC, index=False)
        all_period.to_excel(writer, sheet_name=SHEET_ALL_PERIOD, index=False)
        period_summary.to_excel(writer, sheet_name=SHEET_PERIOD, index=False)
        quality.to_excel(writer, sheet_name=SHEET_QUALITY, index=False)
        format_rows.to_excel(writer, sheet_name=SHEET_FORMAT, index=False)
        instructions.to_excel(writer, sheet_name=SHEET_FILL_HELP, index=False)

        for sheet_name in writer.book.sheetnames:
            sheet = writer.book[sheet_name]
            if sheet_name in (SHEET_CORE_MISSING, SHEET_ALL_MISSING):
                _style_sheet(sheet, freeze="L2", numeric_columns=(12,))
                value_letter = get_column_letter(12)
                validation = DataValidation(
                    type="decimal",
                    operator="between",
                    formula1="-1000000000000000",
                    formula2="1000000000000000",
                    allow_blank=True,
                    showErrorMessage=True,
                    errorTitle="数值格式",
                    error="请填写数字，不要带千分位或单位文字。",
                )
                sheet.add_data_validation(validation)
                validation.add(f"{value_letter}2:{value_letter}{max(sheet.max_row,2)}")
            elif sheet_name == SHEET_INSTRUCTION:
                _style_sheet(sheet, freeze="A2")
                sheet.column_dimensions["A"].width = 24
                sheet.column_dimensions["B"].width = 110
            elif sheet_name == SHEET_FILL_HELP:
                _style_sheet(sheet, freeze="A2")
                sheet.column_dimensions["A"].width = 22
                sheet.column_dimensions["B"].width = 100
            elif sheet_name in (SHEET_PERIOD, SHEET_QUALITY):
                _style_sheet(sheet, freeze="A2")
            else:
                _style_sheet(sheet, freeze="A2")

        # 两个明细表加上数字列提示
        for name in (SHEET_CORE_MISSING, SHEET_ALL_MISSING):
            sheet = writer.book[name]
            sheet.auto_filter.ref = sheet.dimensions
            sheet.cell(row=1, column=12).comment = None

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source integrated workbook")
    parser.add_argument("output", type=Path, help="output report workbook")
    args = parser.parse_args()
    result = build_report(args.source, args.output)
    print(f"Wrote {result}")


if __name__ == "__main__":
    main()
