"""Build the standalone HTML document that Playwright turns into a PDF.

The document never references the Streamlit page: no top bar, no identity
strip, no browser print CSS.  Every page is a fixed 1600x900 box, so the PDF
has deterministic pagination and no blank pages.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from pathlib import Path

from .models import ContentItem, ModuleSnapshot, ReportMeta
from .text import label

PAGE_WIDTH = 1600
PAGE_HEIGHT = 900
CONTENT_WIDTH = 1512  # 1600 - 2 * 44px page padding
FIRST_PAGE_HEIGHT = 720
NEXT_PAGE_HEIGHT = 790
MAX_FULL_FIGURE_HEIGHT = 500
MAX_PAIR_FIGURE_HEIGHT = 340

_CSS = """
@page { size: 1600px 900px; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #ffffff; color: #1f2937;
  font-family: "Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", "Segoe UI", sans-serif; }
.page { width: 1600px; position: relative;
  page-break-after: always; break-after: page; }
.page:last-child { page-break-after: auto; break-after: auto; }
.page.cover { height: 900px; overflow: hidden; }

.cover { background: linear-gradient(135deg, #00338D 0%, #1E49E2 100%); }
.cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.cover-text { position: absolute; left: 140px; right: 140px; bottom: 150px; color: #ffffff; }
.cover-title { font-size: 64px; font-weight: 900; line-height: 1.3; letter-spacing: 1px; }
.cover-subtitle { font-size: 30px; margin-top: 18px; opacity: 0.95; }
.cover-date { font-size: 24px; margin-top: 20px; opacity: 0.85; }

.report-page { padding: 24px 44px 30px; height: 900px; overflow: hidden; }
.report-page.cont .module-body { height: 790px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-end;
  font-size: 15px; color: #6b7280; border-bottom: 2px solid #e5e7eb; padding-bottom: 6px; }
.page-header .hdr-left { font-weight: 600; color: #00338D; }
.module-title { font-size: 30px; font-weight: 800; color: #00338D; margin: 14px 0 8px; }
.module-body { height: 720px; overflow: hidden; }
.module-content { width: 100%; }
.figure { text-align: center; margin-bottom: 10px; }
.figure-wrap { margin-bottom: 8px; }
.figure-wrap.full { width: 100%; }
.company-card { flex: 1 1 0; min-width: 0; border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 10px; background: #fff; }
.company-card .figure { margin: 2px 0 0; }
.company-card .figure svg { max-height: 270px; }
.figures-row .company-card { margin-bottom: 0; }
.two-col { display: flex; gap: 22px; align-items: flex-start; }
.two-col .col { min-width: 0; }
.two-col.multi { flex-wrap: wrap; }
.two-col.multi .col { flex: 0 0 calc(50% - 11px); }
.figure-title { font-size: 15px; font-weight: 600; color: #111827; text-align: left; margin: 2px 0; }
/* `width: 100%` (not max-width) so narrow charts are enlarged instead of
   floating tiny inside a wide white box. */
.figure svg { width: 100%; height: auto; max-height: 560px; display: block; margin: 0 auto; }
.figures-row { display: flex; gap: 16px; align-items: flex-start; }
.figures-row .figure { flex: 1 1 0; min-width: 0; margin-bottom: 0; }
.figures-row .figure-wrap { flex: 1 1 0; min-width: 0; }
.figures-row .figure svg { max-height: 340px; }
.figcaption { font-size: 16px; color: #6b7280; margin-top: 6px; }
.module-note { font-size: 15px; line-height: 1.5; color: #374151; margin-top: 8px; }
.module-note p { margin: 0 0 6px; }
.table-wrap { overflow: visible; }
.table-section { break-inside: avoid; page-break-inside: avoid; }
table { border-collapse: collapse; width: 100%; font-size: 13px; table-layout: fixed; }
th, td { border: 1px solid #d1d5db; padding: 3px 7px; text-align: right; overflow-wrap: anywhere; }
th { background: #f3f4f6; color: #111827; }
thead { display: table-header-group; }
.table-section th, .table-section td { text-align: center; }
td:first-child, th:first-child { text-align: left; }
.page-footer { display: flex; justify-content: space-between; font-size: 15px;
  color: #9ca3af; border-top: 1px solid #e5e7eb; padding-top: 8px; margin-top: 10px; }

.metric-row { display: flex; gap: 16px; margin: 10px 0 16px; }
.figure, .figures-row, table, .metric-row, .metric-card, .s7-insight-card,
.module-note, .solvency-report-analysis, .s7-chart-summary, .solvency-analysis-note,
.s7-capital-insight, .s7-market-side, .s7-pattern-group, .s7-uw-empty {
  break-inside: avoid; page-break-inside: avoid; }
tr { break-inside: avoid; page-break-inside: avoid; }
.page-header { break-after: avoid; page-break-after: avoid; }
.module-title { break-after: avoid; page-break-after: avoid; }
.metric-card { flex: 1 1 0; border: 1px solid #dbe1ea; border-radius: 10px;
  padding: 12px 14px; background: #f8fafc; }
.metric-label { font-size: 16px; color: #4b5563; }
.metric-value { font-size: 27px; font-weight: 800; color: #00338D; margin-top: 4px; }
.metric-delta { font-size: 16px; margin-top: 4px; color: #6b7280; }
.metric-delta.delta-normal { color: #0f766e; }
.metric-delta.delta-inverse { color: #b91c1c; }

.solvency-report-analysis {
  position: relative; box-sizing: border-box;
  background: linear-gradient(120deg,#F7FAFF 0%,#EEF4FF 58%,#FFFFFF 100%);
  border: 1px solid #DCE6F5; border-left: 4px solid #00338D;
  border-radius: 10px; padding: 11px 14px 10px; margin: 6px 0 12px;
  box-shadow: 0 4px 14px rgba(0,51,141,.06);
  font-size: 14px; line-height: 1.5; color: #374151;
}
.solvency-report-analysis p { margin: 2px 0; line-height: 1.65; }
.solvency-note-default { color: #0A1F5C; font-size: 13px; }
.solvency-note-custom { color: #1E49E2; font-size: 14px; font-weight: 600; }
.solvency-report-footnote { color: #777; font-size: 12px; font-style: italic; margin: 4px 0 16px; }

.s7-insight-card {
  border: 1px solid #E2E8F0; border-left: 5px solid var(--s7-insight-accent, #00338D);
  border-radius: 12px; padding: 13px 18px 12px; margin: 6px 0 14px;
  background: linear-gradient(135deg,#FFFFFF 0%,#F8FBFF 55%,#EEF4FF 100%);
  box-shadow: 0 6px 18px rgba(0,51,141,.06);
}
.s7-insight-card__top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 0 0 7px; }
.s7-insight-card__eyebrow {
  display: inline-flex; align-items: center;
  background: var(--s7-insight-accent, #00338D); color: #FFFFFF;
  border-radius: 999px; padding: 2px 9px;
  font-size: 10px; font-weight: 700; letter-spacing: .6px;
}
.s7-insight-card__title { color: #0C233C; font-size: 12px; font-weight: 700; letter-spacing: .3px; }
.s7-insight-card__headline { color: #1E293B; font-size: 18px; font-weight: 650; line-height: 1.45; margin: 0 0 5px; }
.s7-insight-card__value { color: var(--s7-insight-accent, #00338D); font-size: 22px; font-weight: 900; margin: 0 4px; }
.s7-insight-card__delta {
  display: inline-flex; align-items: center;
  border-radius: 999px; padding: 1px 8px; margin-left: 4px;
  font-size: 12px; font-weight: 800; vertical-align: 2px;
}
.s7-insight-card__delta--up { background:#EAF7EE; color:#146C12; border:1px solid #BFE5C8; }
.s7-insight-card__delta--down { background:#FDECEC; color:#B0191C; border:1px solid #F5C2C2; }
.s7-insight-card__delta--neutral { background:#EEF4FF; color:#00338D; border:1px solid #C7D7F5; }
.s7-insight-card__bullets {
  display: grid; grid-template-columns: repeat(auto-fit,minmax(210px,1fr));
  gap: 3px 18px; margin: 4px 0 0; padding-left: 17px;
  color: #334155; font-size: 12px; line-height: 1.6;
}
.s7-insight-card__bullets li::marker { color: var(--s7-insight-accent, #00338D); }
.s7-insight-card__foot { color: #64748B; font-size: 10px; line-height: 1.5; margin-top: 6px; padding-top: 5px; border-top: 1px dashed #D8E2F0; }

.solvency-chart-legend {
  display: flex; flex-wrap: wrap; justify-content: flex-end;
  align-items: center; gap: 10px 18px;
  margin: 2px 10px 8px 0; color: #0C233C; font-size: 10px;
}
.solvency-chart-legend__item { display: inline-flex; align-items: center; gap: 6px; }
.solvency-chart-legend__symbol { --legend-color:#0C233C; display: inline-block; flex: 0 0 auto; }
.solvency-chart-legend__symbol--square { width: 11px; height: 11px; border-radius: 2px; background: var(--legend-color); }
.solvency-chart-legend__symbol--outline { width: 13px; height: 11px; border-radius: 3px; border: 2px solid var(--legend-color); background: transparent; box-sizing: border-box; }
.solvency-chart-legend__symbol--line, .solvency-chart-legend__symbol--dash { width: 22px; height: 0; border-top: 3px solid var(--legend-color); }
.solvency-chart-legend__symbol--dash { border-top-style: dashed; }

.solvency-analysis-note {
  background: #F2F6FC; border-left: 4px solid #00338D;
  border-radius: 4px; padding: 9px 13px; margin: 6px 0 0; color: #243B53;
}
.solvency-analysis-spacer { height: 16px; width: 100%; }
.solvency-analysis-ai { color: #D84315; font-size: 12px; margin-top: 5px; }
.solvency-analysis-ai--error { color: #C00000; }

.s7-chart-summary {
  position: relative; box-sizing: border-box;
  display: flex; align-items: flex-start; gap: 11px;
  background: linear-gradient(120deg,#F7FAFF 0%,#EEF4FF 56%,#FFFFFF 100%);
  border: 1px solid #D8E4F8; border-left: 4px solid #00338D;
  border-radius: 10px; padding: 10px 14px 9px; margin: 6px 0 12px;
  box-shadow: 0 4px 14px rgba(0,51,141,.06);
}
.s7-chart-summary::after {
  content: ""; position: absolute; right: 14px; top: 12px;
  width: 28px; height: 4px; border-radius: 2px; background: #00338D; opacity: .18;
}
.s7-chart-summary__tag {
  flex: 0 0 auto; display: inline-flex; align-items: center;
  background: linear-gradient(135deg,#00338D 0%,#1E49E2 100%);
  color: #FFFFFF; border-radius: 999px; padding: 3px 10px; margin-top: 1px;
  font-size: 10px; font-weight: 800; letter-spacing: .5px;
  box-shadow: 0 2px 6px rgba(0,51,141,.18);
}
.s7-chart-summary__text { margin: 0; padding-right: 38px; color: #0C233C; font-size: 13px; line-height: 1.72; text-align: justify; }

.annual-company-title {
  color: #00338D; font-size: 13px; font-weight: 700; line-height: 1.25;
  text-align: center; white-space: normal; overflow-wrap: anywhere;
  min-height: 17px; margin: 0 0 30px; padding: 0 4px;
}

.s7-pattern-panel-head { display: flex; align-items: center; gap: 6px; min-width: 0; margin: 0 0 2px; }
.s7-pattern-panel-title { color: #0C233C; font-size: 13px; font-weight: 800; line-height: 1.32; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 1 1 auto; }
.s7-pattern-panel-title::before { content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #00338D; margin-right: 6px; vertical-align: 1px; }
.s7-pattern-panel-meta { display: flex; flex-wrap: wrap; gap: 4px; margin: 0 0 3px; }
.s7-pattern-pill { display: inline-flex; align-items: center; background: #EEF4FF; color: #1E3A8A; border: 1px solid #D5E2FA; border-radius: 999px; padding: 1px 7px; font-size: 10px; font-weight: 700; line-height: 1.6; white-space: nowrap; }
.s7-pattern-pill--muted { background: #F8FAFC; color: #64748B; border-color: #E2E8F0; }
.s7-pattern-group { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; background: linear-gradient(90deg,#FDF0ED 0%,#FFF9F7 70%,#FFFFFF 100%); border: 1px solid #F4D8D2; border-left: 5px solid #D94330; border-radius: 10px; padding: 7px 13px; margin: 20px 0 11px; box-shadow: 0 2px 8px rgba(217,67,48,.05); }
.s7-pattern-group__name { color: #8C1D18; font-size: 14px; font-weight: 800; letter-spacing: .3px; }
.s7-pattern-group__count { display: inline-flex; align-items: center; background: #D94330; color: #FFFFFF; border-radius: 999px; padding: 1px 8px; font-size: 11px; font-weight: 800; }
.s7-pattern-group__hint { color: #9A5B52; font-size: 11px; font-weight: 500; }

.s7-uw-year-title { color: #00338D; font-size: 16px; font-weight: 800; letter-spacing: .4px; margin: 2px 0 2px; }
.s7-uw-year-sub { color: #64748B; font-size: 11px; margin: 0 0 4px; }
.s7-uw-counts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 7px 0 10px; }
.s7-uw-chip { display: inline-flex; align-items: center; gap: 4px; border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 700; line-height: 1.4; }
.s7-uw-chip--profit { background: #EAF7EE; color: #146C12; border: 1px solid #BFE5C8; }
.s7-uw-chip--loss { background: #FDECEC; color: #B0191C; border: 1px solid #F5C2C2; }
.s7-uw-chip--even { background: #FFF7E0; color: #8A5A00; border: 1px solid #F1D58A; }
.s7-uw-muted { color: #64748B; font-size: 11px; }
.s7-uw-legend { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; color: #475569; font-size: 11px; margin: 2px 0 10px; }
.s7-uw-legend span { display: inline-flex; align-items: center; gap: 5px; }
.s7-uw-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.s7-uw-dash { width: 24px; height: 0; border-top: 2px dashed #64748B; display: inline-block; }
.s7-uw-empty { min-height: 275px; display: flex; align-items: center; justify-content: center; text-align: center; padding: 28px 20px; box-sizing: border-box; border: 1px dashed #CBD5E1; border-radius: 10px; background: #F8FAFC; color: #94A3B8; font-size: 12px; line-height: 1.8; }

.s7-market-side { min-height: 424px; padding: 16px 18px 14px; border-radius: 12px; border: 1px solid #D9E2F0; background: linear-gradient(180deg,#F7FAFF 0%,#FFFFFF 72%); box-shadow: 0 2px 10px rgba(0,51,141,.05); }
.s7-market-side__title { color: #00338D; font-size: 15px; font-weight: 800; letter-spacing: .3px; margin: 0 0 10px; }
.s7-market-side__stats { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 7px; margin: 0 0 13px; }
.s7-market-side__stat { background: #FFFFFF; border: 1px solid #E3EAF5; border-radius: 8px; padding: 7px 8px 6px; min-width: 0; }
.s7-market-side__stat span { display: block; color: #64748B; font-size: 9.5px; line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.s7-market-side__stat strong { display: block; color: #0C233C; font-size: 16px; line-height: 1.25; font-variant-numeric: tabular-nums; white-space: nowrap; }
.s7-market-side__stat small { color: #94A3B8; font-size: 8.5px; margin-left: 4px; font-weight: 600; }
.s7-market-side__label { color: #64748B; font-size: 10px; font-weight: 700; letter-spacing: .4px; margin: 0 0 3px; }
.s7-market-side__list { margin: 0; }
.s7-market-side__row { display: grid; grid-template-columns: 9px minmax(0,1fr) auto; gap: 7px; align-items: center; padding: 2.5px 0; border-bottom: 1px dashed #EDF1F8; }
.s7-market-side__row:last-child { border-bottom: none; }
.s7-market-side__row i { width: 9px; height: 9px; border-radius: 3px; display: block; }
.s7-market-side__row span { color: #334155; font-size: 11.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.s7-market-side__row b { color: #0C233C; font-size: 11.5px; font-variant-numeric: tabular-nums; }
.s7-market-side__insight { background: #EEF5FF; border-left: 3px solid #00B8F5; border-radius: 6px; padding: 7px 9px; margin: 10px 0 7px; color: #243B53; font-size: 10.5px; line-height: 1.5; }
.s7-market-side__note { color: #94A3B8; font-size: 9.5px; line-height: 1.45; margin: 9px 0 0; }

.s7-premium-panel-title { color: #00338D; font-size: 12.5px; font-weight: 700; line-height: 1.3; margin: 4px 0 1px; }
.s7-premium-panel-sub { color: #64748B; font-size: 10.5px; line-height: 1.4; margin: 0 0 4px; min-height: 29px; }

.s7-capital-insight { border: 1px solid #DCE6F5; border-radius: 14px; padding: 16px 18px 14px; margin: 6px 0 14px; background: linear-gradient(135deg,#FFFFFF 0%,#F7FAFF 55%,#EEF4FF 100%); box-shadow: 0 8px 22px rgba(0,51,141,.07); }
.s7-capital-insight__head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin: 0 0 12px; }
.s7-capital-insight__title { color: #0C233C; font-size: 15px; font-weight: 800; letter-spacing: .2px; }
.s7-capital-insight__sub { color: #64748B; font-size: 11px; margin-top: 3px; }
.s7-capital-insight__badge { display: inline-flex; align-items: center; background: #00338D; color: #FFFFFF; border-radius: 999px; padding: 3px 10px; font-size: 10px; font-weight: 800; letter-spacing: .5px; white-space: nowrap; }
.s7-capital-stats { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 10px; margin: 0 0 12px; }
.s7-capital-stat { background: #FFFFFF; border: 1px solid #DCE6F5; border-radius: 10px; padding: 10px 12px 9px; box-sizing: border-box; border-top: 3px solid #00338D; }
.s7-capital-stat--pink { border-top-color: #FD349C; }
.s7-capital-stat--muted { border-top-color: #94A3B8; }
.s7-capital-stat__label { color: #64748B; font-size: 10px; font-weight: 700; letter-spacing: .3px; }
.s7-capital-stat__value { color: #00338D; font-size: 23px; font-weight: 900; line-height: 1.3; margin: 2px 0 1px; }
.s7-capital-stat--pink .s7-capital-stat__value { color: #C2186B; }
.s7-capital-stat--muted .s7-capital-stat__value { color: #475569; }
.s7-capital-stat__value--small { font-size: 16px; padding: 3px 0 2px; }
.s7-capital-stat__note { color: #94A3B8; font-size: 10px; }
.s7-capital-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; }
.s7-capital-card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 10px 12px 9px; box-sizing: border-box; border-left: 3px solid #00338D; }
.s7-capital-card--accent { border-left-color: #FD349C; background: #FFFAFC; }
.s7-capital-card__title { color: #0C233C; font-size: 12px; font-weight: 800; margin: 0 0 5px; display: flex; align-items: center; gap: 6px; }
.s7-capital-tag { display: inline-flex; align-items: center; background: #EEF4FF; color: #00338D; border-radius: 999px; padding: 1px 7px; font-size: 9px; font-weight: 800; letter-spacing: .4px; }
.s7-capital-tag--pink { background: #FDECF4; color: #C2186B; }
.s7-capital-card__body { color: #334155; font-size: 11.5px; line-height: 1.75; margin: 0; text-align: justify; }
"""

_BACK_CSS = """
.cover--back { background: linear-gradient(135deg, #00338D 0%, #1E49E2 100%); }
"""


def _image_data_uri(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_file():
        return ""
    suffix = candidate.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(suffix, "application/octet-stream")
    payload = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _cover(meta: ReportMeta, *, back: bool) -> str:
    image_path = meta.back_cover_image if back else meta.front_cover_image
    data_uri = _image_data_uri(image_path) if image_path else ""
    background = f'<img src="{data_uri}" alt="" />' if data_uri else ""
    if back:
        return f'<section class="page cover cover--back">{background}</section>'
    return (
        f'<section class="page cover">{background}'
        f'<div class="cover-text">'
        f'<div class="cover-title">{html.escape(meta.title)}</div>'
        f'<div class="cover-subtitle">{html.escape(meta.subtitle)}</div>'
        f'<div class="cover-date">{html.escape(meta.date_text)}</div>'
        f"</div></section>"
    )


def _render_item(item: ContentItem) -> str:
    caption = (
        f'<div class="figcaption">{html.escape(item.caption)}</div>' if item.caption else ""
    )
    if item.kind == "svg":
        payload = item.payload.lstrip()
        if payload.startswith("<?xml"):
            payload = payload.split("?>", 1)[1]
        cls = "figure full" if _is_wide_chart(item) else "figure"
        return f'<div class="{cls}">{payload}{caption}</div>'
    if item.kind == "table":
        return f'<div class="table-wrap">{item.payload}{caption}</div>'
    if item.kind == "image":
        return f'<div class="figure"><img src="{item.payload}" style="max-width:100%;max-height:540px;" />{caption}</div>'
    if item.kind == "metric":
        data = item.payload if isinstance(item.payload, dict) else {}
        delta = str(data.get("delta") or "")
        tone = str(data.get("tone") or "normal")
        if tone not in ("normal", "inverse", "off"):
            tone = "normal"
        delta_html = (
            f'<div class="metric-delta delta-{tone}">{html.escape(delta)}</div>'
            if delta
            else ""
        )
        return (
            '<div class="metric-card">'
            f'<div class="metric-label">{html.escape(str(data.get("label") or ""))}</div>'
            f'<div class="metric-value">{html.escape(str(data.get("value") or ""))}</div>'
            f"{delta_html}</div>"
        )
    return f'<div class="module-note">{item.payload}{caption}</div>'


@dataclass
class Block:
    """One indivisible layout block inside a module page.

    ``est`` is the estimated rendered height in CSS pixels; the renderer can
    replace it with a measured height before pagination runs.
    """

    html: str
    est: float
    charts: int = 0


def _units(items: list[ContentItem]) -> list[list[ContentItem]]:
    """Group a short caption with the chart that follows it.

    Streamlit renders a chart title as a small markdown block right before the
    chart; keeping them in one unit means the title never drifts away from its
    chart when pages are packed or charts are placed side by side.

    Only *short* text is treated as a chart title.  Report cards, insight
    panels and analysis paragraphs are HTML blocks in their own right and must
    keep their full styling, so they are never merged into a title.
    """
    units: list[list[ContentItem]] = []
    index = 0
    while index < len(items):
        item = items[index]
        if item.kind == "svg":
            units.append([item])
            index += 1
        elif item.layout_group is not None:
            card_key = (item.layout_group, item.layout_index)
            card: list[ContentItem] = []
            while index < len(items) and (
                items[index].layout_group, items[index].layout_index
            ) == card_key:
                card.append(items[index])
                index += 1
            units.append(card)
        elif (
            item.kind in ("html", "markdown")
            and "下表显示每家公司" in _plain_text(item.payload)
            and index + 1 < len(items)
            and items[index + 1].kind == "table"
        ):
            units.append([item, items[index + 1]])
            index += 2
        elif (
            item.kind in ("html", "markdown")
            and _looks_like_chart_title(item)
            and index + 1 < len(items)
            and items[index + 1].kind == "svg"
        ):
            units.append([item, items[index + 1]])
            index += 2
        else:
            units.append([item])
            index += 1
    return units


def _plain_text(markup: object) -> str:
    return re.sub(r"<[^>]+>", " ", str(markup or "")).strip()


def _looks_like_chart_title(item: ContentItem) -> bool:
    """True for the small markdown label Streamlit prints above a chart."""
    markup = str(item.payload or "")
    if len(markup) > 260:
        return False
    if "<style" in markup or "<table" in markup or "<img" in markup:
        return False
    # Report cards / insight panels are content blocks, not chart titles.
    for marker in (
        "s7-insight-card",
        "s7-capital",
        "s7-market-side",
        "s7-chart-summary",
        "solvency-report-analysis",
        "s7-pattern",
        "s7-uw-",
        "s7-premium-panel",
        "annual-company-title",
        "solvency-chart-legend",
        "s7-block",
    ):
        if marker in markup:
            return False
    text = _plain_text(markup)
    return 0 < len(text) <= 90


def _cluster_units(units: list[list[ContentItem]]) -> list[list[ContentItem]]:
    """Move long explanatory text behind a run of charts.

    Charts that are separated by a paragraph cannot share a row; the paragraph
    keeps following its charts, just below the whole chart run, which is how
    the web page reads as well.
    """
    clustered: list[list[ContentItem]] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if unit[-1].kind != "svg" or any(
            item.layout_group is not None for item in unit
        ):
            clustered.append(unit)
            index += 1
            continue
        charts = [unit]
        texts: list[list[ContentItem]] = []
        cursor = index + 1
        while cursor < len(units):
            nxt = units[cursor]
            if nxt[-1].kind == "svg":
                charts.append(nxt)
                cursor += 1
            elif len(nxt) == 1 and nxt[0].kind in ("html", "markdown"):
                texts.append(nxt)
                cursor += 1
            else:
                break
        clustered.extend(charts + texts)
        index = cursor
    return clustered


def _render_unit(unit: list[ContentItem]) -> str:
    if any(item.layout_group is not None for item in unit):
        return '<article class="company-card">' + "".join(
            _render_item(item) for item in unit
        ) + "</article>"
    if len(unit) == 2 and unit[1].kind == "table":
        return (
            '<div class="table-section">'
            f'{_render_item(unit[0])}{_render_item(unit[1])}'
            '</div>'
        )
    if len(unit) == 2 and unit[1].kind == "svg":
        return (
            '<div class="figure-wrap">'
            f'<div class="figure-title">{unit[0].payload}</div>'
            f"{_render_item(unit[1])}</div>"
        )
    if len(unit) == 1:
        return _render_item(unit[0])
    return "".join(_render_item(item) for item in unit)


def _is_wide_chart(item: ContentItem) -> bool:
    """Only inherently wide charts occupy a full PDF row."""
    if item.kind != "svg":
        return False
    if item.panels and item.panels > 6:
        return True
    return bool(item.aspect and item.aspect >= 3.2)


def _chart_height(item: ContentItem, *, full: bool) -> float:
    """Rendered height of a chart in a full-width or half-width cell."""
    width = CONTENT_WIDTH if full else (CONTENT_WIDTH - 16) / 2
    cap = MAX_FULL_FIGURE_HEIGHT if full else MAX_PAIR_FIGURE_HEIGHT
    aspect = item.aspect if item.aspect and item.aspect > 0 else 0.0
    if aspect:
        height = width / aspect
    else:
        height = float(item.max_height_px or 340)
    return min(height, cap) + 26.0


def _estimate_height(item: ContentItem) -> float:
    """Rough content height in CSS pixels for text, tables and metrics."""
    kind = item.kind
    if kind in ("altair", "plotly", "svg"):
        return _chart_height(item, full=_is_wide_chart(item))
    if kind == "image":
        return 400.0
    if kind == "table":
        text = str(item.payload or "")
        rows = text.count("<tr")
        return rows * 21.0 + 40
    if kind == "metric":
        return 100.0
    if kind == "columns":
        buckets = item.payload[0] if isinstance(item.payload, tuple) else []
        if not buckets:
            return 200.0
        bucket_heights = [_bucket_height(bucket) for bucket in buckets]
        if len(buckets) > 2:
            rows = (len(buckets) + 1) // 2
            return rows * max(bucket_heights) + (rows - 1) * 22.0
        return max(bucket_heights)
    text = _plain_text(item.payload)
    markup = str(item.payload or "")
    # 15px text at ~1512px width fits roughly 100 CJK characters per line.
    char_lines = max(1, int(len(text) / 96) + 1)
    # HTML blocks (cards, lists, paragraphs) each consume their own line(s);
    # counting them avoids badly under-estimating card-heavy modules.
    block_elements = len(re.findall(r"<(?:p|div|li|ul|ol|br|tr|h[1-6])\b", markup))
    lines = max(char_lines, int(block_elements * 1.15))
    return lines * 25.0 + 18


def _bucket_height(bucket: list[ContentItem]) -> float:
    """Estimated height of one column bucket inside a ``columns`` block."""
    if not bucket:
        return 0.0
    total = 0.0
    row: list[ContentItem] = []
    for item in bucket:
        if item.kind in ("svg", "image") and not _is_wide_chart(item):
            row.append(item)
            if len(row) == 2:
                total += max(_chart_height(i, full=False) for i in row)
                row = []
        else:
            if row:
                total += max(_chart_height(i, full=False) for i in row)
                row = []
            total += _estimate_height(item)
    if row:
        total += max(_chart_height(i, full=False) for i in row)
    return total


def _blocks_for(items: list[ContentItem]) -> list[Block]:
    """Split a module's content into layout blocks.

    Blocks mirror :func:`_render_items` one-to-one: each block is exactly the
    markup emitted for one row/card, so a measured height can drive pagination
    without re-implementing the layout.
    """
    if not items:
        return []
    units = _cluster_units(_units(items))
    blocks: list[Block] = []
    row: list[list[ContentItem]] = []
    metrics: list[ContentItem] = []

    def flush_row() -> None:
        nonlocal row
        company_cards = bool(row) and all(
            any(item.layout_group is not None for item in unit) for unit in row
        )
        per_row = 3 if company_cards else 2
        for start in range(0, len(row), per_row):
            chunk = row[start : start + per_row]
            if len(chunk) > 1:
                inner = "".join(_render_unit(unit) for unit in chunk)
                est = max(_chart_height(unit[-1], full=False) for unit in chunk) + 8.0
                blocks.append(
                    Block(f'<div class="figures-row">{inner}</div>', est, len(chunk))
                )
            elif chunk:
                unit = chunk[0]
                blocks.append(
                    Block(_render_unit(unit), _estimate_height(unit[-1]) + 8.0, 1)
                )
        row = []

    def flush_metrics() -> None:
        nonlocal metrics
        if metrics:
            inner = "".join(_render_item(item) for item in metrics)
            blocks.append(Block(f'<div class="metric-row">{inner}</div>', 116.0))
            metrics = []

    for unit in units:
        first = unit[0]
        if first.kind == "columns":
            flush_row()
            flush_metrics()
            buckets, weights = first.payload
            if not buckets:
                continue
            per_bucket = [_blocks_for(bucket) for bucket in buckets]
            heights = [sum(block.est for block in parts) for parts in per_bucket]
            # Keep the side-by-side card layout only when both panels fit on a
            # page; a tall pair would be shrunk to illegibility by the
            # renderer's fit-on-one-page fallback.
            side_by_side = len(buckets) == 2 and max(heights, default=0) <= 620
            if side_by_side:
                cells = []
                for index, bucket in enumerate(buckets):
                    weight = weights[index] if index < len(weights) else 1.0
                    inner = _render_items(bucket)
                    cells.append(f'<div class="col" style="flex: {weight} 1 0;">{inner}</div>')
                blocks.append(
                    Block('<div class="two-col">' + "".join(cells) + "</div>", max(heights))
                )
            else:
                for parts in per_bucket:
                    blocks.extend(parts)
            continue
        if first.kind == "metric":
            flush_row()
            metrics.append(first)
            continue
        if first.kind == "table":
            flush_row()
            flush_metrics()
            blocks.append(Block(_render_unit(unit), _estimate_height(first), 0))
            continue
        flush_metrics()
        chart = unit[-1]
        if chart.kind == "svg":
            row.append(unit)
            if chart.layout_group is None and len(row) == 2:
                flush_row()
            elif chart.layout_group is not None and len(row) == 3:
                flush_row()
        else:
            flush_row()
            blocks.append(Block(_render_unit(unit), _estimate_height(chart) + 8.0, 0))
    flush_row()
    flush_metrics()
    return blocks


def _render_items(items: list[ContentItem]) -> str:
    """Render a list of content items exactly as :func:`_blocks_for` groups them."""
    return "".join(block.html for block in _blocks_for(items))


def _paginate(
    items: list[ContentItem],
    *,
    measured: list[float] | None = None,
    first_capacity: float = FIRST_PAGE_HEIGHT,
    next_capacity: float = NEXT_PAGE_HEIGHT,
    max_pages: int = 20,
) -> tuple[list[list[Block]], list[Block]]:
    """Pack blocks onto fixed pages using measured (or estimated) heights."""
    blocks = _blocks_for(items)
    pages: list[list[Block]] = []
    current: list[Block] = []
    used = 0.0
    capacity = first_capacity
    for index, block in enumerate(blocks):
        height = block.est
        if measured is not None and index < len(measured) and measured[index] > 0:
            height = measured[index]
        height += 6.0  # block gap from the CSS margin
        if current and used + height > capacity:
            pages.append(current)
            current = [block]
            used = height
            capacity = next_capacity
        else:
            current.append(block)
            used += height
    if current:
        pages.append(current)
    if len(pages) > max_pages:
        pages = pages[: max_pages - 1] + [sum(pages[max_pages - 1 :], [])]
    return pages, blocks


_PROBE_CSS = """
.probe .report-page { height: auto; overflow: visible; }
.probe .module-body { height: auto; overflow: visible; }
.probe .module-content > .s7-block { margin-bottom: 6px; }
"""


def build_probe_html(meta: ReportMeta, modules: list[ModuleSnapshot]) -> str:
    """HTML used to measure real block heights in Chromium before paginating."""
    sections = []
    for module_index, module in enumerate(modules):
        blocks = _blocks_for(module.items)
        body = "".join(
            f'<div class="s7-block" data-m="{module_index}" data-b="{block_index}">'
            f"{block.html}</div>"
            for block_index, block in enumerate(blocks)
        )
        sections.append(
            f'<section class="page report-page probe">'
            f'<div class="module-body"><div class="module-content">{body}'
            f"</div></div></section>"
        )
    if not sections:
        sections.append('<section class="page"></section>')
    return _document(meta, "".join(sections), extra_css=_PROBE_CSS)


def _module_page(
    module: ModuleSnapshot,
    meta: ReportMeta,
    *,
    page_blocks: list[Block],
    first: bool,
    last: bool,
) -> str:
    language = meta.language
    body = "".join(block.html for block in page_blocks)
    if not body and first:
        body = f'<div class="module-note">{html.escape(label(language, "no_content"))}</div>'
    note = (
        f'<div class="module-note">{html.escape(module.note)}</div>'
        if last and module.note
        else ""
    )
    section = html.escape(module.section or "")
    title = (
        f'<h1 class="module-title">{html.escape(module.title)}</h1>' if first else ""
    )
    cls = "page report-page" if first else "page report-page cont"
    return f"""
<section class="{cls}">
  <header class="page-header"><span class="hdr-left">{html.escape(meta.title)}</span><span class="hdr-right">{section}</span></header>
  {title}
  <div class="module-body"><div class="module-content">{body}{note}</div></div>
</section>
"""


def _document(meta: ReportMeta, body: str, *, extra_css: str = "") -> str:
    lang = "en" if meta.language == "en" else "zh"
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8" />
<title>{html.escape(meta.title)}</title>
<style>{_CSS}{_BACK_CSS}{extra_css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def build_report_html(
    meta: ReportMeta,
    modules: list[ModuleSnapshot],
    measured: dict[int, list[float]] | None = None,
) -> str:
    """Return a complete standalone HTML document for the PDF renderer.

    ``measured`` maps a module index to the block heights measured in Chromium
    by :func:`build_probe_html`; when omitted, height estimates are used.
    """
    pages = [_cover(meta, back=False)]
    for module_index, module in enumerate(modules):
        heights = (measured or {}).get(module_index)
        chunks, _ = _paginate(
            module.items,
            measured=heights,
            first_capacity=FIRST_PAGE_HEIGHT,
            next_capacity=NEXT_PAGE_HEIGHT,
            max_pages=20,
        )
        for index, chunk in enumerate(chunks):
            pages.append(
                _module_page(
                    module,
                    meta,
                    page_blocks=chunk,
                    first=index == 0,
                    last=index == len(chunks) - 1,
                )
            )
    pages.append(_cover(meta, back=True))
    return _document(meta, "".join(pages))
