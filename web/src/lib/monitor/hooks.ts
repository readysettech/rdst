// TanStack Query hooks over ./api. Stream-driven hooks live with their owners
// (useMonitorPulse in features/monitor-kit, useReportRun in features/database/reports,
// useInboxUnread in features/inbox); they build on the subscribe* helpers in ./api.
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  addQuery,
  claimAiMonitoring,
  createMuteRule,
  deleteMuteRule,
  enqueueAnalysis,
  getAnalyzeJobs,
  getHealth,
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
  getSettings,
  getUsage,
  inboxAction,
  MonitorApiError,
  markAllSeen,
  putSettings,
} from './api'
import { monitorKeys } from './keys'
import type {
  AddQueryInput,
  ApiRange,
  InboxAction,
  InboxQuery,
  MonitorRange,
  MonitorSettingsUpdate,
  MuteRuleInput,
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

export function useEnqueueAnalysis(target: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (key: string) => enqueueAnalysis(target, key),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: monitorKeys.analyzeJobs(target) }),
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

export function useMarkAllSeen() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (target?: string) => markAllSeen(target),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: monitorKeys.inboxAll }),
  })
}

export const useReportHistory = (target: string, limit = 10) =>
  useQuery({
    queryKey: monitorKeys.reportHistory(target, limit),
    queryFn: () => getReportHistory(target, limit),
    enabled: Boolean(target),
  })

/** One report, or null when there's none (no report yet, or one past the kept history). */
export const useReport = (target: string, id: number | 'latest') =>
  useQuery({
    queryKey: monitorKeys.report(target, id),
    queryFn: async () => {
      try {
        return await getReport(target, id)
      } catch (error) {
        if (error instanceof MonitorApiError && error.status === 404)
          return null
        throw error
      }
    },
    enabled: Boolean(target),
    staleTime: MINUTE,
    refetchInterval: id === 'latest' ? MINUTE : false,
    retry: (count) => count < 2,
  })

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

export const useLoad = (target: string, range: ApiRange, window?: TimeWindow) =>
  useQuery({
    queryKey: monitorKeys.load(target, range, window),
    queryFn: () => getLoad(target, range, window),
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
