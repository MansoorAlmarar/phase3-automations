"""AI notes: the checks in code (wording, numbers, ids, shapes) and what is sent to the model."""
import json

import pytest

import ai_notes as an
import stock_core as sc
from conftest import make_snap


@pytest.fixture
def plan(snap):
    return sc.build_plan(snap, {"position": "watching", "entry": None, "lots": [], "shares": None})


@pytest.mark.parametrize("text", [
    "Consider buying on a dip", "Hold for now", "Price target 30", "Should sell", "Trim the position",
    "Take profit at the goal", "We recommend this", "Exit if it breaks", "A target price of 30",
])
def test_advice_wording_is_dropped(text):
    assert an.clean_text(text, set()) == ("", "advice wording")


def test_numbers_must_come_from_the_data():
    kept, why = an.clean_text("Floor at 20.5", {20.5})
    assert (kept, why) == ("Floor at 20.5", None)
    assert an.clean_text("Floor at 99.9", {20.5})[1].startswith("number not in data")


def test_em_dash_becomes_comma_and_non_text_is_coerced():
    assert an.clean_text("Fast growth — steady", set())[0] == "Fast growth , steady"
    assert an.clean_text(20.5, {20.5}) == ("20.5", None)
    assert an.clean_text(None, set()) == ("", None)


def test_index_rejects_negative_and_out_of_range_ids():
    assert an._index({"id": 1}, 3) == 1
    assert an._index({"id": "2"}, 3) == 2
    assert an._index({"id": -1}, 3) is None
    assert an._index({"id": 3}, 3) is None
    assert an._index("not a dict", 3) is None


def test_allowed_numbers_cover_the_data_and_web_text():
    allowed = an.allowed_numbers({"price": 25.5}, "web says 12%")
    assert an.number_ok(25.5, allowed)
    assert an.number_ok(12.0, allowed)
    assert not an.number_ok(99.0, allowed)


def _raw(**over):
    raw = {
        "headline": "Test Corp at 25.0, RSI 50.0.",
        "fundamentals_notes": {"revenue_growth": "fast growth", "pe": 18.4},
        "fundamentals_verdict": "Growth is solid.",
        "quality_flag": "",
        "peers_note": "",
        "news": [{"id": -1, "take": "positive", "why": "wrong item"}, {"id": 0, "take": "positive", "why": "contract news"}],
        "context": [{"id": "W9", "point": "unknown web id"}, "not a dict"],
        "risks": [{"level": "high", "title": "Thin volume", "detail": "volume is 999 units"}, "bad"],
        "change_conditions": ["A close below 19.7"],
    }
    raw.update(over)
    return raw


def test_bad_ids_and_shapes_drop_items_without_losing_the_notes(monkeypatch, snap, plan):
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(an, "call_model", lambda *a, **k: (_raw(), "test/model", None))
    notes = an.make_notes(snap, plan, {"status": "unchecked"}, use_web=False, log=lambda m: None)
    assert "_error" not in notes
    assert [n["why"] for n in notes["news"]] == ["contract news"]  # id -1 never maps to the last item
    assert notes["context"] == []
    assert notes["fundamentals_notes"]["pe"] == "18.4"
    assert any("volume is 999" in d or "risk.detail" in d for d in notes["_dropped"])


def test_non_object_answer_is_an_error_not_a_crash(monkeypatch, snap, plan):
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(an, "call_model", lambda *a, **k: (["not", "an", "object"], "test/model", None))
    assert an.make_notes(snap, plan, {}, use_web=False, log=lambda m: None) == {"_error": "AI answer was not a JSON object"}


def test_missing_key_says_why(monkeypatch, snap, plan):
    assert an.make_notes(snap, plan, {}, use_web=False, log=lambda m: None) == {"_error": "OPENROUTER_API_KEY missing"}


def test_model_failure_reason_is_passed_on(monkeypatch, snap, plan):
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(an, "call_model", lambda *a, **k: (None, None, "all models HTTP 503"))
    assert an.make_notes(snap, plan, {}, use_web=False, log=lambda m: None) == {"_error": "all models HTTP 503"}


def test_model_input_has_no_share_counts_or_dollar_cost(snap):
    pos = {"position": "holding", "entry": 13.37, "lots": [[863.5, 13.37]], "shares": 863.5, "plan_note": None}
    plan = sc.build_plan(snap, pos)
    data, _web = an.build_input(snap, plan, {"status": "compliant"}, [])
    sent = json.dumps(data)
    assert "863.5" not in sent
    assert "lots" not in sent
    assert '"cost"' not in sent and '"pnl_abs"' not in sent and '"value now"' not in sent
