"""Lease extraction pipeline.

Sends a document's text to the Anthropic API using **structured JSON output**
(tool-use) and records each of the five v1 lease fields as one row in the
`observations` table. The model is instructed to return ``null`` for any field
the document does not state — it must never infer, estimate, or guess. A field
that is genuinely absent therefore lands as ``value=None`` (never fabricated).
"""

import io
import os
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.observation import Observation


# The five v1 fields, in a fixed order. Every extraction always writes exactly
# one observation row per field, even when the value is null.
FIELD_NAMES = [
    "tenant_name",
    "premises_sqft",
    "commencement_date",
    "expiration_date",
    "base_rent_annual",
]

# Match the Claude model the rest of the repo already uses (see outreach_service).
EXTRACTION_MODEL = "claude-sonnet-4-6"


class MissingAPIKeyError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is not configured. Surfaces as a clear
    error to the caller rather than a crash or a silent all-null result."""


def _extract_pages(pdf_bytes: bytes) -> List[str]:
    """Return the text of each PDF page as a list (index 0 == page 1).

    NOTE: pdfplumber.open() needs a path or a file-like object — passing raw
    bytes silently fails. We wrap the bytes in io.BytesIO.
    """
    import pdfplumber

    pages: List[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def _document_text(pages: List[str]) -> str:
    """Join page texts with explicit page markers so the model can cite pages."""
    parts = []
    for idx, text in enumerate(pages, start=1):
        parts.append(f"=== PAGE {idx} ===\n{text}")
    return "\n\n".join(parts)


_FIELD_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {
            "type": ["string", "null"],
            "description": "The value exactly as stated in the document, or null if not stated.",
        },
        "confidence": {
            "type": ["number", "null"],
            "description": "0.0-1.0 confidence the value is correct; null if value is null.",
        },
        "page": {
            "type": ["integer", "null"],
            "description": "1-based page number the value was found on, or null.",
        },
        "snippet": {
            "type": ["string", "null"],
            "description": "The verbatim text quote the value came from, or null.",
        },
    },
    "required": ["value", "confidence", "page", "snippet"],
    "additionalProperties": False,
}

_EXTRACTION_TOOL = {
    "name": "record_lease_fields",
    "description": "Record the five lease fields extracted from the document.",
    "input_schema": {
        "type": "object",
        "properties": {field: _FIELD_OBJECT_SCHEMA for field in FIELD_NAMES},
        "required": list(FIELD_NAMES),
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are a careful commercial-lease abstractor. Extract ONLY the five "
    "requested fields, using the exact wording of the document.\n"
    "CRITICAL RULE: If the document does not explicitly state a value for a "
    "field, return null for that field's value. Never infer, estimate, "
    "calculate, or guess. A missing value is normal and expected — null is the "
    "correct answer, not a best-effort approximation.\n"
    "For each field you DO find, provide a confidence between 0 and 1, the "
    "1-based page number, and a short verbatim snippet quoting the source text. "
    "For any field whose value is null, set confidence, page, and snippet to null."
)

_FIELD_GUIDE = (
    "Fields:\n"
    "- tenant_name: the tenant/lessee's legal name.\n"
    "- premises_sqft: the leased premises size in square feet (number only).\n"
    "- commencement_date: the lease commencement date.\n"
    "- expiration_date: the lease expiration/termination date.\n"
    "- base_rent_annual: the annual base rent (a dollar amount)."
)


def _extract_fields_via_llm(pages: List[str], client=None) -> Dict[str, Dict[str, object]]:
    """Call Anthropic with structured (tool-use) output and return the raw
    per-field dicts: {field: {value, confidence, page, snippet}}.

    ``client`` is injectable so tests can stub the network boundary; in
    production it defaults to a real Anthropic client built from the env key.
    Raises MissingAPIKeyError when no key is configured.
    """
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Add it to backend/.env or your "
                "environment before running document extraction."
            )
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

    document_text = _document_text(pages)
    user_content = (
        f"{_FIELD_GUIDE}\n\n"
        "Extract the five fields from the lease below and record them with the "
        "record_lease_fields tool. Remember: return null for anything the "
        "document does not explicitly state.\n\n"
        f"--- LEASE DOCUMENT ---\n{document_text}"
    )

    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=1500,
        system=_SYSTEM_PROMPT,
        tools=[_EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_lease_fields"},
        messages=[{"role": "user", "content": user_content}],
    )

    tool_input = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_lease_fields":
            tool_input = block.input
            break
    if tool_input is None:
        raise RuntimeError("Model did not return structured lease fields.")

    return _normalize_fields(tool_input)


def _normalize_fields(raw: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    """Coerce the model output into a complete, well-typed per-field map.

    Guarantees every field is present and that an absent/blank value is stored
    as a real None (not "", not the string "null").
    """
    result: Dict[str, Dict[str, object]] = {}
    for field in FIELD_NAMES:
        entry = raw.get(field) or {}
        if not isinstance(entry, dict):
            entry = {}
        value = entry.get("value")
        if isinstance(value, str):
            stripped = value.strip()
            value = stripped if stripped and stripped.lower() != "null" else None
        result[field] = {
            "value": value,
            "confidence": entry.get("confidence") if value is not None else None,
            "page": entry.get("page") if value is not None else None,
            "snippet": entry.get("snippet") if value is not None else None,
        }
    return result


def extract_document(
    document: Document,
    db: Session,
    pdf_bytes: bytes,
    extractor: Optional[Callable[[List[str]], Dict[str, Dict[str, object]]]] = None,
) -> List[Observation]:
    """Run extraction on a document and write five observation rows.

    ``extractor`` is injectable for tests; production uses the real Anthropic
    call. Any failure propagates to the caller (the endpoint marks the document
    ``failed``); nothing is left half-written because the commit happens only
    after all five rows are staged.
    """
    fn = extractor or _extract_fields_via_llm

    pages = _extract_pages(pdf_bytes)
    parsed = fn(pages)

    created: List[Observation] = []
    for field in FIELD_NAMES:
        row = parsed.get(field, {})
        value = row.get("value")
        obs = Observation(
            entity_type=document.entity_type or "document",
            entity_id=document.entity_id or document.id,
            field=field,
            value=str(value) if value is not None else None,
            confidence=row.get("confidence"),
            source_doc=document.filename,
            source_page=row.get("page"),
            source_snippet=row.get("snippet"),
            human_verified=False,
        )
        db.add(obs)
        created.append(obs)

    document.extraction_status = "done"
    db.commit()
    for obs in created:
        db.refresh(obs)
    return created
