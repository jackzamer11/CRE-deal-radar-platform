import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Zap, TrendingUp, AlertTriangle,
  RefreshCw, Plus, Database, Upload, Building2, Target, MessageSquarePlus,
} from 'lucide-react'
import { getDailyBriefing, runPipeline, getProperty } from '../api/client'
import type {
  DailyBriefing, TenantMatchAction, AcquisitionTarget,
  PropertyOut, MatchedTenant,
} from '../types'
import ScoreBadge from '../components/ScoreBadge'
import AddPropertyModal from '../components/AddPropertyModal'
import BulkUploadModal from '../components/BulkUploadModal'
import CoStarImportModal from '../components/CoStarImportModal'
import OutreachDraftModal from '../components/OutreachDraftModal'

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

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'CONTACTED' ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' :
    status === 'FOLLOW_UP' ? 'bg-amber-500/15   text-amber-400   border-amber-500/30' :
                             'bg-surface-muted   text-ink-muted   border-surface-border'
  const label =
    status === 'CONTACTED' ? 'CONTACTED' :
    status === 'FOLLOW_UP' ? 'FOLLOW UP' :
                             'NOT CONTACTED'
  return (
    <span className={`text-[9px] px-2 py-0.5 rounded border font-bold tracking-wider ${cls}`}>
      {label}
    </span>
  )
}

function TenantActionRow({
  action, onDraft, onNavigate,
}: {
  action: TenantMatchAction
  onDraft: (a: TenantMatchAction) => void
  onNavigate: (a: TenantMatchAction) => void
}) {
  const typeLabel = action.outreach_type === 'for_sale_vacancy'
    ? 'For Sale + Vacancy'
    : 'Tenant Match'
  const targetLabel = action.target_type === 'broker'
    ? 'Broker'
    : action.target_type === 'sales_broker' ? 'Sales Broker' : 'Owner'
  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4 flex items-start gap-4">
      <div
        className="flex-1 min-w-0 cursor-pointer"
        onClick={() => onNavigate(action)}
        role="button"
      >
        <div className="flex items-center gap-2 flex-wrap mb-1.5">
          <StatusBadge status={action.contact_status} />
          <span className="text-[9px] px-2 py-0.5 rounded border font-bold bg-violet-500/15 text-violet-400 border-violet-500/30">
            {typeLabel}
          </span>
          <span className="text-[9px] px-2 py-0.5 rounded border font-semibold text-ink-muted border-surface-border">
            → {targetLabel}
          </span>
        </div>
        <div className="text-sm font-semibold text-ink-primary truncate">
          {action.address}
        </div>
        <div className="text-[11px] text-emerald-400 mt-0.5 truncate">
          ↔ {action.tenant_name} <span className="text-ink-muted">· {action.tenant_industry}</span>
        </div>
        <div className="flex items-center gap-3 mt-1.5 text-[11px] text-ink-secondary flex-wrap">
          <span>{action.submarket}</span>
          {action.sf_avail != null && <span>{(action.sf_avail / 1000).toFixed(0)}K SF avail</span>}
          <span>{(action.tenant_sf_needed / 1000).toFixed(0)}K SF needed</span>
          {action.lease_expiry_months != null && (
            <span className={action.lease_expiry_months <= 12 ? 'text-amber-400' : 'text-ink-muted'}>
              {action.lease_expiry_months}mo to expiry
            </span>
          )}
        </div>
      </div>
      <div className="flex-shrink-0 flex flex-col items-end gap-2">
        <ScoreBadge score={action.match_score} size="lg" />
        <button
          onClick={(e) => { e.stopPropagation(); onDraft(action) }}
          className="flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700
                     text-white font-semibold transition-colors"
        >
          <MessageSquarePlus size={11} />
          Draft Outreach
        </button>
      </div>
    </div>
  )
}

function AcquisitionRow({
  target, onDraft, onNavigate,
}: {
  target: AcquisitionTarget
  onDraft: (t: AcquisitionTarget) => void
  onNavigate: (t: AcquisitionTarget) => void
}) {
  const targetLabel = target.target_type === 'sales_broker' ? 'Sales Broker' : 'Owner'
  const fmtPrice = (n: number | null) => n != null
    ? n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2)}M` : `$${(n / 1_000).toFixed(0)}K`
    : '—'
  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4 flex items-start gap-4">
      <div
        className="flex-1 min-w-0 cursor-pointer"
        onClick={() => onNavigate(target)}
        role="button"
      >
        <div className="flex items-center gap-2 flex-wrap mb-1.5">
          <StatusBadge status={target.contact_status} />
          <span className="text-[9px] px-2 py-0.5 rounded border font-bold bg-emerald-500/15 text-emerald-400 border-emerald-500/30">
            Acquisition
          </span>
          <span className="text-[9px] px-2 py-0.5 rounded border font-semibold text-ink-muted border-surface-border">
            → {targetLabel}
          </span>
          {target.dominant_signal && (
            <span className="text-[9px] px-2 py-0.5 rounded border font-semibold bg-amber-500/10 text-amber-400 border-amber-500/30">
              {target.dominant_signal}
            </span>
          )}
        </div>
        <div className="text-sm font-semibold text-ink-primary truncate">{target.address}</div>
        <div className="text-[11px] text-ink-muted">
          {target.submarket} · {(target.total_sf / 1000).toFixed(0)}K SF
          {target.year_built ? ` · Built ${target.year_built}` : ''}
        </div>
        <div className="flex items-center gap-3 mt-1.5 text-[11px] text-ink-secondary flex-wrap">
          <span>Owner: <span className="text-ink-secondary font-medium">{target.owner_name}</span></span>
          {target.vacancy_pct != null && (
            <span className={target.vacancy_pct > 25 ? 'text-red-400' : 'text-amber-400'}>
              {target.vacancy_pct.toFixed(0)}% vacant
            </span>
          )}
          {target.asking_price != null && (
            <span>Asking <span className="text-ink-secondary font-medium">{fmtPrice(target.asking_price)}</span></span>
          )}
          {target.estimated_value != null && (
            <span className="text-ink-muted">Est. {fmtPrice(target.estimated_value)}</span>
          )}
        </div>
      </div>
      <div className="flex-shrink-0 flex flex-col items-end gap-2">
        <ScoreBadge score={target.signal_score} size="lg" />
        <button
          onClick={(e) => { e.stopPropagation(); onDraft(target) }}
          className="flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700
                     text-white font-semibold transition-colors"
        >
          <MessageSquarePlus size={11} />
          Draft Outreach
        </button>
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
  const navigate = useNavigate()
  const [briefing, setBriefing]           = useState<DailyBriefing | null>(null)
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState<string | null>(null)
  const [showAddModal, setShowAddModal]   = useState(false)
  const [showBulkModal, setShowBulkModal] = useState(false)
  const [showCoStarModal, setShowCoStarModal] = useState(false)
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineStatus, setPipelineStatus]   = useState<string | null>(null)

  const [draftTarget, setDraftTarget] = useState<{
    prop: PropertyOut
    type: string
    targetType: string
    pairCompanyId?: string
    pairTenantName?: string
    tenantContext?: string
    matched: MatchedTenant[]
  } | null>(null)

  const navTenantAction = (a: TenantMatchAction) => {
    navigate(`/properties?selected=${a.property_id}`)
  }
  const navAcquisition = (t: AcquisitionTarget) => {
    navigate(`/properties?selected=${t.property_id}`)
  }

  const handleTenantDraft = async (a: TenantMatchAction) => {
    try {
      const prop = await getProperty(a.property_id)
      const tCtx = `Industry: ${a.tenant_industry}; Headcount: ${a.tenant_headcount ?? 'N/A'}; SF Needed: ${a.tenant_sf_needed.toLocaleString()}; Lease Expiry: ${a.lease_expiry_months ?? 'N/A'}mo`
      setDraftTarget({
        prop,
        type: a.outreach_type,
        targetType: a.target_type,
        pairCompanyId: a.tenant_company_id,
        pairTenantName: a.tenant_name,
        tenantContext: tCtx,
        matched: prop.matched_tenants ?? [],
      })
    } catch {
      navigate(`/properties?selected=${a.property_id}`)
    }
  }

  const handleAcqDraft = async (t: AcquisitionTarget) => {
    try {
      const prop = await getProperty(t.property_id)
      setDraftTarget({
        prop,
        type: 'acquisition',
        targetType: t.target_type,
        matched: prop.matched_tenants ?? [],
      })
    } catch {
      navigate(`/properties?selected=${t.property_id}`)
    }
  }

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

  const tenantActions = briefing.tenant_match_actions ?? []
  const acqTargets    = briefing.acquisition_targets ?? []

  const noContent = tenantActions.length === 0 && acqTargets.length === 0

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
        <StatCard label="Tenant Match Actions" value={tenantActions.length}   sub="Section A queue"  icon={Building2}     color="text-violet-400" />
        <StatCard label="Acquisition Targets"  value={acqTargets.length}      sub="Signal ≥ 40"      icon={Zap}           color="text-emerald-400" />
        <StatCard label="Properties"           value={stats.total_properties} sub="In portfolio"     icon={TrendingUp}    color="text-purple-400" />
        <StatCard label="Avg Signal Score"     value={stats.avg_signal_score.toFixed(0)} sub="Portfolio avg" icon={AlertTriangle} color="text-amber-400" />
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
          {/* Section A — Tenant Match Actions (sorted by lease expiry ASC) */}
          <section>
            <SectionHeader
              icon={Building2}
              color="text-violet-400"
              title="Section A — Tenant Match Actions"
              count={tenantActions.length}
            />
            {tenantActions.length === 0 ? (
              <div className="text-xs text-ink-muted px-2">No tenant-match actions queued.</div>
            ) : (
              <div className="space-y-3">
                {tenantActions.map(a => (
                  <TenantActionRow
                    key={`${a.property_id}-${a.tenant_company_id}-${a.outreach_type}`}
                    action={a}
                    onDraft={handleTenantDraft}
                    onNavigate={navTenantAction}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Section B — Acquisition Targets (signal_score >= 40, sorted DESC) */}
          <section>
            <SectionHeader
              icon={Target}
              color="text-emerald-400"
              title="Section B — Acquisition Targets"
              count={acqTargets.length}
            />
            {acqTargets.length === 0 ? (
              <div className="text-xs text-ink-muted px-2">No acquisition targets meet the threshold.</div>
            ) : (
              <div className="space-y-3">
                {acqTargets.map(t => (
                  <AcquisitionRow
                    key={t.property_id}
                    target={t}
                    onDraft={handleAcqDraft}
                    onNavigate={navAcquisition}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {/* Outreach Draft Modal launched from queue rows */}
      {draftTarget && (() => {
        const p = draftTarget.prop
        const tt = draftTarget.targetType
        const looksLikeEmail = (s: string | null | undefined) => !!s && /@/.test(s)
        const recipientName =
          tt === 'broker'        ? (p.landlord_representative ?? '') :
          tt === 'sales_broker'  ? '' :
                                   (p.owner_name ?? '')
        const recipientEmail =
          tt === 'broker'        ? (looksLikeEmail(p.landlord_rep_contact) ? (p.landlord_rep_contact as string) : '') :
          tt === 'sales_broker'  ? (looksLikeEmail(p.sales_contact)        ? (p.sales_contact as string)        : '') :
                                   (p.owner_email ?? '')
        return (
          <OutreachDraftModal
            entity_type="property"
            property={p}
            outreach_type={draftTarget.type}
            target_type={draftTarget.targetType}
            tenant_context={draftTarget.tenantContext}
            pair_company_id={draftTarget.pairCompanyId}
            pair_tenant_name={draftTarget.pairTenantName}
            recipient_name={recipientName}
            recipient_email={recipientEmail}
            matched_tenants={draftTarget.matched}
            onClose={() => setDraftTarget(null)}
            onSaved={() => { setDraftTarget(null); load() }}
          />
        )
      })()}
    </div>
  )
}
