import { useEffect, useRef, useState } from 'react'
import { Check, Pencil, X } from 'lucide-react'
import { setContactStatus } from '../api/client'
import { formatDate } from '../dates'

/**
 * One line about where a person actually stands, in Jack's words.
 *
 * The card otherwise shows the latest entry's summary — what HAPPENED. That is
 * often not where things STAND ("emailed the flyer" vs "waiting on their board
 * until the 15th"), and after a long thread the newest entry is frequently the
 * least informative line available. So a status, when set, takes that slot;
 * when empty the card falls back and reads exactly as it did before.
 *
 * Saving is not an interaction: no activity entry, no stage change, no effect
 * on last touch. The endpoint behind it cannot reach a stage writer.
 *
 * Used on the list card and in the thread panel, so the same line is edited the
 * same way in both places — one component, not two that drift.
 */
export default function StatusLine({
  contactId, status, updatedAt, fallback, onSaved, compact = false,
}: {
  contactId: number
  status: string | null
  updatedAt: string | null
  /** The latest entry summary — shown only when there is no status. */
  fallback?: string | null
  onSaved: () => void
  /** Card density: smaller text, edit affordance only on hover. */
  compact?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(status ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => { setDraft(status ?? '') }, [status])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await setContactStatus(contactId, draft.trim() || null)
      setEditing(false)
      onSaved()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not save this status.')
    } finally {
      setSaving(false)
    }
  }

  const cancel = () => { setDraft(status ?? ''); setEditing(false); setError(null) }

  // The card's root is a click target that opens the thread. Every interactive
  // element in here has to stop the click reaching it, or typing a status would
  // navigate away mid-word.
  const stop = (e: React.SyntheticEvent) => e.stopPropagation()

  const size = compact ? 'text-[11px]' : 'text-xs'

  if (editing) {
    return (
      <div className={`mt-1 ${size}`} onClick={stop}>
        <div className="flex items-center gap-1.5">
          <input
            ref={inputRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onClick={stop}
            onKeyDown={e => {
              stop(e)
              if (e.key === 'Enter') { e.preventDefault(); void save() }
              if (e.key === 'Escape') { e.preventDefault(); cancel() }
            }}
            placeholder="Where do things stand?"
            maxLength={300}
            className={`flex-1 min-w-0 ${size} bg-surface-muted border border-accent-blue/50
                        rounded-lg px-2 py-1 text-ink-primary placeholder:text-ink-muted
                        focus:outline-none`}
          />
          <button
            onClick={e => { stop(e); void save() }}
            disabled={saving}
            title="Save (Enter)"
            className="text-emerald-400 hover:text-emerald-300 disabled:opacity-50 shrink-0"
          >
            <Check size={13} />
          </button>
          <button
            onClick={e => { stop(e); cancel() }}
            title="Cancel (Esc)"
            className="text-ink-muted hover:text-ink-primary shrink-0"
          >
            <X size={13} />
          </button>
        </div>
        {error && <p className="text-[10px] text-red-400 mt-0.5">{error}</p>}
        <p className="text-[10px] text-ink-muted mt-0.5">
          Saving this changes no stage and logs no activity. Empty clears it.
        </p>
      </div>
    )
  }

  if (status) {
    return (
      <div className={`mt-1 flex items-start gap-1.5 group/status ${size}`} onClick={stop}>
        <p className="text-ink-primary flex-1 min-w-0 truncate" title={status}>
          {status}
          {updatedAt && (
            <span className="text-ink-muted font-normal">
              {' '}· {formatDate(updatedAt, { month: 'short', day: 'numeric' })}
            </span>
          )}
        </p>
        <button
          onClick={e => { stop(e); setEditing(true) }}
          title="Edit status"
          className={`shrink-0 text-ink-muted hover:text-accent-blue
            ${compact ? 'opacity-0 group-hover/status:opacity-100 transition-opacity' : ''}`}
        >
          <Pencil size={11} />
        </button>
      </div>
    )
  }

  // No status: the latest entry summary keeps the slot, exactly as before.
  return (
    <div className={`mt-1 flex items-start gap-1.5 group/status ${size}`} onClick={stop}>
      {fallback ? (
        <p className="text-ink-secondary flex-1 min-w-0 truncate">{fallback}</p>
      ) : (
        <span className="text-ink-muted flex-1 min-w-0 truncate">No status set</span>
      )}
      <button
        onClick={e => { stop(e); setEditing(true) }}
        title="Set a current status"
        className={`shrink-0 text-ink-muted hover:text-accent-blue
          ${compact ? 'opacity-0 group-hover/status:opacity-100 transition-opacity' : ''}`}
      >
        <Pencil size={11} />
      </button>
    </div>
  )
}
