import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Standard screen frame.
 *
 * `grid` rather than a flow layout so panels can claim `minmax(0, 1fr)` and
 * scroll internally; the page body itself never scrolls horizontally.
 */
export function Screen({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('grid min-h-0 flex-1 content-start gap-2 overflow-auto p-2', className)}>
      {children}
    </div>
  )
}
