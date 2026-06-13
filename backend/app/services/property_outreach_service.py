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
# LEGACY / FORBIDDEN: this closing line was previously injected into FSV emails and
# duplicated the hardcoded social-proof sentence. It is NO LONGER injected anywhere —
# the social-proof line from _inject_hardcoded_sentences is the single canonical pitch.
# Kept only as the reference string for the regression test that asserts its ABSENCE
# (test_outreach_fixes.py). Do not re-wire this into generation.
_PHASE1_FSV_CLOSING = (
    "I have a qualified tenant in mind — happy to share more if there's interest "
    "in a conversation."
)

# Fix 3: SF-needed fallback. When a matched tenant has no real occupied SF
# (sf_occupied null/zero → estimated_sf_needed None), property-side copy must
# NEVER show a blank, placeholder, or estimated number. Instead it states the
# requirement qualitatively with this exact wording.
def _tenant_sf_fallback_sentence(submarket: Optional[str], lease_expiry_months) -> str:
    sub = submarket or "submarket"
    if lease_expiry_months is not None:
        mo = "month" if int(lease_expiry_months) == 1 else "months"
        timing = f"around {lease_expiry_months} {mo}"
    else:
        timing = "the coming months"
    return (
        f"A qualified tenant is looking for right-sized office space in the "
        f"{sub}, with a lease commencement in {timing}."
    )


# ── Fix 4: two distinct call scripts keyed by call target (OWNER | TENANT) ──────
# The information-extraction questions and the 48-hour close are VERBATIM per spec.
# These are built deterministically in code (not via the LLM) so the ordered
# questions and the close string are guaranteed on every draft.
OWNER_CALL_QUESTIONS = (
    "What's your current thinking on the vacancy — are you working with anyone, or is it still open?",
    "What would the right tenant profile look like for you — any restrictions on use or build-out?",
    "Has anything changed on your end with the property — ownership, financing, timeline?",
)
OWNER_CALL_CLOSE = (
    "I want to move on this quickly — I'll take what you've told me, confirm a few things on my end, "
    "and get back to you within 48 hours. Does that work?"
)

TENANT_CALL_QUESTIONS = (
    "Where are you in your decision process right now — have you started looking, or are you still in planning mode?",
    "What's driving the move — is it the space itself, the location, the economics, or something else?",
    "Who else is involved in the final call on this?",
)
TENANT_CALL_CLOSE = (
    "I'm going to take this back, match it against what I'm seeing in the submarket, and get back to you "
    "within 48 hours with something specific. That work for you?"
)


def build_call_script(call_target: str, property_dict: dict, tenant_dict: Optional[dict] = None) -> dict:
    """Return a deterministic call script for the given call target.

    call_target: 'OWNER' or 'TENANT'.
      OWNER  — opening references the property + vacancy by SUBMARKET only (never the
               street address); core message frames a qualified tenant whose timeline
               aligns with the owner's vacancy.
      TENANT — opening leads with the tenant's lease-timing pain (not the property);
               core message frames submarket-exclusive deal flow moving off-market.

    The three information-extraction questions appear in order; the close carries the
    verbatim 48-hour commitment. Returns opening / core / questions / pain_probe / close.
    """
    target = (call_target or "OWNER").upper()
    submarket = property_dict.get("submarket") or "Northern Virginia"

    if target == "TENANT":
        exp = (tenant_dict or {}).get("lease_expiry_months")
        if exp is not None:
            timing = f"about {exp} {'month' if int(exp) == 1 else 'months'}"
            opening = (
                f"Hi — I know your lease is coming up in {timing}, and a decision like that has a way "
                f"of sneaking up on people, so I wanted to get ahead of it with you."
            )
        else:
            opening = (
                "Hi — I know your lease is coming up before long, and a decision like that has a way "
                "of sneaking up on people, so I wanted to get ahead of it with you."
            )
        core = (
            f"I work {submarket} exclusively, and right now I'm seeing good space get spoken for before "
            f"it ever hits the open market — which is exactly why I wanted to reach you early rather than late."
        )
        questions = TENANT_CALL_QUESTIONS
        close = TENANT_CALL_CLOSE
    else:  # OWNER
        vac = property_dict.get("vacancy_pct")
        vac_str = f", and I see you're showing roughly {float(vac):.0f}% vacancy" if vac else ""
        opening = (
            f"Hi — I'm calling about the unleased office space at your property in {submarket}{vac_str}. "
            f"I'll keep this quick."
        )
        core = (
            f"I'm working with a qualified tenant whose timeline lines up with your availability in "
            f"{submarket}, and I'd rather bring them to you directly than watch good space sit while the "
            f"right fit is already out there looking."
        )
        questions = OWNER_CALL_QUESTIONS
        close = OWNER_CALL_CLOSE

    return {
        "opening": opening,
        "core": core,
        "questions": list(questions),
        # Existing pipeline carries a single pain_probe field — pack the ordered
        # questions into it so nothing downstream (schema / DB / frontend) changes.
        "pain_probe": "\n".join(questions),
        "close": close,
    }


def _inject_hardcoded_sentences(email_body: str, inject_social_proof: bool = True) -> str:
    """Post-LLM: ensure hardcoded intro (after greeting) survives every regeneration.
    Social proof is injected only when inject_social_proof=True (tenant-side only).
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

    # ── Social proof before signature block (tenant-side only) ───────────────
    if inject_social_proof and _HARDCODED_SOCIAL_PROOF not in body:
        sig_idx = next(
            (i for i, para in enumerate(paras) if para.strip().startswith("Thank you")),
            None,
        )
        if sig_idx is not None:
            paras.insert(sig_idx, _HARDCODED_SOCIAL_PROOF)
        else:
            paras.append(_HARDCODED_SOCIAL_PROOF)

    return "\n\n".join(paras)


# Owner-name values that should fall back to the generic salutation.
_UNKNOWN_OWNER_VALUES = frozenset(("unknown", "n/a", "tbd", "none", ""))


def _clean_owner_name(raw: str) -> str:
    """Return empty string for generic/unknown owner names so the salutation falls back."""
    return "" if (raw or "").strip().lower() in _UNKNOWN_OWNER_VALUES else (raw or "").strip()


def _sanitize_bracket_placeholders(body: str, property_dict: dict) -> str:
    """Replace LLM-echoed bracket placeholders with correct values or remove them.

    Handles:
    - Salutation: 'Dear [Owner's Name],' or 'Dear [Name],' → correct salutation
    - 'Dear Unknown,' → correct salutation
    - Remaining [placeholder] tokens → removed
    """
    import re as _re
    owner_raw = _clean_owner_name(property_dict.get("owner_name") or "")
    fallback_sal = f"Dear {owner_raw}," if owner_raw else "Dear Property Owner,"
    # Fix bracket salutation at start of a line: "Dear [...],"
    body = _re.sub(r'^Dear \[[^\]\n]*\],', fallback_sal, body, flags=_re.MULTILINE)
    # Fix "Dear Unknown," (case-insensitive)
    body = _re.sub(r'^Dear Unknown,', fallback_sal, body, flags=_re.MULTILINE | _re.IGNORECASE)
    # Remove any remaining [Placeholder] tokens (starts with letter, no line-breaks)
    body = _re.sub(r'\[[A-Za-z][^\]\n]*\]', '', body)
    return body


def _dedup_lease_clause(body: str, lease_expiry_m) -> str:
    """Ensure the lease-expiry phrase appears exactly once in tenant-side copy.

    If the LLM echoes it a second time (near the close), the duplicate sentence
    is removed. The first occurrence (opening urgency hook) is always kept.
    """
    if lease_expiry_m is None:
        return body
    import re as _re
    mo = "month" if int(lease_expiry_m) == 1 else "months"
    frag_l = f"approximately {lease_expiry_m} {mo}".lower()
    lower = body.lower()
    p1 = lower.find(frag_l)
    if p1 < 0:
        return body
    p2 = lower.find(frag_l, p1 + len(frag_l))
    if p2 < 0:
        return body  # only one occurrence — nothing to do
    # Sentence start: find the last sentence-ending punctuation + whitespace before p2
    last_sent_end = -1
    for m in _re.finditer(r'[.!?]\s+', body[:p2]):
        last_sent_end = m.end()
    sent_start = last_sent_end if last_sent_end >= 0 else 0
    # Sentence end: first punctuation after p2
    m_end = _re.search(r'[.!?]', body[p2:])
    sent_end = p2 + m_end.end() if m_end else len(body)
    # Consume trailing whitespace so the join is clean
    while sent_end < len(body) and body[sent_end] in (' ', '\n'):
        sent_end += 1
    before = body[:sent_start].rstrip()
    after  = body[sent_end:].lstrip()
    if before and after:
        return before + '\n\n' + after
    return (before + after).strip()


VALID_TYPES = {"tenant_match", "for_sale_vacancy", "listing_rep", "acquisition"}

# CBRE Q1 2026 NoVA office benchmarks (avg full-service rent / vacancy %).
# Derived from the single source of truth in app.config.SUBMARKET_BENCHMARKS
# so a quarterly benchmark refresh updates email copy automatically.
from app.config import SUBMARKET_BENCHMARKS as _SUBMARKET_BENCHMARKS
from app.config import OWNER_VACANCY_CITE_THRESHOLD, TENANT_VACANCY_CITE_THRESHOLD

CBRE_2026_BENCHMARKS = {
    submarket: {"rent": b["market_rent_psf"], "vacancy": b["vacancy_pct"]}
    for submarket, b in _SUBMARKET_BENCHMARKS.items()
}


def _submarket_context(submarket: Optional[str], side: str = "all") -> str:
    """Return a CBRE Q1 2026 benchmark string for the given submarket.

    side="owner"  — vacancy cited only when vacancy > OWNER_VACANCY_CITE_THRESHOLD (15%)
    side="tenant" — vacancy cited only when vacancy < TENANT_VACANCY_CITE_THRESHOLD (10%)
    side="all"    — always include vacancy (default; preserves backward compatibility)
    """
    if not submarket:
        return ""
    b = CBRE_2026_BENCHMARKS.get(submarket, {})
    if not b:
        return ""
    vacancy = b["vacancy"]
    include_vacancy = (
        side == "all"
        or (side == "owner"  and vacancy > OWNER_VACANCY_CITE_THRESHOLD)
        or (side == "tenant" and vacancy < TENANT_VACANCY_CITE_THRESHOLD)
    )
    rent_part = f"CBRE Q1 2026 {submarket}: avg rent ${b['rent']:.2f}/SF"
    return f"{rent_part}, vacancy {vacancy}%" if include_vacancy else rent_part


def _submarket_vacancy(submarket: Optional[str]) -> Optional[float]:
    """Return the CBRE Q1 2026 vacancy rate (as a percent, e.g. 7.4) for a
    submarket, or None if unknown."""
    if not submarket:
        return None
    b = CBRE_2026_BENCHMARKS.get(submarket)
    return b["vacancy"] if b else None


# Vacancy-rate thresholds (as fractions of 1.0) that gate the CBRE urgency line.
#   Tenant: a LOW-vacancy submarket creates scarcity urgency  → cite only below 10%.
#   Owner:  a HIGH-vacancy submarket makes tenants scarce      → cite only above 15%.
TENANT_VACANCY_URGENCY_THRESHOLD = 0.10
OWNER_VACANCY_URGENCY_THRESHOLD  = 0.15


def _tenant_vacancy_sentence(submarket: Optional[str]) -> Optional[str]:
    """Fix 2: tenant-side CBRE vacancy line — only when submarket vacancy is BELOW
    10% (scarcity urgency). At/above 10%, return None and rely on lease timing.
    Vacancy only — never rent PSF.
    """
    vac = _submarket_vacancy(submarket)
    if vac is None or (vac / 100.0) >= TENANT_VACANCY_URGENCY_THRESHOLD:
        return None
    return f"With {submarket}'s vacancy rate at {vac:.1f}%, quality options are limited."


def _owner_vacancy_sentence(submarket: Optional[str]) -> Optional[str]:
    """Fix 3: owner-side CBRE vacancy line — only when submarket vacancy is ABOVE
    15% (tenant scarcity). At/below 15%, return None so the caller substitutes a
    lease-timeline urgency line instead. Vacancy only — never rent PSF.
    """
    vac = _submarket_vacancy(submarket)
    if vac is None or (vac / 100.0) <= OWNER_VACANCY_URGENCY_THRESHOLD:
        return None
    return (
        f"With {submarket}'s vacancy rate at {vac:.1f}%, qualified tenants are harder "
        f"to come by — this is one worth moving on."
    )


def _owner_lease_timeline_sentence(tenant_dict: Optional[dict]) -> str:
    """Owner-side urgency fallback when the vacancy line is omitted: lean on the
    matched tenant's lease timeline as the reason to move now. Never names the
    tenant or its industry."""
    exp = (tenant_dict or {}).get("lease_expiry_months")
    if exp is not None:
        mo = "month" if int(exp) == 1 else "months"
        return (
            f"With the tenant's lease expiring in approximately {exp} {mo}, the timing is the "
            f"urgency here — this is one worth moving on while it lines up."
        )
    return (
        "With the tenant's lease timeline already in motion, the timing is the urgency "
        "here — this is one worth moving on while it lines up."
    )


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
            _u = resp.usage
            _cached = getattr(_u, "cache_read_input_tokens", 0) or 0
            print(f"[TOKEN LOG] input: {_u.input_tokens}, output: {_u.output_tokens}, cached: {_cached}")
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
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.7,
    )
    usage = resp.usage
    print(f"[TOKEN LOG] input: {usage.prompt_tokens}, output: {usage.completion_tokens}")
    return resp.choices[0].message.content.strip()


def _addr(p: dict) -> str:
    parts = [p.get("address", ""), p.get("city", ""), p.get("state", "")]
    return ", ".join(x for x in parts if x)


def _prop_context(p: dict) -> str:
    # Minimum fields only — reduces input tokens by ~200–300 per call.
    # Fields accessed directly from `p` in builders (owner_name, landlord_representative,
    # days_on_market, years_owned, dominant_score_type, etc.) are NOT duplicated here.
    lines = [
        f"Address: {p.get('address', 'N/A')}",
        f"Submarket: {p.get('submarket', 'N/A')}",
        f"SF Available: {p.get('sf_avail', 'N/A')}",
        f"Occupancy/Vacancy %: {p.get('vacancy_pct', 'N/A')}",
        f"In-Place Rent: ${p['in_place_rent_psf']:.2f}/SF" if p.get("in_place_rent_psf") else "In-Place Rent: N/A",
        f"Listed For Sale: {'Yes' if p.get('listed_for_sale') else 'No'}",
    ]
    if p.get("loan_maturity_year"):
        lines.append(f"Loan Maturity Year: {p.get('loan_maturity_year')}")
    if p.get("days_on_market"):
        lines.append(f"Days on Market: {p.get('days_on_market')}")
    bm = _submarket_context(p.get("submarket"), side="owner")
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
    benchmark = _submarket_context(p.get("submarket"), side="owner")
    submarket = p.get("submarket") or "Northern Virginia"

    # Fix 3: property-side always uses full street address
    address_display = p.get("address") or p.get("submarket") or "Northern Virginia"
    owner_raw  = _clean_owner_name(p.get("owner_name") or "")
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
    # Fix 2: the owner does not need sector detail — the matched tenant is referred
    # to ONLY as "a qualified tenant". Industry / sector is never passed to the model.
    if tenant_dict is not None:
        sf       = tenant_dict.get("current_sf_occupied")
        exp      = tenant_dict.get("lease_expiry_months")
        sf_str   = f"{sf:,} SF" if sf else None
        exp_str  = (f"approximately {exp} {'month' if exp == 1 else 'months'}" if exp is not None else None)
        # Fix 3 / Fix 5: only reference a real SF number; otherwise use the
        # qualitative "right-sized office space" framing (never a placeholder).
        sf_part  = f" looking for {sf_str}" if sf_str else " looking for right-sized office space"
        exp_part = f" with a lease expiring {exp_str}" if exp_str else ""
        tenant_hint = (
            f"\nPrimary matched tenant profile (refer to them ONLY as 'a qualified tenant'; "
            f"do NOT reveal the tenant's name, industry, or sector): "
            f"a qualified tenant{sf_part} in {submarket}{exp_part}"
        )
    elif tenant_context:
        tenant_hint = (
            f"\nPrimary matched tenant profile (refer to them ONLY as 'a qualified tenant'; "
            f"do NOT reveal the tenant's name, industry, or sector)."
        )
    else:
        tenant_hint = ""

    landlord_rep = p.get("landlord_representative")
    listed_for_sale = bool(p.get("listed_for_sale"))
    for_sale_with_vacancy = listed_for_sale and sf_avail and sf_avail > 0

    if target_type == "broker" and landlord_rep:
        addressee     = f"the landlord representative ({landlord_rep})"
        framing       = (
            "Write broker-to-broker, peer to peer. Lead with the matched tenant referred to "
            "ONLY as 'a qualified tenant' (SF range, submarket, approximate lease expiry) — "
            "never reveal the tenant's company name, industry, or sector."
        )
    elif target_type == "owner":
        addressee = "the property owner"
        framing   = (
            "Write broker-to-owner. Lead with the matched tenant referred to ONLY as "
            "'a qualified tenant' (SF range and submarket) — never reveal the tenant's "
            "company name, industry, or sector. Position the note as bringing them a credible "
            "prospect for the vacancy."
        )
    else:
        addressee = "the property owner" if not landlord_rep else f"the landlord representative ({landlord_rep})"
        framing   = (
            "Choose tone based on whether the recipient is the owner or a listing "
            "broker. Lead with the matched tenant referred to ONLY as 'a qualified tenant'; "
            "never reveal the tenant's company name, industry, or sector — describe them by "
            "size and submarket only. "
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

    # Fix 3: CBRE data is optional and must earn its place against the hook.
    # Null-safe: with no benchmark, instruct OMISSION rather than inventing a number.
    proof_step = (
        f"You MAY cite exactly ONE CBRE Q1 2026 {submarket} number (vacancy rate or avg asking rent), "
        f"and only if it directly sharpens the vacancy hook in the same sentence — if it does not earn "
        f"its place, cut it entirely. "
        if benchmark else
        "Omit any market-statistic sentence; do NOT invent or cite a number you were not given. "
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker who knows the "
        f"Northern Virginia office market cold. You are emailing {addressee}. {framing} "
        f"Write like a sharp broker who just picked up the phone and typed a quick personal note — "
        f"NOT a platform output, NOT a mail-merge. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Use the full property address once in the body: 'your property at {address_display}'. "
        f"HUMANIZATION RULES — follow ALL: "
        f"(1) No more than 3 short paragraphs. No bullet points. No data dumps. "
        f"(2) ONE hook only — this property's open space ({urgency_signal}). Build the note around it; "
        f"do NOT also lean on the tenant's lease timing as a second hook. "
        f"(3) Lead with the matched tenant referred to ONLY as 'a qualified tenant' with an approximate "
        f"size and the submarket (e.g. 'a qualified tenant looking for ~3,500 SF in {submarket}'). "
        f"NEVER reveal the tenant's company name, industry, or sector — the owner does not need sector detail. "
        f"(4) {proof_step}"
        f"(5) End with ONE low-friction ask and nothing else: 'I'd welcome a brief call at your convenience.' "
        f"Do NOT write 'I propose a short call' or any other call-to-action before it. "
        f"Do NOT stack asks (no separate rent / tour / OM request). "
        f"NEVER suggest specific days of the week. "
        f"{sale_clause}{secondary_clause}"
    )
    user = (
        f"Property details:\n{ctx}{tenant_hint}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line — short and human, no platform jargon)\n"
        "2. Email body — maximum 150 words (excluding signature block); no more than 3 short paragraphs; "
        "   no bullet points; sound like a real broker dashing off a quick note; "
        "   use the salutation from the system prompt exactly as written — do NOT use bracket placeholders; "
        "   describe the matched tenant ONLY as 'a qualified tenant' with approximate SF and the submarket — "
        "   never the company name and never the industry/sector; "
        "   build the note around the single vacancy hook; at most ONE CBRE data point and only if it "
        "   supports that hook; ONE closing ask: 'I'd welcome a brief call at your convenience.'\n"
        "3. Call script: Opening\n"
        "4. Call script: Core message\n"
        "5. Call script: Information-gathering questions\n"
        "6. Call script: Close\n\n"
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
    benchmark = _submarket_context(p.get("submarket"), side="owner")
    submarket = p.get("submarket") or "Northern Virginia"
    landlord_rep = p.get("landlord_representative")
    addressee = f"the landlord representative ({landlord_rep})" if landlord_rep else "the property owner"
    framing = "broker-to-broker" if landlord_rep else "broker-to-owner"

    # Fix 3: property-side uses full street address
    address_display = p.get("address") or submarket
    owner_raw  = _clean_owner_name(p.get("owner_name") or "")
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

    # Phase 1 property-side: industry, NAICS code/description, and all sector
    # fields are stripped — the LLM sees only that a qualified prospect exists,
    # plus SF needed and lease expiry.  No industry label reaches the model.
    # The closing line is injected post-LLM in generate_property_outreach.
    if tenant_dict is not None:
        sf  = tenant_dict.get("current_sf_occupied")
        exp = tenant_dict.get("lease_expiry_months")
        sf_str  = f"{sf:,} SF" if sf else None
        exp_str = (f"approximately {exp} {'month' if exp == 1 else 'months'}" if exp is not None else None)
        seeking_part = f" They are seeking {sf_str}." if sf_str else ""
        expiry_part  = f" Their lease expires {exp_str}." if exp_str else ""
        tenant_hint = (
            f"\nA qualified tenant prospect has been matched to this property. "
            f"Do NOT reveal their name, industry, NAICS code, or any sector label."
            + seeking_part
            + expiry_part
        )
        demand_clause = (
            "Reference a single qualified prospect — do NOT mention their industry, "
            "company name, NAICS code, or headcount. "
            "The singular, direct ask must be verbatim: "
            "'Would you be open to leasing a portion of the available space while "
            "the property is being marketed for sale?' "
            "Do NOT reference multiple tenants."
        )
    elif tenant_context:
        tenant_hint = (
            f"\nA qualified tenant prospect exists (do NOT reveal name, industry, "
            f"NAICS code, or headcount): {tenant_context}"
        )
        demand_clause = (
            "Reference a potential qualified prospect — no company name, no industry label, "
            "no NAICS code, no headcount, no SF numbers. "
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

    # Restructured body sequence. Proof step is null-safe (omit when no benchmark).
    proof_step = (
        f"(3) PROOF — cite exactly ONE CBRE Q1 2026 {submarket} data point (a single number — "
        f"vacancy rate or avg asking rent); cut filler like 'indicating strong demand'. "
        if benchmark else
        "(3) PROOF — omit any market-statistic sentence; do NOT invent or cite a number you were not given. "
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        f"specialising in Northern Virginia office. You are addressing {addressee} ({framing}). "
        f"This property is currently listed for sale AND has vacant space. "
        f"Write like a sharp, concise professional — every sentence earns its place. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Use the full property address once in the body: 'your property at {address_display}'. "
        f"Structure the body in this EXACT sequence: "
        f"(1) HOOK — lead with the qualified prospect: a credible tenant is interested in leasing space "
        f"in this building, described ONLY by approximate target SF and timing. "
        f"(2) WHY-NOW + LEASING QUESTION — because the property is listed for sale, NEVER assume the owner "
        f"wants to lease. {demand_clause} Pair the ask with the current {urgency_signal} as the reason it "
        f"is worth considering, and note in ONE sentence that a tenant in place can enhance the property's "
        f"appeal and shorten the marketing window. "
        f"{proof_step}"
        f"(4) ASK — close with EXACTLY this sentence: 'I'd welcome a brief call at your convenience.' "
        f"Do NOT add 'I propose a short call' or any other call-to-action before it. "
        f"NEVER suggest specific days of the week."
    )
    body_instruction = (
        "2. Email body — maximum 150 words (excluding signature block); follow the 4-step sequence "
        "   from the system prompt exactly (hook → why-now + leasing question → one proof point → single ask); "
        "   use full property address once; reference one qualified prospect (no industry label, no company "
        "   name, no headcount); include the leasing question verbatim: 'Would you be open to leasing a "
        "   portion of the available space while the property is being marketed for sale?'; "
        "   ONE submarket data point only; ONE closing ask — a short call\n"
        if (tenant_dict is not None or tenant_context) else
        "2. Email body — maximum 150 words (excluding signature block); follow the 4-step sequence "
        "   from the system prompt exactly (hook → why-now + leasing question → one proof point → single ask); "
        "   use full property address once; reference demand generally (never name a tenant); "
        "   include the leasing question verbatim: 'Would you be open to leasing a portion of the available "
        "   space while the property is being marketed for sale?'; "
        "   ONE submarket data point only; ONE closing ask — a short call\n"
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
    benchmark = _submarket_context(p.get("submarket"), side="tenant")
    asset_class = p.get("asset_class") or "office"
    # Fix 1: round the property's available SF to the nearest 100 before it is ever
    # injected into tenant-side copy. The raw figure is never shown to the tenant.
    raw_sf_avail = p.get("sf_avail") or 0
    sf_avail = round(raw_sf_avail / 100) * 100 if raw_sf_avail else 0
    in_place_rent = p.get("in_place_rent_psf")
    asking_rent = in_place_rent if in_place_rent and in_place_rent > 0 else None

    tenant_name    = tenant_dict.get("name") or "the tenant"
    contact_name   = tenant_dict.get("primary_contact_name") or ""
    industry       = tenant_dict.get("industry") or "professional services firm"
    # Fix 4: headcount is NOT passed to tenant-side copy — SF needed and lease
    # expiry are the permitted identifiers. Never reference headcount in tenant emails.
    sf_needed      = tenant_dict.get("current_sf_occupied")
    lease_expiry_m = tenant_dict.get("lease_expiry_months")
    submarket_pref = tenant_dict.get("current_submarket") or submarket

    # Detect class downgrade: tenant currently occupies higher-class space
    from app.services.match_scoring import _class_rank as _cr
    _t_rank = _cr(tenant_dict.get("current_building_class"))
    _p_rank = _cr(p.get("asset_class"))
    is_class_downgrade = (
        _t_rank is not None and _p_rank is not None and _t_rank > _p_rank
    )

    greeting = (
        f"Hi {contact_name},"
        if contact_name
        else f"Hi {tenant_name} Team,"
    )

    lease_clause = (
        f"With your lease expiring in approximately {lease_expiry_m} {'month' if lease_expiry_m == 1 else 'months'}"
        if lease_expiry_m is not None
        else "With your upcoming lease expiry"
    )

    rent_clause = (
        f" at approximately ${asking_rent:.2f}/SF" if asking_rent else ""
    )
    # Fix 3: SF always displays as approximate, rounded to the nearest 100.
    sf_clause = (
        f" with approximately {sf_avail:,} SF available" if sf_avail else ""
    )

    sf_needed_rounded = round(sf_needed / 100) * 100 if sf_needed else None
    sf_display  = f"approximately {sf_needed_rounded:,} SF" if sf_needed_rounded else None
    exp_display = (f"{lease_expiry_m} {'month' if lease_expiry_m == 1 else 'months'}" if lease_expiry_m is not None else None)

    # Space-fit note: reference SF match only — no headcount
    space_fit_note = (
        f"Space-fit note: the property has approximately {sf_avail:,} SF available — "
        f"reference this as well-matched for a company needing {sf_display}."
        if sf_avail and sf_needed else ""
    )

    tenant_profile_lines = [
        f"Tenant: {tenant_name}",
        f"Industry: {industry}",
        *([ f"SF Needed: {sf_display}" ] if sf_display else []),
        *([ f"Lease Expiry: {exp_display}" ] if exp_display else []),
        f"Submarket Preference: {submarket_pref}",
    ]
    tenant_profile = "\n".join(tenant_profile_lines)

    property_profile_lines = [
        *([] if is_class_downgrade else [f"Asset Class: {asset_class}"]),
        f"Submarket: {submarket}",
        f"SF Available: {sf_avail or 'N/A'}",
        *([ f"Asking Rent: ${asking_rent:.2f}/SF" ] if asking_rent else []),
    ]
    property_profile = "\n".join(property_profile_lines)

    # SF-fit constraint for system prompt (no headcount). SF stays approximate.
    sf_fit_constraint = (
        f"\n- The available space (approximately {sf_avail:,} SF) is a good fit for a company "
        f"needing {sf_display} — weave this in naturally, and always phrase any SF figure as "
        f"'approximately X SF'. Never quote their own data back to them."
        if sf_avail and sf_needed else
        "\n- Always phrase any square-footage figure as 'approximately X SF' (rounded), never an exact number."
    )

    # Fix 3: the only market statistic allowed on tenant-side is a single submarket
    # VACANCY-rate urgency line, injected deterministically post-LLM (see
    # generate_property_outreach). The model must not add any stats of its own —
    # especially never rent PSF.
    tenant_proof_rule = (
        "Do NOT cite or invent any market statistic, rent figure, or vacancy "
        "number — a single market line is added separately. Never mention rent PSF."
    )

    # For class-downgrade pairs: omit the class label from the property description
    # and add an explicit framing rule so the opening never leads with the building.
    prop_description = (
        f"an office property in {submarket}{sf_clause}{rent_clause}"
        if is_class_downgrade
        else f"a {asset_class} property in {submarket}{sf_clause}{rent_clause}"
    )
    downgrade_rule = (
        f"\n\nCLASS DOWNGRADE FRAMING — STRICT RULES (override general rules where they conflict):"
        f"\n  PROHIBITION 1: Do NOT mention building class, class ratings, a 'Class A/B/C' label, "
        f"class differences, or ANY language implying the tenant would be moving down in quality."
        f"\n  PROHIBITION 2: Do NOT lead paragraph one with the property, space availability, or building "
        f"features. Paragraph one MUST be entirely about the tenant's lease timing, {industry} industry "
        f"context, and the {submarket} market — nothing else."
        f"\n  PROHIBITION 3: Paragraph two may reference space availability (SF and {submarket} only — "
        f"no class, no address). Frame it as 'options are moving' or 'timing matters in this market', "
        f"NOT 'I found you a space' or 'I have something for you'."
        f"\n  GOAL: This email exists to get a phone call, not pitch a property. "
        f"End with a specific, time-bound ask: 'Are you free this week or next?' — "
        f"not 'I\\'d welcome a brief call at your convenience.'"
        if is_class_downgrade else ""
    )

    close_rule = (
        "End with a specific, time-bound ask: 'Are you free this week or next?' — "
        "the goal is a phone call, not a property pitch."
        if is_class_downgrade else
        "End with ONE low-friction ask and nothing else: 'I\\'d welcome a brief call at your convenience.'"
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate agent who works the "
        f"Northern Virginia office market every day. You are emailing the decision maker at a tenant "
        f"company about space that fits them. "
        f"Write like a sharp broker who just got off the phone and typed a quick personal note — warm "
        f"and direct, NEVER a mail-merge or a platform output. "
        f"\n\nHUMANIZATION RULES — follow ALL:"
        f"\n- No more than 3 short paragraphs. No bullet points. No data dumps."
        f"\n- ONE hook only: their lease timing ({lease_clause.lower()}). Open on that pain in your own "
        f"words and do NOT repeat the lease-expiry phrase later; do NOT also hook on the building's vacancy."
        f"\n- Greet by name if available; otherwise use 'Hi [Company Name] Team,'."
        f"\n- Describe the property GENERALLY as '{prop_description}'. NEVER reveal the street address."
        f"\n- Do NOT describe the tenant company back to themselves; do NOT reference their headcount or team size."
        + downgrade_rule
        + sf_fit_constraint
        + f"\n- {tenant_proof_rule}"
        f"\n- Tone: knowledgeable and consultative — a trusted market expert, not a salesperson."
        f"\n- Do NOT reveal that any web search or platform tool was used; do NOT mention other tenants or properties."
        f"\n- NEVER suggest specific days of the week."
        f"\n- {close_rule}"
    )

    user = (
        f"Tenant profile (your context only — do NOT describe the tenant to themselves):\n{tenant_profile}\n\n"
        f"Property profile (describe generally — NEVER reveal street address):\n{property_profile}\n\n"
        f"Market benchmark: {benchmark or 'N/A'}\n\n"
        f"Greeting to use: {greeting}\n"
        f"Lease-timing hook (this is the ONE hook — open the note on it, in your own words, and do not "
        f"repeat it later): \"{lease_clause}.\"\n"
        + (f"{space_fit_note}\n" if space_fit_note else "")
        + "\nWrite:\n"
        "1. Email subject line (one line — human, reference the submarket and the lease timing, no street address)\n"
        "2. Email body — maximum 150 words (excluding signature block); no more than 3 short paragraphs; "
        "   no bullet points; open on the lease-timing hook, then the fit, then the single ask; "
        + (f"   describe the property generally (asset class, submarket, SF available, asking rent ${asking_rent:.2f}/SF), " if asking_rent else
           "   describe the property generally (asset class, submarket, SF available — do NOT include a specific rent figure), ")
        + (f"   you may note that the available space (approximately {sf_avail:,} SF) suits a company needing {sf_display}, " if sf_avail and sf_needed else "")
        + "   always phrase SF as 'approximately X SF'; "
        + "   end with 'I'd welcome a brief call at your convenience.'\n"
        "3. Call script: Opening\n"
        "4. Call script: Core message\n"
        "5. Call script: Information-gathering questions\n"
        "6. Call script: Close\n\n"
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
    benchmark = _submarket_context(p.get("submarket"), side="owner")

    # Fix: safe address, owner_name, and salutation
    address_display = p.get("address") or p.get("submarket") or "Northern Virginia"
    owner_raw  = _clean_owner_name(p.get("owner_name") or "")
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
    benchmark = _submarket_context(p.get("submarket"), side="owner")
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
    recipient_raw   = _clean_owner_name(sales_contact or p.get("owner_name") or "")
    salutation      = f"Dear {recipient_raw}," if recipient_raw else "Dear Property Owner,"
    addressee       = f"the sales broker ({sales_contact})" if sales_contact else "the property owner"

    # Context observation for the second sentence — stated as neutral market
    # context, NEVER as pressure on the owner. Hold period in particular is phrased
    # as a plain factual observation ("given your long-term ownership"), never as a
    # reason the owner should feel pressure to sell (Fix 1).
    dom         = p.get("days_on_market")
    vacancy_pct = p.get("vacancy_pct")
    years_owned = p.get("years_owned")
    if dom and int(dom) > 30:
        context_signal = f"on market {dom} days"
    elif vacancy_pct and float(vacancy_pct) > 0:
        context_signal = f"{float(vacancy_pct):.0f}% vacancy"
    elif years_owned:
        context_signal = f"given your long-term ownership ({years_owned}-year hold)"
    else:
        context_signal = signal_hint

    # Fix 5: benchmark clause (omit if no data)
    benchmark_clause = (
        " Include exactly ONE submarket data point (vacancy rate or avg asking rent) from CBRE Q1 2026 mid-body."
        if benchmark else ""
    )

    # Listing-aware framing: a property that is actively marketed for sale must NOT
    # be approached as an "off-market" opportunity. When listed, acknowledge that
    # it is on the market; otherwise keep the discreet off-market framing.
    listed_for_sale = bool(p.get("listed_for_sale"))
    if listed_for_sale:
        listing_framing = (
            f"This property is currently listed and actively marketed for sale — "
            f"acknowledge that directly in the opening, e.g. 'I saw your {submarket} "
            f"property is currently on the market'. Do NOT describe this as a quiet, "
            f"discreet, or unlisted opportunity. "
        )
        ask_line = "The ask: 'Are you open to a conversation about a potential acquisition?' "
        user_ask = "ask about a potential acquisition while the property is on the market"
    else:
        listing_framing = ""
        ask_line = "The ask: 'Are you open to a conversation about a potential off-market sale?' "
        user_ask = "ask about an off-market conversation"

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}. "
        f"Write discreet acquisition outreach addressed to {addressee}. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Frame as: 'I represent a private buyer actively evaluating office assets in {submarket}.' "
        f"Use the full property address: 'your property at {address_display}'. "
        f"The second sentence should reference this only as neutral market context, never as pressure on the owner: {context_signal}. "
        f"Do NOT reveal buyer name, buyer capital, or any specific buyer details. "
        f"Reference the dominant signal subtly without revealing platform intelligence: {signal_hint}. "
        f"{listing_framing}"
        f"{ask_line}"
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
        f"   cite one CBRE Q1 2026 cap rate or vacancy data point; {user_ask}\n"
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
    call_target: Optional[str] = None,
) -> dict:
    """
    Call GPT-4o and return a structured outreach dict.

    target_type auto-resolved when not provided:
      tenant_match: 'broker' if landlord_representative set, else 'owner'
      acquisition:  'sales_broker' if sales_contact set, else 'owner'
      listing_rep:  always 'owner'

    call_target (Fix 4): 'OWNER' or 'TENANT' — selects which deterministic call
    script is attached for the matched-deal flow (tenant_match / for_sale_vacancy).
    Defaults to TENANT for tenant_side and OWNER for property_side.
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
    # "agent" title used in all paths. Fix 3: the social-proof line
    # ("I work exclusively… before they hit the open listings") is no longer
    # injected on any path — tenant-side included.
    is_tenant_side = (direction == "tenant_side")
    email_body = _inject_hardcoded_sentences(parsed["email_body"], inject_social_proof=False)

    # Sanitize bracket placeholders (all paths): "Dear [Owner's Name]," etc.
    email_body = _sanitize_bracket_placeholders(email_body, property_dict)

    # Fix 3: property-side tenant-describing copy with NO real SF must state the
    # requirement qualitatively. Guarantee the exact fallback sentence is present
    # (the LLM is also instructed to, but this makes it deterministic).
    if (not is_tenant_side
            and tenant_dict is not None
            and outreach_type in ("tenant_match", "for_sale_vacancy")
            and not tenant_dict.get("current_sf_occupied")):
        fallback = _tenant_sf_fallback_sentence(
            property_dict.get("submarket"), tenant_dict.get("lease_expiry_months")
        )
        if "right-sized office space" not in email_body:
            if "Thank you," in email_body:
                email_body = email_body.replace("Thank you,", f"{fallback}\n\nThank you,", 1)
            else:
                email_body = f"{email_body}\n\n{fallback}"

    # Remove duplicate lease-expiry phrase on tenant-side copy.
    if is_tenant_side and tenant_dict:
        email_body = _dedup_lease_clause(email_body, tenant_dict.get("lease_expiry_months"))

    # CBRE submarket-vacancy urgency line, injected as a single sentence right
    # after the property pitch (before the closing ask). Vacancy only — no rent PSF.
    def _insert_before_ask(body: str, sentence: str) -> str:
        if not sentence or sentence in body:
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

    submarket = property_dict.get("submarket")
    if is_tenant_side:
        # Fix 2: cite vacancy only when it is BELOW 10% (scarcity); else omit and
        # let the lease-timing hook carry the urgency.
        email_body = _insert_before_ask(email_body, _tenant_vacancy_sentence(submarket))
    elif outreach_type in ("tenant_match", "for_sale_vacancy"):
        # Fix 3: owner-side cites vacancy only when it is ABOVE 15% (tenant
        # scarcity); otherwise substitute a lease-timeline urgency line.
        owner_line = _owner_vacancy_sentence(submarket) or _owner_lease_timeline_sentence(tenant_dict)
        email_body = _insert_before_ask(email_body, owner_line)

    # Safety strip: property-side copy must never mention headcount,
    # and must never contain a redundant "I propose a short call" sentence.
    if not is_tenant_side:
        import re as _re
        email_body = _re.sub(r'I propose a short call[^.!?]*[.!?]\s*', '', email_body)
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

    # Fix 3: the confirmed-leasing disclosure ("Please note that the owner has
    # confirmed openness to leasing discussions…") is NO LONGER injected on
    # tenant-side emails — it made the note read like a platform output.

    # ── Fix 4: attach the deterministic OWNER / TENANT call script ───────────
    # For the matched-deal flow (tenant_match / for_sale_vacancy) the call script
    # is built in code so the ordered information-extraction questions and the
    # verbatim 48-hour close are guaranteed. Other outreach types (listing_rep,
    # acquisition) keep the LLM-generated script.
    resolved_call_target = (call_target or ("TENANT" if is_tenant_side else "OWNER")).upper()
    if outreach_type in ("tenant_match", "for_sale_vacancy"):
        cs = build_call_script(resolved_call_target, property_dict, tenant_dict)
        call_opening, call_core = cs["opening"], cs["core"]
        call_pain_probe, call_close = cs["pain_probe"], cs["close"]
    else:
        resolved_call_target = None
        call_opening, call_core = parsed["opening"], parsed["core"]
        call_pain_probe, call_close = parsed["pain_probe"], parsed["close"]

    return {
        "email_subject":          parsed["subject"],
        "email_body":             email_body,
        "call_script_opening":    call_opening,
        "call_script_core":       call_core,
        "call_script_pain_probe": call_pain_probe,
        "call_script_close":      call_close,
        "outreach_type":          outreach_type,
        "target_type":            target_type,
        "call_target":            resolved_call_target,
        "generated_at":           datetime.utcnow().isoformat(),
    }
