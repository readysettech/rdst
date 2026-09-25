// The custom time frame beside the range presets: from and to, date and time. Applying
// it sets the same zoom a drag across the chart does, so the chart, the list and the
// time-frame chip all follow.
import { cn } from '@rs/tailwind-base'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Label } from '@rs/ui-new/label'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { type FormEvent, useId, useMemo, useState } from 'react'
import { formatClock, formatDayDate } from '@/features/monitor-kit/format'
import type { HistorySpan } from '@/lib/monitor/types'
import { resolveCustomRange, toInputValue } from './customRange'

export interface CustomRangePickerProps {
  /** The window the chart shows now; the fields start there. */
  domain: [number, number]
  history: HistorySpan | null | undefined
  /** A custom time frame (or any zoom) is set. */
  active?: boolean
  onApply: (window: [number, number]) => void
}

export function CustomRangePicker({
  domain,
  history,
  active = false,
  onApply,
}: CustomRangePickerProps) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000))
  const first = history?.first_ts ?? null

  const openWith = (next: boolean) => {
    if (next) {
      const current = Math.floor(Date.now() / 1000)
      setNow(current)
      setFrom(toInputValue(Math.max(domain[0], first ?? domain[0])))
      setTo(toInputValue(Math.min(domain[1], current)))
    }
    setOpen(next)
  }

  const result = useMemo(
    () => resolveCustomRange(from, to, { first, now }),
    [from, to, first, now]
  )
  const bounds = first
    ? `From ${formatDayDate(first)}, ${formatClock(first)} to now, at least 5 minutes.`
    : 'At least 5 minutes.'

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!result.ok) return
    onApply(result.window)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={openWith}>
      <PopoverTrigger asChild>
        <Button
          variant="primary"
          modifier={active ? 'outline' : 'ghost'}
          size="small"
          label="Custom"
          aria-label="Custom time frame"
          icon="chevron-down"
          iconPosition="right"
          data-testid="custom-range"
          className={cn(active && 'border-border-primary-soft')}
        />
      </PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={16}
        className="w-[min(22rem,calc(100vw-2rem))] min-w-0 px-4 py-3"
        data-testid="custom-range-panel"
      >
        <form onSubmit={submit} className="flex w-full flex-col gap-3">
          <div className="space-y-0.5">
            <p className="text-label-large text-content-layout-1">
              Custom time frame
            </p>
            <p className="text-caption text-content-layout-3">{bounds}</p>
          </div>
          <div className="grid grid-cols-1 gap-2">
            <div className="space-y-1">
              <Label htmlFor={`${id}-from`}>From</Label>
              <BaseInputText
                id={`${id}-from`}
                name="custom-range-from"
                type="datetime-local"
                step={60}
                min={first ? toInputValue(first) : undefined}
                max={toInputValue(now)}
                value={from}
                onChange={(event) => setFrom(event.target.value)}
                data-testid="custom-range-from"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`${id}-to`}>To</Label>
              <BaseInputText
                id={`${id}-to`}
                name="custom-range-to"
                type="datetime-local"
                step={60}
                min={first ? toInputValue(first) : undefined}
                max={toInputValue(now)}
                value={to}
                onChange={(event) => setTo(event.target.value)}
                data-testid="custom-range-to"
              />
            </div>
          </div>
          <p
            role={result.ok ? undefined : 'alert'}
            aria-live="polite"
            className={cn(
              'min-h-4 text-caption',
              result.ok
                ? 'text-content-layout-3'
                : 'text-content-negative-plain'
            )}
            data-testid="custom-range-message"
          >
            {result.ok ? result.note : result.error}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              modifier="ghost"
              size="small"
              label="Cancel"
              onClick={() => setOpen(false)}
            />
            <Button
              type="submit"
              variant="primary"
              size="small"
              label="Show this time frame"
              disabled={!result.ok}
              data-testid="custom-range-apply"
            />
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}
