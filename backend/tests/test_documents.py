"""Phase B — lease extraction pipeline contract.

These tests lock the real behaviour of the extraction service without hitting
the network: the Anthropic HTTP boundary is the only thing stubbed (via an
injectable fake client / extractor). The PDF-text path, the tool-use response
parsing, the null-preservation ("no fabrication") rule, and the missing-key
error path are all exercised against the production code.
"""

import io
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.models.document  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.document import Document
from app.services import document_extraction_service as svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers: a real (tiny) text PDF, and a fake Anthropic client
# ---------------------------------------------------------------------------

def _make_text_pdf(text: str) -> bytes:
    """Build a minimal, valid single-page PDF whose text pdfplumber can read.

    Used to prove _extract_pages() actually parses PDF bytes (the BytesIO fix),
    rather than silently returning empty text.
    """
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objs) + 1))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objs) + 1, xref_pos)
    )
    return out.getvalue()


class _FakeToolUseBlock:
    type = "tool_use"
    name = "record_lease_fields"

    def __init__(self, tool_input):
        self.input = tool_input


class _FakeResponse:
    def __init__(self, tool_input):
        self.content = [_FakeToolUseBlock(tool_input)]


class _FakeAnthropic:
    """Stands in for anthropic.Anthropic — returns a canned structured response
    that simulates a correct model abstraction of the lease below."""

    def __init__(self, tool_input):
        self._tool_input = tool_input
        self.messages = self

    def create(self, **kwargs):  # noqa: D401 - mimics client.messages.create
        return _FakeResponse(self._tool_input)


# A synthetic lease: tenant, SF, commencement and expiration are clearly stated;
# base rent is genuinely absent from the text.
SYNTHETIC_LEASE = (
    "OFFICE LEASE AGREEMENT\n"
    "This Lease is entered into by Landlord and Acme Robotics, Inc. (\"Tenant\").\n"
    "Premises: approximately 12,500 square feet on the third floor.\n"
    "Commencement Date: 2024-03-01.\n"
    "Expiration Date: 2029-02-28.\n"
    "Tenant shall pay all utilities. No annual base rent figure is stated herein.\n"
)

# What a correct, non-fabricating model would return for the lease above.
CORRECT_TOOL_INPUT = {
    "tenant_name": {"value": "Acme Robotics, Inc.", "confidence": 0.98, "page": 1,
                    "snippet": "Acme Robotics, Inc. (\"Tenant\")"},
    "premises_sqft": {"value": "12500", "confidence": 0.95, "page": 1,
                      "snippet": "approximately 12,500 square feet"},
    "commencement_date": {"value": "2024-03-01", "confidence": 0.97, "page": 1,
                          "snippet": "Commencement Date: 2024-03-01"},
    "expiration_date": {"value": "2029-02-28", "confidence": 0.97, "page": 1,
                        "snippet": "Expiration Date: 2029-02-28"},
    # Genuinely absent -> the model must return null, never a guess.
    "base_rent_annual": {"value": None, "confidence": None, "page": None, "snippet": None},
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extract_pages_reads_real_pdf_text():
    """The BytesIO fix: _extract_pages must return the actual text, not ''."""
    pdf = _make_text_pdf("Commencement Date 2024-03-01 Acme")
    pages = svc._extract_pages(pdf)
    assert len(pages) == 1
    assert "Acme" in pages[0]
    assert "2024-03-01" in pages[0]


def test_llm_extraction_populates_present_fields_and_nulls_absent():
    """Fabrication check at the model boundary: four stated fields come back
    populated and correct; base rent, genuinely absent, comes back None."""
    fake = _FakeAnthropic(CORRECT_TOOL_INPUT)
    fields = svc._extract_fields_via_llm([SYNTHETIC_LEASE], client=fake)

    assert fields["tenant_name"]["value"] == "Acme Robotics, Inc."
    assert fields["premises_sqft"]["value"] == "12500"
    assert fields["commencement_date"]["value"] == "2024-03-01"
    assert fields["expiration_date"]["value"] == "2029-02-28"

    # The absent field must be a real None, with no fabricated metadata.
    assert fields["base_rent_annual"]["value"] is None
    assert fields["base_rent_annual"]["confidence"] is None
    assert fields["base_rent_annual"]["page"] is None
    assert fields["base_rent_annual"]["snippet"] is None


def test_extract_document_writes_five_rows_absent_field_null(db_session):
    """End-to-end mapping: five observation rows; absent base rent is stored as
    SQL NULL (real None), the four stated fields as populated strings."""
    doc = Document(filename="acme_lease.pdf", storage_path="x", extraction_status="pending")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    rows = svc.extract_document(
        doc, db_session, _make_text_pdf("Acme lease"),
        extractor=lambda pages: CORRECT_TOOL_INPUT,
    )
    by_field = {r.field: r for r in rows}

    assert len(rows) == 5
    assert set(by_field) == set(svc.FIELD_NAMES)
    assert by_field["tenant_name"].value == "Acme Robotics, Inc."
    assert by_field["premises_sqft"].value == "12500"
    assert by_field["base_rent_annual"].value is None
    assert by_field["base_rent_annual"].source_page is None
    assert doc.extraction_status == "done"


def test_normalize_coerces_blank_and_literal_null_to_none():
    """No fabrication may leak in as an empty or 'null' string value."""
    normalized = svc._normalize_fields({
        "tenant_name": {"value": "  ", "confidence": 0.5, "page": 1, "snippet": "x"},
        "base_rent_annual": {"value": "null", "confidence": 0.5, "page": 1, "snippet": "x"},
    })
    assert normalized["tenant_name"]["value"] is None
    assert normalized["base_rent_annual"]["value"] is None
    # Missing fields still appear, as None.
    assert normalized["expiration_date"]["value"] is None


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(svc.MissingAPIKeyError) as exc:
        svc._extract_fields_via_llm([SYNTHETIC_LEASE])
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_extract_endpoint_missing_key_returns_clear_error(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    files = {"file": ("lease.pdf", io.BytesIO(_make_text_pdf("Acme lease")), "application/pdf")}
    doc_id = client.post("/api/documents/", files=files).json()["id"]

    resp = client.post(f"/api/documents/{doc_id}/extract")
    assert resp.status_code == 500
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_upload_and_extract_endpoint_creates_five_observations(client, monkeypatch):
    monkeypatch.setattr(svc, "_extract_fields_via_llm", lambda pages, client=None: CORRECT_TOOL_INPUT)
    files = {"file": ("lease.pdf", io.BytesIO(_make_text_pdf("Acme lease 12500 SF")), "application/pdf")}
    doc_id = client.post("/api/documents/", files=files).json()["id"]

    resp = client.post(f"/api/documents/{doc_id}/extract")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document_id"] == doc_id
    assert len(body["observations"]) == 5
    values = {o["field"]: o["value"] for o in body["observations"]}
    assert values["base_rent_annual"] is None
    assert values["tenant_name"] == "Acme Robotics, Inc."
