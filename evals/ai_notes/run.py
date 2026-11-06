"""Eval for the AI notes guardrails (python/ai_notes.py).

Question: when the model's reply is bad (advice wording, an invented number, a Halal comment, an echoed prompt
injection, a broken shape), does the code keep it out of the report, and does it keep good replies?

Each case in cases.json is a model reply plus what must be absent and present in the final notes. The model call is
replaced by the case's reply, so the run is offline, free and repeatable. This tests the code around the model, not
the model's own quality.

    python evals/ai_notes/run.py            prints a Markdown report, writes evals/ai_notes/results/latest.md
    python evals/ai_notes/run.py --check    also exits 1 if any case fails
"""
import json
import os
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, os.path.join(ROOT, "tests", "python"))

import ai_notes  # noqa: E402
import stock_core  # noqa: E402
from conftest import make_snap  # noqa: E402

GOOD = "good reply (must be kept)"


def _fill(obj, values):
    """Replace {price} style placeholders so cases can use the numbers the model was really given."""
    if isinstance(obj, str):
        for k, v in values.items():
            obj = obj.replace("{" + k + "}", v)
        return obj
    if isinstance(obj, list):
        return [_fill(x, values) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    return obj


def run_case(case):
    snap = make_snap()
    plan = stock_core.build_plan(snap, {"position": "watching", "entry": None, "lots": [], "shares": None})
    values = {"price": f"{plan['price']:.2f}", "rsi14": f"{snap['technicals']['rsi14']:.1f}"}
    raw = _fill(case["raw"], values)
    expect = _fill(case["expect"], values)
    web = case.get("web", [])
    old_call, old_web, old_key = ai_notes.call_model, ai_notes.web_context, os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "offline-eval"
    ai_notes.call_model = lambda *a, **k: (raw, "eval/canned", None)
    ai_notes.web_context = lambda *a, **k: web
    try:
        notes = ai_notes.make_notes(snap, plan, {"status": "unchecked"}, use_web=bool(web), log=lambda m: None)
    finally:
        ai_notes.call_model, ai_notes.web_context = old_call, old_web
        if old_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = old_key
    problems = []
    err = notes.get("_error")
    if expect.get("error"):
        if err != expect["error"]:
            problems.append(f"expected error {expect['error']!r}, got {err!r}")
        return problems
    if err:
        problems.append(f"whole notes lost: {err}")
        return problems
    out = json.dumps({k: v for k, v in notes.items() if not k.startswith("_")}, ensure_ascii=False)
    for s in expect.get("absent", []):
        if s.lower() in out.lower():
            problems.append(f"leaked: {s!r}")
    for s in expect.get("present", []):
        if s not in out:
            problems.append(f"wrongly dropped: {s!r}")
    return problems


def run_all():
    with open(os.path.join(HERE, "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    return [(c, run_case(c)) for c in cases]


def report(results):
    by_cat = OrderedDict()
    for c, probs in results:
        by_cat.setdefault(c["category"], []).append((c, probs))
    bad = [(c, p) for c, p in results if c["category"] != GOOD]
    good = [(c, p) for c, p in results if c["category"] == GOOD]
    caught = sum(1 for _, p in bad if not p)
    kept = sum(1 for _, p in good if not p)
    lines = ["# AI notes guardrail eval", "",
             "Offline. The model reply is canned per case; this scores the code that checks it (`python/ai_notes.py`).", "",
             f"- Bad replies handled correctly: **{caught}/{len(bad)}**",
             f"- Good replies kept intact: **{kept}/{len(good)}**", "",
             "| Category | Passed | Cases |", "|---|---|---|"]
    for cat, items in by_cat.items():
        ok = sum(1 for _, p in items if not p)
        lines.append(f"| {cat} | {ok}/{len(items)} | {', '.join(c['id'] for c, _ in items)} |")
    fails = [(c, p) for c, p in results if p]
    lines += ["", "## Failures" if fails else "## Failures: none"]
    for c, p in fails:
        lines.append(f"- `{c['id']}`: {'; '.join(p)}")
    return "\n".join(lines) + "\n", len(fails)


if __name__ == "__main__":
    text, nfail = report(run_all())
    print(text)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "latest.md"), "w", encoding="utf-8") as f:
        f.write(text)
    if "--check" in sys.argv and nfail:
        sys.exit(1)
