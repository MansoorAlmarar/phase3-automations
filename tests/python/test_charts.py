"""Chart images and the interactive file, from fixtures."""
import json
import os

import charts
from conftest import make_snap


def test_all_charts_are_made_from_fixtures(tmp_path, snap):
    out, errors = charts.make_charts(snap, None, str(tmp_path))
    assert errors == []
    for key in ("sma_short", "sma_long", "rsi"):
        assert out[key] and os.path.getsize(out[key]) > 1000
    assert os.path.exists(out["interactive"])


def test_interactive_file_carries_the_levels(tmp_path, snap):
    plan = {"position": "holding", "entry": 22.0, "stop": 19.7, "target": 23.1}
    out, _ = charts.make_charts(snap, plan, str(tmp_path))
    html = open(out["interactive"], encoding="utf-8").read()
    data = json.loads(html.split("const D = ", 1)[1].split(";\n", 1)[0])
    assert data["entry"] == 22.0
    assert data["stop"] == 19.7
    assert data["ticker"] == "TEST"


def test_values_cannot_close_the_script_tag(tmp_path, snap):
    plan = {"position": "watching"}
    out, _ = charts.make_charts(snap, plan, str(tmp_path))
    html = open(out["interactive"], encoding="utf-8").read()
    assert "</script><script>" not in html.replace("</script>\n<script>", "")
    data = json.loads(html.split("const D = ", 1)[1].split(";\n", 1)[0].replace("\\u003c", "<"))
    assert data["ticker"] == "TEST"


def test_chart_error_is_reported_not_raised(tmp_path, snap):
    snap["technicals"]["history"] = {"dates": [], "close": [], "open": [], "high": [], "low": []}
    out, errors = charts.make_charts(snap, None, str(tmp_path))
    assert out["sma_short"] is None
    assert any(e.startswith("chart sma_short") for e in errors)
