"""
SF = real occupied SF (current_sf_occupied) — never an estimate.

After collapsing current_sf / estimated_sf_needed into the single
current_sf_occupied field and removing the headcount × growth × 175 projection,
this module locks the behaviour:

  (1) The SF used for a company everywhere is its real current_sf_occupied —
      headcount and growth rate never change it.
  (2) current_sf_occupied = null → SF is unknown: the property-side outreach draft
      falls back to the "right-sized office space" copy (no blank / placeholder /
      estimated number).
  (3) current_sf_occupied = null → the matched-tenant card still appears (matched
      on other signals) and shows "SF: Unknown" (decision: keep the card, block
      only outreach).
"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401 — resolves Property/Company relationships
import app.models.outreach_draft  # noqa: F401 — registered for completeness
from app.database import Base
from app.models.company import Company
from app.models.property import Property


# ── In-memory DB fixture ───────────────────────────────────────────────────────
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


def _fake_chat(system, user, *, max_tokens=600):
    """Structurally valid GPT-4o stand-in that contains NO SF figure of its own."""
    return (
        "SUBJECT: Test Subject\n"
        "EMAIL:\n"
        "Dear Property Owner,\n\n"
        "This is a test email body.\n\n"
        "Thank you,\n\n"
        "Jack Zamer\n"
        "OPENING:\nHello.\n"
        "CORE:\nCore.\n"
        "PAIN_PROBE:\nPain?\n"
        "CLOSE:\nI'd welcome a brief call at your convenience."
    )


def _property(db, **over):
    p = Property(
        property_id=over.get("property_id", "NVA-SF"),
        address="1 SF Plaza",
        submarket="Tysons",
        total_sf=50000,
        year_built=2005,
        sf_avail=over.get("sf_avail", 11000),
        owner_name="Owner LLC",
        in_place_rent_psf=35.0,
        market_rent_psf=38.0,
        market_cap_rate=6.5,
        listed_for_sale=False,
    )
    db.add(p)
    db.commit()
    return p


# ── (1) real occupied SF is the figure used — headcount/growth never change it ──
def test_sf_equals_real_occupied_sf(db_session):
    from app.api.routes.properties import _compute_matched_tenants

    prop = _property(db_session, sf_avail=11000)
    # Wildly large headcount and growth must NOT influence the SF figure.
    co = Company(
        company_id="CO-SF", name="SF Co", industry="Technology",
        current_submarket="Tysons", current_headcount=999,
        headcount_growth_pct=200.0, current_sf_occupied=11000,
    )
    db_session.add(co)
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    card = next((m for m in matched if m.company_id == "CO-SF"), None)
    assert card is not None
    assert card.sf_needed == 11000          # the real occupied SF, verbatim
    assert card.sf_display == "11,000 SF"


# ── (2) null occupied SF → "right-sized office space" fallback copy ─────────────
def test_outreach_draft_uses_right_sized_copy_when_sf_missing():
    import app.services.property_outreach_service as svc

    property_dict = {
        "property_id": "TST-001",
        "address": "100 Test Ave, McLean, VA",
        "submarket": "Tysons",
        "sf_avail": 5000,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 35.0,
        "vacancy_pct": 10.0,
        "listed_for_sale": False,
        "dominant_score_type": "tenant_match",
    }
    tenant_dict = {
        "name": "ZZZ Test Co",
        "industry": "Technology",
        "current_submarket": "Tysons",
        "current_sf_occupied": None,   # SF unknown → SF figure is None
        "lease_expiry_months": 12,
    }

    with patch.object(svc, "_chat", side_effect=_fake_chat):
        result = svc.generate_property_outreach(
            property_dict=property_dict,
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=tenant_dict,
        )

    body = result["email_body"]
    assert "right-sized office space" in body, (
        "When SF is missing the draft must use the 'right-sized office space' copy"
    )
    # Never a placeholder or a fabricated number in place of the missing SF.
    assert "[" not in body and "]" not in body, "No bracket placeholders allowed"


# ── (3) null occupied SF → match card shows "SF: Unknown" (card kept) ───────────
def test_match_card_shows_sf_unknown_when_occupied_missing(db_session):
    from app.api.routes.properties import _compute_matched_tenants, _sf_needed_display

    # The display helper is the single source of the card label.
    assert _sf_needed_display(None) == "Unknown"
    assert _sf_needed_display(0) == "Unknown"
    assert _sf_needed_display(11000) == "11,000 SF"

    prop = _property(db_session, property_id="NVA-CARD", sf_avail=5000)
    # Tenant with NO occupied SF, but matchable on submarket → should still appear.
    co = Company(
        company_id="CO-NOSF",
        name="No SF Co",
        industry="Technology",
        current_submarket="Tysons",
        current_sf_occupied=None,
        expansion_signal=True,
    )
    db_session.add(co)
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    card = next((m for m in matched if m.company_id == "CO-NOSF"), None)
    assert card is not None, "A tenant with unknown SF must still match on other signals"
    assert card.sf_needed is None
    assert card.sf_display == "Unknown"
    assert f"SF: {card.sf_display}" == "SF: Unknown"
