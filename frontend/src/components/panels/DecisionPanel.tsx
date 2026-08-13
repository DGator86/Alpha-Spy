import { cn } from '@/lib/cn'
import { EMPTY, humanize, isNum, money, pct, signedMoney, titleize } from '@/lib/format'
import { Chip, Empty, Panel, Row } from '@/components/ui/primitives'
import type { Candidate, Decision, DecisionGate } from '@/lib/types'

const ACTION_COPY: Record<string, { headline: string; tone: 'pass' | 'watch' | 'fail' | 'neutral' }> = {
  SUBMIT_ORDER: { headline: 'SUBMIT ORDER', tone: 'pass' },
  PAPER_ORDER: { headline: 'PAPER ORDER', tone: 'pass' },
  NO_TRADE: { headline: 'NO TRADE', tone: 'fail' },
  WAITING: { headline: 'WAITING', tone: 'neutral' },
}

function strikeLabel(candidate: Candidate | null | undefined): string {
  const strikes = (candidate?.legs ?? [])
    .map((leg) => leg.strike)
    .filter(isNum)
    .map((strike) => Number(strike))
  if (!strikes.length) return EMPTY
  return strikes.join(' / ')
}

function GateLine({ gate }: { gate: DecisionGate }) {
  // Passed gates use a filled mark, failures an X, so the column scans
  // vertically without relying on colour alone. The measurement gets its own
  // line rather than competing with the label for a ~340px rail — truncating
  // "$83.00 allowed against $100.00 base" down to "$83.00 allow" loses exactly
  // the half that answers why.
  return (
    <li className="grid grid-cols-[12px_minmax(0,1fr)] gap-x-2 py-[3px] font-mono text-[10px] leading-tight">
      <span className={cn('text-center', gate.passed ? 'text-pass' : 'text-fail')}>
        {gate.passed ? '✓' : '✕'}
      </span>
      <span className={cn('min-w-0 truncate', gate.passed ? 'text-ink-2' : 'text-fail font-semibold')}>
        {gate.label ?? titleize(gate.name)}
      </span>
      {gate.detail && (
        <span className="col-start-2 truncate text-[9px] text-ink-4" title={gate.detail}>
          {gate.detail}
        </span>
      )}
    </li>
  )
}

export function DecisionPanel({
  decision,
  className,
}: {
  decision: Decision | undefined
  className?: string
}) {
  const action = String(decision?.action ?? 'WAITING').toUpperCase()
  const copy = ACTION_COPY[action] ?? { headline: action, tone: 'neutral' as const }
  const gates = decision?.gates ?? []
  const qualifiers = gates.filter((gate) => gate.kind !== 'veto')
  const vetoes = gates.filter((gate) => gate.kind === 'veto')
  const candidate = decision?.candidate ?? null
  const failed = gates.filter((gate) => !gate.passed)

  const headlineTone =
    copy.tone === 'pass'
      ? 'text-pass'
      : copy.tone === 'fail'
        ? 'text-fail'
        : copy.tone === 'watch'
          ? 'text-watch'
          : 'text-ink-2'

  return (
    <Panel
      kicker="Alpha decision"
      title={candidate ? titleize(candidate.strategy) : 'Decision engine'}
      className={className}
      actions={
        <Chip tone={copy.tone === 'neutral' ? 'muted' : copy.tone}>
          {decision?.health_state ?? 'UNKNOWN'}
        </Chip>
      }
      bodyClassName="flex flex-col gap-3 overflow-auto"
    >
      <div>
        <div className={cn('font-mono text-[20px] leading-none font-bold tracking-tight', headlineTone)}>
          {copy.headline}
        </div>
        {candidate ? (
          <div className="tnum mt-1 text-[13px] text-ink">{strikeLabel(candidate)}</div>
        ) : (
          <div className="mt-1 font-mono text-[10px] text-ink-3">
            {/* The reason code is the whole point of a NO_TRADE. */}
            {decision?.reason ? humanize(decision.reason) : 'NO DECISION PUBLISHED'}
          </div>
        )}
      </div>

      {candidate && (
        <div className="border-t border-line pt-1">
          <Row label="Trade score" value={isNum(candidate.score) ? `${(Number(candidate.score) * 100).toFixed(0)} / 100` : EMPTY} tone="signal" />
          <Row label="P(profit)" value={pct(candidate.probability_profit)} />
          <Row
            label="Expected P&L"
            value={signedMoney(candidate.expected_value, 0)}
            tone={Number(candidate.expected_value) >= 0 ? 'pass' : 'fail'}
          />
          <Row label="Q-relative edge" value={signedMoney(candidate.q_executable_edge, 0)} />
          <Row label="Stress EV" value={signedMoney(candidate.stress_expected_value, 0)} />
          <Row label="Double-cost EV" value={signedMoney(candidate.doubled_cost_expected_value, 0)} />
          <Row label="Max loss" value={money(candidate.max_loss, 0)} tone="fail" />
        </div>
      )}

      {failed.length > 0 && (
        <div className="border border-fail/30 bg-fail/5 px-2 py-1.5">
          <div className="kicker text-fail">Blocking</div>
          <div className="mt-0.5 font-mono text-[10px] leading-snug text-fail">
            {failed.map((gate) => gate.label ?? titleize(gate.name)).join(' · ')}
          </div>
        </div>
      )}

      {gates.length === 0 ? (
        <Empty label="No gate ladder published" field="decision.gates" />
      ) : (
        <div className="grid gap-3">
          <div>
            <div className="kicker mb-1">Qualifiers</div>
            <ul className="border-t border-line/60">
              {qualifiers.map((gate) => (
                <GateLine key={gate.name} gate={gate} />
              ))}
            </ul>
          </div>
          <div>
            <div className="kicker mb-1">Vetoes</div>
            <ul className="border-t border-line/60">
              {vetoes.map((gate) => (
                <GateLine key={gate.name} gate={gate} />
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="mt-auto border-t border-line pt-1">
        <Row label="Allowed risk" value={money(decision?.allowed_risk, 0)} />
        <Row label="Trades today" value={decision?.trades_today ?? EMPTY} />
        <Row
          label="Structures"
          value={
            isNum(decision?.affordable_candidates)
              ? `${decision?.affordable_candidates} affordable / ${decision?.considered_candidates ?? EMPTY} ranked`
              : EMPTY
          }
        />
      </div>
    </Panel>
  )
}
