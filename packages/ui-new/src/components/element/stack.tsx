import { tv } from '@rs/tailwind-base'
import type { ComponentProps } from 'react'

export const stackStyles = tv({
  base: ['flex', 'gap-2'],
  variants: {
    variant: {
      stack: [],
      hstack: ['flex-row items-center'],
      vstack: ['flex-col items-center'],
    },
  },
  defaultVariants: {
    variant: 'stack',
  },
})

export const centerStyles = tv({
  base: ['items-center', 'justify-center'],
  variants: {
    display: {
      default: ['flex'],
      inline: ['inline-flex'],
    },
  },
  defaultVariants: {
    display: 'default',
  },
})

export const HStack = ({ className, ...props }: ComponentProps<'div'>) => (
  <div
    className={stackStyles({ variant: 'hstack', class: className })}
    {...props}
  />
)

export const VStack = ({ className, ...props }: ComponentProps<'div'>) => (
  <div
    className={stackStyles({ variant: 'vstack', class: className })}
    {...props}
  />
)

export const Stack = ({ className, ...props }: ComponentProps<'div'>) => (
  <div
    className={stackStyles({ variant: 'stack', class: className })}
    {...props}
  />
)

export const Center = ({ className, ...props }: ComponentProps<'div'>) => (
  <div className={centerStyles({ class: className })} {...props} />
)
