"""
Tenant Class Deriver — contract tests (in-memory SQLite only, no live DB).

Covers all 12 spec requirements:
  1.  Exact single-property match → confidence 100, auto-fills
  2.  Exact match but multiple companies share address → confidence 75, logs (not auto-filled)
  3.  Partial match (street matches, zip/city differs) → confidence 50, logs (not auto-filled)
  4.  No match → confidence 0, logged as unmatched
  5.  backfill=False processes only null-class tenants; backfill=True processes all
  6.  User correction is logged to feedback table
  7.  Next deriver run reads feedback and uses corrected class instead of re-matching
  8.  Feedback prevents repeat mistakes (same address → same class, no re-match)
  9.  dry_run=True returns preview without persisting; dry_run=False saves changes
 10.  Admin endpoint returns correct JSON summary structure
 11.  Logging appears in pipeline logs
 12.  null current_address is skipped gracefully
"""
import logging
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models          # noqa: F401 — registers core models (includes TenantClassFeedback)
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.company import Company
from app.models.property import Property
from app.models.tenant_class_feedback import TenantClassFeedback


# ── In-memory DB fixture ──────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ── Helpers ───────────────────────────────────────────────────────────────────

_prop_counter = [0]
_co_counter = [0]


def _make_property(session, *, address="100 Test Blvd, Tysons, VA 22102",
                   asset_class="Class B"):
    _prop_counter[0] += 1
    prop = Property(
        property_id=f"NVA-T{_prop_counter[0]}",
        address=address,
        submarket="Tysons",
        asset_class=asset_class,
        total_sf=50_000,
        year_built=2000,
        owner_name="Test Owner LLC",
        in_place_rent_psf=38.0,
        market_rent_psf=40.0,
        market_cap_rate=6.5,
        listed_for_sale=False,
    )
    session.add(prop)
    return prop


def _make_company(session, *, address=None, building_class=None,
                  company_id=None):
    _co_counter[0] += 1
    cid = company_id or f"CO-T{_co_counter[0]}"
    co = Company(
        company_id=cid,
        name=f"Tenant {cid}",
        industry="Technology",
        current_address=address,
        current_building_class=building_class,
    )
    session.add(co)
    return co


# ── 1. Confidence 100: exact single match → auto-fill ────────────────────────

def test_confidence_100_exact_match_autofills(db_session):
    from app.services.tenant_class_deriver import (
        match_address_to_property,
        derive_tenant_building_classes,
    )

    addr = "123 Innovation Dr, Reston, VA 20190"
    _make_property(db_session, address=addr, asset_class="Class A")
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    result = match_address_to_property(addr, db_session)
    assert result is not None
    assert result["confidence"] == 100
    assert result["matched_class"] == "Class A"
    assert result["property_id"] is not None

    stats = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co)

    assert co.current_building_class == "Class A"
    assert stats["auto_filled_100_confidence"] == 1
    assert stats["total_processed"] == 1


# ── 2. Confidence 75: multi-tenant address → logs, does NOT auto-fill ─────────

def test_confidence_75_multi_tenant_address_logs_not_autofill(db_session):
    from app.services.tenant_class_deriver import (
        match_address_to_property,
        derive_tenant_building_classes,
    )

    addr = "200 Shared Plaza, McLean, VA 22101"
    _make_property(db_session, address=addr, asset_class="Class A")
    co1 = _make_company(db_session, address=addr, building_class=None, company_id="CO-MULTI-A")
    co2 = _make_company(db_session, address=addr, building_class=None, company_id="CO-MULTI-B")
    db_session.commit()

    result = match_address_to_property(addr, db_session)
    assert result is not None
    assert result["confidence"] == 75
    assert result["matched_class"] == "Class A"

    stats = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co1)
    db_session.refresh(co2)

    # Neither company should be auto-filled
    assert co1.current_building_class is None
    assert co2.current_building_class is None
    assert len(stats["logged_75_confidence"]) == 2
    assert stats["auto_filled_100_confidence"] == 0


# ── 3. Confidence 50: partial / street-only match → logs, does NOT auto-fill ──

def test_confidence_50_partial_match_logs_not_autofill(db_session):
    from app.services.tenant_class_deriver import (
        match_address_to_property,
        derive_tenant_building_classes,
    )

    # Property address has different city/zip
    _make_property(db_session, address="300 Market St, Fairfax, VA 22030",
                   asset_class="Class C")
    # Tenant has same street but different city
    co = _make_company(db_session,
                       address="300 Market St, Arlington, VA 22201",
                       building_class=None)
    db_session.commit()

    result = match_address_to_property("300 Market St, Arlington, VA 22201", db_session)
    assert result is not None
    assert result["confidence"] == 50
    assert result["matched_class"] == "Class C"

    stats = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co)

    assert co.current_building_class is None  # NOT auto-filled
    assert len(stats["logged_50_confidence"]) == 1
    assert stats["auto_filled_100_confidence"] == 0


# ── 4. Confidence 0: no match → logged as unmatched ──────────────────────────

def test_confidence_0_no_match_logged_as_unmatched(db_session):
    from app.services.tenant_class_deriver import (
        match_address_to_property,
        derive_tenant_building_classes,
    )

    # No property in the universe at all
    co = _make_company(db_session, address="999 Nowhere Ln, Leesburg, VA 20176",
                       building_class=None)
    db_session.commit()

    result = match_address_to_property("999 Nowhere Ln, Leesburg, VA 20176", db_session)
    assert result is None

    stats = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co)

    assert co.current_building_class is None
    assert len(stats["unmatched"]) == 1
    assert stats["unmatched"][0]["name"] == co.name
    assert stats["unmatched"][0]["address"] == co.current_address


# ── 5. backfill=False skips already-classified; backfill=True processes all ───

def test_backfill_false_only_processes_null_class_tenants(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr_a = "401 Alpha Ct, Vienna, VA 22180"
    addr_b = "402 Beta Ct, Vienna, VA 22180"
    _make_property(db_session, address=addr_a, asset_class="Class A")
    _make_property(db_session, address=addr_b, asset_class="Class B")

    co_null = _make_company(db_session, address=addr_a, building_class=None)
    co_set  = _make_company(db_session, address=addr_b, building_class="Class C")
    db_session.commit()

    stats = derive_tenant_building_classes(db_session, backfill=False)
    db_session.refresh(co_null)
    db_session.refresh(co_set)

    # Only the null-class tenant was touched
    assert co_null.current_building_class == "Class A"
    assert co_set.current_building_class == "Class C"  # unchanged
    assert stats["total_processed"] == 1


def test_backfill_true_processes_all_tenants(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr_a = "501 Alpha Way, Herndon, VA 20170"
    addr_b = "502 Beta Way, Herndon, VA 20170"
    _make_property(db_session, address=addr_a, asset_class="Class A")
    _make_property(db_session, address=addr_b, asset_class="Class B")

    co_null = _make_company(db_session, address=addr_a, building_class=None)
    co_set  = _make_company(db_session, address=addr_b, building_class="Class C")
    db_session.commit()

    stats = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co_null)
    db_session.refresh(co_set)

    # Both processed — co_set gets overwritten by property match
    assert co_null.current_building_class == "Class A"
    assert co_set.current_building_class == "Class B"   # overwritten
    assert stats["total_processed"] == 2


# ── 6. User correction is logged to feedback table ───────────────────────────

def test_user_correction_logged_to_feedback_table(db_session):
    from app.services.tenant_class_deriver import record_building_class_feedback

    addr = "600 Feedback Ave, Tysons, VA 22102"
    _make_property(db_session, address=addr, asset_class="Class B")
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    # User corrects to Class A (deriver would say Class B — the property's class)
    record_building_class_feedback(co, "Class A", db_session)

    fb = db_session.query(TenantClassFeedback).filter(
        TenantClassFeedback.company_id == co.id
    ).first()
    assert fb is not None
    assert fb.user_corrected_class == "Class A"
    assert fb.inferred_class == "Class B"
    assert fb.current_address == addr


def test_no_feedback_when_user_confirms_deriver_guess(db_session):
    from app.services.tenant_class_deriver import record_building_class_feedback

    addr = "601 Confirm St, Tysons, VA 22102"
    _make_property(db_session, address=addr, asset_class="Class B")
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    # User enters Class B — same as what deriver would infer → no feedback row
    record_building_class_feedback(co, "Class B", db_session)

    count = db_session.query(TenantClassFeedback).count()
    assert count == 0


# ── 7. Next deriver run reads feedback and uses corrected class ───────────────

def test_deriver_uses_feedback_on_next_run(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "700 Feedback Loop Rd, Reston, VA 20190"
    # Property says Class B
    _make_property(db_session, address=addr, asset_class="Class B")
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    # Manually record a feedback entry saying Class A should be used
    fb = TenantClassFeedback(
        company_id=co.id,
        current_address=addr,
        inferred_class="Class B",
        user_corrected_class="Class A",
    )
    db_session.add(fb)
    db_session.commit()

    stats = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co)

    # Must use Class A from feedback, not Class B from property
    assert co.current_building_class == "Class A"
    assert stats["feedback_hits"] == 1
    assert stats["auto_filled_100_confidence"] == 0


# ── 8. Feedback prevents repeat mistakes ─────────────────────────────────────

def test_feedback_prevents_repeat_mistakes(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "800 Memory Lane, Falls Church, VA 22042"
    _make_property(db_session, address=addr, asset_class="Class C")  # would infer Class C
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    # First run: no feedback → deriver infers Class C (confidence 100, auto-fills)
    stats1 = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co)
    assert co.current_building_class == "Class C"
    assert stats1["auto_filled_100_confidence"] == 1
    assert stats1["feedback_hits"] == 0

    # Simulate user correction to Class A → add feedback entry
    fb = TenantClassFeedback(
        company_id=co.id,
        current_address=addr,
        inferred_class="Class C",
        user_corrected_class="Class A",
    )
    db_session.add(fb)
    co.current_building_class = None  # reset to test re-run
    db_session.commit()

    # Second run: feedback present → uses Class A, no re-matching
    stats2 = derive_tenant_building_classes(db_session, backfill=True)
    db_session.refresh(co)
    assert co.current_building_class == "Class A"
    assert stats2["feedback_hits"] == 1
    assert stats2["auto_filled_100_confidence"] == 0


# ── 9. dry_run=True previews without persisting; dry_run=False saves ──────────

def test_dry_run_returns_preview_without_persisting(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "900 Dry Run Blvd, Vienna, VA 22180"
    _make_property(db_session, address=addr, asset_class="Class A")
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    stats = derive_tenant_building_classes(db_session, backfill=True, dry_run=True)

    # Preview shows a match, but DB not written
    assert stats["auto_filled_100_confidence"] == 1
    db_session.refresh(co)
    assert co.current_building_class is None  # not persisted


def test_persist_run_saves_changes(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "901 Persist Ave, Vienna, VA 22180"
    _make_property(db_session, address=addr, asset_class="Class B")
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    stats = derive_tenant_building_classes(db_session, backfill=True, dry_run=False)

    db_session.refresh(co)
    assert co.current_building_class == "Class B"
    assert stats["auto_filled_100_confidence"] == 1


# ── 10. Endpoint response JSON structure ─────────────────────────────────────

def test_endpoint_returns_correct_json_structure(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "1000 Structure Rd, McLean, VA 22101"
    _make_property(db_session, address=addr, asset_class="Class A")
    _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    result = derive_tenant_building_classes(db_session, backfill=True, dry_run=True)

    required_keys = {
        "total_processed",
        "auto_filled_100_confidence",
        "logged_75_confidence",
        "logged_50_confidence",
        "unmatched",
        "feedback_hits",
    }
    assert required_keys == set(result.keys()), "Response must contain exactly the documented fields"
    assert isinstance(result["total_processed"], int)
    assert isinstance(result["auto_filled_100_confidence"], int)
    assert isinstance(result["logged_75_confidence"], list)
    assert isinstance(result["logged_50_confidence"], list)
    assert isinstance(result["unmatched"], list)
    assert isinstance(result["feedback_hits"], int)


def test_logged_75_entries_have_correct_shape(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "1001 Shape St, McLean, VA 22101"
    _make_property(db_session, address=addr, asset_class="Class B")
    _make_company(db_session, address=addr, building_class=None, company_id="CO-SHP-1")
    _make_company(db_session, address=addr, building_class=None, company_id="CO-SHP-2")
    db_session.commit()

    result = derive_tenant_building_classes(db_session, backfill=True, dry_run=True)

    assert len(result["logged_75_confidence"]) == 2
    for entry in result["logged_75_confidence"]:
        assert "company_id" in entry
        assert "name" in entry
        assert "address" in entry
        assert "matched_class" in entry
        assert "reason" in entry


def test_unmatched_entries_have_correct_shape(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    # No property → unmatched
    _make_company(db_session, address="1002 Unknown Ct, Leesburg, VA 20176",
                  building_class=None)
    db_session.commit()

    result = derive_tenant_building_classes(db_session, backfill=True, dry_run=True)

    assert len(result["unmatched"]) == 1
    entry = result["unmatched"][0]
    assert "company_id" in entry
    assert "name" in entry
    assert "address" in entry


# ── 11. Logging appears in pipeline logs ─────────────────────────────────────

def test_pipeline_logger_emits_summary(db_session, caplog):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "1100 Log Way, Herndon, VA 20170"
    _make_property(db_session, address=addr, asset_class="Class B")
    _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    with caplog.at_level(logging.INFO, logger="deal_radar.pipeline"):
        derive_tenant_building_classes(db_session, backfill=True)

    messages = "\n".join(caplog.messages)
    assert "TenantClassDeriver" in messages
    assert "processed" in messages


def test_pipeline_logger_emits_per_tenant_match(db_session, caplog):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "1101 Debug Blvd, Reston, VA 20190"
    _make_property(db_session, address=addr, asset_class="Class A")
    _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    with caplog.at_level(logging.INFO, logger="deal_radar.pipeline"):
        derive_tenant_building_classes(db_session, backfill=True)

    messages = "\n".join(caplog.messages)
    assert "Auto-fill" in messages


# ── 12. null current_address is skipped gracefully ───────────────────────────

def test_null_address_skipped_gracefully(db_session):
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    co_null_addr = _make_company(db_session, address=None, building_class=None)
    co_blank_addr = _make_company(db_session, address="   ", building_class=None)
    db_session.commit()

    stats = derive_tenant_building_classes(db_session, backfill=True)

    # Neither company counted as processed
    assert stats["total_processed"] == 0
    assert stats["auto_filled_100_confidence"] == 0
    assert stats["unmatched"] == []
    db_session.refresh(co_null_addr)
    db_session.refresh(co_blank_addr)
    assert co_null_addr.current_building_class is None
    assert co_blank_addr.current_building_class is None


def test_match_address_to_property_null_address_returns_none(db_session):
    from app.services.tenant_class_deriver import match_address_to_property

    _make_property(db_session, address="1200 Any St, Tysons, VA")
    db_session.commit()

    assert match_address_to_property(None, db_session) is None
    assert match_address_to_property("", db_session) is None
    assert match_address_to_property("   ", db_session) is None


# ── Additional edge-case tests ────────────────────────────────────────────────

def test_address_match_is_case_insensitive(db_session):
    from app.services.tenant_class_deriver import match_address_to_property

    _make_property(db_session, address="100 Main St, Tysons, VA", asset_class="Class A")
    db_session.commit()

    result = match_address_to_property("100 MAIN ST, TYSONS, VA", db_session)
    assert result is not None
    assert result["confidence"] == 100
    assert result["matched_class"] == "Class A"


def test_address_match_trims_whitespace(db_session):
    from app.services.tenant_class_deriver import match_address_to_property

    _make_property(db_session, address="  200 Park Ave, Vienna, VA  ", asset_class="Class B")
    db_session.commit()

    result = match_address_to_property("200 Park Ave, Vienna, VA", db_session)
    assert result is not None
    assert result["confidence"] == 100


def test_multiple_properties_different_addresses_no_cross_match(db_session):
    """Two distinct properties — tenant at addr_a must not match property at addr_b."""
    from app.services.tenant_class_deriver import match_address_to_property

    addr_a = "300 Alpha Blvd, Reston, VA 20190"
    addr_b = "301 Beta Blvd, Reston, VA 20190"
    _make_property(db_session, address=addr_a, asset_class="Class A")
    _make_property(db_session, address=addr_b, asset_class="Class C")
    db_session.commit()

    result = match_address_to_property(addr_a, db_session)
    assert result is not None
    assert result["matched_class"] == "Class A"
    assert result["confidence"] == 100


def test_backfill_with_mix_of_match_types(db_session):
    """One auto-fill, one 75, one 50, one unmatched in a single backfill run."""
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    # Setup for confidence 100
    addr_100 = "1 Solo Way, McLean, VA 22101"
    _make_property(db_session, address=addr_100, asset_class="Class A")
    _make_company(db_session, address=addr_100, building_class=None, company_id="CO-100")

    # Setup for confidence 75 (two companies at same address)
    addr_75 = "2 Multi Tower, McLean, VA 22101"
    _make_property(db_session, address=addr_75, asset_class="Class B")
    _make_company(db_session, address=addr_75, building_class=None, company_id="CO-75A")
    _make_company(db_session, address=addr_75, building_class=None, company_id="CO-75B")

    # Setup for confidence 50 (street matches, city differs)
    _make_property(db_session, address="3 Fuzzy Ln, Tysons, VA 22102", asset_class="Class C")
    _make_company(db_session, address="3 Fuzzy Ln, Vienna, VA 22180",
                  building_class=None, company_id="CO-50")

    # Setup for unmatched
    _make_company(db_session, address="99 Ghost Rd, Leesburg, VA 20176",
                  building_class=None, company_id="CO-0")

    db_session.commit()

    stats = derive_tenant_building_classes(db_session, backfill=True)

    assert stats["auto_filled_100_confidence"] == 1
    assert len(stats["logged_75_confidence"]) == 2
    assert len(stats["logged_50_confidence"]) == 1
    assert len(stats["unmatched"]) == 1
    assert stats["total_processed"] == 5
