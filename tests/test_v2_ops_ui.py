from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "v2_ops" / "static"


def assets() -> tuple[str, str, str]:
    return tuple(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("index.html", "ops.js", "ops.css")
    )


def test_dashboard_shell_and_canvas_detail_exist() -> None:
    html, _, _ = assets()
    for token in (
        'id="overview-view"',
        'id="execution-view"',
        'id="range-select"',
        'id="kpi-grid"',
        'id="execution-series"',
        'id="status-distribution"',
        'id="trace-distribution"',
        'id="milestones-chart"',
        'id="top-node-types"',
        'id="execution-table-body"',
        'id="execution-mobile-list"',
        'id="empty-state"',
        'id="execution-canvas"',
        'id="input-panel"',
        'id="output-panel"',
        'id="back-to-overview"',
    ):
        assert token in html
    assert "SOMENTE LEITURA" in html
    assert "Dados demonstrativos" not in html


def test_html_has_no_commercial_claims_or_write_controls() -> None:
    html, _, _ = assets()
    lowered = html.casefold()
    for forbidden in (
        "receita",
        "conversão",
        "novos leads",
        "qualificados",
        "interesse",
        "reservas confirmadas",
        "pagamento pago",
        "retry",
        "replay",
    ):
        assert forbidden not in lowered


def test_execution_canvas_assets_have_joint_inspector_and_no_write_controls() -> None:
    html, js, css = assets()

    for token in (
        'id="execution-canvas"',
        'id="edges"',
        'id="nodes"',
        'id="node-title"',
        'id="input-panel"',
        'id="load-full-input"',
        'id="input-summary"',
        'id="input-full"',
        'id="output-panel"',
        'id="load-full-output"',
        'id="output-summary"',
        'id="output-full"',
        'id="metadata"',
        'id="node-error"',
        'id="fit-canvas"',
        'id="zoom-in"',
        'id="zoom-out"',
    ):
        assert token in html
    assert 'id="input-panel" hidden' not in html
    assert 'id="output-panel" hidden' not in html
    assert "EventSource" in js
    assert "textContent" in js
    assert "innerHTML" not in js
    assert "fetch(" in js
    assert "method:" not in js
    for forbidden in (
        "reserve",
        "charge",
        "payment link",
        "retry",
        "replay",
        "send message",
    ):
        assert forbidden not in (html + js).casefold()
    assert "@media" in css
    assert "input-panel" in css and "output-panel" in css
