import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app.models.document import Document
from app.models.observation import Observation
from app.services.document_extraction_service import MissingAPIKeyError, extract_document


class ObservationPayload(BaseModel):
    id: Optional[int] = None
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
    created_at: Optional[str] = None

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: int
    filename: str
    storage_path: str
    uploaded_at: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    extraction_status: str

    class Config:
        from_attributes = True


class ExtractionResponse(BaseModel):
    document_id: int
    observations: List[ObservationPayload]


@router.post("/", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")

    upload_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    storage_path = os.path.join(upload_dir, file.filename)

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    with open(storage_path, "wb") as handle:
        handle.write(contents)

    document = Document(
        filename=file.filename,
        storage_path=storage_path,
        extraction_status="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    payload = {
        "id": document.id,
        "filename": document.filename,
        "storage_path": document.storage_path,
        "uploaded_at": document.uploaded_at.isoformat() if isinstance(document.uploaded_at, datetime) else None,
        "entity_type": document.entity_type,
        "entity_id": document.entity_id,
        "extraction_status": document.extraction_status,
    }
    return payload


@router.post("/{document_id}/extract", response_model=ExtractionResponse)
def extract_document_endpoint(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if not os.path.exists(document.storage_path):
        raise HTTPException(status_code=404, detail="Stored file not found")

    with open(document.storage_path, "rb") as handle:
        pdf_bytes = handle.read()

    try:
        observations = extract_document(document, db, pdf_bytes)
    except MissingAPIKeyError as exc:
        # Configuration problem, not a bad document — leave status untouched so
        # it can be retried once the key is set, and return a clear message.
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        document.extraction_status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}") from exc

    payload = []
    for obs in observations:
        payload.append(
            {
                "id": obs.id,
                "entity_type": obs.entity_type,
                "entity_id": obs.entity_id,
                "field": obs.field,
                "value": obs.value,
                "confidence": obs.confidence,
                "source_doc": obs.source_doc,
                "source_page": obs.source_page,
                "source_snippet": obs.source_snippet,
                "human_verified": obs.human_verified,
                "superseded_by_id": obs.superseded_by_id,
                "created_at": obs.created_at.isoformat() if isinstance(obs.created_at, datetime) else None,
            }
        )

    return {"document_id": document.id, "observations": payload}
