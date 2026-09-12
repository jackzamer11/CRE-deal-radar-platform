"""Contact-thread services: triage, facts, and inbound-email resolution.

Three concerns live here so the route modules stay thin and so an automated
fact extractor has one clean seam to plug into later (`create_fact`).

Design rules enforced here rather than in the routes:
  - Triage is never a manual queue. `mark_engaged()` is called from every write
    path that constitutes real engagement; nothing else sets triaged.
  - Email is the identity key. A display name is never used to match a contact.
  - Free-mail senders never create a company.
  - A stage change is a divider, not a touch. `record_stage_change()` is the one
    writer, and it collapses a burst of clicks into the net move.
"""
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import null
from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact, ContactFact

# Domains where the sender's address says nothing about who they work for.
# A contact from one of these gets a null company_id — never a company called
# "Gmail". Kept as a suffix match so subdomains (e.g. mail.yahoo.co.uk) resolve.
FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "rocketmail.com", "icloud.com",
    "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com", "fastmail.com",
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "cox.net",
    "bellsouth.net", "earthlink.net", "juno.com", "aim.com", "hushmail.com",
    "tutanota.com", "duck.com", "hey.com",
}


# The action_type marking a row as a stage-change event rather than a real
# touch. Kept distinct so entry counts, last-touch dates and the company
# timeline can all exclude it with one filter.
STAGE_CHANGE_ACTION = "STAGE_CHANGE"

# Two stage changes closer together than this, with no real entry between them,
# are one decision expressed in several clicks — they collapse to the net move.
STAGE_CHANGE_COLLAPSE_MINUTES = 30


def is_real_touch(action_type: Optional[str]) -> bool:
    """True for anything that is an actual contact event rather than a divider."""
    return (action_type or "") != STAGE_CHANGE_ACTION


def is_free_mail(domain: Optional[str]) -> bool:
    """True when a domain tells us nothing about the sender's employer."""
    if not domain:
        return True
    d = domain.strip().lower().lstrip("@")
    if d in FREE_MAIL_DOMAINS:
        return True
    # mail.yahoo.co.uk → yahoo.co.uk → co.uk ... match any registrable suffix.
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in FREE_MAIL_DOMAINS:
            return True
    return False


def normalize_email(email: Optional[str]) -> Optional[str]:
    """Lower-cased, stripped address. Email matching is always case-insensitive."""
    if not email:
        return None
    e = email.strip().lower()
    # Tolerate "Joe Smith <joe@acme.com>" — the automation may pass either form.
    if "<" in e and ">" in e:
        e = e[e.rfind("<") + 1:e.rfind(">")].strip()
    return e or None


def email_domain_of(email: Optional[str]) -> Optional[str]:
    e = normalize_email(email)
    if not e or "@" not in e:
        return None
    return e.rsplit("@", 1)[1] or None


# ── Triage ────────────────────────────────────────────────────────────────────

def mark_engaged(db: Session, contact: Optional[Contact],
                 company: Optional[Company] = None) -> None:
    """Flip a contact (and their company) to triaged on real engagement.

    Called from every engagement path: logging a call or meeting, changing a
    stage, setting a next-touch date, editing a field, adding a fact. This is
    the ONLY way a record becomes triaged apart from Jack's explicit manual
    toggle — there is deliberately no queue to work through, because a surface
    that needs tending is a surface that gets abandoned.

    Does not commit: the caller owns the transaction.
    """
    if contact is not None and not contact.triaged:
        contact.triaged = True
        contact.updated_at = datetime.utcnow()
    # Triaging a contact triages their company too.
    target_company = company
    if target_company is None and contact is not None and contact.company_id:
        target_company = db.query(Company).filter(
            Company.id == contact.company_id
        ).first()
    if target_company is not None and not target_company.triaged:
        target_company.triaged = True


# ── Stage changes ─────────────────────────────────────────────────────────────

def stage_change_label(old_stage: Optional[str], new_stage: Optional[str]) -> str:
    """The one-line text a divider renders when it has nothing else to show."""
    return f"Stage: {old_stage or 'Sent'} → {new_stage or 'Sent'}"


def record_stage_change(
    db: Session,
    contact: Contact,
    old_stage: Optional[str],
    new_stage: Optional[str],
) -> Optional[ActivityLog]:
    """Write the divider marking a stage transition, collapsing a burst of them.

    A stage change has no direction and no channel and is not outreach, so it is
    never stamped to a company and never counts as a touch. Six clicks between
    pills used to produce six cards stamped OUT / OTHER and bury the actual
    conversation; this is the single writer that stops that.

    Collapsing rule: consecutive stage changes on the same contact, with no real
    entry logged between them and inside a 30-minute window, resolve to one
    event showing the net move. The window rolls forward as the burst extends —
    nothing happened between the clicks, so they are one decision however long
    it took to settle. If the net move returns to the starting stage the event
    is deleted outright: a misclick corrected is not history.

    Returns the surviving event, or None when the change collapsed to nothing.
    Does not commit — the caller owns the transaction.
    """
    if (old_stage or "Sent") == (new_stage or "Sent"):
        return None

    # Flush first so an entry created earlier in this same transaction is
    # visible to the "was there a real touch between them?" check below.
    db.flush()
    now = datetime.utcnow()

    prior = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.contact_id == contact.id,
            ActivityLog.action_type == STAGE_CHANGE_ACTION,
        )
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .first()
    )

    if _is_collapsible(db, contact, prior, now):
        prior.stage_to = new_stage
        # Back where it started, with nothing in between — drop it entirely.
        if (prior.stage_from or "Sent") == (prior.stage_to or "Sent"):
            db.delete(prior)
            db.flush()
            return None
        prior.action_taken = stage_change_label(prior.stage_from, prior.stage_to)
        prior.log_date = date.today()
        # Roll the window forward so a continuing burst keeps collapsing.
        prior.created_at = now
        return prior

    event = ActivityLog(
        log_date=date.today(),
        contact_id=contact.id,
        # Deliberately unstamped: a divider is not a conversation about a
        # company, so it never appears on the company timeline.
        company_id=None,
        company_stamp_id=None,
        action_type=STAGE_CHANGE_ACTION,
        action_taken=stage_change_label(old_stage, new_stage),
        stage_from=old_stage or "Sent",
        stage_to=new_stage,
        # No direction and no channel — that is what made the old rows read
        # as OUT / OTHER outreach. null() rather than None: both columns carry
        # an ORM-level default, which SQLAlchemy would otherwise apply to a
        # None and hand the divider an "outbound email" it never was.
        direction=null(),
        channel=null(),
        created_by="user",
        created_at=now,
    )
    db.add(event)
    db.flush()
    return event


def _is_collapsible(
    db: Session,
    contact: Contact,
    prior: Optional[ActivityLog],
    now: datetime,
) -> bool:
    """Can this change fold into `prior` rather than adding another divider?"""
    if prior is None or prior.created_at is None:
        # A legacy divider with no timestamp cannot be windowed — leave it be.
        return False
    if now - prior.created_at > timedelta(minutes=STAGE_CHANGE_COLLAPSE_MINUTES):
        return False
    # A real touch since the last divider means the thread moved on; the next
    # stage change is a new decision, not a correction of the last one.
    intervening = (
        db.query(ActivityLog.id)
        .filter(
            ActivityLog.contact_id == contact.id,
            ActivityLog.action_type != STAGE_CHANGE_ACTION,
            ActivityLog.created_at.isnot(None),
            ActivityLog.created_at > prior.created_at,
        )
        .first()
    )
    return intervening is None


# ── Facts ─────────────────────────────────────────────────────────────────────

def create_fact(
    db: Session,
    contact: Contact,
    fact_text: str,
    *,
    source_entry_id: Optional[int] = None,
    learned_date: Optional[date] = None,
    supersedes_id: Optional[int] = None,
) -> ContactFact:
    """Record a durable thing learned about a person.

    This is the seam an automated extractor plugs into: it should call this
    function with the entry id it mined, never write ContactFact rows directly,
    so superseding and triage stay consistent.

    When `supersedes_id` is given the older fact is marked superseded rather
    than deleted — newest active fact wins, and what was believed when stays
    retrievable.
    """
    fact = ContactFact(
        contact_id=contact.id,
        fact_text=fact_text,
        source_entry_id=source_entry_id,
        learned_date=learned_date or date.today(),
        is_active=True,
    )
    db.add(fact)
    db.flush()  # assign fact.id before pointing the old fact at it

    if supersedes_id:
        old = db.query(ContactFact).filter(
            ContactFact.id == supersedes_id,
            ContactFact.contact_id == contact.id,
        ).first()
        if old:
            old.is_active = False
            old.superseded_by_id = fact.id

    mark_engaged(db, contact)
    return fact


def active_facts(db: Session, contact_id: int) -> list:
    """Active facts for a contact, newest first — what the UI renders as prose."""
    return (
        db.query(ContactFact)
        .filter(
            ContactFact.contact_id == contact_id,
            ContactFact.is_active.is_(True),
        )
        .order_by(ContactFact.learned_date.desc(), ContactFact.id.desc())
        .all()
    )


# ── Inbound-email resolution ──────────────────────────────────────────────────

def resolve_contact_by_email(db: Session, email: Optional[str]) -> Optional[Contact]:
    """Find a contact by email address — exact, case-insensitive.

    Email is the identifier. A name string is never used to match: two people
    called "Mike Johnson" are two people.
    """
    e = normalize_email(email)
    if not e:
        return None
    return db.query(Contact).filter(Contact.email == e).first()


def _normalize_company_name(name: str) -> str:
    """Strip punctuation, casing and the usual suffixes for name comparison."""
    import re
    n = (name or "").lower()
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(
        r"\b(inc|llc|l l c|ltd|corp|corporation|company|co|group|the|plc|lp|llp|pc)\b",
        " ", n,
    )
    return re.sub(r"\s+", " ", n).strip()


def resolve_company_for_email(
    db: Session, email: Optional[str], display_name: Optional[str] = None,
) -> Tuple[Optional[Company], bool]:
    """Resolve (or create) the company behind an email address.

    Returns (company, created). Company is None for a free-mail sender — those
    get a contact with no company rather than a company called "Gmail".

    Resolution order:
      1. email_domain match (a company already claims this domain)
      2. the domain appears in an existing company's website
      3. the name derived from the domain, matched loosely against existing
         company names — this is what stops a "Mm-Realestate" duplicate of a
         hand-entered "Corcoran McEnearney"
      4. create, flagged auto_created + untriaged, with no company_type
    """
    domain = email_domain_of(email)
    if not domain or is_free_mail(domain):
        return None, False

    existing = db.query(Company).filter(Company.email_domain == domain).first()
    if existing:
        return existing, False

    # The domain may already be on record as the company's website.
    by_site = (
        db.query(Company)
        .filter(Company.website.isnot(None), Company.website.ilike(f"%{domain}%"))
        .first()
    )
    if by_site:
        # Claim the domain so the next lookup is a direct hit.
        by_site.email_domain = domain
        return by_site, False

    # Derive a display name from the domain: "mm-realestate.com" → "Mm Realestate".
    stem = domain.rsplit(".", 1)[0].split(".")[-1]
    derived = stem.replace("-", " ").replace("_", " ").strip()
    derived_title = " ".join(w.capitalize() for w in derived.split()) or domain

    # Loose name match against what Jack already typed by hand, both directions
    # (a short derived name can be contained in a longer real one).
    target = _normalize_company_name(derived_title)
    if target:
        for cand in db.query(Company).filter(Company.name.isnot(None)).all():
            cand_norm = _normalize_company_name(cand.name)
            if not cand_norm:
                continue
            if cand_norm == target or target in cand_norm or cand_norm in target:
                if not cand.email_domain:
                    cand.email_domain = domain
                return cand, False

    company = Company(
        company_id=_next_company_id(db),
        name=derived_title,
        industry="Unknown",
        email_domain=domain,
        auto_created=True,
        triaged=False,
        company_type=None,   # no guess — Jack sets it
    )
    db.add(company)
    db.flush()
    return company, True


def _next_company_id(db: Session) -> str:
    """Next CO-nnn business key. Companies are keyed by this string externally."""
    from sqlalchemy import func
    count = db.query(func.count(Company.id)).scalar() or 0
    # Probe forward rather than trusting the count — rows may have been deleted.
    n = count + 1
    for _ in range(10000):
        candidate = f"CO-{n:03d}"
        if not db.query(Company.id).filter(Company.company_id == candidate).first():
            return candidate
        n += 1
    return f"CO-{datetime.utcnow().strftime('%y%m%d%H%M%S')}"


def resolve_or_create_contact(
    db: Session,
    email: Optional[str],
    display_name: Optional[str],
    *,
    company: Optional[Company] = None,
) -> Tuple[Optional[Contact], bool]:
    """Find a contact by email, or create one. Returns (contact, created).

    An auto-created contact is auto_created=True and triaged=False — it shows up
    in search immediately and in the default list once Jack engages with it.
    """
    e = normalize_email(email)
    existing = resolve_contact_by_email(db, e)
    if existing:
        # Backfill a company link learned later, but never overwrite one.
        if existing.company_id is None and company is not None:
            existing.company_id = company.id
        return existing, False

    name = (display_name or "").strip()
    if not name:
        # Fall back to the local part: "joe.smith@acme.com" → "Joe Smith".
        local = (e or "").split("@")[0] if e else ""
        name = " ".join(
            w.capitalize() for w in local.replace(".", " ").replace("_", " ").split()
        ) or (e or "Unknown Contact")

    contact = Contact(
        name=name,
        email=e,
        company_id=company.id if company is not None else None,
        # A sender who is not a tenant is a counterparty, but we cannot tell
        # which from an address alone — default to tenant and let Jack correct
        # it. Never used to gate anything automatically.
        contact_type="tenant",
        stage="Sent",
        auto_created=True,
        triaged=False,
        responded=False,
    )
    db.add(contact)
    db.flush()
    return contact, True


def apply_inbound_stage_rules(contact: Contact, direction: Optional[str]) -> bool:
    """Apply the inbound-reply rules to a contact. Returns True if stage moved.

    - direction=inbound → responded=True (permanently; never cleared here).
    - stage moves Sent → Replied ONLY. A contact already at Interested or
      In Play is never regressed by an inbound email.
    """
    if (direction or "").lower() != "inbound":
        return False
    contact.responded = True
    if (contact.stage or "Sent") == "Sent":
        contact.stage = "Replied"
        contact.stage_changed_at = date.today()
        return True
    return False
