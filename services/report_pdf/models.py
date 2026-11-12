"""Data structures handed from the Streamlit render pass to the PDF builder."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContentItem:
    """One block inside a module page.

    ``kind`` is one of ``svg``, ``html``, ``table`` or ``image``.  ``payload``
    holds the ready-to-embed markup: an SVG document, an HTML fragment, an
    HTML table or a data URI image.

    ``panels`` is the number of sub-charts inside the SVG (one for a plain
    chart, several for faceted company grids) and ``aspect`` is the intrinsic
    width/height ratio.  Both drive the PDF layout: multi-panel charts get a
    full page width so their labels stay legible.
    """

    kind: str
    payload: object
    caption: str = ""
    width: str = "100%"
    max_height_px: int = 520
    column: tuple[int, int, int, float] | None = None
    panels: int = 1
    aspect: float = 0.0
    layout_group: int | None = None
    layout_index: int | None = None
    layout_total: int = 0
    layout_weight: float = 1.0


@dataclass
class ModuleSnapshot:
    """A single report module: one PDF page (or a small run of pages)."""

    title: str
    section: str = ""
    items: list[ContentItem] = field(default_factory=list)
    note: str = ""


@dataclass
class ReportMeta:
    """Cover page metadata and document-wide labels."""

    title: str
    subtitle: str = ""
    date_text: str = ""
    language: str = "zh"
    footer_left: str = ""
    front_cover_image: str = ""
    back_cover_image: str = ""
