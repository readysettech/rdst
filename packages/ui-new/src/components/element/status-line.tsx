import { cn } from '@rs/tailwind-base'
import type { ReactNode } from 'react'
import type { WithChildren, WithClassName } from '../../helpers/types'
import { StatusRipple } from './status'
import { Text } from './text'

export type StatusLineTone = 'live' | 'warning' | 'negative' | 'off'

export interface StatusLineProps {
  /** The one sentence, built from data. */
  children: ReactNode
  /** The caption under it: what is watched, the last reading, what comes next. */
  caption?: ReactNode
  /** The dot in the caption: moving while readings arrive, still otherwise. */
  tone?: StatusLineTone
  className?: string
}

const stillDot: Record<Exclude<StatusLineTone, 'live'>, string> = {
  warning: 'bg-content-warning-plain',
  negative: 'bg-content-negative-plain',
  off: 'bg-content-layout-3',
}

/** The first thing on a page: a 24px sentence with its live caption. */
export const StatusLine = ({
  children,
  caption,
  tone = 'live',
  className,
}: StatusLineProps) => (
  <div className={className}>
    <Text
      as="p"
      level="headline-2"
      className="tracking-[-0.005em] text-content-layout-1"
    >
      {children}
    </Text>
    {caption && (
      <div className="mt-2.5 flex items-center gap-2 text-body-small text-content-layout-3">
        {tone === 'live' ? (
          <StatusRipple color="positive" className="-ml-1" />
        ) : (
          <span
            aria-hidden="true"
            className={cn('h-2 w-2 shrink-0 rounded-full', stillDot[tone])}
          />
        )}
        <span>{caption}</span>
      </div>
    )}
  </div>
)

/** The quieter half of the sentence, in the second tone and normal weight. */
const StatusLineQuiet = ({
  children,
  className,
}: WithChildren & WithClassName) => (
  <span className={cn('font-normal text-content-layout-2', className)}>
    {children}
  </span>
)

/** Classes for a link inside the sentence: underlined by a hairline. */
export const statusLinkClass =
  'border-b border-border-layout-2 hover:border-content-layout-2'

StatusLine.Quiet = StatusLineQuiet
