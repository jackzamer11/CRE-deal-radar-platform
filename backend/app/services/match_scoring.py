"""
Tenant ↔ Property composite Match Score.

Single source of truth for the pairing logic used by:
  - GET /api/properties/{id}        → matched_tenants cards
  - GET /api/companies/{id}         → matched_properties cards
  - GET /api/dashboard/briefing     → Section A tenant-match action cards
  - run_deal_creation pipeline      → submarket proximity filter

Contract (all point values / weights live in app.config):
  1. SF-fit HARD GATE first: |sf_needed − sf_avail| must be ≤ MAX_SF_DELTA,
     otherwise the pair is excluded before any scoring happens.
  2. Submarket factor: exact = 100, adjacent = 60, non-adjacent = excluded.
     A submarket string unknown to both the adjacency map and the platform
     dropdown degrades to exact-match-only (never crashes, never matches all).
  3. Building-class factor: same = 100, tenant up one class = 70, down one = 55,
     two classes apart = excluded, null/unparseable on either side = neutral 50.
  4. Composite = 0.40·submarket + 0.30·class + 0.30·sf_fit (0–100).
     The score is informational — it never blocks outreach generation.
"""
from typing import Optional

from app.config import (
    SUBMARKET_ADJACENCY,
    PLATFORM_SUBMARKETS,
    MATCH_SCORE_WEIGHTS,
    SUBMARKET_EXACT_POINTS,
    SUBMARKET_ADJACENT_POINTS,
    CLASS_SAME_POINTS,
    CLASS_UPGRADE_POINTS,
    CLASS_DOWNGRADE_POINTS,
    CLASS_NEUTRAL_POINTS,
    MAX_SF_DELTA,
    SF_FIT_MAX_POINTS,
    SF_FIT_MIN_POINTS,
)

_KNOWN_SUBMARKETS = set(SUBMARKET_ADJACENCY.keys()) | set(PLATFORM_SUBMARKETS)

# Class rank: A=3, B=2, C=1 (higher = better building)
_CLASS_RANK = {"A": 3, "B": 2, "C": 1}


def are_adjacent(submarket_a: Optional[str], submarket_b: Optional[str]) -> bool:
    """Symmetric adjacency lookup. Unknown/null submarkets are never adjacent."""
    if not submarket_a or not submarket_b:
        return False
    return submarket_b in SUBMARKET_ADJACENCY.get(submarket_a, set())


def submarket_score(tenant_submarket: Optional[str], property_submarket: Optional[str]) -> Optional[float]:
    """Exact = 100, adjacent = 60, otherwise None (pair excluded).

    A submarket absent from both the adjacency map and the platform dropdown
    falls back to exact-match-only: it can still score 100 on an exact string
    match, but never matches anything else. Null on either side excludes the
    pair (no basis to match) — never raises.
    """
    if not tenant_submarket or not property_submarket:
        return None
    if tenant_submarket == property_submarket:
        return SUBMARKET_EXACT_POINTS
    if (tenant_submarket not in _KNOWN_SUBMARKETS
            or property_submarket not in _KNOWN_SUBMARKETS):
        return None  # unknown submarket → exact-match-only fallback
    if are_adjacent(tenant_submarket, property_submarket):
        return SUBMARKET_ADJACENT_POINTS
    return None


def _class_rank(raw: Optional[str]) -> Optional[int]:
    """Parse 'Class A' / 'A' / 'class b' → rank. Unparseable → None (neutral)."""
    if not raw or not isinstance(raw, str):
        return None
    letter = raw.strip().upper().replace("CLASS", "").strip()
    return _CLASS_RANK.get(letter)


def class_score(tenant_class: Optional[str], property_class: Optional[str]) -> Optional[float]:
    """Same = 100, tenant up one = 70, down one = 55, two apart = None (excluded),
    null/unparseable on either side = neutral 50 — never a crash or exclusion."""
    t_rank = _class_rank(tenant_class)
    p_rank = _class_rank(property_class)
    if t_rank is None or p_rank is None:
        return CLASS_NEUTRAL_POINTS
    diff = p_rank - t_rank
    if diff == 0:
        return CLASS_SAME_POINTS
    if diff == 1:
        return CLASS_UPGRADE_POINTS
    if diff == -1:
        return CLASS_DOWNGRADE_POINTS
    return None  # two classes apart (A↔C) — pair excluded


def sf_delta_passes_gate(sf_needed: Optional[int], sf_avail: Optional[int]) -> bool:
    """The hard SF gate, applied BEFORE scoring: both sides must be positive
    and the absolute delta must be within MAX_SF_DELTA."""
    if not sf_needed or not sf_avail or sf_needed <= 0 or sf_avail <= 0:
        return False
    return abs(sf_needed - sf_avail) <= MAX_SF_DELTA


def sf_fit_score(sf_needed: int, sf_avail: int) -> float:
    """Linear gradient for pairs that survived the gate:
    delta 0 → SF_FIT_MAX_POINTS, delta MAX_SF_DELTA → SF_FIT_MIN_POINTS."""
    delta = abs(sf_needed - sf_avail)
    span = SF_FIT_MAX_POINTS - SF_FIT_MIN_POINTS
    return SF_FIT_MAX_POINTS - span * (min(delta, MAX_SF_DELTA) / MAX_SF_DELTA)


def compute_match(
    tenant_submarket: Optional[str],
    property_submarket: Optional[str],
    tenant_class: Optional[str],
    property_class: Optional[str],
    sf_needed: Optional[int],
    sf_avail: Optional[int],
) -> Optional[dict]:
    """Full pairing evaluation. Returns None when the pair is excluded
    (SF gate, non-adjacent submarket, or two-class gap); otherwise a dict:

      {
        "score":           float (0–100 composite),
        "submarket_score": float,
        "class_score":     float,
        "sf_fit_score":    float,
        "adjacent":        bool (True when matched via adjacency, not exact),
      }
    """
    # Hard gate FIRST — excluded pairs are never scored.
    if not sf_delta_passes_gate(sf_needed, sf_avail):
        return None

    sub = submarket_score(tenant_submarket, property_submarket)
    if sub is None:
        return None

    cls = class_score(tenant_class, property_class)
    if cls is None:
        return None

    sf = sf_fit_score(sf_needed, sf_avail)

    w = MATCH_SCORE_WEIGHTS
    composite = w["submarket"] * sub + w["class"] * cls + w["sf_fit"] * sf
    return {
        "score":           round(composite, 1),
        "submarket_score": sub,
        "class_score":     cls,
        "sf_fit_score":    round(sf, 1),
        "adjacent":        sub == SUBMARKET_ADJACENT_POINTS,
    }
