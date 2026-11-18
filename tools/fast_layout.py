"""Fast layout iteration harness.

The full verification path re-runs the whole Streamlit report, rebuilds every
chart and converts them to SVG (~2 minutes).  Layout work does not need any of
that: it only needs the captured modules.  This script caches them once and then
re-runs only "measure + compose + render" (a few seconds).

Usage:
    python tools/fast_layout.py --refresh       # capture + convert once, then render
    python tools/fast_layout.py                 # reuse the cache, re-render only
    python tools/fast_layout.py --out tmp/x.pdf # custom output path
"""
from __future__ import annotations

import argparse
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

CACHE = Path("tmp/pdf_diagnose/modules.pkl")

import pandas as pd  # noqa: E402


def _capture() -> list:
    from streamlit.testing.v1 import AppTest

    from services.report_pdf.capture import to_content_items
    from services.solvency_dataset_adapter import read_standard_workbook
    from services.solvency_navigation import PRINT_ALL_LABEL
    from ui.cache import prepare_visualization_source
    from ui.config import PROJECT_MEMBER_ROLE

    source = Path("偿付能力行业集成数据_公司名称与分类规范化.xlsx")
    raw = read_standard_workbook(source.read_bytes(), source.name, include_internal=False)
    frame, _ = prepare_visualization_source(raw, "NON_LIFE_SOLVENCY")
    prem = frame[frame["指标编码"].astype(str).eq("SIGNED_PREMIUM")].copy()
    prem["数值"] = pd.to_numeric(prem["数值"], errors="coerce")
    names = (
        prem.groupby(prem["公司"].astype(str))["数值"]
        .max()
        .sort_values(ascending=False)
        .head(10)
        .index.tolist()
    )
    frame = frame[frame["公司"].astype(str).isin(names)].copy()

    at = AppTest.from_file(str(Path("app.py").resolve()), default_timeout=1800)
    for key, value in {
        "logged_in": True,
        "user_role": PROJECT_MEMBER_ROLE,
        "workflow_main_tab": "STEP7 公司报告",
        "company_nav_level_one": PRINT_ALL_LABEL,
        "integrated_data": frame,
        "major_financing_data": pd.DataFrame(),
    }.items():
        at.session_state[key] = value
    t0 = time.time()
    at.run()
    print(f"[capture] streamlit run {time.time() - t0:.0f}s exceptions={len(at.exception)}")
    t0 = time.time()
    modules = to_content_items(at.session_state.get("_s7_pdf_modules", []))
    print(f"[capture] chart conversion {time.time() - t0:.0f}s modules={len(modules)}")
    return modules


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-capture the report")
    parser.add_argument("--out", default="tmp/fast.pdf")
    args = parser.parse_args()

    t_start = time.time()
    if args.refresh or not CACHE.exists():
        modules = _capture()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(pickle.dumps(modules))
        print(f"[capture] cached -> {CACHE}")
    else:
        modules = pickle.loads(CACHE.read_bytes())
        print(f"[cache] {len(modules)} modules from {CACHE}")

    from services.report_pdf.export import _report_meta
    from services.report_pdf.html_builder import build_probe_html, build_report_html
    from services.report_pdf.renderer import measure_and_render

    import streamlit as st

    st.session_state.setdefault("_s7_pdf_meta", {})
    meta = _report_meta("zh")

    t0 = time.time()
    probe = build_probe_html(meta, modules)
    print(f"[layout] probe html {time.time() - t0:.1f}s")

    t0 = time.time()
    measured, pdf = measure_and_render(
        probe, lambda heights: build_report_html(meta, modules, measured=heights)
    )
    print(f"[layout] measure + compose + render {time.time() - t0:.1f}s")

    out = Path(args.out)
    out.write_bytes(pdf)
    import pymupdf

    doc = pymupdf.open(out)
    print(f"[done] {out} pages={doc.page_count} total={time.time() - t_start:.0f}s")


if __name__ == "__main__":
    main()
