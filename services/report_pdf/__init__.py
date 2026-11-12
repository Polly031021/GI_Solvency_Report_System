"""PDF export pipeline for the Step7 solvency reports.

The public entry point is :func:`services.report_pdf.build_and_render_pdf`.
It turns collected module snapshots into a standalone HTML document and
renders that document to a vector PDF with Playwright's Chromium build.
"""

from .models import ContentItem, ModuleSnapshot, ReportMeta
from .renderer import render_pdf

__all__ = [
    "ContentItem",
    "ModuleSnapshot",
    "ReportMeta",
    "render_pdf",
]
