"""
Tenant Class Deriver — contract tests (in-memory SQLite only, no live DB).

Covers all 12 original spec requirements:
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


# ═══════════════════════════════════════════════════════════════════════════════
# CoStar two-tier matching tests
# These tests inject a mock CoStar lookup dict directly — no xlsx files needed.
# ═══════════════════════════════════════════════════════════════════════════════

# ── CoStar lookup builder helpers ─────────────────────────────────────────────

def test_costar_class_normalization():
    """Single-letter CoStar classes must be mapped to platform format."""
    import pandas as pd
    from app.services.tenant_class_deriver import _build_costar_lookup_from_dataframes

    df = pd.DataFrame({
        "Address": ["1 A Blvd, Reston, VA", "2 B St, Tysons, VA", "3 C Ave, Vienna, VA"],
        "Class": ["A", "B", "C"],
    })
    result = _build_costar_lookup_from_dataframes([df])
    assert result["1 a blvd, reston, va"] == "Class A"
    assert result["2 b st, tysons, va"] == "Class B"
    assert result["3 c ave, vienna, va"] == "Class C"


def test_costar_invalid_class_values_skipped():
    """Rows whose Class is not A/B/C must be silently ignored."""
    import pandas as pd
    from app.services.tenant_class_deriver import _build_costar_lookup_from_dataframes

    df = pd.DataFrame({
        "Address": ["1 Trophy Tower, McLean, VA", "2 Real St, Reston, VA"],
        "Class": ["Trophy", "B"],
    })
    result = _build_costar_lookup_from_dataframes([df])
    assert "1 trophy tower, mclean, va" not in result
    assert result["2 real st, reston, va"] == "Class B"


def test_costar_deduplication_first_occurrence_wins():
    """When the same address appears in two DataFrames, the first wins."""
    import pandas as pd
    from app.services.tenant_class_deriver import _build_costar_lookup_from_dataframes

    df1 = pd.DataFrame({"Address": ["100 Main St, Fairfax, VA"], "Class": ["A"]})
    df2 = pd.DataFrame({"Address": ["100 Main St, Fairfax, VA"], "Class": ["C"]})
    result = _build_costar_lookup_from_dataframes([df1, df2])
    assert result["100 main st, fairfax, va"] == "Class A"   # first wins


def test_costar_deduplication_preserves_other_entries():
    """Deduplication must not discard unique entries from later files."""
    import pandas as pd
    from app.services.tenant_class_deriver import _build_costar_lookup_from_dataframes

    df1 = pd.DataFrame({"Address": ["100 Alpha Ct, Reston, VA"], "Class": ["A"]})
    df2 = pd.DataFrame({"Address": ["200 Beta Ct, Herndon, VA"], "Class": ["B"]})
    result = _build_costar_lookup_from_dataframes([df1, df2])
    assert result["100 alpha ct, reston, va"] == "Class A"
    assert result["200 beta ct, herndon, va"] == "Class B"


def test_costar_address_normalization_in_builder():
    """Builder must store addresses lowercased so lookups are case-insensitive."""
    import pandas as pd
    from app.services.tenant_class_deriver import _build_costar_lookup_from_dataframes

    df = pd.DataFrame({"Address": ["  300 UPPER ST, McLean, VA  "], "Class": ["B"]})
    result = _build_costar_lookup_from_dataframes([df])
    assert "300 upper st, mclean, va" in result


# ── CoStar tier in match_address_to_property ─────────────────────────────────

def test_costar_exact_match_returns_confidence_100(db_session):
    """A tenant address found in the CoStar lookup → confidence 100, no DB hit needed."""
    from app.services.tenant_class_deriver import match_address_to_property

    costar = {"1000 costar blvd, reston, va 20190": "Class A"}
    result = match_address_to_property(
        "1000 CoStar Blvd, Reston, VA 20190",
        db_session,
        _costar_lookup=costar,
    )
    assert result is not None
    assert result["confidence"] == 100
    assert result["matched_class"] == "Class A"
    # CoStar matches have no platform DB property_id
    assert result["property_id"] is None


def test_costar_exact_match_is_case_insensitive(db_session):
    """CoStar lookup match must be case-insensitive."""
    from app.services.tenant_class_deriver import match_address_to_property

    costar = {"100 tech park dr, herndon, va": "Class B"}
    result = match_address_to_property(
        "100 TECH PARK DR, HERNDON, VA",
        db_session,
        _costar_lookup=costar,
    )
    assert result is not None
    assert result["confidence"] == 100
    assert result["matched_class"] == "Class B"


def test_costar_exact_match_multi_tenant_is_confidence_75(db_session):
    """CoStar exact match with multiple companies at that address → confidence 75."""
    from app.services.tenant_class_deriver import match_address_to_property

    addr = "200 Shared Tower, McLean, VA 22102"
    costar = {addr.lower(): "Class A"}
    _make_company(db_session, address=addr, company_id="CO-CS75A")
    _make_company(db_session, address=addr, company_id="CO-CS75B")
    db_session.commit()

    result = match_address_to_property(addr, db_session, _costar_lookup=costar)
    assert result is not None
    assert result["confidence"] == 75
    assert result["matched_class"] == "Class A"


def test_costar_partial_match_is_confidence_50(db_session):
    """Street matches CoStar entry but full address differs → confidence 50."""
    from app.services.tenant_class_deriver import match_address_to_property

    costar = {"300 park blvd, tysons, va 22102": "Class C"}
    result = match_address_to_property(
        "300 Park Blvd, Vienna, VA 22180",   # same street, different city
        db_session,
        _costar_lookup=costar,
    )
    assert result is not None
    assert result["confidence"] == 50
    assert result["matched_class"] == "Class C"


def test_costar_miss_falls_back_to_platform_db(db_session):
    """If the CoStar lookup has no match, the platform DB is tried next."""
    from app.services.tenant_class_deriver import match_address_to_property

    addr = "400 Fallback Way, Vienna, VA 22180"
    _make_property(db_session, address=addr, asset_class="Class B")
    db_session.commit()

    # CoStar lookup is empty — must fall through to DB
    result = match_address_to_property(addr, db_session, _costar_lookup={})
    assert result is not None
    assert result["confidence"] == 100
    assert result["matched_class"] == "Class B"
    assert result["property_id"] is not None   # came from platform DB


def test_costar_miss_and_db_miss_returns_none(db_session):
    """No match in either tier → None (confidence 0)."""
    from app.services.tenant_class_deriver import match_address_to_property

    result = match_address_to_property(
        "999 Ghost Rd, Leesburg, VA 20176",
        db_session,
        _costar_lookup={},
    )
    assert result is None


# ── CoStar tier in derive_tenant_building_classes ─────────────────────────────

def test_derive_uses_costar_lookup_when_injected(db_session):
    """derive_tenant_building_classes must use the injected CoStar lookup."""
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "1500 CoStar Match Ave, Reston, VA 20190"
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    costar = {addr.lower(): "Class A"}
    stats = derive_tenant_building_classes(
        db_session, backfill=True, _costar_lookup=costar
    )
    db_session.refresh(co)

    assert co.current_building_class == "Class A"
    assert stats["auto_filled_100_confidence"] == 1
    assert stats["feedback_hits"] == 0


def test_derive_falls_back_to_db_when_costar_empty(db_session):
    """Empty CoStar lookup → platform DB fallback, class still auto-filled."""
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "1600 DB Only Rd, Tysons, VA 22102"
    _make_property(db_session, address=addr, asset_class="Class B")
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    stats = derive_tenant_building_classes(
        db_session, backfill=True, _costar_lookup={}
    )
    db_session.refresh(co)

    assert co.current_building_class == "Class B"
    assert stats["auto_filled_100_confidence"] == 1


def test_costar_match_respected_in_dry_run(db_session):
    """dry_run=True with CoStar match reports the hit but does not persist."""
    from app.services.tenant_class_deriver import derive_tenant_building_classes

    addr = "1700 Dry Run CoStar Blvd, Herndon, VA 20170"
    co = _make_company(db_session, address=addr, building_class=None)
    db_session.commit()

    costar = {addr.lower(): "Class C"}
    stats = derive_tenant_building_classes(
        db_session, backfill=True, dry_run=True, _costar_lookup=costar
    )

    assert stats["auto_filled_100_confidence"] == 1
    db_session.refresh(co)
    assert co.current_building_class is None   # not written


# ── _load_costar_lookup path / warning tests ──────────────────────────────────

def test_load_costar_lookup_missing_dir_returns_empty_and_warns(tmp_path, caplog):
    """_load_costar_lookup with a non-existent dir → {} + WARNING log."""
    from app.services.tenant_class_deriver import _load_costar_lookup

    missing = tmp_path / "no_such_dir"
    with caplog.at_level(logging.WARNING, logger="deal_radar.pipeline"):
        result = _load_costar_lookup(lookup_dir=missing)

    assert result == {}
    combined = "\n".join(caplog.messages)
    assert "not found" in combined.lower() or "costar_lookup" in combined.lower()


def test_load_costar_lookup_empty_dir_returns_empty_and_warns(tmp_path, caplog):
    """_load_costar_lookup with an existing but empty dir → {} + WARNING log."""
    from app.services.tenant_class_deriver import _load_costar_lookup

    empty_dir = tmp_path / "costar_lookup"
    empty_dir.mkdir()
    with caplog.at_level(logging.WARNING, logger="deal_radar.pipeline"):
        result = _load_costar_lookup(lookup_dir=empty_dir)

    assert result == {}
    assert any("costar" in m.lower() for m in caplog.messages)


def test_load_costar_lookup_loads_real_xlsx(tmp_path):
    """_load_costar_lookup reads xlsx files and returns normalized lookup."""
    import pandas as pd
    from app.services.tenant_class_deriver import _load_costar_lookup

    lkp_dir = tmp_path / "costar_lookup"
    lkp_dir.mkdir()
    df = pd.DataFrame({
        "Address": ["100 Real Way, Reston, VA", "200 Test Blvd, Tysons, VA"],
        "Class": ["A", "B"],
    })
    df.to_excel(lkp_dir / "CostarExport (27).xlsx", index=False)

    result = _load_costar_lookup(lookup_dir=lkp_dir)
    assert result["100 real way, reston, va"] == "Class A"
    assert result["200 test blvd, tysons, va"] == "Class B"


def test_load_costar_lookup_deduplicates_across_files(tmp_path):
    """First-file-wins deduplication works when the same address spans files."""
    import pandas as pd
    from app.services.tenant_class_deriver import _load_costar_lookup

    lkp_dir = tmp_path / "costar_lookup"
    lkp_dir.mkdir()
    pd.DataFrame({"Address": ["300 Dup St, McLean, VA"], "Class": ["A"]}).to_excel(
        lkp_dir / "CostarExport (27).xlsx", index=False
    )
    pd.DataFrame({"Address": ["300 Dup St, McLean, VA"], "Class": ["C"]}).to_excel(
        lkp_dir / "CostarExport (28).xlsx", index=False
    )

    result = _load_costar_lookup(lookup_dir=lkp_dir)
    assert result["300 dup st, mclean, va"] == "Class A"   # file 27 wins


def test_load_costar_lookup_bad_file_skipped_gracefully(tmp_path, caplog):
    """A corrupt/unreadable file is skipped with a warning; valid files still load."""
    import pandas as pd
    from app.services.tenant_class_deriver import _load_costar_lookup

    lkp_dir = tmp_path / "costar_lookup"
    lkp_dir.mkdir()
    # Bad file (not a real xlsx)
    (lkp_dir / "CostarExport (27).xlsx").write_bytes(b"not an excel file")
    # Good file
    pd.DataFrame({"Address": ["400 Good St, Vienna, VA"], "Class": ["B"]}).to_excel(
        lkp_dir / "CostarExport (28).xlsx", index=False
    )

    with caplog.at_level(logging.WARNING, logger="deal_radar.pipeline"):
        result = _load_costar_lookup(lookup_dir=lkp_dir)

    assert result["400 good st, vienna, va"] == "Class B"
    assert any("Could not load" in m or "failed" in m.lower() for m in caplog.messages)
