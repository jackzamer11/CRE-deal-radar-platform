"""Pytest gate on the golden set.

Two tests:
  1. Offline — proves the harness itself correctly detects fabrication and
     scores stated fields, using stub extractors (no network, always runs).
  2. Live  — runs the REAL extraction pipeline against the golden cases and
     fails if the model fabricated any value. Skipped when ANTHROPIC_API_KEY is
     unset so the default suite stays offline (per repo convention).
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_golden import load_cases, run  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"


def _faithful_extractor(case_by_text):
    """Return an extractor that echoes each case's golden values (no fabrication)."""
    def extract(pages):
        expected = case_by_text[pages[0]]
        return {
            field: {"value": val, "confidence": 0.9 if val else None,
                    "page": 1 if val else None, "snippet": None}
            for field, val in expected.items()
        }
    return extract


def test_harness_detects_a_faithful_extractor_as_clean():
    cases = load_cases(CASES_DIR)
    by_text = {c["text"]: c["expected"] for c in cases}
    summary = run(extractor=_faithful_extractor(by_text), verbose=False)
    assert summary["fabrication_count"] == 0
    assert summary["stated_accuracy"] == 1.0


def test_harness_flags_a_fabricating_extractor():
    """A stub that invents a base rent where golden says null must be caught."""
    def fabricating(pages):
        return {
            "tenant_name": {"value": "X"},
            "premises_sqft": {"value": "1"},
            "commencement_date": {"value": "2020-01-01"},
            "expiration_date": {"value": "2025-01-01"},  # invents a date
            "base_rent_annual": {"value": "999999"},      # invents a rent
        }
    summary = run(extractor=fabricating, verbose=False)
    # missing_base_rent (null base rent) and ambiguous_dates (null expiration)
    # each get a fabricated value -> at least 2 fabrications.
    assert summary["fabrication_count"] >= 2


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — live golden run skipped (default suite stays offline)",
)
def test_live_model_does_not_fabricate():
    try:
        summary = run(verbose=True)
    except Exception as exc:
        # Infrastructure problems (no credit, auth, rate limit, network) are not
        # fabrication failures — skip rather than red-flag the fabrication gate.
        # A call that SUCCEEDS and fabricates still fails the assert below.
        msg = str(exc).lower()
        infra = ("credit balance", "authentication", "rate limit", "429",
                 "connection", "timeout", "overloaded")
        if any(k in msg for k in infra):
            pytest.skip(f"Anthropic API unavailable, live golden run skipped: {exc}")
        raise

    assert summary["fabrication_count"] == 0, (
        f"Model fabricated values: {summary['fabrications']}"
    )
