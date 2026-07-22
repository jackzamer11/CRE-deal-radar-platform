"""Phase D signal engine — date math only, no ML, no LLM.

Reads verified/unverified observations, detects three v1 signal types, and turns
them into ranked `intel_opportunities` with plain-English, template-based
rationale. Re-running the generator is idempotent: it never creates a second
open opportunity for the same (entity, signal) key.
"""

import json
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.intel import IntelOpportunity, IntelSignal
from app.models.observation import Observation


# ── Tunable scoring weights (read and edit these freely) ─────────────────────
# Base weight per signal type. lease_expiring (verified) always outranks
# expiration_unverified because the base gap (100 vs 60) exceeds the maximum
# urgency bonus (39) — so a verified fact never loses to an unverified one no
# matter how close its date.
SIGNAL_BASE_WEIGHT = {
    "lease_expiring": 100,          # verified expiration within a year — actionable
    "expiration_unverified": 60,    # extracted but unverified — "verify first"
    "stale_data": 30,               # processed lease with missing/unverified fields
}

# Extra points for urgency, scaled by how soon the lease expires. Capped below
# the base-weight gap so verified > unverified is guaranteed.
URGENCY_MAX = 39

# Only leases expiring within this horizon surface as opportunities.
EXPIRY_HORIZON_DAYS = 365

# The five core lease fields — a lease is "stale" if any is null & unverified.
CORE_LEASE_FIELDS = [
    "tenant_name",
    "premises_sqft",
    "commencement_date",
    "expiration_date",
    "base_rent_annual",
]


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a date string in any of the formats the extractor commonly returns.

    Extraction stores the model's verbatim value, which is often natural language
    ("January 17, 2027"), not ISO. The signal engine must tolerate all of these
    or verified expirations never generate opportunities.
    """
    if not value:
        return None
    s = value.strip()
    for fmt in (
        "%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y",
        "%B %d, %Y", "%b %d, %Y",        # January 17, 2027 / Jan 17, 2027
        "%B %d %Y", "%b %d %Y",          # same without the comma
        "%d %B %Y", "%d %b %Y",          # 17 January 2027
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _urgency_bonus(days_to_expiry: int) -> int:
    """0 at the horizon edge, up to URGENCY_MAX the day of expiry (or sooner)."""
    clamped = max(0, min(days_to_expiry, EXPIRY_HORIZON_DAYS))
    return round((EXPIRY_HORIZON_DAYS - clamped) / EXPIRY_HORIZON_DAYS * URGENCY_MAX)


def _active_observations(db: Session) -> List[Observation]:
    """Non-superseded observations only (the current view of each fact)."""
    return db.query(Observation).filter(Observation.superseded_by_id.is_(None)).all()


def _tenant_label(db: Session, entity_type: str, entity_id: int, active: List[Observation]) -> str:
    """Best-available tenant name for an entity, else a generic entity label."""
    named = [
        o for o in active
        if o.entity_type == entity_type and o.entity_id == entity_id
        and o.field == "tenant_name" and o.value
    ]
    # Prefer a verified name.
    named.sort(key=lambda o: (not o.human_verified,))
    if named:
        return named[0].value
    return f"{entity_type} #{entity_id}"


def _upsert_signal(db: Session, entity_type: str, entity_id: int, signal_type: str,
                   value: Optional[str], evidence_id: Optional[int]) -> IntelSignal:
    """Keep one signal row per (entity, signal_type); refresh it on re-run."""
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


def generate_opportunities(db: Session, today: Optional[date] = None) -> List[IntelOpportunity]:
    """Run the v1 signal rules and upsert ranked opportunities. Idempotent."""
    today = today or date.today()
    active = _active_observations(db)
    touched: List[IntelOpportunity] = []

    # ── Rules 1 & 2: expiration within the horizon (verified vs unverified) ──
    for obs in active:
        if obs.field != "expiration_date" or not obs.value:
            continue
        exp = _parse_date(obs.value)
        if exp is None:
            continue
        days = (exp - today).days
        if days < 0 or days > EXPIRY_HORIZON_DAYS:
            continue

        tenant = _tenant_label(db, obs.entity_type, obs.entity_id, active)
        source = obs.source_doc or "unknown source"
        page = f" p.{obs.source_page}" if obs.source_page is not None else ""

        if obs.human_verified:
            signal_type = "lease_expiring"
            score = SIGNAL_BASE_WEIGHT[signal_type] + _urgency_bonus(days)
            title = f"Lease expiring — {tenant}"
            rationale = (
                f"Lease for {tenant} expires in {days} days "
                f"({exp.isoformat()}, source: {source}{page}). "
                "No renewal activity recorded."
            )
        else:
            signal_type = "expiration_unverified"
            score = SIGNAL_BASE_WEIGHT[signal_type] + _urgency_bonus(days)
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
        }]
        opp = _upsert_opportunity(
            db, f"{obs.entity_type}:{obs.entity_id}:{signal_type}",
            title, obs.entity_type, obs.entity_id, score, rationale, signals_payload,
        )
        if opp is not None:
            touched.append(opp)

    # ── Rule 3: stale data — a processed lease with missing/unverified fields ──
    # Group active observations by entity; only consider entities that actually
    # have observations (i.e. a document was processed for them).
    entities = {(o.entity_type, o.entity_id) for o in active}
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
        if opp is not None:
            touched.append(opp)

    db.commit()
    for opp in touched:
        db.refresh(opp)
    return touched
