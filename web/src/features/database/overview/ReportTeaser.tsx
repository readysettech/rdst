// The latest daily snapshot as one slim line beside the Live / Report tabs: its day and how
// many of its events are still open.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { useMemo } from 'react'
import { useReport } from '@/lib/monitor/hooks'
import { dayLine, dayShort } from '../reports/snapshotModel'

export function ReportTeaser({
  target,
  onOpen,
  className,
}: {
  target: string
  onOpen: () => void
  className?: string
}) {
  const latest = useReport(target, 'daily')
  const model = useMemo(() => {
    const snapshot =
      latest.data?.state === 'done' ? latest.data.report_json?.snapshot : null
    if (!snapshot) return null
    const events = snapshot.events.filter((event) => event.kind !== 'index')
    const line = dayLine({
      events: events.length,
      open_events: events.filter((event) => event.state === 'open').length,
      look_at: snapshot.look_at.length,
      counts: { fix_first: 0, one_fix: 0, health_warn: 0, health_ok: 0 },
    } as Parameters<typeof dayLine>[0])
    const open = events.filter((event) => event.state === 'open').length
    return {
      day: dayShort(snapshot.period.day),
      line: line || 'A quiet day',
      open,
    }
  }, [latest.data])

  if (!model || !latest.data) return null
  return (
    <Pressable
      onClick={onOpen}
      aria-label={`Latest daily snapshot, ${model.day}: ${model.line}. Open the day`}
      className={cn(
        'group flex h-8 min-w-0 items-center gap-2 rounded-pill border border-border-layout-1 bg-surface-layout-1 px-3 text-left transition-colors duration-fast hover:border-border-layout-2 hover:bg-surface-layout-2 focus-visible:shadow-focus focus-visible:outline-none',
        className
      )}
      data-testid="report-teaser"
    >
      <span className="inline-flex shrink-0 text-content-layout-3">
        <Icon
          name="document-validation"
          label=""
          aria-hidden="true"
          className="h-3.5 w-3.5"
        />
      </span>
      <span className="min-w-0 truncate text-label-small text-content-layout-1">
        {model.day} report
      </span>
      {model.open ? (
        <span className="shrink-0 text-label-small text-content-warning-soft">
          {model.open} open
        </span>
      ) : null}
      <span className="inline-flex shrink-0 items-center text-content-primary-soft">
        <Icon
          name="arrow-right"
          label=""
          aria-hidden="true"
          className="h-3.5 w-3.5 transition-transform duration-fast group-hover:translate-x-0.5"
        />
      </span>
    </Pressable>
  )
}
