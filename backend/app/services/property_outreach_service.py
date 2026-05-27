"""
Property-side outreach generation — GPT-4o + three template types.

outreach_type values:
  'tenant_match'  — pitch property to owner/broker as hybrid tenant opportunity
  'listing_rep'   — suggest listing the property (broker-side)
  'acquisition'   — acquisition / value-add buyer pitch

target_type (optional, used by 'tenant_match'):
  'broker' — address the landlord representative (broker-to-broker)
  'owner'  — address the owner directly

Requires OPENAI_API_KEY in the environment.
"""
import os
from datetime import datetime
from typing import Optional

AGENT_NAME = "Jack Zamer"
FIRM_NAME  = "The Commercial Real Estate Group"

_SIGNATURE_INSTRUCTION = (
    "Always end the email body with exactly this signature block on its own lines "
    "(after the last paragraph, on a new line):\n\n"
    "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228"
)

# ── Hardcoded sentences (injected post-LLM to survive regeneration) ────────────
# All outreach paths — property-side and tenant-side — use "agent" (Fix 3).
_HARDCODED_INTRO = (
    "My name is Jack Zamer — I'm a commercial real estate agent "
    "focused exclusively on the Northern Virginia office market."
)
_HARDCODED_SOCIAL_PROOF = (
    "I work exclusively in the Northern Virginia office market, "
    "focused on matching tenants to vacancies before they hit the open listings."
)
# Phase 2 confirmed-leasing disclosure (appended to every tenant-side email when
# owner_confirmed_leasing=True).  Exact wording required by spec.
_PHASE2_CONFIRMED_DISCLOSURE = (
    "Please note that the owner has confirmed openness to leasing discussions "
    "for this property — we are actively moving forward."
)


def _inject_hardcoded_sentences(email_body: str) -> str:
    """Post-LLM: ensure hardcoded intro (after greeting) and social proof
    (before signature) survive every regeneration unchanged.
    Uses 'agent' title in all paths (Fix 3 — advisor → agent everywhere).
    """
    if not email_body:
        return email_body

    body = email_body.strip()
    paras = body.split("\n\n")

    # ── Intro after greeting (para[0]) ───────────────────────────────────────
    # Also strip any stale "advisor" variant that may exist in persisted drafts.
    _STALE_ADVISOR_INTRO = (
        "My name is Jack Zamer — I'm a commercial real estate advisor "
        "focused exclusively on the Northern Virginia office market."
    )
    if _STALE_ADVISOR_INTRO in body:
        body = body.replace(_STALE_ADVISOR_INTRO, _HARDCODED_INTRO)
        paras = body.split("\n\n")
    elif _HARDCODED_INTRO not in body:
        insert_at = 1 if len(paras) > 1 else len(paras)
        paras.insert(insert_at, _HARDCODED_INTRO)

    # ── Social proof before signature block ──────────────────────────────────
    if _HARDCODED_SOCIAL_PROOF not in body:
        sig_idx = next(
            (i for i, para in enumerate(paras) if para.strip().startswith("Thank you")),
            None,
        )
        if sig_idx is not None:
            paras.insert(sig_idx, _HARDCODED_SOCIAL_PROOF)
        else:
            paras.append(_HARDCODED_SOCIAL_PROOF)

    return "\n\n".join(paras)


VALID_TYPES = {"tenant_match", "for_sale_vacancy", "listing_rep", "acquisition"}

# CBRE Q1 2026 NoVA office benchmarks (avg full-service rent / vacancy %).
CBRE_2026_BENCHMARKS = {
    "Arlington (Clarendon)":      {"rent": 42.93, "vacancy": 26.5},
    "Arlington (Rosslyn)":        {"rent": 46.85, "vacancy": 20.6},
    "Arlington (Ballston)":       {"rent": 43.19, "vacancy": 21.1},
    "Arlington (Columbia Pike)":  {"rent": 28.22, "vacancy": 32.1},
    "Alexandria (Old Town)":      {"rent": 36.73, "vacancy": 17.6},
    "Tysons":                     {"rent": 39.10, "vacancy": 27.3},
    "Reston":                     {"rent": 37.84, "vacancy": 22.9},
    "Falls Church":               {"rent": 27.87, "vacancy": 10.4},
    "McLean":                     {"rent": 39.21, "vacancy": 7.4},
    "Vienna":                     {"rent": 24.16, "vacancy": 5.2},
    "Fairfax City":               {"rent": 26.23, "vacancy": 8.5},
}


def _submarket_context(submarket: Optional[str]) -> str:
    if not submarket:
        return ""
    b = CBRE_2026_BENCHMARKS.get(submarket, {})
    if b:
        return f"CBRE Q1 2026 {submarket}: avg rent ${b['rent']:.2f}/SF, vacancy {b['vacancy']}%"
    return ""


def search_property_intelligence(property_dict: dict) -> list:
    """Run two Anthropic web searches on the property+owner and return structured findings.

    Returns list of dicts: {fact, source_url, source_name, relevance_score}
    Returns [] if ANTHROPIC_API_KEY not set or on any error.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []

    owner_name = property_dict.get("owner_name", "")
    submarket  = property_dict.get("submarket", "")
    address    = property_dict.get("address", "")

    if not owner_name and not address:
        return []

    # Keep queries short — only the identifiers, no boilerplate context, to stay under 1,500 input tokens
    queries = [
        f"{owner_name} {submarket} office",
        f"{address} Northern Virginia",
    ]

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    try:
        raw_findings: list[str] = []
        for q in queries:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"{q}. "
                        "FACT: [sentence] | URL: [url or N/A] | SOURCE: [domain]"
                    ),
                }],
            )
            for block in resp.content:
                if hasattr(block, "text") and block.text.strip():
                    raw_findings.append(block.text.strip())
                    break

        # Parse findings — strip leading list markers so "1. FACT:" and "- FACT:" both match
        findings = []
        for raw in raw_findings:
            for line in raw.splitlines():
                line = line.strip().lstrip("0123456789.-•*) \t")
                if not line or "FACT:" not in line:
                    continue
                try:
                    fact_part = line.split("FACT:")[1].split("|")[0].strip()
                    url_part  = line.split("URL:")[1].split("|")[0].strip() if "URL:"    in line else ""
                    src_part  = line.split("SOURCE:")[1].strip()             if "SOURCE:" in line else ""
                    if fact_part and len(fact_part) > 10:
                        findings.append({
                            "fact":            fact_part,
                            "source_url":      url_part,
                            "source_name":     src_part,
                            "relevance_score": 2,
                        })
                except Exception:
                    continue
                if len(findings) >= 3:
                    break
            if len(findings) >= 3:
                break

        return findings[:3]
    except anthropic.RateLimitError:
        print("[search-intelligence] Rate limited — skipping panel")
        return []
    except Exception as e:
        print(f"[search_property_intelligence] Search error: {e}")
        return []


def _chat(system: str, user: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def _addr(p: dict) -> str:
    parts = [p.get("address", ""), p.get("city", ""), p.get("state", "")]
    return ", ".join(x for x in parts if x)


def _prop_context(p: dict) -> str:
    lines = [
        f"Property: {p.get('name') or p.get('address', '')}",
        f"Address: {p.get('address', 'N/A')}",
        f"Submarket: {p.get('submarket', 'N/A')}",
        f"Total SF: {p.get('total_sf', 'N/A'):,}" if p.get("total_sf") else "Total SF: N/A",
        f"Year Built: {p.get('year_built', 'N/A')}",
        f"Vacancy %: {p.get('vacancy_pct', 'N/A')}",
        f"SF Available: {p.get('sf_avail', 'N/A')}",
        f"In-Place Rent: ${p['in_place_rent_psf']:.2f}/SF" if p.get("in_place_rent_psf") else "In-Place Rent: N/A",
        f"Market Rent: ${p['market_rent_psf']:.2f}/SF" if p.get("market_rent_psf") else "Market Rent: N/A",
        f"Owner: {p.get('owner_name', 'N/A')} ({p.get('owner_type', 'N/A')})",
        f"Years Owned: {p.get('years_owned', 'N/A')}",
        f"Cap Rate: {p.get('cap_rate', 'N/A')}",
        f"Asking Price/SF: ${p['asking_price_psf']:.2f}" if p.get("asking_price_psf") else "",
        f"Star Rating: {p.get('star_rating', 'N/A')}",
        f"Tenancy: {p.get('tenancy', 'N/A')}",
        f"Landlord Rep: {p.get('landlord_representative', 'N/A')}",
        f"Listed For Sale: {'Yes' if p.get('listed_for_sale') else 'No'}",
        f"Dominant Score: {p.get('dominant_score_type', 'N/A')}",
    ]
    bm = _submarket_context(p.get("submarket"))
    if bm:
        lines.append(f"Submarket Benchmark: {bm}")
    return "\n".join(l for l in lines if l)


# ── Template builders ──────────────────────────────────────────────────────────

def _build_tenant_match(
    p: dict,
    tenant_context: Optional[str],
    target_type: Optional[str],
    has_secondary_demand: bool = False,
    tenant_dict: Optional[dict] = None,
) -> dict:
    ctx = _prop_context(p)
    benchmark = _submarket_context(p.get("submarket"))

    # Fix 3: property-side always uses full street address
    address_display = p.get("address") or p.get("submarket") or "Northern Virginia"
    owner_raw  = p.get("owner_name") or ""
    salutation = f"Dear {owner_raw}," if owner_raw else "Dear Property Owner,"

    # Fix 4: build urgency signal from vacancy / DOM / years_owned
    vacancy_pct = p.get("vacancy_pct")
    sf_avail    = p.get("sf_avail") or 0
    dom         = p.get("days_on_market")
    years_owned = p.get("years_owned")
    if vacancy_pct and float(vacancy_pct) > 0:
        urgency_signal = (
            f"{float(vacancy_pct):.0f}% vacancy ({sf_avail:,} SF available)"
            if sf_avail else f"{float(vacancy_pct):.0f}% vacancy"
        )
    elif dom:
        urgency_signal = f"on market {dom} days"
    elif years_owned:
        urgency_signal = f"held {years_owned} years with current vacancy"
    else:
        urgency_signal = "current vacancy levels"

    # When tenant_dict is available, build a null-safe natural-language hint so GPT
    # never sees bare "N/A" tokens in the profile (which produce awkward output).
    if tenant_dict is not None:
        industry = tenant_dict.get("industry") or "professional services firm"
        sf       = tenant_dict.get("estimated_sf_needed")
        exp      = tenant_dict.get("lease_expiry_months")
        sf_str   = f"{sf:,} SF" if sf else "office space in the area"
        exp_str  = f"approximately {exp} months" if exp is not None else "in the coming months"
        tenant_hint = (
            f"\nPrimary matched tenant profile (do NOT reveal tenant name): "
            f"a {industry} firm seeking {sf_str} in "
            f"{p.get('submarket', 'Northern Virginia')} with a lease expiring {exp_str}"
        )
    elif tenant_context:
        tenant_hint = f"\nPrimary matched tenant profile (do NOT reveal tenant name): {tenant_context}"
    else:
        tenant_hint = ""

    landlord_rep = p.get("landlord_representative")
    listed_for_sale = bool(p.get("listed_for_sale"))
    for_sale_with_vacancy = listed_for_sale and sf_avail and sf_avail > 0

    if target_type == "broker" and landlord_rep:
        addressee     = f"the landlord representative ({landlord_rep})"
        framing       = (
            "Write broker-to-broker, peer to peer. Lead with the PRIMARY tenant "
            "profile (industry, SF range, submarket, approximate lease "
            "expiry months) — but never reveal the tenant company's name."
        )
    elif target_type == "owner":
        addressee = "the property owner"
        framing   = (
            "Write broker-to-owner. Lead with the PRIMARY tenant profile (industry, "
            "SF range, submarket, approximate lease expiry months) without "
            "revealing the tenant company's name. Position the call as bringing them a "
            "credible prospect for the vacancy."
        )
    else:
        addressee = "the property owner" if not landlord_rep else f"the landlord representative ({landlord_rep})"
        framing   = (
            "Choose tone based on whether the recipient is the owner or a listing "
            "broker. Lead with the PRIMARY tenant profile; never reveal the tenant "
            "company's name; refer to them by industry / size / timing. "
        )

    secondary_clause = ""
    if has_secondary_demand:
        secondary_clause = (
            "\nAfter introducing the primary tenant, include exactly ONE sentence "
            "referencing secondary demand generally: 'Additionally, we have seen "
            "interest from other qualified tenants in this submarket with similar "
            "space requirements.' Do NOT describe secondary tenants specifically."
        )

    sale_clause = ""
    if for_sale_with_vacancy:
        sale_clause = (
            "\nIMPORTANT: This property is currently LISTED FOR SALE and has vacant "
            "space. Frame the outreach as asking whether they are open to leasing the "
            "vacant SF while the property is on the market — many sellers prefer a "
            "tenant in place to a longer marketing window."
        )

    # Fix 5: explicit instruction to cite one benchmark data point (omit if no data)
    benchmark_clause = (
        f" Include exactly ONE submarket data point from the CBRE Q1 2026 benchmark "
        f"mid-body (either vacancy rate or avg asking rent for {p.get('submarket', 'the submarket')})."
        if benchmark else ""
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        f"specialising in Northern Virginia office. You are addressing {addressee}. "
        f"{framing} Be concise, specific, and relationship-oriented. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Use the full property address in the email body: 'your property at {address_display}'. "
        f"The second body sentence must reference the urgency signal: {urgency_signal}. "
        f"Anchor numerical claims to the CBRE Q1 2026 NoVA submarket benchmarks provided.{benchmark_clause} "
        f"NEVER reveal the tenant company name — describe by industry, size, and timing only. "
        f"NEVER suggest specific days of the week. "
        f"Close every email and call script close with: 'I'd welcome a brief call at your convenience.' "
        f"{sale_clause}{secondary_clause}"
    )
    user = (
        f"Property details:\n{ctx}{tenant_hint}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body — maximum 150 words (excluding signature block); "
        "   greet recipient with the salutation above; "
        "   describe tenant as 'a [industry] firm seeking [SF range] "
        "   in [submarket] with a lease expiring in approximately [X months]'; "
        "   reference one CBRE Q1 2026 submarket data point; "
        "   request tour availability, asking rent confirmation, OM materials (broker) or 15-min call (owner)\n"
        "3. Call script: Opening (2 sentences)\n"
        "4. Call script: Core message (3 sentences)\n"
        "5. Call script: Pain probe question (1 sentence)\n"
        "6. Call script: Close / next step (end with 'I'd welcome a brief call at your convenience.')\n\n"
        "Format EXACTLY as:\n"
        "SUBJECT: <subject>\n"
        "EMAIL:\n<body>\n"
        "OPENING:\n<opening>\n"
        "CORE:\n<core>\n"
        "PAIN_PROBE:\n<probe>\n"
        "CLOSE:\n<close>"
    )
    return {"system": system, "user": user}


def _build_for_sale_vacancy(
    p: dict,
    target_type: str,
    tenant_context: Optional[str] = None,
    tenant_dict: Optional[dict] = None,
) -> dict:
    """For Sale + Vacancy outreach: property is listed AND has vacant SF.

    When tenant_context is provided (specific matched tenant), the email leads
    with that tenant's profile while maintaining the For Sale + Vacancy framing.
    Without it, references demand generally ('multiple qualified tenants').
    """
    ctx = _prop_context(p)
    benchmark = _submarket_context(p.get("submarket"))
    submarket = p.get("submarket") or "Northern Virginia"
    landlord_rep = p.get("landlord_representative")
    addressee = f"the landlord representative ({landlord_rep})" if landlord_rep else "the property owner"
    framing = "broker-to-broker" if landlord_rep else "broker-to-owner"

    # Fix 3: property-side uses full street address
    address_display = p.get("address") or submarket
    owner_raw  = p.get("owner_name") or ""
    salutation = f"Dear {owner_raw}," if owner_raw else "Dear Property Owner,"

    # Fix 4: urgency signal
    dom         = p.get("days_on_market")
    sf_avail    = p.get("sf_avail") or 0
    vacancy_pct = p.get("vacancy_pct")
    if dom and int(dom) > 0:
        urgency_signal = (
            f"listed for {dom} days with {sf_avail:,} SF vacant" if sf_avail
            else f"listed for {dom} days"
        )
    elif vacancy_pct and float(vacancy_pct) > 0:
        urgency_signal = f"{float(vacancy_pct):.0f}% vacancy" + (f" ({sf_avail:,} SF)" if sf_avail else "")
    else:
        urgency_signal = f"{sf_avail:,} SF currently vacant" if sf_avail else "current vacancy"

    # Fix 5: benchmark clause (omit if no data)
    benchmark_clause = (
        f" Include exactly ONE submarket data point from the CBRE Q1 2026 benchmark "
        f"mid-body (vacancy rate or avg asking rent for {submarket})."
        if benchmark else ""
    )

    # ── Phase 1: industry-only tenant reference (Fix 1) ─────────────────────
    # When a lead tenant is known, reference their INDUSTRY ONLY.
    # Never mention headcount, SF need, or lease timing in Phase 1 property-side
    # outreach — those details are for Phase 2 tenant-side copy only.
    # The Phase 1 closing line ("I have a potential tenant in mind in the X space…")
    # is injected post-LLM in generate_property_outreach so it survives regeneration.
    lead_industry: Optional[str] = None
    if tenant_dict is not None:
        lead_industry = (tenant_dict.get("industry") or "professional services").strip()
        tenant_hint = (
            f"\nLead tenant industry (reference this industry ONLY — "
            f"NEVER mention headcount, SF requirements, or lease timing): {lead_industry}"
        )
        demand_clause = (
            f"Reference a single potential tenant in the {lead_industry} space — "
            f"use ONLY the industry label, no headcount numbers, no SF ranges, no lease timing. "
            f"The singular, direct ask must be verbatim: "
            f"'Would you be open to leasing a portion of the available space while "
            f"the property is being marketed for sale?' "
            f"Do NOT reference multiple tenants. Do NOT describe the tenant beyond their industry."
        )
    elif tenant_context:
        # Fallback: text-based context passed in — extract first word-phrase as hint
        tenant_hint = f"\nTenant context (reference industry only — no name, no headcount): {tenant_context}"
        demand_clause = (
            "Reference a potential tenant by industry only — no company name, no headcount, no SF numbers. "
            "The singular, direct ask must be: "
            "'Would you be open to leasing a portion of the available space while the property is "
            "being marketed for sale?' "
            "Do NOT reference multiple tenants."
        )
    else:
        tenant_hint = ""
        demand_clause = (
            "Reference demand generally — NEVER name any specific tenant. "
            "Ask: 'Would you be open to leasing a portion of the available space while the property "
            "is being marketed for sale?'"
        )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        f"specialising in Northern Virginia office. You are addressing {addressee} ({framing}). "
        f"This property is currently listed for sale AND has vacant space. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Use the full property address in the email body: 'your property at {address_display}'. "
        f"The second sentence must reference the urgency signal: {urgency_signal}. "
        f"{demand_clause} "
        f"Explain the value: securing a tenant can enhance the property's appeal and shorten "
        f"the marketing window. "
        f"Anchor to CBRE Q1 2026 NoVA submarket benchmarks provided.{benchmark_clause} "
        f"NEVER suggest specific days of the week. "
        f"Close with: 'I'd welcome a brief call at your convenience.'"
    )
    industry_ref = f"the {lead_industry} space (industry only — no company name, no headcount, no SF)" if lead_industry else "a potential tenant"
    body_instruction = (
        "2. Email body — maximum 150 words (excluding signature block); "
        f"  use full property address; acknowledge property is listed for sale; "
        f"  reference one potential tenant in {industry_ref}; "
        "   include verbatim ask: 'Would you be open to leasing a portion of the available space "
        "   while the property is being marketed for sale?'; "
        "   cite one CBRE Q1 2026 submarket data point\n"
        if (lead_industry or tenant_context) else
        "2. Email body — maximum 150 words (excluding signature block); "
        "   use full property address; acknowledge property is listed for sale; "
        "   ask if open to leasing vacant SF while on market; reference demand generally; "
        "   cite one CBRE Q1 2026 submarket data point; "
        "   explain tenant-in-place value proposition\n"
    )
    user = (
        f"Property details:\n{ctx}{tenant_hint}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        f"{body_instruction}"
        "3. Call script: Opening (2 sentences)\n"
        "4. Call script: Core message (3 sentences)\n"
        "5. Call script: Pain probe question (1 sentence)\n"
        "6. Call script: Close (end with 'I'd welcome a brief call at your convenience.')\n\n"
        "Format EXACTLY as:\n"
        "SUBJECT: <subject>\n"
        "EMAIL:\n<body>\n"
        "OPENING:\n<opening>\n"
        "CORE:\n<core>\n"
        "PAIN_PROBE:\n<probe>\n"
        "CLOSE:\n<close>"
    )
    return {"system": system, "user": user}


def _build_tenant_side(p: dict, tenant_dict: dict) -> dict:
    """Tenant-side outreach: write TO the tenant's decision maker about a property
    that fits their requirements. Never reveals the property street address; never
    describes the tenant to themselves; leads with lease expiry urgency."""
    submarket = p.get("submarket") or "Northern Virginia"
    benchmark = _submarket_context(p.get("submarket"))
    asset_class = p.get("asset_class") or "office"
    sf_avail = p.get("sf_avail") or 0
    asking_rent = p.get("market_rent_psf") or p.get("asking_price_psf")

    tenant_name    = tenant_dict.get("name") or "the tenant"
    contact_name   = tenant_dict.get("primary_contact_name") or ""
    industry       = tenant_dict.get("industry") or "professional services firm"
    # Fix 4: headcount is NOT passed to tenant-side copy — SF needed and lease
    # expiry are the permitted identifiers. Never reference headcount in tenant emails.
    sf_needed      = tenant_dict.get("estimated_sf_needed")
    lease_expiry_m = tenant_dict.get("lease_expiry_months")
    submarket_pref = tenant_dict.get("current_submarket") or submarket

    greeting = (
        f"Hi {contact_name},"
        if contact_name
        else f"Hi {tenant_name} Team,"
    )

    lease_clause = (
        f"With your lease expiring in approximately {lease_expiry_m} months"
        if lease_expiry_m is not None
        else "With your upcoming lease expiry"
    )

    rent_clause = (
        f" at approximately ${asking_rent:.2f}/SF" if asking_rent else ""
    )
    sf_clause = (
        f" with {sf_avail:,} square feet available" if sf_avail else ""
    )

    sf_display  = f"{sf_needed:,} SF" if sf_needed else "office space in the area"
    exp_display = f"{lease_expiry_m} months" if lease_expiry_m is not None else "in the coming months"

    # Space-fit note: reference SF match only — no headcount (Fix 4)
    space_fit_note = (
        f"Space-fit note: the property has {sf_avail:,} SF available — "
        f"reference this as well-matched for a company seeking {sf_display}."
        if sf_avail and sf_needed else ""
    )

    tenant_profile_lines = [
        f"Tenant: {tenant_name}",
        f"Industry: {industry}",
        f"SF Needed: {sf_display}",
        f"Lease Expiry: {exp_display}",
        f"Submarket Preference: {submarket_pref}",
    ]
    tenant_profile = "\n".join(tenant_profile_lines)

    property_profile_lines = [
        f"Asset Class: {asset_class}",
        f"Submarket: {submarket}",
        f"SF Available: {sf_avail or 'N/A'}",
        f"Asking Rent: {f'${asking_rent:.2f}/SF' if asking_rent else 'N/A'}",
    ]
    property_profile = "\n".join(property_profile_lines)

    # SF-fit constraint for system prompt (no headcount — Fix 4)
    sf_fit_constraint = (
        f"\n- The available space ({sf_avail:,} SF) is well-matched for a company "
        f"seeking approximately {sf_display} — weave this fit naturally into the email body "
        "without quoting their own data back to them."
        if sf_avail and sf_needed else ""
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate agent "
        f"specialising in Northern Virginia office. You are reaching out TO the "
        f"tenant company's decision maker about a property opportunity that fits "
        f"their criteria. "
        f"\n\nHARD CONSTRAINTS:"
        f"\n- Greet by name if available; otherwise use 'Hi [Company Name] Team,'."
        f"\n- The second sentence of the email body (immediately after the greeting) must open with: "
        f"'{lease_clause}, I wanted to reach out about an opportunity in {submarket} "
        f"that may be a strong fit for your team.' — do NOT repeat the lease expiry later in the email."
        f"\n- Describe the property GENERALLY as 'a {asset_class} property in {submarket}{sf_clause}{rent_clause}'. NEVER reveal street address."
        f"\n- Do NOT describe the tenant company to themselves — they know who they are."
        f"\n- Do NOT reference the tenant's headcount or team size in the email."
        + sf_fit_constraint
        + f"\n- Cite ONE CBRE Q1 2026 NoVA submarket data point for {submarket} as market context."
        f"\n- Tone: knowledgeable, credible, consultative — position yourself as a market expert, not a salesperson."
        f"\n- Do NOT reveal that any web search or platform tool was used."
        f"\n- Do NOT reveal other tenants or other properties being considered."
        f"\n- NEVER suggest specific days of the week."
        f"\n- Close with EXACTLY: 'I'd welcome a brief call at your convenience.'"
    )

    user = (
        f"Tenant profile (for your context — do NOT describe the tenant to themselves):\n{tenant_profile}\n\n"
        f"Property profile (describe generally — NEVER reveal street address):\n{property_profile}\n\n"
        f"Market benchmark: {benchmark or 'N/A'}\n\n"
        f"Greeting to use: {greeting}\n"
        f"Lease-urgency lead-in (second sentence after greeting, use verbatim): "
        f"\"{lease_clause}, I wanted to reach out about an opportunity in {submarket} that may be a strong fit for your team.\"\n"
        + (f"{space_fit_note}\n" if space_fit_note else "")
        + "\nWrite:\n"
        "1. Email subject line (one line — reference the submarket and the lease timing implicitly, no street address)\n"
        "2. Email body — maximum 150 words (excluding signature block); start with the greeting, "
        "   then the lease-urgency lead-in verbatim, "
        "   describe the property generally (asset class, submarket, SF available, asking rent), "
        + (f"   mention that the available {sf_avail:,} SF is well-suited for a company seeking {sf_display}, " if sf_avail and sf_needed else "")
        + "   weave in one CBRE Q1 2026 submarket data point as market context, end with "
        "'I'd welcome a brief call at your convenience.'\n"
        "3. Call script: Opening (2 sentences — knowledgeable, consultative)\n"
        "4. Call script: Core message (3 sentences — lease urgency, property fit, CBRE market context)\n"
        "5. Call script: Pain probe question (1 sentence — about current lease decision drivers)\n"
        "6. Call script: Close (end with 'I'd welcome a brief call at your convenience.')\n\n"
        "Format EXACTLY as:\n"
        "SUBJECT: <subject>\n"
        "EMAIL:\n<body>\n"
        "OPENING:\n<opening>\n"
        "CORE:\n<core>\n"
        "PAIN_PROBE:\n<probe>\n"
        "CLOSE:\n<close>"
    )
    return {"system": system, "user": user}


def _build_listing_rep(p: dict) -> dict:
    ctx = _prop_context(p)
    benchmark = _submarket_context(p.get("submarket"))

    # Fix: safe address, owner_name, and salutation
    address_display = p.get("address") or p.get("submarket") or "Northern Virginia"
    owner_raw  = p.get("owner_name") or ""
    salutation = f"Dear {owner_raw}," if owner_raw else "Dear Property Owner,"

    # Fix 4: urgency signal
    years_owned = p.get("years_owned")
    vacancy_pct = p.get("vacancy_pct")
    sf_avail    = p.get("sf_avail") or 0
    if vacancy_pct and float(vacancy_pct) > 0:
        urgency_signal = (
            f"{float(vacancy_pct):.0f}% vacancy ({sf_avail:,} SF empty)" if sf_avail
            else f"{float(vacancy_pct):.0f}% vacancy"
        )
    elif years_owned:
        urgency_signal = f"{years_owned}-year hold period"
    else:
        urgency_signal = "current market conditions"

    # Fix 5: benchmark clause (omit if no data)
    benchmark_clause = (
        " Include exactly ONE submarket data point (vacancy rate or avg asking rent) from CBRE Q1 2026."
        if benchmark else ""
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. Write outreach to a property owner "
        "suggesting it may be a good time to list or quietly market their building. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Use the full property address: 'your property at {address_display}'. "
        f"The second sentence must reference this urgency signal: {urgency_signal}. "
        "Reference market timing, hold-period, and your ability to run a discreet process. "
        f"Anchor any rent / vacancy claims to the CBRE Q1 2026 submarket benchmarks provided.{benchmark_clause}"
    )
    user = (
        f"Property details:\n{ctx}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body — maximum 150 words (excluding signature block); reference the owner's hold period, "
        "   current market conditions using the CBRE benchmarks above, and the value of "
        "   a confidential process before vacancy erodes NOI further.\n"
        "3. Call script: Opening (2 sentences)\n"
        "4. Call script: Core message (3 sentences)\n"
        "5. Call script: Pain probe question (1 sentence)\n"
        "6. Call script: Close / next step (2 sentences)\n\n"
        "Format EXACTLY as:\n"
        "SUBJECT: <subject>\n"
        "EMAIL:\n<body>\n"
        "OPENING:\n<opening>\n"
        "CORE:\n<core>\n"
        "PAIN_PROBE:\n<probe>\n"
        "CLOSE:\n<close>"
    )
    return {"system": system, "user": user}


def _build_acquisition(p: dict, target_type: str) -> dict:
    ctx = _prop_context(p)
    benchmark = _submarket_context(p.get("submarket"))
    submarket  = p.get("submarket") or "Northern Virginia"
    dom_signal = p.get("dominant_score_type", "")
    signal_hint = {
        "debt_pressure": "given current financing conditions",
        "hold_period":   "given the current market cycle",
        "vacancy_trend": "given the leasing environment",
    }.get(dom_signal, "given current market conditions")

    # Fix 3: property-side uses full street address
    address_display = p.get("address") or submarket
    sales_contact   = p.get("sales_contact") or ""
    recipient_raw   = sales_contact or p.get("owner_name") or ""
    salutation      = f"Dear {recipient_raw}," if recipient_raw else "Dear Property Owner,"
    addressee       = f"the sales broker ({sales_contact})" if sales_contact else "the property owner"

    # Fix 4: urgency signal
    dom         = p.get("days_on_market")
    vacancy_pct = p.get("vacancy_pct")
    years_owned = p.get("years_owned")
    if dom and int(dom) > 30:
        urgency_signal = f"on market {dom} days"
    elif vacancy_pct and float(vacancy_pct) > 0:
        urgency_signal = f"{float(vacancy_pct):.0f}% vacancy"
    elif years_owned:
        urgency_signal = f"{years_owned}-year hold period"
    else:
        urgency_signal = signal_hint

    # Fix 5: benchmark clause (omit if no data)
    benchmark_clause = (
        " Include exactly ONE submarket data point (vacancy rate or avg asking rent) from CBRE Q1 2026 mid-body."
        if benchmark else ""
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. "
        f"Write discreet acquisition outreach addressed to {addressee}. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Frame as: 'I represent a private buyer actively evaluating office assets in {submarket}.' "
        f"Use the full property address: 'your property at {address_display}'. "
        f"The second sentence must reference the urgency signal: {urgency_signal}. "
        f"Do NOT reveal buyer name, buyer capital, or any specific buyer details. "
        f"Reference the dominant signal subtly without revealing platform intelligence: {signal_hint}. "
        f"The ask: 'Are you open to a conversation about a potential off-market sale?' "
        f"Cite CBRE Q1 2026 NoVA data: cap rates, vacancy, transaction volume.{benchmark_clause} "
        f"NEVER suggest specific days of the week. "
        f"Close with: 'I'd welcome a brief call at your convenience.' "
        f"Tone: professional, discreet, relationship-first."
    )
    user = (
        f"Property details:\n{ctx}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body — maximum 150 words (excluding signature block); "
        "   use full property address; "
        "   frame as representing a private buyer; "
        "   cite one CBRE Q1 2026 cap rate or vacancy data point; ask about off-market conversation\n"
        "3. Call script: Opening (2 sentences)\n"
        "4. Call script: Core message (3 sentences)\n"
        "5. Call script: Pain probe question (1 sentence)\n"
        "6. Call script: Close (end with 'I'd welcome a brief call at your convenience.')\n\n"
        "Format EXACTLY as:\n"
        "SUBJECT: <subject>\n"
        "EMAIL:\n<body>\n"
        "OPENING:\n<opening>\n"
        "CORE:\n<core>\n"
        "PAIN_PROBE:\n<probe>\n"
        "CLOSE:\n<close>"
    )
    return {"system": system, "user": user}


def _parse_response(raw: str) -> dict:
    def _section(tag: str, next_tags: list[str]) -> str:
        start = raw.find(f"{tag}:\n")
        if start == -1:
            start = raw.find(f"{tag}: ")
            if start == -1:
                return ""
            line_end = raw.find("\n", start)
            return raw[start + len(tag) + 2: line_end if line_end != -1 else None].strip()
        content_start = start + len(tag) + 2
        ends = []
        for nt in next_tags:
            idx = raw.find(f"\n{nt}:", content_start)
            if idx != -1:
                ends.append(idx)
        content_end = min(ends) if ends else len(raw)
        return raw[content_start:content_end].strip()

    subject = _section("SUBJECT", ["EMAIL"])
    if not subject:
        for line in raw.splitlines():
            if line.startswith("SUBJECT:"):
                subject = line[8:].strip()
                break

    return {
        "subject":    subject,
        "email_body": _section("EMAIL",      ["OPENING", "CORE", "PAIN_PROBE", "CLOSE"]),
        "opening":    _section("OPENING",    ["CORE",    "PAIN_PROBE", "CLOSE"]),
        "core":       _section("CORE",       ["PAIN_PROBE", "CLOSE"]),
        "pain_probe": _section("PAIN_PROBE", ["CLOSE"]),
        "close":      _section("CLOSE",      []),
    }


def generate_property_outreach(
    property_dict: dict,
    outreach_type: str,
    target_type: Optional[str] = None,
    tenant_context: Optional[str] = None,
    intel_context: Optional[list] = None,
    direction: str = "property_side",
    tenant_dict: Optional[dict] = None,
    has_secondary_demand: bool = False,
) -> dict:
    """
    Call GPT-4o and return a structured outreach dict.

    target_type auto-resolved when not provided:
      tenant_match: 'broker' if landlord_representative set, else 'owner'
      acquisition:  'sales_broker' if sales_contact set, else 'owner'
      listing_rep:  always 'owner'
    """
    if outreach_type not in VALID_TYPES:
        raise ValueError(f"outreach_type must be one of {VALID_TYPES}")

    # Auto-resolve target_type
    if direction == "tenant_side":
        target_type = "tenant"
    elif target_type is None:
        if outreach_type == "tenant_match":
            target_type = "broker" if property_dict.get("landlord_representative") else "owner"
        elif outreach_type == "acquisition":
            target_type = "sales_broker" if property_dict.get("sales_contact") else "owner"
        else:
            target_type = "owner"

    # Build intel string from selected findings
    intel_lines: list[str] = []
    if intel_context:
        intel_lines = [
            f["fact"] if isinstance(f, dict) else f
            for f in intel_context
            if (f.get("fact") if isinstance(f, dict) else f)
        ]

    # Direction switch: tenant_side writes TO the tenant company about the property
    if direction == "tenant_side" and tenant_dict and outreach_type in ("tenant_match", "for_sale_vacancy"):
        prompt = _build_tenant_side(property_dict, tenant_dict)
    elif outreach_type == "tenant_match":
        prompt = _build_tenant_match(property_dict, tenant_context, target_type, has_secondary_demand=has_secondary_demand, tenant_dict=tenant_dict)
    elif outreach_type == "for_sale_vacancy":
        prompt = _build_for_sale_vacancy(property_dict, target_type, tenant_context, tenant_dict=tenant_dict)
    elif outreach_type == "listing_rep":
        prompt = _build_listing_rep(property_dict)
    else:
        prompt = _build_acquisition(property_dict, target_type)

    if intel_lines:
        intel_block = (
            "Recent market intelligence to weave naturally into the email "
            "(do NOT list these as bullet points — reference them conversationally "
            "as your own knowledge of the market, and make sure at least one of "
            "these facts is incorporated into the email body):\n"
            + "\n".join(f"- {fact}" for fact in intel_lines)
        )
        prompt["user"] = f"{intel_block}\n\n" + prompt["user"]
        prompt["system"] = (
            prompt["system"]
            + " You have been provided with recent market intelligence findings — "
              "you MUST reference at least one of them conversationally in the email body "
              "(not as a bulleted list)."
        )

    # Inject signature instruction into every call's system prompt
    prompt["system"] = prompt["system"] + "\n\n" + _SIGNATURE_INSTRUCTION

    raw    = _chat(prompt["system"], prompt["user"])
    parsed = _parse_response(raw)

    # ── Post-LLM sentence injection ─────────────────────────────────────────
    # "agent" title used in all paths (Fix 3 — advisor → agent everywhere).
    is_tenant_side = (direction == "tenant_side")
    email_body = _inject_hardcoded_sentences(parsed["email_body"])

    # Safety strip: property-side copy must never mention headcount.
    # Scan for "employees", "employee", or a bare number followed by "HC".
    if not is_tenant_side:
        import re as _re
        _hc_pattern = _re.compile(
            r'[^.!?\n]*\b(?:employees?|\d+\s*HC)\b[^.!?\n]*[.!?]',
            _re.IGNORECASE,
        )
        hc_hits = _hc_pattern.findall(email_body)
        for hit in hc_hits:
            print(f"[headcount-strip] WARNING: removed sentence: {hit.strip()}")
        if hc_hits:
            email_body = _hc_pattern.sub("", email_body)
            email_body = _re.sub(r"  +", " ", email_body).strip()

    # Fix 1: Phase 1 for_sale_vacancy → inject closing line with lead industry.
    # "I have a potential tenant in mind in the [X] space — happy to share more
    # if there's interest in a conversation."
    # Only injected property-side (Phase 1); Phase 2 (tenant_side) gets the
    # confirmed-leasing disclosure instead.
    if outreach_type == "for_sale_vacancy" and not is_tenant_side:
        lead_industry_val = ""
        if tenant_dict:
            lead_industry_val = (tenant_dict.get("industry") or "").strip()
        if lead_industry_val:
            phase1_closing = (
                f"I have a potential tenant in mind in the {lead_industry_val} space — "
                "happy to share more if there's interest in a conversation."
            )
            if phase1_closing not in email_body:
                if "Thank you," in email_body:
                    email_body = email_body.replace(
                        "Thank you,",
                        f"{phase1_closing}\n\nThank you,",
                        1,
                    )
                else:
                    email_body += f"\n\n{phase1_closing}"

    # Fix 2: Phase 2 tenant-side → inject confirmed-leasing disclosure.
    # This applies whenever direction="tenant_side" (the endpoint already guards
    # for owner_confirmed_leasing=True, so this is always correct).
    # The get_tenant_outreach endpoint also strips/updates any prior disclosure,
    # but we inject the canonical wording here so it works from any call site.
    if is_tenant_side:
        if _PHASE2_CONFIRMED_DISCLOSURE not in email_body:
            if "Thank you," in email_body:
                email_body = email_body.replace(
                    "Thank you,",
                    f"{_PHASE2_CONFIRMED_DISCLOSURE}\n\nThank you,",
                    1,
                )
            else:
                email_body += f"\n\n{_PHASE2_CONFIRMED_DISCLOSURE}"

    return {
        "email_subject":          parsed["subject"],
        "email_body":             email_body,
        "call_script_opening":    parsed["opening"],
        "call_script_core":       parsed["core"],
        "call_script_pain_probe": parsed["pain_probe"],
        "call_script_close":      parsed["close"],
        "outreach_type":          outreach_type,
        "target_type":            target_type,
        "generated_at":           datetime.utcnow().isoformat(),
    }
