// TanStack Query hooks over ./api. Stream-driven hooks live with their owners
// (useMonitorPulse in features/monitor-kit, useReportRun in features/database/reports,
// useInboxUnread in features/inbox); they build on the subscribe* helpers in ./api.

import { toast } from '@rs/ui-new/use-toast'
import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { useMemo } from 'react'
import { fetchTargets } from '../api'
import { actionKeys, fetchActions } from './actions'
import {
  addQuery,
  benchmarkFixes,
  cancelQueryAnalysis,
  claimAiMonitoring,
  createMuteRule,
  deleteMuteRule,
  enqueueAnalysis,
  getAnalyzeJobs,
  getHealth,
  getHome,
  getInbox,
  getInboxItem,
  getLoad,
  getMonitoringLimit,
  getMonitorWork,
  getMuteRules,
  getPulses,
  getQueries,
  getQuery,
  getQueryAnalyzeJobs,
  getQuerySeries,
  getReport,
  getReportHistory,
  getReportViewPreference,
  getSettings,
  getUsage,
  inboxAction,
  MonitorApiError,
  putReportViewPreference,
  putSettings,
} from './api'
import { monitorKeys } from './keys'
import type {
  AddQueryInput,
  ApiRange,
  DatabaseActivity,
  HomeDatabaseState,
  HomeModel,
  InboxAction,
  InboxQuery,
  LoadRank,
  MonitorRange,
  MonitorSettingsUpdate,
  MuteRuleInput,
  ReportViewMode,
  TimeWindow,
} from './types'

const MINUTE = 60_000

export const useMonitorHealth = (
  target: string,
  range: MonitorRange,
  window?: TimeWindow
) =>
  useQuery({
    queryKey: window
      ? [...monitorKeys.health(target, range), window.from, window.to]
      : monitorKeys.health(target, range),
    queryFn: () => getHealth(target, range, window),
    placeholderData: keepPreviousData,
    refetchInterval: MINUTE,
    enabled: Boolean(target),
  })

export const usePulses = (target: string, limit = 5) =>
  useQuery({
    queryKey: monitorKeys.pulse(target),
    queryFn: () => getPulses(target, limit),
    refetchInterval: 10_000,
    enabled: Boolean(target),
  })

export const useMonitorQueries = (
  target: string,
  range: MonitorRange,
  options?: {
    limit?: number
    offset?: number
    q?: string
    sort?: string
    filter?: string
    keys?: string[]
    locate?: string
    window?: TimeWindow
    enabled?: boolean
  }
) =>
  useQuery({
    queryKey: options
      ? [...monitorKeys.queries(target, range), options]
      : monitorKeys.queries(target, range),
    queryFn: () =>
      getQueries(
        target,
        range,
        options?.limit,
        options?.window,
        options
          ? {
              offset: options.offset,
              q: options.q,
              sort: options.sort,
              filter: options.filter,
              keys: options.keys,
              locate: options.locate,
            }
          : undefined
      ),
    placeholderData: keepPreviousData,
    refetchInterval: MINUTE,
    enabled: Boolean(target) && options?.enabled !== false,
  })

export const useQuerySeries = (
  target: string,
  key: string,
  range: MonitorRange,
  enabled = true,
  window?: TimeWindow
) =>
  useQuery({
    queryKey: window
      ? [...monitorKeys.series(target, key, range), window.from, window.to]
      : monitorKeys.series(target, key, range),
    queryFn: () => getQuerySeries(target, key, range, window),
    placeholderData: keepPreviousData,
    enabled: enabled && Boolean(target && key),
  })

export const useAnalyzeJobs = (target: string) =>
  useQuery({
    queryKey: monitorKeys.analyzeJobs(target),
    queryFn: () => getAnalyzeJobs(target),
    enabled: Boolean(target),
    // Rows show "Analyzing now" / "Queued, 2nd": keep them current while the queue moves.
    refetchInterval: (query) => {
      const queue = query.state.data?.queue
      // Busy, or in the first hour when work arrives within seconds: keep up.
      return queue?.running || queue?.next?.length || queue?.first_hour
        ? 5_000
        : 30_000
    },
  })

export const useQueryAnalyzeJobs = (
  target: string,
  key: string,
  enabled = true
) =>
  useQuery({
    queryKey: monitorKeys.queryAnalyzeJobs(target, key),
    queryFn: () => getQueryAnalyzeJobs(target, key),
    enabled: enabled && Boolean(target && key),
  })

/** The toast for an Analyze click the backend refused. */
export function analysisDidntStart(error: unknown): string {
  return error instanceof Error && error.message
    ? `The analysis didn't start: ${error.message}`
    : "The analysis didn't start. Try again in a moment."
}

export function useEnqueueAnalysis(target: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (key: string) => enqueueAnalysis(target, key),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: monitorKeys.analyzeJobs(target) }),
    onError: (error) =>
      toast({ title: analysisDidntStart(error), variant: 'warning' }),
  })
}

/** Stop a query's analysis; the queue, the list and the row refetch so the row returns
 * to its previous status. */
export function useCancelAnalyzeJob(target: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (key: string) => cancelQueryAnalysis(target, key),
    onSettled: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: monitorKeys.analyzeJobs(target) }),
        client.invalidateQueries({ queryKey: ['monitor', target, 'queries'] }),
        client.invalidateQueries({ queryKey: ['monitor', target, 'query'] }),
      ]),
  })
}

/** Lists a query analyzed from "Analyze a query"; the list and the row refetch. */
export function useAddQuery() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ target, input }: { target: string; input: AddQueryInput }) =>
      addQuery(target, input),
    onSuccess: (_data, { target }) =>
      Promise.all([
        client.invalidateQueries({ queryKey: ['monitor', target, 'queries'] }),
        client.invalidateQueries({ queryKey: ['monitor', target, 'query'] }),
        client.invalidateQueries({ queryKey: monitorKeys.analyzeJobs(target) }),
      ]),
  })
}

export const useMonitorSettings = (target: string) =>
  useQuery({
    queryKey: monitorKeys.settings(target),
    queryFn: () => getSettings(target),
    enabled: Boolean(target),
    // While AI is still starting, look again so a reason (or "on") shows promptly.
    refetchInterval: (query) =>
      query.state.data?.enabled && query.state.data.ai_monitoring === 'pending'
        ? 20_000
        : false,
  })

export function useUpdateMonitorSettings(target: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (update: MonitorSettingsUpdate) => putSettings(target, update),
    onSuccess: (settings, update) => {
      client.setQueryData(monitorKeys.settings(target), settings)
      // Turning monitoring on or off claims or frees an AI monitoring slot.
      if (update.enabled !== undefined)
        void client.invalidateQueries({ queryKey: monitorKeys.limitAll })
      // The sidebar reads each database's state from Home.
      void client.invalidateQueries({
        queryKey: monitorKeys.home().slice(0, 2),
      })
    },
  })
}

/** The account's AI monitoring limit, read from the Key Service through the backend. */
export const useMonitoringLimit = (target?: string, enabled = true) =>
  useQuery({
    queryKey: monitorKeys.limit(target),
    queryFn: () => getMonitoringLimit(target),
    enabled,
    staleTime: 30_000,
  })

/** Monitoring's work across every database: polled while something may be moving. */
export const useMonitorWork = (enabled = true) =>
  useQuery({
    queryKey: monitorKeys.work,
    queryFn: getMonitorWork,
    enabled,
    refetchInterval: (query) =>
      query.state.data &&
      (query.state.data.running.length ||
        query.state.data.queued.length ||
        query.state.data.reports.length)
        ? 5_000
        : 20_000,
  })

/** Home: the status line, the day card, every database and today's log. */
export const useHome = (target?: string, enabled = true) =>
  useQuery({
    queryKey: monitorKeys.home(target),
    queryFn: ({ signal }) => getHome(target, signal),
    enabled,
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })

/** The configured databases, read from /api/configure/targets. */
export const configuredTargetsKey = ['configure-targets-hosts'] as const

/** One configured database as the sidebar and the database filters show it. */
export interface ConfiguredDatabase {
  name: string
  /** Null until Home has read the database. */
  state: HomeDatabaseState | null
  monitored: boolean
  /** Inbox items that need action now. */
  actNow: number
  /** Changes to do in Actions; null until known. */
  toDo: number | null
  /** What monitoring is doing on the database now; null until Home has read it. */
  activity?: DatabaseActivity | null
  /** The first pass of analyses is still building its first plan. */
  building?: boolean
}

/**
 * Every configured database with its state and counts, monitored first, in the order
 * they were configured. `doFirst` counts the Do first changes across databases.
 */
export function useConfiguredDatabases() {
  const targets = useQuery({
    queryKey: configuredTargetsKey,
    queryFn: fetchTargets,
    staleTime: 30_000,
  })
  const home = useHome()
  const model: HomeModel | undefined = home.data
  const names = useMemo(
    () => [
      ...new Set([
        ...(targets.data ?? []).map((target) => target.name),
        ...(model?.databases ?? []).map((row) => row.target),
      ]),
    ],
    [targets.data, model?.databases]
  )
  const homeRows = new Map(model?.databases.map((row) => [row.target, row]))
  const strip = model?.databases_strip
  // W0: until databases_strip lands, each monitored database's open actions stand in
  // for its to-do count.
  const counted = strip
    ? []
    : names.filter((name) => {
        const state = homeRows.get(name)?.state
        return state != null && state !== 'not_monitored'
      })
  const actions = useQueries({
    queries: counted.map((name) => ({
      queryKey: actionKeys.actions(name),
      queryFn: () => fetchActions(name),
      staleTime: 60_000,
      refetchInterval: 60_000,
    })),
  })
  const openByName = new Map(
    counted.map((name, index) => [name, actions[index]?.data?.open_count])
  )
  const stripRows = new Map(strip?.map((row) => [row.target, row]))

  const databases: ConfiguredDatabase[] = names.map((name) => {
    const row = homeRows.get(name)
    const stripRow = stripRows.get(name)
    const state = stripRow?.state ?? row?.state ?? null
    return {
      name,
      state,
      monitored: state !== 'not_monitored',
      actNow: stripRow?.act_now ?? row?.act_now ?? 0,
      toDo: stripRow?.to_do ?? openByName.get(name) ?? null,
      activity: stripRow?.activity ?? null,
      building: stripRow?.building ?? false,
    }
  })
  databases.sort((a, b) => Number(!a.monitored) - Number(!b.monitored))

  const known = databases.filter((db) => db.toDo != null)
  // W0: status.do_first; until then the open actions across databases.
  const doFirst =
    model?.status.do_first ??
    (known.length ? known.reduce((sum, db) => sum + (db.toDo ?? 0), 0) : null)
  return {
    databases,
    doFirst,
    isPending: targets.isPending && home.isPending,
    isError: targets.isError && home.isError,
    /** Both sources answered: a name missing from `databases` is gone. */
    settled: targets.isSuccess && home.isSuccess,
  }
}

/** Before and after for each query with a fix (POST /benchmark-fixes); one result per
 * key set, kept until the pane closes. */
export const useBenchmarkFixes = (
  target: string,
  keys: string[],
  enabled = true
) =>
  useQuery({
    queryKey: monitorKeys.benchmarkFixes(target, keys),
    queryFn: () => benchmarkFixes(target, keys),
    enabled: enabled && Boolean(target) && keys.length > 0,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })

/** Ask again for AI monitoring on a locally monitored database. */
export function useClaimAiMonitoring(target: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => claimAiMonitoring(target),
    onSuccess: (settings) => {
      client.setQueryData(monitorKeys.settings(target), settings)
      void client.invalidateQueries({ queryKey: monitorKeys.limitAll })
    },
  })
}

export const useMonitorUsage = (target: string, days = 7) =>
  useQuery({
    queryKey: monitorKeys.usage(target, days),
    queryFn: () => getUsage(target, days),
    enabled: Boolean(target),
  })

export const useMuteRules = (target?: string) =>
  useQuery({
    queryKey: monitorKeys.muteRules(target),
    queryFn: () => getMuteRules(target),
  })

export function useCreateMuteRule() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: MuteRuleInput) => createMuteRule(input),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: monitorKeys.muteRulesAll }),
        client.invalidateQueries({ queryKey: monitorKeys.inboxAll }),
      ]),
  })
}

export function useDeleteMuteRule() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => deleteMuteRule(id),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: monitorKeys.muteRulesAll }),
        client.invalidateQueries({ queryKey: monitorKeys.inboxAll }),
      ]),
  })
}

export const useInbox = (query: InboxQuery = {}) =>
  useQuery({
    queryKey: monitorKeys.inbox(query),
    // The signal aborts a superseded or abandoned request so it frees its connection.
    queryFn: ({ signal }) => getInbox(query, signal),
    placeholderData: keepPreviousData,
  })

/** One inbox item in any state, for a page opened from it; null skips the read. */
export const useInboxItem = (id: number | null) =>
  useQuery({
    queryKey: monitorKeys.inboxItem(id ?? 0),
    queryFn: ({ signal }) => getInboxItem(id as number, signal),
    enabled: id != null,
    staleTime: 60_000,
  })

export function useInboxAction() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      action,
      until,
    }: {
      id: number
      action: InboxAction
      until?: number
    }) => inboxAction(id, action, until),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: monitorKeys.inboxAll }),
  })
}

export const useReportHistory = (target: string, limit = 10, daily = false) =>
  useQuery({
    queryKey: monitorKeys.reportHistory(target, limit, daily),
    queryFn: () => getReportHistory(target, limit, daily),
    enabled: Boolean(target),
  })

/**
 * One report, or null when there's none (no report yet, or one past the kept history).
 * Switching the view keeps the same report on screen until the other view loads.
 */
export const useReport = (
  target: string,
  id: number | 'latest' | 'daily',
  view: ReportViewMode = 'quick'
) =>
  useQuery({
    queryKey: monitorKeys.report(target, id, view),
    queryFn: async () => {
      try {
        return await getReport(target, id, view)
      } catch (error) {
        if (error instanceof MonitorApiError && error.status === 404)
          return null
        throw error
      }
    },
    enabled: Boolean(target),
    staleTime: MINUTE,
    refetchInterval: id === 'latest' || id === 'daily' ? MINUTE : false,
    retry: (count) => count < 2,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2] === 'reports' &&
      previousQuery.queryKey[1] === target &&
      previousQuery.queryKey[3] === id
        ? previous
        : undefined,
  })

/** The report view preference, quick until someone picks Detailed. */
export const useReportViewPreference = () =>
  useQuery({
    queryKey: monitorKeys.reportView,
    queryFn: getReportViewPreference,
    staleTime: 5 * MINUTE,
  })

/** Writes the report view preference; the switch moves at once and settles on the reply. */
export function useSetReportView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: putReportViewPreference,
    onMutate: async (view: ReportViewMode) => {
      await client.cancelQueries({ queryKey: monitorKeys.reportView })
      const previous = client.getQueryData<ReportViewMode>(
        monitorKeys.reportView
      )
      client.setQueryData(monitorKeys.reportView, view)
      return { previous }
    },
    onError: (_error, _view, context) =>
      client.setQueryData(monitorKeys.reportView, context?.previous ?? 'quick'),
    onSuccess: (view) => client.setQueryData(monitorKeys.reportView, view),
  })
}

// Database page v4: load breakdown, per-query latency, one row, the analysis header.

/** Rows ranked by share in a range or brushed window, with band colors that match useLoad. */
export const useMonitorQueriesIn = (
  target: string,
  range: ApiRange,
  window?: TimeWindow
) =>
  useQuery({
    queryKey: monitorKeys.queriesIn(target, range, window),
    queryFn: () => getQueries(target, range, 500, window),
    placeholderData: keepPreviousData,
    refetchInterval: window ? false : MINUTE,
    enabled: Boolean(target),
  })

/** The load breakdown; `rank` picks the bands by execution time or by calls. */
export const useLoad = (
  target: string,
  range: ApiRange,
  window?: TimeWindow,
  rank: LoadRank = 'time'
) =>
  useQuery({
    queryKey: monitorKeys.load(target, range, window, rank),
    queryFn: () => getLoad(target, range, window, 8, rank),
    placeholderData: keepPreviousData,
    refetchInterval: window ? false : MINUTE,
    enabled: Boolean(target),
  })

export const useMonitorQuery = (
  target: string,
  key: string,
  range: ApiRange,
  window?: TimeWindow
) =>
  useQuery({
    queryKey: monitorKeys.query(target, key, range, window),
    queryFn: () => getQuery(target, key, range, window),
    enabled: Boolean(target && key),
  })
