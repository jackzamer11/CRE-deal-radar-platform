"""
Property-side outreach generation — GPT-4o + three template types.

outreach_type values (broker template removed — targeting is now smart):
  'tenant_match'  — pitch the property to broker or owner as tenant opportunity
  'listing_rep'   — suggest listing the property to the owner (always owner-targeted)
  'acquisition'   — acquisition / value-add buyer pitch to sales broker or owner direct

target_type values:
  'broker'        — landlord rep (tenant_match: broker-to-broker framing)
  'sales_broker'  — sales/investment broker (acquisition: broker-to-broker framing)
  'owner'         — property owner direct (all templates, when no broker)

Requires OPENAI_API_KEY in the environment.
"""
import os
from datetime import datetime
from typing import Optional

AGENT_NAME = "Jack Zamer"
FIRM_NAME  = "The Commercial Real Estate Group"

VALID_TYPES = {"tenant_match", "listing_rep", "acquisition"}

# CBRE Q1 2026 NoVA Office Benchmarks — injected into GPT-4o prompts
CBRE_BENCHMARKS = """
CBRE Q1 2026 Northern Virginia Office Market Benchmarks:
- Overall vacancy: 21.4%
- Average asking rent: $34.80/SF NNN
- Net absorption: -187,000 SF (negative)
- Submarkets tightening: Reston (17.2%), Tysons (18.9%)
- Submarkets soft: Arlington Columbia Pike (28.1%), Fairfax City (29.4%)
- Trophy/Class A vacancy: 18.2% | Class B: 23.6% | Class C: 31.1%
"""


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
        f"Landlord Rep Contact: {p.get('landlord_rep_contact', 'N/A')}",
        f"Sales Contact: {p.get('sales_contact', 'N/A')}",
    ]
    return "\n".join(l for l in lines if l)


# ── Template builders ──────────────────────────────────────────────────────────

def _build_tenant_match(
    p: dict,
    target_type: str,
    tenant_context: Optional[str],
) -> dict:
    ctx = _prop_context(p)
    tenant_hint = f"\nTenant context: {tenant_context}" if tenant_context else ""
    benchmarks  = CBRE_BENCHMARKS

    if target_type == "broker":
        broker_name = p.get("landlord_rep_contact") or p.get("landlord_representative") or "the listing broker"
        system = (
            f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker in Northern Virginia. "
            "Write a broker-to-broker outreach email and call script to the listing/landlord broker. "
            "Your tone is professional peer-to-peer. You represent a qualified tenant and want to "
            "discuss space availability, asking rent, and tour scheduling."
        )
        user = (
            f"Property details:\n{ctx}\n"
            f"Target: {broker_name} (landlord representative)\n"
            f"{benchmarks}{tenant_hint}\n\n"
            "Write:\n"
            f"1. Email subject line — address it to {broker_name}\n"
            "2. Email body (3-4 short paragraphs) — broker-to-broker framing\n"
            "   Mention: qualified tenant, their industry and headcount, SF need and submarket, "
            "   asking for tour availability and OM/rent confirmation. "
            "   Cite relevant CBRE Q1 2026 submarket vacancy and rent benchmarks.\n"
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
    else:  # owner
        owner_name = p.get("owner_name") or "the property owner"
        system = (
            f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker in Northern Virginia. "
            "Write a broker-to-owner outreach email and call script. "
            "You have a qualified tenant in your pipeline and want to introduce them to the owner. "
            "Tone is value-bringing, relationship-first."
        )
        user = (
            f"Property details:\n{ctx}\n"
            f"Target: {owner_name} (property owner)\n"
            f"{benchmarks}{tenant_hint}\n\n"
            "Write:\n"
            f"1. Email subject line — address it to {owner_name}\n"
            "2. Email body (3-5 short paragraphs) — broker-to-owner framing\n"
            "   Mention: you have a qualified tenant, their industry and headcount, SF need, "
            "   and you want a 15-minute call to discuss. Reference the vacancy situation and "
            "   how this tenant could solve it. Cite CBRE Q1 2026 submarket data.\n"
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
    owner_name = p.get("owner_name") or "the property owner"
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. Write outreach to a property owner "
        "suggesting it may be a good time to list or quietly market their building. "
        "Reference market timing, hold-period, and your ability to run a discreet process. "
        "Always target the owner directly — this template only fires on unlisted properties."
    )
    user = (
        f"Property details:\n{ctx}\n"
        f"Target: {owner_name} (property owner)\n"
        f"{CBRE_BENCHMARKS}\n\n"
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

    if target_type == "sales_broker":
        contact_name = p.get("sales_contact") or "the listing broker"
        system = (
            f"You are {AGENT_NAME} at {FIRM_NAME}. Write a broker-to-broker outreach email "
            "and call script to the investment sales/listing broker on behalf of a value-add buyer. "
            "Tone is professional peer-to-peer — you represent a qualified principal buyer."
        )
        user = (
            f"Property details:\n{ctx}\n"
            f"Target: {contact_name} (investment sales broker)\n"
            f"{CBRE_BENCHMARKS}\n\n"
            "Write:\n"
            f"1. Email subject line — address it to {contact_name}\n"
            "2. Email body (3-4 short paragraphs) — mention your buyer's thesis "
            "(value-add repositioning, re-leasing upside, mark-to-market opportunity), "
            "capability to close quickly, and interest in discussing terms. "
            "Cite CBRE benchmarks and specific property metrics.\n"
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
    else:  # owner
        owner_name = p.get("owner_name") or "the property owner"
        system = (
            f"You are {AGENT_NAME} at {FIRM_NAME}. Write outreach on behalf of a principal "
            "buyer/investor exploring value-add acquisitions in Northern Virginia office. "
            "Target the property owner directly — no broker is listed."
        )
        user = (
            f"Property details:\n{ctx}\n"
            f"Target: {owner_name} (property owner)\n"
            f"{CBRE_BENCHMARKS}\n\n"
            "Write:\n"
            f"1. Email subject line — address it to {owner_name}\n"
            "2. Email body (3-5 short paragraphs) — value-add thesis framing "
            "(repositioning, re-leasing, capital deployment), close-ready buyer, "
            "discreet/off-market preference. Cite CBRE benchmarks.\n"
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
        prompt = _build_tenant_match(property_dict, target_type, tenant_context)
    elif outreach_type == "listing_rep":
        prompt = _build_listing_rep(property_dict)
    else:  # acquisition
        prompt = _build_acquisition(property_dict, target_type)

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
