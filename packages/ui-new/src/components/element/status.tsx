'use client'

import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import { cubicBezier, m, type Transition } from 'motion/react'
import type { WithClassName } from '../../helpers/types'
import { HStack } from './stack'
import { Text } from './text'

export const statusRippleRecipe = tv({
  slots: {
    container: [
      'flex',
      'items-center',
      'justify-center',
      'relative',
      'w-4',
      'h-4',
    ],
    dot: ['w-2', 'h-2', 'rounded-full', 'z-1'],
    ripple: ['w-2', 'h-2', 'rounded-full', 'absolute'],
    text: ['text-label-small'],
  },

  variants: {
    color: {
      primary: {
        dot: ['bg-content-primary-soft'],
        ripple: ['bg-content-primary-soft'],
        text: ['text-content-primary-soft'],
      },
      rising: {
        dot: ['bg-content-rising-plain'],
        ripple: ['bg-content-rising-plain'],
        text: ['text-content-rising-plain'],
      },
      positive: {
        dot: ['bg-content-positive-plain'],
        ripple: ['bg-content-positive-plain'],
        text: ['text-content-positive-plain'],
      },
      negative: {
        dot: ['bg-content-negative-plain'],
        ripple: ['bg-content-negative-plain'],
        text: ['text-content-negative-plain'],
      },
      warning: {
        dot: ['bg-content-warning-plain'],
        ripple: ['bg-content-warning-plain'],
        text: ['text-content-warning-plain'],
      },
      informative: {
        dot: ['bg-content-info-plain'],
        ripple: ['bg-content-info-plain'],
        text: ['text-content-info-plain'],
      },
    },
  },
  defaultVariants: {
    color: 'positive',
  },
})

export type StatusRippleProps = {
  label?: string
} & VariantProps<typeof statusRippleRecipe> &
  WithClassName

const transition: Transition = {
  duration: 1.5,
  ease: [cubicBezier(0.16, 1, 0.3, 1)],
  times: [0, 0.4, 0.5, 1],
  repeat: Number.POSITIVE_INFINITY,
  repeatDelay: 0.1,
}

export const StatusRipple = (props: StatusRippleProps) => {
  const { container, dot, ripple, text } = statusRippleRecipe(props)
  const { label, className } = props

  return (
    <HStack className={cn('gap-1', className)}>
      <div className={container()}>
        <div className={dot()} />
        <m.div
          key="ripple1"
          className={ripple()}
          animate={{ scale: [0, 1, 1.75, 2], opacity: [1, 0.5, 0.25, 0] }}
          transition={transition}
        />
        <m.div
          key="ripple2"
          className={ripple()}
          animate={{ scale: [0, 1, 1.25, 1.5], opacity: [1, 0.5, 0.25, 0] }}
          transition={transition}
        />
      </div>
      {label && (
        <Text level="label-small" className={text()}>
          {label}
        </Text>
      )}
    </HStack>
  )
}
