import * as Switch from '@radix-ui/react-switch'
import { tv } from '@rs/tailwind-base'
import { type ComponentRef, forwardRef } from 'react'
import type { WithClassName } from '../../../helpers'

const switchStyles = tv({
  slots: {
    root: [
      'inline-flex',
      'h-5',
      'w-10',
      'shrink-0',
      'cursor-pointer',
      'items-center',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-2',
      'transition',
      'duration-fast',
      'ease-base',
      'focus-visible:outline-none',
      'focus-visible:shadow-focus',

      'disabled:cursor-not-allowed',
      'disabled:opacity-50',
      'data-[state=checked]:bg-surface-primary-solid',
      'data-[state=unchecked]:bg-surface-layout-2',
    ],
    thumb: [
      'pointer-events-none',
      'flex',
      'h-6',
      'w-4',
      'items-center',
      'justify-center',
      'rounded-md',
      'bg-content-layout-3',
      'shadow-lg',
      'transition',
      'duration-fast',
      'ease-base',
      'data-[state=checked]:translate-x-6',
      'data-[state=checked]:shadow-2xl',
      'data-[state=checked]:bg-content-layout-3',
      'data-[state=unchecked]:translate-x-0',
    ],
  },
})

export type BaseInputSwitchProps = {
  id?: string
  'aria-label'?: string
  'aria-labelledby'?: string
  checked: boolean
  onCheckedChange?: (checked: boolean) => void
  disabled?: boolean
  required?: boolean
  name: string
} & WithClassName

const BaseInputSwitch = forwardRef<
  ComponentRef<typeof Switch.Root>,
  BaseInputSwitchProps
>(({ className, ...props }, ref) => {
  const styles = switchStyles()

  return (
    <Switch.Root
      className={styles.root({ class: className })}
      ref={ref}
      {...props}
    >
      <Switch.Thumb className={styles.thumb()} />
    </Switch.Root>
  )
})

BaseInputSwitch.displayName = 'BaseInputSwitch'

export { BaseInputSwitch, switchStyles }
