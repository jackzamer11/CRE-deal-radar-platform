import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Crosshair, Filter, X, PhoneCall, Building2, Users, MessageSquarePlus } from 'lucide-react'
import { getOpportunities, updateStage, getProperty, getCompany } from '../api/client'
import type { OpportunityListOut, Stage, PropertyOut, CompanyOut } from '../types'
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

function ScoreGrid({ opp }: { opp: OpportunityListOut }) {
  return (
    <div className="grid grid-cols-4 gap-2">
      {[
        { label: 'Prediction',    value: opp.prediction_score,         color: 'text-purple-400' },
        { label: 'Owner Signal',  value: opp.owner_behavior_score,     color: 'text-amber-400' },
        { label: 'Mispricing',    value: opp.mispricing_score,         color: 'text-blue-400' },
        { label: 'Tenant Signal', value: opp.tenant_opportunity_score, color: 'text-emerald-400' },
      ].map(({ label, value, color }) => (
        <div key={label} className="bg-surface-muted rounded-lg p-2 text-center">
          <div className={`text-base font-bold mono ${color}`}>
            {value != null ? value.toFixed(0) : '—'}
          </div>
          <div className="text-[9px] text-ink-muted mt-0.5">{label}</div>
        </div>
      ))}
    </div>
  )
}

export default function Opportunities() {
  const navigate = useNavigate()
  const [opps, setOpps] = useState<OpportunityListOut[]>([])
  const [loading, setLoading] = useState(true)
  const [priority, setPriority] = useState('')
  const [dealType, setDealType] = useState('')
  const [stage, setStage] = useState('')
  const [selected, setSelected] = useState<OpportunityListOut | null>(null)
  const [selectedProp, setSelectedProp] = useState<PropertyOut | null>(null)
  const [selectedCompany, setSelectedCompany] = useState<CompanyOut | null>(null)
  const [outreachModal, setOutreachModal] = useState<{ type: string; targetType?: string } | null>(null)
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

  const openPanel = async (opp: OpportunityListOut) => {
    setSelected(opp)
    setSelectedProp(null)
    setSelectedCompany(null)
    if (opp.property_str_id) {
      getProperty(opp.property_str_id).then(setSelectedProp).catch(() => {})
    }
    if (opp.company_str_id) {
      getCompany(opp.company_str_id).then(setSelectedCompany).catch(() => {})
    }
  }

  const handleStageChange = async (opp: OpportunityListOut, newStage: string) => {
    setUpdating(opp.id)
    try {
      await updateStage(opp.opportunity_id, newStage)
      await load()
      if (selected?.id === opp.id) {
        setSelected(prev => prev ? { ...prev, stage: newStage as Stage } : prev)
      }
    } finally {
      setUpdating(null)
    }
  }

  const bestOutreachType = (opp: OpportunityListOut): { type: string; targetType: string } => {
    if (opp.deal_type === 'TENANT_DRIVEN') {
      const hasLandlordRep = selectedProp?.landlord_representative
      return { type: 'tenant_match', targetType: hasLandlordRep ? 'broker' : 'owner' }
    }
    if (opp.deal_type === 'ACTIVE_MISPRICED') {
      const hasSalesContact = selectedProp?.sales_contact
      return { type: 'acquisition', targetType: hasSalesContact ? 'sales_broker' : 'owner' }
    }
    return { type: 'listing_rep', targetType: 'owner' }
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
            onClick={() => openPanel(opp)}
            className={`bg-surface-card border rounded-xl p-4 cursor-pointer hover:bg-surface-hover transition-colors ${
              selected?.id === opp.id ? 'border-accent-blue/40' : 'border-surface-border'
            }`}
          >
            <div className="flex items-start gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2 mb-1.5">
                  <PriorityBadge priority={opp.priority} />
                  <DealTypeBadge dealType={opp.deal_type} />
                  <ConfidenceBadge confidence={opp.confidence_level} />
                  <StageBadge stage={opp.stage} />
                </div>
                <div className="text-sm font-semibold text-ink-primary mb-0.5 truncate">
                  {opp.property_address || 'Property TBD'}
                  {opp.property_submarket && (
                    <span className="text-ink-muted font-normal text-xs ml-2">· {opp.property_submarket}</span>
                  )}
                </div>
                {opp.company_name && (
                  <div className="text-[11px] text-emerald-400 mb-1">↔ Tenant: {opp.company_name}</div>
                )}
                <div className="text-[12px] text-ink-secondary leading-relaxed line-clamp-2">
                  {opp.thesis}
                </div>
              </div>
              <div className="flex-shrink-0 text-right space-y-1">
                <ScoreBadge score={opp.score} size="lg" />
                {opp.estimated_commission && (
                  <div className="text-[11px] text-emerald-400">
                    ${(opp.estimated_commission / 1000).toFixed(0)}K est.
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Side panel */}
      {selected && (
        <div className="fixed inset-y-0 right-0 w-[480px] bg-surface-card border-l border-surface-border shadow-2xl z-50 overflow-y-auto">
          <div className="p-5 space-y-4">
            {/* Header */}
            <div className="flex items-start justify-between">
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

            {/* Thesis */}
            <div className="bg-surface-muted rounded-lg p-3">
              <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-1">Thesis</div>
              <div className="text-xs text-ink-secondary leading-relaxed">{selected.thesis}</div>
              <div className="mt-2 text-[11px] text-amber-400 font-medium">{selected.next_action}</div>
            </div>

            {/* Score breakdown */}
            <ScoreGrid opp={selected} />

            {/* Property + Company cards */}
            <div className={`grid gap-3 ${selected.property_str_id && selected.company_str_id ? 'grid-cols-2' : 'grid-cols-1'}`}>
              {/* Property card */}
              {(selected.property_address || selectedProp) && (
                <button
                  onClick={() => selected.property_str_id && navigate(`/properties?selected=${selected.property_str_id}`)}
                  className="bg-surface-muted rounded-lg p-3 text-left hover:bg-surface-hover transition-colors border border-surface-border"
                >
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <Building2 size={11} className="text-accent-blue flex-shrink-0" />
                    <div className="text-[10px] text-accent-blue uppercase tracking-wider font-semibold">Property</div>
                  </div>
                  <div className="text-xs font-semibold text-ink-primary leading-snug mb-0.5">
                    {selected.property_submarket || '—'}
                  </div>
                  {selectedProp ? (
                    <>
                      <div className="text-[10px] text-ink-muted">{selectedProp.asset_class} · {(selectedProp.total_sf / 1000).toFixed(0)}K SF</div>
                      {selectedProp.vacancy_pct != null && (
                        <div className="text-[10px] text-ink-secondary mt-1">{selectedProp.vacancy_pct.toFixed(0)}% vacant</div>
                      )}
                      <div className="mt-1.5">
                        <ScoreBadge score={selectedProp.signal_score} size="sm" showBar />
                        <div className="text-[9px] text-ink-muted">signal</div>
                      </div>
                    </>
                  ) : (
                    <div className="text-[10px] text-ink-muted">{selected.property_address}</div>
                  )}
                </button>
              )}

              {/* Company card */}
              {(selected.company_name || selectedCompany) && (
                <button
                  onClick={() => selected.company_str_id && navigate(`/companies?selected=${selected.company_str_id}`)}
                  className="bg-surface-muted rounded-lg p-3 text-left hover:bg-surface-hover transition-colors border border-surface-border"
                >
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <Users size={11} className="text-emerald-400 flex-shrink-0" />
                    <div className="text-[10px] text-emerald-400 uppercase tracking-wider font-semibold">Tenant</div>
                  </div>
                  {selectedCompany ? (
                    <>
                      <div className="text-xs font-semibold text-ink-primary mb-0.5">{selectedCompany.industry}</div>
                      <div className="text-[10px] text-ink-muted">
                        {selectedCompany.current_headcount != null ? `${selectedCompany.current_headcount} HC` : '—'}
                        {selectedCompany.estimated_sf_needed ? ` · ${selectedCompany.estimated_sf_needed.toLocaleString()} SF needed` : ''}
                      </div>
                      {selectedCompany.lease_expiry_months != null && (
                        <div className="text-[10px] text-amber-400 mt-1">Lease: {selectedCompany.lease_expiry_months}mo</div>
                      )}
                      <div className="mt-1.5">
                        <ScoreBadge score={selectedCompany.opportunity_score} size="sm" showBar />
                        <div className="text-[9px] text-ink-muted">opp score</div>
                      </div>
                    </>
                  ) : (
                    <div className="text-xs text-ink-secondary">{selected.company_name}</div>
                  )}
                </button>
              )}
            </div>

            {/* Stage picker */}
            <div>
              <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Update Stage</div>
              <div className="flex flex-wrap gap-1.5">
                {STAGES.map(s => (
                  <button
                    key={s}
                    disabled={updating === selected.id}
                    onClick={() => handleStageChange(selected, s)}
                    className={`text-[10px] px-2 py-1 rounded border font-semibold transition-colors ${
                      selected.stage === s
                        ? STAGE_COLORS[s]
                        : 'text-ink-muted border-surface-border hover:border-ink-muted'
                    }`}
                  >
                    {s.replace('_', ' ')}
                  </button>
                ))}
              </div>
            </div>

            {/* Call script */}
            {selected.deal_type !== 'TENANT_DRIVEN' && (
              <div className="bg-surface-muted rounded-lg p-3">
                <div className="flex items-center gap-2 mb-2">
                  <PhoneCall size={11} className="text-accent-blue" />
                  <div className="text-[10px] text-accent-blue uppercase tracking-wider font-semibold">Next Action</div>
                </div>
                <div className="text-xs text-ink-secondary">{selected.next_action}</div>
              </div>
            )}

            {/* Draft Outreach button — only when we have a property */}
            {selectedProp && (() => {
              const { type, targetType } = bestOutreachType(selected)
              return (
                <button
                  onClick={() => setOutreachModal({ type, targetType })}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg
                             bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold transition-colors"
                >
                  <MessageSquarePlus size={13} />
                  Draft Outreach
                  <span className="opacity-75 capitalize">({type.replace('_', ' ')})</span>
                </button>
              )
            })()}
          </div>
        </div>
      )}

      {/* Outreach modal */}
      {outreachModal && selectedProp && (
        <OutreachDraftModal
          entity_type="property"
          property={selectedProp}
          outreach_type={outreachModal.type}
          target_type={outreachModal.targetType}
          onClose={() => setOutreachModal(null)}
          onSaved={() => setOutreachModal(null)}
        />
      )}
    </div>
  )
}
