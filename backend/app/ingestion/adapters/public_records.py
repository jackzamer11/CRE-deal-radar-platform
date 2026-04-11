"""
Public Records Adapter
========================
Sources:
  - Virginia CAMA (Computer-Aided Mass Appraisal) — property tax records
  - Arlington County Real Estate Assessments: https://arlingtonva.us/services/taxes
  - Fairfax County GIS / RPTA: https://www.fairfaxcounty.gov/maps
  - Alexandria City property records
  - MRIS / BrightMLS transaction data (for ownership transfer dates)

Fields captured:
  - Owner name, ownership entity type
  - Acquisition date + price (deed transfer date)
  - Assessment value
  - Building permit history (proxy for renovation/capex)
  - Mortgage/deed of trust filings (proxy for debt maturity)

Data refresh: Weekly (ownership data changes slowly).
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def fetch_ownership_data(parcel_id: str, county: str = "Arlington") -> Dict[str, Any]:
    """
    Query county property records for ownership and transaction history.

    Production endpoints:
      Arlington: https://arlingtonva.us/cgi-bin/reval.cgi?code={parcel_id}
      Fairfax:   https://icare.fairfaxcounty.gov/ffxcare/
      Alexandria: https://www.alexandriava.gov/realestateassessments

    Returns: {owner_name, owner_type, acquisition_date, acquisition_price,
               assessed_value, deed_book, deed_page}
    """
    logger.info(f"[PublicRecords] Fetching ownership — parcel {parcel_id}, {county}")
    return {}


def fetch_permit_history(address: str, county: str = "Arlington") -> List[Dict[str, Any]]:
    """
    Query county building permit records for renovation/capex history.
    Permits > $100K signal meaningful capital reinvestment.

    Production: Arlington Open Data portal:
      https://data.arlingtonva.us/api/v2/catalog/datasets/building-permits/
    """
    logger.info(f"[PublicRecords] Fetching permit history — {address}")
    return []


def fetch_deed_of_trust(owner_name: str, address: str) -> Optional[Dict[str, Any]]:
    """
    Search deed of trust / mortgage filings to estimate debt maturity.

    Production: Virginia courts case information system + county deed records.
    Proxy for debt pressure signal when direct maturity data unavailable.
    """
    logger.info(f"[PublicRecords] Fetching deed of trust — {owner_name} / {address}")
    return None


def infer_owner_type(owner_name: str) -> str:
    """
    Classify owner entity type from name patterns.
    Matters because LLC/LP owners are more likely motivated by IRR targets
    than individual owners (who may have emotional attachment).
    """
    name_upper = owner_name.upper()
    if any(t in name_upper for t in ["LLC", "L.L.C."]):
        return "LLC"
    if any(t in name_upper for t in ["LP", "L.P.", "LTD PARTNERSHIP"]):
        return "LP"
    if any(t in name_upper for t in ["REIT", "TRUST", "FUND"]):
        return "REIT/FUND"
    if any(t in name_upper for t in ["CORP", "INC", "CORPORATION"]):
        return "Corporation"
    return "Individual/Other"
