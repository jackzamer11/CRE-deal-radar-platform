from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class OpportunityOut(BaseModel):
    id: int
    opportunity_id: str
    deal_type: str
    opportunity_category: str
    property_id: Optional[int]
    company_id: Optional[int]
    score: float
    confidence_level: str
    priority: str
    prediction_score: Optional[float]
    owner_behavior_score: Optional[float]
    mispricing_score: Optional[float]
    tenant_opportunity_score: Optional[float]
    thesis: str
    next_action: str
    call_script: Optional[str]
    stage: str
    estimated_deal_value: Optional[float]
    estimated_commission: Optional[float]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Denormalized for display
    property_address: Optional[str] = None
    property_submarket: Optional[str] = None
    company_name: Optional[str] = None

    class Config:
        from_attributes = True


class OpportunityListOut(BaseModel):
    id: int
    opportunity_id: str
    deal_type: str
    opportunity_category: str
    score: float
    priority: str
    confidence_level: str
    thesis: str
    next_action: str
    stage: str
    estimated_deal_value: Optional[float]
    estimated_commission: Optional[float]
    property_address: Optional[str] = None
    property_submarket: Optional[str] = None
    company_name: Optional[str] = None
    # Signal breakdown included for frontend display
    prediction_score: Optional[float] = None
    owner_behavior_score: Optional[float] = None
    mispricing_score: Optional[float] = None
    tenant_opportunity_score: Optional[float] = None

    class Config:
        from_attributes = True


class StageUpdate(BaseModel):
    stage: str
    note: Optional[str] = None
