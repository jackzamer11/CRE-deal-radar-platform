"""
Fairfax County iCARE Property Assessment Adapter
==================================================
Pulls property assessment data from Fairfax County's public GIS REST API.

Free access — no authentication required.

Endpoints:
  Assessment (CAMA):  https://gis.fairfaxcounty.gov/arcgis/rest/services/CAMA/
                      CadastralAssessmentSearch/MapServer/0/query
  Sales History:      https://gis.fairfaxcounty.gov/arcgis/rest/services/CAMA/
                      SalesSearch/MapServer/0/query

Covers submarkets: Tysons, Reston, Falls Church (all Fairfax County)

Fairfax GIS Open Data: https://www.fairfaxcounty.gov/maps/gis-data
iCARE portal:          https://icare.fairfaxcounty.gov/
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

GIS_BASE = "https://gis.fairfaxcounty.gov/arcgis/rest/services/CAMA"
ASSESSMENT_URL = f"{GIS_BASE}/CadastralAssessmentSearch/MapServer/0/query"
SALES_URL      = f"{GIS_BASE}/SalesSearch/MapServer/0/query"

REQUEST_TIMEOUT = 15.0  # ArcGIS REST can be slow on first call


# ---------------------------------------------------------------------------
# Property Assessment
# ---------------------------------------------------------------------------

def fetch_assessment_by_address(address: str) -> Optional[Dict[str, Any]]:
    """
    Query Fairfax County GIS for a property assessment by street address.

    Searches the CAMA (Computer-Aided Mass Appraisal) parcel layer by
    SITE_ADD (site address field).

    Returns a normalized dict or None if not found / on error.
    """
    # Strip suite/unit and city/state — keep street number + street name
    addr_key = address.upper().split(",")[0].strip()[:40].replace("'", "''")

    params: Dict[str, Any] = {
        "where":             f"SITE_ADD LIKE '{addr_key}%'",
        "outFields":         (
            "PARID,SITE_ADD,OWNER1,OWNER2,"
            "LAND_VAL,IMP_VAL,TOT_VAL,"
            "SALE_PRICE,SALE_DATE,"
            "YR_BLT,EFF_YR_BLT,"
            "GROSS_AREA,GBA,"
            "USE_CODE,USE_DESC"
        ),
        "returnGeometry":    "false",
        "f":                 "json",
        "resultRecordCount": 5,
    }

    try:
        resp = httpx.get(ASSESSMENT_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        features = data.get("features") or []
        if not features:
            logger.debug("[Fairfax] No assessment found for: %s", address)
            return None

        attrs = features[0].get("attributes", {})
        logger.info("[Fairfax] Assessment found for %s (parcel %s)", address, attrs.get("PARID"))
        return _normalize_assessment(attrs)

    except httpx.HTTPStatusError as exc:
        logger.warning("[Fairfax] Assessment API returned %s for %s", exc.response.status_code, address)
        return None
    except httpx.TimeoutException:
        logger.warning("[Fairfax] Assessment API timed out for %s", address)
        return None
    except Exception as exc:
        logger.warning("[Fairfax] Assessment fetch failed for %s: %s", address, exc)
        return None


def fetch_sales_history(parcel_id: str) -> List[Dict[str, Any]]:
    """
    Return recent sales / ownership transfer records for a Fairfax parcel.
    Ordered newest-first.  Used to verify acquisition date and price.
    """
    if not parcel_id:
        return []

    params: Dict[str, Any] = {
        "where":             f"PARID = '{parcel_id}'",
        "outFields":         "PARID,PRICE,SALEDT,INSTRNO,GRANTEE,GRANTOR",
        "returnGeometry":    "false",
        "f":                 "json",
        "orderByFields":     "SALEDT DESC",
        "resultRecordCount": 10,
    }

    try:
        resp = httpx.get(SALES_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return [f.get("attributes", {}) for f in (data.get("features") or [])]

    except Exception as exc:
        logger.warning("[Fairfax] Sales history failed for parcel %s: %s", parcel_id, exc)
        return []


def enrich_property_from_fairfax(address: str) -> Optional[Dict[str, Any]]:
    """
    One-stop call: fetch assessment, optionally fetch sales history,
    return merged dict ready to apply to a Property model.

    Returned keys:
        owner_name, assessed_value, land_value, improvement_value,
        last_sale_price, last_sale_date, year_built, effective_year,
        total_sf, parcel_id, use_code, use_description
    """
    assessment = fetch_assessment_by_address(address)
    if not assessment:
        return None

    # If we have a parcel ID and no sale price, try the sales endpoint
    if assessment.get("parcel_id") and not assessment.get("last_sale_price"):
        sales = fetch_sales_history(assessment["parcel_id"])
        if sales:
            best = sales[0]
            assessment["last_sale_price"] = _to_float(best.get("PRICE"))
            assessment["last_sale_date"]  = _parse_epoch_ms(best.get("SALEDT"))

    return assessment


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_assessment(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Fairfax CAMA ArcGIS field names to our internal schema.
    Common fields: PARID, SITE_ADD, OWNER1, LAND_VAL, IMP_VAL, TOT_VAL,
                   SALE_PRICE, SALE_DATE, YR_BLT, EFF_YR_BLT,
                   GROSS_AREA, GBA, USE_CODE, USE_DESC
    """
    land_val = _to_float(attrs.get("LAND_VAL"))
    imp_val  = _to_float(attrs.get("IMP_VAL"))
    tot_val  = _to_float(attrs.get("TOT_VAL")) or (land_val + imp_val)

    owner = attrs.get("OWNER1") or attrs.get("OWNER2") or ""

    return {
        "parcel_id":         attrs.get("PARID"),
        "owner_name":        _clean_name(owner),
        "assessed_value":    tot_val,
        "land_value":        land_val,
        "improvement_value": imp_val,
        "last_sale_price":   _to_float(attrs.get("SALE_PRICE") or attrs.get("SALEPRICE")),
        "last_sale_date":    _parse_epoch_ms(attrs.get("SALE_DATE") or attrs.get("SALEDT")),
        "year_built":        _to_int(attrs.get("YR_BLT")),
        "effective_year":    _to_int(attrs.get("EFF_YR_BLT")),
        "total_sf":          _to_int(attrs.get("GROSS_AREA") or attrs.get("GBA")),
        "use_code":          attrs.get("USE_CODE") or attrs.get("USECODE"),
        "use_description":   attrs.get("USE_DESC") or attrs.get("USEDESC"),
    }


def _clean_name(name: str) -> str:
    return " ".join(name.split()).title() if name else ""


def _parse_epoch_ms(v: Any) -> Optional[str]:
    """Convert ArcGIS epoch-milliseconds timestamp to ISO date string."""
    if not v:
        return None
    try:
        ts = int(v) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def _to_float(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None
