import { cn } from '@/lib/cn'
import { clamp, threshold as fmtThreshold, titleize } from '@/lib/format'
import { Bar, Chip, Empty } from '@/components/ui/primitives'
import type { Promotion, Replay, ValidationGate } from '@/lib/types'

/**
 * Gate groups, in launch-checklist order.
 *
 * Each entry is a prefix/substring test against the gate name emitted by
 * `alpha_spy.validation`. Anything unmatched lands in OTHER rather than being
 * dropped, so a gate added to the backend still appears here without a
 * frontend change.
 */
const GROUPS: { title: string; match: (name: string) => boolean }[] = [
  {
    title: 'Data',
    match: (n) => /snapshot_fraction|required_input|pq_ready|verified_data|paper_sessions/.test(n),
  },
  { title: 'Forecast', match: (n) => /sample$|matured_forecasts|trained_signal/.test(n) },
  {
    title: 'Calibration',
    match: (n) => /direction|brier|interval_coverage/.test(n),
  },
  {
    title: 'Execution',
    match: (n) => /trades|fill|slippage|reconciliation|max_loss_holds|sandbox/.test(n),
  },
  {
    title: 'Performance',
    match: (n) => /pnl|profit_factor|expectancy|drawdown|doubled_cost/.test(n),
  },
  { title: 'Regime coverage', match: (n) => /regime/.test(n) },
  { title: 'Replay', match: (n) => /replay/.test(n) },
]

function group(gates: ValidationGate[]): { title: string; gates: ValidationGate[] }[] {
  const claimed = new Set<string>()
  const blocks = GROUPS.map(({ title, match }) => {
    const matched = gates.filter((gate) => !claimed.has(gate.name) && match(gate.name))
    for (const gate of matched) claimed.add(gate.name)
    return { title, gates: matched }
  }).filter((block) => block.gates.length > 0)

  const rest = gates.filter((gate) => !claimed.has(gate.name))
  if (rest.length) blocks.push({ title: 'Other', gates: rest })
  return blocks
}

function GateRow({ gate }: { gate: ValidationGate }) {
  return (
    <div
      className="grid grid-cols-[14px_1fr_auto_auto] items-baseline gap-2 py-[3px] font-mono text-[10px]"
      title={gate.detail || undefined}
    >
      <span className={cn('text-center', gate.passed ? 'text-pass' : 'text-fail')}>
        {gate.passed ? '✓' : '✕'}
      </span>
      <span className={cn('min-w-0 truncate', gate.passed ? 'text-ink-2' : 'text-fail')}>
        {titleize(gate.name)}
      </span>
      <span
        className={cn('tnum max-w-[140px] truncate text-right', gate.passed ? 'text-ink' : 'text-fail')}
        title={fmtThreshold(gate.actual)}
      >
        {fmtThreshold(gate.actual)}
      </span>
      {/* Some thresholds are prose ("no loss > 1.15x modeled max"). Clamped and
          tooltipped so one verbose gate cannot reflow the whole checklist. */}
      <span className="tnum w-24 truncate text-right text-ink-4" title={fmtThreshold(gate.threshold)}>
        / {fmtThreshold(gate.threshold)}
      </span>
    </div>
  )
}

export function ValidationChecklist({
  promotion,
  replay,
  className,
}: {
  promotion: Promotion | undefined
  replay?: Replay
  className?: string
}) {
  const gates = promotion?.gates ?? []
  if (!gates.length) {
    return (
      <div className={className}>
        <Empty
          label={
            promotion?.status && promotion.status !== 'NOT_RUN'
              ? `Validation ${promotion.status} — no gate detail published`
              : 'No validation run recorded'
          }
          field="promotion.gates"
        />
      </div>
    )
  }

  const passed = gates.filter((gate) => gate.passed).length
  const ratio = clamp(passed / gates.length, 0, 1)
  const complete = passed === gates.length

  return (
    <div className={cn('flex min-h-0 flex-col gap-3', className)}>
      <div>
        <div className="mb-1 flex items-baseline justify-between gap-3">
          <span className="kicker">Overall</span>
          <span className="tnum text-[13px] font-semibold text-ink">
            {passed} / {gates.length} ·{' '}
            <span className={complete ? 'text-pass' : 'text-watch'}>{(ratio * 100).toFixed(0)}%</span>
          </span>
        </div>
        <Bar value={ratio} tone={complete ? 'pass' : ratio > 0.8 ? 'watch' : 'fail'} className="h-2" />
      </div>

      <div className="grid min-h-0 gap-3 overflow-auto">
        {group(gates).map((block) => (
          <div key={block.title}>
            <div className="kicker mb-0.5 border-b border-line pb-0.5">{block.title}</div>
            {block.gates.map((gate) => (
              <GateRow key={gate.name} gate={gate} />
            ))}
          </div>
        ))}
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-1.5 border-t border-line pt-2">
        <Chip tone={complete ? 'pass' : 'watch'}>{promotion?.status ?? 'UNKNOWN'}</Chip>
        {replay?.status && (
          <Chip tone={replay.status === 'PASSED' ? 'pass' : 'fail'}>
            replay {String(replay.status).toLowerCase()}
            {replay.mismatches != null && ` · ${replay.mismatches} mismatch`}
          </Chip>
        )}
        {/* Promotion never flips live trading on by itself; the screen says so. */}
        <Chip tone="muted">manual live review required</Chip>
      </div>
    </div>
  )
}
