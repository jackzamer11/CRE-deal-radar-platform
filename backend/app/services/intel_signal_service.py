"""Phase D signal engine — date math only, no ML, no LLM.

Reads verified/unverified observations, detects three v1 signal types, and turns
them into ranked `intel_opportunities` with plain-English, template-based
rationale. Re-running the generator is idempotent: it never creates a second
open opportunity for the same (entity, signal) key.
"""

import calendar
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.intel import IntelOpportunity, IntelSignal
from app.models.observation import Observation


# ── Tunable scoring weights (read and edit these freely) ─────────────────────
# Base weight per signal type. lease_expiring (verified) always outranks
# expiration_unverified because the base gap (100 vs 60) exceeds the maximum
# urgency bonus (39) — so a verified fact never loses to an unverified one no
# matter how close its date.
SIGNAL_BASE_WEIGHT = {
    "lease_expiring": 100,          # verified expiration in the window — actionable
    "expiration_unverified": 60,    # extracted but unverified — "verify first"
    "stated_requirement": 55,       # tenant said what they want — chase it
    "stale_data": 30,               # processed lease with missing/unverified fields
}

# Extra points for timing/specificity, capped below the 40-point base-weight gap
# so a verified fact can never lose to an unverified one.
URGENCY_MAX = 39

# Only leases expiring within this horizon surface as opportunities.
EXPIRY_HORIZON_DAYS = 365

# The window the business actually wants (CLAUDE.md: 6-9 months pre-expiry, ahead
# of competing brokers). Full points inside the band, tapering to zero on both
# sides — a lease expiring next week is a tenant who has already re-signed or
# already has three brokers on them, and one 12 months out is too early to help.
WINDOW_PEAK_START_DAYS = 180
WINDOW_PEAK_END_DAYS = 270
WINDOW_TOO_LATE_DAYS = 45       # below this, timing adds nothing

# The five core lease fields — a lease is "stale" if any is null & unverified.
CORE_LEASE_FIELDS = [
    "tenant_name",
    "premises_sqft",
    "commencement_date",
    "expiration_date",
    "base_rent_annual",
]

# Requirement fields that make a tenant worth calling on their own. Contact name
# and email are not requirements — knowing someone's email says nothing about
# whether they need space.
SPECIFIC_REQUIREMENT_FIELDS = {
    "req_sf_min",
    "req_sf_max",
    "req_budget_max_psf",
    "req_timing",
    "req_must_haves",
    "req_ti_expectation",
    "req_buildout_willingness",
    "req_lease_term_years",
}

# Softer requirement fields — real signal, but not enough on their own.
SUPPORTING_REQUIREMENT_FIELDS = {
    "req_submarkets",
    "req_space_type",
    "req_access_needs",
}

ALL_REQUIREMENT_FIELDS = SPECIFIC_REQUIREMENT_FIELDS | SUPPORTING_REQUIREMENT_FIELDS

# A stated-requirement card needs this many distinct requirement fields, at least
# one of them specific. Two soft fields ("Arlington", "office") describe a note,
# not a requirement, and flood the list.
MIN_REQUIREMENT_FIELDS = 2

# How long a stated requirement can sit untouched before it is worth resurfacing.
REQUIREMENT_STALE_DAYS = 30


# ── Date parsing ─────────────────────────────────────────────────────────────
# Extraction stores the model's verbatim value. Real notes produce hedged,
# part-precision text: "~February 2027 (6 months from note date of 2026-08-17)",
# "End of 2026", "2 years from note date (approx. 2028-06)". Before this existed
# every one of those was dropped silently and Intel produced nothing at all.

@dataclass
class ParsedExpiry:
    """A date read out of freeform text, plus how precise the text really was.

    `precision` is deliberately preserved: an "exact" date can be trusted
    straight through, anything coarser is a *suggestion for a human to confirm*
    — never a fact the system asserts on its own.
    """

    date: Optional[date] = None
    precision: Optional[str] = None    # exact | month | quarter | year
    normalized: Optional[str] = None   # ISO form of `date`

    def __bool__(self) -> bool:
        return self.date is not None


_EXACT_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y",
    "%B %d, %Y", "%b %d, %Y",        # January 17, 2027 / Jan 17, 2027
    "%B %d %Y", "%b %d %Y",          # same without the comma
    "%d %B %Y", "%d %b %Y",          # 17 January 2027
)

_MONTH_FORMATS = (
    "%B %Y", "%b %Y",                # February 2027 / Feb 2027
    "%B, %Y", "%b, %Y",
    "%Y-%m", "%Y/%m", "%m/%Y",
)

# Hedges the extractor carries over from the note. They hold no date information,
# and leaving them in front of an otherwise-parseable value is what killed most
# of the rows in the live database.
_HEDGE_RE = re.compile(
    r"^\s*(?:[~\u2248]+|approx\.?|approximately|around|about|circa|ca\.?|est\.?|"
    r"estimated|roughly|maybe|likely|probably|expected|sometime\s+in|in|by|on|"
    r"or\s+so)\s*",
    re.IGNORECASE,
)

_QUARTER_RE = re.compile(r"\bQ([1-4])\s*,?\s*(?:of\s+)?(\d{4})\b", re.IGNORECASE)
_YEAR_PART_RE = re.compile(
    r"\b(end|late|mid|middle|early|start|beginning)\s+(?:of\s+)?(\d{4})\b",
    re.IGNORECASE,
)
_BARE_YEAR_RE = re.compile(r"^(\d{4})$")

# Where a coarse period resolves to. Leases end at month end, so month precision
# takes the LAST day of the month rather than the first.
_YEAR_PART_MONTH = {
    "early": 3, "start": 3, "beginning": 3,
    "mid": 6, "middle": 6,
    "late": 12, "end": 12,
}


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _built(value: date, precision: str) -> "ParsedExpiry":
    return ParsedExpiry(date=value, precision=precision, normalized=value.isoformat())


def _strip_hedges(text: str) -> str:
    """Peel leading hedge words off, repeatedly ("~approx. February 2027")."""
    cleaned = text.strip().strip("\"'")
    while True:
        stripped = _HEDGE_RE.sub("", cleaned, count=1)
        if stripped == cleaned:
            return stripped.strip().strip(".,;: ")
        cleaned = stripped


def _split_parenthetical(text: str) -> Tuple[str, List[str]]:
    """Return (text outside parentheses, [text inside each parenthetical]).

    Order matters downstream: the value the extractor asserts sits OUTSIDE the
    parentheses, and the parenthetical is usually the *note date* it reasoned
    from — "~February 2027 (6 months from note date of 2026-08-17)". Reading the
    inside first would resolve that to 2026-08-17, a year wrong.
    """
    inner = re.findall(r"\(([^)]*)\)", text)
    outer = re.sub(r"\([^)]*\)", " ", text)
    return outer.strip(), [i.strip() for i in inner]


def _parse_candidate(text: str) -> "ParsedExpiry":
    """Parse one candidate string — no recursion, no parenthetical handling."""
    s = _strip_hedges(text)
    if not s:
        return ParsedExpiry()

    for fmt in _EXACT_FORMATS:
        try:
            return _built(datetime.strptime(s, fmt).date(), "exact")
        except ValueError:
            continue

    for fmt in _MONTH_FORMATS:
        try:
            parsed = datetime.strptime(s, fmt).date()
            return _built(_end_of_month(parsed.year, parsed.month), "month")
        except ValueError:
            continue

    quarter = _QUARTER_RE.search(s)
    if quarter:
        return _built(_end_of_month(int(quarter.group(2)), int(quarter.group(1)) * 3),
                      "quarter")

    year_part = _YEAR_PART_RE.search(s)
    if year_part:
        month = _YEAR_PART_MONTH[year_part.group(1).lower()]
        return _built(_end_of_month(int(year_part.group(2)), month), "year")

    bare_year = _BARE_YEAR_RE.match(s)
    if bare_year:
        year = int(bare_year.group(1))
        if 1990 <= year <= 2100:
            return _built(date(year, 12, 31), "year")

    return ParsedExpiry()


# Date shapes worth hunting for inside a longer sentence, most precise first.
_EMBEDDED_PATTERNS = (
    r"\d{4}-\d{1,2}-\d{1,2}",
    r"\d{1,2}/\d{1,2}/\d{4}",
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}",
    r"[A-Za-z]{3,9}\s+\d{4}",
    r"\d{4}-\d{1,2}",
)


def _scan_embedded(text: str) -> "ParsedExpiry":
    """Find a date inside a longer phrase ("expires December 2026 per tenant")."""
    for pattern in _EMBEDDED_PATTERNS:
        for match in re.finditer(pattern, text):
            parsed = _parse_candidate(match.group(0))
            if parsed:
                return parsed
    return ParsedExpiry()


def parse_expiry(value: Optional[str]) -> "ParsedExpiry":
    """Read a lease expiration out of whatever the extractor stored.

    Handles the verbatim forms real notes produce — hedged ("~February 2027"),
    part-precision ("December 2026", "Q1 2027", "End of 2026") and reasoned-out
    ("2 years from note date (approx. 2028-06)"). Returns an empty ParsedExpiry
    when the text genuinely carries no date; it never invents a year.
    """
    if not value:
        return ParsedExpiry()
    text = str(value).strip()
    if not text:
        return ParsedExpiry()

    outer, inner = _split_parenthetical(text)
    for candidate in [text, outer, *inner]:
        if not candidate:
            continue
        parsed = _parse_candidate(candidate)
        if parsed:
            return parsed

    # Last resort: a date embedded in a longer sentence. Outside-first, again.
    for candidate in [outer, *inner]:
        if not candidate:
            continue
        parsed = _scan_embedded(candidate)
        if parsed:
            return parsed
    return ParsedExpiry()


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Back-compatible wrapper — the date only, precision discarded."""
    return parse_expiry(value).date


def _window_bonus(days_to_expiry: int) -> int:
    """Timing points, peaking across the 6-9 month pre-expiry window.

    Deliberately NOT "sooner is better". A lease expiring in three weeks belongs
    to a tenant who has already re-signed or already has competing brokers on
    them; the one worth a call today expires in roughly seven months. Points ramp
    up from WINDOW_TOO_LATE_DAYS, sit at full value across the band, and taper
    back to zero at the horizon.
    """
    days = max(0, days_to_expiry)
    if days < WINDOW_TOO_LATE_DAYS:
        # Too late to get ahead of it — no timing bonus, but still worth surfacing.
        return 0
    if days < WINDOW_PEAK_START_DAYS:
        span = WINDOW_PEAK_START_DAYS - WINDOW_TOO_LATE_DAYS
        return round((days - WINDOW_TOO_LATE_DAYS) / span * URGENCY_MAX)
    if days <= WINDOW_PEAK_END_DAYS:
        return URGENCY_MAX
    if days >= EXPIRY_HORIZON_DAYS:
        return 0
    span = EXPIRY_HORIZON_DAYS - WINDOW_PEAK_END_DAYS
    return round((EXPIRY_HORIZON_DAYS - days) / span * URGENCY_MAX)


def _active_observations(db: Session) -> List[Observation]:
    """Non-superseded observations only (the current view of each fact)."""
    return db.query(Observation).filter(Observation.superseded_by_id.is_(None)).all()


def _first_value(active: List[Observation], entity_type: str, entity_id: int,
                 field: str) -> Optional[str]:
    """Best value for a field on an entity, preferring verified rows."""
    rows = [
        o for o in active
        if o.entity_type == entity_type and o.entity_id == entity_id
        and o.field == field and o.value
    ]
    rows.sort(key=lambda o: (not o.human_verified,))
    return rows[0].value if rows else None


def _tenant_label(db: Session, entity_type: str, entity_id: int, active: List[Observation]) -> str:
    """A label a human can actually recognize.

    Prefers the tenant's company name; falls back to the contact person named in
    the note (activity-log facts carry `contact_name`, not `tenant_name`), and
    qualifies with the space type when known — "Hoda Murad (home health care)"
    beats "activity_log #188". Generic entity ref is the last resort.
    """
    name = (
        _first_value(active, entity_type, entity_id, "tenant_name")
        or _first_value(active, entity_type, entity_id, "contact_name")
    )
    if not name:
        return f"{entity_type} #{entity_id}"
    space = _first_value(active, entity_type, entity_id, "req_space_type")
    return f"{name} ({space})" if space else name


def _upsert_signal(db: Session, entity_type: str, entity_id: int, signal_type: str,
                   value: Optional[str], evidence_id: Optional[int]) -> IntelSignal:
    """Keep one signal row per (entity, signal_type); refresh it on re-run."""
    # Sessions here run with autoflush off, so rows added earlier in this same
    # run are invisible to the query below until flushed — without this, one
    # fact stated in three notes writes three rows instead of updating one.
    db.flush()
    existing = (
        db.query(IntelSignal)
        .filter(
            IntelSignal.entity_type == entity_type,
            IntelSignal.entity_id == entity_id,
            IntelSignal.signal_type == signal_type,
        )
        .first()
    )
    if existing:
        existing.value = value
        existing.detected_at = datetime.utcnow()
        existing.evidence_observation_id = evidence_id
        return existing
    signal = IntelSignal(
        entity_type=entity_type,
        entity_id=entity_id,
        signal_type=signal_type,
        value=value,
        evidence_observation_id=evidence_id,
    )
    db.add(signal)
    return signal


def _upsert_opportunity(db: Session, dedup_key: str, title: str, entity_type: str,
                        entity_id: int, score: float, rationale: str,
                        signals_payload: list) -> Optional[IntelOpportunity]:
    """Create an open opportunity for this key, or refresh an existing open one.

    Idempotent: an already-open row for the same key is updated in place (score,
    rationale, signals) rather than duplicated. Dispositioned (non-open) rows are
    left alone — a human decision is never resurfaced automatically.
    """
    # Same reason as _upsert_signal: make this run's own pending inserts
    # visible, or the dedup_key lookup misses them and duplicates the card.
    db.flush()
    open_existing = (
        db.query(IntelOpportunity)
        .filter(IntelOpportunity.dedup_key == dedup_key, IntelOpportunity.status == "open")
        .first()
    )
    signals_json = json.dumps(signals_payload)
    if open_existing:
        open_existing.title = title
        open_existing.score = score
        open_existing.rationale = rationale
        open_existing.signals_json = signals_json
        return open_existing

    # Don't re-open something already dispositioned for the same key.
    dispositioned = (
        db.query(IntelOpportunity)
        .filter(IntelOpportunity.dedup_key == dedup_key, IntelOpportunity.status != "open")
        .first()
    )
    if dispositioned:
        return None

    opp = IntelOpportunity(
        title=title,
        entity_type=entity_type,
        entity_id=entity_id,
        score=score,
        rationale=rationale,
        signals_json=signals_json,
        dedup_key=dedup_key,
        status="open",
    )
    db.add(opp)
    return opp


def _retire_superseded(db: Session, entity_type: str, entity_id: int, signal_type: str) -> int:
    """Close an open opportunity that a newer, stronger signal has replaced.

    Marked "superseded" rather than a disposition, so it never pollutes the
    accept/reject stats — this is the machine tidying up, not a human decision.
    """
    stale = (
        db.query(IntelOpportunity)
        .filter(
            IntelOpportunity.dedup_key == f"{entity_type}:{entity_id}:{signal_type}",
            IntelOpportunity.status == "open",
        )
        .all()
    )
    for opp in stale:
        opp.status = "superseded"
    return len(stale)


# ── Stated-requirement helpers ───────────────────────────────────────────────

_REQUIREMENT_LABEL = {
    "req_sf_min": "min SF",
    "req_sf_max": "max SF",
    "req_submarkets": "submarkets",
    "req_budget_max_psf": "budget",
    "req_lease_term_years": "term",
    "req_must_haves": "must-haves",
    "req_access_needs": "access",
    "req_buildout_willingness": "buildout",
    "req_ti_expectation": "TI",
    "req_timing": "timing",
    "req_space_type": "space type",
}


def _source_log_id(source_doc: Optional[str]) -> Optional[int]:
    """The activity-log id behind a fact, or None if it came from a document."""
    if not source_doc or not str(source_doc).startswith("activity_log:"):
        return None
    try:
        return int(str(source_doc).split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _last_touch_date(db: Session, rows: List[Observation]) -> Optional[date]:
    """Newest note date behind a set of facts — how long since we last spoke.

    Falls back to the observation's own created_at when the source log is gone,
    so a deleted note degrades to "we know roughly when" rather than crashing.
    """
    log_ids = {lid for lid in (_source_log_id(o.source_doc) for o in rows) if lid}
    dates: List[date] = []
    if log_ids:
        for (log_date,) in (
            db.query(ActivityLog.log_date).filter(ActivityLog.id.in_(log_ids)).all()
        ):
            if log_date:
                dates.append(log_date)
    if not dates:
        dates = [o.created_at.date() for o in rows if o.created_at]
    return max(dates) if dates else None


def _staleness_bonus(days_since_touch: Optional[int]) -> int:
    """Older untouched requirements score higher — they are the ones falling through."""
    if days_since_touch is None:
        return 0
    if days_since_touch <= 0:
        return 0
    capped = min(days_since_touch, REQUIREMENT_STALE_DAYS * 4)
    return round(capped / (REQUIREMENT_STALE_DAYS * 4) * 15)


def _specificity_bonus(field_count: int) -> int:
    """More stated detail = a more real requirement. Capped so it can't run away."""
    return round(min(field_count, 6) / 6 * 24)


def _requirement_summary(active: List[Observation], entity_type: str, entity_id: int,
                         fields: set) -> str:
    """Readable one-liner of what the tenant actually said, specifics first."""
    ordered = [f for f in _REQUIREMENT_LABEL if f in fields]
    parts = []
    for field in ordered:
        value = _first_value(active, entity_type, entity_id, field)
        if value:
            parts.append(f"{_REQUIREMENT_LABEL[field]} {value.strip()}")
    return "; ".join(parts[:6])


def generate_opportunities(db: Session, today: Optional[date] = None) -> List[IntelOpportunity]:
    """Run the signal rules and upsert ranked opportunities. Idempotent.

    Thin wrapper over `generate_with_stats` so existing callers keep the plain
    list they expect.
    """
    return generate_with_stats(db, today=today)[0]


def generate_with_stats(
    db: Session, today: Optional[date] = None,
) -> Tuple[List[IntelOpportunity], Dict[str, object]]:
    """Run the signal rules and report what was actually scanned.

    The stats exist because a generate run that finds nothing used to be
    indistinguishable from a broken button: 748 facts in, empty screen out, no
    way to tell which. Every card that did NOT get made is now accounted for.
    """
    today = today or date.today()
    active = _active_observations(db)
    touched: List[IntelOpportunity] = []
    # Two notes can state the same fact ("~February 2027" recorded three times
    # for one tenant). The upsert correctly returns ONE row each time, so
    # without this the same card is handed back — and counted — repeatedly.
    seen_keys: set = set()
    stats: Dict[str, object] = {
        "facts_scanned": len(active),
        "expirations_found": 0,
        "expirations_unreadable": 0,
        "expirations_past": 0,
        "expirations_beyond_horizon": 0,
        "by_signal_type": {},
    }
    expiry_entities: set = set()

    # ── Rules 1 & 2: expiration within the horizon (verified vs unverified) ──
    for obs in active:
        if obs.field != "expiration_date" or not obs.value:
            continue
        stats["expirations_found"] = int(stats["expirations_found"]) + 1
        parsed = parse_expiry(obs.value)
        exp = parsed.date
        if exp is None:
            stats["expirations_unreadable"] = int(stats["expirations_unreadable"]) + 1
            continue
        days = (exp - today).days
        if days < 0:
            stats["expirations_past"] = int(stats["expirations_past"]) + 1
            continue
        if days > EXPIRY_HORIZON_DAYS:
            stats["expirations_beyond_horizon"] = int(stats["expirations_beyond_horizon"]) + 1
            continue
        expiry_entities.add((obs.entity_type, obs.entity_id))

        tenant = _tenant_label(db, obs.entity_type, obs.entity_id, active)
        source = obs.source_doc or "unknown source"
        page = f" p.{obs.source_page}" if obs.source_page is not None else ""

        if obs.human_verified:
            signal_type = "lease_expiring"
            score = SIGNAL_BASE_WEIGHT[signal_type] + _window_bonus(days)
            title = f"Lease expiring — {tenant}"
            rationale = (
                f"Lease for {tenant} expires in {days} days "
                f"({exp.isoformat()}, source: {source}{page}). "
                "No renewal activity recorded."
            )
            # The fact is verified now, so any open "verify this first" card for
            # the same entity is stale — retire it instead of asking the user to
            # judge the same fact twice under contradictory framing.
            _retire_superseded(db, obs.entity_type, obs.entity_id, "expiration_unverified")
        else:
            signal_type = "expiration_unverified"
            score = SIGNAL_BASE_WEIGHT[signal_type] + _window_bonus(days)
            title = f"Verify lease expiration — {tenant}"
            rationale = (
                f"Possible lease expiration for {tenant} in {days} days "
                f"({exp.isoformat()}) from {source}{page} — verify this fact first."
            )

        _upsert_signal(db, obs.entity_type, obs.entity_id, signal_type, obs.value, obs.id)
        signals_payload = [{
            "signal_type": signal_type,
            "value": obs.value,
            "evidence_observation_id": obs.id,
            "days_to_expiry": days,
            # Lets the UI link straight to the note or document this came from.
            "source_doc": obs.source_doc,
            # The verbatim words behind the card, so it can be trusted or
            # dismissed without leaving the page.
            "source_snippet": obs.source_snippet,
        }]
        dedup_key = f"{obs.entity_type}:{obs.entity_id}:{signal_type}"
        opp = _upsert_opportunity(
            db, dedup_key,
            title, obs.entity_type, obs.entity_id, score, rationale, signals_payload,
        )
        if opp is not None and dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            touched.append(opp)

    # ── Rule 3: stale data — a processed lease with missing/unverified fields ──
    # Only applies to entities that actually had a LEASE DOCUMENT extracted.
    # Facts mined from activity-log notes (source_doc "activity_log:<id>") are
    # conversation intel, not a lease abstract — an entity known only from notes
    # is not an "incomplete lease record" and must not raise this signal.
    entities = {
        (o.entity_type, o.entity_id)
        for o in active
        if o.source_doc and not str(o.source_doc).startswith("activity_log:")
    }
    for entity_type, entity_id in entities:
        rows = [o for o in active if o.entity_type == entity_type and o.entity_id == entity_id]
        missing = []
        for field in CORE_LEASE_FIELDS:
            field_rows = [o for o in rows if o.field == field]
            # Missing if no row, or the current row has no value and isn't verified.
            if not field_rows:
                missing.append(field)
            elif all(o.value is None and not o.human_verified for o in field_rows):
                missing.append(field)
        if not missing:
            continue

        tenant = _tenant_label(db, entity_type, entity_id, active)
        source_rows = [o for o in rows if o.source_doc]
        source = source_rows[0].source_doc if source_rows else "the lease document"
        signal_type = "stale_data"
        score = SIGNAL_BASE_WEIGHT[signal_type]
        title = f"Incomplete lease record — {tenant}"
        rationale = (
            f"Lease {source} was processed but {len(missing)} core field(s) are "
            f"still missing or unverified ({', '.join(missing)}) — review to "
            "complete the record."
        )
        _upsert_signal(db, entity_type, entity_id, signal_type, ", ".join(missing), None)
        signals_payload = [{
            "signal_type": signal_type,
            "missing_fields": missing,
        }]
        opp = _upsert_opportunity(
            db, f"{entity_type}:{entity_id}:{signal_type}",
            title, entity_type, entity_id, score, rationale, signals_payload,
        )
        stale_key = f"{entity_type}:{entity_id}:{signal_type}"
        if opp is not None and stale_key not in seen_keys:
            seen_keys.add(stale_key)
            touched.append(opp)

    # ── Rule 4: stated requirements — the tenant told us what they want ──────
    # The single biggest gap this closes: hundreds of requirement facts mined
    # from call notes previously drove nothing at all, because only
    # `expiration_date` could produce a card.
    by_entity: Dict[Tuple[str, int], List[Observation]] = {}
    for obs in active:
        if obs.field in ALL_REQUIREMENT_FIELDS and obs.value:
            by_entity.setdefault((obs.entity_type, obs.entity_id), []).append(obs)

    for (entity_type, entity_id), rows in by_entity.items():
        fields = {o.field for o in rows}
        # Two soft facts ("Arlington", "office") describe a note, not a
        # requirement — at least one specific ask is required.
        if len(fields) < MIN_REQUIREMENT_FIELDS:
            continue
        if not (fields & SPECIFIC_REQUIREMENT_FIELDS):
            continue
        # A live expiration is strictly more actionable; don't show the same
        # tenant twice under two framings.
        if (entity_type, entity_id) in expiry_entities:
            _retire_superseded(db, entity_type, entity_id, "stated_requirement")
            continue

        last_touch = _last_touch_date(db, rows)
        days_since = (today - last_touch).days if last_touch else None
        score = (
            SIGNAL_BASE_WEIGHT["stated_requirement"]
            + _specificity_bonus(len(fields))
            + _staleness_bonus(days_since)
        )
        tenant = _tenant_label(db, entity_type, entity_id, active)
        summary = _requirement_summary(active, entity_type, entity_id, fields)
        since = (
            f"Last note {days_since} days ago" if days_since is not None
            else "No note date recorded"
        )
        title = f"Stated requirement — {tenant}"
        rationale = (
            f"{tenant} stated: {summary}. {since}; no follow-up recorded."
            if summary else
            f"{tenant} stated a space requirement across {len(fields)} fields. {since}."
        )
        evidence = next((o for o in rows if o.field in SPECIFIC_REQUIREMENT_FIELDS), rows[0])
        _upsert_signal(db, entity_type, entity_id, "stated_requirement",
                       summary or None, evidence.id)
        signals_payload = [{
            "signal_type": "stated_requirement",
            "value": summary or None,
            "evidence_observation_id": evidence.id,
            "stated_fields": sorted(fields),
            "days_since_touch": days_since,
            "source_doc": evidence.source_doc,
            "source_snippet": evidence.source_snippet,
        }]
        req_key = f"{entity_type}:{entity_id}:stated_requirement"
        opp = _upsert_opportunity(
            db, req_key,
            title, entity_type, entity_id, score, rationale, signals_payload,
        )
        if opp is not None and req_key not in seen_keys:
            seen_keys.add(req_key)
            touched.append(opp)

    db.commit()
    for opp in touched:
        db.refresh(opp)

    by_type: Dict[str, int] = {}
    for opp in touched:
        key = (opp.dedup_key or "").rsplit(":", 1)[-1] or "unknown"
        by_type[key] = by_type.get(key, 0) + 1
    stats["by_signal_type"] = by_type
    stats["opportunities"] = len(touched)
    return touched, stats
