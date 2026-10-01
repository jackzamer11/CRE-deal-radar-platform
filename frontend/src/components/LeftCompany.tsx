import { useState } from 'react'
import { LogOut } from 'lucide-react'
import { contactLeftCompany } from '../api/client'
import CompanyPicker from './CompanyPicker'

// "They left <company> on <date>." Different from changing "Works at" above:
// that changes current employment only. This also moves every entry logged
// after the date off the company they left, so the old company keeps exactly
// the years they worked there — and their old company's lease stays out of
// anything written to them from then on.
export default function LeftCompany({
  contactId, companyName, onDone,
}: {
  contactId: number
  companyName: string
  onDone: () => void
}) {
  const [open, setOpen] = useState(false)
  const [leftOn, setLeftOn] = useState('')
  const [newCompany, setNewCompany] = useState<{ id: number; name: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (!leftOn) return
    setBusy(true)
    setError(null)
    try {
      const res = await contactLeftCompany(contactId, leftOn, newCompany?.id ?? null)
      setResult(
        `${res.entries_moved} later entr${res.entries_moved === 1 ? 'y' : 'ies'} moved off ${companyName}.`,
      )
      onDone()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not save that.')
    } finally {
      setBusy(false)
    }
  }

  if (result) return <p className="mt-2 text-[11px] text-emerald-400">{result}</p>

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-1.5 text-[10px] text-ink-muted hover:text-accent-blue flex items-center gap-1"
      >
        <LogOut size={10} /> They left {companyName}…
      </button>
    )
  }

  const field = "text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary focus:outline-none focus:border-accent-blue/50"

  return (
    <div className="mt-2 border border-surface-border rounded-lg p-2.5 space-y-2">
      <p className="text-[10px] text-ink-muted leading-snug">
        Entries logged on or after this date move off {companyName}. Earlier ones stay with it,
        and their thread here stays whole.
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-[10px] text-ink-muted">Left on</label>
        <input type="date" value={leftOn} onChange={e => setLeftOn(e.target.value)} className={field} />
      </div>
      <div>
        <label className="text-[10px] text-ink-muted">Now works at (optional)</label>
        <CompanyPicker value="" placeholder="Search companies…" onPick={setNewCompany} />
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={save}
          disabled={busy || !leftOn}
          className="text-[10px] px-3 py-1.5 rounded-lg bg-accent-blue hover:bg-accent-blueDim
                     text-white font-semibold disabled:opacity-40"
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button onClick={() => setOpen(false)} className="text-[10px] text-ink-muted hover:text-ink-primary">
          Cancel
        </button>
        {error && <span className="text-[10px] text-red-400">{error}</span>}
      </div>
    </div>
  )
}
