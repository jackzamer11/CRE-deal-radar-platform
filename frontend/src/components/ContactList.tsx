import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Building2, Copy, History, Mail, Phone, Plus, Search, TriangleAlert, Users, X,
} from 'lucide-react'
import {
  createContact, getCompanyCards, getContacts, searchContacts,
} from '../api/client'
import type {
  ActivityStage, Channel, CompanyCardRow, Contact, ContactListRow, ContactType,
} from '../types'
import { CLOSED_STAGE, CONTACT_STAGES, CONTACT_TYPE_LABELS, UI_CONTACT_TYPES } from '../types'
import { formatDate } from '../dates'

const STAGE_PILL: Record<ActivityStage, string> = {
  'Sent':           'bg-blue-500/15 text-blue-300 border-blue-500/40',
  'Replied':        'bg-violet-500/15 text-violet-300 border-violet-500/40',
  'Interested':     'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  'In Play':        'bg-amber-500/15 text-amber-300 border-amber-500/40',
  'Not Interested': 'bg-red-500/15 text-red-300 border-red-500/40',
  'Dormant':        'bg-surface-muted text-ink-secondary border-ink-muted/40',
  // Placed. Reads as a result, not as a dead end.
  'Closed':         'bg-teal-500/15 text-teal-300 border-teal-500/40',
}

const CHANNEL_ICONS: Partial<Record<Channel, React.ElementType>> = {
  email: Mail, call: Phone, meeting: Users,
}

const fmtDate = (d: string | null) =>
  d ? formatDate(d, { month: 'short', day: 'numeric' }) : '—'

// ── New-contact form ─────────────────────────────────────────────────────────
function NewContactForm({
  onCreated, onCancel, initialName,
}: {
  onCreated: (c: Contact) => void
  onCancel: () => void
  initialName?: string
}) {
  const [form, setForm] = useState({
    name: initialName ?? '', email: '', phone: '', title: '',
    contact_type: 'tenant' as ContactType,
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (!form.name.trim()) return
    setSaving(true)
    setError(null)
    try {
      const created = await createContact({
        name: form.name.trim(),
        email: form.email.trim() || null,
        phone: form.phone.trim() || null,
        title: form.title.trim() || null,
        contact_type: form.contact_type,
      })
      onCreated(created)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not create this contact.')
    } finally {
      setSaving(false)
    }
  }

  const field = "text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary focus:outline-none focus:border-accent-blue/50"

  return (
    <div className="bg-surface-card border border-accent-blue/40 rounded-xl p-4 mb-4">
      <div className="grid grid-cols-2 gap-2">
        <input autoFocus placeholder="Name *" value={form.name}
               onChange={e => setForm({ ...form, name: e.target.value })} className={field} />
        <input placeholder="Email" value={form.email}
               onChange={e => setForm({ ...form, email: e.target.value })} className={field} />
        <input placeholder="Title" value={form.title}
               onChange={e => setForm({ ...form, title: e.target.value })} className={field} />
        <input placeholder="Phone" value={form.phone}
               onChange={e => setForm({ ...form, phone: e.target.value })} className={field} />
      </div>
      <div className="flex items-center gap-1.5 mt-2">
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
      </div>
      {error && <p className="text-[11px] text-red-400 mt-2">{error}</p>}
      <div className="flex justify-end gap-2 mt-3">
        <button onClick={onCancel} className="px-3 py-1.5 text-[11px] text-ink-muted hover:text-ink-primary">
          Cancel
        </button>
        <button
          onClick={save}
          disabled={saving || !form.name.trim()}
          className="px-3 py-1.5 rounded-lg bg-accent-blue text-white text-[11px] font-semibold
                     hover:bg-accent-blueDim disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Create contact'}
        </button>
      </div>
    </div>
  )
}

// ── One contact row ──────────────────────────────────────────────────────────
function ContactRow({ row, onOpen }: { row: ContactListRow; onOpen: (id: number) => void }) {
  const LastIcon = CHANNEL_ICONS[row.latest_entry_channel ?? 'other']
  const stage = (row.stage ?? 'Sent') as ActivityStage

  return (
    <button
      onClick={() => onOpen(row.id)}
      className="w-full text-left bg-surface-card border border-surface-border rounded-xl p-3
                 hover:border-accent-blue/50 transition-colors"
    >
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-bold text-ink-primary truncate">{row.name}</span>
            {row.company_name && (
              <span className="text-[11px] text-emerald-400 truncate">{row.company_name}</span>
            )}
            <span className={`text-[9px] px-2 py-0.5 rounded-full border font-semibold ${STAGE_PILL[stage]}`}>
              {stage}
            </span>
            {row.days_in_stage !== null && (
              <span className="text-[10px] text-ink-muted">{row.days_in_stage}d in stage</span>
            )}
            {!row.triaged && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400/90
                               border border-amber-500/20">
                untriaged
              </span>
            )}
            {/* The marker is the point: "you placed this tenant in this
                building" is the strongest opening line available on that call.
                The re-entry badge is louder than the plain past-client one
                because it is the reason a Closed contact is on this list. */}
            {row.past_client_reentry ? (
              <span className="text-[9px] px-1.5 py-0.5 rounded font-bold bg-teal-500/20
                               text-teal-200 border border-teal-400/50 flex items-center gap-1">
                <History size={9} />
                PAST CLIENT — back in window
                {row.lease_expiry_months !== null && ` (${row.lease_expiry_months}mo)`}
              </span>
            ) : row.is_past_client && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-300/90
                               border border-teal-500/25 flex items-center gap-1">
                <History size={9} /> past client
              </span>
            )}
          </div>

          <div className="flex items-center gap-3 mt-1 flex-wrap text-[11px] text-ink-muted">
            <span className="flex items-center gap-1">
              {LastIcon && <LastIcon size={10} />}
              {row.latest_entry_date ? `Last ${fmtDate(row.latest_entry_date)}` : 'No activity'}
            </span>
            <span>{row.entry_count} {row.entry_count === 1 ? 'entry' : 'entries'}</span>
            {/* Copies are not correspondence. A person who has only ever been
                on the Cc line reads as exactly that, never as active. */}
            {row.copied_only ? (
              <span
                className="flex items-center gap-1 text-ink-muted/80"
                title={
                  `On the Cc line of ${row.copied_count} ` +
                  `email${row.copied_count === 1 ? '' : 's'} — never written to directly.`
                }
              >
                <Copy size={10} /> copied only
              </span>
            ) : row.copied_count > 0 && (
              <span className="flex items-center gap-1">
                <Copy size={10} /> {row.copied_count} copied
              </span>
            )}
            <span className="uppercase tracking-wider">{row.contact_type}</span>
          </div>

          {row.latest_entry_summary && (
            <p className="text-[11px] text-ink-secondary mt-1 truncate">
              {row.latest_entry_summary}
            </p>
          )}
        </div>

        {row.next_touch_date && (
          <span className={`flex-shrink-0 text-[10px] px-2 py-1 rounded-lg font-semibold border
            ${row.overdue ? 'bg-amber-500/15 text-amber-300 border-amber-500/40'
                          : 'bg-surface-muted text-ink-muted border-surface-border'}`}>
            {row.overdue ? 'Due ' : 'Next '}{fmtDate(row.next_touch_date)}
          </span>
        )}
      </div>
    </button>
  )
}

// ── One company card ─────────────────────────────────────────────────────────
// A company holding entries that are not on a person yet. It sits in the same
// list as the contact cards because it is the same kind of thing: a record with
// a thread behind it. What it is missing is the person, and it says so.
function CompanyCard({
  row, onOpen,
}: {
  row: CompanyCardRow
  onOpen: (companyId: number, name: string) => void
}) {
  const LastIcon = CHANNEL_ICONS[row.latest_entry_channel ?? 'other']

  return (
    <button
      onClick={() => onOpen(row.id, row.name)}
      className="w-full text-left bg-surface-card border border-amber-500/30 rounded-xl p-3
                 hover:border-accent-blue/50 transition-colors"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Building2 size={12} className="text-emerald-400 flex-shrink-0" />
        <span className="text-sm font-bold text-ink-primary">{row.name}</span>
        {/* The reason this card exists rather than a person's. */}
        {row.contact_count === 0 ? (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400/90
                           border border-amber-500/20">no contacts yet</span>
        ) : (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-muted text-ink-muted
                           border border-surface-border">
            {row.contact_count} contact{row.contact_count === 1 ? '' : 's'} — none assigned
          </span>
        )}
        <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400/90
                         border border-amber-500/20">untriaged</span>
      </div>
      <div className="flex items-center gap-2 mt-1 text-[11px] text-ink-muted flex-wrap">
        <span>
          {row.entry_count} entr{row.entry_count === 1 ? 'y' : 'ies'} waiting on a contact
        </span>
        <span>·</span>
        <span className="flex items-center gap-1">
          {LastIcon && <LastIcon size={10} />} last touch {fmtDate(row.last_touch)}
        </span>
      </div>
      {row.latest_entry_summary && (
        <p className="text-[11px] text-ink-secondary mt-1 truncate">
          {row.latest_entry_summary}
        </p>
      )}
    </button>
  )
}

// ── The list ─────────────────────────────────────────────────────────────────
export default function ContactList({
  onOpen, onOpenCompany,
}: {
  onOpen: (id: number) => void
  onOpenCompany: (companyId: number, name: string) => void
}) {
  const [rows, setRows] = useState<ContactListRow[]>([])
  const [companyCards, setCompanyCards] = useState<CompanyCardRow[]>([])
  const [loading, setLoading] = useState(true)
  const [typeFilter, setTypeFilter] = useState<'All' | ContactType>('All')
  const [stageFilter, setStageFilter] = useState<'All' | ActivityStage>('All')
  // The default list shows triaged records only; untriaged sit behind this
  // toggle and stay fully searchable either way.
  const [showUntriaged, setShowUntriaged] = useState(false)
  const [creating, setCreating] = useState(false)
  const [query, setQuery] = useState('')
  const [searchHits, setSearchHits] = useState<Contact[] | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    const filters = {
      contact_type: typeFilter === 'All' ? undefined : typeFilter,
      stage: stageFilter === 'All' ? undefined : stageFilter,
      triaged: showUntriaged ? undefined : true,
      limit: 1000,
    }
    try {
      // Company cards run through the same filters. They are always untriaged,
      // so the default (triaged-only) list returns none and they appear the
      // moment "Show untriaged" is on — which is correct: a card is work that
      // has not been done.
      const [contacts, cards] = await Promise.all([
        getContacts(filters),
        getCompanyCards(filters),
      ])
      setRows(contacts)
      setCompanyCards(cards)
    } finally {
      setLoading(false)
    }
  }, [typeFilter, stageFilter, showUntriaged])

  useEffect(() => { void load() }, [load])

  // Type-ahead reaches untriaged contacts regardless of the toggle.
  useEffect(() => {
    const term = query.trim()
    if (!term) { setSearchHits(null); return }
    let cancelled = false
    const t = setTimeout(async () => {
      const hits = await searchContacts(term)
      if (!cancelled) setSearchHits(hits)
    }, 180)
    return () => { cancelled = true; clearTimeout(t) }
  }, [query])

  const untriagedCount = useMemo(
    () => rows.filter(r => !r.triaged).length,
    [rows],
  )

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4">
        <div className="relative flex-1 max-w-xs">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search name or email…"
            className="w-full text-[11px] bg-surface-card border border-surface-border rounded-lg
                       pl-8 pr-7 py-1.5 text-ink-primary focus:outline-none focus:border-accent-blue/50"
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-muted hover:text-ink-primary"
            >
              <X size={12} />
            </button>
          )}
        </div>
        <button
          onClick={() => setCreating(v => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-blue text-white
                     text-[11px] font-semibold hover:bg-accent-blueDim"
        >
          <Plus size={12} /> New contact
        </button>
      </div>

      {creating && (
        <NewContactForm
          initialName={query.trim() || undefined}
          onCancel={() => setCreating(false)}
          onCreated={c => { setCreating(false); setQuery(''); onOpen(c.id) }}
        />
      )}

      {/* Search results short-circuit the filtered list. */}
      {searchHits !== null ? (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-widest text-ink-muted">
            {searchHits.length} match{searchHits.length === 1 ? '' : 'es'}
          </div>
          {searchHits.map(c => (
            <button
              key={c.id}
              onClick={() => onOpen(c.id)}
              className="w-full text-left bg-surface-card border border-surface-border rounded-xl p-3
                         hover:border-accent-blue/50 transition-colors"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-bold text-ink-primary">{c.name}</span>
                {c.company_name && <span className="text-[11px] text-emerald-400">{c.company_name}</span>}
                {!c.triaged && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400/90
                                   border border-amber-500/20">untriaged</span>
                )}
                {/* Search reaches Closed contacts regardless of the filters,
                    so the row has to say where they stand. */}
                {c.stage === CLOSED_STAGE && (
                  <span className={`text-[9px] px-2 py-0.5 rounded-full border font-semibold
                                   ${STAGE_PILL[CLOSED_STAGE]}`}>Closed</span>
                )}
                {c.is_past_client && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-300/90
                                   border border-teal-500/25 flex items-center gap-1">
                    <History size={9} /> past client
                  </span>
                )}
              </div>
              {c.email && <div className="text-[11px] text-ink-muted mt-0.5">{c.email}</div>}
            </button>
          ))}
          {searchHits.length === 0 && (
            <div className="text-center py-8 text-ink-muted">
              <p className="text-xs">No contact matches “{query}”.</p>
              <button
                onClick={() => setCreating(true)}
                className="mt-2 text-[11px] text-accent-blue hover:underline"
              >
                Create “{query.trim()}” as a new contact
              </button>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="flex items-center gap-1.5 mb-3 flex-wrap">
            {(['All', ...UI_CONTACT_TYPES] as const).map(t => (
              <button
                key={t}
                onClick={() => setTypeFilter(t as 'All' | ContactType)}
                className={`text-[11px] px-2.5 py-1 rounded-full border font-semibold transition-colors
                  ${typeFilter === t ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                                     : 'bg-surface-card text-ink-muted border-surface-border hover:text-ink-secondary'}`}
              >
                {t === 'All' ? 'All' : CONTACT_TYPE_LABELS[t as ContactType]}
              </button>
            ))}
            <div className="w-px h-4 bg-surface-border mx-1" />
            {(['All', ...CONTACT_STAGES] as const).map(s => (
              <button
                key={s}
                onClick={() => setStageFilter(s as 'All' | ActivityStage)}
                className={`text-[11px] px-2.5 py-1 rounded-full border font-semibold transition-colors
                  ${stageFilter === s ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                                      : 'bg-surface-card text-ink-muted border-surface-border hover:text-ink-secondary'}`}
              >
                {s}
              </button>
            ))}
          </div>

          <div className="flex items-center justify-between mb-3">
            <span className="text-[11px] text-ink-muted">
              {rows.length} contact{rows.length === 1 ? '' : 's'}
              {/* Say it out loud rather than letting placed deals vanish
                  silently. Past clients back in the window are still here. */}
              {stageFilter === 'All' && (
                <span className="text-ink-muted/70">
                  {' '}· Closed hidden (filter to Closed to see them)
                </span>
              )}
            </span>
            <button
              onClick={() => setShowUntriaged(v => !v)}
              className={`text-[10px] px-2.5 py-1 rounded-full border font-semibold transition-colors
                ${showUntriaged ? 'bg-amber-500/15 text-amber-300 border-amber-500/40'
                                : 'bg-surface-card text-ink-muted border-surface-border hover:text-ink-secondary'}`}
            >
              {showUntriaged
                ? `Hiding nothing — ${untriagedCount} untriaged shown`
                : 'Show untriaged'}
            </button>
          </div>

          {loading ? (
            <div className="text-center py-12 text-ink-muted">Loading…</div>
          ) : rows.length === 0 && companyCards.length === 0 ? (
            <div className="text-center py-12 text-ink-muted">
              <Users size={32} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">No contacts yet.</p>
              <p className="text-xs mt-1">
                Create one, or log an email — contacts appear as you work.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {/* Companies holding unassigned entries lead, in their own group:
                  the contact rows below are ordered overdue-first, and a card
                  with no next-touch date has no place in that ordering. */}
              {companyCards.length > 0 && (
                <>
                  <div className="text-[10px] uppercase tracking-widest text-amber-400/80 pt-1">
                    Waiting on a contact — {companyCards.length} compan
                    {companyCards.length === 1 ? 'y' : 'ies'}
                  </div>
                  {companyCards.map(card => (
                    <CompanyCard key={`co-${card.id}`} row={card} onOpen={onOpenCompany} />
                  ))}
                  {rows.length > 0 && (
                    <div className="text-[10px] uppercase tracking-widest text-ink-muted pt-2">
                      Contacts
                    </div>
                  )}
                </>
              )}
              {rows.map(row => (
                <ContactRow key={row.id} row={row} onOpen={onOpen} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
