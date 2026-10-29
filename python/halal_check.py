"""Halal gate for the cloud stock report (phase 2).

check_halal(ticker) -> dict, never raises. Shape (read by docx_report.add_halal):
  status        compliant | doubtful | not_compliant | unchecked
  verdicts      {"Yaaqen": "Halal", "Zoya": "Halal", "Musaffa": "Halal"}
  purification_pct, data_date, checked, reasons [{screener, reason}], last_known {status, date, note}, notes
  login_expired True when Osooli showed the sign-in page (the task should Telegram "re-login needed")

How: Firecrawl browser session with the saved login profile (read-only), open Osooli's purification page, type the
ticker, click Check, read the page text. Public pages of the odd-one-out screener are scraped for its stated reason.
Rules: STOIC references/halal-screening.md. An unattended run cannot ask Mansoor, so anything unclear is `doubtful`.
Needs env: FIRECRAWL_API_KEY. Optional: OSOOLI_PROFILE (default "osooli").
"""
import json
import os
import re
import time
from datetime import date

import requests

API = "https://api.firecrawl.dev/v2"
PAGE = "https://www.osooli.app/tools/purification"
HERE = os.path.dirname(os.path.abspath(__file__))

HALAL_WORDS = {"halal", "compliant", "shariah compliant", "sharia compliant"}
BAD_WORDS = {"haram", "non-compliant", "not compliant", "not halal", "non compliant"}
HARAM_INDUSTRY_WORDS = ("casino", "gambling", "alcohol", "brewer", "distiller", "tobacco", "bank", "insurance",
                        "mortgage", "credit services", "capital markets", "weapon", "adult")
LIMITS = {"debt_mcap": 30, "cash_mcap": 30, "interest_rev": 5}
BORDER = 2


def _key():
    return os.environ.get("FIRECRAWL_API_KEY", "").strip()


def _hdr():
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


def _req(method, url, tries=4, **kw):
    """requests with a wait-and-retry on Firecrawl's per-minute rate limit (HTTP 429, honours Retry-After)."""
    r = None
    for i in range(tries):
        r = requests.request(method, url, **kw)
        if r.status_code != 429:
            return r
        try:
            wait = float(r.headers.get("Retry-After") or (r.json() or {}).get("retry_after_seconds") or 2)
        except (ValueError, TypeError):
            wait = 2
        time.sleep(min(wait + 1, 20))
    return r


def _norm(v):
    s = (v or "").strip().lower()
    if s in HALAL_WORDS:
        return "halal"
    if s in BAD_WORDS:
        return "haram"
    if s in ("", "—", "-", "n/a", "none", "not found", "unknown"):
        return "none"
    return "doubtful"  # questionable, doubtful, in review, anything else


LABEL = {"halal": "Halal", "haram": "Haram", "doubtful": "Doubtful / in review", "none": "No verdict"}


def last_known(ticker):
    try:
        with open(os.path.join(HERE, "halal_cases.json"), encoding="utf-8") as f:
            return json.load(f).get(ticker)
    except Exception:  # noqa: BLE001
        return None


# ---------- Osooli through a Firecrawl browser session ----------
def _read_osooli(ticker, log):
    """Return (page_text or None, problem or None). Always closes the session."""
    profile = os.environ.get("OSOOLI_PROFILE", "osooli")
    sid = None
    try:
        r = _req("POST", f"{API}/browser", headers=_hdr(), timeout=60,
                 json={"ttl": 120, "profile": {"name": profile, "saveChanges": False}})
        if r.status_code == 402:
            return None, "Firecrawl out of credits"
        if r.status_code != 200 or not r.json().get("id"):
            return None, f"Firecrawl session failed (HTTP {r.status_code})"
        sid = r.json()["id"]
        log(f"halal: session {sid[:8]} open")
        code = f"""
page.setDefaultTimeout(20000);
const T = {json.dumps(ticker)};
await page.goto({json.dumps(PAGE)}, {{waitUntil: "domcontentloaded"}});
await page.waitForTimeout(2500);
if (/auth|login|signup/.test(page.url())) {{ console.log("OSOOLI_JSON " + JSON.stringify({{login: false}})); }}
else {{
  await page.fill("input", T);
  await page.click('button:has-text("Check")');
  let txt = "";
  for (let i = 0; i < 18; i++) {{
    await page.waitForTimeout(1000);
    txt = await page.innerText("body");
    if (txt.includes("RESULTS FOR " + T) && /Yaaqen\\s*\\n[^\\n]+\\n\\s*Zoya/.test(txt) && !/Yaaqen\\s*\\n\\s*—/.test(txt)) break;
  }}
  console.log("OSOOLI_JSON " + JSON.stringify({{login: true, text: txt}}));
}}
"""
        e = _req("POST", f"{API}/browser/{sid}/execute", headers=_hdr(), timeout=90,
                 json={"code": code, "language": "node", "timeout": 70})
        out = (e.json() or {}).get("stdout", "") if e.status_code == 200 else ""
        m = re.search(r"OSOOLI_JSON (\{.*\})", out, re.S)
        if not m:
            return None, f"could not read Osooli ({(e.json() or {}).get('error', 'no output')[:80] if e.status_code == 200 else 'HTTP ' + str(e.status_code)})"
        data = json.loads(m.group(1))
        if not data.get("login"):
            return None, "login_expired"
        return data["text"], None
    except requests.RequestException as ex:
        return None, f"network error: {type(ex).__name__}"
    except Exception as ex:  # noqa: BLE001
        return None, f"{type(ex).__name__}: {str(ex)[:80]}"
    finally:
        if sid:
            try:
                _req("DELETE", f"{API}/browser/{sid}", headers=_hdr(), timeout=30)
                log(f"halal: session {sid[:8]} closed")
            except requests.RequestException:
                log("halal: WARNING could not close session (it expires by itself)")


def _pct_after(lines, label):
    for i, ln in enumerate(lines):
        if label in ln:
            for nxt in lines[i + 1:i + 6]:
                mm = re.fullmatch(r"([\d.]+)%\*?", nxt.strip())
                if mm:
                    return float(mm.group(1))
                if nxt.strip().lower() == "not found":
                    return None
    return None


def parse_osooli(text, ticker):
    """Page text -> dict, or None if the results are not there (unknown ticker, layout change)."""
    if f"RESULTS FOR {ticker}" not in text:
        return None
    m = re.search(r"Yaaqen\s*\n([^\n]+)\n\s*Zoya\s*\n([^\n]+)\n\s*Musaffa\s*\n([^\n]+)", text)
    if not m:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    pur = re.search(r"PURIFICATION\s*\n\s*([\d.]+)%", text)
    dd = re.search(r"FINANCIAL SCREENING\s*[·.\-]\s*(\d{4}-\d{2}-\d{2})", text)
    return {
        "verdicts_raw": {"Yaaqen": m.group(1).strip(), "Zoya": m.group(2).strip(), "Musaffa": m.group(3).strip()},
        "purification_pct": float(pur.group(1)) if pur else None,
        "purification_incomplete": "figure incomplete" in text or "Interest income not" in text,
        "data_date": dd.group(1) if dd else None,
        "debt_mcap": _pct_after(lines, "Debt ÷ Market Cap"),
        "interest_rev": _pct_after(lines, "Interest income ÷ Revenue"),
    }


# ---------- reasons from the screeners' public pages ----------
PUBLIC = {
    "Yaaqen": "https://yaaqen.com/stocks/{T}",
    "Zoya": "https://zoya.finance/stocks/{t}",
    "Musaffa": "https://musaffa.com/stock/{T}",
}
REASON_WORDS = re.compile(r"(debt|interest|revenue|business|activity|haram|non-?compliant|not compliant|ratio|screen|fail|"
                          r"محل نظر|غير شرعي|دين|فوائد)", re.I)


def _public_reason(screener, ticker, log):
    """Best-effort: the odd screener's stated reason from its public page. Returns text or None."""
    url = PUBLIC[screener].format(T=ticker, t=ticker.lower())
    try:
        r = _req("POST", f"{API}/scrape", headers=_hdr(), timeout=70,
                 json={"url": url, "formats": ["markdown"], "onlyMainContent": True})
        if r.status_code != 200:
            log(f"halal: public page {screener} HTTP {r.status_code}")
            return None
        md = str((r.json().get("data") or {}).get("markdown") or "")
    except Exception as e:  # noqa: BLE001 - an optional reason must never cost the Osooli verdict
        log(f"halal: public page {screener} failed {type(e).__name__}")
        return None
    hits = []
    for ln in md.splitlines():
        ln = re.sub(r"[#*_>\[\]`]|\(https?://[^)]*\)", "", ln).strip()
        if 20 <= len(ln) <= 260 and REASON_WORDS.search(ln) and ln not in hits:
            hits.append(ln)
        if len(hits) == 2:
            break
    return " ".join(hits)[:300] or None


# ---------- our own ratios (for the Yaqeen-only rule) ----------
def _first(df, *rows):
    for r in rows:
        if r in df.index and df.loc[r].notna().any():
            return float(df.loc[r].dropna().iloc[0])
    return None


def own_ratios(ticker):
    import yfinance as yf
    t = yf.Ticker(ticker)
    info, bs, inc = t.info, t.quarterly_balance_sheet, t.income_stmt
    mc = info.get("marketCap")
    debt = _first(bs, "Total Debt")
    cash = _first(bs, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
    rev = _first(inc, "Total Revenue")
    interest = _first(inc, "Interest Income", "Interest Income Non Operating")
    out = {"industry": info.get("industry"),
           "debt_mcap": debt / mc * 100 if debt is not None and mc else None,
           "cash_mcap": cash / mc * 100 if cash is not None and mc else None,
           "interest_rev": interest / rev * 100 if interest is not None and rev else None}
    for k, lim in LIMITS.items():
        v = out[k]
        out[k + "_check"] = "not reported" if v is None else "FAIL" if v >= lim else "borderline" if v >= lim - BORDER else "pass"
    return out


def _fmt(x):
    return "n/a" if x is None else f"{x:.1f}%"


# ---------- the decision ----------
def check_halal(ticker, log=print):
    today = str(date.today())
    res = {"status": "unchecked", "source": "Osooli (Yaaqen, Zoya, Musaffa)", "checked": None, "reasons": [],
           "last_known": last_known(ticker), "notes": "", "login_expired": False, "firecrawl_session_seconds": None}
    t0 = time.time()
    try:
        if not _key():
            res["notes"] = "Halal not checked: FIRECRAWL_API_KEY missing."
            return res
        text, problem = _read_osooli(ticker, log)
        res["firecrawl_session_seconds"] = round(time.time() - t0, 1)
        if problem == "login_expired":
            res["login_expired"] = True
            res["notes"] = "Osooli login expired. Re-login needed, then the check works again."
            return res
        if problem:
            res["notes"] = f"Halal not checked: {problem}."
            return res
        info = parse_osooli(text, ticker)
        if not info:
            res["notes"] = "Halal not checked: Osooli returned no result for this ticker (unknown ticker or the page changed)."
            return res

        raw = info["verdicts_raw"]
        v = {k: _norm(x) for k, x in raw.items()}
        if all(x == "none" for x in v.values()):
            res["notes"] = f"Halal not checked: Osooli has no verdicts for {ticker} (unknown ticker, or not covered)."
            return res
        res["verdicts"] = {k: (LABEL[v[k]] if v[k] != "doubtful" else f"{raw[k]} (doubtful)") for k in raw}
        res["purification_pct"] = info["purification_pct"]
        res["data_date"] = info["data_date"]
        res["checked"] = today
        facts = (f"Osooli ratios: debt / market cap {_fmt(info['debt_mcap'])}, interest income / revenue {_fmt(info['interest_rev'])}."
                 + (" Purification figure is incomplete (interest income not reported)." if info["purification_incomplete"] else ""))

        halal_n = sum(1 for x in v.values() if x == "halal")
        bad_n = sum(1 for x in v.values() if x == "haram")
        if halal_n == 3:
            res["status"], res["notes"] = "compliant", facts
        elif bad_n == 3:
            res["status"], res["notes"] = "not_compliant", facts
        elif v["Zoya"] == "halal" and v["Musaffa"] == "halal":
            # Yaqeen-only rule (halal-screening.md): drop Yaaqen if the ratios it uses pass, else ask a person.
            res["status"] = "compliant"
            try:
                r = own_ratios(ticker)
                ind = (r.get("industry") or "").lower()
                if any(w in ind for w in HARAM_INDUSTRY_WORDS):
                    res["status"] = "doubtful"
                    res["notes"] = f"Yaaqen disagrees and the industry ({r['industry']}) may be a business-activity issue. Decide manually. {facts}"
                else:
                    fails = [k for k in LIMITS if r.get(k + "_check") == "FAIL"]
                    border = [k for k in LIMITS if r.get(k + "_check") == "borderline"]
                    if fails:
                        res["status"] = "doubtful"
                    names = {"debt_mcap": "debt / market cap", "cash_mcap": "cash / market cap", "interest_rev": "interest income / revenue"}
                    res["notes"] = ("Yaaqen dropped (Yaqeen-only rule: Zoya and Musaffa both pass). "
                                    f"Our ratios: debt / market cap {_fmt(r['debt_mcap'])}, cash / market cap {_fmt(r['cash_mcap'])}, "
                                    f"interest income / revenue {_fmt(r['interest_rev'])}."
                                    + (f" FAIL: {', '.join(names[k] for k in fails)}. Decide manually." if fails else "")
                                    + (f" Borderline (within {BORDER} points of the limit): {', '.join(names[k] for k in border)}." if border else "")
                                    + " Screeners may use a longer-average market cap.")
            except Exception as ex:  # noqa: BLE001
                res["status"] = "doubtful"
                res["notes"] = f"Yaaqen disagrees and our own ratio check failed ({type(ex).__name__}). Decide manually. {facts}"
        else:
            res["status"] = "doubtful"
            res["notes"] = f"Zoya or Musaffa did not say Halal. Decide manually. {facts}"

        # reasons from the odd screener's public page (only when there is a disagreement)
        if halal_n != 3 and bad_n != 3:
            odd = [k for k in v if v[k] != "halal"][:2]
            for k in odd:
                if k == "Yaaqen":  # its public page shows the verdict only; the ratios are behind its login
                    why = None
                    locked = "Yaaqen does not publish its reason (the ratios sit behind its login). Our own ratios are in the notes."
                else:
                    why = _public_reason(k, ticker, log)
                    locked = "reason not public (details are locked behind the screener's login or paywall)"
                res["reasons"].append({"screener": k, "reason": why or locked})
    except Exception as ex:  # noqa: BLE001 - the contract is: never raise
        res["status"] = "unchecked"
        res["notes"] = f"Halal not checked: {type(ex).__name__}: {str(ex)[:100]}"
    finally:
        res["firecrawl_session_seconds"] = res.get("firecrawl_session_seconds") or round(time.time() - t0, 1)
    return res
