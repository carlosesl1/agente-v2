from __future__ import annotations

from pathlib import Path


def test_execution_canvas_assets_have_joint_inspector_and_no_write_controls() -> None:
    root = Path(__file__).resolve().parents[1] / "v2_ops"
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "ops.js").read_text(encoding="utf-8")
    css = (root / "static" / "ops.css").read_text(encoding="utf-8")

    assert 'id="execution-list"' in html
    assert 'id="execution-canvas"' in html
    assert 'id="input-panel"' in html
    assert 'id="output-panel"' in html
    assert 'id="fit-canvas"' in html
    assert 'id="zoom-in"' in html and 'id="zoom-out"' in html
    assert "EventSource" in js
    assert "textContent" in js
    assert "innerHTML" not in js
    assert "fetch(" in js
    assert "method:" not in js
    for forbidden in ("reserve", "charge", "payment link", "retry", "replay", "send message"):
        assert forbidden not in (html + js).casefold()
    assert "@media" in css
    assert "input-panel" in css and "output-panel" in css
