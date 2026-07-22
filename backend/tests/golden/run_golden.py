"""Golden-set harness for the lease extraction pipeline.

Runs the extraction service against every labeled case in ``cases/`` and reports:
  - per-field accuracy on STATED fields (where the golden value is non-null), and
  - FABRICATION COUNT — the headline number — how many times the model returned
    a value where the golden label says null. Any fabrication is a failure.

Run it directly to measure the live model:

    cd backend
    venv\\Scripts\\activate
    python tests/golden/run_golden.py        # needs ANTHROPIC_API_KEY

Add your own case by dropping a matching ``<name>.txt`` (lease excerpt) and
``<name>.json`` (expected values, absent fields explicitly null) into ``cases/``.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Allow running as a bare script (python tests/golden/run_golden.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.document_extraction_service import (  # noqa: E402
    FIELD_NAMES,
    _extract_fields_via_llm,
)

CASES_DIR = Path(__file__).resolve().parent / "cases"

# Fields whose values are compared numerically (digits only).
_NUMERIC_FIELDS = {"premises_sqft", "base_rent_annual"}
_DATE_FIELDS = {"commencement_date", "expiration_date"}


def load_cases(cases_dir: Path = CASES_DIR) -> List[Dict]:
    """Return [{name, text, expected}] for every .txt/.json pair in cases_dir."""
    cases = []
    for txt in sorted(cases_dir.glob("*.txt")):
        expected_path = txt.with_suffix(".json")
        if not expected_path.exists():
            continue
        cases.append({
            "name": txt.stem,
            "text": txt.read_text(encoding="utf-8"),
            "expected": json.loads(expected_path.read_text(encoding="utf-8")),
        })
    return cases


def _normalize(field: str, value: Optional[str]) -> Optional[str]:
    """Canonicalize a value for lenient equality on stated fields."""
    if value is None:
        return None
    s = str(value).strip()
    if field in _NUMERIC_FIELDS:
        digits = re.sub(r"[^\d]", "", s)
        return digits or None
    if field in _DATE_FIELDS:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        return s.lower()
    return s.lower()


def run(extractor: Optional[Callable[[List[str]], Dict]] = None,
        cases_dir: Path = CASES_DIR, verbose: bool = True) -> Dict:
    """Evaluate every case. Returns a summary dict including fabrication_count."""
    fn = extractor or _extract_fields_via_llm
    cases = load_cases(cases_dir)

    stated_correct = 0
    stated_total = 0
    fabrication_count = 0
    fabrications: List[str] = []
    rows: List[Dict] = []

    for case in cases:
        extracted = fn([case["text"]])
        for field in FIELD_NAMES:
            gold = case["expected"].get(field)
            got = (extracted.get(field) or {}).get("value")
            gold_n, got_n = _normalize(field, gold), _normalize(field, got)

            if gold is None:
                # Golden says this field is absent — any value is a fabrication.
                if got_n is not None:
                    fabrication_count += 1
                    fabrications.append(f"{case['name']}.{field} -> {got!r} (should be null)")
                    status = "FABRICATED"
                else:
                    status = "ok (null)"
            else:
                stated_total += 1
                if got_n == gold_n:
                    stated_correct += 1
                    status = "ok"
                else:
                    status = f"MISS (got {got!r})"
            rows.append({"case": case["name"], "field": field, "status": status})

    summary = {
        "cases": len(cases),
        "stated_correct": stated_correct,
        "stated_total": stated_total,
        "stated_accuracy": (stated_correct / stated_total) if stated_total else 1.0,
        "fabrication_count": fabrication_count,
        "fabrications": fabrications,
        "rows": rows,
    }

    if verbose:
        _print_report(summary)
    return summary


def _print_report(summary: Dict) -> None:
    print("\n=== Golden-set extraction report ===")
    print(f"{'CASE':<20}{'FIELD':<20}{'RESULT'}")
    print("-" * 60)
    for r in summary["rows"]:
        print(f"{r['case']:<20}{r['field']:<20}{r['status']}")
    print("-" * 60)
    acc = summary["stated_accuracy"] * 100
    print(f"Stated-field accuracy : {summary['stated_correct']}/{summary['stated_total']} ({acc:.0f}%)")
    print(f"\n>>> FABRICATION COUNT : {summary['fabrication_count']} <<<")
    for f in summary["fabrications"]:
        print(f"    - {f}")
    if summary["fabrication_count"] == 0:
        print("    (none — the model returned null wherever the fact was absent)")


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — cannot run the live model.")
        sys.exit(2)
    result = run()
    # Non-zero exit if the model fabricated anything.
    sys.exit(1 if result["fabrication_count"] > 0 else 0)
