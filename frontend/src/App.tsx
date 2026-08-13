import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Sidebar } from '@/components/chrome/Sidebar'
import { TopBar } from '@/components/chrome/TopBar'
import { Button } from '@/components/ui/primitives'
import { bootstrap, useWorkstation } from '@/store/workstation'
import { CommandCenter } from '@/screens/CommandCenter'
import { InternalsScreen, OptionsScreen, RegimeScreen, SpyScreen } from '@/screens/market'
import {
  ActiveTradeScreen,
  DecisionScreen,
  OpportunityScreen,
  OrdersScreen,
  TradeHistoryScreen,
} from '@/screens/trading'
import {
  CalibrationScreen,
  ForecastsScreen,
  ModelsScreen,
  PvsQScreen,
} from '@/screens/intelligence'
import {
  AttributionScreen,
  ConfirmationTapeScreen,
  ReplayLabScreen,
} from '@/screens/research'
import {
  AuditTrailScreen,
  PaperValidationScreen,
  PromotionGatesScreen,
} from '@/screens/governance'
import {
  BrokerScreen,
  ConfigurationScreen,
  DataFeedsScreen,
  EventCalendarScreen,
  SecurityScreen,
  ServicesScreen,
} from '@/screens/system'

function AuthGate() {
  const setViewToken = useWorkstation((store) => store.setViewToken)
  const authError = useWorkstation((store) => store.authError)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)

  return (
    <div className="flex h-full items-center justify-center bg-ground">
      <form
        className="w-[320px] border border-line bg-surface p-5"
        onSubmit={async (event) => {
          event.preventDefault()
          setBusy(true)
          await setViewToken(token.trim())
          setBusy(false)
        }}
      >
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center border border-signal/40 bg-signal/10 font-mono text-[18px] font-bold text-signal">
          A
        </div>
        <h1 className="text-center text-[13px] font-semibold text-ink">Alpha-SPY Workstation</h1>
        <p className="mt-1 text-center font-mono text-[10px] leading-snug text-ink-3">
          Enter the dashboard view token configured on the host.
        </p>
        <input
          type="password"
          autoComplete="current-password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="Dashboard token"
          className="mt-3 w-full border border-line bg-surface-2 px-2 py-2 font-mono text-[12px] text-ink outline-none focus:border-signal/60"
        />
        <Button type="submit" tone="primary" size="md" className="mt-2 w-full" disabled={busy || !token.trim()}>
          {busy ? 'Verifying…' : 'Unlock'}
        </Button>
        {authError && <div className="mt-2 text-center font-mono text-[10px] text-fail">{authError}</div>}
      </form>
    </div>
  )
}

function Toast({ message }: { message: string }) {
  if (!message) return null
  return (
    <div className="pointer-events-none fixed bottom-4 left-1/2 z-50 -translate-x-1/2 border border-line-bright bg-surface-2 px-4 py-2 font-mono text-[11px] text-ink shadow-lg">
      {message}
    </div>
  )
}

export default function App() {
  const viewTokenRequired = useWorkstation((store) => store.viewTokenRequired)
  const disconnect = useWorkstation((store) => store.disconnect)
  const [toast, setToast] = useState('')

  useEffect(() => {
    void bootstrap()
    return () => disconnect()
  }, [disconnect])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(''), 3200)
    return () => clearTimeout(timer)
  }, [toast])

  if (viewTokenRequired) return <AuthGate />

  return (
    <div className="flex h-full min-h-0 bg-ground">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar onToast={setToast} />
        <Routes>
          <Route path="/" element={<CommandCenter />} />
          <Route path="/decision" element={<DecisionScreen />} />

          <Route path="/market/spy" element={<SpyScreen />} />
          <Route path="/market/internals" element={<InternalsScreen />} />
          <Route path="/market/regime" element={<RegimeScreen />} />
          <Route path="/market/options" element={<OptionsScreen />} />

          <Route path="/trading/opportunity" element={<OpportunityScreen />} />
          <Route path="/trading/active" element={<ActiveTradeScreen />} />
          <Route path="/trading/orders" element={<OrdersScreen />} />
          <Route path="/trading/history" element={<TradeHistoryScreen />} />

          <Route path="/intelligence/forecasts" element={<ForecastsScreen />} />
          <Route path="/intelligence/pq" element={<PvsQScreen />} />
          <Route path="/intelligence/models" element={<ModelsScreen />} />
          <Route path="/intelligence/calibration" element={<CalibrationScreen />} />

          <Route path="/research/tape" element={<ConfirmationTapeScreen />} />
          <Route path="/research/replay" element={<ReplayLabScreen />} />
          <Route path="/research/attribution" element={<AttributionScreen />} />

          <Route path="/governance/validation" element={<PaperValidationScreen />} />
          <Route path="/governance/promotion" element={<PromotionGatesScreen />} />
          <Route path="/governance/audit" element={<AuditTrailScreen />} />

          <Route path="/system/services" element={<ServicesScreen />} />
          <Route path="/system/feeds" element={<DataFeedsScreen />} />
          <Route path="/system/events" element={<EventCalendarScreen />} />
          <Route path="/system/broker" element={<BrokerScreen />} />
          <Route path="/system/config" element={<ConfigurationScreen />} />
          <Route path="/system/security" element={<SecurityScreen />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <Toast message={toast} />
    </div>
  )
}
