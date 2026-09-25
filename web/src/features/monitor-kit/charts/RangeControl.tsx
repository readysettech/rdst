// RangeControl - the honest range picker (brief 4.3). Looks and behaves like
// SegmentedControl in radio mode, plus per-segment disabled state with a tooltip
// reason ('Available after 7 days of history').
//
// Usage:
//   <RangeControl value={range} onChange={setRange} history={health.history} />
import { cn } from '@rs/tailwind-base'
import { Pressable } from '@rs/ui-new/pressable'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { type KeyboardEvent, useRef } from 'react'
import type { HistorySpan } from '@/lib/monitor/types'
import { availableRanges, type LiveRange } from './rangeModel'

export interface RangeControlProps<R extends LiveRange = LiveRange> {
  value: R
  onChange: (range: R) => void
  history: HistorySpan | null | undefined
  /** The offered ranges, in order (the first is always enabled). */
  ranges?: readonly R[]
  size?: 'base' | 'small'
  className?: string
  'aria-label'?: string
}

export function RangeControl<R extends LiveRange = LiveRange>({
  value,
  onChange,
  history,
  ranges,
  size = 'small',
  className,
  'aria-label': ariaLabel = 'Time range',
}: RangeControlProps<R>) {
  const options = availableRanges(history, ranges)
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const enabled = options.filter((option) => option.enabled)

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0
    if (!step) return
    event.preventDefault()
    const index = enabled.findIndex((option) => option.range === value)
    const next = enabled[(index + step + enabled.length) % enabled.length]
    if (!next) return
    onChange(next.range as R)
    refs.current[options.indexOf(next)]?.focus()
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div
        role="radiogroup"
        aria-label={ariaLabel}
        onKeyDown={onKeyDown}
        className={cn(
          'flex w-fit max-w-full gap-1 overflow-x-auto rounded-2xl border border-border-layout-1 bg-surface-layout-2/60 p-1',
          className
        )}
      >
        {options.map((option, index) => {
          const selected = option.range === value
          const segment = (
            <Pressable
              key={option.range}
              ref={(node) => {
                refs.current[index] = node
              }}
              role="radio"
              aria-checked={selected}
              aria-disabled={!option.enabled || undefined}
              tabIndex={selected ? 0 : -1}
              onClick={() => option.enabled && onChange(option.range as R)}
              className={cn(
                'flex shrink-0 items-center whitespace-nowrap rounded-xl text-button-small transition-colors duration-fast ease-base',
                size === 'small' ? 'h-7 px-3' : 'h-8 px-4',
                selected
                  ? 'bg-surface-primary-soft text-content-primary-soft'
                  : option.enabled
                    ? 'cursor-pointer text-content-layout-3 hover:bg-surface-layout-2 hover:text-content-layout-1'
                    : 'cursor-not-allowed text-content-layout-disabled'
              )}
            >
              {option.label}
            </Pressable>
          )
          if (option.enabled) return segment
          return (
            <Tooltip key={option.range}>
              <TooltipTrigger asChild>{segment}</TooltipTrigger>
              <TooltipContent
                label={option.reason ?? 'Not enough history yet'}
              />
            </Tooltip>
          )
        })}
      </div>
    </TooltipProvider>
  )
}
