// One fetcher per /api/monitor endpoint plus stream helpers. Each checks
// CONTRACT_STATUS: 'live' calls the backend, 'stub' resolves fixtures after a short
// delay (streams replay fixture events). Signatures do not change when an endpoint
// flips, so pages never branch on it.
import {
  type SubscribeOptions,
  subscribeShared,
} from '@/lib/sse/sharedEventSource'
import { type Endpoint, fixtureOverride, usesFixtures } from './contract'
import {
  analyzeJobsFixture,
  asScenario,
  filterInbox,
  fixtureNow,
  healthFixture,
  inboxItems,
  isFreshRecovery,
  muteRulesFixture,
  nextPulseFixture,
  pulsesFixture,
  reportEventsFixture,
  reportFixture,
  reportHistoryFixture,
  seriesFixture,
  settingsFixture,
  usageFixture,
} from './fixtures'
import {
  loadFixture,
  loadLatencyFixture,
  queriesV4Fixture,
  queryAnalysisFixture,
  queryV4Fixture,
} from './fixturesV4'
import { noteServerDate } from './serverClock'
import type {
  AddedQuery,
  AddQueryInput,
  AnalyzeJob,
  AnalyzeJobPage,
  ApiRange,
  CompareRunSummary,
  DatabaseReport,
  DeepAnalysisDigest,
  InboxAction,
  InboxItem,
  InboxPage,
  InboxQuery,
  LatencyBreakdown,
  LoadBreakdown,
  MonitorHealth,
  MonitoringLimit,
  MonitorPulse,
  MonitorPulsePage,
  MonitorQuery,
  MonitorQueryPage,
  MonitorRange,
  MonitorSettings,
  MonitorSettingsUpdate,
  MonitorUsage,
  MonitorWork,
  MuteRule,
  MuteRuleInput,
  MuteRulePage,
  QuerySeries,
  ReportEvent,
  ReportHistory,
  TimeWindow,
} from './types'

export class MonitorApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message)
  }
}

let fixtureDelayMs = 300
let streamStepMs = 350

/** Tests set both to 0. */
export function setFixtureTiming(delayMs: number, streamMs = delayMs) {
  fixtureDelayMs = delayMs
  streamStepMs = streamMs
}

const scenario = () => asScenario(fixtureOverride())

async function fixture<T>(build: () => T): Promise<T> {
  if (fixtureDelayMs > 0)
    await new Promise((resolve) => setTimeout(resolve, fixtureDelayMs))
  return build()
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  noteServerDate(response.headers?.get('date') ?? null)
  if (!response.ok) {
    let message = `Monitor request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      /* keep the generic message */
    }
    throw new MonitorApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

const jsonBody = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

const ROOT = '/api/monitor'
const base = (target: string) => `${ROOT}/targets/${encodeURIComponent(target)}`
const enc = encodeURIComponent

function query(params: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams()
  for (const [name, value] of Object.entries(params))
    if (value !== undefined && value !== '')
      search.set(
        name,
        typeof value === 'boolean' ? (value ? '1' : '0') : String(value)
      )
  const text = search.toString()
  return text ? `?${text}` : ''
}

function pick<T>(
  endpoint: Endpoint,
  live: () => Promise<T>,
  stub: () => T
): Promise<T> {
  return usesFixtures(endpoint) ? fixture(stub) : live()
}

// In-memory state behind stub writes, so a stub round-trip (mute, resolve, cancel)
// reads back what it wrote until the page reloads.
const stubState = {
  muteRules: null as MuteRule[] | null,
  nextRuleId: 100,
  inbox: new Map<number, Partial<InboxItem>>(),
  settings: new Map<string, MonitorSettings>(),
  cancelled: new Set<number>(),
  nextReportId: 61,
  /** Stub runs that replayed to done: id -> finish time. */
  finished: new Map<number, number>(),
}

/** Tests: forget stub writes. */
export function resetStubState() {
  stubState.muteRules = null
  stubState.nextRuleId = 100
  stubState.inbox.clear()
  stubState.settings.clear()
  stubState.cancelled.clear()
  stubState.nextReportId = 61
  stubState.finished.clear()
}

/** A stub run that finished, served like the backend serves a done report. */
function finishedStubReport(id: number, target: string): DatabaseReport | null {
  const ts = stubState.finished.get(id)
  if (ts === undefined) return null
  const body =
    reportFixture(scenario(), 'latest', fixtureNow(), target) ??
    reportFixture('localdev', 'latest', fixtureNow(), target)
  return body && { ...body, id, ts, trigger: 'user', state: 'done' }
}

// 5.1 health

export const getHealth = (
  target: string,
  range: MonitorRange,
  window?: TimeWindow
) =>
  pick<MonitorHealth>(
    'health',
    () =>
      json(
        `${base(target)}/health${query({ range, from: window?.from, to: window?.to })}`
      ),
    () => healthFixture(scenario(), range, fixtureNow(), target)
  )

// 5.2 pulse

export const getPulses = (target: string, limit = 5) =>
  pick<MonitorPulsePage>(
    'pulse',
    () => json(`${base(target)}/pulse${query({ limit })}`),
    () => pulsesFixture(scenario(), fixtureNow(), limit)
  )

export const collectNow = (target: string) =>
  pick<{ ok: boolean; detail: string }>(
    'collectNow',
    () => json(`${base(target)}/collect-now`, { method: 'POST' }),
    () => ({ ok: true, detail: 'Reading complete' })
  )

// 5.3 / 5.4 queries and series

/** `window` (a brushed [from, to]) overrides `range`; rows re-scope to it. */
export const getQueries = (
  target: string,
  range: ApiRange,
  limit = 500,
  window?: TimeWindow,
  options?: {
    offset?: number
    q?: string
    sort?: string
    filter?: string
    keys?: string[]
    /** Report where this key sits in the whole filtered, sorted list (`located`). */
    locate?: string
  }
) =>
  pick<MonitorQueryPage>(
    'queries',
    () =>
      json(
        `${base(target)}/queries` +
          query({
            range,
            limit,
            from: window?.from,
            to: window?.to,
            ...options,
            keys: options?.keys?.join(','),
          })
      ),
    () => queriesV4Fixture(scenario(), range, fixtureNow())
  )

/** One row, the same as in the list, even when the query didn't run in the range. */
export const getQuery = (
  target: string,
  key: string,
  range: ApiRange,
  window?: TimeWindow
) =>
  pick<MonitorQuery>(
    'query',
    () =>
      json(
        `${base(target)}/queries/${enc(key)}` +
          query({ range, from: window?.from, to: window?.to })
      ),
    () => {
      const row = queryV4Fixture(scenario(), key, range, fixtureNow())
      if (!row) throw new MonitorApiError(404, 'Query not found')
      return row
    }
  )

/** The latest finished deep analysis as a card header; analysis_ref is the full record. */
export const getQueryAnalysis = (target: string, key: string) =>
  pick<DeepAnalysisDigest>(
    'queryAnalysis',
    () => json(`${base(target)}/queries/${enc(key)}/analysis`),
    () => {
      const digest = queryAnalysisFixture(scenario(), key, fixtureNow())
      if (!digest)
        throw new MonitorApiError(404, 'No finished analysis for this query')
      return digest
    }
  )

// Load breakdown (DESIGN_V4 section 2)

export const getLoad = (
  target: string,
  range: ApiRange,
  window?: TimeWindow,
  top = 8
) =>
  pick<LoadBreakdown>(
    'load',
    () =>
      json(
        `${base(target)}/load` +
          query({ range, top, from: window?.from, to: window?.to })
      ),
    () => loadFixture(scenario(), range, fixtureNow(), target, top, window)
  )

export const getLoadLatency = (
  target: string,
  range: ApiRange,
  keys: string[],
  window?: TimeWindow
) =>
  pick<LatencyBreakdown>(
    'loadLatency',
    () =>
      json(
        `${base(target)}/load/latency` +
          query({
            range,
            keys: keys.join(','),
            from: window?.from,
            to: window?.to,
          })
      ),
    () =>
      loadLatencyFixture(scenario(), range, fixtureNow(), keys, target, window)
  )

export const getQuerySeries = (
  target: string,
  key: string,
  range: MonitorRange,
  window?: TimeWindow
) =>
  pick<QuerySeries>(
    'series',
    () =>
      json(
        `${base(target)}/queries/${enc(key)}/series${query({ range, from: window?.from, to: window?.to })}`
      ),
    () => seriesFixture(scenario(), key, range, fixtureNow())
  )

// 5.11 analysis jobs

export const getAnalyzeJobs = (target: string, state?: AnalyzeJob['state']) =>
  pick<AnalyzeJobPage>(
    'analyzeJobs',
    () => json(`${base(target)}/analyze-jobs${query({ state })}`),
    () => {
      const page = analyzeJobsFixture(scenario(), fixtureNow())
      return state
        ? { jobs: page.jobs.filter((job) => job.state === state) }
        : page
    }
  )

export const getQueryAnalyzeJobs = (target: string, key: string, limit = 5) =>
  pick<AnalyzeJobPage>(
    'queryAnalyzeJobs',
    () =>
      json(
        `${base(target)}/queries/${enc(key)}/analyze-jobs${query({ limit })}`
      ),
    () => analyzeJobsFixture(scenario(), fixtureNow(), key, limit)
  )

export const enqueueAnalysis = (target: string, key: string) =>
  pick<AnalyzeJob>(
    'enqueueAnalysis',
    () =>
      json(`${base(target)}/queries/${enc(key)}/analyze`, { method: 'POST' }),
    () => {
      const now = fixtureNow()
      return {
        id: 1000 + (now % 1000),
        target,
        query_key: key,
        reason: 'user',
        state: 'queued',
        priority: 1,
        param_source: null,
        mode: null,
        enqueued_at: now,
        started_at: null,
        finished_at: null,
        error: null,
        analysis_ref: null,
        reason_text: null,
      }
    }
  )

/**
 * 5.12: make sure a monitored query has its Query Library row, so Compare against
 * Readyset (useCompareController) can select it. Idempotent.
 */
export const ensureLibraryEntry = (target: string, key: string) =>
  pick<{ hash: string; created: boolean }>(
    'libraryEntry',
    () =>
      json(`${base(target)}/queries/${enc(key)}/library`, { method: 'POST' }),
    () => ({ hash: key, created: false })
  )

/** Put a query analyzed from "Analyze a query" in the list, with its analysis. */
export const addQuery = (target: string, input: AddQueryInput) =>
  pick<AddedQuery>(
    'addQuery',
    () => json(`${base(target)}/queries`, jsonBody('POST', input)),
    () => ({ key: 'pasted', created: true, job: null })
  )

// 5.10 settings and usage

/** Fills any cost-lever field a response leaves out (the backend serves them all). */
export function withSettingsDefaults(
  raw: Partial<MonitorSettings>
): MonitorSettings {
  return {
    ...settingsFixture('localdev'),
    size_bytes: 0,
    budget_bytes: 0,
    ...raw,
  }
}

export const getSettings = (target: string) =>
  pick<MonitorSettings>(
    'settings',
    async () => withSettingsDefaults(await json(`${base(target)}/settings`)),
    () => stubState.settings.get(target) ?? settingsFixture(scenario())
  )

export const putSettings = (target: string, update: MonitorSettingsUpdate) =>
  pick<MonitorSettings>(
    'settings',
    async () =>
      withSettingsDefaults(
        await json(`${base(target)}/settings`, jsonBody('PUT', update))
      ),
    () => {
      const current =
        stubState.settings.get(target) ?? settingsFixture(scenario())
      const next: MonitorSettings = {
        ...current,
        ...update,
        notify_events: current.notify_events && {
          ...current.notify_events,
          ...update.notify_events,
        },
      }
      stubState.settings.set(target, next)
      return next
    }
  )

/** Frees a slot held for a database this computer doesn't have. */
export const releaseMonitoringSlot = (fingerprint: string) =>
  json<MonitoringLimit>(
    `${ROOT}/limit/release`,
    jsonBody('POST', { fingerprint })
  )

/** AI monitoring on N of the account's limit; `target` adds whether that one would fit. */
export const getMonitoringLimit = (target?: string) =>
  pick<MonitoringLimit>(
    'limit',
    () => json(`${ROOT}/limit${query({ target })}`),
    () => ({
      in_use: 1,
      limit: 3,
      own_key: false,
      known: true,
      full: false,
      signed_in: true,
      target_fits: target ? true : null,
      targets: [],
    })
  )

/** Monitoring's real work across every monitored database (GET /api/monitor/work). */
export const getMonitorWork = () =>
  pick<MonitorWork>(
    'work',
    () => json(`${ROOT}/work`),
    () => ({
      running: [],
      queued: [],
      retrying: [],
      done_today: [],
      done_today_count: 0,
      reports: [],
    })
  )

/** Ask again for AI monitoring on a database that's monitored locally. */
export const claimAiMonitoring = (target: string) =>
  pick<MonitorSettings>(
    'aiMonitoring',
    async () =>
      withSettingsDefaults(
        await json(`${base(target)}/ai-monitoring`, { method: 'POST' })
      ),
    () => {
      const next: MonitorSettings = {
        ...(stubState.settings.get(target) ?? settingsFixture(scenario())),
        ai_monitoring: 'on',
      }
      stubState.settings.set(target, next)
      return next
    }
  )

export const getUsage = (target: string, days = 7) =>
  pick<MonitorUsage>(
    'usage',
    () => json(`${base(target)}/usage${query({ days })}`),
    () => usageFixture(scenario(), fixtureNow(), days)
  )

// 5.6 mute rules

function stubRules(): MuteRule[] {
  stubState.muteRules ??= muteRulesFixture(fixtureNow()).rules
  return stubState.muteRules
}

export const getMuteRules = (target?: string) =>
  pick<MuteRulePage>(
    'muteRules',
    () => json(`${ROOT}/mute-rules${query({ target })}`),
    () => {
      const items = stubInbox({
        items: inboxItems(fixtureNow(), scenario()),
      }).items
      return {
        rules: stubRules()
          .filter(
            (rule) => !target || rule.target === null || rule.target === target
          )
          .map((rule) => ({
            ...rule,
            matched_count: items.filter((item) => item.muted_by === rule.id)
              .length,
          })),
      }
    }
  )

export const createMuteRule = (input: MuteRuleInput) =>
  pick<MuteRule>(
    'muteRules',
    () => json(`${ROOT}/mute-rules`, jsonBody('POST', input)),
    () => {
      if (input.query_key === null && input.type === null)
        throw new MonitorApiError(
          422,
          'A mute rule needs a query or an alert type'
        )
      const rule: MuteRule = {
        ...input,
        id: stubState.nextRuleId++,
        created_at: fixtureNow(),
        matched_count: 0,
      }
      stubRules().push(rule)
      // Like the backend, a new rule moves the open items it covers to Muted.
      const matched = stubInbox({
        items: inboxItems(fixtureNow(), scenario()),
      }).items.filter((item) => item.muted_by === rule.id).length
      return { ...rule, matched_count: matched }
    }
  )

export const deleteMuteRule = (id: number) =>
  pick<{ deleted: number }>(
    'muteRules',
    () => json(`${ROOT}/mute-rules/${id}`, { method: 'DELETE' }),
    () => {
      stubState.muteRules = stubRules().filter((rule) => rule.id !== id)
      return { deleted: id }
    }
  )

// 5.7 inbox

function ruleCovers(rule: MuteRule, item: InboxItem): boolean {
  return (
    (rule.target === null || rule.target === item.target) &&
    (rule.type === null || rule.type === item.type) &&
    (rule.query_key === null || item.query_keys.includes(rule.query_key)) &&
    (rule.until === null || rule.until > fixtureNow())
  )
}

function stubInbox(page: InboxPage): InboxPage {
  const rules = stubRules()
  const muted = new Set(rules.map((rule) => rule.id))
  return {
    items: page.items.map((item) => {
      const patched = { ...item, ...stubState.inbox.get(item.id) }
      if (patched.muted_by !== null && !muted.has(patched.muted_by))
        patched.muted_by = null
      const finished =
        patched.state === 'resolved' || patched.state === 'dismissed'
      if (patched.muted_by === null && !finished)
        patched.muted_by =
          rules.find((rule) => ruleCovers(rule, patched))?.id ?? null
      return patched
    }),
  }
}

export const getInbox = (inbox: InboxQuery = {}, signal?: AbortSignal) =>
  pick<InboxPage>(
    'inbox',
    () =>
      json(
        `${ROOT}/inbox${query({ state: inbox.state, target: inbox.target, include_muted: inbox.include_muted, include_digest: inbox.include_digest, incidents: inbox.incidents })}`,
        { signal }
      ),
    () => ({
      items: filterInbox(
        stubInbox({ items: inboxItems(fixtureNow(), scenario()) }).items,
        inbox,
        fixtureNow()
      ),
    })
  )

export const getInboxItem = (id: number, signal?: AbortSignal) =>
  pick<InboxItem>(
    'inbox',
    () => json(`${ROOT}/inbox/${id}`, { signal }),
    () => {
      const item = stubInbox({
        items: inboxItems(fixtureNow(), scenario()),
      }).items.find((candidate) => candidate.id === id)
      if (!item) throw new MonitorApiError(404, 'Inbox item not found')
      return item
    }
  )

const ACTION_STATE: Record<InboxAction, InboxItem['state']> = {
  seen: 'seen',
  snooze: 'snoozed',
  dismiss: 'dismissed',
  resolve: 'resolved',
  reopen: 'new',
}

export const inboxAction = (id: number, action: InboxAction, until?: number) =>
  pick<InboxItem>(
    'inboxAction',
    () =>
      json(
        `${ROOT}/inbox/${id}/${action}`,
        jsonBody('POST', until ? { until } : {})
      ),
    () => {
      const item = inboxItems(fixtureNow(), scenario()).find(
        (candidate) => candidate.id === id
      )
      if (!item) throw new MonitorApiError(404, 'Inbox item not found')
      const now = fixtureNow()
      const current = { ...item, ...stubState.inbox.get(id) }
      if (
        action === 'seen' &&
        (current.state === 'resolved' || current.state === 'dismissed')
      ) {
        // Seeing a closed item acknowledges its recovery; it stays closed.
        const body =
          typeof current.body_json === 'string'
            ? (JSON.parse(current.body_json) as Record<string, unknown>)
            : current.body_json
        stubState.inbox.set(id, {
          ...stubState.inbox.get(id),
          body_json: { ...body, recovery_seen_at: now },
        })
        return { ...item, ...stubState.inbox.get(id) }
      }
      const finished = action === 'resolve' || action === 'dismiss'
      const patch: Partial<InboxItem> = {
        state: ACTION_STATE[action],
        updated_at: now,
        resolved_at: finished ? now : null,
        resolved_by: finished ? 'user' : null,
      }
      stubState.inbox.set(id, { ...stubState.inbox.get(id), ...patch })
      return { ...item, ...stubState.inbox.get(id) }
    }
  )

export const markAllSeen = (target?: string) =>
  pick<{ updated: number }>(
    'inboxAction',
    () =>
      json(`${ROOT}/inbox/mark-all-seen${query({ target })}`, {
        method: 'POST',
      }),
    () => {
      const items = stubInbox({
        items: inboxItems(fixtureNow(), scenario()),
      }).items.filter((item) => !target || item.target === target)
      const fresh = items.filter((item) => item.state === 'new')
      for (const item of fresh)
        stubState.inbox.set(item.id, {
          ...stubState.inbox.get(item.id),
          state: 'seen',
        })
      const recoveries = items.filter((item) =>
        isFreshRecovery(item, fixtureNow())
      )
      for (const item of recoveries) {
        const body =
          typeof item.body_json === 'string'
            ? (JSON.parse(item.body_json) as Record<string, unknown>)
            : item.body_json
        stubState.inbox.set(item.id, {
          ...stubState.inbox.get(item.id),
          body_json: { ...body, recovery_seen_at: fixtureNow() },
        })
      }
      return { updated: fresh.length + recoveries.length }
    }
  )

// 5.8 / 5.9 reports

export const startReport = (target: string) =>
  pick<DatabaseReport>(
    'reportStart',
    () => json(`${base(target)}/report`, { method: 'POST' }),
    () => ({
      id: stubState.nextReportId++,
      target,
      ts: fixtureNow(),
      trigger: 'user',
      state: 'running',
      provisional: false,
      progress: null,
      report_json: null,
      refs: {},
    })
  )

export const cancelReport = (target: string, id: number) =>
  pick<DatabaseReport>(
    'reportCancel',
    () => json(`${base(target)}/report/${id}/cancel`, { method: 'POST' }),
    () => {
      stubState.cancelled.add(id)
      return {
        id,
        target,
        ts: fixtureNow(),
        trigger: 'user',
        state: 'cancelled',
        provisional: false,
        progress: null,
        report_json: null,
        refs: {},
      }
    }
  )

export const getReportHistory = (target: string, limit = 10) =>
  pick<ReportHistory>(
    'reportHistory',
    () => json(`${base(target)}/report/history${query({ limit })}`),
    () => {
      const history = reportHistoryFixture(scenario(), fixtureNow(), limit)
      const template = reportHistoryFixture('localdev', fixtureNow(), 1)
        .reports[0]
      const runs = [...stubState.finished]
        .sort(([a], [b]) => b - a)
        .map(([id, ts]) => ({
          ...template,
          id,
          ts,
          trigger: 'user' as const,
        }))
      return { reports: [...runs, ...history.reports].slice(0, limit) }
    }
  )

/** `id` may be 'latest'. Rejects with MonitorApiError(404) when there is no such report. */
export const getReport = (target: string, id: number | 'latest') =>
  pick<DatabaseReport>(
    'report',
    () => json(`${base(target)}/report/${id}`),
    () => {
      const newest = Math.max(0, ...stubState.finished.keys())
      const report =
        finishedStubReport(id === 'latest' ? newest : id, target) ??
        reportFixture(scenario(), id, fixtureNow(), target)
      if (!report) throw new MonitorApiError(404, 'Report not found')
      return report
    }
  )

// Streams. Each helper returns an unsubscribe function. Live streams use
// EventSource, which reconnects and sends Last-Event-ID on its own.

type Unsubscribe = () => void

function eventSource(
  url: string,
  handlers: Record<string, (data: unknown, id: string) => void>,
  onError?: () => void
): Unsubscribe {
  if (typeof EventSource === 'undefined') return () => {}
  const source = new EventSource(url)
  for (const [name, handle] of Object.entries(handlers))
    source.addEventListener(name, (event) => {
      const message = event as MessageEvent<string>
      try {
        handle(JSON.parse(message.data), message.lastEventId)
      } catch {
        /* keep-alive or partial frame */
      }
    })
  if (onError) source.onerror = onError
  return () => source.close()
}

/** A long-lived stream shared by every tab (one connection per URL, lib/sse). */
function sharedSource(
  url: string,
  handlers: Record<string, (data: unknown, id: string) => void>,
  options?: SubscribeOptions
): Unsubscribe {
  return subscribeShared(
    url,
    Object.keys(handlers),
    (name, raw, id) => {
      try {
        handlers[name]?.(JSON.parse(raw), id)
      } catch {
        /* keep-alive or partial frame */
      }
    },
    options
  )
}

/** 5.2: `event: pulse` on /targets/{t}/stream, after every collector tick. */
export function subscribePulse(
  target: string,
  onPulse: (pulse: MonitorPulse) => void
): Unsubscribe {
  if (!usesFixtures('stream'))
    return sharedSource(`${base(target)}/stream`, {
      pulse: (data) => onPulse(data as MonitorPulse),
    })
  const current = scenario()
  const timer = setInterval(
    () => onPulse(nextPulseFixture(current, fixtureNow())),
    60_000
  )
  return () => clearInterval(timer)
}

export interface InboxStreamEvent {
  kind: 'upsert' | 'notified'
  data: Record<string, unknown>
}

/** Existing /inbox/stream: refetch the inbox on `upsert`; `notified` marks a desktop alert. */
export function subscribeInbox(
  onEvent: (event: InboxStreamEvent) => void
): Unsubscribe {
  if (!usesFixtures('inboxStream'))
    return sharedSource(
      `${ROOT}/inbox/stream`,
      {
        upsert: (data) =>
          onEvent({ kind: 'upsert', data: data as Record<string, unknown> }),
        notified: (data) =>
          onEvent({ kind: 'notified', data: data as Record<string, unknown> }),
      },
      // The sidebar badge and desktop alerts listen in every tab, hidden or not.
      { background: true }
    )
  return () => {}
}

/**
 * 5.8: report progress. Unnamed SSE frames, `data` is one ReportEvent, `id` its
 * sequence number; the server replays from the start. The stub replays the
 * fixture run one event per step and stops early when the run is cancelled.
 */
export function subscribeReportEvents(
  target: string,
  id: number,
  onEvent: (event: ReportEvent) => void,
  onError?: () => void
): Unsubscribe {
  if (!usesFixtures('reportEvents')) {
    let close: Unsubscribe = () => {}
    close = eventSource(
      `${base(target)}/report/${id}/events`,
      {
        message: (data) => {
          const event = data as ReportEvent
          onEvent(event)
          if (event.type === 'run' && event.state !== 'running') close()
        },
      },
      onError
    )
    return () => close()
  }
  const events = reportEventsFixture(fixtureNow())
  let index = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const step = () => {
    if (stubState.cancelled.has(id)) {
      const started = events[0]
      onEvent({
        type: 'run',
        state: 'cancelled',
        started_at: started.type === 'run' ? started.started_at : fixtureNow(),
        elapsed_s: index,
      })
      return
    }
    if (index >= events.length) return
    const event = events[index++]
    // Record the finish before announcing it, so a refetch on "done" finds the report.
    if (event.type === 'run' && event.state === 'done')
      stubState.finished.set(id, fixtureNow())
    onEvent(event)
    timer = setTimeout(step, streamStepMs)
  }
  timer = setTimeout(step, 0)
  return () => clearTimeout(timer)
}

// Compare against Readyset: in-flight and recent runs with real stages (/api/cache)

export const getCompareRuns = (target: string, queryHash?: string) =>
  pick<{ runs: CompareRunSummary[] }>(
    'compareRuns',
    () =>
      json(
        '/api/cache/compare-runs' + query({ target, query_hash: queryHash })
      ),
    () => ({ runs: [] })
  )
