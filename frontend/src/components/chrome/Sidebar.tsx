import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { NAV } from '@/nav'
import { STATIC_DEMO, useWorkstation } from '@/store/workstation'

const STATUS_COPY: Record<string, { text: string; dot: string }> = {
  live: { text: 'LIVE STREAM', dot: 'bg-pass' },
  connecting: { text: 'CONNECTING', dot: 'bg-watch animate-pulse' },
  reconnecting: { text: 'RECONNECTING', dot: 'bg-watch animate-pulse' },
  unauthorized: { text: 'LOCKED', dot: 'bg-fail' },
}

export function Sidebar() {
  const status = useWorkstation((store) => store.status)
  const engine = useWorkstation((store) => store.state.engine)
  const seq = useWorkstation((store) => store.seq)
  const copy = STATUS_COPY[status] ?? STATUS_COPY.connecting!

  return (
    <aside className="flex w-[186px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <div className="flex h-7 w-7 items-center justify-center border border-signal/40 bg-signal/10 font-mono text-[13px] font-bold text-signal">
          A
        </div>
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[11px] font-bold tracking-[0.12em] text-ink">ALPHA-SPY</div>
          <div className="kicker truncate">Workstation</div>
        </div>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto py-1">
        {NAV.map((group) => (
          <div key={group.title} className="mb-1">
            <div className="kicker px-3 py-1">{group.title}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === '/'}
                className={({ isActive }) =>
                  cn(
                    'block border-l-2 py-[5px] pr-2 pl-3 text-[11px] transition-colors',
                    isActive
                      ? 'border-l-signal bg-signal/8 font-semibold text-signal'
                      : 'border-l-transparent text-ink-2 hover:bg-surface-2 hover:text-ink',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t border-line px-3 py-2">
        {STATIC_DEMO && (
          // Unmissable on the published preview, absent from the real
          // deployment: nobody should mistake a frozen snapshot for a live desk.
          <div className="mb-2 border border-watch/40 bg-watch/10 px-2 py-1 font-mono text-[9px] leading-snug text-watch">
            STATIC PREVIEW
            <span className="mt-0.5 block text-[8px] text-ink-3">
              Frozen synthetic snapshot. No live data, commands disabled.
            </span>
          </div>
        )}
        <div className="flex items-center gap-1.5">
          <span className={cn('h-1.5 w-1.5 rounded-full', copy.dot)} />
          <span className="font-mono text-[9px] tracking-[0.1em] text-ink-2">
            {STATIC_DEMO ? 'SNAPSHOT' : copy.text}
          </span>
        </div>
        <div className="mt-0.5 font-mono text-[9px] text-ink-4">
          v{engine?.version ?? '—'} · frame #{seq}
        </div>
      </div>
    </aside>
  )
}
