"""The submarket list the dropdowns read from — and add to.

GET  /api/submarkets/   every submarket, alphabetical (seeds an empty table)
POST /api/submarkets/   add one; an existing name, matched case-insensitively,
                        is returned instead of duplicated

The list only grows. Assigning a submarket to a company is
PATCH /api/companies/{company_id}/submarket; a confirmed lease assigns one on
its own (api/routes/leases.py).
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.submarket_service import get_or_create_submarket, list_submarkets

router = APIRouter(prefix="/submarkets", tags=["submarkets"])


class SubmarketOut(BaseModel):
    id: int
    name: str
    auto_created: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class SubmarketCreate(BaseModel):
    name: str


class SubmarketCreated(SubmarketOut):
    # False when the name already existed (in any casing) and that row came back.
    created: bool = False


@router.get("/", response_model=List[SubmarketOut])
def get_submarkets(db: Session = Depends(get_db)):
    return list_submarkets(db)


@router.post("/", response_model=SubmarketCreated)
def add_submarket(payload: SubmarketCreate, db: Session = Depends(get_db)):
    try:
        row, created = get_or_create_submarket(db, payload.name, auto_created=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(row)
    out = SubmarketCreated.model_validate(row)
    out.created = created
    return out
