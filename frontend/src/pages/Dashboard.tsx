import { useEffect, useState } from 'react'
import {
  Zap, TrendingUp, Users, AlertTriangle,
  PhoneCall, ChevronRight, RefreshCw, Plus, Database, Upload
} from 'lucide-react'
import { getDailyBriefing, runPipeline } from '../api/client'
import type { DailyBriefing, CallTarget, PropertyOut } from '../types'
import { PriorityBadge, DealTypeBadge, ConfidenceBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'
import AddPropertyModal from '../components/AddPropertyModal'
import BulkUploadModal from '../components/BulkUploadModal'

function StatCard({
  label, value, sub, icon: Icon, color,
}: {
  label: string
  value: number | string
  sub?: string
  icon: React.ElementType
  color: string
}) {
  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-5">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-[11px] text-ink-muted uppercase tracking-widest font-semibold mb-2">
            {label}
          </div>
          <div className={`text-3xl font-bold mono ${color}`}>{value}</div>
          {sub && <div className="text-[11px] text-ink-muted mt-1">{sub}</div>}
        </div>
        <div className={`p-2.5 rounded-lg ${color.replace('text-', 'bg-').replace('-400', '-500/15')}`}>
          <Icon size={18} className={color} />
        </div>
      </div>
    </div>
  )
}

function CallCard({ target, showScript }: { target: CallTarget; showScript: boolean }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl overflow-hidden">
      <div
        className="flex items-start gap-4 p-4 cursor-pointer hover:bg-surface-hover transition-colors"
        onClick={() => setExpanded(e => !e)}
      >
        {/* Rank */}
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-surface-border flex items-center justify-center">
          <span className="text-[11px] mono text-ink-muted font-bold">{target.rank}</span>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1.5">
            <PriorityBadge priority={target.priority} />
            <DealTypeBadge dealType={target.deal_type} />
            <ConfidenceBadge confidence={target.confidence_level} />
          </div>

          <div className="text-sm font-semibold text-ink-primary mb-0.5 truncate">
            {target.property_address || 'Off-Market Property'}
          </div>

          {target.company_name && (
            <div className="text-[11px] text-emerald-400 mb-1">
              ↔ {target.company_name}
            </div>
          )}

          <div className="text-[12px] text-ink-secondary leading-relaxed line-clamp-2">
            {target.thesis}
          </div>

          <div className="flex items-center gap-4 mt-2">
            <div className="text-[11px] text-amber-400 font-medium">{target.next_action}</div>
            {target.estimated_commission && (
              <div className="text-[11px] text-ink-muted">
                Est. commission: <span className="text-emerald-400 font-semibold">
                  ${(target.estimated_commission / 1000).toFixed(0)}K
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Score */}
        <div className="flex-shrink-0 text-right">
          <ScoreBadge score={target.score} size="lg" />
          <div className="text-[10px] text-ink-muted mt-0.5">signal</div>
        </div>

        <ChevronRight
          size={16}
          className={`text-ink-muted flex-shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
        />
      </div>

      {expanded && showScript && target.call_script && (
        <div className="border-t border-surface-border px-4 py-4 bg-surface-muted">
          <div className="flex items-center gap-2 mb-3">
            <PhoneCall size={13} className="text-accent-blue" />
            <span className="text-[11px] font-bold uppercase tracking-widest text-accent-blue">Call Script</span>
          </div>
          <pre className="text-[11px] text-ink-secondary leading-relaxed whitespace-pre-wrap font-mono">
            {target.call_script}
          </pre>
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const [briefing, setBriefing] = useState<DailyBriefing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showBulkModal, setShowBulkModal] = useState(false)
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineStatus, setPipelineStatus] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getDailyBriefing()
      setBriefing(data)
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to load briefing')
    } finally {
      setLoading(false)
    }
  }

  const handleRunPipeline = async () => {
    setPipelineRunning(true)
    setPipelineStatus(null)
    try {
      const result = await runPipeline()
      setPipelineStatus(
        `Done — ${result.properties_refreshed} properties, ${result.new_opportunities} new deals (${result.elapsed_seconds}s)`
      )
      await load()
    } catch {
      setPipelineStatus('Pipeline failed — check server logs')
    } finally {
      setPipelineRunning(false)
    }
  }

  useEffect(() => { load() }, [])

  if (loading) return (
    <div className="flex items-center justify-center h-screen">
      <RefreshCw size={20} className="animate-spin text-accent-blue" />
    </div>
  )

  if (error || !briefing) return (
    <div className="p-8 text-red-400">
      {error || 'No data available. Run the seed script and pipeline first.'}
    </div>
  )

  const { stats } = briefing

  return (
    <div className="p-8 max-w-screen-xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-ink-primary">Daily Briefing</h1>
          <p className="text-ink-muted text-sm mt-0.5">
            {new Date(briefing.briefing_date).toLocaleDateString('en-US', {
              weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
            })} · Northern Virginia Office · Under $10M · 3K–30K SF
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-blue text-white
                       text-xs font-semibold hover:bg-accent-blueDim transition-colors"
          >
            <Plus size={13} /> Add Property
          </button>
          <button
            onClick={() => setShowBulkModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors"
          >
            <Upload size={13} /> Bulk Upload
          </button>
          <button
            onClick={handleRunPipeline}
            disabled={pipelineRunning}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors disabled:opacity-50"
            title="Refresh Arlington + Fairfax county data and recalculate all signals"
          >
            <Database size={13} className={pipelineRunning ? 'animate-pulse text-emerald-400' : ''} />
            {pipelineRunning ? 'Refreshing…' : 'Refresh Data'}
          </button>
          <button
            onClick={load}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs transition-colors"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* Pipeline status toast */}
      {pipelineStatus && (
        <div className="mb-6 px-4 py-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 text-xs flex items-center justify-between">
          <span>{pipelineStatus}</span>
          <button onClick={() => setPipelineStatus(null)} className="text-ink-muted hover:text-ink-primary ml-4">✕</button>
        </div>
      )}

      {/* Add Property Modal */}
      {showAddModal && (
        <AddPropertyModal
          onClose={() => setShowAddModal(false)}
          onSaved={(_saved: PropertyOut) => { setShowAddModal(false); load() }}
        />
      )}

      {showBulkModal && (
        <BulkUploadModal
          onClose={() => setShowBulkModal(false)}
          onDone={load}
        />
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Immediate Deals"
          value={stats.immediate_count}
          sub="Call today"
          icon={Zap}
          color="text-red-400"
        />
        <StatCard
          label="High Priority"
          value={stats.high_count}
          sub="This week"
          icon={AlertTriangle}
          color="text-amber-400"
        />
        <StatCard
          label="Pre-Market"
          value={stats.pre_market_count}
          sub="Predicted"
          icon={TrendingUp}
          color="text-purple-400"
        />
        <StatCard
          label="Tenant Driven"
          value={stats.tenant_driven_count}
          sub="Active matches"
          icon={Users}
          color="text-emerald-400"
        />
      </div>

      {/* Secondary stats */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        <div className="bg-surface-card border border-surface-border rounded-xl p-4">
          <div className="text-[10px] text-ink-muted uppercase tracking-widest mb-2">Portfolio</div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink-secondary">{stats.total_properties} Properties</span>
            <span className="text-sm text-ink-secondary">{stats.total_companies} Companies</span>
            <span className="text-sm text-ink-secondary">{stats.total_opportunities} Opportunities</span>
          </div>
        </div>
        <div className="bg-surface-card border border-surface-border rounded-xl p-4">
          <div className="text-[10px] text-ink-muted uppercase tracking-widest mb-2">Avg Signal Score</div>
          <ScoreBadge score={stats.avg_signal_score} size="lg" showBar />
        </div>
        <div className="bg-surface-card border border-surface-border rounded-xl p-4">
          <div className="text-[10px] text-ink-muted uppercase tracking-widest mb-2">Avg Prediction Score</div>
          <ScoreBadge score={stats.avg_prediction_score} size="lg" showBar />
        </div>
      </div>

      {/* Top 10 Immediate Deals */}
      {briefing.immediate_deals.length > 0 && (
        <section className="mb-10">
          <div className="flex items-center gap-3 mb-4">
            <Zap size={16} className="text-red-400" />
            <h2 className="text-base font-bold text-ink-primary">
              Top {briefing.immediate_deals.length} Immediate Deals — Call Today
            </h2>
            <div className="h-px flex-1 bg-surface-border" />
          </div>
          <div className="space-y-3">
            {briefing.immediate_deals.map(t => (
              <CallCard key={t.opportunity_id} target={t} showScript />
            ))}
          </div>
        </section>
      )}

      {/* Pre-Market Predictions */}
      {briefing.pre_market_predictions.length > 0 && (
        <section className="mb-10">
          <div className="flex items-center gap-3 mb-4">
            <TrendingUp size={16} className="text-purple-400" />
            <h2 className="text-base font-bold text-ink-primary">
              Top {briefing.pre_market_predictions.length} Pre-Market Predictions
            </h2>
            <div className="h-px flex-1 bg-surface-border" />
          </div>
          <div className="space-y-3">
            {briefing.pre_market_predictions.map(t => (
              <CallCard key={t.opportunity_id} target={t} showScript />
            ))}
          </div>
        </section>
      )}

      {/* Tenant-Driven Opportunities */}
      {briefing.tenant_opportunities.length > 0 && (
        <section className="mb-10">
          <div className="flex items-center gap-3 mb-4">
            <Users size={16} className="text-emerald-400" />
            <h2 className="text-base font-bold text-ink-primary">
              Top {briefing.tenant_opportunities.length} Tenant-Driven Opportunities
            </h2>
            <div className="h-px flex-1 bg-surface-border" />
          </div>
          <div className="space-y-3">
            {briefing.tenant_opportunities.map(t => (
              <CallCard key={t.opportunity_id} target={t} showScript />
            ))}
          </div>
        </section>
      )}

      {briefing.immediate_deals.length === 0 &&
       briefing.pre_market_predictions.length === 0 &&
       briefing.tenant_opportunities.length === 0 && (
        <div className="text-center py-16 text-ink-muted">
          <TrendingUp size={40} className="mx-auto mb-4 opacity-30" />
          <p className="text-sm">No opportunities generated yet.</p>
          <p className="text-xs mt-1">Run the pipeline from the sidebar to analyze all properties and companies.</p>
        </div>
      )}
    </div>
  )
}
