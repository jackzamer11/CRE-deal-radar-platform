import { useState } from 'react'
import { X, Clock } from 'lucide-react'
import { snoozeCompany } from '../api/client'
import type { CompanyOut } from '../types'

interface Props {
  companyId: string
  companyName: string
  onClose: () => void
  /** Called with the updated CompanyOut after a successful snooze. */
  onSnoozed: (company: CompanyOut) => void
}

const PRESETS = [
  { label: '30 days',  days: 30  },
  { label: '90 days',  days: 90  },
  { label: '6 months', days: 180 },
  { label: '1 year',   days: 365 },
]

function addDays(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}

export default function CompanySnoozeModal({ companyId, companyName, onClose, onSnoozed }: Props) {
  const [preset, setPreset]       = useState<number>(90)      // default 90 days
  const [customDate, setCustomDate] = useState('')
  const [reason, setReason]       = useState('')
  const [saving, setSaving]       = useState(false)
  const [error, setError]         = useState<string | null>(null)

  // "tomorrow" as ISO string — minimum allowed date
  const tomorrow = addDays(1)

  // Effective date: custom overrides preset
  const effectiveDate = customDate || addDays(preset)

  const dateError = customDate && customDate < tomorrow
    ? 'Snooze date must be at least tomorrow'
    : null

  const canConfirm = !saving && !dateError

  const handleConfirm = async () => {
    if (!canConfirm) return
    setSaving(true)
    setError(null)
    try {
      const result = await snoozeCompany(companyId, {
        snoozed_until: effectiveDate,
        snooze_reason: reason.trim() || undefined,
      })
      onSnoozed(result)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || 'Failed to snooze company')
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-surface-card border border-surface-border rounded-2xl w-full max-w-md shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-amber-500/15 flex items-center justify-center">
              <Clock size={15} className="text-amber-400" />
            </div>
            <div>
              <div className="font-semibold text-ink-primary">Snooze Company</div>
              <div className="text-[11px] text-ink-muted truncate max-w-[260px]">{companyName}</div>
            </div>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1 rounded-lg hover:bg-surface-muted">
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Preset buttons */}
          <div>
            <div className="text-xs text-ink-muted mb-2">Remove from queue until</div>
            <div className="grid grid-cols-4 gap-2">
              {PRESETS.map(p => (
                <button
                  key={p.days}
                  onClick={() => { setPreset(p.days); setCustomDate('') }}
                  className={`px-2 py-2 rounded-lg text-xs font-semibold border transition-colors ${
                    !customDate && preset === p.days
                      ? 'bg-amber-500/20 text-amber-400 border-amber-500/40'
                      : 'bg-surface-muted text-ink-secondary border-surface-border hover:border-amber-500/30 hover:text-ink-primary'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* Custom date */}
          <div>
            <div className="text-xs text-ink-muted mb-2">Or pick a specific date</div>
            <input
              type="date"
              value={customDate}
              min={tomorrow}
              onChange={e => setCustomDate(e.target.value)}
              className="w-full bg-surface border border-surface-border text-ink-primary text-sm rounded-lg
                         px-3 py-2 outline-none focus:border-accent-blue transition-colors"
            />
            {dateError && (
              <div className="mt-1.5 text-[11px] text-red-400">{dateError}</div>
            )}
          </div>

          {/* Reason */}
          <div>
            <div className="text-xs text-ink-muted mb-2">Reason <span className="text-ink-muted font-normal">(optional)</span></div>
            <input
              type="text"
              placeholder='e.g. "Just signed renewal — revisit next cycle"'
              value={reason}
              onChange={e => setReason(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleConfirm()}
              className="w-full bg-surface border border-surface-border text-ink-primary text-sm rounded-lg
                         px-3 py-2 outline-none focus:border-accent-blue transition-colors placeholder:text-ink-muted"
            />
          </div>

          {error && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-xs">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-surface-border bg-surface-muted rounded-b-2xl">
          <button onClick={onClose} className="px-4 py-2 text-sm text-ink-muted hover:text-ink-primary">
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={!canConfirm}
            className="px-5 py-2 rounded-lg bg-amber-500 hover:bg-amber-600 text-white text-sm font-semibold
                       transition-colors disabled:opacity-40"
          >
            {saving ? 'Snoozing…' : `Snooze until ${effectiveDate}`}
          </button>
        </div>
      </div>
    </div>
  )
}
