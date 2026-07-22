import { useEffect, useState } from 'react'
import { ClipboardCheck, Check, Pencil, FileText, X } from 'lucide-react'
import { getObservations, verifyObservation } from '../api/client'
import type { Observation } from '../types'

// Turn a raw field name (e.g. "base_rent_annual") into a readable label.
function fieldLabel(field: string): string {
  return field
    .split('_')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

// Confidence as a simple three-band visual, tolerant of null.
function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence === null || confidence === undefined) {
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full border font-semibold
                       bg-surface-muted text-ink-muted border-surface-border">
        no score
      </span>
    )
  }
  const pct = Math.round(confidence * 100)
  const band =
    confidence >= 0.75 ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
    : confidence >= 0.5 ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
    : 'bg-red-500/15 text-red-400 border-red-500/30'
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold ${band}`}>
      {pct}% confident
    </span>
  )
}

function ReviewRow({
  obs,
  onResolved,
}: {
  obs: Observation
  onResolved: (id: number) => void
}) {
  const [editing, setEditing] = useState(false)
  const [input, setInput] = useState(obs.value ?? '')
  const [busy, setBusy] = useState(false)

  const confirm = async () => {
    setBusy(true)
    try {
      await verifyObservation(obs.id)
      onResolved(obs.id)
    } catch {
      setBusy(false)
    }
  }

  const correct = async () => {
    setBusy(true)
    try {
      await verifyObservation(obs.id, input)
      onResolved(obs.id)
    } catch {
      setBusy(false)
    }
  }

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-wider text-ink-muted">
            {fieldLabel(obs.field)}
          </div>
          {obs.value === null ? (
            <div className="mt-0.5 text-sm font-bold text-red-400">NOT FOUND</div>
          ) : (
            <div className="mt-0.5 text-sm font-bold text-ink-primary break-words">{obs.value}</div>
          )}
        </div>
        <ConfidenceBadge confidence={obs.confidence} />
      </div>

      {/* Source provenance */}
      <div className="mt-2 flex items-center gap-2 text-[11px] text-ink-muted flex-wrap">
        <FileText size={12} className="flex-shrink-0" />
        <span className="text-accent-blue">{obs.source_doc ?? 'unknown source'}</span>
        {obs.source_page !== null && <span>· p.{obs.source_page}</span>}
        <span className="text-ink-muted">· {obs.entity_type} #{obs.entity_id}</span>
      </div>
      {obs.source_snippet && (
        <p className="mt-1.5 text-[11px] italic text-ink-secondary leading-snug border-l-2 border-surface-border pl-2">
          “{obs.source_snippet}”
        </p>
      )}

      {/* Actions */}
      {editing ? (
        <div className="mt-3 space-y-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="Corrected value…"
            className="w-full text-xs bg-surface-muted border border-surface-border rounded-lg px-3 py-2
                       text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={correct}
              disabled={busy}
              className="text-[10px] px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700
                         text-white font-semibold disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Save Correction'}
            </button>
            <button
              onClick={() => { setEditing(false); setInput(obs.value ?? '') }}
              className="text-[10px] text-ink-muted hover:text-ink-primary flex items-center gap-1"
            >
              <X size={11} /> Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={confirm}
            disabled={busy}
            className="flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-lg bg-accent-blue
                       hover:bg-accent-blueDim text-white font-semibold disabled:opacity-50"
          >
            <Check size={12} /> Confirm
          </button>
          <button
            onClick={() => setEditing(true)}
            disabled={busy}
            className="flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted
                       hover:bg-surface-hover text-ink-secondary hover:text-ink-primary font-semibold
                       border border-surface-border disabled:opacity-50"
          >
            <Pencil size={11} /> Correct
          </button>
        </div>
      )}
    </div>
  )
}

export default function ReviewPage() {
  const [rows, setRows] = useState<Observation[]>([])
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      // Unverified only, sorted by confidence ascending (server-side).
      const data = await getObservations({ human_verified: false })
      setRows(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Confirmed/corrected items drop out of the queue immediately.
  const handleResolved = (id: number) => {
    setRows(prev => prev.filter(r => r.id !== id))
  }

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-center gap-3 mb-6">
        <ClipboardCheck size={20} className="text-blue-400" />
        <h1 className="text-xl font-bold text-ink-primary">Review</h1>
        <span className="text-ink-muted text-sm">
          {rows.length} fact{rows.length === 1 ? '' : 's'} awaiting review
        </span>
      </div>

      {loading ? (
        <div className="text-center py-12 text-ink-muted">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <ClipboardCheck size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Nothing awaiting review.</p>
          <p className="text-xs mt-1 text-ink-muted">
            Extracted facts land here for you to confirm before they drive signals.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map(obs => (
            <ReviewRow key={obs.id} obs={obs} onResolved={handleResolved} />
          ))}
        </div>
      )}
    </div>
  )
}
