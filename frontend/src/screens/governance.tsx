import { ValidationChecklist } from '@/components/panels/ValidationChecklist'
import { AlertFeed } from '@/components/panels/SystemPanels'
import {
  Chip,
  Empty,
  Metric,
  Panel,
  Row,
  TableShell,
  Td,
  Th,
} from '@/components/ui/primitives'
import { EMPTY, stamp, threshold, titleize } from '@/lib/format'
import { useWorkstation } from '@/store/workstation'
import { Screen } from './Screen'

export function PaperValidationScreen() {
  const state = useWorkstation((store) => store.state)
  const promotion = state.promotion
  const complete = (promotion?.failed_gates ?? []).length === 0 && (promotion?.gates ?? []).length > 0

  return (
    <Screen>
      <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-5">
        <Metric label="Status" value={titleize(promotion?.status)} tone={complete ? 'pass' : 'watch'} className="border-0" />
        <Metric label="Paper sessions" value={promotion?.sessions ?? 0} className="border-0" />
        <Metric label="Matured forecasts" value={promotion?.matured_forecasts ?? 0} className="border-0" />
        <Metric label="Closed trades" value={promotion?.trades ?? 0} className="border-0" />
        <Metric
          label="Failing gates"
          value={(promotion?.failed_gates ?? []).length}
          tone={complete ? 'pass' : 'fail'}
          className="border-0"
        />
      </div>

      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel kicker="Launch checklist" title="Paper-to-live evidence gates" scroll>
          <ValidationChecklist promotion={promotion} replay={state.replay} />
        </Panel>
        <div className="grid content-start gap-2">
          <Panel kicker="Run" title="Validation provenance">
            <Row label="Validation id" value={promotion?.validation_id ?? EMPTY} />
            <Row label="Run at" value={stamp(promotion?.created_at)} />
            <Row label="Report" value={promotion?.report_path ?? EMPTY} />
            <Row label="Replay id" value={state.replay?.replay_id ?? EMPTY} />
            <Row label="Replay method" value={titleize(state.replay?.method)} />
          </Panel>
          <Panel kicker="Policy" title="Promotion semantics">
            <div className="font-mono text-[10px] leading-relaxed text-ink-2">
              Passing every gate makes the candidate{' '}
              <span className="text-signal-dim">eligible for manual live review</span>. It
              does not enable live trading. Real-money execution stays behind the
              production sentinel and an evidence-bound approval artifact,
              reviewed by a human.
            </div>
            <div className="mt-2">
              <Chip tone="pass">automatic live enable: never</Chip>
            </div>
          </Panel>
        </div>
      </div>
    </Screen>
  )
}

export function PromotionGatesScreen() {
  const state = useWorkstation((store) => store.state)
  const gates = state.promotion?.gates ?? []
  const failed = gates.filter((gate) => !gate.passed)

  return (
    <Screen>
      {failed.length > 0 && (
        <div className="border border-fail/40 bg-fail/5 px-3 py-2">
          <div className="kicker text-fail">Blocking promotion</div>
          <div className="mt-1 font-mono text-[11px] text-fail">
            {failed.map((gate) => titleize(gate.name)).join(' · ')}
          </div>
        </div>
      )}
      <Panel kicker="Every gate" title="Threshold detail" bodyClassName="p-0" scroll>
        {gates.length === 0 ? (
          <Empty label="No validation run recorded" field="promotion.gates" />
        ) : (
          <TableShell className="max-h-[560px]">
            <thead>
              <tr>
                <Th>Gate</Th>
                <Th>Result</Th>
                <Th align="right">Actual</Th>
                <Th align="right">Threshold</Th>
                <Th>Detail</Th>
              </tr>
            </thead>
            <tbody>
              {gates.map((gate) => (
                <tr key={gate.name} className={gate.passed ? undefined : 'bg-fail/5'}>
                  <Td className="font-medium text-ink">{titleize(gate.name)}</Td>
                  <Td>
                    <Chip tone={gate.passed ? 'pass' : 'fail'}>{gate.passed ? 'PASS' : 'FAIL'}</Chip>
                  </Td>
                  <Td align="right" className={gate.passed ? 'text-ink' : 'text-fail'}>
                    {threshold(gate.actual)}
                  </Td>
                  <Td align="right" className="text-ink-3">{threshold(gate.threshold)}</Td>
                  <Td className="text-ink-3">{gate.detail || EMPTY}</Td>
                </tr>
              ))}
            </tbody>
          </TableShell>
        )}
      </Panel>
    </Screen>
  )
}

export function AuditTrailScreen() {
  const state = useWorkstation((store) => store.state)
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Panel kicker="Alert journal" title="Every published alert" bodyClassName="p-0" scroll>
          <AlertFeed alerts={state.alerts} limit={200} />
        </Panel>
        <div className="grid content-start gap-2">
          <Panel kicker="Integrity" title="Data provenance">
            <Row label="Engine" value={`${state.engine?.name ?? EMPTY} ${state.engine?.version ?? ''}`} />
            <Row label="Execution mode" value={titleize(state.engine?.mode)} />
            <Row label="Broker environment" value={titleize(state.engine?.environment)} />
            <Row label="Market data" value={titleize(state.engine?.market_data_environment)} />
            <Row label="Stream enabled" value={state.engine?.market_stream_enabled ? 'YES' : 'NO'} />
          </Panel>
          <Panel kicker="Input health" title="Required-input coverage">
            {Object.keys(state.health?.input_health ?? {}).length === 0 ? (
              <Empty label="No input health record" field="health.input_health" />
            ) : (
              Object.entries(state.health?.input_health ?? {}).map(([key, value]) => (
                <Row
                  key={key}
                  label={key.replace(/_/g, ' ')}
                  value={typeof value === 'number' ? value.toFixed(3) : String(value ?? EMPTY)}
                />
              ))
            )}
          </Panel>
        </div>
      </div>
    </Screen>
  )
}
