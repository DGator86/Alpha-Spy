import { cva, type VariantProps } from 'class-variance-authority'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { EMPTY, clamp, isNum } from '@/lib/format'
import type { Num } from '@/lib/types'

/* ------------------------------------------------------------------ Panel */

export function Panel({
  title,
  kicker,
  actions,
  children,
  className,
  bodyClassName,
  scroll,
}: {
  title?: ReactNode
  kicker?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  scroll?: boolean
}) {
  return (
    <section
      className={cn(
        'flex min-h-0 min-w-0 flex-col border border-line bg-surface',
        className,
      )}
    >
      {(title || kicker || actions) && (
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-3 py-2">
          <div className="min-w-0">
            {kicker && <div className="kicker truncate">{kicker}</div>}
            {title && (
              <h2 className="truncate text-[12px] font-semibold tracking-wide text-ink">{title}</h2>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div
        className={cn(
          'min-h-0 flex-1 p-3',
          scroll && 'overflow-auto',
          bodyClassName,
        )}
      >
        {children}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------- Chip */

const chipStyles = cva(
  'inline-flex items-center gap-1 whitespace-nowrap border px-1.5 py-0.5 font-mono text-[9px] font-semibold tracking-[0.1em] uppercase',
  {
    variants: {
      tone: {
        neutral: 'border-line-bright bg-surface-2 text-ink-2',
        pass: 'border-pass/40 bg-pass/10 text-pass',
        watch: 'border-watch/40 bg-watch/10 text-watch',
        fail: 'border-fail/40 bg-fail/10 text-fail',
        signal: 'border-signal/40 bg-signal/10 text-signal',
        muted: 'border-line bg-transparent text-ink-3',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

export type ChipTone = NonNullable<VariantProps<typeof chipStyles>['tone']>

export function Chip({
  tone,
  children,
  className,
  title,
}: VariantProps<typeof chipStyles> & { children: ReactNode; className?: string; title?: string }) {
  return (
    <span className={cn(chipStyles({ tone }), className)} title={title}>
      {children}
    </span>
  )
}

/** Maps the engine's health / status vocabulary onto the three state colours. */
export function toneForState(value: unknown): ChipTone {
  const text = String(value ?? '').toUpperCase()
  if (['GREEN', 'PASS', 'PASSED', 'OK', 'ONLINE', 'VERIFIED', 'VALID', 'CLEAR', 'ELIGIBLE', 'LIVE', 'CORRECT', 'INSIDE', 'HEALTHY', 'CURRENT', 'WRITING', 'ENABLED'].includes(text))
    return 'pass'
  if (['YELLOW', 'ORANGE', 'WATCH', 'WARNING', 'DEGRADED', 'PENDING', 'ARMED', 'MINOR_REVISION', 'REDUCED', 'SHADOW', 'SHADOW_ONLY', 'PAUSED', 'INCOMPLETE'].includes(text))
    return 'watch'
  if (['RED', 'FAIL', 'FAILED', 'CRITICAL', 'OFFLINE', 'INVALID', 'BLOCKED', 'WRONG', 'BREACHED', 'MATERIAL_REVISION', 'DISABLED', 'REJECTED'].includes(text))
    return 'fail'
  return 'neutral'
}

/* ----------------------------------------------------------------- Metric */

export function Metric({
  label,
  value,
  hint,
  tone,
  className,
  size = 'md',
}: {
  label: ReactNode
  value: ReactNode
  hint?: ReactNode
  tone?: 'pass' | 'fail' | 'watch' | 'signal' | 'neutral'
  className?: string
  size?: 'sm' | 'md' | 'lg'
}) {
  const valueTone =
    tone === 'pass'
      ? 'text-pass'
      : tone === 'fail'
        ? 'text-fail'
        : tone === 'watch'
          ? 'text-watch'
          : tone === 'signal'
            ? 'text-signal'
            : 'text-ink'
  const valueSize = size === 'lg' ? 'text-[22px]' : size === 'sm' ? 'text-[13px]' : 'text-[16px]'
  return (
    <div className={cn('min-w-0 border border-line bg-surface-2 px-2.5 py-2', className)}>
      <div className="kicker truncate">{label}</div>
      <div className={cn('tnum mt-0.5 truncate font-semibold leading-tight', valueSize, valueTone)}>
        {value}
      </div>
      {hint !== undefined && (
        <div className="mt-0.5 truncate font-mono text-[9px] text-ink-3">{hint}</div>
      )}
    </div>
  )
}

/** Colours a P&L-style number by sign; returns the neutral ink at exactly zero. */
export function pnlTone(value: Num): 'pass' | 'fail' | 'neutral' {
  if (!isNum(value)) return 'neutral'
  const n = Number(value)
  if (n > 0) return 'pass'
  if (n < 0) return 'fail'
  return 'neutral'
}

/* -------------------------------------------------------------------- Bar */

/**
 * Horizontal magnitude bar.
 *
 * `value` is normalised against [min, max]. Bipolar series (anything that can
 * be negative, like pressure or contribution) render from a centre baseline so
 * sign is readable as direction rather than only as colour.
 */
export function Bar({
  value,
  min = 0,
  max = 1,
  tone = 'signal',
  bipolar = false,
  className,
}: {
  value: Num
  min?: number
  max?: number
  tone?: 'signal' | 'pass' | 'watch' | 'fail' | 'auto'
  bipolar?: boolean
  className?: string
}) {
  if (!isNum(value)) {
    return <div className={cn('h-1.5 w-full bg-surface-3', className)} />
  }
  const n = Number(value)
  const resolved =
    tone === 'auto' ? (n >= 0 ? 'pass' : 'fail') : tone
  const fill =
    resolved === 'pass'
      ? 'bg-pass'
      : resolved === 'fail'
        ? 'bg-fail'
        : resolved === 'watch'
          ? 'bg-watch'
          : 'bg-signal'

  if (bipolar) {
    const bound = Math.max(Math.abs(min), Math.abs(max)) || 1
    const width = clamp(Math.abs(n) / bound, 0, 1) * 50
    return (
      <div className={cn('relative h-1.5 w-full bg-surface-3', className)}>
        <div className="absolute inset-y-0 left-1/2 w-px bg-line-bright" />
        <div
          className={cn('absolute inset-y-0', fill)}
          style={
            n >= 0
              ? { left: '50%', width: `${width}%` }
              : { right: '50%', width: `${width}%` }
          }
        />
      </div>
    )
  }

  const width = clamp((n - min) / (max - min || 1), 0, 1) * 100
  return (
    <div className={cn('h-1.5 w-full bg-surface-3', className)}>
      <div className={cn('h-full', fill)} style={{ width: `${width}%` }} />
    </div>
  )
}

/* --------------------------------------------------------------- Data row */

export function Row({
  label,
  value,
  tone,
  title,
  className,
}: {
  label: ReactNode
  value: ReactNode
  tone?: 'pass' | 'fail' | 'watch' | 'signal'
  title?: string
  className?: string
}) {
  const valueTone =
    tone === 'pass'
      ? 'text-pass'
      : tone === 'fail'
        ? 'text-fail'
        : tone === 'watch'
          ? 'text-watch'
          : tone === 'signal'
            ? 'text-signal'
            : 'text-ink'
  return (
    <div
      className={cn('flex items-baseline justify-between gap-3 border-b border-line/60 py-1 last:border-0', className)}
      title={title}
    >
      <span className="shrink-0 truncate font-mono text-[10px] tracking-wide text-ink-3 uppercase">
        {label}
      </span>
      {/* Values can be long (file paths, reason strings). Truncating the value
          rather than the label keeps the row identifiable when space runs out. */}
      <span
        className={cn('tnum min-w-0 truncate text-right text-[12px] font-medium', valueTone)}
        title={typeof value === 'string' || typeof value === 'number' ? String(value) : undefined}
      >
        {value}
      </span>
    </div>
  )
}

/* ------------------------------------------------------------ Empty state */

/**
 * Honest empty state.
 *
 * `field` names the exact state key the panel needs. When the engine has not
 * published something yet, saying which field is missing turns a blank panel
 * into a diagnosis instead of a bug report.
 */
export function Empty({ label, field }: { label: string; field?: string }) {
  return (
    <div className="flex h-full min-h-[80px] flex-col items-center justify-center gap-1 px-4 py-6 text-center">
      <div className="font-mono text-[10px] tracking-[0.14em] text-ink-3 uppercase">{label}</div>
      {field && (
        <div className="font-mono text-[9px] text-ink-4">
          waiting on <span className="text-signal-dim">{field}</span>
        </div>
      )}
    </div>
  )
}

/* ----------------------------------------------------------------- Button */

const buttonStyles = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap border font-mono text-[10px] font-semibold tracking-[0.1em] uppercase transition-colors disabled:cursor-not-allowed disabled:opacity-40',
  {
    variants: {
      tone: {
        default: 'border-line-bright bg-surface-2 text-ink-2 hover:border-signal/50 hover:text-signal',
        primary: 'border-signal/50 bg-signal/10 text-signal hover:bg-signal/20',
        warning: 'border-watch/50 bg-watch/10 text-watch hover:bg-watch/20',
        danger: 'border-fail/50 bg-fail/10 text-fail hover:bg-fail/20',
        success: 'border-pass/50 bg-pass/10 text-pass hover:bg-pass/20',
        ghost: 'border-transparent bg-transparent text-ink-3 hover:text-ink',
      },
      size: {
        sm: 'h-6 px-2',
        md: 'h-7 px-3',
      },
    },
    defaultVariants: { tone: 'default', size: 'sm' },
  },
)

export function Button({
  tone,
  size,
  className,
  ...rest
}: VariantProps<typeof buttonStyles> & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={cn(buttonStyles({ tone, size }), className)} {...rest} />
}

/* ------------------------------------------------------------------ Table */

export function TableShell({ children, className }: { children: ReactNode; className?: string }) {
  // Wide tables scroll inside their own container so the page body never does.
  return (
    <div className={cn('min-h-0 w-full overflow-auto', className)}>
      <table className="w-full border-collapse text-[11px]">{children}</table>
    </div>
  )
}

export function Th({
  children,
  align = 'left',
  className,
  ...rest
}: { children: ReactNode; align?: 'left' | 'right' | 'center'; className?: string } & React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        'sticky top-0 z-10 border-b border-line bg-surface px-2 py-1.5 font-mono text-[9px] font-semibold tracking-[0.1em] text-ink-3 uppercase',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        align === 'left' && 'text-left',
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  )
}

export function Td({
  children,
  align = 'left',
  className,
  ...rest
}: { children: ReactNode; align?: 'left' | 'right' | 'center'; className?: string } & React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cn(
        'border-b border-line/50 px-2 py-1 text-ink-2',
        align === 'right' && 'tnum text-right',
        align === 'center' && 'text-center',
        className,
      )}
      {...rest}
    >
      {children}
    </td>
  )
}

/* ------------------------------------------------------------------ Value */

/** A value that falls back to the shared placeholder when absent. */
export function Value({ children }: { children: ReactNode }) {
  return <>{children === null || children === undefined || children === '' ? EMPTY : children}</>
}
