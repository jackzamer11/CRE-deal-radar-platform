"""
Outreach generation service — GPT-4o call + prompt assembly.

Ported from outreach_agent.py so the platform API and the CLI share
identical generation logic.  The CLI calls this service via the API;
it does not invoke GPT-4o directly.

Requires OPENAI_API_KEY in the environment.
"""
import os
import re
from typing import Optional

from app.services.rep_classification import classify_rep, MAJOR_BROKER_FIRMS
from app.config import NOVA_OFFICE_BENCHMARKS, SUBMARKET_BENCHMARKS, TENANT_VACANCY_CITE_THRESHOLD

NOVA_AVG_RENT        = NOVA_OFFICE_BENCHMARKS["avg_market_rent_psf"]
NOVA_AVG_VACANCY     = NOVA_OFFICE_BENCHMARKS["avg_vacancy_pct"]
NOVA_AVG_FREE_RENT   = NOVA_OFFICE_BENCHMARKS["avg_free_rent_months"]
NOVA_AVG_TI          = NOVA_OFFICE_BENCHMARKS["avg_ti_psf"]

SUBMARKET_MARKET_RENT: dict[str, float] = {
    k: v["market_rent_psf"] for k, v in SUBMARKET_BENCHMARKS.items()
}
SUBMARKET_AVG_VACANCY: dict[str, float] = {
    k: v["vacancy_pct"] for k, v in SUBMARKET_BENCHMARKS.items()
}

AGENT_NAME = "Jack Zamer"
FIRM_NAME  = "The Commercial Real Estate Group"

_SIGNATURE_INSTRUCTION = (
    "Always end the email body with exactly this signature block on its own lines "
    "(after the last paragraph, on a new line):\n\n"
    "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228"
)


# Street-address guard — tenant-side copy shows submarket + class only, never a
# property street address. Matches "<number> <words> <street-suffix>" (e.g.
# "100 Test Ave", "1750 Tysons Boulevard"). "12 months" / phone numbers do not match.
_STREET_SUFFIX = (
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|"
    r"Pkwy|Parkway|Pike|Plaza|Sq|Square|Ter|Terrace|Cir|Circle|Hwy|Highway|Suite|Ste)\b\.?"
)
_STREET_ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[A-Za-z0-9.'-]+\s+){{0,4}}{_STREET_SUFFIX}",
    re.IGNORECASE,
)


def _strip_street_address(body: str) -> str:
    """Defensive guard: remove any street-address token a model may have echoed so
    tenant-side copy never shows a property street address (submarket + class only)."""
    if not body:
        return body
    cleaned = _STREET_ADDRESS_RE.sub("", body)
    return re.sub(r"[ \t]{2,}", " ", cleaned)


def _should_cite_vacancy_tenant_side(avg_vacancy: Optional[float]) -> bool:
    """Tenant-side emails cite the vacancy line only when submarket vacancy is strictly
    below TENANT_VACANCY_CITE_THRESHOLD — tight supply is the relevant signal for tenants."""
    return avg_vacancy is not None and avg_vacancy < TENANT_VACANCY_CITE_THRESHOLD


def _industry_pain(industry: str) -> str:
    i = industry.lower()
    if any(k in i for k in ("federal", "government", "defense", "contractor", "dod")):
        return (
            "Federal contractor frame: contract pipeline uncertainty and the cost of a forced "
            "move during a re-compete or ramp period. Emphasize optionality and speed to execute."
        )
    if any(k in i for k in ("health", "clinical", "medical", "pharma", "biotech")):
        return (
            "Healthcare frame: clinical space specs, ADA compliance, build-out lead times, "
            "and operational disruption risk of an unplanned relocation."
        )
    if any(k in i for k in ("consult", "advisory", "law", "legal", "accounting", "cpa")):
        return (
            "Professional services frame: hybrid-work density trends, client-facing image, "
            "and right-sizing opportunities available in the current sublease market."
        )
    if any(k in i for k in ("tech", "software", "cyber", "data", "ai", "cloud", "saas")):
        return (
            "Tech frame: collaboration-first floorplates, fiber/power infrastructure, "
            "and talent-retention value of a premium submarket address."
        )
    return (
        "NoVA office frame: flight-to-quality trend, growing sublease supply, "
        "and landlord concession packages (free rent, TI allowances) available now."
    )


def _web_search_company_intel(company_name: str) -> str:
    """Execute two web searches for recent company intelligence using Anthropic web search.

    Returns a short summary string injected into the GPT-4o prompt as context.
    Silently returns empty string if ANTHROPIC_API_KEY is unset or search fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        queries = [
            f"{company_name} office expansion Northern Virginia 2025 2026",
            f"{company_name} hiring growth lease 2025 2026",
        ]
        findings: list[str] = []
        for q in queries:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search for: {q}. "
                        "Return a 1-2 sentence factual summary of the top 3 most relevant findings. "
                        "Focus on office moves, lease activity, headcount changes, or funding events."
                    ),
                }],
            )
            for block in resp.content:
                if hasattr(block, "text") and block.text.strip():
                    findings.append(block.text.strip())
                    break
        return " ".join(findings[:2]) if findings else ""
    except Exception:
        return ""


def search_company_intelligence(company_name: str) -> list:
    """Execute two web searches on the company and return structured findings.

    Returns list of dicts: {fact, source_url, source_name, relevance_score}
    Returns [] if ANTHROPIC_API_KEY not set or on any error.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []

    queries = [
        f"{company_name} office expansion Northern Virginia 2025 2026",
        f"{company_name} hiring growth lease 2025 2026",
    ]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        raw_findings: list[str] = []
        for q in queries:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search for: {q}. "
                        "Return up to 3 factual findings relevant to tenant outreach for office leasing. "
                        "Each finding on its own line. Focus on: recent office moves, expansion announcements, "
                        "hiring surges, funding rounds, NoVA presence, or lease activity. "
                        "Output EACH finding on its own line in EXACTLY this format — no other text:\n"
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
    except Exception as e:
        print(f"[search_company_intelligence] Search error: {e}")
        return []


def generate_outreach(company: dict) -> dict:
    """
    Build GPT-4o outreach draft for a company dict.

    Returns:
        {
            "email": {"subject": ..., "body": ...},
            "call_script": {"opening": ..., "core_message": ...,
                            "pain_probe": ..., "the_close": ...},
            "projected_sf": int | None,
        }

    Raises RuntimeError if OPENAI_API_KEY is not set.
    """
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
    client = OpenAI(api_key=api_key)

    # ── Core data ─────────────────────────────────────────────────────────────
    company_name  = company["name"]
    submarket     = company.get("current_submarket") or ""
    headcount     = company.get("current_headcount")
    growth_pct    = company.get("headcount_growth_pct")
    # Real occupied SF is the ONLY SF figure — never derived from headcount.
    current_sf    = company.get("current_sf_occupied")
    projected_sf  = current_sf if current_sf else None
    lease_mo      = company.get("lease_expiry_months")
    lease_date    = company.get("lease_expiry_date") or ""
    industry      = (company.get("industry") or "").split("(")[0].strip()
    contact_name  = company.get("primary_contact_name") or ""
    contact_title = company.get("primary_contact_title") or ""
    tenant_rep    = company.get("tenant_representative") or ""
    rep_class     = classify_rep(tenant_rep)
    future_flag   = company.get("future_move_flag")
    future_type   = company.get("future_move_type") or ""
    trajectory    = (company.get("lease_trajectory") or "AUTO").upper()

    market_rent  = SUBMARKET_MARKET_RENT.get(submarket)
    avg_vacancy  = SUBMARKET_AVG_VACANCY.get(submarket)
    show_vacancy = _should_cite_vacancy_tenant_side(avg_vacancy)

    rent_vs_nova    = round(market_rent - NOVA_AVG_RENT, 2)    if market_rent  else None
    vacancy_vs_nova = round(avg_vacancy  - NOVA_AVG_VACANCY, 1) if avg_vacancy else None

    # ── Formatted strings ─────────────────────────────────────────────────────
    growth_str = f"+{growth_pct:.1f}%" if growth_pct else "stable"
    lease_str  = f"{lease_mo} months" if lease_mo is not None else "unknown"
    if lease_date:
        lease_str += f" (break date {lease_date})"

    # SF needed = real occupied SF only. When unknown, never substitute an estimate.
    sf_line = f"{current_sf:,} SF occupied" if current_sf else "SF unknown — do not state or estimate a square footage"

    # Fix 1: the per-tenant rent-gap figure is no longer surfaced — tenant emails
    # lead with lease timing and the market window, not an in-place-vs-market stat.

    if show_vacancy:
        vacancy_str = f"{avg_vacancy:.1f}%"
        if vacancy_vs_nova is not None:
            vac_sign = "+" if vacancy_vs_nova >= 0 else ""
            vacancy_str += f" ({vac_sign}{vacancy_vs_nova:.1f}pp vs {NOVA_AVG_VACANCY:.1f}% NoVA avg, per CBRE Q1 2026)"
    else:
        vacancy_str = None

    rent_vs_nova_str = ""
    if rent_vs_nova is not None:
        r_sign = "+" if rent_vs_nova >= 0 else ""
        rent_vs_nova_str = f"{submarket} rent ${market_rent:.2f}/SF is {r_sign}${rent_vs_nova:.2f} vs ${NOVA_AVG_RENT:.2f}/SF NoVA avg"

    future_line = f"Future move flagged: YES — {future_type}" if future_flag else ""
    greeting    = contact_name if contact_name else "there"

    contraction = bool(
        trajectory == "CONTRACTING"
        or company.get("contraction_signal")
        or (current_sf and headcount and headcount > 0 and (current_sf / headcount) > 230)
    )

    trajectory_note = ""
    if trajectory == "CONTRACTING":
        trajectory_note = (
            f"Broker has confirmed this tenant is contracting. Acknowledge the right-sizing: "
            f"'I noticed your footprint has evolved — I'm seeing a lot of quality smaller suites "
            f"come to market in {submarket} right now that fit a leaner operating model.' "
            f"Do NOT project expansion."
        )
    elif trajectory == "FLAT":
        trajectory_note = "Tenant is in steady-state mode. Focus on lease timing and market rate opportunity, not expansion."

    # ── Rep framing ────────────────────────────────────────────────────────────
    # Fix 1: the represented-vs-unrepresented pivot stays, but it leads with the
    # market window and lease timing — never with a per-tenant rent-gap figure.
    if rep_class == "MAJOR":
        rep_instruction = (
            f"This tenant is already represented by {tenant_rep} (a major brokerage). Do NOT pitch "
            f"direct representation and never position yourself against the incumbent firm. Offer "
            f"yourself as a {submarket}-specialist second read on the current market window — "
            f"sublease supply, flight-to-quality, and landlord concessions — not a competing relationship."
        )
    elif rep_class == "OTHER":
        rep_instruction = (
            f"This tenant has a regional rep on record ({tenant_rep}). Lead with your specific "
            f"{submarket} market knowledge and let the value open the door — do not disparage the rep."
        )
    else:
        rep_instruction = (
            "This tenant has NO broker representation on record. You may offer to represent them "
            "directly, but lead with the lease-timing window and what the market means for them — "
            "keep the ask low-pressure, never aggressive."
        )

    contraction_note = (
        "Tenant shows right-sizing signals. Acknowledge gracefully. Do not project expansion."
    ) if contraction else ""

    # ── NoVA market-window framing (Fix 1) ────────────────────────────────────
    # Tenant emails lead with lease timing and what the current NoVA office market
    # window means for the tenant — rising sublease supply, flight-to-quality, and
    # landlord concessions (free rent, TI allowances, blend-and-extend). They do
    # NOT lead with, or feature, a per-tenant rent-gap figure.
    market_window = (
        f"the NoVA office market currently favors tenants: sublease supply is rising, there is a "
        f"clear flight-to-quality, and landlords are competing with concession packages — averaging "
        f"{NOVA_AVG_FREE_RENT}+ months of free rent and ${NOVA_AVG_TI}+/SF in TI allowances "
        f"(per CBRE Q1 2026), plus blend-and-extend flexibility for tenants who engage ahead of expiry"
    )
    # Tight-supply reinforcement — only cited when submarket vacancy is below the
    # tenant-side threshold (scarcity signal that sharpens the flight-to-quality window).
    tight_supply_line = (
        f"Note that {submarket} vacancy is only {avg_vacancy:.1f}%, so the best space gets spoken for early."
        if show_vacancy and avg_vacancy is not None else ""
    )

    rules = [
        # ── Email structure (Fix 1) ──────────────────────────────────────────
        (
            "EMAIL BODY — follow this order exactly: "
            f"(1) LEAD with their lease timing: their lease is up in {lease_str} in {submarket}, and a "
            f"renewal-or-relocate decision is best driven early — open on this, not on any statistic. "
            f"(2) Explain what the market window means for THEM: {market_window}. "
            + (f"{tight_supply_line} " if tight_supply_line else "")
            + "Do NOT lead with — or feature — a per-tenant rent figure or a rent-gap stat. "
            "(3) Include exactly ONE short, open-ended pain-probe question about their current space situation. "
            "(4) Close low-pressure. "
            "Body MINIMUM 6 sentences, MAXIMUM 150 words (excluding the signature block); subject under 9 words."
        ),
        "Cite '(per CBRE Q1 2026)' on the FIRST market statistic only — do not repeat the citation.",
        # Broker name + NoVA specialty appear exactly once each (Fix 1)
        "Do NOT introduce yourself by name anywhere in the email body — your name appears ONLY in the "
        "signature block, exactly once. State your Northern Virginia office specialty exactly once, "
        "briefly; never repeat 'exclusively NoVA office' or any similar phrasing.",
        "Reference ONLY the submarket and months-to-expiry. NEVER include a street address.",
        f'Greeting: use "{greeting}" — format "Hi {greeting},"',
        "FORBIDDEN phrases: 'happy to discuss', 'let me know if interested', 'feel free to reach out'; "
        "NEVER suggest specific days of the week. Close with: 'I'd welcome a brief call at your convenience.'",
        rep_instruction,
        # ── Call script (Fix 2): tenant-rep discovery structure ──────────────
        (
            "CALL SCRIPT — write a tenant-rep discovery call with four distinct sections: "
            "OPENING — a brief, warm intro and ask permission to ask a few quick questions. "
            "CORE MESSAGE — a natural sequence of discovery questions: how their current space is "
            "fitting, what they're paying in rent now, their growth trajectory, floor-plan needs, "
            "parking count, in-office vs hybrid work model, whether they've started looking yet, and "
            "who else is involved in the decision. "
            "PAIN PROBE — one question that surfaces their single biggest real-estate headache. "
            "THE CLOSE — offer a free, no-obligation market read on their options; apply no pressure."
        ),
        _industry_pain(industry),
    ]
    if trajectory_note:
        rules.append(trajectory_note)
    if contraction_note:
        rules.append(contraction_note)
    rules.append(_SIGNATURE_INSTRUCTION)

    numbered_rules = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))

    system_prompt = f"""You are {AGENT_NAME} from {FIRM_NAME}, a senior commercial real estate broker
specializing in Northern Virginia office tenant representation.
You write precise, data-driven outreach backed by CBRE Q1 2026 market data. No boilerplate.

RULES:
{numbered_rules}

Return valid JSON only — no markdown fences, no extra text:
{{
  "call_script": {{
    "opening": "warm intro + permission to ask a few questions",
    "core_message": "discovery questions: current space fit, current rent, growth trajectory, floor-plan needs, parking count, work model, whether they've started looking, who else decides",
    "pain_probe": "one question surfacing their biggest real-estate headache",
    "the_close": "offer a free, no-obligation market read"
  }},
  "email": {{
    "subject": "...",
    "body": "..."
  }}
}}"""

    nova_context = (
        f"NoVA MARKET BENCHMARKS (CBRE Q1 2026):\n"
        f"  NoVA avg rent:     ${NOVA_AVG_RENT:.2f}/SF/yr NNN\n"
        f"  NoVA avg vacancy:  {NOVA_AVG_VACANCY:.1f}%\n"
        f"  Avg free rent:     {NOVA_AVG_FREE_RENT} months (estimate)\n"
        f"  Avg TI allowance:  ${NOVA_AVG_TI}/SF (estimate)\n"
        f"  Avg lease term:    7 years"
    )

    submarket_context = (
        f"SUBMARKET BENCHMARKS — {submarket} (CBRE Q1 2026):\n"
        f"  Market rent:       ${market_rent:.2f}/SF/yr NNN  ({rent_vs_nova_str})\n"
        + (f"  Submarket vacancy: {vacancy_str}" if show_vacancy else
           "  [Submarket vacancy is above the tenant-side citation threshold — do not cite in email copy]")
    ) if market_rent else f"SUBMARKET: {submarket} (no benchmark data)"

    user_prompt = (
        f"Generate personalized outreach for this NoVA office tenant:\n\n"
        f"COMPANY: {company_name}\n"
        f"INDUSTRY: {industry}\n"
        f"CONTACT: {contact_name or 'Unknown'}{(' — ' + contact_title) if contact_title else ''}\n"
        f"SUBMARKET: {submarket}\n"
        f"LEASE TRAJECTORY: {trajectory}\n\n"
        f"{nova_context}\n\n"
        f"{submarket_context}\n\n"
        f"TENANT DATA:\n"
        f"  Headcount:      {headcount or 'unknown'} employees\n"
        f"  Growth rate:    {growth_str} YoY\n"
        f"  SF footprint:   {sf_line}\n"
        f"  Lease expiry:   {lease_str}\n"
        f"  Broker rep:     {tenant_rep or 'NONE ON RECORD'} [{rep_class}]\n"
        + (f"  {future_line}\n" if future_line else "")
        + f"\nSIGNAL SCORE: {company.get('opportunity_score', 0):.0f}/100 ({company.get('priority', '')})\n\n"
        f"Sign off as {AGENT_NAME} | {FIRM_NAME}."
    )

    # ── Web search intel (Change 9) ───────────────────────────────────────────
    intel = _web_search_company_intel(company_name)
    intel_section = (
        f"\nRECENT COMPANY INTELLIGENCE (from web search — use at least one specific finding):\n{intel}\n"
        if intel else
        "\nNo recent company intelligence found — use CBRE Q1 2026 NoVA submarket data for market references.\n"
    )

    import json
    full_user = user_prompt + intel_section
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": full_user},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    result = json.loads(response.choices[0].message.content.strip())
    result["projected_sf"] = projected_sf
    # Fix 1: do NOT inject a hardcoded broker intro or social-proof line on tenant
    # emails. The broker name appears exactly once — in the signature block — and the
    # NoVA-office specialty is stated once in the body. As a defensive guard, drop any
    # street-address line the model may have echoed (tenant side shows submarket only).
    if isinstance(result.get("email"), dict) and result["email"].get("body"):
        result["email"]["body"] = _strip_street_address(result["email"]["body"])
    return result
