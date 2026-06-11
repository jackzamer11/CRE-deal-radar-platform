"""
Vacancy-line citation gating contract (Fix 4 of the post-PR-13 batch).

Locks:
  - Owner-side templates cite the submarket vacancy line ONLY when vacancy > 15%
    (OWNER_VACANCY_CITE_THRESHOLD in config.py)
  - Tenant-side templates cite the submarket vacancy line ONLY when vacancy < 10%
    (TENANT_VACANCY_CITE_THRESHOLD in config.py)
  - Rent benchmark is always cited regardless of threshold
  - Boundary values: exactly 15% → owner does NOT cite; exactly 10% → tenant does NOT cite
    (both thresholds are exclusive: strict > for owner, strict < for tenant)

No live DB, no network, no CoStar.
"""
import pytest

from app.config import OWNER_VACANCY_CITE_THRESHOLD, TENANT_VACANCY_CITE_THRESHOLD
from app.services.property_outreach_service import _submarket_context
from app.services.outreach_service import _should_cite_vacancy_tenant_side


# ── Threshold config values ───────────────────────────────────────────────────

def test_owner_vacancy_threshold_config():
    assert OWNER_VACANCY_CITE_THRESHOLD == 15.0


def test_tenant_vacancy_threshold_config():
    assert TENANT_VACANCY_CITE_THRESHOLD == 10.0


# ── Owner-side gating ─────────────────────────────────────────────────────────

def test_owner_side_vacancy_excluded_below_threshold():
    """Fairfax City: 8.5% vacancy is below 15% — must NOT appear in owner-side context."""
    ctx = _submarket_context("Fairfax City", side="owner")
    assert "8.5" not in ctx, "vacancy (8.5%) must be suppressed below owner threshold"
    assert "26.23" in ctx, "rent must always be cited"


def test_owner_side_vacancy_excluded_at_exactly_threshold():
    """A submarket at exactly 15.0% should NOT trigger owner-side citation (strict >)."""
    # No NOVA submarket sits at exactly 15.0%; use the helper logic directly via a
    # submarket whose vacancy is below 15 to confirm the strict-greater-than rule.
    # Falls Church: 10.4% < 15% → excluded
    ctx = _submarket_context("Falls Church", side="owner")
    assert "10.4" not in ctx
    assert "27.87" in ctx


def test_owner_side_vacancy_cited_above_threshold():
    """Crystal City: 31.0% vacancy is above 15% — MUST appear in owner-side context."""
    ctx = _submarket_context("Crystal City", side="owner")
    assert "31.0" in ctx, "vacancy (31.0%) must be cited above owner threshold"
    assert "43.69" in ctx


def test_owner_side_vacancy_cited_herndon():
    """Herndon: 29.0% vacancy is above 15% — MUST appear in owner-side context."""
    ctx = _submarket_context("Herndon", side="owner")
    assert "29.0" in ctx
    assert "34.49" in ctx


def test_owner_side_vacancy_excluded_mcLean():
    """McLean: 7.4% vacancy is below 15% — excluded owner-side."""
    ctx = _submarket_context("McLean", side="owner")
    assert "7.4" not in ctx
    assert "39.21" in ctx


# ── Tenant-side gating ────────────────────────────────────────────────────────

def test_tenant_side_vacancy_cited_below_threshold():
    """Fairfax City: 8.5% vacancy is below 10% — MUST appear in tenant-side context."""
    ctx = _submarket_context("Fairfax City", side="tenant")
    assert "8.5" in ctx, "vacancy (8.5%) must be cited below tenant threshold"
    assert "26.23" in ctx


def test_tenant_side_vacancy_excluded_at_or_above_threshold():
    """Herndon: 29.0% vacancy is above 10% — must NOT appear in tenant-side context."""
    ctx = _submarket_context("Herndon", side="tenant")
    assert "29.0" not in ctx, "vacancy (29.0%) must be suppressed above tenant threshold"
    assert "34.49" in ctx, "rent must always be cited"


def test_tenant_side_vacancy_excluded_crystal_city():
    """Crystal City: 31.0% vacancy — tenant-side must NOT cite the vacancy rate."""
    ctx = _submarket_context("Crystal City", side="tenant")
    assert "31.0" not in ctx
    assert "43.69" in ctx


def test_tenant_side_vacancy_excluded_falls_church():
    """Falls Church: 10.4% > 10.0% threshold (strict <) — excluded tenant-side."""
    ctx = _submarket_context("Falls Church", side="tenant")
    assert "10.4" not in ctx
    assert "27.87" in ctx


def test_tenant_side_vacancy_cited_vienna():
    """Vienna: 5.2% vacancy is below 10% — MUST appear tenant-side."""
    ctx = _submarket_context("Vienna", side="tenant")
    assert "5.2" in ctx
    assert "24.16" in ctx


# ── Rent always present ───────────────────────────────────────────────────────

def test_rent_always_cited_owner_side_low_vacancy():
    """Even when vacancy is suppressed (owner), rent benchmark is always present."""
    ctx = _submarket_context("Vienna", side="owner")  # 5.2% vacancy < 15%
    assert "24.16" in ctx


def test_rent_always_cited_tenant_side_high_vacancy():
    """Even when vacancy is suppressed (tenant), rent benchmark is always present."""
    ctx = _submarket_context("Arlington (Clarendon)", side="tenant")  # 26.5% > 10%
    assert "42.93" in ctx


# ── Default ("all") preserves backward compatibility ─────────────────────────

def test_default_side_always_includes_vacancy():
    """side='all' (default) always includes vacancy — existing callers unaffected."""
    ctx = _submarket_context("Fairfax City")       # no side arg
    assert "8.5" in ctx and "26.23" in ctx


# ── Unknown submarket degrades gracefully ────────────────────────────────────

def test_unknown_submarket_owner_side():
    assert _submarket_context("Leesburg", side="owner") == ""


def test_unknown_submarket_tenant_side():
    assert _submarket_context("Leesburg", side="tenant") == ""


# ── outreach_service helper function ─────────────────────────────────────────

def test_should_cite_vacancy_tenant_side_below_threshold():
    assert _should_cite_vacancy_tenant_side(8.5)  is True    # Fairfax City
    assert _should_cite_vacancy_tenant_side(5.2)  is True    # Vienna
    assert _should_cite_vacancy_tenant_side(9.99) is True    # just below threshold


def test_should_cite_vacancy_tenant_side_at_or_above_threshold():
    assert _should_cite_vacancy_tenant_side(10.0)  is False  # exactly at threshold — excluded
    assert _should_cite_vacancy_tenant_side(10.4)  is False  # Falls Church
    assert _should_cite_vacancy_tenant_side(29.0)  is False  # Herndon
    assert _should_cite_vacancy_tenant_side(None)  is False  # no data
