"""
Scoring-change impact report — CRE Deal Radar
----------------------------------------------
Shows exactly which tenants move (and how far) when signal-engine scoring
changes land, by comparing stored scores BEFORE a refresh against
recomputed scores AFTER.

Cohorts surfaced (the two 2026-07 recalibration changes):
  (a) tenants carrying a MAJOR-firm rep (JLL/CBRE/etc.)   — rep delta −25 → −15
  (b) tenants whose SF/head is STRICTLY inside 150–210     — utilization 0 → 50

Usage (with the platform running, AFTER pulling the scoring change):
  python scoring_impact_report.py                 # capture BEFORE, refresh, capture AFTER
  python scoring_impact_report.py --before-only   # just list the affected cohorts, no refresh
  python scoring_impact_report.py --url http://localhost:8000

The BEFORE column is whatever the platform currently has stored (i.e. scores
computed by the engine version that last ran signals). The refresh recomputes
with the engine version now deployed. Run it once, immediately after
deploying the change, for a clean old-vs-new comparison.

Note: refresh-signals recomputes EVERY company, so unrelated inputs that
changed since the last refresh (e.g. lease-expiry decay) can also move a
score. The cohort columns keep the focus on the two scoring changes.
"""

import argparse
import sys

import requests

NEUTRAL_BAND_LO = 150.0   # exclusive — exactly 150 stays in the cramped tier (35.0)
NEUTRAL_BAND_HI = 210.0   # exclusive — exactly 210 stays in the oversized tier (25.0)


def fetch_companies(base_url: str) -> list:
    resp = requests.get(f"{base_url}/api/companies/", timeout=15)
    resp.raise_for_status()
    return resp.json()


def refresh_signals(base_url: str) -> dict:
    resp = requests.post(f"{base_url}/api/companies/refresh-signals", timeout=120)
    resp.raise_for_status()
    return resp.json()


def sf_per_head(company: dict):
    sf = company.get("current_sf_occupied")
    hc = company.get("current_headcount")
    if not sf or not hc or hc <= 0:
        return None
    return sf / hc


def in_neutral_band(company: dict) -> bool:
    ratio = sf_per_head(company)
    return ratio is not None and NEUTRAL_BAND_LO < ratio < NEUTRAL_BAND_HI


def is_major_rep(company: dict) -> bool:
    return company.get("rep_class") == "MAJOR"


def affected(companies: list) -> list:
    """Companies touched by either scoring change, tagged with their cohort(s)."""
    rows = []
    for c in companies:
        cohorts = []
        if is_major_rep(c):
            cohorts.append("MAJOR-rep")
        if in_neutral_band(c):
            cohorts.append("150-210 SF/head")
        if cohorts:
            rows.append((c, cohorts))
    return rows


def _fmt_ratio(company: dict) -> str:
    ratio = sf_per_head(company)
    return f"{ratio:.1f}" if ratio is not None else "—"


def print_report(before: list, after_by_id: dict) -> None:
    rows = affected(before)
    if not rows:
        print("No tenants currently carry a MAJOR-firm rep or fall strictly "
              "inside the 150-210 SF/head band.")
        return

    header = (f"{'company_id':<10} {'name':<34} {'cohort(s)':<26} "
              f"{'SF/head':>8} {'score before':>13} {'score after':>12} "
              f"{'Δ':>7}  tier before → after")
    print(header)
    print("-" * len(header))
    for c, cohorts in sorted(rows, key=lambda r: r[0]["company_id"]):
        cid    = c["company_id"]
        after  = after_by_id.get(cid)
        b_score, b_tier = c["opportunity_score"], c["priority"]
        if after is None:
            a_score_s, a_tier, delta_s = "?", "?", "?"
        else:
            a_score = after["opportunity_score"]
            a_tier  = after["priority"]
            a_score_s = f"{a_score:.2f}"
            delta_s = f"{a_score - b_score:+.2f}"
        print(f"{cid:<10} {c['name'][:34]:<34} {', '.join(cohorts):<26} "
              f"{_fmt_ratio(c):>8} {b_score:>13.2f} {a_score_s:>12} "
              f"{delta_s:>7}  {b_tier} → {a_tier}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scoring-change impact report")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Platform base URL (default: http://localhost:8000)")
    parser.add_argument("--before-only", action="store_true",
                        help="List affected cohorts from stored scores; skip the refresh")
    args = parser.parse_args()

    try:
        before = fetch_companies(args.url)
    except Exception as exc:
        print(f"[ERROR] Cannot reach Deal Radar at {args.url}: {exc}")
        print("Make sure your platform is running (open-platform.bat).")
        sys.exit(1)

    if args.before_only:
        print_report(before, {c["company_id"]: c for c in before})
        return

    print(f"Captured {len(before)} companies (BEFORE). Refreshing signals...")
    result = refresh_signals(args.url)
    print(f"Refreshed {result.get('refreshed')} companies. Capturing AFTER...\n")

    after = fetch_companies(args.url)
    print_report(before, {c["company_id"]: c for c in after})


if __name__ == "__main__":
    main()
