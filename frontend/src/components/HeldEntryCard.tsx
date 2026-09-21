import { useState } from 'react'
import {
  Archive, ArchiveRestore, ArrowDownLeft, ArrowUpRight, Building2, Mail, Phone,
  UserRound, Users,
} from 'lucide-react'
import { assignActivity, setActivityArchived } from '../api/client'
import MoveToContactPicker from './MoveToContactPicker'
import type { ActivityLog, Channel } from '../types'
import { formatDate } from '../dates'

const CHANNEL_ICONS: Partial<Record<Channel, React.ElementType>> = {
  email: Mail, call: Phone, meeting: Users,
}

const fmtDate = (d: string) =>
  formatDate(d, { month: 'short', day: 'numeric', year: 'numeric' })

/**
 * One entry that is not on a person yet, with the one action it needs.
 *
 * Rendered identically on a company's held-entries timeline and in the
 * needs-a-contact queue, because it is the same entry needing the same thing.
 * The queue is the only surface that shows the company on the row — on a
 * company's own timeline that would be every row repeating the header.
 */
export default function HeldEntryCard({
  entry, companyId, companyName, showCompany = false, onMoved,
}: {
  entry: ActivityLog
  /** The company to scope the picker to — null for an entry that has none. */
  companyId: number | null
  companyName: string | null
  showCompany?: boolean
  onMoved: () => void
}) {
  const [picking, setPicking] = useState(false)
  const [busy, setBusy] = useState(false)
  const inbound = entry.direction === 'inbound'
  const Icon = CHANNEL_ICONS[entry.channel ?? 'other']
  const archived = !!entry.archived

  const toggleArchived = async () => {
    setBusy(true)
    try {
      await setActivityArchived(entry.id, !archived)
      onMoved()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className={`border rounded-xl p-3 border-surface-border
        ${archived
          /* Set back and muted: still here, still readable, visibly not in the
             queue any more. */
          ? 'bg-surface-card/40 border-dashed opacity-70'
          : inbound ? 'bg-violet-500/5 border-l-2 border-l-violet-500/60'
                    : 'bg-surface-card border-l-2 border-l-blue-500/40'}`}
    >
      <div className="flex items-center gap-2 flex-wrap mb-1">
        <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase
                          flex items-center gap-1
          ${inbound ? 'bg-violet-500/20 text-violet-300' : 'bg-blue-500/15 text-blue-300'}`}>
          {inbound ? <ArrowDownLeft size={9} /> : <ArrowUpRight size={9} />}
          {inbound ? 'In' : 'Out'}
        </span>
        <span className="text-[10px] text-ink-muted flex items-center gap-1 uppercase font-bold">
          {Icon && <Icon size={10} />}{entry.channel ?? 'other'}
        </span>
        <span className="text-[10px] text-ink-muted">{fmtDate(entry.log_date)}</span>
        {showCompany && (
          companyName ? (
            <span className="text-[10px] text-emerald-400 flex items-center gap-1">
              <Building2 size={9} />{companyName}
            </span>
          ) : (
            <span className="text-[10px] text-ink-muted italic">no company</span>
          )
        )}
        <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400/90
                         border border-amber-500/20">
          no contact
        </span>
        {archived && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-muted text-ink-muted
                           border border-surface-border flex items-center gap-1"
                title="Out of the queue and the badge. Still searchable, still on this company's card.">
            <Archive size={9} /> archived
          </span>
        )}
      </div>

      <p className="text-xs text-ink-secondary">{entry.action_taken}</p>
      {entry.source_note && (
        <p className="text-[10px] text-ink-muted mt-0.5 italic">{entry.source_note}</p>
      )}
      {entry.outcome && <p className="text-xs text-ink-muted mt-1">→ {entry.outcome}</p>}
      {entry.notes && <p className="text-[11px] text-ink-muted mt-1 italic">{entry.notes}</p>}

      {picking ? (
        <MoveToContactPicker
          companyId={companyId}
          companyName={companyName}
          moveLabel="Move"
          onCancel={() => setPicking(false)}
          onMove={async contact => {
            await assignActivity(entry.id, { contact_id: contact.id })
            onMoved()
          }}
        />
      ) : (
        <div className="mt-2 flex items-center gap-3 flex-wrap">
          <button
            onClick={() => setPicking(true)}
            className="text-[10px] text-accent-blue hover:underline flex items-center gap-1"
          >
            <UserRound size={10} /> Move to contact
          </button>
          <button
            onClick={toggleArchived}
            disabled={busy}
            className="text-[10px] text-ink-muted hover:text-ink-primary flex items-center gap-1
                       disabled:opacity-50"
            title={archived
              ? 'Put this back in the Needs a Contact queue.'
              : 'Take this out of the queue and the badge. It stays in the database, searchable, and on this company’s card.'}
          >
            {archived
              ? <><ArchiveRestore size={10} /> Unarchive</>
              : <><Archive size={10} /> Archive</>}
          </button>
        </div>
      )}
    </div>
  )
}
