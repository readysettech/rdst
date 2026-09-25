// Vertical progress timeline for long, multi-step work (Analyze database, a single
// query's analysis). Each step shows a state glyph, a label, a detail line and a
// right-aligned meta (duration or count); a step can carry nested rows.
//
// Usage:
//   <ProgressTimeline label="Analyze database steps" items={[
//     { id: 'select', label: 'Pick queries', state: 'done', meta: '0:02' },
//     { id: 'analyses', label: 'Deep analysis', state: 'running', meta: '9 of 15',
//       children: <ProgressRows>...</ProgressRows> },
//   ]} />
import { cn } from '@rs/tailwind-base'
import { m, useReducedMotion } from '@rs/ui-new/motion'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

export type ProgressState =
  | 'queued'
  | 'running'
  | 'done'
  | 'skipped'
  | 'failed'
  | 'provisional'

export const PROGRESS_STATE_LABEL: Record<ProgressState, string> = {
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  skipped: 'Skipped',
  failed: 'Failed',
  provisional: 'Plan only',
}

export interface ProgressTimelineItem {
  id: string
  label: ReactNode
  state: ProgressState
  /** One line under the label: what is happening or what came out. */
  detail?: ReactNode
  /** Right-aligned: a duration, a count, "waiting". */
  meta?: ReactNode
  /** Nested rows (for example one per query). */
  children?: ReactNode
}

const RING: Record<ProgressState, string> = {
  queued: 'border-border-layout-2 bg-surface-layout-1',
  running: 'border-border-layout-2 bg-surface-layout-1',
  done: 'border-border-positive-soft bg-surface-positive-soft',
  skipped: 'border-border-layout-2 bg-surface-layout-2',
  failed: 'border-border-negative-soft bg-surface-negative-soft',
  provisional: 'border-border-warning-soft bg-surface-warning-soft',
}

/** The state glyph alone: ring, spinning arc, drawn check, dash, cross. */
export function ProgressStateIcon({
  state,
  size = 'base',
  label,
}: {
  state: ProgressState
  size?: 'base' | 'small'
  /** Accessible name; defaults to the state label. */
  label?: string
}) {
  const reduced = useReducedMotion()
  const box = size === 'small' ? 'h-4 w-4' : 'h-5 w-5'
  const draw = reduced
    ? { initial: false as const }
    : {
        initial: { pathLength: 0 },
        animate: { pathLength: 1 },
        transition: { duration: 0.32, ease: [0.16, 1, 0.3, 1] as const },
      }
  return (
    <span
      role="img"
      aria-label={label ?? PROGRESS_STATE_LABEL[state]}
      className={cn(
        'relative inline-flex shrink-0 items-center justify-center rounded-full border',
        'transition-colors duration-normal ease-out',
        box,
        RING[state]
      )}
    >
      {state === 'running' && (
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="absolute -inset-px h-[calc(100%+2px)] w-[calc(100%+2px)] animate-spin text-content-rising-plain [animation-duration:1.1s] motion-reduce:animate-none"
        >
          <circle
            cx="10"
            cy="10"
            r="9"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeDasharray="16 42"
          />
        </svg>
      )}
      {state === 'running' && (
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 rounded-full bg-content-rising-plain"
        />
      )}
      {(state === 'done' || state === 'provisional') && (
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className={cn(
            'h-full w-full',
            state === 'done'
              ? 'text-content-positive-plain'
              : 'text-content-warning-plain'
          )}
        >
          <m.path
            d="M6 10.4l2.7 2.7L14.2 7.4"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            {...draw}
          />
        </svg>
      )}
      {state === 'skipped' && (
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="h-full w-full text-content-layout-3"
        >
          <path
            d="M6.5 10h7"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
          />
        </svg>
      )}
      {state === 'failed' && (
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="h-full w-full text-content-negative-plain"
        >
          <path
            d="M7 7l6 6M13 7l-6 6"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
          />
        </svg>
      )}
    </span>
  )
}

export function ProgressTimeline({
  items,
  label,
  className,
}: {
  items: ProgressTimelineItem[]
  /** Accessible name for the list. */
  label: string
  className?: string
}) {
  return (
    <ol aria-label={label} className={cn('flex flex-col', className)}>
      {items.map((item, index) => {
        const last = index === items.length - 1
        return (
          <li
            key={item.id}
            className="relative grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-3"
          >
            {!last && (
              <span
                aria-hidden="true"
                className={cn(
                  'absolute left-2.5 top-6 bottom-0 w-px -translate-x-1/2',
                  'transition-colors duration-slow ease-out',
                  item.state === 'done' || item.state === 'provisional'
                    ? 'bg-border-positive-soft'
                    : 'bg-content-layout-3/30'
                )}
              />
            )}
            <span className="pt-0.5">
              <ProgressStateIcon state={item.state} />
            </span>
            <div className={cn('min-w-0', last ? 'pb-0' : 'pb-5')}>
              <div className="flex min-h-6 items-baseline justify-between gap-3">
                <Text
                  as="span"
                  level="label-medium"
                  className={cn(
                    'min-w-0',
                    item.state === 'queued'
                      ? 'text-content-layout-3'
                      : 'text-content-layout-1'
                  )}
                >
                  {item.label}
                </Text>
                {item.meta != null && (
                  <Text
                    as="span"
                    level="caption"
                    className="shrink-0 text-content-layout-3 tabular-nums"
                  >
                    {item.meta}
                  </Text>
                )}
              </div>
              {item.detail != null && (
                <Text
                  as="p"
                  level="body-small"
                  className="mt-0.5 text-content-layout-3"
                >
                  {item.detail}
                </Text>
              )}
              {item.children && <div className="mt-3">{item.children}</div>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
