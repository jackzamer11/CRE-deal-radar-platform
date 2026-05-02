"""
Shared tenant representative classification logic.

Single source of truth — imported by:
  - backend/app/services/signal_engine.py  (scoring delta)
  - backend/app/services/outreach_service.py  (prompt framing)
  - outreach_agent.py CLI  (prompt framing)
"""
from typing import Optional

MAJOR_BROKER_FIRMS: list[str] = [
    "JLL", "CBRE", "Cushman", "Cushman & Wakefield",
    "Newmark", "Savills", "Avison Young", "Colliers",
    "Lincoln Property", "Transwestern", "Eastdil",
]


def classify_rep(tenant_representative: Optional[str]) -> str:
    """Return 'BLANK', 'MAJOR', or 'OTHER' for the given rep string."""
    if not tenant_representative or not tenant_representative.strip():
        return "BLANK"
    rep_lower = tenant_representative.lower()
    for firm in MAJOR_BROKER_FIRMS:
        if firm.lower() in rep_lower:
            return "MAJOR"
    return "OTHER"
