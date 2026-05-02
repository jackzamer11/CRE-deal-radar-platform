from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class CallScript(BaseModel):
    opening:     str
    core_message: str
    pain_probe:  str
    the_close:   str


class OutreachDraft(BaseModel):
    """Returned by POST /draft-outreach — not yet persisted."""
    email_subject: str
    email_body:    str
    call_script:   CallScript
    projected_sf:  Optional[int] = None
    score:         float
    priority:      str
    generated_at:  datetime


class OutreachLogCreate(BaseModel):
    """Body for POST /log-outreach."""
    email_subject:         str
    email_body:            str
    call_script_opening:   str
    call_script_core:      str
    call_script_pain_probe: str
    call_script_close:     str
    projected_sf:          Optional[int] = None
    score_at_generation:   float
    priority_at_generation: str
    email_sent:            bool = False
    call_made:             bool = False


class OutreachLogUpdate(BaseModel):
    """Body for PATCH /outreach-log/{log_id}."""
    outcome_notes:    Optional[str] = None
    marked_contacted: Optional[bool] = None
    email_sent:       Optional[bool] = None
    call_made:        Optional[bool] = None


class OutreachLogOut(BaseModel):
    id:                    int
    company_id:            int
    generated_at:          datetime
    email_subject:         str
    email_body:            str
    call_script_opening:   str
    call_script_core:      str
    call_script_pain_probe: str
    call_script_close:     str
    projected_sf:          Optional[int] = None
    score_at_generation:   float
    priority_at_generation: str
    marked_contacted:      bool
    email_sent:            bool
    call_made:             bool
    outcome_notes:         Optional[str] = None
    contacted_at:          Optional[datetime] = None

    class Config:
        from_attributes = True
