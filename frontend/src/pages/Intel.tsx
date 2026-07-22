import { useEffect, useState } from 'react'
import {
  Sparkles, RefreshCw, ArrowRight, AlertTriangle, CheckCircle2, Clock,
  ThumbsUp, ThumbsDown, PauseCircle, X, BookmarkPlus,
} from 'lucide-react'
import {
  getIntelOpportunities, generateIntelOpportunities,
  dispositionIntelOpportunity, getIntelHistory, saveIntelCriterion,
} from '../api/client'
import type { IntelOpportunity, IntelHistoryItem, IntelDisposition } from '../types'

// Signal-type → visual treatment.
const SIGNAL_META: Record<string, { label: string; icon: React.ElementType; color: string }> = {
  lease_expiring:         { label: 'Lease Expiring',    icon: Clock,         color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' },
  expiration_unverified:  { label: 'Verify First',      icon: AlertTriangle, color: 'text-amber-400 bg-amber-500/10 border-amber-500/30' },
  stale_data:             { label: 'Incomplete Record', icon: CheckCircle2,  color: 'text-ink-secondary bg-surface-muted border-surface-border' },
}

// Reason categories for reject/defer (one tap).
const REASON_CATEGORIES: { value: string; label: string }[] = [
  { value: 'durable_policy', label: 'Standing policy' },
  { value: 'conditional',    label: 'Conditional' },
  { value: 'relational',     label: 'Relationship' },
  { value: 'timing',         label: 'Timing' },
  { value: 'already_known',  label: 'Already known' },
  { value: 'other',          label: 'Other' },
]

const DISPOSITION_STYLE: Record<string, string> = {
  accepted: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
  rejected: 'text-red-400 bg-red-500/10 border-red-500/30',
  deferred: 'text-amber-400 bg-amber-500/10 border-amber-500/30',
}

function primarySignal(opp: IntelOpportunity): string {
  return opp.signals[0]?.signal_type ?? 'lease_expiring'
}

function SignalBadge({ opp }: { opp: IntelOpportunity }) {
  const meta = SIGNAL_META[primarySignal(opp)] ?? SIGNAL_META.lease_expiring
  const Icon = meta.icon
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold flex items-center gap-1 ${meta.color}`}>
      <Icon size={11} /> {meta.label}
    </span>
  )
}

// ── Open opportunity card with disposition controls ──────────────────────────
function OppCard({
  opp,
  onDispositioned,
}: {
  opp: IntelOpportunity
  onDispositioned: (id: number, suggestedRule: string | null) => void
}) {
  // null = showing Accept/Reject/Defer; 'rejected'/'deferred' = reason picker open.
  const [reasonFor, setReasonFor] = useState<IntelDisposition | null>(null)
  const [category, setCategory] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (disposition: IntelDisposition, reasonCategory?: string) => {
    setBusy(true)
    try {
      const res = await dispositionIntelOpportunity(opp.id, {
        disposition,
        reason_category: reasonCategory,
        reason_text: text.trim() || undefined,
      })
      onDispositioned(opp.id, res.suggested_rule)
    } catch {
      setBusy(false)
    }
  }

  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <SignalBadge opp={opp} />
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

      <div className="mt-2 flex items-center gap-3 flex-wrap">
        <a href="/review" className="text-[10px] text-accent-blue hover:underline flex items-center gap-1">
          View evidence <ArrowRight size={11} />
        </a>
        {opp.signals[0]?.evidence_observation_id != null && (
          <span className="text-[10px] text-ink-muted">observation #{opp.signals[0].evidence_observation_id}</span>
        )}
      </div>

      {/* Disposition controls */}
      {reasonFor === null ? (
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <button
            onClick={() => submit('accepted')}
            disabled={busy}
            className="flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-lg bg-emerald-600
                       hover:bg-emerald-700 text-white font-semibold disabled:opacity-50"
          >
            <ThumbsUp size={12} /> Accept
          </button>
          <button
            onClick={() => { setReasonFor('rejected'); setCategory(null); setText('') }}
            disabled={busy}
            className="flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted
                       hover:bg-surface-hover text-red-400 font-semibold border border-surface-border disabled:opacity-50"
          >
            <ThumbsDown size={12} /> Reject
          </button>
          <button
            onClick={() => { setReasonFor('deferred'); setCategory(null); setText('') }}
            disabled={busy}
            className="flex items-center gap-1.5 text-[10px] px-3 py-1.5 rounded-lg bg-surface-muted
                       hover:bg-surface-hover text-amber-400 font-semibold border border-surface-border disabled:opacity-50"
          >
            <PauseCircle size={12} /> Defer
          </button>
        </div>
      ) : (
        <div className="mt-3 border-t border-surface-border pt-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold text-ink-secondary uppercase tracking-wider">
              Why {reasonFor === 'rejected' ? 'reject' : 'defer'}? (pick one)
            </span>
            <button onClick={() => setReasonFor(null)} className="text-ink-muted hover:text-ink-primary">
              <X size={13} />
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {REASON_CATEGORIES.map(rc => (
              <button
                key={rc.value}
                onClick={() => setCategory(rc.value)}
                className={`text-[10px] px-2.5 py-1 rounded-full border font-semibold transition-colors
                  ${category === rc.value
                    ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                    : 'bg-surface-muted text-ink-muted border-surface-border hover:text-ink-secondary'}`}
              >
                {rc.label}
              </button>
            ))}
          </div>
          <input
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder="Optional note…"
            className="w-full text-xs bg-surface-muted border border-surface-border rounded-lg px-3 py-2
                       text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue/50"
          />
          <button
            onClick={() => category && submit(reasonFor, category)}
            disabled={busy || !category}
            className="text-[10px] px-3 py-1.5 rounded-lg bg-accent-blue hover:bg-accent-blueDim
                       text-white font-semibold disabled:opacity-40"
          >
            {busy ? 'Saving…' : `Confirm ${reasonFor === 'rejected' ? 'Reject' : 'Defer'}`}
          </button>
        </div>
      )}
    </div>
  )
}

// ── History card (read-only) ─────────────────────────────────────────────────
function HistoryCard({ item }: { item: IntelHistoryItem }) {
  const disp = item.disposition ?? 'rejected'
  return (
    <div className="bg-surface-card border border-surface-border rounded-xl p-4 opacity-90">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold capitalize ${DISPOSITION_STYLE[disp] ?? ''}`}>
              {disp}
            </span>
            <span className="text-[10px] text-ink-muted">{item.entity_type} #{item.entity_id}</span>
          </div>
          <div className="mt-1.5 text-sm font-semibold text-ink-primary">{item.title}</div>
        </div>
        <div className="text-right flex-shrink-0 text-ink-muted">
          <div className="text-sm font-bold leading-none">{Math.round(item.score)}</div>
        </div>
      </div>
      {(item.reason_category || item.reason_text) && (
        <p className="mt-2 text-[11px] text-ink-muted">
          {item.reason_category && <span className="font-semibold capitalize">{item.reason_category.replace('_', ' ')}</span>}
          {item.reason_text && <span> — “{item.reason_text}”</span>}
        </p>
      )}
    </div>
  )
}

export default function IntelPage() {
  const [tab, setTab] = useState<'open' | 'history'>('open')
  const [opps, setOpps] = useState<IntelOpportunity[]>([])
  const [history, setHistory] = useState<IntelHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [ruleSuggestion, setRuleSuggestion] = useState<string | null>(null)
  const [ruleSaved, setRuleSaved] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [o, h] = await Promise.all([getIntelOpportunities('open'), getIntelHistory()])
      setOpps(o)
      setHistory(h)
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

  const handleDispositioned = (id: number, suggestedRule: string | null) => {
    setOpps(prev => prev.filter(o => o.id !== id))
    getIntelHistory().then(setHistory)
    if (suggestedRule) {
      setRuleSuggestion(suggestedRule)
      setRuleSaved(false)
    }
  }

  const handleSaveRule = async () => {
    if (!ruleSuggestion) return
    await saveIntelCriterion(ruleSuggestion, 'durable_policy')
    setRuleSaved(true)
  }

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Sparkles size={20} className="text-blue-400" />
          <h1 className="text-xl font-bold text-ink-primary">Intel</h1>
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

      {/* Standing-rule suggestion banner */}
      {ruleSuggestion && (
        <div className="mb-5 bg-accent-blue/10 border border-accent-blue/30 rounded-xl p-4 flex items-start gap-3">
          <BookmarkPlus size={18} className="text-accent-blue flex-shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-ink-primary">Save as a standing rule?</div>
            <p className="text-[11px] text-ink-secondary mt-0.5">
              You've rejected for this reason more than once: “{ruleSuggestion}”
            </p>
            {ruleSaved ? (
              <div className="text-[11px] text-emerald-400 mt-2 font-semibold">✓ Saved as a standing rule.</div>
            ) : (
              <div className="flex items-center gap-2 mt-2">
                <button
                  onClick={handleSaveRule}
                  className="text-[10px] px-3 py-1.5 rounded-lg bg-accent-blue hover:bg-accent-blueDim text-white font-semibold"
                >
                  Save rule
                </button>
                <button
                  onClick={() => setRuleSuggestion(null)}
                  className="text-[10px] text-ink-muted hover:text-ink-primary"
                >
                  Dismiss
                </button>
              </div>
            )}
          </div>
          <button onClick={() => setRuleSuggestion(null)} className="text-ink-muted hover:text-ink-primary">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex items-center gap-1.5 mb-5">
        {(['open', 'history'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`text-[11px] px-3 py-1 rounded-full border font-semibold capitalize transition-colors
              ${tab === t ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/50'
                          : 'bg-surface-card text-ink-muted border-surface-border hover:text-ink-secondary'}`}
          >
            {t} <span className="ml-1 text-ink-muted">{t === 'open' ? opps.length : history.length}</span>
          </button>
        ))}
      </div>

      {tab === 'open' && (
        <p className="text-[11px] text-ink-muted mb-5 leading-relaxed">
          Ranked from verified and unverified lease facts using date-based rules only — no AI scoring.
          Every accept/reject/defer is recorded with its reason.
        </p>
      )}

      {loading ? (
        <div className="text-center py-12 text-ink-muted">Loading…</div>
      ) : tab === 'open' ? (
        opps.length === 0 ? (
          <div className="text-center py-12 text-ink-muted">
            <Sparkles size={32} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm">No open opportunities.</p>
            <p className="text-xs mt-1 text-ink-muted">Click “Generate Opportunities” after facts have been reviewed.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {opps.map(opp => (
              <OppCard key={opp.id} opp={opp} onDispositioned={handleDispositioned} />
            ))}
          </div>
        )
      ) : history.length === 0 ? (
        <div className="text-center py-12 text-ink-muted">
          <p className="text-sm">No decisions yet.</p>
          <p className="text-xs mt-1 text-ink-muted">Accepted, rejected, and deferred opportunities show up here.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {history.map(item => <HistoryCard key={item.id} item={item} />)}
        </div>
      )}
    </div>
  )
}
