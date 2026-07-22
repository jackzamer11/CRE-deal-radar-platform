# Private Intelligence Layer — Deliverable Summary

A local, single-user layer on top of Deal Radar that turns lease documents into
verified facts, ranked opportunities, and a captured accept/reject feedback loop.

Flow: **upload lease → extract facts → review/verify → generate opportunities →
disposition (accept/reject/defer) with a reason.**

## Phases built

| Phase | What it does | Key files |
|---|---|---|
| A — Observations | Append-only facts (entity/field/value/confidence/source), verify-creates-new-superseding-row | `models/observation.py`, `api/routes/observations.py` |
| B — Extraction | Anthropic structured JSON output for 5 lease fields; returns null when unstated (no fabrication) | `services/document_extraction_service.py`, `api/routes/documents.py` |
| C — Review queue | UI to confirm/correct low-confidence facts; upload a lease PDF to extract facts into the queue | `frontend/src/pages/Review.tsx` |
| D — Signal engine + opportunities ("Intel") | Date-math signals → ranked opportunities with plain-English rationale | `models/intel.py`, `services/intel_signal_service.py`, `api/routes/intel.py`, `frontend/src/pages/Intel.tsx` |
| E — Feedback loop | Accept/reject/defer + reason; History; standing-rule capture | `services/intel_feedback_service.py`, `models/intel.py` |
| F — Golden-set harness | Runs the real pipeline against labeled leases; headlines a fabrication count; pytest gate fails if > 0 | `tests/golden/` (`run_golden.py`, `cases/`, `README.md`) |

## Three numbers to watch weekly
- **Facts awaiting review** — `GET /api/observations/?human_verified=false`
- **Opportunities accepted rate** — accepted vs. total in `GET /api/intel/history`
- **Fabrication count on the golden set** — `python tests/golden/run_golden.py`
  (last run: 0 fabrications, 13/13 stated-field accuracy on the 3 starter cases)

## Known gaps / not-yet-built

- **Saved standing rules do not affect the platform yet (write-only stub).**
  When the same durable-policy rejection reason recurs, Phase E suggests saving
  it and stores it in `intel_criteria` (`GET /api/intel/criteria`). But the
  signal engine (`intel_signal_service.py`) never reads that table, so a saved
  rule does **not** filter, suppress, down-rank, or flag any opportunity. A
  matching opportunity still surfaces in the open queue and must be rejected
  again by hand. This is intentional for v1 — the plan specifies `criteria` as a
  stub whose only job is capture. Making rules *act* is deferred because it
  needs either structured criteria fields (e.g. `max_sf`) or an LLM match
  judge, both of which carry real correctness risk.
- **Rule matching is exact-text (v1).** "No deals under 5,000 SF" and
  "No deals under 5000 SF" are treated as different rules — duplicates are
  possible.
- **Golden set is small (3 synthetic cases).** The fabrication gate is only as
  strong as its cases; add real hand-labeled leases per `tests/golden/README.md`
  to harden it.
