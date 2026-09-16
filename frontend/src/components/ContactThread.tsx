import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowLeft, ArrowDownLeft, ArrowUpRight, Building2, Check, ChevronDown,
  ChevronRight, Clock, Copy, FileText, History, Mail, Paperclip, Pencil, Phone,
  Plus, Trash2, TriangleAlert, Users, UserRound, X,
} from 'lucide-react'
import {
  acceptConflict, acceptPendingUpdate, addContactFact, createActivity,
  deleteContact, deleteContactFact, editContactFact, getContactThread,
  getContactTimeline, rejectConflict, rejectPendingUpdate, restampActivity,
  assignActivity, searchCompanies, searchContacts, updateContact,
} from '../api/client'
import type {
  ActivityStage, Channel, Contact, ContactFact, ContactType, DataConflict,
  PendingUpdate, ThreadHeader, TimelineEntry,
} from '../types'
import {
  CHANNELS, CLOSED_STAGE, CONTACT_STAGES, CONTACT_TYPE_LABELS,
  LEASE_SOURCE, MANUAL_SOURCE, STAGE_CHANGE_ACTION, UI_CONTACT_TYPES,
} from '../types'
import EntryEditor from './EntryEditor'
import LeaseCard from './LeaseCard'
import StageChangeDivider from './StageChangeDivider'

const OUTREACH_TYPE_LABELS: Record<string, string> = {
  tenant_match:         'Tenant Match Outreach',
  tenant_match_owner:   'Owner Outreach (Tenant Match)',
  tenant_match_tenant:  'Tenant Outreach (Tenant Match)',
  for_sale_vacancy:     'For Sale + Vacancy Outreach',
  lease_renewal:        'Lease Renewal Outreach',
  listing_rep:          'Listing Rep Outreach',
}

const CHANNEL_ICONS: Partial<Record<Channel, React.ElementType>> = {
  email: Mail, call: Phone, meeting: Users,
}

const STAGE_ACTIVE: Record<ActivityStage, string> = {
  'Sent':           'bg-blue-500/20 text-blue-300 border-blue-500/50',
  'Replied':        'bg-violet-500/20 text-violet-300 border-violet-500/50',
  'Interested':     'bg-emerald-500/20 text-emerald-300 border-emerald-500/50',
  'In Play':        'bg-amber-500/20 text-amber-300 border-amber-500/50',
  'Not Interested': 'bg-red-500/20 text-red-300 border-red-500/50',
  'Dormant':        'bg-surface-muted text-ink-secondary border-ink-muted/40',
  'Closed':         'bg-teal-500/20 text-teal-300 border-teal-500/50',
}

const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'

const todayISO = () => new Date().toISOString().slice(0, 10)

const plural = (n: number | null, word: string) =>
  n === null ? '—' : `${n} ${word}${n === 1 ? '' : 's'}`

const FIELD = "text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"

// ── Company type-ahead ───────────────────────────────────────────────────────
// Shared by "which company does this person work for" and "move this entry to a
// different company". Queries the four-column picker endpoint, not the
// unpaginated company list.
function CompanyPicker({
  value, placeholder, onPick,
}: {
  value: string
  placeholder: string
  onPick: (company: { id: number; name: string } | null) => void
}) {
  const [query, setQuery] = useState(value)
  const [hits, setHits] = useState<{ id: number; name: string; submarket: string | null }[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => { setQuery(value) }, [value])

  useEffect(() => {
    const term = query.trim()
    if (!term || term === value) { setHits([]); return }
    let cancelled = false
    const t = setTimeout(async () => {
      const rows = await searchCompanies(term)
      if (!cancelled) { setHits(rows); setOpen(true) }
    }, 180)
    return () => { cancelled = true; clearTimeout(t) }
  }, [query, value])

  return (
    <div className="relative">
      <input
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className={`${FIELD} w-full`}
      />
      {query && (
        <button
          onClick={() => { setQuery(''); setHits([]); onPick(null) }}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-muted hover:text-red-400"
          title="Clear"
        >
          <X size={11} />
        </button>
      )}
      {open && hits.length > 0 && (
        <div className="absolute z-20 mt-1 w-full max-h-48 overflow-y-auto bg-surface-card
                        border border-surface-border rounded-lg shadow-lg">
          {hits.map(h => (
            <button
              key={h.id}
              onClick={() => { onPick({ id: h.id, name: h.name }); setQuery(h.name); setOpen(false) }}
              className="w-full text-left px-2.5 py-1.5 text-[11px] text-ink-secondary
                         hover:bg-surface-muted hover:text-ink-primary"
            >
              {h.name}
              {h.submarket && <span className="text-ink-muted ml-2">{h.submarket}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Contact type-ahead ───────────────────────────────────────────────────────
function ContactPicker({
  placeholder, excludeId, onPick,
}: {
  placeholder: string
  excludeId?: number
  onPick: (contact: Contact) => void
}) {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<Contact[]>([])

  useEffect(() => {
    const term = query.trim()
    if (!term) { setHits([]); return }
    let cancelled = false
    const t = setTimeout(async () => {
      const rows = await searchContacts(term)
      if (!cancelled) setHits(rows.filter(c => c.id !== excludeId))
    }, 180)
    return () => { cancelled = true; clearTimeout(t) }
  }, [query, excludeId])

  return (
    <div className="relative">
      <input
        autoFocus
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder={placeholder}
        className={`${FIELD} w-full`}
      />
      {hits.length > 0 && (
        <div className="absolute z-20 mt-1 w-full max-h-48 overflow-y-auto bg-surface-card
                        border border-surface-border rounded-lg shadow-lg">
          {hits.map(c => (
            <button
              key={c.id}
              onClick={() => onPick(c)}
              className="w-full text-left px-2.5 py-1.5 text-[11px] text-ink-secondary
                         hover:bg-surface-muted hover:text-ink-primary"
            >
              {c.name}
              {c.company_name && <span className="text-emerald-400 ml-2">{c.company_name}</span>}
              {c.email && <span className="text-ink-muted ml-2">{c.email}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Contact details editor ───────────────────────────────────────────────────
// Everything the automation guesses about a person — name, address, title,
// type, employer — is correctable here. Changing the employer changes current
// employment only: every existing entry keeps the company it was stamped to,
// which is what keeps a departed contact's history on the old company's page.
function ContactEditor({
  contact, onSaved, onCancel, onDelete,
}: {
  contact: Contact
  onSaved: () => void
  onCancel: () => void
  onDelete: () => void
}) {
  const [form, setForm] = useState({
    name: contact.name ?? '',
    email: contact.email ?? '',
    phone: contact.phone ?? '',
    title: contact.title ?? '',
    contact_type: (contact.contact_type ?? 'tenant') as ContactType,
  })
  const [companyId, setCompanyId] = useState<number | null>(contact.company_id)
  const [triaged, setTriaged] = useState(contact.triaged)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (!form.name.trim()) { setError('Name cannot be empty.'); return }
    setSaving(true)
    setError(null)
    try {
      await updateContact(contact.id, {
        name: form.name.trim(),
        email: form.email.trim() || null,
        phone: form.phone.trim() || null,
        title: form.title.trim() || null,
        contact_type: form.contact_type,
        company_id: companyId,
        triaged,
      })
      onSaved()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not save this contact.')
      setSaving(false)
    }
  }

  return (
    <div className="bg-surface-card border border-accent-blue/40 rounded-xl p-4 mb-3">
      <div className="grid grid-cols-2 gap-2">
        <input autoFocus placeholder="Name *" value={form.name}
               onChange={e => setForm({ ...form, name: e.target.value })} className={FIELD} />
        <input placeholder="Email" value={form.email}
               onChange={e => setForm({ ...form, email: e.target.value })} className={FIELD} />
        <input placeholder="Title" value={form.title}
               onChange={e => setForm({ ...form, title: e.target.value })} className={FIELD} />
        <input placeholder="Phone" value={form.phone}
               onChange={e => setForm({ ...form, phone: e.target.value })} className={FIELD} />
      </div>

      <div className="mt-2">
        <label className="text-[10px] text-ink-muted">
          Works at — changes current employment only; past entries keep their company
        </label>
        <CompanyPicker
          value={contact.company_name ?? ''}
          placeholder="Search companies…"
          onPick={c => setCompanyId(c?.id ?? null)}
        />
      </div>

      <div className="flex items-center gap-1.5 mt-2 flex-wrap">
        {UI_CONTACT_TYPES.map(t => (
          <button
            key={t}
            onClick={() => setForm({ ...form, contact_type: t })}
            className={`text-[10px] px-2.5 py-1 rounded-full border font-semibold transition-colors
              ${form.contact_type === t ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                                        : 'bg-surface-muted text-ink-muted border-surface-border'}`}
          >
            {CONTACT_TYPE_LABELS[t]}
          </button>
        ))}
        <div className="w-px h-4 bg-surface-border mx-1" />
        <button
          onClick={() => setTriaged(v => !v)}
          className={`text-[10px] px-2.5 py-1 rounded-full border font-semibold transition-colors
            ${triaged ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
                      : 'bg-amber-500/10 text-amber-400 border-amber-500/30'}`}
        >
          {triaged ? 'Triaged' : 'Untriaged'}
        </button>
      </div>

      {error && <p className="text-[11px] text-red-400 mt-2">{error}</p>}

      <div className="flex items-center justify-between gap-2 mt-3">
        <button
          onClick={onDelete}
          className="text-[10px] text-ink-muted hover:text-red-400 flex items-center gap-1"
        >
          <Trash2 size={10} /> Delete contact
        </button>
        <div className="flex items-center gap-2">
          <button onClick={onCancel} className="px-3 py-1.5 text-[11px] text-ink-muted hover:text-ink-primary">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving || !form.name.trim()}
            className="px-3 py-1.5 rounded-lg bg-accent-blue text-white text-[11px] font-semibold
                       hover:bg-accent-blueDim disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save contact'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Delete-contact dialog ────────────────────────────────────────────────────
// Two outcomes, stated plainly, because they are not the same decision.
function DeleteContactDialog({
  contact, entryCount, onDeleted, onCancel,
}: {
  contact: Contact
  entryCount: number
  onDeleted: () => void
  onCancel: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (mode: 'unattach' | 'cascade') => {
    setBusy(true)
    setError(null)
    try {
      await deleteContact(contact.id, mode)
      onDeleted()
    } catch {
      setError('Could not delete this contact.')
      setBusy(false)
    }
  }

  const n = `${entryCount} ${entryCount === 1 ? 'entry' : 'entries'}`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onCancel}>
      <div
        className="w-full max-w-md bg-surface-card border border-surface-border rounded-xl p-5"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-sm font-bold text-ink-primary">Delete {contact.name}?</h3>
        <p className="text-[11px] text-ink-muted mt-1">
          This person has {n}. Their facts are deleted either way — a fact is
          something learned about a person, so it cannot outlive the record.
          This cannot be undone.
        </p>

        {error && <p className="text-[11px] text-red-400 mt-2">{error}</p>}

        <div className="space-y-2 mt-4">
          <button
            onClick={() => run('unattach')}
            disabled={busy}
            className="w-full text-left px-3 py-2.5 rounded-lg border border-surface-border
                       bg-surface-muted hover:border-accent-blue/50 disabled:opacity-50"
          >
            <div className="text-[11px] font-bold text-ink-primary">Keep the entries</div>
            <div className="text-[10px] text-ink-muted mt-0.5">
              Delete the contact. The {n} stay in All Activity with no person attached,
              and can be reassigned later.
            </div>
          </button>
          <button
            onClick={() => run('cascade')}
            disabled={busy}
            className="w-full text-left px-3 py-2.5 rounded-lg border border-red-500/30
                       bg-red-500/5 hover:border-red-500/60 disabled:opacity-50"
          >
            <div className="text-[11px] font-bold text-red-300">Delete the entries too</div>
            <div className="text-[10px] text-ink-muted mt-0.5">
              Delete the contact and all {n}, along with anything the intelligence
              layer derived from them. Nothing survives in All Activity.
            </div>
          </button>
        </div>

        <div className="flex justify-end mt-3">
          <button onClick={onCancel} className="px-3 py-1.5 text-[11px] text-ink-muted hover:text-ink-primary">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Slot 1 — Where we are ────────────────────────────────────────────────────
// Largest and top: on a call this is what Jack needs first.
function WhereWeAre({
  header, onStage, onNextTouch,
}: {
  header: ThreadHeader
  onStage: (stage: ActivityStage) => void
  onNextTouch: (date: string | null) => void
}) {
  const c = header.contact
  const stage = (c.stage ?? 'Sent') as ActivityStage
  const LastIcon = CHANNEL_ICONS[header.last_touch_channel ?? 'other']
  const overdue = !!c.next_touch_date && c.next_touch_date <= todayISO()

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <span className={`text-xs px-2.5 py-1 rounded-full border font-bold ${STAGE_ACTIVE[stage]}`}>
          {stage}
        </span>
        <span className="text-[11px] text-ink-muted">
          {plural(header.days_in_stage, 'day')} in stage
        </span>
        {header.last_touch_date && (
          <span className="text-[11px] text-ink-secondary flex items-center gap-1">
            {LastIcon && <LastIcon size={11} />}
            Last touch {fmtDate(header.last_touch_date)}
          </span>
        )}
        {header.days_of_silence !== null && (
          <span className="text-[11px] text-ink-muted flex items-center gap-1">
            <Clock size={11} />
            {plural(header.days_of_silence, 'day')} of silence
          </span>
        )}
        {/* Someone copied on ten emails has no relationship with Jack. This
            header has to say so plainly rather than read as an active thread
            with a last touch and a silence clock. */}
        {header.copied_only && (
          <span
            className="text-[10px] px-2 py-0.5 rounded border font-semibold
                       bg-surface-muted text-ink-secondary border-ink-muted/40
                       flex items-center gap-1"
            title={
              `On the Cc line of ${header.copied_count} ` +
              `email${header.copied_count === 1 ? '' : 's'} — never written to ` +
              `directly. A reply from them starts the relationship.`
            }
          >
            <Copy size={10} /> Copied, never directly contacted
          </span>
        )}
        {!header.copied_only && header.copied_count > 0 && (
          <span className="text-[11px] text-ink-muted flex items-center gap-1">
            <Copy size={11} />
            {header.copied_count} copied
          </span>
        )}
      </div>

      {/* A placed tenant whose lease has come back around. This is the line
          Jack opens the call with, so it sits above the open loop. */}
      {header.past_client_reentry && (
        <div className="mb-3 bg-teal-500/10 border border-teal-400/40 rounded-lg px-3 py-2">
          <p className="text-xs text-teal-200 font-bold flex items-center gap-1.5">
            <History size={12} />
            You placed this tenant — their lease is back in the window
            {header.company_lease_expiry_months !== null &&
              ` (${header.company_lease_expiry_months} months out)`}
          </p>
          <p className="text-[10px] text-ink-secondary mt-0.5">
            Stage stays Closed until you move it. The whole history is below.
          </p>
        </div>
      )}
      {/* Empty when the newest entry carries no open loop — a stale follow-up
          from an older entry is worse than no line at all. */}
      {header.open_loop && (
        <div className="text-sm text-ink-primary font-semibold mb-3">
          ↳ {header.open_loop}
        </div>
      )}
      {!header.open_loop && stage === CLOSED_STAGE && (
        <div className="text-[11px] text-ink-muted italic mb-3">
          Placed — nothing owed in either direction.
        </div>
      )}

      <div className="flex flex-wrap gap-1 mb-3">
        {CONTACT_STAGES.map(s => (
          <button
            key={s}
            onClick={() => s !== stage && onStage(s)}
            className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold transition-colors
              ${s === stage ? STAGE_ACTIVE[s]
                            : 'bg-surface-muted text-ink-muted border-surface-border hover:text-ink-secondary'}`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <span className={`text-[10px] ${overdue ? 'text-amber-400 font-bold' : 'text-ink-muted'}`}>
          Next touch:
        </span>
        <input
          type="date"
          value={c.next_touch_date ?? ''}
          onChange={e => onNextTouch(e.target.value || null)}
          className={`text-[11px] bg-surface-muted border rounded-lg px-2 py-1 text-ink-primary
                      focus:outline-none focus:border-accent-blue/50
                      ${overdue ? 'border-amber-500/60' : 'border-surface-border'}`}
        />
        {c.next_touch_date && (
          <button
            onClick={() => onNextTouch(null)}
            className="text-[10px] text-ink-muted hover:text-red-400"
            title="Clear next-touch date"
          >
            clear
          </button>
        )}
        {overdue && <span className="text-[10px] text-amber-400 font-bold">OVERDUE</span>}
      </div>
    </div>
  )
}

// ── One fact row, editable in place ──────────────────────────────────────────
function FactRow({
  fact, onJumpToEntry, onEdited, onDelete,
}: {
  fact: ContactFact
  onJumpToEntry: (entryId: number) => void
  onEdited: () => void
  onDelete: (fact: ContactFact) => void
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(fact.fact_text)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    const t = text.trim()
    if (!t || t === fact.fact_text) { setEditing(false); return }
    setSaving(true)
    try {
      await editContactFact(fact.id, { fact_text: t })
      setEditing(false)
      onEdited()
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <input
          autoFocus
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') void save()
            if (e.key === 'Escape') { setText(fact.fact_text); setEditing(false) }
          }}
          className={`${FIELD} flex-1`}
        />
        <button
          onClick={save}
          disabled={saving}
          className="text-[10px] px-2 py-1 rounded bg-accent-blue text-white font-semibold disabled:opacity-50"
        >
          Save
        </button>
        <button
          onClick={() => { setText(fact.fact_text); setEditing(false) }}
          className="text-[10px] text-ink-muted hover:text-ink-primary"
        >
          Cancel
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-2 group">
      <span className="text-xs text-ink-secondary flex-1">
        {fact.source_entry_id ? (
          <button
            onClick={() => onJumpToEntry(fact.source_entry_id!)}
            className="text-left hover:text-accent-blue hover:underline decoration-dotted"
          >
            {fact.fact_text}
          </button>
        ) : fact.fact_text}
        <span className="text-ink-muted ml-2 text-[10px]">{fmtDate(fact.learned_date)}</span>
      </span>
      <button
        onClick={() => setEditing(true)}
        className="opacity-0 group-hover:opacity-100 text-ink-muted hover:text-accent-blue transition-opacity"
        title="Edit this fact"
      >
        <Pencil size={10} />
      </button>
      <button
        onClick={() => onDelete(fact)}
        className="opacity-0 group-hover:opacity-100 text-ink-muted hover:text-red-400 transition-opacity"
        title="Delete this fact"
      >
        <Trash2 size={10} />
      </button>
    </div>
  )
}

// ── Slot 2 — Relationship context ────────────────────────────────────────────
// Facts are stored as discrete rows but rendered as prose; Jack never sees the
// raw list. Each line clicks through to the entry it came from.
function RelationshipContext({
  header, onJumpToEntry, onAddFact, onEditedFact, onDeleteFact,
}: {
  header: ThreadHeader
  onJumpToEntry: (entryId: number) => void
  onAddFact: (text: string) => void
  onEditedFact: () => void
  onDeleteFact: (fact: ContactFact) => void
}) {
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const [showAll, setShowAll] = useState(false)

  const submit = () => {
    const t = text.trim()
    if (!t) return
    onAddFact(t)
    setText('')
    setAdding(false)
  }

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-bold uppercase tracking-widest text-ink-muted">
          Relationship
        </span>
        <button
          onClick={() => setAdding(v => !v)}
          className="text-[10px] text-ink-muted hover:text-accent-blue flex items-center gap-1"
        >
          <Plus size={10} /> Add fact
        </button>
      </div>

      {header.relationship_lines.length === 0 && !adding && (
        <p className="text-xs text-ink-muted italic">
          Nothing learned about them yet.
        </p>
      )}

      {/* Two lines of prose, weighted toward recent and stage-relevant. */}
      {header.relationship_lines.map(line => (
        <p key={line.fact_id} className="text-sm text-ink-secondary leading-relaxed">
          {line.source_entry_id ? (
            <button
              onClick={() => onJumpToEntry(line.source_entry_id!)}
              className="text-left hover:text-accent-blue hover:underline decoration-dotted"
              title="Jump to the entry this came from"
            >
              {line.text}
            </button>
          ) : line.text}
        </p>
      ))}

      {adding && (
        <div className="mt-2 flex items-center gap-2">
          <input
            autoFocus
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') setAdding(false) }}
            placeholder="e.g. board is split on relocating"
            className={`${FIELD} flex-1`}
          />
          <button onClick={submit} className="text-[10px] px-2 py-1 rounded bg-accent-blue text-white font-semibold">
            Save
          </button>
        </div>
      )}

      {header.facts.length > 0 && (
        <button
          onClick={() => setShowAll(v => !v)}
          className="mt-2 text-[10px] text-ink-muted hover:text-ink-secondary flex items-center gap-1"
        >
          {showAll ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          {showAll ? 'Hide' : `All ${header.facts.length} ${header.facts.length === 1 ? 'fact' : 'facts'} — edit`}
        </button>
      )}

      {showAll && (
        <div className="mt-2 space-y-1">
          {header.facts.map(f => (
            <FactRow
              key={f.id}
              fact={f}
              onJumpToEntry={onJumpToEntry}
              onEdited={onEditedFact}
              onDelete={onDeleteFact}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Slot 3 — Deal context ────────────────────────────────────────────────────
function DealContext({
  header, onAccept, onReject, onAcceptUpdate, onRejectUpdate,
  onOpenCompany, onLeaseConfirmed,
}: {
  header: ThreadHeader
  onAccept: (c: DataConflict) => void
  onReject: (c: DataConflict) => void
  onAcceptUpdate: (u: PendingUpdate) => void
  onRejectUpdate: (u: PendingUpdate) => void
  onOpenCompany: () => void
  onLeaseConfirmed: () => void
}) {
  if (!header.company_name) {
    return (
      <div className="bg-surface-card border border-surface-border rounded-xl p-4">
        <span className="text-[10px] font-bold uppercase tracking-widest text-ink-muted">Deal</span>
        <p className="text-xs text-ink-muted italic mt-1">No company linked.</p>
      </div>
    )
  }
  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-bold uppercase tracking-widest text-ink-muted">Deal</span>
        <button
          onClick={onOpenCompany}
          className="text-[10px] text-ink-muted hover:text-accent-blue flex items-center gap-1"
        >
          <Building2 size={10} /> Company timeline
        </button>
      </div>
      <div className="flex items-center gap-3 flex-wrap text-xs">
        <span className="text-emerald-400 font-semibold">{header.company_name}</span>
        {header.company_submarket && (
          <span className="text-ink-secondary">{header.company_submarket}</span>
        )}
        {header.company_sf !== null && (
          <span className="text-ink-secondary flex items-center gap-1">
            {header.company_sf.toLocaleString()} SF
            <SourceMark source={header.company_sf_source} />
          </span>
        )}
        {header.company_lease_expiry && (
          <span className="text-ink-secondary flex items-center gap-1">
            Expiry {fmtDate(header.company_lease_expiry)}
            <SourceMark source={header.company_lease_expiry_source} />
          </span>
        )}
        {header.company_address && (
          <span className="text-ink-secondary flex items-center gap-1">
            {header.company_address}
            <SourceMark source={header.company_address_source} />
          </span>
        )}
        {/* A small marker, not a separate conflicts page — a disagreement is
            itself a lead, so it is surfaced here and on the company record. */}
        {header.has_data_conflict && (
          <span className="text-[10px] px-2 py-0.5 rounded border font-semibold
                           bg-amber-500/10 text-amber-400 border-amber-500/30
                           flex items-center gap-1">
            <TriangleAlert size={10} /> Data conflict
          </span>
        )}
      </div>

      {/* One-tap confirmation: both values and where each came from. */}
      {header.conflicts.map(conf => (
        <div
          key={conf.field}
          className="mt-3 bg-amber-500/5 border border-amber-500/30 rounded-lg p-3"
        >
          <p className="text-xs text-ink-primary">
            <span className="font-bold">{conf.company_name} {conf.label}:</span>{' '}
            record says <span className="font-semibold">{conf.verified_value ?? 'unknown'}</span>,
            they said <span className="font-semibold text-amber-400">{conf.reported_value}</span>
            {conf.reported_at ? ` on the ${fmtDate(conf.reported_at)} call` : ''}.
          </p>
          <div className="flex items-center gap-2 mt-2">
            <button
              onClick={() => onAccept(conf)}
              className="text-[10px] px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700
                         text-white font-semibold flex items-center gap-1"
            >
              <Check size={10} /> Use theirs
            </button>
            <button
              onClick={() => onReject(conf)}
              className="text-[10px] px-2.5 py-1 rounded bg-surface-muted hover:bg-surface-border
                         text-ink-secondary font-semibold flex items-center gap-1"
            >
              <X size={10} /> Keep record
            </button>
          </div>
        </div>
      ))}

      {/* The same decision as a conflict above, from written correspondence:
          both values side by side with the sentence it came from. The sentence
          is what makes it answerable — "they said 40" is not reviewable. */}
      {header.pending_updates.map(upd => (
        <div
          key={upd.id}
          className="mt-3 bg-sky-500/5 border border-sky-500/30 rounded-lg p-3"
        >
          <p className="text-xs text-ink-primary">
            <span className="font-bold">{upd.company_name} {upd.label}:</span>{' '}
            record says <span className="font-semibold">{upd.current_value ?? 'unknown'}</span>,
            their email said <span className="font-semibold text-sky-300">{upd.proposed_value}</span>
            {upd.source_entry_date ? ` on ${fmtDate(upd.source_entry_date)}` : ''}.
          </p>
          {upd.source_sentence && (
            <p className="text-[11px] text-ink-secondary italic mt-1.5 pl-2
                          border-l-2 border-sky-500/30">
              “{upd.source_sentence}”
            </p>
          )}
          <div className="flex items-center gap-2 mt-2">
            <button
              onClick={() => onAcceptUpdate(upd)}
              className="text-[10px] px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700
                         text-white font-semibold flex items-center gap-1"
            >
              <Check size={10} /> Use theirs
            </button>
            <button
              onClick={() => onRejectUpdate(upd)}
              className="text-[10px] px-2.5 py-1 rounded bg-surface-muted hover:bg-surface-border
                         text-ink-secondary font-semibold flex items-center gap-1"
            >
              <X size={10} /> Keep record
            </button>
          </div>
        </div>
      ))}

      {/* The signed lease. Upload from here or from the company record; the
          extraction lands in a review panel, never straight onto the record. */}
      {header.contact.company_id !== null && (
        <LeaseCard
          companyPk={header.contact.company_id}
          companyBusinessId={header.company_business_id}
          onConfirmed={onLeaseConfirmed}
        />
      )}
    </div>
  )
}

// A small marker distinguishing a value read off the signed lease from one Jack
// typed, and both from a CoStar-imported one. Lease and manual both outrank
// CoStar, but only one of them came off the page.
function SourceMark({ source }: { source: string | null }) {
  if (source === LEASE_SOURCE) {
    return (
      <span
        className="text-[9px] px-1 py-0.5 rounded bg-teal-500/10 text-teal-300
                   border border-teal-500/30"
        title="Read off the signed lease and confirmed — outranks CoStar."
      >
        lease
      </span>
    )
  }
  if (source === MANUAL_SOURCE) {
    return (
      <span
        className="text-[9px] px-1 py-0.5 rounded bg-amber-500/10 text-amber-300
                   border border-amber-500/30"
        title="Entered by you, not read off the document — outranks CoStar."
      >
        manual
      </span>
    )
  }
  if (source === 'costar') {
    return (
      <span className="text-[9px] text-ink-muted/70" title="Imported from CoStar.">
        costar
      </span>
    )
  }
  return null
}

// ── Move an entry's company stamp ────────────────────────────────────────────
// Its own confirmed action, never an ordinary editable field: the stamp is what
// keeps a departed contact's history on the old company's page.
function RestampPanel({
  entry, onDone, onCancel,
}: {
  entry: TimelineEntry
  onDone: () => void
  onCancel: () => void
}) {
  const [picked, setPicked] = useState<{ id: number; name: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const apply = async () => {
    setBusy(true)
    try {
      await restampActivity(entry.id, picked?.id ?? null)
      onDone()
    } finally {
      setBusy(false)
    }
  }

  const current = entry.company_stamp_name ?? 'no company'
  const next = picked?.name ?? 'no company'

  return (
    <div className="mt-2 border-t border-amber-500/30 pt-3">
      <p className="text-[11px] text-amber-300 font-semibold flex items-center gap-1.5">
        <TriangleAlert size={11} /> Move this entry to a different company
      </p>
      <p className="text-[10px] text-ink-muted mt-1">
        This entry currently sits on <span className="text-ink-secondary font-semibold">{current}</span>’s
        timeline. Moving it takes it off that company’s page and puts it on another —
        it does not change who the entry belongs to, and it does not change where
        this person works now.
      </p>
      <div className="mt-2">
        <CompanyPicker
          value=""
          placeholder="Move to which company?"
          onPick={setPicked}
        />
      </div>
      <div className="flex items-center gap-2 mt-2 flex-wrap">
        <button
          onClick={apply}
          disabled={busy}
          className="text-[10px] px-2.5 py-1 rounded bg-amber-600 hover:bg-amber-700
                     text-white font-semibold disabled:opacity-50"
        >
          {busy ? 'Moving…' : `Move to ${next}`}
        </button>
        <button onClick={onCancel} className="text-[10px] text-ink-muted hover:text-ink-primary">
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Timeline row ─────────────────────────────────────────────────────────────
function ThreadEntry({
  entry, highlighted, contactId, onChanged,
}: {
  entry: TimelineEntry
  highlighted: boolean
  contactId: number
  onChanged: () => void
}) {
  const [mode, setMode] = useState<null | 'edit' | 'move-contact' | 'restamp'>(null)
  const inbound = entry.direction === 'inbound'
  const ChannelIcon = CHANNEL_ICONS[entry.channel ?? 'other']
  const discovery = [
    entry.disc_current_rent_psf !== null && `Rent $${entry.disc_current_rent_psf}/SF`,
    entry.disc_current_sf !== null && `${entry.disc_current_sf.toLocaleString()} SF`,
    entry.disc_lease_expiry && `Expiry ${entry.disc_lease_expiry}`,
    entry.disc_decision_timeline && `Timeline: ${entry.disc_decision_timeline}`,
    entry.disc_buildout_needs && `Buildout: ${entry.disc_buildout_needs}`,
    entry.disc_decision_maker && `Decision maker: ${entry.disc_decision_maker}`,
  ].filter(Boolean) as string[]

  const moveToContact = async (target: Contact) => {
    await assignActivity(entry.id, { contact_id: target.id })
    onChanged()
  }

  return (
    <div
      id={`thread-entry-${entry.id}`}
      className={`border rounded-xl p-3 transition-colors
        ${highlighted ? 'border-accent-blue ring-2 ring-accent-blue/40' : 'border-surface-border'}
        ${entry.participation
          /* Copied, not written to: muted, dashed and set back, so scanning the
             thread never mistakes it for correspondence with this person. */
          ? 'bg-surface-card/40 border-l-2 border-l-ink-muted/30 border-dashed opacity-75'
          : inbound ? 'bg-violet-500/5 border-l-2 border-l-violet-500/60'
                    : 'bg-surface-card border-l-2 border-l-blue-500/40'}`}
    >
      <div className="flex items-center gap-2 flex-wrap mb-1">
        {/* Direction is visually distinct — inbound is the thing Jack scans for.
            A copied entry has no direction worth reading: he was not part of
            it, so it is labelled for what it is. */}
        {entry.participation ? (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider
                       flex items-center gap-1 bg-surface-muted text-ink-muted"
            title="They were copied on this email, not written to. It counts toward nothing."
          >
            <Copy size={9} /> Copied
          </span>
        ) : (
          <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider
                            flex items-center gap-1
            ${inbound ? 'bg-violet-500/20 text-violet-300' : 'bg-blue-500/15 text-blue-300'}`}>
            {inbound ? <ArrowDownLeft size={9} /> : <ArrowUpRight size={9} />}
            {inbound ? 'In' : 'Out'}
          </span>
        )}
        <span className="text-[10px] text-ink-muted flex items-center gap-1 uppercase tracking-wider font-bold">
          {ChannelIcon && <ChannelIcon size={10} />}
          {entry.channel ?? 'other'}
        </span>
        <span className="text-[10px] text-ink-muted">{fmtDate(entry.log_date)}</span>
        {entry.company_stamp_name && (
          <span className="text-[10px] text-emerald-400/80">{entry.company_stamp_name}</span>
        )}
        {entry.outreach_type && (
          <span className="text-[9px] px-2 py-0.5 rounded border font-semibold
                           bg-violet-500/10 text-violet-400 border-violet-500/20">
            {OUTREACH_TYPE_LABELS[entry.outreach_type] ?? 'Outreach'}
          </span>
        )}
      </div>

      {mode === 'edit' ? (
        <EntryEditor
          log={entry}
          onSaved={() => { setMode(null); onChanged() }}
          onCancel={() => setMode(null)}
        />
      ) : (
        <>
          <p className="text-xs text-ink-secondary">{entry.action_taken}</p>
          {entry.source_note && (
            /* Subordinate to the summary: it says where the entry came from
               (a weekly roundup), not what happened. */
            <p className="text-[10px] text-ink-muted mt-0.5 italic">{entry.source_note}</p>
          )}
          {entry.outcome && <p className="text-xs text-ink-muted mt-1">→ {entry.outcome}</p>}
          {entry.follow_up_action && (
            <p className="text-xs text-amber-400 mt-1">↻ {entry.follow_up_action}</p>
          )}
          {entry.notes && <p className="text-[11px] text-ink-muted mt-1 italic">{entry.notes}</p>}
          {discovery.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {discovery.map(d => (
                <span key={d} className="text-[9px] px-2 py-0.5 rounded bg-surface-muted text-ink-secondary">
                  {d}
                </span>
              ))}
            </div>
          )}

          {/* What arrived on the email. Filed under the documents folder by
              year — deliberately not the lease flow: only Jack uploads an
              executed lease, because a draft and the signed copy look the
              same from here. */}
          {entry.attachments.length > 0 && (
            <div className="mt-2 space-y-1">
              {entry.attachments.map(att => (
                <div key={att.id} className="flex items-start gap-1.5 text-[10px]">
                  <Paperclip size={10} className="text-ink-muted mt-0.5 shrink-0" />
                  <span className="text-ink-secondary">
                    {att.file_name}
                    {att.description && (
                      <span className="text-ink-muted"> — {att.description}</span>
                    )}
                    {att.missing && (
                      <span
                        className="ml-1.5 text-amber-400"
                        title="Recorded, but the file is not in the documents folder."
                      >
                        (file missing)
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}

          {mode === 'move-contact' ? (
            <div className="mt-2 border-t border-surface-border pt-2">
              <p className="text-[10px] text-ink-muted mb-1.5">
                Move this entry to another contact — the entry keeps the company it
                is stamped to.
              </p>
              <ContactPicker
                placeholder="Search contacts…"
                excludeId={contactId}
                onPick={moveToContact}
              />
              <button
                onClick={() => setMode(null)}
                className="mt-1.5 text-[10px] text-ink-muted hover:text-ink-primary"
              >
                Cancel
              </button>
            </div>
          ) : mode === 'restamp' ? (
            <RestampPanel
              entry={entry}
              onDone={() => { setMode(null); onChanged() }}
              onCancel={() => setMode(null)}
            />
          ) : (
            <div className="mt-2 flex items-center gap-3 flex-wrap">
              <button
                onClick={() => setMode('edit')}
                className="text-[10px] text-ink-muted hover:text-accent-blue flex items-center gap-1"
              >
                <Pencil size={10} /> Edit
              </button>
              <button
                onClick={() => setMode('move-contact')}
                className="text-[10px] text-ink-muted hover:text-accent-blue flex items-center gap-1"
              >
                <UserRound size={10} /> Move to another contact
              </button>
              <button
                onClick={() => setMode('restamp')}
                className="text-[10px] text-ink-muted hover:text-amber-400 flex items-center gap-1"
              >
                <Building2 size={10} /> Move to a different company
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Log-entry form ───────────────────────────────────────────────────────────
// Logging an inbound reply to an existing contact: pick Inbound, pick the
// channel, type, Save. Three clicks plus typing. Saving an inbound entry moves
// the contact Sent → Replied server-side, so the header and the stage pill can
// never disagree.
function LogEntryForm({
  contactId, companyId, onLogged, onCancel,
}: {
  contactId: number
  companyId: number | null
  onLogged: () => void
  onCancel: () => void
}) {
  const [direction, setDirection] = useState<'outbound' | 'inbound'>('outbound')
  const [channel, setChannel] = useState<Channel>('email')
  const [text, setText] = useState('')
  const [outcome, setOutcome] = useState('')
  const [showDiscovery, setShowDiscovery] = useState(false)
  const [disc, setDisc] = useState({
    disc_current_rent_psf: '', disc_current_sf: '', disc_lease_expiry: '',
    disc_decision_timeline: '', disc_buildout_needs: '', disc_decision_maker: '',
  })
  const [saving, setSaving] = useState(false)

  const actionTypeFor = (ch: Channel) =>
    ch === 'call' ? 'CALL' : ch === 'meeting' ? 'MEETING' : ch === 'email' ? 'EMAIL' : 'NOTE'

  const save = async () => {
    if (!text.trim()) return
    setSaving(true)
    try {
      await createActivity({
        action_type: actionTypeFor(channel),
        action_taken: text.trim(),
        outcome: outcome.trim() || undefined,
        contact_id: contactId,
        company_id: companyId ?? undefined,
        company_stamp_id: companyId ?? undefined,
        direction,
        channel,
        disc_current_rent_psf: disc.disc_current_rent_psf ? Number(disc.disc_current_rent_psf) : null,
        disc_current_sf: disc.disc_current_sf ? Number(disc.disc_current_sf) : null,
        disc_lease_expiry: disc.disc_lease_expiry || null,
        disc_decision_timeline: disc.disc_decision_timeline || null,
        disc_buildout_needs: disc.disc_buildout_needs || null,
        disc_decision_maker: disc.disc_decision_maker || null,
      })
      onLogged()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-surface-card border border-accent-blue/40 rounded-xl p-4 mb-3">
      <div className="flex items-center gap-2 flex-wrap mb-3">
        {(['outbound', 'inbound'] as const).map(d => (
          <button
            key={d}
            onClick={() => setDirection(d)}
            className={`text-[10px] px-2.5 py-1 rounded-full border font-semibold transition-colors
              ${direction === d
                ? (d === 'inbound' ? 'bg-violet-500/20 text-violet-300 border-violet-500/50'
                                   : 'bg-blue-500/20 text-blue-300 border-blue-500/50')
                : 'bg-surface-muted text-ink-muted border-surface-border'}`}
          >
            {d === 'inbound' ? 'Inbound' : 'Outbound'}
          </button>
        ))}
        <div className="w-px h-4 bg-surface-border" />
        {CHANNELS.map(ch => (
          <button
            key={ch}
            onClick={() => setChannel(ch)}
            className={`text-[10px] px-2 py-1 rounded-full border font-semibold transition-colors
              ${channel === ch ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                               : 'bg-surface-muted text-ink-muted border-surface-border'}`}
          >
            {ch}
          </button>
        ))}
      </div>

      <textarea
        autoFocus
        rows={2}
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="What happened?"
        className={`${FIELD} w-full resize-none`}
      />
      <input
        value={outcome}
        onChange={e => setOutcome(e.target.value)}
        placeholder="Outcome (optional)"
        className={`${FIELD} w-full mt-2`}
      />

      <button
        onClick={() => setShowDiscovery(v => !v)}
        className="mt-2 text-[10px] text-ink-muted hover:text-ink-secondary flex items-center gap-1"
      >
        {showDiscovery ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        Discovery
      </button>
      {showDiscovery && (
        <div className="grid grid-cols-2 gap-2 mt-2">
          <input placeholder="Current rent $/SF" value={disc.disc_current_rent_psf}
                 onChange={e => setDisc({ ...disc, disc_current_rent_psf: e.target.value })} className={FIELD} />
          <input placeholder="Current SF" value={disc.disc_current_sf}
                 onChange={e => setDisc({ ...disc, disc_current_sf: e.target.value })} className={FIELD} />
          <input type="date" title="Lease expiry" value={disc.disc_lease_expiry}
                 onChange={e => setDisc({ ...disc, disc_lease_expiry: e.target.value })} className={FIELD} />
          <input placeholder="Decision timeline" value={disc.disc_decision_timeline}
                 onChange={e => setDisc({ ...disc, disc_decision_timeline: e.target.value })} className={FIELD} />
          <input placeholder="Buildout needs" value={disc.disc_buildout_needs}
                 onChange={e => setDisc({ ...disc, disc_buildout_needs: e.target.value })} className={FIELD} />
          <input placeholder="Decision maker" value={disc.disc_decision_maker}
                 onChange={e => setDisc({ ...disc, disc_decision_maker: e.target.value })} className={FIELD} />
        </div>
      )}

      <div className="flex justify-end gap-2 mt-3">
        <button onClick={onCancel} className="px-3 py-1.5 text-[11px] text-ink-muted hover:text-ink-primary">
          Cancel
        </button>
        <button
          onClick={save}
          disabled={saving || !text.trim()}
          className="px-3 py-1.5 rounded-lg bg-accent-blue text-white text-[11px] font-semibold
                     hover:bg-accent-blueDim disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Log entry'}
        </button>
      </div>
    </div>
  )
}

// ── The thread ───────────────────────────────────────────────────────────────
export default function ContactThread({
  contactId, onBack, onOpenCompany,
}: {
  contactId: number
  onBack: () => void
  onOpenCompany: (companyBusinessId: string) => void
}) {
  const [header, setHeader] = useState<ThreadHeader | null>(null)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [logging, setLogging] = useState(false)
  const [editingContact, setEditingContact] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [highlightId, setHighlightId] = useState<number | null>(null)

  const PAGE = 50
  // Stage clicks fire faster than the reload; the last one in wins so a burst
  // of pills never lands the header on a stale value.
  const stageSeq = useRef(0)

  const load = useCallback(async (reset = true) => {
    if (reset) setLoading(true)
    try {
      const [h, page] = await Promise.all([
        getContactThread(contactId),
        getContactTimeline(contactId, { limit: PAGE, offset: 0 }),
      ])
      setHeader(h)
      setEntries(page.entries)
      setTotal(page.total)
    } finally {
      setLoading(false)
    }
  }, [contactId])

  useEffect(() => { void load() }, [load])

  const loadMore = async () => {
    const page = await getContactTimeline(contactId, { limit: PAGE, offset: entries.length })
    setEntries(prev => [...prev, ...page.entries])
    setTotal(page.total)
  }

  const jumpToEntry = (entryId: number) => {
    const el = document.getElementById(`thread-entry-${entryId}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setHighlightId(entryId)
      setTimeout(() => setHighlightId(null), 2800)
    }
  }

  const handleStage = async (stage: ActivityStage) => {
    if (!header) return
    const seq = ++stageSeq.current
    // Optimistic; the reload picks up whatever divider survived the collapse.
    setHeader({ ...header, contact: { ...header.contact, stage } })
    await updateContact(contactId, { stage })
    if (seq === stageSeq.current) await load(false)
  }

  const handleNextTouch = async (d: string | null) => {
    if (!header) return
    setHeader({ ...header, contact: { ...header.contact, next_touch_date: d } })
    await updateContact(contactId, d === null
      ? { clear_next_touch: true }
      : { next_touch_date: d })
    await load(false)
  }

  const handleAddFact = async (text: string) => {
    await addContactFact({ contact_id: contactId, fact_text: text })
    await load(false)
  }

  const handleDeleteFact = async (fact: ContactFact) => {
    if (!window.confirm(`Delete this fact?\n\n"${fact.fact_text}"`)) return
    await deleteContactFact(fact.id)
    await load(false)
  }

  const handleAccept = async (conf: DataConflict) => {
    await acceptConflict(conf.company_id, conf.field)
    await load(false)
  }

  const handleReject = async (conf: DataConflict) => {
    await rejectConflict(conf.company_id, conf.field)
    await load(false)
  }

  // Accept writes the value onto the company marked conversation-sourced;
  // reject leaves the field alone and marks the disagreement. Same two
  // outcomes as a conflict, so the same two handlers shape.
  const handleAcceptUpdate = async (upd: PendingUpdate) => {
    await acceptPendingUpdate(upd.id)
    await load(false)
  }

  const handleRejectUpdate = async (upd: PendingUpdate) => {
    await rejectPendingUpdate(upd.id)
    await load(false)
  }

  if (loading && !header) {
    return <div className="text-center py-12 text-ink-muted">Loading thread…</div>
  }
  if (!header) {
    return (
      <div className="text-center py-12 text-ink-muted">
        <p className="text-sm">Could not load this contact.</p>
        <button onClick={onBack} className="mt-3 text-xs text-accent-blue">Back to contacts</button>
      </div>
    )
  }

  const c = header.contact

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={onBack}
            className="text-ink-muted hover:text-ink-primary flex-shrink-0"
            title="Back to contacts"
          >
            <ArrowLeft size={16} />
          </button>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold text-ink-primary truncate">{c.name}</h2>
              <button
                onClick={() => setEditingContact(v => !v)}
                className="text-ink-muted hover:text-accent-blue flex-shrink-0"
                title="Edit this contact"
              >
                <Pencil size={12} />
              </button>
            </div>
            <div className="text-[11px] text-ink-muted flex items-center gap-2 flex-wrap">
              {c.title && <span>{c.title}</span>}
              {c.email && <span>{c.email}</span>}
              {c.phone && <span>{c.phone}</span>}
              <span className="uppercase tracking-wider">{c.contact_type}</span>
              {!c.triaged && (
                <span className="text-amber-400/80">untriaged</span>
              )}
            </div>
          </div>
        </div>
        <button
          onClick={() => setLogging(v => !v)}
          className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-blue
                     text-white text-[11px] font-semibold hover:bg-accent-blueDim"
        >
          <Plus size={12} /> Log entry
        </button>
      </div>

      {editingContact && (
        <ContactEditor
          contact={c}
          onSaved={() => { setEditingContact(false); void load(false) }}
          onCancel={() => setEditingContact(false)}
          onDelete={() => { setEditingContact(false); setDeleting(true) }}
        />
      )}

      {deleting && (
        <DeleteContactDialog
          contact={c}
          entryCount={header.entry_count}
          onDeleted={() => { setDeleting(false); onBack() }}
          onCancel={() => setDeleting(false)}
        />
      )}

      {/* Header: where we are, relationship, deal — in that order, deliberately. */}
      <div className="space-y-3 mb-5">
        <WhereWeAre header={header} onStage={handleStage} onNextTouch={handleNextTouch} />
        <RelationshipContext
          header={header}
          onJumpToEntry={jumpToEntry}
          onAddFact={handleAddFact}
          onEditedFact={() => void load(false)}
          onDeleteFact={handleDeleteFact}
        />
        <DealContext
          onLeaseConfirmed={() => void load(false)}
          header={header}
          onAccept={handleAccept}
          onReject={handleReject}
          onAcceptUpdate={handleAcceptUpdate}
          onRejectUpdate={handleRejectUpdate}
          onOpenCompany={() => header.company_business_id && onOpenCompany(header.company_business_id)}
        />
      </div>

      {logging && (
        <LogEntryForm
          contactId={contactId}
          companyId={c.company_id}
          onLogged={() => { setLogging(false); void load(false) }}
          onCancel={() => setLogging(false)}
        />
      )}

      <div className="text-[10px] font-bold uppercase tracking-widest text-ink-muted mb-2 flex items-center gap-3">
        Timeline
        <div className="h-px flex-1 bg-surface-border" />
        <span>{header.entry_count} {header.entry_count === 1 ? 'entry' : 'entries'}</span>
      </div>

      {entries.length === 0 ? (
        <p className="text-xs text-ink-muted italic py-6 text-center">
          No entries yet. Log the first one.
        </p>
      ) : (
        <div className="space-y-2">
          {entries.map(e => (
            // A stage change is a divider, not a touch — no card, no direction
            // badge, no channel badge. Bursts are collapsed server-side.
            e.action_type === STAGE_CHANGE_ACTION ? (
              <StageChangeDivider
                key={e.id}
                stageFrom={e.stage_from}
                stageTo={e.stage_to}
                logDate={e.log_date}
                actionTaken={e.action_taken}
              />
            ) : (
              <ThreadEntry
                key={e.id}
                entry={e}
                highlighted={highlightId === e.id}
                contactId={contactId}
                onChanged={() => void load(false)}
              />
            )
          ))}
          {entries.length < total && (
            <button
              onClick={loadMore}
              className="w-full text-[11px] px-3 py-1.5 rounded-lg bg-surface-card border
                         border-surface-border text-ink-muted hover:text-ink-primary"
            >
              Show older — {entries.length} of {total} shown
            </button>
          )}
        </div>
      )}
    </div>
  )
}
