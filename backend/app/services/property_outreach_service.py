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
    if tenant_dict is not None:
        industry = tenant_dict.get("industry") or "professional services firm"
        sf       = tenant_dict.get("estimated_sf_needed")
        exp      = tenant_dict.get("lease_expiry_months")
        sf_str   = f"{sf:,} SF" if sf else None
        exp_str  = (f"approximately {exp} {'month' if exp == 1 else 'months'}" if exp is not None else None)
        # Fix 3 / Fix 5: only reference a real SF number; otherwise use the
        # qualitative "right-sized office space" framing (never a placeholder).
        sf_part  = f" seeking {sf_str}" if sf_str else " seeking right-sized office space"
        exp_part = f" with a lease expiring {exp_str}" if exp_str else ""
        tenant_hint = (
            f"\nPrimary matched tenant profile (do NOT reveal tenant name): "
            f"a {industry} firm{sf_part} in "
            f"{p.get('submarket', 'Northern Virginia')}{exp_part}"
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

    # Restructured body sequence (hook → why-now → one proof point → single ask).
    # Proof step is null-safe: when no benchmark exists, instruct the model to omit
    # the statistic sentence entirely rather than invent a number.
    proof_step = (
        f"(3) PROOF — cite exactly ONE CBRE Q1 2026 {p.get('submarket', 'submarket')} data point "
        f"(a single number — vacancy rate or avg asking rent); no filler like 'underscores the opportunity'. "
        if benchmark else
        "(3) PROOF — omit any market-statistic sentence; do NOT invent or cite a number you were not given. "
    )

    system = (
        f"You are {AGENT_NAME} at {FIRM_NAME}, a commercial real estate broker "
        f"specialising in Northern Virginia office. You are addressing {addressee}. "
        f"{framing} Write like a sharp, concise professional — every sentence earns its place. "
        f"Open the email with exactly '{salutation}' on its own line. "
        f"Use the full property address once in the body: 'your property at {address_display}'. "
        f"Structure the body in this EXACT sequence, one tight sentence each: "
        f"(1) HOOK — lead with the qualified prospect: a credible tenant is interested, described "
        f"ONLY by industry and approximate target SF (e.g. 'a qualified Health Care firm seeking "
        f"~3,500 SF'); NEVER reveal the tenant company name. "
        f"(2) WHY-NOW — one sentence pairing the tenant's lease-expiry timeline with this property's "
        f"{urgency_signal}, so the fit and the timing are both clear. "
        f"{proof_step}"
        f"(4) ASK — close with EXACTLY this sentence and no other call-to-action: "
        f"'I'd welcome a brief call at your convenience.' "
        f"Do NOT write 'I propose a short call' or any other phrasing before it. "
        f"Do NOT stack multiple asks (no separate rent / tour / OM requests — fold them "
        f"into the call's purpose). "
        f"Anchor any numerical claim to the CBRE Q1 2026 NoVA benchmark provided. "
        f"NEVER reveal the tenant company name — describe by industry, size, and timing only. "
        f"NEVER suggest specific days of the week. "
        f"{sale_clause}{secondary_clause}"
    )
    user = (
        f"Property details:\n{ctx}{tenant_hint}\n"
        f"\nMarket benchmark: {benchmark or 'N/A'}\n\n"
        "Write:\n"
        "1. Email subject line (one line)\n"
        "2. Email body — maximum 150 words (excluding signature block); follow the 4-step sequence "
        "   from the system prompt exactly (hook → why-now → one proof point → single call ask); "
        "   use the salutation from the system prompt exactly as written — do NOT use bracket placeholders; "
        "   describe the matched tenant ONLY by industry type and approximate SF using the profile above — "
        "   write the actual values, not placeholders, and never the company name; "
        "   ONE submarket data point only; ONE closing ask — a short call (do NOT stack rent / tour / OM)\n"
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
        sf  = tenant_dict.get("estimated_sf_needed")
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
    benchmark = _submarket_context(p.get("submarket"))
    asset_class = p.get("asset_class") or "office"
    sf_avail = p.get("sf_avail") or 0
    in_place_rent = p.get("in_place_rent_psf")
    asking_rent = in_place_rent if in_place_rent and in_place_rent > 0 else None

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
        f"With your lease expiring in approximately {lease_expiry_m} {'month' if lease_expiry_m == 1 else 'months'}"
        if lease_expiry_m is not None
        else "With your upcoming lease expiry"
    )

    rent_clause = (
        f" at approximately ${asking_rent:.2f}/SF" if asking_rent else ""
    )
    sf_clause = (
        f" with {sf_avail:,} square feet available" if sf_avail else ""
    )

    sf_display  = f"{sf_needed:,} SF" if sf_needed else None
    exp_display = (f"{lease_expiry_m} {'month' if lease_expiry_m == 1 else 'months'}" if lease_expiry_m is not None else None)

    # Space-fit note: reference SF match only — no headcount (Fix 4)
    space_fit_note = (
        f"Space-fit note: the property has {sf_avail:,} SF available — "
        f"reference this as well-matched for a company seeking {sf_display}."
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
        f"Asset Class: {asset_class}",
        f"Submarket: {submarket}",
        f"SF Available: {sf_avail or 'N/A'}",
        *([ f"Asking Rent: ${asking_rent:.2f}/SF" ] if asking_rent else []),
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
        + (f"   describe the property generally (asset class, submarket, SF available, asking rent ${asking_rent:.2f}/SF), " if asking_rent else
           "   describe the property generally (asset class, submarket, SF available — do NOT include a specific rent figure), ")
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
    recipient_raw   = _clean_owner_name(sales_contact or p.get("owner_name") or "")
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
        f"The second sentence must reference the urgency signal: {urgency_signal}. "
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
    email_body = _inject_hardcoded_sentences(parsed["email_body"], inject_social_proof=is_tenant_side)

    # Sanitize bracket placeholders (all paths): "Dear [Owner's Name]," etc.
    email_body = _sanitize_bracket_placeholders(email_body, property_dict)

    # Fix 3: property-side tenant-describing copy with NO real SF must state the
    # requirement qualitatively. Guarantee the exact fallback sentence is present
    # (the LLM is also instructed to, but this makes it deterministic).
    if (not is_tenant_side
            and tenant_dict is not None
            and outreach_type in ("tenant_match", "for_sale_vacancy")
            and not tenant_dict.get("estimated_sf_needed")):
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
