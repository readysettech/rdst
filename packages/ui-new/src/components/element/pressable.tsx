'use client'

import { cn } from '@rs/tailwind-base'
import {
  type ButtonHTMLAttributes,
  forwardRef,
  memo,
  type ReactNode,
} from 'react'

export interface PressableProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  children?: ReactNode
  className?: string
}

/**
 * Style-neutral button semantics for bespoke interactive surfaces.
 *
 * Prefer Button, IconButton, or InteractiveRow whenever their anatomy fits.
 * Pressable exists for controls such as scrims, option cards, and OS chrome
 * whose visual structure is intentionally owned by the caller.
 */
const PressableBase = forwardRef<HTMLButtonElement, PressableProps>(
  ({ children, className, disabled, type = 'button', ...props }, ref) => (
    <button
      {...props}
      ref={ref}
      type={type}
      disabled={disabled}
      className={cn(
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-primary-soft focus-visible:ring-offset-2 focus-visible:ring-offset-surface-layout-1',
        disabled && 'cursor-not-allowed opacity-50',
        className
      )}
    >
      {children}
    </button>
  )
)

PressableBase.displayName = 'Pressable'

export const Pressable = memo(PressableBase)
