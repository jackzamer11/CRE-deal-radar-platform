"""
Tenant ↔ Property composite Match Score + shared match adjustments.

Single source of truth for the pairing logic used by:
  - GET /api/properties/{id}        → matched_tenants cards
  - GET /api/companies/{id}         → matched_properties cards
  - GET /api/dashboard/briefing     → Section A tenant-match action cards
  - run_deal_creation pipeline      → submarket proximity filter

Contract (all point values / weights live in app.config):
  1. Submarket factor: exact = 100, adjacent = 60, non-adjacent = excluded.
     A submarket string unknown to both the adjacency map and the platform
     dropdown degrades to exact-match-only (never crashes, never matches all).
  2. Building-class factor: same = 100, tenant up one class = 70, down one = 55,
     two classes apart = excluded, null/unparseable on either side = neutral 50.
  3. SF-fit HARD GATE: |sf_needed − sf_avail| must be ≤ MAX_SF_DELTA,
     otherwise the pair is excluded before SF scoring happens. SF source is the
     tenant's real occupied SF (current_sf_occupied) vs the property's
     AVAILABLE SF — never total or vacant SF.
  4. Composite = 0.40·submarket + 0.30·class + 0.30·sf_fit (0–100).
     The score is informational — it never blocks outreach generation.
  5. Medical soft penalty: when exactly one side of a pair is medical the
     composite drops by MEDICAL_MISMATCH_PENALTY — the match still appears,
     it just ranks lower. Never a hard filter.
"""
import logging
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

logger = logging.getLogger(__name__)

_KNOWN_SUBMARKETS = set(SUBMARKET_ADJACENCY.keys()) | set(PLATFORM_SUBMARKETS)

# Class rank: A=3, B=2, C=1 (higher = better building)
_CLASS_RANK = {"A": 3, "B": 2, "C": 1}

# Soft penalty applied when exactly one side of a match is medical (a medical
# property matched to a non-medical tenant, or vice versa). It is a SOFT signal:
# the match is still produced and displayed — it just scores lower so cleaner
# fits rank above it. Never use this to hard-filter a match out.
MEDICAL_MISMATCH_PENALTY = -20.0


def medical_mismatch_penalty(prop, company) -> float:
    """Return the medical/non-medical mismatch penalty for a property↔tenant pair.

    Returns MEDICAL_MISMATCH_PENALTY when exactly one side is medical, else 0.0.
    Accepts either ORM objects or anything exposing an ``is_medical`` attribute;
    a missing/None flag is treated as non-medical (the default for all records).
    """
    prop_medical = bool(getattr(prop, "is_medical", False))
    company_medical = bool(getattr(company, "is_medical", False))
    if prop_medical != company_medical:
        return MEDICAL_MISMATCH_PENALTY
    return 0.0


def sf_match_suppressed(company_sf_occupied, property_available_sf) -> bool:
    """True when a pairing must be suppressed on square-footage grounds.

    The comparison is against the property's AVAILABLE SF (sf_avail) only — never
    total or vacant SF.

    Null handling is asymmetric:
      * Unknown tenant occupied SF -> returns False. The pairing is NOT suppressed
        here; that case is handled elsewhere (the card is kept, outreach blocked).
      * Unknown / zero AVAILABLE SF -> returns True (suppress). A pairing cannot be
        sized without the available figure, so it is filtered out entirely.

    Otherwise suppress when
        abs(company_sf_occupied - property_available_sf) > MAX_SF_DELTA.
    """
    if not company_sf_occupied:
        logger.debug(
            "sf_match_suppressed: occupied=%r available=%r -> False "
            "(tenant occupied SF unknown; handled elsewhere)",
            company_sf_occupied, property_available_sf,
        )
        return False
    if not property_available_sf:
        logger.debug(
            "sf_match_suppressed: occupied=%r available=%r -> True "
            "(available SF unknown; cannot size the pairing)",
            company_sf_occupied, property_available_sf,
        )
        return True
    delta = abs(int(company_sf_occupied) - int(property_available_sf))
    suppressed = delta > MAX_SF_DELTA
    logger.debug(
        "sf_match_suppressed: occupied=%s available=%s delta=%s threshold=%s -> %s",
        int(company_sf_occupied), int(property_available_sf), delta, MAX_SF_DELTA, suppressed,
    )
    return suppressed


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
    sf_gate_exempt: bool = False,
) -> Optional[dict]:
    """Full pairing evaluation. Returns None when the pair is excluded
    (non-adjacent submarket, two-class gap, or SF gate); otherwise a dict:

      {
        "score":           float (0–100 composite),
        "submarket_score": float,
        "class_score":     float,
        "sf_fit_score":    float,
        "adjacent":        bool (True when matched via adjacency, not exact),
      }

    Gate order — every gate runs BEFORE any blending, so an excluded factor
    can never be propped up by the other factors in the weighted composite:
      1. Submarket gate: exact (100) or adjacent (60) proceeds; anything else
         (non-adjacent, unknown non-exact, null) returns no match immediately.
      2. Class gate: two classes apart (A↔C) returns no match.
      3. SF gate: |sf_needed − sf_avail| > MAX_SF_DELTA returns no match.
    """
    # Gate 1 — submarket. Non-adjacent pairs are never scored: no card,
    # no composite, no outreach.
    sub = submarket_score(tenant_submarket, property_submarket)
    if sub is None:
        return None

    # Gate 2 — building class (null/unparseable passes through as neutral 50).
    cls = class_score(tenant_class, property_class)
    if cls is None:
        return None

    # Gate 3 — SF delta hard gate, then the gradient for survivors.
    # sf_gate_exempt=True (already-contacted pairs: contacted history is never
    # disturbed) keeps the pair alive at the SF-fit gate floor instead.
    if not sf_delta_passes_gate(sf_needed, sf_avail):
        if not sf_gate_exempt:
            return None
        sf = SF_FIT_MIN_POINTS
    else:
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
