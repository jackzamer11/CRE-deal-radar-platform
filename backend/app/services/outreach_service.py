"""
Outreach generation service — GPT-4o call + prompt assembly.

Ported from outreach_agent.py so the platform API and the CLI share
identical generation logic.  The CLI calls this service via the API;
it does not invoke GPT-4o directly.

Requires OPENAI_API_KEY in the environment.
"""
import math
import os
from typing import Optional

from app.services.rep_classification import classify_rep, MAJOR_BROKER_FIRMS
from app.config import NOVA_OFFICE_BENCHMARKS, SUBMARKET_BENCHMARKS

SF_PER_PERSON        = 175
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


def project_sf(company: dict) -> Optional[int]:
    """
    Project SF needed at the 12-18 month horizon.

    lease_trajectory overrides:
      CONTRACTING / FLAT → return current_sf unchanged
      GROWING            → force growth logic
      AUTO / null        → tiered SF/head logic
    """
    current_sf  = company.get("current_sf")
    headcount   = company.get("current_headcount")
    growth_pct  = company.get("headcount_growth_pct")
    trajectory  = (company.get("lease_trajectory") or "AUTO").upper()
    growth_rate = (growth_pct / 100.0) if growth_pct is not None else 0.15

    if trajectory in ("CONTRACTING", "FLAT"):
        return current_sf

    if current_sf and headcount:
        sf_per_head         = current_sf / headcount
        projected_headcount = headcount * (1 + growth_rate)

        if trajectory == "GROWING" or sf_per_head < 100:
            return math.ceil(current_sf * (1 + growth_rate) / 100) * 100
        elif sf_per_head <= 200:
            return math.ceil(projected_headcount * sf_per_head / 100) * 100
        else:
            return math.ceil(current_sf * 1.05 / 100) * 100

    if headcount:
        return math.ceil(headcount * (1 + growth_rate) * SF_PER_PERSON / 100) * 100
    return None


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
    current_sf    = company.get("current_sf")
    projected_sf  = project_sf(company)
    lease_mo      = company.get("lease_expiry_months")
    lease_date    = company.get("lease_expiry_date") or ""
    industry      = (company.get("industry") or "").split("(")[0].strip()
    contact_name  = company.get("primary_contact_name") or ""
    contact_title = company.get("primary_contact_title") or ""
    tenant_rep    = company.get("tenant_representative") or ""
    rep_class     = classify_rep(tenant_rep)
    current_rent  = company.get("current_rent_psf")
    future_flag   = company.get("future_move_flag")
    future_type   = company.get("future_move_type") or ""
    trajectory    = (company.get("lease_trajectory") or "AUTO").upper()

    market_rent  = SUBMARKET_MARKET_RENT.get(submarket)
    avg_vacancy  = SUBMARKET_AVG_VACANCY.get(submarket)

    rent_vs_nova    = round(market_rent - NOVA_AVG_RENT, 2)    if market_rent  else None
    vacancy_vs_nova = round(avg_vacancy  - NOVA_AVG_VACANCY, 1) if avg_vacancy else None

    # ── Formatted strings ─────────────────────────────────────────────────────
    growth_str = f"+{growth_pct:.1f}%" if growth_pct else "stable"
    lease_str  = f"{lease_mo} months" if lease_mo is not None else "unknown"
    if lease_date:
        lease_str += f" (break date {lease_date})"

    sf_line = f"{current_sf:,} SF currently" if current_sf else "SF unknown"
    if projected_sf and current_sf:
        delta    = projected_sf - current_sf
        sign     = "+" if delta >= 0 else ""
        sf_line += f" → projected {projected_sf:,} SF ({sign}{delta:,} SF)"
    elif projected_sf:
        sf_line += f"; projected need {projected_sf:,} SF"

    rent_line = "in-place rent unknown"
    if current_rent and market_rent:
        diff = round(current_rent - market_rent, 2)
        sign = "+" if diff >= 0 else ""
        ctx  = ("above market — renegotiation pressure likely at renewal" if diff > 2
                else "below market — favorable rate they'll want to preserve" if diff < -2
                else "at-market")
        rent_line = f"${current_rent:.2f}/SF in-place vs ${market_rent:.2f}/SF market ({sign}${diff:.2f} — {ctx})"
    elif market_rent:
        rent_line = f"in-place rate unknown; {submarket} market benchmark ${market_rent:.2f}/SF (per CBRE Q1 2026)"

    vacancy_str = f"{avg_vacancy:.1f}%" if avg_vacancy else "unknown"
    if vacancy_vs_nova is not None:
        vac_sign = "+" if vacancy_vs_nova >= 0 else ""
        vacancy_str += f" ({vac_sign}{vacancy_vs_nova:.1f}pp vs {NOVA_AVG_VACANCY:.1f}% NoVA avg, per CBRE Q1 2026)"

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
    if rep_class == "MAJOR":
        rep_instruction = (
            f"Tenant is already represented by {tenant_rep} (major brokerage). "
            "Do NOT pitch direct representation. "
            f"Pivot to market resource framing: position yourself as a {submarket}-specialist complement. "
            "REQUIRED: Reference at least one quantitative comparison vs. the NoVA average. "
            f"Example: '{submarket} vacancy is sitting at {avg_vacancy:.1f}% — "
            f"well above the {NOVA_AVG_VACANCY:.1f}% NoVA average (per CBRE Q1 2026) — "
            "which means landlords have lost negotiating leverage. "
            f"Recent NoVA renewals are seeing {NOVA_AVG_FREE_RENT}+ months free rent "
            f"and ${NOVA_AVG_TI}+/SF TI on average.' "
            "Never position yourself against a major firm."
        ) if avg_vacancy else (
            f"Tenant is already represented by {tenant_rep} (major brokerage). "
            "Pivot to market resource framing. Never pitch direct representation."
        )
    elif rep_class == "OTHER":
        rep_instruction = (
            f"Tenant has a regional rep on record ({tenant_rep}). Lead with your specific "
            f"{submarket} deal flow knowledge; let value open the door."
        )
    else:
        rep_instruction = (
            "Tenant has NO broker rep on record. Pitch direct tenant representation explicitly and confidently. "
            "REQUIRED: Reference one specific market dislocation. "
            + (f"Example: reference the {submarket} market rate of ${market_rent:.2f}/SF vs the "
               f"${NOVA_AVG_RENT:.2f}/SF NoVA average (per CBRE Q1 2026)." if market_rent else "")
        )

    contraction_note = (
        "Tenant shows right-sizing signals. Acknowledge gracefully. Do not project expansion."
    ) if contraction else ""

    # ── Pain probe ────────────────────────────────────────────────────────────
    if rep_class == "MAJOR":
        _pp_context = "MAJOR firm rep — probe for intel needs, not pain."
        _pp_example = (
            f"What kind of market intel would actually be useful to your team right now — "
            f"recent {submarket} comp activity, sublease availability, or landlord concession behavior?"
        )
    elif trajectory == "CONTRACTING":
        _sf_ref = f"from {current_sf:,} SF " if current_sf else ""
        _pp_context = "Tenant is contracting — probe for the driver."
        _pp_example = f"What drove the right-sizing {_sf_ref}— was that cost-driven, hybrid policy, or part of a broader restructure?"
    elif trajectory == "GROWING":
        _growth_ref = f"at +{growth_pct:.0f}% YoY " if growth_pct else ""
        _pp_context = "Tenant is growing — probe for the growth driver."
        _pp_example = f"What's driving the team expansion {_growth_ref}— new contract wins, M&A, or organic growth?"
    else:
        _sf_ref = f"across {current_sf:,} SF " if current_sf else ""
        _pp_context = "Probe for current space-planning pressures."
        _pp_example = f"What's the biggest pressure on your space planning {_sf_ref}this cycle — cost, talent attraction, hybrid model, or location?"

    pain_probe_rule = (
        f"PAIN PROBE — STRICT RULES: "
        f"(a) Write EXACTLY ONE open-ended question. Zero setup sentences. Zero pitching before the question. "
        f"(b) The question MUST reference at least one specific data point from the tenant record. "
        f"(c) FORBIDDEN: pitching language, multi-sentence setup, binary yes/no questions, generic questions. "
        f"(d) Context: {_pp_context} "
        f"(e) CORRECT example: \"{_pp_example}\" "
        f"(f) BAD example: 'A premium {submarket} address can support talent retention. Are you facing any specific challenges?' "
        f"Write ONE sharp, data-anchored open question only."
    )

    rent_ref = (
        f"${market_rent:.2f}/SF vs ${NOVA_AVG_RENT:.2f}/SF NoVA avg"
        if market_rent else f"submarket rent vs ${NOVA_AVG_RENT:.2f}/SF NoVA avg"
    )
    rules = [
        "Cite '(per CBRE Q1 2026)' on the FIRST market statistic in each message (email and call script).",
        (
            f"Email body: MINIMUM 6 sentences. Subject line under 9 words. "
            f"REQUIRED in email body — both of these comparisons must appear: "
            f"(i) submarket vacancy vs NoVA average with the specific percentage-point delta; "
            f"(ii) submarket rent vs NoVA average — pattern: "
            f"'[Submarket] market rent is {rent_ref} — a [premium/discount] of $X reflecting [reason]'. "
            f"Both comparisons are mandatory regardless of rep status."
        ),
        (
            "Call script OPENING, CORE MESSAGE, and CLOSE: MINIMUM 3 sentences each with specific data. "
            "CORE MESSAGE must include both submarket rent vs NoVA average and vacancy comparison."
        ),
        pain_probe_rule,
        f'Greeting: use "{greeting}" — format "Hi {greeting},"',
        "FORBIDDEN phrases: 'happy to discuss', 'let me know if interested', 'feel free to reach out'. "
        "Use specific CTAs: 'Are you free Tuesday or Wednesday for a 15-minute call?'",
        rep_instruction,
        _industry_pain(industry),
    ]
    if trajectory_note:
        rules.append(trajectory_note)
    if contraction_note:
        rules.append(contraction_note)

    numbered_rules = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))

    system_prompt = f"""You are {AGENT_NAME} from {FIRM_NAME}, a senior commercial real estate broker
specializing in Northern Virginia office tenant representation.
You write precise, data-driven outreach backed by CBRE Q1 2026 market data. No boilerplate.

RULES:
{numbered_rules}

Return valid JSON only — no markdown fences, no extra text:
{{
  "call_script": {{
    "opening": "...",
    "core_message": "...",
    "pain_probe": "...",
    "the_close": "..."
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
        f"  Submarket vacancy: {vacancy_str}"
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
        f"  Rent situation: {rent_line}\n"
        f"  Lease expiry:   {lease_str}\n"
        f"  Broker rep:     {tenant_rep or 'NONE ON RECORD'} [{rep_class}]\n"
        + (f"  {future_line}\n" if future_line else "")
        + f"\nSIGNAL SCORE: {company.get('opportunity_score', 0):.0f}/100 ({company.get('priority', '')})\n\n"
        f"Sign off as {AGENT_NAME} | {FIRM_NAME}."
    )

    import json
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    result = json.loads(response.choices[0].message.content.strip())
    result["projected_sf"] = projected_sf
    return result
