import { ArrowRight } from 'lucide-react'
import type { ActivityStage } from '../types'

const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''

const STAGE_TEXT: Record<ActivityStage, string> = {
  'Sent':           'text-blue-300',
  'Replied':        'text-violet-300',
  'Interested':     'text-emerald-300',
  'In Play':        'text-amber-300',
  'Not Interested': 'text-red-300',
  'Dormant':        'text-ink-secondary',
  'Closed':         'text-teal-300',
}

/**
 * A stage transition, rendered as a thin inline rule rather than a card.
 *
 * A stage change has no direction and no channel and is not outreach, so it
 * gets no direction badge, no channel badge, and none of the OUT / OTHER
 * chrome a real entry carries. Six pill clicks used to produce six full cards
 * and bury the conversation underneath them; the backend collapses a burst into
 * the net move, and this renders whatever survives as one quiet line.
 */
export default function StageChangeDivider({
  stageFrom, stageTo, logDate, actionTaken,
}: {
  stageFrom: string | null
  stageTo: string | null
  logDate: string | null
  /** Pre-columns rows carry the transition only as prose — fall back to it. */
  actionTaken?: string
}) {
  const from = stageFrom as ActivityStage | null
  const to = stageTo as ActivityStage | null

  return (
    <div className="flex items-center gap-2 py-1" aria-label="Stage change">
      <div className="h-px flex-1 bg-surface-border" />
      <span className="text-[10px] text-ink-muted flex items-center gap-1.5 whitespace-nowrap">
        {from && to ? (
          <>
            <span className={STAGE_TEXT[from] ?? 'text-ink-muted'}>{from}</span>
            <ArrowRight size={9} className="text-ink-muted" />
            <span className={`font-semibold ${STAGE_TEXT[to] ?? 'text-ink-muted'}`}>{to}</span>
          </>
        ) : (
          <span>{actionTaken ?? 'Stage changed'}</span>
        )}
        {logDate && <span className="text-ink-muted/70">· {fmtDate(logDate)}</span>}
      </span>
      <div className="h-px flex-1 bg-surface-border" />
    </div>
  )
}
