"""
Arlington County Open Data Adapter
=====================================
Pulls from Arlington County's Socrata-powered Open Data portal.
Free API — no authentication required (anonymous limit: 1,000 rows/req).

Datasets used:
  Building Permits Issued  → enriches last_renovation_year + capex signals
  Real Property Assessment → enriches owner info, assessed value, acquisition data

Portal: https://data.arlingtonva.us
API:    https://data.arlingtonva.us/resource/{dataset_id}.json  (Socrata format)

To find dataset IDs: go to data.arlingtonva.us, open any dataset, click API →
the URL contains the 4-character-dash-4-character ID (e.g. kzfm-bci3).

Datasets verified 2026:
  - Building Permits:    kzfm-bci3
  - Real Estate Records: r6dm-5vxn
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SOCRATA_BASE = "https://data.arlingtonva.us/resource"

# Dataset IDs — verify at data.arlingtonva.us if API returns 404
PERMITS_DATASET    = "kzfm-bci3"   # Building Permits Issued
ASSESSMENT_DATASET = "r6dm-5vxn"   # Real Estate / Property Assessment

REQUEST_TIMEOUT = 12.0  # seconds — Arlington API can be slow


# ---------------------------------------------------------------------------
# Building Permits
# ---------------------------------------------------------------------------

def fetch_building_permits(
    address_fragment: str = "",
    min_value: int = 50_000,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """
    Fetch commercial building permits from Arlington County Open Data.

    Args:
        address_fragment: Partial address to filter (e.g. "3100 Clarendon").
                          Leave empty to pull all recent commercial permits.
        min_value:        Only return permits with construction value >= this.
        limit:            Max records to return per call.

    Returns list of raw permit dicts (field names vary by dataset version).
    Returns [] on any network/API error so pipeline degrades gracefully.
    """
    where_clauses = [f"construction_value >= {min_value}"]
    if address_fragment:
        safe = address_fragment.replace("'", "''").upper()[:40]
        where_clauses.append(f"UPPER(address) LIKE '%{safe}%'")

    params: Dict[str, Any] = {
        "$limit":   limit,
        "$order":   "permit_date DESC",
        "$where":   " AND ".join(where_clauses),
    }

    try:
        resp = httpx.get(
            f"{SOCRATA_BASE}/{PERMITS_DATASET}.json",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        permits = resp.json()
        logger.info("[Arlington] Permits fetched: %d records", len(permits))
        return permits

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[Arlington] Permits API returned %s — check dataset ID '%s'. Error: %s",
            exc.response.status_code, PERMITS_DATASET, exc,
        )
        return []
    except httpx.TimeoutException:
        logger.warning("[Arlington] Permits API timed out after %.0fs", REQUEST_TIMEOUT)
        return []
    except Exception as exc:
        logger.warning("[Arlington] Permits fetch failed: %s", exc)
        return []


def get_last_major_permit_year(
    permits: List[Dict[str, Any]],
    min_value: int = 100_000,
) -> Optional[int]:
    """
    Scan a list of permits and return the year of the most recent major
    capital project (construction_value >= min_value).

    Used as a proxy for last_renovation_year when direct data is missing.
    """
    major_years: List[int] = []

    for p in permits:
        raw_val = p.get("construction_value") or p.get("declared_valuation") or 0
        if _to_float(raw_val) < min_value:
            continue

        date_str = (
            p.get("permit_date")
            or p.get("issue_date")
            or p.get("issued_date")
            or ""
        )
        try:
            if date_str:
                major_years.append(datetime.fromisoformat(date_str[:10]).year)
        except (ValueError, TypeError):
            pass

    return max(major_years) if major_years else None


# ---------------------------------------------------------------------------
# Property Assessment (ownership + value)
# ---------------------------------------------------------------------------

def fetch_property_assessment(address: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the Arlington County property assessment record for an address.

    Returns a normalized dict with:
        owner_name, assessed_value, land_value, improvement_value,
        last_sale_price, last_sale_date, parcel_id

    Returns None if not found or on error.
    """
    # Trim to street number + street name only for fuzzy match
    addr_key = address.upper().split(",")[0].strip()[:40].replace("'", "''")

    params: Dict[str, Any] = {
        "$limit": 5,
        "$where": f"UPPER(property_address) LIKE '%{addr_key}%'",
    }

    try:
        resp = httpx.get(
            f"{SOCRATA_BASE}/{ASSESSMENT_DATASET}.json",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        records = resp.json()

        if not records:
            logger.debug("[Arlington] No assessment found for: %s", address)
            return None

        logger.info("[Arlington] Assessment found for %s", address)
        return _normalize_assessment(records[0])

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[Arlington] Assessment API returned %s — check dataset ID '%s'",
            exc.response.status_code, ASSESSMENT_DATASET,
        )
        return None
    except httpx.TimeoutException:
        logger.warning("[Arlington] Assessment API timed out for %s", address)
        return None
    except Exception as exc:
        logger.warning("[Arlington] Assessment fetch failed for %s: %s", address, exc)
        return None


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_assessment(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map Arlington Socrata field names → internal schema."""
    return {
        "owner_name":        (raw.get("owner_name") or raw.get("owner1") or "").strip().title(),
        "assessed_value":    _to_float(raw.get("assessed_value") or raw.get("total_value") or 0),
        "land_value":        _to_float(raw.get("land_value") or 0),
        "improvement_value": _to_float(raw.get("improvement_value") or raw.get("building_value") or 0),
        "last_sale_price":   _to_float(raw.get("last_sale_price") or raw.get("sale_price") or 0),
        "last_sale_date":    raw.get("last_sale_date") or raw.get("deed_date"),
        "tax_year":          raw.get("tax_year"),
        "parcel_id":         raw.get("parcel_id") or raw.get("account_number"),
    }


def _to_float(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0
