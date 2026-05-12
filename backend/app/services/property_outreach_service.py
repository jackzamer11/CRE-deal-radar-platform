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

    queries = [
        f"{owner_name} {submarket} commercial real estate 2025 2026",
        f"{address} office vacancy Northern Virginia 2025",
    ]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        raw_findings: list[str] = []
        for q in queries:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search for: {q}. "
                        "Return up to 3 factual findings relevant to commercial real estate outreach. "
                        "Each finding on its own line. Focus on: ownership changes, permit activity, "
                        "debt or refinance activity, extended vacancy mentions, or submarket commentary. "
                        "Format each as: FACT: [sentence] | URL: [url] | SOURCE: [domain]"
                    ),
                }],
            )
            for block in resp.content:
                if hasattr(block, "text") and block.text.strip():
                    raw_findings.append(block.text.strip())
                    break

        # Parse findings into structured list
        findings = []
        for raw in raw_findings:
            for line in raw.splitlines():
                line = line.strip()
                if not line or "FACT:" not in line:
                    continue
                try:
                    fact_part   = line.split("FACT:")[1].split("|")[0].strip() if "FACT:" in line else ""
                    url_part    = line.split("URL:")[1].split("|")[0].strip()  if "URL:"  in line else ""
                    src_part    = line.split("SOURCE:")[1].strip()              if "SOURCE:" in line else ""
                    if fact_part:
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
    except Exception:
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
) -> dict:
    ctx = _prop_context(p)
    benchmark = _submarket_context(p.get("submarket"))

    # Sanitised tenant context — never reveal company name; pass through industry,
    # headcount, SF needed, lease expiry months from caller-supplied string.
    tenant_hint = f"\nPrimary matched tenant profile (do NOT reveal tenant name): {tenant_context}" if tenant_context else ""

    landlord_rep = p.get("landlord_representative")
    listed_for_sale = bool(p.get("listed_for_sale"))
    sf_avail        = p.get("sf_avail") or 0
    for_sale_with_vacancy = listed_for_sale and sf_avail and sf_avail > 0

    if target_type == "broker" and landlord_rep:
        addressee     = f"the landlord representative ({landlord_rep})"
        framing       = (
            "Write broker-to-broker, peer to peer. Lead with the PRIMARY tenant "
            "profile (industry, headcount, SF range, submarket, approximate lease "
            "expiry months) — but never reveal the tenant company's name."
        )
    elif target_type == "owner":
        addressee = "the property owner"
        framing   = (
            "Write broker-to-owner. Lead with the PRIMARY tenant profile (industry, "
            "headcount, SF range, submarket, approximate lease expiry months) without "
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

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        f"specialising in Northern Virginia office. You are addressing {addressee}. "
        f"{framing} Be concise, specific, and relationship-oriented. "
        f"Anchor numerical claims to the CBRE Q1 2026 NoVA submarket benchmarks provided. "
        f"NEVER reveal the property street address in the email body — refer to it as "
        f"'your property in [submarket]'. "
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
        "2. Email body (3-5 short paragraphs) — greet recipient by name if available; "
        "   describe tenant as 'a [industry] firm with [headcount] employees seeking [SF range] "
        "   in [submarket] with a lease expiring in approximately [X months]'; "
        "   reference CBRE Q1 2026 submarket vacancy and asking rent; "
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


def _build_for_sale_vacancy(p: dict, target_type: str) -> dict:
    """For Sale + Vacancy outreach: property is listed AND has vacant SF.
    References multiple tenants generally — never names any specific tenant."""
    ctx = _prop_context(p)
    benchmark = _submarket_context(p.get("submarket"))
    landlord_rep = p.get("landlord_representative")
    addressee = f"the landlord representative ({landlord_rep})" if landlord_rep else "the property owner"
    framing = "broker-to-broker" if landlord_rep else "broker-to-owner"
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        f"specialising in Northern Virginia office. You are addressing {addressee} ({framing}). "
        f"This property is currently listed for sale AND has vacant space. "
        f"Ask whether they are open to leasing the vacant space while the property is on the market. "
        f"Reference demand generally ('multiple qualified tenants in this submarket') — NEVER name any specific tenant. "
        f"Explain the value: securing a tenant can enhance the property's appeal and shorten the marketing window. "
        f"Anchor to CBRE Q1 2026 NoVA submarket benchmarks provided. "
        f"NEVER suggest specific days of the week. "
        f"Close with: 'I'd welcome a brief call at your convenience.'"
    )
    user = (
        f"Property details:\n{ctx}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body (3-5 short paragraphs) — acknowledge property is listed for sale; "
        "   ask if open to leasing vacant SF while on market; reference demand generally; "
        "   cite CBRE Q1 2026 submarket vacancy and asking rent; "
        "   explain tenant-in-place value proposition\n"
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
    contact_name   = tenant_dict.get("contact_name") or ""
    industry       = tenant_dict.get("industry") or "your industry"
    headcount      = tenant_dict.get("headcount")
    sf_needed      = tenant_dict.get("sf_needed")
    lease_expiry_m = tenant_dict.get("lease_expiry_months")
    submarket_pref = tenant_dict.get("submarket") or submarket
    growth_rate    = tenant_dict.get("growth_rate")

    greeting = (
        f"Hi {contact_name},"
        if contact_name
        else f"Hi {tenant_name} Team,"
    )

    lease_clause = (
        f"Given your lease expiring in approximately {lease_expiry_m} months"
        if lease_expiry_m is not None
        else "Given your upcoming lease decision"
    )

    rent_clause = (
        f" at approximately ${asking_rent:.2f}/SF" if asking_rent else ""
    )
    sf_clause = (
        f" with {sf_avail:,} square feet available" if sf_avail else ""
    )

    tenant_profile_lines = [
        f"Tenant: {tenant_name}",
        f"Industry: {industry}",
        f"Headcount: {headcount if headcount is not None else 'N/A'}",
        f"SF Needed: {f'{sf_needed:,}' if sf_needed else 'N/A'}",
        f"Lease Expiry: {f'{lease_expiry_m} months' if lease_expiry_m is not None else 'N/A'}",
        f"Submarket Preference: {submarket_pref}",
        f"Growth Rate: {growth_rate if growth_rate is not None else 'N/A'}",
    ]
    tenant_profile = "\n".join(tenant_profile_lines)

    property_profile_lines = [
        f"Asset Class: {asset_class}",
        f"Submarket: {submarket}",
        f"SF Available: {sf_avail or 'N/A'}",
        f"Asking Rent: {f'${asking_rent:.2f}/SF' if asking_rent else 'N/A'}",
    ]
    property_profile = "\n".join(property_profile_lines)

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        f"specialising in Northern Virginia office. You are reaching out TO the "
        f"tenant company's decision maker about a property opportunity that fits "
        f"their criteria. "
        f"\n\nHARD CONSTRAINTS:"
        f"\n- Greet by name if available; otherwise use 'Hi [Company Name] Team,'."
        f"\n- Lead with lease-expiry urgency."
        f"\n- Describe the property GENERALLY as 'a {asset_class} property in {submarket}{sf_clause}{rent_clause}'. NEVER reveal street address."
        f"\n- Do NOT describe the tenant company to themselves — they know who they are."
        f"\n- Cite CBRE Q1 2026 NoVA submarket data for {submarket} as market context showing why now is the right time to move."
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
        f"Lease-urgency lead-in to use: \"{lease_clause}, I wanted to reach out about an opportunity in {submarket} that may be a strong fit for your team.\"\n"
        f"Required ask sentence: \"{lease_clause}, I have a property in {submarket} that fits your criteria — can we connect?\"\n\n"
        "Write:\n"
        "1. Email subject line (one line — reference the submarket and the lease timing implicitly, no street address)\n"
        "2. Email body (3-5 short paragraphs) — start with the greeting, then the lease-urgency lead-in, "
        "   describe the property generally (asset class, submarket, SF available, asking rent), "
        "   weave in CBRE Q1 2026 submarket data as market context, end with the required ask sentence "
        "   followed by 'I'd welcome a brief call at your convenience.'\n"
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
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. Write outreach to a property owner "
        "suggesting it may be a good time to list or quietly market their building. "
        "Reference market timing, hold-period, and your ability to run a discreet process. "
        "Anchor any rent / vacancy claims to the CBRE Q1 2026 submarket benchmarks provided."
    )
    user = (
        f"Property details:\n{ctx}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        f"1. Email subject line — address it to {owner_name}\n"
        "2. Email body (3-5 short paragraphs) — reference the owner's hold period, "
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
    dom_signal = p.get("dominant_score_type", "")
    signal_hint = {
        "debt_pressure": "given current financing conditions",
        "hold_period":   "given the current market cycle",
        "vacancy_trend": "given the leasing environment",
    }.get(dom_signal, "given current market conditions")
    landlord_rep  = p.get("landlord_representative") or ""
    sales_contact = p.get("sales_contact") or ""
    addressee = f"the sales broker ({sales_contact})" if sales_contact else "the property owner"
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. "
        f"Write discreet acquisition outreach addressed to {addressee}. "
        f"Frame as: 'I represent a private buyer actively evaluating office assets in [submarket].' "
        f"Reference the property as 'your property in [submarket]' — NEVER reveal the street address. "
        f"Do NOT reveal buyer name, buyer capital, or any specific buyer details. "
        f"Reference the dominant signal subtly without revealing platform intelligence: {signal_hint}. "
        f"The ask: 'Are you open to a conversation about a potential off-market sale?' "
        f"Cite CBRE Q1 2026 NoVA data: cap rates, vacancy, transaction volume. "
        f"NEVER suggest specific days of the week. "
        f"Close with: 'I'd welcome a brief call at your convenience.' "
        f"Tone: professional, discreet, relationship-first."
    )
    user = (
        f"Property details:\n{ctx}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body (3-5 short paragraphs) — greet recipient by name if available; "
        "   frame as representing a private buyer; reference property in submarket only; "
        "   cite CBRE Q1 2026 cap rates and vacancy; ask about off-market conversation\n"
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
    intel_str = ""
    if intel_context:
        intel_lines = [f"- {f['fact']}" for f in intel_context if f.get('fact')]
        if intel_lines:
            intel_str = "Recent market intelligence (weave in naturally as your own market knowledge):\n" + "\n".join(intel_lines)

    # Direction switch: tenant_side writes TO the tenant company about the property
    if direction == "tenant_side" and tenant_dict and outreach_type in ("tenant_match", "for_sale_vacancy"):
        prompt = _build_tenant_side(property_dict, tenant_dict)
    elif outreach_type == "tenant_match":
        prompt = _build_tenant_match(property_dict, tenant_context, target_type, has_secondary_demand=has_secondary_demand)
    elif outreach_type == "for_sale_vacancy":
        prompt = _build_for_sale_vacancy(property_dict, target_type)
    elif outreach_type == "listing_rep":
        prompt = _build_listing_rep(property_dict)
    else:
        prompt = _build_acquisition(property_dict, target_type)

    if intel_str:
        prompt["user"] = f"Market intelligence (weave into email naturally as your own knowledge):\n{intel_str}\n\n" + prompt["user"]

    raw    = _chat(prompt["system"], prompt["user"])
    parsed = _parse_response(raw)

    return {
        "email_subject":          parsed["subject"],
        "email_body":             parsed["email_body"],
        "call_script_opening":    parsed["opening"],
        "call_script_core":       parsed["core"],
        "call_script_pain_probe": parsed["pain_probe"],
        "call_script_close":      parsed["close"],
        "outreach_type":          outreach_type,
        "target_type":            target_type,
        "generated_at":           datetime.utcnow().isoformat(),
    }
