import { useEffect, useState } from 'react'
import { Building2, Filter, RefreshCw, X, Plus, Upload } from 'lucide-react'
import { getProperties } from '../api/client'
import type { PropertyListOut, PropertyOut } from '../types'
import { PriorityBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'
import AddPropertyModal from '../components/AddPropertyModal'
import BulkUploadModal from '../components/BulkUploadModal'
import CoStarImportModal from '../components/CoStarImportModal'

const SUBMARKETS = [
  'Arlington (Clarendon)',
  'Arlington (Rosslyn)',
  'Arlington (Ballston)',
  'Arlington (Columbia Pike)',
  'Alexandria (Old Town)',
  'Tysons',
  'Reston',
  'Falls Church',
]

const PRIORITIES = ['IMMEDIATE', 'HIGH', 'WORKABLE', 'IGNORE']

function fmt(n: number | null | undefined, prefix = '', suffix = ''): string {
  if (n == null) return '—'
  return `${prefix}${n.toLocaleString()}${suffix}`
}

function fmtK(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${(n / 1000).toFixed(0)}K`
}

export default function Properties() {
  const [properties, setProperties] = useState<PropertyListOut[]>([])
  const [loading, setLoading] = useState(true)
  const [submarket, setSubmarket] = useState('')
  const [priority, setPriority] = useState('')
  const [listedOnly, setListedOnly] = useState<boolean | undefined>()
  const [selected, setSelected] = useState<PropertyListOut | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showBulkModal, setShowBulkModal] = useState(false)
  const [showCoStarModal, setShowCoStarModal] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await getProperties({
        submarket: submarket || undefined,
        priority: priority || undefined,
        is_listed: listedOnly,
      })
      setProperties(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [submarket, priority, listedOnly])

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Building2 size={20} className="text-accent-blue" />
          <h1 className="text-xl font-bold text-ink-primary">Properties</h1>
          <span className="text-ink-muted text-sm">({properties.length})</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-blue text-white text-xs font-semibold
                       hover:bg-accent-blueDim transition-colors"
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
            <Upload size={13} /> Import CoStar Export
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
          {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          value={listedOnly === undefined ? '' : String(listedOnly)}
          onChange={e => setListedOnly(e.target.value === '' ? undefined : e.target.value === 'true')}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">Listed + Unlisted</option>
          <option value="true">Listed Only</option>
          <option value="false">Off-Market Only</option>
        </select>
        {(submarket || priority || listedOnly !== undefined) && (
          <button
            onClick={() => { setSubmarket(''); setPriority(''); setListedOnly(undefined) }}
            className="flex items-center gap-1 text-xs text-ink-muted hover:text-red-400"
          >
            <X size={12} /> Clear
          </button>
        )}
      </div>

      {/* Table */}
      <div className="bg-surface-card border border-surface-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Address</th>
                <th>Submarket</th>
                <th>SF</th>
                <th>Owner</th>
                <th>Yrs Owned</th>
                <th>Vacancy</th>
                <th>Rollover %</th>
                <th>In-Place $/SF</th>
                <th>Mkt $/SF</th>
                <th>Status</th>
                <th>Prediction</th>
                <th>Mispricing</th>
                <th>Signal</th>
                <th>Priority</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={15} className="text-center py-8 text-ink-muted">Loading...</td></tr>
              ) : properties.length === 0 ? (
                <tr><td colSpan={15} className="text-center py-8 text-ink-muted">No properties found</td></tr>
              ) : properties.map(p => (
                <tr key={p.id} onClick={() => setSelected(p)}>
                  <td className="mono text-xs text-accent-blue">{p.property_id}</td>
                  <td className="max-w-xs">
                    <div className="truncate text-ink-primary font-medium" title={p.address}>{p.address}</div>
                    <div className="text-[10px] text-ink-muted">{p.asset_class}</div>
                  </td>
                  <td className="text-ink-secondary text-xs">{p.submarket}</td>
                  <td className="mono text-xs">{fmtK(p.total_sf)}</td>
                  <td className="max-w-[140px]">
                    <div className="truncate text-xs" title={p.owner_name}>{p.owner_name}</div>
                  </td>
                  <td className="mono text-xs">
                    {p.years_owned != null ? `${p.years_owned.toFixed(1)}yr` : '—'}
                  </td>
                  <td>
                    <span className={`mono text-xs font-semibold ${
                      p.occupancy_pct < 55 ? 'text-red-400' :
                      p.occupancy_pct < 75 ? 'text-amber-400' : 'text-emerald-400'
                    }`}>
                      {(100 - p.occupancy_pct).toFixed(0)}%
                    </span>
                  </td>
                  <td className="mono text-xs">{p.lease_rollover_pct.toFixed(0)}%</td>
                  <td className="mono text-xs text-ink-secondary">—</td>
                  <td className="mono text-xs text-ink-secondary">—</td>
                  <td>
                    {p.is_listed ? (
                      <span className="text-[10px] bg-amber-500/15 text-amber-400 border border-amber-500/30 px-2 py-0.5 rounded font-semibold">
                        LISTED
                      </span>
                    ) : (
                      <span className="text-[10px] bg-surface-muted text-ink-muted border border-surface-border px-2 py-0.5 rounded">
                        OFF-MKT
                      </span>
                    )}
                  </td>
                  <td><ScoreBadge score={p.prediction_score} size="sm" showBar /></td>
                  <td><ScoreBadge score={p.mispricing_score} size="sm" showBar /></td>
                  <td><ScoreBadge score={p.signal_score} size="sm" showBar /></td>
                  <td><PriorityBadge priority={p.priority} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

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

      {showCoStarModal && (
        <CoStarImportModal
          onClose={() => setShowCoStarModal(false)}
          onDone={load}
        />
      )}

      {/* Detail panel */}
      {selected && (
        <div className="fixed inset-y-0 right-0 w-96 bg-surface-card border-l border-surface-border
                        shadow-2xl z-50 overflow-y-auto">
          <div className="p-5">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="text-xs text-accent-blue mono mb-1">{selected.property_id}</div>
                <div className="font-bold text-ink-primary">{selected.address}</div>
                <div className="text-xs text-ink-muted mt-0.5">{selected.submarket} · {selected.asset_class}</div>
              </div>
              <button onClick={() => setSelected(null)} className="text-ink-muted hover:text-ink-primary p-1">
                <X size={18} />
              </button>
            </div>

            <div className="space-y-3">
              <PriorityBadge priority={selected.priority} />

              <div className="grid grid-cols-2 gap-3">
                <div className="bg-surface-muted rounded-lg p-3">
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-1">Signal Score</div>
                  <ScoreBadge score={selected.signal_score} size="lg" showBar />
                </div>
                <div className="bg-surface-muted rounded-lg p-3">
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-1">Prediction</div>
                  <ScoreBadge score={selected.prediction_score} size="lg" showBar />
                </div>
              </div>

              <div className="bg-surface-muted rounded-lg p-3 space-y-2">
                <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Property Details</div>
                <Row label="Total SF"    value={fmt(selected.total_sf, '', ' SF')} />
                <Row label="Vacancy"     value={`${(100 - selected.occupancy_pct).toFixed(0)}%`} />
                <Row label="Rollover"    value={`${selected.lease_rollover_pct.toFixed(0)}% (12mo)`} />
                <Row label="Owner"       value={selected.owner_name} />
                <Row label="Held"        value={selected.years_owned ? `${selected.years_owned.toFixed(1)} years` : '—'} />
                <Row label="Listed"      value={selected.is_listed ? 'Yes' : 'Off-Market'} />
              </div>

              {selected.notes && (
                <div className="bg-surface-muted rounded-lg p-3">
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-1">Intel</div>
                  <p className="text-xs text-ink-secondary leading-relaxed">{selected.notes}</p>
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
