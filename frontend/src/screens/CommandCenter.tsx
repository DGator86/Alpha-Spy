import { useMemo } from 'react'
import { LineStyle } from 'lightweight-charts'
import { SpyChart, type PriceLevel } from '@/components/charts/SpyChart'
import { DecisionPanel } from '@/components/panels/DecisionPanel'
import { HorizonRibbon } from '@/components/panels/HorizonRibbon'
import { IndexStateHeatmap, VolatilityState } from '@/components/panels/InternalsHeatmap'
import { AlertFeed } from '@/components/panels/SystemPanels'
import { Empty, Metric, Panel, pnlTone } from '@/components/ui/primitives'
import { EMPTY, isNum, money, pct, signedMoney } from '@/lib/format'
import { useWorkstation } from '@/store/workstation'

export function CommandCenter() {
  const state = useWorkstation((store) => store.state)
  const { market, position, account, health, audit, decision } = state

  const levels = useMemo<PriceLevel[]>(() => {
    const out: PriceLevel[] = []
    // Position strikes first: they are the levels that actually cost money.
    for (const leg of position?.legs ?? []) {
      if (isNum(leg.strike)) {
        out.push({
          price: Number(leg.strike),
          label: `${leg.side ?? ''} ${leg.strike}`.trim(),
          color: String(leg.side).toUpperCase() === 'SELL' ? '#ff4f6b' : '#2ee6a8',
          style: LineStyle.Solid,
        })
      }
    }
    if (isNum(market?.predicted_price_15m)) {
      out.push({ price: Number(market?.predicted_price_15m), label: '15m P50', color: '#38d7ff' })
    }
    return out
  }, [position?.legs, market?.predicted_price_15m])

  const criticalAlert = (state.alerts ?? []).find(
    (alert) => alert.severity === 'critical' && !alert.acknowledged,
  )

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)_auto] gap-2 p-2">
      {criticalAlert && (
        <div className="border border-fail bg-fail/10 px-3 py-1.5 font-mono text-[11px] font-semibold text-fail">
          CRITICAL · {criticalAlert.title} — {criticalAlert.message}
        </div>
      )}

      <div className="grid min-h-0 grid-cols-1 gap-2 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-2">
          <Panel
            kicker="SPY · price, running mean and P/Q forecast cone"
            title={
              <span className="flex items-baseline gap-3">
                <span className="tnum text-[15px]">{market?.price ? Number(market.price).toFixed(2) : EMPTY}</span>
                <span className="font-mono text-[10px] font-normal text-ink-3">
                  bid {market?.bid ? Number(market.bid).toFixed(2) : EMPTY} / ask{' '}
                  {market?.ask ? Number(market.ask).toFixed(2) : EMPTY}
                </span>
              </span>
            }
            bodyClassName="p-0"
          >
            {(state.price_series ?? []).length > 0 ? (
              <SpyChart
                series={state.price_series ?? []}
                horizons={state.forecast_horizons}
                levels={levels}
                className="h-full min-h-[240px] w-full"
              />
            ) : (
              <Empty label="No price history" field="price_series" />
            )}
          </Panel>

          <Panel kicker="Multi-horizon forecast" title="Term structure" bodyClassName="p-0">
            <HorizonRibbon horizons={state.forecast_horizons} />
          </Panel>
        </div>

        <DecisionPanel decision={decision} className="min-h-0" />
      </div>

      <div className="grid items-stretch gap-2 lg:grid-cols-[repeat(3,minmax(0,1fr))]">
        <Panel kicker="Session" title="Account and audit posture" bodyClassName="p-0">
          <div className="grid h-full grid-cols-2 gap-px bg-line sm:grid-cols-3">
            <Metric label="Equity" value={money(account?.equity, 0)} hint={`${money(account?.buying_power, 0)} buying power`} className="border-0" />
            <Metric
              label="Day P&L"
              value={signedMoney(account?.daily_pnl)}
              hint={pct(account?.daily_pnl_pct, 2)}
              tone={pnlTone(account?.daily_pnl)}
              className="border-0"
            />
            <Metric label="Allowed risk" value={money(account?.allowed_risk, 0)} hint={`${money(account?.base_risk, 0)} base`} className="border-0" />
            <Metric
              label="Trust"
              value={pct(health?.trust_score, 0)}
              hint={String(health?.state ?? EMPTY)}
              tone={health?.state === 'GREEN' ? 'pass' : health?.state === 'RED' ? 'fail' : 'watch'}
              className="border-0"
            />
            <Metric label="Direction hit" value={pct(audit?.direction_accuracy)} hint={`${audit?.sample_size ?? 0} anchors`} className="border-0" />
            <Metric label="Range coverage" value={pct(audit?.range_coverage)} hint="target 80%" className="border-0" />
          </div>
        </Panel>

        <Panel kicker="Index state" title="Market internals" scroll>
          <IndexStateHeatmap market={market} />
          <div className="mt-2 border-t border-line pt-2">
            <VolatilityState market={market} />
          </div>
        </Panel>

        <Panel kicker="Audit & operations" title="Live alerts" scroll bodyClassName="p-0 overflow-auto">
          <AlertFeed alerts={state.alerts} limit={12} />
        </Panel>
      </div>
    </div>
  )
}
