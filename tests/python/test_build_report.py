"""The entry point, end to end with fixtures: it must stop clearly on missing data and build a report offline."""
import json
import sys

import pytest

import build_report
import stock_core
from conftest import make_snap, synthetic_history


def _run(monkeypatch, capsys, args, fake_snap):
    monkeypatch.setattr(stock_core, "build_snapshot", lambda ticker, user_peers=None: fake_snap)
    monkeypatch.setattr(sys, "argv", ["build_report.py", *args])
    build_report.main_result = build_report.main()
    return build_report.main_result


def test_too_little_history_stops_with_a_clear_reason(monkeypatch, capsys, tmp_path):
    short = make_snap(full=synthetic_history(bars=30))
    out = _run(monkeypatch, capsys, ["TEST", "--out", str(tmp_path), "--no-halal", "--no-ai"], short)
    assert out["ok"] is False
    assert out["error"] == f"not enough price history (30 daily bars, need {build_report.MIN_BARS})"
    assert "docx" not in out


def test_no_history_stops_with_a_clear_reason(monkeypatch, capsys, tmp_path):
    empty = make_snap()
    empty["technicals"] = {}
    out = _run(monkeypatch, capsys, ["TEST", "--out", str(tmp_path), "--no-halal", "--no-ai"], empty)
    assert out == {"ok": False, "ticker": "TEST", "steps": {"note": out["steps"]["note"], "snapshot": {"errors": [], "rows": 0}},
                   "error": "no price data", "seconds": out["seconds"]}


def test_full_report_builds_offline(monkeypatch, capsys, tmp_path):
    out = _run(monkeypatch, capsys, ["TEST", "--out", str(tmp_path), "--no-halal", "--no-ai",
                                     "--note", "holding 10 @ 22.00"], make_snap())
    assert out["ok"] is True, out
    assert out["plan"]["position"] == "holding"
    assert out["plan"]["entry"] == 22.0
    assert out["docx"].endswith(".docx")
    assert out["html"].endswith(".html")
    assert out["caption"].startswith("TEST ")


def test_result_is_json_serialisable(monkeypatch, capsys, tmp_path):
    out = _run(monkeypatch, capsys, ["TEST", "--out", str(tmp_path), "--no-halal", "--no-ai"], make_snap())
    json.dumps(out, default=str)


def test_bad_ticker_is_refused(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["build_report.py", "AR-LO", "--out", str(tmp_path)])
    out = build_report.main()
    assert out == {"ok": False, "ticker": None, "steps": {}, "error": "bad ticker", "seconds": out["seconds"]}
