# -*- coding: utf-8 -*-
"""查看人保/平安/太保 2022-2025 的 PDF 实际能提取到哪些数据。

这个脚本走完整链路（定位 → 提取 → STEP3 标准化），逐份输出：
  * 封面识别的公司/报告期、命中的公司规则
  * 定位到的目标表与物理页码
  * 每张表提取到的行数
  * 标准化后的窄表记录数、指标编码、有值指标
并把每份的标准窄表 / 填报汇总 / 匹配诊断写成 Excel，便于逐条核对。

用法（在项目根目录）：
  ./venv/Scripts/python.exe -m tests.test_three_companies_extraction
  ./venv/Scripts/python.exe -m tests.test_three_companies_extraction --years 2025
  ./venv/Scripts/python.exe -m tests.test_three_companies_extraction --company 人保产险 --years 2024,2025
  ./venv/Scripts/python.exe -m tests.test_three_companies_extraction --years all --method auto

产物目录：tmp/three_companies_extraction/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from services.batch_processor import build_context_from_profile, locate_one_pdf_bytes
from services.batch_workflow import extract_document_tables, standardize_document_tables
from services.report_profiles import load_profile_workbook

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "tmp/three_companies_extraction"
PROFILE_PATH = ROOT / "config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx"

COMPANY_FILES: dict[str, dict[str, str]] = {
    "人保产险": {
        "2022": "Data/pdfs(2022)/人保产险.pdf",
        "2023": "Data/pdfs(2023)/人保产险.pdf",
        "2024": "Data/pdfs(2024)/人保产险.pdf",
        "2025": "Data/pdfs(2025)/人保产险（偿付能力）.pdf",
    },
    "平安产险": {
        "2022": "Data/pdfs(2022)/平安产险.pdf",
        "2023": "Data/pdfs(2023)/平安产险.pdf",
        "2024": "Data/pdfs(2024)/平安产险.pdf",
        "2025": "Data/pdfs(2025)/平安产险（偿付能力）.pdf",
    },
    "太保产险": {
        "2022": "Data/pdfs(2022)/太保产险.pdf",
        "2023": "Data/pdfs(2023)/太保产险.pdf",
        "2024": "Data/pdfs(2024)/太保产险.pdf",
        "2025": "Data/pdfs(2025)/太保财险（偿付能力）.pdf",
    },
    "国寿财险": {
        "2022": "Data/pdfs(2022)/国寿财险.pdf",
        "2023": "Data/pdfs(2023)/国寿财险.pdf",
        "2024": "Data/pdfs(2024)/国寿财险.pdf",
        "2025": "Data/pdfs(2025)/国寿财险.pdf",
    },
    "众安产险": {
        "2022": "Data/pdfs(2022)/众安产险.pdf",
        "2023": "Data/pdfs(2023)/众安产险.pdf",
        "2024": "Data/pdfs(2024)/众安产险.pdf",
        "2025": "Data/pdfs(2025)/众安产险（偿付能力）.pdf",
    },
    "英大财产": {
        "2022": "Data/pdfs(2022)/英大财产.pdf",
        "2023": "Data/pdfs(2023)/英大财产.pdf",
        "2024": "Data/pdfs(2024)/英大财产.pdf",
        "2025": "Data/pdfs(2025)/英大财产.pdf",
    },
    "大地财产": {
        "2022": "Data/pdfs(2022)/大地财产.pdf",
        "2023": "Data/pdfs(2023)/大地财产.pdf",
        "2024": "Data/pdfs(2024)/大地财产.pdf",
        "2025": "Data/pdfs(2025)/大地财产.pdf",
    },
    "中华联合": {
        "2022": "Data/pdfs(2022)/中华联合.pdf",
        "2023": "Data/pdfs(2023)/中华联合.pdf",
        "2024": "Data/pdfs(2024)/中华联合.pdf",
        "2025": "Data/pdfs(2025)/中华联合.pdf",
    },
    "太平财险": {
        "2022": "Data/pdfs(2022)/太平财险.pdf",
        "2023": "Data/pdfs(2023)/太平财险.pdf",
        "2024": "Data/pdfs(2024)/太平财险.pdf",
        "2025": "Data/pdfs(2025)/太平产险（偿付能力）.pdf",
    },
    "阳光产险": {
        "2022": "Data/pdfs(2022)/阳光产险.pdf",
        "2023": "Data/pdfs(2023)/阳光产险.pdf",
        "2024": "Data/pdfs(2024)/阳光产险.pdf",
        "2025": "Data/pdfs(2025)/阳光产险（偿付能力）.pdf",
    },
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--company",
        default=",".join(COMPANY_FILES),
        help="公司名，逗号分隔；默认三家全跑（人保产险,平安产险,太保产险）",
    )
    parser.add_argument(
        "--years",
        default="all",
        help="年份，逗号分隔或 all；默认 2022,2023,2024,2025",
    )
    parser.add_argument(
        "--method",
        default="rule",
        choices=["rule", "auto"],
        help="提取方式：rule=规则法（默认，离线）；auto=智能模式（需要 AI 配置）",
    )
    parser.add_argument(
        "--no-excel",
        action="store_true",
        help="只打印统计，不写 Excel",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="产物目录；默认 tmp/three_companies_extraction。并行跑多家时请各自指定，避免互相覆盖",
    )
    parser.add_argument(
        "--strict-metrics",
        action="store_true",
        help="只保留 STEP7 需求清单内的指标；默认保留全部提取到的指标（含未映射）",
    )
    return parser.parse_args()


def _format_metrics(data: pd.DataFrame, limit: int = 12) -> str:
    if data.empty or "指标编码" not in data.columns:
        return "—"
    numeric = pd.to_numeric(data.get("数值"), errors="coerce")
    codes = data.loc[numeric.notna(), "指标编码"].dropna().astype(str)
    unique = sorted({code for code in codes if code and code != "nan"})
    if not unique:
        return "—"
    head = "、".join(unique[:limit])
    return f"{head}（共 {len(unique)} 个有值编码）" if len(unique) > limit else head


def _module_breakdown(data: pd.DataFrame) -> str:
    if data.empty:
        return "—"
    columns = [column for column in ("一级模块", "二级模块") if column in data.columns]
    if not columns:
        return "—"
    grouping = data.groupby(columns, dropna=False)["指标编码"].nunique()
    parts = [
        f"{' / '.join(str(value) for value in key)}: {count}"
        for key, count in grouping.sort_values(ascending=False).head(8).items()
    ]
    return "；".join(parts)


def _write_workbook(
    company: str, year: str, standard: dict, path: Path, output_dir: Path = OUTPUT_DIR
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{company}_{year}.xlsx"
    workbook = standard.get("workbook_bytes") or b""
    if workbook:
        target.write_bytes(workbook)
        return target
    data = standard.get("data")
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        if isinstance(data, pd.DataFrame) and not data.empty:
            data.to_excel(writer, sheet_name="标准窄表", index=False)
        summary = standard.get("summary")
        if isinstance(summary, pd.DataFrame) and not summary.empty:
            summary.to_excel(writer, sheet_name="填报汇总", index=False)
        diagnostics = standard.get("diagnostics")
        if isinstance(diagnostics, pd.DataFrame) and not diagnostics.empty:
            diagnostics.to_excel(writer, sheet_name="匹配诊断", index=False)
    return target


def run_one(
    company: str,
    year: str,
    path: Path,
    ctx,
    *,
    method: str,
    write_excel: bool,
    keep_unmapped: bool,
    output_dir: Path = OUTPUT_DIR,
) -> dict[str, object]:
    row: dict[str, object] = {
        "公司": company,
        "年份": year,
        "文件": path.name,
        "状态": "失败",
        "说明": "",
    }
    print(f"\n=== {company} {year} ===")
    print(f"文件: {path}")
    if not path.exists():
        row["说明"] = "文件不存在"
        print("!! 文件不存在，跳过")
        return row

    pdf_bytes = path.read_bytes()
    located = locate_one_pdf_bytes(company, pdf_bytes, ctx, mode="rule")
    metadata = located.get("metadata") or {}
    matches = located.get("matches") or []
    located_tables = [match for match in matches if match.pages]
    row.update(
        {
            "封面公司": metadata.get("公司", ""),
            "报告期": metadata.get("报告期", ""),
            "页数": metadata.get("页数", 0),
            "规则": located.get("rule", "（未命中公司规则）"),
            "定位表数": len(located_tables),
            "定位明细": "；".join(
                f"{match.table_id}{match.pages}" for match in located_tables
            ),
        }
    )
    print(
        f"封面: {metadata.get('公司', '—')} | 报告期: {metadata.get('报告期', '—')} "
        f"| 页数: {metadata.get('页数', 0)}"
    )
    print(f"规则: {located.get('rule') or '（未命中公司规则）'}")
    for match in matches:
        note = f" | {match.rule_note}" if getattr(match, "rule_note", "") else ""
        print(f"  定位 {match.table_id}: {match.pages}{note}")

    outcome = extract_document_tables(
        company=company,
        pdf_bytes=pdf_bytes,
        matches=matches,
        method=method,
        allow_llm_fallback=False,
        whole_page=True,
        api_key=ctx.api_key,
        base_url=ctx.base_url,
        model=ctx.model,
    )
    row["提取表数"] = len(outcome.tables)
    row["提取行数"] = sum(len(getattr(table, "rows", []) or []) for table in outcome.tables)
    row["提取状态"] = outcome.status
    print(f"提取: {len(outcome.tables)} 张表 / {row['提取行数']} 行 | {outcome.status}")
    for log in outcome.logs:
        print(
            f"  {log.get('目标表', '')} 第 {log.get('物理页码', '')} 页 "
            f"| {log.get('识别方式', '')} | {log.get('状态', '')} "
            f"| {str(log.get('说明', ''))[:80]}"
        )
    if not outcome.tables:
        row["说明"] = f"提取为空：{outcome.note}"
        print(f"!! 提取为空：{outcome.note}")
        return row

    standard = standardize_document_tables(
        company=company,
        pdf_name=path.name,
        pdf_bytes=pdf_bytes,
        tables=outcome.tables,
        context=ctx,
        keep_unmapped=keep_unmapped,
    )
    data = standard.get("data")
    if not isinstance(data, pd.DataFrame):
        data = pd.DataFrame()
    numeric = pd.to_numeric(data.get("数值"), errors="coerce")
    codes = data.get("指标编码", pd.Series(dtype=str)).astype(str).str.strip()
    raw_codes = codes[codes.str.startswith("RAW_")]
    row.update(
        {
            "状态": standard.get("状态", "失败"),
            "说明": str(standard.get("说明", ""))[:160],
            "标准化记录数": len(data),
            "指标编码数": codes[codes.ne("")].nunique(),
            "有值指标数": codes[numeric.notna()].nunique(),
            "未映射指标数": raw_codes.nunique(),
            "指标样例": _format_metrics(data),
            "模块分布": _module_breakdown(data),
        }
    )
    print(
        f"标准化: {len(data)} 条 / 指标编码 {row['指标编码数']} 个 "
        f"（有值 {row['有值指标数']}，未映射 {row['未映射指标数']}）"
        f"| {standard.get('状态')}"
    )
    print(f"指标: {row['指标样例']}")
    print(f"模块: {row['模块分布']}")
    warnings = standard.get("warnings") or []
    for warning in warnings[:5]:
        print(f"  警告: {warning}")
    if write_excel:
        target = _write_workbook(company, year, standard, path, output_dir)
        try:
            row["产物"] = str(target.relative_to(ROOT))
        except ValueError:
            row["产物"] = str(target)
        print(f"产物: {row['产物']}")
    return row


def main() -> int:
    args = _parse_args()
    companies = [item.strip() for item in args.company.split(",") if item.strip()]
    if args.years.strip().lower() == "all":
        years = ["2022", "2023", "2024", "2025"]
    else:
        years = [item.strip() for item in args.years.split(",") if item.strip()]

    profile = load_profile_workbook(PROFILE_PATH.read_bytes(), project_root=ROOT)
    ctx = build_context_from_profile(profile, api_key="", base_url="", model="")

    rows: list[dict[str, object]] = []
    keep_unmapped = not args.strict_metrics
    out_dir = Path(args.out_dir) if args.out_dir.strip() else OUTPUT_DIR
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if keep_unmapped:
        print("指标口径：保留全部提取到的指标（未映射记 RAW_*）；"
              "如需只保留 STEP7 需求清单，加 --strict-metrics")
    for company in companies:
        files = COMPANY_FILES.get(company)
        if not files:
            print(f"!! 未知公司：{company}（可选：{', '.join(COMPANY_FILES)}）")
            continue
        for year in years:
            relative = files.get(year)
            if not relative:
                print(f"!! {company} {year} 没有样例文件")
                continue
            rows.append(
                run_one(
                    company,
                    year,
                    ROOT / relative,
                    ctx,
                    method=args.method,
                    write_excel=not args.no_excel,
                    keep_unmapped=keep_unmapped,
                    output_dir=out_dir,
                )
            )

    summary = pd.DataFrame(rows)
    summary_path = out_dir / "summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 100)
    print("汇总")
    print("=" * 100)
    display_columns = [
        column
        for column in (
            "公司",
            "年份",
            "报告期",
            "规则",
            "定位表数",
            "提取表数",
            "提取行数",
            "标准化记录数",
            "指标编码数",
            "有值指标数",
            "未映射指标数",
            "状态",
        )
        if column in summary.columns
    ]
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(summary[display_columns].to_string(index=False))
    print(f"\n汇总文件: {summary_path}")
    failures = summary[summary["状态"].ne("成功")]
    if not failures.empty:
        print(f"\n未成功（{len(failures)} 份）：")
        for _, item in failures.iterrows():
            print(f"  {item['公司']} {item['年份']}: {item.get('说明', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
