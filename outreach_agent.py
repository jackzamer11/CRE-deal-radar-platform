"""
CRE Outreach Agent — Jack Zamer, The Commercial Real Estate Group
Powered by OpenAI GPT-4o
-----------------------------------------------------------------
Pulls companies from your Deal Radar, generates tenant call scripts
and cold emails, tracks outreach in Google Sheets, saves scripts
to Google Docs, and opens Outlook drafts.

Usage:
  python outreach_agent.py                        # All IMMEDIATE + HIGH
  python outreach_agent.py --priority IMMEDIATE   # Only IMMEDIATE
  python outreach_agent.py --company CO-001       # Single company
  python outreach_agent.py --dry-run              # Preview only, no saving
"""

import argparse
import json
import math
import os
import sys
import datetime
import requests
import webbrowser
import urllib.parse
from typing import Optional

from openai import OpenAI
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Configuration ─────────────────────────────────────────────────────────────

AGENT_NAME     = "Jack Zamer"
FIRM_NAME      = "The Commercial Real Estate Group"
DEAL_RADAR_URL = "http://localhost:8000"   # change if your port differs

OPENAI_MODEL   = "gpt-4o"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]

TOKEN_FILE       = "google_token.json"
CREDENTIALS_FILE = "google_credentials.json"

# Paste your Google Sheet ID here after creating the tracker (see SETUP.md)
TRACKER_SHEET_ID = os.environ.get("TRACKER_SHEET_ID", "")

SF_PER_PERSON = 175   # industry standard for NoVA office

# ── CBRE Q1 2026 Northern Virginia Office Benchmarks ──────────────────────────
# Source: CBRE Research, Q1 2026 (Northern Virginia Office Figures)
# Update quarterly when new CBRE report releases (sync with backend/app/config.py)

NOVA_AVG_RENT    = 37.49   # $/SF/yr NNN, market-wide NoVA average
NOVA_AVG_VACANCY = 21.8    # % vacancy, market-wide NoVA average
NOVA_AVG_FREE_RENT_MONTHS = 6    # estimate — update when CompStak active
NOVA_AVG_TI_PSF           = 60   # estimate — update when CompStak active

SUBMARKET_MARKET_RENT: dict[str, float] = {
    "Arlington (Clarendon)":     42.93,
    "Arlington (Rosslyn)":       46.85,
    "Arlington (Ballston)":      43.19,
    "Arlington (Columbia Pike)": 28.22,
    "Alexandria (Old Town)":     36.73,
    "Tysons":                    39.10,
    "Reston":                    37.84,
    "Falls Church":              27.87,
    "McLean":                    39.21,
    "Vienna":                    24.16,
    "Fairfax City":              26.23,
}

SUBMARKET_AVG_VACANCY: dict[str, float] = {
    "Arlington (Clarendon)":     26.5,
    "Arlington (Rosslyn)":       20.6,
    "Arlington (Ballston)":      21.1,
    "Arlington (Columbia Pike)": 32.1,
    "Alexandria (Old Town)":     17.6,
    "Tysons":                    27.3,
    "Reston":                    22.9,
    "Falls Church":              10.4,
    "McLean":                     7.4,
    "Vienna":                     5.2,
    "Fairfax City":               8.5,
}

MAJOR_BROKER_FIRMS: list[str] = [
    "JLL", "CBRE", "Cushman", "Cushman & Wakefield",
    "Newmark", "Savills", "Avison Young", "Colliers",
    "Lincoln Property", "Transwestern", "Eastdil",
]


def classify_rep(tenant_rep: Optional[str]) -> str:
    """Returns 'BLANK', 'MAJOR', or 'OTHER'."""
    if not tenant_rep or not tenant_rep.strip():
        return "BLANK"
    rep_lower = tenant_rep.lower()
    for firm in MAJOR_BROKER_FIRMS:
        if firm.lower() in rep_lower:
            return "MAJOR"
    return "OTHER"


# ── Google Auth ───────────────────────────────────────────────────────────────

def get_google_services():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, GOOGLE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"\n[ERROR] Missing {CREDENTIALS_FILE}")
                print("See SETUP.md — Step 3 for how to create this file.\n")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, GOOGLE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    sheets = build("sheets", "v4", credentials=creds)
    docs   = build("docs",   "v1", credentials=creds)
    drive  = build("drive",  "v3", credentials=creds)
    return sheets, docs, drive

# ── Deal Radar API ────────────────────────────────────────────────────────────

def fetch_companies(priority_filter: Optional[str] = None, company_id: Optional[str] = None):
    url = f"{DEAL_RADAR_URL}/api/companies/"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        companies = resp.json()
    except Exception as e:
        print(f"[ERROR] Cannot reach Deal Radar at {DEAL_RADAR_URL}: {e}")
        print("Make sure your platform is running (open-platform.bat).")
        sys.exit(1)

    if company_id:
        companies = [c for c in companies if c["company_id"] == company_id]
    elif priority_filter:
        companies = [c for c in companies if c["priority"] == priority_filter]
    else:
        companies = [c for c in companies if c["priority"] in ("IMMEDIATE", "HIGH")]

    # Skip companies with missing critical data
    companies = [c for c in companies if not c.get("insufficient_data") and c.get("current_headcount")]
    return companies

# ── Tracker ───────────────────────────────────────────────────────────────────

def get_contacted_ids(sheets_svc):
    if not TRACKER_SHEET_ID:
        return set()
    try:
        result = sheets_svc.spreadsheets().values().get(
            spreadsheetId=TRACKER_SHEET_ID,
            range="Tracker!A:A"
        ).execute()
        rows = result.get("values", [])
        return {r[0] for r in rows[1:] if r}
    except Exception:
        return set()

def log_to_tracker(sheets_svc, company, doc_url):
    if not TRACKER_SHEET_ID:
        return
    row = [
        company["company_id"],
        company["name"],
        company["priority"],
        company["current_submarket"],
        str(company.get("lease_expiry_months", "")),
        datetime.date.today().isoformat(),
        "contacted",
        doc_url,
    ]
    sheets_svc.spreadsheets().values().append(
        spreadsheetId=TRACKER_SHEET_ID,
        range="Tracker!A:H",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()

def init_tracker_sheet(sheets_svc):
    if not TRACKER_SHEET_ID:
        return
    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=TRACKER_SHEET_ID,
        range="Tracker!A1"
    ).execute()
    if not result.get("values"):
        headers = [["company_id", "name", "priority", "submarket",
                    "lease_expiry_months", "date_contacted", "status", "doc_url"]]
        sheets_svc.spreadsheets().values().update(
            spreadsheetId=TRACKER_SHEET_ID,
            range="Tracker!A1",
            valueInputOption="RAW",
            body={"values": headers},
        ).execute()

# ── SF Projection ─────────────────────────────────────────────────────────────

def project_sf(company: dict) -> Optional[int]:
    """
    Project SF needed at the 12-18 month horizon.

    lease_trajectory overrides:
      CONTRACTING → return current_sf (tenant right-sizing; no growth multiplier)
      FLAT        → return current_sf (steady-state footprint)
      GROWING     → force tiered SF/head growth logic regardless of SF/head ratio
      AUTO / null → tiered logic based on actual SF/employee

    Tiered logic (AUTO or GROWING):
      < 100 SF/head  → space-constrained; project up 15% from current footprint
      100-200 SF/head → extrapolate at actual SF/head ratio × projected headcount
      > 200 SF/head  → space-rich; project up only 5%

    Falls back to headcount × growth × 175 when current_sf is unknown.
    """
    current_sf   = company.get("current_sf")
    headcount    = company.get("current_headcount")
    growth_pct   = company.get("headcount_growth_pct")
    trajectory   = (company.get("lease_trajectory") or "AUTO").upper()
    growth_rate  = (growth_pct / 100.0) if growth_pct is not None else 0.15

    # Broker-set trajectory overrides
    if trajectory in ("CONTRACTING", "FLAT"):
        return current_sf  # no change; may be None if SF unknown

    if current_sf and headcount:
        sf_per_employee     = current_sf / headcount
        projected_headcount = headcount * (1 + growth_rate)

        if trajectory == "GROWING" or sf_per_employee < 100:
            return math.ceil(current_sf * (1 + growth_rate) / 100) * 100
        elif sf_per_employee <= 200:
            return math.ceil(projected_headcount * sf_per_employee / 100) * 100
        else:
            return math.ceil(current_sf * 1.05 / 100) * 100

    if headcount:
        return math.ceil(headcount * (1 + growth_rate) * SF_PER_PERSON / 100) * 100
    return None

# ── OpenAI Generation ─────────────────────────────────────────────────────────

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
    client = OpenAI()   # reads OPENAI_API_KEY from environment

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

    # ── Market benchmarks ─────────────────────────────────────────────────────
    market_rent  = SUBMARKET_MARKET_RENT.get(submarket)
    avg_vacancy  = SUBMARKET_AVG_VACANCY.get(submarket)

    # Deltas vs NoVA-wide average (per CBRE Q1 2026)
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
        if diff > 2:
            context = "above market — renegotiation pressure likely at renewal"
        elif diff < -2:
            context = "below market — favorable rate they'll want to preserve"
        else:
            context = "at-market"
        rent_line = f"${current_rent:.2f}/SF in-place vs ${market_rent:.2f}/SF market ({sign}${diff:.2f} — {context})"
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

    future_line  = f"Future move flagged: YES — {future_type}" if future_flag else ""
    greeting     = contact_name if contact_name else "there"

    # Contraction signal: explicit flag, broker override, or SF/head > 230
    contraction = bool(
        trajectory == "CONTRACTING"
        or company.get("contraction_signal")
        or (current_sf and headcount and headcount > 0 and (current_sf / headcount) > 230)
    )

    trajectory_note = ""
    if trajectory == "CONTRACTING":
        trajectory_note = (
            "Broker has confirmed this tenant is contracting. Acknowledge the right-sizing: "
            "'I noticed your footprint has evolved — I'm seeing a lot of quality smaller suites "
            "come to market in {submarket} right now that fit a leaner operating model.' "
            "Do NOT project expansion."
        ).format(submarket=submarket)
    elif trajectory == "FLAT":
        trajectory_note = "Tenant is in steady-state mode. Focus on lease timing and market rate opportunity, not expansion."

    # ── Rep-specific framing ───────────────────────────────────────────────────
    if rep_class == "MAJOR":
        rep_instruction = (
            f"Tenant is already represented by {tenant_rep} (major brokerage). "
            "Do NOT pitch direct representation. "
            f"Pivot to market resource framing: position yourself as a {submarket}-specialist complement. "
            "REQUIRED: Reference at least one quantitative comparison vs. the NoVA average. "
            f"Example: '{submarket} vacancy is sitting at {avg_vacancy:.1f}% — "
            f"well above the {NOVA_AVG_VACANCY:.1f}% NoVA average (per CBRE Q1 2026) — "
            "which means landlords have lost negotiating leverage. "
            f"Recent NoVA renewals are seeing {NOVA_AVG_FREE_RENT_MONTHS}+ months free rent "
            f"and ${NOVA_AVG_TI_PSF}+/SF TI on average.' "
            "Never position yourself against a major firm."
        )
    elif rep_class == "OTHER":
        rep_instruction = (
            f"Tenant has a regional rep on record ({tenant_rep}). Lead with your specific "
            f"{submarket} deal flow knowledge; let value open the door rather than challenging "
            "the existing relationship directly."
        )
    else:
        rep_instruction = (
            "Tenant has NO broker rep on record. Pitch direct tenant representation explicitly and confidently. "
            "REQUIRED: Reference one specific market dislocation. "
            f"Example: If in-place rent is known, calculate annual savings vs. market rate. "
            f"If unknown, reference the {submarket} market rate of ${market_rent:.2f}/SF vs the "
            f"${NOVA_AVG_RENT:.2f}/SF NoVA average (per CBRE Q1 2026)."
        ) if market_rent else (
            "Tenant has NO broker rep on record. Pitch direct tenant representation explicitly and confidently."
        )

    contraction_note = (
        "Tenant shows right-sizing signals. "
        "Acknowledge this gracefully: 'I noticed your footprint has evolved — happy to walk "
        "through current availability that fits your actual operating model.' "
        "Do not project expansion if the data shows contraction."
    ) if contraction else ""

    # ── System prompt ─────────────────────────────────────────────────────────
    rules = [
        "Cite '(per CBRE Q1 2026)' on the FIRST market statistic in each message (email and call script) — establishes data credibility.",
        f"Email body: MINIMUM 6 sentences. Subject line under 9 words.",
        "Call script: four sections (OPENING, CORE MESSAGE, PAIN PROBE, CLOSE), each MINIMUM 3 sentences with specific data.",
        f'Greeting: use "{greeting}" — format "Hi {greeting},"',
        "FORBIDDEN phrases — never write these: 'happy to discuss', 'let me know if interested', 'feel free to reach out'. Replace with a specific CTA like 'Are you free Tuesday or Wednesday for a 15-minute call?'",
        "Always include in EVERY message: (a) contact's name in greeting, (b) submarket vacancy + rent delta vs NoVA avg, (c) one industry-specific pain point.",
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

    # ── User prompt ───────────────────────────────────────────────────────────
    nova_context = f"""NoVA MARKET BENCHMARKS (CBRE Q1 2026):
  NoVA avg rent:        ${NOVA_AVG_RENT:.2f}/SF/yr NNN
  NoVA avg vacancy:     {NOVA_AVG_VACANCY:.1f}%
  Avg free rent:        {NOVA_AVG_FREE_RENT_MONTHS} months (estimate)
  Avg TI allowance:     ${NOVA_AVG_TI_PSF}/SF (estimate)
  Avg lease term:       7 years"""

    submarket_context = f"""SUBMARKET BENCHMARKS — {submarket} (CBRE Q1 2026):
  Market rent:          ${market_rent:.2f}/SF/yr NNN  ({rent_vs_nova_str})
  Submarket vacancy:    {vacancy_str}""" if market_rent else f"SUBMARKET: {submarket} (no benchmark data)"

    user_prompt = f"""Generate personalized outreach for this NoVA office tenant:

COMPANY: {company_name}
INDUSTRY: {industry}
CONTACT: {contact_name or 'Unknown'}{(' — ' + contact_title) if contact_title else ''}
SUBMARKET: {submarket}
LEASE TRAJECTORY: {trajectory}

{nova_context}

{submarket_context}

TENANT DATA:
  Headcount:      {headcount or 'unknown'} employees
  Growth rate:    {growth_str} YoY
  SF footprint:   {sf_line}
  Rent situation: {rent_line}
  Lease expiry:   {lease_str}
  Broker rep:     {tenant_rep or 'NONE ON RECORD'} [{rep_class}]
  {future_line}

SIGNAL SCORE: {company.get('opportunity_score', 0):.0f}/100 ({company.get('priority', '')})

Sign off as {AGENT_NAME} | {FIRM_NAME}."""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()
    return json.loads(raw)

# ── Google Docs ───────────────────────────────────────────────────────────────

def save_to_google_doc(docs_svc, drive_svc, company: dict, outreach: dict) -> str:
    title  = f"Outreach — {company['name']} — {datetime.date.today()}"
    script = outreach["call_script"]
    email  = outreach["email"]

    content = f"""OUTREACH PACKAGE — {company['name']}
Generated: {datetime.date.today()} | Agent: {AGENT_NAME} | {FIRM_NAME}
{'='*60}

COMPANY SNAPSHOT
Priority: {company['priority']} | Score: {company['opportunity_score']:.0f}/100
Headcount: {company['current_headcount']} | Growth: {company.get('headcount_growth_pct', 'N/A')}%
Submarket: {company['current_submarket']} | Lease Expiry: {company.get('lease_expiry_months', 'N/A')} months
Projected SF needed: {f"{project_sf(company):,} SF" if project_sf(company) else "N/A"}

{'='*60}
TENANT CALL SCRIPT
{'='*60}

OPENING:
{script['opening']}

CORE MESSAGE:
{script['core_message']}

PAIN PROBE:
{script['pain_probe']}

THE CLOSE:
{script['the_close']}

{'='*60}
COLD EMAIL
{'='*60}

Subject: {email['subject']}

{email['body']}

{'='*60}
TRACKING
{'='*60}
Status: Outreach sent {datetime.date.today()}
Notes: [add call outcome here]
"""

    doc = docs_svc.documents().create(body={"title": title}).execute()
    doc_id  = doc["documentId"]
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

    docs_svc.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
    ).execute()

    return doc_url

# ── Outlook Draft ─────────────────────────────────────────────────────────────

def open_outlook_draft(company: dict, outreach: dict):
    email   = outreach["email"]
    subject = urllib.parse.quote(email["subject"])
    body    = urllib.parse.quote(email["body"])
    mailto  = f"mailto:?subject={subject}&body={body}"
    webbrowser.open(mailto)

# ── Main ──────────────────────────────────────────────────────────────────────

def run(args):
    print(f"\n{'='*60}")
    print(f"  CRE Outreach Agent | {AGENT_NAME} | {FIRM_NAME}")
    print(f"  Powered by OpenAI GPT-4o")
    print(f"{'='*60}\n")

    if not os.environ.get("OPENAI_API_KEY"):
        print("[ERROR] OPENAI_API_KEY not set.")
        print("Get your key at: https://platform.openai.com/api-keys")
        print("Then run: set OPENAI_API_KEY=sk-your-key-here\n")
        sys.exit(1)

    companies = fetch_companies(
        priority_filter=args.priority,
        company_id=args.company,
    )
    print(f"Found {len(companies)} companies to process.\n")

    if not companies:
        print("No companies matched. Check your priority filter or company ID.")
        return

    if not args.dry_run:
        print("Connecting to Google... (browser may open first time)\n")
        sheets_svc, docs_svc, drive_svc = get_google_services()
        init_tracker_sheet(sheets_svc)
        contacted = get_contacted_ids(sheets_svc)
    else:
        contacted = set()
        sheets_svc = docs_svc = drive_svc = None

    skipped = 0
    processed = 0

    for company in companies:
        cid  = company["company_id"]
        name = company["name"]

        if cid in contacted:
            print(f"  [SKIP] {name} ({cid}) — already contacted")
            skipped += 1
            continue

        print(f"  [GENERATING] {name} ({cid}) | {company['priority']} | {company['current_submarket']}")

        try:
            outreach = generate_outreach(company)
        except Exception as e:
            print(f"    [ERROR] Generation failed: {e}")
            continue

        if args.dry_run:
            print(f"\n  ── DRY RUN: {name} ──")
            s = outreach["call_script"]
            print(f"  OPENING:  {s['opening'][:120]}...")
            print(f"  SUBJECT:  {outreach['email']['subject']}")
            print()
            processed += 1
            continue

        # Google Doc
        try:
            doc_url = save_to_google_doc(docs_svc, drive_svc, company, outreach)
            print(f"    [DOC]     {doc_url}")
        except Exception as e:
            print(f"    [ERROR]   Google Doc failed: {e}")
            doc_url = ""

        # Outlook draft
        open_outlook_draft(company, outreach)
        print(f"    [EMAIL]   Outlook draft opened")

        # Tracker
        try:
            log_to_tracker(sheets_svc, company, doc_url)
            print(f"    [TRACKER] Logged")
        except Exception as e:
            print(f"    [WARN]    Tracker log failed: {e}")

        processed += 1
        print()

    print(f"\n{'='*60}")
    print(f"  Done. Processed: {processed} | Skipped: {skipped}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CRE Outreach Agent — OpenAI")
    parser.add_argument("--priority", choices=["IMMEDIATE", "HIGH", "WORKABLE"])
    parser.add_argument("--company", help="Single company ID, e.g. CO-001")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()
    run(args)
