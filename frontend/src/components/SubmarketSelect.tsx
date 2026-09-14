// The submarket dropdown — read from the growing list, with Add new at the
// bottom. Jack represents tenants wherever they go (Sterling, Loudoun, Prince
// William), so the list grows rather than constrains: a typed name joins it
// permanently and is selected. A name already on the list in another casing
// ("sterling") comes back as the existing entry, never a duplicate.
import { useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'
import { createSubmarket, getSubmarkets } from '../api/client'

const ADD_NEW = '__add_new__'

export default function SubmarketSelect({
  value, onChange, className, emptyLabel = 'Select submarket...', allowAdd = true,
}: {
  value: string
  onChange: (name: string) => void
  className?: string
  emptyLabel?: string
  allowAdd?: boolean
}) {
  const [names, setNames] = useState<string[]>([])
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () =>
    getSubmarkets()
      .then(rows => setNames(rows.map(r => r.name)))
      .catch(() => setError('Could not load submarkets.'))

  useEffect(() => { void load() }, [])

  const add = async () => {
    const name = draft.trim()
    if (!name) return
    setSaving(true)
    setError(null)
    try {
      const row = await createSubmarket(name)
      await load()
      onChange(row.name)
      setAdding(false)
      setDraft('')
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not add this submarket.')
    } finally {
      setSaving(false)
    }
  }

  if (adding) {
    return (
      <div>
        <div className="flex items-center gap-1.5">
          <input
            autoFocus
            className={className}
            placeholder="e.g. Sterling"
            value={draft}
            maxLength={60}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { e.preventDefault(); void add() }
              if (e.key === 'Escape') { setAdding(false); setDraft('') }
            }}
          />
          <button
            type="button"
            onClick={add}
            disabled={saving || !draft.trim()}
            title="Add to the list"
            className="p-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            <Check size={12} />
          </button>
          <button
            type="button"
            onClick={() => { setAdding(false); setDraft('') }}
            title="Cancel"
            className="p-1.5 rounded-lg border border-surface-border text-ink-muted hover:text-ink-primary"
          >
            <X size={12} />
          </button>
        </div>
        {error && <p className="text-[11px] text-red-400 mt-1">{error}</p>}
      </div>
    )
  }

  // A value the company already carries always stays selectable, even before
  // the list has loaded.
  const options = value && !names.some(n => n.toLowerCase() === value.toLowerCase())
    ? [value, ...names]
    : names

  return (
    <div>
      <select
        className={className}
        value={value}
        onChange={e => {
          if (e.target.value === ADD_NEW) { setAdding(true); return }
          onChange(e.target.value)
        }}
      >
        <option value="">{emptyLabel}</option>
        {options.map(n => <option key={n} value={n}>{n}</option>)}
        {allowAdd && <option value={ADD_NEW}>+ Add new…</option>}
      </select>
      {error && <p className="text-[11px] text-red-400 mt-1">{error}</p>}
    </div>
  )
}
