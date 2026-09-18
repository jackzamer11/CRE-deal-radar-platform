import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ClipboardList, Plus, X, Phone, Mail, Users, FileText, Search, RefreshCw, Pencil, Trash2 } from 'lucide-react'
import { getActivity, createActivity, updateActivityNote, updateActivityStage, deleteActivity } from '../api/client'
import type { ActivityLog, ActionType, ActivityStage } from '../types'
import { STAGES, REVISIT_STAGES, STAGE_CHANGE_ACTION } from '../types'
import ContactList from '../components/ContactList'
import ContactThread from '../components/ContactThread'
import CompanyTimelinePanel from '../components/CompanyTimelinePanel'
import EntryEditor from '../components/EntryEditor'
import StageChangeDivider from '../components/StageChangeDivider'
import { formatDate as formatDateOnly } from '../dates'

const ACTION_ICONS: Record<ActionType, React.ElementType> = {
  CALL:          Phone,
  EMAIL:         Mail,
  MEETING:       Users,
  SIGNAL_UPDATE: RefreshCw,
  RESEARCH:      Search,
  NOTE:          FileText,
  STAGE_CHANGE:  RefreshCw,   // never rendered — a divider has no badge
}

const ACTION_COLORS: Record<ActionType, string> = {
  CALL:          'text-blue-400 bg-blue-500/10',
  EMAIL:         'text-purple-400 bg-purple-500/10',
  MEETING:       'text-emerald-400 bg-emerald-500/10',
  SIGNAL_UPDATE: 'text-amber-400 bg-amber-500/10',
  RESEARCH:      'text-ink-muted bg-surface-muted',
  NOTE:          'text-ink-secondary bg-surface-muted',
  STAGE_CHANGE:  'text-ink-muted bg-surface-muted',
}

// Active-pill colors per stage (inactive pills share a neutral style).
const STAGE_ACTIVE: Record<ActivityStage, string> = {
  'Sent':           'bg-blue-500/20 text-blue-300 border-blue-500/50',
  'Replied':        'bg-violet-500/20 text-violet-300 border-violet-500/50',
  'Interested':     'bg-emerald-500/20 text-emerald-300 border-emerald-500/50',
  'In Play':        'bg-amber-500/20 text-amber-300 border-amber-500/50',
  'Not Interested': 'bg-red-500/20 text-red-300 border-red-500/50',
  'Dormant':        'bg-surface-muted text-ink-secondary border-ink-muted/40',
  'Closed':         'bg-teal-500/20 text-teal-300 border-teal-500/50',
}

const OUTREACH_TYPE_LABELS: Record<string, string> = {
  tenant_match:         'Tenant Match Outreach',        // legacy — kept for existing log entries
  tenant_match_owner:   'Owner Outreach (Tenant Match)',
  tenant_match_tenant:  'Tenant Outreach (Tenant Match)',
  for_sale_vacancy:     'For Sale + Vacancy Outreach',
  lease_renewal:        'Lease Renewal Outreach',
  listing_rep:          'Listing Rep Outreach',
}

function OutreachTypeBadge({ outreachType }: { outreachType: string | null }) {
  if (!outreachType) return null
  const label = OUTREACH_TYPE_LABELS[outreachType] ?? 'Outreach'
  return (
    <span className="text-[9px] px-2 py-0.5 rounded border font-semibold
                     bg-violet-500/10 text-violet-400 border-violet-500/20">
      {label}
    </span>
  )
}

function ActionBadge({ type }: { type: ActionType }) {
  const Icon = ACTION_ICONS[type] || FileText
  const color = ACTION_COLORS[type] || ACTION_COLORS.NOTE
  return (
    <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${color}`}>
      <Icon size={13} />
    </div>
  )
}

function formatDate(dateStr: string): string {
  return formatDateOnly(dateStr, {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

const todayISO = () => new Date().toISOString().slice(0, 10)

// How many entries are mounted at a time. Everything stays loaded in state
// and in the filter counts — this caps only the DOM, which is what made
// filter switches and note saves slow once the log passed ~300 entries.
const PAGE_SIZE = 60

// ── Stage selector (button set) with inline revisit / follow-up date picker ────
function StageSelector({
  log,
  onChange,
}: {
  log: ActivityLog
  onChange: (stage: ActivityStage, nextTouchDate?: string | null) => void
}) {
  const current = (log.stage ?? 'Sent') as ActivityStage
  const isRevisit  = REVISIT_STAGES.includes(current)
  const isOptional = current === 'Interested' || current === 'In Play'

  const handleClick = (stage: ActivityStage) => {
    if (stage === current) return
    // Moving to Dormant / Not Interested defaults the revisit date to today so the
    // inline picker appears pre-filled and the contact surfaces in Re-engage Today.
    if (REVISIT_STAGES.includes(stage)) onChange(stage, log.next_touch_date ?? todayISO())
    else onChange(stage) // Sent/Replied clear the date server-side; others keep existing
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap gap-1">
        {STAGES.map(s => {
          const active = s === current
          return (
            <button
              key={s}
              onClick={() => handleClick(s)}
              className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold transition-colors
                ${active ? STAGE_ACTIVE[s]
                         : 'bg-surface-muted text-ink-muted border-surface-border hover:text-ink-secondary'}`}
            >
              {s}
            </button>
          )
        })}
      </div>
      {(isRevisit || isOptional) && (
        <div className="mt-2 flex items-center gap-2">
          <span className={`text-[10px] ${isRevisit ? 'text-amber-400' : 'text-ink-muted'}`}>
            {isRevisit ? 'Revisit on:' : 'Follow-up on:'}
          </span>
          <input
            type="date"
            value={log.next_touch_date ?? ''}
            onChange={e => onChange(current, e.target.value || null)}
            className="text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1
                       text-ink-primary focus:outline-none focus:border-accent-blue/50"
          />
        </div>
      )}
    </div>
  )
}

function NoteSection({
  log,
  onSaved,
}: {
  log: ActivityLog
  onSaved: (updated: ActivityLog) => void
}) {
  const [editing, setEditing] = useState(false)
  const [input, setInput]     = useState(log.notes ?? '')
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState<string | null>(null)

  // A failed save used to leave the box open with no explanation, which read as
  // "the button does nothing" — surface it instead.
  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await updateActivityNote(log.id, input)
      onSaved(updated)
      setEditing(false)
    } catch {
      setError('Could not save the note. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="mt-2 space-y-1.5">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          rows={3}
          placeholder="Add a note…"
          className="w-full text-xs bg-surface-muted border border-surface-border rounded-lg px-3 py-2
                     text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50
                     resize-none"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="text-[10px] px-3 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-700
                       text-white font-semibold disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save Note'}
          </button>
          <button
            onClick={() => { setEditing(false); setInput(log.notes ?? ''); setError(null) }}
            className="text-[10px] text-ink-muted hover:text-ink-primary"
          >
            Cancel
          </button>
        </div>
        {error && <p className="text-[10px] text-red-400">{error}</p>}
      </div>
    )
  }

  if (log.notes) {
    return (
      <div className="mt-1.5 flex items-start gap-1.5">
        <p className="text-[11px] text-ink-muted flex-1 leading-snug">{log.notes}</p>
        <button
          onClick={() => { setInput(log.notes ?? ''); setEditing(true) }}
          className="text-ink-muted hover:text-accent-blue flex-shrink-0 mt-0.5"
          title="Edit note"
        >
          <Pencil size={10} />
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setEditing(true)}
      className="mt-1 text-[10px] text-ink-muted hover:text-accent-blue"
    >
      + Add Note
    </button>
  )
}

// ── New entry form ───────────────────────────────────────────────────────────
// Owns its own draft state, so typing here re-renders only this form instead of
// every log entry behind it.
function NewActivityForm({
  onCreated,
  onCancel,
}: {
  onCreated: () => void | Promise<void>
  onCancel: () => void
}) {
  const [form, setForm] = useState({
    action_type: 'CALL',
    action_taken: '',
    outcome: '',
    follow_up_action: '',
  })
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
    if (!form.action_taken.trim()) return
    setSaving(true)
    try {
      await createActivity({
        action_type: form.action_type,
        action_taken: form.action_taken,
        outcome: form.outcome || undefined,
        follow_up_action: form.follow_up_action || undefined,
      })
      setForm({ action_type: 'CALL', action_taken: '', outcome: '', follow_up_action: '' })
    } finally {
      setSaving(false)
    }
    await onCreated()
  }

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-5 mb-6">
      <div className="flex items-center justify-between mb-4">
        <div className="text-sm font-semibold text-ink-primary">New Activity Entry</div>
        <button onClick={onCancel} className="text-ink-muted hover:text-ink-primary">
          <X size={16} />
        </button>
      </div>
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <label className="text-xs text-ink-muted w-24">Type</label>
          <select
            value={form.action_type}
            onChange={e => setForm(f => ({ ...f, action_type: e.target.value }))}
            className="bg-surface-muted border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
          >
            {['CALL','EMAIL','MEETING','RESEARCH','NOTE','SIGNAL_UPDATE'].map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div className="flex gap-3">
          <label className="text-xs text-ink-muted w-24 pt-2">Action Taken</label>
          <textarea
            value={form.action_taken}
            onChange={e => setForm(f => ({ ...f, action_taken: e.target.value }))}
            placeholder="What did you do? Who did you contact?"
            rows={2}
            className="flex-1 bg-surface-muted border border-surface-border text-ink-secondary text-xs
                       rounded-lg px-3 py-2 resize-none outline-none focus:border-accent-blue"
          />
        </div>
        <div className="flex gap-3">
          <label className="text-xs text-ink-muted w-24 pt-2">Outcome</label>
          <textarea
            value={form.outcome}
            onChange={e => setForm(f => ({ ...f, outcome: e.target.value }))}
            placeholder="Result of the action (optional)"
            rows={2}
            className="flex-1 bg-surface-muted border border-surface-border text-ink-secondary text-xs
                       rounded-lg px-3 py-2 resize-none outline-none focus:border-accent-blue"
          />
        </div>
        <div className="flex gap-3">
          <label className="text-xs text-ink-muted w-24 pt-2">Follow-up</label>
          <input
            value={form.follow_up_action}
            onChange={e => setForm(f => ({ ...f, follow_up_action: e.target.value }))}
            placeholder="Follow-up action (optional)"
            className="flex-1 bg-surface-muted border border-surface-border text-ink-secondary text-xs
                       rounded-lg px-3 py-2 outline-none focus:border-accent-blue"
          />
        </div>
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <button
          onClick={onCancel}
          className="px-4 py-2 text-xs text-ink-muted hover:text-ink-primary"
        >
          Cancel
        </button>
        <button
          onClick={handleCreate}
          disabled={saving || !form.action_taken.trim()}
          className="px-4 py-2 rounded-lg bg-accent-blue text-white text-xs font-semibold
                     hover:bg-accent-blueDim transition-colors disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save Entry'}
        </button>
      </div>
    </div>
  )
}

// ── One log entry ────────────────────────────────────────────────────────────
// memo'd: paired with the page's stable callbacks, a keystroke or a stage click
// re-renders only the rows whose own data changed, not all of them.
const LogRow = memo(function LogRow({
  log,
  highlighted,
  editing,
  onStageChange,
  onNoteSaved,
  onEdited,
  onStartEdit,
  onCancelEdit,
  onDelete,
}: {
  log: ActivityLog
  highlighted: boolean
  editing: boolean
  onStageChange: (log: ActivityLog, stage: ActivityStage, nextTouchDate?: string | null) => void
  onNoteSaved: (updated: ActivityLog) => void
  onEdited: (updated: ActivityLog) => void
  onStartEdit: (id: number) => void
  onCancelEdit: () => void
  onDelete: (log: ActivityLog) => void
}) {
  return (
    <div
      id={`activity-${log.id}`}
      className={`flex items-start gap-3 bg-surface-card border rounded-xl p-3 transition-colors
        ${highlighted ? 'border-accent-blue ring-2 ring-accent-blue/40' : 'border-surface-border'}`}
    >
      <ActionBadge type={log.action_type as ActionType} />
      <div className="flex-1 min-w-0">
        {log.contact_name ? (
          <>
            {/* Contact-first hierarchy */}
            <div className="text-sm font-bold text-ink-primary truncate">{log.contact_name}</div>
            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
              <span className="text-[10px] font-bold uppercase tracking-wider text-ink-muted">
                {log.action_type}
              </span>
              {log.property_address && (
                <span className="text-[11px] text-accent-blue truncate">{log.property_address}</span>
              )}
              {log.company_name && (
                <span className="text-[11px] text-emerald-400">{log.company_name}</span>
              )}
              {log.opportunity_ref && (
                <span className="text-[10px] text-ink-muted">{log.opportunity_ref}</span>
              )}
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2 mb-0.5 flex-wrap">
            <span className="text-[10px] font-bold uppercase tracking-wider text-ink-muted">
              {log.action_type}
            </span>
            {log.property_address && (
              <span className="text-[11px] text-accent-blue truncate">{log.property_address}</span>
            )}
            {log.company_name && (
              <span className="text-[11px] text-emerald-400">{log.company_name}</span>
            )}
            {log.opportunity_ref && (
              <span className="text-[10px] text-ink-muted">{log.opportunity_ref}</span>
            )}
          </div>
        )}
        {editing ? (
          <EntryEditor
            log={log}
            onSaved={onEdited}
            onCancel={onCancelEdit}
          />
        ) : (
        <>
        <p className="text-xs text-ink-secondary mt-0.5">{log.action_taken}</p>
        {log.source_note && (
          <p className="text-[10px] text-ink-muted mt-0.5 italic">{log.source_note}</p>
        )}
        {log.outreach_type && (
          <div className="mt-1">
            <OutreachTypeBadge outreachType={log.outreach_type} />
          </div>
        )}
        {log.outcome && (
          <p className="text-xs text-ink-muted mt-1">→ {log.outcome}</p>
        )}
        {log.follow_up_action && (
          <p className="text-xs text-amber-400 mt-1">↻ {log.follow_up_action}</p>
        )}
        <StageSelector log={log} onChange={(stage, nextDate) => onStageChange(log, stage, nextDate)} />
        <div className="flex items-center gap-3">
          <NoteSection log={log} onSaved={onNoteSaved} />
          <button
            onClick={() => onStartEdit(log.id)}
            className="mt-1 text-[10px] text-ink-muted hover:text-accent-blue flex items-center gap-1"
            title="Edit this entry"
          >
            <Pencil size={10} /> Edit entry
          </button>
          <button
            onClick={() => onDelete(log)}
            className="mt-1 text-[10px] text-ink-muted hover:text-red-400 flex items-center gap-1"
            title="Delete this entry"
          >
            <Trash2 size={10} /> Delete
          </button>
        </div>
        </>
        )}
      </div>
    </div>
  )
})

// ── All Activity — the original flat feed, preserved exactly as it was ───────
// Nothing in here changed when contact threads landed: the same load, the same
// stage pills, the same grouping, the same deep link. It stays the fallback
// view for the 355 entries that have no contact attached yet.
function AllActivityFeed() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [logs, setLogs] = useState<ActivityLog[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [stageFilter, setStageFilter] = useState<'All' | ActivityStage>('All')
  const [query, setQuery] = useState('')
  const [highlightId, setHighlightId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  // Mirror of `logs` for handlers that need the current list without depending
  // on it — a dependency would make every callback unstable and defeat memo.
  const logsRef = useRef<ActivityLog[]>([])
  useEffect(() => { logsRef.current = logs }, [logs])

  // Search runs on the SERVER, not over the loaded page. It has to reach the
  // linked contact and company names, which are not in the rows' prose:
  // summaries are written cleanly now, with the person and the company as
  // structured links, so a client-side filter over action_taken would return
  // nothing for "Corcoran" while nine entries sat linked to them.
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const term = query.trim()
      const data = await getActivity({ limit: 1000, ...(term ? { q: term } : {}) })
      setLogs(data)
    } finally {
      setLoading(false)
    }
  }, [query])

  // Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => { void load() }, query.trim() ? 250 : 0)
    return () => clearTimeout(t)
  }, [load, query])

  // Deep link: /activity?focus=<id> scrolls to and highlights that entry.
  useEffect(() => {
    if (loading) return
    const focus = Number(searchParams.get('focus'))
    if (!focus) return
    // The linked entry may sit past the current page — mount enough rows to
    // reach it first, so a deep link never lands on nothing.
    const idx = displayedLogs.findIndex(l => l.id === focus)
    if (idx >= 0 && idx >= visibleCount) {
      setVisibleCount(idx + 1)
      return
    }
    const el = document.getElementById(`activity-${focus}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setHighlightId(focus)
      const t = setTimeout(() => setHighlightId(null), 2800)
      // Clear the param so a refresh doesn't re-trigger.
      const next = new URLSearchParams(searchParams)
      next.delete('focus')
      setSearchParams(next, { replace: true })
      return () => clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, logs.length, visibleCount])

  const handleCreated = useCallback(async () => {
    setShowForm(false)
    await load()
  }, [load])

  const handleNoteUpdated = useCallback((updated: ActivityLog) => {
    setLogs(prev => prev.map(l => l.id === updated.id ? { ...l, notes: updated.notes } : l))
  }, [])

  // Full-entry edits replace the row outright (any field may have changed).
  const handleEdited = useCallback((updated: ActivityLog) => {
    setLogs(prev => prev.map(l => l.id === updated.id ? updated : l))
    setEditingId(null)
  }, [])

  const handleStartEdit = useCallback((id: number) => setEditingId(id), [])
  const handleCancelEdit = useCallback(() => setEditingId(null), [])

  const handleDelete = useCallback(async (log: ActivityLog) => {
    const label = log.contact_name || log.action_taken?.slice(0, 60) || `entry #${log.id}`
    if (!window.confirm(
      `Delete this activity log?\n\n"${label}"\n\n` +
      `This also removes any facts the intelligence layer extracted from it. ` +
      `Other Deal Radar data is untouched. This cannot be undone.`
    )) return
    const prev = logsRef.current
    setLogs(cur => cur.filter(l => l.id !== log.id))  // optimistic
    try {
      await deleteActivity(log.id)
    } catch {
      setLogs(prev)  // restore on failure
      window.alert('Could not delete. Please try again.')
    }
  }, [])

  // Optimistic stage move — no page reload.
  const handleStageChange = useCallback(async (log: ActivityLog, stage: ActivityStage, nextTouchDate?: string | null) => {
    const optimisticDate = stage === 'Sent' ? null : (nextTouchDate !== undefined ? nextTouchDate : log.next_touch_date)
    setLogs(prev => prev.map(l => l.id === log.id ? { ...l, stage, next_touch_date: optimisticDate } : l))
    try {
      const updated = await updateActivityStage(log.id, {
        stage,
        next_touch_date: stage === 'Sent' ? null : (nextTouchDate !== undefined ? nextTouchDate : log.next_touch_date),
      })
      setLogs(prev => prev.map(l => l.id === updated.id ? updated : l))
    } catch {
      load() // revert to server truth on failure
    }
  }, [load])

  const displayedLogs = useMemo(
    () => stageFilter === 'All' ? logs : logs.filter(l => (l.stage ?? 'Sent') === stageFilter),
    [logs, stageFilter],
  )

  // One pass over logs instead of a full filter per stage pill, per render.
  const stageCounts = useMemo(() => {
    const counts = {} as Record<ActivityStage, number>
    for (const s of STAGES) counts[s] = 0
    for (const l of logs) {
      const s = (l.stage ?? 'Sent') as ActivityStage
      if (s in counts) counts[s] += 1
    }
    return counts
  }, [logs])

  const visibleLogs = useMemo(
    () => displayedLogs.slice(0, visibleCount),
    [displayedLogs, visibleCount],
  )
  const hasMore = displayedLogs.length > visibleCount

  const showMore = useCallback(
    () => setVisibleCount(c => c + PAGE_SIZE),
    [],
  )

  // Mount the next page as the end of the list comes into view.
  useEffect(() => {
    if (!hasMore) return
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver(
      entries => { if (entries[0]?.isIntersecting) showMore() },
      { rootMargin: '400px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasMore, showMore])

  // Group by date (newest day first)
  const dateGroups = useMemo(() => {
    const grouped: Record<string, ActivityLog[]> = {}
    for (const log of visibleLogs) {
      const key = log.log_date
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(log)
    }
    return Object.entries(grouped).sort(([a], [b]) => b.localeCompare(a))
  }, [visibleLogs])

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <span className="text-ink-muted text-sm">{displayedLogs.length} entries</span>
        <button
          onClick={() => setShowForm(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-blue text-white text-xs font-semibold
                     hover:bg-accent-blueDim transition-colors"
        >
          <Plus size={13} />
          Log Activity
        </button>
      </div>

      {/* Search. Matches the entry's own prose AND the names of the contact and
          company linked to it — entries no longer repeat those in the summary,
          so prose-only search would miss most of them. */}
      <div className="relative mb-3">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
        <input
          value={query}
          onChange={e => { setQuery(e.target.value); setVisibleCount(PAGE_SIZE) }}
          placeholder="Search entries, contacts and companies…"
          className="w-full bg-surface-card border border-surface-border rounded-lg
                     pl-9 pr-8 py-2 text-xs text-ink-primary placeholder:text-ink-muted
                     focus:outline-none focus:border-accent-blue/50"
        />
        {query && (
          <button
            onClick={() => { setQuery(''); setVisibleCount(PAGE_SIZE) }}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted hover:text-red-400"
            title="Clear search"
          >
            <X size={13} />
          </button>
        )}
      </div>

      {/* Stage filter bar */}
      <div className="flex items-center gap-1.5 mb-5 flex-wrap">
        {(['All', ...STAGES] as const).map(opt => {
          const active = stageFilter === opt
          const count = opt === 'All' ? logs.length : stageCounts[opt]
          return (
            <button
              key={opt}
              onClick={() => { setStageFilter(opt); setVisibleCount(PAGE_SIZE) }}
              className={`text-[11px] px-2.5 py-1 rounded-full border font-semibold transition-colors
                ${active ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                         : 'bg-surface-card text-ink-muted border-surface-border hover:text-ink-secondary'}`}
            >
              {opt}
              <span className="ml-1 text-ink-muted">{count}</span>
            </button>
          )
        })}
      </div>

      {/* New entry form */}
      {showForm && (
        <NewActivityForm onCreated={handleCreated} onCancel={() => setShowForm(false)} />
      )}

      {/* Log entries */}
      {loading ? (
        <div className="text-center py-12 text-ink-muted">Loading...</div>
      ) : displayedLogs.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <ClipboardList size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">
            {query.trim()
              ? `Nothing matches "${query.trim()}".`
              : stageFilter === 'All'
                ? 'No activity logged yet.'
                : `No entries in "${stageFilter}".`}
          </p>
          {stageFilter === 'All' && !query.trim() && (
            <p className="text-xs mt-1 text-ink-muted">Log calls, emails, and meetings to track your deal progress.</p>
          )}
        </div>
      ) : (
        <div className="space-y-6">
          {dateGroups.map(([date, entries]) => (
              <div key={date}>
                <div className="text-[10px] font-bold uppercase tracking-widest text-ink-muted mb-3 flex items-center gap-3">
                  {formatDate(date)}
                  <div className="h-px flex-1 bg-surface-border" />
                  <span>{entries.length}</span>
                </div>
                <div className="space-y-2">
                  {entries.map(log => (
                    // A stage change is a divider, not a touch — no card, no
                    // direction badge, no channel badge.
                    log.action_type === STAGE_CHANGE_ACTION ? (
                      <StageChangeDivider
                        key={log.id}
                        stageFrom={log.stage_from}
                        stageTo={log.stage_to}
                        logDate={log.log_date}
                        actionTaken={log.action_taken}
                      />
                    ) : (
                    <LogRow
                      key={log.id}
                      log={log}
                      highlighted={highlightId === log.id}
                      editing={editingId === log.id}
                      onStageChange={handleStageChange}
                      onNoteSaved={handleNoteUpdated}
                      onEdited={handleEdited}
                      onStartEdit={handleStartEdit}
                      onCancelEdit={handleCancelEdit}
                      onDelete={handleDelete}
                    />
                    )
                  ))}
                </div>
              </div>
            ))
          }
          {hasMore && (
            <div ref={sentinelRef} className="pt-1 text-center">
              <button
                onClick={showMore}
                className="text-[11px] px-3 py-1.5 rounded-lg bg-surface-card border border-surface-border
                           text-ink-muted hover:text-ink-primary transition-colors"
              >
                Show older — {visibleLogs.length} of {displayedLogs.length} shown
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}


// ── The page ─────────────────────────────────────────────────────────────────
// Two views over the same data. By Contact is the default; All Activity is the
// original flat feed, kept intact.
type View = 'contacts' | 'all'

export default function ActivityLogPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // The view, the open thread and the open company all live in the URL, so a
  // deep link from the Dashboard or a browser Back lands where it should.
  const view = (searchParams.get('view') === 'all' ? 'all' : 'contacts') as View
  const openContactId = Number(searchParams.get('contact')) || null
  const openCompanyId = searchParams.get('company')

  const setParam = useCallback((key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams)
    if (value === null) next.delete(key)
    else next.set(key, value)
    setSearchParams(next, { replace: false })
  }, [searchParams, setSearchParams])

  const setView = (v: View) => {
    const next = new URLSearchParams(searchParams)
    next.set('view', v)
    next.delete('contact')
    setSearchParams(next, { replace: false })
  }

  // A ?focus=<entry id> deep link still means the flat feed — that is where
  // entry ids resolve.
  useEffect(() => {
    if (searchParams.get('focus') && searchParams.get('view') !== 'all') {
      const next = new URLSearchParams(searchParams)
      next.set('view', 'all')
      setSearchParams(next, { replace: true })
    }
  }, [searchParams, setSearchParams])

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-center justify-between mb-5 gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <ClipboardList size={20} className="text-blue-400 flex-shrink-0" />
          <h1 className="text-xl font-bold text-ink-primary">Activity Log</h1>
        </div>
        <div className="flex items-center gap-1 bg-surface-card border border-surface-border rounded-lg p-0.5">
          {([['contacts', 'By Contact'], ['all', 'All Activity']] as const).map(([v, label]) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`text-[11px] px-3 py-1.5 rounded-md font-semibold transition-colors
                ${view === v ? 'bg-accent-blue text-white'
                             : 'text-ink-muted hover:text-ink-secondary'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {view === 'all' ? (
        <AllActivityFeed />
      ) : openContactId ? (
        <ContactThread
          contactId={openContactId}
          onBack={() => setParam('contact', null)}
          onOpenCompany={businessId => setParam('company', businessId)}
        />
      ) : (
        <ContactList onOpen={id => setParam('contact', String(id))} />
      )}

      {openCompanyId && (
        <CompanyTimelinePanel
          companyId={openCompanyId}
          onClose={() => setParam('company', null)}
          onOpenContact={id => {
            const next = new URLSearchParams(searchParams)
            next.delete('company')
            next.set('view', 'contacts')
            next.set('contact', String(id))
            setSearchParams(next, { replace: false })
          }}
        />
      )}
    </div>
  )
}
