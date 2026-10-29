# Review: phase3-automations

Read-first review of the cloud stock report (Trigger.dev tasks in `src/trigger/`, Python in `python/`).
Baseline: `main` at `8dcab18`. Line numbers below refer to that commit. This branch sits on `main` at `c86c7dc`, which pauses the weekly ARLO task (the list is now empty) and changes two chart line widths. Both were read; neither has an issue. Severities: **High** (wrong numbers or a silent failure a reader would act on), **Medium** (a visible failure or a risk to the repo), **Low** (hygiene), **Judgment** (a decision for Mansoor, not changed here).

Checks run: `npm test` (73 Python tests, offline, fixtures only), and a TypeScript typecheck of `src/trigger/*.ts` with the locked dependencies (zero errors). Each regression test was also run against `HEAD`: 17 failed and 19 errored there, so the tests do catch these bugs.

## Fixed in this PR

| # | Sev | Where (at `HEAD`) | Finding | Fix |
|---|---|---|---|---|
| 1 | High | `python/stock_core.py:108-111` | A bare share count was read as the entry price. "holding 2 shares" became entry 2.0, so profit, loss and stop were all wrong, with no warning. | A price now needs a marker (`at`, `@`, `entry` or `$`). Otherwise the note is treated as watching, with a warning. Tests: `tests/python/test_parse_note.py`. |
| 2 | High | `python/stock_core.py:355` | A floor the price had broken and not recovered from was filtered out, so the report said "no clear floor". The "Floor broken" flag and the broken-floor text never appeared. | Floors under the price are preferred. A floor above the price is only used when the price has broken it. Otherwise it is reported as no clear floor. Test: `test_broken_floor_is_reported_as_broken`. |
| 3 | High | `python/stock_core.py:67-73` | A malformed `HOLDINGS_JSON` was silently ignored, so a real holding was reported as "watching". | `holdings_for` returns a problem, which is shown as a warning on the note. Test: `test_broken_holdings_file_is_a_visible_warning`. |
| 4 | Medium | `python/stock_core.py:96-99`, `:116-117` | A zero share lot (`holding 0 @ 12`, or the same in `HOLDINGS_JSON`) raised `ZeroDivisionError` and failed the run. | Zero-share lots are ignored. Tests: `test_zero_share_lot_*`. |
| 5 | Medium | `src/trigger/stock-report.ts:124` | The note was passed as a separate argv token. A note starting with `-` made argparse fail, so no report was built. | Passed as `--note=<note>`, one token. |
| 6 | Medium | `python/halal_check.py:186-187` | `_public_reason` caught only `RequestException`. A non-JSON reply raised out of `check_halal` and replaced a valid Osooli verdict with "unchecked". | Any error in the optional public reason is logged and skipped. Test: `test_bad_public_page_does_not_discard_the_osooli_verdict`. |
| 7 | Medium | `python/ai_notes.py:243`, `:254`, `:261` | A negative AI id indexed from the end (the wrong headline). A non-object item raised and dropped all AI notes. | Ids must be real list indexes. Non-object items are skipped. Tests in `test_ai_notes.py`. |
| 8 | Medium | `python/ai_notes.py:199`, `:232`, `:261` | A number where the model should write text (`"pe": 18.4`) raised and dropped all AI notes. | Non-text values are converted to text before checking. |
| 9 | Medium | `python/ai_notes.py:212` | With no `OPENROUTER_API_KEY`, notes returned `None` with no reason, so the Telegram warning read "AI notes unavailable: " with nothing after it. | Returns a reason, `OPENROUTER_API_KEY missing`. |
| 10 | Medium | `python/ai_notes.py:73-80` | A bad JSON or shape reply from the web search dropped all AI notes. | Caught; the search is skipped and the notes continue. |
| 11 | Medium | `.gitignore:1` | The file began with a UTF-8 byte-order mark, so the first pattern was `﻿node_modules/` and never matched. `node_modules/` could be committed. | BOM removed. `.pytest_cache/` added. |
| 12 | Medium | `python/build_report.py:96-99` | A report built from 30 daily bars showed floor, RSI and SMA blanks. | Fails with `not enough price history (N daily bars, need 60)`. Missing price history still fails with `no price data`. |
| 13 | Medium | `python/stock_core.py:429` | `int(NaN)` on a volume with no value raised, which dropped the whole price history and reported "no price data". | Missing volume is `null`. |
| 14 | Medium | `python/docx_report.py:233`, `:240`, `:455` | Wording told the reader what to do: "Do not use this plan", "Set one manually". Those read as trading instructions. | Changed to descriptions of what the data shows. |
| 15 | Medium | `python/docx_report.py:351-353` | "If you were entering today at" and "For a new entry today at". | Now "At today's price". The numbers are unchanged (see Judgment J1). |
| 16 | Medium | `python/ai_notes.py:26` | The advice filter did not cover "hold", "target", "trim", "exit" or "take profit". | Added. |
| 17 | Medium | `python/docx_report.py:516-522`, `:399` | Blank cells where AI notes were off, and a blank company name and sector in the header. | Show `n/a`, and "company name not available" / "sector not available". Market cap now has a note. |
| 18 | Medium | `src/trigger/stock-report.ts:103` | `onFailure` was typed so the payload was `void`. The typecheck failed at `HEAD` too. | Payload typed. The typecheck is now clean. |
| 19 | Low | `src/trigger/stock-report.ts:165`, `:182` | `photos` was a constant `0` that was always reported. | Removed. |
| 20 | Low | `.env.example` | `FMP_API_KEY`, `FINNHUB_API_KEY`, `OPENROUTER_API_KEY`, `OPENROUTER_MODELS`, `OSOOLI_PROFILE` and `HOLDINGS_JSON` were missing. | Added with empty values. No secrets. |
| 21 | Low | `package.json:7` | `npm test` was a placeholder that always failed. | Runs `pytest tests/python`. `requirements-dev.txt` adds pytest only; the deployed `requirements.txt` is unchanged. |
| 22 | Low | `python/charts.py:245` | The interactive chart's inline JSON was not escaped for `</script>`. The only inputs are validated, so this is defence in depth. | `<` is escaped. Test: `test_values_cannot_close_the_script_tag`. |
| 23 | Low | `python/stock_core.py:398` (`technicals`) | Price fetch and indicator maths were one function, so the maths could not be tested offline. | Split into `technicals_from_history(full)`, which is pure. |

## Left for a decision (Judgment)

**J1. Price levels that read like targets or entry levels.** The numbers come from Mansoor's own rules (+5% goal, 1.5% stop buffer, ladder), not from any advice in the code. But in a research report, "Goal", "Profit goal", "Distance to goal" (`python/charts.py:326`, `python/docx_report.py:452-466`) and "Entry price where reward : risk is 1 : 1" (`python/docx_report.py:357`) read close to a price target and an entry level. I changed the surrounding wording (fix 14 and 15) but kept the numbers and labels. Mansoor should decide whether to rename them (for example "Rule level +5%") or drop the 1:1 entry line.

**J2. Yaqeen-only Halal rule.** `python/halal_check.py:271-291` marks a stock compliant when Zoya and Musaffa both pass and our own ratios pass, dropping Yaaqen. That is a compliance decision. Left as is.

**J3. `halal_cases.json` is personal.** `python/halal_cases.json:9` records trading history ("Mansoor sold and purified all profit in Sept 2026"). The repo is private, so this is acceptable, but it is personal data in git. Consider moving it to the STOIC repo or an env value.

**J4. AI notes go to third-party free models.** `python/ai_notes.py:84-110` (`build_input`) send price levels, ratios, news and Halal verdicts, but no share counts or cost (verified by `test_model_input_has_no_share_counts_or_dollar_cost`). Confirm that sending the rest to free OpenRouter models is acceptable.

**J5. Thresholds I chose.** `MIN_BARS = 60` (`python/build_report.py`, new constant) is my choice. Floor tolerance, break percentage and lookback (`python/stock_core.py:23-25`, `:339`) were kept from the original. Confirm them.

**J6. Dead scaffolding.** `src/trigger/example.ts` is the Trigger.dev hello-world template and is still deployed as task `hello-world`. `python/hello_test.py` with `src/trigger/python-hello-test.ts` is Phase 0. Removing either changes the deployed Trigger project, so I left them.

**J7. Unpinned dependencies.** `requirements.txt:1-4` has no versions. Builds are not reproducible, and `yfinance` depends on unofficial Yahoo endpoints. Pinning after a successful deploy is recommended.

## Left as is (Low or Medium, not changed)

- `src/trigger/stock-report.ts:122`: the temporary report folder is never removed. Trigger containers are ephemeral, so this is low.
- `src/trigger/stock-report.ts:56`: `sendFile` has a `photo` branch that nothing calls.
- `python/stock_core.py:19` and `python/docx_report.py:16`: `HALAL_RECHECK_DAYS` is defined twice.
- `python/ai_notes.py:21`: imports the private `_req` from `halal_check`. A shared module would be cleaner.
- `python/stock_core.py:293`: one news item without `datetime` makes the whole news list fail. The error is recorded, so it is visible in the footer.
- `python/stock_core.py:203-204`: peer profile calls ignore their errors, so a peer can show a blank name.
- `python/charts.py:162-164`: an RSI value of NaN is drawn as "nan".
- `src/trigger/stock-report.ts:93`: the rejected-ticker message echoes up to 12 characters of input into Telegram. Plain text, so no injection, but it is raw input.
- `trigger.config.ts:28`: `devPythonBinaryPath` is a Windows path. It only matters for local dev.
- `README.md` is empty. `package.json` points `main` at a missing `index.js`.

## Not reviewable from this repo

- **Telegram `/report` handler.** The bot's input handling is not in this repo. The only input here is the Trigger payload: the ticker is checked against `^[A-Z]{1,6}$` and the note is capped at 300 characters and passed without a shell. Review the bot code where it lives.
- **Git history.** The clone is shallow (`HEAD` only), so history was not scanned. Run a secret scanner (for example gitleaks) over the full history. Tracked files were checked: no key-like literals.

## Verified OK

- No API keys in tracked files. Keys are read from the environment only. `get()` returns only the exception type, so a key in a URL does not reach the error text (`test_key_never_appears_in_errors`).
- Telegram token is redacted in logs (`stock-report.ts:47`, `:84`), and only status codes are logged.
- The task validates the ticker before any work. The Firecrawl Osooli code embeds the ticker with `json.dumps`.
- Advice wording: the static Word report text has no buy, sell or "should" wording (`test_report_wording_is_research_only`). The Word footer and the interactive chart both say "Research only, not advice."
- Missing sources: every external call is wrapped. A failing source adds to `snapshot.errors`, which appears in the report footer.

## How to check

```bash
npm test
```

```bash
npx tsc -p tsconfig.review.json
```

The typecheck needs `@types/node` installed and a `tsconfig.review.json` like this: target ES2022, module and resolution NodeNext, strict, `typeRoots` pointing at `node_modules/@types`, `types: ["node"]`, and the five files in `src/trigger/` except `trigger.config.ts`. It is not committed.
