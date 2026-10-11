"""Phase 0 test for the cloud stock report: can Python run inside a Trigger.dev task?

Checks, in order: libraries import, yfinance can fetch prices from this machine, a chart can be drawn,
a .docx can be built. Prints one JSON line. Never raises: every check reports ok or an error string.
"""
import json
import os
import sys
import tempfile
import time

result = {"python": sys.version.split()[0], "checks": {}}


def check(name, fn):
    t0 = time.time()
    try:
        detail = fn()
        result["checks"][name] = {"ok": True, "detail": detail, "s": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001 - report everything, crash nothing
        result["checks"][name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "s": round(time.time() - t0, 1)}


ticker = sys.argv[1] if len(sys.argv) > 1 else "ARLO"
closes = {}


def libs():
    import docx  # noqa: F401
    import matplotlib
    import requests  # noqa: F401
    import yfinance
    return f"matplotlib {matplotlib.__version__}, yfinance {yfinance.__version__}"


def prices():
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period="2y", auto_adjust=False)
    if hist.empty:
        raise RuntimeError("no price rows returned")
    closes["s"] = hist["Close"]
    return f"{len(hist)} daily rows, last close {float(hist['Close'].iloc[-1]):.2f}"


def chart():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = closes.get("s")
    if s is None:
        raise RuntimeError("skipped: no prices")
    sma20 = s.rolling(20).mean()
    sma200 = s.rolling(200).mean()
    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=120)
    ax.plot(s.index[-180:], s.iloc[-180:], label="Close")
    ax.plot(sma20.index[-180:], sma20.iloc[-180:], label="SMA 20")
    ax.plot(sma200.index[-180:], sma200.iloc[-180:], label="SMA 200")
    ax.legend()
    path = os.path.join(tempfile.gettempdir(), f"{ticker}-test.png")
    fig.savefig(path)
    plt.close(fig)
    closes["png"] = path
    return f"{os.path.getsize(path)} bytes"


def word():
    from docx import Document
    from docx.shared import Inches
    d = Document()
    d.add_heading(f"{ticker} Python test", level=1)
    d.add_paragraph("If you can read this, Python and python-docx work inside Trigger.dev.")
    if closes.get("png"):
        d.add_picture(closes["png"], width=Inches(6))
    path = os.path.join(tempfile.gettempdir(), f"{ticker}-test.docx")
    d.save(path)
    return f"{os.path.getsize(path)} bytes"


check("libs", libs)
check("prices", prices)
check("chart", chart)
check("docx", word)
result["all_ok"] = all(c["ok"] for c in result["checks"].values())
print("RESULT_JSON " + json.dumps(result))
