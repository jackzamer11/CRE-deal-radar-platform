import type { SignalBreakdown } from '../types'

interface SignalBarProps {
  label: string
  value: number
  color?: string
}

function SignalBar({ label, value, color = '#3B82F6' }: SignalBarProps) {
  const barColor = value >= 75 ? '#EF4444' : value >= 55 ? '#F59E0B' : value >= 35 ? '#3B82F6' : '#374151'
  return (
    <div className="flex items-center gap-3">
      <div className="w-36 text-[11px] text-slate-400 truncate">{label}</div>
      <div className="flex-1 h-1.5 bg-surface-border rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${Math.min(value, 100)}%`, backgroundColor: barColor }}
        />
      </div>
      <div className="w-8 text-[11px] mono text-right" style={{ color: barColor }}>
        {value.toFixed(0)}
      </div>
    </div>
  )
}

interface Props {
  breakdown: SignalBreakdown
  predictionScore: number
  ownerBehaviorScore: number
  mispricingScore: number
}

export default function SignalBreakdownView({
  breakdown,
  predictionScore,
  ownerBehaviorScore,
  mispricingScore,
}: Props) {
  return (
    <div className="space-y-5">
      {/* A. Prediction */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-bold uppercase tracking-widest text-purple-400">
            A. Prediction Signals
          </div>
          <div className="text-[11px] text-purple-400 mono font-bold">
            {predictionScore.toFixed(0)}/100
          </div>
        </div>
        <div className="space-y-1.5">
          <SignalBar label="Lease Rollover"      value={breakdown.lease_rollover} />
          <SignalBar label="Vacancy Trend"       value={breakdown.vacancy_trend} />
          <SignalBar label="Ownership Duration"  value={breakdown.ownership_duration} />
          <SignalBar label="Leasing Drought"     value={breakdown.leasing_drought} />
          <SignalBar label="CapEx Gap"           value={breakdown.capex_gap} />
        </div>
      </div>

      {/* B. Owner Behavior */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-bold uppercase tracking-widest text-amber-400">
            B. Owner Behavior
          </div>
          <div className="text-[11px] text-amber-400 mono font-bold">
            {ownerBehaviorScore.toFixed(0)}/100
          </div>
        </div>
        <div className="space-y-1.5">
          <SignalBar label="Hold Period"          value={breakdown.hold_period} />
          <SignalBar label="Occupancy Decline"    value={breakdown.occupancy_decline} />
          <SignalBar label="Rent Stagnation"      value={breakdown.rent_stagnation} />
          <SignalBar label="Reinvestment Gap"     value={breakdown.reinvestment_inactivity} />
          <SignalBar label="Debt Pressure"        value={breakdown.debt_pressure} />
        </div>
      </div>

      {/* C. Mispricing */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-bold uppercase tracking-widest text-emerald-400">
            C. Mispricing
          </div>
          <div className="text-[11px] text-emerald-400 mono font-bold">
            {mispricingScore.toFixed(0)}/100
          </div>
        </div>
        <div className="space-y-1.5">
          <SignalBar label="In-Place vs Mkt Rent" value={breakdown.rent_gap} />
          <SignalBar label="Price/SF vs Comps"    value={breakdown.price_psf} />
          <SignalBar label="Days on Market"       value={breakdown.dom_premium} />
          <SignalBar label="Cap Rate Spread"      value={breakdown.cap_rate_spread} />
        </div>
      </div>
    </div>
  )
}
