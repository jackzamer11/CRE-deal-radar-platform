from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.observation import Observation

router = APIRouter(prefix="/observations", tags=["observations"])


class ObservationOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    field: str
    value: Optional[str] = None
    confidence: Optional[float] = None
    source_doc: Optional[str] = None
    source_page: Optional[int] = None
    source_snippet: Optional[str] = None
    human_verified: bool
    superseded_by_id: Optional[int] = None
    created_at: str
    # Derived, never stored: a clean ISO date read out of a hedged value like
    # "~February 2027", offered in Review as a one-tap correction. Null unless
    # this is a date field whose text needs pinning down.
    suggested_value: Optional[str] = None
    # exact | month | quarter | year — how precise the stored text really was.
    value_precision: Optional[str] = None

    class Config:
        from_attributes = True


# Fields whose stored text is a date and may need normalizing before it is useful.
DATE_FIELDS = {"expiration_date", "commencement_date"}


def _date_hint(observation: Observation) -> tuple:
    """(suggested ISO value, precision) for a date field — (None, None) otherwise.

    Only offered when the stored text is NOT already an exact date: an exact
    value needs no suggestion, and an unparseable one gets no invented guess.
    """
    if observation.field not in DATE_FIELDS or not observation.value:
        return None, None
    from app.services.intel_signal_service import parse_expiry

    parsed = parse_expiry(observation.value)
    if not parsed or parsed.precision == "exact":
        return None, parsed.precision
    return parsed.normalized, parsed.precision


def _to_out(observation: Observation) -> ObservationOut:
    suggested, precision = _date_hint(observation)
    return ObservationOut(
        id=observation.id,
        entity_type=observation.entity_type,
        entity_id=observation.entity_id,
        field=observation.field,
        value=observation.value,
        confidence=observation.confidence,
        source_doc=observation.source_doc,
        source_page=observation.source_page,
        source_snippet=observation.source_snippet,
        human_verified=observation.human_verified,
        superseded_by_id=observation.superseded_by_id,
        created_at=observation.created_at.isoformat() if observation.created_at else "",
        suggested_value=suggested,
        value_precision=precision,
    )


class ObservationCreate(BaseModel):
    entity_type: str
    entity_id: int
    field: str
    value: Optional[str] = None
    confidence: Optional[float] = None
    source_doc: Optional[str] = None
    source_page: Optional[int] = None
    source_snippet: Optional[str] = None


class ObservationVerify(BaseModel):
    value: Optional[str] = None


@router.post("/", response_model=ObservationOut, status_code=201)
def create_observation(payload: ObservationCreate, db: Session = Depends(get_db)):
    observation = Observation(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        field=payload.field,
        value=payload.value,
        confidence=payload.confidence,
        source_doc=payload.source_doc,
        source_page=payload.source_page,
        source_snippet=payload.source_snippet,
    )
    db.add(observation)
    db.commit()
    db.refresh(observation)
    return _to_out(observation)


@router.get("/", response_model=List[ObservationOut])
def list_observations(
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    human_verified: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Observation)
    if entity_type is not None:
        query = query.filter(Observation.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(Observation.entity_id == entity_id)
    if human_verified is not None:
        query = query.filter(Observation.human_verified == human_verified)
        if human_verified is False:
            query = query.filter(Observation.superseded_by_id.is_(None))
        else:
            query = query.filter(Observation.superseded_by_id.is_(None))

    rows = query.order_by(Observation.confidence.asc().nulls_last(), Observation.created_at.asc()).all()
    return [_to_out(row) for row in rows]


@router.post("/{observation_id}/verify", response_model=ObservationOut)
def verify_observation(
    observation_id: int,
    payload: ObservationVerify,
    db: Session = Depends(get_db),
):
    original = db.query(Observation).filter(Observation.id == observation_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Observation not found")

    corrected = Observation(
        entity_type=original.entity_type,
        entity_id=original.entity_id,
        field=original.field,
        value=payload.value if payload.value is not None else original.value,
        confidence=original.confidence,
        source_doc=original.source_doc,
        source_page=original.source_page,
        source_snippet=original.source_snippet,
        human_verified=True,
        verified_by="human",
    )
    db.add(corrected)
    db.flush()
    original.superseded_by_id = corrected.id
    original.human_verified = False
    corrected.human_verified = True
    original.superseded_by = corrected
    db.commit()
    db.refresh(corrected)
    return _to_out(corrected)
