"""Reading the free-text note. A share count must never be read as a price."""
import json

import stock_core as sc


def test_two_lots_give_share_weighted_average():
    pos = sc.parse_note("holding 864 @ 12.97, 56.8 @ 12.55")
    assert pos["position"] == "holding"
    assert pos["lots"] == [[864.0, 12.97], [56.8, 12.55]]
    assert pos["shares"] == 920.8
    assert pos["entry"] == round((864 * 12.97 + 56.8 * 12.55) / 920.8, 4)
    assert pos["source"] == "note"


def test_price_only_gives_entry_without_shares():
    pos = sc.parse_note("holding at 14.20")
    assert (pos["position"], pos["entry"], pos["shares"]) == ("holding", 14.2, None)


def test_bare_share_count_is_not_a_price():
    # Regression: "holding 2 shares" used to become an entry price of 2.0.
    pos = sc.parse_note("holding 2 shares")
    assert pos["position"] == "watching"
    assert pos["entry"] is None
    assert "no entry price" in pos["warning"]


def test_shares_with_price_is_one_lot():
    pos = sc.parse_note("holding 2 shares at 14.20")
    assert pos["lots"] == [[2.0, 14.2]]


def test_zero_share_lot_does_not_crash():
    # Regression: "holding 0 @ 12" used to raise ZeroDivisionError.
    pos = sc.parse_note("holding 0 @ 12")
    assert pos["entry"] == 12.0
    assert pos["shares"] is None


def test_watching_wins_over_holdings_file(monkeypatch):
    monkeypatch.setenv("HOLDINGS_JSON", json.dumps({"ARLO": {"lots": [[10, 5]]}}))
    assert sc.parse_note("watching", "ARLO")["position"] == "watching"


def test_holdings_file_is_used_when_note_is_empty(monkeypatch):
    monkeypatch.setenv("HOLDINGS_JSON", json.dumps({"ARLO": {"lots": [[10, 5], [10, 7]], "plan_note": "test plan"}}))
    pos = sc.parse_note("", "ARLO")
    assert pos["position"] == "holding"
    assert pos["entry"] == 6.0
    assert pos["plan_note"] == "test plan"
    assert pos["source"] == "holdings"


def test_zero_share_lot_in_holdings_file_is_ignored(monkeypatch):
    monkeypatch.setenv("HOLDINGS_JSON", json.dumps({"ARLO": {"lots": [[0, 5]]}}))
    assert sc.parse_note("", "ARLO")["position"] == "watching"


def test_broken_holdings_file_is_a_visible_warning(monkeypatch):
    monkeypatch.setenv("HOLDINGS_JSON", "{not json")
    pos = sc.parse_note("", "ARLO")
    assert pos["position"] == "watching"
    assert "not valid JSON" in pos["warning"]


def test_peers_in_note_are_read_and_not_taken_as_position():
    pos = sc.parse_note("peers: ALRM, REZI, ADT")
    assert pos["peers"] == ["ALRM", "REZI", "ADT"]
    assert pos["position"] == "watching"


def test_holding_word_without_any_price_is_flagged():
    pos = sc.parse_note("holding for the long term")
    assert pos["position"] == "watching"
    assert pos["warning"]
