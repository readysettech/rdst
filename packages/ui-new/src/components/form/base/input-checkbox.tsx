'use client'

import * as CheckboxPrimitive from '@radix-ui/react-checkbox'
import { cn, tv } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type ComponentRef,
  forwardRef,
  type ReactNode,
} from 'react'
import {
  controlTransition,
  focusRing,
  focusRingClass,
} from '../../../helpers/focus'
import { Icon } from '../../svg/icon'

const checkboxStyles = tv({
  slots: {
    root: [
      'peer',
      'flex',
      'h-6',
      'w-6',
      'items-center',
      'justify-center',
      'shrink-0',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-1',
      'text-content-layout-1',
      controlTransition,
      'duration-fast',
      'ease-base',
      ...focusRing,
      'disabled:cursor-not-allowed',
      'disabled:opacity-50',
      'data-[state=checked]:bg-surface-primary-solid',
      'data-[state=checked]:text-content-primary-solid',
    ],
    indicator: ['flex', 'items-center', 'justify-center', 'text-current'],
  },
})

const BaseInputCheckbox = forwardRef<
  ComponentRef<typeof CheckboxPrimitive.Root>,
  ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, ...props }, ref) => {
  const styles = checkboxStyles()

  return (
    <CheckboxPrimitive.Root
      ref={ref}
      className={styles.root({ class: className })}
      {...props}
    >
      <CheckboxPrimitive.Indicator className={styles.indicator()}>
        <Icon name="tick" label="" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
})

BaseInputCheckbox.displayName = 'BaseInputCheckbox'

// The square's fill follows the row's own data-state, so a row can draw the box
// as decoration instead of nesting a second, unnamed checkbox inside itself.
const rowBoxStyles = [
  'flex h-6 w-6 shrink-0 items-center justify-center rounded-lg',
  'border-(length:--border-base) border-border-layout-1',
  'bg-surface-layout-1 text-content-layout-1',
  'group-data-[state=checked]:bg-surface-primary-solid',
  'group-data-[state=checked]:text-content-primary-solid',
  'group-data-[state=indeterminate]:bg-surface-primary-solid',
  'group-data-[state=indeterminate]:text-content-primary-solid',
].join(' ')

/**
 * A whole row that *is* the checkbox: the square is drawn inside it as
 * decoration and the caller owns everything else in the row. Use it wherever a
 * dense row toggles a selection — a clickable wrapper around a separate
 * checkbox gives assistive technology two controls where the user sees one.
 */
const BaseInputCheckboxRow = forwardRef<
  ComponentRef<typeof CheckboxPrimitive.Root>,
  ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root> & {
    children?: ReactNode
  }
>(({ className, children, ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    className={cn(
      'group flex w-full cursor-pointer items-center gap-3 text-left',
      'transition-[background-color,color] duration-fast ease-base',
      focusRingClass,
      'focus-visible:ring-offset-0',
      'aria-disabled:cursor-not-allowed disabled:cursor-not-allowed disabled:opacity-50',
      className
    )}
    {...props}
  >
    <span className={rowBoxStyles}>
      <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
        <Icon name="tick" label="" />
      </CheckboxPrimitive.Indicator>
    </span>
    {children}
  </CheckboxPrimitive.Root>
))

BaseInputCheckboxRow.displayName = 'BaseInputCheckboxRow'

export { BaseInputCheckbox, BaseInputCheckboxRow, checkboxStyles }
