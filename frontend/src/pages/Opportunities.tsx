import { useEffect, useState } from 'react'
import { Crosshair, Filter, X, MessageSquarePlus } from 'lucide-react'
import { getOpportunities, updateStage, getProperty, getCompany } from '../api/client'
import type { OpportunityListOut, PropertyOut, CompanyOut, Stage } from '../types'
import { PriorityBadge, DealTypeBadge, ConfidenceBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'
import OutreachDraftModal from '../components/OutreachDraftModal'

const STAGES: Stage[] = ['IDENTIFIED', 'CONTACTED', 'ACTIVE', 'UNDER_LOI', 'CLOSED', 'DEAD']

const STAGE_COLORS: Record<Stage, string> = {
  IDENTIFIED: 'text-blue-400 bg-blue-500/10 border-blue-500/30',
  CONTACTED:  'text-amber-400 bg-amber-500/10 border-amber-500/30',
  ACTIVE:     'text-purple-400 bg-purple-500/10 border-purple-500/30',
  UNDER_LOI:  'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
  CLOSED:     'text-emerald-600 bg-emerald-500/10 border-emerald-600/30',
  DEAD:       'text-slate-500 bg-slate-800 border-slate-700',
}

function StageBadge({ stage }: { stage: Stage }) {
  return (
    <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${STAGE_COLORS[stage]}`}>
      {stage.replace('_', ' ')}
    </span>
  )
}

function ScoreBar({ label, value, color }: { label: string; value: number | null; color: string }) {
  const pct = value != null ? Math.max(0, Math.min(100, value)) : 0
  return (
    <div>
      <div className="flex items-center justify-between mb-0.5">
        <span className="text-[10px] text-ink-muted">{label}</span>
        <span className={`text-[11px] font-bold mono ${color}`}>{value != null ? value.toFixed(0) : '—'}</span>
      </div>
      <div className="h-1.5 bg-surface-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color.replace('text-', 'bg-')}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function Opportunities() {
  const [opps, setOpps] = useState<OpportunityListOut[]>([])
  const [loading, setLoading] = useState(true)
  const [priority, setPriority] = useState('')
  const [dealType, setDealType] = useState('')
  const [stage, setStage] = useState('')
  const [selected, setSelected] = useState<OpportunityListOut | null>(null)
  const [selProp, setSelProp] = useState<PropertyOut | null>(null)
  const [selCompany, setSelCompany] = useState<CompanyOut | null>(null)
  const [updating, setUpdating] = useState<number | null>(null)
  const [outreachModal, setOutreachModal] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await getOpportunities({
        priority: priority || undefined,
        deal_type: dealType || undefined,
        stage: stage || undefined,
        active_only: stage !== 'DEAD',
      })
      setOpps(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [priority, dealType, stage])

  const handleSelect = async (opp: OpportunityListOut) => {
    setSelected(opp)
    setSelProp(null)
    setSelCompany(null)
    if (opp.property_ref) {
      getProperty(opp.property_ref).then(setSelProp).catch(() => {})
    }
    if (opp.company_ref) {
      getCompany(opp.company_ref).then(c => setSelCompany(c as CompanyOut)).catch(() => {})
    }
  }

  const handleStageChange = async (opp: OpportunityListOut, newStage: string) => {
    setUpdating(opp.id)
    try {
      await updateStage(opp.opportunity_id, newStage)
      await load()
      if (selected?.id === opp.id) {
        setSelected(prev => prev ? { ...prev, stage: newStage as Stage } : null)
      }
    } finally {
      setUpdating(null)
    }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Crosshair size={20} className="text-red-400" />
          <h1 className="text-xl font-bold text-ink-primary">Opportunities</h1>
          <span className="text-ink-muted text-sm">({opps.length})</span>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Filter size={13} className="text-ink-muted" />
        <select
          value={priority}
          onChange={e => setPriority(e.target.value)}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">All Priorities</option>
          {['IMMEDIATE','HIGH','WORKABLE'].map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          value={dealType}
          onChange={e => setDealType(e.target.value)}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">All Types</option>
          <option value="PRE_MARKET">Pre-Market</option>
          <option value="ACTIVE_MISPRICED">Active Mispriced</option>
          <option value="TENANT_DRIVEN">Tenant Driven</option>
        </select>
        <select
          value={stage}
          onChange={e => setStage(e.target.value)}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">All Stages</option>
          {STAGES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </select>
        {(priority || dealType || stage) && (
          <button
            onClick={() => { setPriority(''); setDealType(''); setStage('') }}
            className="flex items-center gap-1 text-xs text-ink-muted hover:text-red-400"
          >
            <X size={12} /> Clear
          </button>
        )}
      </div>

      {/* List */}
      <div className="space-y-2">
        {loading ? (
          <div className="text-center py-12 text-ink-muted">Loading...</div>
        ) : opps.length === 0 ? (
          <div className="text-center py-12 text-ink-muted">No opportunities found</div>
        ) : opps.map(opp => (
          <div
            key={opp.id}
            onClick={() => handleSelect(opp)}
            className={`bg-surface-card border rounded-xl p-4 cursor-pointer transition-colors hover:bg-surface-hover ${
              selected?.id === opp.id ? 'border-accent-blue/50' : 'border-surface-border'
            }`}
          >
            <div className="flex items-start gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  <PriorityBadge priority={opp.priority} />
                  <DealTypeBadge dealType={opp.deal_type} />
                  <ConfidenceBadge confidence={opp.confidence_level} />
                  <StageBadge stage={opp.stage} />
                </div>
                <div className="text-sm font-semibold text-ink-primary mb-0.5">
                  {opp.property_address || 'Property TBD'}
                  {opp.property_submarket && (
                    <span className="text-ink-muted font-normal text-xs ml-2">· {opp.property_submarket}</span>
                  )}
                </div>
                {opp.company_name && (
                  <div className="text-[11px] text-emerald-400 mb-1">↔ Tenant: {opp.company_name}</div>
                )}
                <div className="text-[12px] text-ink-secondary leading-relaxed line-clamp-2">{opp.thesis}</div>
              </div>
              <div className="flex-shrink-0 text-right">
                <ScoreBadge score={opp.score} size="lg" />
                {opp.estimated_commission && (
                  <div className="text-[11px] text-emerald-400 mt-0.5">
                    ${(opp.estimated_commission / 1000).toFixed(0)}K commission
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Side detail panel */}
      {selected && (
        <div className="fixed inset-y-0 right-0 w-[560px] bg-surface-card border-l border-surface-border shadow-2xl z-50 overflow-y-auto">
          <div className="p-5">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="flex flex-wrap items-center gap-2 mb-1">
                  <PriorityBadge priority={selected.priority} />
                  <DealTypeBadge dealType={selected.deal_type} />
                  <StageBadge stage={selected.stage} />
                </div>
                <div className="text-xs text-ink-muted mono">{selected.opportunity_id}</div>
              </div>
              <button onClick={() => setSelected(null)} className="text-ink-muted hover:text-ink-primary p-1">
                <X size={18} />
              </button>
            </div>

            {/* Stage picker */}
            <div className="flex items-center gap-2 mb-4 flex-wrap">
              <span className="text-[10px] text-ink-muted uppercase tracking-wider">Stage:</span>
              {STAGES.map(s => (
                <button
                  key={s}
                  disabled={updating === selected.id}
                  onClick={() => handleStageChange(selected, s)}
                  className={`text-[10px] px-2 py-0.5 rounded border font-semibold transition-colors ${
                    selected.stage === s ? STAGE_COLORS[s] : 'text-ink-muted border-surface-border hover:border-ink-muted'
                  }`}
                >
                  {s.replace('_', ' ')}
                </button>
              ))}
            </div>

            {/* Match breakdown */}
            <div className="bg-surface-muted rounded-xl p-4 mb-4">
              <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-3">Match Breakdown</div>
              <div className="space-y-2">
                <ScoreBar label="Prediction"     value={selected.prediction_score}         color="text-purple-400" />
                <ScoreBar label="Owner Behavior" value={selected.owner_behavior_score}     color="text-amber-400" />
                <ScoreBar label="Mispricing"     value={selected.mispricing_score}         color="text-blue-400" />
                <ScoreBar label="Tenant Signal"  value={selected.tenant_opportunity_score} color="text-emerald-400" />
              </div>
              <div className="mt-3 pt-3 border-t border-surface-border flex items-center justify-between">
                <span className="text-[10px] text-ink-muted">Composite Score</span>
                <ScoreBadge score={selected.score} size="lg" />
              </div>
            </div>

            {/* Property + Tenant side-by-side */}
            <div className="grid grid-cols-2 gap-3 mb-4">
              {/* Property card */}
              <div className="bg-surface-muted rounded-xl p-3">
                <div className="text-[9px] text-ink-muted uppercase tracking-wider mb-2">Property</div>
                {selProp ? (
                  <div className="space-y-1">
                    <div className="text-xs font-semibold text-ink-primary line-clamp-2">{selProp.address}</div>
                    <div className="text-[10px] text-ink-muted">{selProp.submarket} · {selProp.asset_class}</div>
                    <div className="text-[10px] text-ink-secondary">{selProp.total_sf.toLocaleString()} SF total</div>
                    {selProp.sf_avail != null && <div className="text-[10px] text-violet-400">{selProp.sf_avail.toLocaleString()} SF avail</div>}
                    {selProp.vacancy_pct != null && <div className="text-[10px] text-ink-muted">{selProp.vacancy_pct.toFixed(0)}% vacant</div>}
                    {selProp.in_place_rent_psf != null && <div className="text-[10px] text-ink-secondary">${selProp.in_place_rent_psf.toFixed(2)}/SF</div>}
                    <div className="text-[10px] text-ink-muted">{selProp.owner_name}</div>
                    {selProp.landlord_representative && (
                      <div className="text-[10px] text-amber-400">Rep: {selProp.landlord_representative}</div>
                    )}
                  </div>
                ) : (
                  <div className="space-y-1">
                    <div className="text-xs font-semibold text-ink-primary line-clamp-2">{selected.property_address || '—'}</div>
                    <div className="text-[10px] text-ink-muted">{selected.property_submarket}</div>
                    {!selected.property_ref && <div className="text-[10px] text-ink-muted italic">No property linked</div>}
                  </div>
                )}
              </div>

              {/* Tenant card */}
              <div className="bg-surface-muted rounded-xl p-3">
                <div className="text-[9px] text-ink-muted uppercase tracking-wider mb-2">Tenant</div>
                {selCompany ? (
                  <div className="space-y-1">
                    <div className="text-xs font-semibold text-ink-primary">{selCompany.name}</div>
                    <div className="text-[10px] text-ink-muted">{selCompany.industry}</div>
                    {selCompany.current_headcount != null && <div className="text-[10px] text-ink-secondary">{selCompany.current_headcount} employees</div>}
                    {selCompany.current_sf != null && <div className="text-[10px] text-ink-muted">{selCompany.current_sf.toLocaleString()} SF now</div>}
                    {selCompany.lease_expiry_months != null && (
                      <div className={`text-[10px] font-semibold ${selCompany.lease_expiry_months <= 12 ? 'text-red-400' : 'text-amber-400'}`}>
                        Lease expires {selCompany.lease_expiry_months}mo
                      </div>
                    )}
                    {selCompany.expansion_signal && (
                      <div className="text-[10px] text-emerald-400 font-bold">↑ Expansion signal</div>
                    )}
                    {selCompany.tenant_representative && (
                      <div className="text-[10px] text-ink-muted">Rep: {selCompany.tenant_representative}</div>
                    )}
                  </div>
                ) : (
                  <div className="space-y-1">
                    {selected.company_name ? (
                      <div className="text-xs font-semibold text-ink-primary">{selected.company_name}</div>
                    ) : (
                      <div className="text-[10px] text-ink-muted italic">No tenant linked</div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Thesis + next action */}
            <div className="bg-surface-muted rounded-xl p-3 mb-4">
              <div className="text-[9px] text-ink-muted uppercase tracking-wider mb-1.5">Thesis</div>
              <div className="text-xs text-ink-secondary leading-relaxed">{selected.thesis}</div>
              <div className="mt-2 text-[11px] text-amber-400 font-medium">{selected.next_action}</div>
            </div>

            {/* Draft Outreach CTA */}
            {selected.property_ref && selProp && (
              <button
                onClick={() => setOutreachModal(true)}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl
                           bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold transition-colors"
              >
                <MessageSquarePlus size={15} />
                Draft Outreach
              </button>
            )}

            {/* Financials */}
            {(selected.estimated_commission || selected.estimated_deal_value) && (
              <div className="mt-4 flex items-center gap-4 text-xs text-ink-muted">
                {selected.estimated_commission && (
                  <span>Est. commission: <span className="text-emerald-400 font-semibold">${(selected.estimated_commission / 1000).toFixed(0)}K</span></span>
                )}
                {selected.estimated_deal_value && (
                  <span>Deal value: <span className="text-ink-secondary font-semibold">${(selected.estimated_deal_value / 1_000_000).toFixed(2)}M</span></span>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Outreach modal — uses dominant score type from property */}
      {outreachModal && selected && selProp && (
        <OutreachDraftModal
          entity_type="property"
          property={selProp}
          outreach_type={selProp.dominant_score_type ?? 'tenant_match'}
          target_type={selProp.landlord_representative ? 'broker' : 'owner'}
          onClose={() => setOutreachModal(false)}
          onSaved={() => { setOutreachModal(false); load() }}
        />
      )}
    </div>
  )
}
