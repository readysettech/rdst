import { tv, type VariantProps } from '@rs/tailwind-base'
import { m } from 'motion/react'
import { memo } from 'react'
import { getTransition } from '../../motion/transition'

const rootStyle = tv({
  base: ['relative'],
  variants: {
    size: {
      base: ['h-4', 'w-4'],
      large: ['h-6', 'w-6'],
    },
  },
  defaultVariants: {
    size: 'base',
  },
})

const spinnerStyle = tv({
  base: [
    'absolute',
    'block',
    'box-border',
    'rounded-full',
    'border',
    'border-transparent',
  ],
  variants: {
    size: {
      base: ['h-4', 'w-4'],
      large: ['h-6', 'w-6'],
    },
    color: {
      layout: ['border-t-content-layout-1'],
      'primary-solid': ['border-t-content-primary-solid'],
      'primary-soft': ['border-t-content-primary-soft'],
      'rising-solid': ['border-t-content-rising-solid'],
      'rising-soft': ['border-t-content-rising-soft'],
      'positive-solid': ['border-t-content-positive-solid'],
      'positive-soft': ['border-t-content-positive-soft'],
      'negative-solid': ['border-t-content-negative-solid'],
      'negative-soft': ['border-t-content-negative-soft'],
      'warning-solid': ['border-t-content-warning-solid'],
      'warning-soft': ['border-t-content-warning-soft'],
      'info-solid': ['border-t-content-info-solid'],
      'info-soft': ['border-t-content-info-soft'],
    },
  },
  defaultVariants: {
    size: 'base',
    color: 'layout',
  },
})

type SpinnerProps = VariantProps<typeof spinnerStyle>

export const Spinner = memo(({ color, size }: SpinnerProps) => {
  const transition = getTransition('linear')

  return (
    <div className={rootStyle({ size })}>
      <m.span
        id="loader"
        className={spinnerStyle({ size, color })}
        transition={transition}
        animate={{
          rotate: 360,
        }}
      />
    </div>
  )
})
