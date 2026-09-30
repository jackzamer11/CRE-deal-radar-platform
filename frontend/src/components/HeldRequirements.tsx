import { useEffect, useState } from 'react'
import { ArrowRight, Inbox } from 'lucide-react'
import { assignRequirement, dismissRequirement, getUnassignedRequirements } from '../api/client'
import type { HeldRequirement } from '../api/client'
import { formatDate } from '../dates'
import AttachTargetPicker from './AttachTargetPicker'
import type { AttachPick } from './AttachTargetPicker'

// Requirements a note stated for a client it did not name — Jack asking a
// landlord's broker about "a tenant seeking 500-600 sqft". They are kept, never
// filed under the brokerage, and wait here for one answer: whose are they?
// A tenant company (or a tenant contact at one) puts them on that Intel card;
// any other person — a counterparty, an investor — keeps them on their page.
// "Not a requirement" keeps them out for good. Renders nothing when empty.
export default function HeldRequirements({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<HeldRequirement[]>([])

  const load = () => {
    getUnassignedRequirements().then(setRows).catch(() => setRows([]))
  }

  useEffect(load, [refreshKey])

  if (rows.length === 0) return null

  const drop = (entryId: number) => setRows(prev => prev.filter(r => r.entry_id !== entryId))

  return (
    <div className="mb-5 bg-surface-card border border-amber-500/30 rounded-xl p-4">
      <div className="text-sm font-semibold text-ink-primary flex items-center gap-2">
        <Inbox size={14} className="text-amber-400" />
        Requirements waiting for an owner
        <span className="text-ink-muted font-normal">{rows.length}</span>
      </div>
      <p className="text-[11px] text-ink-muted mt-0.5 leading-relaxed">
        A note stated someone's requirement without saying whose — usually you asking a broker
        about space for a client. Attach it to a tenant and it joins their Intel card; attach it
        to anyone else and it's kept on their page.
      </p>
      <div className="mt-3 space-y-3">
        {rows.map(row => (
          <HeldRow key={row.entry_id} row={row} onDone={() => drop(row.entry_id)} />
        ))}
      </div>
    </div>
  )
}

function whereFrom(row: HeldRequirement): string {
  const who = row.contact_name
    ? `${row.contact_name}${row.contact_company ? ` (${row.contact_company})` : ''}`
    : 'an entry with no contact'
  if (row.direction === 'outbound') return `Your email to ${who}`
  if (row.direction === 'inbound') return `Email from ${who}`
  return `Entry with ${who}`
}

function HeldRow({ row, onDone }: { row: HeldRequirement; onDone: () => void }) {
  const [target, setTarget] = useState<AttachPick | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onDone()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not save that.')
      setBusy(false)
    }
  }

  return (
    <div className="border border-surface-border rounded-lg p-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[11px] text-ink-secondary">
          {whereFrom(row)}
          {row.log_date && (
            <span className="text-ink-muted"> · {formatDate(row.log_date, { month: 'short', day: 'numeric' })}</span>
          )}
        </span>
        <a
          href={`/activity?focus=${row.entry_id}`}
          className="text-[10px] text-accent-blue hover:underline flex items-center gap-1"
        >
          View entry <ArrowRight size={10} />
        </a>
      </div>
      {row.summary && (
        <p className="text-[11px] text-ink-muted mt-1 line-clamp-2">{row.summary}</p>
      )}
      {row.said_name && (
        <p className="text-[11px] text-amber-300/90 mt-1">
          The note says “{row.said_name}” — no single company on file matches.
        </p>
      )}
      <div className="flex flex-wrap gap-1.5 mt-2">
        {row.facts.map(f => (
          <span
            key={f.id}
            title={f.snippet ? `“${f.snippet}”` : undefined}
            className="text-[10px] px-2 py-0.5 rounded-full bg-surface-muted border border-surface-border text-ink-secondary"
          >
            <span className="text-ink-muted">{f.label}</span> {f.value}
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2 mt-2.5 flex-wrap">
        <div className="flex-1 min-w-[180px]">
          <AttachTargetPicker placeholder="Whose is this? A company or a person…" onPick={setTarget} />
        </div>
        <button
          disabled={busy || !target}
          onClick={() => target && act(() => assignRequirement(
            row.entry_id,
            target.kind === 'company' ? { company_id: target.id } : { contact_id: target.id },
          ))}
          className="text-[10px] px-3 py-1.5 rounded-lg bg-accent-blue hover:bg-accent-blueDim
                     text-white font-semibold disabled:opacity-40"
        >
          Attach
        </button>
        <button
          disabled={busy}
          onClick={() => act(() => dismissRequirement(row.entry_id))}
          className="text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted hover:bg-surface-hover
                     text-ink-muted hover:text-ink-primary font-semibold border border-surface-border
                     disabled:opacity-40"
        >
          Not a requirement
        </button>
      </div>
      {error && <p className="text-[10px] text-red-400 mt-1">{error}</p>}
    </div>
  )
}
