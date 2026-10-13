import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import type { ReactNode } from 'react'
import { Pressable } from './pressable'
import { Text } from './text'

const metricStyles = tv({
  slots: {
    root: [
      'min-w-0 border-r border-border-layout-2 pr-5 mr-5 text-left',
      'last:mr-0 last:border-r-0',
    ],
    number: ['tabular-nums text-content-layout-1'],
  },
  variants: {
    size: {
      small: { root: 'py-2', number: 'text-stat-small' },
      base: { root: 'py-4', number: 'text-stat-large' },
      large: { root: 'py-4', number: 'text-display-small' },
      hero: { root: 'py-4', number: 'text-stat-hero' },
    },
    tone: {
      neutral: {},
      positive: { number: 'text-content-positive-soft' },
      warning: { number: 'text-content-warning-soft' },
      negative: { number: 'text-content-negative-soft' },
    },
    selected: {
      true: { root: '-mb-px border-b-2 border-b-content-layout-1' },
      false: { root: '-mb-px border-b-2 border-b-transparent' },
    },
  },
  defaultVariants: { size: 'base', tone: 'neutral', selected: false },
})

type MetricVariants = VariantProps<typeof metricStyles>

export interface MetricProps extends Omit<MetricVariants, 'selected'> {
  label: string
  /** The number itself; a link when the number leads somewhere. */
  value: ReactNode
  unit?: string
  /** One line under the number: a peak, a comparison word, a share. */
  detail?: string
  /** Why the number is out of range, in the tone's color. */
  reason?: string
  /** The one metric the chart below is showing. */
  selected?: boolean
  /** Makes the metric a button that picks it for the chart. */
  onSelect?: () => void
  className?: string
}

/** A number on the page: label, numeral with its unit, one line of context. */
export const Metric = ({
  label,
  value,
  unit,
  detail,
  reason,
  tone,
  size,
  selected = false,
  onSelect,
  className,
}: MetricProps) => {
  const styles = metricStyles({ size, tone, selected })
  const body = (
    <>
      <Text as="p" level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <p className={cn('mt-1', styles.number())}>
        {value}
        {unit && (
          <span className="ml-1 text-body-small font-normal tracking-normal text-content-layout-3">
            {unit}
          </span>
        )}
      </p>
      {detail && (
        <Text as="p" level="caption" className="mt-0.5 text-content-layout-3">
          {detail}
        </Text>
      )}
      {reason && (
        <Text
          as="p"
          level="caption"
          className={cn(
            'mt-0.5',
            tone === 'negative'
              ? 'text-content-negative-soft'
              : 'text-content-warning-soft'
          )}
        >
          {reason}
        </Text>
      )}
    </>
  )
  if (onSelect)
    return (
      <Pressable
        aria-pressed={selected}
        onClick={onSelect}
        className={styles.root({ class: cn('cursor-pointer', className) })}
      >
        {body}
      </Pressable>
    )
  return <div className={styles.root({ class: className })}>{body}</div>
}

export interface MetricRowProps {
  /** Hairlines above and below the row; a card that already frames the row drops them. */
  bordered?: boolean
  children: ReactNode
  className?: string
}

/** Metrics side by side, divided by hairlines rather than boxes. */
export const MetricRow = ({
  bordered = true,
  children,
  className,
}: MetricRowProps) => (
  <div
    className={cn(
      'grid grid-flow-col auto-cols-fr',
      bordered && 'border-y border-border-layout-2',
      className
    )}
  >
    {children}
  </div>
)
