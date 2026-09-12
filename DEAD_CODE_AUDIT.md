# Dead-Code & Dormant-Feature Audit — CRE Deal Radar

**Date:** 2026-08-17
**Scope:** Read-only audit. No code was deleted, renamed, moved, or changed. The only new file in this PR is this report.
**Baseline:** `pytest backend/tests/ -v` → **671 passed, 0 failed** (full output in §6).

**Method.** Reachability was traced from the live entry points listed in §1 using a full-repo import graph (every Python module cross-referenced against every `from X` / `import X` in the tree), a React import graph (every component/page/`client.ts` export cross-referenced against actual `import` statements, ignoring commented-out lines), a registered-route inventory (70 router endpoints + 4 app-level endpoints), and a live-database schema dump compared against the ORM. Where the answer depended on runtime behaviour rather than static structure, `backend/pipeline.log` and `backend/deal_radar.db` were used as evidence.

---

## 1. Reachability map — the live entry points

| Entry point | What it reaches |
|---|---|
| `open-platform.bat` | `uvicorn app.main:app` (backend, port 8000) + `npm run dev` (frontend, port 5173). Nothing else. |
| `backend/app/main.py` → `create_app()` | 13 registered routers, 4 app-level endpoints (`/api/pipeline/run`, `/api/pipeline/refresh-public-records`, `/api/benchmarks/nova`, `/health`), the SPA catch-all, and the startup chain `migrations.rename_is_listed.run()` → `migrations.ensure_schema.run()` → `init_db()` → `start_scheduler()`. |
| `backend/app/api/routes/*` | All 13 route modules are registered. 70 endpoints total. |
| `frontend/src/App.tsx` | 6 live routes: `/`, `/properties`, `/companies`, `/review`, `/intel`, `/activity`. `/opportunities` is commented out in both `App.tsx` and `Sidebar.tsx`. |
| `outreach_agent.py` | HTTP client only. Calls `GET /api/companies`, `POST /api/companies/{id}/draft-outreach`, `POST /api/companies/{id}/log-outreach`, `GET /api/properties/{id}`, `POST /api/properties/{id}/draft-outreach`, `POST /api/properties/{id}/log-outreach`. Imports no application module. |
| 06:00 scheduled pipeline (`app/ingestion/scheduler.py` → `run_full_pipeline`) | `refresh_public_records` (→ `arlington_opendata`, `fairfax_icare`) → `tenant_class_deriver` → `refresh_property_signals` (→ `signal_engine`, `scoring_model`) → `refresh_company_signals` → `run_deal_creation` (→ `deal_creation_engine` → `match_scoring`). |

**Critical downstream contract — verified intact.** `outreach_agent.py` reads `priority`, `headcount`, `growth_rate`, `lease_expiry_months`, `submarket`, `score`, `company_id` off `/api/companies/`. Nothing in this audit proposes touching `companies.py`, `schemas/company.py`, or the `Company` model fields backing those values.

---

## 2. Classification

### 2A. ORPHANED — nothing imports it, no test covers it, no route or UI reaches it

| File | Lines | Evidence |
|---|---:|---|
| `backend/app/ingestion/adapters/costar.py` | 109 | Zero importers. All five functions (`fetch_active_listings`, `fetch_property_history`, `fetch_comp_sales`, `fetch_lease_comps`, `normalize_property`) are dev stubs returning `[]` / `{}`. `NOVA_COSTAR_SUBMARKET_CODES` referenced nowhere. |
| `backend/app/ingestion/adapters/linkedin.py` | 109 | Zero importers. All stubs return `None` / `0` / `[]`. `sf_needed_from_occupied` here is dead — the live SF logic is the `current_sf_occupied` column. |
| `backend/app/ingestion/adapters/public_records.py` | 81 | Zero importers. All stubs. `infer_owner_type` referenced nowhere outside this file. |
| `frontend/src/components/BulkUploadModal.tsx` | 244 | No `import` anywhere. Sole consumer of `uploadPropertiesBulk`. |
| `frontend/src/components/EditCompanyModal.tsx` | 111 | No `import` anywhere. |
| `frontend/src/components/SignalBreakdown.tsx` | 99 | No `import` anywhere. The only repo hits on "SignalBreakdown" are the *type* in `types/index.ts`, which is a different symbol. |
| `debug_pdf.py` | 8 | Hardcodes `C:\Users\Jackz\Downloads\report (6).pdf` and POSTs to `/api/lease-comps/debug-text` — an endpoint that no longer exists (only `/preview` and `/confirm` are registered). Broken as well as unreferenced. |
| `test_expire.py` | 5 | Root-level throwaway that hand-edits one company's `lease_expiry_date` in `backend/deal_radar.db`. Not a pytest file; not collected (rootdir collection is `backend/tests/`). |
| `1` | 6 | Stray file — an aborted git merge commit message accidentally saved as a file named `1`. |
| `backend/app/models/outreach_draft.py` export gap | — | Not orphaned code, but noted: `OutreachDraft` is the only model absent from `app/models/__init__.py`'s `__all__`. It is imported directly by `outreach_drafts.py`, so it works; the omission is a consistency bug, not dead code. **Do not "fix" by deleting anything.** |

**Unused `client.ts` exports** (`frontend/src/api/client.ts`) — no `.tsx` file imports these:

| Export | Line | Backing endpoint | Endpoint still needed? |
|---|---:|---|---|
| `getTenantOutreach` | 73 | `GET /api/properties/{id}/tenant-outreach` | See SUPERSEDED — replaced by `matched_tenants` |
| `refreshAllSignals` | 76 | `POST /api/properties/refresh-signals` | Yes — reachable by hand / curl |
| `refreshPropertySignals` | 79 | `POST /api/properties/{id}/refresh-signals` | Yes |
| `updateCompanyTrajectory` | 126 | `PATCH /api/companies/{id}/trajectory` | Yes |
| `getOutreachHistory` | 183 | `GET /api/companies/{id}/outreach-history` | Yes (property-side twin *is* used) |
| `listOutreachDrafts` | 256 | `GET /api/outreach-drafts/{property_id}` | Yes |
| `getOpportunity` | 303 | `GET /api/opportunities/{id}` | Dormant with the Opportunities page |
| `refreshPublicRecords` | 387 | `POST /api/pipeline/refresh-public-records` | Yes — the only manual trigger for §4 |
| `getIntelCriteria` | 577 | `GET /api/intel/criteria` | Yes — `saveIntelCriterion` is used, the read is not |

These are *client wrappers*, not endpoints. Deleting a wrapper is low risk; deleting the endpoint behind it is not. Treat them separately.

**Orphaned endpoints** (registered, but no UI caller and no test):

| Endpoint | File / line | Note |
|---|---|---|
| `GET /api/properties/bulk-template` | `properties.py:946` | Only consumer was `BulkUploadModal.tsx` (orphaned) |
| `POST /api/properties/bulk-upload` | `properties.py:961` | Same |

### 2B. DORMANT-BUT-WIRED — reachable and tested, deliberately frozen. **Preserve. No deletion proposed.**

| Component | Lines | Wiring | Test coverage |
|---|---:|---|---|
| `backend/app/services/property_outreach_service.py` | 1,324 | Imported by `outreach_drafts.py` and `properties.py`; serves `POST /api/properties/{id}/draft-outreach` | 11 test files |
| `backend/app/services/match_scoring.py` | 314 | Imported by `companies.py`, `properties.py`, `deal_creation_engine.py`, `output_engine.py` | 6 test files (incl. `test_match_scoring.py`, `test_lease_expiry_match_scoring.py`) |
| `frontend/src/pages/Opportunities.tsx` | 370 | Route + nav item commented out in `App.tsx` and `Sidebar.tsx` with an explicit "code retained, not deleted" comment (commit `8abc604`) | — |
| `backend/app/api/routes/opportunities.py` | 95 | Registered router; page above is its only UI consumer | — |
| `backend/app/services/opportunity_stage_service.py` | 155 | Imported by `outreach.py` and `properties.py` — **still live even with the page hidden** | `test_opportunity_autoadvance.py` |
| `backend/app/services/deal_creation_engine.py` | 383 | Runs every pipeline pass; writes the `opportunities` table (919 rows) | via pipeline tests |
| `Opportunity` model + `output_engine` queries | — | The Daily Briefing reads `opportunities` directly (`output_engine.py:371–443`). **Hiding the tab did not make the data dormant.** | 6 test files on `output_engine` |

The frozen surface is the *Opportunities UI*, not the opportunity data model. Anything that deletes `Opportunity` or `deal_creation_engine` breaks the Daily Briefing.

### 2C. SUPERSEDED — a newer implementation does the same job

| Old (superseded) | New (live) | Evidence |
|---|---|---|
| `adapters/public_records.py` (81 lines, stubs) | `adapters/arlington_opendata.py` (202) + `adapters/fairfax_icare.py` (203) | `pipeline.refresh_public_records` imports the two county adapters directly; nothing imports `public_records` |
| `adapters/costar.py` (109 lines, API stubs) | `adapters/costar_lease_activity.py` (311) + `POST /api/properties/costar-import` + `POST /api/companies/costar-import` + `CoStarImportModal.tsx` | CoStar data now arrives as XLSX upload, not API |
| `adapters/linkedin.py::sf_needed_from_occupied` | `companies.current_sf_occupied` column | `test_sf_needed_from_occupied.py` docstring records the decision: "never an estimate" |
| `frontend/src/components/BulkUploadModal.tsx` | `frontend/src/components/CoStarImportModal.tsx` | Generic CSV upload replaced by the CoStar-shaped importer |
| `frontend/src/components/EditCompanyModal.tsx` (SF-only editor) | `AddCompanyModal.tsx` in edit mode (`editCompanyId` prop, `client.ts` line 126–158 PATCH family) | `AddCompanyModal` edits every field; `EditCompanyModal` edits one |
| `GET /api/properties/{id}/tenant-outreach` (`properties.py:1430`) | `PropertyOut.matched_tenants` (`_compute_matched_tenants`, consumed at `Properties.tsx:287, 673, 790`) | Same match data, delivered on the property payload instead of a second round-trip. No test covers the endpoint. |
| `backend/mine_activity_logs.py` (80 lines, CLI) | `POST /api/intel/activity/mine` + `GET /api/intel/activity/status` → `Review.tsx` | Same `mine_all_activity_logs` service, now driven from the UI |
| `migrations/add_property_fields.py` (101) | `ensure_schema.py:85–103` | **All 16 columns duplicated** in `ensure_schema` — verified column-by-column |
| `migrations/add_lease_trajectory.py` (67) | `ensure_schema.py` | `lease_trajectory` present in `ensure_schema` |
| `migrations/add_user_data_protection.py` (91) | `ensure_schema.py:106, 634` | `last_modified_by_user` present for both tables |
| `migrations/rename_is_listed.py` (54) | — | **NOT superseded — still called on every startup** (`main.py:52`). Listed here only to prevent it being swept up with its siblings. |
| `outreach_log` legacy table | `_outreach_log_pre_fix` (0 rows) left behind by `ensure_schema`'s table rebuild (`ensure_schema.py:513`) | Dead table in the live DB |
| `companies.current_sf`, `companies.estimated_sf_needed` | `companies.current_sf_occupied` | Legacy columns still present in the live DB with 381 populated rows each; `ensure_schema.py:686` reads them to backfill the new column |

**Migrations that are NOT superseded** — `ensure_schema.py` does not contain these columns, so the one-off scripts are the only place the `ALTER TABLE` lives:

| Migration | Columns absent from `ensure_schema.py` |
|---|---|
| `add_company_costar_columns.py` | `current_rent_psf`, `future_move_flag`, `future_move_type`, `linked_property_id`, `tenant_representative` |
| `add_lease_expiry_metadata.py` | `lease_expiry_source`, `lease_expiry_last_verified` |
| `add_signal_metadata.py` | `signals_scored_count`, `insufficient_data` |
| `add_outreach_log.py` / `add_property_outreach_fields.py` | Table creation + the `outreach_log` nullability rebuild — partly re-implemented in `ensure_schema`, not verified equivalent |
| `make_headcount_nullable.py` / `make_occupancy_nullable.py` | Table-rebuild nullability changes, not column adds |

> **Scale-proofing gap (no fix in this PR).** Nine ORM columns exist in the live DB only because a one-off migration was run by hand. `create_all()` will not add them to an existing database and `ensure_schema.py` does not either. A fresh clone + existing `.db` file would be missing them. Folding these into `ensure_schema.py` is the prerequisite for deleting the one-off scripts — that is a separate PR.

Every one-off migration also hardcodes `DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")` instead of reading `settings.database_url`. Only `ensure_schema.py` follows the convention.

### 2D. LIVE — in active use

**Backend routers (13):** `activity`, `admin`, `companies`, `dashboard`, `documents`, `import_routes`, `intel`, `lease_comps`, `observations`, `opportunities`*, `outreach`, `outreach_drafts`, `properties`. (*see DORMANT.)

**Backend services (15/16 live):** `activity_intel_service`, `deal_creation_engine`, `document_extraction_service`, `intel_feedback_service`, `intel_signal_service`, `lease_comps_service`, `match_scoring`*, `opportunity_stage_service`, `output_engine`, `outreach_service`, `property_outreach_service`*, `rep_classification`, `scoring_model`, `signal_engine`, `tenant_class_deriver`. (*frozen-but-wired.)

**Ingestion:** `pipeline.py`, `scheduler.py`, `adapters/arlington_opendata.py`, `adapters/fairfax_icare.py`, `adapters/costar_lease_activity.py`.

**Models (14 tables):** all live. `Property` (333 rows), `Company` (528), `Opportunity` (919), `ActivityLog` (343), `OutreachLog` (182), `OutreachDraft` (184), `Observation` (469), `IntelActivityExtraction` (300), `IntelOpportunity` (2), `IntelSignal` (2), `IntelFeedback` (1), `TenantClassFeedback` (22), `Document` (0), `IntelCriterion` (0).

**Frontend pages (6 live):** `Dashboard`, `Properties`, `Companies`, `Review`, `Intel`, `ActivityLog`.
**Frontend components (11 live):** `Sidebar`, `AddCompanyModal`, `AddPropertyModal`, `CoStarImportModal`, `CoStarTenantImportModal`, `CompanySnoozeModal`, `SnoozeModal`, `LeaseCompsModal`, `OutreachDraftModal`, `PriorityBadge`, `ScoreBadge`.

**Root / backend scripts still useful (not orphaned, but not on any hot path):** `scoring_impact_report.py` (140) — operational tool, run by hand after a scoring change; `backend/seed_data.py` (761), `seed_intel_test.py` (57), `seed_observations.py` (60) — demo/dev fixtures; `migrations/delete_seeds.py` (374) — the counterpart that removes them. All four seed scripts are referenced by each other or by `delete_seeds`, not by the app. Keep as tooling.

---

## 3. CLI vs UI — which is the active outreach interface

**Answer: the platform UI. `outreach_agent.py` is dormant and has been for the life of the current data.** The code does not meaningfully support both — the CLI is a thinner, older path that is missing the features the UI writes.

Evidence, in order of strength:

1. **ActivityLog writes.** Every `ActivityLog(...)` construction in the app lives in `activity.py:301` (`POST /api/activity/`), `opportunities.py:83`, `properties.py:1394/1417` (snooze/unsnooze), `companies.py:730/751` (snooze/unsnooze). `outreach_agent.py` **never creates an ActivityLog** — it logs to `outreach_log` via `POST /{id}/log-outreach` and to a Google Doc. The UI does write ActivityLog: `OutreachDraftModal.tsx` imports `createActivity` from `client.ts` (line 360 → `POST /api/activity/`).
2. **Live data shape.** `activity_logs` holds 343 rows, **all with `created_by = 'user'`** (the value `activity.py:315` hardcodes for the UI endpoint), spanning 2026-04-14 → 2026-08-13. `outreach_drafts` holds 184 rows (105 `property_side`, 79 `tenant_side`) — a table `outreach_agent.py` never touches, because the agent only calls `draft-outreach` and `log-outreach`, never `/api/outreach-drafts/`.
3. **The frontend calls the outreach endpoints directly.** `OutreachDraftModal.tsx` is the sole consumer of `draftOutreach`, `draftPropertyOutreach`, `logOutreach`, `logPropertyOutreach`, `saveOutreachDraft`, `getOutreachDraft`, `deleteOutreachDraft`, `updateOutreachLog`, `searchIntelligence` and `createActivity`. It is imported by four pages.
4. **The Google Sheets tracker path is NOT reachable.** Three independent blockers:
   - `TRACKER_SHEET_ID = os.environ.get("TRACKER_SHEET_ID", "")` (`outreach_agent.py:47`) and `TRACKER_SHEET_ID` appears in **no** `.env` file, no `.env.example`, and nowhere else in the repo. All three tracker functions (`get_contacted_ids:198`, `log_to_tracker:212`, `init_tracker_sheet:233`) begin with `if not TRACKER_SHEET_ID: return`.
   - `google_token.json` does not exist on disk, so OAuth has never been completed. Without it `get_google_services()` falls through to `InstalledAppFlow.run_local_server()`.
   - `google-auth`, `google-auth-oauthlib` and `google-api-python-client` are **not in `backend/requirements.txt`**. The agent cannot import in the project venv without a manual install.
5. **Feature drift.** `outreach_log` rows carry four `outreach_type` values (`tenant` 115, `tenant_match` 58, `acquisition` 5, `for_sale_vacancy` 4). The agent's `--outreach-type` flag documents only `listing_rep` and auto-selection; `for_sale_vacancy` and the owner-discretion path are UI-side logic in `property_outreach_service.py`.

**Conclusion:** the UI is the interface. `outreach_agent.py` (531 lines) is dormant-by-disuse rather than dormant-by-design — nothing in the codebase marks it frozen the way the Opportunities page is marked. It still *runs* against a live backend if you install the Google libs and complete OAuth, so it is not broken; it is unused. **It is not on the deletion list**, because it is the documented CLI in `SETUP.md` and because it is the single hardest thing in this repo to re-derive if the decision reverses.

---

## 4. County enrichment adapters — invoked, and failing silently

**Invoked: yes.** `run_full_pipeline` step 1 calls `refresh_public_records(db)` (`pipeline.py:509`), which imports `fetch_building_permits`, `fetch_property_assessment`, `get_last_major_permit_year` from `arlington_opendata` and `enrich_property_from_fairfax` from `fairfax_icare` (`pipeline.py:419–424`), then dispatches by submarket string: `"arlington" in submarket` → Arlington; `tysons|reston|falls church|fairfax` → Fairfax.

**Succeeding: no. They fail silently, on every run.**

| Evidence | Value |
|---|---|
| `[Pipeline] Public records refresh — N/M properties enriched` lines in `backend/pipeline.log` | **34 occurrences, every one with N = 0.** Latest: `0/322`, `0/300`, `0/47`, `0/42` |
| `[Pipeline] Public records failed for …` (the `except` branch at `pipeline.py:483`) | **0 occurrences** — no exception is ever propagating |
| `[Arlington]` / `[Fairfax]` log lines | **0 occurrences in `pipeline.log`** |
| `properties.last_renovation_year` populated | 59 of 333 rows — all from CoStar/manual entry, none attributable to a permit lookup |
| Last full pipeline run in the log | 2026-06-06 (the app has been started since; the 06:00 job has not produced a logged run) |

**Why it is silent, mechanically:**

1. `updated += 1` only executes inside `if assessment:` (`pipeline.py:464–482`). `assessment` is `None` on every property, so the counter never moves.
2. Both adapters catch `HTTPStatusError`, `TimeoutException` and bare `Exception` and `return None` (`arlington_opendata.py:166–177`, `fairfax_icare.py:81–88`). A 404 on a stale dataset ID looks identical to "no such address".
3. Their diagnostics are unreachable. Both use `logging.getLogger(__name__)` — loggers named `app.ingestion.adapters.*`. The rotating file handler is attached **only** to the `deal_radar.pipeline` logger (`pipeline.py:41–54`). So every `[Arlington] Assessment API returned 404 — check dataset ID` warning goes to a logger with no handler and disappears. **This is why there is no error to point at.**
4. Most likely root cause, unverified from inside this audit: the hardcoded Socrata dataset IDs `kzfm-bci3` / `r6dm-5vxn` (`arlington_opendata.py:33–34`, comment says "verify at data.arlingtonva.us if API returns 404") and the Socrata/ArcGIS field names (`property_address`, `SITE_ADD`). A renamed dataset or field yields exactly this signature: no exception, no records, zero enriched.

**Classification: DORMANT-BUT-WIRED, degraded.** Do not delete. They are reachable, they are the successor to `public_records.py`, and they are one dataset-ID fix away from working. The correct next PR attaches the pipeline file handler to the `app.ingestion.adapters` logger (or renames the adapter loggers under `deal_radar.pipeline.*`) so the failure becomes visible, then re-verifies the dataset IDs.

---

## 5. Orphaned DB columns — **listed only. Nothing dropped.**

### 5A. In the ORM, never read or written by any endpoint, service or UI

| Table.column | Declared | Live rows populated | Note |
|---|---|---:|---|
| `opportunities.is_featured` | `models/opportunity.py` | 0 of 919 | Only occurrence in the entire repo is the column declaration. Not in `schemas/opportunity.py`, not in `ensure_schema.py`, never set. Truly dead. |
| `intel_activity_extractions.extracted_at` | `models/intel.py` | 300 | Written by SQLAlchemy's `default=`; never read by any service, endpoint or UI. Write-only. |
| `properties.num_floors` | `models/property.py` | 0 of 333 | Referenced only by `seed_data.py`. Absent from `schemas/property.py`, so it never reaches the API. |

### 5B. In the ORM and exposed by a schema, but never written by anything

| Table.column | Exposed via | Live rows populated | Note |
|---|---|---:|---|
| `companies.relocation_signal` | `schemas/company.py` | 0 of 528 | Read path exists; no writer anywhere (`signal_engine`, `pipeline`, routes all skip it). Its siblings `expansion_signal` / `contraction_signal` are written. |
| `properties.estimated_ltv` | `schemas/property.py` | 0 of 333 | Only other reference is `seed_data.py` |
| `properties.listing_date` | `schemas/property.py` | 0 of 333 | Only other reference is `seed_data.py` |

### 5C. Write-only (set by the backend, never surfaced)

| Table.column | Writer | Note |
|---|---|---|
| `properties.last_signal_run` | `pipeline.py`, `properties.py` | Not in `schemas/property.py`; the frontend cannot see it. Useful for debugging — recommend *exposing*, not dropping. |
| `properties.updated_at`, `companies.updated_at`, `opportunities.updated_at` | ORM `onupdate` | In the schemas; no UI reads them. |

### 5D. In the live database but NOT in the ORM — legacy leftovers

| Object | Rows | Note |
|---|---:|---|
| `companies.current_sf` | 381 populated | Legacy. Superseded by `current_sf_occupied`. **Still read** by `ensure_schema.py:686` to backfill. Dropping it breaks that backfill for any DB that has not yet run it. |
| `companies.estimated_sf_needed` | 381 populated | Same. |
| `_outreach_log_pre_fix` (whole table, 19 cols) | **0 rows** | Left behind by `ensure_schema.py:513`'s `ALTER TABLE outreach_log RENAME TO _outreach_log_pre_fix` rebuild. Zero rows, zero references. The only genuinely droppable DB object in this list — and even so, drop it in its own PR after a backup. |

The `companies` table has 56 columns; the ORM declares 54. The two-column delta is exactly `current_sf` + `estimated_sf_needed`. `properties` matches the ORM exactly at 88.

---

## 6. Test baseline — green before any deletion

Run unchanged, no test added, weakened or removed:

```
$ pytest backend/tests/ -v
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0
cachedir: .pytest_cache
rootdir: <repo root>
plugins: anyio-4.13.0
collecting ... collected 671 items

backend/tests/golden/test_golden.py::test_harness_detects_a_faithful_extractor_as_clean PASSED [  0%]
backend/tests/golden/test_golden.py::test_harness_flags_a_fabricating_extractor PASSED [  0%]
backend/tests/golden/test_golden.py::test_live_model_does_not_fabricate PASSED [  0%]
backend/tests/test_activity_edit.py::test_every_freeform_field_is_editable PASSED [  0%]
... 667 more ...
====================== 671 passed, 24 warnings in 22.45s =======================
```

**671 passed, 0 failed, 0 skipped.** This is the baseline. Any deletion PR that follows must reproduce exactly this line before it is merged.

24 warnings, all pre-existing and unrelated to this audit: 14 Pydantic `class-based config` deprecations, 8 FastAPI `on_event` deprecations (`main.py:51`, `main.py:60`), 1 SQLAlchemy `Query.get()` legacy warning, 1 misc.

---

## 7. Recommended deletion order — lowest risk first

Each entry names **what breaks if this classification is wrong.** Nothing below has been executed.

| # | Delete | Lines | Risk | What breaks if I'm wrong |
|---:|---|---:|---|---|
| 1 | `1` (stray merge-message file) | 6 | **None** | Nothing. It is not code, not referenced, not importable. |
| 2 | `test_expire.py` | 5 | **None** | Nothing. Not collected by pytest (`rootdir` collection targets `backend/tests/`); a one-time hand-edit of one company's lease date. Losing it costs a 5-line retype. |
| 3 | `debug_pdf.py` | 8 | **None** | Nothing. Already broken — it POSTs to `/api/lease-comps/debug-text`, which is not a registered route. |
| 4 | `frontend/src/components/SignalBreakdown.tsx` | 99 | **Very low** | Nothing at build time — no file imports it, so Vite already tree-shakes it. If wrong, `npm run build` fails immediately with an unresolved import. Cheap to detect. |
| 5 | `frontend/src/components/EditCompanyModal.tsx` | 111 | **Very low** | The single-field SF editor. `AddCompanyModal` in edit mode covers it. If wrong, a "quick edit SF" affordance somewhere loses its modal — but nothing imports it, so nothing can. |
| 6 | `backend/app/ingestion/adapters/public_records.py` | 81 | **Very low** | Nothing. Pure stubs (`return {}`, `return []`, `return None`). If some future code was going to call `infer_owner_type`, it would get a name error at import — caught by the first `pytest` run. |
| 7 | `backend/app/ingestion/adapters/linkedin.py` | 109 | **Low** | Nothing at runtime — all stubs. The real loss is documentation: the module carries the Apollo/ZoomInfo/LinkedIn-ToS notes that are the plan of record for company enrichment. **Move those comments into `CLAUDE.md` before deleting.** |
| 8 | `backend/app/ingestion/adapters/costar.py` | 109 | **Low** | Nothing at runtime — all stubs. Same caveat: `NOVA_COSTAR_SUBMARKET_CODES` is the only place the CoStar submarket code mapping is written down. **Preserve that dict somewhere first** — re-deriving it means a CoStar support ticket. |
| 9 | `frontend/src/components/BulkUploadModal.tsx` **+** `GET /api/properties/bulk-template` (`properties.py:946`) **+** `POST /api/properties/bulk-upload` (`properties.py:961`) **+** `uploadPropertiesBulk` (`client.ts:407`) | 244 + ~40 | **Medium** | This is the only non-CoStar path for getting properties into the system in bulk. If a CSV import is ever needed for a source CoStar does not cover, it is gone. No test covers the endpoints, so **pytest will not catch a mistake here** — verification is manual: confirm CoStar import is the only import route being used. Delete the component first, leave the endpoints one release, then remove them. |
| 10 | `backend/mine_activity_logs.py` | 80 | **Medium** | The CLI backfill for activity-log mining. Superseded by `POST /api/intel/activity/mine` in `Review.tsx`. If wrong, the only way to re-mine 300+ historical logs is through the UI one batch at a time. `--force` re-mining has no UI equivalent. **Verify the UI exposes `force` before deleting.** |
| 11 | `migrations/add_property_fields.py` | 101 | **Medium** | All 16 of its columns are verified present in `ensure_schema.py`, so a fresh DB is fine. What breaks is *history*: if a `.db` file predating `ensure_schema` ever needs migrating, this is the only script that knows the intended column types (`REAL DEFAULT 0` vs `REAL`). Low probability, unrecoverable if hit. |
| 12 | `migrations/add_lease_trajectory.py`, `migrations/add_user_data_protection.py` | 67 + 91 | **Medium** | Same shape as #11 — columns confirmed in `ensure_schema.py`. Same history-loss risk. |
| — | **DO NOT DELETE — blocked** | | | |
| ✗ | `migrations/add_company_costar_columns.py`, `add_lease_expiry_metadata.py`, `add_signal_metadata.py`, `add_outreach_log.py`, `add_property_outreach_fields.py`, `make_headcount_nullable.py`, `make_occupancy_nullable.py` | 610 | **Blocked** | Their columns are **absent from `ensure_schema.py`** (§2C). Deleting them removes the only `ALTER TABLE` for `tenant_representative`, `linked_property_id`, `lease_expiry_source`, `lease_expiry_last_verified`, `signals_scored_count`, `insufficient_data`, `current_rent_psf`, `future_move_flag`, `future_move_type`. Any existing DB missing those columns becomes unmigratable, and `/api/companies/` 500s on the fields `outreach_agent.py` depends on. **Fold them into `ensure_schema.py` first.** |
| ✗ | `migrations/rename_is_listed.py` | 54 | **Blocked** | Called on every app startup (`main.py:52`). Deleting it breaks boot. |
| ✗ | `outreach_agent.py` | 531 | **Blocked** | Dormant, not dead. Documented in `SETUP.md`; the Google Docs package format is not reproduced anywhere else. Revisit only after a deliberate decision to kill the CLI. |
| ✗ | `frontend/src/pages/Opportunities.tsx`, `api/routes/opportunities.py`, `opportunity_stage_service.py`, `deal_creation_engine.py`, `models/opportunity.py` | 1,003+ | **Blocked** | Deliberately frozen UI over live data. The Daily Briefing queries `opportunities` directly (`output_engine.py:371–443`); `opportunity_stage_service` is imported by `outreach.py` and `properties.py` and runs regardless of the hidden tab. Deleting any of it silently empties the Daily Briefing. |
| ✗ | `property_outreach_service.py`, `match_scoring.py` | 1,638 | **Blocked** | Frozen features, fully wired and covered by 17 test files between them. |
| ✗ | `adapters/arlington_opendata.py`, `adapters/fairfax_icare.py` | 405 | **Blocked** | Invoked every pipeline run. Failing silently ≠ dead. Fix logging visibility first (§4). |
| ✗ | Any DB column or table, including `_outreach_log_pre_fix` | — | **Blocked in this PR** | No schema change belongs in a dead-code sweep. `companies.current_sf` / `estimated_sf_needed` are still read by `ensure_schema.py:686` and hold 381 rows each. |

**Sequencing rule.** Items 1–8 are safe as one PR: they are unreferenced, and `pytest backend/tests/ -v` plus `npm run build` between each step is sufficient verification. Items 9–12 each deserve their own PR with the manual check named in the row. Nothing in the blocked list moves until its stated prerequisite lands.

**Total safely removable now (items 1–8): 528 lines.** Total in the medium tier (9–12): ~623 lines. Total blocked pending prerequisite work: ~4,241 lines.
