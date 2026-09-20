import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Inbox } from 'lucide-react'
import { getNeedsContact } from '../api/client'
import HeldEntryCard from './HeldEntryCard'
import type { NeedsContactEntry } from '../types'

const PAGE = 100

/**
 * Every entry still waiting on a person, oldest first — the queue to drain.
 *
 * Entries with a company and entries without are in one list on purpose: both
 * need exactly one thing, and splitting them would make the count something
 * other than "how much is left". Zero is the goal, and the header says so.
 */
export default function NeedsContactQueue({
  onChanged,
}: {
  /** Fires whenever an entry moves, so the page badge can recount. */
  onChanged: () => void
}) {
  const [entries, setEntries] = useState<NeedsContactEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const page = await getNeedsContact({ limit: PAGE })
      setEntries(page.entries)
      setTotal(page.total)
    } catch {
      setError('Could not load the queue.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const loadMore = async () => {
    const next = await getNeedsContact({ limit: PAGE, offset: entries.length })
    setEntries([...entries, ...next.entries])
    setTotal(next.total)
  }

  const afterMove = async () => {
    onChanged()
    await load()
  }

  return (
    <div>
      <div className="bg-surface-card border border-surface-border rounded-xl p-4 mb-4">
        <div className="flex items-center gap-2">
          <Inbox size={15} className="text-amber-400 flex-shrink-0" />
          <h2 className="text-base font-bold text-ink-primary">Needs a contact</h2>
          {total > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full font-bold
                             bg-amber-500/15 text-amber-300 border border-amber-500/40">
              {total}
            </span>
          )}
        </div>
        <p className="text-[11px] text-ink-muted mt-1.5">
          Every entry not yet on a person, oldest first — with a company and
          without. Move them one at a time here, or a whole company at once from
          its card in the list. Nothing auto-assigns.
        </p>
      </div>

      {loading ? (
        <div className="text-center py-12 text-ink-muted text-sm">Loading…</div>
      ) : error ? (
        <div className="text-center py-12 text-ink-muted text-sm">{error}</div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <CheckCircle2 size={32} className="mx-auto mb-3 opacity-40 text-emerald-400" />
          <p className="text-sm">Nothing waiting. Every entry is on a contact.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map(e => (
            <HeldEntryCard
              key={e.id}
              entry={e}
              companyId={e.effective_company_id}
              companyName={e.effective_company_name}
              showCompany
              onMoved={afterMove}
            />
          ))}
          {entries.length < total && (
            <button
              onClick={loadMore}
              className="w-full text-[11px] px-3 py-1.5 rounded-lg bg-surface-card border
                         border-surface-border text-ink-muted hover:text-ink-primary"
            >
              Show more — {entries.length} of {total} shown
            </button>
          )}
        </div>
      )}
    </div>
  )
}
