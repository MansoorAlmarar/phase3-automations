"""The Word report: research wording only, and no blank cells where data is missing."""
import re

import pytest
from docx import Document

import docx_report as dr
import stock_core as sc
from conftest import make_snap

# Wording that would read as advice. "holding" and "entry" are descriptive and allowed.
ADVICE = re.compile(r"\b(buy\w*|sell\w*|should|must|recommend\w*|advis\w*|enter|entering|target price)\b", re.I)
HALAL_UNCHECKED = {"status": "unchecked", "notes": "Halal not checked: test run."}


def _plan(snap, entry=None, shares=None):
    pos = {"position": "holding" if entry else "watching", "entry": entry, "lots": [[shares, entry]] if entry else [],
           "shares": shares, "plan_note": None}
    return sc.build_plan(snap, pos)


def _text(path):
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            parts.extend(c.text for c in row.cells)
    return "\n".join(parts)


def _tables(path):
    return Document(path).tables


def _build(tmp_path, snap, plan, notes=None, halal=HALAL_UNCHECKED, charts=None, name="r.docx"):
    path = str(tmp_path / name)
    dr.build_docx(snap, plan, halal, notes, charts or {}, path, note_text="", note_warning=None)
    return path


@pytest.mark.parametrize("entry, shares", [(None, None), (22.0, 10.0)])
def test_report_wording_is_research_only(tmp_path, snap, entry, shares):
    path = _build(tmp_path, snap, _plan(snap, entry, shares))
    hits = ADVICE.findall(_text(path))
    assert hits == []


def test_no_blank_cells_in_the_fundamentals_grid(tmp_path, snap):
    path = _build(tmp_path, snap, _plan(snap))
    grid = next(t for t in _tables(path) if [c.text for c in t.rows[0].cells] == ["Metric", "Value", "Note"])
    for row in grid.rows[1:]:
        values = [c.text.strip() for c in row.cells]
        assert all(values), f"blank cell in row {values}"


def test_missing_company_details_are_named_not_left_blank(tmp_path, snap):
    snap["profile"] = {}
    path = _build(tmp_path, snap, _plan(snap))
    text = _text(path)
    assert "company name not available" in text
    assert "sector not available" in text


def test_missing_halal_and_ai_are_stated(tmp_path, snap):
    text = _text(_build(tmp_path, snap, _plan(snap)))
    assert "NOT CHECKED" in text
    assert "AI notes: none in this report." in text


def test_data_gaps_are_listed_in_the_footer(tmp_path, snap):
    snap["errors"] = ["Finnhub metrics: HTTP 500"]
    assert "Data gaps: Finnhub metrics: HTTP 500" in _text(_build(tmp_path, snap, _plan(snap)))


def test_broken_floor_is_flagged(tmp_path, snap):
    snap["technicals"]["range"] = {"floor": [19.7, 20.1], "broken": {
        "first_close_below": "2026-10-20", "last_close_below": "2026-10-28", "closes_below": 7, "lowest_close": 16.7},
        "touches": 5, "bounces": [], "verdict": "floor broken", "good_bounces": 0, "trend_1y_pct": 3}
    text = _text(_build(tmp_path, snap, _plan(snap)))
    assert "Floor broken" in text
    assert "Floor broken: 7 closes under it" in text


def test_eps_note_is_not_blank_when_ai_is_off(tmp_path, snap):
    path = _build(tmp_path, snap, _plan(snap))
    for t in _tables(path):
        for row in t.rows:
            if row.cells[0].text.startswith("EPS (adjusted)"):
                assert row.cells[2].text.strip() == "n/a"
