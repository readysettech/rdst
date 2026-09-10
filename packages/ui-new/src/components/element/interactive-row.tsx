'use client'

import { cn } from '@rs/tailwind-base'
import {
  type ButtonHTMLAttributes,
  forwardRef,
  memo,
  type ReactNode,
} from 'react'
import { controlTransition, focusRingClass } from '../../helpers/focus'

interface InteractiveRowProps
  extends Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    'aria-label' | 'children' | 'className'
  > {
  /** Required action-oriented accessible name for the full row. */
  label: string
  /** Fully custom visible row content. */
  children: ReactNode
  /** Applies the shared selected-row treatment without changing semantics. */
  active?: boolean
  className?: string
}

/**
 * A full-width button for dense, custom row content such as history entries or
 * recent-work items. It owns the native interaction, focus, press, and disabled
 * behavior while callers own the visible row layout.
 */
const InteractiveRow = forwardRef<HTMLButtonElement, InteractiveRowProps>(
  ({ label, children, active, disabled, className, ...restProps }, ref) => (
    <button
      type="button"
      {...restProps}
      ref={ref}
      aria-label={label}
      disabled={disabled}
      data-active={active || undefined}
      className={cn(
        'group relative w-full cursor-pointer text-left',
        controlTransition,
        'duration-fast ease-base',
        focusRingClass,
        'active:scale-[0.995] active:origin-center',
        active && 'bg-surface-primary-soft/10',
        disabled && 'pointer-events-none cursor-not-allowed opacity-50',
        className
      )}
    >
      {children}
    </button>
  )
)

InteractiveRow.displayName = 'InteractiveRow'

const MemoizedInteractiveRow = memo(InteractiveRow)

export { MemoizedInteractiveRow as InteractiveRow }
export type { InteractiveRowProps }
