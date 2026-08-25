import { cn } from '@/lib/cn'
import { EMPTY, age, humanize, isNum, ms } from '@/lib/format'
import { Chip, Empty, Row, toneForState } from '@/components/ui/primitives'
import { useWorkstation } from '@/store/workstation'
import type { Alert, Security, ServiceRow } from '@/lib/types'

export function ServiceList({ services }: { services: ServiceRow[] | undefined }) {
  if (!services?.length) return <Empty label="No service heartbeats" field="services" />
  return (
    <div>
      {services.map((service) => (
        <div
          key={service.name}
          className="grid grid-cols-[1fr_auto_60px] items-baseline gap-2 border-b border-line/50 py-1 last:border-0"
        >
          <span className="min-w-0 truncate font-mono text-[10px] text-ink-2">{service.name}</span>
          <Chip tone={toneForState(service.status)}>{service.status ?? EMPTY}</Chip>
          <span className="tnum text-right text-[10px] text-ink-3">{ms(service.latency_ms)}</span>
        </div>
      ))}
    </div>
  )
}

/**
 * Production authorization state.
 *
 * Deliberately the loudest thing on the operations screen. Alpha-SPY keeps
 * real-money execution behind a sentinel file plus an evidence-bound approval
 * artifact, and an operator should never have to hunt for whether that lock is
 * currently open.
 */
export function SecurityPanel({ security }: { security: Security | undefined }) {
  if (!security) return <Empty label="No security state" field="security" />
  const locked = !security.live_authorization

  const facts: [string, boolean | undefined, string][] = [
    ['Production credential', security.production_credential_present, 'Tradier production access token'],
    ['Approval artifact', security.production_approval_valid, String(security.production_approval ?? '')],
    ['Production sentinel', security.production_sentinel_present, String(security.production_sentinel ?? '')],
    ['Order submission', security.submit_orders, 'engine may place broker orders'],
  ]

  return (
    <div className="flex flex-col gap-3">
      <div
        className={cn(
          'border-2 px-3 py-3 text-center',
          locked ? 'border-pass/40 bg-pass/5' : 'border-fail bg-fail/10',
        )}
      >
        <div className="kicker">Real money execution</div>
        <div
          className={cn(
            'mt-1 font-mono text-[18px] leading-none font-bold tracking-tight',
            locked ? 'text-pass' : 'text-fail',
          )}
        >
          {locked ? '🔒 PRODUCTION LOCKED' : '⚠ LIVE AUTHORIZED'}
        </div>
        <div className="mt-1 font-mono text-[9px] text-ink-3">
          {humanize(security.execution_mode)} · broker {security.broker_environment ?? EMPTY} · data{' '}
          {security.market_data_environment ?? EMPTY}
        </div>
      </div>

      <div>
        {facts.map(([label, present, hint]) => (
          <div
            key={label}
            className="flex items-baseline justify-between gap-2 border-b border-line/50 py-1 last:border-0"
            title={hint}
          >
            <span className="font-mono text-[10px] tracking-wide text-ink-3 uppercase">{label}</span>
            <span
              className={cn(
                'font-mono text-[10px] font-semibold',
                present ? 'text-watch' : 'text-ink-3',
              )}
            >
              {present ? 'PRESENT' : 'ABSENT'}
            </span>
          </div>
        ))}
        <Row label="Live authorization" value={locked ? 'DISABLED' : 'ENABLED'} tone={locked ? 'pass' : 'fail'} />
        <Row label="Automatic live enable" value="NEVER" tone="pass" />
      </div>

      {security.production_approval_reason && (
        <div className="border border-line bg-surface-2 px-2 py-1.5 font-mono text-[9px] leading-snug text-ink-3">
          {security.production_approval_reason}
        </div>
      )}
    </div>
  )
}

export function AlertFeed({ alerts, limit = 40 }: { alerts: Alert[] | undefined; limit?: number }) {
  const acknowledge = useWorkstation((store) => store.acknowledgeAlert)
  const adminToken = useWorkstation((store) => store.adminToken)
  if (!alerts?.length) return <Empty label="No alerts" field="alerts" />

  return (
    <div className="flex flex-col">
      {alerts.slice(0, limit).map((alert, index) => {
        const tone =
          alert.severity === 'critical' ? 'fail' : alert.severity === 'warning' ? 'watch' : 'signal'
        return (
          <div
            key={alert.id ?? `${alert.timestamp}-${index}`}
            className={cn(
              'border-l-2 border-b border-b-line/50 py-1.5 pr-2 pl-2 last:border-b-0',
              tone === 'fail' ? 'border-l-fail' : tone === 'watch' ? 'border-l-watch' : 'border-l-signal-dim',
              alert.acknowledged && 'opacity-45',
            )}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate text-[11px] font-semibold text-ink">{alert.title}</span>
              <span className="tnum shrink-0 font-mono text-[9px] text-ink-4">{age(alert.timestamp)}</span>
            </div>
            <div className="mt-0.5 text-[10px] leading-snug text-ink-2">{alert.message}</div>
            <div className="mt-0.5 flex items-center gap-2">
              <span className="font-mono text-[9px] text-ink-4">{alert.source}</span>
              {!alert.acknowledged && alert.id != null && adminToken && (
                <button
                  type="button"
                  onClick={() => void acknowledge(alert.id as number)}
                  className="font-mono text-[9px] text-ink-3 underline-offset-2 hover:text-signal hover:underline"
                >
                  acknowledge
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Per-section arrival ages. Makes a partially stalled feed visible. */
export function FeedFreshness() {
  const sectionUpdatedAt = useWorkstation((store) => store.sectionUpdatedAt)
  const entries = Object.entries(sectionUpdatedAt).sort(([a], [b]) => a.localeCompare(b))
  if (!entries.length) return <Empty label="No frames received" field="ws/live" />
  const now = Date.now()
  return (
    <div className="grid gap-x-6 sm:grid-cols-2">
      {entries.map(([name, at]) => {
        const seconds = (now - at) / 1000
        return (
          <div key={name} className="flex items-baseline justify-between gap-2 border-b border-line/50 py-1">
            <span className="font-mono text-[10px] text-ink-3">{name}</span>
            <span
              className={cn(
                'tnum font-mono text-[10px]',
                seconds > 120 ? 'text-fail' : seconds > 30 ? 'text-watch' : 'text-ink-2',
              )}
            >
              {isNum(seconds) ? `${seconds < 10 ? seconds.toFixed(1) : seconds.toFixed(0)}s` : EMPTY}
            </span>
          </div>
        )
      })}
    </div>
  )
}
