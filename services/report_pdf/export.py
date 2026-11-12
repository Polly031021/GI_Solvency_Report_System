"""Streamlit-facing controller for the PDF export flow."""

from __future__ import annotations

import os
import sys
import time
import re
from datetime import date

import streamlit as st

from .capture import captured_modules, clear_result, result, store_result, to_content_items
from .html_builder import build_probe_html, build_report_html
from .models import ReportMeta
from .renderer import measure_blocks, render_pdf

_META = "_s7_pdf_meta"


def _label(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


def _report_meta(language: str) -> ReportMeta:
    stored = st.session_state.get(_META) or {}
    today = date.today()
    return ReportMeta(
        title=str(
            stored.get("title")
            or _label(
                language,
                "保险公司偿付能力公司对标报告",
                "Insurance Company Solvency Benchmarking Report",
            )
        ),
        subtitle=str(stored.get("subtitle") or ""),
        date_text=str(
            stored.get("date_text")
            or (
                f"{today.year}-{today.month:02d}"
                if language == "en"
                else f"{today.year}年{today.month}月"
            )
        ),
        language=str(stored.get("language") or language),
        front_cover_image=str(stored.get("front_cover_image") or ""),
        back_cover_image=str(stored.get("back_cover_image") or ""),
    )


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(text or "")).strip("_ ")
    return cleaned[:80] or "report"


def _export_pdf(language: str) -> bytes | None:
    """Convert the captured modules and render them; returns None on failure.

    The export has several distinct stages (chart conversion, HTML composition,
    Chromium rendering) and the first one can take tens of seconds for a full
    report.  A progress bar keeps the user informed instead of leaving the
    button apparently unresponsive.
    """
    t0 = time.perf_counter()
    progress = None
    try:
        raw_modules = captured_modules()
        if not raw_modules:
            st.warning(
                _label(
                    language,
                    "没有可导出的模块，请刷新页面后重试。",
                    "No modules to export. Refresh the page and try again.",
                )
            )
            return None

        progress = st.progress(
            0.0, text=_label(language, "正在准备导出…", "Preparing export…")
        )

        def _on_chart(done: int, total: int, _msg: str) -> None:
            fraction = 0.05 + 0.55 * (done / max(total, 1))
            progress.progress(
                min(fraction, 0.60),
                text=_label(
                    language,
                    f"正在转换图表 {done}/{total}…",
                    f"Converting charts {done}/{total}…",
                ),
            )

        modules = to_content_items(raw_modules, progress_cb=_on_chart)
        t1 = time.perf_counter()
        if not modules:
            progress.empty()
            st.warning(
                _label(
                    language,
                    "没有可导出的模块，请刷新页面后重试。",
                    "No modules to export. Refresh the page and try again.",
                )
            )
            return None

        meta = _report_meta(language)
        progress.progress(
            0.62, text=_label(language, "正在测量排版…", "Measuring layout…")
        )
        probe_html = build_probe_html(meta, modules)
        measured = measure_blocks(probe_html)

        progress.progress(
            0.72, text=_label(language, "正在排版报告…", "Composing report…")
        )
        html_text = build_report_html(meta, modules, measured=measured)
        t2 = time.perf_counter()

        progress.progress(
            0.80,
            text=_label(
                language,
                "正在渲染 PDF（约 5–10 秒）…",
                "Rendering PDF (about 5–10 seconds)…",
            ),
        )
        pdf = render_pdf(html_text)
        progress.progress(
            1.0, text=_label(language, "导出完成", "Export complete")
        )
        progress.empty()
    except Exception as exc:  # pragma: no cover - defensive
        if progress is not None:
            progress.empty()
        st.error(
            _label(
                language,
                f"PDF 导出失败：{exc}",
                f"PDF export failed: {exc}",
            )
        )
        return None

    if os.environ.get("S7_EXPORT_DEBUG"):
        for m in modules:
            print(
                f"[s7-module] {m.title[:20]} {[(i.kind, i.max_height_px, len(str(i.payload))) for i in m.items]}",
                file=sys.stderr,
                flush=True,
            )
        print(
            f"[s7-export] modules={len(modules)} items={sum(len(m.items) for m in modules)} "
            f"convert={t1-t0:.1f}s html={t2-t1:.1f}s pdf={time.perf_counter()-t2:.1f}s",
            file=sys.stderr,
            flush=True,
        )
    return pdf


def _render_panel_body(*, key: str, language: str) -> None:
    label = _label(language, "导出 PDF 报告", "Export PDF report")
    if st.button(
        label,
        key=f"{key}_export_pdf",
        icon=":material/picture_as_pdf:",
        use_container_width=True,
    ):
        clear_result()
        pdf_bytes = _export_pdf(language)
        if pdf_bytes:
            meta = _report_meta(language)
            filename = f"{_safe_filename(meta.title)}_{date.today():%Y%m%d}.pdf"
            store_result(key, pdf_bytes, filename)

    ready = result()
    if ready and ready.get("key") == key:
        st.download_button(
            _label(language, "下载 PDF", "Download PDF"),
            data=ready["bytes"],
            file_name=ready["filename"],
            mime="application/pdf",
            icon=":material/download:",
            key=f"{key}_download_pdf",
            use_container_width=True,
        )


def render_export_control(*, key: str, language: str, enabled: bool = True) -> None:
    """Render the export panel.

    The panel runs as a fragment so clicking export re-executes only the panel
    instead of re-rendering the whole report.  It reads the modules captured by
    the last full run from session state.
    """
    if not enabled:
        return
    fragment = getattr(st, "fragment", None)
    if fragment is None:
        _render_panel_body(key=key, language=language)
        return
    fragment(_render_panel_body)(key=key, language=language)
