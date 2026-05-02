"""
CRE Outreach Agent — Jack Zamer, The Commercial Real Estate Group
-----------------------------------------------------------------
Batch outreach CLI. Pulls companies from the Deal Radar platform,
calls the platform's draft-outreach endpoint (GPT-4o logic lives
there), saves each package to Google Docs, and logs to the platform.

Usage:
  python outreach_agent.py                        # All IMMEDIATE + HIGH
  python outreach_agent.py --priority IMMEDIATE   # Only IMMEDIATE
  python outreach_agent.py --company CO-001       # Single company
  python outreach_agent.py --dry-run              # Preview only, no saving
"""

import argparse
import datetime
import os
import sys
import requests
import urllib.parse
import webbrowser
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Configuration ─────────────────────────────────────────────────────────────

AGENT_NAME     = "Jack Zamer"
FIRM_NAME      = "The Commercial Real Estate Group"
DEAL_RADAR_URL = "http://localhost:8000"   # change if your port differs

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]

TOKEN_FILE       = "google_token.json"
CREDENTIALS_FILE = "google_credentials.json"

# Paste your Google Sheet ID here after creating the tracker (see SETUP.md)
TRACKER_SHEET_ID = os.environ.get("TRACKER_SHEET_ID", "")

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

def _api(path: str) -> str:
    return f"{DEAL_RADAR_URL}/api{path}"


def fetch_companies(priority_filter: Optional[str] = None, company_id: Optional[str] = None):
    try:
        resp = requests.get(_api("/companies"), timeout=10)
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


def draft_outreach_via_api(company_id: str) -> dict:
    """
    Calls POST /api/companies/{company_id}/draft-outreach.
    Returns the OutreachDraft dict from the platform (GPT-4o lives there).
    """
    resp = requests.post(_api(f"/companies/{company_id}/draft-outreach"), timeout=60)
    resp.raise_for_status()
    return resp.json()


def log_outreach_via_api(company_id: str, draft: dict) -> Optional[int]:
    """
    Persists a draft to the platform's outreach_log table.
    Returns the log id, or None on failure.
    """
    script = draft["call_script"]
    payload = {
        "email_subject":          draft["email_subject"],
        "email_body":             draft["email_body"],
        "call_script_opening":    script["opening"],
        "call_script_core":       script["core_message"],
        "call_script_pain_probe": script["pain_probe"],
        "call_script_close":      script["the_close"],
        "projected_sf":           draft.get("projected_sf"),
        "score_at_generation":    draft["score"],
        "priority_at_generation": draft["priority"],
        "email_sent":             False,
        "call_made":              False,
    }
    resp = requests.post(_api(f"/companies/{company_id}/log-outreach"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("id")

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

# ── Google Docs ───────────────────────────────────────────────────────────────

def save_to_google_doc(docs_svc, drive_svc, company: dict, draft: dict) -> str:
    title  = f"Outreach — {company['name']} — {datetime.date.today()}"
    script = draft["call_script"]

    projected_sf_str = f"{draft['projected_sf']:,} SF" if draft.get("projected_sf") else "N/A"

    content = f"""OUTREACH PACKAGE — {company['name']}
Generated: {datetime.date.today()} | Agent: {AGENT_NAME} | {FIRM_NAME}
{'='*60}

COMPANY SNAPSHOT
Priority: {company['priority']} | Score: {company['opportunity_score']:.0f}/100
Headcount: {company['current_headcount']} | Growth: {company.get('headcount_growth_pct', 'N/A')}%
Submarket: {company['current_submarket']} | Lease Expiry: {company.get('lease_expiry_months', 'N/A')} months
Projected SF needed: {projected_sf_str}

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

Subject: {draft['email_subject']}

{draft['email_body']}

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

def open_outlook_draft(draft: dict):
    subject = urllib.parse.quote(draft["email_subject"])
    body    = urllib.parse.quote(draft["email_body"])
    webbrowser.open(f"mailto:?subject={subject}&body={body}")

# ── Main ──────────────────────────────────────────────────────────────────────

def run(args):
    print(f"\n{'='*60}")
    print(f"  CRE Outreach Agent | {AGENT_NAME} | {FIRM_NAME}")
    print(f"  Powered by Deal Radar Platform (GPT-4o)")
    print(f"{'='*60}\n")

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
            draft = draft_outreach_via_api(cid)
        except Exception as e:
            print(f"    [ERROR] Draft generation failed: {e}")
            continue

        if args.dry_run:
            print(f"\n  ── DRY RUN: {name} ──")
            print(f"  OPENING:  {draft['call_script']['opening'][:120]}...")
            print(f"  SUBJECT:  {draft['email_subject']}")
            print()
            processed += 1
            continue

        # Log to platform so it appears in outreach history
        try:
            log_id = log_outreach_via_api(cid, draft)
            print(f"    [PLATFORM] Logged outreach (id={log_id})")
        except Exception as e:
            print(f"    [WARN]    Platform log failed: {e}")

        # Google Doc
        try:
            doc_url = save_to_google_doc(docs_svc, drive_svc, company, draft)
            print(f"    [DOC]     {doc_url}")
        except Exception as e:
            print(f"    [ERROR]   Google Doc failed: {e}")
            doc_url = ""

        # Outlook draft
        open_outlook_draft(draft)
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
    parser = argparse.ArgumentParser(description="CRE Outreach Agent — Deal Radar Platform")
    parser.add_argument("--priority", choices=["IMMEDIATE", "HIGH", "WORKABLE"])
    parser.add_argument("--company", help="Single company ID, e.g. CO-001")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()
    run(args)
