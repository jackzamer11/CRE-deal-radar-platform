"""
Verification for two fixes:

(a) The acquisition outreach prompt (_build_acquisition) must never frame the
    owner's hold period — or anything else — as "urgency". The word "urgency"
    must not appear anywhere in the generated prompt (system + user), regardless
    of listing status or which dominant signal drives the copy.

(b) Snooze filtering logic must exclude records whose snoozed_until is a FUTURE
    date by default, while treating null / today / past dates as not snoozed.
    This mirrors the client-side default-hide behavior on the Companies and
    Properties list pages.
"""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest


def _import_svc():
    import app.services.property_outreach_service as svc
    return svc


def _property(*, listed_for_sale=False, dominant_score_type="hold_period",
             years_owned=12, days_on_market=None, vacancy_pct=None,
             submarket="Fairfax City"):
    return {
        "property_id": "TST-ACQ-001",
        "address": "9000 Acquisition Way, Fairfax, VA",
        "submarket": submarket,
        "total_sf": 30000,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 25.0,
        "market_cap_rate": 6.5,
        "listed_for_sale": listed_for_sale,
        "vacancy_pct": vacancy_pct,
        "days_on_market": days_on_market,
        "years_owned": years_owned,
        "dominant_score_type": dominant_score_type,
    }


# ── (a) acquisition prompt never contains the word "urgency" ───────────────────

@pytest.mark.parametrize("listed_for_sale", [True, False])
@pytest.mark.parametrize("dominant_score_type", ["hold_period", "debt_pressure", "vacancy_trend", ""])
def test_acquisition_prompt_never_says_urgency(listed_for_sale, dominant_score_type):
    svc = _import_svc()
    prompt = svc._build_acquisition(
        _property(listed_for_sale=listed_for_sale, dominant_score_type=dominant_score_type),
        target_type="owner",
    )
    combined = (prompt["system"] + prompt["user"]).lower()
    assert "urgency" not in combined, (
        f"acquisition prompt must not contain 'urgency' "
        f"(listed={listed_for_sale}, signal={dominant_score_type!r})"
    )


def test_acquisition_hold_period_is_factual_not_pressure():
    """When hold period drives the copy (no DOM / vacancy), it must be phrased as a
    neutral factual observation, never as urgency/pressure."""
    svc = _import_svc()
    prompt = svc._build_acquisition(
        _property(years_owned=14, days_on_market=None, vacancy_pct=None),
        target_type="owner",
    )
    combined = prompt["system"] + prompt["user"]
    lower = combined.lower()
    assert "urgency" not in lower
    # Hold period appears only as a factual ownership observation, not as a
    # "hold period" pressure cue.
    assert "long-term ownership" in lower
    assert "hold period" not in lower


# ── (b) snooze filtering excludes future-snoozed records by default ────────────

def test_future_snooze_is_excluded_by_default():
    from app.services.output_engine import _is_snoozed, _is_company_snoozed
    future = SimpleNamespace(snoozed_until=date.today() + timedelta(days=5))
    assert _is_snoozed(future) is True
    assert _is_company_snoozed(future) is True


def test_null_past_and_today_are_not_snoozed():
    from app.services.output_engine import _is_snoozed, _is_company_snoozed
    for snoozed_until in (None, date.today() - timedelta(days=1), date.today()):
        rec = SimpleNamespace(snoozed_until=snoozed_until)
        assert _is_snoozed(rec) is False, f"snoozed_until={snoozed_until} should not be snoozed"
        assert _is_company_snoozed(rec) is False, f"snoozed_until={snoozed_until} should not be snoozed"


def test_default_view_filter_hides_only_future_snoozed():
    """Mirror the list-page default predicate: keep a record unless its snooze is
    still active (future date)."""
    from app.services.output_engine import _is_snoozed
    records = [
        SimpleNamespace(name="active",       snoozed_until=date.today() + timedelta(days=3)),
        SimpleNamespace(name="expired",      snoozed_until=date.today() - timedelta(days=3)),
        SimpleNamespace(name="never",        snoozed_until=None),
    ]
    visible_by_default = [r.name for r in records if not _is_snoozed(r)]
    snoozed_only_view  = [r.name for r in records if _is_snoozed(r)]
    assert visible_by_default == ["expired", "never"]
    assert snoozed_only_view == ["active"]
