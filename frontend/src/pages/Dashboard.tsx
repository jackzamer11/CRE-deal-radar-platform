import { useEffect, useState } from 'react'
import {
  Zap, TrendingUp, Users, AlertTriangle,
  PhoneCall, ChevronRight, RefreshCw, Plus, Database, Upload, Building2,
} from 'lucide-react'
import { getDailyBriefing, runPipeline } from '../api/client'
import type { DailyBriefing, CallTarget, TenantMatchTarget, PropertyOut } from '../types'
import { PriorityBadge, DealTypeBadge, ConfidenceBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'
import AddPropertyModal from '../components/AddPropertyModal'
import BulkUploadModal from '../components/BulkUploadModal'
import CoStarImportModal from '../components/CoStarImportModal'

function StatCard({
  label, value, sub, icon: Icon, color,
}: {
  label: string; value: number | string; sub?: string
  icon: React.ElementType; color: string
}) {
  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-5">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-[11px] text-ink-muted uppercase tracking-widest font-semibold mb-2">{label}</div>
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
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-surface-border flex items-center justify-center">
          <span className="text-[11px] mono text-ink-muted font-bold">{target.rank}</span>
        </div>
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
            <div className="text-[11px] text-emerald-400 mb-1">↔ {target.company_name}</div>
          )}
          <div className="text-[12px] text-ink-secondary leading-relaxed line-clamp-2">{target.thesis}</div>
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

function TenantMatchCard({ target }: { target: TenantMatchTarget }) {
  const fmtRent = (n: number | null) => n != null ? `$${n.toFixed(2)}/SF` : '—'
  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4 flex items-center gap-4">
      <div className="flex-shrink-0 w-7 h-7 rounded-full bg-violet-500/15 flex items-center justify-center">
        <span className="text-[11px] mono text-violet-400 font-bold">{target.rank}</span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-ink-primary truncate">{target.address}</div>
        <div className="text-[11px] text-ink-muted">
          {target.submarket} · {target.asset_class} · {(target.total_sf / 1000).toFixed(0)}K SF
        </div>
        <div className="flex items-center gap-3 mt-1 text-[11px] text-ink-secondary">
          {target.vacancy_pct != null && (
            <span className={target.vacancy_pct > 25 ? 'text-red-400' : 'text-amber-400'}>
              {(100 - target.vacancy_pct).toFixed(0)}% vacant
            </span>
          )}
          {target.sf_avail != null && <span>{(target.sf_avail / 1000).toFixed(0)}K SF avail</span>}
          <span>{fmtRent(target.in_place_rent_psf)} in-place</span>
          <span className="text-ink-muted">{fmtRent(target.market_rent_psf)} market</span>
        </div>
      </div>
      <div className="flex-shrink-0 text-right">
        <ScoreBadge score={target.tenant_match_score} size="lg" />
        <div className="text-[10px] text-violet-400 mt-0.5">tenant match</div>
      </div>
    </div>
  )
}

function SectionHeader({
  icon: Icon, color, title, count,
}: { icon: React.ElementType; color: string; title: string; count: number }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <Icon size={16} className={color} />
      <h2 className="text-base font-bold text-ink-primary">{title} ({count})</h2>
      <div className="h-px flex-1 bg-surface-border" />
    </div>
  )
}

export default function Dashboard() {
  const [briefing, setBriefing]           = useState<DailyBriefing | null>(null)
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState<string | null>(null)
  const [showAddModal, setShowAddModal]   = useState(false)
  const [showBulkModal, setShowBulkModal] = useState(false)
  const [showCoStarModal, setShowCoStarModal] = useState(false)
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineStatus, setPipelineStatus]   = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setBriefing(await getDailyBriefing())
    } catch (e: unknown) {
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to load briefing')
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
  // Part 11: use today's actual date, not briefing_date from DB
  const todayLabel = new Date().toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  })

  const noContent = (
    briefing.immediate_deals.length === 0 &&
    briefing.high_priority_deals.length === 0 &&
    briefing.pre_market_predictions.length === 0 &&
    briefing.tenant_opportunities.length === 0 &&
    briefing.tenant_match_properties.length === 0
  )

  return (
    <div className="p-8 max-w-screen-xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-ink-primary">Daily Briefing</h1>
          <p className="text-ink-muted text-sm mt-0.5">
            {todayLabel} · Northern Virginia Office · Under $7M · 3K–30K SF
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
            onClick={() => setShowCoStarModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors"
          >
            <Upload size={13} /> Import CoStar
          </button>
          <button
            onClick={handleRunPipeline}
            disabled={pipelineRunning}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors disabled:opacity-50"
          >
            <Database size={13} className={pipelineRunning ? 'animate-pulse text-emerald-400' : ''} />
            {pipelineRunning ? 'Refreshing…' : 'Refresh Data'}
          </button>
          <button
            onClick={load}
            className="p-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary transition-colors"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* Pipeline status */}
      {pipelineStatus && (
        <div className="mb-6 px-4 py-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 text-xs flex items-center justify-between">
          <span>{pipelineStatus}</span>
          <button onClick={() => setPipelineStatus(null)} className="text-ink-muted hover:text-ink-primary ml-4">✕</button>
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard label="Immediate Deals"  value={stats.immediate_count}     sub="Call today"       icon={Zap}           color="text-red-400" />
        <StatCard label="High Priority"    value={stats.high_count}          sub="This week"        icon={AlertTriangle} color="text-amber-400" />
        <StatCard label="Pre-Market"       value={stats.pre_market_count}    sub="Predicted"        icon={TrendingUp}    color="text-purple-400" />
        <StatCard label="Tenant Driven"    value={stats.tenant_driven_count} sub="Active matches"   icon={Users}         color="text-emerald-400" />
      </div>

      {/* Portfolio row */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        <div className="bg-surface-card border border-surface-border rounded-xl p-4">
          <div className="text-[10px] text-ink-muted uppercase tracking-widest mb-2">Portfolio</div>
          <div className="flex items-center justify-between text-sm text-ink-secondary">
            <span>{stats.total_properties} Properties</span>
            <span>{stats.total_companies} Companies</span>
            <span>{stats.total_opportunities} Opps</span>
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

      {/* Modals */}
      {showAddModal && (
        <AddPropertyModal
          onClose={() => setShowAddModal(false)}
          onSaved={(_saved: PropertyOut) => { setShowAddModal(false); load() }}
        />
      )}
      {showBulkModal && <BulkUploadModal onClose={() => setShowBulkModal(false)} onDone={load} />}
      {showCoStarModal && <CoStarImportModal onClose={() => setShowCoStarModal(false)} onDone={load} />}

      {noContent ? (
        <div className="text-center py-16 text-ink-muted">
          <TrendingUp size={40} className="mx-auto mb-4 opacity-30" />
          <p className="text-sm">No opportunities generated yet.</p>
          <p className="text-xs mt-1">Run the pipeline from the sidebar to analyze all properties and companies.</p>
        </div>
      ) : (
        <div className="space-y-10">
          {/* Top 3 Immediate */}
          {briefing.immediate_deals.length > 0 && (
            <section>
              <SectionHeader icon={Zap} color="text-red-400" title="Top 3 Immediate — Call Today" count={briefing.immediate_deals.length} />
              <div className="space-y-3">
                {briefing.immediate_deals.map(t => <CallCard key={t.opportunity_id} target={t} showScript />)}
              </div>
            </section>
          )}

          {/* Top 10 High Priority */}
          {briefing.high_priority_deals.length > 0 && (
            <section>
              <SectionHeader icon={AlertTriangle} color="text-amber-400" title="Top 10 High Priority" count={briefing.high_priority_deals.length} />
              <div className="space-y-3">
                {briefing.high_priority_deals.map(t => <CallCard key={t.opportunity_id} target={t} showScript={false} />)}
              </div>
            </section>
          )}

          {/* Top 5 Pre-Market */}
          {briefing.pre_market_predictions.length > 0 && (
            <section>
              <SectionHeader icon={TrendingUp} color="text-purple-400" title="Top 5 Pre-Market Predictions" count={briefing.pre_market_predictions.length} />
              <div className="space-y-3">
                {briefing.pre_market_predictions.map(t => <CallCard key={t.opportunity_id} target={t} showScript />)}
              </div>
            </section>
          )}

          {/* Top 10 Tenant Matches (property-side) */}
          {briefing.tenant_match_properties.length > 0 && (
            <section>
              <SectionHeader icon={Building2} color="text-violet-400" title="Top 10 Tenant Match Properties" count={briefing.tenant_match_properties.length} />
              <div className="space-y-3">
                {briefing.tenant_match_properties.map(t => <TenantMatchCard key={t.property_id} target={t} />)}
              </div>
            </section>
          )}

          {/* Tenant-Driven Opportunities */}
          {briefing.tenant_opportunities.length > 0 && (
            <section>
              <SectionHeader icon={Users} color="text-emerald-400" title="Tenant-Driven Opportunities" count={briefing.tenant_opportunities.length} />
              <div className="space-y-3">
                {briefing.tenant_opportunities.map(t => <CallCard key={t.opportunity_id} target={t} showScript />)}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  )
}
