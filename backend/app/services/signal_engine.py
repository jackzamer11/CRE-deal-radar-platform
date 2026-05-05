"""
Deal Radar OS — Signal Engine
==============================
Four categories of proprietary signals with precise formulas, weights, and thresholds.

A. PREDICTION SIGNALS   — Which buildings will hit market in 3-12 months
B. OWNER BEHAVIOR       — Motivated / fatigued seller indicators
C. MISPRICING           — Active listings with hidden upside
D. TENANT OPPORTUNITY   — Companies that can CREATE deals

All scores are normalized 0-100.
Signals return None (abstain) when required input data is missing.
Composites are weighted averages over scored (non-None) signals only.
"""

from datetime import date
from typing import Optional

from app.services.rep_classification import MAJOR_BROKER_FIRMS, classify_rep as _classify_rep


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _weighted_composite(
    scores: dict,
    weights: dict,
    min_scored: int = 3,
) -> dict:
    """
    Compute weighted average over non-None signals only.

    Returns composite on the same 0-100 scale regardless of how many signals
    abstained, by normalising weights to the scored subset.  Flags
    insufficient_data when fewer than min_scored signals contributed.
    """
    scored = {k: v for k, v in scores.items() if v is not None}
    n = len(scored)
    if n == 0:
        composite = 0.0
    else:
        total_weight = sum(weights[k] for k in scored)
        composite = (
            sum(scored[k] * weights[k] for k in scored) / total_weight
            if total_weight > 0 else 0.0
        )
    return {
        "composite": round(composite, 2),
        "breakdown": {k: round(v, 1) if v is not None else None for k, v in scores.items()},
        "signals_scored": n,
        "insufficient_data": n < min_scored,
    }


# ===========================================================================
# A. PREDICTION SIGNALS
# Which buildings are likely to hit the market within 3-12 months.
# ===========================================================================

def sig_lease_rollover(lease_rollover_pct: float) -> float:
    """
    Lease rollover clustering — when >30% of a building's leases expire
    in the next 12 months, the owner faces a binary decision: re-lease
    or sell before vacancy spikes.

    Formula: lease_rollover_pct = sf_expiring_12mo / total_sf * 100
    """
    r = lease_rollover_pct
    if r >= 60:  return 100.0
    if r >= 40:  return 80.0
    if r >= 25:  return 58.0
    if r >= 15:  return 32.0
    return _clamp(r * 1.5)


def sig_vacancy_trend(current_vacancy: Optional[float], vacancy_12mo_ago: Optional[float]) -> Optional[float]:
    """
    Rising vacancy trend — delta from 12 months ago.
    Accelerating vacancy is the single strongest forward indicator of
    an owner's motivation to exit before the asset enters distress.

    Returns None when current vacancy is unknown (abstain).

    Formula: delta_pp = current_vacancy_pct - vacancy_pct_12mo_ago
    """
    if current_vacancy is None:
        return None  # abstain — occupancy not yet populated
    if vacancy_12mo_ago is None:
        # No history — use absolute vacancy level as base signal
        if current_vacancy >= 50: return 60.0
        if current_vacancy >= 35: return 35.0
        if current_vacancy >= 20: return 15.0
        return 0.0

    delta = current_vacancy - vacancy_12mo_ago
    if delta >= 20:  return 100.0
    if delta >= 12:  return 78.0
    if delta >= 6:   return 48.0
    if delta >= 2:   return 22.0
    if delta >= 0:   return _clamp(delta * 8)
    return 0.0  # Vacancy improving — no signal


def sig_ownership_duration(years_owned: float) -> float:
    """
    Ownership duration vs NoVA average hold period (7 yrs).
    At 7yr mark: investor has likely hit target returns and is evaluating
    exit. At 10+ yrs: fatigue, capital recycling pressure, depreciation
    schedule exhausted.

    Formula: base = min(years_owned / 7.0 * 60, 80) + fatigue_bonus
    """
    base = _clamp(years_owned / NOVA_AVG_HOLD_YEARS * 60, 0, 80)
    fatigue = 20.0 if years_owned >= 12 else (12.0 if years_owned >= 10 else 0.0)
    return _clamp(base + fatigue)


def sig_leasing_drought(years_since_last_lease: float) -> float:
    """
    No new lease signed in N years = owner has stopped investing in the
    asset, likely pursuing passive hold or prepping for sale.

    Formula: score = years_since_last_lease * 18, capped at 100
    """
    return _clamp(years_since_last_lease * 18.0)


def sig_capex_gap(year_built: int, last_renovation_year: Optional[int]) -> float:
    """
    Building age without capital investment. Owners who haven't renovated
    in 15+ years are either preparing to sell or can no longer compete
    for tenants — both drive deal probability.

    Formula: years_since_reno = CURRENT_YEAR - last_reno (or year_built)
             score = min(years_since_reno / 20 * 100, 100)
    """
    baseline = last_renovation_year if last_renovation_year else year_built
    years_since = CURRENT_YEAR - baseline
    return _clamp(years_since / 20.0 * 100.0)


def compute_prediction_score(
    lease_rollover_pct: float,
    vacancy_pct: Optional[float],
    vacancy_12mo_ago: Optional[float],
    years_owned: float,
    years_since_last_lease: float,
    year_built: int,
    last_renovation_year: Optional[int],
) -> dict:
    """
    Weighted composite of all 5 prediction signals.

    Weights:
      Lease rollover ............ 30%  (most actionable — known timeline)
      Vacancy trend ............. 25%  (forward velocity indicator)
      Ownership duration ........ 25%  (hold period psychology)
      Leasing drought ........... 10%  (passive management signal)
      CapEx gap ................. 10%  (asset obsolescence proxy)
    """
    scores = {
        "lease_rollover":      sig_lease_rollover(lease_rollover_pct),
        "vacancy_trend":       sig_vacancy_trend(vacancy_pct, vacancy_12mo_ago),
        "ownership_duration":  sig_ownership_duration(years_owned),
        "leasing_drought":     sig_leasing_drought(years_since_last_lease),
        "capex_gap":           sig_capex_gap(year_built, last_renovation_year),
    }
    weights = {
        "lease_rollover":     0.30,
        "vacancy_trend":      0.25,
        "ownership_duration": 0.25,
        "leasing_drought":    0.10,
        "capex_gap":          0.10,
    }
    return _weighted_composite(scores, weights)


# ===========================================================================
# B. OWNER BEHAVIOR SIGNALS
# Motivated or fatigued sellers — behavioral and financial proxies.
# ===========================================================================

def sig_hold_period(years_owned: float) -> float:
    """
    Hold period vs exit-motivation curve. Most NoVA small-office owners
    target 7-10yr holds. Past 10 yrs: depreciation exhausted, management
    fatigue sets in, loan likely matured or refinanced at higher rate.

    Thresholds calibrated to observed NoVA transaction data.
    """
    if years_owned >= 17: return 100.0
    if years_owned >= 14: return 88.0
    if years_owned >= 11: return 72.0
    if years_owned >= 8:  return 52.0
    if years_owned >= 5:  return 28.0
    return _clamp(years_owned * 4.0)


def sig_occupancy_decline(vacancy_pct: Optional[float], vacancy_12mo_ago: Optional[float]) -> Optional[float]:
    """
    Two components:
    1. Rate of occupancy decline (trend signal — high urgency)
    2. Absolute current vacancy (stress signal — baseline pressure)

    Returns None when vacancy data is unknown (abstain).

    Combining both captures the owner who is either actively losing
    tenants OR has been stuck at high vacancy for an extended period.
    """
    if vacancy_pct is None:
        return None  # abstain — occupancy not yet populated
    # Rate component
    if vacancy_12mo_ago is not None:
        delta = vacancy_pct - vacancy_12mo_ago
        if delta >= 15:   rate_score = 100.0
        elif delta >= 10: rate_score = 80.0
        elif delta >= 5:  rate_score = 55.0
        elif delta >= 2:  rate_score = 25.0
        else:             rate_score = 0.0
    else:
        rate_score = 0.0

    # Absolute stress component
    base_score = _clamp(vacancy_pct * 1.4)

    return _clamp(base_score * 0.55 + rate_score * 0.45)


def sig_rent_stagnation(in_place_rent: float, market_rent: float) -> float:
    """
    Gap between in-place rent and current market rent.
    A large gap means the owner has not been actively releasing at market —
    either because of long-term leases they can't break or passive management.
    Both signal motivation to sell (capture mark-to-market via sale vs
    waiting for lease rollover).

    Formula: gap_pct = (market_rent - in_place_rent) / market_rent * 100
             score = gap_pct * 3.0, capped at 100
    """
    if market_rent <= 0:
        return 0.0
    gap_pct = (market_rent - in_place_rent) / market_rent * 100.0
    return _clamp(gap_pct * 3.0)


def sig_reinvestment_inactivity(year_built: int, last_renovation_year: Optional[int]) -> float:
    """
    No capital reinvestment = owner is not committed to the asset.
    Owners who stop investing are either planning to sell or have already
    lost competitive positioning (which itself accelerates vacancy).
    """
    baseline = last_renovation_year if last_renovation_year else year_built
    years_stale = CURRENT_YEAR - baseline
    if years_stale >= 22: return 100.0
    if years_stale >= 17: return 80.0
    if years_stale >= 12: return 55.0
    if years_stale >= 7:  return 28.0
    return _clamp(years_stale * 3.5)


def sig_debt_pressure(
    years_owned: float,
    estimated_loan_maturity_year: Optional[int],
) -> float:
    """
    Debt maturity creates forced decision points.
    - Direct: use stored loan maturity year when available
    - Proxy: if owned 7+ yrs, original 10yr loan likely approaching maturity
      or already refinanced at higher post-2022 rates
    """
    if estimated_loan_maturity_year:
        years_to_maturity = estimated_loan_maturity_year - CURRENT_YEAR
        if years_to_maturity <= 0:   return 100.0   # Already matured / in default risk
        if years_to_maturity <= 1:   return 90.0
        if years_to_maturity <= 2:   return 70.0
        if years_to_maturity <= 3:   return 45.0
        return 0.0
    else:
        # Proxy: long hold = loan likely at or near maturity
        if years_owned >= 10: return 65.0
        if years_owned >= 8:  return 40.0
        if years_owned >= 6:  return 20.0
        return 0.0


def compute_owner_behavior_score(
    years_owned: float,
    vacancy_pct: Optional[float],
    vacancy_12mo_ago: Optional[float],
    in_place_rent: float,
    market_rent: float,
    year_built: int,
    last_renovation_year: Optional[int],
    estimated_loan_maturity_year: Optional[int],
) -> dict:
    """
    Weighted composite of all 5 owner behavior signals.

    Weights:
      Hold period .................. 30%  (primary fatigue indicator)
      Occupancy decline ............ 25%  (financial stress driver)
      Rent stagnation .............. 20%  (passive management signal)
      Reinvestment inactivity ...... 15%  (asset commitment signal)
      Debt pressure ................ 10%  (forced decision proxy)
    """
    scores = {
        "hold_period":             sig_hold_period(years_owned),
        "occupancy_decline":       sig_occupancy_decline(vacancy_pct, vacancy_12mo_ago),
        "rent_stagnation":         sig_rent_stagnation(in_place_rent, market_rent),
        "reinvestment_inactivity": sig_reinvestment_inactivity(year_built, last_renovation_year),
        "debt_pressure":           sig_debt_pressure(years_owned, estimated_loan_maturity_year),
    }
    weights = {
        "hold_period":             0.30,
        "occupancy_decline":       0.25,
        "rent_stagnation":         0.20,
        "reinvestment_inactivity": 0.15,
        "debt_pressure":           0.10,
    }
    return _weighted_composite(scores, weights)


# ===========================================================================
# C. MISPRICING SIGNALS (active listings only)
# Hidden upside in properties already on the market.
# ===========================================================================

def sig_rent_gap(in_place_rent: float, market_rent: float) -> float:
    """
    In-place vs market rent gap on an active listing.
    A buyer who can see mark-to-market potential on rollover has a
    value-add thesis the market is pricing incorrectly if the seller
    is presenting NOI based on depressed in-place rents.

    Formula: gap_pct = (market - in_place) / market * 100
             score = gap_pct * 3.0
    """
    if market_rent <= 0:
        return 0.0
    gap_pct = (market_rent - in_place_rent) / market_rent * 100.0
    return _clamp(gap_pct * 3.0)


def sig_price_psf(
    asking_price_psf: Optional[float],
    submarket_avg_psf: float,
) -> float:
    """
    Price/SF vs submarket comparable sales.
    When a property is priced below comparable transactions, the market
    may be anchoring incorrectly on current income vs. stabilized value.

    Formula: discount_pct = (avg_psf - asking_psf) / avg_psf * 100
             score = discount_pct * 2.5  (capped at 100)
    """
    if not asking_price_psf or submarket_avg_psf <= 0:
        return 0.0
    discount_pct = (submarket_avg_psf - asking_price_psf) / submarket_avg_psf * 100.0
    return _clamp(discount_pct * 2.5)


def sig_dom_premium(days_on_market: Optional[int], submarket_avg_dom: Optional[int]) -> float:
    """
    Days on market vs. submarket average.
    Extended DOM indicates either overpricing (negotiable) or a
    disclosure issue the market hasn't fully priced — both present
    opportunity for a sophisticated buyer.

    Formula: dom_ratio = days_on_market / submarket_avg_dom
    """
    if not days_on_market or not submarket_avg_dom or submarket_avg_dom <= 0:
        return 0.0
    ratio = days_on_market / submarket_avg_dom
    if ratio >= 3.0:  return 100.0
    if ratio >= 2.0:  return 82.0
    if ratio >= 1.5:  return 55.0
    if ratio >= 1.25: return 30.0
    if ratio >= 1.0:  return 12.0
    return 0.0


def sig_cap_rate_spread(cap_rate: Optional[float], market_cap_rate: float) -> float:
    """
    In-place cap rate vs. submarket market cap rate.
    A positive spread (property cap > market cap) means the asset
    generates more income per dollar than comparable assets = underpriced
    on current income, or markets haven't repriced it yet.

    NOTE: Negative spread (compressed cap) flags an overpriced listing.

    Formula: spread_bps = (cap_rate - market_cap_rate) * 100
             score = spread_bps * 20 (capped at 100)
    """
    if not cap_rate:
        return 0.0
    spread = cap_rate - market_cap_rate
    if spread >= 1.5:  return 100.0
    if spread >= 1.0:  return 80.0
    if spread >= 0.5:  return 50.0
    if spread > 0:     return _clamp(spread * 40.0)
    return 0.0


def compute_mispricing_score(
    in_place_rent: float,
    market_rent: float,
    asking_price_psf: Optional[float],
    submarket_avg_psf: float,
    days_on_market: Optional[int],
    submarket_avg_dom: Optional[int],
    cap_rate: Optional[float],
    market_cap_rate: float,
    is_listed: bool,
) -> dict:
    """
    Weighted composite of all 4 mispricing signals.
    Only meaningful for active listings — returns zeros for unlisted assets.

    Weights:
      Rent gap ................. 35%  (most direct upside signal)
      Price/SF vs comps ........ 25%  (market pricing anchor)
      Days on market ........... 25%  (seller motivation proxy)
      Cap rate spread .......... 15%  (income yield signal)
    """
    if not is_listed:
        return {
            "composite": 0.0,
            "breakdown": {"rent_gap": 0.0, "price_psf": 0.0, "dom_premium": 0.0, "cap_rate_spread": 0.0},
            "signals_scored": 0,
            "insufficient_data": True,
        }

    scores = {
        "rent_gap":        sig_rent_gap(in_place_rent, market_rent),
        "price_psf":       sig_price_psf(asking_price_psf, submarket_avg_psf),
        "dom_premium":     sig_dom_premium(days_on_market, submarket_avg_dom),
        "cap_rate_spread": sig_cap_rate_spread(cap_rate, market_cap_rate),
    }
    weights = {
        "rent_gap":        0.35,
        "price_psf":       0.25,
        "dom_premium":     0.25,
        "cap_rate_spread": 0.15,
    }
    return _weighted_composite(scores, weights)


# ===========================================================================
# D. TENANT OPPORTUNITY SIGNALS
# Companies whose growth trajectory can CREATE deals.
# ===========================================================================

def sig_headcount_growth(growth_pct: Optional[float]) -> Optional[float]:
    """
    Year-over-year headcount growth rate.
    Companies growing >25% annually will outgrow their space
    within 12-18 months — creating a predictable relocation need.

    Returns None when growth data is unavailable (abstain).
    """
    if growth_pct is None:
        return None
    g = growth_pct
    if g >= 50:  return 100.0
    if g >= 35:  return 82.0
    if g >= 25:  return 65.0
    if g >= 15:  return 42.0
    if g >= 8:   return 22.0
    return _clamp(g * 2.0)


def sig_hiring_velocity(open_positions: int, current_headcount: Optional[int]) -> Optional[float]:
    """
    Open positions as % of current headcount.
    High hiring velocity = space need will materialize faster than
    the lease expiry clock suggests.

    Returns None when headcount is unknown (abstain).

    Formula: velocity = open_positions / current_headcount * 100
    """
    if not current_headcount or current_headcount <= 0:
        return None
    velocity = open_positions / current_headcount * 100.0
    if velocity >= 30:  return 100.0
    if velocity >= 20:  return 80.0
    if velocity >= 12:  return 55.0
    if velocity >= 5:   return 28.0
    return _clamp(velocity * 4.0)


def sig_lease_expiry_proximity(lease_expiry_months: Optional[int]) -> Optional[float]:
    """
    Months until lease expiry.
    The closer the expiry, the higher the urgency — and the more leverage
    you have as a broker who arrives BEFORE the tenant starts their own search.

    Returns None when lease expiry is unknown (abstain).

    Window of max impact: 6-18 months out (decision window).
    """
    if lease_expiry_months is None:
        return None
    m = lease_expiry_months
    if m <= 0:   return 100.0   # Already expired
    if m <= 6:   return 100.0
    if m <= 12:  return 85.0
    if m <= 18:  return 65.0
    if m <= 24:  return 40.0
    if m <= 36:  return 18.0
    return 0.0


def sig_space_utilization(current_sf: Optional[int], current_headcount: Optional[int]) -> Optional[float]:
    """
    Space utilization: SF per employee vs. modern standard (175 SF/head).

    Cramped (<130 SF/head): urgent need for more space — relocation demand
    Oversized (>230 SF/head): likely to downsize — landlord exit signal

    Returns None when SF or headcount is unknown (abstain).

    Both are actionable — either as tenant rep or as a signal to approach
    the current landlord about absorbing vacant space.
    """
    if not current_sf or not current_headcount or current_headcount <= 0:
        return None
    sf_per_head = current_sf / current_headcount

    # Expansion signal (cramped)
    if sf_per_head <= 90:   return 100.0
    if sf_per_head <= 110:  return 80.0
    if sf_per_head <= 130:  return 58.0
    if sf_per_head <= 150:  return 35.0

    # Downsizing signal
    if sf_per_head >= 280:  return 70.0
    if sf_per_head >= 240:  return 45.0
    if sf_per_head >= 210:  return 25.0

    return 0.0


def sig_geo_clustering(
    company_submarket: Optional[str],
    nearby_company_count: int = 0,
) -> float:
    """
    Companies in submarkets with high industry peer density tend to
    cluster — meaning active deal flow in the submarket increases the
    probability of a transaction.

    This is derived from real-time competitor location data (LinkedIn,
    CoStar tenant data, Google Maps industry clustering).

    For now: scored from seed-time context + nearby_company_count proxy.
    """
    if not company_submarket:
        return 0.0
    if nearby_company_count >= 5:  return 80.0
    if nearby_company_count >= 3:  return 55.0
    if nearby_company_count >= 1:  return 30.0
    return 15.0  # Base signal — every NoVA submarket has industry clusters


CURRENT_YEAR = 2026
NOVA_AVG_HOLD_YEARS = 7.0
MODERN_SF_PER_HEAD = 175


# ===========================================================================
# E. PROPERTY-SIDE OUTREACH SCORES
# Three independent 0-100 scores; the highest determines `dominant_score_type`,
# which drives the right outreach template (tenant_match | listing_rep |
# acquisition). Each signal returns None to abstain when its inputs are
# missing; the composite normalises across whatever scored.
# ===========================================================================

def _pscore(scores: dict, weights: dict) -> Optional[float]:
    """Property-score composite: weighted avg over scored signals only.

    Returns None when no signal could score (insufficient data).
    """
    scored = {k: v for k, v in scores.items() if v is not None}
    if not scored:
        return None
    total_weight = sum(weights[k] for k in scored)
    if total_weight <= 0:
        return None
    composite = sum(scored[k] * weights[k] for k in scored) / total_weight
    return round(_clamp(composite), 2)


def compute_tenant_match_score(
    vacancy_pct: Optional[float],
    sf_avail: Optional[int],
    total_sf: int,
    submarket_vacancy_avg: Optional[float],
    market_rent_psf: float,
    in_place_rent_psf: Optional[float],
    asking_rent_psf: Optional[float],
    tenancy: Optional[str],
) -> Optional[float]:
    """Likelihood this property is a strong target for a hybrid tenant pitch
    to the owner. High vacancy + abundant available SF + multi-tenant +
    asking rent at-or-below market = stronger match.
    """
    sigs: dict = {}

    # Vacancy headroom — landlord has rooms to fill
    if vacancy_pct is not None:
        if vacancy_pct >= 40:   sigs["vacancy"] = 100.0
        elif vacancy_pct >= 25: sigs["vacancy"] = 75.0
        elif vacancy_pct >= 15: sigs["vacancy"] = 50.0
        elif vacancy_pct >= 8:  sigs["vacancy"] = 25.0
        else:                   sigs["vacancy"] = 5.0
    else:
        sigs["vacancy"] = None

    # Available SF as proportion of total
    if sf_avail and total_sf > 0:
        avail_pct = sf_avail / total_sf * 100
        if avail_pct >= 30:   sigs["avail_sf"] = 100.0
        elif avail_pct >= 15: sigs["avail_sf"] = 70.0
        elif avail_pct >= 5:  sigs["avail_sf"] = 40.0
        else:                 sigs["avail_sf"] = 15.0
    else:
        sigs["avail_sf"] = None

    # Submarket activity — high submarket vacancy = soft demand, easier deal
    if submarket_vacancy_avg is not None:
        if submarket_vacancy_avg >= 25:   sigs["submarket"] = 80.0
        elif submarket_vacancy_avg >= 18: sigs["submarket"] = 55.0
        else:                              sigs["submarket"] = 30.0
    else:
        sigs["submarket"] = None

    # Asking vs market rent (lower asking vs market = pricing flexibility)
    asking = asking_rent_psf or in_place_rent_psf
    if asking and market_rent_psf > 0:
        delta_pct = (market_rent_psf - asking) / market_rent_psf * 100
        if delta_pct >= 10:  sigs["rent_gap"] = 80.0
        elif delta_pct >= 0: sigs["rent_gap"] = 55.0
        elif delta_pct >= -5: sigs["rent_gap"] = 35.0
        else:                 sigs["rent_gap"] = 15.0
    else:
        sigs["rent_gap"] = None

    # Tenancy — multi-tenant buildings absorb hybrid tenants more easily
    if tenancy:
        sigs["tenancy"] = 80.0 if tenancy.lower().startswith("multi") else 35.0
    else:
        sigs["tenancy"] = None

    weights = {
        "vacancy":   0.30,
        "avail_sf":  0.25,
        "submarket": 0.15,
        "rent_gap":  0.20,
        "tenancy":   0.10,
    }
    return _pscore(sigs, weights)


def compute_listing_rep_score(
    years_owned: Optional[float],
    in_place_rent_psf: Optional[float],
    market_rent_psf: float,
    year_built: int,
    last_renovation_year: Optional[int],
    estimated_loan_maturity_year: Optional[int],
    owner_type: Optional[str],
) -> Optional[float]:
    """Likelihood this owner is ready to list — and would value a quiet,
    relationship-first conversation with a listing broker. Long hold +
    rent below market + capex gap + debt pressure + LLC ownership all
    push the score up.
    """
    sigs: dict = {}

    # Hold period
    if years_owned is not None:
        if years_owned >= 20:   sigs["hold"] = 100.0
        elif years_owned >= 12: sigs["hold"] = 75.0
        elif years_owned >= 8:  sigs["hold"] = 45.0
        elif years_owned >= 5:  sigs["hold"] = 20.0
        else:                   sigs["hold"] = 5.0
    else:
        sigs["hold"] = None

    # In-place rent vs market — lower = stronger mark-to-market motivation
    if in_place_rent_psf and market_rent_psf > 0:
        gap_pct = (market_rent_psf - in_place_rent_psf) / market_rent_psf * 100
        sigs["rent_spread"] = _clamp(gap_pct * 3.0)
    else:
        sigs["rent_spread"] = None

    # Capex gap — years since renovation (or built)
    baseline = last_renovation_year if last_renovation_year else year_built
    years_stale = CURRENT_YEAR - baseline
    if years_stale >= 25:   sigs["capex"] = 100.0
    elif years_stale >= 18: sigs["capex"] = 70.0
    elif years_stale >= 12: sigs["capex"] = 45.0
    elif years_stale >= 6:  sigs["capex"] = 20.0
    else:                   sigs["capex"] = 5.0

    # Debt pressure — loan maturity within 24 months
    if estimated_loan_maturity_year is not None:
        years_to_maturity = estimated_loan_maturity_year - CURRENT_YEAR
        if years_to_maturity <= 0:   sigs["debt"] = 100.0
        elif years_to_maturity <= 1: sigs["debt"] = 80.0
        elif years_to_maturity <= 2: sigs["debt"] = 60.0
        elif years_to_maturity <= 3: sigs["debt"] = 30.0
        else:                        sigs["debt"] = 0.0
    else:
        sigs["debt"] = None

    # Owner type — small LLCs are more receptive than institutional capital
    if owner_type:
        ot = owner_type.lower()
        if "llc" in ot or "individual" in ot: sigs["owner"] = 75.0
        elif "lp" in ot or "partnership" in ot: sigs["owner"] = 55.0
        elif "reit" in ot or "institutional" in ot or "private equity" in ot: sigs["owner"] = 25.0
        else: sigs["owner"] = 50.0
    else:
        sigs["owner"] = None

    weights = {
        "hold":        0.30,
        "rent_spread": 0.20,
        "capex":       0.20,
        "debt":        0.20,
        "owner":       0.10,
    }
    return _pscore(sigs, weights)


def compute_acquisition_score(
    cap_rate: Optional[float],
    market_cap_rate: float,
    asking_price_psf: Optional[float],
    submarket_avg_psf: float,
    sf_avail: Optional[int],
    total_sf: int,
    is_listed: bool,
    years_owned: Optional[float],
    star_rating: Optional[int],
    year_built: int,
    last_renovation_year: Optional[int],
) -> Optional[float]:
    """Likelihood the property has a value-add / repositioning thesis a
    principal buyer would underwrite. Cap rate spread, price/SF discount,
    sublease overhang, hold + listed status, and condition (low star + capex
    gap = value-add) all factor in.
    """
    sigs: dict = {}

    # Cap rate spread vs submarket avg
    if cap_rate and market_cap_rate > 0:
        spread = cap_rate - market_cap_rate
        if spread >= 1.5:  sigs["cap_spread"] = 100.0
        elif spread >= 1.0: sigs["cap_spread"] = 75.0
        elif spread >= 0.5: sigs["cap_spread"] = 45.0
        elif spread > 0:    sigs["cap_spread"] = _clamp(spread * 40.0)
        else:               sigs["cap_spread"] = 0.0
    else:
        sigs["cap_spread"] = None

    # Asking price/SF vs submarket comp avg
    if asking_price_psf and submarket_avg_psf > 0:
        discount_pct = (submarket_avg_psf - asking_price_psf) / submarket_avg_psf * 100
        sigs["price_psf"] = _clamp(discount_pct * 2.5)
    else:
        sigs["price_psf"] = None

    # Sublease / available SF overhang — value-add via re-leasing
    if sf_avail and total_sf > 0:
        avail_pct = sf_avail / total_sf * 100
        if avail_pct >= 30: sigs["sublease"] = 90.0
        elif avail_pct >= 15: sigs["sublease"] = 60.0
        elif avail_pct >= 5:  sigs["sublease"] = 30.0
        else:                 sigs["sublease"] = 0.0
    else:
        sigs["sublease"] = None

    # Listed × hold — long hold listings are negotiable
    if is_listed and years_owned is not None:
        if years_owned >= 12:   sigs["hold_listed"] = 90.0
        elif years_owned >= 8:  sigs["hold_listed"] = 60.0
        elif years_owned >= 5:  sigs["hold_listed"] = 30.0
        else:                   sigs["hold_listed"] = 10.0
    elif is_listed:
        sigs["hold_listed"] = 25.0
    else:
        sigs["hold_listed"] = None

    # Building condition — low star rating + capex gap = value-add play
    baseline = last_renovation_year if last_renovation_year else year_built
    years_stale = CURRENT_YEAR - baseline
    if star_rating is not None:
        condition = (5 - star_rating) * 15.0  # 1-star → 60, 5-star → 0
        capex_bonus = 30.0 if years_stale >= 18 else (15.0 if years_stale >= 10 else 0.0)
        sigs["condition"] = _clamp(condition + capex_bonus)
    elif years_stale >= 18:
        sigs["condition"] = 50.0
    elif years_stale >= 10:
        sigs["condition"] = 25.0
    else:
        sigs["condition"] = None

    weights = {
        "cap_spread":  0.30,
        "price_psf":   0.20,
        "sublease":    0.15,
        "hold_listed": 0.15,
        "condition":   0.20,
    }
    return _pscore(sigs, weights)


def determine_dominant_score_type(
    tenant_match: Optional[float],
    listing_rep:  Optional[float],
    acquisition:  Optional[float],
) -> Optional[str]:
    """Pick the highest-scoring outreach axis. Returns None if nothing scored
    or all three are below an actionable threshold (15)."""
    candidates = [
        ("tenant_match", tenant_match or 0.0),
        ("listing_rep",  listing_rep  or 0.0),
        ("acquisition",  acquisition  or 0.0),
    ]
    best_name, best_value = max(candidates, key=lambda x: x[1])
    if best_value < 15:
        return None
    return best_name


def sig_tenant_rep(tenant_representative: Optional[str]) -> float:
    """
    Tenant representative adjustment — applied as a delta to the composite score.

    No rep on record    → +10  (uncontested; ideal for direct representation pitch)
    Major-firm rep      → −25  (JLL/CBRE/etc. make the deal effectively unwinnable)
    Regional/other rep  → −5   (existing relationship reduces win probability)

    Returns a signed delta, NOT a 0-100 signal score.
    """
    rep_class = _classify_rep(tenant_representative)
    if rep_class == "BLANK":
        return 10.0
    if rep_class == "MAJOR":
        return -25.0
    return -5.0


def compute_tenant_opportunity_score(
    headcount_growth_pct: Optional[float],
    open_positions: int,
    current_headcount: Optional[int],
    lease_expiry_months: Optional[int],
    current_sf: Optional[int],
    current_submarket: Optional[str],
    tenant_representative: Optional[str] = None,
    nearby_company_count: int = 0,
) -> dict:
    """
    Weighted composite of all 5 tenant opportunity signals, plus a rep adjustment.

    Weights:
      Lease expiry proximity ....... 30%  (creates urgency / hard deadline)
      Headcount growth ............. 25%  (predicts space need expansion)
      Space utilization ............ 20%  (current squeeze pressure)
      Hiring velocity .............. 20%  (near-term growth materialization)
      Geographic clustering ........ 5%   (market activity context)

    Post-composite: tenant_representative delta applied directly to composite.
    Major-firm rep (JLL/CBRE/etc.) drops a HIGH tenant to WORKABLE or below.
    """
    scores = {
        "headcount_growth":  sig_headcount_growth(headcount_growth_pct),
        "hiring_velocity":   sig_hiring_velocity(open_positions, current_headcount),
        "lease_expiry":      sig_lease_expiry_proximity(lease_expiry_months),
        "space_utilization": sig_space_utilization(current_sf, current_headcount),
        "geo_clustering":    sig_geo_clustering(current_submarket, nearby_company_count),
    }
    weights = {
        "headcount_growth":  0.25,
        "hiring_velocity":   0.20,
        "lease_expiry":      0.30,
        "space_utilization": 0.20,
        "geo_clustering":    0.05,
    }
    result = _weighted_composite(scores, weights)

    rep_delta = sig_tenant_rep(tenant_representative)
    result["composite"] = round(_clamp(result["composite"] + rep_delta), 2)
    result["breakdown"]["tenant_rep_adjustment"] = rep_delta

    return result
