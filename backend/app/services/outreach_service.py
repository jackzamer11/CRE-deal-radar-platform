"""
Outreach generation service — GPT-4o call + prompt assembly.

Ported from outreach_agent.py so the platform API and the CLI share
identical generation logic.  The CLI calls this service via the API;
it does not invoke GPT-4o directly.

Requires OPENAI_API_KEY in the environment.
"""
import os
import re
from datetime import date
from typing import NamedTuple, Optional

from app.services.rep_classification import classify_rep, MAJOR_BROKER_FIRMS
from app.config import (
    NOVA_OFFICE_BENCHMARKS,
    SUBMARKET_BENCHMARKS,
    TENANT_VACANCY_CITE_THRESHOLD,
    is_provisional_submarket,
)

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


# ── Rent-gap ladder (deterministic — drives the email rent line AND the call
#    sheet's ANGLE line, so the two never tell different stories) ──────────────
#
# Six rungs, sharpest set field wins (effective > starting > hedge; within each,
# building asking > submarket asking):
#   (1) effective + building asking          → direction-framed line vs building asking
#   (2) effective, no building               → direction-framed line vs submarket asking
#   (3) starting + building, no effective    → direction-framed line vs building asking
#   (4) starting alone (no building)         → direction-framed line vs submarket asking
#   (5) building asking only                 → hedge framing off building asking
#   (6) nothing set                          → hedge framing off submarket asking
#
# Direction (rungs 1-4): every rung that compares the tenant's number (effective
# or starting) to a benchmark (building or submarket asking) checks direction
# before choosing framing.
#   tenant BELOW benchmark → "the market's moved since you signed" — factual,
#     never implying the tenant is behind or overpaying. Rung 3's below branch
#     keeps the spec-locked "crept closer to that asking number" wording.
#   tenant ABOVE benchmark → neutral information framing: they are paying above
#     current asking and it's worth confirming what that means before renewal —
#     no blame, no manufactured urgency.
# The hedge rungs (5/6) claim NO direction; their lease-vintage clause anchors
# to the tenant's REAL lease_signed_year when known and falls back to vague
# tenure phrasing when unknown — never a fixed pivot year, never a fabricated
# date.
#
# Provisional submarkets (Fix 4): a submarket flagged provisional in
# SUBMARKET_BENCHMARKS is a placeholder, so rungs 2/4/6 never select it — the
# ladder falls through to the next applicable rung or returns None rather than
# mailing a placeholder number to a real tenant.
#
# When no field nor a quotable submarket benchmark exists, there is no rent
# line (return None) — never a crash, never an invented number.

# Full-service positioning + free-lease-analysis close: injected verbatim on
# every rung so the lines survive regeneration (same pattern as the property
# outreach service's hardcoded sentences).
FULL_SERVICE_LINE = (
    "When I look at a lease, I review the full economics — TI allowance, "
    "base-year op-ex, taxes, and escalations — not just the space match, "
    "and it costs you nothing as the tenant."
)
FREE_ANALYSIS_LINE = (
    "If it would be useful, I'll put together a free lease analysis of your "
    "current terms — no obligation either way."
)


class RentStory(NamedTuple):
    """The rung + direction the ladder selected for a tenant. Both the email
    rent line and the call sheet's ANGLE line derive from the SAME selection."""
    rung:            int             # 1-6
    direction:       Optional[str]   # 'below' | 'above' (rungs 1-4); None on hedges
    tenant_value:    Optional[float] # effective (1-2) or starting (3-4) rent $/SF
    benchmark_value: float           # the asking rent compared against
    benchmark_kind:  str             # 'building' | 'submarket'


def _quotable_submarket_asking(submarket: Optional[str]) -> Optional[float]:
    """Submarket asking rent usable in tenant-facing copy — None when unknown
    OR when the benchmark is provisional (placeholder numbers are never mailed
    to a real tenant; rungs 2/4/6 fall through instead)."""
    if is_provisional_submarket(submarket):
        return None
    return SUBMARKET_MARKET_RENT.get(submarket or "")


def select_rent_story(
    effective_rent_psf: Optional[float],
    starting_rent_psf: Optional[float],
    building_asking_rent_psf: Optional[float],
    submarket: Optional[str],
) -> Optional[RentStory]:
    """Pick the ladder rung + direction for a tenant, or None when no rent
    reference exists. Pure and null-safe: zero/negative/blank values count as
    unset; an unknown or provisional submarket removes rungs 2/4/6."""
    eff      = effective_rent_psf if (effective_rent_psf or 0) > 0 else None
    starting = starting_rent_psf if (starting_rent_psf or 0) > 0 else None
    bldg     = building_asking_rent_psf if (building_asking_rent_psf or 0) > 0 else None
    submarket_asking = _quotable_submarket_asking(submarket)

    def _direction(tenant_value: float, benchmark: float) -> str:
        # Equal counts as "above": the tenant is at parity with today's asking
        # number, and after escalations/pass-throughs they are almost certainly
        # paying more than a new tenant signing tomorrow — never "below," which
        # would wrongly trigger the escalation-creep framing.
        return "above" if tenant_value >= benchmark else "below"

    if eff is not None and bldg is not None:
        return RentStory(1, _direction(eff, bldg), eff, bldg, "building")
    if eff is not None and submarket_asking is not None:
        return RentStory(2, _direction(eff, submarket_asking), eff, submarket_asking, "submarket")
    if starting is not None and bldg is not None:
        return RentStory(3, _direction(starting, bldg), starting, bldg, "building")
    if starting is not None and submarket_asking is not None:
        return RentStory(4, _direction(starting, submarket_asking), starting, submarket_asking, "submarket")
    if bldg is not None:
        return RentStory(5, None, None, bldg, "building")
    if submarket_asking is not None:
        return RentStory(6, None, None, submarket_asking, "submarket")
    return None


def _direct_line(eff: float, asking: float, where: str, direction: str) -> str:
    """Rungs 1-2 (effective vs asking), framed by direction."""
    if direction == "above":
        # Neutral information framing — no blame, no manufactured urgency.
        return (
            f"You're at ${eff:.2f}/SF effective against ${asking:.2f}/SF asking in "
            f"{where} right now — you're paying above the current asking number, and "
            f"it's worth confirming what that means for your options before your "
            f"renewal conversation."
        )
    # BELOW asking — the market's moved since they signed; factual, never
    # implying the tenant is behind or overpaying.
    return (
        f"You're at ${eff:.2f}/SF effective against ${asking:.2f}/SF asking in "
        f"{where} right now — the market's moved since you signed, and it's worth "
        f"knowing exactly where that leaves you before you renew."
    )


def _creep_line(starting: float, asking: float, quoted_by: str, direction: str) -> str:
    """Rungs 3-4 (starting rent vs asking), framed by direction.

    BELOW branch: the escalation-creep line. quoted_by is "your building's"
    (rung 3 — this exact wording is spec-locked to THIS branch only) or
    "<submarket> is" (rung 4).
    ABOVE branch: starting rent is at or above today's asking — the
    overpaying-in-your-own-building angle. Hedged (never a specific current
    rent we can't see): after escalations and pass-throughs, they're almost
    certainly paying more today than a new tenant signing tomorrow.
    """
    if direction == "above":
        return (
            f"I see your lease started at ${starting:.2f}/SF, and {quoted_by} quoting "
            f"${asking:.2f} right now — after annual escalations and expense "
            f"pass-throughs, you're almost certainly paying more today than a new "
            f"tenant signing tomorrow. I'd like to dig in and show you exactly where "
            f"you stand."
        )
    return (
        f"I see your lease started at ${starting:.2f}/SF, and {quoted_by} quoting "
        f"${asking:.2f} right now. By the nature of things — annual escalations, "
        f"operating expense pass-throughs — your rent's likely crept closer to that "
        f"asking number by now, and that's exactly the kind of thing that's easy to "
        f"miss until the decision to renew or relocate is already on top of you. "
        f"I'd like to dig in and show you exactly where you stand."
    )


def _signed_reference(lease_signed_year: Optional[int]) -> str:
    """Lease-vintage clause of the rung 5/6 hedge lines.

    Known signing year → phrasing scaled to the ACTUAL elapsed time (a lease
    signed this year is never described as sitting above the market). Unknown
    year → vague tenure phrasing with no year cited. Never a fabricated date.
    """
    if not lease_signed_year:
        return "a lot of tenants who've been in place a while are sitting above that"
    years_ago = max(0, date.today().year - int(lease_signed_year))
    if years_ago <= 1:
        return (
            f"even a lease signed as recently as {lease_signed_year} can land "
            f"differently against today's asking numbers"
        )
    if years_ago <= 3:
        return (
            f"a lot of leases signed a couple of years back, in {lease_signed_year}, "
            f"are sitting above that"
        )
    return f"a lot of leases signed back in {lease_signed_year} are sitting above that"


def build_rent_gap_line(
    effective_rent_psf: Optional[float],
    starting_rent_psf: Optional[float],
    building_asking_rent_psf: Optional[float],
    submarket: Optional[str],
    lease_signed_year: Optional[int] = None,
) -> Optional[str]:
    """Return the rent line for the tenant email, or None.

    Pure and null-safe. Rung + direction come from select_rent_story — the same
    selection that produces the call sheet's ANGLE line.
    """
    story = select_rent_story(
        effective_rent_psf, starting_rent_psf, building_asking_rent_psf, submarket
    )
    if story is None:
        return None

    where     = "your building" if story.benchmark_kind == "building" else submarket
    quoted_by = "your building's" if story.benchmark_kind == "building" else f"{submarket} is"

    if story.rung in (1, 2):
        return _direct_line(story.tenant_value, story.benchmark_value, where, story.direction)
    if story.rung in (3, 4):
        return _creep_line(story.tenant_value, story.benchmark_value, quoted_by, story.direction)
    # Rungs 5/6 — hedge framing, no direction claimed, anchored to real vintage.
    return (
        f"Asking rents in {where} are quoting around ${story.benchmark_value:.2f}/SF right now — "
        f"{_signed_reference(lease_signed_year)}. "
        f"I don't know where your lease lands, but that gap is exactly what I check."
    )


def build_angle_line(
    effective_rent_psf: Optional[float],
    starting_rent_psf: Optional[float],
    building_asking_rent_psf: Optional[float],
    submarket: Optional[str],
    lease_signed_year: Optional[int] = None,
) -> str:
    """The call sheet's ANGLE — one line stating which story the numbers
    support, derived from the SAME rung + direction selection as the email rent
    line. Internal reference for Jack mid-call, never read to the tenant."""
    story = select_rent_story(
        effective_rent_psf, starting_rent_psf, building_asking_rent_psf, submarket
    )
    if story is None:
        return "No rent data on file — no rent angle; lead with lease timing and discovery."

    where = "building" if story.benchmark_kind == "building" else f"{submarket}"
    held = ""
    if lease_signed_year and (date.today().year - int(lease_signed_year)) >= 2:
        held = f", held since {lease_signed_year}"

    if story.rung in (1, 2):
        if story.direction == "below":
            return (
                f"Below-market lease{held} — effective ${story.tenant_value:.2f}/SF vs "
                f"${story.benchmark_value:.2f}/SF {where} asking; the market's moved since they signed."
            )
        return (
            f"Paying above current asking — effective ${story.tenant_value:.2f}/SF vs "
            f"${story.benchmark_value:.2f}/SF {where} asking; neutral framing, confirm what "
            f"it means before renewal."
        )
    if story.rung in (3, 4):
        if story.direction == "below":
            return (
                f"Below-market start{held} — started at ${story.tenant_value:.2f}/SF vs "
                f"${story.benchmark_value:.2f}/SF {where} asking; escalations have likely "
                f"crept the rent toward asking."
            )
        return (
            f"Started above current asking — ${story.tenant_value:.2f}/SF vs "
            f"${story.benchmark_value:.2f}/SF {where} asking; neutral framing, confirm what "
            f"it means before renewal."
        )
    return (
        f"No tenant rent on file — hedge off {where} asking "
        f"(${story.benchmark_value:.2f}/SF); claim no direction."
    )


# ── CALL SHEET (deterministic — reference material Jack glances at mid-call) ───
#
# Built entirely in code from tenant + benchmark data; NO LLM call. Sections:
#   OPENING    — one talking-point cue: who Jack is + lease timing
#   DATA       — labeled raw values; every missing value renders "not on file"
#   ANGLE      — one line from the same rung + direction logic as the email
#   DISCOVERY  — the core-message questions as a bulleted checklist
#   PAIN PROBE — single-line cue
#   THE CLOSE  — single-line cue (references the free lease analysis)
# The sheet carries no prose framing — the email owns the framing language.

NOT_ON_FILE = "not on file"
PROVISIONAL_SUFFIX = "(provisional — verify before quoting)"

DISCOVERY_QUESTIONS = (
    "How is the current space fitting the team?",
    "What are you paying in rent right now?",
    "What's the growth trajectory for the next 12–24 months?",
    "Any floor-plan needs changing?",
    "How many parking spaces does the team need?",
    "In-office or hybrid — what's the work model?",
    "Have you started looking at options yet?",
    "Who else is involved in the decision?",
)

PAIN_PROBE_CUE = "What's the single biggest real-estate headache on your plate right now?"


def _sheet_money(value: Optional[float]) -> str:
    return f"${value:.2f}/SF" if (value or 0) > 0 else NOT_ON_FILE


def _sheet_count(value: Optional[int]) -> str:
    return f"{int(value):,}" if (value or 0) > 0 else NOT_ON_FILE


def build_call_sheet(company: dict) -> dict:
    """Build the tenant CALL SHEET deterministically — never calls an LLM.

    Null-safe on every field: missing values render "not on file", never blank,
    never invented, never a 500. Returns the call_script dict:
    {opening, data, angle, core_message, pain_probe, the_close}.
    """
    submarket = company.get("current_submarket") or ""
    lease_mo  = company.get("lease_expiry_months")

    # OPENING — a cue, not a script: who Jack is + lease timing.
    timing = (
        f"lease expiring in ~{lease_mo} months" if lease_mo is not None
        else f"lease expiry {NOT_ON_FILE}"
    )
    opening = f"{AGENT_NAME}, {FIRM_NAME} — {timing}."

    # DATA — raw values, labeled. Provisional submarket numbers are flagged.
    bench = SUBMARKET_BENCHMARKS.get(submarket)
    provisional = is_provisional_submarket(submarket)
    vacancy_val = f"{bench['vacancy_pct']:.1f}%" if bench else NOT_ON_FILE
    asking_val  = f"${bench['market_rent_psf']:.2f}/SF" if bench else NOT_ON_FILE
    if bench and provisional:
        vacancy_val += f" {PROVISIONAL_SUFFIX}"
        asking_val  += f" {PROVISIONAL_SUFFIX}"

    building_class = (company.get("current_building_class") or "").strip() or NOT_ON_FILE
    signed_year    = company.get("lease_signed_year")

    data_lines = [
        f"Effective Rent: {_sheet_money(company.get('effective_rent_psf'))}",
        f"Starting Rent: {_sheet_money(company.get('starting_rent_psf'))}",
        f"Building Asking Rent: {_sheet_money(company.get('building_asking_rent_psf'))}",
        f"Lease Signed Year: {int(signed_year) if signed_year else NOT_ON_FILE}",
        f"Lease Expires (months): {lease_mo if lease_mo is not None else NOT_ON_FILE}",
        f"SF Occupied: {_sheet_count(company.get('current_sf_occupied'))}",
        f"Headcount: {_sheet_count(company.get('current_headcount'))}",
        f"Building Class: {building_class}",
        f"Submarket Vacancy %: {vacancy_val}",
        f"Submarket Asking Rent: {asking_val}",
    ]

    angle = build_angle_line(
        company.get("effective_rent_psf"),
        company.get("starting_rent_psf"),
        company.get("building_asking_rent_psf"),
        submarket,
        lease_signed_year=signed_year,
    )

    return {
        "opening":      opening,
        "data":         "\n".join(data_lines),
        "angle":        angle,
        "core_message": "\n".join(f"• {q}" for q in DISCOVERY_QUESTIONS),
        "pain_probe":   PAIN_PROBE_CUE,
        "the_close":    FREE_ANALYSIS_LINE,
    }


def _insert_paragraph_before_ask(body: str, sentence: Optional[str]) -> str:
    """Insert `sentence` as its own paragraph before the closing ask (or the
    signature block when no ask is present). No-op when the sentence is empty
    or already present — regeneration never duplicates it."""
    if not sentence or not body or sentence in body:
        return body
    paras = body.split("\n\n")
    idx = next((i for i, p in enumerate(paras) if "I'd welcome a brief call" in p), None)
    if idx is None:
        idx = next((i for i, p in enumerate(paras) if p.strip().startswith("Thank you")), None)
    if idx is None:
        paras.append(sentence)
    else:
        paras.insert(idx, sentence)
    return "\n\n".join(paras)


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
    Build the tenant outreach package: a GPT-4o email draft plus the
    deterministic CALL SHEET (built in code — the call-sheet portion makes
    NO LLM call; GPT-4o generates the email only).

    Returns:
        {
            "email": {"subject": ..., "body": ...},
            "call_script": {"opening": ..., "data": ..., "angle": ...,
                            "core_message": ..., "pain_probe": ...,
                            "the_close": ...},
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

    # Provisional submarkets (Fix 4): placeholder benchmarks are never quoted in
    # tenant email copy — drop them from the prompt context entirely so a
    # placeholder number can't be mailed to a real tenant. (The CALL SHEET still
    # shows them, flagged "(provisional — verify before quoting)".)
    submarket_provisional = is_provisional_submarket(submarket)
    market_rent  = SUBMARKET_MARKET_RENT.get(submarket) if not submarket_provisional else None
    avg_vacancy  = SUBMARKET_AVG_VACANCY.get(submarket) if not submarket_provisional else None
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

    # Rent-gap ladder: the email's rent line. Six rungs, sharpest set field
    # wins, direction-framed; None when no rent reference exists (never an
    # invented number). The CALL SHEET's ANGLE line derives from the SAME
    # select_rent_story selection, so email and call sheet never tell
    # different stories for the same tenant.
    rent_line = build_rent_gap_line(
        company.get("effective_rent_psf"),
        company.get("starting_rent_psf"),
        company.get("building_asking_rent_psf"),
        submarket,
        lease_signed_year=company.get("lease_signed_year"),
    )

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
        # ── Email structure ──────────────────────────────────────────────────
        (
            "EMAIL BODY — follow this order exactly: "
            f"(1) LEAD with their lease timing: their lease is up in {lease_str} in {submarket}, and a "
            f"renewal-or-relocate decision is best driven early — open on this, not on any statistic. "
            + (f"(2) Include this exact rent sentence VERBATIM as its own sentence early in the body: "
               f"\"{rent_line}\" " if rent_line else "")
            + f"(3) Explain what the market window means for THEM: {market_window}. "
            + (f"{tight_supply_line} " if tight_supply_line else "")
            + f"(4) Include this full-service positioning sentence VERBATIM: \"{FULL_SERVICE_LINE}\" "
            "(5) Include exactly ONE short, open-ended pain-probe question about their current space situation. "
            f"(6) Close low-pressure with the free lease analysis offer VERBATIM: \"{FREE_ANALYSIS_LINE}\" "
            "Body MINIMUM 6 sentences, MAXIMUM 170 words (excluding the signature block); subject under 9 words."
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
        # The call side is a deterministic CALL SHEET built in code
        # (build_call_sheet) — the model generates the EMAIL only.
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

    if market_rent:
        submarket_context = (
            f"SUBMARKET BENCHMARKS — {submarket} (CBRE Q1 2026):\n"
            f"  Market rent:       ${market_rent:.2f}/SF/yr NNN  ({rent_vs_nova_str})\n"
            + (f"  Submarket vacancy: {vacancy_str}" if show_vacancy else
               "  [Submarket vacancy is above the tenant-side citation threshold — do not cite in email copy]")
        )
    elif submarket_provisional:
        submarket_context = (
            f"SUBMARKET: {submarket} (benchmark is provisional/placeholder — do NOT "
            f"quote any submarket rent or vacancy figure in the email)"
        )
    else:
        submarket_context = f"SUBMARKET: {submarket} (no benchmark data)"

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

    # ── Deterministic rent-ladder guarantee (email) ───────────────────────────
    # The lease-timing opener, rent line, full-service positioning, and free
    # lease-analysis close are injected post-LLM whenever the model dropped
    # them, so every rung of the ladder survives regeneration. Each insert is
    # guarded by a presence check — verbatim model output is never duplicated.
    email = result.get("email") if isinstance(result.get("email"), dict) else None
    if email is not None and email.get("body"):
        body = email["body"]
        if lease_mo is not None and f"{lease_mo} month" not in body:
            paras = body.split("\n\n")
            timing_line = (
                f"Your lease is expiring in about {lease_mo} months — that's the window "
                f"where getting ahead of the decision still pays."
            )
            paras.insert(1 if len(paras) > 1 else len(paras), timing_line)
            body = "\n\n".join(paras)
        # Insertion order puts them rent line → positioning → analysis offer,
        # each ahead of the closing ask.
        body = _insert_paragraph_before_ask(body, rent_line)
        body = _insert_paragraph_before_ask(body, FULL_SERVICE_LINE)
        body = _insert_paragraph_before_ask(body, FREE_ANALYSIS_LINE)
        email["body"] = body

    # ── CALL SHEET — fully deterministic, built from tenant + benchmark data.
    # Whatever the model returned for a call script (it isn't asked for one) is
    # discarded; the sheet can never drift from the data or the email's story.
    result["call_script"] = build_call_sheet(company)

    # The broker name appears exactly once — in the signature block. As a defensive
    # guard, drop any street-address token the model may have echoed (tenant side
    # shows submarket only).
    if email is not None and email.get("body"):
        email["body"] = _strip_street_address(email["body"])
    return result
