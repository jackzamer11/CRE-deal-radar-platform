import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Zap, TrendingUp, AlertTriangle,
  RefreshCw, Plus, Database, Upload, Building2, Target, MessageSquarePlus, Clock, X, RotateCcw,
  History, ClipboardList,
} from 'lucide-react'
import { getDailyBriefing, runPipeline, getProperty, importLeaseActivity, unsnoozeProperty, getReEngage } from '../api/client'
import type { CoStarImportResult } from '../api/client'
import type {
  DailyBriefing, TenantMatchAction, AcquisitionTarget,
  PropertyOut, MatchedTenant, ActivityLog, ActivityStage,
} from '../types'
import SnoozeModal from '../components/SnoozeModal'
import ScoreBadge from '../components/ScoreBadge'
import AddPropertyModal from '../components/AddPropertyModal'
import LeaseCompsModal from '../components/LeaseCompsModal'
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

const STAGE_CHIP: Record<ActivityStage, string> = {
  'Sent':           'bg-blue-500/15 text-blue-400 border-blue-500/30',
  'Replied':        'bg-violet-500/15 text-violet-400 border-violet-500/30',
  'Interested':     'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  'In Play':        'bg-amber-500/15 text-amber-400 border-amber-500/30',
  'Not Interested': 'bg-red-500/15 text-red-400 border-red-500/30',
  'Dormant':        'bg-surface-muted text-ink-muted border-surface-border',
}

function StageChip({ stage }: { stage: ActivityStage }) {
  const cls = STAGE_CHIP[stage] ?? STAGE_CHIP['Sent']
  return (
    <span className={`text-[9px] px-2 py-0.5 rounded border font-bold tracking-wider ${cls}`}>
      {stage}
    </span>
  )
}

function TenantActionRow({
  action, onDraft, onNavigate, onSnooze, isContacted, returnedFromSnooze, onUnsnooze,
}: {
  action: TenantMatchAction
  onDraft: (a: TenantMatchAction) => void
  onNavigate: (a: TenantMatchAction) => void
  onSnooze: (a: TenantMatchAction) => void
  isContacted?: boolean
  returnedFromSnooze?: boolean
  onUnsnooze?: (a: TenantMatchAction) => void
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
          <StatusBadge status={isContacted ? 'CONTACTED' : action.contact_status} />
          <span className="text-[9px] px-2 py-0.5 rounded border font-bold bg-violet-500/15 text-violet-400 border-violet-500/30">
            {typeLabel}
          </span>
          <span className="text-[9px] px-2 py-0.5 rounded border font-semibold text-ink-muted border-surface-border">
            → {targetLabel}
          </span>
          {returnedFromSnooze && (
            <span className="text-[9px] px-2 py-0.5 rounded border font-bold bg-amber-500/15 text-amber-400 border-amber-500/30">
              ↩ Returned from Snooze
            </span>
          )}
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
        {onUnsnooze ? (
          <button
            onClick={(e) => { e.stopPropagation(); onUnsnooze(action) }}
            className="flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted hover:bg-emerald-500/10
                       text-ink-muted hover:text-emerald-400 border border-surface-border hover:border-emerald-500/40
                       font-semibold transition-colors"
            title="Unsnooze — return to the active queue"
          >
            <RotateCcw size={11} />
            Unsnooze
          </button>
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); onSnooze(action) }}
            className="flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted hover:bg-amber-500/10
                       text-ink-muted hover:text-amber-400 border border-surface-border hover:border-amber-500/40
                       font-semibold transition-colors"
            title="Snooze — remove from queue temporarily"
          >
            <Clock size={11} />
            Snooze
          </button>
        )}
      </div>
    </div>
  )
}

function AcquisitionRow({
  target, onDraft, onNavigate, onSnooze, returnedFromSnooze, onUnsnooze,
}: {
  target: AcquisitionTarget
  onDraft: (t: AcquisitionTarget) => void
  onNavigate: (t: AcquisitionTarget) => void
  onSnooze: (t: AcquisitionTarget) => void
  returnedFromSnooze?: boolean
  onUnsnooze?: (t: AcquisitionTarget) => void
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
          {returnedFromSnooze && (
            <span className="text-[9px] px-2 py-0.5 rounded border font-bold bg-amber-500/15 text-amber-400 border-amber-500/30">
              ↩ Returned from Snooze
            </span>
          )}
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
        {onUnsnooze ? (
          <button
            onClick={(e) => { e.stopPropagation(); onUnsnooze(target) }}
            className="flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted hover:bg-emerald-500/10
                       text-ink-muted hover:text-emerald-400 border border-surface-border hover:border-emerald-500/40
                       font-semibold transition-colors"
            title="Unsnooze — return to the active queue"
          >
            <RotateCcw size={11} />
            Unsnooze
          </button>
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); onSnooze(target) }}
            className="flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted hover:bg-amber-500/10
                       text-ink-muted hover:text-amber-400 border border-surface-border hover:border-amber-500/40
                       font-semibold transition-colors"
            title="Snooze — remove from queue temporarily"
          >
            <Clock size={11} />
            Snooze
          </button>
        )}
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
  const [reEngage, setReEngage]           = useState<ActivityLog[]>([])
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState<string | null>(null)
  const [showAddModal, setShowAddModal]   = useState(false)
  const [showLeaseCompsModal, setShowLeaseCompsModal] = useState(false)
  const [showCoStarModal, setShowCoStarModal] = useState(false)
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineStatus, setPipelineStatus]   = useState<string | null>(null)
  const [leaseImportStatus, setLeaseImportStatus] = useState<string | null>(null)
  const [costarImportStatus, setCostarImportStatus] = useState<string | null>(null)
  const [contactedPairs, setContactedPairs] = useState<Record<string, boolean>>(() => {
    try {
      const stored = localStorage.getItem('deal_radar_contacted_pairs')
      if (stored) {
        const parsed = JSON.parse(stored)
        if (Array.isArray(parsed)) {
          return Object.fromEntries(parsed.map((k: string) => [k, true]))
        }
        if (typeof parsed === 'object' && parsed !== null) return parsed
      }
    } catch { /* ignore */ }
    return {}
  })

  useEffect(() => {
    try {
      localStorage.setItem('deal_radar_contacted_pairs', JSON.stringify(contactedPairs))
    } catch { /* ignore quota errors */ }
  }, [contactedPairs])

  const handleContacted = (pairKey: string) => {
    setContactedPairs(prev => ({ ...prev, [pairKey]: true }))
  }

  const [draftTarget, setDraftTarget] = useState<{
    prop: PropertyOut
    type: string
    targetType: string
    pairCompanyId?: string
    pairTenantName?: string
    tenantContext?: string
    matched: MatchedTenant[]
  } | null>(null)

  // Snooze state
  const [snoozeTarget, setSnoozeTarget] = useState<{ propertyId: string; address: string } | null>(null)
  // "Snoozed" toggle bubbles — default hidden (Fix 1 / Fix 2).
  const [showSnoozedTenants, setShowSnoozedTenants] = useState(false)
  const [showSnoozedAcq, setShowSnoozedAcq] = useState(false)
  const [undoToast, setUndoToast] = useState<{
    propertyId: string; address: string; timer: ReturnType<typeof setTimeout>
  } | null>(null)

  const handleSnoozedFromBriefing = (prop: PropertyOut) => {
    setSnoozeTarget(null)
    // Optimistically remove from Section A/B (silentRefresh will do this properly)
    void silentRefresh()
    // Show UNDO toast
    if (undoToast) clearTimeout(undoToast.timer)
    const timer = setTimeout(() => setUndoToast(null), 5000)
    setUndoToast({ propertyId: prop.property_id, address: prop.address, timer })
  }

  const handleUndoSnooze = async () => {
    if (!undoToast) return
    clearTimeout(undoToast.timer)
    const pid = undoToast.propertyId
    setUndoToast(null)
    try {
      await unsnoozeProperty(pid)
      void silentRefresh()
    } catch { /* ignore */ }
  }

  const handleUnsnoozeProperty = async (propertyId: string) => {
    try {
      await unsnoozeProperty(propertyId)
      void silentRefresh()
    } catch { /* ignore */ }
  }

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

  const loadReEngage = async () => {
    try {
      setReEngage(await getReEngage())
    } catch { /* ignore — Re-engage Today is non-critical */ }
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
    void loadReEngage()
  }

  const silentRefresh = async () => {
    try {
      setBriefing(await getDailyBriefing())
    } catch { /* ignore — stale data is fine */ }
    void loadReEngage()
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

  const tenantActions     = briefing.tenant_match_actions ?? []
  const acqTargets        = briefing.acquisition_targets ?? []
  const expiredLeases     = briefing.expired_leases ?? []
  const returnedSnoozeIds = new Set(briefing.returned_from_snooze_property_ids ?? [])

  // Section A is split into Not Contacted / Contacted columns. A row counts as
  // contacted from the local optimistic flag OR the server contact_status.
  const tenantActionKey = (a: TenantMatchAction) =>
    `${a.property_id}:${a.tenant_company_id}:${a.outreach_type}`
  const isActionContacted = (a: TenantMatchAction) =>
    contactedPairs[tenantActionKey(a)] === true || a.contact_status === 'CONTACTED'
  const notContactedActions = tenantActions.filter(a => !isActionContacted(a))
  const contactedActions    = tenantActions.filter(isActionContacted)

  // Snoozed cards — hidden by default, revealed by the per-section "Snoozed" toggle.
  const snoozedTenantActions = briefing.snoozed_tenant_match_actions ?? []
  const snoozedAcqTargets    = briefing.snoozed_acquisition_targets ?? []

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
            onClick={() => setShowLeaseCompsModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors"
            title="Import a CoStar Lease Activity PDF to set tenant lease expirations"
          >
            <Upload size={13} /> Import Lease Comps
          </button>
          <button
            onClick={() => setShowCoStarModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors"
          >
            <Upload size={13} /> Import CoStar
          </button>
          <label
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors cursor-pointer"
            title="Upload CoStar Lease Activity export (.xlsx) to update property rents"
          >
            <Upload size={13} /> Import Rent Comps
            <input
              type="file"
              accept=".xlsx,.xls,.csv"
              className="hidden"
              onChange={async e => {
                const file = e.target.files?.[0]
                if (!file) return
                e.target.value = ''
                setLeaseImportStatus(null)
                try {
                  const result = await importLeaseActivity(file)
                  setLeaseImportStatus(
                    `Rent comps imported — ${result.updated} properties updated` +
                    (result.skipped_no_match > 0 ? `, ${result.skipped_no_match} unmatched` : '')
                  )
                } catch {
                  setLeaseImportStatus('Rent comps import failed — check file format')
                }
              }}
            />
          </label>
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
      {leaseImportStatus && (
        <div className={`mb-6 px-4 py-2.5 rounded-lg text-xs flex items-center justify-between ${
          leaseImportStatus.includes('failed')
            ? 'bg-red-500/10 border border-red-500/25 text-red-400'
            : 'bg-emerald-500/10 border border-emerald-500/25 text-emerald-400'
        }`}>
          <span>{leaseImportStatus}</span>
          <button onClick={() => setLeaseImportStatus(null)} className="text-ink-muted hover:text-ink-primary ml-4">✕</button>
        </div>
      )}
      {costarImportStatus && (
        <div className="mb-6 px-4 py-2.5 rounded-lg text-xs flex items-center justify-between
                        bg-emerald-500/10 border border-emerald-500/25 text-emerald-400">
          <span>{costarImportStatus}</span>
          <button onClick={() => setCostarImportStatus(null)} className="text-ink-muted hover:text-ink-primary ml-4">✕</button>
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
      {showLeaseCompsModal && <LeaseCompsModal onClose={() => setShowLeaseCompsModal(false)} onDone={silentRefresh} />}
      {snoozeTarget && (
        <SnoozeModal
          propertyId={snoozeTarget.propertyId}
          propertyAddress={snoozeTarget.address}
          onClose={() => setSnoozeTarget(null)}
          onSnoozed={handleSnoozedFromBriefing}
        />
      )}
      {undoToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3
                        bg-surface-card border border-amber-500/40 rounded-xl px-5 py-3 shadow-2xl">
          <Clock size={14} className="text-amber-400 flex-shrink-0" />
          <span className="text-sm text-ink-secondary">
            <span className="font-semibold text-amber-400">Snoozed</span>
            {' '}· {undoToast.address.split(',')[0]}
          </span>
          <button
            onClick={handleUndoSnooze}
            className="ml-2 px-3 py-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-400
                       text-xs font-semibold transition-colors border border-amber-500/40"
          >
            UNDO
          </button>
          <button onClick={() => { clearTimeout(undoToast.timer); setUndoToast(null) }}
                  className="text-ink-muted hover:text-ink-primary ml-1">
            <X size={14} />
          </button>
        </div>
      )}
      {showCoStarModal && (
        <CoStarImportModal
          onClose={() => setShowCoStarModal(false)}
          onDone={(result: CoStarImportResult) => {
            setCostarImportStatus(
              `Import complete — ${result.updated} updated, ${result.inserted} inserted, ${result.skipped} skipped`
            )
            setShowCoStarModal(false)
            void silentRefresh()
          }}
        />
      )}

      {/* Expired Leases — Action Required (renders above Section A) */}
      {expiredLeases.length > 0 && (
        <section className="mb-10">
          <SectionHeader
            icon={AlertTriangle}
            color="text-red-400"
            title="Expired Leases — Action Required"
            count={expiredLeases.length}
          />
          <div className="space-y-3">
            {expiredLeases.map(lease => (
              <div
                key={lease.company_id}
                className="bg-surface-card border border-red-500/30 rounded-xl p-4 flex items-start gap-4"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1.5">
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide
                                     bg-red-500/15 text-red-400 border border-red-500/30">
                      ● EXPIRED
                    </span>
                    <span className="text-sm font-semibold text-ink-primary truncate">{lease.name}</span>
                  </div>
                  <div className="flex items-center gap-3 text-[11px] text-ink-secondary flex-wrap">
                    <span>{lease.industry}</span>
                    {lease.sf_needed != null && <span>{(lease.sf_needed / 1000).toFixed(0)}K SF needed</span>}
                    {lease.submarket && <span>{lease.submarket}</span>}
                  </div>
                </div>
                <button
                  onClick={() => navigate(`/companies?selected=${lease.company_id}`)}
                  className="flex-shrink-0 flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg
                             bg-red-600 hover:bg-red-700 text-white font-semibold transition-colors"
                >
                  <MessageSquarePlus size={11} />
                  Follow Up
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Re-engage Today — activity contacts whose next_touch_date is due */}
      {reEngage.length > 0 && (
        <section className="mb-10">
          <SectionHeader
            icon={History}
            color="text-amber-400"
            title="Re-engage Today"
            count={reEngage.length}
          />
          <div className="space-y-3">
            {reEngage.map(item => (
              <div
                key={item.id}
                className="bg-surface-card border border-amber-500/30 rounded-xl p-4 flex items-start gap-4"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="text-sm font-bold text-ink-primary truncate">
                      {item.contact_name || item.company_name || 'Contact'}
                    </span>
                    <StageChip stage={(item.stage ?? 'Sent') as ActivityStage} />
                    {item.next_touch_date && (
                      <span className="text-[10px] text-amber-400">Due {item.next_touch_date}</span>
                    )}
                  </div>
                  <div className="text-[11px] text-ink-secondary truncate">
                    {item.property_address || item.company_name || item.action_taken}
                  </div>
                </div>
                <button
                  onClick={() => navigate(`/activity?focus=${item.id}`)}
                  className="flex-shrink-0 flex items-center gap-1 text-[10px] px-3 py-1.5 rounded-lg
                             bg-amber-600 hover:bg-amber-700 text-white font-semibold transition-colors"
                >
                  <ClipboardList size={11} />
                  Open in Activity Log
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

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
            <div className="flex items-center justify-between gap-3">
              <SectionHeader
                icon={Building2}
                color="text-violet-400"
                title="Section A — Tenant Match Actions"
                count={tenantActions.length}
              />
              <button
                onClick={() => setShowSnoozedTenants(v => !v)}
                className={`flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-full border font-semibold transition-colors
                  ${showSnoozedTenants
                    ? 'bg-amber-500/15 text-amber-400 border-amber-500/40'
                    : 'bg-surface-muted text-ink-muted border-surface-border hover:text-amber-400 hover:border-amber-500/40'}`}
                title="Show / hide snoozed tenant matches"
                aria-pressed={showSnoozedTenants}
              >
                <Clock size={11} />
                Snoozed
                <span className="px-1.5 py-0.5 rounded-full bg-black/20 text-[9px]">{snoozedTenantActions.length}</span>
              </button>
            </div>
            {showSnoozedTenants ? (
              snoozedTenantActions.length === 0 ? (
                <div className="text-xs text-ink-muted px-2 py-4">No snoozed tenant matches.</div>
              ) : (
                <div className="space-y-3 max-h-[70vh] overflow-y-auto pr-1">
                  {snoozedTenantActions.map(a => (
                    <TenantActionRow
                      key={`snz-${a.property_id}-${a.tenant_company_id}-${a.outreach_type}`}
                      action={a}
                      onDraft={handleTenantDraft}
                      onNavigate={navTenantAction}
                      onSnooze={() => {}}
                      onUnsnooze={a => handleUnsnoozeProperty(a.property_id)}
                      isContacted={isActionContacted(a)}
                    />
                  ))}
                </div>
              )
            ) : tenantActions.length === 0 ? (
              <div className="text-xs text-ink-muted px-2">No tenant-match actions queued.</div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* Left column — Not Contacted */}
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-2 px-1">
                    <span className="text-xs font-bold uppercase tracking-wider text-ink-secondary">Not Contacted</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-muted text-ink-muted border border-surface-border font-semibold">
                      {notContactedActions.length}
                    </span>
                  </div>
                  {notContactedActions.length === 0 ? (
                    <div className="text-xs text-ink-muted px-2 py-4">Nothing left to contact.</div>
                  ) : (
                    <div className="space-y-3 max-h-[70vh] overflow-y-auto pr-1">
                      {notContactedActions.map(a => (
                        <TenantActionRow
                          key={`${a.property_id}-${a.tenant_company_id}-${a.outreach_type}`}
                          action={a}
                          onDraft={handleTenantDraft}
                          onNavigate={navTenantAction}
                          onSnooze={a => setSnoozeTarget({ propertyId: a.property_id, address: a.address })}
                          isContacted={false}
                          returnedFromSnooze={returnedSnoozeIds.has(a.property_id)}
                        />
                      ))}
                    </div>
                  )}
                </div>

                {/* Right column — Contacted */}
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-2 px-1">
                    <span className="text-xs font-bold uppercase tracking-wider text-emerald-400">Contacted</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-semibold">
                      {contactedActions.length}
                    </span>
                  </div>
                  {contactedActions.length === 0 ? (
                    <div className="text-xs text-ink-muted px-2 py-4">No tenants contacted yet.</div>
                  ) : (
                    <div className="space-y-3 max-h-[70vh] overflow-y-auto pr-1">
                      {contactedActions.map(a => (
                        <TenantActionRow
                          key={`${a.property_id}-${a.tenant_company_id}-${a.outreach_type}`}
                          action={a}
                          onDraft={handleTenantDraft}
                          onNavigate={navTenantAction}
                          onSnooze={a => setSnoozeTarget({ propertyId: a.property_id, address: a.address })}
                          isContacted={true}
                          returnedFromSnooze={returnedSnoozeIds.has(a.property_id)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </section>

          {/* Section B — Acquisition Targets (signal_score >= 40, sorted DESC) */}
          <section>
            <div className="flex items-center justify-between gap-3">
              <SectionHeader
                icon={Target}
                color="text-emerald-400"
                title="Section B — Acquisition Targets"
                count={acqTargets.length}
              />
              <button
                onClick={() => setShowSnoozedAcq(v => !v)}
                className={`flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-full border font-semibold transition-colors
                  ${showSnoozedAcq
                    ? 'bg-amber-500/15 text-amber-400 border-amber-500/40'
                    : 'bg-surface-muted text-ink-muted border-surface-border hover:text-amber-400 hover:border-amber-500/40'}`}
                title="Show / hide snoozed acquisition targets"
                aria-pressed={showSnoozedAcq}
              >
                <Clock size={11} />
                Snoozed
                <span className="px-1.5 py-0.5 rounded-full bg-black/20 text-[9px]">{snoozedAcqTargets.length}</span>
              </button>
            </div>
            {showSnoozedAcq ? (
              snoozedAcqTargets.length === 0 ? (
                <div className="text-xs text-ink-muted px-2 py-4">No snoozed acquisition targets.</div>
              ) : (
                <div className="space-y-3 max-h-[70vh] overflow-y-auto pr-1">
                  {snoozedAcqTargets.map(t => (
                    <AcquisitionRow
                      key={`snz-${t.property_id}`}
                      target={t}
                      onDraft={handleAcqDraft}
                      onNavigate={navAcquisition}
                      onSnooze={() => {}}
                      onUnsnooze={t => handleUnsnoozeProperty(t.property_id)}
                    />
                  ))}
                </div>
              )
            ) : acqTargets.length === 0 ? (
              <div className="text-xs text-ink-muted px-2">No acquisition targets meet the threshold.</div>
            ) : (
              <div className="space-y-3">
                {acqTargets.map(t => (
                  <AcquisitionRow
                    key={t.property_id}
                    target={t}
                    onDraft={handleAcqDraft}
                    onNavigate={navAcquisition}
                    onSnooze={t => setSnoozeTarget({ propertyId: t.property_id, address: t.address })}
                    returnedFromSnooze={returnedSnoozeIds.has(t.property_id)}
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
            onContacted={handleContacted}
          />
        )
      })()}
    </div>
  )
}
