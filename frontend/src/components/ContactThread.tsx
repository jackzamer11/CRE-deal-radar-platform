import { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft, ArrowDownLeft, ArrowUpRight, Building2, Check, ChevronDown,
  ChevronRight, Clock, Mail, Phone, Plus, Trash2, TriangleAlert, Users, X,
} from 'lucide-react'
import {
  acceptConflict, addContactFact, createActivity, deleteContactFact,
  getContactThread, getContactTimeline, rejectConflict, updateContact,
} from '../api/client'
import type {
  ActivityStage, Channel, ContactFact, DataConflict, ThreadHeader,
  TimelineEntry,
} from '../types'
import { CHANNELS, STAGES } from '../types'

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
}

const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'

const todayISO = () => new Date().toISOString().slice(0, 10)

const plural = (n: number | null, word: string) =>
  n === null ? '—' : `${n} ${word}${n === 1 ? '' : 's'}`

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
      </div>

      {header.open_loop && (
        <div className="text-sm text-ink-primary font-semibold mb-3">
          ↳ {header.open_loop}
        </div>
      )}

      <div className="flex flex-wrap gap-1 mb-3">
        {STAGES.map(s => (
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

// ── Slot 2 — Relationship context ────────────────────────────────────────────
// Facts are stored as discrete rows but rendered as prose; Jack never sees the
// raw list. Each line clicks through to the entry it came from.
function RelationshipContext({
  header, onJumpToEntry, onAddFact, onDeleteFact,
}: {
  header: ThreadHeader
  onJumpToEntry: (entryId: number) => void
  onAddFact: (text: string) => void
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
            className="flex-1 text-xs bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5
                       text-ink-primary focus:outline-none focus:border-accent-blue/50"
          />
          <button onClick={submit} className="text-[10px] px-2 py-1 rounded bg-accent-blue text-white font-semibold">
            Save
          </button>
        </div>
      )}

      {header.facts.length > 2 && (
        <button
          onClick={() => setShowAll(v => !v)}
          className="mt-2 text-[10px] text-ink-muted hover:text-ink-secondary flex items-center gap-1"
        >
          {showAll ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          {showAll ? 'Hide' : `All ${header.facts.length} facts`}
        </button>
      )}

      {showAll && (
        <div className="mt-2 space-y-1">
          {header.facts.map(f => (
            <div key={f.id} className="flex items-start gap-2 group">
              <span className="text-xs text-ink-secondary flex-1">
                {f.source_entry_id ? (
                  <button
                    onClick={() => onJumpToEntry(f.source_entry_id!)}
                    className="text-left hover:text-accent-blue hover:underline decoration-dotted"
                  >
                    {f.fact_text}
                  </button>
                ) : f.fact_text}
                <span className="text-ink-muted ml-2 text-[10px]">{fmtDate(f.learned_date)}</span>
              </span>
              <button
                onClick={() => onDeleteFact(f)}
                className="opacity-0 group-hover:opacity-100 text-ink-muted hover:text-red-400 transition-opacity"
                title="Delete this fact"
              >
                <Trash2 size={10} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Slot 3 — Deal context ────────────────────────────────────────────────────
function DealContext({
  header, onAccept, onReject, onOpenCompany,
}: {
  header: ThreadHeader
  onAccept: (c: DataConflict) => void
  onReject: (c: DataConflict) => void
  onOpenCompany: () => void
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
          <span className="text-ink-secondary">{header.company_sf.toLocaleString()} SF</span>
        )}
        {header.company_lease_expiry && (
          <span className="text-ink-secondary">Expiry {fmtDate(header.company_lease_expiry)}</span>
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
    </div>
  )
}

// ── Timeline row ─────────────────────────────────────────────────────────────
function ThreadEntry({ entry, highlighted }: { entry: TimelineEntry; highlighted: boolean }) {
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

  return (
    <div
      id={`thread-entry-${entry.id}`}
      className={`border rounded-xl p-3 transition-colors
        ${highlighted ? 'border-accent-blue ring-2 ring-accent-blue/40' : 'border-surface-border'}
        ${inbound ? 'bg-violet-500/5 border-l-2 border-l-violet-500/60'
                  : 'bg-surface-card border-l-2 border-l-blue-500/40'}`}
    >
      <div className="flex items-center gap-2 flex-wrap mb-1">
        {/* Direction is visually distinct — inbound is the thing Jack scans for. */}
        <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider
                          flex items-center gap-1
          ${inbound ? 'bg-violet-500/20 text-violet-300' : 'bg-blue-500/15 text-blue-300'}`}>
          {inbound ? <ArrowDownLeft size={9} /> : <ArrowUpRight size={9} />}
          {inbound ? 'In' : 'Out'}
        </span>
        <span className="text-[10px] text-ink-muted flex items-center gap-1 uppercase tracking-wider font-bold">
          {ChannelIcon && <ChannelIcon size={10} />}
          {entry.channel ?? 'other'}
        </span>
        <span className="text-[10px] text-ink-muted">{fmtDate(entry.log_date)}</span>
        {entry.outreach_type && (
          <span className="text-[9px] px-2 py-0.5 rounded border font-semibold
                           bg-violet-500/10 text-violet-400 border-violet-500/20">
            {OUTREACH_TYPE_LABELS[entry.outreach_type] ?? 'Outreach'}
          </span>
        )}
      </div>
      <p className="text-xs text-ink-secondary">{entry.action_taken}</p>
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
    </div>
  )
}

// ── Log-entry form ───────────────────────────────────────────────────────────
// Logging an inbound reply to an existing contact: pick Inbound, pick the
// channel, type, Save. Three clicks plus typing.
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

  const field = "text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary focus:outline-none focus:border-accent-blue/50"

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
        className={`${field} w-full resize-none`}
      />
      <input
        value={outcome}
        onChange={e => setOutcome(e.target.value)}
        placeholder="Outcome (optional)"
        className={`${field} w-full mt-2`}
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
                 onChange={e => setDisc({ ...disc, disc_current_rent_psf: e.target.value })} className={field} />
          <input placeholder="Current SF" value={disc.disc_current_sf}
                 onChange={e => setDisc({ ...disc, disc_current_sf: e.target.value })} className={field} />
          <input type="date" title="Lease expiry" value={disc.disc_lease_expiry}
                 onChange={e => setDisc({ ...disc, disc_lease_expiry: e.target.value })} className={field} />
          <input placeholder="Decision timeline" value={disc.disc_decision_timeline}
                 onChange={e => setDisc({ ...disc, disc_decision_timeline: e.target.value })} className={field} />
          <input placeholder="Buildout needs" value={disc.disc_buildout_needs}
                 onChange={e => setDisc({ ...disc, disc_buildout_needs: e.target.value })} className={field} />
          <input placeholder="Decision maker" value={disc.disc_decision_maker}
                 onChange={e => setDisc({ ...disc, disc_decision_maker: e.target.value })} className={field} />
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
  const [highlightId, setHighlightId] = useState<number | null>(null)

  const PAGE = 50

  const load = useCallback(async (reset = true) => {
    setLoading(true)
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
    // Optimistic; the reload picks up the timeline event the change writes.
    setHeader({ ...header, contact: { ...header.contact, stage } })
    await updateContact(contactId, { stage })
    await load()
  }

  const handleNextTouch = async (d: string | null) => {
    if (!header) return
    setHeader({ ...header, contact: { ...header.contact, next_touch_date: d } })
    await updateContact(contactId, d === null
      ? { clear_next_touch: true }
      : { next_touch_date: d })
    await load()
  }

  const handleAddFact = async (text: string) => {
    await addContactFact({ contact_id: contactId, fact_text: text })
    await load()
  }

  const handleDeleteFact = async (fact: ContactFact) => {
    if (!window.confirm(`Delete this fact?\n\n"${fact.fact_text}"`)) return
    await deleteContactFact(fact.id)
    await load()
  }

  const handleAccept = async (conf: DataConflict) => {
    await acceptConflict(conf.company_id, conf.field)
    await load()
  }

  const handleReject = async (conf: DataConflict) => {
    await rejectConflict(conf.company_id, conf.field)
    await load()
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
            <h2 className="text-lg font-bold text-ink-primary truncate">{c.name}</h2>
            <div className="text-[11px] text-ink-muted flex items-center gap-2 flex-wrap">
              {c.title && <span>{c.title}</span>}
              {c.email && <span>{c.email}</span>}
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

      {/* Header: where we are, relationship, deal — in that order, deliberately. */}
      <div className="space-y-3 mb-5">
        <WhereWeAre header={header} onStage={handleStage} onNextTouch={handleNextTouch} />
        <RelationshipContext
          header={header}
          onJumpToEntry={jumpToEntry}
          onAddFact={handleAddFact}
          onDeleteFact={handleDeleteFact}
        />
        <DealContext
          header={header}
          onAccept={handleAccept}
          onReject={handleReject}
          onOpenCompany={() => header.company_business_id && onOpenCompany(header.company_business_id)}
        />
      </div>

      {logging && (
        <LogEntryForm
          contactId={contactId}
          companyId={c.company_id}
          onLogged={() => { setLogging(false); void load() }}
          onCancel={() => setLogging(false)}
        />
      )}

      <div className="text-[10px] font-bold uppercase tracking-widest text-ink-muted mb-2 flex items-center gap-3">
        Timeline
        <div className="h-px flex-1 bg-surface-border" />
        <span>{total}</span>
      </div>

      {entries.length === 0 ? (
        <p className="text-xs text-ink-muted italic py-6 text-center">
          No entries yet. Log the first one.
        </p>
      ) : (
        <div className="space-y-2">
          {entries.map(e => (
            <ThreadEntry key={e.id} entry={e} highlighted={highlightId === e.id} />
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
