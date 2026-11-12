"""Render a standalone HTML document to a vector PDF with Playwright."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_FALLBACK_TMP: Path | None = None


def _ensure_writable_temp() -> None:
    """Point Playwright's driver at the project temp folder.

    Chromium's driver creates ``playwright-artifacts-*`` folders below ``TEMP``;
    the Windows default temp directory can reject that with ``EPERM``.  A
    project-local ``tmp/playwright`` folder is always writable for this app.
    """
    global _FALLBACK_TMP
    if _FALLBACK_TMP is not None:
        return
    fallback = Path(__file__).resolve().parents[2] / "tmp" / "playwright"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - keep the system default
        _FALLBACK_TMP = Path()
        return
    os.environ["TEMP"] = str(fallback)
    os.environ["TMP"] = str(fallback)
    _FALLBACK_TMP = fallback


def _stamp_page_numbers(pdf_bytes: bytes) -> bytes:
    """Add a subtle ``n / total`` footer to every page."""
    try:
        import fitz  # PyMuPDF
    except Exception:  # pragma: no cover - optional runtime dependency
        return pdf_bytes
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        total = document.page_count
        for index, page in enumerate(document):
            rect = page.rect
            page.insert_text(
                (rect.width - 62, rect.height - 14),
                f"{index + 1} / {total}",
                fontsize=9,
                fontname="helv",
                color=(0.6, 0.6, 0.6),
            )
        data = document.tobytes()
        document.close()
        return data
    except Exception:  # pragma: no cover - never fail the export on decoration
        logger.warning("Could not stamp page numbers", exc_info=True)
        return pdf_bytes


def measure_blocks(html: str, *, width: int = 1600, height: int = 900) -> dict[int, list[float]]:
    """Measure real block heights of a probe document in Chromium.

    Pagination cannot rely on heuristics alone: insight cards, HTML panels and
    faceted charts differ widely from a text-line estimate, and a bad estimate
    forces the renderer's shrink-to-fit fallback, which is what made whole
    modules unreadably small.  Measuring the blocks first makes every page fit
    exactly.
    """
    _ensure_writable_temp()
    from playwright.sync_api import sync_playwright

    measured: dict[int, list[float]] = {}
    with _LOCK:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.set_content(html, wait_until="load")
                page.wait_for_timeout(250)
                data = page.evaluate(
                    """() => {
                      const out = {};
                      document.querySelectorAll('.s7-block[data-m]').forEach((el) => {
                        const m = el.dataset.m;
                        (out[m] = out[m] || []).push(Math.ceil(el.getBoundingClientRect().height));
                      });
                      return out;
                    }"""
                )
            finally:
                browser.close()
    for key, values in (data or {}).items():
        try:
            measured[int(key)] = [float(value) for value in values]
        except (TypeError, ValueError):
            continue
    return measured


def render_pdf(html: str, *, width: int = 1600, height: int = 900) -> bytes:
    """Render ``html`` to PDF bytes using Chromium's print pipeline.

    The page size is fixed to the 16:9 canvas used by the HTML builder.  Text
    and inline SVG stay vector in the output.
    """
    _ensure_writable_temp()
    from playwright.sync_api import sync_playwright

    with _LOCK:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.set_content(html, wait_until="load")
                # Give embedded SVG fonts a moment to settle before printing.
                page.wait_for_timeout(300)
                # Pagination is decided from measured block heights before this
                # render.  Do not apply a second browser-scale pass: it causes
                # titles, SVGs and table headers to drift together and hides
                # layout defects instead of creating a new page.
                page.evaluate(
                    """() => {
                      window.__S7_SCALES = [];
                      return window.__S7_SCALES;
                    }"""
                )
                if __import__("os").environ.get("S7_EXPORT_DEBUG"):
                    import sys as _sys
                    print("[s7-scale]", page.evaluate("() => window.__S7_SCALES || []"), file=_sys.stderr, flush=True)
                pdf_bytes = page.pdf(
                    width=f"{width}px",
                    height=f"{height}px",
                    print_background=True,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                browser.close()
    return _stamp_page_numbers(pdf_bytes)
