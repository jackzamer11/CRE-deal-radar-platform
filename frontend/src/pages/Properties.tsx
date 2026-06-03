import { useEffect, useState, useRef } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Building2, Filter, RefreshCw, X, Plus, Upload, Pencil, Check, MessageSquarePlus, Clock } from 'lucide-react'
import {
  getProperties, getProperty, updatePropertyInPlaceRent, getPropertyOutreachHistory,
  unsnoozeProperty,
} from '../api/client'
import type { PropertyListOut, PropertyOut, OutreachLog } from '../types'
import { PriorityBadge } from '../components/PriorityBadge'
import ScoreBadge from '../components/ScoreBadge'
import AddPropertyModal from '../components/AddPropertyModal'
import LeaseCompsModal from '../components/LeaseCompsModal'
import CoStarImportModal from '../components/CoStarImportModal'
import OutreachDraftModal from '../components/OutreachDraftModal'
import SnoozeModal from '../components/SnoozeModal'
import { SUBMARKETS } from '../constants'

const PRIORITIES = ['IMMEDIATE', 'HIGH', 'WORKABLE', 'IGNORE']

const SCORE_TYPE_LABELS: Record<string, string> = {
  tenant_match:    'Tenant Match',
  for_sale_vacancy:'For Sale + Vacancy',
  acquisition:     'Acquisition',
}

function fmt(n: number | null | undefined, prefix = '', suffix = ''): string {
  if (n == null) return '—'
  return `${prefix}${n.toLocaleString()}${suffix}`
}
function fmtK(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${(n / 1000).toFixed(0)}K`
}
function fmtRent(n: number | null | undefined): string {
  if (n == null) return '—'
  return `$${n.toFixed(2)}`
}

// ── Inline rent pencil ─────────────────────────────────────────────────────

function RentPencilCell({
  propertyId,
  value,
  onUpdated,
}: {
  propertyId: string
  value: number | null | undefined
  onUpdated: (newVal: number) => void
}) {
  const [editing, setEditing]   = useState(false)
  const [input, setInput]       = useState(value != null ? String(value) : '')
  const [saving, setSaving]     = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const commit = async () => {
    const parsed = parseFloat(input)
    if (isNaN(parsed) || parsed <= 0) { setEditing(false); return }
    setSaving(true)
    try {
      await updatePropertyInPlaceRent(propertyId, { in_place_rent_psf: parsed, in_place_rent_source: 'manual' })
      onUpdated(parsed)
    } finally {
      setSaving(false)
      setEditing(false)
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          autoFocus
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
          className="w-16 text-xs mono bg-surface-muted border border-accent-blue/50 rounded px-1 py-0.5 text-ink-primary outline-none"
          placeholder="$/SF"
        />
        <button onClick={commit} disabled={saving} className="text-emerald-400 hover:text-emerald-300">
          {saving ? '…' : <Check size={11} />}
        </button>
        <button onClick={() => setEditing(false)} className="text-ink-muted hover:text-red-400">
          <X size={11} />
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1 group">
      <span className="mono text-xs text-ink-secondary">{fmtRent(value)}</span>
      <button
        onClick={e => { e.stopPropagation(); setEditing(true) }}
        title="Edit in-place rent"
        className={`opacity-0 group-hover:opacity-100 text-ink-muted hover:text-accent-blue transition-opacity ${value == null ? 'opacity-60' : ''}`}
      >
        <Pencil size={10} />
      </button>
    </div>
  )
}

// ── Score type badge ───────────────────────────────────────────────────────

function DominantBadge({ type }: { type: string | null | undefined }) {
  // Change 1: treat listing_rep as fallback to tenant_match
  const effective = type === 'listing_rep' ? 'tenant_match' : type
  if (!effective) return <span className="text-xs text-ink-muted">—</span>
  const colours: Record<string, string> = {
    tenant_match: 'bg-violet-500/15 text-violet-400 border-violet-500/30',
    acquisition:  'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  }
  const cls = colours[effective] ?? 'bg-surface-muted text-ink-muted border-surface-border'
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded border font-semibold ${cls}`}>
      {SCORE_TYPE_LABELS[effective] ?? effective}
    </span>
  )
}

// ── Main component ─────────────────────────────────────────────────────────

export default function Properties() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [properties, setProperties]         = useState<PropertyListOut[]>([])
  const [loading, setLoading]               = useState(true)
  const [submarket, setSubmarket]           = useState('')
  const [priority, setPriority]             = useState('')
  const [listedOnly, setListedOnly]         = useState<boolean | undefined>()
  const [scoreTypeFilter, setScoreTypeFilter] = useState('')
  const [needsOutreach, setNeedsOutreach]   = useState(false)
  const [selected, setSelected]             = useState<PropertyOut | null>(null)
  const [selectedLogs, setSelectedLogs]     = useState<OutreachLog[]>([])
  const [showAddModal, setShowAddModal]     = useState(false)
  const [editProperty, setEditProperty]     = useState<PropertyOut | null>(null)
  const [snoozeTarget, setSnoozeTarget]     = useState<PropertyOut | null>(null)
  const [showSnoozedOnly, setShowSnoozedOnly] = useState(false)
  const [undoToast, setUndoToast]           = useState<{
    propertyId: string; address: string; timer: ReturnType<typeof setTimeout>
  } | null>(null)
  const [showLeaseCompsModal, setShowLeaseCompsModal] = useState(false)
  const [showCoStarModal, setShowCoStarModal] = useState(false)
  const [outreachModal, setOutreachModal]   = useState<{
    prop: PropertyOut
    type: string
    targetType?: string
    tenantContext?: string
    pairCompanyId?: string
    pairTenantName?: string
  } | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const data = await getProperties({
        submarket: submarket || undefined,
        priority:  priority  || undefined,
        listed_for_sale: listedOnly,
        dominant_score_type: scoreTypeFilter || undefined,
        needs_outreach: needsOutreach || undefined,
      })
      setProperties(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [submarket, priority, listedOnly, scoreTypeFilter, needsOutreach])

  const handleSelect = async (p: PropertyListOut | PropertyOut) => {
    // Always re-fetch full PropertyOut to get matched_tenants + listed_for_lease.
    try {
      const full = await getProperty(p.property_id)
      setSelected(full)
    } catch {
      setSelected(p as PropertyOut)
    }
    try {
      const logs = await getPropertyOutreachHistory(p.property_id)
      setSelectedLogs(logs)
    } catch {
      setSelectedLogs([])
    }
  }

  // Auto-open via ?selected= URL param
  useEffect(() => {
    const id = searchParams.get('selected')
    if (id && (!selected || selected.property_id !== id)) {
      getProperty(id)
        .then(p => {
          setSelected(p)
          return getPropertyOutreachHistory(id).catch(() => [] as OutreachLog[])
        })
        .then(logs => { if (Array.isArray(logs)) setSelectedLogs(logs) })
        .catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const closePanel = () => {
    setSelected(null)
    if (searchParams.get('selected')) {
      const next = new URLSearchParams(searchParams)
      next.delete('selected')
      setSearchParams(next, { replace: true })
    }
  }

  // ── Snooze helpers ──────────────────────────────────────────────────────
  const applySnooze = (prop: PropertyOut) => {
    setSnoozeTarget(null)
    // Update local state
    setSelected(s => s?.property_id === prop.property_id ? prop : s)
    setProperties(ps => ps.map(p => p.property_id === prop.property_id
      ? { ...p, snoozed_until: prop.snoozed_until, snooze_reason: prop.snooze_reason } : p))
    // Show UNDO toast for 5 seconds
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
      const prop = await unsnoozeProperty(pid)
      setSelected(s => s?.property_id === pid ? prop : s)
      setProperties(ps => ps.map(p => p.property_id === pid
        ? { ...p, snoozed_until: null, snooze_reason: null } : p))
    } catch { /* ignore — state already cleared */ }
  }

  const handleUnsnoozeSelected = async () => {
    if (!selected) return
    try {
      const prop = await unsnoozeProperty(selected.property_id)
      setSelected(prop)
      setProperties(ps => ps.map(p => p.property_id === prop.property_id
        ? { ...p, snoozed_until: null, snooze_reason: null } : p))
    } catch { /* ignore */ }
  }

  const handleRentUpdated = (propertyId: string, newVal: number) => {
    setProperties(ps => ps.map(p =>
      p.property_id === propertyId ? { ...p, in_place_rent_psf: newVal } : p
    ))
    if (selected?.property_id === propertyId) {
      setSelected(s => s ? { ...s, in_place_rent_psf: newVal } : s)
    }
  }

  // ── Strict Outreach Routing (Change 6) ───────────────────────────────────
  // Decision tree — no manual override, no button if no path applies.
  type OutreachPlan = { type: string; targetType: string; label: string } | null

  const planOutreach = (p: PropertyOut): OutreachPlan => {
    const sfAvail = p.sf_avail ?? 0
    const hasMatched = (p.matched_tenants?.length ?? 0) > 0
    const dom = p.dominant_score_type === 'listing_rep' ? 'tenant_match' : p.dominant_score_type

    // Type 0: Owner confirmed open to leasing — always use tenant_match modal (Fix 1).
    // This gives the full Property Side / Tenant Side toggle + per-tenant tabs,
    // identical to off-market tenant match properties.
    if (p.owner_confirmed_leasing && sfAvail > 0 && hasMatched) {
      const targetType = p.landlord_representative ? 'broker' : 'owner'
      return {
        type: 'tenant_match',
        targetType,
        label: '⚡ Draft Tenant Outreach (Lease While Listed)',
      }
    }

    // Type 1: For Sale + Vacancy + matched tenants (Phase 1, not yet confirmed) → for_sale_vacancy
    if (sfAvail > 0 && p.listed_for_sale && hasMatched) {
      const targetType = p.sales_contact ? 'sales_broker' : 'owner'
      return {
        type: 'for_sale_vacancy',
        targetType,
        label: targetType === 'sales_broker'
          ? 'Draft Broker Outreach (For Sale + Vacancy)'
          : 'Draft Owner Outreach (For Sale + Vacancy)',
      }
    }

    // Type 2: Vacancy + matched tenants (not listed) → tenant_match
    if (sfAvail > 0 && !p.listed_for_sale && hasMatched) {
      const targetType = p.landlord_representative ? 'broker' : 'owner'
      return {
        type: 'tenant_match',
        targetType,
        label: targetType === 'broker'
          ? 'Draft Broker Outreach (Tenant Match)'
          : 'Draft Owner Outreach (Tenant Match)',
      }
    }

    // Type 3: Strong signal + acquisition dominant → acquisition
    if (p.signal_score >= 40 && dom === 'acquisition') {
      const targetType = p.sales_contact ? 'sales_broker' : 'owner'
      return {
        type: 'acquisition',
        targetType,
        label: targetType === 'sales_broker'
          ? 'Draft Broker Outreach (Acquisition)'
          : 'Draft Owner Outreach (Acquisition)',
      }
    }

    return null
  }

  const clearFilters = () => {
    setSubmarket(''); setPriority(''); setListedOnly(undefined)
    setScoreTypeFilter(''); setNeedsOutreach(false); setShowSnoozedOnly(false)
  }

  const anyFilter = submarket || priority || listedOnly !== undefined || scoreTypeFilter || needsOutreach || showSnoozedOnly

  // Client-side snooze filter (all properties are already loaded)
  const isSnoozedActive = (p: PropertyListOut) =>
    p.snoozed_until != null && p.snoozed_until > new Date().toISOString().slice(0, 10)

  const displayedProperties = showSnoozedOnly
    ? properties.filter(isSnoozedActive)
    : properties

  const colCount = 19

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Building2 size={20} className="text-accent-blue" />
          <h1 className="text-xl font-bold text-ink-primary">Properties</h1>
          <span className="text-ink-muted text-sm">({displayedProperties.length})</span>
          {needsOutreach && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/30 font-semibold">
              Top 20 Needing Outreach
            </span>
          )}
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
          <option value="">Listed + Off-Market</option>
          <option value="true">For Sale Only</option>
          <option value="false">Off-Market Only</option>
        </select>
        <select
          value={scoreTypeFilter}
          onChange={e => setScoreTypeFilter(e.target.value)}
          className="bg-surface-card border border-surface-border text-ink-secondary text-xs rounded-lg px-3 py-1.5"
        >
          <option value="">All Score Types</option>
          <option value="tenant_match">Tenant Match</option>
          <option value="acquisition">Acquisition</option>
        </select>
        <button
          onClick={() => setNeedsOutreach(v => !v)}
          className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border font-medium transition-colors
            ${needsOutreach
              ? 'bg-amber-500/15 text-amber-400 border-amber-500/40'
              : 'bg-surface-card border-surface-border text-ink-muted hover:text-ink-primary'}`}
        >
          <MessageSquarePlus size={12} />
          Top 20 Needing Outreach
        </button>
        <button
          onClick={() => setShowSnoozedOnly(v => !v)}
          className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border font-medium transition-colors
            ${showSnoozedOnly
              ? 'bg-amber-500/15 text-amber-400 border-amber-500/40'
              : 'bg-surface-card border-surface-border text-ink-muted hover:text-ink-primary'}`}
        >
          <Clock size={12} />
          Snoozed
        </button>
        {anyFilter && (
          <button
            onClick={clearFilters}
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
                <th>Yrs</th>
                <th>Vac%</th>
                <th>Rollover</th>
                <th>In-Place $/SF</th>
                <th>Mkt $/SF</th>
                <th>Avail SF</th>
                <th>Status</th>
                <th>Prediction</th>
                <th>Mispricing</th>
                <th>Signal</th>
                <th>TM</th>
                <th>Acq</th>
                <th>Dominant</th>
                <th>Priority</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={colCount} className="text-center py-8 text-ink-muted">Loading...</td></tr>
              ) : properties.length === 0 ? (
                <tr><td colSpan={colCount} className="text-center py-8 text-ink-muted">No properties found</td></tr>
              ) : displayedProperties.map(p => (
                <tr key={p.id} onClick={() => handleSelect(p)}>
                  <td className="mono text-xs text-accent-blue">{p.property_id}</td>
                  <td className="max-w-xs">
                    <div className="truncate text-ink-primary font-medium" title={p.address}>{p.address}</div>
                    <div className="text-[10px] text-ink-muted">{p.asset_class}</div>
                    {showSnoozedOnly && p.snooze_reason && (
                      <div className="text-[10px] text-amber-400 truncate">{p.snooze_reason}</div>
                    )}
                  </td>
                  <td className="text-ink-secondary text-xs">{p.submarket}</td>
                  <td className="mono text-xs">{fmtK(p.total_sf)}</td>
                  <td className="max-w-[120px]">
                    <div className="truncate text-xs" title={p.owner_name}>{p.owner_name}</div>
                  </td>
                  <td className="mono text-xs">
                    {p.years_owned != null ? `${p.years_owned.toFixed(1)}yr` : '—'}
                  </td>
                  <td>
                    {p.occupancy_pct == null ? (
                      <span className="mono text-xs text-ink-muted">—</span>
                    ) : (
                      <span className={`mono text-xs font-semibold ${
                        p.occupancy_pct < 55 ? 'text-red-400' :
                        p.occupancy_pct < 75 ? 'text-amber-400' : 'text-emerald-400'
                      }`}>
                        {(100 - p.occupancy_pct).toFixed(0)}%
                      </span>
                    )}
                  </td>
                  <td className="mono text-xs">{p.lease_rollover_pct.toFixed(0)}%</td>
                  <td onClick={e => e.stopPropagation()}>
                    <RentPencilCell
                      propertyId={p.property_id}
                      value={p.in_place_rent_psf}
                      onUpdated={v => handleRentUpdated(p.property_id, v)}
                    />
                  </td>
                  <td className="mono text-xs text-ink-secondary">{fmtRent(p.market_rent_psf)}</td>
                  <td className="mono text-xs text-ink-secondary">{fmt(p.sf_avail, '', ' SF')}</td>
                  <td>
                    <div className="flex flex-col gap-1">
                      {p.snoozed_until && isSnoozedActive(p) && (
                        <span className="text-[10px] bg-amber-500/15 text-amber-400 border border-amber-500/30 px-2 py-0.5 rounded font-semibold flex items-center gap-1">
                          <Clock size={9} /> SNOOZED
                        </span>
                      )}
                      {p.listed_for_sale ? (
                        <span className="text-[10px] bg-amber-500/15 text-amber-400 border border-amber-500/30 px-2 py-0.5 rounded font-semibold">
                          FOR SALE
                        </span>
                      ) : null}
                      {p.owner_confirmed_leasing && p.listed_for_sale ? (
                        <span className="text-[10px] bg-amber-400/20 text-amber-300 border border-amber-400/40 px-2 py-0.5 rounded font-semibold">
                          LEASE WHILE LISTED
                        </span>
                      ) : null}
                      {p.listed_for_lease ? (
                        <span className="text-[10px] bg-violet-500/15 text-violet-400 border border-violet-500/30 px-2 py-0.5 rounded font-semibold">
                          FOR LEASE
                        </span>
                      ) : null}
                      {!p.listed_for_sale && !p.listed_for_lease && !isSnoozedActive(p) && (
                        <span className="text-[10px] bg-surface-muted text-ink-muted border border-surface-border px-2 py-0.5 rounded">
                          OFF-MKT
                        </span>
                      )}
                    </div>
                  </td>
                  <td><ScoreBadge score={p.prediction_score} size="sm" showBar /></td>
                  <td><ScoreBadge score={p.mispricing_score} size="sm" showBar /></td>
                  <td><ScoreBadge score={p.signal_score} size="sm" showBar /></td>
                  <td><ScoreBadge score={p.tenant_match_score} size="sm" showBar /></td>
                  <td><ScoreBadge score={p.acquisition_score}  size="sm" showBar /></td>
                  <td><DominantBadge type={p.dominant_score_type} /></td>
                  <td><PriorityBadge priority={p.priority} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Modals */}
      {showAddModal && (
        <AddPropertyModal
          onClose={() => setShowAddModal(false)}
          onSaved={(_saved: PropertyOut) => { setShowAddModal(false); load() }}
        />
      )}
      {editProperty && (
        <AddPropertyModal
          editPropertyId={editProperty.property_id}
          initialData={editProperty}
          onClose={() => setEditProperty(null)}
          onSaved={prop => {
            setEditProperty(null)
            setSelected(prop)
            setProperties(ps => ps.map(p => p.property_id === prop.property_id ? prop : p))
          }}
        />
      )}
      {snoozeTarget && (
        <SnoozeModal
          propertyId={snoozeTarget.property_id}
          propertyAddress={snoozeTarget.address}
          onClose={() => setSnoozeTarget(null)}
          onSnoozed={applySnooze}
        />
      )}

      {/* UNDO toast — 5 second window after snooze confirmed */}
      {undoToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3
                        bg-surface-card border border-amber-500/40 rounded-xl px-5 py-3 shadow-2xl
                        animate-in slide-in-from-bottom-2 duration-200">
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
      {showLeaseCompsModal && (
        <LeaseCompsModal onClose={() => setShowLeaseCompsModal(false)} onDone={load} />
      )}
      {showCoStarModal && (
        <CoStarImportModal onClose={() => setShowCoStarModal(false)} onDone={load} />
      )}
      {outreachModal && (() => {
        const p = outreachModal.prop
        // Recipient pre-fill (Change 7) — pick by target_type
        const tt = outreachModal.targetType
        const recipientName =
          tt === 'broker'        ? (p.landlord_representative ?? '') :
          tt === 'sales_broker'  ? '' :
          tt === 'owner'         ? (p.owner_name ?? '') :
          ''
        const looksLikeEmail = (s: string | null | undefined) => !!s && /@/.test(s)
        const recipientEmail =
          tt === 'broker'        ? (looksLikeEmail(p.landlord_rep_contact) ? (p.landlord_rep_contact as string) : '') :
          tt === 'sales_broker'  ? (looksLikeEmail(p.sales_contact)        ? (p.sales_contact as string)        : '') :
          tt === 'owner'         ? (p.owner_email ?? '') :
          ''
        return (
          <OutreachDraftModal
            entity_type="property"
            property={p}
            outreach_type={outreachModal.type}
            target_type={outreachModal.targetType}
            tenant_context={outreachModal.tenantContext}
            pair_company_id={outreachModal.pairCompanyId}
            pair_tenant_name={outreachModal.pairTenantName}
            recipient_name={recipientName}
            recipient_email={recipientEmail}
            matched_tenants={p.matched_tenants}
            onClose={() => setOutreachModal(null)}
            onSaved={() => { setOutreachModal(null); load() }}
          />
        )
      })()}

      {/* Detail panel */}
      {selected && (() => {
        const plan = planOutreach(selected)
        const forSaleLine = selected.listed_for_sale
          ? `Yes${selected.asking_price ? ` ($${(selected.asking_price/1_000_000).toFixed(2)}M${selected.asking_price_psf ? ` / $${selected.asking_price_psf.toFixed(0)}/SF` : ''})` : ''}`
          : 'No'
        const forLeaseLine = selected.listed_for_lease
          ? `Yes (${(selected.sf_avail || 0).toLocaleString()} SF avail${selected.market_rent_psf ? ` @ $${selected.market_rent_psf.toFixed(0)}/SF` : ''})`
          : 'No'
        return (
        <div className="fixed inset-y-0 right-0 w-[420px] bg-surface-card border-l border-surface-border
                        shadow-2xl z-50 overflow-y-auto">
          <div className="p-5">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="text-xs text-accent-blue mono mb-1">{selected.property_id}</div>
                <div className="font-bold text-ink-primary">{selected.address}</div>
                <div className="text-xs text-ink-muted mt-0.5">{selected.submarket} · {selected.asset_class}</div>
              </div>
              <div className="flex items-center gap-0.5">
                <button
                  onClick={() => setEditProperty(selected)}
                  title="Edit property"
                  className="text-ink-muted hover:text-accent-blue p-1 rounded transition-colors"
                >
                  <Pencil size={15} />
                </button>
                <button
                  onClick={() => setSnoozeTarget(selected)}
                  title={selected.snoozed_until ? 'Edit snooze' : 'Snooze property'}
                  className="text-ink-muted hover:text-amber-400 p-1 rounded transition-colors"
                >
                  <Clock size={15} />
                </button>
                <button onClick={closePanel} className="text-ink-muted hover:text-ink-primary p-1">
                  <X size={18} />
                </button>
              </div>
            </div>

            <div className="space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <PriorityBadge priority={selected.priority} />
                <DominantBadge type={selected.dominant_score_type} />
                {selected.owner_confirmed_leasing && selected.listed_for_sale && (
                  <span className="text-[10px] px-2 py-0.5 rounded border font-semibold bg-amber-400/20 text-amber-300 border-amber-400/40">
                    ⚡ LEASE WHILE LISTED
                  </span>
                )}
              </div>

              {/* Snoozed badge with inline unsnooze */}
              {selected.snoozed_until && new Date(selected.snoozed_until) > new Date() && (
                <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/30">
                  <Clock size={12} className="text-amber-400 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <span className="text-[11px] text-amber-400 font-semibold">
                      SNOOZED until {new Date(selected.snoozed_until).toLocaleDateString()}
                    </span>
                    {selected.snooze_reason && (
                      <div className="text-[10px] text-ink-muted truncate">{selected.snooze_reason}</div>
                    )}
                  </div>
                  <button
                    onClick={handleUnsnoozeSelected}
                    title="Remove snooze"
                    className="text-amber-400/60 hover:text-amber-400 flex-shrink-0 transition-colors"
                  >
                    <X size={13} />
                  </button>
                </div>
              )}

              {/* Traditional scores */}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-surface-muted rounded-lg p-3">
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-1">Signal</div>
                  <ScoreBadge score={selected.signal_score} size="lg" showBar />
                </div>
                <div className="bg-surface-muted rounded-lg p-3">
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-1">Prediction</div>
                  <ScoreBadge score={selected.prediction_score} size="lg" showBar />
                </div>
              </div>

              {/* Outreach scores (Change 1: Listing Rep hidden) */}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-violet-500/8 border border-violet-500/20 rounded-lg p-2">
                  <div className="text-[9px] text-violet-400 uppercase tracking-wider mb-1">Tenant Match</div>
                  <ScoreBadge score={selected.tenant_match_score} size="sm" showBar />
                </div>
                <div className="bg-emerald-500/8 border border-emerald-500/20 rounded-lg p-2">
                  <div className="text-[9px] text-emerald-400 uppercase tracking-wider mb-1">Acquisition</div>
                  <ScoreBadge score={selected.acquisition_score} size="sm" showBar />
                </div>
              </div>

              {/* Smart Draft Outreach button — strict routing, no manual override (Change 6) */}
              {plan ? (
                <button
                  onClick={() => {
                    // Pair with top matched tenant for tenant_match / for_sale_vacancy
                    const top = (plan.type === 'tenant_match' || plan.type === 'for_sale_vacancy')
                      ? selected.matched_tenants?.[0]
                      : undefined
                    const tCtx = top
                      ? `Industry: ${top.industry}; Headcount: ${top.headcount ?? 'N/A'}; SF Needed: ${top.sf_needed.toLocaleString()}; Submarket: ${top.submarket ?? 'N/A'}`
                      : undefined
                    setOutreachModal({
                      prop: selected,
                      type: plan.type,
                      targetType: plan.targetType,
                      tenantContext: tCtx,
                      pairCompanyId: top?.company_id,
                      pairTenantName: top?.name,
                    })
                  }}
                  className="w-full flex items-center justify-center gap-2 py-2 rounded-lg
                             bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold transition-colors"
                >
                  <MessageSquarePlus size={13} />
                  {plan.label}
                </button>
              ) : (
                <div className="w-full text-center py-2 rounded-lg bg-surface-muted border border-surface-border
                                text-[11px] text-ink-muted">
                  No outreach path — needs vacancy + matched tenants, or signal ≥ 40 + acquisition dominant.
                </div>
              )}

              <div className="bg-surface-muted rounded-lg p-3 space-y-2">
                <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">Property Details</div>
                <Row label="Total SF"    value={fmt(selected.total_sf, '', ' SF')} />
                <Row label="Avail SF"    value={fmt(selected.sf_avail, '', ' SF')} />
                <Row label="Vacancy"     value={selected.occupancy_pct == null ? '—' : `${(100 - selected.occupancy_pct).toFixed(0)}%`} />
                <Row label="In-Place $/SF" value={fmtRent(selected.in_place_rent_psf)} />
                <Row label="Market $/SF"   value={fmtRent(selected.market_rent_psf)} />
                <Row label="Rollover"    value={`${selected.lease_rollover_pct.toFixed(0)}% (12mo)`} />
                <Row label="Owner"       value={selected.owner_name} />
                <Row label="Held"        value={selected.years_owned ? `${selected.years_owned.toFixed(1)} years` : '—'} />
                <Row label="For Sale"    value={forSaleLine} />
                <Row label="For Lease"   value={forLeaseLine} />
                {selected.landlord_representative && (
                  <Row label="Landlord Rep" value={selected.landlord_representative} />
                )}
                {selected.landlord_rep_contact && (
                  <Row label="Rep Contact" value={selected.landlord_rep_contact} />
                )}
                {selected.sales_contact && (
                  <Row label="Sales Contact" value={selected.sales_contact} />
                )}
                {selected.star_rating != null && (
                  <Row label="Star Rating" value={`${selected.star_rating}★`} />
                )}
              </div>

              {/* Matched Tenants section — Phase 1 vs Phase 2 mode (Fix 3) */}
              {(() => {
                const tenants = selected.matched_tenants ?? []
                // Phase 1: listed for sale + vacancy + NOT yet confirmed leasing.
                // Show a single lead-tenant card (industry only, no company name).
                const isPhase1 = selected.listed_for_sale &&
                  (selected.sf_avail ?? 0) > 0 &&
                  !selected.owner_confirmed_leasing
                // Phase 2: owner_confirmed_leasing=true — show all tenants with names
                const displayTenants = isPhase1 ? tenants.slice(0, 1) : tenants

                return (
                  <div className="bg-surface-muted rounded-lg p-3">
                    <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">
                      {isPhase1
                        ? 'LEAD TENANT — INDUSTRY REFERENCE ONLY'
                        : `MATCHED TENANTS (${tenants.length})`}
                    </div>

                    {tenants.length === 0 ? (
                      <div className="text-xs text-ink-muted">
                        No matched tenants yet — add tenants to the platform to generate outreach.
                      </div>
                    ) : (
                      <>
                        {isPhase1 && (
                          <div className="mb-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30
                                          text-[10px] text-amber-300 leading-relaxed">
                            Once the owner confirms interest in leasing, this will expand to full
                            Tenant Match mode with top 3 matches.
                          </div>
                        )}
                        <div className="space-y-2">
                          {displayTenants.map(t => {
                            let outreachType = 'tenant_match'
                            let targetType = 'owner'
                            let label = 'Draft Owner Outreach (Tenant Match)'
                            if (selected.listed_for_sale && (selected.sf_avail ?? 0) > 0) {
                              outreachType = 'for_sale_vacancy'
                              targetType = selected.sales_contact ? 'sales_broker' : 'owner'
                              label = targetType === 'sales_broker'
                                ? 'Draft Broker Outreach (For Sale + Vacancy)'
                                : 'Draft Owner Outreach (For Sale + Vacancy)'
                            } else if (selected.landlord_representative) {
                              targetType = 'broker'
                              label = 'Draft Broker Outreach (Tenant Match)'
                            }
                            const tCtx = `Industry: ${t.industry}; Headcount: ${t.headcount ?? 'N/A'}; SF Needed: ${t.sf_needed.toLocaleString()}; Submarket: ${t.submarket ?? 'N/A'}`
                            return (
                              <div
                                key={t.company_id}
                                className={`border border-surface-border rounded-lg p-2 transition-colors
                                  ${!isPhase1 ? 'hover:bg-surface-card cursor-pointer' : ''}`}
                                onClick={!isPhase1 ? () => navigate(`/companies?selected=${t.company_id}`) : undefined}
                              >
                                <div className="flex items-start justify-between gap-2">
                                  <div className="min-w-0 flex-1">
                                    {/* Phase 1: hide company name; Phase 2: show it */}
                                    {!isPhase1 && (
                                      <div className="text-xs font-semibold text-ink-primary truncate">{t.name}</div>
                                    )}
                                    <div className="text-[10px] text-ink-muted">
                                      {t.industry}
                                      {isPhase1
                                        ? ` · ${t.sf_needed.toLocaleString()} SF`
                                        : ` · ${t.headcount ?? '—'} HC · ${t.sf_needed.toLocaleString()} SF needed`}
                                    </div>
                                    {/* Match reasons only shown in Phase 2 */}
                                    {!isPhase1 && t.match_reasons.length > 0 && (
                                      <div className="flex flex-wrap gap-1 mt-1">
                                        {t.match_reasons.map((r, i) => (
                                          <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-surface-card text-ink-secondary border border-surface-border">
                                            {r}
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                  <div className="flex-shrink-0 text-right">
                                    <ScoreBadge score={t.match_score} size="sm" />
                                  </div>
                                </div>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setOutreachModal({
                                      prop: selected,
                                      type: outreachType,
                                      targetType,
                                      tenantContext: tCtx,
                                      pairCompanyId: t.company_id,
                                      pairTenantName: t.name,
                                    })
                                  }}
                                  className="mt-2 w-full text-[10px] py-1.5 rounded-lg border border-emerald-500/40
                                             text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                                >
                                  {label}
                                </button>
                              </div>
                            )
                          })}
                        </div>
                      </>
                    )}
                  </div>
                )
              })()}

              {/* Outreach history */}
              {selectedLogs.length > 0 && (
                <div className="bg-surface-muted rounded-lg p-3">
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-2">
                    Outreach History ({selectedLogs.length})
                  </div>
                  <div className="space-y-2">
                    {selectedLogs.slice(0, 5).map(log => (
                      <div key={log.id} className="text-xs border-b border-surface-border pb-2 last:border-0 last:pb-0">
                        <div className="flex items-center justify-between">
                          <span className="text-ink-secondary font-medium truncate">{log.email_subject}</span>
                          <span className="text-[9px] text-ink-muted ml-2 shrink-0">
                            {new Date(log.generated_at).toLocaleDateString()}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[9px] text-ink-muted">{log.outreach_type}</span>
                          {log.marked_contacted && (
                            <span className="text-[9px] text-emerald-400">contacted</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
        )
      })()}
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
