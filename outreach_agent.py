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

def project_sf(headcount, growth_pct):
    if growth_pct:
        projected = headcount * (1 + growth_pct / 100)
    else:
        projected = headcount * 1.15
    return math.ceil(projected * SF_PER_PERSON / 100) * 100

# ── OpenAI Generation ─────────────────────────────────────────────────────────

def generate_outreach(company: dict) -> dict:
    client = OpenAI()   # reads OPENAI_API_KEY from environment

    headcount    = company["current_headcount"]
    growth_pct   = company.get("headcount_growth_pct")
    projected_sf = project_sf(headcount, growth_pct)
    lease_mo     = company.get("lease_expiry_months", "unknown")
    submarket    = company["current_submarket"]
    industry     = company["industry"].split("(")[0].strip()
    company_name = company["name"]
    growth_str   = f"+{growth_pct:.0f}%" if growth_pct else "stable"

    prompt = f"""You are {AGENT_NAME} from {FIRM_NAME}, a commercial real estate broker
specializing in Northern Virginia office leasing.

Generate TWO pieces of outreach for this tenant lead. Use ONLY the data provided.
Be specific, confident, and direct. No fluff. Sound like a sharp broker, not a template.

COMPANY DATA:
- Name: {company_name}
- Industry: {industry}
- Headcount: {headcount} employees, growing at {growth_str}
- Projected SF needed (12-18 mo): {projected_sf:,} SF
- Lease expiry: {lease_mo} months
- Submarket: {submarket}
- Opportunity score: {company['opportunity_score']:.0f}/100

OUTPUT FORMAT — return valid JSON only, no markdown, no extra text:
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
}}

RULES:
- Call script opening asks for 3 minutes. Core message cites the projected SF number
  and lease window. Pain probe asks one open question about their real hiring/space constraint.
  Close offers a specific next step (30-min meeting, specific submarket buildings).
- Email: subject under 8 words, body under 150 words, same data points, ends with one CTA.
  Sign off as {AGENT_NAME} | {FIRM_NAME}.
- Do NOT invent contact names. Greeting should be "Hi there," if no name available.
- Return ONLY the JSON object. No markdown fences. No preamble.
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "You are a precise commercial real estate outreach assistant. Return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.7,
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
Projected SF needed: {project_sf(company['current_headcount'], company.get('headcount_growth_pct')):,} SF

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
