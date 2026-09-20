import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Building2, Users } from 'lucide-react'
import { getNeedsContact, moveAllToContact } from '../api/client'
import MoveToContactPicker from './MoveToContactPicker'
import HeldEntryCard from './HeldEntryCard'
import type { NeedsContactEntry } from '../types'

/**
 * A company's held entries, opened from its card in the By Contact list.
 *
 * The same timeline shape a contact thread has, for the entries that do not
 * have a person yet. Two ways out of here, and both are Jack's: one entry at a
 * time, or the whole card at once — which is the common case, because a
 * company holding unassigned entries usually has exactly one person behind all
 * of them.
 */
export default function CompanyHeldEntries({
  companyId, companyName, onBack, onChanged,
}: {
  companyId: number
  companyName: string
  onBack: () => void
  /** Fires whenever entries move, so the list behind can refresh its counts. */
  onChanged: () => void
}) {
  const [entries, setEntries] = useState<NeedsContactEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [movingAll, setMovingAll] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await getNeedsContact({ company_id: companyId, limit: 500 })
      setEntries(page.entries)
    } catch {
      setError('Could not load what this company is holding.')
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => { void load() }, [load])

  // An entry moved: reload this panel, and tell the list behind it to recount.
  const afterMove = async () => {
    onChanged()
    await load()
  }

  return (
    <div>
      <button
        onClick={onBack}
        className="flex items-center gap-1.5 text-[11px] text-ink-muted hover:text-ink-primary mb-3"
      >
        <ArrowLeft size={12} /> Back to the list
      </button>

      <div className="bg-surface-card border border-surface-border rounded-xl p-4 mb-4">
        <div className="flex items-center gap-2 flex-wrap">
          <Building2 size={15} className="text-emerald-400 flex-shrink-0" />
          <h2 className="text-base font-bold text-ink-primary">{companyName}</h2>
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400/90
                           border border-amber-500/20">
            {entries.length} waiting on a contact
          </span>
        </div>
        <p className="text-[11px] text-ink-muted mt-1.5">
          These entries are stamped to {companyName} but are not on a person.
          They stay here until you move them — nothing is assigned automatically,
          and no placeholder contact is created.
        </p>

        {entries.length > 0 && (
          movingAll ? (
            <MoveToContactPicker
              companyId={companyId}
              companyName={companyName}
              moveLabel={`Move all ${entries.length}`}
              onCancel={() => setMovingAll(false)}
              onMove={async contact => {
                await moveAllToContact(companyId, contact.id)
                setMovingAll(false)
                await afterMove()
              }}
            />
          ) : (
            <button
              onClick={() => setMovingAll(true)}
              className="mt-3 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-blue
                         text-white text-[11px] font-semibold hover:bg-accent-blueDim"
            >
              <Users size={12} /> Move all {entries.length} to contact
            </button>
          )
        )}
      </div>

      {loading ? (
        <div className="text-center py-12 text-ink-muted text-sm">Loading…</div>
      ) : error ? (
        <div className="text-center py-12 text-ink-muted text-sm">{error}</div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <Users size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Nothing left waiting here.</p>
          <p className="text-xs mt-1">Every entry for {companyName} is on a person.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map(e => (
            <HeldEntryCard
              key={e.id}
              entry={e}
              companyId={companyId}
              companyName={companyName}
              onMoved={afterMove}
            />
          ))}
        </div>
      )}
    </div>
  )
}
