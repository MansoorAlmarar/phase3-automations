"""Indicators, floors and the trade-plan numbers, from fixtures only."""
import pandas as pd
import pytest
import requests

import stock_core as sc
from conftest import make_snap, synthetic_history


def test_num_rejects_non_numbers_and_nan():
    assert sc.num(None) is None
    assert sc.num("x") is None
    assert sc.num(float("nan")) is None
    assert sc.num("1.236", 2) == 1.24


def test_growth_is_none_when_base_is_not_positive():
    assert sc._growth(2, 0) is None
    assert sc._growth(2, -1) is None
    assert sc._growth(3, 2) == 50.0


def test_eps_growth_compares_latest_quarter_and_last_four():
    hist = [{"period": f"2026-{q:02d}-30", "actual": a} for q, a in
            zip((9, 6, 3, 12, 9, 6, 3, 12), (0.30, 0.30, 0.30, 0.30, 0.20, 0.20, 0.20, 0.20))]
    g = sc.eps_growth(hist)
    assert g["quarter"]["growth_pct"] == 50.0
    assert g["ttm"] == {"now": 1.2, "year_ago": 0.8, "growth_pct": 50.0}


def test_eps_growth_is_empty_without_history():
    assert sc.eps_growth([]) == {}


def test_rsi_stays_between_0_and_100(history):
    r = sc.rsi(history["Close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_levels_split_support_below_and_resistance_above(history):
    last = float(history["Close"].iloc[-1])
    lv = sc.levels(history.tail(252), last)
    assert all(s["price"] < last for s in lv["support"])
    assert all(r["price"] > last for r in lv["resistance"])


def test_repeating_floor_is_found_near_the_trough(history):
    rg = sc.range_cycles(history.tail(252), float(history["Close"].iloc[-1]))
    assert rg["verdict"] in ("repeating floor", "floor, weak bounces")
    assert 19 < rg["floor"][0] < 21
    assert rg["touches"] >= 2
    assert rg["good_bounces"] >= 2  # 20 to 30 is about +50% per swing


def test_broken_floor_is_reported_as_broken(history):
    # Regression: a floor the price has fallen through (and not come back to) used to be dropped, so the
    # report said "no clear floor" and never raised "Floor broken".
    slide = [19.6, 19.2, 18.8, 18.4, 18.0, 17.7, 17.4, 17.2, 17.0, 16.9, 16.8, 16.7]
    dropped = history.copy()
    for i, v in enumerate(slide):
        dropped.iloc[-len(slide) + i, dropped.columns.get_indexer(["Open", "High", "Low", "Close"])] = [v + 0.1, v + 0.2, v - 0.2, v]
    rg = sc.range_cycles(dropped.tail(252), 16.7)
    assert rg["verdict"] == "floor broken"
    assert rg["now"] == "below the floor"
    assert rg["broken"]["closes_below"] >= 2


def test_no_floor_on_a_straight_line():
    idx = pd.bdate_range(end="2026-10-28", periods=252)
    line = pd.DataFrame({"Open": range(1, 253), "High": range(1, 253), "Low": range(1, 253),
                         "Close": range(1, 253), "Volume": 1.0}, index=idx, dtype=float)
    assert sc.range_cycles(line, 252.0)["verdict"] == "no clear floor"


def test_technicals_survive_nan_volume():
    full = synthetic_history()
    full.iloc[-1, full.columns.get_loc("Volume")] = float("nan")
    t = sc.technicals_from_history(full)
    assert t["volume"]["last"] is None
    assert t["volume"]["avg_20d"] is not None


def test_plan_holding_numbers_follow_the_rules(snap):
    snap["technicals"]["range"] = {"floor": [20.0, 20.4], "bounces": [], "broken": None}
    pos = {"position": "holding", "entry": 22.0, "lots": [[10.0, 22.0]], "shares": 10.0, "plan_note": None}
    plan = sc.build_plan(snap, pos)
    assert plan["target"] == 23.1  # +5% goal from the entry
    assert plan["stop"] == round(20.0 * (1 - sc.STOP_BUFFER_PCT / 100), 2)  # floor bottom is within 15% of the entry
    assert plan["loss_pct"] < 0
    assert plan["reward_risk"] == round(sc.GOAL_PCT / abs(plan["loss_pct"]), 2)
    assert plan["cost"] == 220.0
    assert plan["value"] == round(10 * plan["price"], 2)
    assert plan["breakeven_trigger"] == 22.66


def test_plan_watching_has_no_position_numbers(snap):
    plan = sc.build_plan(snap, {"position": "watching", "entry": None, "lots": [], "shares": None})
    assert plan["position"] == "watching"
    assert plan["entry"] == round(plan["price"], 2)
    assert "pnl_pct" not in plan and "cost" not in plan


def test_plan_without_any_price_says_so(snap):
    snap["quote"] = {}
    snap["technicals"]["last_close"] = None
    assert sc.build_plan(snap, {"position": "watching"}) == {"error": "no price"}


def test_peer_table_is_only_for_verified_peers(monkeypatch):
    # With no verified list, candidates are names only; no numbers are shown.
    monkeypatch.setattr(sc, "get", lambda url, params: ([], None))
    rows, status = sc.peers("TEST", [], own_industry=None)
    assert rows == []
    assert "No verified peers" in status


def test_key_never_appears_in_errors(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "SECRETKEY123")

    def boom(url, **kw):
        raise requests.ConnectionError(f"{url}?apikey=SECRETKEY123")

    monkeypatch.setattr(sc.requests, "get", boom)
    errors = []
    data, err = sc.get(f"{sc.FMP}/profile", {"symbol": "TEST", "apikey": "SECRETKEY123"})
    assert data is None
    assert "SECRETKEY123" not in (err or "")


def test_snapshot_survives_every_source_failing(monkeypatch):
    monkeypatch.setattr(sc, "get", lambda url, params: (None, "HTTP 500"))
    monkeypatch.setattr(sc, "technicals", lambda ticker, errors: (_ for _ in ()).throw(RuntimeError("yahoo down")))
    monkeypatch.setattr(sc, "gaap_eps", lambda ticker, errors: {})
    monkeypatch.setattr(sc, "eps_history_yf", lambda ticker, errors: [])
    snap = sc.build_snapshot("TEST")
    assert snap["technicals"] == {}
    assert snap["quote"] == {}
    assert snap["errors"], "every failed source must be listed"
    assert any("yahoo down" in e or "RuntimeError" in e for e in snap["errors"])
