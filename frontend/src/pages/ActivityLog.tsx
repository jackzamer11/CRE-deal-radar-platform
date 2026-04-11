import { useEffect, useState } from 'react'
import { ClipboardList, Plus, X, Phone, Mail, Users, FileText, Search, RefreshCw } from 'lucide-react'
import { getActivity, createActivity } from '../api/client'
import type { ActivityLog, ActionType } from '../types'

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

export default function ActivityLogPage() {
  const [logs, setLogs] = useState<ActivityLog[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
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

  // Group by date
  const grouped: Record<string, ActivityLog[]> = {}
  for (const log of logs) {
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
          <span className="text-ink-muted text-sm">({logs.length})</span>
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
      ) : logs.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <ClipboardList size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">No activity logged yet.</p>
          <p className="text-xs mt-1 text-ink-muted">Log calls, emails, and meetings to track your deal progress.</p>
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
                    <div key={log.id} className="flex items-start gap-3 bg-surface-card border border-surface-border rounded-xl p-3">
                      <ActionBadge type={log.action_type as ActionType} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-ink-muted">
                            {log.action_type}
                          </span>
                          {log.property_address && (
                            <span className="text-[11px] text-accent-blue truncate">
                              {log.property_address}
                            </span>
                          )}
                          {log.company_name && (
                            <span className="text-[11px] text-emerald-400">{log.company_name}</span>
                          )}
                          {log.opportunity_ref && (
                            <span className="text-[10px] text-ink-muted">{log.opportunity_ref}</span>
                          )}
                        </div>
                        <p className="text-xs text-ink-secondary">{log.action_taken}</p>
                        {log.outcome && (
                          <p className="text-xs text-ink-muted mt-1">→ {log.outcome}</p>
                        )}
                        {log.follow_up_action && (
                          <p className="text-xs text-amber-400 mt-1">↻ {log.follow_up_action}</p>
                        )}
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
