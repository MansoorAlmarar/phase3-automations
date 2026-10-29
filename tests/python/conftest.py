"""Shared fixtures for the Python report code. No network: prices are generated, pages are saved files."""
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "python"))

import stock_core  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
TODAY = "2026-10-28"  # fixed date so every test is repeatable


def synthetic_history(bars=260, end=TODAY):
    """Daily bars that swing between about 20 and 30 with a 50-bar period: a repeating floor near 20."""
    idx = pd.bdate_range(end=end, periods=bars)
    rng = np.random.RandomState(1)
    t = np.arange(bars)
    close = 25 + 5 * np.sin(2 * math.pi * t / 50) + rng.normal(0, 0.05, bars)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.3
    low = np.minimum(open_, close) - 0.3
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": np.full(bars, 1_000_000.0)}, index=idx)


def make_snap(full=None, date=TODAY):
    """A snapshot shaped like stock_core.build_snapshot, built from fixtures instead of the network."""
    full = synthetic_history() if full is None else full
    last = float(full["Close"].iloc[-1])
    return {
        "ticker": "TEST", "date": date,
        "profile": {"companyName": "Test Corp", "sector": "Technology", "industry": "software application",
                    "country": "US", "exchange": "NASDAQ", "marketCap": 2.0e9, "beta": 1.1},
        "quote": {"price": last, "change_pct": 0.42, "day_low": last - 0.5, "day_high": last + 0.5, "prev_close": last - 0.1},
        "metrics": {"revenueGrowthQuarterlyYoy": 12.3, "revenueGrowthTTMYoy": 9.1, "epsGrowthTTMYoy": None,
                    "grossMarginTTM": 41.0, "operatingMarginTTM": 12.0, "netProfitMarginTTM": 6.5, "peTTM": 18.4,
                    "roeTTM": 14.0, "currentRatioQuarterly": 1.8, "totalDebt/totalEquityQuarterly": 0.42, "beta": 1.1},
        "analysts": [{"period": "2026-10-01", "strongBuy": 3, "buy": 5, "hold": 4, "sell": 0, "strongSell": 0}],
        "earnings": {"next": {"date": "2026-11-12", "time": "amc", "eps_estimate": 0.21, "days_away": 15},
                     "last_4": [{"period": "2026-09-30", "actual": 0.30, "estimate": 0.25, "surprise_pct": 20.0}],
                     "eps_growth": {"quarter": {"period": "2026-09-30", "now": 0.30, "year_ago": 0.24, "growth_pct": 25.0}}},
        "news": [{"date": "2026-10-20", "headline": "Test Corp wins contract", "source": "Wire", "summary": "",
                  "url": "https://example.com/a"}],
        "technicals": stock_core.technicals_from_history(full),
        "peers": [], "peers_status": "No verified peers for TEST, so no comparison table.",
        "gaap_eps": {"quarter": "2026-09-30", "value": 0.22},
        "errors": [],
    }


@pytest.fixture
def history():
    return synthetic_history()


@pytest.fixture
def snap():
    return make_snap()


@pytest.fixture
def osooli_text():
    with open(os.path.join(FIXTURES, "osooli_arlo.txt"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def no_keys_leak_into_tests(monkeypatch):
    """Tests never see real keys, and never reach a real service by accident."""
    for k in ("FMP_API_KEY", "FINNHUB_API_KEY", "OPENROUTER_API_KEY", "FIRECRAWL_API_KEY", "HOLDINGS_JSON"):
        monkeypatch.delenv(k, raising=False)
