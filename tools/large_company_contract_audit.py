"""Validate every target-table contract for top-ten and large companies."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.batch_processor import (  # noqa: E402
    build_context_from_profile,
    locate_one_pdf_bytes,
)
from services.batch_workflow import extract_document_tables  # noqa: E402
from services.company_extractors._document import contract_failure  # noqa: E402
from services.extraction_rules import (  # noqa: E402
    resolve_rule,
)
from services.report_profiles import load_profile_workbook  # noqa: E402
from tools.company_extraction_audit import sample_path  # noqa: E402


def scope_companies(path: Path, categories: set[str]) -> list[str]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    companies: list[str] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        company = str(row[0] or "").strip()
        category = str(row[2] or "").strip()
        if company and category in categories:
            companies.append(company)
    return companies


def _log_for(logs, table_name: str):
    return next(
        (
            log
            for log in logs
            if str(log.get("目标表") or "").strip() == table_name
        ),
        {},
    )


def _page_status(match, rule) -> str:
    actual = [int(page) for page in (getattr(match, "pages", []) or [])]
    table_id = str(getattr(match, "table_id", ""))
    aliases = {"OPERATING_METRICS": "NON_LIFE_OPERATING_METRICS"}
    table_id = aliases.get(table_id, table_id)
    config = (getattr(rule, "tables", {}) or {}).get(table_id, {})
    expected = [int(page) for page in (config.get("expected_pages") or [])]
    if not expected:
        return "未配置"
    if not actual:
        return "未定位"
    tolerance = max(0, int(config.get("tolerance", 2) or 0))
    within = all(
        any(abs(page - candidate) <= tolerance for candidate in expected)
        for page in actual
    )
    if within:
        return "通过" if set(actual) == set(expected) else "年度窗口内"
    return "超出窗口"


def audit_company_year(company: str, year: int, context) -> list[dict]:
    rule = resolve_rule(
        company,
        year,
        profile_id=context.profile.profile_id,
    )
    canonical = rule.canonical if rule is not None else company
    path = sample_path(canonical, year)
    if path is None or not path.is_file():
        return [
            {
                "公司": company,
                "年份": year,
                "表编码": "",
                "目标表": "",
                "页码": "",
                "数据行数": 0,
                "表头": "",
                "契约状态": "样例缺失",
                "页码状态": "样例缺失",
                "说明": "未找到对应 PDF",
            }
        ]

    pdf_bytes = path.read_bytes()
    located = locate_one_pdf_bytes(canonical, pdf_bytes, context, mode="rule")
    matches = list(located.get("matches") or [])
    outcome = extract_document_tables(
        company=canonical,
        pdf_bytes=pdf_bytes,
        matches=matches,
        method="全部规则法（离线，零模型调用）",
        allow_llm_fallback=False,
        metadata=located.get("metadata"),
        profile_id=context.profile.profile_id,
    )
    tables_by_id = {
        str(getattr(table, "table_id", "")): table
        for table in outcome.tables
    }
    rows: list[dict] = []
    for match in matches:
        table_id = str(getattr(match, "table_id", ""))
        table_name = str(getattr(match, "table_name", ""))
        table = tables_by_id.get(table_id)
        log = _log_for(outcome.logs, table_name)
        if table is None:
            contract_status = str(log.get("状态") or "未生成")
            if contract_status == "未披露":
                contract_status = "通过（未披露）"
            contract_note = str(log.get("说明") or "公司专属提取未返回该表")
            row_count = 0
            header = ""
        else:
            failure = contract_failure(table, list(table.rows or []))
            contract_status = "通过" if not failure else "失败"
            contract_note = failure
            row_count = max(0, len(table.rows or []) - 1)
            header = " | ".join(map(str, (table.rows or [[]])[0]))
        rows.append(
            {
                "公司": company,
                "年份": year,
                "表编码": table_id,
                "目标表": table_name,
                "页码": "、".join(map(str, getattr(match, "pages", []) or [])),
                "数据行数": row_count,
                "表头": header,
                "契约状态": contract_status,
                "页码状态": (
                    "未披露"
                    if contract_status == "通过（未披露）"
                    else _page_status(match, rule)
                ),
                "说明": contract_note,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", default="2022,2023,2024,2025")
    parser.add_argument("--companies", default="")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--company-list", default=str(ROOT / "财险公司列表.xlsx"))
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    companies = (
        [value.strip() for value in args.companies.split(",") if value.strip()]
        if args.companies
        else scope_companies(
            Path(args.company_list),
            {"前十大", "大型公司"},
        )
    )
    profile = load_profile_workbook(
        (ROOT / "config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx").read_bytes(),
        project_root=ROOT,
    )
    context = build_context_from_profile(
        profile,
        api_key="",
        base_url="",
        model="",
        use_vision=False,
    )
    rows: list[dict] = []
    jobs = [
        (company, int(year))
        for company in companies
        for year in args.years.split(",")
    ]
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(audit_company_year, company, year, context): (
                company,
                year,
            )
            for company, year in jobs
        }
        for future in as_completed(futures):
            company, year = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = [
                    {
                        "公司": company,
                        "年份": year,
                        "表编码": "",
                        "目标表": "",
                        "页码": "",
                        "数据行数": 0,
                        "表头": "",
                        "契约状态": "异常",
                        "页码状态": "异常",
                        "说明": str(exc),
                    }
                ]
            rows.extend(result)
            print(
                json.dumps(
                    {
                        "公司": company,
                        "年份": year,
                        "表数": len(result),
                        "通过": sum(
                            row["契约状态"] in {"通过", "通过（未披露）"}
                            for row in result
                        ),
                        "失败": sum(row["契约状态"] == "失败" for row in result),
                        "样例缺失": sum(
                            row["契约状态"] == "样例缺失" for row in result
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    frame = pd.DataFrame(rows)
    frame.to_csv(out_dir / "contract_matrix.csv", index=False, encoding="utf-8-sig")
    summary = (
        frame.groupby(["公司", "年份", "契约状态"], dropna=False)
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    summary.to_csv(out_dir / "contract_summary.csv", index=False, encoding="utf-8-sig")
    return int((frame["契约状态"] == "失败").any())


if __name__ == "__main__":
    raise SystemExit(main())
