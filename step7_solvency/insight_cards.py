"""STEP7 行业概览模块的统一解读卡片。"""

from __future__ import annotations

import html

import streamlit as st


def render_insight_card(
    *,
    title: str,
    headline: str,
    value: str = "",
    delta: str = "",
    delta_tone: str = "neutral",
    bullets: tuple[str, ...] | list[str] = (),
    footnote: str = "",
    accent: str = "#00338D",
    eyebrow: str = "模块解读",
) -> None:
    """Render a compact, print-safe summary card for one chart module.

    The card is deliberately HTML rather than st.metric so the headline number,
    delta and interpretation bullets stay on one visual surface in both the
    screen report and the browser print output.
    """
    tone = delta_tone if delta_tone in {"up", "down", "neutral"} else "neutral"
    bullet_html = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in bullets
        if str(item).strip()
    )
    value_html = (
        f"<strong class='s7-insight-card__value'>{html.escape(value)}</strong>"
        if value
        else ""
    )
    delta_html = (
        f"<span class='s7-insight-card__delta s7-insight-card__delta--{tone}'>"
        f"{html.escape(delta)}</span>"
        if delta
        else ""
    )
    footnote_html = (
        f"<div class='s7-insight-card__foot'>{html.escape(footnote)}</div>"
        if footnote
        else ""
    )
    st.markdown(
        f"""
        <div class="s7-insight-card" style="--s7-insight-accent:{html.escape(accent)};">
          <div class="s7-insight-card__top">
            <span class="s7-insight-card__eyebrow">{html.escape(eyebrow)}</span>
            <span class="s7-insight-card__title">{html.escape(title)}</span>
          </div>
          <div class="s7-insight-card__headline">
            {html.escape(headline)}{value_html}{delta_html}
          </div>
          {f"<ul class='s7-insight-card__bullets'>{bullet_html}</ul>" if bullet_html else ""}
          {footnote_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
