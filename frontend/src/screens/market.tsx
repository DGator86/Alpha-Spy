import { useMemo, useState } from 'react'
import { SpyChart } from '@/components/charts/SpyChart'
import { PayoffChart } from '@/components/charts/PayoffChart'
import { HorizonRibbon } from '@/components/panels/HorizonRibbon'
import {
  ConstituentPressure,
  IndexStateHeatmap,
  VolatilityState,
} from '@/components/panels/InternalsHeatmap'
import { RegimeCockpit } from '@/components/panels/RegimeCockpit'
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
import { EMPTY, isNum, money, num, pct, signedMoney, titleize } from '@/lib/format'
import { useWorkstation } from '@/store/workstation'
import type { Candidate } from '@/lib/types'
import { Screen } from './Screen'

export function SpyScreen() {
  const state = useWorkstation((store) => store.state)
  const market = state.market
  return (
    <Screen>
      <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-2">
        <Panel kicker="SPY" title="Price and forecast cone" bodyClassName="p-0">
          {(state.price_series ?? []).length > 0 ? (
            <SpyChart
              series={state.price_series ?? []}
              horizons={state.forecast_horizons}
              className="h-full min-h-[320px] w-full"
            />
          ) : (
            <Empty label="No price history" field="price_series" />
          )}
        </Panel>
        <div className="grid gap-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel kicker="Term structure" title="Horizon ribbon" bodyClassName="p-0">
            <HorizonRibbon horizons={state.forecast_horizons} />
          </Panel>
          <Panel kicker="Quote" title="Microstructure">
            <Row label="Last" value={num(market?.price)} />
            <Row label="Bid / ask" value={`${num(market?.bid)} / ${num(market?.ask)}`} />
            <Row label="Spread" value={num(market?.spread, 3)} />
            <Row label="Liquidity" value={titleize(market?.liquidity_state)} />
            <Row label="Gamma state" value={titleize(market?.gamma_state)} />
            <Row label="Event state" value={titleize(market?.event_state)} />
          </Panel>
        </div>
      </div>
    </Screen>
  )
}

export function InternalsScreen() {
  const state = useWorkstation((store) => store.state)
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-2">
        <Panel kicker="Index state" title="Breadth, correlation and dispersion">
          <IndexStateHeatmap market={state.market} />
        </Panel>
        <Panel kicker="Volatility state" title="Physical vs implied">
          <VolatilityState market={state.market} />
          <div className="mt-2 border-t border-line pt-2">
            <Row label="IV reference expiry" value={state.market?.iv_reference_expiration ?? EMPTY} />
            <Row label="Surface coverage" value={pct(state.market?.iv_coverage)} />
          </div>
        </Panel>
      </div>
      <Panel kicker="Constituent transmission" title="What is actually driving SPY" scroll>
        <ConstituentPressure rows={state.constituent_attribution} limit={10} />
      </Panel>
      <Panel kicker="Causal chain" title="Constituent to option edge">
        <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] tracking-wide text-ink-3">
          {['Constituent pressure', 'Breadth / concentration', 'Correlation / dispersion', 'SPY distribution', 'Option edge'].map(
            (node, index, all) => (
              <span key={node} className="flex items-center gap-2">
                <span
                  className={
                    index === all.length - 1
                      ? 'border border-signal/40 bg-signal/10 px-2 py-1 text-signal'
                      : 'border border-line bg-surface-2 px-2 py-1'
                  }
                >
                  {node.toUpperCase()}
                </span>
                {index < all.length - 1 && <span className="text-ink-4">→</span>}
              </span>
            ),
          )}
        </div>
      </Panel>
    </Screen>
  )
}

export function RegimeScreen() {
  const state = useWorkstation((store) => store.state)
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <Panel kicker="Hierarchical regime" title="Regime cockpit" scroll>
          <RegimeCockpit market={state.market} />
        </Panel>
        <div className="grid content-start gap-2">
          <Panel kicker="Composite" title="Published regime label">
            <div className="font-mono text-[16px] font-bold text-signal">
              {titleize(state.market?.regime)}
            </div>
            <div className="mt-2">
              <Row label="Gamma proxy" value={titleize(state.market?.gamma_state)} />
              <Row label="Event source" value={titleize(state.market?.event_source)} />
              <Row
                label="History samples"
                value={
                  isNum((state.market?.regime_state as { history_samples?: number })?.history_samples)
                    ? String((state.market?.regime_state as { history_samples?: number }).history_samples)
                    : EMPTY
                }
              />
            </div>
          </Panel>
          <Panel kicker="Strategy eligibility" title="Structure × regime" scroll>
            {(state.strategy_matrix ?? []).length === 0 ? (
              <Empty label="No eligibility matrix" field="strategy_matrix" />
            ) : (
              <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
                {(state.strategy_matrix ?? []).map((row, index) => (
                  <div key={`${row.strategy}-${index}`} className="border border-line bg-surface-2 p-2">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-[11px] font-semibold text-ink">
                        {titleize(row.strategy)}
                      </span>
                      <Chip tone={toneForState(row.status)}>{row.status ?? EMPTY}</Chip>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between font-mono text-[10px]">
                      <span className="text-ink-4">EV</span>
                      <span className={Number(row.expectancy) >= 0 ? 'tnum text-pass' : 'tnum text-fail'}>
                        {signedMoney(row.expectancy, 0)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </Screen>
  )
}

type SortKey = 'score' | 'expected_value' | 'probability_profit' | 'max_loss' | 'q_executable_edge'

/**
 * Options strategy lab.
 *
 * Every structure the ranker scored, including the ones it rejected and why.
 * Selecting a row opens its payoff against the forecast quantiles, so the
 * selection logic becomes inspectable rather than magical.
 */
export function OptionsScreen() {
  const state = useWorkstation((store) => store.state)
  const [sort, setSort] = useState<SortKey>('score')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const rows = useMemo(() => {
    const list = [...(state.candidates ?? [])]
    return list.sort((a, b) => {
      const av = Number(a[sort] ?? Number.NEGATIVE_INFINITY)
      const bv = Number(b[sort] ?? Number.NEGATIVE_INFINITY)
      // Max loss is the one column where smaller is better.
      return sort === 'max_loss' ? av - bv : bv - av
    })
  }, [state.candidates, sort])

  const selected: Candidate | undefined =
    rows.find((row) => row.candidate_id === selectedId) ?? rows[0]

  const quantiles = state.forecast_horizons?.['15m']?.distribution?.quantiles

  const columns: [SortKey | null, string, 'left' | 'right'][] = [
    [null, 'Strategy', 'left'],
    [null, 'Strikes', 'left'],
    [null, 'Cost', 'right'],
    ['max_loss', 'Max loss', 'right'],
    ['probability_profit', 'P(P)', 'right'],
    ['expected_value', 'P EV', 'right'],
    ['q_executable_edge', 'Q edge', 'right'],
    [null, 'Stress EV', 'right'],
    ['score', 'Score', 'right'],
    [null, 'Status', 'left'],
  ]

  return (
    <Screen>
      <div className="grid min-h-0 gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <Panel
          kicker="Ranked structures"
          title={`Strategy lab · ${rows.length} scored`}
          bodyClassName="p-0 min-h-0 overflow-hidden"
        >
          {rows.length === 0 ? (
            <Empty label="No candidate structures" field="candidates" />
          ) : (
            <TableShell className="max-h-[440px]">
              <thead>
                <tr>
                  {columns.map(([key, label, align]) => (
                    <Th
                      key={label}
                      align={align}
                      className={key ? 'cursor-pointer select-none hover:text-signal' : undefined}
                      onClick={key ? () => setSort(key) : undefined}
                    >
                      {label}
                      {sort === key && <span className="ml-1 text-signal">▾</span>}
                    </Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const active = row.candidate_id === selected?.candidate_id
                  return (
                    <tr
                      key={row.candidate_id ?? row.strategy}
                      onClick={() => setSelectedId(row.candidate_id ?? null)}
                      className={
                        active
                          ? 'cursor-pointer bg-signal/10'
                          : 'cursor-pointer hover:bg-surface-2'
                      }
                    >
                      <Td className="font-medium text-ink">{titleize(row.strategy)}</Td>
                      <Td className="tnum">
                        {(row.legs ?? []).map((leg) => leg.strike).filter(isNum).join(' / ') || EMPTY}
                      </Td>
                      <Td align="right">{money(row.entry_value)}</Td>
                      <Td align="right" className="text-fail">{money(row.max_loss, 0)}</Td>
                      <Td align="right">{pct(row.probability_profit, 0)}</Td>
                      <Td align="right" className={Number(row.expected_value) >= 0 ? 'text-pass' : 'text-fail'}>
                        {signedMoney(row.expected_value, 0)}
                      </Td>
                      <Td align="right">{signedMoney(row.q_executable_edge, 0)}</Td>
                      <Td align="right" className={Number(row.stress_expected_value) >= 0 ? 'text-ink-2' : 'text-fail'}>
                        {signedMoney(row.stress_expected_value, 0)}
                      </Td>
                      <Td align="right" className="font-semibold text-signal">
                        {isNum(row.score) ? (Number(row.score) * 100).toFixed(0) : EMPTY}
                      </Td>
                      <Td>
                        <Chip tone={toneForState(row.status)}>{row.status ?? EMPTY}</Chip>
                      </Td>
                    </tr>
                  )
                })}
              </tbody>
            </TableShell>
          )}
        </Panel>

        <div className="grid content-start gap-2">
          <Panel kicker="Selected structure" title={titleize(selected?.strategy) || 'No selection'}>
            {!selected ? (
              <Empty label="Select a structure" field="candidates" />
            ) : (
              <>
                <div className="grid grid-cols-2 gap-px bg-line">
                  <Metric label="Score" value={isNum(selected.score) ? (Number(selected.score) * 100).toFixed(0) : EMPTY} tone="signal" className="border-0" size="sm" />
                  <Metric label="P(profit)" value={pct(selected.probability_profit, 0)} className="border-0" size="sm" />
                  <Metric label="Expected P&L" value={signedMoney(selected.expected_value, 0)} className="border-0" size="sm" />
                  <Metric label="Max loss" value={money(selected.max_loss, 0)} tone="fail" className="border-0" size="sm" />
                </div>
                <div className="mt-2">
                  <Row label="Valuation method" value={titleize(selected.valuation_method)} />
                  <Row label="Double-cost EV" value={signedMoney(selected.doubled_cost_expected_value, 0)} />
                  <Row label="Breakevens" value={(selected.breakevens ?? []).map((b) => num(b)).join(' / ') || EMPTY} />
                  {selected.rejection_reason && (
                    <Row label="Rejected because" value={titleize(selected.rejection_reason)} tone="fail" />
                  )}
                </div>
                {Object.keys(selected.greeks ?? {}).length > 0 && (
                  <div className="mt-2 grid grid-cols-4 gap-px bg-line">
                    {Object.entries(selected.greeks ?? {}).map(([key, value]) => (
                      <Metric key={key} label={key} value={num(value, 3)} className="border-0" size="sm" />
                    ))}
                  </div>
                )}
              </>
            )}
          </Panel>

          <Panel kicker="Expiry payoff" title="Against the 15m distribution" bodyClassName="p-1">
            {selected?.legs?.length ? (
              <PayoffChart
                legs={selected.legs}
                entryCost={selected.entry_value}
                spot={state.market?.price}
                quantiles={quantiles}
                className="h-[220px] w-full"
              />
            ) : (
              <Empty label="No legs to price" field="candidates[].legs" />
            )}
          </Panel>
        </div>
      </div>
    </Screen>
  )
}
