# Golden-set test harness

Measures how well the lease extraction pipeline pulls the five core fields —
`tenant_name`, `premises_sqft`, `commencement_date`, `expiration_date`,
`base_rent_annual` — from real lease text, and, most importantly, **whether the
model ever fabricates a value where the fact isn't actually stated.**

## Case format

Each case is a pair of files in `cases/`, sharing a base name:

- `<name>.txt` — a lease excerpt (plain text).
- `<name>.json` — the expected values. **Absent fields must be explicitly
  `null`** — that's what turns a case into a fabrication test.

Example (`missing_base_rent.json`):

```json
{
  "tenant_name": "Harbor Point Design Group, Inc.",
  "premises_sqft": "14200",
  "commencement_date": "2021-09-15",
  "expiration_date": "2026-09-14",
  "base_rent_annual": null
}
```

Numbers are digits-only strings (`"14200"`, not `"14,200 SF"`); dates are
`YYYY-MM-DD`. The harness normalizes formatting before comparing, so the model
may answer `"$340,000"` or `"April 30, 2028"` and still match.

## Starter cases

- `complete` — all five fields clearly stated.
- `missing_base_rent` — base rent genuinely absent (must come back null).
- `ambiguous_dates` — only "term of five (5) years" is given, with **no stated
  expiration date**. The model must return `null` for `expiration_date`, not
  compute commencement + 5 years. (Guessing here is the classic fabrication.)

## Running it

Measure the live model (needs `ANTHROPIC_API_KEY`):

```bash
cd backend
venv\Scripts\activate
python tests/golden/run_golden.py
```

It prints a per-field table, a stated-field accuracy score, and the headline
**FABRICATION COUNT**. Exit code is non-zero if anything was fabricated.

As a test gate:

```bash
pytest tests/golden -v
```

- The two offline tests (harness self-checks) always run.
- `test_live_model_does_not_fabricate` runs the real pipeline and **fails if
  fabrication count > 0** — but is skipped when `ANTHROPIC_API_KEY` is unset, so
  the normal offline test suite stays green.

## Adding your own hand-labeled leases

1. Paste a real lease excerpt into `cases/my_lease.txt`.
2. Create `cases/my_lease.json` with the true values — and set any field the
   document doesn't state to `null`.
3. Re-run `python tests/golden/run_golden.py`. Watch the fabrication count: it
   should stay `0`. If a null field comes back with a value, the model guessed,
   and that's a real defect to investigate.
