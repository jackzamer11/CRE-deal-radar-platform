import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { searchCompanies } from '../api/client'

const FIELD = "text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"

// ── Company type-ahead ───────────────────────────────────────────────────────
// Shared by "which company does this person work for", "move this entry to a
// different company" and Review's "which tenant is this requirement for".
// Queries the four-column picker endpoint, not the unpaginated company list.
export default function CompanyPicker({
  value, placeholder, onPick,
}: {
  value: string
  placeholder: string
  onPick: (company: { id: number; name: string } | null) => void
}) {
  const [query, setQuery] = useState(value)
  const [hits, setHits] = useState<{ id: number; name: string; submarket: string | null }[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => { setQuery(value) }, [value])

  useEffect(() => {
    const term = query.trim()
    if (!term || term === value) { setHits([]); return }
    let cancelled = false
    const t = setTimeout(async () => {
      const rows = await searchCompanies(term)
      if (!cancelled) { setHits(rows); setOpen(true) }
    }, 180)
    return () => { cancelled = true; clearTimeout(t) }
  }, [query, value])

  return (
    <div className="relative">
      <input
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className={`${FIELD} w-full`}
      />
      {query && (
        <button
          onClick={() => { setQuery(''); setHits([]); onPick(null) }}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-muted hover:text-red-400"
          title="Clear"
        >
          <X size={11} />
        </button>
      )}
      {open && hits.length > 0 && (
        <div className="absolute z-20 mt-1 w-full max-h-48 overflow-y-auto bg-surface-card
                        border border-surface-border rounded-lg shadow-lg">
          {hits.map(h => (
            <button
              key={h.id}
              onClick={() => { onPick({ id: h.id, name: h.name }); setQuery(h.name); setOpen(false) }}
              className="w-full text-left px-2.5 py-1.5 text-[11px] text-ink-secondary
                         hover:bg-surface-muted hover:text-ink-primary"
            >
              {h.name}
              {h.submarket && <span className="text-ink-muted ml-2">{h.submarket}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
