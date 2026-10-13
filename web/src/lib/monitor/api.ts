// One fetcher per /api/monitor endpoint plus stream helpers. Each checks
// CONTRACT_STATUS: 'live' calls the backend, 'stub' resolves fixtures after a short
// delay (streams replay fixture events). Signatures do not change when an endpoint
// flips, so pages never branch on it.
import {
  type SubscribeOptions,
  subscribeShared,
} from '@/lib/sse/sharedEventSource'
import { resetActionStubState } from './actions'
import { type Endpoint, fixtureOverride, usesFixtures } from './contract'
import {
  analyzeJobsFixture,
  asScenario,
  filterInbox,
  fixtureNow,
  healthFixture,
  inboxItems,
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
import { asCaScenario, caBase, caFixtures } from './fixturesCa'
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
  BenchmarkFixesResult,
  CompareRunSummary,
  DatabaseReport,
  DeepAnalysisDigest,
  HomeModel,
  InboxAction,
  InboxItem,
  InboxPage,
  InboxQuery,
  LatencyBreakdown,
  LoadBreakdown,
  LoadRank,
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
  ReportViewMode,
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

const scenario = () => asScenario(caBase(fixtureOverride()))

/** The redesign fixture set when a `ca-*` scenario is selected; its clock is fixed. */
function ca() {
  const name = asCaScenario(fixtureOverride())
  return name ? caFixtures(name) : null
}

const clock = () => ca()?.now ?? fixtureNow()
const allItems = (): InboxItem[] =>
  ca()?.inbox() ?? inboxItems(clock(), scenario())
const baseSettings = (target: string): MonitorSettings =>
  ca()?.settings(target) ?? settingsFixture(scenario())

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
  /** Query keys a stub enqueue put in the queue; a cancel takes them out. */
  analyzing: new Set<string>(),
  reportView: 'quick' as ReportViewMode,
}

/** Tests: forget stub writes. */
export function resetStubState() {
  resetActionStubState()
  stubState.muteRules = null
  stubState.nextRuleId = 100
  stubState.inbox.clear()
  stubState.settings.clear()
  stubState.cancelled.clear()
  stubState.nextReportId = 61
  stubState.finished.clear()
  stubState.analyzing.clear()
  stubState.reportView = 'quick'
}

/** A stub row while its analysis is queued: the status and a queued last_analysis. */
function withAnalyzing<T extends MonitorQuery>(row: T): T {
  if (!stubState.analyzing.has(row.key)) return row
  return {
    ...row,
    status: 'analyzing',
    last_analysis: {
      state: 'queued',
      finished_at: null,
      analysis_ref: null,
      mode: '',
      param_source: null,
      reason: null,
      queue_state: 'today',
      queue_position: 1,
      summary: row.last_analysis?.summary ?? null,
    },
  }
}

/** A stub run that finished, served like the backend serves a done report. */
function finishedStubReport(id: number, target: string): DatabaseReport | null {
  const ts = stubState.finished.get(id)
  if (ts === undefined) return null
  const body =
    ca()?.report(target, 'latest') ??
    reportFixture(scenario(), 'latest', clock(), target) ??
    reportFixture('localdev', 'latest', clock(), target)
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
    () =>
      ca()?.health(target, range) ??
      healthFixture(scenario(), range, clock(), target)
  )

// 5.2 pulse

export const getPulses = (target: string, limit = 5) =>
  pick<MonitorPulsePage>(
    'pulse',
    () => json(`${base(target)}/pulse${query({ limit })}`),
    () =>
      ca()?.pulses(target, limit) ?? pulsesFixture(scenario(), clock(), limit)
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
    () => {
      const page =
        ca()?.queries(target, range, limit, options?.offset ?? 0) ??
        queriesV4Fixture(scenario(), range, clock())
      return { ...page, queries: page.queries.map(withAnalyzing) }
    }
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
      const set = ca()
      const row = set
        ? set.query(target, key)
        : queryV4Fixture(scenario(), key, range, clock())
      if (!row) throw new MonitorApiError(404, 'Query not found')
      return withAnalyzing(row)
    }
  )

/** The latest finished deep analysis as a card header; analysis_ref is the full record. */
export const getQueryAnalysis = (target: string, key: string) =>
  pick<DeepAnalysisDigest>(
    'queryAnalysis',
    () => json(`${base(target)}/queries/${enc(key)}/analysis`),
    () => {
      const set = ca()
      const digest = set
        ? set.queryAnalysis(target, key)
        : queryAnalysisFixture(scenario(), key, clock())
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
  top = 8,
  rank: LoadRank = 'time'
) =>
  pick<LoadBreakdown>(
    'load',
    () =>
      json(
        `${base(target)}/load` +
          query({ range, top, from: window?.from, to: window?.to, rank })
      ),
    () =>
      ca()?.load(target, range, top, window) ??
      loadFixture(scenario(), range, clock(), target, top, window)
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
    () => loadLatencyFixture(scenario(), range, clock(), keys, target, window)
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
    () => seriesFixture(scenario(), key, range, clock())
  )

// 5.11 analysis jobs

export const getAnalyzeJobs = (target: string, state?: AnalyzeJob['state']) =>
  pick<AnalyzeJobPage>(
    'analyzeJobs',
    () => json(`${base(target)}/analyze-jobs${query({ state })}`),
    () => {
      const page = analyzeJobsFixture(scenario(), clock())
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
    () => analyzeJobsFixture(scenario(), clock(), key, limit)
  )

export const enqueueAnalysis = (target: string, key: string) =>
  pick<AnalyzeJob>(
    'enqueueAnalysis',
    () =>
      json(`${base(target)}/queries/${enc(key)}/analyze`, { method: 'POST' }),
    () => {
      const now = clock()
      stubState.analyzing.add(key)
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

/** Stop a query's queued or running analysis; its latest analysis stays as it was. */
export const cancelQueryAnalysis = (target: string, key: string) =>
  pick<AnalyzeJob>(
    'cancelAnalysis',
    () =>
      json(`${base(target)}/queries/${enc(key)}/analyze/cancel`, {
        method: 'POST',
      }),
    () => {
      if (!stubState.analyzing.has(key))
        throw new MonitorApiError(409, 'This query has no analysis in progress')
      stubState.analyzing.delete(key)
      const now = clock()
      return {
        id: 1000 + (now % 1000),
        target,
        query_key: key,
        reason: 'user',
        state: 'skipped',
        priority: 1,
        param_source: null,
        mode: null,
        enqueued_at: now - 30,
        started_at: null,
        finished_at: now,
        error: 'cancelled',
        analysis_ref: null,
        reason_text: 'Cancelled.',
        cancelled: true,
      }
    }
  )

/** Stop one analyze job by id. */
export const cancelAnalyzeJob = (target: string, jobId: number) =>
  pick<AnalyzeJob>(
    'cancelAnalysis',
    () =>
      json(`${base(target)}/analyze-jobs/${jobId}/cancel`, { method: 'POST' }),
    () => {
      const key = [...stubState.analyzing][0]
      if (key === undefined)
        throw new MonitorApiError(409, 'This analysis has already ended')
      stubState.analyzing.delete(key)
      const now = clock()
      return {
        id: jobId,
        target,
        query_key: key,
        reason: 'user',
        state: 'skipped',
        priority: 1,
        param_source: null,
        mode: null,
        enqueued_at: now - 30,
        started_at: null,
        finished_at: now,
        error: 'cancelled',
        analysis_ref: null,
        reason_text: 'Cancelled.',
        cancelled: true,
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

/** Before and after for each query with a fix; the backend says whether it timed them. */
export const benchmarkFixes = (target: string, keys: string[]) =>
  pick<BenchmarkFixesResult>(
    'benchmarkFixes',
    () =>
      json(
        `${base(target)}/benchmark-fixes`,
        jsonBody('POST', { query_keys: keys })
      ),
    () => {
      const set = ca()
      if (set) return set.benchmarkFixes(target, keys)
      return {
        target,
        ran_at: clock(),
        runs: 20,
        scratch_schema: false,
        rows: keys.map((key) => ({
          key,
          name: key,
          before_ms: null,
          after_ms: null,
          change_pct: null,
          estimated: true,
        })),
        share: 0,
        method: `Estimated from the planner on ${target}: each fix tested as a hypothetical index, before from the last 24 h.`,
      }
    }
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
    () => stubState.settings.get(target) ?? baseSettings(target)
  )

export const putSettings = (target: string, update: MonitorSettingsUpdate) =>
  pick<MonitorSettings>(
    'settings',
    async () =>
      withSettingsDefaults(
        await json(`${base(target)}/settings`, jsonBody('PUT', update))
      ),
    () => {
      const current = stubState.settings.get(target) ?? baseSettings(target)
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
    () =>
      ca()?.limit(target) ?? {
        in_use: 1,
        limit: 3,
        own_key: false,
        known: true,
        full: false,
        signed_in: true,
        target_fits: target ? true : null,
        targets: [],
      }
  )

/** Home (GET /api/monitor/home): the status line, the day card, every database, today. */
export const getHome = (target?: string, signal?: AbortSignal) =>
  pick<HomeModel>(
    'home',
    () => json(`${ROOT}/home${query({ target })}`, { signal }),
    () => {
      const set = ca()
      if (set) return set.home()
      const now = clock()
      return {
        status: {
          state: 'quiet',
          target: target ?? null,
          needs_you: 0,
          to_watch: 0,
          headline: null,
          link: null,
          checks_ran_at: now - 120,
          since: now - 3600,
          last_reading_at: now - 23,
          next_report_at: now + 3600,
          watching: 1,
        },
        day: null,
        databases: [],
        not_monitored: 0,
        today: [],
        since_yesterday: { opened_closed: 0, fixes_confirmed: 0 },
        recently_analyzed: [],
        generated_at: now,
      }
    }
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
        ...(stubState.settings.get(target) ?? baseSettings(target)),
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
    () => usageFixture(scenario(), clock(), days)
  )

// 5.6 mute rules

function stubRules(): MuteRule[] {
  stubState.muteRules ??= muteRulesFixture(clock()).rules
  return stubState.muteRules
}

export const getMuteRules = (target?: string) =>
  pick<MuteRulePage>(
    'muteRules',
    () => json(`${ROOT}/mute-rules${query({ target })}`),
    () => {
      const items = stubInbox({
        items: allItems(),
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
        created_at: clock(),
        matched_count: 0,
      }
      stubRules().push(rule)
      // Like the backend, a new rule moves the open items it covers to Muted.
      const matched = stubInbox({
        items: allItems(),
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
    (rule.until === null || rule.until > clock())
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
        stubInbox({ items: allItems() }).items,
        inbox,
        clock()
      ),
    })
  )

export const getInboxItem = (id: number, signal?: AbortSignal) =>
  pick<InboxItem>(
    'inbox',
    () => json(`${ROOT}/inbox/${id}`, { signal }),
    () => {
      const item = stubInbox({
        items: allItems(),
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
      const item = allItems().find((candidate) => candidate.id === id)
      if (!item) throw new MonitorApiError(404, 'Inbox item not found')
      const now = clock()
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

/** Actions was opened for this database: its open Changes ready event closes. */
export const actionsOpened = (target: string) =>
  pick<{ target: string; closed: boolean }>(
    'inboxAction',
    () => json(`${base(target)}/actions/opened`, { method: 'POST' }),
    () => {
      const now = clock()
      const ready = stubInbox({ items: allItems() }).items.filter(
        (item) =>
          item.target === target &&
          item.type === 'changes_ready' &&
          item.state !== 'resolved' &&
          item.state !== 'dismissed'
      )
      for (const item of ready)
        stubState.inbox.set(item.id, {
          ...stubState.inbox.get(item.id),
          state: 'resolved',
          updated_at: now,
          resolved_at: now,
          resolved_by: 'code',
        })
      return { target, closed: ready.length > 0 }
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
      ts: clock(),
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
        ts: clock(),
        trigger: 'user',
        state: 'cancelled',
        provisional: false,
        progress: null,
        report_json: null,
        refs: {},
      }
    }
  )

export const getReportHistory = (target: string, limit = 10, daily = false) =>
  pick<ReportHistory>(
    'reportHistory',
    () =>
      json(
        `${base(target)}/report/history${query(daily ? { limit, daily: 'true' } : { limit })}`
      ),
    () => {
      const history =
        ca()?.reportHistory(target, limit) ??
        reportHistoryFixture(scenario(), clock(), limit)
      const template = reportHistoryFixture('localdev', clock(), 1).reports[0]
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

/**
 * `id` may be 'latest' (the newest run of any kind) or 'daily' (the newest nightly report).
 * `view` 'detailed' adds the Detailed sections. Rejects with MonitorApiError(404) when there
 * is no such report.
 */
export const getReport = (
  target: string,
  id: number | 'latest' | 'daily',
  view: ReportViewMode = 'quick'
) =>
  pick<DatabaseReport>(
    'report',
    () => {
      const detailed = view === 'detailed' ? 'detailed' : undefined
      return json(
        id === 'daily'
          ? `${base(target)}/report/latest${query({ daily: 'true', view: detailed })}`
          : `${base(target)}/report/${id}${query({ view: detailed })}`
      )
    },
    () => {
      const newest = Math.max(0, ...stubState.finished.keys())
      const latest = id === 'latest' || id === 'daily'
      const set = ca()
      const report =
        finishedStubReport(latest ? newest : id, target) ??
        (set
          ? set.report(target, latest ? 'latest' : id)
          : reportFixture(scenario(), latest ? 'latest' : id, clock(), target))
      if (!report) throw new MonitorApiError(404, 'Report not found')
      return view === 'detailed' ? report : { ...report, detailed: null }
    }
  )

// The report view preference lives with the app preferences; fixtures keep it in memory
// on the settings endpoint's side.
const PREFERENCES = '/api/settings/preferences'

export const getReportViewPreference = () =>
  pick<ReportViewMode>(
    'settings',
    () =>
      json<{ report_view?: ReportViewMode }>(PREFERENCES).then(
        (preferences) => preferences.report_view ?? 'quick'
      ),
    () => stubState.reportView
  )

export const putReportViewPreference = (view: ReportViewMode) =>
  pick<ReportViewMode>(
    'settings',
    () =>
      json<{ report_view?: ReportViewMode }>(
        PREFERENCES,
        jsonBody('PUT', { report_view: view })
      ).then((preferences) => preferences.report_view ?? view),
    () => {
      stubState.reportView = view
      return view
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
    () => onPulse(nextPulseFixture(current, clock())),
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
  const events = reportEventsFixture(clock())
  let index = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const step = () => {
    if (stubState.cancelled.has(id)) {
      const started = events[0]
      onEvent({
        type: 'run',
        state: 'cancelled',
        started_at: started.type === 'run' ? started.started_at : clock(),
        elapsed_s: index,
      })
      return
    }
    if (index >= events.length) return
    const event = events[index++]
    // Record the finish before announcing it, so a refetch on "done" finds the report.
    if (event.type === 'run' && event.state === 'done')
      stubState.finished.set(id, clock())
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
