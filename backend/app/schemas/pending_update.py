# backend/app/schemas/pending_update.py
"""The shape a pending company update takes on the wire.

Lives in schemas/ rather than in a route because three surfaces render the same
record: the thread header, the company record, and the digest count the
scheduled task reads. One definition, so the three can never drift into showing
different things about the same claim.
"""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class PendingUpdateOut(BaseModel):
    id: int
    company_id: int
    company_name: Optional[str] = None
    field: str
    label: str
    # Both sides, always, so the panel can show them beside each other. A null
    # current_value means the company had nothing on file for this field.
    proposed_value: Optional[str] = None
    current_value: Optional[str] = None
    # The sentence the claim came from. Without it the confirmation is not
    # answerable — "they said 40" is not reviewable, the sentence is.
    source_sentence: Optional[str] = None
    source_entry_id: Optional[int] = None
    source_entry_date: Optional[date] = None
    status: str = "pending"
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PendingUpdateDigest(BaseModel):
    """What the scheduled task's digest counts."""
    total: int
    by_company: List[dict] = []
    updates: List[PendingUpdateOut] = []
