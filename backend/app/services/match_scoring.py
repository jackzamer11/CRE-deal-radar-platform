"""
Shared match-scoring adjustments applied to tenant ↔ property matches.

These live in one module so every match surface (the Daily Briefing tenant
actions, the property-side matched-tenant card, and the company-side matched-
property card) applies identical scoring rules.
"""

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


# ── SF match tolerance (Fix 2) ──────────────────────────────────────────────────
# Hard ceiling on the absolute gap between a tenant's real occupied SF
# (current_sf_occupied) and a property's AVAILABLE SF. A pairing whose gap exceeds
# this is suppressed entirely — no match card, no outreach — UNLESS one side of the
# pair has already been marked contacted (contacted history is never disturbed).
MAX_SF_DELTA = 800


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
        return False
    if not property_available_sf:
        return True
    return abs(int(company_sf_occupied) - int(property_available_sf)) > MAX_SF_DELTA

