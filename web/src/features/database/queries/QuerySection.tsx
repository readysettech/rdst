import { Button } from '@rs/ui-new/button'
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownTrigger,
} from '@rs/ui-new/dropdown-rf'
import { EmptyState } from '@rs/ui-new/empty-state'
import { AnimatePresence, LayoutGroup } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  Fragment,
  type ReactNode,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  QueryListErrorState,
  QueryListSkeleton,
} from '@/components/query-list-state'
import {
  fetchRange,
  type LiveRange,
  type LoadModel,
  OTHER_COLOR,
} from '@/features/monitor-kit/charts'
import { fromScroll } from '@/features/monitor-kit/pointerIntent'
import type { JevAssessmentSummary } from '@/lib/api'
import {
  useAnalyzeJobs,
  useEnqueueAnalysis,
  useInbox,
  useMonitorQueries,
  useMonitorQuery,
} from '@/lib/monitor/hooks'
import type {
  AnalyzeQueueEntry,
  HistorySpan,
  MonitorQuery,
} from '@/lib/monitor/types'
import { useQueryRegistry } from '@/lib/useQueryRegistry'
import { plural } from '../../monitor-kit/format'
import { analyzability } from './analyzability'
import { anchorRow, scrollParent } from './anchor'
import { type Exclusion, exclusionSentence, exclusions } from './focusModel'
import { jevRead } from './jev'
import { LegendHeader, LegendRow } from './LegendRow'
import { QueryDetail } from './QueryDetail'
import { QueryToolbar, type TimeFrame } from './QueryToolbar'
import {
  FILTER_LABELS,
  orderedRows,
  type RowFacts,
  recoveryCopy,
  selectQueryList,
  tableCounts,
} from './querySelectors'
import { inboxSignals, queryStatus } from './queryStatus'
import {
  alreadyQueuedNote,
  classificationChip,
  isWrite,
  waitingJobs as jobsByQuery,
  rowActivity,
} from './rowActivity'
import { stableOrder } from './stableOrder'
import { useQueryListUrl } from './useQueryListUrl'

export interface QuerySectionProps {
  target: string
  range: LiveRange
  /** The hero's window (the range, or a zoom inside it). */
  domain: [number, number]
  /** Load per query in the window; rows re-scope to it and take its colors. */
  model: LoadModel | null
  /** 'in the last hour', 'from 2:10 to 2:25 PM'. */
  scopeLabel: string
  /** Band raised in the chart. */
  hoverKey: string | null
  onHoverKey: (key: string | null) => void
  /** Rows the backend already ranked and re-scoped to the window. */
  scopedRows?: MonitorQuery[] | null
  /** The range is the list's first filter; it also scopes the chart above. */
  onRangeChange?: (range: LiveRange) => void
  ranges?: readonly LiveRange[]
  history?: HistorySpan | null
  /** The zoom window as a removable chip in the toolbar. */
  timeFrame?: TimeFrame | null
  /** Sets a custom time frame from the toolbar's picker. */
  onCustomRange?: (window: [number, number]) => void
  /** Opens "Analyze a query": paste SQL, analyze it, and add it to this list. */
  onAnalyzeQuery?: () => void
  /** A small copy of the hero for the sticky bar, once the hero has scrolled away. */
  stickyChart?: ReactNode
}

export const PAGE_SIZE = 10

type JevCarrier = MonitorQuery & {
  jev_assessment?: JevAssessmentSummary | null
}

/** The query as seen inside the window: share, time, calls and series re-scoped. */
export function scopedQuery(
  query: MonitorQuery,
  model: LoadModel | null,
  domain: [number, number]
): MonitorQuery {
  const series = query.series.filter(
    (point) => point.ts >= domain[0] && point.ts <= domain[1]
  )
  if (!model) return { ...query, series }
  const scope = model.scope.get(query.key)
  if (!scope)
    return { ...query, series, db_time_share: 0, db_time_ms: 0, qps: 0 }
  return {
    ...query,
    series,
    db_time_share: scope.share,
    db_time_ms: scope.totalMs,
    qps: scope.qps,
    mean_ms: scope.meanMs ?? query.mean_ms,
  }
}

function scrollToRow(key: string) {
  window.requestAnimationFrame(() => {
    document
      .getElementById(`query-${key}`)
      ?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
  })
}

/**
 * After a page change, bring the list's heading back just under the sticky toolbar
 * when it's out of view. The toolbar itself is sticky, so scrolling it into view
 * would scroll to where it sits unstuck, near the top of the page.
 */
export function revealListTop(section: HTMLElement | null) {
  const heading = section?.querySelector<HTMLElement>(
    '[data-testid=query-list-sentence]'
  )
  if (!section || !heading) return
  const bar = section.querySelector<HTMLElement>('[data-sticky-toolbar]')
  const floor = (bar?.getBoundingClientRect().bottom ?? 0) + 8
  const delta = heading.getBoundingClientRect().top - floor
  if (delta >= 0 && delta < window.innerHeight - floor) return
  const parent = scrollParent(section)
  const options: ScrollToOptions = { top: delta, behavior: 'smooth' }
  if (parent) parent.scrollBy?.(options)
  else window.scrollBy?.(options)
}

function FirstReading() {
  return (
    <EmptyState
      layout="compact"
      icon="observe"
      title="No queries recorded yet"
      body="The first reading lands within a minute. Each query shows up here with its trend as soon as the database runs it."
    />
  )
}

/**
 * The Database page query list, which is also the hero chart's legend: each row's
 * swatch is its band's color, rows follow share of load in the chart's window, and
 * hovering either side raises the other. Rows expand in place to everything about
 * the query (charts, SQL, classification, deep analysis, benchmarks).
 */
export function QuerySection({
  target,
  range,
  domain,
  model,
  scopeLabel,
  hoverKey,
  onHoverKey,
  scopedRows = null,
  onRangeChange,
  ranges,
  history,
  timeFrame = null,
  onCustomRange,
  onAnalyzeQuery,
  stickyChart = null,
}: QuerySectionProps) {
  const url = useQueryListUrl()
  const initialPage = useMonitorQueries(target, fetchRange(range))
  const search = useDeferredValue(url.q)
  const [pageIndex, setPageIndex] = useState(0)
  const serverPaged = (initialPage.data?.total ?? 0) > 500
  const remoteParams = {
    q: [search, url.table].filter(Boolean).join(' '),
    sort:
      url.sort === 'qps'
        ? 'qps'
        : url.sort === 'latency'
          ? 'mean_ms'
          : url.sort === 'change'
            ? 'change'
            : url.sort.startsWith('score_')
              ? url.sort
              : 'db_time_share',
    filter: url.filter,
    keys: url.keys ?? undefined,
    window: timeFrame ? { from: domain[0], to: domain[1] } : undefined,
  }
  const remotePage = useMonitorQueries(target, fetchRange(range), {
    ...remoteParams,
    enabled: serverPaged,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  })
  // A server-paged list asks where the focused query sits under the same filters and sort.
  const located = useMonitorQueries(target, fetchRange(range), {
    ...remoteParams,
    enabled: serverPaged && Boolean(url.focusKey),
    limit: 1,
    offset: 0,
    locate: url.focusKey ?? undefined,
  })
  const locatedAt = located.data?.located
  const page = serverPaged ? remotePage : initialPage
  const scrolledFor = useRef<string | null>(null)

  const linked = useMonitorQuery(
    target,
    serverPaged ? (url.openKey ?? url.focusKey ?? '') : '',
    fetchRange(range),
    timeFrame ? { from: domain[0], to: domain[1] } : undefined
  )
  const rawQueries = useMemo(() => {
    const rows = page.data?.queries
    if (
      !serverPaged ||
      !linked.data ||
      rows?.some((row) => row.key === linked.data?.key) ||
      (linked.data.key !== url.openKey && linked.data.key !== url.focusKey)
    )
      return rows
    return [linked.data, ...(rows ?? [])]
  }, [page.data?.queries, serverPaged, linked.data, url.openKey, url.focusKey])
  const queries = useMemo(
    () =>
      (serverPaged ? null : scopedRows) ??
      rawQueries?.map((query) => scopedQuery(query, model, domain)),
    [serverPaged, scopedRows, rawQueries, model, domain]
  )
  const registryRows = useQueryRegistry(500, target)
  const jevByKey = useMemo(() => {
    const map = new Map<string, JevAssessmentSummary | null | undefined>()
    for (const entry of registryRows.queries)
      map.set(entry.hash, entry.jev_assessment)
    return map
  }, [registryRows.queries])
  // Needs attention and Watch come from open Inbox items, digest included.
  const inbox = useInbox({ state: 'open', target, include_digest: true })
  const signals = useMemo(
    () => inboxSignals(inbox.data?.items, target),
    [inbox.data, target]
  )
  const jobs = useAnalyzeJobs(target)
  const waitingJobs = useMemo(() => {
    return jobsByQuery(jobs.data ?? { jobs: [] })
  }, [jobs.data])
  // Classification per row: the monitor row's assessment, else the Query Library entry.
  const jevFor = useCallback(
    (query: MonitorQuery) =>
      jevRead(
        query,
        (query as JevCarrier).jev_assessment ?? jevByKey.get(query.key) ?? null
      ),
    [jevByKey]
  )
  const factsOf = useMemo(() => {
    const cache = new WeakMap<MonitorQuery, RowFacts>()
    const now = Date.now() / 1000
    return (query: MonitorQuery): RowFacts => {
      const hit = cache.get(query)
      if (hit) return hit
      const facts = {
        status: queryStatus(query, {
          signal: signals.get(query.key),
          gate: analyzability(query),
          job: waitingJobs.get(query.key),
          now,
        }),
        classification: jevFor(query).assessment,
        analyzed: query.last_analysis?.state === 'done',
      }
      cache.set(query, facts)
      return facts
    }
  }, [signals, waitingJobs, jevFor])
  // Writes stay in the chart's totals and can still alert; the list hides them until asked.
  const [writesOn, setWritesOn] = useState(false)
  const writeKeys = useMemo(
    () =>
      new Set(
        (queries ?? [])
          .filter((query) => isWrite(analyzability(query)))
          .map((query) => query.key)
      ),
    [queries]
  )
  const showWrites =
    writesOn || Boolean(url.openKey && writeKeys.has(url.openKey))
  const listed = useMemo(
    () =>
      showWrites || !writeKeys.size
        ? queries
        : queries?.filter((query) => !writeKeys.has(query.key)),
    [queries, showWrites, writeKeys]
  )
  const view = useMemo(() => {
    const selected = selectQueryList(listed ?? [], {
      search,
      filter: url.filter,
      sort: url.sort,
      factsOf,
    })
    return serverPaged
      ? { ...selected, rows: listed ?? [], quiet: [], tail: [] }
      : selected
  }, [serverPaged, listed, search, url.filter, url.sort, factsOf])
  const queriesByKey = useMemo(
    () => new Map((queries ?? []).map((query) => [query.key, query])),
    [queries]
  )
  const { open: openRow, setTab } = url
  const toggle = useCallback(
    (key: string) => {
      anchorRow(key)
      openRow(url.openKey === key ? null : key)
    },
    [openRow, url.openKey]
  )
  const enqueue = useEnqueueAnalysis(target)
  // A click on a query already in line says where it stands, for a few seconds.
  const [clickNote, setClickNote] = useState<{
    key: string
    text: string
  } | null>(null)
  useEffect(() => {
    if (!clickNote) return
    const timer = setTimeout(() => setClickNote(null), 4000)
    return () => clearTimeout(timer)
  }, [clickNote])
  const { mutate: enqueueKey } = enqueue
  const analyzeRow = useCallback(
    (key: string) => {
      enqueueKey(key, {
        onSuccess: (job) => {
          if (job.already_queued)
            setClickNote({ key, text: alreadyQueuedNote(job) })
        },
      })
    },
    [enqueueKey]
  )
  // The target's classifier is at work once any row has an assessment.
  const classifying = useMemo(
    () => (queries ?? []).some((query) => query.assessment != null),
    [queries]
  )

  const bandKeys = useMemo(
    () =>
      new Set(
        (model?.bands ?? [])
          .filter((band) => !band.isOther)
          .map((band) => band.key)
      ),
    [model]
  )
  const raisedBand = hoverKey && bandKeys.has(hoverKey) ? hoverKey : null

  const narrowed =
    Boolean(search.trim()) ||
    url.filter !== 'all' ||
    Boolean(url.table) ||
    Boolean(url.keys)
  const freshOrdered = useMemo(
    () =>
      serverPaged
        ? (listed ?? [])
        : orderedRows(view, url.sort, url.table, url.keys),
    [serverPaged, listed, view, url.sort, url.table, url.keys]
  )
  // Rows hold their places between readings and re-rank with a margin when one lands,
  // so the list doesn't shuffle while the first readings settle. A new sort, filter or
  // range takes the plain order at once.
  const reading = Math.max(
    history?.last_ts ?? 0,
    initialPage.data?.history?.last_ts ?? 0
  )
  const viewKey = `${range}|${timeFrame ? domain.join('-') : ''}|${search}|${url.filter}|${url.table}|${url.sort}|${url.keys}|${writesOn}`
  const rowSource = scopedRows ?? rawQueries
  const shownOrder = useRef<{
    keys: string[]
    view: string
    reading: number | null
    source: typeof rowSource
  } | null>(null)
  const ordered = useMemo(() => {
    if (serverPaged) return freshOrdered
    const before = shownOrder.current
    const same = before?.view === viewKey
    const rows = stableOrder(
      same ? (before?.keys ?? null) : null,
      freshOrdered,
      url.sort,
      !same || before?.reading !== reading || before?.source !== rowSource,
      undefined,
      url.openKey
    )
    shownOrder.current = {
      keys: rows.map((row) => row.key),
      view: viewKey,
      reading,
      source: rowSource,
    }
    return rows
  }, [
    serverPaged,
    freshOrdered,
    viewKey,
    url.sort,
    reading,
    rowSource,
    url.openKey,
  ])
  const tables = useMemo(() => tableCounts(listed ?? []), [listed])

  // Pages of ten. Any change of window or filters starts again at the first page. The key
  // names the window by its range and zoom, never by its label: a new reading grows the
  // label ("last 4 minutes") and must leave the page, and any open query, where they are.
  const matching = serverPaged ? (page.data?.total ?? 0) : ordered.length
  const pages = Math.max(1, Math.ceil(matching / PAGE_SIZE))
  const filterKey = `${range}|${timeFrame?.label ?? ''}|${search}|${url.filter}|${url.table}|${url.sort}|${url.keys}|${showWrites}`
  useEffect(() => setPageIndex(0), [filterKey])
  // After that reset: a server-paged list opens the page the focused query sits on.
  const locating =
    serverPaged && Boolean(url.focusKey) && !located.isPlaceholderData
  useEffect(() => {
    if (locating && typeof locatedAt === 'number')
      setPageIndex(Math.floor(locatedAt / PAGE_SIZE))
  }, [locating, locatedAt, filterKey])
  const current = Math.min(pageIndex, pages - 1)
  const pageNumbers = [
    ...new Set([
      0,
      ...Array.from({ length: 5 }, (_, index) => current + index - 2).filter(
        (index) => index >= 0 && index < pages
      ),
      pages - 1,
    ]),
  ].sort((a, b) => a - b)
  const first = current * PAGE_SIZE
  const shown = serverPaged ? ordered : ordered.slice(first, first + PAGE_SIZE)
  const sectionRef = useRef<HTMLElement>(null)
  // A new page starts with every row closed.
  const goToPage = (next: number) => {
    if (url.openKey) openRow(null)
    setPageIndex(next)
    window.requestAnimationFrame(() => revealListTop(sectionRef.current))
  }

  // The Analyses list points at a query: open the page it sits on, scroll it into view
  // and highlight it, with filters and sort left alone. When the filters hide it, the
  // notice says which ones and "Show it" clears only those.
  const [highlightKey, setHighlightKey] = useState<string | null>(null)
  const focusedFor = useRef<string | null>(null)
  const focusKey = url.focusKey
  const { clearFocus } = url
  useEffect(() => {
    if (!focusKey) focusedFor.current = null
  }, [focusKey])
  useEffect(() => {
    if (!focusKey || !queries || focusedFor.current === focusKey) return
    if (serverPaged) {
      // Wait for the server to say where it sits, then for that page to load.
      if (
        located.isFetching ||
        located.isPlaceholderData ||
        located.data?.located === undefined
      )
        return
      const at = located.data.located
      if (
        typeof at === 'number' &&
        (Math.floor(at / PAGE_SIZE) !== pageIndex ||
          !page.data?.queries.some((query) => query.key === focusKey))
      )
        return
    }
    const index = ordered.findIndex((query) => query.key === focusKey)
    if (index < 0) return
    focusedFor.current = focusKey
    if (!serverPaged) {
      const target = Math.floor(index / PAGE_SIZE)
      if (target !== pageIndex && url.openKey && url.openKey !== focusKey)
        openRow(null)
      setPageIndex(target)
    }
    setHighlightKey(focusKey)
    window.setTimeout(() => scrollToRow(focusKey), 60)
    clearFocus()
  }, [
    focusKey,
    queries,
    ordered,
    serverPaged,
    located.isFetching,
    located.isPlaceholderData,
    located.data,
    page.data?.queries,
    pageIndex,
    url.openKey,
    openRow,
    clearFocus,
  ])
  useEffect(() => {
    if (!highlightKey) return
    const timer = window.setTimeout(() => setHighlightKey(null), 3000)
    return () => window.clearTimeout(timer)
  }, [highlightKey])
  const focusQuery = focusKey
    ? (queriesByKey.get(focusKey) ??
      (linked.data?.key === focusKey ? linked.data : undefined))
    : undefined
  const hiddenBy: Exclusion[] | null =
    focusKey && queries && !ordered.some((query) => query.key === focusKey)
      ? focusQuery
        ? exclusions(focusQuery, {
            search,
            filter: url.filter,
            table: url.table,
            keys: url.keys,
            writesHidden: !showWrites,
            isWrite: writeKeys.has(focusKey),
            factsOf,
          })
        : []
      : null
  const showFocused = () => {
    for (const reason of hiddenBy ?? []) {
      if (reason === 'writes') setWritesOn(true)
      if (reason === 'picked') url.clearKeys()
      if (reason === 'table') url.setTable(null)
      if (reason === 'search') url.setQ('')
      if (reason === 'filter') url.setFilter('all')
    }
  }
  // The notice is the answer to the click, so it comes into view like the row would.
  const noticeShown = Boolean(focusKey && hiddenBy)
  const noticeRef = useRef<HTMLOutputElement | null>(null)
  useEffect(() => {
    if (noticeShown)
      noticeRef.current?.scrollIntoView?.({
        block: 'center',
        behavior: 'smooth',
      })
  }, [noticeShown])
  const focusNotice =
    focusKey && hiddenBy ? (
      <output
        ref={noticeRef}
        className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-panel border border-border-primary-soft bg-surface-primary-soft/30 px-3 py-2"
        data-testid="focus-hidden"
      >
        <Text
          level="body-small"
          className="min-w-0 flex-1 text-content-layout-1"
        >
          {hiddenBy.length
            ? exclusionSentence(
                focusQuery?.display_name || 'This query',
                hiddenBy,
                { filter: url.filter, table: url.table }
              )
            : `${focusQuery?.display_name || 'This query'} didn't run ${scopeLabel}.`}
        </Text>
        {hiddenBy.length ? (
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Show it"
            onClick={showFocused}
          />
        ) : timeFrame ? (
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Remove the time frame"
            onClick={timeFrame.onClear}
          />
        ) : null}
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Dismiss"
          onClick={clearFocus}
        />
      </output>
    ) : null

  // A deep link (Inbox, report, chart band) opens the page its row is on and
  // scrolls the row into view once the list has it.
  useEffect(() => {
    const key = url.openKey
    if (!key || !queries || scrolledFor.current === key) return
    if (!queriesByKey.has(key)) return
    const index = ordered.findIndex((query) => query.key === key)
    if (index < 0) return
    scrolledFor.current = key
    if (!serverPaged) setPageIndex(Math.floor(index / PAGE_SIZE))
    scrollToRow(key)
  }, [serverPaged, url.openKey, queries, queriesByKey, ordered])

  const renderRow = (query: MonitorQuery) => {
    const open = url.openKey === query.key
    const gate = analyzability(query)
    const jev = jevFor(query)
    const { status } = factsOf(query)
    const chip = classificationChip(jev.assessment)
    const activity = rowActivity({
      query,
      gate,
      job: waitingJobs.get(query.key),
      classification: jev.assessment,
      classifying,
      note: clickNote?.key === query.key ? clickNote.text : null,
    })
    return (
      <LegendRow
        key={query.key}
        query={query}
        color={model ? model.colorFor(query.key) : OTHER_COLOR}
        status={status}
        activity={activity}
        finding={chip?.label ?? null}
        findingClear={chip?.clear ?? false}
        highlighted={highlightKey === query.key}
        onAnalyze={
          gate.ok && query.last_analysis?.state !== 'done'
            ? () => analyzeRow(query.key)
            : undefined
        }
        onOpenAnalysis={() => openRow(query.key, 'analysis')}
        open={open}
        raised={hoverKey === query.key}
        dimmed={Boolean(raisedBand) && raisedBand !== query.key}
        onToggle={toggle}
        onHover={onHoverKey}
        expansion={
          open ? (
            <QueryDetail
              query={query}
              target={target}
              tab={url.tab}
              range={fetchRange(range)}
              domain={domain}
              jev={jev.assessment}
              jevWaiting={jev.waitingReason}
              onTabChange={setTab}
            />
          ) : undefined
        }
      />
    )
  }

  let body: ReactNode
  if (page.isPending) {
    body = <QueryListSkeleton count={5} />
  } else if (page.isError && !page.data) {
    body = (
      <QueryListErrorState
        title="Queries couldn't be loaded"
        message="RDST couldn't read the monitored queries for this target."
        trustworthy="Monitoring keeps recording in the background."
        error={page.error}
        onRetry={() => void page.refetch()}
      />
    )
  } else if ((!queries || queries.length === 0) && timeFrame && !narrowed) {
    body = (
      <EmptyState
        layout="compact"
        icon="search"
        title="No queries ran in this time frame"
        body={`Nothing was recorded ${scopeLabel}.`}
        action={{
          label: 'Remove the time frame',
          icon: 'close',
          onClick: timeFrame.onClear,
        }}
      />
    )
  } else if ((!queries || queries.length === 0) && !narrowed) {
    body = <FirstReading />
  } else if (queries?.length && !listed?.length) {
    body = (
      <EmptyState
        layout="compact"
        icon="observe"
        title="Only writes so far"
        body="The list hides writes. They still count in the chart above."
        action={{
          label: `Show writes (${writeKeys.size})`,
          onClick: () => setWritesOn(true),
        }}
      />
    )
  } else if (ordered.length === 0) {
    const copy = recoveryCopy({
      filter: url.filter,
      search,
      total: serverPaged
        ? (initialPage.data?.total ?? 0)
        : (listed?.length ?? 0),
      searchedCount:
        serverPaged && !search.trim()
          ? (initialPage.data?.total ?? 0)
          : view.searched.length,
    })
    body = (
      <EmptyState
        layout="compact"
        icon={copy.clear === 'filter' ? 'filter-remove' : 'search'}
        title={
          url.keys && !search.trim() && url.filter === 'all'
            ? 'None of these queries ran in this time frame'
            : url.table && !search.trim()
              ? `No queries on ${url.table}`
              : copy.title
        }
        body={
          url.keys && !search.trim() && url.filter === 'all'
            ? `RDST recorded none of the ${plural(url.keys.length, 'query', 'queries')} from this alert ${scopeLabel}.`
            : copy.body
        }
        action={
          url.keys
            ? {
                label: 'Show every query',
                icon: 'filter-remove',
                onClick: url.clearKeys,
              }
            : url.table
              ? {
                  label: 'All tables',
                  icon: 'filter-remove',
                  onClick: () => url.setTable(null),
                }
              : copy.clear === 'filter'
                ? {
                    label: `Remove '${FILTER_LABELS[url.filter]}'`,
                    icon: 'filter-remove',
                    onClick: () => url.setFilter('all'),
                  }
                : {
                    label: 'Clear search',
                    icon: 'close',
                    onClick: () => url.setQ(''),
                  }
        }
      />
    )
  } else {
    body = (
      <LayoutGroup>
        <VStack
          className="items-stretch gap-2"
          data-testid="monitor-query-list"
          // A hover left on a row that scrolled away clears once the pointer moves.
          onPointerMove={(event) => {
            if (
              hoverKey &&
              !fromScroll(event) &&
              !(event.target as HTMLElement).closest('[data-query-hash]')
            )
              onHoverKey(null)
          }}
        >
          <LegendHeader />
          {/* A new page or filter swaps the rows at once; within one list a row that
              ranks in fades in. */}
          <AnimatePresence initial={false} key={`${current}|${filterKey}`}>
            {shown.map(renderRow)}
          </AnimatePresence>
          {pages > 1 ? (
            <nav
              aria-label="Query pages"
              className="flex flex-wrap items-center justify-between gap-2 pt-2"
              data-testid="query-pages"
            >
              <Text
                level="caption"
                className="tabular-nums text-content-layout-3"
              >
                {first + 1} to {first + shown.length} of {matching}
              </Text>
              <HStack className="items-center gap-1">
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  icon="chevron-left"
                  iconPosition="left"
                  label="Previous"
                  disabled={current === 0}
                  onClick={() => goToPage(current - 1)}
                />
                {pageNumbers.map((index, position) => (
                  <Fragment key={index}>
                    {position > 0 && index - pageNumbers[position - 1] > 1 ? (
                      <Text level="caption" aria-hidden="true">
                        ...
                      </Text>
                    ) : null}
                    <Button
                      variant="primary"
                      modifier={index === current ? 'outline' : 'ghost'}
                      size="small"
                      label={String(index + 1)}
                      aria-label={`Page ${index + 1}`}
                      aria-current={index === current ? 'page' : undefined}
                      onClick={() => goToPage(index)}
                      className="min-w-8 tabular-nums"
                    />
                  </Fragment>
                ))}
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  icon="chevron-right"
                  iconPosition="right"
                  label="Next"
                  disabled={current >= pages - 1}
                  onClick={() => goToPage(current + 1)}
                />
              </HStack>
            </nav>
          ) : null}
        </VStack>
      </LayoutGroup>
    )
  }

  return (
    <section
      ref={sectionRef}
      aria-label="Queries"
      className="flex flex-col gap-4"
      data-testid="query-section"
    >
      <div
        data-sticky-toolbar
        className="sticky top-0 z-20 -mx-2 space-y-2 rounded-b-card bg-surface-layout-2/95 px-2 pt-2 pb-2 backdrop-blur"
      >
        {/* Hangs below the bar instead of growing it, so the list never shifts
            when the hero scrolls away. */}
        {stickyChart ? (
          <div className="mk-fade absolute inset-x-2 top-full hidden pt-1 tablet:block">
            {stickyChart}
          </div>
        ) : null}
        {narrowed ||
        (initialPage.data?.total ?? 0) > 0 ||
        (queries?.length ?? 0) > 0 ? (
          <QueryToolbar
            range={range}
            onRangeChange={onRangeChange}
            ranges={ranges}
            history={history}
            timeFrame={timeFrame}
            domain={domain}
            onCustomRange={onCustomRange}
            search={url.q}
            onSearchChange={url.setQ}
            filter={url.filter}
            onFilterChange={url.setFilter}
            sort={url.sort}
            onSortChange={url.setSort}
            counts={view.counts}
            tables={tables}
            table={url.table}
            onTableChange={url.setTable}
            picked={url.keys}
            onClearPicked={url.clearKeys}
          />
        ) : null}
      </div>
      <HStack className="flex-wrap items-baseline gap-x-2 gap-y-1">
        <Text
          as="h2"
          level="subtitle-1"
          className="text-content-layout-1"
          data-testid="query-list-sentence"
        >
          Queries
        </Text>
        {page.isFetching && !page.isPending ? (
          <span className="sr-only" aria-live="polite">
            Updating queries
          </span>
        ) : null}
        <AnalyzedRecently
          jobs={jobs.data?.queue?.recent}
          shownKeys={shown.map((query) => query.key)}
          onShow={url.showQuery}
        />
        {writeKeys.size ? (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label={
              showWrites
                ? `Hide writes (${writeKeys.size})`
                : `Show writes (${writeKeys.size})`
            }
            aria-pressed={showWrites}
            onClick={() => setWritesOn(!showWrites)}
            data-testid="writes-toggle"
            className="self-center"
          />
        ) : null}
        {onAnalyzeQuery ? (
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            icon="speedometer"
            iconPosition="left"
            label="Analyze a query"
            onClick={onAnalyzeQuery}
            className="ml-auto self-center"
          />
        ) : null}
      </HStack>
      {focusNotice}
      {body}
      {page.isError && page.data ? (
        <HStack className="items-center justify-center gap-2">
          <Text level="caption" className="text-content-warning-soft">
            Showing the last readings. The latest refresh failed.
          </Text>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Try again"
            onClick={() => void page.refetch()}
          />
        </HStack>
      ) : null}
    </section>
  )
}

/** How long a finished analysis stays in "Analyzed recently". */
const RECENT_ANALYSIS_S = 30 * 60

/**
 * Analyses that finished in the last half hour on queries this page doesn't show (a
 * reading moved them): one click brings each into view. Hidden when they're all here.
 */
function AnalyzedRecently({
  jobs,
  shownKeys,
  onShow,
}: {
  jobs: AnalyzeQueueEntry[] | undefined
  shownKeys: string[]
  onShow: (key: string) => void
}) {
  const now = Date.now() / 1000
  const shown = new Set(shownKeys)
  const seen = new Set<string>()
  const offPage = (jobs ?? []).filter((job) => {
    const fresh =
      job.state === 'done' &&
      job.finished_at != null &&
      now - job.finished_at < RECENT_ANALYSIS_S
    if (!fresh || shown.has(job.query_key) || seen.has(job.query_key))
      return false
    seen.add(job.query_key)
    return true
  })
  if (!offPage.length) return null
  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label={`Analyzed recently (${offPage.length})`}
          icon="chevron-down"
          iconPosition="right"
          className="self-center"
          data-testid="analyzed-recently"
        />
      </DropdownTrigger>
      <DropdownContent
        align="start"
        className="max-h-80 min-w-64 overflow-y-auto"
      >
        {offPage.map((job) => (
          <DropdownItem
            key={job.query_key}
            onSelect={() => onShow(job.query_key)}
          >
            <span className="min-w-0 truncate">{job.display_name}</span>
          </DropdownItem>
        ))}
      </DropdownContent>
    </Dropdown>
  )
}
