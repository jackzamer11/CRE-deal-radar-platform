import { useEffect, useState, useCallback } from 'react'
import {
  X, Copy, Check, Mail, Phone, Loader2, Save, ExternalLink, ChevronDown, ChevronRight,
} from 'lucide-react'
import {
  draftOutreach, logOutreach,
  draftPropertyOutreach, logPropertyOutreach,
  updateOutreachLog,
} from '../api/client'
import type { CompanyListOut, PropertyListOut, OutreachDraft } from '../types'

const OUTREACH_TYPE_LABELS: Record<string, string> = {
  tenant_match: 'Tenant Match',
  listing_rep:  'Listing Rep',
  acquisition:  'Acquisition',
  tenant:       'Tenant Outreach',
}

interface CompanyProps {
  entity_type: 'company'
  company: CompanyListOut
  property?: never
  outreach_type?: never
  onClose: () => void
  onSaved: () => void
}

interface PropertyProps {
  entity_type: 'property'
  property: PropertyListOut
  company?: never
  outreach_type: string
  target_type?: string
  onClose: () => void
  onSaved: () => void
}

type Props = CompanyProps | PropertyProps

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }
  return (
    <button
      onClick={copy}
      className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-surface-border
                 text-ink-muted hover:text-ink-primary hover:border-accent-blue/40 transition-colors"
    >
      {copied ? <Check size={10} className="text-emerald-400" /> : <Copy size={10} />}
      {copied ? 'Copied!' : label}
    </button>
  )
}

function Section({
  title, content, extra,
}: { title: string; content: string; extra?: React.ReactNode }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="border border-surface-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2 bg-surface-muted hover:bg-surface-card text-left"
      >
        <span className="text-[11px] font-semibold text-ink-secondary uppercase tracking-wider">{title}</span>
        <div className="flex items-center gap-2">
          {extra}
          {open ? <ChevronDown size={13} className="text-ink-muted" /> : <ChevronRight size={13} className="text-ink-muted" />}
        </div>
      </button>
      {open && (
        <div className="p-3 text-xs text-ink-secondary whitespace-pre-wrap leading-relaxed bg-surface-card">
          {content}
        </div>
      )}
    </div>
  )
}

export default function OutreachDraftModal(props: Props) {
  const { entity_type, onClose, onSaved } = props

  const entityLabel = entity_type === 'company'
    ? `${props.company.name} · ${props.company.current_submarket}`
    : `${props.property.property_id} · ${props.property.submarket}`

  const typeLabel = entity_type === 'property'
    ? OUTREACH_TYPE_LABELS[props.outreach_type] ?? props.outreach_type
    : 'Tenant Outreach'

  const [draft, setDraft]         = useState<OutreachDraft | null>(null)
  const [error, setError]         = useState<string | null>(null)
  const [emailSent, setEmailSent] = useState(false)
  const [callMade, setCallMade]   = useState(false)
  const [notes, setNotes]         = useState('')
  const [saving, setSaving]       = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      let result: OutreachDraft
      if (entity_type === 'company') {
        result = await draftOutreach(props.company.company_id)
      } else {
        result = await draftPropertyOutreach(
          props.property.property_id,
          props.outreach_type,
          undefined,
          props.target_type,
        )
      }
      setDraft(result)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || 'Generation failed. Check that OPENAI_API_KEY is set and try again.'
      setError(msg)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entity_type,
    entity_type === 'company' ? props.company.company_id : props.property.property_id,
    entity_type === 'property' ? props.outreach_type : null,
  ])

  useEffect(() => { load() }, [load])

  const openOutlook = () => {
    if (!draft) return
    const subject = encodeURIComponent(draft.email_subject)
    const body    = encodeURIComponent(draft.email_body)
    window.open(`mailto:?subject=${subject}&body=${body}`)
  }

  const fullScript = draft
    ? [
        `OPENING:\n${draft.call_script.opening}`,
        `\nCORE MESSAGE:\n${draft.call_script.core_message}`,
        `\nPAIN PROBE:\n${draft.call_script.pain_probe}`,
        `\nTHE CLOSE:\n${draft.call_script.the_close}`,
      ].join('\n')
    : ''

  const handleSave = async () => {
    if (!draft) return
    setSaving(true)
    try {
      const base = {
        email_subject:          draft.email_subject,
        email_body:             draft.email_body,
        call_script_opening:    draft.call_script.opening,
        call_script_core:       draft.call_script.core_message,
        call_script_pain_probe: draft.call_script.pain_probe,
        call_script_close:      draft.call_script.the_close,
        projected_sf:           draft.projected_sf ?? null,
        score_at_generation:    draft.score,
        priority_at_generation: draft.priority,
        email_sent:             emailSent,
        call_made:              callMade,
      }

      let logId: number
      if (entity_type === 'company') {
        const log = await logOutreach(props.company.company_id, base)
        logId = log.id
      } else {
        const log = await logPropertyOutreach(props.property.property_id, {
          ...base,
          outreach_type: props.outreach_type,
        })
        logId = log.id
      }

      if (notes.trim() || emailSent || callMade) {
        await updateOutreachLog(logId, {
          outcome_notes:    notes.trim() || undefined,
          marked_contacted: emailSent || callMade,
        })
      }
      onSaved()
      onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-surface-card border border-surface-border rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-surface-border flex-shrink-0">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-ink-primary text-base">Outreach Package</span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-accent-blue/15 text-accent-blue font-medium">
                {typeLabel}
              </span>
            </div>
            <div className="text-xs text-ink-muted mt-0.5">{entityLabel}</div>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Loading */}
          {!draft && !error && (
            <div className="flex flex-col items-center justify-center py-16 gap-4 text-ink-muted">
              <Loader2 size={28} className="animate-spin text-accent-blue" />
              <div className="text-sm">Drafting outreach with GPT-4o…</div>
              <div className="text-xs text-ink-muted">This usually takes 10–15 seconds.</div>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg p-4">
              <div className="font-semibold mb-1">Generation failed</div>
              <div className="text-xs">{error}</div>
              <button onClick={load} className="mt-3 text-xs px-3 py-1.5 rounded-lg bg-red-500/20 hover:bg-red-500/30 text-red-300">
                Retry
              </button>
            </div>
          )}

          {draft && (
            <>
              {/* Stats bar */}
              <div className="flex items-center gap-3 text-[11px] text-ink-muted">
                <span className="text-ink-secondary font-semibold">Score {draft.score.toFixed(0)}/100</span>
                <span>·</span>
                <span className="text-ink-secondary font-semibold">{draft.priority}</span>
                {draft.target_type && (
                  <>
                    <span>·</span>
                    <span className="capitalize">→ {draft.target_type.replace('_', ' ')}</span>
                  </>
                )}
                {draft.projected_sf && (
                  <>
                    <span>·</span>
                    <span>Projected {draft.projected_sf.toLocaleString()} SF</span>
                  </>
                )}
              </div>

              {/* EMAIL */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs font-semibold text-ink-secondary uppercase tracking-wider">
                    <Mail size={13} />
                    Email
                  </div>
                  <div className="flex items-center gap-2">
                    <CopyButton text={draft.email_subject} label="Copy Subject" />
                    <CopyButton text={draft.email_body} label="Copy Body" />
                    <button
                      onClick={openOutlook}
                      className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-surface-border
                                 text-ink-muted hover:text-accent-blue hover:border-accent-blue/40 transition-colors"
                    >
                      <ExternalLink size={10} />
                      Open in Outlook
                    </button>
                  </div>
                </div>
                <div className="border border-surface-border rounded-lg overflow-hidden">
                  <div className="bg-surface-muted px-3 py-2 text-[11px] text-ink-muted border-b border-surface-border">
                    Subject: <span className="text-ink-secondary font-medium">{draft.email_subject}</span>
                  </div>
                  <div className="p-3 text-xs text-ink-secondary whitespace-pre-wrap leading-relaxed bg-surface-card">
                    {draft.email_body}
                  </div>
                </div>
              </div>

              {/* CALL SCRIPT */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs font-semibold text-ink-secondary uppercase tracking-wider">
                    <Phone size={13} />
                    Call Script
                  </div>
                  <CopyButton text={fullScript} label="Copy Full Script" />
                </div>
                <div className="space-y-2">
                  <Section title="Opening"      content={draft.call_script.opening} />
                  <Section title="Core Message" content={draft.call_script.core_message} />
                  <Section title="Pain Probe"   content={draft.call_script.pain_probe} />
                  <Section title="The Close"    content={draft.call_script.the_close} />
                </div>
              </div>

              {/* TRACKING */}
              <div className="border border-surface-border rounded-lg p-4 space-y-3">
                <div className="text-xs font-semibold text-ink-secondary uppercase tracking-wider">Tracking</div>
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-2 text-xs text-ink-secondary cursor-pointer">
                    <input
                      type="checkbox"
                      checked={emailSent}
                      onChange={e => setEmailSent(e.target.checked)}
                      className="accent-emerald-500"
                    />
                    Email sent
                  </label>
                  <label className="flex items-center gap-2 text-xs text-ink-secondary cursor-pointer">
                    <input
                      type="checkbox"
                      checked={callMade}
                      onChange={e => setCallMade(e.target.checked)}
                      className="accent-emerald-500"
                    />
                    Call made
                  </label>
                </div>
                <textarea
                  value={notes}
                  onChange={e => setNotes(e.target.value)}
                  placeholder="Outcome notes (optional)…"
                  rows={3}
                  className="w-full text-xs bg-surface-muted border border-surface-border rounded-lg px-3 py-2
                             text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50 resize-none"
                />
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        {draft && (
          <div className="flex items-center justify-between p-4 border-t border-surface-border flex-shrink-0">
            <button
              onClick={onClose}
              className="text-xs px-4 py-2 rounded-lg border border-surface-border text-ink-muted hover:text-ink-primary"
            >
              Cancel (don't save)
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 text-xs px-4 py-2 rounded-lg bg-emerald-600 text-white
                         hover:bg-emerald-700 disabled:opacity-50 font-semibold"
            >
              <Save size={13} />
              {saving ? 'Saving…' : 'Save & Mark Contacted'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
