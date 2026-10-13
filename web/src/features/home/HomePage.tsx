import { Button, buttonStyles } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Metric, MetricRow } from '@rs/ui-new/metric-row'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { StatusLine } from '@rs/ui-new/status-line'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link, useNavigate } from '@tanstack/react-router'
import { type ReactNode, useState } from 'react'
import { useDbFilter } from '../../hooks/useDbFilter'
import { useApplied } from '../../lib/monitor/actions'
import type {
  HomeDayCard,
  HomeLogEntry,
  HomeModel,
  HomeStripRow,
} from '../../lib/monitor/types'
import { formatClock, plural } from '../monitor-kit/format'
import { RADAR_STATE, Radar } from '../monitor-kit/Radar'
import { SignInNote, useSignInHeld } from '../monitor-kit/signInHold'
import { DATABASE_WORD, DatabaseFilter } from '../shared/filters/DatabaseFilter'
import { DayChart, dayChartModel } from './DayChart'
import { HomeEmpty, HomeSentence, homeCaption } from './HomeStates'
import {
  appliedLine,
  doFirstTarget,
  homeTone,
  isEmptyHome,
  latencyWord,
  monitorLinkTo,
  OUTCOME_WORD,
  type RouteLink,
  relativeDay,
  reportDayLabel,
  reportLink,
  stripRows,
  useHomeModel,
} from './homeModel'

const DASH = '\u2014'
/** Databases in the strip before the rest fold under "N more". */
const ROWS_SHOWN = 8

const ghostLink = buttonStyles({
  variant: 'primary',
  modifier: 'ghost',
  size: 'small',
}).root()

const hairlineLink =
  'border-b border-border-layout-2 hover:border-content-layout-2'

function longDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })
}

/** "9:55": a log time in the day's own clock. */
function shortClock(ts: number): string {
  const date = new Date(ts * 1000)
  return `${date.getHours() % 12 || 12}:${String(date.getMinutes()).padStart(2, '0')}`
}

function RouteLinkTo({
  link,
  className,
  children,
  ...rest
}: {
  link: RouteLink
  className?: string
  'aria-label'?: string
  children: ReactNode
}) {
  return (
    <Link
      to={link.to as never}
      search={link.search as never}
      className={className}
      {...rest}
    >
      {children}
    </Link>
  )
}

function SectionTitle({
  id,
  title,
  meta,
}: {
  id: string
  title: string
  meta?: string | null
}) {
  return (
    <div className="mb-3 flex items-baseline gap-3">
      <h2 id={id} className="text-headline-5 text-content-layout-1">
        {title}
      </h2>
      {meta && (
        <Text as="span" level="caption" className="text-content-layout-3">
          {meta}
        </Text>
      )}
    </div>
  )
}

function Chevron() {
  return (
    <Icon
      name="chevron-right"
      label=""
      size="small"
      className="justify-self-end text-content-layout-3"
    />
  )
}

function DayCard({
  day,
  now,
  needsYouFirst,
  status,
}: {
  day: HomeDayCard
  now: number
  /** This database has Act now items and leads the page. */
  needsYouFirst: boolean
  /** The database's row when no across-databases strip shows its radar. */
  status?: HomeStripRow
}) {
  const midnight = new Date(now * 1000)
  midnight.setHours(0, 0, 0, 0)
  const chart = dayChartModel(
    day.series,
    now,
    Math.floor(midnight.getTime() / 1000)
  )
  const latency = latencyWord(day.mean_ms_delta)
  const cacheOut = Boolean(day.cache_hit_reason)
  const report = reportLink(day)
  const applied = appliedLine(useApplied(day.target).data?.totals)
  const fixes = (
    <Link
      to="/actions"
      search={{ db: day.target } as never}
      className={hairlineLink}
    >
      {day.fixes_ready}
    </Link>
  )
  return (
    <section
      aria-labelledby="home-card-title"
      data-testid="home-day-card"
      className="mt-8 rounded-card border border-border-layout-2 bg-surface-primary-soft px-6 py-5.5"
    >
      <div className="flex items-baseline gap-3.5">
        {status && (
          <span className="self-center">
            <Radar
              state={RADAR_STATE[status.state]}
              activity={status.activity}
              name={status.target}
              toDo={status.to_do}
            />
          </span>
        )}
        <h2
          id="home-card-title"
          className="text-headline-5 text-content-layout-1"
        >
          {day.target}
        </h2>
        <Text as="span" level="caption" className="text-content-layout-3">
          {chart.short && chart.since
            ? `since ${formatClock(chart.since)}`
            : needsYouFirst
              ? 'needs you first, today vs yesterday'
              : 'today, vs yesterday'}
        </Text>
        <div className="ml-auto flex items-center gap-4">
          <Link
            to="/database"
            search={{ target: day.target } as never}
            className={`text-body-small text-content-layout-2 ${hairlineLink}`}
          >
            Open {day.target}
          </Link>
          {report && day.report_day && (
            <RouteLinkTo link={report} className={ghostLink}>
              Daily report, {reportDayLabel(day.report_day)}
            </RouteLinkTo>
          )}
        </div>
      </div>
      {applied && (
        <p className="mt-1.5 flex items-baseline gap-3 text-body-small text-content-layout-2">
          <span>{applied}</span>
          <Link
            to="/actions"
            search={{ db: day.target, scope: 'applied' } as never}
            className={`text-caption ${hairlineLink}`}
          >
            See Applied
          </Link>
        </p>
      )}
      <MetricRow bordered={false} className="mt-4">
        <Metric
          size="small"
          label="Queries per second"
          value={day.qps == null ? DASH : day.qps.toFixed(1)}
          detail={
            day.qps_peak != null && day.qps_peak_at != null
              ? `peak ${day.qps_peak.toFixed(1)} at ${shortClock(day.qps_peak_at)}`
              : undefined
          }
        />
        <Metric
          size="small"
          label="Avg latency"
          value={day.mean_ms == null ? DASH : day.mean_ms.toFixed(1)}
          unit={day.mean_ms == null ? undefined : 'ms'}
          detail={latency ?? undefined}
        />
        <Metric
          size="small"
          label="Fixes ready"
          value={fixes}
          detail={
            day.fixes_share == null
              ? undefined
              : `${Math.round(day.fixes_share * 100)}% of database time`
          }
        />
        <Metric
          size="small"
          label="Cache hit"
          value={
            day.cache_hit_ratio == null
              ? DASH
              : (day.cache_hit_ratio * 100).toFixed(1)
          }
          unit={day.cache_hit_ratio == null ? undefined : '%'}
          tone={cacheOut ? 'warning' : 'neutral'}
          reason={day.cache_hit_reason ?? undefined}
          detail={cacheOut ? undefined : (latency ?? undefined)}
        />
      </MetricRow>
      <div className="mt-4.5">
        <DayChart model={chart} ariaLabel="Queries per second since midnight" />
      </div>
    </section>
  )
}

/** A count in the strip; zero reads in the third tone. */
function Count({ value }: { value: number }) {
  return (
    <div
      className={`text-right text-body-small tabular-nums ${value > 0 ? 'text-content-layout-1' : 'text-content-layout-3'}`}
    >
      {value}
    </div>
  )
}

const STRIP_COLUMNS = 'minmax(200px,1.3fr) 1fr 120px 120px 140px 136px'

function StripRow({ row }: { row: HomeStripRow }) {
  const navigate = useNavigate()
  const off = row.state === 'not_monitored'
  const cells = (
    <>
      <Text
        as="div"
        level="body-medium"
        className={`min-w-0 truncate ${off ? 'text-content-layout-3' : 'font-medium text-content-layout-1'}`}
      >
        {row.target}
      </Text>
      <div className="flex items-center gap-2 text-body-small text-content-layout-2">
        <Radar
          state={RADAR_STATE[row.state]}
          activity={row.activity}
          name={row.target}
          toDo={row.to_do}
        />
        {DATABASE_WORD[row.state]}
      </div>
      <div className="flex justify-end text-body-small tabular-nums">
        {row.act_now > 0 ? (
          <Tag
            size="small"
            variant="negative"
            modifier="ghost"
            className="inline-flex w-auto rounded-pill whitespace-nowrap"
            label={`${row.act_now} act now`}
          />
        ) : (
          <span className="text-content-layout-3">0</span>
        )}
      </div>
      <Count value={row.to_do} />
      <Count value={row.fixes_ready} />
    </>
  )
  if (row.state === 'cant_reach' || off)
    return (
      <ListRow>
        {cells}
        <div className="justify-self-end">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label={off ? 'Turn on' : 'Fix connection'}
            aria-label={
              off
                ? `Turn on monitoring for ${row.target}`
                : `Fix connection for ${row.target}`
            }
            onClick={() =>
              off
                ? navigate({
                    to: '/database',
                    search: { target: row.target, panel: 'enable' } as never,
                  })
                : navigate({
                    to: '/configure',
                    search: { panel: 'connections', edit: row.target },
                  })
            }
          />
        </div>
      </ListRow>
    )
  return (
    <ListRow asChild interactive>
      <Link
        to="/database"
        search={{ target: row.target } as never}
        aria-label={`Open ${row.target}`}
      >
        {cells}
        <Chevron />
      </Link>
    </ListRow>
  )
}

/** Every database side by side, in the order they need you; the filter never narrows it. */
function AcrossDatabases({ rows }: { rows: HomeStripRow[] }) {
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? rows : rows.slice(0, ROWS_SHOWN)
  const hidden = rows.length - ROWS_SHOWN
  return (
    <section aria-labelledby="home-databases" className="mt-12">
      <SectionTitle
        id="home-databases"
        title="Across your databases"
        meta={`${rows.length}, sorted by what needs you`}
      />
      <DividedList
        columns={STRIP_COLUMNS}
        head={[
          { label: 'Database' },
          { label: 'State' },
          { label: 'Needs you', align: 'end' },
          { label: 'To do', align: 'end' },
          { label: 'Fixes ready', align: 'end' },
          { label: '' },
        ]}
      >
        {shown.map((row) => (
          <StripRow key={row.target} row={row} />
        ))}
      </DividedList>
      {hidden > 0 && !showAll && (
        <p className="mt-2.5 text-caption text-content-layout-3">
          <Pressable
            onClick={() => setShowAll(true)}
            className={`cursor-pointer ${hairlineLink}`}
          >
            {plural(hidden, 'more database')}
          </Pressable>
        </p>
      )}
    </section>
  )
}

function LogRow({ entry, named }: { entry: HomeLogEntry; named: boolean }) {
  const detail = [named ? entry.link.target : null, entry.detail]
    .filter(Boolean)
    .join(', ')
  return (
    <ListRow asChild interactive className="min-h-11.5 py-2">
      <RouteLinkTo link={monitorLinkTo(entry.link)}>
        <span className="text-mono-small tabular-nums text-content-layout-3">
          {shortClock(entry.ts)}
        </span>
        <span className="flex min-w-0 flex-wrap items-center gap-2 text-body-small text-content-layout-1">
          {entry.text}
          {entry.band === 'act_now' && (
            <Tag
              size="small"
              variant="negative"
              modifier="ghost"
              className="inline-flex w-auto rounded-pill whitespace-nowrap"
              label="act now"
            />
          )}
          {detail && <span className="text-content-layout-3">{detail}</span>}
        </span>
        <span className="text-caption text-content-layout-3">
          {entry.link.label}
        </span>
      </RouteLinkTo>
    </ListRow>
  )
}

function awayLine(away: HomeModel['since_yesterday']): string | null {
  const parts: string[] = []
  if (away.opened_closed)
    parts.push(`${plural(away.opened_closed, 'item')} opened and closed`)
  if (away.fixes_confirmed)
    parts.push(`${plural(away.fixes_confirmed, 'fix', 'fixes')} confirmed`)
  return parts.length ? `since yesterday: ${parts.join(', ')}` : null
}

function Later({
  model,
  today,
  named,
  scoped,
}: {
  model: HomeModel
  today: HomeLogEntry[]
  /** More than one database is configured, so each row names its own. */
  named: boolean
  /** The log is narrowed to one database; the since-yesterday counts are not. */
  scoped: boolean
}) {
  return (
    <div className="mt-12 grid grid-cols-[1.25fr_1fr] gap-10">
      <section aria-labelledby="home-today">
        <SectionTitle
          id="home-today"
          title="Today"
          meta={scoped ? null : awayLine(model.since_yesterday)}
        />
        <DividedList columns="48px 1fr auto">
          {today.map((entry) => (
            <LogRow
              key={`${entry.ts}-${entry.text}-${entry.link.target}`}
              entry={entry}
              named={named}
            />
          ))}
        </DividedList>
        <p className="mt-2.5 text-caption text-content-layout-3">
          <Link to="/agent-activity" className={hairlineLink}>
            All activity
          </Link>
        </p>
      </section>
      {model.recently_analyzed.length > 0 && (
        <section aria-labelledby="home-recent">
          <SectionTitle id="home-recent" title="Recently analyzed by you" />
          <DividedList columns="1fr auto">
            {model.recently_analyzed.map((item) => (
              <ListRow
                key={`${item.target}-${item.query_key}`}
                asChild
                interactive
                className="min-h-11"
              >
                <RouteLinkTo
                  link={monitorLinkTo({
                    kind: 'analysis',
                    target: item.target,
                    label: 'Analysis',
                    query_key: item.query_key,
                    analysis_ref: item.analysis_ref,
                  })}
                >
                  <span className="min-w-0">
                    <Text
                      as="span"
                      level="body-medium"
                      className="block truncate font-medium text-content-layout-1"
                    >
                      {item.display_name}
                    </Text>
                    <Text
                      as="span"
                      level="caption"
                      className="block text-content-layout-3"
                    >
                      {named && `${item.target}, `}
                      {relativeDay(item.analyzed_at, model.generated_at)},{' '}
                      {OUTCOME_WORD[item.outcome]}
                    </Text>
                  </span>
                  <Chevron />
                </RouteLinkTo>
              </ListRow>
            ))}
          </DividedList>
        </section>
      )}
    </div>
  )
}

function HomeLoading() {
  return (
    <>
      <Skeleton className="h-8 w-96 max-w-full rounded-lg" />
      <Skeleton className="mt-3 h-4 w-72 max-w-full rounded-lg" />
      <Skeleton className="mt-8 h-56 rounded-card" />
    </>
  )
}

export function HomeView({
  model,
  db = null,
  onDbChange,
}: {
  model: HomeModel
  /** The database filter: it narrows the day card and the log, never the strip. */
  db?: string | null
  onDbChange?: (db: string | null) => void
}) {
  const strip = stripRows(model)
  const scope = db && strip.some((row) => row.target === db) ? db : null
  const day =
    model.day && (scope === null || model.day.target === scope)
      ? model.day
      : null
  const needsYouFirst =
    scope === null &&
    day !== null &&
    (strip.find((row) => row.target === day.target)?.act_now ?? 0) > 0
  const today = scope
    ? model.today.filter((entry) => entry.link.target === scope)
    : model.today
  const signIn =
    useSignInHeld(
      strip.filter((row) => row.state === 'ai_paused').map((row) => row.target)
    ).size > 0
  return (
    <>
      <PageBar
        title="Home"
        meta={<span>{longDate(model.generated_at)}</span>}
        actions={
          onDbChange && strip.length >= 2 ? (
            <DatabaseFilter value={scope} onChange={onDbChange} />
          ) : undefined
        }
      />
      <PageContainer className="pt-9 pb-24">
        {isEmptyHome(model) ? (
          <HomeEmpty />
        ) : (
          <>
            <StatusLine
              tone={homeTone(model.status)}
              caption={homeCaption(model.status, strip.length, signIn)}
            >
              <HomeSentence
                status={model.status}
                database={model.databases[0]?.target}
                doFirstOn={doFirstTarget(model)}
                oneDatabase={strip.length <= 1}
              />
            </StatusLine>
            {signIn && <SignInNote className="mt-2" />}
            {day && (
              <DayCard
                day={day}
                now={model.generated_at}
                needsYouFirst={needsYouFirst}
                status={
                  strip.length < 2
                    ? strip.find((row) => row.target === day.target)
                    : undefined
                }
              />
            )}
            {strip.length >= 2 && <AcrossDatabases rows={strip} />}
            <Later
              model={model}
              today={today}
              named={strip.length > 1}
              scoped={scope !== null}
            />
          </>
        )}
      </PageContainer>
    </>
  )
}

export function HomePage() {
  const { db, setDb } = useDbFilter('home')
  const home = useHomeModel(db)
  if (home.data)
    return <HomeView model={home.data} db={db} onDbChange={setDb} />
  return (
    <>
      <PageBar title="Home" />
      <PageContainer className="pt-9 pb-24">
        {home.isPending ? (
          <HomeLoading />
        ) : (
          <ErrorState
            tone="quiet"
            errorClass="rdst-service"
            title="Home could not load"
            message="RDST could not read the monitoring state. No database or query data was changed."
            onRetry={() => void home.refetch()}
            detail={
              home.error instanceof Error ? home.error.message : undefined
            }
          />
        )}
      </PageContainer>
    </>
  )
}
