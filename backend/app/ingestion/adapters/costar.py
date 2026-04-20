"""
CoStar / LoopNet Adapter
=========================
Production:  Replace _fetch_costar_data() with authenticated CoStar API call.
             CoStar API docs: https://product.costar.com/api

Fields captured per property:
  - address, submarket, SF, asking price, vacancy %, lease rollover
  - days on market, cap rate, in-place rents
  - historical listing data (status changes, price cuts)

Data refresh: Daily at 06:00 EST via APScheduler.
Rate limit: CoStar API = 1,000 calls/day standard tier.
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Submarket codes used in CoStar API queries for NoVA office
NOVA_COSTAR_SUBMARKET_CODES = {
    "Arlington (Clarendon)":     "WDC-NO-ARC",
    "Arlington (Rosslyn)":       "WDC-NO-ARR",
    "Arlington (Ballston)":      "WDC-NO-ARB",
    "Arlington (Columbia Pike)": "WDC-NO-ARP",
    "Alexandria (Old Town)":     "WDC-NO-ALX",
    "Tysons":                    "WDC-NO-TYS",
    "Reston":                    "WDC-NO-RES",
    "Falls Church":              "WDC-NO-FLC",
    "McLean":                    "WDC-NO-MCL",
    "Vienna":                    "WDC-NO-VIE",
    "Fairfax City":              "WDC-NO-FFX",
}

# CoStar property type filter: Office = 3
PROPERTY_TYPE_OFFICE = 3
MAX_PRICE_FILTER = 10_000_000   # $10M ceiling
MIN_SF = 3_000
MAX_SF = 30_000


def fetch_active_listings(api_key: str, submarket: str = None) -> List[Dict[str, Any]]:
    """
    Fetch active office listings from CoStar API.

    Production implementation:
      POST https://api.costar.com/v1/property/search
      Headers: Authorization: Bearer {api_key}
      Body: {
        "propertyType": 3,
        "submarketCodes": [...],
        "listingStatus": "active",
        "minSF": 3000, "maxSF": 30000,
        "maxPrice": 10000000
      }

    Returns normalized list of property dicts matching our schema.
    """
    logger.info(f"[CoStar] Fetching active listings — submarket: {submarket or 'ALL NoVA'}")
    # Stub: returns empty list in dev mode
    # Replace with: response = httpx.post(COSTAR_API_URL, headers=..., json=payload)
    return []


def fetch_property_history(api_key: str, costar_id: str) -> Dict[str, Any]:
    """
    Fetch historical listing data for a specific property.
    Includes: prior listings, price changes, vacancy history.
    """
    logger.info(f"[CoStar] Fetching history for property {costar_id}")
    return {}


def fetch_comp_sales(api_key: str, submarket: str, max_age_months: int = 24) -> List[Dict[str, Any]]:
    """
    Fetch recent comparable sales in submarket.
    Used to calibrate price/SF benchmarks in the mispricing signal.
    """
    logger.info(f"[CoStar] Fetching comp sales — {submarket}, last {max_age_months}mo")
    return []


def fetch_lease_comps(api_key: str, submarket: str) -> List[Dict[str, Any]]:
    """
    Fetch lease comps to calibrate market rent benchmarks.
    """
    logger.info(f"[CoStar] Fetching lease comps — {submarket}")
    return []


def normalize_property(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map CoStar API response fields to our internal schema.
    CoStar field names → our property model fields.
    """
    return {
        "address":         raw.get("propertyAddress", ""),
        "submarket":       raw.get("submarket", ""),
        "total_sf":        raw.get("rentableBuildingArea", 0),
        "year_built":      raw.get("yearBuilt", 0),
        "occupancy_pct":   raw.get("occupancyRate", 0),
        "vacancy_pct":     100 - raw.get("occupancyRate", 100),
        "asking_price":    raw.get("listingPrice", None),
        "cap_rate":        raw.get("capRate", None),
        "days_on_market":  raw.get("daysOnMarket", None),
        "in_place_rent_psf": raw.get("averageActualRent", 0),
        "is_listed":       raw.get("listingStatus", "") == "active",
    }
