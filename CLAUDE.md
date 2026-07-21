# CLAUDE.md

Guidance to Claude Code (claude.ai/code) for working in this repository.

## Current focus — read first

**The only active workflow is tenant-side outreach.** Before proposing work, check its bucket:

| Area | Status |
|---|---|
| Tenant-side outreach generation | **ACTIVE** — all new work happens here |
| Tenant scoring / prioritization | **ACTIVE** — drives who gets contacted next |
| Tenant requirements capture | **ACTIVE GAP** — known weak spot, see below |
| Property-side outreach (owners) | **DORMANT** |
| Tenant↔property match scoring | **FROZEN** |
| Property-side ingestion/scoring | **DORMANT** — still feeds the DB; not acted on |

**Dormant/frozen means:** leave the code and its passing tests in place; don't refactor it, build on it, delete/"clean up" it, or let it influence tenant-side scoring or outreach. It is not dead code.

## What this is

**Deal Radar OS** — a local, single-user deal-sourcing and outreach tool for one CRE broker (Jack Zamer, The Commercial Real Estate Group) working the Northern Virginia (NoVA) office/retail market.

Operating thesis is sequential, and it shapes feature design:

1. Surface tenants approaching lease expiry (6–9 month pre-expiry window, before competing brokers engage).
2. Generate outreach — email + call script — to get Jack on the phone.
3. **The tenant states their own requirements on that call** (SF, timing, must-haves, budget, deal structure) — real info from a human, not inferred from headcount math.
4. Only then does a property search happen, driven by those stated requirements.

Step 4 is not automated and not the near-term goal. **The platform's job ends at step 2** — its output is a conversation, not a property list. Features that don't improve step 1 or 2 are out of scope.

Stack: **FastAPI + SQLAlchemy (SQLite) backend**, **React + TypeScript + Vite + Tailwind frontend**. OpenAI GPT-4o and Anthropic Claude are both wired in for outreach copy generation.

## Known gap: tenant requirements capture

Stated requirements currently land unstructured in two places: **ActivityLog** (freeform, messy) and the **Companies tab** (partial, inconsistent). There is no dedicated structured place on the Company record for stated requirements (SF range, must-have features, budget ceiling, ground-floor/access needs, lease term/option preferences, target submarkets, buildout willingness, TI expectations). This is the highest-value area for new work. Any proposal should:

- Distinguish **stated** requirements (from the tenant, high confidence) from **inferred** signals (headcount × SF/person, low confidence). Never blend them into one field.
- Not break the `/api/companies/` contract (see "Working conventions").
- Route any new column through `ensure_schema.py` (see "Database migrations").

## Commands

### Backend (from `backend/`)
```bash
venv\Scripts\activate                      # Windows venv, present at backend/venv
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pytest tests/ -v                           # all tests
pytest tests/test_scoring.py -v            # single file
pytest tests/test_scoring.py::test_name -v # single test
python seed_data.py                        # seed dev data
python -m migrations.ensure_schema         # manually apply pending migrations
```
A root-level `venv/` (outreach CLI scripts) is distinct from `backend/venv/` (FastAPI app) — activate the one matching what you're running.

### Frontend (from `frontend/`)
```bash
npm run dev       # vite dev server, port 5173, proxies /api → localhost:8000
npm run build     # tsc && vite build → frontend/dist (served by FastAPI in prod, see main.py)
npm run preview
```

### Whole-stack
- `open-platform.bat` — launches backend (uvicorn:8000) + frontend (vite:5173) in separate terminals and opens the browser. **Do not modify this file.**
- `docker-compose.yml` — containerized backend + frontend; backend runs `seed_data.py` then uvicorn.

### Outreach CLI (status: UNVERIFIED — confirm before relying on it)
`outreach_agent.py` (repo root) pulls from the running Deal Radar API, calls GPT-4o, and pushes to Google Docs/Sheets + Outlook. Its generation logic was ported into `backend/app/services/outreach_service.py` so API and CLI share identical prompts — the CLI calls the API rather than duplicating logic. See `SETUP.md` for one-time OAuth/API-key setup.

**Unconfirmed whether the CLI is still in active use or outreach is now fully UI-driven.** Until settled, treat it as a live consumer of the `/api/companies/` contract — don't break it, don't invest in extending it. Any command block that runs it must include navigation + venv activation:
```
cd C:\Users\Jackz\CRE-deal-radar-platform
venv\Scripts\activate
python outreach_agent.py [args]
```

## Architecture

### Backend layout
```
backend/app/
  main.py           FastAPI app factory; registers routers, runs startup migrations, serves built frontend
  config.py         ALL tunable constants: CBRE benchmarks, submarket adjacency, match-score weights,
                    concession-data verification gate — nothing hardcoded inline in services.
                    Read before touching scoring or outreach logic.
  database.py       SQLAlchemy engine/session/Base (SQLite only)
  models/           ORM: Property, Company, Opportunity, ActivityLog, OutreachLog, OutreachDraft, TenantClassFeedback
  schemas/          Pydantic request/response schemas, one file per resource
  api/routes/       One router per resource (properties, companies, opportunities, activity, dashboard,
                    outreach, outreach_drafts, import_routes, lease_comps, admin)
  services/         Business logic — scoring, matching, outreach generation (see below)
  ingestion/        Pipeline: adapters/ (CoStar, Arlington Open Data, Fairfax iCARE, LinkedIn),
                    pipeline.py (orchestration), scheduler.py (APScheduler refresh)
backend/migrations/ Hand-written idempotent SQLite migrations (NOT Alembic, despite the dependency)
backend/tests/      pytest, flat directory, one file per behavior/bugfix (not per module)
```

### Database migrations — read before adding/changing any ORM column
No Alembic, despite it being in `requirements.txt`. `backend/migrations/ensure_schema.py` is hand-written and runs on every startup (in `main.py`'s `on_startup`, before `init_db()`). `Base.metadata.create_all()` only creates brand-new tables — never alters existing ones — so **any new ORM column must also be added to `ensure_schema.py`** as a guarded `ALTER TABLE`, or it will silently exist only on fresh databases and 500 on every live/Docker-volume database. Conventions to follow:

- Every `ALTER TABLE ADD COLUMN` is guarded by a `PRAGMA table_info` existence check via `_add_column()`, often also wrapped in try/except so one partial migration can't abort startup.
- SQLite can't drop a `NOT NULL` constraint via `ALTER TABLE` — pattern is rename → recreate without the constraint → copy rows → drop old table (see `fix_outreach_log_company_id_nullable`, `ensure_nullable_columns`).
- DB path resolves from `settings.database_url` (never hardcoded), supporting local dev (`sqlite:///./deal_radar.db`) and Docker (`sqlite:////app/data/deal_radar.db`).
- `migrations/rename_is_listed.py` runs before `ensure_schema` in startup — a one-off column rename kept separate from the general schema-sync.

### Scoring
**Tenant-side scoring is ACTIVE.** `services/scoring_model.py` / `signal_engine.py` compute company-side signals (headcount growth, hiring velocity, lease expiry, space utilization). Sub-scores are stored individually (`sig_*` columns) for transparency, then rolled into a composite `opportunity_score` and a `priority` bucket (`IMMEDIATE`/`HIGH`/`WORKABLE`/`IGNORE`). Lease expiry is the sole driver of tenant priority; other signals are retained/displayed but carry no scoring weight. Thin-data tenants floating to the top on lease timing alone is intended, not a bug.

**Property-side signal scoring (lease rollover, vacancy trend, ownership duration, debt pressure) is DORMANT.** It still runs and populates `signal_score`, but nothing downstream acts on it. Don't tune, extend, or delete it.

`services/tenant_class_deriver.py` + the `TenantClassFeedback` model infer a tenant's building-class fit from its address when unset, and remember user corrections so the same address is never re-guessed wrong twice. **ACTIVE** — feeds tenant-side outreach context.

### Match scoring — FROZEN
`services/match_scoring.py` is the tenant↔property composite Match Score: a weighted blend of lease-expiry timing, submarket (exact vs. adjacent, via `SUBMARKET_ADJACENCY` in `config.py`), building-class fit, and SF fit (hard-gated by `MAX_SF_DELTA`, then scored on a gradient). All weights and point values live in `config.py`. **Freeze it** — don't extend, refactor, wire new callers in, or let its output influence tenant priority or outreach copy. Existing tests/code stay in place.

*Directional note, not a work item:* the eventual goal is to accept a tenant's **stated** requirements and return matching properties from the DB. That's a long way out with real uncertainty (matching logic correctness and property-data completeness), explicitly not on the near-term roadmap. Not license to start building toward it.

`services/deal_creation_engine.py` and `opportunity_stage_service.py` turn scored signals into `Opportunity` records and manage pipeline stage. Pipeline stage tracking is ACTIVE for tenant opportunities; property-side opportunity creation is dormant.

### Outreach generation — privacy is a hard requirement
`services/outreach_service.py` (tenant-side, **ACTIVE**) and `services/property_outreach_service.py` (property-side, **DORMANT**) build LLM prompts and generate email + call-script copy. Two rules are covered by dedicated tests (`tests/test_outreach_privacy.py`) — **both remain in force even though the property side is dormant; do not delete or weaken these tests:**

1. **Tenant-side copy must never contain the property street address** (submarket + building class only).
2. **Property-side copy must never contain the tenant company name.**

Both are checked by asserting the sensitive value never even reaches the LLM prompt (not just rendered output). Keep both green if you touch either builder. When a property is both for-sale and available for leasing, outreach copy must include owner-discretion language. Other things baked into these services:

- **GPT-4o is the locked model for outreach email generation.** Don't swap it. Claude is used for the Intelligence Review panel; that split is intentional.
- `config.CONCESSION_DATA_VERIFIED` gates whether copy may cite numeric concession figures (free-rent months, TI $/PSF) — unverified CBRE estimates. While `False`, both generators fall back to a fixed qualitative paragraph with no numbers, percentages, or named source. Read at call time (never cached at import) so flipping it needs no generator rewrite.
- `config.is_provisional_submarket()` marks submarkets with placeholder/proxy benchmarks. Tenant email generation must never quote a provisional submarket's asking rent; the call sheet and Submarket Intel card append a "(provisional — verify before quoting)" disclaimer instead.
- **No fabricated or unverified statistics in generated copy, ever.** No named sources (e.g. CBRE) unless the data is verified and flagged in `config.py`.
- Rent figures in a generated email must match the call sheet DATA block exactly — numeric parity is a contract.
- Email copy leads with lease timing and market context, not per-tenant rent-gap stats. Rent comparisons use hedged language ("likely," "almost certainly," "flat since you signed"). No forced hard closes.
- CoStar import fields are null-safe throughout — the relevant column is `"Total Available Space (SF)"`. Endpoints must handle null/empty input without 500ing.

### Market benchmark data
`config.py`'s `NOVA_OFFICE_BENCHMARKS` and `SUBMARKET_BENCHMARKS` are sourced from CBRE's quarterly NoVA office report, meant to be updated each quarter — check the `data_as_of` field before trusting them as current. Annandale, Crystal City, Merrifield, and Springfield currently carry provisional placeholder benchmarks awaiting real CBRE data.

### Frontend layout
```
frontend/src/
  App.tsx        Route table (react-router-dom) — Dashboard, Properties, Companies, Opportunities, ActivityLog
  api/client.ts  All backend API calls (axios instance, baseURL '/api'), grouped by resource
  pages/         One component per route
  components/    Modals (Add/Edit Company & Property, CoStar import, Snooze, Outreach Draft, Lease Comps,
                 Bulk Upload) and shared display components (badges, signal breakdown)
  types/index.ts Shared TS interfaces mirroring backend Pydantic schemas
  constants.ts   Submarket list — keep in sync with `PLATFORM_SUBMARKETS` in backend `config.py`
```
Vite dev proxies `/api` to `http://localhost:8000` (see `vite.config.ts`); in production the FastAPI app serves the built `frontend/dist` (see `main.py`).

## Working conventions specific to this repo
- **Don't touch `open-platform.bat`.**
- **Don't delete or refactor dormant/frozen code** (property-side outreach, match scoring). Leaving it untouched is the requirement.
- **Don't weaken or delete a test to make new code pass** — including tests covering dormant features.
- New pytest files under `backend/tests/` lock a real contract with in-memory inputs only — no live DB, network, or CoStar calls. One file per behavior/bugfix (e.g. `test_outreach_privacy.py`, `test_rent_gap_ladder.py`), not per module.
- `backend/conftest.py` adds `backend/` to `sys.path` so tests import as `app.*` — run pytest from `backend/` (or rely on this conftest if invoking elsewhere).
- `outreach_agent.py` reads `/api/companies/` for `priority`, `headcount`, `growth_rate`, `lease_expiry_months`, `submarket`, `score`, `company_id` — **don't rename or drop these fields**, regardless of whether the CLI is currently run.
- Loose root-level `check_*.py` / `debug_pdf.py` / `scoring_impact_report.py` are ad hoc investigation scripts, not application library code — don't import from them.
