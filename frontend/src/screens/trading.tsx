import { PayoffChart } from '@/components/charts/PayoffChart'
import { DecisionPanel } from '@/components/panels/DecisionPanel'
import {
  ExitEngine,
  PositionGreeks,
  PositionSummary,
  RiskBudget,
  ThesisChecklist,
} from '@/components/panels/PositionPanels'
import {
  Chip,
  Empty,
  Panel,
  Row,
  TableShell,
  Td,
  Th,
  toneForState,
} from '@/components/ui/primitives'
import { EMPTY, clock, money, pct, signedMoney, stamp, titleize } from '@/lib/format'
import { useWorkstation } from '@/store/workstation'
import { Screen } from './Screen'

export function DecisionScreen() {
  const state = useWorkstation((store) => store.state)
  return (
    <Screen>
      <div className="grid min-h-0 gap-2 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <DecisionPanel decision={state.decision} />
        <div className="grid content-start gap-2">
          <Panel kicker="Risk envelope" title="What the decision was sized against">
            <Row label="Health state" value={state.decision?.health_state ?? EMPTY} />
            <Row label="Trust score" value={pct(state.decision?.trust_score)} />
            <Row label="Allowed risk" value={money(state.decision?.allowed_risk, 0)} />
            <Row label="Base risk" value={money(state.account?.base_risk, 0)} />
            <Row label="Buying power" value={money(state.account?.buying_power, 0)} />
            <Row label="Daily loss limit" value={money(state.account?.daily_loss_limit, 0)} />
            <Row
              label="Account source"
              value={titleize(state.account?.source)}
              tone={state.account?.valid ? undefined : 'fail'}
            />
          </Panel>
          <Panel kicker="Provenance" title="Decision record">
            <Row label="Decision id" value={state.decision?.decision_id ?? EMPTY} />
            <Row label="Prediction id" value={state.decision?.prediction_id ?? EMPTY} />
            <Row label="Candidate id" value={state.decision?.candidate_id ?? EMPTY} />
            <Row label="Created" value={stamp(state.decision?.created_at)} />
            <Row label="Reason code" value={state.decision?.reason ?? EMPTY} />
          </Panel>
        </div>
      </div>
    </Screen>
  )
}

export function OpportunityScreen() {
  const state = useWorkstation((store) => store.state)
  const eligible = (state.candidates ?? []).filter(
    (candidate) => String(candidate.status).toUpperCase() === 'ELIGIBLE',
  )
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <DecisionPanel decision={state.decision} />
        <Panel kicker="Qualified now" title={`${eligible.length} structures inside the gates`} scroll>
          {eligible.length === 0 ? (
            <Empty label="No qualified structures" field="candidates" />
          ) : (
            <div className="grid gap-2 xl:grid-cols-2">
              {eligible.map((candidate) => (
                <div key={candidate.candidate_id} className="border border-line bg-surface-2 p-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[12px] font-semibold text-ink">{titleize(candidate.strategy)}</span>
                    <Chip tone="pass">{pct(candidate.probability_profit, 0)} PoP</Chip>
                  </div>
                  <div className="mt-1">
                    <Row label="Expected P&L" value={signedMoney(candidate.expected_value, 0)} />
                    <Row label="Q edge" value={signedMoney(candidate.q_executable_edge, 0)} />
                    <Row label="Max loss" value={money(candidate.max_loss, 0)} tone="fail" />
                  </div>
                  <div className="mt-1 h-[110px]">
                    <PayoffChart
                      legs={candidate.legs ?? []}
                      entryCost={candidate.entry_value}
                      spot={state.market?.price}
                      quantiles={state.forecast_horizons?.['15m']?.distribution?.quantiles}
                      className="h-full w-full"
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </Screen>
  )
}

export function ActiveTradeScreen() {
  const state = useWorkstation((store) => store.state)
  const position = state.position
  return (
    <Screen>
      <div className="grid gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,320px)_minmax(0,320px)]">
        <div className="grid content-start gap-2">
          <Panel kicker="Position" title="Managed trade">
            <PositionSummary position={position} />
          </Panel>
          <Panel kicker="Payoff" title="Position at expiry" bodyClassName="p-1">
            {position?.open && position.legs?.length ? (
              <PayoffChart
                legs={position.legs}
                entryCost={position.entry_debit}
                spot={state.market?.price}
                quantiles={state.forecast_horizons?.['15m']?.distribution?.quantiles}
                className="h-[220px] w-full"
              />
            ) : (
              <Empty label="No position to price" field="position.legs" />
            )}
          </Panel>
        </div>
        <div className="grid content-start gap-2">
          <Panel kicker="Thesis" title="Live thesis state">
            <ThesisChecklist market={state.market} position={position} />
          </Panel>
          <Panel kicker="Greeks" title="Position sensitivity">
            <PositionGreeks position={position} />
          </Panel>
          <Panel kicker="Risk" title="Budget consumption">
            <RiskBudget position={position} allowedRisk={state.account?.allowed_risk} />
          </Panel>
        </div>
        <Panel kicker="Exit engine" title="Decision stack" scroll>
          <ExitEngine position={position} forcedFlat={state.session?.forced_flat_time ?? null} />
        </Panel>
      </div>
    </Screen>
  )
}

export function OrdersScreen() {
  const state = useWorkstation((store) => store.state)
  const commands = state.commands ?? []
  const reconciliation = (state.broker_reconciliation ?? {}) as Record<string, unknown>
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel kicker="Operator journal" title="Queued commands" bodyClassName="p-0" scroll>
          {commands.length === 0 ? (
            <Empty label="No operator commands" field="commands" />
          ) : (
            <TableShell>
              <thead>
                <tr>
                  <Th>Time</Th>
                  <Th>Command</Th>
                  <Th>Status</Th>
                  <Th>Reason</Th>
                  <Th>Message</Th>
                </tr>
              </thead>
              <tbody>
                {commands.map((row, index) => (
                  <tr key={row.id ?? index}>
                    <Td className="tnum">{clock(row.created_at)}</Td>
                    <Td className="text-ink">{row.command}</Td>
                    <Td>
                      <Chip tone={toneForState(row.status)}>{row.status ?? EMPTY}</Chip>
                    </Td>
                    <Td>{row.reason ?? EMPTY}</Td>
                    <Td>{row.message || EMPTY}</Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </Panel>
        <Panel kicker="Broker" title="Reconciliation state">
          {Object.keys(reconciliation).length === 0 ? (
            <Empty label="No reconciliation record" field="broker_reconciliation" />
          ) : (
            Object.entries(reconciliation).map(([key, value]) => (
              <Row
                key={key}
                label={key.replace(/_/g, ' ')}
                value={typeof value === 'boolean' ? (value ? 'YES' : 'NO') : String(value || EMPTY)}
                tone={key === 'blocked' && value ? 'fail' : key === 'ok' && value ? 'pass' : undefined}
              />
            ))
          )}
        </Panel>
      </div>
    </Screen>
  )
}

export function TradeHistoryScreen() {
  const state = useWorkstation((store) => store.state)
  // Closed-trade history is not part of the published dashboard state today;
  // the audit trail holds it. Say so rather than rendering an empty table that
  // looks like "no trades ever happened".
  return (
    <Screen>
      <Panel kicker="Closed trades" title="Trade history">
        <Empty label="Closed-trade history is not published on the state feed" field="positions (CLOSED)" />
        <div className="mt-2 border border-line bg-surface-2 px-3 py-2 font-mono text-[10px] leading-relaxed text-ink-3">
          The engine writes closed positions to the audit journal, and the
          validation run aggregates them into{' '}
          <span className="text-signal-dim">promotion.metrics</span>. Realised
          performance for the current validation window is on{' '}
          <span className="text-signal-dim">Governance → Paper Validation</span>.
          <div className="mt-1.5">
            Session totals published now: net day P&L {signedMoney(state.account?.daily_pnl)} ·{' '}
            {state.promotion?.trades ?? 0} closed trades counted by the last validation run.
          </div>
        </div>
      </Panel>
    </Screen>
  )
}
