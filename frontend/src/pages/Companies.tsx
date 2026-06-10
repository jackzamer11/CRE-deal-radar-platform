import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  Users, Filter, X, TrendingUp, Clock, MapPin, Plus, RefreshCw,
  Upload, Pencil, Check, AlertTriangle, Zap, Send, Building2,
} from 'lucide-react'
import { getCompanies, getCompany, updateCompanyLease, updateCompanyTrajectory, unsnoozeCompany } from '../api/client'
import type { CompanyListOut, CompanyOut, RepClass } from '../types'
import { PriorityBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'
import AddCompanyModal from '../components/AddCompanyModal'
import CoStarTenantImportModal from '../components/CoStarTenantImportModal'
import OutreachDraftModal from '../components/OutreachDraftModal'
import CompanySnoozeModal from '../components/CompanySnoozeModal'
import { SUBMARKETS } from '../constants'

const LEASE_SOURCES = [
  { value: 'manual',              label: 'Manual entry' },
  { value: 'costar',             label: 'CoStar' },
  { value: 'sec_filing',         label: 'SEC filing' },
  { value: 'landlord_confirmed', label: 'Landlord confirmed' },
  { value: 'public_record',      label: 'Public record' },
]

const TRAJECTORY_OPTIONS = [
  { value: 'AUTO',        label: 'Auto (tiered SF/head)',   color: 'text-ink-secondary' },
  { value: 'GROWING',     label: 'Growing',                 color: 'text-emerald-400'   },
  { value: 'FLAT',        label: 'Flat (steady-state)',     color: 'text-blue-400'       },
  { value: 'CONTRACTING', label: 'Contracting',             color: 'text-amber-400'      },
]

function GrowthBadge({ pct }: { pct: number | null }) {
  if (pct == null) return <span className="text-ink-muted text-xs">—</span>
  const color = pct >= 35 ? 'text-red-400' : pct >= 20 ? 'text-amber-400' : pct >= 8 ? 'text-emerald-400' : 'text-ink-muted'
  return <span className={`mono text-xs font-semibold ${color}`}>+{pct.toFixed(0)}%</span>
}

function ExpiryBadge({ months }: { months: number | null }) {
  if (months == null) return <span className="text-ink-muted text-xs">—</span>
  const color = months <= 6 ? 'text-red-400' : months <= 12 ? 'text-amber-400' : months <= 24 ? 'text-blue-400' : 'text-ink-muted'
  return <span className={`mono text-xs ${color}`}>{months}mo</span>
}

function isLeaseExpired(c: { lease_expiry_months: number | null; lease_expiry_date: string | null }): boolean {
  if (c.lease_expiry_months === 0) return true
  if (c.lease_expiry_date) {
    const today = new Date(); today.setHours(0, 0, 0, 0)
    const exp = new Date(`${c.lease_expiry_date}T00:00:00`)
    return exp <= today
  }
  return false
}

function ExpiredBadge() {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide
                     bg-red-500/15 text-red-400 border border-red-500/30">
      ● EXPIRED
    </span>
  )
}

function RepBadge({ repClass, repName }: { repClass: RepClass; repName: string | null }) {
  if (repClass === 'BLANK') {
    return (
      <span className="flex items-center gap-1 text-[10px] text-emerald-400">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0" />
        No Rep
      </span>
    )
  }
  if (repClass === 'MAJOR') {
    const name = repName ? repName.split(/[\s,/]/)[0] : 'Major'
    return (
      <span className="flex items-center gap-1 text-[10px] text-red-400">
        <span className="w-1.5 h-1.5 rounded-full bg-red-400 flex-shrink-0" />
        {name}
      </span>
    )
  }
  const name = repName ? repName.split(/[\s,/]/)[0] : 'Rep'
  return (
    <span className="flex items-center gap-1 text-[10px] text-amber-400">
      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0" />
      {name}
    </span>
  )
}

export default function Companies() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [companies, setCompanies]   = useState<CompanyListOut[]>([])
  const [loading, setLoading]       = useState(true)
  const [submarket, setSubmarket]   = useState('')
  const [priority, setPriority]     = useState('')
  const [repFilter, setRepFilter]   = useState('')
  const [expansionOnly, setExpansionOnly]         = useState(false)
  const [topExpiryMode, setTopExpiryMode]         = useState(false)
  const [topOutreachMode, setTopOutreachMode]     = useState(false)
  const [selected, setSelected]     = useState<CompanyOut | null>(null)
  const [showAddModal, setShowAddModal]             = useState(false)
  const [showTenantImportModal, setShowTenantImportModal] = useState(false)
  const [showOutreachModal, setShowOutreachModal]   = useState(false)
  const [showSnoozeModal, setShowSnoozeModal]       = useState(false)

  // Trajectory state
  const [trajectorySaving, setTrajectorySaving] = useState(false)

  // Lease expiry inline edit state
  const [editingLease, setEditingLease]     = useState(false)
  const [leaseMonthsInput, setLeaseMonthsInput] = useState('')
  const [leaseDateInput, setLeaseDateInput]   = useState('')
  const [leaseInputMode, setLeaseInputMode]   = useState<'months' | 'date'>('months')
  const [leaseSource, setLeaseSource]         = useState('manual')
  const [leaseSaving, setLeaseSaving]         = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const params: Record<string, string | boolean | undefined> = {
        submarket:    submarket || undefined,
        priority:     priority  || undefined,
        expansion_only: expansionOnly || undefined,
        rep_filter:   repFilter || undefined,
        outreach_status: topOutreachMode ? 'needs-outreach' : undefined,
      }
      const data = await getCompanies(params)
      setCompanies(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [submarket, priority, expansionOnly, repFilter, topOutreachMode])

  // Auto-open detail panel from URL param ?selected=CO-001
  useEffect(() => {
    const cid = searchParams.get('selected')
    if (cid) {
      getCompany(cid).then(c => setSelected(c as CompanyOut)).catch(() => {})
      setSearchParams({}, { replace: true })
    }
  }, [])

  const displayedCompanies = topExpiryMode
    ? companies.filter(c => c.lease_expiry_months === null).slice(0, 20)
    : topOutreachMode
      ? companies.slice(0, 20)
      : companies

  const needingExpiryCount   = companies.filter(c => c.lease_expiry_months === null).length
  const needingOutreachCount = companies.length

  const saveTrajectory = async (company: typeof selected, value: string) => {
    if (!company) return
    setTrajectorySaving(true)
    try {
      await updateCompanyTrajectory(company.company_id, value)
      setSelected({ ...company, lease_trajectory: value })
      load()
    } finally {
      setTrajectorySaving(false)
    }
  }

  const openLeaseEdit = () => {
    setLeaseMonthsInput('')
    setLeaseDateInput('')
    setLeaseInputMode('months')
    setLeaseSource('manual')
    setEditingLease(true)
  }

  const cancelLeaseEdit = () => setEditingLease(false)

  const saveLease = async () => {
    if (!selected) return
    const payload: { lease_expiry_months?: number; lease_expiry_date?: string; lease_expiry_source: string } = {
      lease_expiry_source: leaseSource,
    }
    if (leaseInputMode === 'months') {
      const m = parseInt(leaseMonthsInput)
      if (!leaseMonthsInput || isNaN(m) || m < 0) return
      payload.lease_expiry_months = m
    } else {
      if (!leaseDateInput) return
      payload.lease_expiry_date = leaseDateInput
    }
    setLeaseSaving(true)
    try {
      await updateCompanyLease(selected.company_id, payload)
      const updatedMonths = leaseInputMode === 'months'
        ? parseInt(leaseMonthsInput)
        : (() => {
            const exp = new Date(leaseDateInput)
            const now = new Date()
            return Math.max(0, (exp.getFullYear() - now.getFullYear()) * 12 + (exp.getMonth() - now.getMonth()))
          })()
      setSelected({ ...selected, lease_expiry_months: updatedMonths })
      setEditingLease(false)
      load()
    } finally {
      setLeaseSaving(false)
    }
  }

  const handleSnoozed = (updated: CompanyOut) => {
    setSelected(updated)
    setShowSnoozeModal(false)
    load()
  }

  const handleUnsnooze = async (company: typeof selected) => {
    if (!company) return
    try {
      const updated = await unsnoozeCompany(company.company_id)
      setSelected(updated)
      load()
    } catch { /* no-op — leave panel as-is on failure */ }
  }

  const handleSelectCompany = async (c: CompanyListOut) => {
    setEditingLease(false)
    try {
      const full = await getCompany(c.company_id)
      setSelected(full)
    } catch {
      setSelected(c as CompanyOut)
    }
  }

  // Auto-open via ?selected= URL param
  useEffect(() => {
    const id = searchParams.get('selected')
    if (id && (!selected || selected.company_id !== id)) {
      getCompany(id).then(setSelected).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const closePanel = () => {
    setSelected(null)
    setEditingLease(false)
    if (searchParams.get('selected')) {
      const next = new URLSearchParams(searchParams)
      next.delete('selected')
      setSearchParams(next, { replace: true })
    }
  }

  const clearFilters = () => {
    setSubmarket(''); setPriority(''); setRepFilter('')
    setExpansionOnly(false); setTopExpiryMode(false); setTopOutreachMode(false)
  }
  const hasActiveFilters = submarket || priority || repFilter || expansionOnly || topExpiryMode || topOutreachMode

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Users size={20} className="text-emerald-400" />
          <h1 className="text-xl font-bold text-ink-primary">Companies</h1>
          <span className="text-ink-muted text-sm">({companies.length})</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-600 text-white text-xs font-semibold
                       hover:bg-emerald-700 transition-colors"
          >
            <Plus size={13} /> Add Company
          </button>
          <button
            onClick={() => setShowTenantImportModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-card border border-surface-border
                       text-ink-secondary hover:text-ink-primary text-xs font-semibold transition-colors"
          >
            <Upload size={13} /> Import CoStar Tenants
          </button>
          <button onClick={load} className="p-2 rounded-lg hover:bg-surface-card text-ink-muted hover:text-ink-primary">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Filter size={13} className="text-ink-muted" />
        <select
          value={submarket}
          onChange={e => setSubmarket(e.target.value)}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">All Submarkets</option>
          {SUBMARKETS.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          value={priority}
          onChange={e => setPriority(e.target.value)}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">All Priorities</option>
          {['IMMEDIATE','HIGH','WORKABLE','IGNORE'].map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          value={repFilter}
          onChange={e => setRepFilter(e.target.value)}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">All Reps</option>
          <option value="BLANK">No Rep (direct pitch)</option>
          <option value="MAJOR">Major Firm</option>
          <option value="OTHER">Regional / Other</option>
        </select>
        <label className="flex items-center gap-2 text-xs text-ink-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={expansionOnly}
            onChange={e => setExpansionOnly(e.target.checked)}
            className="accent-emerald-500"
          />
          Expansion Signal Only
        </label>

        {/* Top 20 Needing Expiry */}
        <button
          onClick={() => { setTopExpiryMode(v => !v); if (topOutreachMode) setTopOutreachMode(false) }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors
            ${topExpiryMode
              ? 'bg-amber-500/20 border-amber-500/50 text-amber-300'
              : 'bg-surface-card border-surface-border text-ink-secondary hover:text-ink-primary'}`}
        >
          <Clock size={12} />
          Top 20 Needing Expiry
          {needingExpiryCount > 0 && !topExpiryMode && (
            <span className="ml-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-bold bg-surface-muted text-ink-muted">
              {needingExpiryCount}
            </span>
          )}
        </button>

        {/* Top 20 Needing Outreach */}
        <button
          onClick={() => { setTopOutreachMode(v => !v); if (topExpiryMode) setTopExpiryMode(false) }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors
            ${topOutreachMode
              ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-300'
              : 'bg-surface-card border-surface-border text-ink-secondary hover:text-ink-primary'}`}
        >
          <Send size={12} />
          Top 20 Needing Outreach
        </button>

        {hasActiveFilters && (
          <button
            onClick={clearFilters}
            className="flex items-center gap-1 text-xs text-ink-muted hover:text-red-400"
          >
            <X size={12} /> Clear
          </button>
        )}
      </div>

      {topExpiryMode && (
        <div className="mb-3 flex items-center gap-2 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
          <Clock size={13} />
          Showing top {Math.min(needingExpiryCount, 20)} of {needingExpiryCount} companies missing lease expiry data. Click a card to enter manually.
        </div>
      )}
      {topOutreachMode && (
        <div className="mb-3 flex items-center gap-2 text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2">
          <Send size={13} />
          Showing top {Math.min(needingOutreachCount, 20)} companies needing outreach — no contact in 90 days, MAJOR firm reps excluded.
        </div>
      )}

      {/* Cards grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
        {loading ? (
          <div className="col-span-3 text-center py-12 text-ink-muted">Loading...</div>
        ) : displayedCompanies.length === 0 ? (
          <div className="col-span-3 text-center py-12 text-ink-muted">No companies found</div>
        ) : displayedCompanies.map(c => (
          <div
            key={c.id}
            className="bg-surface-card border border-surface-border rounded-xl p-4 cursor-pointer
                       hover:border-accent-blue/40 transition-colors"
            onClick={() => handleSelectCompany(c)}
          >
            <div className="flex items-start justify-between mb-3">
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-ink-primary text-sm truncate">{c.name}</div>
                <div className="text-[11px] text-ink-muted mt-0.5">{c.industry}</div>
              </div>
              <div className="flex items-center gap-1.5 flex-shrink-0 ml-2">
                {c.insufficient_data && (
                  <span title={`Only ${c.signals_scored_count} signal(s) scored`}>
                    <AlertTriangle size={12} className="text-amber-400" />
                  </span>
                )}
                <RepBadge repClass={c.rep_class} repName={c.tenant_representative} />
                {c.late_stage && (
                  <span
                    title="Lease expires within 1-3 months — likely already in negotiations"
                    className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider
                               bg-amber-500/15 text-amber-400 border border-amber-500/30"
                  >
                    Late Stage
                  </span>
                )}
                {isLeaseExpired(c) && <ExpiredBadge />}
                <PriorityBadge priority={c.priority} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 mb-3">
              <div className="bg-surface-muted rounded-lg p-2">
                <div className="text-[10px] text-ink-muted mb-0.5">Opportunity Score</div>
                <ScoreBadge score={c.opportunity_score} size="md" showBar />
              </div>
              <div className="bg-surface-muted rounded-lg p-2">
                <div className="text-[10px] text-ink-muted mb-0.5">Headcount</div>
                <div className="text-sm font-semibold mono text-ink-primary">{c.current_headcount ?? '—'}</div>
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                  <TrendingUp size={11} /> Growth
                </div>
                <GrowthBadge pct={c.headcount_growth_pct} />
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                  <Clock size={11} /> Lease Expiry
                </div>
                <ExpiryBadge months={c.lease_expiry_months} />
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                  <MapPin size={11} /> Submarket
                </div>
                <span className="text-[11px] text-ink-secondary">{c.current_submarket || '—'}</span>
              </div>
            </div>

            {c.expansion_signal && (
              <div className="mt-3 flex items-center gap-1.5 text-[10px] text-emerald-400 font-bold uppercase tracking-wider">
                <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Expansion Signal Active
              </div>
            )}
          </div>
        ))}
      </div>

      {showAddModal && (
        <AddCompanyModal
          onClose={() => setShowAddModal(false)}
          onSaved={(_saved: CompanyOut) => { setShowAddModal(false); load() }}
        />
      )}
      {showTenantImportModal && (
        <CoStarTenantImportModal
          onClose={() => setShowTenantImportModal(false)}
          onDone={load}
        />
      )}
      {showOutreachModal && selected && (
        <OutreachDraftModal
          entity_type="company"
          company={selected}
          onClose={() => setShowOutreachModal(false)}
          onSaved={() => { setShowOutreachModal(false); load() }}
        />
      )}
      {showSnoozeModal && selected && (
        <CompanySnoozeModal
          companyId={selected.company_id}
          companyName={selected.name}
          onClose={() => setShowSnoozeModal(false)}
          onSnoozed={handleSnoozed}
        />
      )}

      {/* Detail panel */}
      {selected && !showOutreachModal && (
        <div className="fixed inset-y-0 right-0 w-96 bg-surface-card border-l border-surface-border shadow-2xl z-50 overflow-y-auto">
          <div className="p-5">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="font-bold text-ink-primary text-base">{selected.name}</div>
                <div className="text-xs text-ink-muted mt-0.5">{selected.industry}</div>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setShowSnoozeModal(true)}
                  title="Snooze company — remove from Daily Briefing / outreach queue"
                  className="text-ink-muted hover:text-amber-400 p-1 rounded-lg hover:bg-surface-muted"
                >
                  <Clock size={16} />
                </button>
                <button onClick={closePanel} className="text-ink-muted hover:text-ink-primary p-1">
                  <X size={18} />
                </button>
              </div>
            </div>

            {/* Active snooze banner */}
            {selected.snoozed_until && (
              <div className="flex items-center justify-between gap-2 mb-4 px-3 py-2 rounded-lg
                              bg-amber-500/10 border border-amber-500/30">
                <div className="flex items-center gap-2 min-w-0">
                  <Clock size={13} className="text-amber-400 flex-shrink-0" />
                  <span className="text-[11px] text-amber-300 truncate">
                    Snoozed until {selected.snoozed_until}
                    {selected.snooze_reason ? ` — ${selected.snooze_reason}` : ''}
                  </span>
                </div>
                <button
                  onClick={() => handleUnsnooze(selected)}
                  className="text-[11px] font-semibold text-amber-400 hover:text-amber-300 flex-shrink-0"
                >
                  Unsnooze
                </button>
              </div>
            )}

            {/* Draft Outreach CTA */}
            <button
              onClick={() => setShowOutreachModal(true)}
              className="w-full flex items-center justify-center gap-2 mb-4 px-4 py-2.5 rounded-xl
                         bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold transition-colors"
            >
              <Zap size={15} />
              Draft Outreach
            </button>

            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <PriorityBadge priority={selected.priority} />
                {selected.insufficient_data && (
                  <span className="flex items-center gap-1 text-[10px] text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded-full">
                    <AlertTriangle size={10} />
                    Low data ({selected.signals_scored_count}/5 signals)
                  </span>
                )}
              </div>

              <div className="bg-surface-muted rounded-lg p-3">
                <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Tenant Intelligence</div>
                <div className="space-y-2">
                  <Row label="Current Headcount" value={selected.current_headcount != null ? String(selected.current_headcount) : '—'} />
                  <Row label="YoY Growth" value={selected.headcount_growth_pct != null ? `+${selected.headcount_growth_pct.toFixed(0)}%` : '—'} />

                  {/* Lease Expiry with inline edit */}
                  <div className="flex justify-between items-start">
                    <span className="text-xs text-ink-muted flex-shrink-0">Lease Expiry</span>
                    {editingLease ? (
                      <div className="flex flex-col gap-2 items-end ml-2 w-full">
                        <div className="flex rounded-lg overflow-hidden border border-surface-border text-[10px] self-end">
                          <button
                            onClick={() => setLeaseInputMode('months')}
                            className={`px-2 py-1 ${leaseInputMode === 'months' ? 'bg-accent-blue/20 text-accent-blue' : 'text-ink-muted hover:text-ink-secondary'}`}
                          >Months</button>
                          <button
                            onClick={() => setLeaseInputMode('date')}
                            className={`px-2 py-1 ${leaseInputMode === 'date' ? 'bg-accent-blue/20 text-accent-blue' : 'text-ink-muted hover:text-ink-secondary'}`}
                          >Date</button>
                        </div>
                        {leaseInputMode === 'months' ? (
                          <input
                            type="number" min="0" placeholder="Months (e.g. 18)"
                            value={leaseMonthsInput} onChange={e => setLeaseMonthsInput(e.target.value)}
                            className="w-full text-xs bg-surface-card border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary focus:outline-none focus:border-accent-blue/50"
                            autoFocus
                          />
                        ) : (
                          <input
                            type="date" value={leaseDateInput} onChange={e => setLeaseDateInput(e.target.value)}
                            className="w-full text-xs bg-surface-card border border-surface-border rounded-lg px-2 py-1.5 text-ink-primary focus:outline-none focus:border-accent-blue/50"
                            autoFocus
                          />
                        )}
                        <select
                          value={leaseSource} onChange={e => setLeaseSource(e.target.value)}
                          className="w-full text-xs bg-surface-card border border-surface-border rounded-lg px-2 py-1.5 text-ink-secondary"
                        >
                          {LEASE_SOURCES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                        </select>
                        <div className="flex gap-1.5 self-end">
                          <button onClick={cancelLeaseEdit} className="text-[10px] px-2 py-1 rounded-lg border border-surface-border text-ink-muted hover:text-ink-primary">Cancel</button>
                          <button
                            onClick={saveLease} disabled={leaseSaving}
                            className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
                          >
                            <Check size={10} /> {leaseSaving ? 'Saving…' : 'Save & Re-score'}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs text-ink-secondary font-medium mono">
                          {selected.lease_expiry_months != null ? `${selected.lease_expiry_months} months` : '—'}
                        </span>
                        <button onClick={openLeaseEdit} title="Enter lease expiry" className="text-ink-muted hover:text-accent-blue transition-colors">
                          <Pencil size={11} />
                        </button>
                      </div>
                    )}
                  </div>

                  <Row label="Submarket" value={selected.current_submarket || '—'} />
                  <Row label="Expansion Signal" value={selected.expansion_signal ? '✓ Active' : '—'} />

                  {/* Lease Trajectory dropdown */}
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-ink-muted flex-shrink-0">Lease Trajectory</span>
                    <select
                      value={selected.lease_trajectory || 'AUTO'}
                      disabled={trajectorySaving}
                      onChange={e => saveTrajectory(selected, e.target.value)}
                      className={`text-xs bg-surface-card border border-surface-border rounded-lg px-2 py-1
                                  focus:outline-none focus:border-accent-blue/50 disabled:opacity-50
                                  ${selected.lease_trajectory === 'CONTRACTING' ? 'text-amber-400'
                                    : selected.lease_trajectory === 'GROWING' ? 'text-emerald-400'
                                    : selected.lease_trajectory === 'FLAT' ? 'text-blue-400'
                                    : 'text-ink-secondary'}`}
                    >
                      {TRAJECTORY_OPTIONS.map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>

                  {/* Tenant Rep */}
                  <div className="flex justify-between items-start pt-0.5">
                    <span className="text-xs text-ink-muted flex-shrink-0">Tenant Rep</span>
                    <div className="text-right ml-2">
                      {selected.rep_class === 'BLANK' && (
                        <div>
                          <div className="text-xs text-emerald-400 font-medium">None — open opportunity</div>
                          <div className="text-[10px] text-ink-muted">+10 score (no rep penalty)</div>
                        </div>
                      )}
                      {selected.rep_class === 'MAJOR' && (
                        <div>
                          <div className="text-xs text-red-400 font-medium">{selected.tenant_representative}</div>
                          <div className="text-[10px] text-ink-muted">−25 score (major firm representation)</div>
                        </div>
                      )}
                      {selected.rep_class === 'OTHER' && (
                        <div>
                          <div className="text-xs text-amber-400 font-medium">{selected.tenant_representative}</div>
                          <div className="text-[10px] text-ink-muted">−5 score (regional/independent rep)</div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div className="bg-surface-muted rounded-lg p-3">
                <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Opportunity Score</div>
                <ScoreBadge score={selected.opportunity_score} size="lg" showBar />
                {selected.insufficient_data && (
                  <div className="mt-1.5 text-[10px] text-amber-400">
                    Score based on {selected.signals_scored_count} of 5 signals — enter lease expiry to improve accuracy.
                  </div>
                )}
              </div>

              {/* Matched Properties section */}
              {'matched_properties' in selected && (
                <div className="bg-surface-muted rounded-lg p-3">
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">
                    MATCHED PROPERTIES ({(selected as CompanyOut).matched_properties?.length ?? 0})
                  </div>
                  {(!(selected as CompanyOut).matched_properties || (selected as CompanyOut).matched_properties.length === 0) ? (
                    <div className="text-xs text-ink-muted">No matched properties — add lease expiry or SF data to improve matching.</div>
                  ) : (
                    <div className="space-y-2">
                      {(selected as CompanyOut).matched_properties.map(p => (
                        <div
                          key={p.property_id}
                          className="border border-surface-border rounded-lg p-2 hover:bg-surface-card cursor-pointer transition-colors"
                          onClick={() => navigate(`/properties?selected=${p.property_id}`)}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5 mb-0.5">
                                <Building2 size={10} className="text-ink-muted flex-shrink-0" />
                                <div className="text-xs font-semibold text-ink-primary truncate">{p.address}</div>
                              </div>
                              <div className="text-[10px] text-ink-muted">
                                {p.submarket}{p.sf_avail ? ` · ${p.sf_avail.toLocaleString()} SF avail` : ''}{p.vacancy_pct != null ? ` · ${p.vacancy_pct.toFixed(0)}% vac` : ''}
                              </div>
                              <div className="text-[10px] text-ink-secondary">
                                {p.in_place_rent_psf != null ? `$${p.in_place_rent_psf.toFixed(0)}/SF in-place` : ''}
                                {p.listed_for_sale ? <span className="ml-1 text-amber-400 font-semibold">FOR SALE</span> : null}
                              </div>
                              {p.adjacent_submarket && (
                                <span className="inline-block mt-1 text-[9px] px-1.5 py-0.5 rounded border font-bold bg-sky-500/15 text-sky-400 border-sky-500/30">
                                  Adjacent Submarket
                                </span>
                              )}
                              {p.match_reasons.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1">
                                  {p.match_reasons.map((r, i) => (
                                    <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-surface-card text-ink-secondary border border-surface-border">
                                      {r}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                            <div className="flex-shrink-0 text-right">
                              <ScoreBadge score={p.match_score} size="sm" />
                              <div className="text-[9px] text-violet-400 mt-0.5">match</div>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-xs text-ink-muted">{label}</span>
      <span className="text-xs text-ink-secondary font-medium mono">{value}</span>
    </div>
  )
}
