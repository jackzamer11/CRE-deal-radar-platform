"""Phase D signal engine — date math only, no ML, no LLM.

Reads verified/unverified observations, detects three v1 signal types, and turns
them into ranked `intel_opportunities` with plain-English, template-based
rationale. Re-running the generator is idempotent: it never creates a second
open opportunity for the same (entity, signal) key.

Three rules about time and place, added once the Activity Log became per-contact
threads and it was clear this runs for years, not weeks:

  * **A fact belongs to the company its note is about — now.** An activity-log
    fact is filed under whatever company its source entry is stamped to at the
    moment the generator runs, not whatever it was filed under when mined. Move
    an entry and its facts follow; archive it and they drop out.
  * **One lease date per company, from the most trustworthy source.** A
    confirmed lease beats a date Jack entered, which beats a note he verified,
    and so on down to CoStar (see RANK_*). Disagreement is shown on the card,
    never silently resolved.
  * **A decision is scoped to what was decided.** Accepting or rejecting a
    lease card covers that lease cycle only; the next lease is a new card. A
    deferral comes back on its date. Only "not a tenant" is permanent.
"""

import calendar
import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import NOT_A_TENANT, IntelFeedback, IntelOpportunity, IntelSignal
from app.models.lease import Lease
from app.models.observation import Observation
from app.services.requirement_subject import (
    ABOUT_DISMISSED, ABOUT_MARKET, ABOUT_NAMED, ABOUT_UNASSIGNED, NameResolver,
    counterparty_side_log_ids,
)


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


# ── Where each lease date comes from, most trustworthy first ─────────────────
# Lower wins. A tenant's own words (a note) outrank CoStar: the platform's whole
# premise is that what a tenant says on a call beats what a database inferred.
# A hedged note date still wins over CoStar, but only as a "verify first" card.
RANK_CONFIRMED_LEASE = 1   # the current lease on file, confirmed by Jack
RANK_RECORD_CONFIRMED = 2  # the company's expiry, typed or confirmed by Jack
RANK_NOTE_VERIFIED = 3     # a note fact Jack verified in Review
RANK_NOTE_EXACT = 4        # a note fact stated as an exact date (auto-cleared)
RANK_NOTE_HEDGED = 5       # a note fact the tenant hedged — verify first
RANK_RECORD_OTHER = 6      # CoStar, SEC filing, public record

# Company.lease_expiry_source values that mean Jack put the date there himself,
# or accepted it from a lease or a conversation.
CONFIRMED_RECORD_SOURCES = {"lease_document", "manual", "landlord_confirmed", "conversation"}

_RECORD_SOURCE_LABEL = {
    "lease_document": "company record (from the lease)",
    "manual": "company record (entered by you)",
    "landlord_confirmed": "company record (landlord-confirmed)",
    "conversation": "company record (confirmed from email)",
    "costar": "CoStar",
    "sec_filing": "SEC filing",
    "public_record": "public record",
}

# Two lease dates this close together are the same lease, restated or pinned
# down ("~Feb 2027", then "2027-03-01"). Further apart, they are different
# leases. Leases run for years, so six months separates cycles comfortably.
SAME_CYCLE_MONTHS = 6

# Sources further apart than this are called out on the card as a conflict.
CONFLICT_DAYS = 45

LEASE_SIGNALS = {"lease_expiring", "expiration_unverified"}


@dataclass
class _ExpiryCandidate:
    """One source's claim about when an entity's lease ends."""

    date: date
    rank: int
    label: str
    precision: Optional[str] = None
    obs: Optional[Observation] = None
    # Newest-first tie-break within a rank: a restated date supersedes.
    stated_on: Optional[date] = None

    @property
    def verified(self) -> bool:
        return self.rank != RANK_NOTE_HEDGED


@dataclass
class _Context:
    """Everything the rules need besides the facts themselves, loaded once."""

    # obs.id -> the (entity_type, entity_id) it belongs to now.
    entity_of: Dict[int, Tuple[str, int]] = dc_field(default_factory=dict)
    # activity-log id -> its log_date, and the name of the person it is on.
    log_date: Dict[int, date] = dc_field(default_factory=dict)
    log_contact: Dict[int, str] = dc_field(default_factory=dict)
    companies: Dict[int, Company] = dc_field(default_factory=dict)
    current_lease: Dict[int, Lease] = dc_field(default_factory=dict)
    # company id -> the types of everyone on file who works there now.
    staff_types: Dict[int, Set[str]] = dc_field(default_factory=dict)
    # Everyone who works at each company, and the person on each entry — for
    # holding a card while its tenant is being worked (see _hold_reason).
    people: Dict[int, List[Contact]] = dc_field(default_factory=dict)
    log_contact_id: Dict[int, int] = dc_field(default_factory=dict)
    contacts_by_id: Dict[int, Contact] = dc_field(default_factory=dict)
    # (entity_type, entity_id) -> (date, channel, person) of the newest real
    # entry: what a card says instead of claiming "no follow-up recorded".
    last_touch: Dict[Tuple[str, int], Tuple[date, Optional[str], Optional[str]]] = \
        dc_field(default_factory=dict)


def _chunks(items: List[int], size: int = 500) -> Iterable[List[int]]:
    """IN-lists in slices — SQLite caps bound parameters per statement."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _active_observations(db: Session) -> List[Observation]:
    """Non-superseded observations only (the current view of each fact)."""
    return db.query(Observation).filter(Observation.superseded_by_id.is_(None)).all()


def _load_context(db: Session, active: List[Observation]) -> Tuple[_Context, List[Observation]]:
    """Resolve where every fact belongs now, and drop facts from archived notes.

    Returns the context and the facts still in play. A note-sourced fact is
    re-filed under the company its entry is stamped to — falling back to the
    entry's legacy company link, then its contact's current company. A fact
    whose entry carries no company at all keeps the entity it was mined under,
    and so does one whose entry no longer exists. Document facts never move.

    A fixed number of queries whatever the fact count: logs, contacts,
    companies and leases are each fetched in bulk.
    """
    ctx = _Context()
    log_ids = sorted({lid for lid in (_source_log_id(o.source_doc) for o in active) if lid})
    logs: Dict[int, tuple] = {}
    for chunk in _chunks(log_ids):
        for row in (
            db.query(
                ActivityLog.id, ActivityLog.company_stamp_id, ActivityLog.company_id,
                ActivityLog.contact_id, ActivityLog.archived, ActivityLog.log_date,
            )
            .filter(ActivityLog.id.in_(chunk))
            .all()
        ):
            logs[row[0]] = row

    contact_ids = sorted({row[3] for row in logs.values() if row[3]})
    contacts: Dict[int, tuple] = {}
    for chunk in _chunks(contact_ids):
        for cid, name, company_id in (
            db.query(Contact.id, Contact.name, Contact.company_id)
            .filter(Contact.id.in_(chunk))
            .all()
        ):
            contacts[cid] = (name, company_id)

    resolver: Optional[NameResolver] = None
    # People Jack attached a requirement to: (type, company) for each.
    attached_ids = sorted({o.assigned_contact_id for o in active if o.assigned_contact_id})
    attached: Dict[int, Tuple[Optional[str], Optional[int]]] = {}
    for chunk in _chunks(attached_ids):
        for cid, ctype, company_id in (
            db.query(Contact.id, Contact.contact_type, Contact.company_id)
            .filter(Contact.id.in_(chunk))
            .all()
        ):
            attached[cid] = (ctype, company_id)
    # Facts mined before `about` existed carry no subject. Those from a
    # counterparty's own firm wait for a tenant, exactly as a new one would.
    legacy_held = counterparty_side_log_ids(db, [
        lid for lid in (_source_log_id(o.source_doc) for o in active if o.about is None)
        if lid
    ])

    kept: List[Observation] = []
    for obs in active:
        # Who the fact is about comes before where its note is filed. What a
        # broker said about the market, and what Jack dismissed, drive nothing.
        about = obs.about
        if about in (ABOUT_MARKET, ABOUT_DISMISSED):
            continue
        entity = (obs.entity_type, obs.entity_id)
        lid = _source_log_id(obs.source_doc)
        row = logs.get(lid) if lid else None
        if row is not None:
            _, stamp, legacy_company, contact_id, archived, log_date = row
            if archived:
                continue   # noise Jack put away — it drives nothing
            contact = contacts.get(contact_id) if contact_id else None
            company_id = stamp or legacy_company or (contact[1] if contact else None)
            if company_id:
                entity = ("company", company_id)
            if log_date:
                ctx.log_date[lid] = log_date
            if contact:
                ctx.log_contact[lid] = contact[0]

        if obs.assigned_company_id:
            entity = ("company", obs.assigned_company_id)   # Jack's answer wins
        elif obs.assigned_contact_id:
            # Attached to a person: a tenant at a company joins that company's
            # card; a counterparty, or someone with no company, keeps it on
            # their own page. Intel stays tenant cards only.
            ctype, owner_company = attached.get(obs.assigned_contact_id, (None, None))
            if ctype == "counterparty" or not owner_company:
                continue
            entity = ("company", owner_company)
        elif about == ABOUT_UNASSIGNED or (about is None and lid in legacy_held):
            continue   # waits in Review's holding list
        elif about == ABOUT_NAMED:
            resolver = resolver or NameResolver(db)
            named = resolver.resolve(obs.about_name)
            if named is None:
                continue   # no single company fits the name — holding list
            entity = ("company", named)
        ctx.entity_of[obs.id] = entity
        kept.append(obs)

    company_ids = sorted({eid for et, eid in ctx.entity_of.values() if et == "company"})
    for chunk in _chunks(company_ids):
        for company in db.query(Company).filter(Company.id.in_(chunk)).all():
            ctx.companies[company.id] = company
        for lease in (
            db.query(Lease)
            .filter(
                Lease.company_id.in_(chunk),
                Lease.is_current.is_(True),
                Lease.confirmed_at.isnot(None),
                Lease.expiration_date.isnot(None),
            )
            .all()
        ):
            ctx.current_lease[lease.company_id] = lease
        for person in db.query(Contact).filter(Contact.company_id.in_(chunk)).all():
            ctx.staff_types.setdefault(person.company_id, set()).add(person.contact_type or "")
            ctx.people.setdefault(person.company_id, []).append(person)
            ctx.contacts_by_id[person.id] = person

    # The person on each entry, for cards that belong to no company.
    for lid, row in logs.items():
        if row[3]:
            ctx.log_contact_id[lid] = row[3]
    missing = sorted(set(ctx.log_contact_id.values()) - set(ctx.contacts_by_id))
    for chunk in _chunks(missing):
        for person in db.query(Contact).filter(Contact.id.in_(chunk)).all():
            ctx.contacts_by_id[person.id] = person

    _load_last_touches(db, ctx, company_ids)
    return ctx, kept


def _real_entry_filters():
    """An entry that is a conversation: not a copy, not a divider, not noise."""
    return (
        ActivityLog.participation.isnot(True),
        ActivityLog.action_type != "STAGE_CHANGE",
        ActivityLog.archived.isnot(True),
    )


def _load_last_touches(db: Session, ctx: _Context, company_ids: List[int]) -> None:
    """The newest real entry per company (by the company it was stamped to),
    and per person for cards that belong to no company."""
    def newest(rows, key_of):
        for key, when, lid, channel, contact_id in rows:
            if key is None or when is None:
                continue
            k = key_of(key)
            seen = ctx.last_touch.get(k)
            if seen is None or (when, lid) > (seen[0], seen[3]):
                person = ctx.contacts_by_id.get(contact_id) if contact_id else None
                ctx.last_touch[k] = (when, channel, person.name if person else None, lid)

    for chunk in _chunks(company_ids):
        newest(
            db.query(ActivityLog.company_stamp_id, ActivityLog.log_date, ActivityLog.id,
                     ActivityLog.channel, ActivityLog.contact_id)
            .filter(ActivityLog.company_stamp_id.in_(chunk), *_real_entry_filters())
            .all(),
            lambda cid: ("company", cid),
        )
    # Entry-level cards: the person on that entry, across all their entries.
    by_person: Dict[int, List[int]] = {}
    for lid, cid in ctx.log_contact_id.items():
        by_person.setdefault(cid, []).append(lid)
    person_touch: Dict[int, tuple] = {}
    for chunk in _chunks(sorted(by_person)):
        for cid, when, lid, channel, _ in (
            db.query(ActivityLog.contact_id, ActivityLog.log_date, ActivityLog.id,
                     ActivityLog.channel, ActivityLog.contact_id)
            .filter(ActivityLog.contact_id.in_(chunk), *_real_entry_filters())
            .all()
        ):
            seen = person_touch.get(cid)
            if when and (seen is None or (when, lid) > (seen[0], seen[3])):
                person = ctx.contacts_by_id.get(cid)
                person_touch[cid] = (when, channel, person.name if person else None, lid)
    for cid, lids in by_person.items():
        if cid in person_touch:
            for lid in lids:
                ctx.last_touch[("activity_log", lid)] = person_touch[cid]


# ── Holding a card while its tenant is being worked ──────────────────────────
# A card is a prompt to call. It is noise while the deal is already in play,
# while the tenant said when to come back, or while they said no — for a
# while. Held cards are not decisions: they come back by themselves when the
# reason runs out. None of this applies to a past client.
HOLD_DORMANT_DAYS = 90
HOLD_NOT_INTERESTED_DAYS = 365
PAST_CLIENT_BONUS = 10


def _people_for(ctx: _Context, entity_type: str, entity_id: int) -> List[Contact]:
    if entity_type == "company":
        people = ctx.people.get(entity_id, [])
    else:
        cid = ctx.log_contact_id.get(entity_id)
        people = [ctx.contacts_by_id[cid]] if cid in ctx.contacts_by_id else []
    return [p for p in people if p.contact_type != "counterparty"]


def _hold_reason(people: List[Contact], today: date) -> Tuple[Optional[str], bool]:
    """(why this card waits, or None; whether a past client is among them)."""
    past = any(p.is_past_client or p.stage == "Closed" for p in people)
    if not people:
        return None, past
    for p in people:
        if p.stage == "In Play":
            return f"In Play with {p.name}", past
    for p in people:
        if p.next_touch_date and p.next_touch_date > today:
            d = p.next_touch_date
            return f"{p.name} is due {d:%b} {d.day}", past
    if past:
        return None, past
    quiet = [p for p in people if p.stage in ("Not Interested", "Dormant")]
    if len(quiet) != len(people):
        return None, past
    for p in quiet:
        if p.next_touch_date and p.next_touch_date <= today:
            return None, past   # the date they gave has come
        span = HOLD_DORMANT_DAYS if p.stage == "Dormant" else HOLD_NOT_INTERESTED_DAYS
        if today >= (p.stage_changed_at or today) + timedelta(days=span):
            return None, past   # long enough ago to ask again
    return f"{quiet[0].name} is {quiet[0].stage}", past


def _touch_line(ctx: _Context, entity_type: str, entity_id: int, today: date) -> str:
    """The card's claim about follow-up, read off the timeline — never assumed."""
    touch = ctx.last_touch.get((entity_type, entity_id))
    if touch is None:
        return " No conversation logged yet."
    when, channel, person = touch[0], touch[1], touch[2]
    how = channel if channel and channel != "other" else "entry"
    who = f", {person}" if person else ""
    ago = (today - when).days
    return f" Last touch {when:%b} {when.day} ({how}{who}), {ago} day{'s' if ago != 1 else ''} ago."


def _group(ctx: _Context, facts: List[Observation]) -> Dict[Tuple[str, int], List[Observation]]:
    out: Dict[Tuple[str, int], List[Observation]] = {}
    for obs in facts:
        out.setdefault(ctx.entity_of[obs.id], []).append(obs)
    return out


def _first_value(rows: List[Observation], field: str) -> Optional[str]:
    """Best value for a field among one entity's facts, preferring verified rows."""
    matches = [o for o in rows if o.field == field and o.value]
    matches.sort(key=lambda o: (not o.human_verified,))
    return matches[0].value if matches else None


def _tenant_label(ctx: _Context, entity_type: str, entity_id: int,
                  rows: List[Observation]) -> str:
    """A label a human can actually recognize.

    The company's own name when the facts belong to a company on record.
    Otherwise the tenant or contact name the note gave, qualified with the space
    type — "Hoda Murad (home health care)" beats "activity_log #188". The
    generic entity ref is the last resort.
    """
    company = ctx.companies.get(entity_id) if entity_type == "company" else None
    if company is not None and company.name:
        return company.name
    name = _first_value(rows, "tenant_name") or _first_value(rows, "contact_name")
    if not name:
        return f"{entity_type} #{entity_id}"
    space = _first_value(rows, "req_space_type")
    return f"{name} ({space})" if space else name


def _contact_for(ctx: _Context, obs: Optional[Observation],
                 rows: Iterable[Observation] = ()) -> Optional[str]:
    """The person on the entry a fact came from — or, when the fact did not
    come from a note (a lease, the company record), the person on the newest
    note behind the card."""
    lid = _source_log_id(obs.source_doc) if obs is not None else None
    if lid and lid in ctx.log_contact:
        return ctx.log_contact[lid]
    newest = None
    for row in rows:
        rid = _source_log_id(row.source_doc)
        if rid and rid in ctx.log_contact:
            key = (ctx.log_date.get(rid) or date.min, rid)
            if newest is None or key > newest[0]:
                newest = (key, ctx.log_contact[rid])
    return newest[1] if newest else None


def _stated_on(ctx: _Context, obs: Observation) -> Optional[date]:
    """When the fact was said: its note's date, else when it was recorded."""
    lid = _source_log_id(obs.source_doc)
    if lid and lid in ctx.log_date:
        return ctx.log_date[lid]
    return obs.created_at.date() if obs.created_at else None


# ── The lease date ───────────────────────────────────────────────────────────

def _note_candidate(ctx: _Context, obs: Observation) -> Optional[_ExpiryCandidate]:
    parsed = parse_expiry(obs.value)
    if not parsed:
        return None
    if not obs.human_verified:
        rank = RANK_NOTE_HEDGED
    elif obs.verified_by == "human":
        rank = RANK_NOTE_VERIFIED
    else:
        rank = RANK_NOTE_EXACT
    page = f" p.{obs.source_page}" if obs.source_page is not None else ""
    return _ExpiryCandidate(
        date=parsed.date, rank=rank, precision=parsed.precision, obs=obs,
        label=f"{obs.source_doc or 'unknown source'}{page}",
        stated_on=_stated_on(ctx, obs),
    )


def _record_candidates(ctx: _Context, entity_type: str, entity_id: int) -> List[_ExpiryCandidate]:
    """The confirmed lease and the company record, for a company entity."""
    if entity_type != "company":
        return []
    out: List[_ExpiryCandidate] = []
    lease = ctx.current_lease.get(entity_id)
    if lease is not None:
        out.append(_ExpiryCandidate(
            date=lease.expiration_date, rank=RANK_CONFIRMED_LEASE,
            label="your confirmed lease", precision="exact",
            stated_on=lease.confirmed_at.date() if lease.confirmed_at else None,
        ))
    company = ctx.companies.get(entity_id)
    if company is not None and company.lease_expiry_date:
        source = company.lease_expiry_source or ""
        rank = RANK_RECORD_CONFIRMED if source in CONFIRMED_RECORD_SOURCES else RANK_RECORD_OTHER
        out.append(_ExpiryCandidate(
            date=company.lease_expiry_date, rank=rank, precision="exact",
            label=_RECORD_SOURCE_LABEL.get(source, "company record"),
        ))
    return out


def _choose(candidates: List[_ExpiryCandidate]) -> Tuple[_ExpiryCandidate, List[_ExpiryCandidate]]:
    """The date to act on, and the sources that disagree with it.

    Best rank wins; within a rank the most recently stated wins — a tenant who
    said "August" in July and "October" in September means October.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (c.rank, -(c.stated_on or date.min).toordinal(),
                       -(c.obs.id if c.obs is not None else 0)),
    )
    chosen = ordered[0]
    # One line per disagreeing source and date, not one per restatement.
    seen: Set[Tuple[str, date]] = set()
    conflicts = []
    for c in ordered[1:]:
        if abs((c.date - chosen.date).days) <= CONFLICT_DAYS:
            continue
        key = (c.label, c.date)
        if key not in seen:
            seen.add(key)
            conflicts.append(c)
    return chosen, conflicts


def _cycle_of(d: Optional[date]) -> Optional[str]:
    return d.strftime("%Y-%m") if d else None


def _months_apart(a: str, b: str) -> Optional[int]:
    try:
        ay, am = (int(x) for x in a.split("-")[:2])
        by, bm = (int(x) for x in b.split("-")[:2])
    except (ValueError, AttributeError):
        return None
    return abs((ay * 12 + am) - (by * 12 + bm))


def _same_cycle(a: Optional[str], b: Optional[str]) -> bool:
    """Whether two lease dates are the same lease.

    Unknown counts as the same, so an old decision with no readable date keeps
    blocking rather than letting a card Jack already judged straight back.
    """
    if not a or not b:
        return True
    apart = _months_apart(a, b)
    return apart is None or apart <= SAME_CYCLE_MONTHS


# ── Decisions, scoped in time ────────────────────────────────────────────────

def _signal_family(dedup_key: Optional[str]) -> str:
    kind = (dedup_key or "").rsplit(":", 1)[-1]
    return "lease" if kind in LEASE_SIGNALS else kind


class _Decisions:
    """Every card Jack has decided on, indexed for "does this still hold?".

    The most recent decision on a thing governs it. What a decision covers:

      * rejected as not_a_tenant — the entity, forever.
      * deferred — until its resurface date.
      * accepted / rejected, lease card — that lease cycle only. Verify-first
        and verified cards for one lease are the same lease: accepting the
        verify card is not undone by the date then being confirmed.
      * accepted / rejected, requirement card — until the tenant says
        something newer than the decision.
      * accepted / rejected, anything else — for good, as before.

    Decisions from before cards carried a cycle are matched by the facts they
    were about as well as by key, because re-filing facts under companies
    changed keys ("activity_log:188:..." became "company:444:...").
    """

    def __init__(self, db: Session, today: date):
        self.today = today
        rows = (
            db.query(IntelOpportunity)
            .filter(IntelOpportunity.status.in_(("accepted", "rejected", "deferred")))
            .all()
        )
        feedback: Dict[int, IntelFeedback] = {}
        if rows:
            for chunk in _chunks([r.id for r in rows]):
                for fb in (
                    db.query(IntelFeedback)
                    .filter(IntelFeedback.opportunity_id.in_(chunk))
                    .order_by(IntelFeedback.created_at.asc(), IntelFeedback.id.asc())
                    .all()
                ):
                    feedback[fb.opportunity_id] = fb   # the last one wins
        self.decided: List[dict] = []
        self.never_again: Set[Tuple[str, int]] = set()
        for opp in rows:
            fb = feedback.get(opp.id)
            decided_at = (fb.created_at if fb else None) or opp.surfaced_at or datetime.utcnow()
            evidence_ids: Set[int] = set()
            cycle = opp.cycle
            try:
                signals = json.loads(opp.signals_json or "[]")
            except (ValueError, TypeError):
                signals = []
            for sig in signals if isinstance(signals, list) else []:
                if not isinstance(sig, dict):
                    continue
                if sig.get("evidence_observation_id") is not None:
                    evidence_ids.add(sig["evidence_observation_id"])
                if cycle is None and sig.get("value"):
                    # A card from before cycles existed: its date is in its value.
                    cycle = _cycle_of(parse_expiry(sig["value"]).date)
            entity = (opp.entity_type, opp.entity_id)
            if opp.status == "rejected" and fb is not None and fb.reason_category == NOT_A_TENANT:
                self.never_again.add(entity)
            self.decided.append({
                "opp": opp, "entity": entity, "family": _signal_family(opp.dedup_key),
                "key": opp.dedup_key, "cycle": cycle, "evidence": evidence_ids,
                "decided_on": decided_at.date(),
                "resurface_at": opp.resurface_at or (fb.resurface_at if fb else None),
            })

    def blocks(self, *, entity: Tuple[str, int], dedup_key: str,
               cycle: Optional[str] = None, evidence_date: Optional[date] = None,
               evidence_ids: Iterable[int] = ()) -> bool:
        """Whether a decision already made still covers this card."""
        if entity in self.never_again:
            return True
        family = _signal_family(dedup_key)
        evidence_ids = set(evidence_ids)
        matches = []
        for d in self.decided:
            if d["family"] != family:
                continue
            same_thing = (
                d["key"] == dedup_key
                or (family == "lease" and d["entity"] == entity)
                or bool(evidence_ids and d["evidence"] & evidence_ids)
            )
            if not same_thing:
                continue
            if family == "lease" and not _same_cycle(d["cycle"], cycle):
                continue   # that decision was about a different lease
            matches.append(d)
        if not matches:
            return False
        latest = max(matches, key=lambda d: (d["decided_on"], d["opp"].id))
        if latest["opp"].status == "deferred":
            back_on = latest["resurface_at"]
            if back_on is None:
                # Deferred before deferrals carried a date: the default window.
                from app.services.intel_feedback_service import DEFAULT_DEFER_DAYS
                back_on = latest["decided_on"] + timedelta(days=DEFAULT_DEFER_DAYS)
            return self.today < back_on
        if family == "stated_requirement":
            return not (evidence_date and evidence_date > latest["decided_on"])
        return True


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
                        signals_payload: list, *, decisions: _Decisions,
                        cycle: Optional[str] = None,
                        evidence_date: Optional[date] = None,
                        evidence_ids: Iterable[int] = ()) -> Optional[IntelOpportunity]:
    """Create an open opportunity for this key, or refresh an existing open one.

    Idempotent: an already-open row for the same key is updated in place (score,
    rationale, signals, cycle) rather than duplicated. A decided card is never
    reopened — when its decision no longer covers what the card is about (a new
    lease cycle, a deferral come due, something new said) a NEW open row is
    written, so History keeps each decision exactly as it was made.
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
        open_existing.cycle = cycle
        open_existing.evidence_date = evidence_date
        return open_existing

    if decisions.blocks(entity=(entity_type, entity_id), dedup_key=dedup_key,
                        cycle=cycle, evidence_date=evidence_date,
                        evidence_ids=evidence_ids):
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
        cycle=cycle,
        evidence_date=evidence_date,
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


def _last_touch_date(ctx: _Context, rows: List[Observation]) -> Optional[date]:
    """Newest note date behind a set of facts — how long since we last spoke.

    Falls back to the observation's own created_at when the source log is gone,
    so a deleted note degrades to "we know roughly when" rather than crashing.
    """
    dates = [
        ctx.log_date[lid]
        for lid in (_source_log_id(o.source_doc) for o in rows)
        if lid and lid in ctx.log_date
    ]
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


def _newest_value(ctx: _Context, rows: List[Observation], field: str) -> Optional[str]:
    """The most recently stated value for a field — a requirement said again
    later ("actually 3,000 now") replaces what was said before. Verified wins a
    same-day tie."""
    matches = [o for o in rows if o.field == field and o.value]
    if not matches:
        return None
    matches.sort(key=lambda o: (_stated_on(ctx, o) or date.min, bool(o.human_verified), o.id),
                 reverse=True)
    return matches[0].value


def _requirement_summary(ctx: _Context, rows: List[Observation], fields: set) -> str:
    """Readable one-liner of what the tenant said, specifics first, each item
    as most recently stated."""
    ordered = [f for f in _REQUIREMENT_LABEL if f in fields]
    parts = []
    for field in ordered:
        value = _newest_value(ctx, rows, field)
        if value:
            parts.append(f"{_REQUIREMENT_LABEL[field]} {value.strip()}")
    return "; ".join(parts[:6])


def _shopped_with(ctx: _Context, rows: List[Observation]) -> List[Dict[str, Optional[str]]]:
    """The people a requirement reached this tenant through — Jack attached it
    from their thread, or the note named this tenant. Usually brokers he asked
    about space. Oldest first, one line per person."""
    seen: Dict[str, Optional[date]] = {}
    for obs in rows:
        if not (obs.assigned_company_id or obs.assigned_contact_id or obs.about == ABOUT_NAMED):
            continue
        lid = _source_log_id(obs.source_doc)
        who = ctx.log_contact.get(lid) if lid else None
        if not who:
            continue
        when = ctx.log_date.get(lid)
        if who not in seen or (when and (seen[who] is None or when < seen[who])):
            seen[who] = when
    ordered = sorted(seen.items(), key=lambda kv: (kv[1] or date.min, kv[0]))
    return [
        # "Jul 28" — built by hand: strftime's no-padding flag differs by OS.
        {"contact": who, "date": f"{when:%b} {when.day}" if when else None}
        for who, when in ordered
    ]


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

    Any open card this run does not re-derive is retired as superseded: the
    fact behind it was deleted, archived or moved, or a stronger source now
    says otherwise. An open card is a claim that something is true now.
    """
    today = today or date.today()
    active = _active_observations(db)
    ctx, facts = _load_context(db, active)
    decisions = _Decisions(db, today)
    by_entity = _group(ctx, facts)
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
        # Cards not shown because the tenant is being worked, said when to
        # come back, or said no recently. They return on their own.
        "held_by_stage": 0,
    }

    def _keep(opp: Optional[IntelOpportunity], key: str) -> None:
        if opp is not None and key not in seen_keys:
            seen_keys.add(key)
            touched.append(opp)

    # Counted per fact, as before: what the notes said, whichever source wins.
    for obs in facts:
        if obs.field != "expiration_date" or not obs.value:
            continue
        stats["expirations_found"] = int(stats["expirations_found"]) + 1
        parsed = parse_expiry(obs.value)
        if parsed.date is None:
            stats["expirations_unreadable"] = int(stats["expirations_unreadable"]) + 1
            continue
        days = (parsed.date - today).days
        if days < 0:
            stats["expirations_past"] = int(stats["expirations_past"]) + 1
        elif days > EXPIRY_HORIZON_DAYS:
            stats["expirations_beyond_horizon"] = int(stats["expirations_beyond_horizon"]) + 1

    def _is_counterparty(entity_type: str, entity_id: int) -> bool:
        """A firm on the other side of the table gets no tenant-side card.

        Either Jack marked the firm itself, or — short of that — everyone on
        file who works there is someone he confirmed as a counterparty. Mike
        Shuler confirmed as a broker makes Avison Young a brokerage, even if
        "whole firm" was never clicked. A firm Jack marked tenant stays one.
        """
        if entity_type != "company":
            return False
        company = ctx.companies.get(entity_id)
        if company is not None and company.company_type:
            return company.company_type == "counterparty"
        staff = ctx.staff_types.get(entity_id)
        return bool(staff) and staff == {"counterparty"}

    # ── Rules 1 & 2: one lease date per entity, verified vs unverified ───────
    expiry_entities: set = set()
    for (entity_type, entity_id), rows in by_entity.items():
        if _is_counterparty(entity_type, entity_id):
            continue
        candidates = [
            c for c in (
                _note_candidate(ctx, o) for o in rows
                if o.field == "expiration_date" and o.value
            )
            if c is not None
        ]
        has_requirement = any(o.field in ALL_REQUIREMENT_FIELDS and o.value for o in rows)
        # The company's own record only speaks for a company the notes already
        # put on the radar. Every company with a CoStar date is the Companies
        # tab's job, not a card.
        if candidates or has_requirement:
            candidates += _record_candidates(ctx, entity_type, entity_id)
        if not candidates:
            continue

        chosen, conflicts = _choose(candidates)
        exp = chosen.date
        days = (exp - today).days
        if days < 0 or days > EXPIRY_HORIZON_DAYS:
            continue
        expiry_entities.add((entity_type, entity_id))
        held, past_client = _hold_reason(_people_for(ctx, entity_type, entity_id), today)
        if held:
            stats["held_by_stage"] = int(stats["held_by_stage"]) + 1
            continue

        tenant = _tenant_label(ctx, entity_type, entity_id, rows)
        contact = _contact_for(ctx, chosen.obs, rows)
        who = f" Contact: {contact}." if contact else ""
        who += _touch_line(ctx, entity_type, entity_id, today)
        if past_client:
            who = " Past client — you placed them before." + who
        disagree = ""
        if conflicts:
            disagree = " Sources disagree: " + "; ".join(
                f"{c.label} says {c.date.isoformat()}" for c in conflicts
            ) + "."

        if chosen.verified:
            signal_type = "lease_expiring"
            title = f"Lease expiring — {tenant}"
            rationale = (
                f"Lease for {tenant} expires in {days} days "
                f"({exp.isoformat()}, source: {chosen.label}).{who}{disagree}"
            )
            # The fact is verified now, so any open "verify this first" card for
            # the same entity is stale — retire it instead of asking the user to
            # judge the same fact twice under contradictory framing.
            _retire_superseded(db, entity_type, entity_id, "expiration_unverified")
        else:
            signal_type = "expiration_unverified"
            title = f"Verify lease expiration — {tenant}"
            rationale = (
                f"Possible lease expiration for {tenant} in {days} days "
                f"({exp.isoformat()}) from {chosen.label} — verify this fact first."
                f"{who}{disagree}"
            )
        score = SIGNAL_BASE_WEIGHT[signal_type] + _window_bonus(days)
        if past_client and chosen.verified:
            # "You placed this tenant in this building" is the strongest
            # opening line there is — worth a few places, never a tier, and
            # never enough to lift an unverified date over a verified one.
            score += PAST_CLIENT_BONUS

        obs = chosen.obs
        value = obs.value if obs is not None else exp.isoformat()
        _upsert_signal(db, entity_type, entity_id, signal_type, value,
                       obs.id if obs is not None else None)
        signals_payload = [{
            "signal_type": signal_type,
            "value": value,
            "evidence_observation_id": obs.id if obs is not None else None,
            "days_to_expiry": days,
            # Lets the UI link straight to the note or document this came from.
            "source_doc": obs.source_doc if obs is not None else None,
            # The verbatim words behind the card, so it can be trusted or
            # dismissed without leaving the page.
            "source_snippet": obs.source_snippet if obs is not None else None,
            # Where the date came from, and every source that disagrees.
            "expiry_source": chosen.label,
            "expiry_date": exp.isoformat(),
            "contact_name": contact,
            "conflicts": [
                {"source": c.label, "date": c.date.isoformat()} for c in conflicts
            ],
            "past_client": past_client,
        }]
        dedup_key = f"{entity_type}:{entity_id}:{signal_type}"
        opp = _upsert_opportunity(
            db, dedup_key, title, entity_type, entity_id, score, rationale,
            signals_payload, decisions=decisions, cycle=_cycle_of(exp),
            evidence_date=chosen.stated_on,
            evidence_ids=[c.obs.id for c in candidates if c.obs is not None],
        )
        _keep(opp, dedup_key)

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

        tenant = _tenant_label(ctx, entity_type, entity_id, rows)
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
        stale_key = f"{entity_type}:{entity_id}:{signal_type}"
        opp = _upsert_opportunity(
            db, stale_key, title, entity_type, entity_id, score, rationale,
            signals_payload, decisions=decisions,
        )
        _keep(opp, stale_key)

    # ── Rule 4: stated requirements — the tenant told us what they want ──────
    # The single biggest gap this closes: hundreds of requirement facts mined
    # from call notes previously drove nothing at all, because only
    # `expiration_date` could produce a card.
    for (entity_type, entity_id), all_rows in by_entity.items():
        rows = [o for o in all_rows if o.field in ALL_REQUIREMENT_FIELDS and o.value]
        if not rows or _is_counterparty(entity_type, entity_id):
            continue
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
        held, past_client = _hold_reason(_people_for(ctx, entity_type, entity_id), today)
        if held:
            stats["held_by_stage"] = int(stats["held_by_stage"]) + 1
            continue

        last_touch = _last_touch_date(ctx, rows)
        days_since = (today - last_touch).days if last_touch else None
        score = (
            SIGNAL_BASE_WEIGHT["stated_requirement"]
            + _specificity_bonus(len(fields))
            + _staleness_bonus(days_since)
        )
        tenant = _tenant_label(ctx, entity_type, entity_id, all_rows)
        summary = _requirement_summary(ctx, rows, fields)
        since = (
            f"Last note {days_since} days ago" if days_since is not None
            else "No note date recorded"
        )
        via = _shopped_with(ctx, rows)
        also = (
            " Also raised with " + ", ".join(
                f"{v['contact']} ({v['date']})" if v["date"] else v["contact"] for v in via
            ) + "."
        ) if via else ""
        title = f"Stated requirement — {tenant}"
        touch = _touch_line(ctx, entity_type, entity_id, today)
        rationale = (
            f"{tenant} stated: {summary}. {since}.{touch}{also}"
            if summary else
            f"{tenant} stated a space requirement across {len(fields)} fields. {since}.{touch}{also}"
        )
        evidence = next((o for o in rows if o.field in SPECIFIC_REQUIREMENT_FIELDS), rows[0])
        contact = _contact_for(ctx, evidence, rows)
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
            "contact_name": contact,
            "past_client": past_client,
            # Brokers Jack shopped this requirement with — facts that reached
            # this tenant from someone else's thread.
            "via": via,
        }]
        req_key = f"{entity_type}:{entity_id}:stated_requirement"
        opp = _upsert_opportunity(
            db, req_key, title, entity_type, entity_id, score, rationale,
            signals_payload, decisions=decisions, evidence_date=last_touch,
            evidence_ids=[o.id for o in rows],
        )
        _keep(opp, req_key)

    # ── Retire what no longer holds ──────────────────────────────────────────
    db.flush()
    live = {opp.id for opp in touched}
    for stale in db.query(IntelOpportunity).filter(IntelOpportunity.status == "open").all():
        if stale.id not in live:
            stale.status = "superseded"

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
