"""Audit company document extraction and compare reproducible metric snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from services.batch_processor import build_context_from_profile, locate_one_pdf_bytes
from services.batch_workflow import extract_document_tables, standardize_document_tables
from services.report_profiles import load_profile_workbook
from services.solvency_validator import load_validation_rules, validate_standard_data_detailed
from tests.test_three_companies_extraction import COMPANY_FILES
import yaml

TOP_TEN = tuple(list(COMPANY_FILES)[:10])
COMPANY_CONFIG_DIR = ROOT / "config/extraction/companies"
ALL_COMPANIES = tuple(
    (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("company", {}).get("canonical", path.stem)
    for path in sorted(COMPANY_CONFIG_DIR.glob("*.yaml"))
)


def sample_path(company, year):
    relative = COMPANY_FILES.get(company, {}).get(str(year))
    if relative and (ROOT / relative).is_file():
        return ROOT / relative
    folder = ROOT / f"Data/pdfs({year})"
    aliases = {company}
    config_path = COMPANY_CONFIG_DIR / f"{company}.yaml"
    if config_path.is_file():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        aliases.update((config.get("company") or {}).get("aliases") or [])
    candidates = [p for p in folder.glob("*.pdf") if p.stem in aliases or any(alias and alias in p.stem for alias in aliases)]
    return candidates[0] if len(candidates) == 1 else None


def audit_one(company, year, context, output, validation_rules):
    path = sample_path(company, year)
    if path is None or not path.is_file():
        return {"公司": company, "年份": year, "状态": "样例缺失"}
    payload = path.read_bytes()
    located = locate_one_pdf_bytes(company, payload, context, mode="rule")
    outcome = extract_document_tables(
        company=company, pdf_bytes=payload, matches=located.get("matches", []),
        method="全部规则法（离线，零模型调用）", allow_llm_fallback=False,
        metadata=located.get("metadata"), profile_id=context.profile.profile_id,
    )
    standard = standardize_document_tables(
        company=company, pdf_name=path.name, pdf_bytes=payload, tables=outcome.tables,
        context=context, keep_unmapped=False, metadata=located.get("metadata"),
        extraction_traces=outcome.traces, build_workbook=False,
    )
    data = standard["data"]
    folder = output / company / str(year)
    folder.mkdir(parents=True, exist_ok=True)
    data.to_csv(folder / "standard.csv", index=False, encoding="utf-8-sig")
    standard["diagnostics"].to_csv(folder / "diagnostics.csv", index=False, encoding="utf-8-sig")
    snapshot = {
        "metadata": located.get("metadata"), "logs": outcome.logs, "traces": outcome.traces,
        "tables": [{"table_id": t.table_id, "pages": t.source_pages, "rows": t.rows,
                    "blocks": t.table_blocks, "unit": getattr(t, "unit", ""),
                    "blank_cell_evidence": getattr(t, "blank_cell_evidence", [])}
                   for t in outcome.tables],
    }
    (folder / "tables.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    checks, evidence = validate_standard_data_detailed(data, validation_rules, context.taxonomy)
    checks.to_csv(folder / "validation.csv", index=False, encoding="utf-8-sig")
    evidence.to_csv(folder / "evidence.csv", index=False, encoding="utf-8-sig")
    failures = [log for log in outcome.logs if log.get("状态") == "失败"]
    return {"公司": company, "年份": year, "状态": "提取不完整" if failures else standard["状态"],
            "提取状态": outcome.status, "表数": len(outcome.tables),
            "行数": sum(len(t.rows) for t in outcome.tables), "记录数": len(data),
            "编码数": data["指标编码"].nunique() if not data.empty else 0,
            "零值数": int(pd.to_numeric(data["数值"], errors="coerce").eq(0).sum()),
            "说明": outcome.note,
            "勾稽未通过": int(checks["status"].eq("未通过").sum()) if "status" in checks else 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", default=",".join(TOP_TEN))
    parser.add_argument("--all-companies", action="store_true",
                        help="audit every company registered under config/extraction/companies")
    parser.add_argument("--years", default="2022,2023,2024,2025")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    output = Path(args.out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    profile = load_profile_workbook((ROOT / "config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx").read_bytes(), project_root=ROOT)
    context = build_context_from_profile(profile, api_key="", base_url="", model="")
    rules = load_validation_rules(ROOT / "config/solvency_validation_rules.xlsx")
    companies = ALL_COMPANIES if args.all_companies else tuple(company.strip() for company in args.companies.split(","))
    jobs = [(company, int(year)) for company in companies for year in args.years.split(",")]
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(audit_one, company, year, context, output, rules): (company, year) for company, year in jobs}
        for future in as_completed(futures):
            company, year = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {"公司": company, "年份": year, "状态": "异常", "说明": str(exc)}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            pd.DataFrame(results).sort_values(["公司", "年份"]).to_csv(output / "summary.csv", index=False, encoding="utf-8-sig")
    return int(any(row["状态"] != "成功" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
