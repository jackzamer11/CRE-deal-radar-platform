import { useEffect, useState } from 'react'
import { Users, Filter, X, TrendingUp, Clock, MapPin, Plus, RefreshCw, Upload } from 'lucide-react'
import { getCompanies } from '../api/client'
import type { CompanyListOut, CompanyOut } from '../types'
import { PriorityBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'
import AddCompanyModal from '../components/AddCompanyModal'
import CoStarTenantImportModal from '../components/CoStarTenantImportModal'

const SUBMARKETS = [
  'Arlington (Clarendon)', 'Arlington (Rosslyn)', 'Arlington (Ballston)',
  'Arlington (Columbia Pike)', 'Alexandria (Old Town)', 'Tysons', 'Reston', 'Falls Church',
  'McLean', 'Vienna', 'Fairfax City',
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

export default function Companies() {
  const [companies, setCompanies] = useState<CompanyListOut[]>([])
  const [loading, setLoading] = useState(true)
  const [submarket, setSubmarket] = useState('')
  const [priority, setPriority] = useState('')
  const [expansionOnly, setExpansionOnly] = useState(false)
  const [selected, setSelected] = useState<CompanyListOut | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showTenantImportModal, setShowTenantImportModal] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await getCompanies({
        submarket: submarket || undefined,
        priority: priority || undefined,
        expansion_only: expansionOnly || undefined,
      })
      setCompanies(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [submarket, priority, expansionOnly])

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
        <label className="flex items-center gap-2 text-xs text-ink-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={expansionOnly}
            onChange={e => setExpansionOnly(e.target.checked)}
            className="accent-emerald-500"
          />
          Expansion Signal Only
        </label>
        {(submarket || priority || expansionOnly) && (
          <button
            onClick={() => { setSubmarket(''); setPriority(''); setExpansionOnly(false) }}
            className="flex items-center gap-1 text-xs text-ink-muted hover:text-red-400"
          >
            <X size={12} /> Clear
          </button>
        )}
      </div>

      {/* Cards grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
        {loading ? (
          <div className="col-span-3 text-center py-12 text-ink-muted">Loading...</div>
        ) : companies.length === 0 ? (
          <div className="col-span-3 text-center py-12 text-ink-muted">No companies found</div>
        ) : companies.map(c => (
          <div
            key={c.id}
            className="bg-surface-card border border-surface-border rounded-xl p-4 cursor-pointer
                       hover:border-accent-blue/40 transition-colors"
            onClick={() => setSelected(c)}
          >
            <div className="flex items-start justify-between mb-3">
              <div className="flex-1">
                <div className="font-semibold text-ink-primary text-sm">{c.name}</div>
                <div className="text-[11px] text-ink-muted mt-0.5">{c.industry}</div>
              </div>
              <PriorityBadge priority={c.priority} />
            </div>

            <div className="grid grid-cols-2 gap-3 mb-3">
              <div className="bg-surface-muted rounded-lg p-2">
                <div className="text-[10px] text-ink-muted mb-0.5">Opportunity Score</div>
                <ScoreBadge score={c.opportunity_score} size="md" showBar />
              </div>
              <div className="bg-surface-muted rounded-lg p-2">
                <div className="text-[10px] text-ink-muted mb-0.5">Headcount</div>
                <div className="text-sm font-semibold mono text-ink-primary">{c.current_headcount}</div>
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                  <TrendingUp size={11} />
                  Growth
                </div>
                <GrowthBadge pct={c.headcount_growth_pct} />
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                  <Clock size={11} />
                  Lease Expiry
                </div>
                <ExpiryBadge months={c.lease_expiry_months} />
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                  <MapPin size={11} />
                  Submarket
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

      {/* Detail panel */}
      {selected && (
        <div className="fixed inset-y-0 right-0 w-96 bg-surface-card border-l border-surface-border shadow-2xl z-50 overflow-y-auto">
          <div className="p-5">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="font-bold text-ink-primary text-base">{selected.name}</div>
                <div className="text-xs text-ink-muted mt-0.5">{selected.industry}</div>
              </div>
              <button onClick={() => setSelected(null)} className="text-ink-muted hover:text-ink-primary p-1">
                <X size={18} />
              </button>
            </div>

            <div className="space-y-3">
              <PriorityBadge priority={selected.priority} />

              <div className="bg-surface-muted rounded-lg p-3">
                <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Tenant Intelligence</div>
                <div className="space-y-1.5">
                  <Row label="Current Headcount"  value={String(selected.current_headcount)} />
                  <Row label="YoY Growth"         value={selected.headcount_growth_pct != null ? `+${selected.headcount_growth_pct.toFixed(0)}%` : '—'} />
                  <Row label="Lease Expiry"       value={selected.lease_expiry_months != null ? `${selected.lease_expiry_months} months` : '—'} />
                  <Row label="Submarket"          value={selected.current_submarket || '—'} />
                  <Row label="Expansion Signal"   value={selected.expansion_signal ? '✓ Active' : '—'} />
                </div>
              </div>

              <div className="bg-surface-muted rounded-lg p-3">
                <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Opportunity Score</div>
                <ScoreBadge score={selected.opportunity_score} size="lg" showBar />
              </div>
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
