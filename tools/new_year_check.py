# -*- coding: utf-8 -*-
"""新年度接入检查：把新一年的 PDF 批量跑一遍定位，核对公司 YAML 里的页码窗口。

用途：每年新报告出来后，先跑这个脚本，看哪些公司的哪张表页码漂了，
再按输出里的“建议 expected_pages”改 ``config/extraction/companies/<公司>.yaml``。

用法（项目根目录）：
  python tools/new_year_check.py --pdf-dir "Data/pdfs(2026)" --year 2026
  python tools/new_year_check.py --pdf-dir "Data/pdfs(2025)" --year 2025 --companies 众安产险,大地财产
产物：
  tmp/new_year_check.csv（逐表核对明细，覆盖写）
控制台：
  每家公司一行摘要 + 全量统计；有漂移/缺失/未配置规则时退出码为 2。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.batch_processor import build_context_from_profile, locate_one_pdf_bytes
from services.extraction_rules import resolve_rule
from services.report_profiles import load_profile_workbook
from services.solvency_pdf_locator import extract_report_metadata

PROFILE_PATH = ROOT / "config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx"

FIELDS = [
    "文件",
    "公司",
    "报告年度",
    "table_id",
    "expected_pages",
    "tolerance",
    "page_match",
    "命中页",
    "偏差页",
    "判定",
    "建议expected_pages",
    "说明",
]

OK = "窗口内"
DRIFT = "窗口外回退"
MISSING = "缺失"
STRICT_MISS = "strict未命中"
NO_RULE = "未配置公司规则"


def _expected_pages(table: dict[str, Any]) -> list[int]:
    pages = table.get("expected_pages") or []
    result = []
    for page in pages:
        text = str(page).strip()
        if text.isdigit() and int(text) > 0:
            result.append(int(text))
    return result


def _judge(match: Any, expected: list[int], tolerance: int, page_match: str) -> tuple[str, str, list[int]]:
    """返回 (判定, 偏差说明, 实际命中页列表)。"""
    actual = sorted(int(page) for page in (getattr(match, "pages", []) or []))
    if not actual:
        if page_match == "strict":
            return STRICT_MISS, "strict 模式窗口内未命中", []
        return MISSING, "未定位到该表", []
    if not expected:
        return OK, "无预期页码，仅锚点命中", actual
    drift = min(abs(page - exp) for page in actual for exp in expected)
    note = getattr(match, "rule_note", "") or ""
    if "已回退" in note:
        return DRIFT, f"窗口外命中，最近偏差 {drift} 页", actual
    if "未命中" in note and page_match == "strict":
        return STRICT_MISS, note, actual
    if drift > tolerance:
        return DRIFT, f"超出容差 {tolerance} 页，偏差 {drift} 页", actual
    return OK, "", actual


def _company_from_pdf(pdf_path: Path, metadata: dict[str, Any]) -> str:
    company = str(metadata.get("公司") or "").strip()
    if company:
        return company
    return pdf_path.stem.strip()


def _year_from(metadata: dict[str, Any], fallback: int) -> int | None:
    raw = metadata.get("报告年度") or metadata.get("报告期") or ""
    text = str(raw)
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return fallback or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", required=True, help="新年度 PDF 所在目录")
    parser.add_argument("--year", type=int, default=0, help="报告年度（0=从 PDF 封面识别）")
    parser.add_argument("--companies", default="", help="只检查这些公司，逗号分隔")
    parser.add_argument("--csv", default="tmp/new_year_check.csv", help="明细输出路径")
    parser.add_argument("--mode", default="rule", choices=["rule", "hybrid"], help="定位模式")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.is_absolute():
        pdf_dir = ROOT / pdf_dir
    if not pdf_dir.is_dir():
        print(f"[错误] 目录不存在：{pdf_dir}")
        return 1

    wanted = {item.strip() for item in args.companies.split(",") if item.strip()}
    profile = load_profile_workbook(PROFILE_PATH.read_bytes(), project_root=ROOT)
    ctx = build_context_from_profile(profile, api_key="", base_url="", model="")

    rows: list[dict[str, str]] = []
    summary: list[tuple[str, int, int, int, int]] = []
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        if pdf_path.name.startswith(("~$", ".")):
            continue
        pdf_bytes = pdf_path.read_bytes()
        try:
            metadata = extract_report_metadata(pdf_bytes)
        except Exception as exc:  # noqa: BLE001 - 单份失败不影响整批
            print(f"[跳过] {pdf_path.name}: 封面识别失败 {exc}")
            continue
        company = _company_from_pdf(pdf_path, metadata)
        if wanted and company not in wanted and pdf_path.stem not in wanted:
            continue
        year = _year_from(metadata, args.year)

        rule = resolve_rule(company, year, profile_id=profile.profile_id)
        located = locate_one_pdf_bytes(company, pdf_bytes, ctx, mode=args.mode)
        matches = {str(getattr(m, "table_id", "")): m for m in located.get("matches") or []}

        if rule is None:
            ok = drift = missing = skip = 0
            for match in matches.values():
                rows.append({
                    "文件": pdf_path.name,
                    "公司": company,
                    "报告年度": str(year or ""),
                    "table_id": getattr(match, "table_id", ""),
                    "expected_pages": "",
                    "tolerance": "",
                    "page_match": "",
                    "命中页": "、".join(str(p) for p in getattr(match, "pages", []) or []),
                    "偏差页": "",
                    "判定": NO_RULE,
                    "建议expected_pages": "",
                    "说明": "未配置公司 YAML，按 anchor_only 全文锚点",
                })
                skip += 1
            summary.append((company, 0, 0, 0, skip))
            print(f"{company:<8} 未配置公司规则（沿用默认 anchor_only）")
            continue

        ok = drift = missing = 0
        for table_id, table in rule.tables.items():
            expected = _expected_pages(table)
            try:
                tolerance = max(0, int(table.get("tolerance", 2)))
            except (TypeError, ValueError):
                tolerance = 2
            page_match = str(table.get("page_match") or "anchor_only")
            match = matches.get(table_id)
            verdict, note, actual = _judge(match, expected, tolerance, page_match)
            if verdict == OK:
                ok += 1
            elif verdict == MISSING:
                missing += 1
            else:
                drift += 1
            suggestion = ""
            if verdict in (DRIFT, MISSING, STRICT_MISS) and actual:
                suggestion = ",".join(str(page) for page in actual)
            rows.append({
                "文件": pdf_path.name,
                "公司": company,
                "报告年度": str(year or ""),
                "table_id": table_id,
                "expected_pages": ",".join(str(page) for page in expected),
                "tolerance": str(tolerance),
                "page_match": page_match,
                "命中页": "、".join(str(page) for page in actual),
                "偏差页": note,
                "判定": verdict,
                "建议expected_pages": suggestion,
                "说明": (getattr(match, "rule_note", "") if match is not None else ""),
            })
        summary.append((company, ok, drift, missing, 0))
        flag = "" if drift == 0 and missing == 0 else "  ← 需改 YAML"
        print(
            f"{company:<8} 年度={year or '?'}  窗口内={ok} 回退={drift} 缺失={missing}{flag}"
        )
        if drift or missing:
            for row in rows:
                if row["公司"] != company:
                    continue
                if row["判定"] in (OK, NO_RULE):
                    continue
                print(
                    f"    - {row['table_id']:<28} 预期[{row['expected_pages']}] "
                    f"命中[{row['命中页']}] {row['判定']} 建议[{row['建议expected_pages']}]"
                )

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = ROOT / csv_path
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    total_ok = sum(item[1] for item in summary)
    total_drift = sum(item[2] for item in summary)
    total_missing = sum(item[3] for item in summary)
    no_rule = sum(item[4] for item in summary)
    print()
    print(f"公司数={len(summary)}  表级：窗口内={total_ok} 回退={total_drift} 缺失={total_missing} 无规则={no_rule}")
    print(f"明细：{csv_path.relative_to(ROOT) if csv_path.is_relative_to(ROOT) else csv_path}")
    if total_drift or total_missing or no_rule:
        print("提示：把“建议expected_pages”填回对应公司 YAML 的 expected_pages 即可。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
