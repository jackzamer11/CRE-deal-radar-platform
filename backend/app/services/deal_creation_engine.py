"""
Deal Radar OS — Deal Creation Engine
======================================
This is the differentiated edge of the system.

It connects TENANT signals to BUILDING signals to CREATE opportunities
that neither the tenant nor the landlord have identified yet.

Core Logic:
  IF company.expansion_signal = True
  AND company.lease_expiry_months <= 24
  AND nearby_building.vacancy_pct >= 20%
  AND nearby_building.prediction_score OR mispricing_score >= threshold
  → CREATE a TENANT_DRIVEN opportunity with dual-side pitch script

Deal categories created:
  1. TENANT REP — represent the growing company
  2. LANDLORD REP — approach landlord with qualified tenant
  3. ACQUISITION — identify buy-side play with tenant as anchor

Each opportunity includes:
  - Who to call (tenant side then landlord side)
  - What to say (angle calibrated to signal mix)
  - Why now (urgency driver)
  - Estimated deal value and commission
"""

import uuid
from datetime import date
from typing import List, Optional

from app.models.property import Property
from app.models.company import Company
from app.services.signal_engine import (
    compute_prediction_score,
    compute_owner_behavior_score,
    compute_mispricing_score,
    compute_tenant_opportunity_score,
)
from app.services.scoring_model import score_property, compute_deal_score, determine_deal_type


# ---------------------------------------------------------------------------
# Adjacent submarket mapping — used to find cross-submarket matches
# ---------------------------------------------------------------------------
ADJACENT_SUBMARKETS = {
    "Arlington (Clarendon)":     ["Arlington (Rosslyn)", "Arlington (Ballston)", "Arlington (Columbia Pike)"],
    "Arlington (Rosslyn)":       ["Arlington (Clarendon)", "Arlington (Ballston)"],
    "Arlington (Ballston)":      ["Arlington (Clarendon)", "Arlington (Columbia Pike)", "Falls Church"],
    "Arlington (Columbia Pike)": ["Arlington (Ballston)", "Falls Church", "Alexandria (Old Town)"],
    "Alexandria (Old Town)":     ["Arlington (Columbia Pike)", "Arlington (Rosslyn)"],
    "Tysons":                    ["Reston", "Falls Church", "Arlington (Ballston)", "McLean", "Vienna"],
    "Reston":                    ["Tysons", "Falls Church", "Vienna"],
    "Falls Church":              ["Tysons", "Arlington (Ballston)", "Arlington (Columbia Pike)", "Reston", "McLean", "Fairfax City"],
    "McLean":                    ["Tysons", "Falls Church"],
    "Vienna":                    ["McLean", "Tysons", "Fairfax City", "Reston"],
    "Fairfax City":              ["Vienna", "Falls Church"],
}

# Typical NoVA office commission rate (% of total lease value or sale price)
LEASE_COMMISSION_RATE = 0.04    # 4% of total lease value
SALE_COMMISSION_RATE  = 0.02    # 2% of sale price
AVG_LEASE_TERM_YEARS  = 5       # Standard new lease term in NoVA


def _is_nearby(prop_submarket: str, company_submarket: Optional[str]) -> bool:
    if not company_submarket:
        return False
    if prop_submarket == company_submarket:
        return True
    return prop_submarket in ADJACENT_SUBMARKETS.get(company_submarket, [])


def _estimated_sf_needed(company: Company) -> int:
    """Project the company's SF need at 12-18mo growth horizon."""
    growth_factor = 1.0
    if company.headcount_growth_pct:
        growth_factor = 1 + (company.headcount_growth_pct / 100.0) * 1.25  # 15-mo projection
    projected_heads = int((company.current_headcount or 1) * growth_factor)
    return projected_heads * 175   # Modern standard SF/head


def _generate_call_script(
    opportunity_category: str,
    deal_type: str,
    prop: Property,
    company: Optional[Company],
    score: float,
    estimated_deal_value: float,
    estimated_commission: float,
) -> str:
    """Generate a precise, angle-specific call script."""
    rent_gap_pct = 0.0
    if prop.market_rent_psf and prop.in_place_rent_psf:
        rent_gap_pct = (prop.market_rent_psf - prop.in_place_rent_psf) / prop.market_rent_psf * 100.0

    acq_year = prop.acquisition_date.year if prop.acquisition_date else "N/A"
    yrs_owned_str = f"{prop.years_owned:.0f}" if prop.years_owned else "?"

    if deal_type == "TENANT_DRIVEN" and company:
        sf_needed = _estimated_sf_needed(company)
        months = company.lease_expiry_months or 0
        expiry_urgency = "immediately" if months <= 6 else f"in {months} months"

        return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TENANT CALL — {company.name}
Contact: {company.primary_contact_name or 'Decision Maker'} | {company.primary_contact_title or 'CRE Decision Maker'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPENING:
"Hi [Name], I'm [Your Name] from [Firm]. I work exclusively with
{company.industry} companies in Northern Virginia. I've been watching
{company.name}'s growth trajectory closely — congratulations on the
expansion. Do you have 3 minutes?"

CORE MESSAGE:
"Based on your current footprint of ~{company.sf_per_head:.0f} SF per
person and your growth rate, my models show you'll need approximately
{sf_needed:,} SF within the next 12-18 months. Your lease comes up
{expiry_urgency}. I want to get you positioned BEFORE you're in
negotiation under a deadline."

PAIN PROBE:
"Are you finding your current space is limiting your ability to hire?
What's your 18-month headcount target?"

THE CLOSE:
"I've identified 2-3 buildings in {company.current_submarket or 'your submarket'}
with available space, motivated landlords, and room to structure a deal
below current market. I'd like 30 minutes to show you what I'm seeing."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LANDLORD CALL — {prop.owner_name} re: {prop.address}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPENING:
"Hi, this is [Your Name] with [Firm]. I specialize in {prop.submarket}
office. I'm calling because I have a qualified, credit-worthy tenant
actively looking in your building's submarket."

CORE MESSAGE:
"The company is in the {company.industry} sector, {company.current_headcount}
employees and growing at {company.headcount_growth_pct:.0f}% per year.
They need {sf_needed:,} SF and their lease expires in {months} months —
they're motivated to move quickly. Your {prop.vacant_sf:,.0f} SF of
vacancy at {prop.address} matches their requirements."

VALUE PROP:
"I can bring you a pre-negotiated LOI within 2 weeks. This solves your
vacancy without an extended marketing campaign."

DEAL STATS:
  • Tenant: {company.name} | {company.current_headcount} heads +{company.headcount_growth_pct:.0f}%/yr
  • Space needed: {sf_needed:,} SF | Lease expiry: {months}mo
  • Building: {prop.address} | {prop.vacancy_pct:.0f}% vacant ({prop.vacant_sf:,.0f} SF)
  • Est. deal value: ${estimated_deal_value:,.0f} | Est. commission: ${estimated_commission:,.0f}
  • Your Signal Score: {score:.0f}/100"""

    elif deal_type == "ACTIVE_MISPRICED":
        return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BUY-SIDE CALL — {prop.address}
Listing Agent / Seller Contact
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPENING:
"Hi, I'm [Your Name] with [Firm]. I represent an equity buyer
specifically looking for office assets under $10M in {prop.submarket}.
I've been tracking {prop.address}."

CORE MESSAGE:
"This asset has been on market {prop.days_on_market or 0} days.
My buyer understands the in-place rent story at ${prop.in_place_rent_psf:.2f}/SF
versus the {prop.submarket} market at ${prop.market_rent_psf:.2f}/SF —
that's a {rent_gap_pct:.0f}% mark-to-market gap. They're buying the
rollover upside, not the current NOI."

PRICE ANGLE:
"At ${prop.asking_price_psf:.2f}/SF asking, I believe we can find a
number that works for both sides. My buyer can close in 45 days,
all-cash, minimal contingencies."

CLOSE:
"Can we schedule a 20-minute conversation with the seller this week?"

DEAL STATS:
  • Address: {prop.address} | {prop.submarket}
  • Vacancy: {prop.vacancy_pct:.0f}% | In-place rent: ${prop.in_place_rent_psf:.2f}/SF
  • Market rent: ${prop.market_rent_psf:.2f}/SF | Gap: {rent_gap_pct:.0f}%
  • Days on market: {prop.days_on_market or 0} (mkt avg: {prop.submarket_avg_dom or '—'})
  • Asking: ${prop.asking_price_psf:.2f}/SF | Est. value: ${estimated_deal_value:,.0f}
  • Your Signal Score: {score:.0f}/100"""

    else:  # PRE_MARKET
        return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OFF-MARKET CALL — {prop.owner_name}
Owner of: {prop.address}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPENING:
"Hi, this is [Your Name] with [Firm]. I specialize in {prop.submarket}
office market and I've been tracking {prop.address} for several months."

CORE MESSAGE:
"I'm currently working with buyers specifically targeting {prop.submarket}
assets in your size range. Given the current leasing environment —
{prop.vacancy_pct:.0f}% vacancy, {prop.lease_rollover_pct:.0f}% of
leases rolling in the next 12 months — I think the timing to have a
confidential conversation is better now than 12 months from now."

PAIN PROBE:
"You've owned the asset since {acq_year} — that's {yrs_owned_str} years.
Have you been evaluating your options for this building? The market has
shifted and I think we can position you very well right now."

SELLER MOTIVATION THESIS:
  • You've held {yrs_owned_str} years (NoVA avg hold: 7yr)
  • Vacancy at {prop.vacancy_pct:.0f}% and {prop.lease_rollover_pct:.0f}% rolling
  • In-place rents {rent_gap_pct:.0f}% below market — a buyer absorbs that risk
  • {prop.year_built} vintage, {(CURRENT_YEAR - (prop.last_renovation_year or prop.year_built)):.0f}yr since last reno

CLOSE:
"Would you be open to a confidential 20-minute call to hear what
qualified buyers are paying for comparable assets right now?"

DEAL STATS:
  • Address: {prop.address} | {prop.submarket}
  • Owner: {prop.owner_name} (since {acq_year})
  • Est. market value: ${estimated_deal_value:,.0f}
  • Your Signal Score: {score:.0f}/100"""


CURRENT_YEAR = 2026


def _build_thesis(
    deal_type: str,
    prop: Property,
    company: Optional[Company],
    score: float,
) -> str:
    """Build a concise, factual deal thesis — no fluff."""
    rent_gap_pct = 0.0
    if prop.market_rent_psf and prop.in_place_rent_psf:
        rent_gap_pct = (prop.market_rent_psf - prop.in_place_rent_psf) / prop.market_rent_psf * 100

    if deal_type == "TENANT_DRIVEN" and company:
        sf_needed = _estimated_sf_needed(company)
        return (
            f"{company.name} ({company.industry}) has grown {company.headcount_growth_pct:.0f}% YoY to "
            f"{company.current_headcount} employees, is operating at {company.sf_per_head:.0f} SF/head "
            f"(vs 175 SF standard), and their lease expires in {company.lease_expiry_months} months. "
            f"They need ~{sf_needed:,} SF. {prop.address} has {prop.vacant_sf:,.0f} SF of vacancy at "
            f"${prop.in_place_rent_psf:.2f}/SF — {rent_gap_pct:.0f}% below the "
            f"{prop.submarket} market. Dual-side opportunity: tenant rep + landlord introduction. "
            f"Score: {score:.0f}/100."
        )
    elif deal_type == "ACTIVE_MISPRICED":
        acq_year = prop.acquisition_date.year if prop.acquisition_date else "N/A"
        return (
            f"{prop.address} ({prop.total_sf:,} SF, {prop.submarket}) is listed at "
            f"${prop.asking_price_psf:.2f}/SF with {prop.days_on_market or 0} days on market "
            f"({(prop.days_on_market or 0) - (prop.submarket_avg_dom or 120):+d} vs submarket avg). "
            f"In-place rents at ${prop.in_place_rent_psf:.2f}/SF are {rent_gap_pct:.0f}% below market "
            f"(${prop.market_rent_psf:.2f}/SF). Owner {prop.owner_name} has held since {acq_year}. "
            f"Value-add thesis: buy at compressed NOI, mark rents to market on rollover. Score: {score:.0f}/100."
        )
    else:  # PRE_MARKET
        yrs = f"{prop.years_owned:.0f}" if prop.years_owned else "?"
        reno_gap = CURRENT_YEAR - (prop.last_renovation_year or prop.year_built)
        return (
            f"{prop.address} ({prop.total_sf:,} SF, {prop.submarket}) — pre-market prediction. "
            f"Owner {prop.owner_name} has held {yrs} years (NoVA avg: 7yr). Vacancy at {prop.vacancy_pct:.0f}%, "
            f"{prop.lease_rollover_pct:.0f}% of leases rolling in 12 months. "
            f"In-place rents {rent_gap_pct:.0f}% below market. Last reno: {reno_gap}yr ago. "
            f"System predicts 3-12 month sale probability. Score: {score:.0f}/100."
        )


def create_opportunity_from_match(
    prop: Property,
    company: Optional[Company],
    signal_results: dict,
) -> Optional[dict]:
    """
    Given computed signal results, create a structured opportunity dict.
    Returns None if the opportunity doesn't meet minimum thresholds.
    """
    prediction = signal_results["prediction"]["composite"]
    owner_behavior = signal_results["owner_behavior"]["composite"]
    mispricing = signal_results["mispricing"]["composite"]
    tenant = signal_results.get("tenant", {}).get("composite", 0.0)

    scored = score_property(prediction, owner_behavior, mispricing, tenant, prop.is_listed)

    if scored["priority"] == "IGNORE":
        return None

    deal_type = scored["deal_type"]
    score = scored["score"]

    # Determine opportunity category
    if deal_type == "TENANT_DRIVEN":
        category = "TENANT_REP" if company else "LANDLORD_REP"
    elif deal_type == "ACTIVE_MISPRICED":
        category = "ACQUISITION"
    else:
        category = "ACQUISITION"

    # Financials
    if prop.asking_price:
        deal_value = prop.asking_price
    elif prop.estimated_value:
        deal_value = prop.estimated_value
    else:
        # Estimate from NOI / market cap rate
        if prop.noi:
            deal_value = prop.noi / (prop.market_cap_rate / 100.0)
        else:
            in_place_noi = prop.in_place_rent_psf * prop.leased_sf * 0.55 if prop.leased_sf else 0
            deal_value = in_place_noi / (prop.market_cap_rate / 100.0) if in_place_noi > 0 else 0

    if deal_type == "TENANT_DRIVEN" and company:
        # Lease TLV
        sf_needed = _estimated_sf_needed(company)
        annual_rent = sf_needed * prop.in_place_rent_psf
        deal_value = annual_rent * AVG_LEASE_TERM_YEARS
        commission = deal_value * LEASE_COMMISSION_RATE
    else:
        commission = deal_value * SALE_COMMISSION_RATE

    thesis = _build_thesis(deal_type, prop, company, score)
    next_action = _get_next_action(deal_type, prop, company)
    call_script = _generate_call_script(
        category, deal_type, prop, company, score, deal_value, commission
    )

    opp_id = f"OPP-{str(uuid.uuid4())[:8].upper()}"

    return {
        "opportunity_id":          opp_id,
        "deal_type":               deal_type,
        "opportunity_category":    category,
        "property_id":             prop.id,
        "company_id":              company.id if company else None,
        "score":                   score,
        "confidence_level":        scored["confidence_level"],
        "priority":                scored["priority"],
        "prediction_score":        prediction,
        "owner_behavior_score":    owner_behavior,
        "mispricing_score":        mispricing,
        "tenant_opportunity_score": tenant,
        "thesis":                  thesis,
        "next_action":             next_action,
        "call_script":             call_script,
        "estimated_deal_value":    round(deal_value, 0),
        "estimated_commission":    round(commission, 0),
        "stage":                   "IDENTIFIED",
        "is_active":               True,
    }


def _get_next_action(deal_type: str, prop: Property, company: Optional[Company]) -> str:
    if deal_type == "TENANT_DRIVEN" and company:
        months = company.lease_expiry_months or 0
        urgency = "TODAY" if months <= 6 else "THIS WEEK"
        return (
            f"[{urgency}] Call {company.primary_contact_name or company.name} — "
            f"lease expires in {months}mo, company is at {company.sf_per_head:.0f} SF/head. "
            f"Pitch: off-market relocation to {prop.address} before they start their own search."
        )
    elif deal_type == "ACTIVE_MISPRICED":
        return (
            f"[THIS WEEK] Contact listing agent for {prop.address}. "
            f"Asset has been on market {prop.days_on_market or 0} days. "
            f"Prepare value-add buy-side LOI framing mark-to-market rent upside."
        )
    else:
        return (
            f"[THIS WEEK] Cold approach {prop.owner_name} — owner of {prop.address}. "
            f"Has held {prop.years_owned:.0f}yr. Position as off-market conversation "
            f"with qualified buyers ready to close."
        )


# AVG_LEASE_TERM_YEARS and SALE_COMMISSION_RATE are defined as module-level
# constants at the top of this file. No additional definitions needed.
