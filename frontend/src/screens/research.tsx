import { useEffect, useMemo, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { EChart, chartTheme } from '@/components/charts/EChart'
import {
  Button,
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
import { cn } from '@/lib/cn'
import { EMPTY, clock, hhmm, isNum, num, pct, signed, stamp, titleize } from '@/lib/format'
import { STATIC_DEMO, useWorkstation } from '@/store/workstation'
import type { Prediction } from '@/lib/types'
import { Screen } from './Screen'

/** Fetches the full record for one forecast, including the model payload. */
function useForecastDetail(id: string | null) {
  const viewToken = useWorkstation((store) => store.viewToken)
  const predictions = useWorkstation((store) => store.state.predictions)
  const [record, setRecord] = useState<Prediction | null>(null)
  const [error, setError] = useState<string>('')

  useEffect(() => {
    if (!id) {
      setRecord(null)
      setError('')
      return
    }
    // The static preview has no API behind it, so the inspector resolves from
    // the snapshot already in the store.
    if (STATIC_DEMO) {
      const found = (predictions ?? []).find((row) => row.prediction_id === id) ?? null
      setRecord(found)
      setError(found ? '' : `Forecast ${id} not found`)
      return
    }
    let cancelled = false
    setRecord(null)
    setError('')
    fetch(`/api/v1/predictions/${encodeURIComponent(id)}`, {
      headers: viewToken ? { 'X-Dashboard-Token': viewToken } : {},
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Forecast ${id} not found`)
        return (await response.json()) as Prediction
      })
      .then((value) => {
        if (!cancelled) setRecord(value)
      })
      .catch((exc: Error) => {
        if (!cancelled) setError(exc.message)
      })
    return () => {
      cancelled = true
    }
  }, [id, viewToken, predictions])

  return { record, error }
}

function ForecastInspector({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { record, error } = useForecastDetail(id)
  const payload = (record?.payload ?? {}) as Record<string, unknown>
  const outcome = (payload.outcome ?? {}) as Record<string, unknown>
  const signalModel = (payload.signal_model ?? {}) as Record<string, unknown>
  const shadowModel = (payload.shadow_model ?? {}) as Record<string, unknown>
  const regime = (payload.regime_hierarchy ?? payload.regime_state ?? {}) as Record<string, unknown>

  const error_ = isNum(record?.actual_price)
    ? Number(record?.actual_price) - Number(record?.predicted_price)
    : null
  const inside =
    isNum(record?.actual_price) && isNum(record?.predicted_low) && isNum(record?.predicted_high)
      ? Number(record?.actual_price) >= Number(record?.predicted_low) &&
        Number(record?.actual_price) <= Number(record?.predicted_high)
      : null

  return (
    <Dialog.Root open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/75" />
        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 max-h-[86vh] w-[560px] -translate-x-1/2 -translate-y-1/2 overflow-auto border border-line-bright bg-surface p-4">
          <Dialog.Title className="kicker">Forecast inspector</Dialog.Title>
          <div className="mt-0.5 font-mono text-[13px] font-semibold text-ink">{id}</div>

          {error && <div className="mt-3 font-mono text-[11px] text-fail">{error}</div>}
          {!record && !error && (
            <div className="mt-3 font-mono text-[11px] text-ink-3">Loading…</div>
          )}

          {record && (
            <div className="mt-3 grid gap-3">
              <div className="grid grid-cols-3 gap-px bg-line">
                <Metric label="Spot at t0" value={num(record.spy_price)} className="border-0" size="sm" />
                <Metric label="Forecast P50" value={num(record.predicted_price)} tone="signal" className="border-0" size="sm" />
                <Metric label="Actual" value={num(record.actual_price)} className="border-0" size="sm" />
                <Metric label="P10" value={num(record.predicted_low)} className="border-0" size="sm" />
                <Metric label="P90" value={num(record.predicted_high)} className="border-0" size="sm" />
                <Metric label="P(up)" value={pct(record.probability_up, 1)} className="border-0" size="sm" />
              </div>

              <div>
                <div className="kicker mb-1">Outcome</div>
                <Row label="Error" value={error_ === null ? EMPTY : signed(error_, 3)} tone={error_ === null ? undefined : Math.abs(error_) < 0.25 ? 'pass' : 'watch'} />
                <Row
                  label="Direction"
                  value={record.direction_correct === null || record.direction_correct === undefined ? 'PENDING' : record.direction_correct ? 'CORRECT' : 'WRONG'}
                  tone={record.direction_correct === null || record.direction_correct === undefined ? undefined : record.direction_correct ? 'pass' : 'fail'}
                />
                <Row
                  label="Interval"
                  value={inside === null ? EMPTY : inside ? 'INSIDE' : 'OUTSIDE'}
                  tone={inside === null ? undefined : inside ? 'pass' : 'fail'}
                />
                <Row label="Created" value={stamp(record.created_at)} />
                <Row label="Target" value={stamp(record.target_at)} />
                <Row label="Horizon" value={record.horizon_minutes ? `${record.horizon_minutes}m` : EMPTY} />
              </div>

              {(Object.keys(signalModel).length > 0 || Object.keys(shadowModel).length > 0) && (
                <div>
                  <div className="kicker mb-1">Models</div>
                  {Object.entries(signalModel).map(([key, value]) => (
                    <Row key={`c-${key}`} label={`champion ${key.replace(/_/g, ' ')}`} value={typeof value === 'number' ? num(value, 4) : String(value ?? EMPTY)} />
                  ))}
                  {Object.entries(shadowModel).map(([key, value]) => (
                    <Row key={`s-${key}`} label={`shadow ${key.replace(/_/g, ' ')}`} value={typeof value === 'number' ? num(value, 4) : String(value ?? EMPTY)} />
                  ))}
                </div>
              )}

              {Object.keys(regime).length > 0 && (
                <div>
                  <div className="kicker mb-1">Regime at forecast time</div>
                  {Object.entries(regime).map(([key, value]) => (
                    <Row
                      key={key}
                      label={key.replace(/_/g, ' ')}
                      value={
                        typeof value === 'object' && value !== null
                          ? String((value as { label?: string; key?: string }).label ?? (value as { key?: string }).key ?? EMPTY)
                          : String(value ?? EMPTY)
                      }
                    />
                  ))}
                </div>
              )}

              <div>
                <div className="kicker mb-1">Integrity</div>
                <Row label="Record integrity" value={record.integrity ?? EMPTY} tone={record.integrity === 'VERIFIED' ? 'pass' : 'watch'} />
                <Row label="Model version" value={record.model_version ?? EMPTY} />
                {Object.keys(outcome).length > 0 && (
                  <>
                    <Row label="Confirmed at" value={stamp(outcome.confirmed_at as string)} />
                    <Row label="Realised MFE" value={num(outcome.actual_mfe as number, 3)} />
                    <Row label="Realised MAE" value={num(outcome.actual_mae as number, 3)} />
                  </>
                )}
              </div>
            </div>
          )}

          <div className="mt-4 flex justify-end">
            <Button size="md" onClick={onClose}>
              Close
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export function ConfirmationTapeScreen() {
  const state = useWorkstation((store) => store.state)
  const [selected, setSelected] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'matured' | 'wrong'>('all')

  const audit = { ...(state.audit ?? {}), ...(state.prediction_metrics ?? {}) }
  const rows = useMemo(() => {
    const all = state.predictions ?? []
    if (filter === 'matured') return all.filter((row) => isNum(row.actual_price))
    if (filter === 'wrong') return all.filter((row) => row.direction_correct === false)
    return all
  }, [state.predictions, filter])

  return (
    <Screen>
      <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
        <Metric label="Formal anchors" value={audit.sample_size ?? 0} hint="non-overlapping" className="border-0" />
        <Metric label="Brier" value={num(audit.brier, 3)} hint="lower is better" className="border-0" />
        <Metric label="Price MAE" value={num(audit.price_mae, 3)} hint="SPY points" className="border-0" />
        <Metric label="Current forecast" value={titleize(audit.current_prediction_status)} className="border-0" />
      </div>

      <Panel
        kicker="Immutable forecast / realised outcome"
        title="Confirmation tape"
        bodyClassName="p-0 min-h-0"
        actions={
          <div className="flex gap-1">
            {(['all', 'matured', 'wrong'] as const).map((key) => (
              <Button key={key} tone={filter === key ? 'primary' : 'ghost'} onClick={() => setFilter(key)}>
                {key}
              </Button>
            ))}
          </div>
        }
      >
        {rows.length === 0 ? (
          <Empty label="No forecasts recorded" field="predictions" />
        ) : (
          <TableShell className="max-h-[540px]">
            <thead>
              <tr>
                <Th>Created</Th>
                <Th>Target</Th>
                <Th align="right">Spot</Th>
                <Th align="right">Forecast</Th>
                <Th align="right">Actual</Th>
                <Th align="right">P(up)</Th>
                <Th align="right">Error</Th>
                <Th>Direction</Th>
                <Th>Integrity</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const err = isNum(row.actual_price) ? Number(row.actual_price) - Number(row.predicted_price) : null
                return (
                  <tr
                    key={row.prediction_id}
                    onClick={() => setSelected(row.prediction_id)}
                    className="cursor-pointer hover:bg-surface-2"
                  >
                    <Td className="tnum">{hhmm(row.created_at)}</Td>
                    <Td className="tnum">{hhmm(row.target_at)}</Td>
                    <Td align="right">{num(row.spy_price)}</Td>
                    <Td align="right" className="text-signal">{num(row.predicted_price)}</Td>
                    <Td align="right">{num(row.actual_price)}</Td>
                    <Td align="right">{pct(row.probability_up, 0)}</Td>
                    <Td align="right" className={err === null ? undefined : Math.abs(err) < 0.25 ? 'text-pass' : 'text-watch'}>
                      {err === null ? EMPTY : signed(err, 3)}
                    </Td>
                    <Td>
                      <Chip tone={row.direction_correct === null || row.direction_correct === undefined ? 'muted' : row.direction_correct ? 'pass' : 'fail'}>
                        {row.direction_correct === null || row.direction_correct === undefined ? 'PENDING' : row.direction_correct ? 'CORRECT' : 'WRONG'}
                      </Chip>
                    </Td>
                    <Td>
                      <Chip tone={toneForState(row.integrity)}>{row.integrity ?? EMPTY}</Chip>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </TableShell>
        )}
      </Panel>

      <ForecastInspector id={selected} onClose={() => setSelected(null)} />
    </Screen>
  )
}

/**
 * Replay lab.
 *
 * Scrubs the forecast tape the dashboard already holds. Full tape replay — the
 * one that rewinds candles, regime, internals and decisions together — is a
 * server capability: it needs an as-of state endpoint backed by the engine's
 * captured-tape store, which the dashboard API does not expose today. What is
 * real here is labelled as real, and what is missing is named.
 */
export function ReplayLabScreen() {
  const state = useWorkstation((store) => store.state)
  const [selected, setSelected] = useState<string | null>(null)
  const replay = state.replay

  const ordered = useMemo(
    () =>
      [...(state.predictions ?? [])].sort(
        (a, b) => new Date(a.created_at ?? 0).getTime() - new Date(b.created_at ?? 0).getTime(),
      ),
    [state.predictions],
  )
  const [index, setIndex] = useState<number | null>(null)
  const cursor = index === null ? ordered.length - 1 : index
  const current = ordered[cursor]
  const upTo = ordered.slice(0, cursor + 1)

  const option = useMemo(() => {
    if (upTo.length < 2) return null
    return {
      animation: false,
      grid: { top: 14, right: 12, bottom: 24, left: 46 },
      tooltip: { ...chartTheme.tooltip, trigger: 'axis' },
      xAxis: { type: 'category', data: upTo.map((row) => hhmm(row.created_at)), ...chartTheme.axis },
      yAxis: { type: 'value', scale: true, ...chartTheme.axis },
      series: [
        {
          name: 'Spot',
          type: 'line',
          data: upTo.map((row) => row.spy_price ?? null),
          showSymbol: false,
          lineStyle: { color: '#e8f1f7', width: 1.6 },
        },
        {
          name: 'Forecast',
          type: 'line',
          data: upTo.map((row) => row.predicted_price ?? null),
          showSymbol: false,
          lineStyle: { color: '#38d7ff', width: 1.2, type: 'dashed' },
        },
      ],
    }
  }, [upTo])

  return (
    <Screen>
      <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
        <Metric label="Replay status" value={replay?.status ?? 'NOT RUN'} tone={replay?.status === 'PASSED' ? 'pass' : 'watch'} className="border-0" />
        <Metric label="Samples" value={replay?.samples ?? 0} className="border-0" />
        <Metric label="Mismatches" value={replay?.mismatches ?? 0} tone={Number(replay?.mismatches ?? 0) === 0 ? 'pass' : 'fail'} className="border-0" />
        <Metric label="Method" value={titleize(replay?.method)} className="border-0" />
      </div>

      <Panel
        kicker="Forecast tape scrubber"
        title={current ? `${hhmm(current.created_at)} · ${current.prediction_id}` : 'No tape loaded'}
        actions={
          <div className="flex items-center gap-1">
            <Button onClick={() => setIndex(Math.max(0, cursor - 1))} disabled={cursor <= 0}>
              ◀ step
            </Button>
            <Button onClick={() => setIndex(Math.min(ordered.length - 1, cursor + 1))} disabled={cursor >= ordered.length - 1}>
              step ▶
            </Button>
            <Button tone="ghost" onClick={() => setIndex(null)}>
              live
            </Button>
          </div>
        }
      >
        {ordered.length === 0 ? (
          <Empty label="No forecast tape" field="predictions" />
        ) : (
          <>
            <input
              type="range"
              min={0}
              max={ordered.length - 1}
              value={cursor}
              onChange={(event) => setIndex(Number(event.target.value))}
              className="w-full accent-[#38d7ff]"
            />
            <div className="mt-1 flex justify-between font-mono text-[9px] text-ink-4">
              <span>{hhmm(ordered[0]?.created_at)}</span>
              <span className="text-signal">
                {cursor + 1} / {ordered.length}
              </span>
              <span>{hhmm(ordered.at(-1)?.created_at)}</span>
            </div>
            {option && <EChart option={option} className="mt-2 h-[220px] w-full" />}
          </>
        )}
      </Panel>

      <div className="grid gap-2 lg:grid-cols-2">
        <Panel kicker="State at cursor" title="Forecast under inspection">
          {!current ? (
            <Empty label="Nothing selected" field="predictions" />
          ) : (
            <>
              <Row label="Spot" value={num(current.spy_price)} />
              <Row label="Forecast P50" value={num(current.predicted_price)} tone="signal" />
              <Row label="P10 / P90" value={`${num(current.predicted_low)} / ${num(current.predicted_high)}`} />
              <Row label="P(up)" value={pct(current.probability_up, 1)} />
              <Row label="Actual" value={num(current.actual_price)} />
              <Row
                label="Direction"
                value={current.direction_correct == null ? 'PENDING' : current.direction_correct ? 'CORRECT' : 'WRONG'}
                tone={current.direction_correct == null ? undefined : current.direction_correct ? 'pass' : 'fail'}
              />
              <div className="mt-2">
                <Button onClick={() => setSelected(current.prediction_id)}>Open inspector</Button>
              </div>
            </>
          )}
        </Panel>
        <Panel kicker="Scope" title="What this scrubber covers">
          <div className="font-mono text-[10px] leading-relaxed text-ink-2">
            The scrubber replays the <span className="text-signal-dim">forecast tape</span> the
            dashboard already holds — spot, forecast, distribution and realised
            outcome per anchor.
          </div>
          <div className="mt-2 font-mono text-[10px] leading-relaxed text-ink-3">
            Rewinding candles, regime, internals, the candidate book and the
            decision ladder together needs an as-of state endpoint backed by the
            engine's captured-tape store. The engine has that store — deterministic
            replay is what produces the{' '}
            <span className="text-signal-dim">replay.status</span> above — but the
            dashboard API does not expose a point-in-time state read yet.
          </div>
        </Panel>
      </div>

      <ForecastInspector id={selected} onClose={() => setSelected(null)} />
    </Screen>
  )
}

export function AttributionScreen() {
  const state = useWorkstation((store) => store.state)
  const rows = state.attribution ?? []

  const option = useMemo(() => {
    const usable = rows.filter((row) => isNum(row.count))
    if (!usable.length) return null
    const sorted = [...usable].sort((a, b) => Number(a.count) - Number(b.count))
    return {
      animation: false,
      grid: { top: 10, right: 40, bottom: 20, left: 150 },
      tooltip: { ...chartTheme.tooltip, trigger: 'item' },
      xAxis: { type: 'value', ...chartTheme.axis },
      yAxis: {
        type: 'category',
        data: sorted.map((row) => titleize(row.cause)),
        ...chartTheme.axis,
        splitLine: { show: false },
      },
      series: [
        {
          type: 'bar',
          data: sorted.map((row) => Number(row.count)),
          barWidth: '58%',
          itemStyle: { color: '#38d7ff55', borderColor: '#38d7ff', borderWidth: 1 },
          label: { show: true, position: 'right', color: '#9db2c3', fontSize: 10, fontFamily: 'SFMono-Regular, monospace' },
        },
      ],
    }
  }, [rows])

  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel kicker="Failure decomposition" title="Where forecasts go wrong" bodyClassName="p-1">
          {option ? (
            <EChart option={option} className="h-[280px] w-full" />
          ) : (
            <Empty label="No attribution recorded" field="attribution" />
          )}
        </Panel>
        <Panel kicker="Constituent drivers" title="Latest transmission" scroll>
          {(state.constituent_attribution ?? []).length === 0 ? (
            <Empty label="No constituent attribution" field="constituent_attribution" />
          ) : (
            (state.constituent_attribution ?? []).slice(0, 14).map((row, index) => (
              <div key={`${row.symbol}-${index}`} className="flex items-baseline justify-between gap-2 border-b border-line/50 py-1 last:border-0">
                <span className="font-mono text-[10px] font-semibold text-ink-2">{row.symbol}</span>
                <span className={cn('tnum text-[11px]', Number(row.contribution) >= 0 ? 'text-pass' : 'text-fail')}>
                  {signed(Number(row.contribution) * 100, 2)}
                </span>
              </div>
            ))
          )}
        </Panel>
      </div>
      <Panel kicker="Session" title="Latest confirmation timestamps">
        <Row label="State timestamp" value={clock(state.timestamp)} />
        <Row label="Exchange clock" value={state.session?.exchange_time ?? EMPTY} />
        <Row label="Forced flat" value={state.session?.forced_flat_time ?? EMPTY} />
      </Panel>
    </Screen>
  )
}
