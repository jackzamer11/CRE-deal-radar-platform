import { useEffect, useState } from 'react'
import { Building2, User } from 'lucide-react'
import { searchCompanies, searchContacts } from '../api/client'
import type { CompanyPickerRow } from '../api/client'
import type { Contact } from '../types'
import { CONTACT_TYPE_LABELS } from '../types'

export type AttachPick =
  | { kind: 'company'; id: number; name: string }
  | { kind: 'contact'; id: number; name: string }

const FIELD = "text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"

// "Whose is this?" — companies and people in one type-ahead. A requirement
// can belong to a tenant company, or to a person of any type: a counterparty,
// or an investor with no company at all.
export default function AttachTargetPicker({
  placeholder, onPick,
}: {
  placeholder: string
  onPick: (pick: AttachPick | null) => void
}) {
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [companies, setCompanies] = useState<CompanyPickerRow[]>([])
  const [people, setPeople] = useState<Contact[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const term = query.trim()
    if (!term || term === picked) { setCompanies([]); setPeople([]); return }
    let cancelled = false
    const t = setTimeout(async () => {
      const [co, ppl] = await Promise.all([searchCompanies(term), searchContacts(term)])
      if (!cancelled) { setCompanies(co.slice(0, 6)); setPeople(ppl.slice(0, 6)); setOpen(true) }
    }, 180)
    return () => { cancelled = true; clearTimeout(t) }
  }, [query, picked])

  const choose = (pick: AttachPick) => {
    setPicked(pick.name)
    setQuery(pick.name)
    setOpen(false)
    onPick(pick)
  }

  const row = "w-full text-left px-2.5 py-1.5 text-[11px] text-ink-secondary hover:bg-surface-muted hover:text-ink-primary flex items-center gap-2"
  const group = "px-2.5 pt-1.5 pb-0.5 text-[9px] uppercase tracking-widest text-ink-muted"

  return (
    <div className="relative">
      <input
        value={query}
        onChange={e => { setQuery(e.target.value); setPicked(null); onPick(null) }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className={`${FIELD} w-full`}
      />
      {open && (companies.length > 0 || people.length > 0) && (
        <div className="absolute z-20 mt-1 w-full max-h-64 overflow-y-auto bg-surface-card
                        border border-surface-border rounded-lg shadow-lg">
          {companies.length > 0 && <div className={group}>Companies</div>}
          {companies.map(c => (
            <button key={`co-${c.id}`} className={row}
                    onClick={() => choose({ kind: 'company', id: c.id, name: c.name })}>
              <Building2 size={10} className="text-emerald-400 flex-shrink-0" />
              {c.name}
              {c.submarket && <span className="text-ink-muted">{c.submarket}</span>}
            </button>
          ))}
          {people.length > 0 && <div className={group}>People</div>}
          {people.map(p => (
            <button key={`ct-${p.id}`} className={row}
                    onClick={() => choose({ kind: 'contact', id: p.id, name: p.name })}>
              <User size={10} className="text-accent-blue flex-shrink-0" />
              {p.name}
              {p.company_name && <span className="text-emerald-400">{p.company_name}</span>}
              <span className="text-ink-muted uppercase text-[9px] tracking-wider">
                {CONTACT_TYPE_LABELS[p.contact_type] ?? p.contact_type}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
