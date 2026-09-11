import { useEffect, useState } from 'react'
import {
  ArrowDownLeft, ArrowUpRight, Building2, Mail, Phone, TriangleAlert, Users, X,
} from 'lucide-react'
import { getCompanyTimeline } from '../api/client'
import type { Channel, CompanyTimelinePage } from '../types'

const CHANNEL_ICONS: Partial<Record<Channel, React.ElementType>> = {
  email: Mail, call: Phone, meeting: Users,
}

const fmtDate = (d: string) =>
  new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })

/**
 * Every entry stamped to a company, interleaved by date across all contacts —
 * including entries with no contact attached.
 *
 * Reads company_stamp_id, so a contact who has since changed jobs still has
 * their history here, on the company the conversation was actually about.
 */
export default function CompanyTimelinePanel({
  companyId, onClose, onOpenContact,
}: {
  companyId: string
  onClose: () => void
  onOpenContact: (contactId: number) => void
}) {
  const [page, setPage] = useState<CompanyTimelinePage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const PAGE = 100

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getCompanyTimeline(companyId, { limit: PAGE, offset: 0 })
      .then(p => { if (!cancelled) setPage(p) })
      .catch(() => { if (!cancelled) setError('Could not load this company timeline.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [companyId])

  const loadMore = async () => {
    if (!page) return
    const next = await getCompanyTimeline(companyId, {
      limit: PAGE, offset: page.entries.length,
    })
    setPage({ ...next, entries: [...page.entries, ...next.entries] })
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div
        className="w-full max-w-xl h-full bg-surface-base border-l border-surface-border
                   overflow-y-auto p-5"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <Building2 size={15} className="text-emerald-400 flex-shrink-0" />
              <h2 className="text-base font-bold text-ink-primary truncate">
                {page?.company_name ?? companyId}
              </h2>
              {page?.has_data_conflict && (
                <span className="text-[10px] px-2 py-0.5 rounded border font-semibold
                                 bg-amber-500/10 text-amber-400 border-amber-500/30
                                 flex items-center gap-1">
                  <TriangleAlert size={10} /> Data conflict
                </span>
              )}
            </div>
            <p className="text-[11px] text-ink-muted mt-0.5">
              {page ? `${page.total} entr${page.total === 1 ? 'y' : 'ies'} across all contacts` : ''}
            </p>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary flex-shrink-0">
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div className="text-center py-12 text-ink-muted text-sm">Loading…</div>
        ) : error ? (
          <div className="text-center py-12 text-ink-muted text-sm">{error}</div>
        ) : !page || page.entries.length === 0 ? (
          <div className="text-center py-12 text-ink-muted">
            <p className="text-sm">No entries stamped to this company yet.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {page.entries.map(e => {
              const inbound = e.direction === 'inbound'
              const Icon = CHANNEL_ICONS[e.channel ?? 'other']
              return (
                <div
                  key={e.id}
                  className={`border rounded-xl p-3 border-surface-border
                    ${inbound ? 'bg-violet-500/5 border-l-2 border-l-violet-500/60'
                              : 'bg-surface-card border-l-2 border-l-blue-500/40'}`}
                >
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase
                                      flex items-center gap-1
                      ${inbound ? 'bg-violet-500/20 text-violet-300' : 'bg-blue-500/15 text-blue-300'}`}>
                      {inbound ? <ArrowDownLeft size={9} /> : <ArrowUpRight size={9} />}
                      {inbound ? 'In' : 'Out'}
                    </span>
                    <span className="text-[10px] text-ink-muted flex items-center gap-1 uppercase font-bold">
                      {Icon && <Icon size={10} />}{e.channel ?? 'other'}
                    </span>
                    <span className="text-[10px] text-ink-muted">{fmtDate(e.log_date)}</span>
                    {/* An unattached entry (a voicemail to the main line) keeps
                        its place in the timeline rather than dropping out. */}
                    {e.contact_id && e.contact_name ? (
                      <button
                        onClick={() => onOpenContact(e.contact_id!)}
                        className="text-[10px] text-accent-blue hover:underline font-semibold"
                      >
                        {e.contact_name}
                      </button>
                    ) : (
                      <span className="text-[10px] text-ink-muted italic">no contact</span>
                    )}
                  </div>
                  <p className="text-xs text-ink-secondary">{e.action_taken}</p>
                  {e.outcome && <p className="text-xs text-ink-muted mt-1">→ {e.outcome}</p>}
                  {e.notes && <p className="text-[11px] text-ink-muted mt-1 italic">{e.notes}</p>}
                </div>
              )
            })}
            {page.entries.length < page.total && (
              <button
                onClick={loadMore}
                className="w-full text-[11px] px-3 py-1.5 rounded-lg bg-surface-card border
                           border-surface-border text-ink-muted hover:text-ink-primary"
              >
                Show older — {page.entries.length} of {page.total} shown
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
