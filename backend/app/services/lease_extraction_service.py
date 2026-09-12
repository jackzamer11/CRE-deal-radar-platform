"""Abstract a signed lease into the nine fields Jack reviews, with citations.

Relationship to document_extraction_service.py
----------------------------------------------
That service extracts FIVE fields into `observations` rows for the Review page,
and its field list is a locked contract. This one reads NINE fields destined for
the Company record behind a confirmation panel. The PDF-text path
(`_extract_pages`, the page-marked document text) and the missing-key error are
REUSED from it rather than reimplemented — only the field set, the prompt and
the destination differ.

Two rules this module enforces
------------------------------
1. **A value with no supporting clause text is not extracted.** The model is
   told to return null rather than infer, and `_normalize()` drops any value
   that arrives without source text — so a field the document does not state
   comes back "not found" instead of quietly becoming a number Jack acts on.

2. **The reading is the risk, not the document.** Commencement vs. expiration,
   rentable vs. usable SF, and whether an option term shifts the effective
   expiry are exactly the misreadings that would silently move a past client's
   re-entry date by years. So nothing is written on upload: the extraction lands
   in a review panel with each value next to its clause, and only a confirmed
   field reaches the company record.

Lease content is private. Nothing here feeds generated outreach copy.
"""
import json
import os
from typing import Callable, Dict, List, Optional

from app.config import settings
# Reused rather than reimplemented — same PDF path as the 5-field pipeline.
from app.services.document_extraction_service import (
    MissingAPIKeyError, _document_text, _extract_pages,
)

__all__ = [
    "LEASE_FIELDS", "LEASE_FIELD_LABELS", "MissingAPIKeyError",
    "ExtractionUnavailable", "extract_lease", "extract_lease_from_pdf",
    "COMPANY_WRITEBACK_FIELDS",
]


# The nine fields, in the order the review panel renders them.
LEASE_FIELDS = [
    "lease_commencement_date",
    "lease_expiration_date",
    "premises_address",
    "suite_or_unit",
    "rentable_square_footage",
    "base_rent",
    "escalation_terms",
    "renewal_options",
    "tenant_legal_entity_name",
]

LEASE_FIELD_LABELS = {
    "lease_commencement_date":   "Lease commencement date",
    "lease_expiration_date":     "Lease expiration date",
    "premises_address":          "Premises address",
    "suite_or_unit":             "Suite / unit",
    "rentable_square_footage":   "Rentable square footage",
    "base_rent":                 "Base rent",
    "escalation_terms":          "Escalation terms",
    "renewal_options":           "Renewal / extension options",
    "tenant_legal_entity_name":  "Tenant legal entity name",
}

# The three extracted fields that write through to the company record on
# confirm. Everything else is stored in lease_extraction_json for reference —
# the company record has nowhere structured to put an escalation clause, and
# inventing a column for it is not this build.
COMPANY_WRITEBACK_FIELDS = (
    "lease_expiration_date",
    "premises_address",
    "rentable_square_footage",
)


class ExtractionUnavailable(RuntimeError):
    """Extraction could not run or could not be read.

    Never fatal to the upload: the caller stores and links the file first, then
    reports this. Losing the document because the reading failed would be the
    one unacceptable outcome.
    """


_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {
            "type": ["string", "null"],
            "description": (
                "The value as the document states it, or null when the document "
                "does not state it."
            ),
        },
        "source_text": {
            "type": ["string", "null"],
            "description": (
                "The verbatim clause or page text the value came from. Null only "
                "when value is null."
            ),
        },
        "page": {
            "type": ["integer", "null"],
            "description": "1-based page number the value was found on, or null.",
        },
    },
    "required": ["value", "source_text", "page"],
    "additionalProperties": False,
}

_EXTRACTION_TOOL = {
    "name": "record_lease_abstract",
    "description": "Record the nine lease fields, each with its source clause text.",
    "input_schema": {
        "type": "object",
        "properties": {field: _FIELD_SCHEMA for field in LEASE_FIELDS},
        "required": list(LEASE_FIELDS),
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are a careful commercial-lease abstractor working for a tenant-side "
    "broker. Extract ONLY the nine requested fields, using the document's own "
    "wording.\n"
    "CRITICAL RULE: every value you return MUST be supported by verbatim text "
    "you quote in source_text. If you cannot quote the clause that states a "
    "value, return null for that field. Never infer, calculate, estimate or "
    "guess — a null is the correct answer for anything the document does not "
    "state, and a wrong value is far more damaging than a missing one.\n"
    "Be precise about the distinctions that are easy to get wrong:\n"
    "- The COMMENCEMENT date is not the expiration date, and neither is the "
    "execution/signature date.\n"
    "- RENTABLE square footage is not usable square footage. If the document "
    "states only usable SF, return null for rentable square footage.\n"
    "- The expiration date is the stated expiration of the CURRENT term. Do NOT "
    "extend it by an unexercised renewal or extension option; describe those "
    "options in renewal_options instead, including each notice deadline the "
    "document states."
)

_FIELD_GUIDE = (
    "Fields:\n"
    "- lease_commencement_date: the commencement date of the lease term.\n"
    "- lease_expiration_date: the expiration date of the current term.\n"
    "- premises_address: the street address of the leased premises.\n"
    "- suite_or_unit: the suite, unit or floor designation of the premises.\n"
    "- rentable_square_footage: rentable SF of the premises (number as stated).\n"
    "- base_rent: the base rent as stated (include the period, e.g. per year, "
    "per month, or $/SF).\n"
    "- escalation_terms: how base rent escalates over the term.\n"
    "- renewal_options: renewal or extension options, WITH the notice deadline "
    "stated for each.\n"
    "- tenant_legal_entity_name: the tenant's full legal entity name."
)


def _blank_field() -> Dict[str, Optional[object]]:
    return {"value": None, "source_text": None, "page": None, "found": False}


def _clean(raw) -> Optional[str]:
    """Trim a model string, treating blanks and the literal "null" as absent."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "none", "n/a", "not stated"}:
        return None
    return text


def _normalize(raw: Dict[str, object]) -> Dict[str, Dict[str, Optional[object]]]:
    """Coerce model output into a complete, well-typed per-field map.

    Enforces rule 1: a value arriving with no supporting clause text is dropped
    to "not found" rather than kept as an unsupported claim.
    """
    result: Dict[str, Dict[str, Optional[object]]] = {}
    for field in LEASE_FIELDS:
        entry = raw.get(field)
        if not isinstance(entry, dict):
            result[field] = _blank_field()
            continue
        value = _clean(entry.get("value"))
        source_text = _clean(entry.get("source_text"))
        if value is None or source_text is None:
            # Unsupported value → not found. This is the whole point: a number
            # with no clause behind it is exactly what would move a re-entry
            # date by years without anyone noticing.
            result[field] = _blank_field()
            continue
        page = entry.get("page")
        result[field] = {
            "value": value,
            "source_text": source_text,
            "page": int(page) if isinstance(page, int) else None,
            "found": True,
        }
    return result


def _extract_via_llm(pages: List[str], client=None) -> Dict[str, object]:
    """Call Anthropic with structured (tool-use) output; return the raw map.

    `client` is injectable so tests stub the network boundary. Raises
    MissingAPIKeyError when no key is configured — the caller turns that into
    "stored and linked, extraction skipped", never a crash.
    """
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key
        if not api_key:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set, so the lease could not be read. "
                "The file is stored and linked; add the key to backend/.env and "
                "re-run extraction."
            )
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

    user_content = (
        f"{_FIELD_GUIDE}\n\n"
        "Extract the nine fields from the lease below and record them with the "
        "record_lease_abstract tool. Quote the clause behind every value; "
        "return null for anything the document does not state.\n\n"
        f"--- LEASE DOCUMENT ---\n{_document_text(pages)}"
    )

    response = client.messages.create(
        # Read at call time so the model is a setting, not a code edit.
        model=settings.LEASE_EXTRACTION_MODEL,
        max_tokens=8000,
        system=_SYSTEM_PROMPT,
        tools=[_EXTRACTION_TOOL],
        messages=[{"role": "user", "content": user_content}],
    )

    for block in response.content:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == "record_lease_abstract"
        ):
            return block.input or {}
    raise ExtractionUnavailable(
        "The model did not return a structured lease abstract."
    )


def extract_lease(
    pages: List[str],
    extractor: Optional[Callable[[List[str]], Dict[str, object]]] = None,
) -> Dict[str, Dict[str, Optional[object]]]:
    """Extract the nine fields from already-read page text.

    `extractor` is injectable for tests; production uses the real Anthropic
    call. Raises MissingAPIKeyError or ExtractionUnavailable — both of which the
    upload path reports without losing the file.
    """
    fn = extractor or _extract_via_llm
    raw = fn(pages)
    if not isinstance(raw, dict):
        raise ExtractionUnavailable("Lease extraction returned no fields.")
    return _normalize(raw)


def extract_lease_from_pdf(
    pdf_bytes: bytes,
    extractor: Optional[Callable[[List[str]], Dict[str, object]]] = None,
) -> Dict[str, Dict[str, Optional[object]]]:
    """Read a PDF and extract the nine fields.

    An unreadable PDF raises ExtractionUnavailable rather than propagating a
    pdfplumber error, so the caller has one failure type to report against a
    file it has already stored.
    """
    try:
        pages = _extract_pages(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 — any parse failure is the same outcome
        raise ExtractionUnavailable(
            f"The PDF could not be read ({exc}). The file is stored and linked."
        ) from exc
    if not any((page or "").strip() for page in pages):
        # A scanned lease with no text layer. Nothing to abstract, but the
        # document itself is still worth keeping and linking.
        raise ExtractionUnavailable(
            "This PDF has no readable text (it may be a scan). The file is "
            "stored and linked; the fields need entering by hand."
        )
    return extract_lease(pages, extractor=extractor)


def dumps_extraction(extraction: Dict[str, Dict[str, Optional[object]]]) -> str:
    """Serialize the full extraction for Company.lease_extraction_json.

    Everything is stored, including the fields Jack unchecked and the ones that
    came back not-found: the point of the column is that any lease-sourced field
    traces back to its clause, and that trail has to include what was rejected.
    """
    return json.dumps(extraction, sort_keys=True, default=str)
