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

VALID_TYPES = {"tenant_match", "listing_rep", "acquisition"}

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

def _build_tenant_match(p: dict, tenant_context: Optional[str], target_type: Optional[str]) -> dict:
    ctx = _prop_context(p)
    benchmark = _submarket_context(p.get("submarket"))

    # Sanitised tenant context — never reveal company name; pass through industry,
    # headcount, SF needed, lease expiry months from caller-supplied string.
    tenant_hint = f"\nTenant context (do NOT reveal tenant name): {tenant_context}" if tenant_context else ""

    landlord_rep = p.get("landlord_representative")
    listed_for_sale = bool(p.get("listed_for_sale"))
    sf_avail        = p.get("sf_avail") or 0
    for_sale_with_vacancy = listed_for_sale and sf_avail and sf_avail > 0

    if target_type == "broker" and landlord_rep:
        addressee     = f"the landlord representative ({landlord_rep})"
        framing       = (
            "Write broker-to-broker, peer to peer. Reference market context, the "
            "available SF, and that you have a credible tenant in the wings — but "
            "never reveal the tenant company's name. Use the tenant_context strictly "
            "to paint the profile (industry, size, timing)."
        )
    elif target_type == "owner":
        addressee = "the property owner"
        framing   = (
            "Write broker-to-owner. Lead with the tenant profile (size, industry, "
            "timing) without revealing the tenant company's name. Position the call "
            "as bringing them a credible prospect for the vacancy."
        )
    else:
        # Default — same as owner-direct
        addressee = "the property owner" if not landlord_rep else f"the landlord representative ({landlord_rep})"
        framing   = (
            "Choose tone based on whether the recipient is the owner or a listing "
            "broker. Never reveal the tenant company's name; refer to them by "
            "industry / size / timing. "
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
        f"Anchor numerical claims to the CBRE Q1 2026 NoVA submarket benchmarks "
        f"provided. {sale_clause}"
    )
    user = (
        f"Property details:\n{ctx}{tenant_hint}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body (3-5 short paragraphs)\n"
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
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. Write outreach on behalf of a principal "
        "buyer/investor exploring value-add acquisitions in Northern Virginia office. "
        "Focus on the value-add thesis: repositioning, re-leasing, or capital deployment. "
        "Anchor pricing / cap-rate claims to the CBRE Q1 2026 submarket benchmarks provided."
    )
    user = (
        f"Property details:\n{ctx}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body (3-5 short paragraphs)\n"
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
    if target_type is None:
        if outreach_type == "tenant_match":
            target_type = "broker" if property_dict.get("landlord_representative") else "owner"
        elif outreach_type == "acquisition":
            target_type = "sales_broker" if property_dict.get("sales_contact") else "owner"
        else:
            target_type = "owner"

    if outreach_type == "tenant_match":
        prompt = _build_tenant_match(property_dict, tenant_context, target_type)
    elif outreach_type == "listing_rep":
        prompt = _build_listing_rep(property_dict)
    else:
        prompt = _build_acquisition(property_dict)

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
