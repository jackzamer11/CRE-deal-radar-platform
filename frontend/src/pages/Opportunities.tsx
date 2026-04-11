import { useEffect, useState } from 'react'
import { Crosshair, Filter, X, PhoneCall, ChevronDown, ChevronRight } from 'lucide-react'
import { getOpportunities, updateStage } from '../api/client'
import type { OpportunityListOut, Stage } from '../types'
import { PriorityBadge, DealTypeBadge, ConfidenceBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'

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

export default function Opportunities() {
  const [opps, setOpps] = useState<OpportunityListOut[]>([])
  const [loading, setLoading] = useState(true)
  const [priority, setPriority] = useState('')
  const [dealType, setDealType] = useState('')
  const [stage, setStage] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [updating, setUpdating] = useState<number | null>(null)

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

  const handleStageChange = async (opp: OpportunityListOut, newStage: string) => {
    setUpdating(opp.id)
    try {
      await updateStage(opp.opportunity_id, newStage)
      await load()
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
      <div className="space-y-3">
        {loading ? (
          <div className="text-center py-12 text-ink-muted">Loading...</div>
        ) : opps.length === 0 ? (
          <div className="text-center py-12 text-ink-muted">No opportunities found</div>
        ) : opps.map(opp => (
          <div
            key={opp.id}
            className="bg-surface-card border border-surface-border rounded-xl overflow-hidden"
          >
            <div
              className="flex items-start gap-4 p-4 cursor-pointer hover:bg-surface-hover transition-colors"
              onClick={() => setExpanded(expanded === opp.id ? null : opp.id)}
            >
              {/* Left: badges + info */}
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
                  <div className="text-[11px] text-emerald-400 mb-1.5 flex items-center gap-1">
                    ↔ Tenant: {opp.company_name}
                  </div>
                )}

                <div className="text-[12px] text-ink-secondary leading-relaxed line-clamp-2 mb-2">
                  {opp.thesis}
                </div>

                <div className="text-[11px] text-amber-400 font-medium">{opp.next_action}</div>
              </div>

              {/* Right: score + financials */}
              <div className="flex-shrink-0 text-right space-y-1">
                <ScoreBadge score={opp.score} size="lg" />
                {opp.estimated_commission && (
                  <div className="text-[11px] text-emerald-400">
                    ${(opp.estimated_commission / 1000).toFixed(0)}K commission
                  </div>
                )}
                {opp.estimated_deal_value && (
                  <div className="text-[10px] text-ink-muted">
                    ${(opp.estimated_deal_value / 1_000_000).toFixed(2)}M deal
                  </div>
                )}
              </div>

              <div className="text-ink-muted flex-shrink-0">
                {expanded === opp.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </div>
            </div>

            {/* Expanded: call script + stage update */}
            {expanded === opp.id && (
              <div className="border-t border-surface-border">
                {/* Stage picker */}
                <div className="flex items-center gap-2 px-4 py-3 bg-surface-muted border-b border-surface-border">
                  <span className="text-[11px] text-ink-muted uppercase tracking-wider">Stage:</span>
                  {STAGES.map(s => (
                    <button
                      key={s}
                      disabled={updating === opp.id}
                      onClick={e => { e.stopPropagation(); handleStageChange(opp, s) }}
                      className={`text-[10px] px-2 py-1 rounded border font-semibold transition-colors ${
                        opp.stage === s
                          ? STAGE_COLORS[s]
                          : 'text-ink-muted border-surface-border hover:border-ink-muted'
                      }`}
                    >
                      {s.replace('_', ' ')}
                    </button>
                  ))}
                </div>

                {/* Signal breakdown */}
                <div className="px-4 py-3 grid grid-cols-4 gap-4 bg-surface-muted border-b border-surface-border">
                  {[
                    { label: 'Prediction', value: opp.prediction_score, color: 'text-purple-400' },
                    { label: 'Owner Behavior', value: opp.owner_behavior_score, color: 'text-amber-400' },
                    { label: 'Mispricing', value: opp.mispricing_score, color: 'text-blue-400' },
                    { label: 'Tenant Signal', value: opp.tenant_opportunity_score, color: 'text-emerald-400' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="text-center">
                      <div className={`text-lg font-bold mono ${color}`}>
                        {value != null ? value.toFixed(0) : '—'}
                      </div>
                      <div className="text-[10px] text-ink-muted">{label}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
