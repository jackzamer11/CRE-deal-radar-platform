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
from typing import Dict, List, Optional, Tuple

from sqlalchemy import null
from sqlalchemy.orm import Session

from app.config import settings
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact, ContactFact, CLOSED_STAGE
from app.models.email_ingest import ContactAddressOverride

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


# ── Addresses Jack owns ─────────────────────────────────────────────────────────
#
# Lives here (rather than email_ingest_service, which imports it) because the
# alias/dedup guard below needs it too, and contact_service must not import
# email_ingest_service — email_ingest_service already imports from here.

def _split_setting(raw: Optional[str]) -> set:
    return {
        part.strip().lower().lstrip("@")
        for part in str(raw or "").split(",")
        if part.strip()
    }


def own_email_domains() -> set:
    """Read at call time so the env var is live, never cached at import."""
    return _split_setting(getattr(settings, "OWN_EMAIL_DOMAINS", ""))


def own_email_addresses() -> set:
    return _split_setting(getattr(settings, "OWN_EMAIL_ADDRESSES", ""))


def is_own_address(email: Optional[str]) -> bool:
    """True when this address is Jack's own.

    Two tests, because they are not the same question. A domain Jack owns covers
    every address at it (jzamer@z-reg.com, anything@z-reg.com). A free-mail
    address has to match exactly — blocklisting gmail.com as a domain would
    swallow every real contact who uses it.
    """
    normalized = normalize_email(email)
    if not normalized:
        return False
    if normalized in own_email_addresses():
        return True
    if "@" not in normalized:
        return False
    domain = normalized.rsplit("@", 1)[1]
    if domain in own_email_domains():
        return True
    # Subdomain of an owned domain (mail.z-reg.com) is still Jack's.
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in own_email_domains():
            return True
    return False


def is_own_literal_address(email: Optional[str]) -> bool:
    """True only for the exact addresses Jack owns — the domain list is not read.

    The narrower of the two tests, and the right one wherever Jack is naming an
    address deliberately rather than an ingested email being attributed. The
    domain sweep in is_own_address exists so no colleague at simpsondev.com is
    ever resolved AS Jack during ingestion; it would also refuse
    FZamer@simpsondev.com as an alias of Fred Zamer, which is the case the alias
    feature exists for. A colleague's address is not Jack's address.
    """
    normalized = normalize_email(email)
    if not normalized:
        return False
    return normalized in own_email_addresses()


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


def apply_closed_stage_bookkeeping(
    contact: Contact, new_stage: Optional[str], today: Optional[date] = None,
) -> None:
    """Keep closed_at and is_past_client in step with a stage move.

    The single writer for both columns, so the asymmetry between them lives in
    one place:

      - closed_at is SET when the stage moves to Closed and CLEARED when it
        moves off. It is the date the deal was placed, not a stage timestamp.
      - is_past_client is set with it and NEVER cleared. If a renewal falls
        through and the contact comes back to In Play, Jack still placed this
        tenant once — that is a permanent fact about the relationship, and it is
        what surfaces them as a past client when their next expiry comes around.

    Closed is never set automatically anywhere; this only reacts to a stage Jack
    chose. Does not commit — the caller owns the transaction.
    """
    if (new_stage or "Sent") == CLOSED_STAGE:
        contact.closed_at = today or date.today()
        contact.is_past_client = True
    else:
        contact.closed_at = None


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
    triage: bool = True,
) -> ContactFact:
    """Record a durable thing learned about a person.

    This is the seam an automated extractor plugs into: it should call this
    function with the entry id it mined, never write ContactFact rows directly,
    so superseding and triage stay consistent.

    When `supersedes_id` is given the older fact is marked superseded rather
    than deleted — newest active fact wins, and what was believed when stays
    retrievable.

    `triage=False` records the fact without marking the contact engaged. Used
    for facts from a multi-deal roundup: Ann's weekly notes name seven people
    Jack has never contacted, and triaging them would fill the main list with
    people he is not working. They graduate the normal way — the moment Jack
    logs a touch, changes a stage, sets a next-touch date or edits them.
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

    if triage:
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

    Checks the primary field first, then the alias table (contacts.email —
    itself mirrored into the alias table as that contact's primary row — has
    no reason to be checked twice, but going to the ORM column directly here
    saves a query on the overwhelmingly common case). A hit on either the
    primary field or an alias returns the same contact, so a contact with
    several addresses (a work inbox and a personal one) resolves identically
    from any of them.

    Email is the identifier. A name string is never used to match: two people
    called "Mike Johnson" are two people.
    """
    e = normalize_email(email)
    if not e:
        return None
    contact = db.query(Contact).filter(Contact.email == e).first()
    if contact is not None:
        return contact
    alias = db.query(ContactAddressOverride).filter(
        ContactAddressOverride.email == e
    ).first()
    if alias is None:
        return None
    return db.query(Contact).filter(Contact.id == alias.contact_id).first()


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
    return resolve_companies_for_emails(db, [email]).get(
        normalize_email(email), (None, False),
    )


def resolve_companies_for_emails(
    db: Session, emails: List[Optional[str]],
) -> Dict[str, Tuple[Optional[Company], bool]]:
    """resolve_company_for_email() for several addresses, in ONE pass.

    Returns {normalized email: (company or None, created)} for every address
    given. The company table is read a fixed number of times however many
    addresses are asked about — a roundup naming ten tenant contacts must not
    cost ten resolution passes. The rules and their order are exactly those of
    the single-address resolver, which delegates here so there is one of them.

    A domain resolved (or created) for an earlier address is reused by a later
    one, and a company created here is a name-match candidate for the rest.
    """
    out: Dict[str, Tuple[Optional[Company], bool]] = {}
    pending = []   # (normalized email, domain) needing a lookup
    for email in emails:
        e = normalize_email(email)
        if not e or e in out:
            continue
        domain = email_domain_of(e)
        if not domain or is_free_mail(domain):
            out[e] = (None, False)
        else:
            pending.append((e, domain))
    if not pending:
        return out

    domains = list(dict.fromkeys(d for _, d in pending))
    by_domain: Dict[str, Tuple[Company, bool]] = {}
    for c in db.query(Company).filter(Company.email_domain.in_(domains)).all():
        # First match wins, as .first() did in the single-address resolver.
        by_domain.setdefault(c.email_domain, (c, False))

    unresolved = [d for d in domains if d not in by_domain]
    if unresolved:
        # Read once each, and only when some domain is still unknown.
        with_site = (
            db.query(Company).filter(Company.website.isnot(None)).all()
        )
        named = [
            (cand, _normalize_company_name(cand.name))
            for cand in db.query(Company).filter(Company.name.isnot(None)).all()
        ]
        for domain in unresolved:
            # The domain may already be on record as the company's website.
            by_site = next(
                (c for c in with_site if domain in (c.website or "").lower()), None,
            )
            if by_site is not None:
                # Claim the domain so the next lookup is a direct hit.
                by_site.email_domain = domain
                by_domain[domain] = (by_site, False)
                continue

            # Derive a display name from the domain: "mm-realestate.com" → "Mm Realestate".
            stem = domain.rsplit(".", 1)[0].split(".")[-1]
            derived = stem.replace("-", " ").replace("_", " ").strip()
            derived_title = " ".join(w.capitalize() for w in derived.split()) or domain

            # Loose name match against what Jack already typed by hand, both
            # directions (a short derived name can be contained in a longer one).
            target = _normalize_company_name(derived_title)
            match = None
            if target:
                for cand, cand_norm in named:
                    if not cand_norm:
                        continue
                    if cand_norm == target or target in cand_norm or cand_norm in target:
                        match = cand
                        break
            if match is not None:
                if not match.email_domain:
                    match.email_domain = domain
                by_domain[domain] = (match, False)
                continue

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
            named.append((company, _normalize_company_name(company.name)))
            by_domain[domain] = (company, True)

    for e, domain in pending:
        out[e] = by_domain[domain]
    return out


def resolve_company_by_name(
    db: Session, name: Optional[str],
) -> Tuple[Optional[Company], bool]:
    """Resolve (or create) a company by the name an email named it by.

    Returns (company, created). Used when an email is clearly ABOUT a company
    that is not the sender's employer — a broker writing about their client.
    Matching is the same loose comparison resolve_company_for_email() uses, so
    "Collaborative AV, LLC" finds the hand-entered "Collaborative AV" instead of
    creating a rival record.
    """
    raw = (name or "").strip()
    if not raw:
        return None, False
    return resolve_companies_by_names(db, [raw])[raw]


def resolve_companies_by_names(
    db: Session, names: List[str],
) -> Dict[str, Tuple[Company, bool]]:
    """Resolve (or create) several companies by name in ONE pass.

    Returns {stripped name: (company, created)}; blank names are omitted. The
    company table is read once however many names are asked for — a weekly
    roundup naming ten tenants must not cost ten full scans.

    Names are resolved in order, and a company created for an earlier name is
    a candidate for a later one, so "Scott Management" and "Scott Management
    LLC" in the same email land on one record rather than two.
    """
    wanted = []
    for name in names:
        raw = (name or "").strip()
        if raw and raw not in wanted:
            wanted.append(raw)
    if not wanted:
        return {}

    candidates = [
        (cand, _normalize_company_name(cand.name))
        for cand in db.query(Company).filter(Company.name.isnot(None)).all()
    ]

    resolved: Dict[str, Tuple[Company, bool]] = {}
    created_ids = set()
    for raw in wanted:
        target = _normalize_company_name(raw)
        match = None
        if target:
            for cand, cand_norm in candidates:
                if not cand_norm:
                    continue
                if cand_norm == target or target in cand_norm or cand_norm in target:
                    match = cand
                    break
        if match is not None:
            resolved[raw] = (match, id(match) in created_ids)
            continue

        company = Company(
            company_id=_next_company_id(db),
            name=raw,
            industry="Unknown",
            auto_created=True,
            triaged=False,
            company_type=None,   # no guess — Jack sets it
        )
        db.add(company)
        db.flush()
        created_ids.add(id(company))
        candidates.append((company, _normalize_company_name(raw)))
        resolved[raw] = (company, True)
    return resolved


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
    return create_contact(db, e, display_name, company=company), True


def create_contact(
    db: Session,
    email: Optional[str],
    display_name: Optional[str],
    *,
    company: Optional[Company] = None,
) -> Contact:
    """Create an untriaged contact. The caller has already established that no
    contact holds this address — a batch resolver looks them all up at once."""
    e = normalize_email(email)
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
    # Deliberately NOT mirrored into the alias table here: this is the
    # high-volume auto-creation path (every inbound sender, every recipient),
    # and the alias table doubles as the "Jack corrected a misattribution"
    # memory — eagerly writing a row for every auto-created contact's address
    # would make that memory indistinguishable from a real correction. The
    # mirror gets backfilled lazily (contact_addresses(), the first time
    # anyone looks), by the startup migration, or immediately for a
    # human-driven create/edit (routes/contacts.py, sync_primary_alias).
    return contact


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


# ── Contact addresses (a contact can own several) ───────────────────────────────
#
# A contact owns one identity but may be reachable at more than one address —
# Fred Zamer answers mail at both a personal and a work inbox, and it is the
# same relationship either way. Contact.email is the primary address and stays
# the source of truth for it; every address (primary and alias alike) also has
# a row in ContactAddressOverride, which doubles as this table and as the
# "Jack corrected a misattribution" memory the email ingestion path writes to.
# One address, one contact, always: adding or promoting an address that
# already belongs to someone else is refused, never silently reassigned.

class AddressOwnedByJack(Exception):
    """Raised when an address Jack owns is offered as a contact's alias."""


class AddressAlreadyClaimed(Exception):
    """Raised when an address already resolves to a different contact."""

    def __init__(self, contact: Contact):
        self.contact = contact
        super().__init__(f"{contact.email or contact.name} already holds this address")


class PrimaryAddressRemoval(Exception):
    """Raised by remove_contact_address on the primary address.

    Removing the last/primary address would leave the contact with no address
    to promote in its place — promote another address first.
    """


def sync_primary_alias(
    db: Session, contact: Contact, old_email: Optional[str] = None,
) -> None:
    """Keep the alias table's primary-mirror row in step with contact.email.

    Call after contact.email has been set to its new value — on create, or
    after the generic contact edit changes it. `old_email` is whatever
    contact.email held immediately before, so its mirror row (if any) can be
    demoted rather than left incorrectly flagged primary.

    Clearing the email (new value None) demotes the old mirror to a plain
    alias rather than deleting it: the address still belongs to this contact
    and still resolves to them, it is just no longer the primary one.

    Does not commit — the caller owns the transaction. Trusts the caller to
    have already checked the new address is not claimed elsewhere (every
    caller runs that check first, because it is also where the 409 belongs).
    """
    new_email = contact.email
    if old_email and old_email != new_email:
        old_row = db.query(ContactAddressOverride).filter(
            ContactAddressOverride.email == old_email,
            ContactAddressOverride.contact_id == contact.id,
        ).first()
        if old_row is not None and old_row.is_primary:
            old_row.is_primary = False

    if not new_email:
        return

    row = db.query(ContactAddressOverride).filter(
        ContactAddressOverride.email == new_email
    ).first()
    if row is None:
        row = ContactAddressOverride(
            email=new_email, contact_id=contact.id, is_primary=True,
        )
        db.add(row)
    else:
        row.contact_id = contact.id
        row.is_primary = True
        row.updated_at = datetime.utcnow()
    db.flush()


def contact_addresses(db: Session, contact_id: int) -> List[ContactAddressOverride]:
    """Every address this contact is known by, primary first.

    Self-healing: an auto-created contact (the overwhelming majority — every
    inbound sender, every recipient) is deliberately never mirrored into the
    alias table at create time (see create_contact). The first time anyone
    actually looks at their addresses, the mirror is written here so the list
    is complete — the startup migration and every human-driven edit do the
    same, this just covers the gap between them. Does not commit; the caller
    (a GET route) does.
    """
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is not None and contact.email:
        row = db.query(ContactAddressOverride).filter(
            ContactAddressOverride.email == contact.email
        ).first()
        if row is None:
            db.add(ContactAddressOverride(
                email=contact.email, contact_id=contact.id, is_primary=True,
            ))
            db.flush()
        elif row.contact_id == contact.id and not row.is_primary:
            row.is_primary = True
            db.flush()

    return (
        db.query(ContactAddressOverride)
        .filter(ContactAddressOverride.contact_id == contact_id)
        .order_by(ContactAddressOverride.is_primary.desc(), ContactAddressOverride.id.asc())
        .all()
    )


def add_contact_address(
    db: Session, contact: Contact, email: Optional[str],
) -> ContactAddressOverride:
    """Add an alias address to a contact.

    Raises AddressOwnedByJack if the address is one of Jack's own, and
    AddressAlreadyClaimed(other_contact) if it already resolves to a
    different contact — the route turns each into the appropriate HTTP error.
    Adding an address the contact already holds (primary or alias) is a
    no-op that returns the existing row.

    The owned-address test here is the LITERAL one, not the domain sweep that
    ingestion uses: Jack adding FZamer@simpsondev.com as Fred Zamer's second
    address is the whole point of aliases, while jzamer@simpsondev.com is still
    refused. Ingestion keeps the wider test — see is_own_literal_address.

    Does not commit — the caller owns the transaction.
    """
    normalized = normalize_email(email)
    if not normalized:
        raise ValueError("email is required")
    if is_own_literal_address(normalized):
        raise AddressOwnedByJack(normalized)

    owner = resolve_contact_by_email(db, normalized)
    if owner is not None and owner.id != contact.id:
        raise AddressAlreadyClaimed(owner)
    if owner is not None and owner.id == contact.id:
        row = db.query(ContactAddressOverride).filter(
            ContactAddressOverride.email == normalized
        ).first()
        if row is None:
            # This matched via Contact.email directly — the contact's own
            # primary address, never mirrored (an auto-created contact no one
            # has looked at yet). Self-heal rather than return nothing.
            row = ContactAddressOverride(
                email=normalized, contact_id=contact.id, is_primary=True,
            )
            db.add(row)
            db.flush()
        return row

    row = ContactAddressOverride(email=normalized, contact_id=contact.id, is_primary=False)
    db.add(row)
    db.flush()
    return row


def remove_contact_address(db: Session, contact: Contact, address_id: int) -> None:
    """Remove one of a contact's addresses.

    Raises LookupError if the address does not belong to this contact, and
    PrimaryAddressRemoval if it is the primary — promote another address
    first (set_primary_contact_address), then remove the old one.

    Does not commit — the caller owns the transaction.
    """
    row = db.query(ContactAddressOverride).filter(
        ContactAddressOverride.id == address_id,
        ContactAddressOverride.contact_id == contact.id,
    ).first()
    if row is None:
        raise LookupError(address_id)
    if row.is_primary:
        raise PrimaryAddressRemoval(row.email)
    db.delete(row)
    db.flush()


def set_primary_contact_address(
    db: Session, contact: Contact, address_id: int,
) -> ContactAddressOverride:
    """Promote one of a contact's addresses to primary.

    Demotes whichever address held is_primary before, and mirrors the change
    onto Contact.email — the field stays the source of truth for "the"
    primary address, this table just has to agree with it.

    Raises LookupError if the address does not belong to this contact. A
    no-op, returning the row unchanged, if it is already primary.

    Does not commit — the caller owns the transaction.
    """
    row = db.query(ContactAddressOverride).filter(
        ContactAddressOverride.id == address_id,
        ContactAddressOverride.contact_id == contact.id,
    ).first()
    if row is None:
        raise LookupError(address_id)
    if row.is_primary:
        return row

    old_primary = db.query(ContactAddressOverride).filter(
        ContactAddressOverride.contact_id == contact.id,
        ContactAddressOverride.is_primary.is_(True),
    ).first()
    if old_primary is not None:
        old_primary.is_primary = False

    row.is_primary = True
    row.updated_at = datetime.utcnow()
    contact.email = row.email
    contact.updated_at = datetime.utcnow()
    db.flush()
    return row


# ── Merging two contacts ─────────────────────────────────────────────────────

class ContactMergeConflict(Exception):
    """Raised when the two contacts given are not eligible to merge."""


def merge_contacts(db: Session, target: Contact, source: Contact) -> Contact:
    """Merge `source` into `target`. One transaction; target wins on conflict.

    Moves every ActivityLog entry, ContactFact and address (alias row) from
    source onto target, makes source's own address an alias of target, and
    deletes source. Everything here is a bulk UPDATE against an explicit
    filter (never a per-row Python loop), so a contact with hundreds of
    entries costs the same as one with a handful, and the whole thing lives
    inside the caller's transaction — a failure partway rolls back with
    nothing written, never a half-merged pair.

    target keeps its own name, stage, next_touch_date, company and type
    untouched — nothing here ever copies those fields from source, so
    target's stage can never regress by way of a merge. responded and
    is_past_client carry over from source only when True, since both are
    permanent "this happened at least once" flags rather than current state.

    Raises ContactMergeConflict if target and source are the same contact.
    Caller commits.
    """
    if target.id == source.id:
        raise ContactMergeConflict("Cannot merge a contact into itself")

    db.query(ActivityLog).filter(ActivityLog.contact_id == source.id).update(
        {"contact_id": target.id}, synchronize_session=False,
    )
    db.query(ContactFact).filter(ContactFact.contact_id == source.id).update(
        {"contact_id": target.id}, synchronize_session=False,
    )
    db.query(ContactAddressOverride).filter(
        ContactAddressOverride.contact_id == source.id
    ).update(
        {"contact_id": target.id, "is_primary": False}, synchronize_session=False,
    )

    # Belt-and-suspenders for a source whose primary email predates the alias
    # table being kept in sync (or any other gap): guarantee the address
    # itself ends up mapped to target, even if no row existed to move above.
    if source.email:
        row = db.query(ContactAddressOverride).filter(
            ContactAddressOverride.email == source.email
        ).first()
        if row is None:
            db.add(ContactAddressOverride(
                email=source.email, contact_id=target.id, is_primary=False,
            ))
        else:
            row.contact_id = target.id
            row.is_primary = False

    if source.responded:
        target.responded = True
    if source.is_past_client:
        target.is_past_client = True
    target.updated_at = datetime.utcnow()

    db.flush()
    db.delete(source)
    db.flush()
    return target
