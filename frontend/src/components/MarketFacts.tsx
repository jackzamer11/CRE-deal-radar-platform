import { useEffect, useState } from 'react'
import { getAttachedRequirements, getMarketFacts } from '../api/client'
import type { MarketFact } from '../api/client'
import { formatDate } from '../dates'

// What this person has told Jack that is not a tenant card:
//
//   * Requirements Jack attached to them from Review's holding list — a
//     counterparty's or an investor's own stated needs.
//   * Market info — a building, what is available, asking rents, concessions.
//     Mostly brokers and landlords.
//
// Reference only, and says so: a broker's asking rent is an unverified figure,
// so nothing here scores, makes an Intel card, or reaches outreach copy. It is
// kept so what Jack was told is never lost. Renders nothing when there is none.
export default function MarketFacts({
  contactId, onJumpToEntry,
}: {
  contactId: number
  onJumpToEntry: (entryId: number) => void
}) {
  const [needs, setNeeds] = useState<MarketFact[]>([])
  const [market, setMarket] = useState<MarketFact[]>([])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      getAttachedRequirements(contactId).catch(() => []),
      getMarketFacts(contactId).catch(() => []),
    ]).then(([n, m]) => {
      if (!cancelled) { setNeeds(n); setMarket(m) }
    })
    return () => { cancelled = true }
  }, [contactId])

  if (needs.length === 0 && market.length === 0) return null

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <span className="text-[10px] font-bold uppercase tracking-widest text-ink-muted">
        What they've told you
      </span>
      {needs.length > 0 && (
        <Section title="What they're looking for" rows={needs} onJumpToEntry={onJumpToEntry} />
      )}
      {market.length > 0 && (
        <Section title="Market info — reference only, unverified" rows={market}
                 onJumpToEntry={onJumpToEntry} />
      )}
    </div>
  )
}

function Section({
  title, rows, onJumpToEntry,
}: {
  title: string
  rows: MarketFact[]
  onJumpToEntry: (entryId: number) => void
}) {
  return (
    <div className="mt-2">
      <div className="text-[10px] text-ink-muted mb-1">{title}</div>
      <div className="space-y-1">
        {rows.map(f => (
          <button
            key={f.id}
            onClick={() => onJumpToEntry(f.entry_id)}
            title={f.snippet ? `“${f.snippet}” — jump to the entry` : 'Jump to the entry'}
            className="w-full text-left text-xs text-ink-secondary hover:text-accent-blue flex gap-2"
          >
            <span className="text-ink-muted w-24 flex-shrink-0">{f.label}</span>
            <span className="flex-1">{f.value}</span>
            {f.log_date && (
              <span className="text-[10px] text-ink-muted flex-shrink-0">
                {formatDate(f.log_date, { month: 'short', day: 'numeric' })}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
