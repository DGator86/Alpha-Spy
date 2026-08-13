import { useMemo } from 'react'
import { EChart, chartTheme } from './EChart'
import type { Leg, Num, Quantiles } from '@/lib/types'
import { isNum } from '@/lib/format'

/** Expiry value of one option leg at an underlying price. */
function legValue(leg: Leg, underlying: number): number {
  const strike = Number(leg.strike)
  if (!Number.isFinite(strike)) return 0
  const right = String(leg.right ?? leg.type ?? 'CALL').toUpperCase()
  const quantity = Number(leg.quantity ?? 1) || 1
  const sign = String(leg.side ?? 'BUY').toUpperCase() === 'SELL' ? -1 : 1
  const intrinsic = right.startsWith('P')
    ? Math.max(0, strike - underlying)
    : Math.max(0, underlying - strike)
  return sign * quantity * intrinsic * 100
}

/**
 * Expiry payoff for a multi-leg structure.
 *
 * Drawn at expiry rather than marked-to-model mid-life: the entry decision is
 * made against terminal payoff, and an interpolated theta surface would imply a
 * precision the state feed does not carry.
 */
export function PayoffChart({
  legs,
  entryCost,
  spot,
  quantiles,
  className,
}: {
  legs: Leg[]
  entryCost: Num
  spot: Num
  quantiles?: Quantiles
  className?: string
}) {
  const option = useMemo(() => {
    const strikes = legs.map((leg) => Number(leg.strike)).filter(Number.isFinite)
    const centre = isNum(spot) ? Number(spot) : strikes.length ? strikes[0]! : 0
    if (!centre) return {}

    const span = Math.max(3, (Math.max(...strikes, centre) - Math.min(...strikes, centre)) * 2.2)
    const low = centre - span
    const high = centre + span
    const cost = isNum(entryCost) ? Number(entryCost) * 100 : 0

    const points: [number, number][] = []
    const steps = 160
    for (let i = 0; i <= steps; i++) {
      const underlying = low + ((high - low) * i) / steps
      const value = legs.reduce((total, leg) => total + legValue(leg, underlying), 0)
      points.push([underlying, value - cost])
    }

    const marks: Record<string, unknown>[] = []
    if (isNum(spot)) {
      marks.push({
        xAxis: Number(spot),
        lineStyle: { color: '#e8f1f7', width: 1, type: 'solid' },
        label: { formatter: 'SPOT', color: '#e8f1f7', fontSize: 8, position: 'insideEndTop' },
      })
    }
    for (const [key, color] of [
      ['p10', '#ff4f6b'],
      ['p50', '#38d7ff'],
      ['p90', '#2ee6a8'],
    ] as const) {
      const value = quantiles?.[key]
      if (isNum(value)) {
        marks.push({
          xAxis: Number(value),
          lineStyle: { color, width: 1, type: 'dashed' },
          label: { formatter: key.toUpperCase(), color, fontSize: 8, position: 'insideEndBottom' },
        })
      }
    }

    return {
      animation: false,
      grid: { top: 12, right: 10, bottom: 20, left: 44 },
      tooltip: {
        ...chartTheme.tooltip,
        trigger: 'axis',
        formatter: (params: { value: [number, number] }[]) => {
          const point = params[0]?.value
          if (!point) return ''
          return `SPY ${point[0].toFixed(2)}<br/>P&L ${point[1] >= 0 ? '+' : '−'}$${Math.abs(point[1]).toFixed(0)}`
        },
      },
      xAxis: { type: 'value', min: low, max: high, ...chartTheme.axis, splitLine: { show: false } },
      yAxis: {
        type: 'value',
        ...chartTheme.axis,
        axisLabel: { ...chartTheme.axis.axisLabel, formatter: (v: number) => `${v >= 0 ? '' : '−'}$${Math.abs(v).toFixed(0)}` },
      },
      series: [
        {
          type: 'line',
          data: points,
          showSymbol: false,
          lineStyle: { color: '#38d7ff', width: 2 },
          // Profit and loss regions are filled in the reserved state colours so
          // the max-profit zone reads at a glance.
          areaStyle: {
            origin: 0,
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(46, 230, 168, 0.22)' },
                { offset: 0.5, color: 'rgba(46, 230, 168, 0.02)' },
                { offset: 0.5, color: 'rgba(255, 79, 107, 0.02)' },
                { offset: 1, color: 'rgba(255, 79, 107, 0.22)' },
              ],
            },
          },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [{ yAxis: 0, lineStyle: { color: '#263a4d', width: 1 } }, ...marks],
          },
        },
      ],
    }
  }, [legs, entryCost, spot, quantiles])

  return <EChart option={option} className={className} />
}
