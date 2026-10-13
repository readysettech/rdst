// The Database page: the bar with the radar, the name, the state caption and the engine; the
// Live and Report tabs; four numbers above the chart card and the query list,
// whose changes to do link to this database on the Actions page. The detail pane is a
// second panel in the layout, closed by default, opened by a row, the gear (settings) or a
// benchmark; each panel scrolls on its own.

import { cn } from '@rs/tailwind-base'
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { IconButton } from '@rs/ui-new/icon-button'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Skeleton } from '@rs/ui-new/skeleton'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { Text } from '@rs/ui-new/text'
import { TwoPane } from '@rs/ui-new/two-pane'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useOpenAddDatabase } from '@/components/configure/addDatabase'
import { QuerySection } from '@/features/database/queries'
import {
  ReportView,
  useReportRun,
  useRunCompletion,
} from '@/features/database/reports'
import { MonitoringStatusAction } from '@/features/monitor-kit/AiMonitoringStatus'
import {
  monitoringStatus,
  needsUserAction,
} from '@/features/monitor-kit/aiMonitoring'
import {
  availableRanges,
  chartPauses,
  domainFor,
  fetchRange,
  type LiveRange,
  loadFromBreakdown,
  loadFromQueries,
  RANGE_SECONDS,
} from '@/features/monitor-kit/charts'
import { formatClock } from '@/features/monitor-kit/format'
import { Radar, type RadarState } from '@/features/monitor-kit/Radar'
import { useMonitorPulse } from '@/features/monitor-kit/useMonitorPulse'
import type { SwitcherTone } from '@/features/shared/filters/DatabaseFilter'
import { useTargetResolution } from '@/hooks/useTarget'
import { fetchTargets } from '@/lib/api'
import { actionKeys, useActions, useIndexPlan } from '@/lib/monitor/actions'
import { fixtureOverride } from '@/lib/monitor/contract'
import { asCaScenario, caFixtures } from '@/lib/monitor/fixturesCa'
import {
  useAnalyzeJobs,
  useCancelAnalyzeJob,
  useConfiguredDatabases,
  useEnqueueAnalysis,
  useInbox,
  useLoad,
  useMonitorHealth,
  useMonitoringLimit,
  useMonitorQueriesIn,
  useMonitorQuery,
  useMonitorSettings,
  useReport,
  useReportHistory,
} from '@/lib/monitor/hooks'
import type { MonitorQuery } from '@/lib/monitor/types'
import { useSystemStatus } from '@/lib/useSystemStatus'
import {
  buildingFirstPlan,
  changeCount,
  planSqlFor,
} from './actions/actionsModel'
import { FirstPlanPointer } from './actions/FirstPlanPointer'
import { AnalyzeQueryDialog } from './analyze/AnalyzeQueryDialog'
import { stepsForPhase } from './analyze/analyzingSteps'
import { useFollowAnalyses } from './analyze/useFollowAnalyses'
import { BenchmarkFixesDialog } from './benchmark/BenchmarkFixesDialog'
import { BenchmarkResultPane } from './benchmark/BenchmarkResultPane'
import { queriesWithFixes } from './benchmark/fixBenchmarkModel'
import {
  analysesWaitForSignIn,
  analysisUnderWay,
  barState,
  chartMarkers,
  engineLabel,
  type HealthMetricId,
  healthMetrics,
  markerClock,
  type RowSort,
  sortRows,
} from './databaseModel'
import {
  FirstRunStrip,
  firstRunLabel,
  firstRunWindow,
  MeasuringQueries,
  useFirstRun,
  WaitingForQueries,
} from './overview/FirstReadings'
import { showFirstRun } from './overview/firstReadingsModel'
import { HealthMetrics } from './overview/HealthMetrics'
import { LoadCard } from './overview/LoadCard'
import { QueryDetail } from './queries/QueryDetail'
import { queryName } from './queries/querySelectors'
import { useQueryListUrl } from './queries/useQueryListUrl'
import {
  ConnectionRepair,
  connectionWarning,
} from './settings/ConnectionRepair'
import { EnableMonitoringDialog } from './settings/EnableMonitoringDialog'
import { MonitoringSettingsPane } from './settings/MonitoringSettingsPane'
import { MonitoringSetupDialog } from './settings/MonitoringSetupDialog'
import { type HealthPanel, useDatabaseUrl } from './useDatabaseUrl'

const SEEN_KEY = 'rdst_report_seen'
const PLAN_SEEN_KEY = 'rdst_actions_seen'
const DAY = 86400
const SIGN_IN_NOTE =
  'AI checks need a Readyset sign-in, even with your own key.'
const TOOLBAR_RANGES: readonly LiveRange[] = [
  '15m',
  '1h',
  '24h',
  '7d',
  '30d',
  'all',
]

const HEALTH_TO_PANEL: Record<HealthMetricId, HealthPanel | null> = {
  qps: null,
  connections: 'connections',
  hit: 'hit',
  rollback: 'rollback',
}

const RADAR_BY_TONE: Record<SwitcherTone, RadarState> = {
  live: 'watching',
  warning: 'paused',
  negative: 'unreachable',
  off: 'off',
}

/**
 * The page's clock: the current minute, never behind the last reading. A redesign
 * fixture is frozen on its own timeline, so the page follows that clock instead.
 */
function pageNow(wallClock: number, lastTs: number | null | undefined): number {
  const scenario = asCaScenario(fixtureOverride())
  if (scenario) return caFixtures(scenario).now
  return Math.max(Math.floor(wallClock / 60) * 60, lastTs ?? 0)
}

/** True once `active` has held for `ms` (skeletons never flash on fast loads). */
function useDelayed(active: boolean, ms = 100) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!active) {
      setShown(false)
      return
    }
    const timer = setTimeout(() => setShown(true), ms)
    return () => clearTimeout(timer)
  }, [active, ms])
  return shown
}

/** The newest report this viewer opened, per target (a per-viewer convenience). */
function useReportSeen(
  target: string,
  latestId: number | null,
  viewing: boolean
) {
  const read = useCallback(() => {
    try {
      const raw = window.localStorage.getItem(`${SEEN_KEY}:${target}`)
      return raw ? Number(raw) : null
    } catch {
      return null
    }
  }, [target])
  const [seen, setSeen] = useState<number | null>(read)
  useEffect(() => setSeen(read()), [read])
  useEffect(() => {
    if (!viewing || latestId == null || latestId === seen) return
    try {
      window.localStorage.setItem(`${SEEN_KEY}:${target}`, String(latestId))
    } catch {
      // Storage can be unavailable; the dot then shows until the next visit.
    }
    setSeen(latestId)
  }, [viewing, latestId, seen, target])
  return latestId != null && (seen == null || latestId > seen)
}

/** Whether this viewer has opened Actions since the first plan arrived (per target). */
function usePlanSeen(target: string, ready: boolean) {
  const key = `${PLAN_SEEN_KEY}:${target}`
  const read = useCallback(() => {
    try {
      return window.localStorage.getItem(key) === '1'
    } catch {
      return false
    }
  }, [key])
  const [seen, setSeen] = useState(read)
  useEffect(() => setSeen(read()), [read])
  const markSeen = useCallback(() => {
    if (!ready || seen) return
    try {
      window.localStorage.setItem(key, '1')
    } catch {
      // Storage can be unavailable; the pointer then shows until the next visit.
    }
    setSeen(true)
  }, [key, ready, seen])
  return [seen, markSeen] as const
}

const LIVE_DOT =
  "after:absolute after:top-3 after:-right-2.5 after:h-1.5 after:w-1.5 after:rounded-full after:bg-content-rising-plain after:content-['']"

/** Names a tab's live dot for assistive tech; the dot itself is the tab's ::after. */
function LiveDotLabel({ id, label }: { id: string; label: string }) {
  return (
    <span id={id} data-testid="tab-live-dot" className="sr-only">
      {label}
    </span>
  )
}

interface BenchmarkRun {
  ranAt: number
  keys: string[]
}

function DatabaseView({ target }: { target: string }) {
  const page = useDatabaseUrl()
  const list = useQueryListUrl()
  const settings = useMonitorSettings(target)
  const health = useMonitorHealth(target, fetchRange(page.range))
  const rows = useMonitorQueriesIn(target, page.range)
  // The calls chart stacks the busiest queries; the others rank by execution time.
  const load = useLoad(
    target,
    page.range,
    undefined,
    page.metric === 'calls' ? 'calls' : 'time'
  )
  const pulse = useMonitorPulse(target)
  const run = useReportRun(target)
  // What the Report tab opens on: the newest daily report.
  const latest = useReport(target, 'daily')
  const reports = useReportHistory(target)
  const inbox = useInbox({ target })
  const jobs = useAnalyzeJobs(target)
  const enqueue = useEnqueueAnalysis(target)
  const cancelAnalysis = useCancelAnalyzeJob(target)
  const targets = useQuery({
    queryKey: ['configure-targets-hosts'],
    queryFn: fetchTargets,
    staleTime: 60_000,
  })
  const [hoverKey, setHoverKey] = useState<string | null>(null)
  const [enableOpen, setEnableOpen] = useState(false)
  const [repairOpen, setRepairOpen] = useState(false)
  const [benchmarkOpen, setBenchmarkOpen] = useState(false)
  // The query pane estimates its own fix's gain; the list's button covers every fix.
  const [benchmarkScope, setBenchmarkScope] = useState<string[] | null>(null)
  const [benchmark, setBenchmark] = useState<BenchmarkRun | null>(null)
  const { range, setRange, setView, openReport } = page
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const actions = useActions(target)
  const indexPlan = useIndexPlan(target, true, actions.data?.computed_at).data
  const view = page.requestedView === 'report' ? 'report' : 'live'
  const planReady =
    !buildingFirstPlan(actions.data) && actions.data?.computed_at != null
  const [planSeen, markPlanSeen] = usePlanSeen(target, planReady)
  // This database as the sidebar shows it: its state, and the count Actions shows.
  const configured = useConfiguredDatabases().databases.find(
    (db) => db.name === target
  )
  const planToDo = configured?.toDo
  const openActions = planToDo ?? changeCount(actions.data, indexPlan)
  // This database's slice of the Actions page.
  const goToActions = useCallback(() => {
    markPlanSeen()
    void navigate({ to: '/actions', search: { db: target } } as never)
  }, [markPlanSeen, navigate, target])
  const analyses = useFollowAnalyses(target, jobs.data?.jobs)
  const queriesLive = analyses.active.size > 0
  const signIn = analysesWaitForSignIn(
    actions.data?.checkup,
    rows.data?.queries
  )
  const data = health.data
  const history = data?.history ?? rows.data?.history
  const enabled = settings.data?.enabled ?? true
  const unmonitored = settings.data ? !settings.data.enabled : false
  const outage = data?.outages?.find((entry) => entry.end === null) ?? null
  const unreachable = pulse.state === 'connection_lost' || outage !== null
  const now = pageNow(pulse.now, history?.last_ts)
  const firstRun = enabled && showFirstRun(settings.data, history, now)
  // The first minutes: the steps under way, and until the queries are known and ranked, a
  // card in place of the chart or the list.
  const firstRunModel = useFirstRun({
    target,
    settings: settings.data,
    history,
    pulse,
    queries: rows.data?.queries,
    nextReadingAt: health.data?.next_reading_at,
  })
  const waiting =
    enabled && !unmonitored && firstRunModel.waitingForQueries && !rows.isError
  const measuring =
    firstRun && !unmonitored && firstRunModel.measuring && !rows.isError
  const day = range === 'all' || RANGE_SECONDS[range] >= DAY
  const recorded = history?.span_seconds ?? 0
  // The first minutes keep one window until more than one range of readings is in.
  const firstWindow = firstRun && recorded <= RANGE_SECONDS['15m']

  // The chart spans what it has recorded: a span shorter than the range fills the plot.
  // Before the first reading the first window starts now.
  const chartHistory = load.data?.history ?? history
  const domain = useMemo<[number, number]>(
    () =>
      firstWindow
        ? firstRunWindow(chartHistory?.first_ts ?? now, now)
        : domainFor(range, chartHistory, now),
    [firstWindow, chartHistory, range, now]
  )
  const rangeLabel = firstWindow ? firstRunLabel(domain, now) : undefined
  const model = useMemo(() => {
    if (load.data && !load.isError) return loadFromBreakdown(load.data)
    return rows.data
      ? loadFromQueries(rows.data.queries, domain, 8, queryName)
      : null
  }, [load.data, load.isError, rows.data, domain])
  const markers = useMemo(
    () =>
      chartMarkers({
        health: data,
        items: inbox.data?.items,
        reports: reports.data?.reports,
        domain,
      }),
    [data, inbox.data, reports.data, domain]
  )
  const pauses = useMemo(
    () =>
      model
        ? chartPauses({
            readings: model.xs.filter((_, i) =>
              model.calls.some((row) => row[i] != null)
            ),
            res: model.res,
            domain,
            outages: data?.outages ?? [],
            now,
          })
        : [],
    [model, domain, data?.outages, now]
  )
  const ranges = useMemo(
    () => availableRanges(history, TOOLBAR_RANGES),
    [history]
  )
  const cacheReason =
    latest.data?.day?.tiles.find((tile) => tile.metric === 'cache_hit')
      ?.reason ??
    latest.data?.health?.find((row) => row.check === 'cache')?.detail ??
    null
  const metrics = useMemo(
    () => (data ? healthMetrics(data, { day, cacheReason, firstRun }) : []),
    [data, day, cacheReason, firstRun]
  )
  const selectedMetric: HealthMetricId = page.health ?? 'qps'
  const bar = barState(settings.data, pulse, data, configured?.state)
  // Why AI is off when the user has to act on it, with the way to fix it.
  const aiStatus = monitoringStatus({
    enabled: settings.data?.enabled,
    ai: settings.data?.ai_monitoring,
    reason: signIn ? 'signed_out' : settings.data?.ai_reason,
    since: settings.data?.ai_since,
    now: pulse.now,
  })
  const aiActionNeeded = Boolean(settings.data) && needsUserAction(aiStatus)
  const limit = useMonitoringLimit(undefined, aiActionNeeded)
  const engine = engineLabel(data)
  const lastReading = data?.last_reading_at ?? pulse.clock.lastAt
  const activity = pulse.activity
    ? { ...pulse.activity, last_reading_at: lastReading }
    : lastReading != null
      ? {
          kind: 'reading' as const,
          label: 'Reading query statistics',
          last_reading_at: lastReading,
          every_s: pulse.clock.period,
        }
      : null
  const reason = data?.delay_reason ?? pulse.delayReason ?? null
  const targetDetail = targets.data?.find((entry) => entry.name === target)
  const warning = connectionWarning(
    targetDetail?.host
      ? {
          host: targetDetail.host,
          port: Number(targetDetail.port ?? 0),
          engine: targetDetail.engine ?? data?.engine ?? 'postgresql',
        }
      : null,
    reason
  )

  const latestId = latest.data?.state === 'done' ? latest.data.id : null
  const finishedId = run.status === 'done' ? run.reportId : null
  // Any finished report opens the Report tab: the newest daily one, else the newest run.
  const newestId =
    latestId ??
    (reports.data?.reports ?? []).reduce<{ id: number; ts: number } | null>(
      (best, entry) => (!best || entry.ts > best.ts ? entry : best),
      null
    )?.id ??
    finishedId
  const reportDot = useReportSeen(target, latestId, view === 'report')
  const watchingRun = view === 'report'
  useRunCompletion(target, run, {
    watching: watchingRun,
    onOpen: (id) => openReport(id),
  })
  // A finished run opens by id (the Report tab otherwise shows the daily report) and its
  // findings move Actions.
  const watchingReport = useRef(watchingRun)
  watchingReport.current = watchingRun
  useEffect(() => {
    if (finishedId == null) return
    void queryClient.invalidateQueries({ queryKey: actionKeys.actions(target) })
    if (watchingReport.current) openReport(finishedId)
  }, [finishedId, queryClient, target, openReport])

  // The list's order follows the chart: by share, or by latency with the Latency series.
  const sort: RowSort =
    selectedMetric === 'qps' && page.metric === 'latency' ? 'latency' : 'share'
  const ordered = useMemo(
    () => sortRows(rows.data?.queries ?? [], sort),
    [rows.data, sort]
  )
  const rowByKey = useMemo(
    () => new Map(ordered.map((row) => [row.key, row])),
    [ordered]
  )
  // A linked query outside the list (another range) is fetched on its own.
  const missing =
    list.openKey && !rowByKey.has(list.openKey) ? list.openKey : ''
  const lone = useMonitorQuery(target, missing, range)
  const openQuery: MonitorQuery | null = list.openKey
    ? (rowByKey.get(list.openKey) ??
      (lone.data?.key === list.openKey ? lone.data : null))
    : null
  const openIndex = openQuery ? ordered.indexOf(openQuery) : -1
  const walk = useCallback(
    (step: 1 | -1) => {
      if (!ordered.length) return
      const next =
        openIndex < 0
          ? step > 0
            ? 0
            : ordered.length - 1
          : Math.min(ordered.length - 1, Math.max(0, openIndex + step))
      list.open(ordered[next].key)
    },
    [ordered, openIndex, list]
  )

  const settingsOpen = page.panel === 'settings'
  const paneOpen = settingsOpen || benchmark !== null || openQuery !== null
  // Closing the query pane hands focus back to its row, and again once a list the pane
  // covered shows again.
  const returnFocus = useRef<string | null>(null)
  const closeQuery = useCallback(() => {
    const key = list.openKey
    returnFocus.current = key
    list.open(null)
    if (key) document.getElementById(`query-${key}`)?.focus()
  }, [list])
  useEffect(() => {
    if (paneOpen || !returnFocus.current) return
    document.getElementById(`query-${returnFocus.current}`)?.focus()
    returnFocus.current = null
  }, [paneOpen])
  const closePane = useCallback(() => {
    if (settingsOpen) page.setPanel(null)
    if (benchmark) setBenchmark(null)
    if (list.openKey) closeQuery()
  }, [settingsOpen, page, benchmark, list.openKey, closeQuery])

  // Escape closes the pane; j and k walk the list.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target
      if (
        el instanceof Element &&
        el.closest('input, textarea, [contenteditable="true"], [role="dialog"]')
      )
        return
      if (event.key === 'Escape' && paneOpen) closePane()
      else if (event.key === 'j') walk(1)
      else if (event.key === 'k') walk(-1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [paneOpen, closePane, walk])

  const analyzing = openQuery
    ? analysisUnderWay(openQuery, !unmonitored) ||
      analyses.active.has(openQuery.key) ||
      (enqueue.isPending && enqueue.variables === openQuery.key)
    : false
  const fixRows = useMemo(() => queriesWithFixes(ordered), [ordered])
  const benchmarkRows = useMemo(
    () =>
      benchmark
        ? ordered.filter((row) => benchmark.keys.includes(row.key))
        : [],
    [benchmark, ordered]
  )

  const selectKey = useCallback(
    (key: string) => {
      if (settingsOpen) page.setPanel(null)
      setBenchmark(null)
      list.open(key)
    },
    [settingsOpen, page, list]
  )
  const benchmarkKeys = benchmarkScope ?? fixRows.map((row) => row.key)
  const closeBenchmark = () => {
    setBenchmarkOpen(false)
    setBenchmarkScope(null)
    if (page.panel === 'benchmark') page.setPanel(null)
  }
  const runBenchmark = () => {
    setBenchmarkOpen(false)
    setBenchmarkScope(null)
    if (list.openKey) list.open(null, undefined, { closePanel: true })
    else if (settingsOpen || page.panel === 'benchmark') page.setPanel(null)
    setBenchmark({ ranAt: now, keys: benchmarkKeys })
  }
  const checkAgain = () => {
    void health.refetch()
    void settings.refetch()
  }

  const showSkeleton = useDelayed(health.isPending || settings.isPending)
  const loadingPage = (health.isPending || settings.isPending) && !data

  const tabs =
    (unmonitored && newestId == null) || loadingPage ? undefined : (
      <TabList aria-label="Database views" className="border-b-0">
        <TabItemButton
          layoutPrefix="database"
          label="Live"
          active={view === 'live'}
          onClick={() => setView('live')}
          className={cn('h-10', queriesLive && LIVE_DOT)}
          aria-describedby={queriesLive ? 'database-live-live' : undefined}
        />
        {queriesLive && (
          <LiveDotLabel id="database-live-live" label="Analyses running" />
        )}
        <TabItemButton
          layoutPrefix="database"
          label="Report"
          active={view === 'report'}
          onClick={() =>
            latestId == null && newestId != null
              ? openReport(newestId)
              : setView('report')
          }
          className={cn(
            'h-10',
            reportDot &&
              view !== 'report' &&
              "after:absolute after:top-3 after:-right-2.5 after:h-1.5 after:w-1.5 after:rounded-full after:bg-series-1 after:content-['']"
          )}
          data-report-dot={reportDot && view !== 'report' ? '' : undefined}
        />
      </TabList>
    )

  const pageBar = (
    <PageBar
      title={target}
      leading={
        loadingPage ? undefined : (
          <Radar
            state={RADAR_BY_TONE[bar.tone]}
            activity={activity}
            note={
              bar.tone === 'warning' &&
              (signIn || settings.data?.ai_reason === 'signed_out')
                ? SIGN_IN_NOTE
                : null
            }
            size="large"
            label={`${target}, ${bar.word}`}
          />
        )
      }
      meta={
        loadingPage ? undefined : (
          <>
            <span className="whitespace-nowrap">
              {bar.tone === 'live' ? 'Actively monitoring' : bar.word}
              {bar.since != null && `, since ${formatClock(bar.since)}`}
            </span>
            {aiActionNeeded && aiStatus.headline && (
              <span
                className="whitespace-nowrap text-content-warning-soft"
                data-testid="ai-off-reason"
              >
                {aiStatus.headline}
              </span>
            )}
            {firstRun && history ? (
              <span className="whitespace-nowrap">
                {Math.max(1, Math.round(history.span_seconds / 60))} min
                recorded
              </span>
            ) : (
              engine && <span>{engine}</span>
            )}
          </>
        )
      }
      actions={
        loadingPage || unmonitored ? undefined : (
          <>
            {aiActionNeeded && (
              <MonitoringStatusAction
                target={target}
                status={aiStatus}
                contact={limit.data?.contact}
              />
            )}
            <IconButton
              icon="settings"
              label="Monitoring settings"
              variant="primary"
              modifier="ghost"
              size="small"
              onClick={() =>
                settingsOpen ? page.setPanel(null) : page.setPanel('settings')
              }
            />
          </>
        )
      }
      tabs={tabs}
    />
  )

  const loadCard = (onSelect: (key: string) => void) => (
    <LoadCard
      selected={selectedMetric}
      metric={page.metric}
      onMetricChange={page.setMetric}
      range={range}
      ranges={ranges}
      onRangeChange={setRange}
      rangeLabel={rangeLabel}
      rangeHint={
        rangeLabel
          ? 'More ranges open after 15 minutes of readings.'
          : undefined
      }
      caption={
        unreachable && outage
          ? `no readings since ${markerClock(outage.start, 0)}`
          : (load.data?.recorded_label ?? null)
      }
      model={model}
      health={data}
      domain={domain}
      markers={markers}
      pauses={pauses}
      loading={load.isPending && rows.isPending}
      hoverKey={hoverKey}
      onHoverKey={setHoverKey}
      focusKey={list.openKey}
      onSelectKey={onSelect}
      firstRun={firstRun ? { firstReport: newestId == null } : undefined}
    />
  )

  const unreachableAlert = unreachable && (
    <Alert
      tone="quiet"
      variant="warning"
      className="-mt-3 mb-6 items-center"
      label={
        pulse.connectionProblem
          ? `${pulse.connectionProblem.reason} ${pulse.connectionProblem.fix}`
          : warning
      }
    >
      <Button
        variant="primary"
        modifier="outline"
        size="small"
        label="Fix connection"
        onClick={() => setRepairOpen(true)}
      />
    </Alert>
  )

  const live = (
    <PageContainer className="pt-9 pb-24">
      {unreachableAlert}
      {!unmonitored && !unreachable && (
        <div className="mb-6 empty:hidden">
          <FirstPlanPointer
            page={actions.data}
            seen={planSeen}
            onOpenActions={goToActions}
          />
        </div>
      )}
      {(firstRun || waiting || measuring) && !unreachable && (
        <div className="mb-6">
          <FirstRunStrip model={firstRunModel} now={pulse.now} />
        </div>
      )}
      {unmonitored ? (
        <div className="flex items-center gap-4 border-y border-border-layout-2 py-5">
          <Text as="p" level="body-medium" className="text-content-layout-2">
            Turn on monitoring to see live numbers, fixes and a daily report.
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Turn on monitoring"
            className="ml-auto"
            onClick={() => setEnableOpen(true)}
          />
        </div>
      ) : waiting ? (
        <WaitingForQueries
          target={target}
          model={firstRunModel}
          now={pulse.now}
        />
      ) : health.isError && !data ? (
        <ErrorState
          tone="quiet"
          title="Health readings didn't load"
          message="RDST couldn't read this database's health history. Queries below may still load."
          onRetry={() => void health.refetch()}
        />
      ) : data ? (
        <>
          <HealthMetrics
            metrics={metrics}
            selected={selectedMetric}
            onSelect={(id) => page.setHealth(HEALTH_TO_PANEL[id])}
            compact={paneOpen}
            staleSince={unreachable ? (data.last_reading_at ?? null) : null}
          />
          {loadCard(selectKey)}
        </>
      ) : null}
      {waiting ? null : measuring ? (
        <div className="mt-6">
          <MeasuringQueries />
        </div>
      ) : (
        <QuerySection
          rows={rows.data?.queries}
          loading={rows.isPending}
          error={rows.isError}
          onRetry={() => void rows.refetch()}
          sort={sort}
          openKey={list.openKey}
          onOpen={selectKey}
          hoverKey={hoverKey}
          onHoverKey={setHoverKey}
          paneOpen={paneOpen}
          mode={unmonitored ? 'scan' : firstRun ? 'first_run' : 'live'}
          scanAt={unmonitored ? (rows.data?.history.first_ts ?? null) : null}
          seenTotal={rows.data?.seen_total}
          filters={{ status: page.status, table: page.table, q: page.q }}
          onFiltersChange={page.setFilters}
          onAnalyzeQuery={() => page.setPanel('analyze')}
          onBenchmark={() => setBenchmarkOpen(true)}
          jobs={jobs.data?.jobs}
          aside={
            openActions > 0 &&
            !unmonitored && (
              <Link
                to="/actions"
                search={{ db: target } as never}
                onClick={markPlanSeen}
                className="border-b border-border-layout-2 text-caption text-content-layout-3 hover:text-content-layout-1"
              >
                {openActions} {openActions === 1 ? 'change' : 'changes'} to do
                in Actions
              </Link>
            )
          }
        />
      )}
    </PageContainer>
  )

  const pane = settingsOpen ? (
    <MonitoringSettingsPane
      target={target}
      onClose={() => page.setPanel(null)}
    />
  ) : benchmark ? (
    <BenchmarkResultPane
      target={target}
      queries={benchmarkRows}
      ranAt={benchmark.ranAt}
      now={now}
      onRerun={() => setBenchmarkOpen(true)}
      onClose={() => setBenchmark(null)}
    />
  ) : openQuery ? (
    <QueryDetail
      target={target}
      query={openQuery}
      now={now}
      range={range}
      planSql={planSqlFor(indexPlan, openQuery.key)}
      monitored={!unmonitored}
      job={
        jobs.data?.jobs.find(
          (job) => job.query_key === openQuery.key && job.state === 'queued'
        ) ?? null
      }
      onClose={closeQuery}
      onPrev={openIndex > 0 ? () => walk(-1) : undefined}
      onNext={
        openIndex >= 0 && openIndex < ordered.length - 1
          ? () => walk(1)
          : undefined
      }
      analyzing={
        analyzing
          ? {
              steps: stepsForPhase(openQuery.last_analysis?.phase),
              aboutSeconds: jobs.data?.queue?.average_run_s ?? null,
            }
          : null
      }
      onAnalyzeAgain={() =>
        enqueue.mutate(openQuery.key, {
          onSuccess: () => analyses.follow(openQuery.key),
        })
      }
      onBenchmark={() => {
        setBenchmarkScope([openQuery.key])
        setBenchmarkOpen(true)
      }}
      onCancel={() => {
        if (analyzing) cancelAnalysis.mutate(openQuery.key)
        list.open(null)
      }}
    />
  ) : null

  return (
    <>
      <TwoPane
        open={paneOpen}
        className={cn(
          paneOpen &&
            'max-desktop:[&>div:first-child]:hidden max-desktop:[&>div:last-child]:ml-0 max-desktop:[&>div:last-child]:flex-1',
          // From 1280 px the pane gives up width before the page drops under its minimum.
          paneOpen &&
            'desktop:[&>div:first-child]:min-w-[37.5rem] desktop:[&>div:last-child]:min-w-[25rem] desktop:[&>div:last-child]:shrink'
        )}
        primary={
          <>
            {pageBar}
            {loadingPage ? (
              showSkeleton ? (
                <PageSkeleton />
              ) : (
                <div className="min-h-96" />
              )
            ) : view === 'report' ? (
              <PageContainer className="pt-9 pb-24">
                <ReportView
                  target={target}
                  reportId={page.report}
                  run={run}
                  onShowLatest={() => openReport('latest')}
                  onPick={(id) => openReport(id)}
                  onOpenActions={goToActions}
                />
              </PageContainer>
            ) : (
              live
            )}
          </>
        }
        secondary={pane}
      />

      <MonitoringSetupDialog
        target={target}
        open={page.panel === 'setup'}
        onClose={() => page.setPanel(null)}
      />
      <EnableMonitoringDialog
        target={target}
        open={
          enableOpen ||
          (page.panel === 'enable' && settings.data?.enabled === false)
        }
        onClose={() => {
          setEnableOpen(false)
          if (page.panel === 'enable') page.setPanel(null)
        }}
      />
      <ConnectionRepair
        target={target}
        open={repairOpen}
        onClose={() => setRepairOpen(false)}
        reason={pulse.connectionProblem?.reason ?? reason}
        onSaved={checkAgain}
      />
      <AnalyzeQueryDialog
        target={target}
        open={page.panel === 'analyze'}
        onClose={() => page.setPanel(null)}
        onShowQuery={(key) => list.open(key, undefined, { closePanel: true })}
      />
      <BenchmarkFixesDialog
        count={benchmarkKeys.length}
        title={benchmarkScope ? 'Estimate fix gain' : undefined}
        open={benchmarkOpen || page.panel === 'benchmark'}
        onClose={closeBenchmark}
        onRun={runBenchmark}
      />
    </>
  )
}

function PageSkeleton() {
  return (
    <PageContainer className="space-y-6 pt-9" aria-busy="true">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-[260px] w-full rounded-card" />
      <Skeleton className="h-16 w-full rounded-panel" />
      <Skeleton className="h-16 w-full rounded-panel" />
    </PageContainer>
  )
}

function Shell({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <TwoPane
      open={false}
      primary={
        <>
          <PageBar title={title} />
          <PageContainer className="pt-9 pb-24">{children}</PageContainer>
        </>
      }
      secondary={null}
    />
  )
}

export function DatabasePage() {
  const { target, isResolving, isUnavailable, refetch } = useTargetResolution()
  const status = useSystemStatus()
  const openAddDatabase = useOpenAddDatabase()

  if (!target) {
    if (isResolving)
      return (
        <TwoPane
          open={false}
          primary={
            <>
              <PageBar title="Database" />
              <PageSkeleton />
            </>
          }
          secondary={null}
        />
      )
    if (isUnavailable)
      return (
        <Shell title="Database">
          <ErrorState
            tone="quiet"
            title="Databases didn't load"
            message="RDST couldn't read the configured databases."
            onRetry={() => void refetch()}
          />
        </Shell>
      )
    const hasTargets = (status.data?.targets.length ?? 0) > 0
    return (
      <Shell title="Database">
        <EmptyState
          tone="quiet"
          title={
            hasTargets ? 'Choose a database to monitor' : 'No database yet.'
          }
          body={
            hasTargets ? 'Pick one under Databases in the sidebar.' : undefined
          }
          action={
            hasTargets
              ? undefined
              : {
                  label: 'Add a database',
                  onClick: () => openAddDatabase(),
                }
          }
        />
      </Shell>
    )
  }

  return <DatabaseView key={target} target={target} />
}
