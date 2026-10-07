// The top of the daily snapshot (FEEDBACK_ROUND3 B1, B2; ROUND4 R2): the database's health
// for the day, its numbers, the day in brief (its top priorities, each with what it
// means). Each brief line leads to its section; query chips
// open the query in place. Number tiles put their reading on the day's chart.
import { cn } from '@rs/tailwind-base'
import { Icon, type IconStrokeName } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Pressable } from '@rs/ui-new/pressable'
import { MiniTrend, SERIES_COLOR } from '@/features/monitor-kit/charts'
import type {
  SnapshotBriefItem,
  SnapshotCheck,
  SnapshotMetric,
} from '@/lib/monitor/types'
import {
  CARD,
  QueryPanel,
  Section,
  Sentence,
  type SnapshotContext,
  useInlineQuery,
} from './snapshotKit'
import {
  briefItems,
  briefTarget,
  type ChartView,
  comparisonLine,
  NUMBER_VIEW,
  numberView,
  vitalPoints,
} from './snapshotModel'

export function scrollToSection(id: string) {
  document
    .getElementById(`${id}-section`)
    ?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

// The day in brief

const TOPIC: Record<
  string,
  { icon: IconStrokeName; tone: string; go: string }
> = {
  traffic: {
    icon: 'dashboard',
    tone: 'bg-surface-info-soft text-content-info-soft',
    go: 'See the chart',
  },
  latency: {
    icon: 'speedometer',
    tone: 'bg-surface-rising-soft text-content-rising-soft',
    go: 'See the chart',
  },
  spike: {
    icon: 'arrow-up',
    tone: 'bg-surface-warning-soft text-content-warning-soft',
    go: 'See the chart',
  },
  queries: {
    icon: 'database',
    tone: 'bg-surface-primary-soft text-content-primary-soft',
    go: 'See the queries',
  },
  events: {
    icon: 'notification',
    tone: 'bg-surface-negative-soft text-content-negative-soft',
    go: 'See what happened',
  },
  health: {
    icon: 'database-settings',
    tone: 'bg-surface-warning-soft text-content-warning-soft',
    go: 'See the database actions',
  },
  todo: {
    icon: 'tick-double',
    tone: 'bg-surface-positive-soft text-content-positive-soft',
    go: 'See the database actions',
  },
  index: {
    icon: 'layers',
    tone: 'bg-surface-layout-2 text-content-layout-2',
    go: 'See what happened',
  },
}
const NOTE = {
  icon: 'info' as IconStrokeName,
  tone: 'bg-surface-layout-2 text-content-layout-2',
  go: '',
}

function BriefRow({
  item,
  context,
}: {
  item: SnapshotBriefItem
  context: SnapshotContext
}) {
  const inline = useInlineQuery()
  const look = TOPIC[item.topic ?? ''] ?? NOTE
  const target = briefTarget(item.topic)
  return (
    <li
      className="group relative px-4 py-3 transition-colors duration-fast hover:bg-surface-layout-2/50 tablet:px-5"
      data-testid="brief-item"
      data-topic={item.topic ?? ''}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className={cn(
            'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-panel transition-transform duration-fast group-hover:scale-105',
            look.tone
          )}
        >
          <Icon name={look.icon} label="" size="small" />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-body-large leading-relaxed text-content-layout-1">
            <Sentence
              text={item.text}
              context={context}
              onToggle={inline.toggle}
              activeKey={inline.openKey}
            />
          </p>
          {item.why ? (
            <p
              className="text-body-medium text-content-layout-2"
              data-testid="brief-why"
            >
              {item.why}
            </p>
          ) : null}
        </div>
        {target ? (
          <IconButton
            icon="arrow-down"
            label={look.go}
            modifier="ghost"
            size="small"
            className="hidden shrink-0 opacity-0 transition-opacity duration-fast group-hover:opacity-100 focus-visible:opacity-100 tablet:inline-flex print:hidden"
            onClick={() => scrollToSection(target)}
          />
        ) : null}
      </div>
      {inline.openKey ? (
        <div className="tablet:pl-10">
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

export function Brief({ context }: { context: SnapshotContext }) {
  const { snapshot } = context
  const items = briefItems(snapshot)
  if (!items.length) return null
  const top =
    items.length === 1 ? 'The top priority' : `Top ${items.length} priorities`
  return (
    <Section
      id="snapshot-brief"
      title="The day in brief"
      aside={
        snapshot.brief_source === 'model'
          ? `${top}, as RDST reads the day's measurements`
          : `${top} from the day's measurements`
      }
    >
      <ul
        className={cn(CARD, 'divide-y divide-border-layout-1 overflow-hidden')}
      >
        {items.map((item, index) => (
          <BriefRow key={index} item={item} context={context} />
        ))}
      </ul>
    </Section>
  )
}

// Database health for the day

const VERDICT_LOOK = {
  ok: {
    tag: 'positive',
    ring: 'bg-surface-positive-soft text-content-positive-soft',
    text: 'text-content-positive-soft',
    icon: 'tick',
  },
  warn: {
    tag: 'warning',
    ring: 'bg-surface-warning-soft text-content-warning-soft',
    text: 'text-content-warning-soft',
    icon: 'alert',
  },
  bad: {
    tag: 'negative',
    ring: 'bg-surface-negative-soft text-content-negative-soft',
    text: 'text-content-negative-soft',
    icon: 'alert',
  },
} as const

const CHECK_DOT: Record<SnapshotCheck['status'], string> = {
  ok: 'bg-content-positive-plain',
  warn: 'bg-content-warning-plain',
  bad: 'bg-content-negative-plain',
  unknown: 'bg-content-layout-3',
}

const DELTA_TONE = {
  good: 'bg-surface-positive-soft text-content-positive-soft',
  warn: 'bg-surface-warning-soft text-content-warning-soft',
  bad: 'bg-surface-negative-soft text-content-negative-soft',
  neutral: 'bg-surface-layout-2 text-content-layout-2',
} as const

const TILE_COLOR: Record<SnapshotMetric, string> = {
  qps: SERIES_COLOR.throughput,
  mean_ms: SERIES_COLOR.latency,
  active_conns_peak: SERIES_COLOR.connections,
  cache_hit_ratio_min: SERIES_COLOR.hit,
  xact_rollback: SERIES_COLOR.rollback,
  deadlocks: SERIES_COLOR.rollback,
}

const TILE_POINTS: Record<SnapshotMetric, Parameters<typeof vitalPoints>[1]> = {
  qps: 'qps',
  mean_ms: 'mean_ms',
  active_conns_peak: 'active_conns',
  cache_hit_ratio_min: 'cache_hit_ratio',
  xact_rollback: 'xact_rollback',
  deadlocks: 'deadlocks',
}

function Checks({ checks }: { checks: SnapshotCheck[] }) {
  return (
    <ul className="grid grid-cols-2 gap-px bg-border-layout-1 laptop:grid-cols-4">
      {checks.map((check) => (
        <li
          key={check.check}
          className="flex min-w-0 items-start gap-2.5 bg-surface-layout-1 px-3 py-3 tablet:px-4"
          data-testid="health-check"
          data-status={check.status}
        >
          <span
            aria-hidden="true"
            className={cn(
              'mt-1.5 h-2 w-2 shrink-0 rounded-full',
              CHECK_DOT[check.status]
            )}
          />
          <span className="min-w-0">
            <span className="block text-label-medium text-content-layout-1">
              {check.label}
            </span>
            <span
              className={cn(
                'block text-body-small',
                check.status === 'ok' || check.status === 'unknown'
                  ? 'text-content-layout-3'
                  : 'text-content-layout-2'
              )}
            >
              {check.value}
            </span>
          </span>
        </li>
      ))}
      {/* Fill the last row so the grid has no hole. */}
      {Array.from({ length: (4 - (checks.length % 4)) % 4 }, (_, index) => (
        <li
          key={`fill-${index}`}
          aria-hidden="true"
          className="hidden bg-surface-layout-1 laptop:block"
        />
      ))}
      {checks.length % 2 ? (
        <li aria-hidden="true" className="bg-surface-layout-1 laptop:hidden" />
      ) : null}
    </ul>
  )
}

function NumberTile({
  metric,
  context,
  view,
  onView,
}: {
  metric: SnapshotMetric
  context: SnapshotContext
  view: ChartView
  onView: (view: ChartView) => void
}) {
  const number = context.snapshot.numbers.find((row) => row.metric === metric)
  if (!number) return null
  const shown = numberView(number)
  const target = NUMBER_VIEW[metric]
  const selected = view === target
  const points = vitalPoints(context.snapshot.vitals, TILE_POINTS[metric])
  const color = TILE_COLOR[metric]
  const domain: [number, number] = [
    context.snapshot.period.start,
    context.snapshot.period.end,
  ]
  return (
    <li className="min-w-0">
      <Pressable
        onClick={() => {
          onView(target)
          scrollToSection('snapshot-chart')
        }}
        aria-pressed={selected}
        aria-label={`${shown.label} ${shown.value}${shown.unit}. Show it on the chart`}
        data-testid="day-number"
        className={cn(
          CARD,
          'group relative flex h-full w-full flex-col gap-1 overflow-hidden px-4 pt-3 pb-2 text-left transition-[transform,box-shadow,border-color] duration-fast',
          'hover:-translate-y-0.5 hover:border-border-layout-2 hover:shadow-elevation-2 focus-visible:shadow-focus',
          selected &&
            'border-border-primary-soft bg-surface-primary-soft/20 shadow-elevation-1'
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'absolute inset-x-0 top-0 h-[3px] transition-opacity duration-fast',
            selected ? 'opacity-100' : 'opacity-30 group-hover:opacity-100'
          )}
          style={{ background: color }}
        />
        <span className="flex items-center justify-between gap-2 text-label-small text-content-layout-2">
          {shown.label}
          <Icon
            name="arrow-down"
            label=""
            aria-hidden="true"
            className="h-3.5 w-3.5 text-content-layout-3 opacity-0 transition-opacity duration-fast group-hover:opacity-100 print:hidden"
          />
        </span>
        <span className="flex items-baseline gap-1">
          <span className="text-headline-4 font-semibold tabular-nums text-content-layout-1">
            {shown.value}
          </span>
          {shown.unit ? (
            <span className="text-body-small text-content-layout-3">
              {shown.unit}
            </span>
          ) : null}
        </span>
        <span className="flex flex-wrap items-center gap-1.5 text-caption text-content-layout-3">
          {shown.delta ? (
            <span
              className={cn(
                'inline-flex items-center gap-0.5 rounded-sm px-1.5 py-px font-semibold tabular-nums',
                DELTA_TONE[shown.tone]
              )}
            >
              {shown.direction === 'up' || shown.direction === 'down' ? (
                <Icon
                  name={shown.direction === 'up' ? 'arrow-up' : 'arrow-down'}
                  label={shown.direction === 'up' ? 'Up' : 'Down'}
                  className="h-3 w-3"
                />
              ) : null}
              {shown.delta}
            </span>
          ) : null}
          <span className="tabular-nums">
            {shown.prior ? `was ${shown.prior}` : 'no earlier day'}
          </span>
        </span>
        {points.length ? (
          <span className="mt-auto block w-full pt-1 opacity-70 transition-opacity duration-fast group-hover:opacity-100">
            <MiniTrend
              className="w-full"
              points={points}
              color={color}
              ariaLabel={`${shown.label} over the day`}
              domain={domain}
              height={36}
              resolution={context.snapshot.vitals?.res}
              interactive={false}
            />
          </span>
        ) : null}
      </Pressable>
    </li>
  )
}

export function Health({ context }: { context: SnapshotContext }) {
  const { snapshot } = context
  const overview = snapshot.overview
  const look = overview ? VERDICT_LOOK[overview.status] : null
  return (
    <Section
      id="snapshot-health"
      title="Database health"
      aside="How the database itself did this day"
    >
      {overview && look ? (
        <div
          className={cn(CARD, 'overflow-hidden')}
          data-testid="health-overview"
        >
          <div className="flex items-start gap-4 px-4 py-4 tablet:px-5">
            <span
              aria-hidden="true"
              className={cn(
                'flex h-10 w-10 shrink-0 items-center justify-center rounded-full',
                look.ring
              )}
            >
              <Icon name={look.icon} label="" size="base" />
            </span>
            <div className="min-w-0 space-y-0.5">
              <p className={cn('text-headline-5', look.text)}>
                {overview.label}
              </p>
              <p className="text-body-large text-content-layout-2">
                {overview.headline}
              </p>
            </div>
          </div>
          <div className="border-t border-border-layout-1">
            <Checks checks={overview.checks} />
          </div>
        </div>
      ) : null}
    </Section>
  )
}

/** The day's readings as tiles; picking one puts it on the chart. */
export function Numbers({
  context,
  view,
  onView,
}: {
  context: SnapshotContext
  view: ChartView
  onView: (view: ChartView) => void
}) {
  const { snapshot } = context
  const against = comparisonLine(snapshot)
  return (
    <Section
      id="snapshot-numbers"
      title="The day in numbers"
      aside={
        <span data-testid="day-numbers-against">
          {against ?? 'No earlier day to compare with'}. Pick one to chart it.
        </span>
      }
    >
      <ul className="grid grid-cols-2 gap-2 tablet:grid-cols-3">
        {snapshot.numbers.map((number) => (
          <NumberTile
            key={number.metric}
            metric={number.metric}
            context={context}
            view={view}
            onView={onView}
          />
        ))}
      </ul>
    </Section>
  )
}
