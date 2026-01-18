'use client'

import * as CheckboxPrimitive from '@radix-ui/react-checkbox'
import { tv } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type ComponentRef,
  forwardRef,
} from 'react'
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
      'transition',
      'duration-fast',
      'ease-base',
      'focus-visible:outline-none',
      'focus-visible:ring-2',
      'focus-visible:ring-border-primary-soft',
      'focus-visible:ring-offset-2',
      'focus-visible:ring-offset-surface-layout-1',
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
        <Icon name="tick" label="Selected" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
})

BaseInputCheckbox.displayName = 'BaseInputCheckbox'

export { BaseInputCheckbox, checkboxStyles }
