import { useState } from 'react'
import { X, Pencil } from 'lucide-react'
import { updateCompanySfOccupied } from '../api/client'
import type { CompanyOut } from '../types'

interface Props {
  company: CompanyOut
  onClose: () => void
  /** Called with the updated CompanyOut after a successful save. */
  onSaved: (company: CompanyOut) => void
}

/**
 * Edit a company's real occupied square footage.
 *
 * current_sf_occupied is the ONLY SF field — the actual SF from CoStar's
 * "SF Occupied", entered manually here. It is never projected or derived from
 * headcount. Leaving the field blank clears it (SF: Unknown).
 */
export default function EditCompanyModal({ company, onClose, onSaved }: Props) {
  const [sfOccupied, setSfOccupied] = useState<string>(
    company.current_sf_occupied != null ? String(company.current_sf_occupied) : '',
  )
  const [saving, setSaving] = useState(false)
  const [error, setError]   = useState<string | null>(null)

  const trimmed = sfOccupied.trim()
  const parsed  = trimmed === '' ? null : Number(trimmed)
  const invalid = trimmed !== '' && (!Number.isInteger(parsed) || (parsed as number) < 0)

  const handleSave = async () => {
    if (saving || invalid) return
    setSaving(true)
    setError(null)
    try {
      const updated = await updateCompanySfOccupied(company.company_id, parsed)
      onSaved(updated)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || 'Failed to save company')
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-surface-card border border-surface-border rounded-2xl w-full max-w-md shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-accent-blue/15 flex items-center justify-center">
              <Pencil size={15} className="text-accent-blue" />
            </div>
            <div>
              <div className="font-semibold text-ink-primary">Edit Company</div>
              <div className="text-[11px] text-ink-muted truncate max-w-[260px]">{company.name}</div>
            </div>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1 rounded-lg hover:bg-surface-muted">
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-5 space-y-4">
          <div>
            <label className="text-xs text-ink-muted mb-2 block">SF Occupied (CoStar)</label>
            <input
              type="number"
              min="0"
              placeholder="e.g. 5500 — leave blank if unknown"
              value={sfOccupied}
              onChange={e => setSfOccupied(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSave()}
              autoFocus
              className="w-full bg-surface border border-surface-border text-ink-primary text-sm rounded-lg
                         px-3 py-2 outline-none focus:border-accent-blue transition-colors placeholder:text-ink-muted"
            />
            <div className="mt-1.5 text-[11px] text-ink-muted">
              Real occupied SF — never estimated. Blank = SF Unknown.
            </div>
            {invalid && (
              <div className="mt-1.5 text-[11px] text-red-400">Enter a whole number ≥ 0, or leave blank.</div>
            )}
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
            onClick={handleSave}
            disabled={saving || invalid}
            className="px-5 py-2 rounded-lg bg-accent-blue hover:opacity-90 text-white text-sm font-semibold
                       transition-colors disabled:opacity-40"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
