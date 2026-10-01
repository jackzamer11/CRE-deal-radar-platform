"""Facts from broker conversations — kept, and filed under who they are about.

Much of what Jack learns comes from brokers and landlords, not tenants. Jack
emailing Next Realty's George McGregor about "a tenant seeking 500-600 sqft" in
Alexandria states HIS CLIENT's requirement in an entry stamped to Next Realty.
Filed there, the brokerage looked like a tenant; hidden, the requirement was
lost. What this file locks:

  - the extractor is told who the person is and which side they are on
  - a requirement is never filed under a counterparty's own firm, whatever the
    extractor says — it waits in Review's holding list instead
  - an unnamed client's requirement waits too; attaching it puts it on that
    tenant's card, and re-mining keeps the answer; dismissing it keeps it gone
  - a named tenant is matched only when exactly one company fits
  - a roundup entry about one deal still belongs to that deal's company
  - what a broker says about space and rents is kept on the broker, drives no
    card, and is listed on their thread
  - copies of one email are mined once; stage dividers never; archived waits
  - reclassifying someone puts their entries back in line to be read again

In-memory SQLite, a stand-in extractor. No live DB, no network, no API calls.
"""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import IntelActivityExtraction, IntelOpportunity
from app.models.observation import Observation
from app.services.activity_intel_service import (
    EXTRACTED_FIELDS,
    build_log_text,
    mine_activity_log,
    mine_all_activity_logs,
)
from app.services.contact_type_service import confirm_type
from app.services.intel_signal_service import generate_opportunities

TODAY = date.today()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _company(db, name, **kw):
    n = db.query(Company).count() + 1
    c = Company(company_id=f"CO-{n:03d}", name=name, industry="Office", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _contact(db, name, company=None, contact_type="tenant"):
    c = Contact(name=name, company_id=company.id if company else None,
                contact_type=contact_type, stage="Sent", triaged=True)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _entry(db, *, company=None, contact=None, text="Emailed about space",
           direction="outbound", **kw):
    log = ActivityLog(
        log_date=kw.pop("on", TODAY - timedelta(days=5)), action_type="EMAIL",
        action_taken=text, direction=direction, channel="email",
        contact_id=contact.id if contact else None,
        company_stamp_id=company.id if company else None, **kw,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _extractor(kind="entry_company", tenant_name=None, **values):
    """A stand-in for the model: returns exactly the fields given."""
    def run(_text):
        out = {f: {"value": None, "confidence": None, "snippet": None} for f in EXTRACTED_FIELDS}
        for field, value in values.items():
            out[field] = {"value": value, "confidence": 0.9, "snippet": f"said {value}"}
        out["requirement_for"] = {"kind": kind, "tenant_name": tenant_name}
        return out
    return run


def _george(db):
    """Jack's real email: a client's requirement, sent to a landlord's broker."""
    next_realty = _company(db, "Next Realty Mid-Atlantic")
    george = _contact(db, "George McGregor", next_realty, contact_type="counterparty")
    log = _entry(db, company=next_realty, contact=george,
                 text="Asked George about 2451 Eisenhower for a tenant seeking 500-600 sqft")
    return next_realty, george, log


def _open(db):
    return db.query(IntelOpportunity).filter_by(status="open").all()


# ══ 1. The extractor is told who it is reading ════════════════════════════════

def test_the_note_text_says_who_the_person_is_and_which_way_it_went(db):
    _, _, log = _george(db)
    text = build_log_text(log)
    assert "George McGregor at Next Realty Mid-Atlantic" in text
    assert "COUNTERPARTY" in text
    assert "outbound (Jack wrote to them)" in text
    assert "Entry is filed under company: Next Realty Mid-Atlantic" in text


def test_the_extractor_reads_what_the_email_check_recorded_from_the_full_email(db):
    """The entry is a one-line summary; the email check read the whole email
    and wrote facts and discovery values. The extractor sees them too."""
    from app.models.contact import ContactFact

    tartan = _company(db, "Tartan Properties")
    fatimah = _contact(db, "Fatimah Wilson", tartan, contact_type="counterparty")
    log = _entry(db, company=tartan, contact=fatimah, direction="inbound",
                 text="Fatimah replied about a therapy office")
    log.disc_decision_timeline = "by end of December 2026"
    db.add(ContactFact(contact_id=fatimah.id, source_entry_id=log.id,
                       learned_date=TODAY, fact_text=(
                           "Fatimah Wilson is representing One Life One Love, a therapy "
                           "office looking to occupy about 2,000 to 2,500 square feet.")))
    db.add(ContactFact(contact_id=fatimah.id, source_entry_id=log.id, learned_date=TODAY,
                       fact_text="An old, replaced fact.", is_active=False))
    db.commit()

    text = build_log_text(log)
    assert "representing One Life One Love" in text
    assert "decision timeline: by end of December 2026" in text
    assert "An old, replaced fact." not in text


# ══ 2. A client's requirement never lands on the brokerage ════════════════════

def test_a_requirement_is_never_filed_under_the_brokerage(db):
    """Even when the model says the entry's company is the tenant."""
    next_realty, _, log = _george(db)
    facts = mine_activity_log(log, db, extractor=_extractor(
        "entry_company", req_sf_min="500", req_sf_max="600", req_submarkets="Alexandria"))
    db.commit()
    assert {o.about for o in facts} == {"unassigned"}

    generate_opportunities(db)
    assert _open(db) == []   # no "Stated requirement — Next Realty"


def test_the_held_requirement_waits_in_review_then_attaches_to_the_tenant(db, client):
    _, _, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_sf_max="600", req_submarkets="Alexandria"))
    db.commit()

    held = client.get("/api/intel/unassigned-requirements").json()
    assert len(held) == 1
    assert held[0]["entry_id"] == log.id
    assert held[0]["contact_name"] == "George McGregor"
    assert [f["label"] for f in held[0]["facts"]] == ["Min SF", "Max SF", "Submarkets"]

    jim_co = _company(db, "At Home in Alexandria")
    resp = client.post(f"/api/intel/unassigned-requirements/{log.id}/assign",
                       json={"company_id": jim_co.id})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/intel/unassigned-requirements").json() == []

    generate_opportunities(db)
    card = _open(db)[0]
    assert (card.entity_type, card.entity_id) == ("company", jim_co.id)
    assert card.title == "Stated requirement — At Home in Alexandria"
    # The card says who the requirement was shopped with.
    assert "Also raised with George McGregor" in card.rationale
    assert json.loads(card.signals_json)[0]["via"][0]["contact"] == "George McGregor"


def test_the_card_shows_each_item_as_most_recently_stated(db, client):
    """Jim said 300-700 SF in July; Jack's September email to a broker says
    500-600. The card reads 500-600, and says it came via the broker."""
    at_home = _company(db, "At Home in Alexandria")
    jim = _contact(db, "Jim Woolwine", at_home)
    july = _entry(db, company=at_home, contact=jim, on=TODAY - timedelta(days=70))
    mine_activity_log(july, db, extractor=_extractor(
        req_sf_min="300", req_sf_max="700", req_budget_max_psf="30"))
    _, _, sept = _george(db)
    mine_activity_log(sept, db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_sf_max="600"))
    db.commit()
    client.post(f"/api/intel/unassigned-requirements/{sept.id}/assign",
                json={"company_id": at_home.id})

    generate_opportunities(db)
    card = _open(db)[0]
    assert "min SF 500; max SF 600" in card.rationale
    assert "budget 30" in card.rationale   # nothing newer said about budget


def test_a_requirement_can_be_attached_to_a_person_with_no_company(db, client):
    """James Mancera wants an investment property. The requirement is his own;
    there is no tenant company. It lives on his page, not on an Intel card."""
    james = _contact(db, "James Mancera", contact_type="counterparty")
    log = _entry(db, contact=james, direction="inbound",
                 text="Cold call — wants an investment property, any condition")
    mine_activity_log(log, db, extractor=_extractor(
        "entry_company", req_must_haves="any condition accepted",
        req_space_type="investment property"))
    db.commit()
    assert [h["entry_id"] for h in client.get("/api/intel/unassigned-requirements").json()] == [log.id]

    resp = client.post(f"/api/intel/unassigned-requirements/{log.id}/assign",
                       json={"contact_id": james.id})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/intel/unassigned-requirements").json() == []

    generate_opportunities(db)
    assert _open(db) == []   # Intel stays tenant cards only
    on_page = client.get("/api/intel/attached-requirements",
                         params={"contact_id": james.id}).json()
    assert {(r["label"], r["value"]) for r in on_page} == {
        ("Must-haves", "any condition accepted"), ("Space type", "investment property"),
    }


def test_attached_to_a_counterparty_it_stays_on_their_page(db, client):
    firm, george, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_budget_max_psf="32"))
    db.commit()
    client.post(f"/api/intel/unassigned-requirements/{log.id}/assign",
                json={"contact_id": george.id})
    generate_opportunities(db)
    assert _open(db) == []
    assert len(client.get("/api/intel/attached-requirements",
                          params={"contact_id": george.id}).json()) == 2


def test_attached_to_a_tenant_contact_it_joins_their_companys_card(db, client):
    _, _, log = _george(db)
    at_home = _company(db, "At Home in Alexandria")
    jim = _contact(db, "Jim Woolwine", at_home)
    mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_budget_max_psf="32"))
    db.commit()
    client.post(f"/api/intel/unassigned-requirements/{log.id}/assign",
                json={"contact_id": jim.id})
    generate_opportunities(db)
    assert _open(db)[0].entity_id == at_home.id


def test_attach_takes_a_company_or_a_person_not_both(db, client):
    _, george, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor("unnamed_client", req_sf_min="500"))
    db.commit()
    other = _company(db, "Somewhere")
    for body in ({}, {"company_id": other.id, "contact_id": george.id}):
        assert client.post(f"/api/intel/unassigned-requirements/{log.id}/assign",
                           json=body).status_code == 400


def test_re_reading_an_attached_entry_does_not_bring_it_back(db, client):
    """What happened to Mike's email: attached in September, re-read in October,
    and the requirement reappeared in the holding list. It must not."""
    _, _, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_sf_max="600"))
    db.commit()
    at_home = _company(db, "At Home in Alexandria")
    client.post(f"/api/intel/unassigned-requirements/{log.id}/assign",
                json={"company_id": at_home.id})

    db.query(IntelActivityExtraction).delete()
    db.commit()
    mine_all_activity_logs(db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_sf_max="600", req_timing="by spring"))

    assert client.get("/api/intel/unassigned-requirements").json() == []
    live = db.query(Observation).filter(
        Observation.source_doc == f"activity_log:{log.id}",
        Observation.superseded_by_id.is_(None)).all()
    # No second copy of what he kept; the new fact follows his answer.
    assert sorted(o.field for o in live) == ["req_sf_max", "req_sf_min", "req_timing"]
    assert {o.assigned_company_id for o in live} == {at_home.id}


def test_copies_made_before_the_fix_are_tidied_by_the_next_mine(db, client):
    _, _, log = _george(db)
    at_home = _company(db, "At Home in Alexandria")
    db.add(Observation(entity_type="company", entity_id=1, field="req_sf_min", value="500",
                       source_doc=f"activity_log:{log.id}", human_verified=True,
                       verified_by="human", assigned_company_id=at_home.id))
    db.add(Observation(entity_type="company", entity_id=1, field="req_sf_min", value="500",
                       source_doc=f"activity_log:{log.id}", human_verified=True,
                       verified_by="auto", about="unassigned"))     # the stray copy
    db.add(IntelActivityExtraction(activity_log_id=log.id, status="done", fields_found=1))
    db.commit()
    assert len(client.get("/api/intel/unassigned-requirements").json()) == 1

    mine_all_activity_logs(db, extractor=_extractor())
    assert client.get("/api/intel/unassigned-requirements").json() == []


def test_a_dismissed_entry_stays_dismissed_when_re_read(db, client):
    _, _, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor("unnamed_client", req_sf_min="500",
                                                    req_budget_max_psf="32"))
    db.commit()
    client.post(f"/api/intel/unassigned-requirements/{log.id}/dismiss")
    db.query(IntelActivityExtraction).delete()
    db.commit()
    mine_all_activity_logs(db, extractor=_extractor("unnamed_client", req_sf_min="500",
                                                    req_timing="soon"))
    assert client.get("/api/intel/unassigned-requirements").json() == []


def test_an_entry_that_only_names_someone_is_not_held(db, client):
    _, _, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", contact_name="Koki Adasi", contact_email="koki@teamkoki.com"))
    db.commit()
    assert client.get("/api/intel/unassigned-requirements").json() == []


def test_re_mining_keeps_jacks_answer(db, client):
    _, _, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_budget_max_psf="32"))
    db.commit()
    jim_co = _company(db, "At Home in Alexandria")
    client.post(f"/api/intel/unassigned-requirements/{log.id}/assign",
                json={"company_id": jim_co.id})

    db.query(IntelActivityExtraction).delete()
    db.commit()
    mine_all_activity_logs(db, extractor=_extractor("unnamed_client", req_sf_min="500"))

    kept = db.query(Observation).filter(Observation.assigned_company_id == jim_co.id).all()
    assert {o.field for o in kept} == {"req_sf_min", "req_budget_max_psf"}


def test_dismissing_a_held_requirement_keeps_it_out(db, client):
    _, _, log = _george(db)
    mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", req_sf_min="500", req_budget_max_psf="32"))
    db.commit()
    assert client.post(f"/api/intel/unassigned-requirements/{log.id}/dismiss").status_code == 200
    assert client.get("/api/intel/unassigned-requirements").json() == []
    generate_opportunities(db)
    assert _open(db) == []
    # Kept, not deleted.
    assert db.query(Observation).filter_by(about="dismissed").count() == 2


def test_facts_mined_before_this_wait_too_without_an_api_call(db, client):
    """Mike's facts were mined before the miner said whose they were. Filed
    under his own firm, they wait for a tenant immediately — no re-read needed."""
    avison = _company(db, "Avison Young")
    mike = _contact(db, "Mike Shuler", avison, contact_type="counterparty")
    log = _entry(db, company=avison, contact=mike)
    tenant_co = _company(db, "Real Tenant")
    tenant = _contact(db, "Real Person", tenant_co)
    tenant_log = _entry(db, company=tenant_co, contact=tenant)
    for entry, co in ((log, avison), (tenant_log, tenant_co)):
        for field, value in (("req_sf_min", "500"), ("req_budget_max_psf", "32")):
            db.add(Observation(entity_type="company", entity_id=co.id, field=field,
                               value=value, source_doc=f"activity_log:{entry.id}",
                               human_verified=True, verified_by="auto"))   # about: null
    db.commit()

    generate_opportunities(db)
    assert [o.entity_id for o in _open(db)] == [tenant_co.id]   # the tenant's stay put
    held = client.get("/api/intel/unassigned-requirements").json()
    assert [h["entry_id"] for h in held] == [log.id]


# ══ 3. Named tenants ══════════════════════════════════════════════════════════

def test_a_named_tenant_is_filed_on_its_card(db):
    _, _, log = _george(db)
    tenant = _company(db, "Harbor Dental, PLLC")
    mine_activity_log(log, db, extractor=_extractor(
        "named_tenant", tenant_name="Harbor Dental", req_sf_min="2000",
        req_budget_max_psf="36"))
    db.commit()
    generate_opportunities(db)
    assert _open(db)[0].entity_id == tenant.id


def test_a_person_named_means_their_employer(db):
    _, _, log = _george(db)
    at_home = _company(db, "At Home in Alexandria")
    _contact(db, "Jim Woolwine", at_home)
    mine_activity_log(log, db, extractor=_extractor(
        "named_tenant", tenant_name="Jim Woolwine", req_sf_min="300", req_sf_max="700"))
    db.commit()
    generate_opportunities(db)
    assert _open(db)[0].entity_id == at_home.id


def test_a_name_two_companies_fit_waits_for_jack(db, client):
    _, _, log = _george(db)
    _company(db, "Summit Dental")
    _company(db, "Summit Dental LLC")   # a duplicate record — ambiguous
    mine_activity_log(log, db, extractor=_extractor(
        "named_tenant", tenant_name="Summit Dental", req_sf_min="2000",
        req_budget_max_psf="36"))
    db.commit()
    generate_opportunities(db)
    assert _open(db) == []
    held = client.get("/api/intel/unassigned-requirements").json()
    assert held[0]["said_name"] == "Summit Dental"


def test_a_firm_jack_marked_tenant_keeps_its_requirements_whoever_is_on_the_entry(db):
    sola = _company(db, "SolaREIT", company_type="tenant")
    laura = _contact(db, "Laura Pagliarulo", sola, contact_type="counterparty")
    log = _entry(db, company=sola, contact=laura, direction="inbound")
    facts = mine_activity_log(log, db, extractor=_extractor(
        "entry_company", req_sf_min="1200", req_budget_max_psf="40"))
    assert {o.about for o in facts} == {"entry"}


def test_a_roundup_entry_still_belongs_to_the_deal_company(db):
    """Ann is a counterparty, but her roundup entry about Scott Management is
    stamped to Scott Management — the tenant."""
    simpson = _company(db, "Simpson Properties")
    ann = _contact(db, "Ann Waller", simpson, contact_type="counterparty")
    scott = _company(db, "Scott Management")
    log = _entry(db, company=scott, contact=ann, direction="inbound", deal_sourced=True,
                 text="Scott Management renewing 4,200 SF")
    facts = mine_activity_log(log, db, extractor=_extractor(
        "entry_company", req_sf_min="4200", req_budget_max_psf="38"))
    db.commit()
    assert {o.about for o in facts} == {"entry"}
    generate_opportunities(db)
    assert _open(db)[0].entity_id == scott.id


# ══ 4. Market information is kept on the broker ═══════════════════════════════

def test_what_a_broker_said_about_space_is_kept_and_drives_no_card(db, client):
    avison = _company(db, "Avison Young")
    mike = _contact(db, "Mike Shuler", avison, contact_type="counterparty")
    log = _entry(db, company=avison, contact=mike, direction="inbound",
                 text="Mike: 600 SF at 2451 Eisenhower, $32 asking, free parking")
    facts = mine_activity_log(log, db, extractor=_extractor(
        "unnamed_client", mkt_property="2451 Eisenhower Ave", mkt_available_sf="600",
        mkt_asking_rent="$32/SF", mkt_concessions="free parking"))
    db.commit()
    assert {o.about for o in facts} == {"market"}

    generate_opportunities(db)
    assert _open(db) == []
    assert client.get("/api/intel/unassigned-requirements").json() == []

    listed = client.get("/api/intel/market-facts", params={"contact_id": mike.id}).json()
    assert {(m["label"], m["value"]) for m in listed} == {
        ("Property", "2451 Eisenhower Ave"), ("Available", "600"),
        ("Asking rent", "$32/SF"), ("Concessions", "free parking"),
    }


# ══ 5. Copies are mined once ══════════════════════════════════════════════════

def test_copies_dividers_and_archived_entries_are_not_mined(db):
    co = _company(db, "Halverson Dental")
    maria = _contact(db, "Maria Chen", co)
    office = _contact(db, "Office Manager", co)
    primary = _entry(db, company=co, contact=maria, source_message_id="<m1@mail>")
    _entry(db, company=co, contact=office, source_message_id="<m1@mail>#p9")
    _entry(db, company=co, contact=office, participation=True, source_message_id="<m2@mail>#p9")
    divider = _entry(db, contact=maria)
    divider.action_type = "STAGE_CHANGE"
    archived = _entry(db, company=co, contact=maria, archived=True)
    db.commit()

    read = []

    def extractor(text):
        read.append(text)
        return _extractor(req_sf_min="3000")(text)

    result = mine_all_activity_logs(db, extractor=extractor)
    assert result["processed"] == 1
    assert db.query(Observation).filter(
        Observation.source_doc == f"activity_log:{primary.id}").count() == 1

    # Unarchiving puts an entry back in line.
    archived.archived = False
    db.commit()
    assert mine_all_activity_logs(db, extractor=extractor)["processed"] == 1


def test_facts_mined_from_copies_before_are_cleared_but_jacks_are_kept(db):
    co = _company(db, "Halverson Dental")
    maria = _contact(db, "Maria Chen", co)
    copy = _entry(db, company=co, contact=maria, source_message_id="<m1@mail>#p3")
    for by in ("auto", "human"):
        db.add(Observation(entity_type="company", entity_id=co.id, field="req_sf_min",
                           value="3000", source_doc=f"activity_log:{copy.id}",
                           human_verified=True, verified_by=by))
    db.add(IntelActivityExtraction(activity_log_id=copy.id, status="done", fields_found=2))
    db.commit()

    mine_all_activity_logs(db, extractor=_extractor())
    left = db.query(Observation).filter(Observation.source_doc == f"activity_log:{copy.id}").all()
    assert [o.verified_by for o in left] == ["human"]


def test_the_status_counts_only_entries_a_run_would_read(db, client):
    co = _company(db, "Halverson Dental")
    maria = _contact(db, "Maria Chen", co)
    _entry(db, company=co, contact=maria, source_message_id="<m1@mail>")
    _entry(db, company=co, contact=maria, source_message_id="<m1@mail>#p4")
    _entry(db, company=co, contact=maria, archived=True)
    status = client.get("/api/intel/activity/status").json()
    assert status["total_logs"] == 1
    assert status["remaining"] == 1


def test_a_failed_read_still_counts_as_waiting(db, client):
    """Counted as read, a failure hid the Mine button and could not be retried."""
    co = _company(db, "Halverson Dental")
    maria = _contact(db, "Maria Chen", co)
    log = _entry(db, company=co, contact=maria)
    db.add(IntelActivityExtraction(activity_log_id=log.id, status="failed", fields_found=0,
                                   error="database is locked"))
    db.commit()
    status = client.get("/api/intel/activity/status").json()
    assert status["remaining"] == 1 and status["failed"] == 1


# ══ 6. Reclassifying someone re-reads their notes ═════════════════════════════

def test_confirming_a_broker_queues_their_entries_to_be_read_again(db):
    firm = _company(db, "Avison Young")
    mike = _contact(db, "Mike Shuler", firm, contact_type="unconfirmed")
    log = _entry(db, company=firm, contact=mike)
    mine_all_activity_logs(db, extractor=_extractor("entry_company", req_sf_min="500",
                                                    req_budget_max_psf="32"))
    generate_opportunities(db)
    assert len(_open(db)) == 1   # read while nobody knew Mike was a broker

    confirm_type(db, mike, "counterparty")
    db.commit()
    assert db.query(IntelActivityExtraction).filter_by(activity_log_id=log.id).count() == 0

    # The next run reads it again; the old facts are replaced, not doubled.
    mine_all_activity_logs(db, extractor=_extractor("entry_company", req_sf_min="500",
                                                    req_budget_max_psf="32"))
    facts = db.query(Observation).filter(Observation.source_doc == f"activity_log:{log.id}").all()
    assert len(facts) == 2 and {o.about for o in facts} == {"unassigned"}
    generate_opportunities(db)
    assert _open(db) == []
