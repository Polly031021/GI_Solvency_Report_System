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
        [class*="st-key-s7_module_expander_"] [data-testid="stExpander"] {
            border:0!important;
            background:transparent!important;
        }
        [class*="st-key-s7_module_expander_"] details > summary {
            box-sizing:border-box;
            color:#0C233C!important;
            font-family:Microsoft YaHei, 微软雅黑, sans-serif!important;
            font-size:17px!important;
            font-weight:800!important;
            line-height:normal!important;
            letter-spacing:.5px!important;
            background:linear-gradient(90deg,#F1F6FF 0%,#FFFFFF 82%)!important;
            border:1px solid #DCE6F5!important;
            border-left:5px solid #00338D!important;
            border-radius:9px!important;
            padding:9px 16px!important;
            margin:10px 0 18px!important;
            width:fit-content!important;
            max-width:100%!important;
            box-shadow:0 3px 10px rgba(12,35,60,.06)!important;
        }
        [class*="st-key-s7_module_expander_"] details > summary p,
        [class*="st-key-s7_module_expander_"] details > summary div,
        [class*="st-key-s7_module_expander_"] details > summary [data-testid="stMarkdownContainer"] {
            color:#0C233C!important;
            font-family:Microsoft YaHei, 微软雅黑, sans-serif!important;
            font-size:17px!important;
            font-weight:800!important;
            line-height:normal!important;
            letter-spacing:.5px!important;
            margin:0!important;
        }
        [class*="st-key-s7_module_expander_"] details[open] > summary {
            margin-bottom:10px!important;
        }
        /* Step 7 company filters keep the complete option text visible in the
           dropdown checklist, including long company names. */
        [class*="st-key-s7_company_filter_checklist_"] [data-testid="stCheckbox"] label,
        [class*="st-key-s7_company_filter_checklist_"] [data-testid="stCheckbox"] p {
            white-space:normal!important;
            overflow:visible!important;
            text-overflow:clip!important;
            overflow-wrap:anywhere;
            line-height:1.45;
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
        .s7-market-side {
            min-height:424px; padding:16px 18px 14px; border-radius:12px;
            border:1px solid #D9E2F0;
            background:linear-gradient(180deg,#F7FAFF 0%,#FFFFFF 72%);
            box-shadow:0 2px 10px rgba(0,51,141,.05);
        }
        .s7-market-side__title {
            color:#00338D; font-size:15px; font-weight:800; letter-spacing:.3px;
            margin:0 0 10px;
        }
        .s7-market-side__stats {
            display:grid; grid-template-columns:repeat(3,minmax(0,1fr));
            gap:7px; margin:0 0 13px;
        }
        .s7-market-side__stat {
            background:#FFFFFF; border:1px solid #E3EAF5; border-radius:8px;
            padding:7px 8px 6px; min-width:0;
        }
        .s7-market-side__stat span {
            display:block; color:#64748B; font-size:9.5px; line-height:1.35;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        }
        .s7-market-side__stat strong {
            display:block; color:#0C233C; font-size:16px; line-height:1.25;
            font-variant-numeric:tabular-nums; white-space:nowrap;
        }
        .s7-market-side__stat small {
            color:#94A3B8; font-size:8.5px; margin-left:4px; font-weight:600;
        }
        .s7-market-side__label {
            color:#64748B; font-size:10px; font-weight:700; letter-spacing:.4px;
            margin:0 0 3px;
        }
        .s7-market-side__list {margin:0;}
        .s7-market-side__row {
            display:grid; grid-template-columns:9px minmax(0,1fr) auto;
            gap:7px; align-items:center; padding:2.5px 0;
            border-bottom:1px dashed #EDF1F8;
        }
        .s7-market-side__row:last-child {border-bottom:none;}
        .s7-market-side__row i {
            width:9px; height:9px; border-radius:3px; display:block;
        }
        .s7-market-side__row span {
            color:#334155; font-size:11.5px; white-space:nowrap;
            overflow:hidden; text-overflow:ellipsis;
        }
        .s7-market-side__row b {
            color:#0C233C; font-size:11.5px; font-variant-numeric:tabular-nums;
        }
        .s7-market-side__insight {
            background:#EEF5FF; border-left:3px solid #00B8F5;
            border-radius:6px; padding:7px 9px; margin:10px 0 7px;
            color:#243B53; font-size:10.5px; line-height:1.5;
        }
        .s7-market-side__note {
            color:#94A3B8; font-size:9.5px; line-height:1.45; margin:9px 0 0;
        }
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
        .solvency-print-cover--front {page-break-after:auto; break-after:auto;}
        .solvency-print-cover--back {page-break-before:always; break-before:page;}
        @media print {
            [data-testid="stHeader"], [data-testid="stSidebar"],
            [data-testid="stToolbar"], [data-testid="collapsedControl"],
            div[role="tablist"], h1,
            .platform-page-heading, .solvency-report-title,
            .solvency-no-print,
            [class*="st-key-capital_bubble_print_controls_"],
            [class*="st-key-risk_scatter_print_controls_"] {display:none!important;}
            [data-testid="stExpander"] {display:none!important;}
            [class*="st-key-s7_module_expander_wrap_"] [data-testid="stExpander"] {
                display:block!important;
            }
            /* The open state lives on the inner <details>, never on the expander
               div itself.  Matching :not([open]) on the div hid every report
               module in print and produced a cover-only PDF. */
            [class*="st-key-s7_module_expander_wrap_"] [data-testid="stExpander"]:has(> details:not([open])) {
                display:none!important;
            }
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
            /* A report module is routinely taller than one 16:9 sheet.  Asking
               the browser to keep the whole module on one page made it insert
               blank spill pages before the module; let the module flow while
               its chart rows and company cards keep their own intact rules. */
            [class*="st-key-s7_report_module_"] {
                break-inside:auto!important; page-break-inside:auto!important;
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
            /* The screen strip scrolls horizontally; printing must release that
               scroll box, otherwise an 80-company grid is clipped and the
               module shows only its summary cards. */
            [class*="st-key-annual_company_grid_scroll_"] {
                overflow:visible!important;
                max-height:none!important;
                height:auto!important;
                flex-wrap:wrap!important;
            }
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
                --solvency-print-page-width:1600px;
                --solvency-print-page-height:900px;
                --solvency-print-content-width:1600px;
                --solvency-print-content-height:900px;
            }
            /* Each exported module is wrapped in exactly one fixed sheet by the
               print control.  Charts are measured at sheet width and scaled to
               fit instead of being left to Chrome's page fragmentation. */
            .s7-print-sheet {
                position:relative!important;
                display:block!important;
                width:1600px!important;
                max-width:1600px!important;
                min-height:0!important;
                margin:0 auto!important;
                padding:0!important;
                background:#FFFFFF!important;
                overflow:visible!important;
                box-sizing:border-box!important;
                break-before:page!important;
                page-break-before:always!important;
                break-after:auto!important;
                page-break-after:auto!important;
                break-inside:auto!important;
            }
            .s7-print-sheet--portrait {
                width:794px!important; max-width:794px!important;
            }
            .s7-print-sheet__inner {
                transform-origin:top left!important;
                box-sizing:border-box!important;
            }
            .s7-print-sheet--flow .s7-print-sheet__inner {
                width:100%!important;
                transform:none!important;
                position:static!important;
            }
            /* A module that fits the sheet is stretched to the full page height
               and its top-level rows are spaced evenly, so a page holding only
               one or two charts does not leave a large empty band at the
               bottom. */
            /* 8px of headroom keeps Chrome from emitting an empty sheet after
               a full-height fill page, while still printing edge to edge. */
            .s7-print-sheet--fill,
            .s7-print-sheet--scaled {
                overflow:hidden!important;
                height:892px!important;
                max-height:892px!important;
            }
            /* Scaled sheets start a fraction below the paper edge so the module
               card's top border and title are not clipped by the page break. */
            .s7-print-sheet--scaled { padding-top:6px!important; }
            .s7-print-sheet--fill.s7-print-sheet--portrait,
            .s7-print-sheet--scaled.s7-print-sheet--portrait {
                height:1115px!important;
                max-height:1115px!important;
            }
            .s7-print-sheet--fill .s7-print-sheet__inner {
                position:static!important;
                width:100%!important;
                height:100%!important;
                display:flex!important;
                flex-direction:column!important;
            }
            .s7-print-sheet--fill [class*="st-key-s7_report_module_"] {
                height:100%!important;
                min-height:0!important;
                display:flex!important;
                flex-direction:column!important;
            }
            .s7-print-sheet--fill [data-testid="stExpander"] {
                height:100%!important;
                min-height:0!important;
                display:flex!important;
                flex-direction:column!important;
            }
            .s7-print-sheet--fill [data-testid="stExpander"] > details {
                height:100%!important;
                min-height:0!important;
                display:flex!important;
                flex-direction:column!important;
            }
            .s7-print-sheet--fill [data-testid="stExpanderDetails"] {
                flex:1 1 auto!important;
                min-height:0!important;
                display:flex!important;
                flex-direction:column!important;
            }
            .s7-print-sheet--fill [data-testid="stExpanderDetails"] > .stVerticalBlock {
                flex:1 1 auto!important;
                min-height:0!important;
                display:flex!important;
                flex-direction:column!important;
                justify-content:space-evenly!important;
            }
            .s7-print-sheet [class*="st-key-s7_report_module_"] {
                width:100%!important;
                min-width:0!important;
                max-width:100%!important;
                height:auto!important;
                max-height:none!important;
                overflow:visible!important;
                margin:0!important;
                padding:0!important;
                break-inside:auto!important;
                page-break-inside:auto!important;
            }
            .s7-print-sheet .solvency-report-page-break {display:none!important;}
            /* Streamlit defers off-screen element containers; printing must
               paint every chart in the module, including its last row. */
            .s7-print-sheet [data-testid="stElementContainer"],
            .s7-print-sheet [data-testid="stVerticalBlock"],
            .s7-print-sheet [data-testid="stLayoutWrapper"] {
                content-visibility:visible!important;
            }
            .s7-print-sheet [class*="st-key-s7_company_bars_page_"] {
                break-before:auto!important;
                page-break-before:auto!important;
            }
            .s7-print-sheet [data-testid="stHorizontalBlock"],
            .s7-print-sheet [data-testid="stVegaLiteChart"],
            .s7-print-sheet [data-testid="stElementContainer"]:has(.js-plotly-plot) {
                break-inside:avoid!important;
                page-break-inside:avoid!important;
            }
            /* A chart row is a block wrapper, not a horizontal block, in the
               Streamlit DOM.  Without this rule Chrome split the pair of chart
               headings from their plots when a tall module flowed to a second
               sheet.  Scope to the expander body so the outer module wrapper
               can still fragment. */
            .s7-print-sheet [data-testid="stExpanderDetails"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] {
                break-inside:avoid!important;
                page-break-inside:avoid!important;
                /* A row that lands at the top of a printed page otherwise has
                   its chart heading clipped by the zero-margin page edge. */
                margin-top:0!important;
                padding-top:26px!important;
            }
            .s7-print-sheet--flow [data-testid="stElementContainer"],
            .s7-print-sheet--flow [data-testid="stMarkdownContainer"] {
                overflow:visible!important;
            }
            /* Plotly/Vega canvases can paint their white plot background over
               the heading that precedes them when a module flows. */
            .s7-print-sheet--flow [data-testid="stMarkdownContainer"] {
                position:relative!important;
                z-index:50!important;
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
            /* A company strip can hold 80+ panels.  The old wide-screen rule
               forced every panel into one row, so each chart was ~18 px wide
               and the module looked like an empty summary.  Wrap instead, with
               a readable minimum width per panel. */
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_"],
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_"] [data-testid="stHorizontalBlock"] {
                width:100%!important; min-width:0!important; max-width:100%!important;
                margin-left:0!important; margin-right:0!important;
                grid-template-columns:repeat(auto-fit,minmax(210px,1fr))!important;
                grid-auto-flow:row!important;
                grid-auto-columns:auto!important;
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
                width:1600px!important; height:900px!important; max-width:1600px!important;
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
                width:1600px!important; max-width:1600px!important;
                height:900px!important; margin:0 auto!important; padding:0!important;
                overflow:hidden!important; line-height:0!important;
                break-inside:avoid-page!important; page-break-inside:avoid!important;
            }
            /* Both the cover itself and its Streamlit wrapper carried a
               break-after rule, which inserted two blank sheets after the
               front cover (and before the back cover).  Keep the break on the
               wrapper only. */
            html.solvency-print-mode-widescreen .solvency-print-cover--front,
            html.solvency-print-mode-widescreen .solvency-print-cover--back {
                break-before:auto!important; page-break-before:auto!important;
                break-after:auto!important; page-break-after:auto!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stVerticalBlock"]:has(.solvency-print-cover) {
                gap:0!important; margin:0!important; padding:0!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] {
                width:100%!important;
                min-width:0!important;
                max-width:100%!important;
                min-height:0!important; padding:0!important;
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
            /* Chromium fragments a rounded, bordered card across sheets by
               filling the whole fragment with the border colours, which then
               paints over charts.  Print modules as flat white sheets. */
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"],
            html.solvency-print-mode-widescreen [class*="st-key-s7_trend_group_"],
            html.solvency-print-mode-widescreen .s7-insight-card {
                border:0!important; border-left:0!important; border-radius:0!important;
                background:transparent!important; background-image:none!important;
                box-shadow:none!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] img {
                max-width:100%!important; height:auto!important;
            }
            html.solvency-print-mode-widescreen [class*="st-key-s7_report_module_"] table {
                width:100%!important; max-width:100%!important;
                table-layout:fixed!important; box-sizing:border-box!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover--front) {
                break-after:auto!important; page-break-after:auto!important;
                overflow:visible!important;
            }
            html.solvency-print-mode-widescreen .solvency-print-cover--front {
                aspect-ratio:16/9!important; height:900px!important;
                width:1600px!important; max-width:1600px!important;
            }
            /* The report is authored for a 1600x900 screen.  Print it at that
               width and zoom the whole block to 0.8, which maps exactly onto the
               1280x720 (338.67x190.5mm) sheet.  Keeping the authored width means
               the two chart columns keep their aspect ratio; forcing 1280px made
               every module spill its header onto a separate sheet. */
            html.solvency-print-mode-widescreen [data-testid="stApp"],
            html.solvency-print-mode-widescreen [data-testid="stAppViewContainer"],
            html.solvency-print-mode-widescreen [data-testid="stMain"],
            html.solvency-print-mode-widescreen [data-testid="stMainBlockContainer"] {
                width:1600px!important; max-width:1600px!important;
                margin:0!important; padding:0!important;
            }
            /* Streamlit keeps a 300px sidebar column in the app grid even while
               the sidebar is hidden for print, which left every sheet 310px to
               the right and clipped the right edge.  Print as plain block flow
               so the 1600px sheet starts at x=0. */
            html.solvency-print-mode-widescreen [data-testid="stApp"],
            html.solvency-print-mode-widescreen [data-testid="stAppViewContainer"] {
                display:block!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stMain"] {
                padding-left:0!important; padding-right:0!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stMainBlockContainer"] {
                padding-left:0!important; padding-right:0!important;
                margin-left:0!important; margin-right:0!important;
            }
            /* Chrome fragments flex items poorly in paged media: a column-flex
               child taller than a sheet can be pushed past the next sheet and
               leave an empty page behind.  Plain block flow prints predictably. */
            html.solvency-print-mode-widescreen [data-testid="stVerticalBlock"],
            html.solvency-print-mode-widescreen [data-testid="stLayoutWrapper"] {
                display:block!important;
            }
            /* The generic block-flow rule above must not flatten a filled
               sheet: that sheet relies on a flex column to spread its rows
               across the whole page.  These selectors are deliberately more
               specific and come later in the stylesheet. */
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [class*="st-key-s7_report_module_"],
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [data-testid="stExpander"],
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [data-testid="stExpander"] > details,
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [data-testid="stExpanderDetails"],
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [data-testid="stExpanderDetails"] > .stVerticalBlock {
                display:flex!important;
                flex-direction:column!important;
            }
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [class*="st-key-s7_report_module_"] {
                height:100%!important;
                min-height:0!important;
            }
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [class*="st-key-s7_report_module_"] > [data-testid="stLayoutWrapper"],
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [class*="st-key-s7_report_module_"] > [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"],
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [class*="st-key-s7_report_module_"] > [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"],
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [class*="st-key-s7_report_module_"] > [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] > [data-testid="stExpander"],
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [class*="st-key-s7_report_module_"] > [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] > [data-testid="stExpander"] > details {
                flex:1 1 auto!important;
                height:100%!important;
                min-height:0!important;
            }
            /* Chromium does not lay out the slotted children of a native
               <details> as ordinary flex items, so flex-grow on the body is
               ignored.  Give the body an explicit height below its summary and
               let the inner block spread the rows across that height. */
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [data-testid="stExpander"] > details {
                position:relative!important;
            }
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [data-testid="stExpanderDetails"] {
                position:absolute!important;
                top:42px!important;
                right:0!important;
                bottom:0!important;
                left:0!important;
                height:auto!important;
                max-height:none!important;
                min-height:0!important;
            }
            html.solvency-print-mode-widescreen .s7-print-sheet--fill [data-testid="stExpanderDetails"] > .stVerticalBlock {
                display:flex!important;
                flex-direction:column!important;
                justify-content:space-between!important;
                height:100%!important;
                min-height:0!important;
                padding:0 0 12px!important;
            }
            /* Keep a whole chart row on one sheet; otherwise Chrome slices a
               chart in half and the next row prints on top of it. */
            html.solvency-print-mode-widescreen [data-testid="stHorizontalBlock"],
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.js-plotly-plot) {
                break-inside:avoid!important; page-break-inside:avoid!important;
            }
            html.solvency-print-mode-widescreen [data-testid="stElementContainer"]:has(.solvency-print-cover--back) {
                break-before:page!important; page-break-before:always!important;
            }
            /* A company strip is thousands of pixels tall once its cards wrap.
               The generic horizontal-row rule above would push the whole strip
               to the next page and leave the module header alone on a blank
               sheet.  Let the strip start right below the header and fragment
               between card rows. */
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_scroll_"],
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_scroll_"] [data-testid="stHorizontalBlock"],
            html.solvency-print-mode-widescreen [class*="st-key-annual_company_grid_scroll_"] [data-testid="stColumn"] {
                break-inside:auto!important; page-break-inside:auto!important;
            }
            /* The layout wrappers around the strip also carry a generic
               break-inside:avoid rule.  Clear it on every wrapper that contains
               the strip so the grid can begin directly under the module header
               instead of starting on a fresh page. */
            .s7-print-sheet [data-testid="stLayoutWrapper"]:has([class*="st-key-annual_company_grid_scroll_"]),
            .s7-print-sheet [data-testid="stElementContainer"]:has([class*="st-key-annual_company_grid_scroll_"]) {
                break-inside:auto!important; page-break-inside:auto!important;
            }
            /* The section caption sits in its own markdown block immediately
               above the two chart columns.  In print the columns start a few
               pixels before that block ends, so the left panel title overlapped
               its caption.  Add the separation inside the title box. */
            .s7-premium-panel-title {
                padding-top:36px!important;
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
