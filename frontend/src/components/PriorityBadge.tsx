import type { Priority, DealType, Confidence } from '../types'

interface PriorityBadgeProps {
  priority: Priority
}

const PRIORITY_CONFIG: Record<Priority, { label: string; classes: string }> = {
  IMMEDIATE: { label: '● IMMEDIATE', classes: 'bg-red-500/15 text-red-400 border border-red-500/30' },
  HIGH:      { label: '◆ HIGH',      classes: 'bg-amber-500/15 text-amber-400 border border-amber-500/30' },
  WORKABLE:  { label: '○ WORKABLE',  classes: 'bg-blue-500/15 text-blue-400 border border-blue-500/30' },
  IGNORE:    { label: '— IGNORE',    classes: 'bg-slate-800 text-slate-500 border border-slate-700' },
}

export function PriorityBadge({ priority }: PriorityBadgeProps) {
  const cfg = PRIORITY_CONFIG[priority] || PRIORITY_CONFIG.IGNORE
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide ${cfg.classes}`}>
      {cfg.label}
    </span>
  )
}


interface DealTypeBadgeProps {
  dealType: DealType | string
}

const DEAL_TYPE_CONFIG: Record<string, { label: string; classes: string }> = {
  PRE_MARKET:      { label: 'PRE-MARKET',  classes: 'bg-purple-500/15 text-purple-400 border border-purple-500/30' },
  ACTIVE_MISPRICED:{ label: 'MISPRICED',   classes: 'bg-amber-500/15 text-amber-400 border border-amber-500/30' },
  TENANT_DRIVEN:   { label: 'TENANT',      classes: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' },
}

export function DealTypeBadge({ dealType }: DealTypeBadgeProps) {
  const cfg = DEAL_TYPE_CONFIG[dealType] || { label: dealType, classes: 'bg-slate-800 text-slate-400 border border-slate-700' }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide ${cfg.classes}`}>
      {cfg.label}
    </span>
  )
}


interface ConfidenceBadgeProps {
  confidence: Confidence
}

const CONF_CONFIG: Record<Confidence, { label: string; classes: string }> = {
  HIGH:   { label: 'HIGH CONF',   classes: 'text-emerald-400' },
  MEDIUM: { label: 'MED CONF',    classes: 'text-blue-400' },
  LOW:    { label: 'LOW CONF',    classes: 'text-slate-500' },
}

export function ConfidenceBadge({ confidence }: ConfidenceBadgeProps) {
  const cfg = CONF_CONFIG[confidence]
  return (
    <span className={`text-[10px] font-bold uppercase tracking-wider ${cfg.classes}`}>
      {cfg.label}
    </span>
  )
}
