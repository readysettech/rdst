// The Database page (DESIGN_V4): one question, "what is my database spending its time
// on, and what should I fix first?". The header carries identity and the one live
// pulse. Actions lists the few changes worth making, by kind, with the whole index plan;
// Queries is the load-by-query hero with the query list as its legend; Reports holds the
// daily reports. Switching target crossfades the content; per-target query caches make
// switching back instant.
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DatabaseSwitcher } from '@/components/DatabaseSwitcher'
import { QuerySection } from '@/features/database/queries'
import { ReportView } from '@/features/database/reports'
import { InboxContextBanner, incidentWindow } from '@/features/inbox'
import { QueryContextBanner } from '@/features/inbox/InboxContextBanner'
import {
  bandColors,
  buildOverlay,
  ChartHighlightContext,
  clampRange,
  domainFor,
  fetchRange,
  LIVE_RANGES,
  type LiveRange,
  loadFromBreakdown,
  loadFromQueries,
  pickHighlight,
  RANGE_SECONDS,
  rangePhrase,
} from '@/features/monitor-kit/charts'
import {
  DatabaseGlyph,
  type GlyphTone,
} from '@/features/monitor-kit/DatabaseGlyph'
import { useMonitorPulse } from '@/features/monitor-kit/useMonitorPulse'
import { useTargetResolution } from '@/hooks/useTarget'
import { useActions, useIndexPlan } from '@/lib/monitor/actions'
import {
  useInboxItem,
  useLoad,
  useMonitorHealth,
  useMonitorQueries,
  useMonitorQueriesIn,
  useMonitorQuery,
  useMonitorSettings,
  useMonitorWork,
  useReport,
} from '@/lib/monitor/hooks'
import { useSystemStatus } from '@/lib/useSystemStatus'
import {
  ActionsTab,
  buildingFirstPlan,
  changeCount,
} from './actions/ActionsTab'
import { FirstPlanPointer } from './actions/FirstPlanPointer'
import { AnalyzeQueryDialog } from './analyze/AnalyzeQueryDialog'
import { DatabaseBenchmarksSection } from './benchmark/DatabaseBenchmarksSection'
import { DatabaseHeader, useSignInNeeded } from './overview/DatabaseHeader'
import {
  FirstRunStrip,
  MeasuringQueries,
  useFirstRun,
  WaitingForQueries,
} from './overview/FirstReadings'
import { firstRunDomain, showFirstRun } from './overview/firstReadingsModel'
import { overallTone, tileModels } from './overview/healthModel'
import { LoadHero, LoadStrip } from './overview/LoadHero'
import { ReportTeaser } from './overview/ReportTeaser'
import { queryName } from './queries/querySelectors'
import { useQueryListUrl } from './queries/useQueryListUrl'
import { EnableMonitoringDialog } from './settings/EnableMonitoringDialog'
import { MonitoringSettingsSheet } from './settings/MonitoringSettingsSheet'
import { MonitoringSetupDialog } from './settings/MonitoringSetupDialog'
import {
  type DatabaseView as DatabaseTab,
  fitZoom,
  frameLabel,
  useDatabaseUrl,
  windowLabel,
} from './useDatabaseUrl'

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

const SEEN_KEY = 'rdst_report_seen'
const PLAN_SEEN_KEY = 'rdst_actions_seen'

/** Whether this viewer has opened Actions since the first plan arrived (per target). */
function usePlanSeen(target: string, ready: boolean, viewing: boolean) {
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
  useEffect(() => {
    if (!ready || !viewing || seen) return
    try {
      window.localStorage.setItem(key, '1')
    } catch {
      // Storage can be unavailable; the pointer then shows until the next visit.
    }
    setSeen(true)
  }, [key, ready, viewing, seen])
  return seen
}

const STRIP_TITLE = {
  calls: 'Queries per second',
  latency: 'Latency',
  time: 'Time spent',
} as const

/** The next range to try for a linked query the current range doesn't hold. */
/** The smallest page range whose window reaches back to `start`. */
export function rangeHolding(start: number, now: number): LiveRange {
  for (const range of ['1h', '24h', '7d', '30d', '90d'] as const)
    if (now - RANGE_SECONDS[range] <= start) return range
  return 'all'
}

const RANGE_ORDER: readonly LiveRange[] = LIVE_RANGES
/** The ranges the toolbar offers; links can still carry the others. */
const TOOLBAR_RANGES: readonly LiveRange[] = [
  '15m',
  '1h',
  '24h',
  '7d',
  '30d',
  'all',
]

export function widerRange(range: LiveRange): LiveRange | null {
  if (range === 'all') return null
  if (range === '5m' || range === '15m' || range === '1h') return '24h'
  return 'all'
}

const FIRST_RUN_HIDDEN_KEY = 'rdst_first_run_hidden'

/** The first-run strip, hidden by this viewer for this turn-on (a per-viewer convenience). */
function useFirstRunHidden(target: string, enabledAt: number | null) {
  const key = `${FIRST_RUN_HIDDEN_KEY}:${target}`
  const read = useCallback(() => {
    try {
      return window.localStorage.getItem(key)
    } catch {
      return null
    }
  }, [key])
  const [hiddenFor, setHiddenFor] = useState<string | null>(read)
  useEffect(() => setHiddenFor(read()), [read])
  const hide = useCallback(() => {
    const value = String(enabledAt ?? 'first')
    try {
      window.localStorage.setItem(key, value)
    } catch {
      // Storage can be unavailable; the strip then hides until the page reloads.
    }
    setHiddenFor(value)
  }, [key, enabledAt])
  return [hiddenFor === String(enabledAt ?? 'first'), hide] as const
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
  return latestId != null && seen != null
    ? latestId > seen
    : latestId != null && seen == null
}

function DatabaseView({ target }: { target: string }) {
  const page = useDatabaseUrl()
  const list = useQueryListUrl()
  const settings = useMonitorSettings(target)
  const health = useMonitorHealth(
    target,
    fetchRange(page.range),
    page.zoom ? { from: page.zoom[0], to: page.zoom[1] } : undefined
  )
  const queries = useMonitorQueries(target, fetchRange(page.range))
  const pulse = useMonitorPulse(target)
  const signInNeeded = useSignInNeeded(pulse.state)
  // What the Reports tab shows: the nightly reports.
  const latest = useReport(target, 'daily')
  const [hoverKey, setHoverKey] = useState<string | null>(null)
  const [enableOpen, setEnableOpen] = useState(false)
  const [queueFocus, setQueueFocus] = useState(false)
  const data = health.data
  const history = data?.history ?? queries.data?.history
  const { range, setRange, setView, openReport } = page
  // Queries carries the first run; Actions leads once the first plan is in.
  const actions = useActions(target)
  const indexPlan = useIndexPlan(target, true, actions.data?.computed_at).data
  // Actions leads, the first run included: it shows the first plan being built, with the live
  // load beside it. Queries leads only before monitoring has a checkup in sight.
  const firstState = actions.data?.checkup?.state
  const autoView: DatabaseTab =
    (actions.data?.open_count ?? 0) > 0 ||
    firstState === 'first_pass' ||
    (firstState === 'done' && actions.data?.computed_at != null)
      ? 'actions'
      : 'live'
  const view = page.requestedView ?? autoView
  const navigate = useNavigate()
  const showQuery = useCallback(
    (key: string) =>
      void navigate({
        to: '/database',
        search: (prev: Record<string, unknown>) => ({
          ...prev,
          view: 'live',
          focus: key,
        }),
      } as never),
    [navigate]
  )
  // The first hour after turning monitoring on: timed steps above the live chart.
  const firstRunModel = useFirstRun({
    target,
    settings: settings.data,
    history,
    pulse,
    queries: queries.data?.queries,
    nextReadingAt: data?.next_reading_at,
  })

  // Honest ranges: a range beyond the stored history clamps to the largest enabled one.
  useEffect(() => {
    if (!history || health.isPlaceholderData) return
    const clamped = clampRange<LiveRange>(range, history, LIVE_RANGES)
    if (clamped !== range) setRange(clamped)
  }, [history, health.isPlaceholderData, range, setRange])

  // Arriving on a time frame (an Inbox link, a shared zoom) shows every query in it:
  // the Status filter resets once per arrival.
  const { arrive } = page
  const arrivedFor = useRef<string | null>(null)
  useEffect(() => {
    const key = page.fromItem != null ? `from:${page.fromItem}` : ''
    const first = arrivedFor.current === null
    if (key === arrivedFor.current) return
    arrivedFor.current = key
    if ((key || (first && page.zoom)) && list.filter !== 'all')
      arrive({ resetFilter: true })
  }, [page.fromItem, page.zoom, list.filter, arrive])

  const minute = Math.floor(pulse.now / 60) * 60
  // Under five minutes of history, the chart spans the first reading and the next few
  // minutes, so the first points draw at once with room to grow, the minute still
  // filling up included.
  const early = firstRunDomain(history, minute)
  const earlyFrom = early?.[0] ?? null
  const earlyTo = early?.[1] ?? null
  const base = useMemo<[number, number]>(
    () =>
      earlyFrom != null && earlyTo != null
        ? [earlyFrom, earlyTo]
        : domainFor(range, history, Math.max(minute, history?.last_ts ?? 0)),
    [earlyFrom, earlyTo, range, history, minute]
  )
  // In the first hour the minute still filling up draws too, rated over the time up to the
  // latest reading. The first reading only sets the baseline, so rates start from it.
  const { firstAt: readingFrom, lastAt: readingTo } = firstRunModel
  const inFirstRun = showFirstRun(settings.data, history, pulse.now)
  const coverage = useMemo(
    () =>
      inFirstRun && readingTo != null
        ? {
            from: readingFrom,
            until: readingTo,
          }
        : null,
    [inFirstRun, readingFrom, readingTo]
  )
  // A zoom is at least five minutes and anywhere in the recorded history; the range
  // widens to hold it (a time frame from the Inbox can be days old).
  const now = Math.max(minute, history?.last_ts ?? 0)
  const zoom = useMemo(
    () => (page.zoom ? fitZoom(page.zoom, now + 60, history?.first_ts) : null),
    [page.zoom, now, history?.first_ts]
  )
  const domain = useMemo<[number, number]>(() => zoom ?? base, [base, zoom])
  const zoomed = zoom != null

  // The range grows to hold the zoom, as far as the recorded history goes.
  useEffect(() => {
    if (!zoom || !history || health.isPlaceholderData) return
    const needed = clampRange<LiveRange>(
      rangeHolding(zoom[0], now),
      history,
      LIVE_RANGES
    )
    if (RANGE_ORDER.indexOf(needed) > RANGE_ORDER.indexOf(range))
      arrive({ range: needed })
  }, [zoom, history, health.isPlaceholderData, now, range, arrive])
  const onZoom = useCallback(
    (next: [number, number]) =>
      page.setZoom(fitZoom(next, now + 60, history?.first_ts)),
    [page.setZoom, now, history?.first_ts]
  )

  // The backend's breakdown and window-ranked rows when it serves them (finer buckets
  // inside a zoom); otherwise the same model from each query's series.
  const window = zoomed ? { from: domain[0], to: domain[1] } : undefined
  const load = useLoad(target, range, window)
  const rowsIn = useMonitorQueriesIn(target, range, window)
  const served = Boolean(
    load.data && rowsIn.data && !load.isError && !rowsIn.isError
  )
  const model = useMemo(() => {
    if (served && load.data)
      return loadFromBreakdown(load.data, undefined, coverage)
    return queries.data
      ? loadFromQueries(queries.data.queries, domain, 8, queryName, undefined, {
          coverage,
        })
      : null
  }, [served, load.data, queries.data, domain, coverage])
  // Queries/s picks its bands by call count, keeping each query's time-view color.
  // Inside a zoom, the window's own rows carry series fine enough for it.
  const callRows = served ? rowsIn.data?.queries : queries.data?.queries
  const callsModel = useMemo(() => {
    if (!callRows || !model) return null
    return loadFromQueries(callRows, domain, 8, queryName, undefined, {
      rankBy: 'calls',
      pinned: bandColors(model),
      coverage,
    })
  }, [callRows, model, domain, coverage])
  const scopeLabel = zoomed
    ? `from ${windowLabel(domain, now)}`
    : `in the ${rangePhrase(range, history)}`
  // The glyph's color is the database's health: how much the open actions matter, or a vital
  // gone critical.
  const severities = [
    ...(actions.data?.actions ?? []),
    ...(actions.data?.more ?? []),
  ].map((card) => card.severity)
  // Until the first plan, health isn't known yet: the glyph stays neutral.
  const actionTone: GlyphTone =
    actions.data?.computed_at == null
      ? 'neutral'
      : severities.includes('high')
        ? 'negative'
        : severities.includes('medium')
          ? 'warning'
          : 'positive'
  const vitalsTone = useMemo(
    () => (data ? overallTone(tileModels(data)) : 'positive'),
    [data]
  )
  // A vital in warning alone doesn't color it: Actions says whether there's anything to do.
  const tone = vitalsTone === 'negative' ? 'negative' : actionTone
  const work = useMonitorWork().data
  const analyzing = Boolean(
    work?.running.some((job) => job.target === target) ||
      work?.queued.some((job) => job.target === target)
  )
  const checkupState = actions.data?.checkup?.state
  const planReady =
    !buildingFirstPlan(actions.data) && actions.data?.computed_at != null
  const planSeen = usePlanSeen(target, planReady, view === 'actions')

  // A link from the Inbox carries its item: a banner says why you're here and every
  // chart shades the incident window.
  const inboxItem = useInboxItem(page.fromItem)
  const fromItem =
    inboxItem.data?.target === target && page.fromItem != null
      ? inboxItem.data
      : null
  const highlight = useMemo(() => {
    const window = fromItem ? incidentWindow(fromItem, list.openKey) : null
    return window
      ? { start: window.start, end: window.end, label: 'From your Inbox' }
      : null
  }, [fromItem, list.openKey])

  const banner = fromItem ? (
    <InboxContextBanner
      item={fromItem}
      queryKey={list.openKey}
      zoomed={zoomed}
      onShowDay={page.showDay}
      onDismiss={page.clearFrom}
    />
  ) : null
  // An opened query shows the banner at the top of its detail, where the page lands;
  // until its detail is on the page, the banner sits above the chart.
  const [detailBanner, setDetailBanner] = useState(false)
  const queryBanner =
    banner && list.openKey
      ? { queryKey: list.openKey, banner, onShown: setDetailBanner }
      : null

  const latestId = latest.data?.state === 'done' ? latest.data.id : null
  const reportDot = useReportSeen(target, latestId, view === 'report')

  // A link to a query outside the current range (a report opened on 15m) widens the
  // range to the report's day, then to everything, until the row is there.
  const listRows = served ? rowsIn.data?.queries : queries.data?.queries

  // The charts always show the highlighted query: its band, or its own line from its
  // series in the same window (the list's row when it's there, else fetched alone).
  const heroModel = page.metric === 'calls' ? (callsModel ?? model) : model
  const rowsByKey = useMemo(
    () => new Map((listRows ?? []).map((query) => [query.key, query])),
    [listRows]
  )
  const pick = pickHighlight(heroModel, {
    openKey: list.openKey,
    hoverKey,
    hasSeries: (key) => rowsByKey.has(key),
  })
  const overlayKey = pick?.mode === 'overlay' ? pick.key : null
  const missing = overlayKey && !rowsByKey.has(overlayKey) ? overlayKey : ''
  const lone = useMonitorQuery(target, missing, range, window)
  const overlayRow = overlayKey
    ? (rowsByKey.get(overlayKey) ??
      (lone.data?.key === overlayKey ? lone.data : null))
    : null
  const overlay = useMemo(
    () =>
      buildOverlay(
        heroModel,
        page.metric,
        overlayRow
          ? {
              key: overlayRow.key,
              label: queryName(overlayRow),
              series: overlayRow.series,
            }
          : null
      ),
    [heroModel, page.metric, overlayRow]
  )
  const widened = useRef<string | null>(null)
  const arriving = useRef<string | null>(null)
  useEffect(() => {
    arriving.current = list.openKey
  }, [list.openKey])
  useEffect(() => {
    const key = list.openKey
    if (!key || arriving.current !== key || !listRows) return
    if (rowsIn.isFetching || queries.isFetching) return
    if (listRows.some((query) => query.key === key)) {
      arriving.current = null
      return
    }
    const next = widerRange(range)
    if (!next || widened.current === `${key}:${next}`) return
    widened.current = `${key}:${next}`
    setRange(next)
  }, [
    list.openKey,
    listRows,
    rowsIn.isFetching,
    queries.isFetching,
    range,
    setRange,
  ])

  // Once the hero scrolls away, a small copy rides in the list's sticky bar.
  const [hero, setHero] = useState<HTMLDivElement | null>(null)
  const [heroGone, setHeroGone] = useState(false)
  useEffect(() => {
    if (!hero || typeof IntersectionObserver === 'undefined') {
      setHeroGone(false)
      return
    }
    const observer = new IntersectionObserver(
      ([entry]) =>
        setHeroGone(!entry.isIntersecting && entry.boundingClientRect.top < 0),
      { threshold: 0, rootMargin: '-120px 0px 0px 0px' }
    )
    observer.observe(hero)
    return () => observer.disconnect()
  }, [hero])
  const showChart = useCallback(
    () => hero?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' }),
    [hero]
  )

  const showSkeleton = useDelayed(health.isPending || settings.isPending)
  const enabled = settings.data?.enabled ?? true
  const neverRecorded = Boolean(data) && !history?.first_ts
  const [firstRunHidden, hideFirstRun] = useFirstRunHidden(
    target,
    settings.data?.first_hour_started_at ?? null
  )
  const firstRun = inFirstRun && !firstRunHidden
  // Readings are in and nothing has run yet: the waiting card stands in for chart and list.
  const waiting =
    enabled && firstRunModel.waitingForQueries && !zoomed && !queries.isError
  // Queries known, none measured yet: the list waits for the reading that ranks it.
  const measuring =
    enabled &&
    inFirstRun &&
    firstRunModel.measuring &&
    !zoomed &&
    !queries.isError
  const checkAgain = () => {
    void health.refetch()
    void settings.refetch()
  }
  // A query picked in the chart opens its row and brings it into view.
  const selectBand = useCallback(
    (key: string) => {
      list.open(key)
      globalThis.requestAnimationFrame(() =>
        document
          .getElementById(`query-${key}`)
          ?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
      )
    },
    [list]
  )

  // A query analyzed from "Analyze a query" opens once the dialog has left the URL,
  // so the two URL writes don't race.
  const [showKey, setShowKey] = useState<string | null>(null)
  const panelOpen = page.panel === 'analyze'
  useEffect(() => {
    if (!showKey || panelOpen) return
    setShowKey(null)
    selectBand(showKey)
  }, [showKey, panelOpen, selectBand])

  if ((health.isPending || settings.isPending) && !data)
    return showSkeleton ? <PageSkeleton /> : <div className="min-h-96" />

  return (
    <div className="space-y-5 pb-12">
      <DatabaseHeader
        target={target}
        health={data}
        settings={settings.data}
        pulse={pulse}
        glyphTone={tone}
        view={view}
        onViewChange={setView}
        teaser={
          view === 'live' ? (
            <ReportTeaser
              target={target}
              onOpen={() => setView('report')}
              className="max-w-xl"
            />
          ) : null
        }
        reportDot={reportDot}
        openActions={changeCount(actions.data, indexPlan)}
        actionsLive={
          checkupState === 'first_pass' ||
          (planReady && !planSeen && changeCount(actions.data) > 0)
        }
        queriesLive={analyzing}
        switcher={<DatabaseSwitcher />}
        signInNeeded={signInNeeded}
        onOpenSettings={() => page.setPanel('settings')}
        onEnable={() => setEnableOpen(true)}
        onCheckAgain={checkAgain}
      />

      <AnimatePresence mode="wait" initial={false}>
        <m.div
          key={view}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1, transition: { duration: 0.2 } }}
          exit={{ opacity: 0, transition: { duration: 0.12 } }}
          className="space-y-5"
        >
          {view === 'report' ? (
            <ReportView
              target={target}
              reportId={page.report}
              onShowLatest={() => openReport('latest')}
              onPick={(id) => openReport(id)}
              onOpenActions={() => setView('actions')}
            />
          ) : !enabled && neverRecorded ? (
            <div className="rounded-card border border-border-layout-1 bg-surface-layout-1">
              <EmptyState
                media={<DatabaseGlyph size={96} tone="neutral" />}
                title={`Turn on monitoring for ${target}`}
                body="RDST reads query statistics every minute, spots new and changed queries, and runs a nightly checkup. It only reads."
                action={{
                  label: 'Turn on monitoring',
                  onClick: () => setEnableOpen(true),
                }}
              />
            </div>
          ) : view === 'actions' ? (
            <ActionsTab
              target={target}
              section={page.section}
              onSectionChange={page.setSection}
              onShowQuery={showQuery}
              onOpenQueries={() => setView('live')}
              liveCard={
                model ? (
                  <LoadStrip
                    model={model}
                    metric="calls"
                    domain={domain}
                    res={model.res ?? data?.res ?? 60}
                    title="Queries per second"
                    hoverKey={hoverKey}
                    onHoverKey={setHoverKey}
                    focusKey={null}
                    overlay={null}
                    onSelectKey={showQuery}
                    onShowChart={() => setView('live')}
                  />
                ) : (
                  <Text
                    as="p"
                    level="body-small"
                    className="text-content-layout-3"
                  >
                    Waiting for the first reading of your queries.
                  </Text>
                )
              }
            />
          ) : (
            <ChartHighlightContext.Provider value={highlight}>
              <QueryContextBanner.Provider value={queryBanner}>
                <ReportTeaser
                  target={target}
                  onOpen={() => setView('report')}
                  className="laptop:hidden"
                />
                {!(queryBanner && detailBanner) && banner}
                <FirstPlanPointer
                  page={actions.data}
                  seen={planSeen}
                  onOpenActions={() => setView('actions')}
                />
                {firstRun || waiting || measuring ? (
                  <FirstRunStrip
                    model={firstRunModel}
                    now={pulse.now}
                    onHide={waiting ? undefined : hideFirstRun}
                    onOpenQueue={() => {
                      setQueueFocus(true)
                      page.setPanel('settings')
                    }}
                  />
                ) : null}
                {waiting ? (
                  <WaitingForQueries
                    target={target}
                    model={firstRunModel}
                    now={pulse.now}
                  />
                ) : health.isError && !data ? (
                  <ErrorState
                    title="Health readings didn't load"
                    message="RDST couldn't read this target's health history. Queries below may still load."
                    onRetry={() => void health.refetch()}
                  />
                ) : (
                  <div ref={setHero} className="scroll-mt-4">
                    <LoadHero
                      model={model}
                      callsModel={callsModel}
                      health={data}
                      loading={queries.isPending}
                      domain={domain}
                      scopeLabel={scopeLabel}
                      zoomed={zoomed}
                      onZoom={onZoom}
                      onResetZoom={() => page.setZoom(null)}
                      metric={page.metric}
                      onMetricChange={page.setMetric}
                      panel={page.health}
                      onPanelChange={page.setHealth}
                      hoverKey={hoverKey}
                      onHoverKey={setHoverKey}
                      focusKey={list.openKey}
                      overlay={overlay}
                      onSelectKey={selectBand}
                    />
                  </div>
                )}
                {waiting ? null : measuring ? (
                  <MeasuringQueries />
                ) : (
                  <QuerySection
                    target={target}
                    range={range}
                    domain={domain}
                    model={model}
                    scopeLabel={scopeLabel}
                    hoverKey={hoverKey}
                    onHoverKey={setHoverKey}
                    scopedRows={served ? (rowsIn.data?.queries ?? null) : null}
                    onRangeChange={setRange}
                    ranges={TOOLBAR_RANGES}
                    history={history}
                    timeFrame={
                      zoomed
                        ? {
                            label: frameLabel(domain, highlight, now),
                            fromInbox: page.fromItem != null,
                            onClear: () => page.setZoom(null),
                          }
                        : null
                    }
                    onCustomRange={onZoom}
                    onAnalyzeQuery={() => page.setPanel('analyze')}
                    stickyChart={
                      heroGone && !page.health ? (
                        <LoadStrip
                          model={
                            page.metric === 'calls'
                              ? (callsModel ?? model)
                              : model
                          }
                          metric={page.metric}
                          domain={domain}
                          res={model?.res ?? data?.res ?? 60}
                          title={`${STRIP_TITLE[page.metric]}, ${scopeLabel.replace(/^in the /, '')}`}
                          hoverKey={hoverKey}
                          onHoverKey={setHoverKey}
                          focusKey={list.openKey}
                          overlay={overlay}
                          onSelectKey={selectBand}
                          onShowChart={showChart}
                        />
                      ) : null
                    }
                  />
                )}
                <DatabaseBenchmarksSection
                  target={target}
                  queries={queries.data?.queries}
                />
              </QueryContextBanner.Provider>
            </ChartHighlightContext.Provider>
          )}
        </m.div>
      </AnimatePresence>

      <MonitoringSettingsSheet
        target={target}
        open={page.panel === 'settings'}
        focus={queueFocus ? 'queue' : null}
        onClose={() => {
          setQueueFocus(false)
          page.setPanel(null)
        }}
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
      <AnalyzeQueryDialog
        target={target}
        open={page.panel === 'analyze'}
        onClose={() => page.setPanel(null)}
        onShowQuery={setShowKey}
      />
    </div>
  )
}

function PageSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true">
      <div className="flex items-center gap-4">
        <Skeleton className="h-[72px] w-[72px] rounded-full" />
        <div className="space-y-2">
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-4 w-72" />
        </div>
      </div>
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-[440px] w-full rounded-card" />
      <Skeleton className="h-16 w-full rounded-panel" />
      <Skeleton className="h-16 w-full rounded-panel" />
    </div>
  )
}

export function DatabasePage() {
  const { target, isResolving, isUnavailable, refetch } = useTargetResolution()
  const status = useSystemStatus()
  const navigate = useNavigate()
  const reduceMotion = useReducedMotion()

  if (!target) {
    if (isResolving) return <PageSkeleton />
    if (isUnavailable)
      return (
        <ErrorState
          title="Targets didn't load"
          message="RDST couldn't read the configured targets."
          onRetry={() => void refetch()}
        />
      )
    const hasTargets = (status.data?.targets.length ?? 0) > 0
    return (
      <div className="rounded-card border border-border-layout-1 bg-surface-layout-1">
        <EmptyState
          media={<DatabaseGlyph size={96} tone="neutral" />}
          title="Choose a target to monitor"
          body={
            hasTargets
              ? 'Pick a target in the selector at the top of the sidebar.'
              : 'Add a target and RDST starts watching its queries.'
          }
          action={
            hasTargets
              ? undefined
              : {
                  label: 'Add target',
                  onClick: () => void navigate({ to: '/configure' }),
                }
          }
        />
      </div>
    )
  }

  return (
    <AnimatePresence mode="wait" initial={false}>
      <m.div
        key={target}
        initial={{ opacity: 0, y: reduceMotion ? 0 : 6 }}
        animate={{
          opacity: 1,
          y: 0,
          transition: { duration: 0.2, ease: [0, 0, 0.2, 1] },
        }}
        exit={{
          opacity: 0,
          transition: { duration: 0.15, ease: [0.4, 0, 1, 1] },
        }}
      >
        <DatabaseView target={target} />
      </m.div>
    </AnimatePresence>
  )
}
