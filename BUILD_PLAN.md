# CRE Deal Radar — Private Intelligence Layer: Build Plan

You are Claude Code working inside the existing repo `CRE-deal-radar-platform` (FastAPI + SQLAlchemy backend, React + TypeScript + Vite frontend, Windows/PowerShell environment). Your job is to implement the plan below using subagents where parallel work is safe, and sequentially where phases depend on each other.

The owner of this repo (Jack) is a beginner who reviews everything you do. Work in a way he can follow.

## Operating rules (apply to every agent and every phase)

1. **Explore before you build.** Before writing any code, map the repo: where models, database setup, routes, and frontend pages live. Match existing conventions (import style, naming, how routes are registered, how the frontend calls the API). Do not introduce new frameworks, ORMs, or state libraries.
2. **New branch, incremental commits.** Create branch `private-intelligence-layer`. Commit at the end of every phase with a clear message. Never commit directly to main.
3. **Do not break existing features.** The current app must keep working after every phase. If a change risks existing behavior, stop and explain the risk in plain language before proceeding.
4. **Nothing destructive without asking.** No deleting tables, no dropping columns, no rewriting existing models. Additive changes only.
5. **Every phase ends with a verification step Jack can run himself** — a command or a click-path in the UI. Print these instructions at the end of each phase in plain, non-jargon language.
6. **Secrets:** the Anthropic API key comes from an environment variable (`ANTHROPIC_API_KEY`) loaded via the backend's existing config pattern (or a `.env` file added to `.gitignore` if none exists). Never hardcode it. If the key is missing, the extraction endpoint must fail with a clear error message, not a crash.
7. **All extracted/AI-produced database columns are nullable.** Missing data is a normal state, never an error.
8. **No autonomous actions.** The system surfaces and ranks; it never sends emails, contacts anyone, or changes records without a human clicking something.
9. **Out of scope — do not build:** embeddings or vector search, fine-tuning, multi-tenant/auth systems beyond what exists, market-data scraping, autonomous agents, any new microservices. If you believe one of these is necessary, stop and make the case in plain language instead of building it.

## Architecture in one paragraph (context for every agent)

We are adding a **private intelligence layer** on top of Deal Radar. Raw facts extracted from documents land in an `observations` table (entity/field/value/confidence/source — append-only, nothing overwritten). A human review queue lets Jack verify low-confidence facts. Verified facts feed a **signal engine** (date-based rules: lease expirations, notice deadlines). Signals produce a **weekly ranked list of opportunities**, each with reasoning and source links. Every opportunity captures **accept/reject + a reason** — this feedback loop is the core asset of the whole system and must exist from v1.

---

## Phase A — Observations layer (foundation; sequential, do this first)

**Build:**
- `Observation` SQLAlchemy model: `id`, `entity_type` (str), `entity_id` (int), `field` (str), `value` (str, nullable), `confidence` (float, nullable), `source_doc` (str, nullable), `source_page` (int, nullable), `source_snippet` (text, nullable), `human_verified` (bool, default false), `superseded_by_id` (int, nullable, self-referencing), `created_at` (datetime).
- Table creation wired into however this repo currently creates tables (inspect first; use Alembic only if the repo already uses it).
- CRUD API endpoints: create observation, list observations filterable by `entity_type`, `entity_id`, `human_verified`, sorted by confidence ascending; and a verify endpoint that sets `human_verified=true` and optionally corrects `value` (a correction creates a NEW observation with `human_verified=true` and marks the old one superseded — never edit in place).
- Seed script that inserts 3 sample observations: one high confidence (0.95), one low (0.4), one with `value=None`.

**Acceptance criteria:**
- Backend starts cleanly; existing endpoints unaffected.
- `GET` on the list endpoint returns the 3 seeded rows sorted by confidence, nulls handled without error.
- Pytest tests: creating an observation with null value succeeds; correction creates a new row and supersedes the old; list filter by `human_verified` works.

**Jack verification:** one PowerShell command to run the seed script, one URL to open in the browser (FastAPI docs page) to call the list endpoint and see the 3 rows.

---

## Phase B — Lease extraction pipeline (sequential, after A)

**Build:**
- A `documents` table: `id`, `filename`, `storage_path`, `uploaded_at`, `entity_type` (nullable), `entity_id` (nullable), `extraction_status` (pending/done/failed).
- Upload endpoint: accepts a PDF, stores the file under a `data/uploads/` folder (gitignored), creates the document row.
- Extraction service: sends the PDF text (extract with `pypdf` or `pdfplumber` — check what's already installed first) to the Anthropic API using **structured JSON output** with EXACTLY these five fields for v1: `tenant_name`, `premises_sqft`, `commencement_date`, `expiration_date`, `base_rent_annual`. Every field must be present in the JSON but explicitly allowed to be `null`. The prompt must instruct: *if the document does not state a value, return null; never infer, estimate, or guess.* Each field returns `{value, confidence, page, snippet}`.
- Each extracted field is written as one row in `observations` with `source_doc` = filename.
- An extraction endpoint: `POST /documents/{id}/extract` runs the pipeline and returns the created observations.

**Acceptance criteria:**
- Uploading a text-based PDF and calling extract produces 5 observation rows, each with source snippet and page.
- A field genuinely absent from the document comes back as `value=None` — write a test with a synthetic minimal "lease" text missing base rent, and assert base rent is null (fabrication check).
- API key missing → clear 500-level error message, not a stack-trace crash.
- Costs and failures logged; a failed extraction sets `extraction_status=failed` and does not corrupt anything.

**Jack verification:** upload one real lease PDF via the docs page, run extract, then view the observations list and see 5 new rows with page numbers and snippets he can check against the actual PDF.

---

## Phase C — Review queue UI (can run as a parallel agent once Phase A endpoints are stable)

**Build (frontend, React/TS, matching existing component and styling conventions — inspect existing pages first):**
- A "Review" page listing unverified observations sorted by confidence ascending. Each row shows: field name, extracted value (or "NOT FOUND" for null), confidence as a simple visual, source doc + page, and the snippet.
- Two actions per row: **Confirm** (marks verified) and **Correct** (inline input; submits corrected value → backend creates the superseding verified observation).
- A simple count at top: "X facts awaiting review."
- Route/navigation added wherever the app's existing navigation lives.

**Acceptance criteria:**
- Page loads real data from the API, actions round-trip correctly, confirmed items disappear from the queue.
- No new styling framework; reuse whatever the app already uses (check for Tailwind config).

**Jack verification:** open the app, click Review, confirm one fact, correct another, refresh, and see both gone from the queue.

---

## Phase D — Signal engine + weekly opportunities (sequential, after B and C)

**Build:**
- A `signals` table (`id`, `entity_type`, `entity_id`, `signal_type`, `value`, `detected_at`, `evidence_observation_id`) and an `opportunities` table (`id`, `title`, `entity_type`, `entity_id`, `score`, `rationale`, `signals_json`, `surfaced_at`, `status` default "open").
- v1 signal rules — **date math only, no ML, no LLM scoring**:
  1. `lease_expiring`: verified `expiration_date` within the next 365 days.
  2. `expiration_unverified`: extracted but unverified expiration within 365 days (surfaced as "verify this first").
  3. `stale_data`: a lease document with extraction done but any of the 5 core fields still null and unverified.
- Scoring: transparent weighted rules (closer expiration = higher score; verified > unverified). The weights live in one obvious, commented Python dict so Jack can read and change them.
- Rationale: a plain-English sentence assembled from the signal data itself (template-based, not LLM, for v1) — e.g., "Lease for {tenant} expires in {n} days ({date}, source: {doc} p.{page}). No renewal activity recorded."
- An endpoint `POST /opportunities/generate` that runs the rules and creates opportunities (idempotent — re-running doesn't duplicate open items), and `GET /opportunities` sorted by score.
- Frontend: an "Opportunities" page listing open opportunities with score, rationale, and source links back to the evidence.

**Acceptance criteria:**
- With seeded test data (one lease expiring in 90 days, one in 400, one unverified), generate produces exactly the right opportunities in the right order; test asserts this.
- Re-running generate creates no duplicates.

**Jack verification:** seed the test leases with a provided script, click generate (or run the command), open the Opportunities page, and see the 90-day lease ranked first with a readable explanation.

---

## Phase E — Feedback loop (sequential, after D; small but MANDATORY — do not skip or defer)

**Build:**
- A `feedback` table: `id`, `opportunity_id`, `disposition` (accepted/rejected/deferred), `reason_category` (enum: `durable_policy`, `conditional`, `relational`, `timing`, `already_known`, `other`), `reason_text` (free text, nullable), `created_at`.
- On the Opportunities page: Accept / Reject / Defer buttons on each item. Reject and Defer REQUIRE picking a reason category (one tap) with optional free text. Accept asks nothing extra.
- Disposition updates the opportunity status; dispositioned items move out of the open list into a simple "History" view showing what was decided and why.
- A `criteria` table stub (`id`, `statement`, `criterion_type`, `active`, `created_at`) and one small feature: when the same `reason_category=durable_policy` free-text reason appears on 2+ rejections, show a banner suggesting "Save as a standing rule?" which writes it to `criteria`. (v1 matching can be exact-text; do not build anything fancy.)

**Acceptance criteria:**
- Every disposition is stored with its reason; History shows it; tests cover the require-reason path.
- The standing-rule suggestion appears after two matching rejections in a test.

**Jack verification:** reject two opportunities with the same typed reason, see the "save as standing rule" prompt appear, save it, and find it stored.

---

## Phase F — Golden-set test harness (parallel agent, can start any time after B)

**Build:**
- A `tests/golden/` folder with a README explaining the format: each case is a `.txt` lease excerpt plus a `.json` of expected values where **absent fields are explicitly `null`**.
- 3 synthetic starter cases: one complete, one missing base rent, one with ambiguous dates.
- A script `run_golden.py` that runs the extraction service against every case and prints a table: per-field accuracy on stated fields, and **fabrication count** (model returned a value where golden says null) as its own headline number.
- A pytest wrapper so `pytest tests/golden` fails if fabrication count > 0.

**Acceptance criteria:** harness runs, current pipeline passes the 3 starter cases, README tells Jack how to add his own hand-labeled real leases.

---

## Execution order

A → B → (C ∥ F) → D → E. Use subagents for C and F in parallel where safe. After each phase: run the full test suite, commit, and print Jack's verification steps in plain language before moving on.

## Final deliverable

When all phases pass, print a summary for Jack: what was built, the file paths of everything new, how to run the whole loop end-to-end with one of his real leases (upload → extract → review → generate → disposition), and the three numbers he should watch weekly from now on: facts awaiting review, opportunities accepted rate, fabrication count on the golden set.
