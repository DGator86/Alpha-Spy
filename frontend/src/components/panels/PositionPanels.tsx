import { cn } from '@/lib/cn'
import { EMPTY, clock, humanize, isNum, money, pct, signedMoney, titleize } from '@/lib/format'
import { Chip, Empty, Metric, Row, pnlTone, toneForState } from '@/components/ui/primitives'
import type { Market, Position } from '@/lib/types'

export function PositionSummary({ position }: { position: Position | undefined }) {
  if (!position?.open) {
    return <Empty label="Flat — no managed position" field="position.open" />
  }
  const pnl = position.pnl
  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div>
        <div className="text-[13px] font-semibold text-ink">{titleize(position.strategy)}</div>
        <div className="font-mono text-[10px] text-ink-3">
          {position.description ?? EMPTY} · opened {clock(position.opened_at)}
        </div>
      </div>

      <div className={cn('tnum text-[30px] leading-none font-bold', pnlTone(pnl) === 'pass' ? 'text-pass' : pnlTone(pnl) === 'fail' ? 'text-fail' : 'text-ink')}>
        {signedMoney(pnl, 2)}
      </div>

      <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
        <Metric label="Entry" value={money(position.entry_debit)} className="border-0" size="sm" />
        <Metric label="Current" value={money(position.current_value)} className="border-0" size="sm" />
        <Metric label="MFE" value={signedMoney(position.mfe, 0)} tone="pass" className="border-0" size="sm" />
        <Metric label="MAE" value={signedMoney(position.mae, 0)} tone="fail" className="border-0" size="sm" />
        <Metric label="Max profit" value={money(position.max_profit, 0)} className="border-0" size="sm" />
        <Metric label="Max loss" value={money(position.max_loss, 0)} className="border-0" size="sm" />
        <Metric label="Target" value={signedMoney(position.profit_target, 0)} className="border-0" size="sm" />
        <Metric label="Stop" value={signedMoney(position.stop_loss, 0)} className="border-0" size="sm" />
      </div>

      <div>
        <div className="kicker mb-1">Legs</div>
        {(position.legs ?? []).map((leg, index) => (
          <div
            key={`${leg.symbol ?? index}`}
            className="flex items-baseline justify-between gap-2 border-b border-line/50 py-1 font-mono text-[10px] last:border-0"
          >
            <Chip tone={String(leg.side).toUpperCase() === 'SELL' ? 'fail' : 'pass'}>{leg.side}</Chip>
            <span className="min-w-0 flex-1 truncate text-ink-2">{leg.symbol}</span>
            <span className="tnum text-ink">
              {leg.right ?? leg.type ?? ''} {leg.strike} ×{leg.quantity ?? 1}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Live thesis checks.
 *
 * These are evaluated client-side from published market state and are labelled
 * as derived, not as engine output — the engine publishes a single
 * `thesis_status`, and inventing per-check authority it never claimed would
 * misrepresent it.
 */
export function ThesisChecklist({ market, position }: { market: Market | undefined; position: Position | undefined }) {
  if (!position?.open) return <Empty label="No open thesis" field="position.open" />

  const checks: [string, boolean | null][] = [
    ['Direction', isNum(market?.probability_up) ? Number(market?.probability_up) > 0.5 : null],
    ['Breadth', isNum(market?.breadth) ? Number(market?.breadth) > 0.55 : null],
    ['Correlation', isNum(market?.correlation) ? Number(market?.correlation) < 0.75 : null],
    ['Volatility edge', isNum(market?.vol_gap) ? Number(market?.vol_gap) < 0.015 : null],
    ['Liquidity', market?.liquidity_state ? String(market.liquidity_state).toUpperCase() === 'NORMAL' : null],
    ['Event window', market?.event_state ? !/HIGH|BLOCK/i.test(String(market.event_state)) : null],
  ]

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="kicker">Engine thesis</span>
        <Chip tone={toneForState(position.thesis_status)}>{position.thesis_status ?? EMPTY}</Chip>
      </div>
      {checks.map(([label, ok]) => (
        <div key={label} className="flex items-baseline justify-between gap-2 border-b border-line/50 py-1 last:border-0">
          <span className="font-mono text-[10px] tracking-wide text-ink-3 uppercase">{label}</span>
          <span className={cn('font-mono text-[10px] font-semibold', ok === null ? 'text-ink-4' : ok ? 'text-pass' : 'text-watch')}>
            {ok === null ? EMPTY : ok ? 'VALID' : 'WATCH'}
          </span>
        </div>
      ))}
      <div className="mt-1.5 font-mono text-[9px] leading-snug text-ink-4">
        Derived on the client from published market state. The engine's own
        verdict is the badge above.
      </div>
    </div>
  )
}

/** The eight-rung exit ladder, driven by whatever `management_state` publishes. */
const EXIT_RUNGS: [string, string][] = [
  ['catastrophic_stop', 'Catastrophic stop'],
  ['short_strike_threat', 'Short-strike threat'],
  ['thesis_invalidation', 'Thesis invalidation'],
  ['iv_edge_invalidation', 'IV-edge invalidation'],
  ['move_completion', 'Move completion'],
  ['mfe_trailing', 'MFE trailing'],
  ['time_stop', 'Time stop'],
  ['forced_flat', 'Forced flat'],
]

export function ExitEngine({ position, forcedFlat }: { position: Position | undefined; forcedFlat?: string | null }) {
  if (!position?.open) return <Empty label="Exit engine idle" field="position.management_state" />
  const state = (position.management_state ?? {}) as Record<string, unknown>
  const published = Object.keys(state).length > 0

  return (
    <div>
      {EXIT_RUNGS.map(([key, label], index) => {
        const raw = state[key]
        const value =
          raw !== undefined
            ? typeof raw === 'boolean'
              ? raw
                ? 'TRIGGERED'
                : 'CLEAR'
              : String(raw)
            : key === 'forced_flat'
              ? (forcedFlat ?? EMPTY)
              : null
        return (
          <div key={key} className="flex items-baseline justify-between gap-2 border-b border-line/50 py-1 last:border-0">
            <span className="min-w-0 truncate font-mono text-[10px] text-ink-3">
              <span className="text-ink-4">{index + 1}</span> {label}
            </span>
            {value === null ? (
              <span className="font-mono text-[9px] text-ink-4">not published</span>
            ) : (
              <Chip tone={toneForState(value)}>{humanize(value)}</Chip>
            )}
          </div>
        )
      })}
      <div className="mt-1.5 flex items-baseline justify-between border-t border-line pt-1.5">
        <span className="kicker">Engine recommendation</span>
        <span className="font-mono text-[10px] font-semibold text-signal">
          {humanize(position.exit_recommendation)}
        </span>
      </div>
      {!published && (
        <div className="mt-1 font-mono text-[9px] text-ink-4">
          Rungs show as unpublished until the engine writes{' '}
          <span className="text-signal-dim">position.management_state</span>.
        </div>
      )}
    </div>
  )
}

export function PositionGreeks({ position }: { position: Position | undefined }) {
  const greeks = ((position?.management_state as Record<string, unknown>)?.greeks ?? null) as Record<string, number> | null
  if (!position?.open || !greeks) return <Empty label="No live greeks" field="position.management_state.greeks" />
  return (
    <div className="grid grid-cols-4 gap-px bg-line">
      {(['delta', 'gamma', 'theta', 'vega'] as const).map((key) => (
        <Metric key={key} label={key} value={isNum(greeks[key]) ? Number(greeks[key]).toFixed(3) : EMPTY} className="border-0" size="sm" />
      ))}
    </div>
  )
}

export function RiskBudget({ position, allowedRisk }: { position: Position | undefined; allowedRisk: number | null | undefined }) {
  return (
    <div>
      <Row label="Allowed risk" value={money(allowedRisk, 0)} />
      <Row label="Position max loss" value={money(position?.max_loss, 0)} tone="fail" />
      <Row
        label="Budget used"
        value={
          isNum(allowedRisk) && isNum(position?.max_loss) && Number(allowedRisk) > 0
            ? pct(Number(position?.max_loss) / Number(allowedRisk))
            : EMPTY
        }
      />
    </div>
  )
}
