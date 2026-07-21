import { useEffect, useState } from 'react'
import { Sparkles, RefreshCw, ArrowRight, AlertTriangle, CheckCircle2, Clock } from 'lucide-react'
import { getIntelOpportunities, generateIntelOpportunities } from '../api/client'
import type { IntelOpportunity } from '../types'

// Signal-type → visual treatment.
const SIGNAL_META: Record<string, { label: string; icon: React.ElementType; color: string }> = {
  lease_expiring:         { label: 'Lease Expiring',   icon: Clock,         color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' },
  expiration_unverified:  { label: 'Verify First',     icon: AlertTriangle, color: 'text-amber-400 bg-amber-500/10 border-amber-500/30' },
  stale_data:             { label: 'Incomplete Record', icon: CheckCircle2, color: 'text-ink-secondary bg-surface-muted border-surface-border' },
}

function primarySignal(opp: IntelOpportunity): string {
  return opp.signals[0]?.signal_type ?? 'lease_expiring'
}

function OppCard({ opp }: { opp: IntelOpportunity }) {
  const meta = SIGNAL_META[primarySignal(opp)] ?? SIGNAL_META.lease_expiring
  const Icon = meta.icon
  // Evidence link: deep-link to the Review page filtered to this entity's facts.
  const evidenceHref = `/review`

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold flex items-center gap-1 ${meta.color}`}>
              <Icon size={11} /> {meta.label}
            </span>
            <span className="text-[10px] text-ink-muted">{opp.entity_type} #{opp.entity_id}</span>
          </div>
          <div className="mt-1.5 text-sm font-bold text-ink-primary">{opp.title}</div>
        </div>
        <div className="text-right flex-shrink-0">
          <div className="text-lg font-bold text-accent-blue leading-none">{Math.round(opp.score)}</div>
          <div className="text-[9px] text-ink-muted uppercase tracking-wider mt-0.5">score</div>
        </div>
      </div>

      {opp.rationale && (
        <p className="mt-2.5 text-xs text-ink-secondary leading-relaxed">{opp.rationale}</p>
      )}

      <div className="mt-3 flex items-center gap-3 flex-wrap">
        <a
          href={evidenceHref}
          className="text-[10px] text-accent-blue hover:underline flex items-center gap-1"
        >
          View evidence <ArrowRight size={11} />
        </a>
        {opp.signals[0]?.evidence_observation_id != null && (
          <span className="text-[10px] text-ink-muted">
            observation #{opp.signals[0].evidence_observation_id}
          </span>
        )}
      </div>
    </div>
  )
}

export default function IntelPage() {
  const [opps, setOpps] = useState<IntelOpportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      setOpps(await getIntelOpportunities('open'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      await generateIntelOpportunities()
      await load()
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Sparkles size={20} className="text-blue-400" />
          <h1 className="text-xl font-bold text-ink-primary">Intel</h1>
          <span className="text-ink-muted text-sm">({opps.length} open)</span>
        </div>
        <button
          onClick={handleGenerate}
          disabled={generating}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-blue text-white text-xs font-semibold
                     hover:bg-accent-blueDim transition-colors disabled:opacity-50"
        >
          <RefreshCw size={13} className={generating ? 'animate-spin' : ''} />
          {generating ? 'Generating…' : 'Generate Opportunities'}
        </button>
      </div>

      <p className="text-[11px] text-ink-muted mb-5 leading-relaxed">
        Ranked from verified and unverified lease facts using date-based rules only —
        no AI scoring. Closer expirations rank higher; verified facts always outrank
        unverified ones.
      </p>

      {loading ? (
        <div className="text-center py-12 text-ink-muted">Loading…</div>
      ) : opps.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <Sparkles size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">No open opportunities.</p>
          <p className="text-xs mt-1 text-ink-muted">
            Click “Generate Opportunities” after facts have been reviewed.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {opps.map(opp => <OppCard key={opp.id} opp={opp} />)}
        </div>
      )}
    </div>
  )
}
