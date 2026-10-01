import { useEffect, useState } from 'react'
import { Target } from 'lucide-react'
import { getNeeds } from '../api/client'
import type { StatedNeed } from '../api/client'
import { formatDate } from '../dates'

// What a tenant has told Jack they need — SF, budget, must-haves, timing, their
// lease date — shown in the Activity Log next to the notes it was read from.
// Each item once, as most recently said, with the date and a link to that
// note. Older statements are marked to re-confirm on the next call. Renders
// nothing when nothing has been said.
export default function WhatTheyNeed({
  contactId, companyKey, onJumpToEntry,
}: {
  contactId?: number
  companyKey?: string
  // Inside a thread, jump to the entry in place; elsewhere, open the Activity Log.
  onJumpToEntry?: (entryId: number) => void
}) {
  const [needs, setNeeds] = useState<StatedNeed[]>([])

  useEffect(() => {
    let cancelled = false
    getNeeds(contactId != null ? { contact_id: contactId } : { company_key: companyKey })
      .then(rows => { if (!cancelled) setNeeds(rows) })
      .catch(() => { if (!cancelled) setNeeds([]) })
    return () => { cancelled = true }
  }, [contactId, companyKey])

  if (needs.length === 0) return null

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-center gap-1.5 mb-2">
        <Target size={11} className="text-blue-400" />
        <span className="text-[10px] font-bold uppercase tracking-widest text-ink-muted">
          What they need
        </span>
      </div>
      <div className="space-y-1">
        {needs.map(n => {
          const row = (
            <>
              <span className="text-ink-muted w-24 flex-shrink-0">{n.label}</span>
              <span className="flex-1">{n.value}</span>
              {!n.fresh && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300
                                 border border-amber-500/30 flex-shrink-0">re-confirm</span>
              )}
              {n.stated_on && (
                <span className="text-[10px] text-ink-muted flex-shrink-0">
                  {formatDate(n.stated_on, { month: 'short', day: 'numeric', year: 'numeric' })}
                </span>
              )}
            </>
          )
          const cls = "w-full text-left text-xs text-ink-secondary hover:text-accent-blue flex gap-2 items-center"
          const title = n.snippet ? `“${n.snippet}” — open the note` : 'Open the note'
          if (n.entry_id != null && onJumpToEntry) {
            return (
              <button key={n.field} className={cls} title={title}
                      onClick={() => onJumpToEntry(n.entry_id!)}>{row}</button>
            )
          }
          if (n.entry_id != null) {
            return (
              <a key={n.field} className={cls} title={title}
                 href={`/activity?focus=${n.entry_id}`}>{row}</a>
            )
          }
          return <div key={n.field} className={cls}>{row}</div>
        })}
      </div>
    </div>
  )
}
