import { create } from 'zustand'
import { apiUrl, wsUrl } from '@/lib/backend'
import type { CommandName, ConnectionStatus, Frame, WorkstationState } from '@/lib/types'

const VIEW_TOKEN_KEY = 'alphaSpyViewToken'
const ADMIN_TOKEN_KEY = 'alphaSpyAdminToken'

interface WorkstationStore {
  state: WorkstationState
  status: ConnectionStatus
  /** Server frame sequence, so a gap is visible rather than silently tolerated. */
  seq: number
  lastFrameAt: number | null
  /** Per-section arrival times; drives the staleness readouts. */
  sectionUpdatedAt: Record<string, number>
  viewToken: string
  adminToken: string
  viewTokenRequired: boolean
  authError: string

  connect: () => void
  disconnect: () => void
  setViewToken: (token: string) => Promise<boolean>
  setAdminToken: (token: string) => void
  sendCommand: (command: CommandName, confirm?: string) => Promise<{ ok: boolean; message: string }>
  acknowledgeAlert: (id: number) => Promise<boolean>
}

let socket: WebSocket | null = null
let retryTimer: ReturnType<typeof setTimeout> | null = null
let retryDelay = 1000

const REMEMBER_KEY = 'alphaSpyRemember'

/**
 * Whether tokens survive closing the tab.
 *
 * Session storage was the original behaviour and it is wrong for the way this
 * is actually used: a phone, on a private network, opened and closed dozens of
 * times a day. It meant re-typing a 43-character token every time. The choice
 * is now the operator's, and it persists so the answer is only given once.
 */
export function readRemember(): boolean {
  try {
    // Absent means "not answered yet", which defaults to remembering — the
    // same default the unlock screen shows. Only an explicit '0' opts out.
    return localStorage.getItem(REMEMBER_KEY) !== '0'
  } catch {
    return true
  }
}

function store(remember: boolean): Storage | null {
  try {
    return remember ? localStorage : sessionStorage
  } catch {
    return null
  }
}

function readToken(key: string): string {
  // Checked in both places: flipping "remember" off must not strand a token
  // that is still sitting in the other store from a previous visit.
  try {
    return localStorage.getItem(key) ?? sessionStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writeToken(key: string, value: string, remember = readRemember()): void {
  try {
    // Always clear both, so a token never lingers in the store that is no
    // longer in use after the setting changes.
    localStorage.removeItem(key)
    sessionStorage.removeItem(key)
    if (value) store(remember)?.setItem(key, value)
  } catch {
    /* storage unavailable in private modes; tokens simply do not persist */
  }
}

export function setRemember(remember: boolean): void {
  try {
    localStorage.setItem(REMEMBER_KEY, remember ? '1' : '0')
  } catch {
    return
  }
  // Move any token already held into the newly chosen store.
  for (const key of [VIEW_TOKEN_KEY, ADMIN_TOKEN_KEY]) {
    const existing = readToken(key)
    if (existing) writeToken(key, existing, remember)
  }
}

function viewHeaders(token: string): HeadersInit {
  return token ? { 'X-Dashboard-Token': token } : {}
}

/**
 * Merge a frame's sections into the flat state.
 *
 * Sections are merged at the top-level key, not deep-merged: the server always
 * emits a whole section when any part of it changes, so a deep merge would only
 * risk leaving a stale sub-key alive after the engine dropped it.
 */
function mergeSections(
  current: WorkstationState,
  sections: Record<string, Partial<WorkstationState>>,
): WorkstationState {
  const next: WorkstationState = { ...current }
  for (const body of Object.values(sections)) {
    Object.assign(next, body)
  }
  return next
}

export const useWorkstation = create<WorkstationStore>((set, get) => ({
  state: {},
  status: 'connecting',
  seq: 0,
  lastFrameAt: null,
  sectionUpdatedAt: {},
  viewToken: readToken(VIEW_TOKEN_KEY),
  adminToken: readToken(ADMIN_TOKEN_KEY),
  viewTokenRequired: false,
  authError: '',

  connect: () => {
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }

    const token = get().viewToken
    const query = token ? `?token=${encodeURIComponent(token)}` : ''
    const next = new WebSocket(wsUrl(`/ws/live${query}`))
    socket = next
    set({ status: get().state.timestamp ? 'reconnecting' : 'connecting' })

    next.onopen = () => {
      retryDelay = 1000
      set({ status: 'live' })
    }

    next.onmessage = (event) => {
      let frame: Frame
      try {
        frame = JSON.parse(event.data as string) as Frame
      } catch {
        return
      }
      const now = Date.now()
      if (frame.type === 'heartbeat') {
        set({ seq: frame.seq, lastFrameAt: now, status: 'live' })
        return
      }
      const arrived: Record<string, number> = { ...get().sectionUpdatedAt }
      for (const name of Object.keys(frame.sections)) arrived[name] = now
      set((store) => ({
        state:
          frame.type === 'snapshot'
            ? mergeSections({ timestamp: frame.timestamp }, frame.sections)
            : { ...mergeSections(store.state, frame.sections), timestamp: frame.timestamp },
        seq: frame.seq,
        lastFrameAt: now,
        sectionUpdatedAt: arrived,
        status: 'live',
      }))
    }

    next.onerror = () => next.close()

    next.onclose = (event) => {
      socket = null
      // 4401 is the server's explicit auth rejection. Retrying it in a loop
      // would just hammer the endpoint with a token that will never work.
      if (event.code === 4401) {
        set({ status: 'unauthorized', viewTokenRequired: true })
        return
      }
      set({ status: 'reconnecting' })
      retryTimer = setTimeout(() => get().connect(), retryDelay)
      retryDelay = Math.min(retryDelay * 2, 15000)
    }
  },

  disconnect: () => {
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }
  },

  setViewToken: async (token: string) => {
    // Validated against the REST endpoint before it is stored, so a bad token
    // fails with a message here instead of an opaque socket close later.
    try {
      const response = await fetch(apiUrl('/api/v1/dashboard/state'), { headers: viewHeaders(token) })
      if (!response.ok) {
        set({ authError: 'Token rejected' })
        return false
      }
      // A 2xx is not proof the dashboard answered. A CDN or proxy sitting in
      // front of a misconfigured VITE_API_ORIGIN will happily return its own
      // index.html with a 200, which would store a bogus token and then fail
      // at the socket with no explanation.
      const contentType = response.headers.get('content-type') ?? ''
      if (!contentType.includes('json')) {
        set({ authError: 'Not the dashboard API — check VITE_API_ORIGIN' })
        return false
      }
    } catch {
      set({ authError: 'Dashboard unreachable' })
      return false
    }
    writeToken(VIEW_TOKEN_KEY, token)
    set({ viewToken: token, authError: '', viewTokenRequired: false })
    get().connect()
    return true
  },

  setAdminToken: (token: string) => {
    writeToken(ADMIN_TOKEN_KEY, token)
    set({ adminToken: token })
  },

  sendCommand: async (command, confirm = '') => {
    if (STATIC_DEMO) {
      return { ok: false, message: `${command} is disabled in the static preview` }
    }
    const admin = get().adminToken
    if (!admin) return { ok: false, message: 'Administrator token required' }
    try {
      const response = await fetch(apiUrl('/api/v1/control/command'), {
        method: 'POST',
        headers: { 'X-Dashboard-Token': admin, 'Content-Type': 'application/json' },
        body: JSON.stringify({ command, confirm, reason: 'workstation operator' }),
      })
      const body = (await response.json()) as { detail?: string; command_id?: number }
      if (!response.ok) {
        // A rejected admin token is cleared so the next attempt re-prompts
        // rather than silently failing against a stale credential.
        if (response.status === 403) {
          writeToken(ADMIN_TOKEN_KEY, '')
          set({ adminToken: '' })
        }
        return { ok: false, message: body.detail ?? 'Command rejected' }
      }
      return { ok: true, message: `${command} queued as #${body.command_id}` }
    } catch {
      return { ok: false, message: 'Dashboard unreachable' }
    }
  },

  acknowledgeAlert: async (id: number) => {
    const admin = get().adminToken
    if (!admin) return false
    try {
      const response = await fetch(apiUrl(`/api/v1/alerts/${id}/acknowledge`), {
        method: 'POST',
        headers: { 'X-Dashboard-Token': admin },
      })
      return response.ok
    } catch {
      return false
    }
  },
}))

/**
 * Static-demo mode.
 *
 * The GitHub Pages preview is a real build of this application with the network
 * layer replaced by a committed snapshot, so the published page can never drift
 * from the shipping UI the way a hand-maintained mock does. Guarded by a build
 * flag: the deployed workstation never takes this path.
 */
export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === '1'

async function loadStaticDemo(): Promise<void> {
  const { default: snapshot } = await import('@/demo/snapshot.json')
  const state = snapshot as unknown as WorkstationState
  const now = Date.now()
  useWorkstation.setState({
    state,
    status: 'live',
    seq: 1,
    lastFrameAt: now,
    sectionUpdatedAt: Object.fromEntries(Object.keys(state).map((key) => [key, now])),
  })
}

/** Probes whether the deployment requires a view token before connecting. */
export async function bootstrap(): Promise<void> {
  if (STATIC_DEMO) {
    await loadStaticDemo()
    return
  }
  const store = useWorkstation.getState()
  try {
    const response = await fetch(apiUrl('/api/v1/auth/mode'))
    const mode = (await response.json()) as { view_token_required?: boolean }
    if (mode.view_token_required && !store.viewToken) {
      useWorkstation.setState({ viewTokenRequired: true, status: 'unauthorized' })
      return
    }
  } catch {
    /* fall through and let the socket surface the failure */
  }
  store.connect()
}
