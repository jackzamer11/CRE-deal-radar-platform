import { useState } from 'react'
import { HelpCircle } from 'lucide-react'
import { confirmContactType } from '../api/client'
import type { ContactTypeConfirmResult } from '../api/client'
import type { ConfirmableType } from '../types'
import { CONTACT_TYPE_LABELS, UI_CONTACT_TYPES } from '../types'

// "Tenant or counterparty?" on an unconfirmed contact.
//
// The suggested answer is pre-selected (highlighted, with its reason on hover)
// but nothing is written until Jack clicks — a suggestion never becomes a type
// on its own. After a click, if others at the same firm are still unconfirmed,
// one follow-up offers to apply the same answer to all of them and to anyone
// the email task creates there later. That is what keeps the problem from
// coming back one broker at a time.
//
// Every click stops propagation: this sits inside a clickable contact card.
export default function TypeConfirm({
  contactId, suggestedType, suggestedReason, onDone, compact = false,
}: {
  contactId: number
  suggestedType: ConfirmableType | null
  suggestedReason: string | null
  onDone: () => void
  compact?: boolean
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Set after a confirmation that left others unconfirmed at the same firm.
  const [offer, setOffer] = useState<
    { type: ConfirmableType; result: ContactTypeConfirmResult } | null
  >(null)

  const confirm = async (type: ConfirmableType, applyToFirm: boolean) => {
    setSaving(true)
    setError(null)
    try {
      const result = await confirmContactType(contactId, type, applyToFirm)
      if (!applyToFirm && result.firm_unconfirmed > 0 && result.firm_name) {
        setOffer({ type, result })
      } else {
        setOffer(null)
        onDone()
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not save the type.')
    } finally {
      setSaving(false)
    }
  }

  const stop = (e: React.SyntheticEvent) => e.stopPropagation()
  const size = compact ? 'text-[9px] px-2 py-0.5' : 'text-[10px] px-2.5 py-1'

  if (offer) {
    const { type, result } = offer
    const n = result.firm_unconfirmed
    return (
      <div onClick={stop} onKeyDown={stop}
           className="flex items-center gap-1.5 flex-wrap text-[10px] text-ink-secondary">
        <span>
          Also mark {n} other{n === 1 ? '' : 's'} at <b>{result.firm_name}</b> — and anyone
          new from there — as {CONTACT_TYPE_LABELS[type].toLowerCase()}?
        </span>
        <button
          disabled={saving}
          onClick={() => void confirm(type, true)}
          className={`${size} rounded-full border font-semibold bg-accent-blue/20 text-accent-blue
                      border-accent-blue/50 hover:bg-accent-blue/30 disabled:opacity-50`}
        >
          Yes, whole firm
        </button>
        <button
          disabled={saving}
          onClick={() => { setOffer(null); onDone() }}
          className={`${size} rounded-full border font-semibold bg-surface-muted text-ink-muted
                      border-surface-border hover:text-ink-primary`}
        >
          Just this person
        </button>
        {error && <span className="text-red-400">{error}</span>}
      </div>
    )
  }

  return (
    <div onClick={stop} onKeyDown={stop} className="flex items-center gap-1.5 flex-wrap">
      <span className="flex items-center gap-1 text-[10px] text-amber-300/90">
        <HelpCircle size={10} /> Tenant or counterparty?
      </span>
      {UI_CONTACT_TYPES.map(t => {
        const type = t as ConfirmableType
        const suggested = suggestedType === type
        return (
          <button
            key={type}
            disabled={saving}
            onClick={() => void confirm(type, false)}
            title={suggested && suggestedReason ? `Suggested: ${suggestedReason}` : undefined}
            className={`${size} rounded-full border font-semibold transition-colors disabled:opacity-50
              ${suggested
                ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50 hover:bg-accent-blue/30'
                : 'bg-surface-muted text-ink-muted border-surface-border hover:text-ink-primary'}`}
          >
            {CONTACT_TYPE_LABELS[type]}
          </button>
        )
      })}
      {suggestedReason && !compact && (
        <span className="text-[10px] text-ink-muted">suggested: {suggestedReason}</span>
      )}
      {error && <span className="text-[10px] text-red-400">{error}</span>}
    </div>
  )
}
