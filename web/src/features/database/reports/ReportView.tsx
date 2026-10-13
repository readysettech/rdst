// The Report tab: the daily report read in one pass. Head (title, date line, the Quick |
// Detailed switch, day menu, kebab), the verdict, The day (four numbers and the chart),
// Fix first (one numbered list) with what is open on Actions beside it for the current
// day, Health (the checks that need you, the fine ones folded into a line) and the
// footer. Detailed keeps that order and adds the day before's numbers, the busy stretches,
// What changed, confirmed fixes, every check with the settings audit, index changes, what
// RDST did and the totals. Before the first report the head and The day stand alone;
// signed out, the numbers stay and every copy becomes a sign-in link.
import { cn } from '@rs/tailwind-base'
import { Button, buttonStyles } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Dropdown } from '@rs/ui-new/dropdown'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Metric, MetricRow } from '@rs/ui-new/metric-row'
import { Pressable } from '@rs/ui-new/pressable'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { Verdict } from '@rs/ui-new/verdict'
import { Link } from '@tanstack/react-router'
import { type ReactNode, useCallback, useMemo, useState } from 'react'
import {
  type AiCanRun,
  aiCanRun,
  firstReportLine,
} from '@/features/monitor-kit/aiMonitoring'
import { domainFor, loadFromBreakdown } from '@/features/monitor-kit/charts'
import { formatBytes, formatClock, plural } from '@/features/monitor-kit/format'
import { downloadCsv } from '@/lib/csv'
import { fixtureOverride } from '@/lib/monitor/contract'
import { asCaScenario, caFixtures } from '@/lib/monitor/fixturesCa'
import {
  useLoad,
  useMonitorHealth,
  useMonitorSettings,
  useReport,
  useReportHistory,
  useReportViewPreference,
  useSetReportView,
} from '@/lib/monitor/hooks'
import type {
  ConfirmedFixRow,
  DatabaseReport,
  HealthCheckRow,
  ReportDetailed,
  ReportFixItem,
  ReportHealthRow,
  ReportViewMode,
  WhatChangedRow,
} from '@/lib/monitor/types'
import { ActionsNow } from '../actions/ActionsNow'
import { activityLabel } from '../analyze/runModel'
import { healthMetrics } from '../databaseModel'
import { DayChart } from './DayChart'
import { DayPicker } from './DayPicker'
import {
  CHANGED_TAG,
  changedMeta,
  changedSub,
  changedValue,
  chartFromLoad,
  checksMeta,
  confirmedSub,
  copyCount,
  dayMenu,
  detailedDayMeta,
  detailedTileText,
  didTime,
  fineLabel,
  fixesSql,
  fixFirstMeta,
  healthMeta,
  indexChangeText,
  longDay,
  monthDay,
  reportCsv,
  reportCsvName,
  reportDateLine,
  reportDay,
  reportMarkdown,
  reportTitle,
  runLine,
  spikesMeta,
  splitFixes,
  stretchQueries,
  stretchRollbacks,
  stretchTitle,
  tileText,
  tileTone,
  timeSpan,
} from './reportModel'
import { REPORT_HISTORY_LIMIT } from './reportNav'
import type { ReportRun } from './useReportRun'
import './reportPrint.css'

const SIGN_IN = '/account-login'
const FIX_SECTION = 'report-fix-first'
const AUDIT_SECTION = 'report-settings-audit'

const VIEW_SEGMENTS: { value: ReportViewMode; label: string }[] = [
  { value: 'quick', label: 'Quick' },
  { value: 'detailed', label: 'Detailed' },
]

const hairlineLink =
  'border-b border-border-layout-2 hover:border-content-layout-2'
const solidLink = buttonStyles({
  variant: 'primary',
  modifier: 'solid',
  size: 'base',
}).root()

/** The page's clock: a redesign fixture is frozen on its own timeline. */
function useNow(): number {
  const scenario = asCaScenario(fixtureOverride())
  if (scenario) return caFixtures(scenario).now
  return Math.floor(Date.now() / 60000) * 60
}

function useCopy() {
  const { toast } = useToast()
  return useCallback(
    (text: string) =>
      void navigator.clipboard
        ?.writeText(text)
        .then(() => toast({ title: 'Copied', duration: 2000 }))
        .catch(() =>
          toast({
            title: "Couldn't copy",
            description: 'The browser blocked the clipboard.',
            variant: 'negative',
          })
        ),
    [toast]
  )
}

function LockGlyph() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      className="h-3 w-3 shrink-0"
    >
      <rect x="2.5" y="5.5" width="7" height="5" rx="1" />
      <path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" />
    </svg>
  )
}

/** The sign-in caption that stands where a copy action would be, signed out. */
function SignInLock() {
  return (
    <Link
      to={SIGN_IN}
      className="inline-flex items-center gap-1.5 whitespace-nowrap text-caption text-content-layout-3 hover:text-content-layout-1"
    >
      <LockGlyph />
      Sign in
    </Link>
  )
}

function Section({
  id,
  title,
  meta,
  right,
  className,
  children,
}: {
  id: string
  title: string
  meta?: string
  right?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className={className}>
      <div className="flex items-baseline gap-3 border-b border-border-layout-2 pb-3">
        <h3
          id={`${id}-title`}
          className="text-headline-3 text-content-layout-1"
        >
          {title}
        </h3>
        {meta && (
          <Text as="span" level="caption" className="text-content-layout-3">
            {meta}
          </Text>
        )}
        {right && (
          <div className="ml-auto flex items-center gap-2.5 text-caption text-content-layout-3">
            {right}
          </div>
        )}
      </div>
      {children}
    </section>
  )
}

/** A second-level title inside a section; the list under it carries the rule. */
function SubSection({
  id,
  title,
  meta,
  children,
}: {
  id: string
  title: string
  meta?: string
  children: ReactNode
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`}>
      <div className="flex items-baseline gap-3 pt-7 pb-2.5">
        <h4
          id={`${id}-title`}
          className="text-body-medium font-medium text-content-layout-1"
        >
          {title}
        </h4>
        {meta && (
          <Text as="span" level="caption" className="text-content-layout-3">
            {meta}
          </Text>
        )}
      </div>
      {children}
    </section>
  )
}

/** A row's name with the quieter line under it. */
function RowText({
  name,
  sub,
  quiet = false,
}: {
  name: ReactNode
  sub?: ReactNode
  quiet?: boolean
}) {
  return (
    <span className="min-w-0">
      <span
        className={cn(
          'block text-body-medium',
          quiet ? 'text-content-layout-2' : 'text-content-layout-1'
        )}
      >
        {name}
      </span>
      {sub && (
        <span className="mt-0.5 block truncate text-caption text-content-layout-3">
          {sub}
        </span>
      )}
    </span>
  )
}

function QueryLink({
  queryKey,
  label,
  search,
}: {
  queryKey?: string | null
  label: string
  search?: Record<string, string>
}) {
  return (
    <Link
      to="/database"
      search={
        {
          view: 'live',
          ...(queryKey ? { query: queryKey } : {}),
          ...search,
        } as never
      }
      className={cn(
        'justify-self-end whitespace-nowrap text-caption text-content-layout-3',
        hairlineLink
      )}
    >
      {label}
    </Link>
  )
}

function Head({
  dateLine,
  title = 'Daily report',
  children,
}: {
  dateLine: string
  title?: string
  children?: ReactNode
}) {
  return (
    <div className="flex items-baseline gap-3.5">
      <Text as="h2" level="headline-2" className="text-content-layout-1">
        {title}
      </Text>
      <Text as="span" level="body-small" className="text-content-layout-3">
        {dateLine}
      </Text>
      {children && (
        <div className="ml-auto flex items-center gap-1.5 print:hidden">
          {children}
        </div>
      )}
    </div>
  )
}

function ReportMenu({ report, now }: { report: DatabaseReport; now: number }) {
  const copy = useCopy()
  const [open, setOpen] = useState(false)
  const item = 'h-8.5 rounded-md px-2.5 text-body-small'
  return (
    <Dropdown open={open} onOpenChange={setOpen}>
      <Dropdown.Trigger asChild>
        <IconButton
          icon="more"
          label="Report menu"
          variant="primary"
          modifier="ghost"
          size="small"
        />
      </Dropdown.Trigger>
      <Dropdown.Content
        align="end"
        className="min-w-52 rounded-panel bg-surface-primary-soft p-1.5"
      >
        <Dropdown.Item
          label="Print"
          className={item}
          onClick={() => window.print()}
        />
        <Dropdown.Item
          label="Download CSV"
          className={item}
          onClick={() => downloadCsv(reportCsv(report), reportCsvName(report))}
        />
        <Dropdown.Item
          label="Copy as Markdown"
          className={item}
          onClick={() => copy(reportMarkdown(report, now))}
        />
      </Dropdown.Content>
    </Dropdown>
  )
}

function TheDay({
  report,
  target,
}: {
  report: DatabaseReport
  target: string
}) {
  const day = report.day
  if (!day) return null
  return (
    <Section id="report-day" title="The day" meta={day.label} className="mt-9">
      <MetricRow bordered={false} className="mt-1">
        {day.tiles.map((tile) => {
          const text = tileText(tile)
          return (
            <Metric
              key={tile.metric}
              label={tile.label}
              value={text.value}
              unit={text.unit}
              detail={tile.reason ? undefined : (tile.detail ?? undefined)}
              reason={tile.reason ?? undefined}
              tone={tileTone(tile)}
            />
          )
        })}
      </MetricRow>
      {day.chart && (
        <DayChart
          chart={day.chart}
          spikes={day.spikes}
          queryCount={day.query_count}
          target={target}
          observedFrom={report.report_json?.snapshot?.period?.observed_from}
        />
      )}
    </Section>
  )
}

function StateChip({ item }: { item: ReportFixItem }) {
  if (item.state === 'needs_attention')
    return (
      <Tag
        size="small"
        variant="negative"
        modifier="ghost"
        className="rounded-pill whitespace-nowrap"
        label="Needs attention"
      />
    )
  if (item.state === 'worth_doing')
    return (
      <Tag
        size="small"
        variant="neutral"
        modifier="solid"
        className="rounded-pill whitespace-nowrap"
        label="Worth doing"
      />
    )
  if (item.query_keys.length === 0) return null
  const several = item.query_keys.length > 1
  return (
    <Link
      to="/database"
      search={
        (several
          ? { view: 'live' }
          : { view: 'live', query: item.query_keys[0] }) as never
      }
      className={cn('text-caption text-content-layout-3', hairlineLink)}
    >
      {several ? 'Open queries' : 'Open query'}
    </Link>
  )
}

function FixRow({
  item,
  signedOut,
  onCopy,
}: {
  item: ReportFixItem
  signedOut: boolean
  onCopy: (sql: string) => void
}) {
  const fixed = item.state === 'fixed'
  const grouped = item.query_keys.length > 1 && item.sql
  const action = fixed ? null : signedOut ? (
    item.state === 'worth_doing' || item.state === 'smaller' ? null : (
      <SignInLock />
    )
  ) : item.sql ? (
    <Button
      variant="primary"
      modifier={item.state === 'needs_attention' ? 'solid' : 'outline'}
      size="small"
      label={item.state === 'needs_attention' ? 'Copy change' : 'Copy fix'}
      onClick={() => onCopy(item.sql as string)}
    />
  ) : null
  return (
    <ListRow
      data-testid="fix-row"
      data-state={item.state}
      className="min-h-15 py-3"
    >
      <span className="text-caption tabular-nums text-content-layout-3">
        {fixed ? <Icon name="tick" label="Fixed" size="small" /> : item.rank}
      </span>
      <span className="min-w-0">
        <span
          className={cn(
            'block text-body-medium',
            fixed ? 'text-content-layout-3' : 'text-content-layout-1'
          )}
        >
          {item.title}
        </span>
        {(item.detail || grouped) && (
          <span className="mt-0.5 flex min-w-0 items-center gap-2 text-caption text-content-layout-3">
            {grouped && (
              <span className="truncate text-mono-small text-content-layout-2">
                {item.sql?.split(' ').slice(0, 3).join(' ')} ...
              </span>
            )}
            {item.detail && <span>{item.detail}</span>}
          </span>
        )}
      </span>
      <span className="justify-self-end">
        {fixed ? null : <StateChip item={item} />}
      </span>
      <span className="justify-self-end">{action}</span>
    </ListRow>
  )
}

function ConfirmedRow({
  row,
  dayStart,
}: {
  row: ConfirmedFixRow
  dayStart: number
}) {
  return (
    <ListRow data-testid="confirmed-row" className="min-h-15 py-3">
      <span className="text-caption text-content-layout-3">
        <Icon name="tick" label="" size="small" />
      </span>
      <RowText quiet name={row.title} sub={confirmedSub(row, dayStart)} />
      <Tag
        size="small"
        variant="positive"
        modifier="ghost"
        className="justify-self-end rounded-pill whitespace-nowrap"
        label="Confirmed"
      />
      <span />
    </ListRow>
  )
}

function FixFirst({
  report,
  signedOut,
  detailed,
}: {
  report: DatabaseReport
  signedOut: boolean
  /** Detailed: every fix in one list, then the fixes applied and confirmed. */
  detailed?: ReportDetailed | null
}) {
  const copy = useCopy()
  const [showSmaller, setShowSmaller] = useState(false)
  const items = report.fix_first ?? []
  const { main, smaller } = useMemo(
    () =>
      detailed
        ? {
            main: items.filter((item) => item.state !== 'fixed'),
            smaller: [],
          }
        : splitFixes(items),
    [items, detailed]
  )
  const confirmed = detailed?.fixes_confirmed ?? []
  if (items.length === 0 && confirmed.length === 0) return null
  const count = copyCount(report)
  const smallerWord = `${smaller.length} smaller ${smaller.length === 1 ? 'fix' : 'fixes'}`
  return (
    <Section
      id={FIX_SECTION}
      title="Fix first"
      meta={signedOut ? 'waiting for sign-in' : fixFirstMeta(items)}
      className="mt-13"
      right={
        signedOut ? (
          <SignInLock />
        ) : count > 0 ? (
          <Pressable
            className={cn('cursor-pointer', hairlineLink)}
            onClick={() => copy(fixesSql(items))}
          >
            Copy all {count} as SQL
          </Pressable>
        ) : undefined
      }
    >
      <DividedList columns="28px 1fr auto auto" className="[&>div]:border-t-0">
        {main.map((item) => (
          <FixRow
            key={`${item.rank ?? 'fixed'}-${item.title}`}
            item={item}
            signedOut={signedOut}
            onCopy={copy}
          />
        ))}
        {/* Signed out the smaller fixes are listed: a health row's link may lead to one. */}
        {(showSmaller || signedOut) &&
          smaller.map((item) => (
            <FixRow
              key={`${item.rank}-${item.title}`}
              item={item}
              signedOut={signedOut}
              onCopy={copy}
            />
          ))}
      </DividedList>
      {confirmed.length > 0 && (
        <SubSection
          id="report-fixes-confirmed"
          title="Fixes applied and confirmed"
          meta="measured after the change"
        >
          <DividedList columns="28px 1fr auto auto">
            {confirmed.map((row) => (
              <ConfirmedRow
                key={`${row.title}-${row.applied_at}`}
                row={row}
                dayStart={reportDay(report)}
              />
            ))}
          </DividedList>
        </SubSection>
      )}
      {smaller.length > 0 && !signedOut && (
        <Text as="p" level="caption" className="mt-2.5 text-content-layout-3">
          <Pressable
            className={cn('cursor-pointer', hairlineLink)}
            aria-expanded={showSmaller}
            onClick={() => setShowSmaller((value) => !value)}
          >
            {showSmaller ? 'Hide smaller fixes' : smallerWord}
          </Pressable>
        </Text>
      )}
    </Section>
  )
}

function HealthRow({ row }: { row: ReportHealthRow }) {
  return (
    <ListRow asChild interactive className="min-h-12 py-2">
      <a href={`#${FIX_SECTION}`}>
        <span className="min-w-0">
          <span className="flex items-center gap-2.5 text-body-medium text-content-layout-1">
            <Icon
              name="alert"
              label={row.state === 'bad' ? 'Needs attention' : 'Needs a look'}
              size="small"
              className={
                row.state === 'bad'
                  ? 'text-content-negative-soft'
                  : 'text-content-warning-soft'
              }
            />
            {row.finding}
          </span>
          {row.detail && (
            <span className="mt-0.5 block pl-6 text-caption text-content-layout-3">
              {row.detail}
            </span>
          )}
        </span>
        <span className="text-caption text-content-layout-3">
          {row.fix_rank != null && `Fix ${row.fix_rank}`}
        </span>
        <Icon
          name="chevron-right"
          label=""
          size="small"
          className="justify-self-end text-content-layout-3"
        />
      </a>
    </ListRow>
  )
}

function Health({ report }: { report: DatabaseReport }) {
  const [showFine, setShowFine] = useState(false)
  const rows = report.health ?? []
  const fine = report.health_fine ?? []
  if (rows.length === 0 && fine.length === 0) return null
  return (
    <Section
      id="report-health"
      title="Health"
      meta={healthMeta(rows, fine)}
      className="mt-13"
    >
      {rows.length > 0 && (
        <DividedList columns="1fr auto auto" className="[&>div]:border-t-0">
          {rows.map((row) => (
            <HealthRow key={row.check} row={row} />
          ))}
        </DividedList>
      )}
      {fine.length > 0 && (
        <>
          <div className="flex items-center gap-2.5 py-3.5 text-body-small text-content-layout-2">
            <Icon
              name="tick"
              label=""
              size="small"
              className="text-content-positive-soft"
            />
            {fineLabel(fine)}
            <span className="text-content-layout-3">{fine.join(', ')}</span>
            <Pressable
              className={cn(
                'ml-auto cursor-pointer text-caption text-content-layout-3',
                hairlineLink
              )}
              aria-expanded={showFine}
              onClick={() => setShowFine((value) => !value)}
            >
              {showFine ? 'Hide' : 'Show'}
            </Pressable>
          </div>
          {showFine && (
            <DividedList columns="1fr" className="[&>div]:border-t-0">
              {fine.map((name) => (
                <ListRow key={name} className="min-h-10 py-1.5">
                  <span className="flex items-center gap-2.5 text-body-small text-content-layout-2">
                    <Icon
                      name="tick"
                      label=""
                      size="small"
                      className="text-content-positive-soft"
                    />
                    {name}
                  </span>
                </ListRow>
              ))}
            </DividedList>
          )}
        </>
      )}
    </Section>
  )
}

// Detailed sections

function DetailedDay({
  report,
  detailed,
  target,
}: {
  report: DatabaseReport
  detailed: ReportDetailed
  target: string
}) {
  const day = report.day
  const stretches = detailed.spikes
  const rollbacks =
    detailed.tiles.find((tile) => tile.metric === 'rollbacks_deadlocks')
      ?.value ?? null
  return (
    <Section
      id="report-day"
      title="The day"
      meta={detailedDayMeta(day?.label, detailed)}
      className="mt-9"
    >
      <MetricRow bordered={false} className="mt-1">
        {detailed.tiles.map((tile) => {
          const text = detailedTileText({ ...tile, prior_note: null })
          return (
            <Metric
              key={tile.metric}
              size="small"
              label={tile.label}
              value={text.value}
              unit={text.unit}
              detail={text.detail}
              tone={tileTone(tile)}
            />
          )
        })}
      </MetricRow>
      {day?.chart && (
        <DayChart
          chart={day.chart}
          spikes={day.spikes}
          queryCount={day.query_count}
          target={target}
          observedFrom={report.report_json?.snapshot?.period?.observed_from}
          stretches={stretches}
        />
      )}
      {stretches.length > 0 && (
        <SubSection
          id="report-spikes"
          title="Spikes"
          meta={spikesMeta(stretches)}
        >
          <DividedList columns="150px 1fr auto 96px">
            {stretches.map((stretch) => (
              <ListRow
                key={stretch.start}
                data-testid="spike-row"
                className="min-h-15 py-3"
              >
                <span className="text-mono-small text-content-layout-2">
                  {timeSpan(stretch.start, stretch.end)}
                </span>
                <RowText
                  name={stretchTitle(stretch)}
                  sub={stretchQueries(stretch) || undefined}
                />
                <span className="text-caption tabular-nums text-content-layout-3">
                  {stretchRollbacks(stretch, rollbacks)}
                </span>
                <QueryLink
                  label="Open queries"
                  search={{ zoom: `${stretch.start}-${stretch.end}` }}
                />
              </ListRow>
            ))}
          </DividedList>
        </SubSection>
      )}
    </Section>
  )
}

function ChangedRow({ row }: { row: WhatChangedRow }) {
  const tag = CHANGED_TAG[row.kind]
  const value = changedValue(row)
  const plan = row.kind === 'plan_changed'
  return (
    <ListRow data-testid="changed-row" data-kind={row.kind} className="py-3">
      <span>
        <Tag
          size="small"
          variant={tag.variant}
          modifier={tag.variant === 'neutral' ? 'solid' : 'ghost'}
          className="rounded-pill whitespace-nowrap"
          label={tag.label}
        />
      </span>
      <RowText name={row.display_name} sub={changedSub(row) ?? undefined} />
      <span className="text-body-small tabular-nums text-content-layout-2">
        {value.lead}
        <span className="font-medium text-content-layout-1">
          {value.strong}
        </span>
      </span>
      {row.query_key ? (
        <QueryLink
          queryKey={row.query_key}
          label={plan ? 'Open plans' : 'Open query'}
          search={plan ? { tab: 'analysis' } : undefined}
        />
      ) : (
        <span />
      )}
    </ListRow>
  )
}

function WhatChanged({
  report,
  detailed,
}: {
  report: DatabaseReport
  detailed: ReportDetailed
}) {
  const rows = detailed.what_changed
  return (
    <Section
      id="report-changed"
      title="What changed"
      meta={changedMeta(
        report,
        detailed.totals.queries_seen,
        detailed.compared
      )}
      className="mt-13"
    >
      {rows.length > 0 ? (
        <DividedList
          columns="120px 1fr auto 96px"
          className="[&>div]:border-t-0"
        >
          {rows.map((row) => (
            <ChangedRow
              key={`${row.kind}-${row.query_key ?? row.display_name}`}
              row={row}
            />
          ))}
        </DividedList>
      ) : (
        <Text
          as="p"
          level="body-small"
          className="py-3.5 text-content-layout-2"
        >
          No query moved by a fifth or more.
        </Text>
      )}
    </Section>
  )
}

function CheckIcon({ state }: { state: HealthCheckRow['state'] }) {
  if (state === 'bad' || state === 'warn')
    return (
      <Icon
        name="alert"
        label={state === 'bad' ? 'Needs attention' : 'Needs a look'}
        size="small"
        className={
          state === 'bad'
            ? 'text-content-negative-soft'
            : 'text-content-warning-soft'
        }
      />
    )
  if (state === 'ok')
    return (
      <Icon
        name="tick"
        label="Fine"
        size="small"
        className="text-content-positive-soft"
      />
    )
  return (
    <Icon
      name="minus"
      label="Not checked"
      size="small"
      className="text-content-layout-3"
    />
  )
}

function CheckRow({ check }: { check: HealthCheckRow }) {
  const anchor = check.check === 'settings' ? AUDIT_SECTION : FIX_SECTION
  return (
    <ListRow
      data-testid="check-row"
      data-state={check.state}
      className="min-h-12 py-2"
    >
      <span className="min-w-0">
        <span className="flex items-center gap-2.5 text-body-medium text-content-layout-1">
          <CheckIcon state={check.state} />
          {check.label}
        </span>
        {check.finding && (
          <span className="mt-0.5 block pl-6 text-caption text-content-layout-3">
            {check.finding}
          </span>
        )}
      </span>
      <span className="text-body-small tabular-nums text-content-layout-2">
        {check.number}
      </span>
      <span className="justify-self-end text-caption text-content-layout-3">
        {check.fix_rank != null && (
          <a href={`#${anchor}`} className={hairlineLink}>
            Fix {check.fix_rank}
          </a>
        )}
      </span>
    </ListRow>
  )
}

function DetailedHealth({ detailed }: { detailed: ReportDetailed }) {
  const checks = detailed.health_checks
  const audit = detailed.settings_audit
  const onActions = audit.filter((row) => row.in_actions).length
  return (
    <Section
      id="report-health"
      title="Health"
      meta={checksMeta(checks)}
      className="mt-13"
    >
      <DividedList columns="1fr auto 64px" className="[&>div]:border-t-0">
        {checks.map((check) => (
          <CheckRow key={check.check} check={check} />
        ))}
      </DividedList>
      <SubSection
        id={AUDIT_SECTION}
        title="Settings audit"
        meta={
          audit.length > 0
            ? `${audit.length} off recommendation${onActions ? `, ${onActions} on Actions` : ''}`
            : 'every setting matches the recommendation'
        }
      >
        {audit.length > 0 && (
          <DividedList
            columns="1fr 140px 140px 120px"
            head={[
              { label: 'Parameter' },
              { label: 'Current', align: 'end' },
              { label: 'Recommended', align: 'end' },
              { label: 'Takes effect', align: 'end' },
            ]}
          >
            {audit.map((row) => (
              <ListRow
                key={row.parameter}
                data-testid="audit-row"
                className="min-h-11 py-2"
              >
                <span className="text-mono-small text-content-layout-1">
                  {row.parameter}
                  {row.in_actions && (
                    <span className="ml-2 text-caption text-content-layout-3">
                      on Actions
                    </span>
                  )}
                </span>
                <span className="text-right text-body-small tabular-nums text-content-layout-3">
                  {row.current}
                </span>
                <span className="text-right text-body-small tabular-nums text-content-layout-1">
                  {row.recommended}
                </span>
                <span className="text-right text-body-small text-content-layout-3">
                  {row.takes_effect}
                </span>
              </ListRow>
            ))}
          </DividedList>
        )}
      </SubSection>
    </Section>
  )
}

function IndexChanges({
  report,
  detailed,
}: {
  report: DatabaseReport
  detailed: ReportDetailed
}) {
  const changes = detailed.index_changes_seen
  if (changes.length === 0) return null
  return (
    <Section
      id="report-index-changes"
      title="Index changes seen"
      meta={`${changes.length} on ${monthDay(reportDay(report))}`}
      className="mt-13"
    >
      <DividedList columns="150px 1fr auto" className="[&>div]:border-t-0">
        {changes.map((change) => {
          const text = indexChangeText(change)
          return (
            <ListRow key={`${change.ts}-${change.index_name}`} className="py-3">
              <span className="text-mono-small text-content-layout-3">
                {formatClock(change.ts)}
              </span>
              <RowText name={text.title} sub={text.sub} />
              <span className="text-caption text-content-layout-3">
                {text.value}
              </span>
            </ListRow>
          )
        })}
      </DividedList>
    </Section>
  )
}

function RdstDid({
  report,
  detailed,
}: {
  report: DatabaseReport
  detailed: ReportDetailed
}) {
  const rows = detailed.what_rdst_did
  if (rows.length === 0) return null
  const dayEnd = report.period_end ?? reportDay(report) + 86400
  return (
    <Section
      id="report-rdst"
      title="What RDST did"
      meta={monthDay(reportDay(report))}
      className="mt-13"
      right={
        <Link to="/agent-activity" className={hairlineLink}>
          Activity
        </Link>
      }
    >
      <DividedList columns="150px 1fr auto" className="[&>div]:border-t-0">
        {rows.map((row) => (
          <ListRow
            key={`${row.ts}-${row.text}`}
            data-testid="did-row"
            className="min-h-11 py-2"
          >
            <span className="text-mono-small text-content-layout-3">
              {didTime(row, dayEnd)}
            </span>
            <span className="text-body-medium text-content-layout-1">
              {row.text}
            </span>
            <span className="justify-self-end text-caption text-content-layout-3">
              {row.detail ??
                (row.report_id != null ? (
                  <Link
                    to="/database"
                    search={
                      { view: 'report', report: String(row.report_id) } as never
                    }
                    className={hairlineLink}
                  >
                    Report #{row.report_id}
                  </Link>
                ) : row.item_id != null ? (
                  <Link
                    to="/inbox"
                    search={{ item: row.item_id } as never}
                    className={hairlineLink}
                  >
                    Open
                  </Link>
                ) : null)}
            </span>
          </ListRow>
        ))}
      </DividedList>
    </Section>
  )
}

function DetailedFoot({
  detailed,
  trigger,
}: {
  detailed: ReportDetailed
  trigger?: 'nightly' | 'on_request'
}) {
  const totals = detailed.totals
  const strong = 'font-medium text-content-layout-2'
  return (
    <div
      data-testid="report-totals"
      className="mt-14 flex gap-4 border-t border-border-layout-2 pt-3.5 text-caption text-content-layout-3"
    >
      <span>
        <span className={strong}>
          {totals.queries_seen.toLocaleString('en-US')}
        </span>{' '}
        queries seen
      </span>
      <span>
        <span className={strong}>
          {totals.readings.toLocaleString('en-US')}
        </span>{' '}
        {totals.readings === 1 ? 'reading' : 'readings'}
      </span>
      <span>
        Data kept {totals.data_kept_days} days, {formatBytes(totals.data_bytes)}
      </span>
      <span className="ml-auto">{runLine(totals.ran_at, trigger)}</span>
      <span>Report #{totals.report_number}</span>
    </div>
  )
}

function Foot({
  report,
  signedOut,
}: {
  report: DatabaseReport
  signedOut: boolean
}) {
  const run = report.run
  if (!run) return null
  return (
    <div className="mt-14 flex gap-4 border-t border-border-layout-2 pt-3.5 text-caption text-content-layout-3">
      <span>{runLine(run.ran_at, run.trigger)}</span>
      <span>
        {signedOut
          ? 'Numbers only'
          : `${plural(run.analyses, 'analysis', 'analyses')}, ${plural(run.rewrites, 'rewrite')} tested`}
      </span>
      <span className="ml-auto">Report #{run.number}</span>
    </div>
  )
}

function ReportDocument({
  report,
  target,
  now,
  run,
  days,
  onPick,
  view,
  onViewChange,
}: {
  report: DatabaseReport
  target: string
  now: number
  run: ReportRun
  days: ReturnType<typeof dayMenu>
  onPick: (id: number) => void
  view: ReportViewMode
  onViewChange: (view: ReportViewMode) => void
}) {
  // What to change lives on Actions, so only the current day points there.
  const current = report.id === days[0]?.id
  const signedOut = Boolean(report.narrative_missing) && !report.verdict
  // Detailed reads the payload's own sections; a report without them reads as Quick.
  const detailed = view === 'detailed' ? (report.detailed ?? null) : null
  const step = run.view ? activityLabel(run.view) : null
  const progress = step === 'Starting' ? null : step
  return (
    <div
      data-print-root
      data-testid="report-document"
      data-report-id={report.id}
      data-view={detailed ? 'detailed' : 'quick'}
      aria-busy={run.active || undefined}
    >
      <Head dateLine={reportDateLine(report, now)} title={reportTitle(report)}>
        <SegmentedControl<ReportViewMode>
          size="small"
          aria-label="Report view"
          value={view}
          onValueChange={onViewChange}
          segments={VIEW_SEGMENTS}
          className="border-transparent bg-transparent"
        />
        <DayPicker
          days={days}
          selectedId={report.id}
          target={target}
          onPick={onPick}
        />
        <ReportMenu report={report} now={now} />
      </Head>
      {run.active && (
        <div className="mt-4 flex items-center gap-4">
          <Text as="p" level="body-medium" className="text-content-layout-2">
            Running the report now
            {progress && (
              <span className="text-content-layout-3">. {progress}.</span>
            )}
          </Text>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Cancel"
            onClick={() => void run.cancel()}
          />
        </div>
      )}
      <div className={cn(run.active && 'opacity-50')}>
        {signedOut ? (
          <div className="mt-4 flex items-center gap-4">
            <Text
              as="p"
              level="headline-3"
              className="font-normal text-content-layout-2"
            >
              Sign in to get the written report and fixes.
            </Text>
            <Link to={SIGN_IN} className={solidLink}>
              Sign in
            </Link>
          </div>
        ) : (
          report.verdict && (
            <Verdict lead={report.verdict.lead} className="mt-4 max-w-[72ch]">
              {report.verdict.rest}
            </Verdict>
          )
        )}
        {detailed ? (
          <>
            <DetailedDay report={report} detailed={detailed} target={target} />
            <WhatChanged report={report} detailed={detailed} />
          </>
        ) : (
          <TheDay report={report} target={target} />
        )}
        <FixFirst report={report} signedOut={signedOut} detailed={detailed} />
        {current && (
          <div className="mt-9 print:hidden">
            <ActionsNow target={target} />
          </div>
        )}
        {detailed ? (
          <>
            <DetailedHealth detailed={detailed} />
            <IndexChanges report={report} detailed={detailed} />
            <RdstDid report={report} detailed={detailed} />
            <DetailedFoot detailed={detailed} trigger={report.run?.trigger} />
          </>
        ) : (
          <>
            <Health report={report} />
            <Foot report={report} signedOut={signedOut} />
          </>
        )}
      </div>
    </div>
  )
}

/** Before the first report: the head, one line on when it lands, and the day so far. */
function FirstReport({
  target,
  now,
  note,
  can: given,
  onOpenActions,
}: {
  target: string
  now: number
  note?: string
  can?: AiCanRun
  onOpenActions?: () => void
}) {
  const settings = useMonitorSettings(target)
  const can = given ?? aiCanRun({ settings: settings.data, now })
  const signIn = !note && can.report === 'signed_out'
  const health = useMonitorHealth(target, '24h')
  const load = useLoad(target, '24h')
  const history = health.data?.history
  const since = history?.first_ts ?? settings.data?.enabled_at ?? null
  const tiles = useMemo(
    () =>
      health.data
        ? healthMetrics(health.data, {
            day: true,
            cacheReason: null,
            firstRun: true,
          })
        : [],
    [health.data]
  )
  const chart = useMemo(() => {
    if (!load.data) return null
    const domain: [number, number] = since
      ? [since, now]
      : domainFor('24h', history, now)
    return chartFromLoad(loadFromBreakdown(load.data), domain)
  }, [load.data, since, history, now])
  const line =
    note ??
    [
      firstReportLine(settings.data, can, now),
      can.report ? null : 'After that, one runs every night.',
      since ? `Monitoring since ${formatClock(since)}.` : null,
    ]
      .filter(Boolean)
      .join(' ')
  return (
    <div data-testid="report-first">
      <Head dateLine={longDay(now)} />
      <div className="mt-4 flex items-center gap-4">
        {line && (
          <Text
            as="p"
            level="headline-5"
            className="font-normal text-content-layout-2"
          >
            {line}
          </Text>
        )}
        {signIn ? (
          <Link to={SIGN_IN} className={solidLink}>
            Sign in
          </Link>
        ) : (
          onOpenActions && (
            <Button
              variant="primary"
              modifier="ghost"
              label="Open Actions"
              onClick={onOpenActions}
            />
          )
        )}
      </div>
      <Section
        id="report-day"
        title="The day"
        meta={
          since
            ? `since ${formatClock(since)}, comparisons start tomorrow`
            : 'comparisons start tomorrow'
        }
        className="mt-9"
      >
        {tiles.length > 0 && (
          <MetricRow bordered={false} className="mt-1">
            {tiles.map((tile) => (
              <Metric
                key={tile.id}
                label={tile.label}
                value={tile.value}
                unit={tile.unit}
                detail={tile.detail}
                reason={tile.reason}
                tone={tile.tone}
              />
            ))}
          </MetricRow>
        )}
        {chart && (
          <DayChart
            chart={chart}
            spikes={[]}
            queryCount={chart.bands.length}
            target={target}
          />
        )}
      </Section>
    </div>
  )
}

function ReportSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true">
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-7 w-full max-w-2xl" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-56 w-full rounded-card" />
    </div>
  )
}

export function ReportView({
  target,
  reportId,
  can,
  run,
  onShowLatest,
  onPick,
  onOpenActions,
}: {
  target: string
  reportId: number | 'latest'
  /** What AI work can run now; read from the settings when absent. */
  can?: AiCanRun
  run: ReportRun
  onShowLatest: () => void
  onPick: (id: number) => void
  onOpenActions?: () => void
}) {
  const now = useNow()
  const preference = useReportViewPreference()
  const setView = useSetReportView()
  const view = preference.data ?? 'quick'
  // The latest is the newest daily report; a run opens its own by id. The fetch waits for
  // the view preference so the first read is the view the user reads.
  const report = useReport(
    preference.isPending ? '' : target,
    reportId === 'latest' ? 'daily' : reportId,
    view
  )
  const history = useReportHistory(target, REPORT_HISTORY_LIMIT, true)
  const days = useMemo(
    () => dayMenu(history.data?.reports, REPORT_HISTORY_LIMIT),
    [history.data]
  )

  if (report.isPending) return <ReportSkeleton />

  if (report.isError)
    return (
      <ErrorState
        tone="quiet"
        title="The report didn't load"
        message="RDST couldn't read this report."
        onRetry={() => void report.refetch()}
      />
    )

  const data = report.data
  const written = data && data.state === 'done' && (data.day || data.verdict)

  if (!written) {
    if (data && reportId !== 'latest' && data.state === 'done')
      return (
        <div data-testid="report-gone">
          <Head
            dateLine={reportDateLine(data, now)}
            title={reportTitle(data)}
          />
          <div className="mt-4 flex items-center gap-4">
            <Text
              as="p"
              level="headline-5"
              className="font-normal text-content-layout-2"
            >
              This report was not kept.
            </Text>
            <Button
              variant="primary"
              modifier="ghost"
              label="Open the latest report"
              onClick={onShowLatest}
            />
          </div>
        </div>
      )
    const stopped =
      data && (data.state === 'failed' || data.state === 'cancelled')
    return (
      <FirstReport
        target={target}
        now={now}
        can={can}
        onOpenActions={onOpenActions}
        note={
          stopped
            ? data.state === 'failed'
              ? 'The report did not finish.'
              : 'The report was cancelled.'
            : undefined
        }
      />
    )
  }

  return (
    <ReportDocument
      report={data}
      target={target}
      now={now}
      run={run}
      days={days}
      onPick={onPick}
      view={view}
      onViewChange={(next) => setView.mutate(next)}
    />
  )
}
