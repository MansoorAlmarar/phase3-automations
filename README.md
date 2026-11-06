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
| `tests/python/` | Offline tests, including the eval below (fixtures only, no network) |
| `evals/ai_notes/` | Guardrail eval for the AI notes: 28 labelled model replies, runner, report |
| `REVIEW.md` | Findings from a read-first review, with the fixes and open judgment calls |

## Eval: do the AI-note guardrails hold?

An LLM writes notes into a report that a person reads and may act on, so the code between the model and the page matters more than the model. `evals/ai_notes/` scores that code. It has 28 labelled model replies (`cases.json`): 22 bad ones and 6 good ones. Each bad reply tries one thing: advice wording ("consider buying"), an invented number, a Halal comment, an echoed prompt injection, or a broken shape (a string where a list belongs, a negative id). Each good reply must come through intact. The model call is replaced by the canned reply, so the run is offline, free and repeatable.

```
python evals/ai_notes/run.py --check
```

Result: 22 of 22 bad replies handled, 6 of 6 good replies kept (`evals/ai_notes/results/latest.md`). The first run scored 18 of 22 and found three real gaps, fixed in the same change: the Halal-comment rule was in the prompt but not enforced in code, a `news` field returned as an object threw away every note, and `change_conditions` returned as a string was split into single letters. The cases run as part of `pytest`, so CI fails if a guardrail regresses.

What it does not measure: how good the model's own notes are. It tests the checks around the model, not the model.

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
