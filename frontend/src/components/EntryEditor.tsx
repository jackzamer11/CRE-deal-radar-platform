import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { editActivity } from '../api/client'
import type {
  ActivityLog, Channel, Direction, EditableEntry,
} from '../types'
import { CHANNELS, LOGGABLE_ACTION_TYPES } from '../types'

/**
 * The one entry editor, shared by the flat All Activity feed and the contact
 * thread. Everything the automation writes has to be correctable by hand, so
 * this covers the prose, the direction and channel it guessed, the date it
 * stamped, and the six discovery fields.
 *
 * Two fields are deliberately absent. Which contact an entry belongs to moves
 * through "Move to another contact"; which company it is stamped to moves
 * through "Move this entry to a different company". Both are re-attachments
 * rather than edits — the stamp in particular is what keeps a departed
 * contact's history on the old company's page, so it never changes in passing.
 *
 * Saving a prose change re-mines the entry so the intelligence layer's
 * extracted facts match the corrected text; correcting only a channel or a date
 * saves without paying for that.
 */
export default function EntryEditor({
  log, onSaved, onCancel,
}: {
  log: EditableEntry
  onSaved: (updated: ActivityLog) => void
  onCancel: () => void
}) {
  const [form, setForm] = useState({
    action_type: log.action_type ?? 'CALL',
    action_taken: log.action_taken ?? '',
    outcome: log.outcome ?? '',
    notes: log.notes ?? '',
    follow_up_action: log.follow_up_action ?? '',
    direction: (log.direction ?? 'outbound') as Direction,
    channel: (log.channel ?? 'other') as Channel,
    log_date: log.log_date ?? '',
    disc_current_rent_psf: log.disc_current_rent_psf?.toString() ?? '',
    disc_current_sf: log.disc_current_sf?.toString() ?? '',
    disc_lease_expiry: log.disc_lease_expiry ?? '',
    disc_decision_timeline: log.disc_decision_timeline ?? '',
    disc_buildout_needs: log.disc_buildout_needs ?? '',
    disc_decision_maker: log.disc_decision_maker ?? '',
  })
  const [showDiscovery, setShowDiscovery] = useState(
    log.disc_current_rent_psf !== null || log.disc_current_sf !== null ||
    !!log.disc_lease_expiry || !!log.disc_decision_timeline ||
    !!log.disc_buildout_needs || !!log.disc_decision_maker,
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: keyof typeof form, v: string) => setForm(f => ({ ...f, [k]: v }))

  const save = async () => {
    if (!form.action_taken.trim()) {
      setError('Action taken cannot be empty.')
      return
    }
    setSaving(true)
    setError(null)

    // A blank box means "clear this field". None means "omitted" on the wire,
    // so an emptied field is named in clear_fields rather than sent as null.
    const payload: Record<string, unknown> = {
      action_type: form.action_type,
      action_taken: form.action_taken,
      direction: form.direction,
      channel: form.channel,
    }
    const clear_fields: string[] = []

    const text = (key: 'outcome' | 'notes' | 'follow_up_action') => {
      const value = form[key].trim()
      if (value) payload[key] = value
      else if (log[key]) clear_fields.push(key)
    }
    text('outcome'); text('notes'); text('follow_up_action')

    if (form.log_date) payload.log_date = form.log_date

    const num = (key: 'disc_current_rent_psf' | 'disc_current_sf') => {
      const raw = form[key].trim()
      if (raw && Number.isFinite(Number(raw))) payload[key] = Number(raw)
      else if (log[key] !== null) clear_fields.push(key)
    }
    num('disc_current_rent_psf'); num('disc_current_sf')

    const str = (key: 'disc_lease_expiry' | 'disc_decision_timeline'
                     | 'disc_buildout_needs' | 'disc_decision_maker') => {
      const value = form[key].trim()
      if (value) payload[key] = value
      else if (log[key]) clear_fields.push(key)
    }
    str('disc_lease_expiry'); str('disc_decision_timeline')
    str('disc_buildout_needs'); str('disc_decision_maker')

    if (clear_fields.length) payload.clear_fields = clear_fields

    try {
      onSaved(await editActivity(log.id, payload))
    } catch {
      setError('Could not save. Please try again.')
      setSaving(false)
    }
  }

  const input = "text-xs bg-surface-muted border border-surface-border rounded-lg px-3 py-1.5 text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"

  const area = (label: string, key: 'action_taken' | 'outcome' | 'notes' | 'follow_up_action', rows = 2) => (
    <div className="flex gap-3">
      <label className="text-[10px] text-ink-muted w-20 pt-2 flex-shrink-0">{label}</label>
      <textarea
        value={form[key]}
        onChange={e => set(key, e.target.value)}
        rows={rows}
        className={`${input} flex-1 py-2 resize-none`}
      />
    </div>
  )

  return (
    <div className="mt-2 space-y-2 border-t border-surface-border pt-3">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-[10px] text-ink-muted w-20 flex-shrink-0">Type</label>
        <select
          value={form.action_type}
          onChange={e => set('action_type', e.target.value)}
          className="bg-surface-muted border border-surface-border text-ink-secondary text-xs
                     rounded-lg px-3 py-1.5"
        >
          {LOGGABLE_ACTION_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <input
          type="date"
          title="Date this happened"
          value={form.log_date}
          onChange={e => set('log_date', e.target.value)}
          className={input}
        />
      </div>

      {/* Direction and channel — what the automation guessed, corrected. */}
      <div className="flex items-start gap-3 flex-wrap">
        <label className="text-[10px] text-ink-muted w-20 pt-1 flex-shrink-0">Direction</label>
        <div className="flex items-center gap-1.5 flex-wrap flex-1">
          {(['outbound', 'inbound'] as const).map(d => (
            <button
              key={d}
              onClick={() => set('direction', d)}
              className={`text-[10px] px-2.5 py-1 rounded-full border font-semibold transition-colors
                ${form.direction === d
                  ? (d === 'inbound' ? 'bg-violet-500/20 text-violet-300 border-violet-500/50'
                                     : 'bg-blue-500/20 text-blue-300 border-blue-500/50')
                  : 'bg-surface-muted text-ink-muted border-surface-border'}`}
            >
              {d === 'inbound' ? 'Inbound' : 'Outbound'}
            </button>
          ))}
          <div className="w-px h-4 bg-surface-border mx-1" />
          {CHANNELS.map(ch => (
            <button
              key={ch}
              onClick={() => set('channel', ch)}
              className={`text-[10px] px-2 py-1 rounded-full border font-semibold transition-colors
                ${form.channel === ch ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                                      : 'bg-surface-muted text-ink-muted border-surface-border'}`}
            >
              {ch}
            </button>
          ))}
        </div>
      </div>

      {area('Action', 'action_taken')}
      {area('Outcome', 'outcome')}
      {area('Notes', 'notes')}
      {area('Follow-up', 'follow_up_action', 1)}

      <button
        onClick={() => setShowDiscovery(v => !v)}
        className="ml-[92px] text-[10px] text-ink-muted hover:text-ink-secondary flex items-center gap-1"
      >
        {showDiscovery ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        Discovery
      </button>
      {showDiscovery && (
        <div className="ml-[92px] grid grid-cols-2 gap-2">
          <input placeholder="Current rent $/SF" value={form.disc_current_rent_psf}
                 onChange={e => set('disc_current_rent_psf', e.target.value)} className={input} />
          <input placeholder="Current SF" value={form.disc_current_sf}
                 onChange={e => set('disc_current_sf', e.target.value)} className={input} />
          <input type="date" title="Lease expiry" value={form.disc_lease_expiry}
                 onChange={e => set('disc_lease_expiry', e.target.value)} className={input} />
          <input placeholder="Decision timeline" value={form.disc_decision_timeline}
                 onChange={e => set('disc_decision_timeline', e.target.value)} className={input} />
          <input placeholder="Buildout needs" value={form.disc_buildout_needs}
                 onChange={e => set('disc_buildout_needs', e.target.value)} className={input} />
          <input placeholder="Decision maker" value={form.disc_decision_maker}
                 onChange={e => set('disc_decision_maker', e.target.value)} className={input} />
        </div>
      )}

      {error && <p className="text-[10px] text-red-400 ml-[92px]">{error}</p>}
      <div className="flex items-center gap-2 ml-[92px] flex-wrap">
        <button
          onClick={save}
          disabled={saving}
          className="text-[10px] px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700
                     text-white font-semibold disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
        <button onClick={onCancel} className="text-[10px] text-ink-muted hover:text-ink-primary">
          Cancel
        </button>
        <span className="text-[9px] text-ink-muted">
          Editing the wording updates the extracted facts for this entry.
        </span>
      </div>
    </div>
  )
}
