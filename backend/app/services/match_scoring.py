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
