import { cn } from '@rs/tailwind-base'
import type { ReactNode } from 'react'
import { Scrollable } from '../overlay/scrollable'

export interface TwoPaneProps {
  /** The main panel: the page, scrolling on its own. */
  primary: ReactNode
  /** The second panel: a side pane, scrolling on its own. */
  secondary: ReactNode
  /** Whether the second panel is on screen; it slides open and closed. */
  open?: boolean
  /** Width of the second panel in pixels when open. */
  secondaryWidth?: number
  /**
   * Fixes the first panel's width instead, so the second panel takes the rest
   * (a queue beside its detail).
   */
  primaryWidth?: number
  /**
   * Whether the first panel scrolls as a whole. Pass false when `primary`
   * keeps its own header fixed over a body that scrolls (a queue).
   */
  primaryScrolls?: boolean
  className?: string
}

const panelClass =
  'flex min-h-0 min-w-0 flex-col rounded-canvas border border-border-layout-soft bg-surface-layout-1'

/**
 * Two separate rounded panels filling the shell: no gap while the second is
 * closed, 12px when it opens. Each panel scrolls itself; the page never does.
 */
export const TwoPane = ({
  primary,
  secondary,
  open = true,
  secondaryWidth = 560,
  primaryWidth,
  primaryScrolls = true,
  className,
}: TwoPaneProps) => (
  <div
    data-two-pane
    data-open={open || undefined}
    className={cn('flex h-full min-h-0 min-w-0 items-stretch', className)}
  >
    <div
      className={cn(panelClass, 'overflow-hidden', !primaryWidth && 'flex-1')}
      style={primaryWidth ? { flex: `0 0 ${primaryWidth}px` } : undefined}
    >
      {primaryScrolls ? (
        <Scrollable rootClassName="min-h-0 flex-1">{primary}</Scrollable>
      ) : (
        primary
      )}
    </div>
    <div
      aria-hidden={!open}
      inert={!open}
      className={cn(
        panelClass,
        'overflow-hidden transition-[flex-basis,margin-left,opacity,border-width] duration-slow ease-base motion-reduce:transition-none',
        primaryWidth && open ? 'ml-3 flex-1' : 'flex-none',
        !primaryWidth && (open ? 'ml-3 opacity-100' : 'ml-0 border-0 opacity-0')
      )}
      style={
        primaryWidth
          ? open
            ? undefined
            : { flex: '0 0 0px', marginLeft: 0, opacity: 0, borderWidth: 0 }
          : { flexBasis: open ? secondaryWidth : 0 }
      }
    >
      {open ? secondary : null}
    </div>
  </div>
)
