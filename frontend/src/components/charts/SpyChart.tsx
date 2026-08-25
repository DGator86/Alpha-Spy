import { useEffect, useRef } from 'react'
import { cn } from '@/lib/cn'
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { ForecastConeSeries, type ConeData } from './forecastConeSeries'
import type { HorizonForecast, PricePoint } from '@/lib/types'
import { isNum } from '@/lib/format'

export interface PriceLevel {
  price: number
  label: string
  color: string
  style?: LineStyle
}

const GRID = '#131f2a'
const INK = '#64798c'

function toSeconds(value: string | null | undefined, fallback: number): UTCTimestamp {
  if (!value) return fallback as UTCTimestamp
  const parsed = new Date(value).getTime()
  return (Number.isNaN(parsed) ? fallback : Math.floor(parsed / 1000)) as UTCTimestamp
}

/**
 * Builds the forward cone from the horizon forecasts.
 *
 * Every horizon contributes one point at its own target time, anchored at the
 * current spot with zero width. The result is a genuine term structure across
 * 5m→1d rather than a straight-line interpolation of a single horizon, which is
 * what the previous hand-drawn canvas showed.
 */
function buildCone(
  horizons: Record<string, HorizonForecast> | undefined,
  spot: number | null,
  anchorTime: number,
  horizonLimit: number,
): ConeData[] {
  if (!horizons || spot === null) return []
  const points: ConeData[] = [
    { time: anchorTime as UTCTimestamp, p10: spot, p25: spot, p50: spot, p75: spot, p90: spot },
  ]
  const entries = Object.values(horizons)
    .filter((h) => isNum(h.predicted_price))
    .map((h) => {
      const quantiles = h.distribution?.quantiles
      const target = toSeconds(h.target_at, anchorTime)
      return {
        time: target,
        p10: Number(quantiles?.p10 ?? h.predicted_low ?? NaN),
        p25: Number(quantiles?.p25 ?? NaN),
        p50: Number(h.predicted_price),
        p75: Number(quantiles?.p75 ?? NaN),
        p90: Number(quantiles?.p90 ?? h.predicted_high ?? NaN),
      }
    })
    // Horizons past the limit are dropped rather than drawn. The 1d cone is
    // several dollars wide; leaving it in forces the price scale to a range
    // where an intraday move of twenty cents is invisible. The long horizons
    // are still readable in full on the Forecasts screen.
    .filter((p) => p.time > anchorTime && p.time <= horizonLimit)
    .sort((a, b) => a.time - b.time)

  for (const entry of entries) {
    // Missing inner quantiles fall back to a linear blend of the outer band so
    // the inner ribbon still renders instead of vanishing entirely.
    const p25 = Number.isFinite(entry.p25) ? entry.p25 : entry.p50 - (entry.p50 - entry.p10) * 0.53
    const p75 = Number.isFinite(entry.p75) ? entry.p75 : entry.p50 + (entry.p90 - entry.p50) * 0.53
    points.push({
      time: entry.time as UTCTimestamp,
      p10: Number.isFinite(entry.p10) ? entry.p10 : undefined,
      p25,
      p50: entry.p50,
      p75,
      p90: Number.isFinite(entry.p90) ? entry.p90 : undefined,
    })
  }
  // Duplicate timestamps break the series; keep the last write per second.
  const deduped = new Map<number, ConeData>()
  for (const point of points) deduped.set(point.time as number, point)
  return [...deduped.values()].sort((a, b) => (a.time as number) - (b.time as number))
}

export function SpyChart({
  series,
  horizons,
  levels = [],
  className,
}: {
  series: PricePoint[]
  horizons?: Record<string, HorizonForecast>
  levels?: PriceLevel[]
  className?: string
}) {
  const host = useRef<HTMLDivElement | null>(null)
  const chart = useRef<IChartApi | null>(null)
  const price = useRef<ISeriesApi<'Area'> | null>(null)
  const cone = useRef<ISeriesApi<'Custom'> | null>(null)
  const vwap = useRef<ISeriesApi<'Line'> | null>(null)
  /** Guards the one-shot viewport fit so live ticks never reset the user's pan. */
  const fitted = useRef(false)

  useEffect(() => {
    const element = host.current
    if (!element) return

    const instance = createChart(element, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: INK,
        fontSize: 10,
        fontFamily: 'SFMono-Regular, Menlo, Consolas, ui-monospace, monospace',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: GRID, style: LineStyle.Dotted },
        horzLines: { color: GRID, style: LineStyle.Dotted },
      },
      rightPriceScale: { borderColor: '#1b2937', scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: '#1b2937', timeVisible: true, secondsVisible: false, rightOffset: 6 },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#38d7ff66', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#0e1720' },
        horzLine: { color: '#38d7ff66', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#0e1720' },
      },
      handleScale: { axisPressedMouseMove: { time: true, price: false } },
    })

    // The cone is added first so the realised price line always draws over it.
    cone.current = instance.addCustomSeries(new ForecastConeSeries(), {
      priceLineVisible: false,
      lastValueVisible: false,
    })
    vwap.current = instance.addSeries(LineSeries, {
      color: '#ffb648aa',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })
    price.current = instance.addSeries(AreaSeries, {
      lineColor: '#e8f1f7',
      lineWidth: 2,
      topColor: 'rgba(232, 241, 247, 0.10)',
      bottomColor: 'rgba(232, 241, 247, 0.00)',
      priceLineColor: '#38d7ff',
      priceLineStyle: LineStyle.Dotted,
      lastValueVisible: true,
    })
    chart.current = instance
    // A freshly created chart has no viewport yet, so the one-shot fit has to
    // re-arm — otherwise a remount leaves the new instance at its default range.
    fitted.current = false

    return () => {
      instance.remove()
      chart.current = null
      price.current = null
      cone.current = null
      vwap.current = null
    }
  }, [])

  useEffect(() => {
    const line = price.current
    const vwapLine = vwap.current
    if (!line || !vwapLine) return

    const now = Math.floor(Date.now() / 1000)
    const points = series
      .map((point, index) => ({
        time: toSeconds(point.timestamp, now - (series.length - index) * 60),
        value: Number(point.price),
      }))
      .filter((point) => Number.isFinite(point.value))

    const deduped = new Map<number, { time: number; value: number }>()
    for (const point of points) deduped.set(point.time, point)
    const ordered = [...deduped.values()].sort((a, b) => a.time - b.time)
    line.setData(ordered.map((p) => ({ time: p.time as Time, value: p.value })))

    // Session VWAP is unweighted here because the state feed publishes SPY
    // prices without per-tick volume; it is labelled as a running mean in the
    // legend rather than claimed as a true volume-weighted price.
    let sum = 0
    vwapLine.setData(
      ordered.map((p, index) => {
        sum += p.value
        return { time: p.time as Time, value: sum / (index + 1) }
      }),
    )
  }, [series])

  useEffect(() => {
    const coneSeries = cone.current
    const instance = chart.current
    if (!coneSeries || !instance) return
    const last = series.at(-1)
    const spot = isNum(last?.price) ? Number(last?.price) : null
    const anchor = toSeconds(last?.timestamp, Math.floor(Date.now() / 1000)) as number
    const first = series[0]
    const start = toSeconds(first?.timestamp, anchor - series.length * 60) as number

    // Forward window scales with the history on screen — roughly a third of it,
    // capped at 45 minutes — so the cone always reads as a projection off the
    // end of the tape rather than dominating it.
    const historySpan = Math.max(anchor - start, 300)
    const horizonLimit = anchor + Math.min(historySpan * 0.35, 45 * 60)
    const points = buildCone(horizons, spot, anchor, horizonLimit)
    coneSeries.setData(points)

    // Set the viewport once, on the first frame that carries data. Re-applying
    // it on every tick would snap the viewport back and make the chart
    // impossible to pan. The range is stated explicitly rather than left to
    // fitContent, which fits the dense price bars and clips the handful of
    // sparse forward cone points off the right edge.
    if (!fitted.current && points.length > 0 && series.length > 0) {
      fitted.current = true
      instance.timeScale().setVisibleRange({
        from: start as Time,
        to: horizonLimit as Time,
      })
    }
  }, [horizons, series])

  useEffect(() => {
    const line = price.current
    if (!line) return
    // Price lines have no stable identity in the API, so the previous set is
    // torn down and rebuilt whenever the level list changes.
    const created = levels
      .filter((level) => Number.isFinite(level.price))
      .map((level) =>
        line.createPriceLine({
          price: level.price,
          color: level.color,
          lineWidth: 1,
          lineStyle: level.style ?? LineStyle.Dashed,
          axisLabelVisible: true,
          title: level.label,
        }),
      )
    return () => {
      // On unmount React runs this cleanup after the chart-creation effect's,
      // which has already disposed the chart and every series on it. Touching
      // the series then throws "Object is disposed"; `chart.current` is nulled
      // by that teardown, so it doubles as the liveness check.
      if (!chart.current) return
      for (const priceLine of created) line.removePriceLine(priceLine)
    }
  }, [levels])

  return (
    <div className={cn('relative', className)}>
      <div ref={host} className="absolute inset-0" />
      {/* Three series with three different meanings — realised, smoothed and
          forecast — are indistinguishable without naming them. */}
      <div className="pointer-events-none absolute top-1.5 left-2 z-10 flex flex-wrap gap-3 font-mono text-[9px] tracking-[0.08em] text-ink-3 uppercase">
        <span className="flex items-center gap-1">
          <span className="h-[2px] w-3 bg-ink" /> SPY
        </span>
        <span className="flex items-center gap-1">
          <span className="h-[2px] w-3 bg-watch/70" /> running mean
        </span>
        <span className="flex items-center gap-1">
          <span className="h-[2px] w-3 bg-signal" /> forecast P50
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-3 bg-signal/20" /> P10–P90 cone
        </span>
      </div>
    </div>
  )
}
