import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import { cn } from '@/lib/cn'
import { EMPTY, isNum, num, signed, signedPct } from '@/lib/format'
import { Button, Chip } from '@/components/ui/primitives'
import { TITLES } from '@/nav'
import { useWorkstation } from '@/store/workstation'
import type { CommandName } from '@/lib/types'

const FLATTEN_PHRASE = 'FLATTEN_SPY_ALPHA_POSITION'

/** Returns a one-shot flash class whenever the tracked number moves. */
function useTickFlash(value: number | null): string {
  const previous = useRef<number | null>(null)
  const [flash, setFlash] = useState('')
  useEffect(() => {
    const last = previous.current
    previous.current = value
    if (last === null || value === null || last === value) return
    const next = value > last ? 'tick-up' : 'tick-down'
    setFlash(next)
    const timer = setTimeout(() => setFlash(''), 640)
    return () => clearTimeout(timer)
  }, [value])
  return flash
}

function Divider() {
  return <span className="h-5 w-px shrink-0 bg-line" />
}

export function TopBar({ onToast }: { onToast: (message: string) => void }) {
  const location = useLocation()
  const state = useWorkstation((store) => store.state)
  const lastFrameAt = useWorkstation((store) => store.lastFrameAt)
  const sendCommand = useWorkstation((store) => store.sendCommand)
  const adminToken = useWorkstation((store) => store.adminToken)
  const setAdminToken = useWorkstation((store) => store.setAdminToken)

  const [flattenOpen, setFlattenOpen] = useState(false)
  const [phrase, setPhrase] = useState('')
  const [latency, setLatency] = useState<number | null>(null)

  const market = state.market
  const price = isNum(market?.price) ? Number(market?.price) : null
  const flash = useTickFlash(price)

  useEffect(() => {
    // Age of the newest frame, sampled on a timer so it keeps counting up when
    // the socket goes quiet instead of freezing at its last value.
    const timer = setInterval(() => {
      setLatency(lastFrameAt === null ? null : Date.now() - lastFrameAt)
    }, 500)
    return () => clearInterval(timer)
  }, [lastFrameAt])

  const open = state.session?.market_open
  const security = state.security
  const locked = !security?.live_authorization
  const change = market?.change
  const changeTone = !isNum(change) ? 'text-ink-3' : Number(change) >= 0 ? 'text-pass' : 'text-fail'

  async function run(command: CommandName, confirm = '') {
    let token = adminToken
    if (!token) {
      token = window.prompt('Administrator token') ?? ''
      if (!token) return
      setAdminToken(token)
    }
    const result = await sendCommand(command, confirm)
    onToast(result.message)
  }

  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
      <div className="min-w-0 shrink-0">
        <div className="kicker leading-none">Alpha-SPY</div>
        <div className="truncate text-[12px] leading-tight font-semibold text-ink">
          {TITLES[location.pathname] ?? 'Workstation'}
        </div>
      </div>

      <Divider />

      <div className={cn('flex shrink-0 items-baseline gap-2 px-1', flash)}>
        <span className="font-mono text-[10px] text-ink-3">{market?.symbol ?? 'SPY'}</span>
        <span className="tnum text-[16px] leading-none font-bold text-ink">{num(price, 2)}</span>
        <span className={cn('tnum text-[11px]', changeTone)}>
          {signed(change, 2)} · {signedPct(market?.change_pct, 2)}
        </span>
      </div>

      <Divider />

      <div className="flex shrink-0 items-center gap-1.5">
        <Chip tone={open ? 'pass' : 'muted'}>{open ? 'Market open' : 'Market closed'}</Chip>
        <Chip tone={toneForWindow(state.session?.entry_window)}>
          entry {String(state.session?.entry_window ?? '—').toLowerCase()}
        </Chip>
      </div>

      <Divider />

      <div className="flex shrink-0 items-baseline gap-1 font-mono text-[10px]">
        <span className="text-ink-4">DATA</span>
        <span
          className={cn(
            'tnum',
            latency === null ? 'text-ink-3' : latency > 8000 ? 'text-fail' : latency > 3000 ? 'text-watch' : 'text-pass',
          )}
        >
          {latency === null ? EMPTY : `${(latency / 1000).toFixed(1)}s`}
        </span>
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        <span className="tnum font-mono text-[10px] text-ink-3">
          {state.session?.exchange_time ?? EMPTY}
          <span className="ml-1 text-ink-4">ET</span>
        </span>

        <Chip tone={locked ? 'pass' : 'fail'} title={security?.production_approval_reason ?? undefined}>
          {locked ? '🔒 ' : '⚠ '}
          {String(security?.execution_mode ?? state.engine?.mode ?? 'UNKNOWN').replace(/_/g, ' ')}
        </Chip>

        <Button onClick={() => void run('PAUSE_NEW_ENTRIES')} tone="warning">
          Pause
        </Button>
        <Button onClick={() => void run('RESUME_NEW_ENTRIES')} tone="success">
          Resume
        </Button>
        <Button onClick={() => setFlattenOpen(true)} tone="danger">
          Flatten
        </Button>
      </div>

      <Dialog.Root open={flattenOpen} onOpenChange={setFlattenOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70" />
          <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[420px] -translate-x-1/2 -translate-y-1/2 border border-fail/50 bg-surface p-4 shadow-2xl">
            <Dialog.Title className="text-[13px] font-semibold text-fail">Emergency flatten</Dialog.Title>
            <Dialog.Description className="mt-1 text-[11px] leading-snug text-ink-2">
              Queues a request to close only the position managed by Alpha-SPY. The
              dashboard never submits broker orders itself — the engine picks the
              command up and acts on it.
            </Dialog.Description>
            <code className="mt-3 block bg-surface-2 px-2 py-1.5 font-mono text-[11px] text-watch">
              {FLATTEN_PHRASE}
            </code>
            <input
              value={phrase}
              onChange={(event) => setPhrase(event.target.value)}
              placeholder="Type the confirmation phrase"
              className="mt-2 w-full border border-line bg-surface-2 px-2 py-1.5 font-mono text-[11px] text-ink outline-none focus:border-signal/60"
            />
            <div className="mt-3 flex justify-end gap-2">
              <Button size="md" tone="ghost" onClick={() => setFlattenOpen(false)}>
                Cancel
              </Button>
              <Button
                size="md"
                tone="danger"
                disabled={phrase.trim() !== FLATTEN_PHRASE}
                onClick={async () => {
                  await run('FLATTEN_MANAGED_POSITION', phrase.trim())
                  setPhrase('')
                  setFlattenOpen(false)
                }}
              >
                Queue flatten
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </header>
  )
}

function toneForWindow(value: string | null | undefined): 'pass' | 'watch' | 'muted' {
  const text = String(value ?? '').toUpperCase()
  if (text === 'OPEN') return 'pass'
  if (text === 'PAUSED') return 'watch'
  return 'muted'
}
