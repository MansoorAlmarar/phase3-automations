# phase3-automations

Scheduled and on-demand automations that run unattended on [Trigger.dev](https://trigger.dev). Two agents live here: a **stock research report** and a **food deals alert**. Python does the data work, TypeScript tasks schedule it and deliver the result to Telegram.

## Stock research report

Send `/report TICKER [note]` to a Telegram bot (or trigger the task over HTTP) and get back a Word report plus an interactive chart.

What one run does (`python/build_report.py`):
1. **Market data and indicators** from FMP, Finnhub and yfinance. Floor and bounce detection, trade-plan numbers (goal, stop, reward-to-risk, ladder). Gaps are recorded as errors, never raised.
2. **Halal screen**: a logged-in browser session reads a Halal screener, and anything unclear is marked `doubtful`, never `compliant`.
3. **AI notes**: an LLM (OpenRouter) writes short notes from the numbers and headlines. Its output is validated before use: ids must be real, non-text values are coerced, and a bad reply drops one note, not the whole report.
4. **Charts and report**: three charts, an interactive lightweight-charts HTML file, and a `.docx`.
5. **Delivery**: the task sends the files to Telegram, with an idempotency key (ticker plus date) so a double tap sends one report.

Every step is caught and logged, so a failing source shows up as a warning in the report instead of a failed run.

Research tool only. It does not give buy or sell calls and never places orders.

## Food deals alert

A daily schedule (13:00 Dubai) scrapes the Talabat menu page of each watchlist branch, reads the embedded menu data, computes each item's discount from price versus old price, and sends a Telegram alert only when a branch has a whole-menu discount (at least 60% of priced items at 40% off or more). No LLM involved.

## Layout

| Path | What |
|---|---|
| `src/trigger/stock-report.ts` | On-demand report task, Telegram delivery |
| `src/trigger/stock-report-weekly.ts` | Weekly schedule (ticker list is currently empty, so it is paused) |
| `src/trigger/food-deals-daily.ts` | Daily deals schedule |
| `python/stock_core.py` | Data layer, indicators, trade plan, note parsing |
| `python/halal_check.py` | Halal gate |
| `python/ai_notes.py` | Validated LLM notes |
| `python/charts.py`, `docx_report.py`, `build_report.py` | Charts, Word report, orchestration |
| `tests/python/` | 73 offline tests (fixtures only, no network) |
| `REVIEW.md` | Findings from a read-first review, with the fixes and open judgment calls |

## Run the tests

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/python -q
```

## Configuration

Copy `.env.example` and fill in the keys (Trigger.dev, Firecrawl, Telegram, FMP, Finnhub, OpenRouter). Keys are read from the environment and never printed. In production they are Trigger.dev environment variables.

```
npm install
npx trigger.dev@latest dev      # local
npx trigger.dev@latest deploy   # production
```
