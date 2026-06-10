"""
Standalone Tenant Outreach email — no rent PSF and no jargon.

Confirms that the post-LLM strip pipeline applied by generate_outreach() removes
any rent-PSF figures or jargon terms (flight-to-quality, sublease supply,
TI allowances, NoVA average, free rent) from the email body, even if the LLM
sneaks them in.
"""
import json
import os
from unittest.mock import MagicMock, patch

import app.services.outreach_service as svc


_COMPANY = {
    "name": "Test Co",
    "current_submarket": "Tysons",
    "current_sf_occupied": 2400,
    "lease_expiry_months": 14,
    "primary_contact_name": "Jane",
    "industry": "Technology",
    "tenant_representative": "",
    "opportunity_score": 75,
    "priority": "HIGH",
}

# A mock LLM response body that intentionally embeds every banned term.
_DIRTY_BODY = (
    "Hi Jane,\n\n"
    "With your lease expiring in 14 months at 2,400 SF in Tysons, "
    "I wanted to reach out.\n\n"
    "The flight-to-quality trend is reshaping the Tysons market. "
    "Sublease supply has expanded significantly this quarter. "
    "TI allowances are at record levels right now. "
    "Free rent concessions are being extended by landlords. "
    "The market rate is $38.50/SF against the NoVA average of $34.20/SF.\n\n"
    "I'd welcome a brief call at your convenience.\n\n"
    "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228"
)


def _make_mock_client(body: str) -> MagicMock:
    payload = json.dumps({
        "email": {"subject": "Tysons lease timing", "body": body},
        "call_script": {
            "opening": "I specialize in Tysons.",
            "core_message": "Your lease expires in 14 months.",
            "pain_probe": "What's driving your space planning this cycle?",
            "the_close": "I'd welcome a brief call at your convenience.",
        },
    })
    choice = MagicMock()
    choice.message.content = payload
    completion = MagicMock()
    completion.choices = [choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = completion
    return mock_client


def test_standalone_outreach_no_psf_no_jargon():
    """Even when the LLM injects banned terms, the strip pipeline removes them."""
    with (
        patch("openai.OpenAI", return_value=_make_mock_client(_DIRTY_BODY)),
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        patch.object(svc, "_web_search_company_intel", return_value=""),
    ):
        result = svc.generate_outreach(_COMPANY)

    body = result["email"]["body"]

    # No rent PSF figures
    assert "/SF" not in body, f"rent PSF (/SF) must not appear in email body:\n{body}"
    assert "PSF" not in body, f"PSF must not appear in email body:\n{body}"

    # No jargon
    for term in (
        "flight-to-quality",
        "sublease supply",
        "TI allowance",
        "NoVA average",
        "free rent",
    ):
        assert term.lower() not in body.lower(), (
            f"jargon term {term!r} must not appear in email body:\n{body}"
        )


def test_standalone_outreach_fact_sentences_survive():
    """Fact-bearing sentences that contain none of the banned terms are preserved."""
    clean_body = (
        "Hi Jane,\n\n"
        "With your lease expiring in 14 months at 2,400 SF in Tysons, "
        "I wanted to flag a few options in the submarket.\n\n"
        "I specialize in Tysons office deals and have active off-market relationships "
        "with the key landlords in your building class.\n\n"
        "I'd welcome a brief call at your convenience.\n\n"
        "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228"
    )
    with (
        patch("openai.OpenAI", return_value=_make_mock_client(clean_body)),
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        patch.object(svc, "_web_search_company_intel", return_value=""),
    ):
        result = svc.generate_outreach(_COMPANY)

    body = result["email"]["body"]
    assert "14 months" in body, "lease timing fact must survive the strip"
    assert "Tysons" in body, "submarket fact must survive the strip"
