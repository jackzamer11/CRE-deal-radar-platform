import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ClipboardList, Plus, X, Phone, Mail, Users, FileText, Search, RefreshCw, Pencil } from 'lucide-react'
import { getActivity, createActivity, updateActivityNote, updateActivityStage } from '../api/client'
import type { ActivityLog, ActionType, ActivityStage } from '../types'
import { STAGES, REVISIT_STAGES } from '../types'

const ACTION_ICONS: Record<ActionType, React.ElementType> = {
  CALL:          Phone,
  EMAIL:         Mail,
  MEETING:       Users,
  SIGNAL_UPDATE: RefreshCw,
  RESEARCH:      Search,
  NOTE:          FileText,
}

const ACTION_COLORS: Record<ActionType, string> = {
  CALL:          'text-blue-400 bg-blue-500/10',
  EMAIL:         'text-purple-400 bg-purple-500/10',
  MEETING:       'text-emerald-400 bg-emerald-500/10',
  SIGNAL_UPDATE: 'text-amber-400 bg-amber-500/10',
  RESEARCH:      'text-ink-muted bg-surface-muted',
  NOTE:          'text-ink-secondary bg-surface-muted',
}

// Active-pill colors per stage (inactive pills share a neutral style).
const STAGE_ACTIVE: Record<ActivityStage, string> = {
  'Sent':           'bg-blue-500/20 text-blue-300 border-blue-500/50',
  'Replied':        'bg-violet-500/20 text-violet-300 border-violet-500/50',
  'Interested':     'bg-emerald-500/20 text-emerald-300 border-emerald-500/50',
  'In Play':        'bg-amber-500/20 text-amber-300 border-amber-500/50',
  'Not Interested': 'bg-red-500/20 text-red-300 border-red-500/50',
  'Dormant':        'bg-surface-muted text-ink-secondary border-ink-muted/40',
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
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

const todayISO = () => new Date().toISOString().slice(0, 10)

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

  const handleSave = async () => {
    setSaving(true)
    try {
      const updated = await updateActivityNote(log.id, input)
      onSaved(updated)
      setEditing(false)
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
            onClick={() => { setEditing(false); setInput(log.notes ?? '') }}
            className="text-[10px] text-ink-muted hover:text-ink-primary"
          >
            Cancel
          </button>
        </div>
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

export default function ActivityLogPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [logs, setLogs] = useState<ActivityLog[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [stageFilter, setStageFilter] = useState<'All' | ActivityStage>('All')
  const [highlightId, setHighlightId] = useState<number | null>(null)
  const [form, setForm] = useState({
    action_type: 'CALL',
    action_taken: '',
    outcome: '',
    follow_up_action: '',
  })
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await getActivity({ limit: 100 })
      setLogs(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Deep link: /activity?focus=<id> scrolls to and highlights that entry.
  useEffect(() => {
    if (loading) return
    const focus = Number(searchParams.get('focus'))
    if (!focus) return
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
  }, [loading, logs.length])

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
      setShowForm(false)
      await load()
    } finally {
      setSaving(false)
    }
  }

  const handleNoteUpdated = (updated: ActivityLog) => {
    setLogs(prev => prev.map(l => l.id === updated.id ? { ...l, notes: updated.notes } : l))
  }

  // Optimistic stage move — no page reload.
  const handleStageChange = async (log: ActivityLog, stage: ActivityStage, nextTouchDate?: string | null) => {
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
  }

  const displayedLogs = stageFilter === 'All' ? logs : logs.filter(l => (l.stage ?? 'Sent') === stageFilter)

  const stageCount = (s: ActivityStage) => logs.filter(l => (l.stage ?? 'Sent') === s).length

  // Group by date
  const grouped: Record<string, ActivityLog[]> = {}
  for (const log of displayedLogs) {
    const key = log.log_date
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(log)
  }

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ClipboardList size={20} className="text-blue-400" />
          <h1 className="text-xl font-bold text-ink-primary">Activity Log</h1>
          <span className="text-ink-muted text-sm">({displayedLogs.length})</span>
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-blue text-white text-xs font-semibold
                     hover:bg-accent-blueDim transition-colors"
        >
          <Plus size={13} />
          Log Activity
        </button>
      </div>

      {/* Stage filter bar */}
      <div className="flex items-center gap-1.5 mb-5 flex-wrap">
        {(['All', ...STAGES] as const).map(opt => {
          const active = stageFilter === opt
          const count = opt === 'All' ? logs.length : stageCount(opt)
          return (
            <button
              key={opt}
              onClick={() => setStageFilter(opt)}
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
        <div className="bg-surface-card border border-surface-border rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm font-semibold text-ink-primary">New Activity Entry</div>
            <button onClick={() => setShowForm(false)} className="text-ink-muted hover:text-ink-primary">
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
              onClick={() => setShowForm(false)}
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
      )}

      {/* Log entries */}
      {loading ? (
        <div className="text-center py-12 text-ink-muted">Loading...</div>
      ) : displayedLogs.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <ClipboardList size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">{stageFilter === 'All' ? 'No activity logged yet.' : `No entries in "${stageFilter}".`}</p>
          {stageFilter === 'All' && (
            <p className="text-xs mt-1 text-ink-muted">Log calls, emails, and meetings to track your deal progress.</p>
          )}
        </div>
      ) : (
        <div className="space-y-6">
          {Object.entries(grouped)
            .sort(([a], [b]) => b.localeCompare(a))
            .map(([date, entries]) => (
              <div key={date}>
                <div className="text-[10px] font-bold uppercase tracking-widest text-ink-muted mb-3 flex items-center gap-3">
                  {formatDate(date)}
                  <div className="h-px flex-1 bg-surface-border" />
                  <span>{entries.length}</span>
                </div>
                <div className="space-y-2">
                  {entries.map(log => (
                    <div
                      key={log.id}
                      id={`activity-${log.id}`}
                      className={`flex items-start gap-3 bg-surface-card border rounded-xl p-3 transition-colors
                        ${highlightId === log.id ? 'border-accent-blue ring-2 ring-accent-blue/40' : 'border-surface-border'}`}
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
                        <p className="text-xs text-ink-secondary mt-0.5">{log.action_taken}</p>
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
                        <StageSelector log={log} onChange={(stage, nextDate) => handleStageChange(log, stage, nextDate)} />
                        <NoteSection log={log} onSaved={handleNoteUpdated} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          }
        </div>
      )}
    </div>
  )
}
