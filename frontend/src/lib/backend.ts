/**
 * Where the workstation talks to the dashboard.
 *
 * Served by FastAPI on the trading host, the UI and the API share an origin and
 * every call is a plain relative path. Hosted anywhere else — Vercel, a laptop
 * dev server, a static preview — the origin has to be stated, because a
 * relative `/api/v1/...` would resolve against the CDN rather than the engine.
 *
 * `VITE_API_ORIGIN` is baked in at build time. Leaving it unset preserves the
 * original same-origin behaviour exactly, so the VPS deployment is unaffected.
 */
const RAW_ORIGIN = (import.meta.env.VITE_API_ORIGIN ?? '').trim()

/** Normalised backend origin, or '' when the API shares this page's origin. */
export const API_ORIGIN = RAW_ORIGIN.replace(/\/+$/, '')

export const IS_REMOTE_BACKEND = API_ORIGIN !== ''

/** Absolute URL for a REST path. */
export function apiUrl(path: string): string {
  return API_ORIGIN ? `${API_ORIGIN}${path}` : path
}

/**
 * Absolute websocket URL for a socket path.
 *
 * The scheme is derived from the backend origin rather than the page's, since a
 * page served over https from a CDN may still be pointed at a plain-http
 * dashboard on a private network. Browsers block that mixed content, which is a
 * configuration error worth surfacing as a failed connection rather than
 * silently rewriting to a scheme the backend is not listening on.
 */
export function wsUrl(path: string): string {
  if (!API_ORIGIN) {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${scheme}://${location.host}${path}`
  }
  return `${API_ORIGIN.replace(/^http/, 'ws')}${path}`
}
