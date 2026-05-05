"""
Property-side outreach generation — GPT-4o + four template types.

outreach_type values:
  'tenant_match'  — pitch property to owner as hybrid tenant opportunity
  'listing_rep'   — suggest listing the property (broker-side)
  'acquisition'   — acquisition / value-add buyer pitch
  'broker'        — generic broker intro / relationship outreach

Requires OPENAI_API_KEY in the environment.
"""
import os
from datetime import datetime
from typing import Optional

AGENT_NAME = "Jack Zamer"
FIRM_NAME  = "The Commercial Real Estate Group"

VALID_TYPES = {"tenant_match", "listing_rep", "acquisition", "broker"}


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
        f"Property: {p.get('name') or _addr(p)}",
        f"Address: {_addr(p)}",
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
        f"Dominant Score: {p.get('dominant_score_type', 'N/A')}",
    ]
    return "\n".join(l for l in lines if l)


# ── Template builders ──────────────────────────────────────────────────────────

def _build_tenant_match(p: dict, tenant_context: Optional[str]) -> dict:
    ctx = _prop_context(p)
    tenant_hint = f"\nTenant context: {tenant_context}" if tenant_context else ""
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        "specialising in Northern Virginia office. Write outreach to a property owner "
        "suggesting you can bring them a qualified tenant prospect (the 'hybrid tenant pitch'). "
        "Be concise, specific, and relationship-oriented."
    )
    user = (
        f"Property details:\n{ctx}{tenant_hint}\n\n"
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
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. Write outreach to a property owner "
        "suggesting it may be a good time to list or quietly market their building. "
        "Reference market timing, hold-period, and your ability to run a discreet process."
    )
    user = (
        f"Property details:\n{ctx}\n\n"
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


def _build_acquisition(p: dict) -> dict:
    ctx = _prop_context(p)
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. Write outreach on behalf of a principal "
        "buyer/investor exploring value-add acquisitions in Northern Virginia office. "
        "Focus on the value-add thesis: repositioning, re-leasing, or capital deployment."
    )
    user = (
        f"Property details:\n{ctx}\n\n"
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


def _build_broker(p: dict) -> dict:
    ctx = _prop_context(p)
    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. Write a warm, introductory broker-to-owner "
        "outreach email and call script. No hard pitch — relationship-first, market insight-led."
    )
    user = (
        f"Property details:\n{ctx}\n\n"
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
    """Extract labelled sections from GPT response into a structured dict."""
    def _section(tag: str, next_tags: list[str]) -> str:
        start = raw.find(f"{tag}:\n")
        if start == -1:
            # Try inline (no newline after colon — used for SUBJECT)
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
    # Also try inline SUBJECT
    if not subject:
        for line in raw.splitlines():
            if line.startswith("SUBJECT:"):
                subject = line[8:].strip()
                break

    return {
        "subject":    subject,
        "email_body": _section("EMAIL",       ["OPENING", "CORE", "PAIN_PROBE", "CLOSE"]),
        "opening":    _section("OPENING",     ["CORE",    "PAIN_PROBE", "CLOSE"]),
        "core":       _section("CORE",        ["PAIN_PROBE", "CLOSE"]),
        "pain_probe": _section("PAIN_PROBE",  ["CLOSE"]),
        "close":      _section("CLOSE",       []),
    }


def generate_property_outreach(
    property_dict: dict,
    outreach_type: str,
    tenant_context: Optional[str] = None,
) -> dict:
    """
    Call GPT-4o and return a structured outreach dict with keys:
      email_subject, email_body, call_script_{opening,core,pain_probe,close},
      outreach_type, generated_at
    """
    if outreach_type not in VALID_TYPES:
        raise ValueError(f"outreach_type must be one of {VALID_TYPES}")

    if outreach_type == "tenant_match":
        prompt = _build_tenant_match(property_dict, tenant_context)
    elif outreach_type == "listing_rep":
        prompt = _build_listing_rep(property_dict)
    elif outreach_type == "acquisition":
        prompt = _build_acquisition(property_dict)
    else:
        prompt = _build_broker(property_dict)

    raw = _chat(prompt["system"], prompt["user"])
    parsed = _parse_response(raw)

    return {
        "email_subject":          parsed["subject"],
        "email_body":             parsed["email_body"],
        "call_script_opening":    parsed["opening"],
        "call_script_core":       parsed["core"],
        "call_script_pain_probe": parsed["pain_probe"],
        "call_script_close":      parsed["close"],
        "outreach_type":          outreach_type,
        "generated_at":           datetime.utcnow().isoformat(),
    }
