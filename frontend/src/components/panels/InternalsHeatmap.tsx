import { cn } from '@/lib/cn'
import { EMPTY, isNum, num, pct, signedPct } from '@/lib/format'
import { Bar, Empty } from '@/components/ui/primitives'
import type { ConstituentRow, Market, Num } from '@/lib/types'

interface Gauge {
  label: string
  value: Num
  /** Rendered scale; `bipolar` series draw from a centre baseline. */
  min: number
  max: number
  bipolar?: boolean
  format: (value: Num) => string
  hint?: string
}

function GaugeRow({ gauge }: { gauge: Gauge }) {
  return (
    <div className="grid grid-cols-[128px_1fr_68px] items-center gap-2 py-[3px]" title={gauge.hint}>
      <span className="truncate font-mono text-[9px] tracking-[0.1em] text-ink-3 uppercase">
        {gauge.label}
      </span>
      <Bar
        value={gauge.value}
        min={gauge.min}
        max={gauge.max}
        bipolar={gauge.bipolar}
        tone={gauge.bipolar ? 'auto' : 'signal'}
      />
      <span className="tnum text-right text-[11px] text-ink">{gauge.format(gauge.value)}</span>
    </div>
  )
}

export function IndexStateHeatmap({ market, className }: { market: Market | undefined; className?: string }) {
  const gauges: Gauge[] = [
    { label: 'Breadth', value: market?.breadth, min: 0, max: 1, format: (v) => pct(v, 0) },
    { label: 'Weighted pressure', value: market?.pressure, min: -2, max: 2, bipolar: true, format: (v) => num(v, 2) },
    { label: 'Correlation', value: market?.correlation, min: 0, max: 1, format: (v) => num(v, 2) },
    { label: 'Downside correlation', value: market?.downside_correlation, min: 0, max: 1, format: (v) => num(v, 2) },
    { label: 'Dispersion', value: market?.dispersion, min: 0, max: 0.03, format: (v) => num(v, 4) },
    { label: 'Concentration', value: market?.concentration, min: 0, max: 1, format: (v) => pct(v, 0) },
    { label: 'Assimilation', value: (market as { assimilation_speed?: Num })?.assimilation_speed, min: 0, max: 1, format: (v) => pct(v, 0) },
  ]
  const populated = gauges.some((gauge) => isNum(gauge.value))
  if (!populated) return <Empty label="No index internals" field="market.breadth" />
  return (
    <div className={className}>
      {gauges.map((gauge) => (
        <GaugeRow key={gauge.label} gauge={gauge} />
      ))}
    </div>
  )
}

export function VolatilityState({ market, className }: { market: Market | undefined; className?: string }) {
  const rows: [string, Num, string][] = [
    ['SPY IV', market?.spy_iv, 'market implied'],
    ['Synthetic IV', market?.constituent_iv, 'constituent-built index IV'],
    ['Physical vol', market?.physical_vol, 'model realised forecast'],
    ['IV premium', market?.vol_gap, 'market minus model'],
    ['Skew gap', market?.skew_gap, 'market minus synthetic'],
  ]
  if (!rows.some(([, value]) => isNum(value))) {
    return <Empty label="No volatility surface" field="market.spy_iv" />
  }
  return (
    <div className={className}>
      {rows.map(([label, value, hint]) => {
        const isGap = label.includes('premium') || label.includes('gap')
        return (
          <div
            key={label}
            className="flex items-baseline justify-between gap-3 border-b border-line/50 py-1 last:border-0"
            title={hint}
          >
            <span className="font-mono text-[9px] tracking-[0.1em] text-ink-3 uppercase">{label}</span>
            <span
              className={cn(
                'tnum text-[12px] font-semibold',
                !isGap ? 'text-ink' : Number(value) >= 0 ? 'text-pass' : 'text-fail',
              )}
            >
              {isGap ? signedPct(value, 2) : pct(value, 1)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/**
 * Constituent contribution map.
 *
 * Split into positive and negative pressure rather than a single signed list:
 * the operational question is "what is pushing SPY up and what is fighting it",
 * and a mixed ranking buries the second half.
 */
export function ConstituentPressure({
  rows,
  className,
  limit = 8,
}: {
  rows: ConstituentRow[] | undefined
  className?: string
  limit?: number
}) {
  const usable = (rows ?? []).filter((row) => isNum(row.contribution) && row.symbol)
  if (!usable.length) return <Empty label="No constituent attribution" field="constituent_attribution" />

  const sorted = [...usable].sort((a, b) => Number(b.contribution) - Number(a.contribution))
  const positive = sorted.filter((row) => Number(row.contribution) > 0).slice(0, limit)
  const negative = sorted.filter((row) => Number(row.contribution) < 0).slice(-limit).reverse()
  const bound = Math.max(...usable.map((row) => Math.abs(Number(row.contribution))), 1e-6)

  const block = (title: string, group: ConstituentRow[], tone: 'pass' | 'fail') => (
    <div className="min-w-0">
      <div className={cn('kicker mb-1', tone === 'pass' ? 'text-pass' : 'text-fail')}>{title}</div>
      {group.length === 0 ? (
        <div className="font-mono text-[9px] text-ink-4">none</div>
      ) : (
        group.map((row) => (
          <div key={row.symbol} className="grid grid-cols-[52px_1fr_54px] items-center gap-2 py-[2px]">
            <span className="truncate font-mono text-[10px] font-semibold text-ink-2">{row.symbol}</span>
            <Bar value={Math.abs(Number(row.contribution))} min={0} max={bound} tone={tone} />
            <span className="tnum text-right text-[10px] text-ink-3">
              {isNum(row.change_pct) ? signedPct(row.change_pct, 2) : EMPTY}
            </span>
          </div>
        ))
      )}
    </div>
  )

  return (
    <div className={cn('grid gap-4 sm:grid-cols-2', className)}>
      {block('Positive pressure', positive, 'pass')}
      {block('Negative pressure', negative, 'fail')}
    </div>
  )
}
