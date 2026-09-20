"""Contactless activity entries — the ones not yet put on a person.

The goal is that every entry ends up on a contact. Two rules shape everything
here, and both are Jack's:

  * **No placeholder contacts.** Nothing in this module invents a person to
    park an entry on. An entry with nobody behind it waits as itself.
  * **No auto-assignment.** A contactless entry for a company that already has
    contacts stays on that company's card until Jack moves it. Guessing which
    of three people at Scott Management a voicemail belongs to is how a thread
    ends up quietly wrong, and a wrong thread is worse than an empty one.

So a contactless entry is *held* — on its company's card when it has a company,
in the needs-a-contact queue either way — and only a deliberate move takes it
off. The queue draining to zero is the whole point of the surface.

A contactless entry's company is `company_stamp_id` when it has one, falling
back to `company_id`: the stamp is what the conversation was about and is the
better answer, but 54 of the manually-logged entries carry only the legacy
free link, and they have to appear on a card too.
"""
from datetime import date
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.contact import Contact, ContactFact
from app.services.contact_service import STAGE_CHANGE_ACTION, mark_engaged


def company_key_column():
    """The SQL expression for "which company does this entry belong to".

    A single COALESCE, so grouping contactless entries by company stays one
    aggregated query rather than a per-entry lookup.
    """
    return func.coalesce(ActivityLog.company_stamp_id, ActivityLog.company_id)


def contactless_filters():
    """The WHERE terms that define "contactless entry", in one place.

    Stage-change rows are excluded for the same reason the By Contact counts
    exclude them: a divider reading "Stage: Sent -> Replied" is not an entry
    that needs a person behind it. There are none contactless today; this keeps
    it that way if one is ever written.

    `archived` is deliberately NOT here. Archiving says "keep this out of my
    queue", not "this company is no longer holding it" — so a company card
    still counts an archived entry and opening the card still shows it. Only
    the queue and its badge filter on it, via not_archived().
    """
    return (
        ActivityLog.contact_id.is_(None),
        ActivityLog.action_type != STAGE_CHANGE_ACTION,
    )


def not_archived():
    """The queue's extra term. `.isnot(True)` rather than `.is_(False)`: it is
    null-safe for any row written before the column existed."""
    return ActivityLog.archived.isnot(True)


def contactless_query(db: Session):
    """Every entry with no contact attached, unordered."""
    return db.query(ActivityLog).filter(*contactless_filters())


def entry_company_id(log: ActivityLog) -> Optional[int]:
    """The Python-side twin of company_key_column()."""
    return log.company_stamp_id or log.company_id


def move_entry_to_contact(
    db: Session,
    log: ActivityLog,
    contact: Contact,
    *,
    fallback_company_stamp_id: Optional[int] = None,
) -> int:
    """Put one entry on a contact, and bring its facts with it.

    Returns the number of facts that moved.

    `company_stamp_id` is filled only when the entry has none: the stamp
    records what the conversation was about at the time, so moving an entry to
    someone who has since changed jobs must never rewrite it.

    **Facts follow a contactless entry.** ContactFact.contact_id is NOT NULL,
    so an entry with no contact has no facts of its own — but a multi-deal
    roundup writes the deal's facts onto the *sender* while leaving the deal
    entry itself contactless. Moving the Scott Management deal entry to a Scott
    Management contact has to carry the fact it produced, or the fact stays
    stranded on Ann, filed under a conversation that is no longer on her
    thread. Only facts sourced to THIS entry move; everything else the contact
    knows is untouched.

    Does not commit — the caller owns the transaction.
    """
    was_contactless = log.contact_id is None

    log.contact_id = contact.id
    if log.company_stamp_id is None:
        log.company_stamp_id = (
            fallback_company_stamp_id
            or log.company_id
            or contact.company_id
        )

    facts_moved = 0
    if was_contactless:
        stranded = (
            db.query(ContactFact)
            .filter(
                ContactFact.source_entry_id == log.id,
                ContactFact.contact_id != contact.id,
            )
            .all()
        )
        for fact in stranded:
            fact.contact_id = contact.id
            facts_moved += 1

    mark_engaged(db, contact)
    return facts_moved


def move_company_entries_to_contact(
    db: Session, company_pk: int, contact: Contact,
) -> Tuple[int, int]:
    """Move every contactless entry held by one company onto one contact.

    Returns (entries moved, facts moved). The whole company card in one action —
    which is the common case, because a company with contactless entries usually
    has exactly one person behind all of them.

    Does not commit — the caller owns the transaction.
    """
    logs: List[ActivityLog] = (
        db.query(ActivityLog)
        .filter(*contactless_filters())
        .filter(company_key_column() == company_pk)
        .order_by(ActivityLog.log_date.asc(), ActivityLog.id.asc())
        .all()
    )
    facts = 0
    for log in logs:
        facts += move_entry_to_contact(
            db, log, contact, fallback_company_stamp_id=company_pk,
        )
    return len(logs), facts


def needs_contact_count(db: Session) -> int:
    """How many entries are still waiting on a person — the badge number.

    Counts entries with a company and entries without, because both need the
    same thing from Jack. It reaching zero is the point of the queue, which is
    exactly why archived entries are excluded: a hundred entries that will
    never have a counterparty would put zero out of reach permanently and the
    number would stop meaning anything.
    """
    return (
        db.query(func.count(ActivityLog.id))
        .filter(*contactless_filters())
        .filter(not_archived())
        .scalar()
    ) or 0


def archived_count_for_company(db: Session, company_pk: int) -> int:
    """How many of a company's held entries are archived — the card's note."""
    return (
        db.query(func.count(ActivityLog.id))
        .filter(*contactless_filters())
        .filter(company_key_column() == company_pk)
        .filter(ActivityLog.archived.is_(True))
        .scalar()
    ) or 0


def last_touch_for_company(db: Session, company_pk: int) -> Optional[date]:
    """Most recent contactless entry date for a company — the card's last touch."""
    return (
        db.query(func.max(ActivityLog.log_date))
        .filter(*contactless_filters())
        .filter(company_key_column() == company_pk)
        .scalar()
    )
