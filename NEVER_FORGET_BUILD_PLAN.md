# Never-Forget Build Plan

Extends the Private Intelligence Layer from **capture-and-rank** into **memory-and-recall**:
take every interaction, remember it, and hand the right piece back at the right moment so nothing
is forgotten.

**Status: PROPOSED — not built. Awaiting phase-by-phase approval.**

## Where we are today

The layer already does **Capture**: tenant notes + lease PDFs → 379 structured facts, reviewed and
ranked. What's missing is everything that makes it a memory:

| Pillar | Today | This plan |
|---|---|---|
| Capture (messy input → data) | ✅ Working | unchanged |
| **Never-forget** (open loops resurface) | ❌ `follow_up_action` is a dead-end string | **Phase 1** |
| **Recall** (all I know about a contact, one screen) | ❌ No per-relationship view | **Phase 2** |
| **Pattern recognition** (connections across interactions) | ⚠️ Single-field date math only | **Phase 3** |
| Coverage: agents + owners, not just tenants | ❌ Tenant-side only | **Phase 4 (gated)** |

Design rules carried from CLAUDE.md: any new column routes through `ensure_schema.py`; new tables are
`intel_*` namespaced; the `/api/companies/` contract stays intact; observations stay append-only;
`verified_by` keeps machine vs. human honest.

---

## Phase 1 — Open-Loops engine  *(the never-forget core, highest value)*

**Goal:** every commitment you make resurfaces until it's done. "Call Hoda back," "send the Tysons
owner comps" — nothing falls through.

- **Data:** new `intel_followups` table (namespaced): `entity_type`, `entity_id`, `source_log_id`,
  `description`, `created_date`, `due_date` (nullable), `status` (open/done/dismissed),
  `resolved_by_log_id`, `resolved_at`. Migration added to `ensure_schema.py`.
- **Source:** the `follow_up_action` field already on every activity log. A non-empty value → an open
  follow-up. The miner can additionally pull a due date from text ("call back next week") — optional,
  degrades to no-due-date.
- **Auto-close (the magic):** logging a *newer* interaction with the same entity marks that entity's
  older open follow-ups resolved. The list is always only what you actually owe right now.
- **Backend:** `intel_followup_service` (sync-from-log, resolve-on-new-log, list-open, complete,
  dismiss); routes `GET /api/intel/followups`, `POST /api/intel/followups/{id}/resolve|dismiss`.
  Idempotent backfill over existing logs (newer logs close older ones).
- **Frontend:** an "Open Loops" block on the Daily Briefing, sorted most-overdue first, one-click
  Done / Dismiss. Optional dedicated tab.
- **Tests:** sync creates an open loop; a newer log auto-closes the older; manual complete/dismiss;
  no duplicates on re-sync; backfill idempotent.

---

## Phase 2 — Per-contact recall  *(memory on one screen)*

**Goal:** click a company, see everything the layer knows — every stated fact and every promise —
in one place, so you walk into any call fully briefed.

- **Backend:** `GET /api/intel/company/{id}/dossier` — read-only aggregation, **no new tables**.
  Assembles: latest verified value per requirement field (append-only "newest wins"), a timeline of
  activity logs, and open follow-ups. Provenance links preserved.
- **Frontend:** a Dossier view / drawer on the Companies page; deep-linked from Intel cards.
- **Tests:** newest-verified-value-wins per field; dossier includes open follow-ups and full timeline.

---

## Phase 3 — Pattern recognition  *(connections across interactions)*

**Goal:** surface patterns a human loses across hundreds of notes. Start deterministic and
explainable before anything fuzzy.

- **Patterns v1 (concrete, no LLM guesswork):**
  1. **Expiry clusters** — several tenants in one submarket expiring in the same window → a
     canvassing opportunity.
  2. **Going-cold** — a high-priority tenant with no interaction in N days → nudge before a competitor.
  3. **Contradiction flags** — a stated fact (e.g. SF) that changed between calls → route to Review.
- **Design:** a periodic pass emitting new `IntelSignal` / `IntelOpportunity` types, each with plain
  reasoning. LLM-based fuzzy patterns only after these prove out.
- **Tests:** cluster detection, going-cold detection, contradiction flagging.

---

## Phase 4 — Coverage: agents & property owners  *(GATED — scope decision required)*

**Goal:** run the same capture → recall → never-forget machinery for agents and owners, not just
tenants — matching the full vision.

- **Blocker:** CLAUDE.md freezes the agent- and owner-side as **dormant**. This phase crosses that
  line. It needs an explicit scope decision and a CLAUDE.md update **before** any work.
- **Design sketch:** add a relationship/contact-type dimension so interactions carry who they're with;
  the Phases 1–3 surfaces then filter by relationship. No throwaway work — the earlier phases are
  built relationship-agnostic so this is additive.

---

## Future capture sources (not a phase yet)

Auto-ingesting interactions from email would deepen "capture every interaction." Your **Outlook (M365)
is already connected**; **Gmail is not yet authorized** (needs enabling in claude.ai connector
settings). Noted as a direction, not scheduled.

---

## Recommended order

**Phase 1 → 2 → 3**, with **Phase 4 gated** on your scope call. Phase 1 alone delivers the core of
"don't let me forget anything" and is mostly retrieval + UI on data you're already capturing.
