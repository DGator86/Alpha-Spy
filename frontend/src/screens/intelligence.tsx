import { useMemo } from 'react'
import { EChart, chartTheme } from '@/components/charts/EChart'
import { HorizonRibbon } from '@/components/panels/HorizonRibbon'
import {
  Chip,
  Empty,
  Metric,
  Panel,
  Row,
  TableShell,
  Td,
  Th,
  toneForState,
} from '@/components/ui/primitives'
import { EMPTY, hhmm, isNum, num, pct, signed, titleize } from '@/lib/format'
import { useWorkstation } from '@/store/workstation'
import { Screen } from './Screen'

export function ForecastsScreen() {
  const state = useWorkstation((store) => store.state)
  const horizons = Object.entries(state.forecast_horizons ?? {})
  return (
    <Screen>
      <Panel kicker="Term structure" title="Horizon ribbon" bodyClassName="p-0">
        <HorizonRibbon horizons={state.forecast_horizons} />
      </Panel>
      <Panel kicker="Per-horizon detail" title="Published forecasts" bodyClassName="p-0" scroll>
        {horizons.length === 0 ? (
          <Empty label="No horizon forecasts" field="forecast_horizons" />
        ) : (
          <TableShell>
            <thead>
              <tr>
                <Th>Horizon</Th>
                <Th>Role</Th>
                <Th align="right">P(up)</Th>
                <Th align="right">E[r] bp</Th>
                <Th align="right">P10</Th>
                <Th align="right">P50</Th>
                <Th align="right">P90</Th>
                <Th align="right">σ bp</Th>
                <Th align="right">Model unc.</Th>
                <Th align="right">Target</Th>
                <Th>Integrity</Th>
              </tr>
            </thead>
            <tbody>
              {horizons.map(([name, forecast]) => {
                const q = forecast.distribution?.quantiles
                return (
                  <tr key={name}>
                    <Td className="font-semibold text-ink">{name.toUpperCase()}</Td>
                    <Td>{titleize(forecast.role)}</Td>
                    <Td align="right" className={Number(forecast.probability_up) >= 0.5 ? 'text-pass' : 'text-fail'}>
                      {pct(forecast.probability_up, 1)}
                    </Td>
                    <Td align="right">
                      {isNum(forecast.expected_return) ? signed(Number(forecast.expected_return) * 10000, 1) : EMPTY}
                    </Td>
                    <Td align="right">{num(q?.p10 ?? forecast.predicted_low)}</Td>
                    <Td align="right" className="text-signal">{num(forecast.predicted_price)}</Td>
                    <Td align="right">{num(q?.p90 ?? forecast.predicted_high)}</Td>
                    <Td align="right">
                      {isNum(forecast.sigma_return) ? (Number(forecast.sigma_return) * 10000).toFixed(1) : EMPTY}
                    </Td>
                    <Td align="right">{num(forecast.model_uncertainty, 2)}</Td>
                    <Td align="right">{hhmm(forecast.target_at)}</Td>
                    <Td>
                      <Chip tone={toneForState(forecast.integrity)}>{forecast.integrity ?? EMPTY}</Chip>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </TableShell>
        )}
      </Panel>
    </Screen>
  )
}

/**
 * P vs Q.
 *
 * The physical distribution is what the model believes; the risk-neutral one is
 * what the option market charges. The gap between them is the edge the ranker
 * trades, so they are drawn on one axis rather than in two panels.
 */
export function PvsQScreen() {
  const state = useWorkstation((store) => store.state)
  const market = state.market

  const option = useMemo(() => {
    const entries = Object.entries(state.forecast_horizons ?? {})
      .map(([name, forecast]) => ({
        name,
        physical: forecast.distribution?.physical_sigma,
        riskNeutral: forecast.distribution?.risk_neutral_sigma,
      }))
      .filter((row) => isNum(row.physical) || isNum(row.riskNeutral))
    if (!entries.length) return null
    return {
      animation: false,
      grid: { top: 24, right: 12, bottom: 24, left: 48 },
      tooltip: { ...chartTheme.tooltip, trigger: 'axis' },
      legend: {
        top: 0,
        textStyle: { color: '#64798c', fontSize: 9, fontFamily: 'SFMono-Regular, monospace' },
        itemWidth: 10,
        itemHeight: 2,
      },
      xAxis: { type: 'category', data: entries.map((row) => row.name.toUpperCase()), ...chartTheme.axis },
      yAxis: {
        type: 'value',
        ...chartTheme.axis,
        axisLabel: { ...chartTheme.axis.axisLabel, formatter: (v: number) => `${(v * 10000).toFixed(0)}bp` },
      },
      series: [
        {
          name: 'Physical (P)',
          type: 'line',
          data: entries.map((row) => row.physical ?? null),
          lineStyle: { color: '#38d7ff', width: 2 },
          itemStyle: { color: '#38d7ff' },
          symbolSize: 5,
        },
        {
          name: 'Risk-neutral (Q)',
          type: 'line',
          data: entries.map((row) => row.riskNeutral ?? null),
          lineStyle: { color: '#ffb648', width: 2, type: 'dashed' },
          itemStyle: { color: '#ffb648' },
          symbolSize: 5,
        },
      ],
    }
  }, [state.forecast_horizons])

  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel kicker="Term structure of uncertainty" title="P vs Q sigma" bodyClassName="p-1">
          {option ? (
            <EChart option={option} className="h-[280px] w-full" />
          ) : (
            <Empty label="No distribution detail" field="forecast_horizons[].distribution" />
          )}
        </Panel>
        <Panel kicker="Surface" title="Where the edge comes from">
          <Metric
            label="IV premium (market − model)"
            value={isNum(market?.vol_gap) ? `${(Number(market?.vol_gap) * 100).toFixed(2)}%` : EMPTY}
            tone={Number(market?.vol_gap) >= 0 ? 'pass' : 'fail'}
            className="mb-2"
          />
          <Row label="SPY IV" value={pct(market?.spy_iv)} />
          <Row label="Synthetic constituent IV" value={pct(market?.constituent_iv)} />
          <Row label="Physical vol forecast" value={pct(market?.physical_vol)} />
          <Row label="Skew gap" value={pct(market?.skew_gap, 2)} />
          <Row label="Reference expiry" value={market?.iv_reference_expiration ?? EMPTY} />
          <Row label="Surface coverage" value={pct(market?.iv_coverage)} />
          <div className="mt-2 font-mono text-[9px] leading-snug text-ink-4">
            A positive IV premium means the option market is charging more
            volatility than the model forecasts — the condition the credit
            structures are selected under.
          </div>
        </Panel>
      </div>
    </Screen>
  )
}

export function ModelsScreen() {
  const state = useWorkstation((store) => store.state)
  const challengers = state.challengers ?? []
  const signalModel = (state.market?.signal_model ?? {}) as Record<string, unknown>

  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-2">
        <Panel kicker="Governance" title="Champion and challengers" scroll>
          {challengers.length === 0 ? (
            <Empty label="No model registry published" field="challengers" />
          ) : (
            challengers.map((row, index) => (
              <div key={`${row.name}-${index}`} className="mb-2 border border-line bg-surface-2 p-2 last:mb-0">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[11px] font-semibold text-ink">{row.name}</span>
                  <Chip tone={toneForState(row.status)}>{row.status ?? EMPTY}</Chip>
                </div>
                <div className="mt-1 grid grid-cols-3 gap-px bg-line">
                  <Metric label="Calibration" value={pct(row.calibration)} className="border-0" size="sm" />
                  <Metric label="Expectancy" value={num(row.expectancy, 1)} className="border-0" size="sm" />
                  <Metric label="Tail loss" value={num(row.tail_loss, 1)} className="border-0" size="sm" />
                </div>
              </div>
            ))
          )}
        </Panel>
        <Panel kicker="Active signal" title="Trading authority">
          {Object.keys(signalModel).length === 0 ? (
            <Empty label="No signal model detail" field="market.signal_model" />
          ) : (
            Object.entries(signalModel).map(([key, value]) => (
              <Row
                key={key}
                label={key.replace(/_/g, ' ')}
                value={typeof value === 'number' ? num(value, 4) : String(value ?? EMPTY)}
              />
            ))
          )}
          <div className="mt-2 border-t border-line pt-2">
            <Row label="Model version" value={state.engine?.version ?? EMPTY} />
            <Row label="Intraday retraining" value="DISABLED" tone="pass" />
          </div>
        </Panel>
      </div>
    </Screen>
  )
}

export function CalibrationScreen() {
  const state = useWorkstation((store) => store.state)
  const audit = { ...(state.audit ?? {}), ...(state.prediction_metrics ?? {}) }
  const predictions = (state.predictions ?? []).filter((row) => isNum(row.actual_price))

  /**
   * Reliability curve: forecast P(up) bucketed against realised up-rate. A
   * perfectly calibrated model sits on the diagonal.
   */
  const option = useMemo(() => {
    if (predictions.length < 10) return null
    const buckets = Array.from({ length: 10 }, () => ({ n: 0, up: 0 }))
    for (const row of predictions) {
      const p = Number(row.probability_up)
      if (!Number.isFinite(p)) continue
      const index = Math.min(9, Math.max(0, Math.floor(p * 10)))
      const bucket = buckets[index]
      if (!bucket) continue
      bucket.n += 1
      if (Number(row.actual_price) >= Number(row.spy_price)) bucket.up += 1
    }
    const points = buckets
      .map((bucket, index) => (bucket.n === 0 ? null : [index / 10 + 0.05, bucket.up / bucket.n]))
      .filter((point): point is [number, number] => point !== null)
    if (points.length < 3) return null
    return {
      animation: false,
      grid: { top: 16, right: 16, bottom: 26, left: 40 },
      tooltip: { ...chartTheme.tooltip, trigger: 'item' },
      xAxis: { type: 'value', min: 0, max: 1, name: 'forecast', nameTextStyle: { color: '#64798c', fontSize: 9 }, ...chartTheme.axis },
      yAxis: { type: 'value', min: 0, max: 1, name: 'realised', nameTextStyle: { color: '#64798c', fontSize: 9 }, ...chartTheme.axis },
      series: [
        {
          type: 'line',
          data: [[0, 0], [1, 1]],
          showSymbol: false,
          lineStyle: { color: '#263a4d', width: 1, type: 'dashed' },
          silent: true,
        },
        {
          type: 'scatter',
          data: points,
          symbolSize: 8,
          itemStyle: { color: '#38d7ff' },
        },
      ],
    }
  }, [predictions])

  return (
    <Screen>
      <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-5">
        <Metric label="Formal anchors" value={audit.sample_size ?? 0} hint="non-overlapping" className="border-0" />
        <Metric label="Direction hit" value={pct(audit.direction_accuracy)} className="border-0" />
        <Metric label="Brier" value={num(audit.brier, 3)} hint="lower is better" className="border-0" />
        <Metric label="Interval coverage" value={pct(audit.range_coverage)} hint="target 80%" className="border-0" />
        <Metric label="Price MAE" value={num(audit.price_mae, 3)} hint="SPY points" className="border-0" />
      </div>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel kicker="Reliability" title="Forecast vs realised up-rate" bodyClassName="p-1">
          {option ? (
            <EChart option={option} className="h-[280px] w-full" />
          ) : (
            <Empty label="Not enough matured forecasts to bucket" field="predictions[].actual_price" />
          )}
        </Panel>
        <Panel kicker="Control surface" title="Calibration actions">
          <Row label="Integrity verified" value={pct(audit.integrity_verified_pct)} />
          <Row label="Current forecast" value={titleize(audit.current_prediction_status)} />
          <Row label="T-15 match" value={titleize(audit.t_minus_15_match)} />
          <div className="mt-2 font-mono text-[9px] leading-relaxed text-ink-4">
            Conservative probability is applied before EV, and forecast intervals
            widen when coverage falls below target. Risk can fall immediately on
            a calibration breach; it only recovers gradually.
          </div>
        </Panel>
      </div>
    </Screen>
  )
}
