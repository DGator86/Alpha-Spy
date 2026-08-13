import { useState } from 'react'
import { cn } from '@/lib/cn'
import { EMPTY, humanize, isNum, pct } from '@/lib/format'
import { Bar, Chip, Empty, toneForState } from '@/components/ui/primitives'
import type { Market, RegimeLevel } from '@/lib/types'

/** Regime words the engine emits, mapped onto the three reserved state colours. */
function regimeTone(label: unknown): 'pass' | 'fail' | 'watch' | 'neutral' {
  const text = String(label ?? '').toUpperCase()
  if (/(BULL|RISK_ON|RISK ON|TREND_UP|TRENDING_UP|BROAD_UP|BROAD UP|UP|CONTANGO|GOOD|CLEAR|NORMAL)/.test(text))
    return 'pass'
  if (/(BEAR|RISK_OFF|RISK OFF|TREND_DOWN|TRENDING_DOWN|BROAD_DOWN|DOWN|BACKWARDATION|STRESSED|POOR|ELEVATED)/.test(text))
    return 'fail'
  if (/(NEUTRAL|MIXED|TRANSITION|RISING|CHOPPY|MODERATE|UNKNOWN)/.test(text)) return 'watch'
  return 'neutral'
}

const LEVELS: [string, string][] = [
  ['micro', 'Micro'],
  ['intraday', 'Intraday'],
  ['swing', 'Swing'],
  ['structural', 'Structural'],
]

function LevelRow({
  name,
  level,
  expanded,
  onToggle,
}: {
  name: string
  level: RegimeLevel | undefined
  expanded: boolean
  onToggle: () => void
}) {
  const label = level?.label ?? level?.key
  const tone = regimeTone(label)
  return (
    <div className="border-b border-line/60 last:border-0">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-baseline justify-between gap-3 py-1.5 text-left transition-colors hover:bg-surface-2"
      >
        <span className="font-mono text-[10px] tracking-[0.1em] text-ink-3 uppercase">{name}</span>
        <span
          className={cn(
            'truncate font-mono text-[11px] font-semibold tracking-wide',
            tone === 'pass' ? 'text-pass' : tone === 'fail' ? 'text-fail' : tone === 'watch' ? 'text-watch' : 'text-ink-2',
          )}
        >
          {label ? humanize(label) : EMPTY}
        </span>
      </button>
      {expanded && (
        // Clicking a level opens the evidence that produced the classification,
        // so a regime label is auditable instead of asserted.
        <div className="border-l-2 border-signal-dim bg-surface-2 px-2 py-1.5">
          <div className="font-mono text-[9px] leading-snug text-ink-2">
            {level?.evidence ?? 'No classification evidence published for this level.'}
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {isNum(level?.history_samples) && (
              <Chip tone="muted">{level?.history_samples} samples</Chip>
            )}
            {level?.key && <Chip tone="muted">{level.key}</Chip>}
          </div>
        </div>
      )}
    </div>
  )
}

export function RegimeCockpit({ market, className }: { market: Market | undefined; className?: string }) {
  const [open, setOpen] = useState<string | null>(null)
  const hierarchy = market?.regime_hierarchy
  const transition = hierarchy?.transition_risk

  // The lower block is derived state rather than the hierarchy proper: these are
  // the conditioning variables the classifier reads.
  const conditions: [string, unknown][] = [
    ['Volatility', market?.physical_vol == null ? null : Number(market.physical_vol) > 0.22 ? 'ELEVATED' : 'NORMAL'],
    ['Correlation', market?.correlation == null ? null : Number(market.correlation) > 0.6 ? 'RISING' : 'CONTAINED'],
    ['Breadth', market?.breadth == null ? null : Number(market.breadth) > 0.55 ? 'BROAD_UP' : 'NARROW'],
    ['Concentration', market?.concentration == null ? null : Number(market.concentration) > 0.35 ? 'HIGH' : 'MODERATE'],
    ['Liquidity', market?.liquidity_state],
    ['Dealer gamma', market?.gamma_state],
    ['Event risk', market?.event_state],
  ]

  if (!hierarchy && !market?.regime) {
    return <Empty label="No regime classification" field="market.regime_hierarchy" />
  }

  return (
    <div className={cn('flex min-h-0 flex-col gap-2', className)}>
      <div>
        {LEVELS.map(([key, label]) => (
          <LevelRow
            key={key}
            name={label}
            level={hierarchy?.[key] as RegimeLevel | undefined}
            expanded={open === key}
            onToggle={() => setOpen(open === key ? null : key)}
          />
        ))}
      </div>

      <div className="border-t border-line pt-1.5">
        {conditions.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 py-[3px]">
            <span className="font-mono text-[9px] tracking-[0.1em] text-ink-4 uppercase">{label}</span>
            <Chip tone={value ? toneForState(value) : 'muted'}>{value ? humanize(String(value)) : EMPTY}</Chip>
          </div>
        ))}
      </div>

      {isNum(transition) && (
        <div className="mt-auto border-t border-line pt-2">
          <div className="mb-1 flex items-baseline justify-between">
            <span className="kicker">Transition risk</span>
            <span className="tnum text-[12px] font-semibold text-ink">{pct(transition, 0)}</span>
          </div>
          <Bar
            value={transition}
            tone={Number(transition) > 0.66 ? 'fail' : Number(transition) > 0.33 ? 'watch' : 'pass'}
          />
        </div>
      )}
    </div>
  )
}
