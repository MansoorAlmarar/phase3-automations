"""Runs the offline guardrail eval (evals/ai_notes) as part of the normal test run, so CI fails if a guardrail regresses."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "evals", "ai_notes"))
import run as ai_eval  # noqa: E402

RESULTS = ai_eval.run_all()


@pytest.mark.parametrize("case,problems", RESULTS, ids=[c["id"] for c, _ in RESULTS])
def test_eval_case(case, problems):
    assert problems == []


def test_eval_has_cases_in_every_category():
    cats = {c["category"] for c, _ in RESULTS}
    assert len(RESULTS) >= 25
    assert {"advice wording", "invented number", "prompt injection", "bad ids and shapes", ai_eval.GOOD} <= cats
