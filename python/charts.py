"""TradingView-style charts for the Word report, plus an interactive HTML version.

Static (in the .docx), three images, year to date (never shorter than 3 months):
  1) candles + SMA 9 + SMA 20     2) candles + SMA 50 + SMA 200     3) candles (no SMA) + RSI 14 panel underneath
Price axis on the right, red/green candles, like TradingView.

Interactive (separate .html, opens in any browser; Word cannot run code, so it cannot hold a zoomable chart):
  Chart A: candles + SMA 9/20/50/200 (tick boxes) + levels.  Chart B: candles without SMAs, with the RSI under it.
  Built to scroll the page freely: wheel and one-finger vertical drags scroll the PAGE; zoom with the +/- buttons,
  Ctrl + wheel, or pinch. Uses TradingView's open source Lightweight Charts from a CDN.
Indicators are computed on 2 years, then cropped, so the 200-day line is complete.
"""
import json
import os
from datetime import date, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

# TradingView light theme. SMA colors chosen to stay clear of the red/green candles and each other.
UP, DOWN = "#089981", "#F23645"
SMA_COLORS = {9: "#2962FF", 20: "#FF9800", 50: "#8E24AA", 200: "#212121"}
SMA_WIDTH = {9: 1.3, 20: 1.3, 50: 1.5, 200: 2.0}
RSI_COLOR = "#7E57C2"
GRID, TEXT, BG = "#E0E3EB", "#131722", "#FFFFFF"


def frame(snap):
    h = snap["technicals"]["history"]
    df = pd.DataFrame({"open": h["open"], "high": h["high"], "low": h["low"], "close": h["close"]},
                      index=pd.to_datetime(h["dates"])).astype("float64").dropna()
    return df[~df.index.duplicated()]


def start_of_view(last):
    jan1 = pd.Timestamp(date(last.year, 1, 1))
    return min(jan1, last - timedelta(days=92))


def rsi_series(close, n=14):
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def _month_ticks(idx):
    pos, lab, seen = [], [], set()
    for i, ts in enumerate(idx):
        key = (ts.year, ts.month)
        if key not in seen:
            seen.add(key)
            pos.append(i)
            lab.append(ts.strftime("%b") if ts.month != 1 else str(ts.year))
    return pos, lab


def _candles(ax, view):
    for i, (o, h, l, c) in enumerate(zip(view["open"], view["high"], view["low"], view["close"])):
        col = UP if c >= o else DOWN
        ax.vlines(i, l, h, color=col, linewidth=0.8, zorder=2)
        body = max(abs(c - o), (h - l) * 0.002 or 0.01)
        ax.add_patch(Rectangle((i - 0.35, min(o, c)), 0.7, body, facecolor=col, edgecolor=col, linewidth=0.5, zorder=3))


def _right_axis(ax):
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.grid(True, color=GRID, linewidth=0.7)
    for side in ("top", "left"):
        ax.spines[side].set_visible(False)
    for side in ("right", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=8, length=0)


def _tag(ax, y, text, color):
    """Price tag on the right axis, like TradingView's last-price label."""
    ax.annotate(text, xy=(1.0, y), xycoords=("axes fraction", "data"), xytext=(3, 0), textcoords="offset points",
                fontsize=7.5, color="white", va="center", ha="left", zorder=10, annotation_clip=False,
                bbox=dict(boxstyle="square,pad=0.25", fc=color, ec=color))


def _figure(snap, plan, smas, title, path, with_rsi=False):
    df = frame(snap)
    start = start_of_view(df.index[-1])
    view = df[df.index >= start]
    full_close = df["close"]
    n = len(view)
    if with_rsi:
        fig, (ax, axr) = plt.subplots(2, 1, figsize=(8, 5.6), dpi=150, sharex=True, facecolor=BG,
                                      gridspec_kw={"height_ratios": [3.2, 1], "hspace": 0.04})
    else:
        fig, ax = plt.subplots(1, 1, figsize=(8, 4.4), dpi=150, facecolor=BG)
        axr = None
    ax.set_facecolor(BG)
    _candles(ax, view)
    lo, hi = float(view["low"].min()), float(view["high"].max())

    legend = []
    for k in smas:
        sm = full_close.rolling(k).mean()[view.index]
        ax.plot(range(n), sm.values, color=SMA_COLORS[k], linewidth=SMA_WIDTH[k], zorder=4)
        if sm.notna().any():
            legend.append((f"SMA {k}  {sm.dropna().iloc[-1]:,.2f}", SMA_COLORS[k]))
            lo, hi = min(lo, float(sm.min())), max(hi, float(sm.max()))

    rg = snap["technicals"].get("range") or {}
    if rg.get("floor") and not rg.get("broken") and rg["floor"][0] >= lo * 0.80:
        f0, f1 = rg["floor"]
        ax.axhspan(f0, f1, color="#787B86", alpha=0.16, zorder=1)
        ax.axhline(f0, color="#787B86", linestyle="--", linewidth=0.9, zorder=1)
        ax.text(0.01, f1, f" Floor {f0:,.2f}-{f1:,.2f}", transform=ax.get_yaxis_transform(), fontsize=7.5,
                color="#555B6E", va="bottom", zorder=6, bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none", alpha=0.85))
        lo = min(lo, f0)
    if plan and plan.get("position") == "holding":
        ax.axhline(plan["entry"], color="#2962FF", linestyle=":", linewidth=1.2, zorder=1)
        _tag(ax, plan["entry"], f"{plan['entry']:,.2f}", "#2962FF")
        lo, hi = min(lo, plan["entry"]), max(hi, plan["entry"])
    pad = (hi - lo) * 0.05 or 1
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, n + 2)
    last = float(view["close"].iloc[-1])
    _tag(ax, last, f"{last:,.2f}", UP if last >= float(view["open"].iloc[-1]) else DOWN)

    ax.text(0.01, 0.985, title, transform=ax.transAxes, fontsize=10, fontweight="bold", color=TEXT, va="top")
    x = 0.01
    for text, col in legend:
        ax.text(x, 0.915, text, transform=ax.transAxes, fontsize=8, color=col, va="top", fontweight="bold")
        x += 0.21
    _right_axis(ax)

    pos, lab = _month_ticks(view.index)
    if axr is not None:
        plt.setp(ax.get_xticklabels(), visible=False)
        rsi = rsi_series(full_close)[view.index]
        axr.set_facecolor(BG)
        axr.axhspan(30, 70, color=RSI_COLOR, alpha=0.08)
        axr.axhline(70, color="#787B86", linestyle="--", linewidth=0.8)
        axr.axhline(30, color="#787B86", linestyle="--", linewidth=0.8)
        axr.axhline(50, color="#B2B5BE", linestyle=":", linewidth=0.6)
        axr.plot(range(n), rsi.values, color=RSI_COLOR, linewidth=1.3)
        axr.set_ylim(0, 100)
        axr.set_yticks([30, 50, 70])
        _tag(axr, float(rsi.iloc[-1]), f"{rsi.iloc[-1]:.1f}", RSI_COLOR)
        axr.text(0.01, 0.93, f"RSI 14  {rsi.iloc[-1]:.2f}", transform=axr.transAxes, fontsize=8, color=RSI_COLOR,
                 va="top", fontweight="bold")
        _right_axis(axr)
        axr.set_xticks(pos)
        axr.set_xticklabels(lab)
        fig.subplots_adjust(left=0.02, right=0.93, top=0.98, bottom=0.06)
    else:
        ax.set_xticks(pos)
        ax.set_xticklabels(lab)
        fig.subplots_adjust(left=0.02, right=0.93, top=0.98, bottom=0.07)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


def make_charts(snap, plan, outdir):
    """Return ({name: path or None}, errors). Each chart is independent."""
    os.makedirs(outdir, exist_ok=True)
    t = snap["ticker"]
    jobs = {
        "sma_short": lambda: _figure(snap, plan, [9, 20], f"{t}  Daily  |  SMA 9 and SMA 20",
                                     os.path.join(outdir, f"{t}-chart-sma-9-20.png")),
        "sma_long": lambda: _figure(snap, plan, [50, 200], f"{t}  Daily  |  SMA 50 and SMA 200",
                                    os.path.join(outdir, f"{t}-chart-sma-50-200.png")),
        "rsi": lambda: _figure(snap, plan, [], f"{t}  Daily  |  RSI 14",
                               os.path.join(outdir, f"{t}-chart-rsi.png"), with_rsi=True),
    }
    out, errors = {}, []
    for name, fn in jobs.items():
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            out[name] = None
            errors.append(f"chart {name}: {type(e).__name__}: {str(e)[:100]}")
    try:
        out["interactive"] = make_interactive(snap, plan, os.path.join(outdir, f"{t}-chart.html"))
    except Exception as e:  # noqa: BLE001
        out["interactive"] = None
        errors.append(f"interactive chart: {type(e).__name__}: {str(e)[:100]}")
    return out, errors


# ---------- interactive HTML ----------
def make_interactive(snap, plan, path):
    df = frame(snap)
    close = df["close"]
    start = start_of_view(df.index[-1])
    dates = [d.strftime("%Y-%m-%d") for d in df.index]

    def line(series):
        return [{"time": d, "value": round(float(v), 4)} for d, v in zip(dates, series) if v == v]

    rg = snap["technicals"].get("range") or {}
    data = {
        "ticker": snap["ticker"], "start": start.strftime("%Y-%m-%d"), "end": dates[-1],
        "candles": [{"time": d, "open": o, "high": h, "low": l, "close": c}
                    for d, o, h, l, c in zip(dates, df["open"], df["high"], df["low"], df["close"])],
        "sma": {str(k): line(close.rolling(k).mean()) for k in (9, 20, 50, 200)},
        "colors": {str(k): v for k, v in SMA_COLORS.items()},
        "rsi": line(rsi_series(close)),
        "floor": rg.get("floor") if not rg.get("broken") else None,
        "entry": plan.get("entry") if plan.get("position") == "holding" else None,
        "stop": plan.get("stop"), "target": plan.get("target"),
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(HTML.replace("__DATA__", json.dumps(data)))
    return path


HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chart</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{box-sizing:border-box}
body{margin:0;padding:10px 0 48px;font:14px -apple-system,Segoe UI,Roboto,sans-serif;background:#f4f5f7;color:#131722}
header{padding:0 12px 8px} h1{font-size:18px;margin:0 0 4px}
.hint{color:#6b7280;font-size:12.5px;line-height:1.5;margin:0}
.card{background:#fff;border-top:1px solid #e0e3eb;border-bottom:1px solid #e0e3eb;padding:10px 6px;margin:0 0 26px}
.card:fullscreen{overflow:auto;padding:10px 8px}
.card h2{font-size:14px;margin:0 6px 8px}
.controls{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin:0 6px 8px}
.controls label{display:flex;align-items:center;gap:5px;font-weight:600;cursor:pointer}
button{font:inherit;font-weight:600;padding:5px 11px;border:1px solid #c9ccd6;background:#fff;border-radius:6px;cursor:pointer}
button:active{background:#eef0f5}
.chart{width:100%;touch-action:pan-y}
#a{height:72vh;min-height:420px} #b{height:46vh;min-height:300px} #r{height:22vh;min-height:150px;margin-top:6px}
.card:fullscreen #a{height:calc(100vh - 130px)} .card:fullscreen #b{height:calc(100vh - 290px)} .card:fullscreen #r{height:150px}
</style></head><body>
<header><h1 id="t"></h1>
<p class="hint">TradingView-style: mouse wheel zooms, drag moves through time, drag the price or time axis to stretch it, double-click an axis to reset.
On a phone: pinch to zoom, one finger drags sideways, swipe up or down scrolls the page. Each chart moves on its own. Research only, not advice.</p></header>

<div class="card" id="cardA"><h2>Price with moving averages and levels</h2>
<div class="controls"><span id="boxes" style="display:contents"></span>
<label><input type="checkbox" id="lv" checked>Levels</label></div>
<div class="controls"><button data-c="A" data-z="in">+</button><button data-c="A" data-z="out">-</button>
<button data-c="A" data-r="ytd">YTD</button><button data-c="A" data-r="m3">3M</button><button data-c="A" data-r="all">All</button>
<button class="wheel">Wheel: zoom</button><button class="fs" data-card="cardA">Full screen</button></div>
<div id="a" class="chart"></div></div>

<div class="card" id="cardB"><h2>Candles with RSI 14</h2>
<div class="controls"><button data-c="B" data-z="in">+</button><button data-c="B" data-z="out">-</button>
<button data-c="B" data-r="ytd">YTD</button><button data-c="B" data-r="m3">3M</button><button data-c="B" data-r="all">All</button>
<button class="wheel">Wheel: zoom</button><button class="fs" data-card="cardB">Full screen</button></div>
<div id="b" class="chart"></div><div id="r" class="chart"></div></div>

<script>
const D = __DATA__;
document.getElementById('t').textContent = D.ticker + ' Daily';
const LC = LightweightCharts;
const opts = wheelZoom => ({autoSize:true,
  layout:{background:{color:'#ffffff'},textColor:'#131722'},grid:{vertLines:{color:'#e0e3eb'},horzLines:{color:'#e0e3eb'}},
  rightPriceScale:{borderColor:'#e0e3eb',minimumWidth:70},timeScale:{borderColor:'#e0e3eb',rightOffset:4},crosshair:{mode:0},
  handleScroll:{mouseWheel:false,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
  handleScale:{mouseWheel:wheelZoom,pinch:true,axisPressedMouseMove:true,axisDoubleClickReset:true}});
const mk = id => LC.createChart(document.getElementById(id), opts(true));
const A = mk('a'), B = mk('b'), R = mk('r');
const charts = {A, B};
const candleOpts = {upColor:'#089981',downColor:'#F23645',borderUpColor:'#089981',borderDownColor:'#F23645',wickUpColor:'#089981',wickDownColor:'#F23645'};
const cA = A.addCandlestickSeries(candleOpts); cA.setData(D.candles);
const cB = B.addCandlestickSeries(candleOpts); cB.setData(D.candles);

const widths = {9:1,20:1,50:2,200:3}, smaS = {};
for (const k of ['9','20','50','200']) {
  smaS[k] = A.addLineSeries({color:D.colors[k],lineWidth:widths[k],priceLineVisible:false,lastValueVisible:true,title:'SMA '+k});
  smaS[k].setData(D.sma[k]);
  const l = document.createElement('label'); l.style.color = D.colors[k];
  l.innerHTML = '<input type="checkbox" checked data-k="'+k+'">SMA '+k;
  document.getElementById('boxes').appendChild(l);
}
document.getElementById('boxes').addEventListener('change', e => { const k = e.target.dataset.k; if (k) smaS[k].applyOptions({visible:e.target.checked}); });

const lines = [];
function level(p, color, title, style) { if (p != null) lines.push(cA.createPriceLine({price:p,color:color,lineWidth:1,lineStyle:style,axisLabelVisible:true,title:title})); }
function drawLevels() { if (D.floor) { level(D.floor[0],'#787B86','Floor bottom',2); level(D.floor[1],'#787B86','Floor top',2); }
  level(D.entry,'#2962FF','Entry',1); level(D.stop,'#F23645','Stop',2); level(D.target,'#089981','Goal',2); }
drawLevels();
document.getElementById('lv').addEventListener('change', e => { lines.forEach(l => cA.removePriceLine(l)); lines.length = 0; if (e.target.checked) drawLevels(); });

const rs = R.addLineSeries({color:'#7E57C2',lineWidth:2,priceLineVisible:false,title:'RSI 14',
  autoscaleInfoProvider:() => ({priceRange:{minValue:0,maxValue:100}})});
rs.setData(D.rsi);
rs.createPriceLine({price:70,color:'#787B86',lineWidth:1,lineStyle:2,axisLabelVisible:false});
rs.createPriceLine({price:30,color:'#787B86',lineWidth:1,lineStyle:2,axisLabelVisible:false});

// Chart A moves alone. Chart B and its RSI strip move together (they are one chart).
function pair(from, to) { from.timeScale().subscribeVisibleLogicalRangeChange(r => {
  if (!r) return; const o = to.timeScale().getVisibleLogicalRange();
  if (o && Math.abs(o.from - r.from) < 0.01 && Math.abs(o.to - r.to) < 0.01) return;  // already equal: stop the echo
  to.timeScale().setVisibleLogicalRange(r); }); }
pair(B, R); pair(R, B);

function setRange(c, from){ c.timeScale().setVisibleRange({from:from,to:D.end}); }
function zoom(c, f){ const ts = c.timeScale(), r = ts.getVisibleLogicalRange(); if (!r) return;
  const right = r.to, span = (r.to - r.from) * f; ts.setVisibleLogicalRange({from:right-span,to:right}); }
document.querySelectorAll('[data-z]').forEach(b => b.onclick = () => zoom(charts[b.dataset.c], b.dataset.z === 'in' ? 0.6 : 1.6));
document.querySelectorAll('[data-r]').forEach(b => b.onclick = () => { const c = charts[b.dataset.c], k = b.dataset.r;
  if (k === 'ytd') setRange(c, D.start);
  else if (k === 'm3') { const d = new Date(D.end); d.setDate(d.getDate()-92); setRange(c, d.toISOString().slice(0,10)); }
  else c.timeScale().fitContent(); });
// Wheel: zoom (like TradingView) or scroll the page, per card
document.querySelectorAll('.wheel').forEach(b => b.onclick = () => {
  const zoomOn = b.textContent.includes('scroll'); b.textContent = zoomOn ? 'Wheel: zoom' : 'Wheel: scroll page';
  const cs = b.closest('.card').id === 'cardA' ? [A] : [B, R];
  cs.forEach(c => c.applyOptions({handleScale:{mouseWheel:zoomOn}})); });
// Full screen per card
document.querySelectorAll('.fs').forEach(b => b.onclick = () => { const el = document.getElementById(b.dataset.card);
  if (document.fullscreenElement) document.exitFullscreen(); else if (el.requestFullscreen) el.requestFullscreen(); });
// start at year to date, after the first layout (the charts size themselves)
function start() { setRange(A, D.start); setRange(B, D.start); }
requestAnimationFrame(() => setTimeout(start, 150)); setTimeout(start, 700);
</script></body></html>"""
