import { cn } from '@/lib/cn'
import { EMPTY, clamp, convictionBand, directionArrow, isNum, pct, signed } from '@/lib/format'
import { Empty } from '@/components/ui/primitives'
import type { HorizonForecast } from '@/lib/types'

/** Display order; anything the engine publishes outside this list is appended. */
const ORDER = ['1m', '5m', '15m', '30m', '60m', '120m', '240m', 'eod', '1d', '1w']

function sortHorizons(horizons: Record<string, HorizonForecast>): [string, HorizonForecast][] {
  return Object.entries(horizons).sort(([a], [b]) => {
    const ia = ORDER.indexOf(a)
    const ib = ORDER.indexOf(b)
    if (ia === -1 && ib === -1) return a.localeCompare(b)
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })
}

/**
 * Multi-horizon forecast ribbon.
 *
 * Colour intensity encodes conviction — the distance of P(up) from a coin flip —
 * so agreement and conflict across the term structure are visible as a pattern
 * rather than read off seven separate numbers.
 */
export function HorizonRibbon({
  horizons,
  className,
  compact = false,
}: {
  horizons: Record<string, HorizonForecast> | undefined
  className?: string
  compact?: boolean
}) {
  const entries = horizons ? sortHorizons(horizons) : []
  if (!entries.length) {
    return <Empty label="No horizon forecasts" field="forecast_horizons" />
  }

  return (
    <div className={cn('grid min-w-0 gap-px bg-line', className)} style={{ gridTemplateColumns: `repeat(${entries.length}, minmax(0, 1fr))` }}>
      {entries.map(([name, forecast]) => {
        const probability = forecast.probability_up
        const edge = isNum(probability) ? Number(probability) - 0.5 : 0
        const conviction = convictionBand(probability)
        const up = edge > 0.005
        const down = edge < -0.005
        // Opacity is driven by |edge| so a 51% call is visibly weaker than 68%.
        const intensity = clamp(Math.abs(edge) / 0.2, 0.06, 0.9)
        const tint = up ? '46, 230, 168' : down ? '255, 79, 107' : '93, 113, 133'

        return (
          <div
            key={name}
            className="flex min-w-0 flex-col items-center gap-1 bg-surface px-1 py-2"
            style={{ boxShadow: `inset 0 -3px 0 rgba(${tint}, ${intensity + 0.1})` }}
            title={forecast.role ? `role: ${forecast.role}` : undefined}
          >
            <div className="kicker truncate">{name.toUpperCase()}</div>
            <div
              className={cn(
                'text-[15px] leading-none font-bold',
                up ? 'text-pass' : down ? 'text-fail' : 'text-flat',
              )}
              style={{ opacity: 0.45 + intensity * 0.55 }}
            >
              {directionArrow(edge)}
            </div>
            <div className="tnum text-[12px] font-semibold text-ink">{pct(probability, 0)}</div>
            {!compact && (
              <>
                <div
                  className={cn(
                    'font-mono text-[8px] tracking-[0.1em]',
                    conviction === 'HIGH' ? 'text-ink-2' : conviction === 'MED' ? 'text-ink-3' : 'text-ink-4',
                  )}
                >
                  {conviction ?? EMPTY}
                </div>
                <div className="tnum text-[9px] text-ink-3">
                  {isNum(forecast.expected_return)
                    ? signed(Number(forecast.expected_return) * 10000, 0)
                    : EMPTY}
                  <span className="text-ink-4">bp</span>
                </div>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}
