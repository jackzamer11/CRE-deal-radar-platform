interface ScoreBadgeProps {
  score: number
  size?: 'sm' | 'md' | 'lg'
  showBar?: boolean
}

function scoreColor(score: number): string {
  if (score >= 75) return 'text-red-400'
  if (score >= 62) return 'text-amber-400'
  if (score >= 42) return 'text-blue-400'
  return 'text-slate-500'
}

function barColor(score: number): string {
  if (score >= 75) return 'bg-red-500'
  if (score >= 62) return 'bg-amber-500'
  if (score >= 42) return 'bg-blue-500'
  return 'bg-slate-600'
}

export default function ScoreBadge({ score, size = 'md', showBar = false }: ScoreBadgeProps) {
  const sizeClass = size === 'sm' ? 'text-sm' : size === 'lg' ? 'text-xl' : 'text-base'

  return (
    <div className="flex items-center gap-2">
      <span className={`font-bold mono ${sizeClass} ${scoreColor(score)}`}>
        {score.toFixed(0)}
      </span>
      {showBar && (
        <div className="w-20 h-1.5 bg-surface-border rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${barColor(score)}`}
            style={{ width: `${Math.min(score, 100)}%` }}
          />
        </div>
      )}
    </div>
  )
}
