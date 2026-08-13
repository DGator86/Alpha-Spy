import {
  customSeriesDefaultOptions,
  type CustomData,
  type CustomSeriesOptions,
  type CustomSeriesPricePlotValues,
  type ICustomSeriesPaneRenderer,
  type ICustomSeriesPaneView,
  type PaneRendererCustomData,
  type PriceToCoordinateConverter,
  type Time,
  type WhitespaceData,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas'

/**
 * A forecast cone drawn as a Lightweight Charts custom series.
 *
 * Alpha-SPY produces P10/P25/P50/P75/P90 for every horizon. Overlaying that as
 * a separate absolutely-positioned canvas would desynchronise the moment the
 * user pans or zooms, so the cone is a real series inside the chart's own
 * coordinate system instead — it scales, scrolls and price-scales with the
 * candles for free.
 */
export interface ConeData extends CustomData<Time> {
  time: Time
  p10?: number
  p25?: number
  p50?: number
  p75?: number
  p90?: number
}

export interface ConeSeriesOptions extends CustomSeriesOptions {
  outerColor: string
  innerColor: string
  medianColor: string
  medianWidth: number
}

const DEFAULTS: ConeSeriesOptions = {
  ...customSeriesDefaultOptions,
  outerColor: 'rgba(56, 215, 255, 0.10)',
  innerColor: 'rgba(56, 215, 255, 0.18)',
  medianColor: 'rgba(56, 215, 255, 0.85)',
  medianWidth: 1,
} as const satisfies ConeSeriesOptions

interface Point {
  x: number
  p10: number | null
  p25: number | null
  p50: number | null
  p75: number | null
  p90: number | null
}

class ConeRenderer implements ICustomSeriesPaneRenderer {
  private _data: PaneRendererCustomData<Time, ConeData> | null = null
  private _options: ConeSeriesOptions | null = null

  update(data: PaneRendererCustomData<Time, ConeData>, options: ConeSeriesOptions): void {
    this._data = data
    this._options = options
  }

  draw(target: CanvasRenderingTarget2D, priceToCoordinate: PriceToCoordinateConverter): void {
    const data = this._data
    const options = this._options
    if (!data || !options || data.bars.length === 0 || data.visibleRange === null) return

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context
      const hRatio = scope.horizontalPixelRatio
      const vRatio = scope.verticalPixelRatio
      const range = data.visibleRange
      if (range === null) return

      const points: Point[] = []
      for (let i = range.from; i < range.to; i++) {
        const bar = data.bars[i]
        if (!bar) continue
        const original = bar.originalData
        const y = (value: number | undefined): number | null =>
          value === undefined || !Number.isFinite(value)
            ? null
            : (priceToCoordinate(value) ?? null) === null
              ? null
              : (priceToCoordinate(value) as number) * vRatio
        points.push({
          x: bar.x * hRatio,
          p10: y(original.p10),
          p25: y(original.p25),
          p50: y(original.p50),
          p75: y(original.p75),
          p90: y(original.p90),
        })
      }
      if (points.length < 2) return

      this._band(ctx, points, 'p10', 'p90', options.outerColor)
      this._band(ctx, points, 'p25', 'p75', options.innerColor)

      // Median runs on top of both bands so it stays readable inside the fill.
      const median = points.filter((p) => p.p50 !== null)
      if (median.length >= 2) {
        ctx.beginPath()
        median.forEach((p, index) => {
          if (index === 0) ctx.moveTo(p.x, p.p50 as number)
          else ctx.lineTo(p.x, p.p50 as number)
        })
        ctx.strokeStyle = options.medianColor
        ctx.lineWidth = Math.max(1, options.medianWidth * vRatio)
        ctx.setLineDash([4 * hRatio, 4 * hRatio])
        ctx.stroke()
        ctx.setLineDash([])
      }
    })
  }

  /** Fills between a lower and upper quantile, skipping gaps where either is absent. */
  private _band(
    ctx: CanvasRenderingContext2D,
    points: Point[],
    lower: keyof Point,
    upper: keyof Point,
    color: string,
  ): void {
    const usable = points.filter((p) => p[lower] !== null && p[upper] !== null)
    if (usable.length < 2) return
    ctx.beginPath()
    usable.forEach((p, index) => {
      const y = p[upper] as number
      if (index === 0) ctx.moveTo(p.x, y)
      else ctx.lineTo(p.x, y)
    })
    for (let i = usable.length - 1; i >= 0; i--) {
      const p = usable[i]
      if (!p) continue
      ctx.lineTo(p.x, p[lower] as number)
    }
    ctx.closePath()
    ctx.fillStyle = color
    ctx.fill()
  }
}

export class ForecastConeSeries implements ICustomSeriesPaneView<Time, ConeData, ConeSeriesOptions> {
  private _renderer = new ConeRenderer()

  priceValueBuilder(plotRow: ConeData): CustomSeriesPricePlotValues {
    // Reported low/high/close so the price scale autoscales to contain the whole
    // cone rather than clipping its tails.
    return [plotRow.p10 ?? NaN, plotRow.p90 ?? NaN, plotRow.p50 ?? NaN]
  }

  isWhitespace(data: ConeData | WhitespaceData<Time>): data is WhitespaceData<Time> {
    return (data as ConeData).p50 === undefined
  }

  renderer(): ICustomSeriesPaneRenderer {
    return this._renderer
  }

  update(data: PaneRendererCustomData<Time, ConeData>, options: ConeSeriesOptions): void {
    this._renderer.update(data, options)
  }

  defaultOptions(): ConeSeriesOptions {
    return DEFAULTS
  }
}
