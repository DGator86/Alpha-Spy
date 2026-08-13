import type { Num, Str } from './types'

/** The single em-dash placeholder used everywhere a value is genuinely absent. */
export const EMPTY = '—'

export function isNum(value: unknown): value is number {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
}

export function num(value: Num, digits = 2): string {
  return isNum(value) ? Number(value).toFixed(digits) : EMPTY
}

export function int(value: Num): string {
  return isNum(value) ? Math.round(Number(value)).toLocaleString() : EMPTY
}

/** Money with a true minus sign rather than a hyphen, so columns align. */
export function money(value: Num, digits = 2): string {
  if (!isNum(value)) return EMPTY
  const n = Number(value)
  const body = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return `${n < 0 ? '−' : ''}$${body}`
}

export function signedMoney(value: Num, digits = 2): string {
  if (!isNum(value)) return EMPTY
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${money(n, digits)}`
}

/** Fractions in, percent out. Pass `alreadyPercent` for values the engine already scaled. */
export function pct(value: Num, digits = 1, alreadyPercent = false): string {
  if (!isNum(value)) return EMPTY
  const n = Number(value) * (alreadyPercent ? 1 : 100)
  return `${n.toFixed(digits)}%`
}

export function signedPct(value: Num, digits = 2, alreadyPercent = false): string {
  if (!isNum(value)) return EMPTY
  const n = Number(value) * (alreadyPercent ? 1 : 100)
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

export function signed(value: Num, digits = 2): string {
  if (!isNum(value)) return EMPTY
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}`
}

export function bps(value: Num, digits = 1): string {
  return isNum(value) ? `${(Number(value) * 10000).toFixed(digits)}bp` : EMPTY
}

export function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value))
}

export function clock(value: Str): string {
  if (!value) return EMPTY
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

export function hhmm(value: Str): string {
  if (!value) return EMPTY
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

export function stamp(value: Str): string {
  if (!value) return EMPTY
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return `${parsed.toLocaleDateString([], { month: '2-digit', day: '2-digit' })} ${hhmm(value)}`
}

/** Relative age, for staleness readouts where the absolute time is noise. */
export function age(value: Str, now = Date.now()): string {
  if (!value) return EMPTY
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return EMPTY
  const seconds = Math.max(0, (now - parsed.getTime()) / 1000)
  if (seconds < 90) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  if (seconds < 5400) return `${(seconds / 60).toFixed(0)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

export function ms(value: Num): string {
  return isNum(value) ? `${Math.round(Number(value))}ms` : EMPTY
}

/** SCREAMING_SNAKE identifiers into something readable, without losing meaning. */
export function humanize(value: Str): string {
  if (!value) return EMPTY
  return String(value).replace(/[_-]+/g, ' ').trim().toUpperCase()
}

export function titleize(value: Str): string {
  if (!value) return EMPTY
  return String(value)
    .replace(/[_-]+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Renders a threshold that may be a scalar, a range pair, or a structured object. */
export function threshold(value: unknown): string {
  if (value === null || value === undefined) return EMPTY
  if (Array.isArray(value)) return value.map((v) => threshold(v)).join(' – ')
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k.replace(/_/g, ' ')} ${threshold(v)}`)
      .join(', ')
  }
  return String(value)
}

export function directionArrow(value: Num): string {
  if (!isNum(value)) return '·'
  const n = Number(value)
  if (n > 0) return '▲'
  if (n < 0) return '▼'
  return '→'
}

/** Confidence bucket used by the horizon ribbon; thresholds are on |p(up) − 0.5|. */
export function convictionBand(probabilityUp: Num): 'HIGH' | 'MED' | 'LOW' | null {
  if (!isNum(probabilityUp)) return null
  const edge = Math.abs(Number(probabilityUp) - 0.5)
  if (edge >= 0.12) return 'HIGH'
  if (edge >= 0.05) return 'MED'
  return 'LOW'
}
