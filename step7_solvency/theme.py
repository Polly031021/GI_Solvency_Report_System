"""STEP7 报告专用 CSS（屏幕与打印两套样式）。"""

from __future__ import annotations

import streamlit as st


def render_report_style() -> None:
    st.markdown(
        """
        <style>
        /* STEP7 uses the same white canvas as the chart cards.  Scope the
           override to the report page so other workflow tabs keep their
           platform background. */
        html:has(.solvency-report-title),
        body:has(.solvency-report-title),
        [data-testid="stAppViewContainer"]:has(.solvency-report-title),
        [data-testid="stMain"]:has(.solvency-report-title) {
            background:#FFFFFF!important;
        }
        body:has(.solvency-report-title) [data-testid="stHeader"] {
            background:#FFFFFF!important;
        }
        /* 报告页的主滚动容器固定在 stMain 上。Vega 图表异步挂载后，
           Streamlit 偶尔不会重新计算公式高度，这里显式恢复纵向滚动，
           避免鼠标停在图表上时出现“下面还有内容但滚不动”的情况。 */
        [data-testid="stMain"]:has(.solvency-report-title) {
            overflow-y:auto!important;
            overflow-x:hidden!important;
            overscroll-behavior-y:contain;
            scrollbar-gutter:stable;
        }
        [data-testid="stMainBlockContainer"]:has(.solvency-report-title) {
            overflow:visible!important;
            padding-bottom:4rem!important;
        }
        [class*="st-key-s7_report_module_"],
        [class*="st-key-s7_trend_group_"],
        .solvency-report-title, .solvency-module-title,
        .solvency-report-analysis, .solvency-report-footnote,
        /* STEP7 industry-overview insight card: one summary surface per module. */
        .s7-insight-card {
            --s7-insight-accent:#00338D;
            position:relative; box-sizing:border-box;
            border:1px solid #E2E8F0; border-left:5px solid var(--s7-insight-accent);
            border-radius:12px; padding:13px 18px 12px; margin:6px 0 14px;
            background:linear-gradient(135deg,#FFFFFF 0%,#F8FBFF 55%,#EEF4FF 100%);
            box-shadow:0 6px 18px rgba(0,51,141,.06);
            break-inside:avoid-page; page-break-inside:avoid;
            font-family:Microsoft YaHei, 微软雅黑, sans-serif;
        }
        .s7-insight-card__top {
            display:flex; align-items:center; gap:10px; flex-wrap:wrap;
            margin:0 0 7px;
        }
        .s7-insight-card__eyebrow {
            display:inline-flex; align-items:center;
            background:var(--s7-insight-accent); color:#FFFFFF;
            border-radius:999px; padding:2px 9px;
            font-size:10px; font-weight:700; letter-spacing:.6px;
        }
        .s7-insight-card__title {
            color:#0C233C; font-size:12px; font-weight:700; letter-spacing:.3px;
        }
        .s7-insight-card__headline {
            color:#1E293B; font-size:18px; font-weight:650; line-height:1.45;
            margin:0 0 5px;
        }
        .s7-insight-card__value {
            color:var(--s7-insight-accent); font-size:22px; font-weight:900;
            margin:0 4px;
        }
        .s7-insight-card__delta {
            display:inline-flex; align-items:center;
            border-radius:999px; padding:1px 8px; margin-left:4px;
            font-size:12px; font-weight:800; vertical-align:2px;
        }
        .s7-insight-card__delta--up {background:#EAF7EE;color:#146C12;border:1px solid #BFE5C8;}
        .s7-insight-card__delta--down {background:#FDECEC;color:#B0191C;border:1px solid #F5C2C2;}
        .s7-insight-card__delta--neutral {background:#EEF4FF;color:#00338D;border:1px solid #C7D7F5;}
        .s7-insight-card__bullets {
            display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
            gap:3px 18px; margin:4px 0 0; padding-left:17px;
            color:#334155; font-size:12px; line-height:1.6;
        }
        .s7-insight-card__bullets li::marker {color:var(--s7-insight-accent);}
        .s7-insight-card__foot {
            color:#64748B; font-size:10px; line-height:1.5;
            margin-top:6px; padding-top:5px; border-top:1px dashed #D8E2F0;
        }
        .solvency-chart-legend {
            font-family:Microsoft YaHei, 微软雅黑, sans-serif;
        }
        [class*="st-key-s7_report_module_"] {
            font-size:14px;
        }
        [class*="st-key-s7_report_module_"] p,
        [class*="st-key-s7_report_module_"] label,
        [class*="st-key-s7_report_module_"] button,
        [class*="st-key-s7_report_module_"] input {
            font-family:Microsoft YaHei, 微软雅黑, sans-serif!important;
            font-size:14px;
        }
        [data-testid="stMainBlockContainer"] {
            max-width:none!important; padding-left:10px!important; padding-right:10px!important;
        }
        .solvency-report-title {
            color:#00338D; font-size:30px; font-weight:900;
            border-bottom:2px solid #00338D; padding-bottom:8px; margin:6px 0 18px;
        }
        .solvency-module-title {
            color:#0C233C; font-size:17px; font-weight:800; letter-spacing:.5px;
            background:linear-gradient(90deg,#F1F6FF 0%,#FFFFFF 82%);
            border:1px solid #DCE6F5; border-left:5px solid #00338D;
            border-radius:9px; padding:9px 16px; width:fit-content; max-width:100%;
            box-shadow:0 3px 10px rgba(12,35,60,.06);
            margin:10px 0 18px;
        }
        [class*="st-key-s7_report_module_"] h3 {
            color:#0C233C!important; font-size:17px!important; font-weight:800!important;
            letter-spacing:.5px!important;
            background:linear-gradient(90deg,#F1F6FF 0%,#FFFFFF 82%)!important;
            border:1px solid #DCE6F5!important; border-left:5px solid #00338D!important;
            border-radius:9px!important; padding:9px 16px!important;
            width:fit-content!important; max-width:100%!important;
            margin:10px 0 18px!important;
            box-shadow:0 3px 10px rgba(12,35,60,.06)!important;
        }
        /* 公司业绩展示：每家公司一张独立的浅色卡片，标题、最新名次和图表
           共用同一视觉表面，避免在白色画布上“融为一体”。 */
        [class*="st-key-s7_performance_panel_"] {
            background:linear-gradient(180deg,#F7FAFF 0%,#FFFFFF 72%);
            border:1px solid #DCE6F5; border-radius:12px;
            padding:10px 11px 5px;
            box-shadow:0 5px 16px rgba(12,35,60,.06);
            break-inside:avoid-page; page-break-inside:avoid;
            overflow:visible!important;
            box-sizing:border-box;
        }
        [class*="st-key-s7_performance_panel_"] > [data-testid="stVerticalBlock"] {
            gap:.22rem!important;
        }
        [class*="st-key-s7_performance_panel_"] [data-testid="stVegaLiteChart"] {
            background:#FFFFFF; border:1px solid #E4EBF5; border-radius:9px;
            padding:3px 4px 0; overflow:hidden;
        }
        .s7-pattern-panel-head {
            display:flex; align-items:center; gap:6px; min-width:0;
            margin:0 0 2px;
        }
        .s7-pattern-panel-title {
            color:#0C233C; font-size:13px; font-weight:800; line-height:1.32;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
            min-width:0; flex:1 1 auto;
        }
        .s7-pattern-panel-title::before {
            content:""; display:inline-block; width:6px; height:6px;
            border-radius:50%; background:#00338D; margin-right:6px;
            vertical-align:1px;
        }
        .s7-pattern-panel-meta {
            display:flex; flex-wrap:wrap; gap:4px; margin:0 0 3px;
        }
        .s7-pattern-pill {
            display:inline-flex; align-items:center;
            background:#EEF4FF; color:#1E3A8A;
            border:1px solid #D5E2FA; border-radius:999px;
            padding:1px 7px; font-size:10px; font-weight:700; line-height:1.6;
            white-space:nowrap;
        }
        .s7-pattern-pill--muted {
            background:#F8FAFC; color:#64748B; border-color:#E2E8F0;
        }
        .s7-pattern-group {
            display:flex; align-items:center; gap:10px; flex-wrap:wrap;
            background:linear-gradient(90deg,#FDF0ED 0%,#FFF9F7 70%,#FFFFFF 100%);
            border:1px solid #F4D8D2; border-left:5px solid #D94330;
            border-radius:10px; padding:7px 13px; margin:20px 0 11px;
            box-shadow:0 2px 8px rgba(217,67,48,.05);
        }
        .s7-pattern-group__name {
            color:#8C1D18; font-size:14px; font-weight:800; letter-spacing:.3px;
        }
        .s7-pattern-group__count {
            display:inline-flex; align-items:center;
            background:#D94330; color:#FFFFFF; border-radius:999px;
            padding:1px 8px; font-size:11px; font-weight:800;
        }
        .s7-pattern-group__hint {
            color:#9A5B52; font-size:11px; font-weight:500;
        }
        /* 能力五边形的公司名称已作为 Vega 分面标题渲染在每张小图正上方，
           名称与五边形天然共用中心线，不再需要额外对齐样式。 */
        .solvency-analysis-note {
            background:#F2F6FC; border-left:4px solid #00338D;
            border-radius:4px; padding:9px 13px; margin:6px 0 0; color:#243B53;
        }
        .solvency-analysis-spacer {height:16px; width:100%;}
        .s7-uw-year-title {
            color:#00338D; font-size:16px; font-weight:800; letter-spacing:.4px;
            margin:2px 0 2px;
        }
        .s7-uw-year-sub {color:#64748B; font-size:11px; margin:0 0 4px;}
        .s7-uw-counts {
            display:flex; flex-wrap:wrap; align-items:center; gap:8px;
            margin:7px 0 10px;
        }
        .s7-uw-chip {
            display:inline-flex; align-items:center; gap:4px;
            border-radius:999px; padding:3px 10px;
            font-size:12px; font-weight:700; line-height:1.4;
        }
        .s7-uw-chip--profit {background:#EAF7EE;color:#146C12;border:1px solid #BFE5C8;}
        .s7-uw-chip--loss {background:#FDECEC;color:#B0191C;border:1px solid #F5C2C2;}
        .s7-uw-chip--even {background:#FFF7E0;color:#8A5A00;border:1px solid #F1D58A;}
        .s7-uw-muted {color:#64748B;font-size:11px;}
        .s7-uw-legend {
            display:flex; flex-wrap:wrap; gap:14px; align-items:center;
            color:#475569; font-size:11px; margin:2px 0 10px;
        }
        .s7-uw-legend span {display:inline-flex; align-items:center; gap:5px;}
        .s7-uw-dot {width:9px; height:9px; border-radius:50%; display:inline-block;}
        .s7-uw-dash {
            width:24px; height:0; border-top:2px dashed #64748B;
            display:inline-block;
        }
        .s7-uw-empty {
            min-height:275px; display:flex; align-items:center; justify-content:center;
            text-align:center; padding:28px 20px; box-sizing:border-box;
            border:1px dashed #CBD5E1; border-radius:10px;
            background:#F8FAFC; color:#94A3B8; font-size:12px; line-height:1.8;
        }
        .solvency-chart-legend {
            display:flex; flex-wrap:wrap; justify-content:flex-end;
            align-items:center; gap:10px 18px;
            margin:2px 10px 8px 0; color:#0C233C; font-size:10px;
        }
        .solvency-chart-legend__item {display:inline-flex; align-items:center; gap:6px;}
        .solvency-chart-legend__symbol {
            --legend-color:#0C233C; display:inline-block; flex:0 0 auto;
        }
        .solvency-chart-legend__symbol--square {
            width:11px; height:11px; border-radius:2px; background:var(--legend-color);
        }
        .solvency-chart-legend__symbol--outline {
            width:13px; height:11px; border-radius:3px;
            border:2px solid var(--legend-color); background:transparent;
            box-sizing:border-box;
        }
        .solvency-chart-legend__symbol--line,
        .solvency-chart-legend__symbol--dash {
            width:22px; height:0; border-top:3px solid var(--legend-color);
        }
        .solvency-chart-legend__symbol--dash {border-top-style:dashed;}
        [class*="st-key-"][class*="_industry_premium_chart"] [data-testid="stVegaLiteChart"] {
            width:100%!important; max-width:100%!important;
            margin-left:auto!important; margin-right:auto!important;
        }
        .s7-premium-panel-title {
            color:#00338D; font-size:12.5px; font-weight:700;
            line-height:1.3; margin:4px 0 1px;
        }
        .s7-premium-panel-sub {
            color:#64748B; font-size:10.5px; line-height:1.4;
            margin:0 0 4px; min-height:29px;
        }
        [class*="st-key-"][class*="_industry_premium_chart"] .solvency-chart-legend {
            gap:6px 12px; min-height:52px; align-content:flex-start;
        }
        .solvency-analysis-ai {color:#D84315; font-size:12px; margin-top:5px;}
        .solvency-analysis-ai--error {color:#C00000;}
        .solvency-report-analysis {
            position:relative; box-sizing:border-box;
            background:linear-gradient(120deg,#F7FAFF 0%,#EEF4FF 58%,#FFFFFF 100%);
            border:1px solid #DCE6F5; border-left:4px solid #00338D;
            border-radius:10px; padding:11px 14px 10px; margin:6px 0 12px;
            box-shadow:0 4px 14px rgba(0,51,141,.06);
            break-inside:avoid-page; page-break-inside:avoid;
        }
        .solvency-report-analysis p {margin:2px 0; line-height:1.65;}
        .solvency-note-default {color:#0A1F5C; font-size:13px;}
        .solvency-note-custom {color:#1E49E2; font-size:14px; font-weight:600;}
        .solvency-report-footnote {color:#777; font-size:12px; font-style:italic; margin:4px 0 16px;}
        /* 图表前的数据型结论条：与 Excel 分析文字区分，保证每张图先给结论。 */
        .s7-chart-summary {
            position:relative; box-sizing:border-box;
            display:flex; align-items:flex-start; gap:11px;
            background:linear-gradient(120deg,#F7FAFF 0%,#EEF4FF 56%,#FFFFFF 100%);
            border:1px solid #D8E4F8; border-left:4px solid #00338D;
            border-radius:10px; padding:10px 14px 9px; margin:6px 0 12px;
            box-shadow:0 4px 14px rgba(0,51,141,.06);
            break-inside:avoid-page; page-break-inside:avoid;
        }
        .s7-chart-summary::after {
            content:""; position:absolute; right:14px; top:12px;
            width:28px; height:4px; border-radius:2px;
            background:#00338D; opacity:.18;
        }
        .s7-chart-summary__tag {
            flex:0 0 auto; display:inline-flex; align-items:center;
            background:linear-gradient(135deg,#00338D 0%,#1E49E2 100%);
            color:#FFFFFF; border-radius:999px;
            padding:3px 10px; margin-top:1px;
            font-size:10px; font-weight:800; letter-spacing:.5px;
            box-shadow:0 2px 6px rgba(0,51,141,.18);
        }
        .s7-chart-summary__text {
            margin:0; padding-right:38px; color:#0C233C;
            font-size:13px; line-height:1.72; text-align:justify;
        }
        /* 公司小图横向滚动：每家公司固定宽度，超出页面宽度后出现横向滚动条。 */
        @media screen {
            [class*="st-key-annual_company_grid_scroll_"] {
                max-width:100%!important;
                overflow-x:auto!important; overflow-y:hidden!important;
                padding:2px 2px 12px!important;
                scrollbar-width:thin; scrollbar-color:#B9C7E2 #F2F6FC;
                overscroll-behavior-x:contain;
            }
            [class*="st-key-annual_company_grid_scroll_"] [data-testid="stHorizontalBlock"] {
                display:flex!important; flex-wrap:nowrap!important;
                align-items:flex-start!important;
                width:max-content!important; min-width:100%!important;
                gap:10px!important;
            }
            [class*="st-key-annual_company_grid_scroll_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                flex:0 0 300px!important;
                width:300px!important;
                min-width:300px!important;
                max-width:300px!important;
            }
            [class*="st-key-annual_company_grid_scroll_"]::-webkit-scrollbar {height:10px;}
            [class*="st-key-annual_company_grid_scroll_"]::-webkit-scrollbar-track {
                background:#F2F6FC; border-radius:6px;
            }
            [class*="st-key-annual_company_grid_scroll_"]::-webkit-scrollbar-thumb {
                background:#B9C7E2; border-radius:6px;
            }
            [class*="st-key-annual_company_grid_scroll_"]::-webkit-scrollbar-thumb:hover {
                background:#93A9D2;
            }
            /* 固定宽度卡片内让较长的公司名换行，避免被裁切。 */
            [class*="st-key-annual_company_grid_scroll_"] .annual-company-title {
                white-space:normal!important; overflow-wrap:anywhere;
                line-height:1.3; min-height:34px; margin:0 0 16px;
            }
        }
        [class*="st-key-annual_company_panel_"] {
            border:1px solid #EAEAEA!important; border-radius:0!important;
            padding:14px 4px 2px!important; box-shadow:none!important;
            box-sizing:border-box!important;
        }
        .annual-company-title {
            color:#00338D; font-size:13px; font-weight:700; line-height:1.25;
            text-align:center; white-space:nowrap; min-height:17px;
            margin:0 0 30px; padding:0 4px;
        }
        [class*="st-key-annual_company_panel_"] [data-testid="stMarkdownContainer"]:has(.annual-company-title) {
            margin:0!important; padding:0!important;
        }
        [class*="st-key-annual_company_panel_gray_"] {
            background:rgba(200,200,200,.12)!important;
        }
        [class*="st-key-annual_company_panel_white_"] {
            background:rgba(255,255,255,0)!important;
        }
        [class*="st-key-annual_company_panel_tracked_"] {
            background:rgba(0,51,141,.03)!important;
            border:1.5px solid rgba(0,51,141,.35)!important;
        }
        [class*="st-key-s7_report_module_"] [data-testid="stTable"] table,
        [class*="st-key-s7_report_module_"] [data-testid="stDataFrame"] {
            font-family:Microsoft YaHei, 微软雅黑, sans-serif!important;
            font-size:11px!important;
        }
        [class*="st-key-s7_report_module_"] [data-testid="stTable"] thead th {
            background:#00338D; color:#FFFFFF; font-size:11px;
            font-weight:700; padding:6px 4px; border:1px solid #FFFFFF;
        }
        [class*="st-key-s7_report_module_"] [data-testid="stTable"] tbody td {
            font-size:11px; padding:4px; border:1px solid #EAEAEA;
        }
        [class*="st-key-s7_report_module_"] {break-inside:avoid-page; page-break-inside:avoid;}
        [class*="st-key-s7_trend_group_"] {break-inside:avoid-page; page-break-inside:avoid;}
        .solvency-print-cover {
            position:relative; width:100%; aspect-ratio:16/9; overflow:hidden;
            margin:0; padding:0; background:#00338D;
            -webkit-print-color-adjust:exact; print-color-adjust:exact;
        }
        .solvency-print-cover img, .solvency-cover-fallback {
            position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
        }
        .solvency-cover-fallback {background:linear-gradient(135deg,#00338D,#1E49E2);}
        .solvency-print-cover__text {
            position:absolute; inset:0; z-index:2; display:flex; flex-direction:column;
            justify-content:center; align-items:flex-start; padding:0 8%; box-sizing:border-box;
            color:#fff; text-shadow:2px 2px 5px rgba(0,0,0,.45);
        }
        .solvency-print-cover__title {font-size:48px; font-weight:900; line-height:1.35;}
        .solvency-print-cover__subtitle {font-size:23px; font-weight:650; margin-top:14px;}
        .solvency-print-cover__date {font-size:19px; margin-top:18px;}
        .solvency-print-cover--front {page-break-after:always; break-after:page;}
        .solvency-print-cover--back {page-break-before:always; break-before:page;}
        @media print {
            [data-testid="stHeader"], [data-testid="stSidebar"],
            [data-testid="stToolbar"], [data-testid="collapsedControl"],
            [data-testid="stExpander"], div[role="tablist"], h1,
            .platform-page-heading, .solvency-report-title,
            .solvency-no-print,
            [class*="st-key-capital_bubble_print_controls_"],
            [class*="st-key-risk_scatter_print_controls_"] {display:none!important;}
            .capital_efficiency_zoom_brush,
            .capital_efficiency_zoom_brush_bg,
            [class*="capital_efficiency_zoom_"][class*="_brush"],
            .risk_ratio_zoom_brush,
            .risk_ratio_zoom_brush_bg,
            [class*="risk_ratio_zoom_"][class*="_brush"] {
                display:none!important;
            }
            [data-testid="stElementContainer"]:has(.platform-page-heading),
            [data-testid="stElementContainer"]:has(.solvency-report-title) {display:none!important;}
            [class*="st-key-s7_report_module_"] {
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-s7_report_module_"]:has(.solvency-grouped-trend) {
                break-inside:auto!important; page-break-inside:auto!important;
            }
            /* A dual-view metric deliberately spans two sheets: the trend stays
               on the first sheet and the complete company-bar group starts the next. */
            [class*="st-key-s7_report_module_"]:has([class*="st-key-s7_company_bars_page_"]) {
                break-inside:auto!important; page-break-inside:auto!important;
            }
            /* The underwriting module contains three rows of paired charts.
               Let the module flow across sheets while keeping each row intact. */
            [class*="st-key-s7_report_module_"]:has(.solvency-underwriting-grid) {
                break-inside:auto!important; page-break-inside:auto!important;
            }
            [class*="st-key-underwriting_row_"] {
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            [class*="st-key-s7_company_bars_page_"] {
                break-before:page!important; page-break-before:always!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-s7_trend_group_"] {
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-s7_report_module_"] [data-testid="stVegaLiteChart"] {
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                max-width:100%!important;
            }
            /* Keep every company card intact.  The print-only grid packs cards
               tightly enough to avoid an otherwise nearly empty spill page. */
            [class*="st-key-annual_company_grid_"],
            [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] {
                display:grid!important;
                grid-template-columns:repeat(auto-fit,minmax(155px,1fr))!important;
                align-items:stretch!important; gap:8px!important;
                width:100%!important; min-width:0!important; max-width:100%!important;
                break-inside:auto!important; page-break-inside:auto!important;
            }
            [class*="st-key-annual_company_grid_"] > [data-testid="stLayoutWrapper"],
            [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] > [data-testid="stLayoutWrapper"],
            [class*="st-key-annual_company_grid_"] [data-testid="stElementContainer"]:has([class*="st-key-annual_company_panel_"]),
            [class*="st-key-annual_company_grid_"] [data-testid="stVerticalBlockBorderWrapper"]:has([class*="st-key-annual_company_panel_"]) {
                width:auto!important; min-width:0!important; max-width:100%!important;
                box-sizing:border-box!important;
            }
            /* st.columns keeps a flex-basis width after its parent becomes a
               print grid.  Fill the grid track instead of shrinking to it. */
            [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                justify-self:stretch!important; box-sizing:border-box!important;
            }
            [class*="st-key-annual_company_panel_"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                align-self:stretch!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
                overflow:visible!important;
            }
            [class*="st-key-annual_company_panel_"] [data-testid="stVegaLiteChart"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                min-height:280px!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            [class*="st-key-annual_company_panel_"] [data-testid="stVegaLiteChart"] canvas,
            [class*="st-key-annual_company_panel_"] [data-testid="stVegaLiteChart"] svg {
                width:100%!important; min-width:0!important; max-width:100%!important;
                height:280px!important; min-height:280px!important; max-height:280px!important;
            }
            /* Follow the annual-report platform's centered full-sheet canvas.
               Covers use the whole 16:9 page; body modules provide their own
               symmetric 12 mm side margins and 8 mm top margin. */
            html.solvency-print-mode-widescreen {
                --solvency-print-page-width:338.67mm;
                --solvency-print-page-height:190.5mm;
                --solvency-print-content-width:314.67mm;
                --solvency-print-content-height:174.5mm;
            }
            html.solvency-print-mode-widescreen,
            html.solvency-print-mode-widescreen body,
            html.solvency-print-mode-widescreen [data-testid="stAppViewContainer"],
            html.solvency-print-mode-widescreen [data-testid="stMain"] {
                width:var(--solvency-print-page-width)!important;
                max-width:var(--solvency-print-page-width)!important;
                margin:0 auto!important; padding:0!important;
                overflow:visible!important; box-sizing:border-box!important;
                -webkit-print-color-adjust:exact!important;
                print-color-adjust:exact!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stMainBlockContainer"] {
                width:var(--solvency-print-page-width)!important;
                min-width:var(--solvency-print-page-width)!important;
                max-width:var(--solvency-print-page-width)!important;
                margin:0 auto!important; padding:0!important;
                box-sizing:border-box!important; overflow:visible!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_"],
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                margin-left:0!important; margin-right:0!important;
                grid-template-columns:none!important;
                grid-auto-flow:column!important;
                grid-auto-columns:minmax(0,1fr)!important;
                gap:8px!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_panel_"] {
                height:100%!important; padding:12px 3px 2px!important;
            }
            html.solvency-print-mode-widescreen .annual-company-title {
                margin:0 0 12px!important;
            }
            html.solvency-print-mode-widescreen .key-solvency-overview {
                width:100%!important; min-width:0!important; max-width:100%!important;
                margin-left:0!important; margin-right:0!important;
                table-layout:fixed!important;
            }
            html.solvency-print-mode-widescreen .key-solvency-overview th,
            html.solvency-print-mode-widescreen .key-solvency-overview td {
                min-width:0!important; max-width:none!important;
                white-space:normal!important; overflow-wrap:anywhere!important;
                word-break:break-word!important; box-sizing:border-box!important;
                font-size:9px!important; padding:3px 2px!important;
            }
            html.solvency-print-mode-widescreen .key-solvency-overview th:first-child,
            html.solvency-print-mode-widescreen .key-solvency-overview td:first-child {
                width:6.5%!important; white-space:nowrap!important;
                word-break:keep-all!important; overflow-wrap:normal!important;
            }
            .solvency-report-page-break {break-before:page; page-break-before:always;}
            html.solvency-print-mode-widescreen .solvency-print-cover {
                width:338.67mm!important; height:190.5mm!important; max-width:338.67mm!important;
                aspect-ratio:auto!important; margin:0 auto!important; padding:0!important;
                box-sizing:border-box!important; overflow:hidden!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            html.solvency-print-mode-widescreen .solvency-print-cover img {
                width:100%!important; height:100%!important; object-fit:contain!important;
                object-position:center!important; display:block!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover),
            html.solvency-print-mode-widescreen [data-testid="stMarkdownContainer"]:has(.solvency-print-cover),
            html.solvency-print-mode-widescreen .stMarkdown:has(.solvency-print-cover) {
                width:338.67mm!important; max-width:338.67mm!important;
                height:190.5mm!important; margin:0 auto!important; padding:0!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stVerticalBlock"]:has(.solvency-print-cover) {
                gap:0!important; margin:0!important; padding:0!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] {
                width:var(--solvency-print-content-width)!important;
                min-width:var(--solvency-print-content-width)!important;
                max-width:var(--solvency-print-content-width)!important;
                min-height:0!important; padding:8mm 0 0!important;
                margin:0 auto!important; box-sizing:border-box!important;
                overflow:visible!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] > [data-testid="stVerticalBlock"],
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] [data-testid="stElementContainer"],
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] [data-testid="stVegaLiteChart"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                box-sizing:border-box!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] > [data-testid="stVerticalBlock"] {
                gap:0.45rem!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] img {
                max-width:100%!important; height:auto!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] table {
                width:100%!important; max-width:100%!important;
                table-layout:fixed!important; box-sizing:border-box!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover--front) {
                break-after:page!important; page-break-after:always!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover--back) {
                break-before:page!important; page-break-before:always!important;
            }
            .solvency-module-title {
                color:#00338D!important; font-size:30px!important; font-weight:900!important;
                background:transparent!important; border:none!important; border-radius:0!important;
                box-shadow:none!important; padding:0!important; margin:10px 0 8px!important;
            }
        }

        /* 核心资本占比：口径说明 + 行业观察的双层卡片 */
        .s7-capital-insight {
            border:1px solid #DCE6F5; border-radius:14px;
            padding:16px 18px 14px; margin:6px 0 14px;
            background:linear-gradient(135deg,#FFFFFF 0%,#F7FAFF 55%,#EEF4FF 100%);
            box-shadow:0 8px 22px rgba(0,51,141,.07);
            break-inside:avoid-page; page-break-inside:avoid;
            font-family:Microsoft YaHei, 微软雅黑, sans-serif;
        }
        .s7-capital-insight__head {
            display:flex; align-items:flex-start; justify-content:space-between;
            gap:12px; flex-wrap:wrap; margin:0 0 12px;
        }
        .s7-capital-insight__title {
            color:#0C233C; font-size:15px; font-weight:800; letter-spacing:.2px;
        }
        .s7-capital-insight__sub {
            color:#64748B; font-size:11px; margin-top:3px;
        }
        .s7-capital-insight__badge {
            display:inline-flex; align-items:center;
            background:#00338D; color:#FFFFFF; border-radius:999px;
            padding:3px 10px; font-size:10px; font-weight:800; letter-spacing:.5px;
            white-space:nowrap;
        }
        .s7-capital-stats {
            display:grid; grid-template-columns:repeat(3,minmax(0,1fr));
            gap:10px; margin:0 0 12px;
        }
        .s7-capital-stat {
            background:#FFFFFF; border:1px solid #DCE6F5; border-radius:10px;
            padding:10px 12px 9px; box-sizing:border-box;
            border-top:3px solid #00338D;
        }
        .s7-capital-stat--pink {border-top-color:#FD349C;}
        .s7-capital-stat--muted {border-top-color:#94A3B8;}
        .s7-capital-stat__label {
            color:#64748B; font-size:10px; font-weight:700; letter-spacing:.3px;
        }
        .s7-capital-stat__value {
            color:#00338D; font-size:23px; font-weight:900; line-height:1.3;
            margin:2px 0 1px;
        }
        .s7-capital-stat--pink .s7-capital-stat__value {color:#C2186B;}
        .s7-capital-stat--muted .s7-capital-stat__value {color:#475569;}
        .s7-capital-stat__value--small {font-size:16px; padding:3px 0 2px;}
        .s7-capital-stat__note {color:#94A3B8; font-size:10px;}
        .s7-capital-grid {
            display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px;
        }
        .s7-capital-card {
            background:#FFFFFF; border:1px solid #E2E8F0; border-radius:10px;
            padding:10px 12px 9px; box-sizing:border-box;
            border-left:3px solid #00338D;
        }
        .s7-capital-card--accent {border-left-color:#FD349C; background:#FFFAFC;}
        .s7-capital-card__title {
            color:#0C233C; font-size:12px; font-weight:800;
            margin:0 0 5px; display:flex; align-items:center; gap:6px;
        }
        .s7-capital-tag {
            display:inline-flex; align-items:center;
            background:#EEF4FF; color:#00338D; border-radius:999px;
            padding:1px 7px; font-size:9px; font-weight:800; letter-spacing:.4px;
        }
        .s7-capital-tag--pink {background:#FDECF4; color:#C2186B;}
        .s7-capital-card__body {
            color:#334155; font-size:11.5px; line-height:1.75; margin:0;
            text-align:justify;
        }
        @media (max-width:900px) {
            .s7-capital-stats, .s7-capital-grid {grid-template-columns:1fr;}
        }
        @media print {
            .s7-capital-insight {
                box-shadow:none!important; background:#FFFFFF!important;
                border:1px solid #CBD5E1!important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
