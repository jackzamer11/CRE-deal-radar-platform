# CRE Outreach Agent — Setup Guide
Jack Zamer | The Commercial Real Estate Group | Powered by OpenAI GPT-4o

Total setup time: ~25 minutes. Do it once, then it's one command forever.

---

## STEP 1 — Install Python dependencies

Open a terminal in C:\Users\Jackz\CRE-deal-radar-platform\ and run:

```
venv\Scripts\activate
pip install openai google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

---

## STEP 2 — Get your OpenAI API key ($5 minimum)

1. Go to https://platform.openai.com/api-keys
2. Sign in or create an account
3. Click "+ Create new secret key" — name it "deal-radar-agent"
4. Copy the key (starts with sk-...)
5. Go to https://platform.openai.com/settings/billing
6. Add $5 in credits — that's 1,600+ outreach packages at GPT-4o pricing

Set the key in your terminal:
```
set OPENAI_API_KEY=sk-your-key-here
```

To make it permanent (recommended):
- Windows Start → search "Environment Variables"
- "Edit the system environment variables" → "Environment Variables"
- Under User variables → New
- Name: OPENAI_API_KEY  |  Value: your key
- Click OK — restart terminal

---

## STEP 3 — Set up Google OAuth (one-time, ~15 minutes)

### 3a. Create a Google Cloud project
1. Go to https://console.cloud.google.com
2. Project dropdown at top → "New Project"
3. Name: "CRE Deal Radar Agent" → Create

### 3b. Enable APIs
Go to "APIs & Services" → "Enable APIs and Services" → search and enable:
- Google Docs API
- Google Sheets API
- Google Drive API

### 3c. Create OAuth credentials
1. "APIs & Services" → "Credentials" → "+ Create Credentials" → "OAuth client ID"
2. If prompted for consent screen:
   - Choose "External"
   - App name: CRE Deal Radar Agent
   - Add your Gmail as a test user → Save
3. Application type: Desktop app | Name: Deal Radar Agent → Create
4. Click "Download JSON"
5. Rename file to: google_credentials.json
6. Move to: C:\Users\Jackz\CRE-deal-radar-platform\outreach_agent\

First run will open a browser → click Allow → done forever.

---

## STEP 4 — Create your Google Sheets tracker

1. Go to https://sheets.google.com → New spreadsheet
2. Name it: CRE Outreach Tracker
3. Rename the default sheet tab to exactly: Tracker
4. Copy the URL:
   https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID_HERE/edit
5. The long string between /d/ and /edit is your Sheet ID

6. Open outreach_agent.py, find this line:
   TRACKER_SHEET_ID = os.environ.get("TRACKER_SHEET_ID", "")
   
   Replace with your ID:
   TRACKER_SHEET_ID = os.environ.get("TRACKER_SHEET_ID", "YOUR_SHEET_ID_HERE")

---

## STEP 5 — Place files and run

Put outreach_agent.py in:
  C:\Users\Jackz\CRE-deal-radar-platform\outreach_agent\

Make sure your Deal Radar is running (open-platform.bat), then:

```bash
cd C:\Users\Jackz\CRE-deal-radar-platform
venv\Scripts\activate

# Preview first — no saving, no sending
python outreach_agent\outreach_agent.py --dry-run

# Run IMMEDIATE priority only (your 3 hottest leads)
python outreach_agent\outreach_agent.py --priority IMMEDIATE

# Run all IMMEDIATE + HIGH
python outreach_agent\outreach_agent.py

# Single company test
python outreach_agent\outreach_agent.py --company CO-001
```

---

## What it does per company

1. Pulls data from your Deal Radar API
2. Calculates projected SF (headcount × growth × 175 SF/person)
3. Sends to GPT-4o → call script + email generated
4. Creates a Google Doc with full outreach package
5. Opens Outlook draft with email pre-filled
6. Logs company + date + doc link to your Google Sheet tracker
7. Skips anyone already in the tracker automatically

---

## Tracker columns

| company_id | name | priority | submarket | lease_expiry_months | date_contacted | status | doc_url |

Update "status" manually after each call: contacted → called → meeting_set → deal

---

## Cost estimate (OpenAI GPT-4o)

~$0.003 per company outreach package
102 companies = ~$0.30 total
$5 credit = ~1,600 packages
