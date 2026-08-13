import { Bar, Empty, Metric, Panel, Row } from '@/components/ui/primitives'
import { FeedFreshness, SecurityPanel, ServiceList } from '@/components/panels/SystemPanels'
import { EMPTY, age, clock, pct, titleize } from '@/lib/format'
import { useWorkstation } from '@/store/workstation'
import { Screen } from './Screen'

export function ServicesScreen() {
  const state = useWorkstation((store) => store.state)
  const health = state.health
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Panel kicker="Runtime" title="Service heartbeats" scroll>
          <ServiceList services={state.services} />
        </Panel>
        <div className="grid content-start gap-2">
          <Panel kicker="Supervisory" title="Health components">
            <div className="mb-2">
              <Metric
                label="Trust score"
                value={pct(health?.trust_score, 0)}
                hint={String(health?.state ?? EMPTY)}
                tone={health?.state === 'GREEN' ? 'pass' : health?.state === 'RED' ? 'fail' : 'watch'}
                size="lg"
              />
            </div>
            {Object.keys(health?.components ?? {}).length === 0 ? (
              <Empty label="No health components" field="health.components" />
            ) : (
              Object.entries(health?.components ?? {}).map(([key, value]) => (
                <div key={key} className="grid grid-cols-[124px_1fr_46px] items-center gap-2 py-[3px]">
                  <span className="truncate font-mono text-[9px] tracking-[0.1em] text-ink-3 uppercase">
                    {key.replace(/_/g, ' ')}
                  </span>
                  <Bar
                    value={value}
                    tone={Number(value) >= 0.8 ? 'pass' : Number(value) >= 0.5 ? 'watch' : 'fail'}
                  />
                  <span className="tnum text-right text-[10px] text-ink">{pct(value, 0)}</span>
                </div>
              ))
            )}
          </Panel>
          <Panel kicker="Session" title="Timing">
            <Row label="State timestamp" value={clock(state.timestamp)} />
            <Row label="State age" value={age(state.timestamp)} />
            <Row label="Exchange clock" value={state.session?.exchange_time ?? EMPTY} />
            <Row label="Entry window" value={titleize(state.session?.entry_window)} />
            <Row label="Entry grid" value={state.session?.entry_grid_minutes ? `${state.session.entry_grid_minutes}m` : EMPTY} />
            <Row label="Exit monitor" value={state.session?.exit_monitor_seconds ? `${state.session.exit_monitor_seconds}s` : EMPTY} />
            <Row label="Forced flat" value={state.session?.forced_flat_time ?? EMPTY} />
          </Panel>
        </div>
      </div>
    </Screen>
  )
}

export function DataFeedsScreen() {
  const state = useWorkstation((store) => store.state)
  const tradier = (state.tradier ?? {}) as Record<string, unknown>
  const quote = tradier.quote as { updated_at?: string } | null | undefined
  const account = tradier.account as { updated_at?: string; error?: string } | null | undefined

  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-2">
        <Panel kicker="Socket" title="Section freshness">
          <div className="mb-2 font-mono text-[9px] leading-snug text-ink-4">
            Each section of the state arrives independently. A single stale row
            here localises a partial outage that a whole-page timestamp hides.
          </div>
          <FeedFreshness />
        </Panel>
        <Panel kicker="Broker feed" title="Tradier">
          <Row label="Configured" value={tradier.configured ? 'YES' : 'NO'} tone={tradier.configured ? 'pass' : undefined} />
          <Row label="Environment" value={titleize(String(tradier.environment ?? ''))} />
          <Row label="Quote updated" value={age(quote?.updated_at)} />
          <Row label="Account updated" value={age(account?.updated_at)} />
          {account?.error && <Row label="Account error" value={account.error} tone="fail" />}
          <div className="mt-2 border-t border-line pt-2">
            <Row label="Market data environment" value={titleize(state.engine?.market_data_environment)} />
            <Row label="Stream enabled" value={state.engine?.market_stream_enabled ? 'YES' : 'NO'} />
          </div>
        </Panel>
      </div>
    </Screen>
  )
}

export function EventCalendarScreen() {
  const state = useWorkstation((store) => store.state)
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-2">
        <Panel kicker="Event protection" title="Current event state">
          <Metric
            label="Event state"
            value={titleize(state.market?.event_state)}
            hint={`source: ${state.market?.event_source ?? EMPTY}`}
            tone={/CLEAR|NONE/i.test(String(state.market?.event_state ?? '')) ? 'pass' : 'watch'}
            size="lg"
          />
          <div className="mt-2 font-mono text-[10px] leading-relaxed text-ink-3">
            Event protection is a veto on new entries, not a signal. When the
            calendar marks a window as elevated, the entry ladder blocks before a
            structure is ever priced.
          </div>
        </Panel>
        <Panel kicker="Scheduled" title="Upcoming events">
          <Empty label="The dashboard state does not carry the event schedule" field="event calendar" />
          <div className="mt-2 border border-line bg-surface-2 px-3 py-2 font-mono text-[10px] leading-relaxed text-ink-3">
            The engine loads its calendar from{' '}
            <span className="text-signal-dim">config/events.json</span> and
            publishes only the derived state above. Surfacing the schedule itself
            needs the engine to include it in the snapshot payload.
          </div>
        </Panel>
      </div>
    </Screen>
  )
}

export function BrokerScreen() {
  const state = useWorkstation((store) => store.state)
  const account = state.account
  const reconciliation = (state.broker_reconciliation ?? {}) as Record<string, unknown>

  return (
    <Screen>
      <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
        <Metric label="Equity" value={account?.equity != null ? `$${Number(account.equity).toLocaleString()}` : EMPTY} className="border-0" />
        <Metric label="Cash" value={account?.cash != null ? `$${Number(account.cash).toLocaleString()}` : EMPTY} className="border-0" />
        <Metric label="Buying power" value={account?.buying_power != null ? `$${Number(account.buying_power).toLocaleString()}` : EMPTY} className="border-0" />
        <Metric
          label="Balance source"
          value={titleize(account?.source)}
          tone={account?.valid ? 'pass' : 'fail'}
          hint={account?.valid ? 'accepted' : (account?.reason ?? 'invalid')}
          className="border-0"
        />
      </div>
      <div className="grid gap-2 lg:grid-cols-2">
        <Panel kicker="Risk limits" title="Configured envelope">
          <Row label="Base trade risk" value={account?.base_risk != null ? `$${Number(account.base_risk).toFixed(0)}` : EMPTY} />
          <Row label="Allowed risk now" value={account?.allowed_risk != null ? `$${Number(account.allowed_risk).toFixed(0)}` : EMPTY} />
          <Row label="Daily loss limit" value={account?.daily_loss_limit != null ? `$${Number(account.daily_loss_limit).toFixed(0)}` : EMPTY} />
          <Row label="Day P&L" value={account?.daily_pnl != null ? `$${Number(account.daily_pnl).toFixed(2)}` : EMPTY} tone={Number(account?.daily_pnl) >= 0 ? 'pass' : 'fail'} />
        </Panel>
        <Panel kicker="Reconciliation" title="Broker vs journal">
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

export function ConfigurationScreen() {
  const state = useWorkstation((store) => store.state)
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-2">
        <Panel kicker="Runtime" title="Engine identity">
          <Row label="Name" value={state.engine?.name ?? EMPTY} />
          <Row label="Version" value={state.engine?.version ?? EMPTY} />
          <Row label="Execution mode" value={titleize(state.engine?.mode)} />
          <Row label="Broker environment" value={titleize(state.engine?.environment)} />
          <Row label="Market data environment" value={titleize(state.engine?.market_data_environment)} />
          <Row label="Market stream" value={state.engine?.market_stream_enabled ? 'ENABLED' : 'DISABLED'} />
        </Panel>
        <Panel kicker="Session policy" title="Entry and exit timing">
          <Row label="Entry window" value={titleize(state.session?.entry_window)} />
          <Row label="Entry grid" value={state.session?.entry_grid_minutes ? `${state.session.entry_grid_minutes} min` : EMPTY} />
          <Row label="Exit monitor interval" value={state.session?.exit_monitor_seconds ? `${state.session.exit_monitor_seconds} s` : EMPTY} />
          <Row label="Forced flat" value={state.session?.forced_flat_time ?? EMPTY} />
          <div className="mt-2 font-mono text-[9px] leading-relaxed text-ink-4">
            Configuration is read-only here by design. The workstation issues
            guarded commands; it never writes engine configuration.
          </div>
        </Panel>
      </div>
    </Screen>
  )
}

export function SecurityScreen() {
  const state = useWorkstation((store) => store.state)
  return (
    <Screen>
      <div className="grid gap-2 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <Panel kicker="Authorization" title="Real-money execution">
          <SecurityPanel security={state.security} />
        </Panel>
        <div className="grid content-start gap-2">
          <Panel kicker="Separation" title="Data and execution environments">
            <Row label="Market data" value={titleize(state.security?.market_data_environment ?? state.engine?.market_data_environment)} />
            <Row label="Order execution" value={titleize(state.security?.broker_environment ?? state.engine?.environment)} />
            <Row label="Paper mode" value={state.security?.paper_mode ? 'ON' : 'OFF'} tone={state.security?.paper_mode ? 'pass' : 'watch'} />
            <div className="mt-2 font-mono text-[10px] leading-relaxed text-ink-3">
              Production market data with sandbox execution is the intended
              configuration during paper validation: forecasts are trained and
              scored against real quotes while no real money can be committed.
            </div>
          </Panel>
          <Panel kicker="Dashboard" title="Command authority">
            <Row label="Command surface" value="PAUSE / RESUME / FLATTEN / RELOAD" />
            <Row label="Order submission from UI" value="NEVER" tone="pass" />
            <div className="mt-2 font-mono text-[10px] leading-relaxed text-ink-3">
              Operator commands are queued for the engine, which remains
              authoritative. The workstation cannot place, modify or cancel a
              broker order.
            </div>
          </Panel>
        </div>
      </div>
    </Screen>
  )
}
