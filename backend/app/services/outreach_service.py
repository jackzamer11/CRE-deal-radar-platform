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
from app.config import NOVA_OFFICE_BENCHMARKS, SUBMARKET_BENCHMARKS

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

# Sentences containing rent-PSF figures or jargon the standalone tenant outreach
# must never deliver: flight-to-quality, sublease supply, TI allowances, free rent,
# NoVA average comparisons, or any dollar-per-SF figure.  Also strips the canned
# self-description ("I work exclusively in the Northern Virginia office market")
# and recipient-directed probe phrasings ("biggest pressure", "space planning",
# "hybrid model") that the closing paragraph must never carry.
# Uses (?:[^.!?\n]|\.\d)* so decimal numbers like $34.20 inside a sentence do not
# create a false sentence boundary before the terminal [.!?].
_STANDALONE_OUTREACH_STRIP_PATTERN = re.compile(
    r'(?:[^.!?\n]|\.\d)*(?:'
    r'\bflight.to.quality\b'
    r'|\bsublease\s+supply\b'
    r'|\bTI\s+allowances?\b'
    r'|\bNoVA\s+average\b'
    r'|\bfree\s+rent\b'
    r'|\$\d+(?:\.\d+)?/SF'
    r'|\bpsf\b'
    r'|I\s+work\s+exclusively\s+in\s+the\s+Northern\s+Virginia\s+office\s+market'
    r'|\bbiggest\s+pressure\b'
    r'|\bspace\s+planning\b'
    r'|\bhybrid\s+model\b'
    r')(?:[^.!?\n]|\.\d)*[.!?]\s*',
    re.IGNORECASE,
)


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
    import json
    from openai import OpenAI
    from app.services.property_outreach_service import (
        _inject_hardcoded_sentences,
        _strip_sentences,
        _MARKET_FILLER_PATTERN,
        _round_sf_to_hundred,
    )

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
    client = OpenAI(api_key=api_key)

    # ── Core data ─────────────────────────────────────────────────────────────
    company_name  = company["name"]
    submarket     = company.get("current_submarket") or ""
    current_sf    = company.get("current_sf_occupied")
    projected_sf  = current_sf if current_sf else None
    lease_mo      = company.get("lease_expiry_months")
    lease_date    = company.get("lease_expiry_date") or ""
    industry      = (company.get("industry") or "").split("(")[0].strip()
    contact_name  = company.get("primary_contact_name") or ""
    contact_title = company.get("primary_contact_title") or ""
    tenant_rep    = company.get("tenant_representative") or ""
    rep_class     = classify_rep(tenant_rep)
    trajectory    = (company.get("lease_trajectory") or "AUTO").upper()

    avg_vacancy   = SUBMARKET_AVG_VACANCY.get(submarket)
    greeting      = contact_name if contact_name else "there"

    # ── Formatted strings ─────────────────────────────────────────────────────
    lease_str = f"{lease_mo} months" if lease_mo is not None else "unknown"
    if lease_date:
        lease_str += f" (break date {lease_date})"

    sf_rounded = _round_sf_to_hundred(current_sf) if current_sf else None
    sf_prompt  = (f"{sf_rounded:,} SF occupied" if sf_rounded
                  else "SF unknown — do not state or estimate a square footage")

    # Vacancy sentence injected when submarket vacancy < 10%
    vacancy_sentence = ""
    if avg_vacancy is not None and avg_vacancy < 10.0:
        vacancy_sentence = (
            f"With {submarket}'s vacancy rate at {avg_vacancy:.1f}%, "
            "quality options are moving faster than most people expect."
        )

    # ── Rep framing ────────────────────────────────────────────────────────────
    if rep_class == "MAJOR":
        rep_instruction = (
            f"Tenant is already represented by {tenant_rep} (major brokerage). "
            "Do NOT pitch direct representation. "
            f"Position yourself as a {submarket}-specialist market resource."
        )
    elif rep_class == "OTHER":
        rep_instruction = (
            f"Tenant has a regional rep on record ({tenant_rep}). "
            f"Lead with your specific {submarket} deal flow knowledge."
        )
    else:
        rep_instruction = (
            "Tenant has NO broker rep on record. "
            "Pitch direct tenant representation — one clear, confident sentence."
        )

    # ── Pain probe ────────────────────────────────────────────────────────────
    if trajectory == "CONTRACTING":
        _sf_ref    = f"from {sf_rounded:,} SF " if sf_rounded else ""
        _pp_example = (f"What drove the right-sizing {_sf_ref}— "
                       "was that cost-driven, hybrid policy, or part of a broader restructure?")
    elif trajectory == "GROWING":
        _pp_example = ("What's driving the team expansion — "
                       "new contract wins, M&A, or organic growth?")
    else:
        _sf_ref    = f"across {sf_rounded:,} SF " if sf_rounded else ""
        _pp_example = (f"What's the biggest pressure on your space planning {_sf_ref}"
                       "this cycle — cost, talent attraction, hybrid model, or location?")

    pain_probe_rule = (
        "PAIN PROBE — Write EXACTLY ONE open-ended question, no setup sentences, no pitching. "
        f"CORRECT example: \"{_pp_example}\""
    )

    # ── Rules ─────────────────────────────────────────────────────────────────
    rules = [
        (
            "EMAIL BODY: Maximum 3 short paragraphs. "
            "Open with the tenant's lease timing "
            "(e.g. 'With your lease expiring in X months...'). "
            "Warm, credible tone — write like a trusted market expert, not a salesperson. "
            "STRICT: the email body is AT MOST 3 paragraphs. "
            "The CLOSING paragraph must be ONLY the single call ask — exactly one sentence, "
            "no questions to the recipient, no additional sentences after it. "
            "Every sentence must contain a specific fact, timeline, or location. "
            "FORBIDDEN from email body: rent PSF figures, NoVA averages, TI allowances, "
            "free rent, 'flight-to-quality', 'sublease supply', 'at-market', "
            "'below-market', 'above-market', any dollar-per-SF figure, "
            "any mention of current rent."
        ),
        (
            "VACANCY LINE: "
            + (
                f"Include EXACTLY this sentence verbatim in the email body: "
                f"\"{vacancy_sentence}\""
                if vacancy_sentence else
                "Submarket vacancy is at or above 10% — do NOT include any vacancy rate sentence."
            )
        ),
        "SUBJECT LINE: Under 9 words.",
        f'Greeting: "Hi {greeting},"',
        (
            "FORBIDDEN closing phrases: 'happy to discuss', 'let me know if interested', "
            "'feel free to reach out'. "
            "Close with: 'I’d welcome a brief call at your convenience.'"
        ),
        rep_instruction,
        pain_probe_rule,
        (
            "CALL SCRIPT — OPENING, CORE MESSAGE, CLOSE: 2-3 sentences each, fact-bearing. "
            "No rent PSF. No NoVA averages. No jargon."
        ),
        _SIGNATURE_INSTRUCTION,
    ]

    if trajectory == "CONTRACTING":
        rules.append(
            "Tenant is contracting. Acknowledge gracefully; do not project expansion."
        )

    numbered_rules = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))

    system_prompt = (
        f"You are {AGENT_NAME} from {FIRM_NAME}, a senior commercial real estate broker "
        "specializing in Northern Virginia office tenant representation.\n"
        "Write precise, warm outreach. No boilerplate. No jargon.\n\n"
        f"RULES:\n{numbered_rules}\n\n"
        "Return valid JSON only — no markdown fences, no extra text:\n"
        '{\n'
        '  "call_script": {\n'
        '    "opening": "...",\n'
        '    "core_message": "...",\n'
        '    "pain_probe": "...",\n'
        '    "the_close": "..."\n'
        '  },\n'
        '  "email": {\n'
        '    "subject": "...",\n'
        '    "body": "..."\n'
        '  }\n'
        '}'
    )

    user_prompt = (
        f"Generate personalized outreach for this NoVA office tenant:\n\n"
        f"COMPANY: {company_name}\n"
        f"INDUSTRY: {industry}\n"
        f"CONTACT: {contact_name or 'Unknown'}"
        f"{(' — ' + contact_title) if contact_title else ''}\n"
        f"SUBMARKET: {submarket}\n"
        f"LEASE EXPIRY: {lease_str}\n"
        f"SF FOOTPRINT: {sf_prompt}\n"
        f"BROKER REP: {tenant_rep or 'NONE ON RECORD'} [{rep_class}]\n"
        f"SIGNAL SCORE: {company.get('opportunity_score', 0):.0f}/100 "
        f"({company.get('priority', '')})\n\n"
        f"Sign off as {AGENT_NAME} | {FIRM_NAME}."
    )

    intel = _web_search_company_intel(company_name)
    if intel:
        user_prompt += (
            f"\n\nRECENT COMPANY INTELLIGENCE "
            f"(use at least one specific finding):\n{intel}"
        )

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

    # Post-LLM: inject hardcoded sentences, strip generic filler + jargon/PSF
    if isinstance(result.get("email"), dict) and result["email"].get("body"):
        body = result["email"]["body"]
        body = _inject_hardcoded_sentences(body)
        body = _strip_sentences(body, _MARKET_FILLER_PATTERN)
        body = _strip_sentences(body, _STANDALONE_OUTREACH_STRIP_PATTERN)
        result["email"]["body"] = body

    return result
