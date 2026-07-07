import { useEffect, useState, useCallback, useRef } from 'react'
import {
  X, Copy, Check, Mail, Phone, Loader2, Save, ExternalLink, ChevronDown, ChevronRight, RotateCcw,
  Search,
} from 'lucide-react'
import {
  draftOutreach, logOutreach,
  draftPropertyOutreach, logPropertyOutreach,
  updateOutreachLog,
  getOutreachDraft, saveOutreachDraft, deleteOutreachDraft,
  createActivity, searchIntelligence,
} from '../api/client'
import type {
  CompanyListOut, PropertyListOut, OutreachDraft, MatchedTenant, OutreachDraftRecord, IntelFinding,
} from '../types'

const OUTREACH_TYPE_LABELS: Record<string, string> = {
  tenant_match:     'Tenant Match',
  for_sale_vacancy: 'For Sale + Vacancy',
  acquisition:      'Acquisition',
  tenant:           'Tenant Outreach',
}

interface CompanyProps {
  entity_type: 'company'
  company: CompanyListOut
  property?: never
  outreach_type?: never
  onClose: () => void
  onSaved: () => void
  onContacted?: (pairKey: string) => void
}

interface PropertyProps {
  entity_type: 'property'
  property: PropertyListOut
  company?: never
  outreach_type: string
  target_type?: string
  tenant_context?: string
  // Pair-specific (Change 5)
  pair_company_id?: string
  pair_tenant_name?: string
  // Recipient pre-fill (Change 7)
  recipient_name?: string
  recipient_email?: string
  // For for_sale_vacancy internal panel (Change 5) + tenant tabs
  matched_tenants?: MatchedTenant[]
  onClose: () => void
  onSaved: () => void
  onContacted?: (pairKey: string) => void
}

type Props = CompanyProps | PropertyProps

type Direction = 'property_side' | 'tenant_side'
type IntelPhase = 'idle' | 'loading' | 'review' | 'generating'

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
  const { entity_type, onClose, onSaved, onContacted } = props

  const propertyAddress = entity_type === 'property' ? props.property.address : ''
  const tenantName      = entity_type === 'property' ? (props.pair_tenant_name ?? '') : ''

  const entityLabel = entity_type === 'company'
    ? `${props.company.name} · ${props.company.current_submarket}`
    : `${props.property.property_id} · ${props.property.submarket}`

  const typeLabel = entity_type === 'property'
    ? OUTREACH_TYPE_LABELS[props.outreach_type] ?? props.outreach_type
    : 'Tenant Outreach'

  // ── Direction toggle ────────────────────────────────────────────────────────
  const [direction, setDirection] = useState<Direction>('property_side')

  // ── Tenant tab state (tenant_match only, top 3 by match_score) ─────────────
  const matchedTenants = entity_type === 'property' ? (props.matched_tenants ?? []) : []
  const tabTenants = [...matchedTenants]
    .sort((a, b) => b.match_score - a.match_score)
    .slice(0, 3)
  const isTabMode = entity_type === 'property'
    && props.outreach_type === 'tenant_match'
    && tabTenants.length >= 1
  const [activeTabCompanyId, setActiveTabCompanyId] = useState<string | null>(
    entity_type !== 'property'
      ? null
      // Always honour pair_company_id when provided (e.g. from Daily Briefing row) even
      // if it didn't rank in the top-3 matched tenants for this property's tab list.
      // Fall back to the #1-ranked tab tenant only when no pair_company_id is given
      // (i.e. modal opened directly from the property detail panel).
      : props.pair_company_id ?? tabTenants[0]?.company_id ?? null
  )
  const activeTabTenant = tabTenants.find(t => t.company_id === activeTabCompanyId) ?? tabTenants[0]

  // Effective pair company id — may change when tab changes
  const effectiveCompanyId = isTabMode ? activeTabCompanyId : (
    entity_type === 'property' ? (props.pair_company_id ?? null) : null
  )
  const effectiveTenantName = isTabMode
    ? (activeTabTenant?.name ?? tenantName)
    : tenantName

  // Name of the tenant whose profile the property-side email leads with.
  // Looks up effectiveCompanyId in the full matched_tenants list (not just the
  // top-3 tabTenants) so it stays correct even when pair_company_id ranked
  // outside the tabs (e.g. when opened from the Daily Briefing).
  const leadingTenantName: string | null = entity_type === 'property'
    ? ((props.matched_tenants ?? []).find(t => t.company_id === effectiveCompanyId)?.name
       ?? props.pair_tenant_name
       ?? null)
    : null

  // ── Draft state ─────────────────────────────────────────────────────────────
  const [draft, setDraft]         = useState<OutreachDraft | null>(null)
  const [persistedId, setPersistedId] = useState<number | null>(null)
  const [error, setError]         = useState<string | null>(null)
  const [emailSent, setEmailSent] = useState(false)
  const [callMade, setCallMade]   = useState(false)
  const [notes, setNotes]         = useState('')
  const [saving, setSaving]       = useState(false)
  const [resetting, setResetting] = useState(false)

  // ── Intel phase ─────────────────────────────────────────────────────────────
  const [intelPhase, setIntelPhase] = useState<IntelPhase>('idle')
  const [intelFindings, setIntelFindings] = useState<IntelFinding[]>([])

  // ── Recipient ────────────────────────────────────────────────────────────────
  // Fix 3: the recipient field is shown on the tenant outreach modal too. For a
  // company it pre-fills from the contact on file (primary_contact_name); the email
  // has no contact-on-file field, so it stays empty and surfaces the warning — the
  // same behavior as the Daily Briefing modal when a contact email isn't on record.
  const initialRecipientName  = entity_type === 'property'
    ? (props.recipient_name  ?? '')
    : (props.company.primary_contact_name ?? '')
  const initialRecipientEmail = entity_type === 'property' ? (props.recipient_email ?? '') : ''
  const [recipientName,  setRecipientName]  = useState(initialRecipientName)
  const [recipientEmail, setRecipientEmail] = useState(initialRecipientEmail)

  const draftToBaseDraft = (rec: OutreachDraftRecord): OutreachDraft => ({
    email_subject:     rec.subject,
    email_body:        rec.body,
    call_script: {
      opening:      rec.call_script_opening   ?? '',
      data:         rec.call_script_data      ?? '',
      // ANGLE line — reused call_script_hook column (legacy rows hold THE HOOK prose)
      angle:        rec.call_script_hook      ?? '',
      core_message: rec.call_script_core      ?? '',
      pain_probe:   rec.call_script_pain_probe ?? '',
      the_close:    rec.call_script_close     ?? '',
    },
    projected_sf:  null,
    score:         rec.score    ?? 0,
    priority:      (rec.priority as OutreachDraft['priority']) ?? 'WORKABLE',
    generated_at:  rec.created_at,
    outreach_type: rec.outreach_type,
    target_type:   rec.target_type,
  })

  const persistDraft = useCallback(async (
    d: OutreachDraft,
    checkedFindings: IntelFinding[],
    allFindings: IntelFinding[],
  ) => {
    if (entity_type !== 'property') return
    const intelCtx = checkedFindings.length > 0
      ? JSON.stringify(checkedFindings.map(f => f.fact))
      : (props.tenant_context ?? null)
    // Persist full findings (with source_url etc.) so reopening doesn't re-search.
    // Empty array is also stored ("[]") to signal "search ran, no results".
    const intelFindingsJson = JSON.stringify(
      allFindings.map(({ fact, source_url, source_name, relevance_score }) =>
        ({ fact, source_url, source_name, relevance_score })
      )
    )
    try {
      const rec = await saveOutreachDraft({
        property_id:  props.property.property_id,
        company_id:   effectiveCompanyId ?? null,
        outreach_type: props.outreach_type,
        direction,
        subject:      d.email_subject,
        body:         d.email_body,
        call_script_opening:    d.call_script.opening,
        call_script_hook:       d.call_script.angle || null,
        call_script_data:       d.call_script.data || null,
        call_script_core:       d.call_script.core_message,
        call_script_pain_probe: d.call_script.pain_probe,
        call_script_close:      d.call_script.the_close,
        target_type:  props.target_type ?? d.target_type ?? 'owner',
        recipient_name:  recipientName  || null,
        recipient_email: recipientEmail || null,
        internal_context: intelCtx,
        intelligence_findings: intelFindingsJson,
        score:    d.score,
        priority: d.priority,
      })
      setPersistedId(rec.id)
      setDraft(draftToBaseDraft(rec))
    } catch {
      // Persistence failure shouldn't block usage of the live draft
    }
  }, [entity_type,
      entity_type === 'property' ? props.property.property_id : null,
      effectiveCompanyId,
      entity_type === 'property' ? props.outreach_type : null,
      entity_type === 'property' ? props.target_type : null,
      entity_type === 'property' ? props.tenant_context : null,
      direction,
      recipientName, recipientEmail])

  const generateFresh = useCallback(async (checkedFindings: IntelFinding[] = []) => {
    setError(null)
    setDraft(null)
    setIntelPhase('generating')
    try {
      let result: OutreachDraft
      if (entity_type === 'company') {
        result = await draftOutreach(props.company.company_id)
      } else {
        // Build intel_context from checked findings
        const intelList = checkedFindings.map(f => f.fact)
        result = await draftPropertyOutreach(
          props.property.property_id,
          props.outreach_type,
          props.tenant_context,
          props.target_type,
          intelList.length > 0 ? JSON.stringify(intelList) : undefined,
          direction,
          effectiveCompanyId ?? undefined,
        )
      }
      setDraft(result)
      setIntelPhase('idle')
      if (entity_type === 'property') {
        void persistDraft(result, checkedFindings, intelFindings)
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || 'Generation failed. Check that OPENAI_API_KEY is set and try again.'
      setError(msg)
      setIntelPhase('idle')
    }
  }, [entity_type,
      entity_type === 'company' ? props.company.company_id : null,
      entity_type === 'property' ? props.property.property_id : null,
      entity_type === 'property' ? props.outreach_type : null,
      entity_type === 'property' ? props.tenant_context : null,
      entity_type === 'property' ? props.target_type : null,
      intelFindings,
      persistDraft])

  const runIntelAndGenerate = useCallback(async () => {
    if (entity_type !== 'property') { await generateFresh(); return }
    setIntelPhase('loading')
    setIntelFindings([])
    setDraft(null)
    setError(null)
    try {
      const resp = await searchIntelligence(
        props.property.property_id,
        effectiveCompanyId,
        direction,
      )
      const findings = (resp.findings ?? []).map(f => ({ ...f, checked: true }))
      if (findings.length === 0) {
        await generateFresh([])
      } else {
        setIntelFindings(findings)
        setIntelPhase('review')
      }
    } catch {
      await generateFresh([])
    }
  }, [entity_type,
      entity_type === 'property' ? props.property.property_id : null,
      effectiveCompanyId,
      direction,
      generateFresh])

  // Refresh-only: force a new Claude search without deleting the existing draft.
  // Shows the review panel so the user can select findings before regenerating.
  const handleRefreshIntelligence = useCallback(async () => {
    if (entity_type !== 'property') return
    setIntelPhase('loading')
    setError(null)
    try {
      const resp = await searchIntelligence(
        props.property.property_id,
        effectiveCompanyId,
        direction,
        true, // force_refresh — bypass all caches, run live search
      )
      const findings = (resp.findings ?? []).map(f => ({ ...f, checked: true }))
      setIntelFindings(findings)
      setIntelPhase('review')
    } catch {
      setIntelPhase('idle')
    }
  }, [entity_type,
      entity_type === 'property' ? props.property.property_id : null,
      effectiveCompanyId,
      direction])

  const load = useCallback(async () => {
    setError(null)
    setDraft(null)
    setIntelFindings([])
    setIntelPhase('idle')
    // Property-side: try persisted draft first
    if (entity_type === 'property' && effectiveCompanyId) {
      try {
        const existing = await getOutreachDraft(
          props.property.property_id,
          effectiveCompanyId,
          props.outreach_type,
          direction,
        )
        if (existing) {
          setDraft(draftToBaseDraft(existing))
          setPersistedId(existing.id)
          if (existing.recipient_name)  setRecipientName(existing.recipient_name)
          if (existing.recipient_email) setRecipientEmail(existing.recipient_email)
          // Hydrate cached intel findings — null = not yet searched; "[]" = searched, none found
          if (existing.intelligence_findings != null) {
            try {
              const parsed: IntelFinding[] = JSON.parse(existing.intelligence_findings)
              setIntelFindings(parsed.map(f => ({ ...f, checked: true })))
            } catch { /* corrupted JSON — leave intelFindings empty */ }
          }
          return
        }
      } catch {
        // fall through
      }
    }
    // No cached draft — run intel + generate
    await runIntelAndGenerate()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entity_type,
      entity_type === 'property' ? props.property.property_id : null,
      effectiveCompanyId,
      entity_type === 'property' ? props.outreach_type : null,
      direction])

  // Key representing the current load target — used to deduplicate the effect.
  // React 18 Strict Mode fires effects twice (mount → cleanup → remount) in dev;
  // without a guard both invocations race to call searchIntelligence, producing
  // two backend requests.  The ref persists across the cleanup cycle, so the
  // remount sees the same key and skips.  When effectiveCompanyId or direction
  // actually changes (tab switch, toggle) the key differs → load() runs again.
  const loadedForKeyRef = useRef('')

  useEffect(() => {
    const key = `${effectiveCompanyId ?? '_'}::${direction}`
    if (loadedForKeyRef.current === key) return
    loadedForKeyRef.current = key
    load()
  }, [load, effectiveCompanyId, direction])

  const handleResetDraft = async () => {
    if (resetting) return
    setResetting(true)
    try {
      if (persistedId != null) {
        try { await deleteOutreachDraft(persistedId) } catch { /* ignore */ }
        setPersistedId(null)
      }
      setDraft(null)
      await runIntelAndGenerate()
    } finally {
      setResetting(false)
    }
  }

  const openOutlook = () => {
    if (!draft) return
    const subject = encodeURIComponent(draft.email_subject)
    const body    = encodeURIComponent(draft.email_body)
    const to      = recipientEmail ? encodeURIComponent(recipientEmail) : ''
    window.open(`mailto:${to}?subject=${subject}&body=${body}`)
  }

  // Tenant drafts carry the deterministic CALL SHEET (DATA is never empty for
  // them — missing values render "not on file"); property-side scripts don't.
  const isCallSheet = !!(draft?.call_script.data || draft?.call_script.angle)

  const fullScript = draft
    ? [
        `OPENING:\n${draft.call_script.opening}`,
        ...(draft.call_script.data ? [`\nDATA:\n${draft.call_script.data}`] : []),
        ...(draft.call_script.angle ? [`\nANGLE:\n${draft.call_script.angle}`] : []),
        `\n${isCallSheet ? 'DISCOVERY' : 'CORE MESSAGE'}:\n${draft.call_script.core_message}`,
        `\nPAIN PROBE:\n${draft.call_script.pain_probe}`,
        `\nTHE CLOSE:\n${draft.call_script.the_close}`,
      ].join('\n')
    : ''

  const handleSave = async () => {
    if (!draft) return
    setSaving(true)

    const base = {
      email_subject:          draft.email_subject,
      email_body:             draft.email_body,
      call_script_opening:    draft.call_script.opening,
      call_script_hook:       draft.call_script.angle || null,
      call_script_data:       draft.call_script.data || null,
      call_script_core:       draft.call_script.core_message,
      call_script_pain_probe: draft.call_script.pain_probe,
      call_script_close:      draft.call_script.the_close,
      projected_sf:           draft.projected_sf ?? null,
      score_at_generation:    draft.score,
      priority_at_generation: draft.priority,
      email_sent:             emailSent,
      call_made:              callMade,
    }

    let logId: number | undefined
    const propertyDbId = entity_type === 'property' ? props.property.id : undefined
    const companyDbId  = entity_type === 'company'  ? props.company.id  : undefined

    // 1) Persist outreach_log — independent fault tolerance
    try {
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
    } catch {
      // outreach_log failure shouldn't block activity logging or status update
    }

    // 2) Mark contacted — always send on "Save & Mark Contacted"; do NOT gate on
    // tracking checkboxes (emailSent/callMade). The button's intent IS "mark contacted".
    // pair_company_id lets the backend advance the exact tenant-match Opportunity row
    // rather than just the first Opportunity for this property.
    if (logId != null) {
      try {
        await updateOutreachLog(logId, {
          outcome_notes:    notes.trim() || undefined,
          marked_contacted: true,
          pair_company_id:  entity_type === 'property' ? (props.pair_company_id ?? undefined) : undefined,
        })
      } catch { /* ignore */ }
    }

    // 3) ALWAYS fire activity log on Save & Mark Contacted — independent
    try {
      const contact_method = callMade ? 'phone' : 'email'
      const action_type    = callMade ? 'CALL'  : 'EMAIL'
      const action_taken   = callMade
        ? `Called ${recipientName || 'contact'}${effectiveTenantName ? ` re ${effectiveTenantName}` : ''}`
        : `Emailed ${recipientName || 'contact'}${effectiveTenantName ? ` re ${effectiveTenantName}` : ''}`
      const notesLine = `Outreach sent: ${draft.email_subject}${recipientName ? ` to ${recipientName}` : ''}`
      await createActivity({
        action_type,
        action_taken,
        outcome: notes.trim() || notesLine,
        property_id: propertyDbId,
        company_id:  companyDbId,
        outreach_type: entity_type === 'property'
          ? (props.outreach_type === 'tenant_match'
              ? (direction === 'tenant_side' ? 'tenant_match_tenant' : 'tenant_match_owner')
              : props.outreach_type)
          : 'tenant',
        target_type:   entity_type === 'property'
          ? (direction === 'tenant_side' ? 'tenant' : (props.target_type ?? 'owner'))
          : 'tenant',
        contact_method,
        subject: draft.email_subject,
      } as Parameters<typeof createActivity>[0])
    } catch {
      // Activity log failure shouldn't block status update
    }

    // 4) Notify parent so status badge can update in real-time — always
    if (onContacted && entity_type === 'property') {
      const pairKey = `${props.property.property_id}:${effectiveCompanyId ?? ''}:${props.outreach_type}`
      onContacted(pairKey)
    }

    setSaving(false)
    onSaved()
    onClose()
  }

  const showInternalPanel = entity_type === 'property'
    && props.outreach_type === 'for_sale_vacancy'
    && matchedTenants.length > 0

  // ── Pair header text ─────────────────────────────────────────────────────────
  const pairHeaderLine = entity_type === 'property' && (() => {
    if (props.outreach_type === 'for_sale_vacancy') {
      return (
        <div className="text-[11px] text-emerald-400 mt-1.5 font-medium">
          Drafting for: <span className="text-ink-primary">{propertyAddress}</span>
          <span className="text-ink-muted mx-1">→</span>
          <span className="text-ink-primary">All Matched Tenants (For Sale + Vacancy)</span>
        </div>
      )
    }
    if (effectiveTenantName) {
      return (
        <div className="text-[11px] text-emerald-400 mt-1.5 font-medium">
          Drafting for: <span className="text-ink-primary">{propertyAddress}</span>
          <span className="text-ink-muted mx-1">→</span>
          <span className="text-ink-primary">{effectiveTenantName}</span>
        </div>
      )
    }
    return null
  })()

  const isLoading = !draft && !error && intelPhase !== 'review'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className={`bg-surface-card border border-surface-border rounded-2xl shadow-2xl w-full ${showInternalPanel ? 'max-w-4xl' : 'max-w-2xl'} max-h-[90vh] flex flex-col`}>
        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-surface-border flex-shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-bold text-ink-primary text-base">Outreach Package</span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-accent-blue/15 text-accent-blue font-medium">
                {typeLabel}
              </span>
            </div>
            <div className="text-xs text-ink-muted mt-0.5">{entityLabel}</div>
            {pairHeaderLine}
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1 ml-3 flex-shrink-0">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto flex">
          <div className="flex-1 overflow-y-auto p-5 space-y-4 min-w-0">

            {/* Direction toggle — property outreach only, not for for_sale_vacancy */}
            {entity_type === 'property' && props.outreach_type !== 'for_sale_vacancy' && (
              <div className="flex gap-2">
                <button
                  onClick={() => { if (direction !== 'property_side') setDirection('property_side') }}
                  className={`flex-1 text-[11px] py-1.5 rounded-lg border font-semibold transition-colors ${
                    direction === 'property_side'
                      ? 'bg-emerald-600 border-emerald-600 text-white'
                      : 'border-surface-border text-ink-muted hover:text-ink-primary'
                  }`}
                >
                  Property Side
                </button>
                <button
                  onClick={() => { if (direction !== 'tenant_side') setDirection('tenant_side') }}
                  className={`flex-1 text-[11px] py-1.5 rounded-lg border font-semibold transition-colors ${
                    direction === 'tenant_side'
                      ? 'bg-emerald-600 border-emerald-600 text-white'
                      : 'border-surface-border text-ink-muted hover:text-ink-primary'
                  }`}
                >
                  Tenant Side
                </button>
              </div>
            )}

            {/* Tenant tabs for tenant_match with multiple matches */}
            {isTabMode && (
              <div className="flex gap-1 border-b border-surface-border pb-0">
                {tabTenants.map(t => (
                  <button
                    key={t.company_id}
                    onClick={() => { if (activeTabCompanyId !== t.company_id) setActiveTabCompanyId(t.company_id) }}
                    className={`text-[11px] px-3 py-1.5 rounded-t-lg border border-b-0 font-semibold transition-colors ${
                      activeTabCompanyId === t.company_id
                        ? 'bg-emerald-600 border-emerald-600 text-white'
                        : 'border-surface-border text-ink-muted hover:text-ink-primary'
                    }`}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            )}

            {/* Active tenant context bar — always visible while tab mode is active */}
            {isTabMode && activeTabTenant && (
              <div className="flex items-center gap-2 flex-wrap text-[10px] text-ink-muted bg-surface-muted border border-surface-border rounded-lg px-3 py-2">
                <span className="text-ink-secondary font-semibold">{activeTabTenant.name}</span>
                <span>·</span>
                <span>{activeTabTenant.industry}</span>
                <span>·</span>
                <span>SF: {activeTabTenant.sf_display}</span>
              </div>
            )}

            {/* Intelligence Review Phase */}
            {intelPhase === 'review' && (
              <div className="border border-accent-blue/30 rounded-xl p-4 space-y-3 bg-accent-blue/5">
                <div className="flex items-center gap-2">
                  <Search size={13} className="text-accent-blue" />
                  <span className="text-xs font-semibold text-ink-primary">Intelligence Found — Review Before Generating</span>
                </div>
                <div className="space-y-2">
                  {intelFindings.map((f, i) => (
                    <label key={i} className="flex items-start gap-2 cursor-pointer group">
                      <input
                        type="checkbox"
                        checked={f.checked ?? true}
                        onChange={e => {
                          const updated = intelFindings.map((x, j) =>
                            j === i ? { ...x, checked: e.target.checked } : x
                          )
                          setIntelFindings(updated)
                        }}
                        className="mt-0.5 accent-emerald-500 flex-shrink-0"
                      />
                      <div className="min-w-0">
                        <div className="text-xs text-ink-secondary group-hover:text-ink-primary leading-snug">{f.fact}</div>
                        <div className="text-[10px] text-ink-muted mt-0.5">
                          {f.source_name}
                          {f.source_url && f.source_url !== 'N/A' && (
                            <a
                              href={f.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="ml-1.5 text-accent-blue hover:underline"
                              onClick={e => e.stopPropagation()}
                            >
                              ↗
                            </a>
                          )}
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => generateFresh(intelFindings.filter(f => f.checked))}
                    className="flex-1 text-xs py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold"
                  >
                    Generate Email
                  </button>
                  {draft ? (
                    // Refreshing intel on an existing draft — Skip goes back to the draft
                    <button
                      onClick={() => setIntelPhase('idle')}
                      className="text-xs px-4 py-2 rounded-lg border border-surface-border text-ink-muted hover:text-ink-primary"
                    >
                      Keep Existing Draft
                    </button>
                  ) : (
                    <button
                      onClick={() => generateFresh([])}
                      className="text-xs px-4 py-2 rounded-lg border border-surface-border text-ink-muted hover:text-ink-primary"
                    >
                      Skip Intelligence / Generate Anyway
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Loading */}
            {isLoading && (
              <div className="flex flex-col items-center justify-center py-16 gap-4 text-ink-muted">
                <Loader2 size={28} className="animate-spin text-accent-blue" />
                <div className="text-sm">
                  {intelPhase === 'loading'    ? 'Searching web for intelligence…' :
                   intelPhase === 'generating' ? 'Generating draft…' :
                   resetting                   ? 'Regenerating draft…' :
                                                 'Loading draft…'}
                </div>
                {intelPhase === 'generating' && (
                  <div className="text-xs text-ink-muted">GPT generation takes 10–15 seconds.</div>
                )}
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg p-4">
                <div className="font-semibold mb-1">Generation failed</div>
                <div className="text-xs">{error}</div>
                <button onClick={() => generateFresh()} className="mt-3 text-xs px-3 py-1.5 rounded-lg bg-red-500/20 hover:bg-red-500/30 text-red-300">
                  Retry
                </button>
              </div>
            )}

            {draft && (
              <>
                {/* Stats bar + Reset Draft */}
                <div className="flex items-center justify-between gap-3 text-[11px] text-ink-muted flex-wrap">
                  <div className="flex items-center gap-3">
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
                        {/* SF now sources from the tenant's real current_sf, so it
                            is the current footprint — not a projected estimate. */}
                        <span>Current {draft.projected_sf.toLocaleString()} SF</span>
                      </>
                    )}
                  </div>
                  {entity_type === 'property' && (
                    <div className="flex items-center gap-3">
                      <button
                        onClick={handleRefreshIntelligence}
                        className="flex items-center gap-1 text-[10px] text-ink-muted hover:text-accent-blue"
                        title="Re-run web intelligence search for this property"
                      >
                        <Search size={10} />
                        Refresh Intelligence
                      </button>
                      <button
                        onClick={handleResetDraft}
                        disabled={resetting}
                        className="flex items-center gap-1 text-[10px] text-ink-muted hover:text-accent-blue disabled:opacity-50"
                        title="Delete and regenerate this draft"
                      >
                        <RotateCcw size={10} />
                        Reset Draft
                      </button>
                    </div>
                  )}
                </div>

                {/* RECIPIENT (Change 7; Fix 3 — also shown on the tenant outreach modal) */}
                <div className="border border-surface-border rounded-lg p-3 space-y-2">
                    <div className="text-[11px] font-semibold text-ink-secondary uppercase tracking-wider">Recipient</div>
                    <div className="grid grid-cols-2 gap-2">
                      <input
                        value={recipientName}
                        onChange={e => setRecipientName(e.target.value)}
                        placeholder="Recipient name"
                        className="text-xs bg-surface-muted border border-surface-border rounded-lg px-3 py-2
                                   text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"
                      />
                      <input
                        value={recipientEmail}
                        onChange={e => setRecipientEmail(e.target.value)}
                        placeholder={initialRecipientEmail ? '' : 'Add recipient before sending — contact not on file'}
                        className={`text-xs bg-surface-muted border rounded-lg px-3 py-2
                                    text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50
                                    ${recipientEmail ? 'border-surface-border' : 'border-amber-500/40'}`}
                      />
                    </div>
                    {!recipientEmail && (
                      <div className="text-[10px] text-amber-400">
                        Add recipient before sending — contact not on file
                      </div>
                    )}
                </div>

                {/* Leading-with label — property_side only */}
                {direction === 'property_side' && leadingTenantName && (
                  <div className="text-[11px] text-ink-muted mb-1">
                    Leading with: <span className="text-ink-secondary">{leadingTenantName}</span>
                  </div>
                )}

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
                      {isCallSheet ? 'Call Sheet' : 'Call Script'}
                    </div>
                    <CopyButton text={fullScript} label="Copy Full Script" />
                  </div>
                  <div className="space-y-2">
                    <Section title="Opening"      content={draft.call_script.opening} />
                    {draft.call_script.data ? (
                      <Section title="Data"       content={draft.call_script.data} />
                    ) : null}
                    {draft.call_script.angle ? (
                      <Section title="Angle"      content={draft.call_script.angle} />
                    ) : null}
                    <Section
                      title={isCallSheet ? 'Discovery' : 'Core Message'}
                      content={draft.call_script.core_message}
                    />
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

          {/* Internal side panel (Change 5): for_sale_vacancy matched tenants */}
          {showInternalPanel && (
            <div className="w-72 border-l border-surface-border bg-surface-muted p-4 overflow-y-auto flex-shrink-0">
              <div className="text-[10px] text-amber-400 font-bold uppercase tracking-widest mb-1">
                Internal Only
              </div>
              <div className="text-[11px] text-ink-muted mb-3">
                Matched tenants — never reveal in email.
              </div>
              <div className="space-y-2">
                {matchedTenants.map(t => (
                  <div key={t.company_id} className="border border-surface-border rounded-lg p-2 bg-surface-card">
                    <div className="text-xs font-semibold text-ink-primary truncate">{t.name}</div>
                    <div className="text-[10px] text-ink-muted">
                      {t.industry} · {t.headcount ?? '—'} HC
                    </div>
                    <div className="text-[10px] text-ink-secondary mt-0.5">
                      SF: {t.sf_display}
                    </div>
                    <div className="text-[10px] text-violet-400 mt-1">
                      Match {t.match_score.toFixed(0)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
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
