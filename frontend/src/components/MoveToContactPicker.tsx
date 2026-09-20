import { useCallback, useEffect, useState } from 'react'
import { Plus, Search, UserRound, X } from 'lucide-react'
import {
  createContact, searchCompanies, searchContacts, type CompanyPickerRow,
} from '../api/client'
import type { Contact } from '../types'

/**
 * Put a held entry on a person.
 *
 * Two doors, and no third: pick somebody who already exists, or create the
 * person right here. There is deliberately no "assign automatically" and no
 * "unknown contact" placeholder — a wrong thread is worse than an empty one,
 * so the only way an entry lands on a contact is Jack choosing.
 *
 * When the entry has a company, the picker opens on that company's people and
 * searches within it: moving a Scott Management entry means one of the people
 * at Scott Management. When it has none, it searches everyone, and creating a
 * person there takes a company picked from the list or typed by hand.
 */
const FIELD =
  'text-[11px] bg-surface-muted border border-surface-border rounded-lg px-2 py-1.5 ' +
  'text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50'

// ── Company field, for an entry that has no company ──────────────────────────
// Picked wins over typed; a typed name is resolved against existing companies
// server-side before anything new is created.
function CompanyField({
  picked, onPick, typed, onType,
}: {
  picked: CompanyPickerRow | null
  onPick: (c: CompanyPickerRow | null) => void
  typed: string
  onType: (v: string) => void
}) {
  const [hits, setHits] = useState<CompanyPickerRow[]>([])

  useEffect(() => {
    const term = typed.trim()
    if (!term || picked) { setHits([]); return }
    let cancelled = false
    const t = setTimeout(async () => {
      try {
        const rows = await searchCompanies(term)
        if (!cancelled) setHits(rows)
      } catch { if (!cancelled) setHits([]) }
    }, 180)
    return () => { cancelled = true; clearTimeout(t) }
  }, [typed, picked])

  if (picked) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-emerald-400 font-semibold">{picked.name}</span>
        <button
          onClick={() => { onPick(null); onType('') }}
          className="text-ink-muted hover:text-ink-primary"
          title="Pick a different company"
        >
          <X size={11} />
        </button>
      </div>
    )
  }

  return (
    <div className="relative">
      <input
        value={typed}
        onChange={e => onType(e.target.value)}
        placeholder="Company — pick one or type a new name"
        className={`${FIELD} w-full`}
      />
      {hits.length > 0 && (
        <div className="absolute z-30 mt-1 w-full max-h-40 overflow-y-auto bg-surface-card
                        border border-surface-border rounded-lg shadow-lg">
          {hits.map(c => (
            <button
              key={c.id}
              onClick={() => onPick(c)}
              className="w-full text-left px-2.5 py-1.5 text-[11px] text-ink-secondary
                         hover:bg-surface-muted hover:text-ink-primary"
            >
              {c.name}
              {c.submarket && <span className="text-ink-muted ml-2">{c.submarket}</span>}
            </button>
          ))}
        </div>
      )}
      {typed.trim() && hits.length === 0 && (
        <p className="text-[10px] text-ink-muted mt-1">
          No company matches — “{typed.trim()}” will be created.
        </p>
      )}
    </div>
  )
}

export default function MoveToContactPicker({
  companyId, companyName, onMove, onCancel, moveLabel = 'Move',
}: {
  companyId: number | null
  companyName: string | null
  /** Resolved contact — the caller performs the actual move and refreshes. */
  onMove: (contact: Contact) => Promise<void>
  onCancel: () => void
  /** Verb for the confirmation line, e.g. "Move all 4 entries". */
  moveLabel?: string
}) {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<Contact[]>([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ name: '', email: '' })
  const [pickedCompany, setPickedCompany] = useState<CompanyPickerRow | null>(null)
  const [typedCompany, setTypedCompany] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // With a company, this loads its people on mount (empty term + company_id)
  // and narrows as Jack types. Without one, it searches everyone and shows
  // nothing until he types.
  const runSearch = useCallback(async (term: string) => {
    if (!term.trim() && companyId == null) { setHits([]); return }
    try {
      setHits(await searchContacts(term.trim(), companyId ?? undefined))
    } catch {
      setHits([])
    }
  }, [companyId])

  useEffect(() => {
    let cancelled = false
    const t = setTimeout(() => { if (!cancelled) void runSearch(query) },
                         query.trim() ? 180 : 0)
    return () => { cancelled = true; clearTimeout(t) }
  }, [query, runSearch])

  const move = async (contact: Contact) => {
    setBusy(true)
    setError(null)
    try {
      await onMove(contact)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not move this.')
      setBusy(false)
    }
  }

  const createAndMove = async () => {
    const name = form.name.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    try {
      const created = await createContact({
        name,
        email: form.email.trim() || null,
        // The entry's own company when it has one; otherwise what Jack picked
        // or typed. A picked company wins server-side too.
        company_id: companyId ?? pickedCompany?.id ?? null,
        company_name: companyId == null && !pickedCompany
          ? (typedCompany.trim() || null)
          : null,
      })
      await onMove(created)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not create this contact.')
      setBusy(false)
    }
  }

  return (
    <div className="mt-2 border-t border-surface-border pt-2.5">
      <p className="text-[10px] text-ink-muted mb-2">
        {companyName
          ? <>Put this on a person at <span className="text-emerald-400">{companyName}</span>.
              The entry keeps the company it is stamped to.</>
          : <>This entry has no company. Search every contact, or create the person
             and say where they work.</>}
      </p>

      {creating ? (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input
              autoFocus placeholder="Name *" value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              className={FIELD}
            />
            <input
              placeholder="Email (optional)" value={form.email}
              onChange={e => setForm({ ...form, email: e.target.value })}
              className={FIELD}
            />
          </div>
          {companyId == null && (
            <CompanyField
              picked={pickedCompany} onPick={setPickedCompany}
              typed={typedCompany} onType={setTypedCompany}
            />
          )}
          {error && <p className="text-[11px] text-red-400">{error}</p>}
          <div className="flex items-center gap-2">
            <button
              onClick={createAndMove}
              disabled={busy || !form.name.trim()}
              className="text-[10px] px-2.5 py-1 rounded bg-accent-blue hover:bg-accent-blueDim
                         text-white font-semibold disabled:opacity-50"
            >
              {busy ? 'Moving…' : `Create & ${moveLabel.toLowerCase()}`}
            </button>
            <button
              onClick={() => { setCreating(false); setError(null) }}
              className="text-[10px] text-ink-muted hover:text-ink-primary"
            >
              Back to search
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="relative">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted" />
            <input
              autoFocus value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={companyId != null
                ? 'Search this company’s contacts…'
                : 'Search all contacts…'}
              className={`${FIELD} w-full pl-7`}
            />
          </div>

          {error && <p className="text-[11px] text-red-400 mt-1.5">{error}</p>}

          <div className="mt-1.5 max-h-44 overflow-y-auto space-y-0.5">
            {hits.map(c => (
              <button
                key={c.id}
                onClick={() => move(c)}
                disabled={busy}
                className="w-full text-left px-2.5 py-1.5 rounded text-[11px] text-ink-secondary
                           hover:bg-surface-muted hover:text-ink-primary disabled:opacity-50
                           flex items-center gap-1.5"
              >
                <UserRound size={10} className="text-ink-muted shrink-0" />
                <span className="font-semibold">{c.name}</span>
                {c.company_name && <span className="text-emerald-400">{c.company_name}</span>}
                {c.email && <span className="text-ink-muted truncate">{c.email}</span>}
              </button>
            ))}
            {hits.length === 0 && (
              <p className="text-[10px] text-ink-muted px-1 py-1.5">
                {companyId != null
                  ? 'No contacts here yet — create the person below.'
                  : query.trim() ? 'No match.' : 'Type a name or email.'}
              </p>
            )}
          </div>

          <div className="flex items-center gap-3 mt-2">
            <button
              onClick={() => { setCreating(true); setForm({ name: query.trim(), email: '' }) }}
              className="text-[10px] text-accent-blue hover:underline flex items-center gap-1"
            >
              <Plus size={10} /> New contact
              {companyName ? ` at ${companyName}` : ''}
            </button>
            <button onClick={onCancel} className="text-[10px] text-ink-muted hover:text-ink-primary">
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  )
}
