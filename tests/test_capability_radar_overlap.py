# -*- coding: utf-8 -*-
"""能力五边形叠加：追踪 + 对比公司双层填充，且不影响单公司渲染。"""

from __future__ import annotations

import pandas as pd

from services.solvency_step7_charts.radar import build_capability_radar_chart

AXES = ["资本", "偿付", "盈利", "承保", "业务"]


def _score_frame(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"公司": company, **{f"{axis}_得分": score for axis in AXES}}
            for company, score in rows
        ]
    )


def _mark_types(chart) -> list[str]:
    payload = chart.to_dict(validate=True)
    layers = payload.get("layer") or []
    return [str(layer.get("mark", {}).get("type", "")) for layer in layers]


def test_overlay_adds_area_layer() -> None:
    frame = _score_frame([("甲公司", 80.0), ("乙公司", 55.0)])
    colors = {"甲公司": "#00338D", "乙公司": "#ED2124"}
    baseline = build_capability_radar_chart(
        frame,
        AXES,
        colors,
        fill=False,
        panel_width=320,
        panel_height=320,
    )
    overlay = build_capability_radar_chart(
        frame,
        AXES,
        colors,
        fill=True,
        fill_opacity=0.16,
        panel_width=320,
        panel_height=320,
    )
    base_marks = _mark_types(baseline)
    overlay_marks = _mark_types(overlay)
    assert "area" not in base_marks
    assert "area" in overlay_marks
    assert len(overlay_marks) == len(base_marks) + 1
    # 描边仍然存在，填充层不替代折线层。
    assert "line" in overlay_marks


def test_overlay_contains_both_companies() -> None:
    frame = _score_frame([("甲公司", 80.0), ("乙公司", 55.0)])
    chart = build_capability_radar_chart(
        frame,
        AXES,
        {"甲公司": "#00338D", "乙公司": "#ED2124"},
        fill=True,
        panel_width=320,
        panel_height=320,
    )
    payload = chart.to_dict(validate=True)
    text = str(payload)
    assert "甲公司" in text
    assert "乙公司" in text


def test_single_company_overlay_still_valid() -> None:
    frame = _score_frame([("甲公司", 80.0)])
    chart = build_capability_radar_chart(
        frame,
        AXES,
        {"甲公司": "#00338D"},
        fill=True,
        panel_width=280,
        panel_height=280,
    )
    assert "area" in _mark_types(chart)


def test_empty_frame_raises() -> None:
    try:
        build_capability_radar_chart(
            pd.DataFrame(columns=["公司", *[f"{axis}_得分" for axis in AXES]]),
            AXES,
            {},
        )
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("空数据应当报错")


if __name__ == "__main__":
    test_overlay_adds_area_layer()
    test_overlay_contains_both_companies()
    test_single_company_overlay_still_valid()
    test_empty_frame_raises()
    print("test_capability_radar_overlap: OK")
