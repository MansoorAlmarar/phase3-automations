"""Entry point for the cloud stock report (phase 1: data, charts, .docx).

Usage: python build_report.py TICKER [--note "holding at 14.20"] [--out DIR] [--halal halal.json] [--notes notes.json]

--halal  JSON from the Halal step (phase 2). Missing: report says "not checked".
--notes  JSON from the AI step (phase 3). Missing: report uses rule-based text only.
Prints one line "RESULT_JSON {...}". Never raises: every failure is reported in the JSON.
Keys (FMP_API_KEY, FINNHUB_API_KEY) are read from the environment. Locally, a .env next to the repo is loaded.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
import traceback
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_dotenv_light(path):
    """Tiny .env reader for local runs; in Trigger.dev the variables are already in the environment."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def read_json(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def caption(snap, plan, halal):
    t = snap["technicals"]
    rg = t.get("range") or {}
    nx = (snap.get("earnings") or {}).get("next") or {}
    status = halal.get("status", "unchecked")
    hl = {"compliant": "Halal", "not_compliant": "NOT Halal", "doubtful": "Doubtful, decide manually"}.get(status, "Halal not checked")
    if halal.get("purification_pct") is not None:
        hl += f", purification {halal['purification_pct']}%"
    floor = ("floor broken" if rg.get("broken") else
             f"{rg.get('now')} ({rg['floor'][0]}-{rg['floor'][1]})" if rg.get("floor") else "no clear floor")
    rr = f"{plan['reward_risk']} : 1" if plan.get("reward_risk") else "n/a"
    stop = f"stop {plan['stop']} ({plan['loss_pct']}%)" if plan.get("stop") else "no stop level"
    earn = f"earnings {nx['date']} ({nx['days_away']}d)" if nx.get("date") else "no earnings date"
    return "\n".join([f"{snap['ticker']} {plan['price']} | {hl}", f"Floor: {floor}",
                      f"Reward : risk {rr}, {stop}", f"Next: {earn}",
                      f"RSI {t.get('rsi14')} | SMA200 {t.get('sma200')}"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "stock-report"))
    ap.add_argument("--halal")
    ap.add_argument("--no-ai", action="store_true", help="skip the AI notes")
    ap.add_argument("--no-web", action="store_true", help="AI notes without the 2 Firecrawl web searches")
    ap.add_argument("--no-halal", action="store_true", help="skip the Osooli check (saves Firecrawl credits)")
    ap.add_argument("--notes")
    a = ap.parse_args()

    load_dotenv_light(os.path.join(HERE, "..", ".env"))
    result = {"ok": False, "ticker": None, "steps": {}}
    t0 = time.time()
    try:
        ticker = a.ticker.strip().upper()
        if not re.fullmatch(r"[A-Z]{1,6}", ticker):
            result["error"] = "bad ticker"
            return result
        result["ticker"] = ticker
        note = a.note.strip()[:300]

        import charts
        import docx_report
        import ai_notes
        import halal_check
        import stock_core

        pos = stock_core.parse_note(note, ticker)
        result["steps"]["note"] = {k: pos[k] for k in ("position", "entry", "shares", "peers", "source")}

        snap = stock_core.build_snapshot(ticker, user_peers=pos["peers"])
        result["steps"]["snapshot"] = {"errors": snap["errors"], "rows": len((snap["technicals"].get("history") or {}).get("close", []))}
        if not snap["technicals"].get("history"):
            result["error"] = "no price data"
            return result

        plan = stock_core.build_plan(snap, pos)
        if plan.get("error"):
            result["error"] = plan["error"]
            return result

        os.makedirs(a.out, exist_ok=True)
        chart_paths, chart_errors = charts.make_charts(snap, plan, a.out)
        result["steps"]["charts"] = {"made": [k for k, v in chart_paths.items() if v], "errors": chart_errors}

        halal = read_json(a.halal)
        if halal is None:
            if a.no_halal:
                halal = {"status": "unchecked", "notes": "Halal check skipped for this run.",
                         "last_known": halal_check.last_known(ticker)}
            else:
                halal = halal_check.check_halal(ticker, log=lambda m: print(m, file=sys.stderr))
        result["steps"]["halal"] = {k: halal.get(k) for k in ("status", "login_expired", "firecrawl_session_seconds")}
        notes = read_json(a.notes)
        if notes is None and not a.no_ai:
            notes = ai_notes.make_notes(snap, plan, halal, use_web=not a.no_web, log=lambda m: print(m, file=sys.stderr))
        result["steps"]["ai"] = ({"ok": False, "error": notes.get("_error")} if notes and notes.get("_error") else
                                 {"ok": bool(notes), "model": (notes or {}).get("model"), "dropped": (notes or {}).get("_dropped"),
                                  "seconds": (notes or {}).get("_seconds"), "web_items": (notes or {}).get("_web_items")})
        path = os.path.join(a.out, f"{ticker}-{date.today()}.docx")
        docx_report.build_docx(snap, plan, halal, notes, chart_paths, path, note_text=note, note_warning=pos["warning"])
        result.update({"ok": True, "docx": path, "html": chart_paths.get("interactive"), "halal": halal, "bytes": os.path.getsize(path), "caption": caption(snap, plan, halal),
                       "plan": {k: plan.get(k) for k in ("position", "entry", "stop", "loss_pct", "reward_risk")}})
    except Exception as e:  # noqa: BLE001 - the contract is: never raise
        result["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        result["trace"] = traceback.format_exc()[-600:]
    finally:
        result["seconds"] = round(time.time() - t0, 1)
    return result


if __name__ == "__main__":
    out = main()
    print("RESULT_JSON " + json.dumps(out, default=str))
