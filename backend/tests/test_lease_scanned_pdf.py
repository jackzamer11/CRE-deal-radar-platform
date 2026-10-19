"""A scanned lease is read off its page images, and the abstract is forced.

The bug this locks: a signed lease that went through DocuSign as a scan has a
text layer of exactly one line per page — "Docusign Envelope ID: ..." — and
none of the lease. That line got past the "no readable text" check, the model
was sent a document with nothing in it, and, with the tool not forced, it
answered in prose. Jack saw "The model did not return a structured lease
abstract." against a perfectly good signed addendum.

Two contracts:
  1. A page whose only text is the DocuSign stamp is a scan, and a scan is
     sent to the model as the PDF itself (page images), not as empty text.
  2. Every extraction call forces the record_lease_abstract tool, so the
     reply is always the structured abstract.

In-memory only: _extract_pages and the Anthropic client are stubbed.
"""
import base64
from types import SimpleNamespace

from app.services import lease_extraction_service as lease_svc

STAMP = "Docusign Envelope ID: 7B8BFB7A-E512-8370-825C-905E5234BCCB"
TEXT_PAGE = (
    "ADDENDUM NUMBER TEN TO LEASE AGREEMENT. The above referenced Lease "
    "Agreement and subsequent addenda are hereby amended and modified in the "
    "following manner: the existing Lease shall expire on December 31, 2026."
)


def _abstract():
    raw = {f: {"value": None, "source_text": None, "page": None} for f in lease_svc.LEASE_FIELDS}
    raw["lease_expiration_date"] = {
        "value": "December 31, 2027",
        "source_text": "resulting in a new lease expiration date of December 31, 2027",
        "page": 1,
    }
    return raw


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(
            type="tool_use", name="record_lease_abstract", input=_abstract(),
        )])


def _route(monkeypatch, pages):
    """Run extract_lease_from_pdf over `pages`; return the kwargs the LLM saw."""
    seen = {}

    def _fake_llm(p, client=None, pdf_bytes=None):
        seen["pages"] = p
        seen["pdf_bytes"] = pdf_bytes
        return _abstract()

    monkeypatch.setattr(lease_svc, "_extract_pages", lambda b: pages)
    monkeypatch.setattr(lease_svc, "_extract_via_llm", _fake_llm)
    result = lease_svc.extract_lease_from_pdf(b"%PDF-signed")
    assert result["lease_expiration_date"]["value"] == "December 31, 2027"
    return seen


# ── 1. A DocuSign-stamped scan is read as a PDF ──────────────────────────────

def test_a_docusign_stamp_alone_is_not_a_text_layer(monkeypatch):
    seen = _route(monkeypatch, [STAMP])
    assert seen["pdf_bytes"] == b"%PDF-signed"


def test_a_page_with_no_text_at_all_is_read_as_a_pdf(monkeypatch):
    seen = _route(monkeypatch, [""])
    assert seen["pdf_bytes"] == b"%PDF-signed"


def test_a_lease_with_one_scanned_page_is_read_as_a_pdf(monkeypatch):
    """A text lease with a scanned signature page or addendum still needs images."""
    seen = _route(monkeypatch, [TEXT_PAGE, STAMP])
    assert seen["pdf_bytes"] == b"%PDF-signed"


def test_a_pdf_pdfplumber_cannot_open_is_still_sent_to_the_model(monkeypatch):
    def _broken(_b):
        raise ValueError("bad xref")

    seen = {}
    monkeypatch.setattr(lease_svc, "_extract_pages", _broken)
    monkeypatch.setattr(
        lease_svc, "_extract_via_llm",
        lambda p, client=None, pdf_bytes=None: seen.setdefault("pdf", pdf_bytes) and _abstract(),
    )
    lease_svc.extract_lease_from_pdf(b"%PDF-odd")
    assert seen["pdf"] == b"%PDF-odd"


def test_a_text_lease_is_read_from_its_text(monkeypatch):
    stamped = f"{STAMP}\n{TEXT_PAGE}"
    seen = _route(monkeypatch, [stamped, TEXT_PAGE])
    assert seen["pdf_bytes"] is None
    assert seen["pages"] == [stamped, TEXT_PAGE]


# ── 2. The abstract is forced, and the PDF reaches the model ─────────────────

def test_the_pdf_route_sends_the_document_and_forces_the_tool():
    client = _FakeClient()
    raw = lease_svc._extract_via_llm([STAMP], client=client, pdf_bytes=b"%PDF-signed")
    assert raw["lease_expiration_date"]["value"] == "December 31, 2027"

    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_lease_abstract"}
    content = call["messages"][0]["content"]
    document = next(block for block in content if block["type"] == "document")
    assert document["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(document["source"]["data"]) == b"%PDF-signed"


def test_the_text_route_forces_the_tool_too():
    client = _FakeClient()
    lease_svc._extract_via_llm([TEXT_PAGE], client=client)
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_lease_abstract"}
    assert TEXT_PAGE in call["messages"][0]["content"]
