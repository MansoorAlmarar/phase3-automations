"""Cloud stock report, data layer. Ported from STOIC scripts/stock_snapshot.py + the numbers of stock_report.py.

build_snapshot(ticker) -> dict      market data, indicators, floor/bounce detection (never raises, gaps go in "errors")
build_plan(snap, position, entry)   Mansoor's trade-plan numbers (goal, stop, reward:risk, ladder)
parse_note(note)                    "holding at 14.20" -> ("holding", 14.2)

Keys come from the environment (Trigger.dev env vars): FMP_API_KEY, FINNHUB_API_KEY. Never printed.
"""
import os
import re
from datetime import date, timedelta

import requests

# Mansoor's trading rules (STOIC decisions/log.md, 2026-10-02). Change here, every report follows.
GOAL_PCT = 5
STOP_BUFFER_PCT = 1.5
LADDER = [5, 10, 15, 20]
HALAL_RECHECK_DAYS = 7

FMP = "https://financialmodelingprep.com/stable"
FH = "https://finnhub.io/api/v1"
BREAK_PCT = 1.5
BREAK_MIN_CLOSES = 2
BREAK_RECENT_DAYS = 60


def _keys():
    return os.environ.get("FMP_API_KEY", "").strip(), os.environ.get("FINNHUB_API_KEY", "").strip()


def get(url, params):
    """GET JSON; on failure return (None, message) without leaking keys."""
    try:
        r = requests.get(url, params=params, timeout=20)
    except requests.RequestException as e:
        return None, f"network error: {type(e).__name__}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return r.json(), None
    except ValueError:
        return None, "not JSON"


def num(x, nd=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if x != x else round(x, nd)


def _whole(x):
    return None if x != x else int(x)


def retry(fn, tries=3, wait=2.0):
    """Yahoo (yfinance) is unofficial and flaky: try a few times before giving up. Re-raises the last error."""
    import time
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(wait * (i + 1))
    raise last


def holdings_for(ticker):
    """Positions kept in the HOLDINGS_JSON env var (not in git). {"ARLO": {"lots": [[864, 12.97], [56.8, 12.55]], "plan_note": "..."}}
    Returns (position or None, problem or None). A broken value is a problem to report, not a silent "watching"."""
    import json
    try:
        return (json.loads(os.environ.get("HOLDINGS_JSON", "") or "{}") or {}).get(ticker), None
    except ValueError:
        return None, "HOLDINGS_JSON is not valid JSON, so no stored position was read."


def parse_note(note, ticker=None):
    """Read the free-text note. Returns a dict:
    position 'holding'|'watching', lots [[shares, price], ...], entry (average), shares, peers [..], plan_note, warning.

    'holding 864 @ 12.97, 56.8 @ 12.55'  -> two lots, average entry
    'holding at 14.20'                   -> one price, shares unknown
    'watching'                           -> watching even if HOLDINGS_JSON has the ticker
    'peers: ALRM, REZI, ADT'             -> peer override
    no position words and ticker in HOLDINGS_JSON -> holding from there
    """
    text = (note or "").strip()
    out = {"position": "watching", "lots": [], "entry": None, "shares": None, "peers": [], "plan_note": None,
           "warning": None, "source": "none"}
    pm = re.search(r"\bpeers?\b\s*[:=]?\s*([A-Za-z]{1,6}(?:\s*[,/ ]\s*[A-Za-z]{1,6}){0,5})", text, re.I)
    if pm:
        out["peers"] = [s.upper() for s in re.split(r"[,/ ]+", pm.group(1).strip()) if s][:5]
        text_pos = text[:pm.start()] + " " + text[pm.end():]
    else:
        text_pos = text

    def finish(lots, source):
        shares = sum(s for s, _ in lots)
        out.update(position="holding", lots=lots, shares=round(shares, 4),
                   entry=round(sum(s * p for s, p in lots) / shares, 4), source=source)

    explicit_watch = re.search(r"\bwatch(?:ing)?\b", text_pos, re.I)
    if re.search(r"\bhold(?:ing)?\b", text_pos, re.I):
        lots = [[float(a), float(b)] for a, b in
                re.findall(r"(\d+(?:\.\d+)?)\s*(?:shares?)?\s*(?:@|at)\s*\$?(\d+(?:\.\d+)?)", text_pos, re.I) if float(a) > 0]
        if lots:
            finish(lots, "note")
        else:
            # A price needs a marker (at, @, entry or $). A bare number is a share count: "holding 2 shares" is not a price.
            m = re.search(r"(?:\bat\b|@|\bentry\b|\$)\s*(\d+(?:\.\d+)?)", text_pos, re.I)
            if m:
                out.update(position="holding", entry=float(m.group(1)), source="note")
            else:
                out["warning"] = "Note says holding but no entry price was readable, so the report treats it as watching."
    elif not explicit_watch and ticker:
        h, problem = holdings_for(ticker)
        h = h or {}
        lots = [[float(s), float(p)] for s, p in h.get("lots") or [] if float(s) > 0]
        if lots:
            finish(lots, "holdings")
            out["plan_note"] = h.get("plan_note")
        elif problem:
            out["warning"] = problem
    return out


# ---------- data pulls ----------
def profile(ticker, errors):
    fmp, _ = _keys()
    data, err = get(f"{FMP}/profile", {"symbol": ticker, "apikey": fmp})
    if not data:
        errors.append(f"FMP profile: {err or 'empty'}")
        return {}
    p = data[0]
    keys = ["companyName", "sector", "industry", "country", "exchange", "marketCap", "beta", "website", "ipoDate"]
    return {k: p.get(k) for k in keys}


def quote(ticker, errors):
    _, fh = _keys()
    data, err = get(f"{FH}/quote", {"symbol": ticker, "token": fh})
    if not data or not data.get("c"):
        errors.append(f"Finnhub quote: {err or 'empty'}")
        return {}
    return {"price": data["c"], "change_pct": num(data.get("dp")), "day_low": data.get("l"),
            "day_high": data.get("h"), "prev_close": data.get("pc")}


def analysts(ticker, errors):
    _, fh = _keys()
    data, err = get(f"{FH}/stock/recommendation", {"symbol": ticker, "token": fh})
    if not data:
        errors.append(f"Finnhub ratings: {err or 'none'}")
        return []
    return data[:3]


def metrics(ticker, errors):
    _, fh = _keys()
    data, err = get(f"{FH}/stock/metric", {"symbol": ticker, "metric": "all", "token": fh})
    m = (data or {}).get("metric") or {}
    if not m:
        errors.append(f"Finnhub metrics: {err or 'none'}")
        return {}
    keys = ["peTTM", "psTTM", "pbQuarterly", "epsTTM", "epsGrowthTTMYoy", "revenueGrowthTTMYoy",
            "revenueGrowthQuarterlyYoy", "grossMarginTTM", "operatingMarginTTM", "netProfitMarginTTM",
            "roeTTM", "currentRatioQuarterly", "totalDebt/totalEquityQuarterly",
            "52WeekHigh", "52WeekLow", "52WeekPriceReturnDaily", "10DayAverageTradingVolume", "beta"]
    return {k: num(m.get(k)) for k in keys}


# Finnhub sometimes lists the wrong industry. Add the real competitors here (learned by hand, e.g. ARLO).
PEER_OVERRIDES = {"ARLO": ["ALRM", "REZI", "ADT"]}


def _industry(sym):
    fmp, _ = _keys()
    data, _e = get(f"{FMP}/profile", {"symbol": sym, "apikey": fmp})
    return ((data or [{}])[0].get("industry") or "").strip().lower() or None


def peers(ticker, errors, own_industry, user_peers=None, n=3):
    """A wrong peer table is worse than none. Numbers are shown ONLY for peers a person verified:
    (1) peers given in the note, or (2) PEER_OVERRIDES. Otherwise the report lists unverified candidates
    (same FMP industry label as the company) as names only, no numbers. Returns (peers, status_text)."""
    _, fh = _keys()
    manual = user_peers or PEER_OVERRIDES.get(ticker)
    if manual:
        picked = [s for s in manual if s != ticker][:5]
        status = "Peers verified by you (note or override list)."
    else:
        data, err = get(f"{FH}/stock/peers", {"symbol": ticker, "token": fh})
        cands = []
        for sym in [s for s in (data or []) if s != ticker][:8]:
            if own_industry and _industry(sym) == own_industry:
                cands.append(sym)
            if len(cands) == n:
                break
        if not data:
            errors.append(f"Finnhub peers: {err or 'none'}")
        hint = (f" Unverified candidates by industry label ({own_industry}): {', '.join(cands)}. Industry labels are not proof "
                f"they compete with the main business." if cands else "")
        example = ", ".join(cands) if cands else "AAA, BBB, CCC"
        return [], (f"No verified peers for {ticker}, so no comparison table.{hint} To add a table, send the real "
                    f"competitors in the note, e.g. peers: {example}")
    out = []
    for sym in picked:
        prof, _e = get(f"{FH}/stock/profile2", {"symbol": sym, "token": fh})
        met, _e = get(f"{FH}/stock/metric", {"symbol": sym, "metric": "all", "token": fh})
        m = (met or {}).get("metric") or {}
        out.append({
            "ticker": sym, "name": (prof or {}).get("name"),
            "market_cap_m": num((prof or {}).get("marketCapitalization"), 0),
            "revenue_growth_yoy": num(m.get("revenueGrowthTTMYoy")),
            "gross_margin": num(m.get("grossMarginTTM")),
            "net_margin": num(m.get("netProfitMarginTTM")),
            "pe_ttm": num(m.get("peTTM")),
            "return_52w": num(m.get("52WeekPriceReturnDaily")),
        })
    return out, status


def earnings(ticker, errors):
    _, fh = _keys()
    today = date.today()
    cal, err = get(f"{FH}/calendar/earnings", {"symbol": ticker, "from": str(today),
                                               "to": str(today + timedelta(days=120)), "token": fh})
    upcoming = sorted((cal or {}).get("earningsCalendar") or [], key=lambda e: e.get("date") or "")
    if cal is None:
        errors.append(f"Finnhub earnings calendar: {err}")
    nxt = upcoming[0] if upcoming else {}
    hist, err = get(f"{FH}/stock/earnings", {"symbol": ticker, "limit": 8, "token": fh})
    if hist is None:
        errors.append(f"Finnhub earnings history: {err}")
    hist = sorted(hist or [], key=lambda h: h.get("period") or "", reverse=True)
    return {
        "next": {"date": nxt.get("date"), "time": nxt.get("hour"), "eps_estimate": nxt.get("epsEstimate"),
                 "days_away": (date.fromisoformat(nxt["date"]) - today).days if nxt.get("date") else None},
        "last_4": [{"period": h.get("period"), "actual": h.get("actual"), "estimate": h.get("estimate"),
                    "surprise_pct": num(h.get("surprisePercent"))} for h in hist[:4]],
        "eps_growth": eps_growth(hist if len([h for h in hist if h.get("actual") is not None]) >= 8
                                 else eps_history_yf(ticker, errors) or hist),
    }


def eps_history_yf(ticker, errors):
    """Reported (adjusted) EPS per quarter, newest first, from Yahoo. Finnhub's free plan returns only 4 quarters."""
    import yfinance as yf
    try:
        df = retry(lambda: yf.Ticker(ticker).get_earnings_dates(limit=16))
        df = df[df["Reported EPS"].notna()].sort_index(ascending=False)
        return [{"period": str(ix.date()), "actual": num(v, 4)} for ix, v in zip(df.index, df["Reported EPS"])]
    except Exception as e:  # noqa: BLE001
        errors.append(f"yfinance EPS history: {type(e).__name__}")
        return []


def _growth(new, old):
    """Percent growth, or None when the old value is zero or negative (not meaningful)."""
    if new is None or old is None or old <= 0:
        return None
    return num((new / old - 1) * 100, 1)


def eps_growth(hist):
    """Adjusted EPS growth from Finnhub's reported quarters (newest first): latest quarter vs the same quarter a
    year earlier, and the last 4 quarters vs the 4 before. Finnhub's own epsGrowthTTMYoy is often empty."""
    eps = [h.get("actual") for h in hist]
    out = {}
    if len(eps) >= 5 and eps[0] is not None and eps[4] is not None:
        out["quarter"] = {"period": hist[0].get("period"), "now": eps[0], "year_ago": eps[4], "growth_pct": _growth(eps[0], eps[4])}
    if len(eps) >= 8 and all(x is not None for x in eps[:8]):
        a, b = sum(eps[:4]), sum(eps[4:8])
        out["ttm"] = {"now": round(a, 2), "year_ago": round(b, 2), "growth_pct": _growth(a, b)}
    return out


def gaap_eps(ticker, errors):
    """Latest quarterly GAAP diluted EPS (yfinance), to compare with adjusted EPS. Optional."""
    import yfinance as yf
    try:
        inc = retry(lambda: yf.Ticker(ticker).quarterly_income_stmt)
        row = inc.loc["Diluted EPS"].dropna()
        return {"quarter": str(row.index[0].date()), "value": num(row.iloc[0], 2)}
    except Exception as e:  # noqa: BLE001
        errors.append(f"yfinance GAAP EPS: {type(e).__name__}")
        return {}


def news(ticker, errors, days=30, n=10):
    _, fh = _keys()
    today = date.today()
    data, err = get(f"{FH}/company-news", {"symbol": ticker, "from": str(today - timedelta(days=days)),
                                           "to": str(today), "token": fh})
    if data is None:
        errors.append(f"Finnhub news: {err}")
        return []
    return [{"date": str(date.fromtimestamp(a["datetime"])), "headline": a.get("headline"),
             "source": a.get("source"), "summary": (a.get("summary") or "")[:300], "url": a.get("url")}
            for a in data[:n]]


# ---------- indicators ----------
def rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def macd(close):
    line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = line.ewm(span=9, adjust=False).mean()
    hist = line - signal
    return {"macd": num(line.iloc[-1], 3), "signal": num(signal.iloc[-1], 3), "histogram": num(hist.iloc[-1], 3),
            "crossed_up_recently": bool((hist.tail(5) > 0).iloc[-1] and (hist.tail(5) <= 0).any()),
            "crossed_down_recently": bool((hist.tail(5) < 0).iloc[-1] and (hist.tail(5) >= 0).any())}


def levels(hist, last, window=5, merge_pct=1.5):
    h = hist.tail(126)
    lows, highs = h["Low"], h["High"]
    swing_lows = [lows.iloc[i] for i in range(window, len(h) - window)
                  if lows.iloc[i] == lows.iloc[i - window:i + window + 1].min()]
    swing_highs = [highs.iloc[i] for i in range(window, len(h) - window)
                   if highs.iloc[i] == highs.iloc[i - window:i + window + 1].max()]

    def merge(prices, descending):
        out = []
        for p in sorted(prices, reverse=descending):
            if not out or abs(p / out[-1]["price"] - 1) * 100 > merge_pct:
                out.append({"price": num(p), "touches": 1})
            else:
                out[-1]["touches"] += 1
        return out

    sup = merge([p for p in swing_lows if p < last], descending=True)[:3]
    res = merge([p for p in swing_highs if p > last], descending=False)[:3]
    for lv in sup + res:
        lv["distance_pct"] = num((lv["price"] / last - 1) * 100)
    return {"support": sup, "resistance": res}


def range_cycles(hist, last, window=7, tol_pct=5, min_bounce_pct=10, lookahead=40):
    """Does the stock keep bouncing off the same floor? `hist` is the last year of daily bars."""
    low, close = hist["Low"], hist["Close"]
    swings = [(low.index[i], low.iloc[i]) for i in range(window, len(low) - window)
              if low.iloc[i] == low.iloc[i - window:i + window + 1].min()]
    if len(low) > window and low.iloc[-window:].min() == low.iloc[-2 * window:].min():
        i = int(low.iloc[-window:].argmin()) + len(low) - window
        swings.append((low.index[i], low.iloc[i]))

    floors = []
    for ts, p in sorted(swings, key=lambda x: x[1]):
        if floors and p <= floors[-1]["band"][0] * (1 + tol_pct / 100):
            floors[-1]["band"][1] = p
            floors[-1]["touches"].append(ts)
        else:
            floors.append({"band": [p, p], "touches": [ts]})
    floors = [f for f in floors if len(f["touches"]) >= 2]
    trend_1y = (close.iloc[-1] / close.iloc[0] - 1) * 100
    if not floors:
        return {"verdict": "no clear floor", "trend_1y_pct": num(trend_1y, 0)}
    # Prefer a floor under the price. A floor above the price still matters when the price has broken it (checked below).
    held = [f for f in floors if f["band"][0] <= last]
    f = max(held or floors, key=lambda f: (len(f["touches"]), f["band"][0]))

    bounces = []
    touches = sorted(f["touches"])
    for k, ts in enumerate(touches):
        i = close.index.get_loc(ts)
        stop = close.index.get_loc(touches[k + 1]) if k + 1 < len(touches) else len(close)
        after = close.iloc[i + 1:min(i + 1 + lookahead, stop)]
        if after.empty:
            continue
        peak_ts, peak = after.idxmax(), after.max()
        bounces.append({"low_date": str(ts.date()), "low": num(low.loc[ts]), "peak_date": str(peak_ts.date()),
                        "peak": num(peak), "days": (peak_ts - ts).days,
                        "gain_pct": num((peak / low.loc[ts] - 1) * 100)})
    good = [b for b in bounces if b["gain_pct"] >= min_bounce_pct]
    above = (last / f["band"][1] - 1) * 100
    below = close[close.index > touches[-1]]
    below = below[below < f["band"][0] * (1 - BREAK_PCT / 100)]
    if len(below) >= BREAK_MIN_CLOSES and (close.index[-1] - below.index[-1]).days <= BREAK_RECENT_DAYS:
        return {
            "floor": [num(f["band"][0]), num(f["band"][1])], "touches": len(f["touches"]), "bounces": bounces,
            "good_bounces": len(good), "pct_above_floor": num(above), "trend_1y_pct": num(trend_1y, 0),
            "broken": {"first_close_below": str(below.index[0].date()), "last_close_below": str(below.index[-1].date()),
                       "closes_below": len(below), "lowest_close": num(below.min())},
            "now": "back at the old floor from below" if last >= f["band"][0] else "below the floor",
            "verdict": "floor broken",
        }
    if f["band"][0] > last:  # above the price and not broken: the price has no floor under it
        return {"verdict": "no clear floor", "trend_1y_pct": num(trend_1y, 0)}
    return {
        "floor": [num(f["band"][0]), num(f["band"][1])], "touches": len(f["touches"]), "bounces": bounces,
        "good_bounces": len(good),
        "avg_bounce_pct": num(sum(b["gain_pct"] for b in good) / len(good)) if good else None,
        "avg_days_to_peak": round(sum(b["days"] for b in good) / len(good)) if good else None,
        "pct_above_floor": num(above),
        "now": "at floor" if above <= 3 else f"{num(above, 1)}% above floor",
        "trend_1y_pct": num(trend_1y, 0),
        "verdict": "repeating floor" if len(good) >= 2 else "floor, weak bounces",
    }


def technicals(ticker, errors):
    """Fetch 2 years of daily bars (SMA 200 needs the extra history). Floor and levels use the last year only."""
    import yfinance as yf
    try:
        full = retry(lambda: yf.Ticker(ticker).history(period="2y"))
    except Exception as e:  # yfinance raises many types
        errors.append(f"yfinance history: {type(e).__name__}")
        return {}
    if full.empty:
        errors.append("yfinance history: no rows")
        return {}
    return technicals_from_history(full)


def technicals_from_history(full):
    """Indicators from 2 years of daily bars. Pure: no network, so tests can feed fixtures."""
    hist = full.tail(252)
    close = hist["Close"]
    fclose = full["Close"]
    last = close.iloc[-1]

    def ret(days):
        return num((last / close.iloc[-days - 1] - 1) * 100) if len(close) > days else None

    def sma(n):
        return num(fclose.rolling(n).mean().iloc[-1]) if len(fclose) >= n else None

    return {
        "last_close": num(last), "as_of": str(hist.index[-1].date()),
        "sma9": sma(9), "sma20": sma(20), "sma50": sma(50), "sma200": sma(200),
        "rsi14": num(rsi(fclose).iloc[-1], 1),
        "macd": macd(fclose),
        "levels": levels(hist, last),
        "range": range_cycles(hist, last),
        "return_pct": {"5d": ret(5), "1m": ret(21), "3m": ret(63), "6m": ret(126)},
        "range_1y": {"low": num(hist["Low"].min()), "high": num(hist["High"].max())},
        "volume": {"last": _whole(hist["Volume"].iloc[-1]), "avg_20d": _whole(hist["Volume"].tail(20).mean())},
        "history": {"dates": [str(d.date()) for d in full.index], "close": [num(c, 4) for c in fclose],
                    "open": [num(x, 4) for x in full["Open"]], "high": [num(x, 4) for x in full["High"]],
                    "low": [num(x, 4) for x in full["Low"]]},
    }


def build_snapshot(ticker, user_peers=None):
    errors = []
    snap = {"ticker": ticker, "date": str(date.today())}
    steps = [("profile", lambda: profile(ticker, errors), {}), ("quote", lambda: quote(ticker, errors), {}),
             ("metrics", lambda: metrics(ticker, errors), {}), ("analysts", lambda: analysts(ticker, errors), []),
             ("earnings", lambda: earnings(ticker, errors), {"next": {}, "last_4": [], "eps_growth": {}}),
             ("news", lambda: news(ticker, errors), []), ("technicals", lambda: technicals(ticker, errors), {})]
    for name, fn, empty in steps:
        try:
            snap[name] = fn()
        except Exception as e:  # one failing source must not sink the report
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:100]}")
            snap[name] = empty
    try:
        own = (snap["profile"].get("industry") or "").strip().lower() or None
        snap["peers"], snap["peers_status"] = peers(ticker, errors, own, user_peers)
    except Exception as e:  # noqa: BLE001
        errors.append(f"peers: {type(e).__name__}: {str(e)[:100]}")
        snap["peers"], snap["peers_status"] = [], "Peers unavailable."
    snap["gaap_eps"] = gaap_eps(ticker, errors)
    snap["errors"] = errors
    return snap


# ---------- trade plan (port of stock_report.trade_plan, numbers only) ----------
def build_plan(snap, pos):
    """`pos` is the dict from parse_note. All numbers; no advice. Works for holding and watching."""
    t = snap["technicals"]
    price = (snap.get("quote") or {}).get("price") or t.get("last_close")
    if price is None:
        return {"error": "no price"}
    holding = pos.get("position") == "holding" and pos.get("entry")
    entry = pos["entry"] if holding else price
    rg = t.get("range") or {}
    lv = t.get("levels") or {"support": [], "resistance": []}

    def stop_for(ref_price):
        """Stop reference: floor bottom if it is within 15% below `ref_price`, else nearest support below it."""
        if rg.get("floor") and 0 < (1 - rg["floor"][0] / ref_price) * 100 <= 15:
            return rg["floor"][0], "floor bottom"
        below = [s["price"] for s in lv["support"] if s["price"] < ref_price]
        return (max(below), "nearest support") if below else (None, None)

    ref, ref_lbl = stop_for(entry)
    stop = round(ref * (1 - STOP_BUFFER_PCT / 100), 2) if ref else None
    loss = (stop / entry - 1) * 100 if stop else None
    target = entry * (1 + GOAL_PCT / 100)
    in_way = [r["price"] for r in lv["resistance"] if entry < r["price"] < target]

    done = [b for b in rg.get("bounces", []) if b["peak_date"] < snap["date"]]
    hit = [b for b in done if b["gain_pct"] >= GOAL_PCT]
    plan = {
        "position": "holding" if holding else "watching", "entry": round(entry, 2), "price": price,
        "lots": pos.get("lots") or [], "shares": pos.get("shares"), "plan_note": pos.get("plan_note"),
        "goal_pct": GOAL_PCT, "target": round(target, 2), "in_way": in_way,
        "stop": stop, "stop_ref": ref, "stop_ref_label": ref_lbl, "stop_buffer_pct": STOP_BUFFER_PCT,
        "loss_pct": round(loss, 2) if loss is not None else None,
        "reward_risk": round(GOAL_PCT / abs(loss), 2) if loss else None,
        "past_bounces": {"done": len(done), "hit": len(hit),
                         "best": max((b["gain_pct"] for b in done), default=None),
                         "worst": min((b["gain_pct"] for b in done), default=None)},
        "ladder": [{"level": l, "price": round(entry * (1 + l / 100), 2),
                    "safer_stop": round(entry * (1 + (l - 5) / 100), 2),
                    "tighter_stop": round(entry * (1 + (l - 3) / 100), 2)} for l in LADDER],
        "breakeven_trigger": round(entry * 1.03, 2),  # trading-lessons rule 2: ~+3% above entry, stop moves to entry
    }
    if holding:
        plan["pnl_pct"] = round((price / entry - 1) * 100, 2)
        if pos.get("shares"):
            plan["cost"] = round(pos["shares"] * entry, 2)
            plan["value"] = round(pos["shares"] * price, 2)
            plan["pnl_abs"] = round(plan["value"] - plan["cost"], 2)
        plan["to_goal_pct"] = round((target / price - 1) * 100, 2)
        plan["to_stop_pct"] = round((stop / price - 1) * 100, 2) if stop else None
        plan["risk_abs"] = round(pos["shares"] * (stop - price), 2) if (stop and pos.get("shares")) else None
    # Entry check (what a new buyer faces today), shown for watching and as context for holding.
    ref_p, lbl_p = stop_for(price)
    if ref_p:
        stop_p = round(ref_p * (1 - STOP_BUFFER_PCT / 100), 2)
        loss_p = (stop_p / price - 1) * 100
        ec = {"price": price, "stop": stop_p, "stop_ref_label": lbl_p, "loss_pct": round(loss_p, 2),
              "reward_risk": round(GOAL_PCT / abs(loss_p), 2) if loss_p else None,
              "entry_for_1to1": round(stop_p / (1 - GOAL_PCT / 100), 2)}
        ec["pct_above_1to1"] = round((price / ec["entry_for_1to1"] - 1) * 100, 2)
        if rg.get("floor") and not rg.get("broken"):
            ftop = rg["floor"][1]
            ec["floor_top"] = ftop
            ec["pct_above_floor_top"] = round((price / ftop - 1) * 100, 2)
            ec["reward_risk_at_floor_top"] = round(GOAL_PCT / ((ftop - stop_p) / ftop * 100), 2) if ftop > stop_p else None
        plan["entry_check"] = ec
    return plan
