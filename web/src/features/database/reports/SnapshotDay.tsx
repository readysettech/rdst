// The middle of the daily snapshot: the day's chart with a metric switch (B3), the spikes
// with their details (B8), and what happened (B4, B5). Queries open in place everywhere;
// hovering a spike shades it on the chart.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { useState } from 'react'
import type { ChartHighlight } from '@/features/monitor-kit/charts'
import {
  formatClock,
  formatMsWithUnit,
  formatQps,
} from '@/features/monitor-kit/format'
import type { SnapshotEvent, SnapshotSpike } from '@/lib/monitor/types'
import { DayChart } from './DayChart'
import {
  CARD,
  CodeBlock,
  QueryName,
  QueryPanel,
  Section,
  type SnapshotContext,
  Swatch,
  useInlineQuery,
} from './snapshotKit'
import {
  availableMetrics,
  type ChartMetric,
  type ChartView,
  eventState,
  METRIC_SUBTITLE,
  METRIC_TITLE,
  metricBands,
  spikeSize,
  spikeWhen,
} from './snapshotModel'

const SEGMENTS: { value: ChartMetric; label: string }[] = [
  { value: 'calls', label: 'Queries/s' },
  { value: 'latency', label: 'Latency' },
  { value: 'time', label: 'Time spent' },
]

function bandSummary(values: (number | null)[]): number | null {
  const measured = values.filter((value): value is number => value != null)
  if (!measured.length) return null
  return measured.reduce((sum, value) => sum + value, 0) / measured.length
}

export function ChartSection({
  context,
  view,
  onView,
  highlight,
}: {
  context: SnapshotContext
  view: ChartView
  onView: (view: ChartView) => void
  highlight: ChartHighlight | null
}) {
  const { snapshot } = context
  const [hover, setHover] = useState<string | null>(null)
  const inline = useInlineQuery()
  const chart = snapshot.chart
  if (!chart) return null
  const domain: [number, number] = [snapshot.period.start, snapshot.period.end]
  const peak = snapshot.peak
  const metrics = availableMetrics(chart)
  const queryView = view === 'calls' || view === 'latency' || view === 'time'
  const legendMetric: ChartMetric = queryView ? view : 'calls'
  const averages = new Map(
    metricBands(chart, legendMetric).map((band) => [
      band.id,
      bandSummary(band.values),
    ])
  )
  const legendValue = (key: string, qps: number) => {
    if (legendMetric === 'calls') return `${formatQps(qps)}/s`
    const value = averages.get(key)
    if (value == null) return ''
    if (legendMetric === 'time') return `${Math.round(value)}%`
    const ms = formatMsWithUnit(value)
    return `${ms.value} ${ms.unit}`
  }
  const aside = [
    snapshot.period.observed_from &&
    snapshot.period.observed_from > snapshot.period.start + 3600
      ? `Recording started at ${formatClock(snapshot.period.observed_from)}`
      : null,
    view === 'calls' && peak.qps != null && peak.ts != null
      ? `Busiest at ${formatClock(peak.ts)}, ${formatQps(peak.qps)}/s`
      : METRIC_SUBTITLE[view],
  ]
    .filter(Boolean)
    .join('. ')
  return (
    <Section
      id="snapshot-chart"
      title={METRIC_TITLE[view]}
      aside={aside}
      action={
        metrics.length > 1 || !queryView ? (
          <div className="-mx-1 max-w-full overflow-x-auto px-1 [scrollbar-width:none]">
            <SegmentedControl<string>
              className="w-max [&_button]:whitespace-nowrap"
              mode="radio"
              size="small"
              aria-label="Chart metric"
              value={queryView ? view : ''}
              onValueChange={(value) => onView(value as ChartMetric)}
              segments={SEGMENTS.filter((segment) =>
                metrics.includes(segment.value)
              )}
            />
          </div>
        ) : null
      }
    >
      <div className={cn(CARD, 'space-y-3 p-4')}>
        <DayChart
          chart={chart}
          vitals={snapshot.vitals}
          target={context.target}
          domain={domain}
          view={view}
          highlight={highlight}
          hoverKey={hover}
          onHoverKey={setHover}
          onSelectKey={inline.toggle}
        />
        {queryView ? (
          <ul className="flex flex-wrap gap-x-1 gap-y-1 border-t border-border-layout-1 pt-3">
            {chart.bands.map((band) => {
              const color =
                band.kind === 'query'
                  ? context.colors.get(band.key)
                  : 'var(--mk-band-other)'
              const value = legendValue(band.key, band.qps)
              const content = (
                <>
                  <Swatch color={color} />
                  <span className="max-w-[16rem] truncate">
                    {band.display_name}
                  </span>
                  {value ? (
                    <span className="tabular-nums text-content-layout-3">
                      {value}
                    </span>
                  ) : null}
                </>
              )
              const dim = hover && hover !== band.key
              return (
                <li
                  key={band.key}
                  onPointerEnter={() => setHover(band.key)}
                  onPointerLeave={() => setHover(null)}
                >
                  {band.kind === 'query' ? (
                    <Pressable
                      onClick={() => inline.toggle(band.key)}
                      aria-expanded={inline.openKey === band.key}
                      className={cn(
                        'flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-label-small transition-colors duration-fast hover:bg-surface-layout-2',
                        dim ? 'text-content-layout-3' : 'text-content-layout-1',
                        inline.openKey === band.key && 'bg-surface-layout-2'
                      )}
                    >
                      {content}
                    </Pressable>
                  ) : (
                    <span
                      className={cn(
                        'flex items-center gap-1.5 px-1.5 py-0.5 text-label-small',
                        dim ? 'text-content-layout-3' : 'text-content-layout-1'
                      )}
                    >
                      {content}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="border-t border-border-layout-1 pt-3 text-body-small text-content-layout-3">
            Database-wide.{' '}
            <Pressable
              className="text-content-primary-soft hover:text-content-primary-plain"
              onClick={() => onView('calls')}
            >
              Back to queries per second
            </Pressable>
          </p>
        )}
        {inline.openKey ? (
          <QueryPanel
            queryKey={inline.openKey}
            context={context}
            onClose={inline.close}
          />
        ) : null}
      </div>
    </Section>
  )
}

// Spikes

function SpikeCard({
  spike,
  context,
  onHighlight,
}: {
  spike: SnapshotSpike
  context: SnapshotContext
  onHighlight: (highlight: ChartHighlight | null) => void
}) {
  const inline = useInlineQuery()
  const size = spikeSize(spike)
  const traffic = spike.metric === 'traffic'
  const label = `${traffic ? 'Traffic' : 'Latency'} spike, ${spikeWhen(spike).toLowerCase()}`
  return (
    <li
      className={cn(
        CARD,
        'group space-y-3 p-4 transition-[box-shadow,border-color] duration-fast hover:border-border-layout-2 hover:shadow-elevation-1'
      )}
      data-testid="spike"
      onPointerEnter={() =>
        onHighlight({ start: spike.start, end: spike.end, label })
      }
      onPointerLeave={() => onHighlight(null)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <p className="flex items-center gap-2 text-label-large text-content-layout-1">
            <span
              aria-hidden="true"
              className="flex h-6 w-6 items-center justify-center rounded-panel bg-surface-warning-soft text-content-warning-soft"
            >
              <Icon
                name={traffic ? 'arrow-up' : 'speedometer'}
                label=""
                size="small"
              />
            </span>
            {traffic ? 'Traffic spike' : 'Latency spike'}
          </p>
          <p className="text-body-small tabular-nums text-content-layout-2">
            {spikeWhen(spike)}
          </p>
        </div>
        {size.ratio ? (
          <span className="shrink-0 rounded-md bg-surface-warning-soft px-2 py-0.5 text-label-medium font-semibold tabular-nums text-content-warning-soft">
            {size.ratio}
          </span>
        ) : null}
      </div>
      <p className="flex flex-wrap items-baseline gap-x-2 text-body-medium">
        <span className="text-headline-5 font-semibold tabular-nums text-content-layout-1">
          {size.peak}
        </span>
        <span className="text-content-layout-3">
          at the peak, against a usual {size.usual}
          {traffic ? '' : ' a query'}
        </span>
      </p>
      {spike.queries.length ? (
        <div className="space-y-1.5">
          <p className="text-label-small text-content-layout-3">
            Queries that moved most
          </p>
          <ul className="space-y-1">
            {spike.queries.map((row) => (
              <li
                key={row.query_key}
                className="flex items-center justify-between gap-3 text-body-small"
              >
                <QueryName
                  queryKey={row.query_key}
                  context={context}
                  onToggle={inline.toggle}
                  active={inline.openKey === row.query_key}
                />
                <span className="shrink-0 whitespace-nowrap tabular-nums text-content-layout-2">
                  {size.format(row.before)} to{' '}
                  <span className="font-semibold text-content-layout-1">
                    {size.format(row.during)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-body-small text-content-layout-3">
          No single busy query stood out; it was spread across the workload.
        </p>
      )}
      {spike.events.length ? (
        <p className="text-body-small text-content-layout-2">
          <span className="text-content-layout-3">At the same time: </span>
          {spike.events
            .map((event) => `${event.what} at ${formatClock(event.ts)}`)
            .join(', ')}
        </p>
      ) : null}
      {inline.openKey ? (
        <QueryPanel
          queryKey={inline.openKey}
          context={context}
          onClose={inline.close}
        />
      ) : null}
    </li>
  )
}

export function Spikes({
  context,
  onHighlight,
}: {
  context: SnapshotContext
  onHighlight: (highlight: ChartHighlight | null) => void
}) {
  const spikes = context.snapshot.spikes ?? []
  if (!context.snapshot.chart) return null
  return (
    <Section
      id="snapshot-spikes"
      title="Spikes"
      aside={
        spikes.length
          ? "Against the day's usual level. Hover one to see it on the chart"
          : undefined
      }
    >
      {spikes.length ? (
        <ul className="grid gap-2 laptop:grid-cols-2">
          {spikes.map((spike) => (
            <SpikeCard
              key={spike.id}
              spike={spike}
              context={context}
              onHighlight={onHighlight}
            />
          ))}
        </ul>
      ) : (
        <p
          className={cn(
            CARD,
            'px-5 py-4 text-body-medium text-content-layout-2'
          )}
        >
          No spikes. Traffic and latency stayed near their usual level all day.
        </p>
      )}
    </Section>
  )
}

// What happened

const DOT_TONE: Record<string, string> = {
  negative: 'bg-content-negative-plain',
  warning: 'bg-content-warning-plain',
  positive: 'bg-content-positive-plain',
}

const STATE_TONE = {
  negative: 'text-content-negative-soft',
  positive: 'text-content-positive-soft',
  neutral: 'text-content-layout-3',
} as const

function EventRow({
  event,
  context,
}: {
  event: SnapshotEvent
  context: SnapshotContext
}) {
  const state = eventState(event, context.snapshot.period.end)
  const inline = useInlineQuery()
  const keys = event.query_keys
  return (
    <li
      className={cn(
        'px-4 py-2.5 transition-colors duration-fast hover:bg-surface-layout-2/40',
        inline.openKey && 'bg-surface-layout-2/30'
      )}
    >
      <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 gap-y-0.5 tablet:grid-cols-[5rem_minmax(0,1fr)_12.5rem]">
        <span className="pt-1 text-label-small tabular-nums text-content-layout-2">
          {formatClock(event.ts)}
        </span>
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="flex items-center gap-2 text-label-large text-content-layout-1">
              <span
                aria-hidden="true"
                className={cn(
                  'h-2 w-2 shrink-0',
                  event.kind === 'index'
                    ? 'rotate-45 rounded-sm'
                    : 'rounded-full',
                  DOT_TONE[event.tone ?? ''] ?? 'bg-content-layout-3'
                )}
              />
              {event.what}
            </span>
            {event.kind === 'index' ? (
              <span className="text-body-small text-content-layout-2">
                {event.title}
              </span>
            ) : null}
            {keys.slice(0, 3).map((key) => (
              <QueryName
                key={key}
                queryKey={key}
                context={context}
                onToggle={inline.toggle}
                active={inline.openKey === key}
              />
            ))}
            {keys.length > 3 ? (
              <span className="text-caption text-content-layout-3">
                and {keys.length - 3} more
              </span>
            ) : null}
          </div>
          {event.detail ? (
            <p className="pl-4 text-body-small tabular-nums text-content-layout-2">
              {event.detail}
            </p>
          ) : null}
          {event.definition ? <CodeBlock sql={event.definition} /> : null}
        </div>
        <span
          className={cn(
            'col-start-2 text-label-small tablet:col-start-3 tablet:pt-1 tablet:text-right',
            STATE_TONE[state.tone]
          )}
        >
          {state.label}
        </span>
      </div>
      {inline.openKey ? (
        <div className="tablet:pl-[5.75rem]">
          <QueryPanel
            queryKey={inline.openKey}
            context={context}
            onClose={inline.close}
          />
        </div>
      ) : null}
    </li>
  )
}

const TIMELINE_FOLD = 8

export function Timeline({ context }: { context: SnapshotContext }) {
  const events = context.snapshot.events
  const open = events.filter(
    (event) => event.state === 'open' && event.kind !== 'resolved'
  ).length
  const [all, setAll] = useState(false)
  const shown =
    all || context.printing || events.length <= TIMELINE_FOLD + 2
      ? events
      : events.slice(0, TIMELINE_FOLD)
  return (
    <Section
      id="snapshot-events"
      title="What happened"
      aside={
        events.length
          ? `${events.length} ${events.length === 1 ? 'event' : 'events'}${open ? `, ${open} still open` : ''}. Pick a query to see it here`
          : undefined
      }
    >
      {events.length ? (
        <ol
          className={cn(
            CARD,
            'divide-y divide-border-layout-1 overflow-hidden'
          )}
          data-testid="snapshot-timeline"
        >
          {shown.map((event, index) => (
            <EventRow
              key={`${event.ts}-${index}`}
              event={event}
              context={context}
            />
          ))}
          {shown.length < events.length ? (
            <li className="px-4 py-2 print:hidden">
              <Button
                variant="primary"
                modifier="link"
                size="small"
                label={`Show all ${events.length} events`}
                onClick={() => setAll(true)}
              />
            </li>
          ) : null}
        </ol>
      ) : (
        <p
          className={cn(
            CARD,
            'px-5 py-4 text-body-medium text-content-layout-2'
          )}
        >
          Nothing unusual happened.
        </p>
      )}
    </Section>
  )
}
