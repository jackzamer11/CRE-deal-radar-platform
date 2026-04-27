import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Building2,
  Users,
  Crosshair,
  ClipboardList,
  Radio,
  RefreshCw,
} from 'lucide-react'
import { runPipeline } from '../../api/client'
import { useState } from 'react'

const navItems = [
  { to: '/',             icon: LayoutDashboard, label: 'Daily Briefing' },
  { to: '/properties',   icon: Building2,       label: 'Properties' },
  { to: '/companies',    icon: Users,            label: 'Companies' },
  { to: '/opportunities',icon: Crosshair,        label: 'Opportunities' },
  { to: '/activity',     icon: ClipboardList,    label: 'Activity Log' },
]

export default function Sidebar() {
  const [running, setRunning] = useState(false)
  const [lastRun, setLastRun] = useState<string | null>(null)

  const handlePipeline = async () => {
    setRunning(true)
    try {
      const result = await runPipeline()
      setLastRun(`${result.new_opportunities} new opps`)
    } catch {
      setLastRun('Error')
    } finally {
      setRunning(false)
    }
  }

  return (
    <aside className="flex flex-col w-60 min-h-screen bg-surface-card border-r border-surface-border">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-surface-border">
        <div className="w-8 h-8 rounded-lg bg-accent-blue flex items-center justify-center">
          <Radio size={16} className="text-white" />
        </div>
        <div>
          <div className="text-sm font-bold text-ink-primary leading-tight">Deal Radar OS</div>
          <div className="text-[10px] text-ink-muted uppercase tracking-widest">NoVA Office</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-4 px-2 space-y-0.5">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                isActive
                  ? 'bg-accent-blue/15 text-accent-blue font-medium'
                  : 'text-ink-secondary hover:bg-surface-muted hover:text-ink-primary'
              }`
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Pipeline refresh */}
      <div className="px-4 py-4 border-t border-surface-border">
        <button
          onClick={handlePipeline}
          disabled={running}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg
                     bg-surface-muted hover:bg-surface-hover text-ink-secondary hover:text-ink-primary
                     text-xs font-medium transition-colors disabled:opacity-50"
        >
          <RefreshCw size={13} className={running ? 'animate-spin' : ''} />
          {running ? 'Running Pipeline...' : 'Refresh Signals'}
        </button>
        {lastRun && (
          <div className="text-[10px] text-ink-muted text-center mt-1.5">Last: {lastRun}</div>
        )}
        <div className="text-[10px] text-ink-muted text-center mt-2 leading-relaxed">
          Targets: 3K–30K SF · Under $7M
          <br />
          Reston · Tysons · Arlington
          <br />
          Alexandria · Falls Church
        </div>
      </div>
    </aside>
  )
}
