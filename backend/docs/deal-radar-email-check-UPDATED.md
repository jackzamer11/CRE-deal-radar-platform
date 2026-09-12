---
name: deal-radar-email-check
description: Daily 5pm check of jzamer@z-reg.com mailbox — logs genuine person/company email correspondence directly into Deal Radar OS's Activity Log (localhost:5173) via its API, skipping automated/platform noise and anything already in DR. Auto-starts the Deal Radar OS dev server via its Desktop launcher if it isn't running.
---

<!--
COPY THIS FILE OVER:
  C:\Users\Jackz\OneDrive\Documents\Claude\Scheduled\deal-radar-email-check\SKILL.md

What changed from the version currently in OneDrive:

1. ONE frontmatter block, not two. The old file had two stacked `---` blocks;
   most loaders take the first and silently drop the rest — which meant the
   dev-server auto-start line in the second block was never being read. The two
   descriptions are merged here.

2. Dedup reads GET /api/activity/message-ids instead of scanning
   /api/activity/?limit=1000. The old scan rebuilt the marker set from the
   `notes` text of the 1,000 most recent entries, so past 1,000 entries the
   oldest markers fell off the end and old emails relogged.

3. Entries are created via POST /api/activity/from-email, which resolves or
   creates the contact and company and stamps the entry in one transaction.

4. The dedup marker is no longer written into `notes`. It goes in the
   source_message_id column: indexed, unique, and not user-editable. The old
   marker lived in the same field the "Add Note" button edits, so editing a
   note destroyed the marker and the email relogged on the next run.

5. Lookback widened from 2 days to 7. Skipped 5pm runs on Sept 5, 6, 8 and 9
   meant a 2-day window silently lost mail.
-->

This is an automated run of a scheduled task. The user (Jack Zamer, jzamer@z-reg.com) is not present — execute autonomously, make reasonable calls, and note them in your final output. Only take "write" actions matching what this file specifies. This task's job is to write new email activity DIRECTLY into Jack's local "Deal Radar OS" CRM app via its REST API — it does NOT write to any file. There is no separate markdown log anymore; do not create or touch one.

MAILBOX: jzamer@z-reg.com, via the connected Microsoft/Outlook connector (tools named mcp__*__outlook_email_search, mcp__*__read_resource — search available tools for "outlook_email_search" if the exact prefix is unknown). This is Jack's own mailbox — omit mailboxOwnerEmail.

DEAL RADAR OS: a local web app running at http://localhost:5173 (Vite dev server). It has a REST API reachable at the SAME origin under /api. This app only exists in the user's actual browser (it's localhost, not reachable from any sandboxed fetch/bash/web_fetch tool) — you MUST use the Claude-in-Chrome browser tools to reach it:
1. Load the Chrome tools if deferred: ToolSearch "select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__computer"
2. Call tabs_context_mcp{createIfEmpty:true}, then navigate a tab to http://localhost:5173/activity (this puts the tab on the right origin so same-origin fetch works).
3. Use javascript_tool (action: "javascript_exec") on that tab to run fetch() calls against /api/... endpoints — e.g. `const r = await fetch('/api/activity/message-ids'); await r.json()`. Do NOT try to fetch /src/*.ts files (that gets blocked) — only /api/* calls are needed.
4. If http://localhost:5173 does not load, retry the navigation ONCE after a brief pause (it has been observed to be slow to respond on first load, not actually down).
5. If it STILL fails after that retry, the Vite dev server is genuinely not running. Start it yourself rather than giving up, using this exact procedure and nothing else:
   a. Load computer-use tools if deferred: ToolSearch "computer-use".
   b. Call request_access with apps: ["File Explorer"], reason: "start the Deal Radar OS dev server via its desktop launcher".
   c. Call open_application("File Explorer"), then click the "Desktop" entry in the left sidebar to navigate there.
   d. Take a screenshot and visually locate the file named exactly "open-platform.bat" on the Desktop. Double-click it. This is a Windows Batch File that starts the Deal Radar OS dev server in a new Command Prompt window.
   e. HARD CONSTRAINTS on this step: only ever click "open-platform.bat" — never click, open, move, rename, or delete any other file, folder, or icon on the Desktop or anywhere else. Never right-click, never type into File Explorer or into the Command Prompt window the .bat file opens, never close that Command Prompt window (closing it would kill the dev server). If "open-platform.bat" is not visible on the Desktop when you look, STOP this step entirely and fall through to the failure-reporting instruction below — do not go looking for it elsewhere or substitute a different file.
   f. Wait about 15-20 seconds for the dev server to boot, then navigate the Chrome tab back to http://localhost:5173/activity and retry the API fetch from Step 1.
   g. If it still fails after this, stop — do not attempt any further troubleshooting or additional retries, and report Deal Radar OS as unreachable in your final summary (note that you attempted the auto-start).

THE ENDPOINT THIS TASK USES (backend/app/api/routes/activity.py — ActivityFromEmail):

POST /api/activity/from-email
{
  from_email:        string,   // the sender's address, e.g. "miriam@mm-realestate.com"
  from_name:         string,   // display name as it appears on the message
  to_email:          string,   // the recipient's address
  direction:         "inbound" | "outbound",
  subject:           string,
  action_taken:      string,   // your one-to-three sentence summary (see Step 5)
  outcome:           string,   // optional
  follow_up_action:  string,   // optional
  source_message_id: string,   // the Outlook internetMessageId — REQUIRED for dedup
  sent_at:           "YYYY-MM-DD"
}

What it does for you, in one transaction: resolves or creates the contact by
email address, resolves or creates the company by email domain, stamps the
entry to that company, and creates the entry. You no longer have to put the
contact's name and company inline in the prose for them to be captured as
records — though keep writing good prose, because that is what Jack reads.

Rules it applies, so you do not have to:
- Contacts are matched on email address only — exact, case-insensitive. A name
  string is never used to match.
- An unseen sender becomes a new contact, auto-created and untriaged. It flips
  to triaged automatically the first time Jack engages with it.
- Free-mail senders (gmail, outlook, yahoo, hotmail, icloud, aol, proton and
  similar) get a contact with NO company. It will never create a company
  called "Gmail".
- Before creating a company from a domain, it checks the derived name against
  existing companies, so "Mm-Realestate" does not become a duplicate of a
  hand-entered "Corcoran McEnearney".
- direction: "inbound" sets responded=true on the contact, and moves the stage
  Sent → Replied. It never regresses a contact already at Interested or In Play.

Do NOT send contact_name, property_address, or company_name — those are not
create-time inputs. The contact comes from from_email/from_name; the company is
resolved from the email domain.

STEP 1 — Build the dedup set:
Fetch GET /api/activity/message-ids via the browser as described above. It
returns a flat array of the source_message_id values already logged, read
straight from an indexed column — no full-row scan and no limit ceiling, so it
cannot lose old markers the way the previous /api/activity/?limit=1000 scan
did. Optionally narrow it with ?since=YYYY-MM-DD.

Also build a same-day overlap index for activity Jack logged manually (without
a marker): fetch GET /api/activity/?since=<7 days ago> and, for every entry,
note its `log_date` plus any email address or full contact name in its
`action_taken`/`contact_name`/`outcome` text.

STEP 2 — Pull recent mail:
Call outlook_email_search with folderName "Sent Items", afterDateTime "7 days ago", order "newest" (paginate via nextOffset if needed). Then the same for folderName "Inbox". For every email, note id, internetMessageId, conversationId, subject, from/to, receivedDateTime/sentDateTime, hasAttachments.

Seven days, not two: a skipped run must not lose mail. Re-running over mail
already logged is free — the source_message_id dedup catches it.

STEP 3 — Filter out automated/platform noise (same standing rule as always — Jack does not want this in Deal Radar OS at all):
Exclude any email that is not genuine person-to-person or person-to-company correspondence:
- no-reply/donotreply/notifications/alerts-style sender addresses
- known automated/platform/vendor senders (GoDaddy account/security mail, Microsoft account/security/Teams notifications, Zillow listing alerts, ShowingTime feedback-request bots, CoStar lead-notification mail, mailer-daemon bounces, out-of-office auto-replies, "companies you follow"/social/marketing/newsletter/receipt/subscription-confirmation mail)
- any one-way broadcast/notification rather than an actual back-and-forth with a real human
When unsure whether a sender is a real person vs. automated/generic, lean toward excluding.

This is the ONLY relevance gate. If an email passes it, its sender is clean
enough to become a contact — do not apply a second judgement about whether the
person "matters enough" to be a record.

STEP 4 — Dedup:
Discard any email whose internetMessageId is already in the marker set from Step 1. Also discard (skip, do not log) any email where an existing DR OS entry already has the same log_date AND mentions the counterpart's email address or full name in its action_taken/contact_name/outcome — that means Jack (or a prior process) already logged this activity manually; do not create a second entry for it, and do not modify the existing one. Everything remaining is genuinely new.

STEP 5 — Get full content and summarize:
For each new email, use read_resource on its URI for the full body. Write a factual, third-person, past-tense one-to-three sentence action_taken, matching the style already used in Deal Radar OS, e.g.: "Emailed Miriam Miller (miriam@mm-realestate.com, Corcoran McEnearney) re: 1205 N Pitt St Unit #2C, Alexandria - she reported two offers on the unit and asked for my read; advised her to just take them." or "sent Jim Woolwine (accounting@athomeinalexandria.org) a market search for properties between 300 and 700 sqft in Alexandria." Include contact name + email, property/company/deal reference, and any dollar figures, dates, or deadlines mentioned — state numbers explicitly, no hedging language. If the email is an inbound reply, phrase action_taken as what THEY told/asked Jack.

STEP 6 — Create the entry:
POST to /api/activity/from-email (same javascript_exec/fetch approach as Step 1) with the fields listed above.

source_message_id is mandatory on every entry — it is the only mechanism future
runs have for dedup. It is stored in its own indexed, unique column that no UI
control can edit, so unlike the old `notes` marker it cannot be destroyed by
Jack editing a note.

Response handling:
- 200 → created. Log the returned id in your run summary.
- 409 → an entry for that message id already exists. This is not an error;
  it means the message was already logged. Skip it and move on.
- 500 → stop creating further entries and report the failure in your summary
  rather than retrying or guessing at a workaround.

Set direction correctly — it is what drives the contact's stage and responded
flag. "inbound" for mail Jack received, "outbound" for mail he sent.

STEP 7 — No other output:
Do not create, edit, or write to any file (including the old DealRadar/activity-log.md — that file is retired, leave it untouched). Do not send any email or message. Do not touch any file or app on the user's computer other than the single "open-platform.bat" double-click described above when strictly needed. Never delete, move, or rename anything. Your only side effects should be new POST /api/activity/from-email entries in Deal Radar OS, and (only when needed) launching the dev server via its existing Desktop shortcut. End your run with a brief internal summary of how many entries were created, how many were skipped as duplicates, and whether the auto-start step was used (for the completion notification) — nothing else.

If you cannot reach Outlook (auth error), stop and note the specific failure reason — do not guess or fabricate entries.
