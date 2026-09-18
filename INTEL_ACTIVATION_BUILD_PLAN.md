# Intel Activation — Build Plan

Turns the Intel tab from "always empty" into a working ranked call list, using facts that are
**already in the database**. No new data sources, no re-mining, no new tables.

**Status: BUILT — phases 1–6 implemented, 717 backend tests passing, frontend builds clean.**

Dry-run against a copy of the live database: **0 → 25 opportunities** (1 lease-expiring,
5 verify-first, 19 stated-requirement), stable and duplicate-free on repeat runs.

---

## The problem in one paragraph

The miner works: 355 activity notes produced 748 structured facts. The signal engine reads almost
none of them. It only makes an opportunity out of `expiration_date`, only 12 of the 748 facts *are*
expiration dates, and 10 of those 12 are stored in natural language (`"~February 2027"`,
`"End of 2026"`) that `_parse_date` cannot read, so they are dropped silently. The 2 it can read are
both outside the window — one expired last month, one is four years out. 12 → 2 → 0. Meanwhile the
736 requirement facts (SF, submarket, timing, budget, must-haves) are ineligible to produce anything
at all. Review reads 0 for a separate reason: every mined field auto-approves, so nothing can ever
queue.

## What "working" means

> **Notes → Facts → Review (only what matters) → Signals → Ranked call list → Outreach**

Today the loop breaks between *Facts* and *Signals*. These six phases close it.

### Design rules carried from CLAUDE.md
- Additive only. No dropped columns, no rewritten models, **no new tables** — this plan needs none.
- Dormant/frozen code (property-side outreach, match scoring) is not touched.
- No test is weakened to make new code pass.
- Observations stay append-only; `verified_by` keeps machine vs. human approval honest.
- Nothing is fabricated: a normalized fuzzy date is a *suggestion Jack confirms*, never a silent fact.

---

## Phase 1 — Read the dates people actually write

**Problem:** `_parse_date` accepts 10 rigid formats. Real extracted values look like
`"~February 2027 (6 months from note date of 2026-08-17)"`.

**Build:** `parse_expiry(value) -> ParsedExpiry(date, precision, normalized)` in
`intel_signal_service.py`, handling:

| Stored value | Parsed | Precision |
|---|---|---|
| `2027-01-23` | 2027-01-23 | `exact` |
| `December 2026` | 2026-12-31 | `month` |
| `~February 2027 (6 months from note date of 2026-08-17)` | 2027-02-28 | `month` |
| `Q1 2027` | 2027-03-31 | `quarter` |
| `End of 2026` | 2026-12-31 | `year` |
| `2 years from note date (approx. 2028-06)` | 2028-06-30 | `month` |

Rules: strip hedges (`~`, `approx.`, `circa`); **try the text outside parentheses before the text
inside** so `"~February 2027 (… note date of 2026-08-17)"` resolves to Feb 2027 and not to the note
date; month-precision resolves to the **last day** of the month (leases end at month end).
`_parse_date` stays as a thin wrapper so existing callers and tests are unaffected.

**Test:** `tests/test_intel_fuzzy_dates.py` — every real value currently in the database parses to
the right date and the right precision; genuinely unparseable text still returns `None`.

**Verify:** `pytest tests/test_intel_fuzzy_dates.py -v`

---

## Phase 2 — Rank the 6–9 month window highest

**Problem:** `_urgency_bonus` scores *sooner = better*, peaking on the expiry date itself. That is
backwards for the operating thesis: a lease expiring in 30 days is a tenant who has already re-signed
or already has three brokers on them. The one worth calling expires in 7 months.

**Build:** `_window_bonus(days)` replacing `_urgency_bonus`. Full points across the **180–270 day**
band, tapering to 0 at both ends (too late below ~45 days, too early past ~365). Still capped at
`URGENCY_MAX = 39`, below the 40-point base-weight gap, so **a verified fact never loses to an
unverified one** — the existing invariant holds unchanged.

**Test:** `tests/test_intel_window_scoring.py` — a 210-day lease outranks both a 20-day and a
350-day lease; verified still beats unverified at every distance.

**Verify:** `pytest tests/test_intel_window_scoring.py -v`

---

## Phase 3 — Stated requirements become signals

**Problem:** 736 requirement facts drive nothing. A tenant who said *"3,000–5,000 SF, Alexandria,
needs an elevator, deciding in Q1"* is a live deal whether or not their expiration date was captured.

**Build:** a fourth signal type, `stated_requirement`. Fires when an entity has **≥2 distinct
requirement fields** and **≥1 high-specificity field** (SF min/max, budget, timing, must-haves, TI,
buildout, lease term) — contact name/email don't count as requirements. Scored on specificity
(how many fields stated) plus staleness (days since the newest source note). Base 55, bonuses capped
at 39 → range 55–94, always below a verified `lease_expiring`.

Against today's database this produces ~22 cards. (≥2 fields alone would produce 51 — too noisy;
the specificity gate is what makes it a call list rather than a dump.)

**Test:** `tests/test_intel_stated_requirements.py` — fires on a specific requirement set, does not
fire on contact details alone or on a lone submarket, never outranks a verified expiration, and
de-dupes on re-run.

**Verify:** `pytest tests/test_intel_stated_requirements.py -v`

---

## Phase 4 — Review gets the facts that actually matter

**Problem:** `AUTO_APPROVE_FIELDS = set(REQUIREMENT_FIELDS)` clears everything, so Review is
structurally incapable of holding anything from a note.

**Build:** auto-approval becomes value-aware, not just field-aware. An `expiration_date` from a note
auto-approves **only when it parses to an exact date**; a fuzzy one (`"~February 2027"`) queues for
confirmation. Everything else is unchanged — contact details, submarkets and space types still clear
themselves, and lease-PDF facts still always queue.

Plus `requeue_fuzzy_dates(db)`: a backfill that sends the 10 already-auto-approved fuzzy dates back
to Review. It flips only the verification flag — value, snippet, confidence and provenance untouched
— and never touches a row Jack verified himself. Exposed as `POST /api/intel/activity/requeue-dates`.

**Test:** `tests/test_intel_fuzzy_dates.py` — exact dates auto-approve, fuzzy ones queue, lease-PDF
facts still never auto-approve, backfill is idempotent and leaves human-verified rows alone.

**Verify:** open Review; the fuzzy dates are waiting there.

---

## Phase 5 — Review shows a one-tap suggested date

**Build:** `ObservationOut` gains derived (non-persisted) `suggested_value` and `value_precision`.
The Review card for a fuzzy date shows the verbatim note text *and* a pre-filled normalized guess —
`~February 2027` → `2027-02-28` — with a "month precision, confirm the day" hint. Confirming writes
the clean date through the existing supersede path; nothing edits in place.

**Verify:** Review → a fuzzy date card → the Correct box is pre-filled with the ISO date → Save.

---

## Phase 6 — Generate stops going blank

**Problem:** clicking "Generate Opportunities" with 748 facts returns `[]` and re-renders the same
empty state. Indistinguishable from the button not working.

**Build:** `generate_with_stats(db)` returns the opportunities **and** a scan summary — facts
scanned, expiration dates found, how many were unparseable, and a per-signal-type breakdown. The
route returns `{opportunities, stats}`; Intel renders it as one line under the button
(*"Scanned 748 facts · 12 expirations · 0 unreadable → 28 opportunities"*). `generate_opportunities`
keeps its list-returning signature so no existing caller or test changes.

Intel cards also gain the verbatim source snippet, so a card can be trusted or dismissed without
leaving the page.

**Verify:** Intel → Generate Opportunities → a summary line appears and cards render with quotes.

---

## What Jack should see when this is done

1. **Review** holds ~10 fuzzy expiration dates, each with a pre-filled clean date to confirm.
2. Confirm them → **Intel** shows lease-expiring cards for the December 2026 / January 2027 /
   February 2027 / March 2027 tenants, ranked with the 6–9 month band on top.
3. **Intel** additionally shows ~22 stated-requirement cards — tenants who told you what they want
   and never got followed up.
4. Accept / Reject / Defer still records a reason on every one, exactly as before.

## Found while building — a second, hidden bug

The dry run surfaced a defect the empty Intel tab had been masking. `_upsert_opportunity` and
`_upsert_signal` look up an existing row by `dedup_key` before inserting, but the session runs with
`autoflush=False`, so rows added *earlier in the same run* were invisible to that lookup. One fact
stated in three notes (the live database holds three `"~February 2027"` rows for one tenant) wrote
**three duplicate opportunity rows** instead of updating one. Nobody could see it before, because
the engine never produced any rows at all.

Fixed with an explicit `db.flush()` in both upserts, plus a returned-list guard so the same card
can't be handed back twice. Covered by
`test_the_same_fact_stated_twice_is_one_card`.

## Out of scope (deliberately)

- Rolling `entity_type='activity_log'` facts up onto company records. 606 of 748 facts hang off a
  note rather than a company, so the same tenant across five notes stays five islands. Real problem,
  bigger change, separate plan.
- Anything from `NEVER_FORGET_BUILD_PLAN.md` (follow-ups, dossiers).
- Property-side and match-scoring work — frozen.
