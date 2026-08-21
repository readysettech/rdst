import type { components as apiComponents } from './api.generated'
import { api as typedClient } from './client'
import { extractDetail, throwIfApiError, throwIfNotOk } from './httpError'

export type AnalyzeRequest = apiComponents['schemas']['AnalyzeRequest']

export type ProgressEvent = apiComponents['schemas']['ProgressEvent']

export type ExplainResults = apiComponents['schemas']['ExplainResults']
export type PerformanceAssessment =
  apiComponents['schemas']['PerformanceAssessment']
export type RewriteSuggestion = apiComponents['schemas']['RewriteSuggestion']
export type IndexRecommendation =
  apiComponents['schemas']['IndexRecommendation']
export type OptimizationOpportunity =
  apiComponents['schemas']['OptimizationOpportunity']
export type LLMAnalysis = apiComponents['schemas']['LLMAnalysis']
export type TestedRewrite = apiComponents['schemas']['TestedRewrite']
export type RewriteTesting = apiComponents['schemas']['RewriteTesting']
export type IndexTesting = apiComponents['schemas']['IndexTesting']
export type IndexPlannerResult = apiComponents['schemas']['IndexPlannerResult']
export type ReadysetCacheability =
  apiComponents['schemas']['ReadysetCacheability']

export type AnalysisSummary = apiComponents['schemas']['AnalysisSummary']

export type InitTargetInfo = apiComponents['schemas']['InitTargetInfo']
export type InitStatusResponse = apiComponents['schemas']['InitStatusResponse']

export type AnalysisMetadata = apiComponents['schemas']['AnalysisMetadata']
export type FormattedAnalysis = apiComponents['schemas']['FormattedAnalysis']
export type CompleteEvent = apiComponents['schemas']['CompleteEvent']

export async function fetchInitStatus(): Promise<InitStatusResponse> {
  const response = await fetch('/api/init/status')
  if (!response.ok) {
    throw new Error(`Failed to fetch init status: ${response.status}`)
  }
  return response.json()
}

export type ErrorEvent = apiComponents['schemas']['ErrorEvent']

export type AnalysisState = 'idle' | 'analyzing' | 'complete' | 'error'

export type TargetInfo = apiComponents['schemas']['TargetInfo']

export type StatusResponse = apiComponents['schemas']['StatusResponse']

export async function fetchStatus(): Promise<StatusResponse> {
  const response = await fetch('/api/status')
  if (!response.ok) {
    throw new Error(`Failed to fetch status: ${response.status}`)
  }
  return response.json()
}

export type TargetSummary = apiComponents['schemas']['TargetSummaryResponse']
export type TargetListResponse = apiComponents['schemas']['TargetListResponse']

/**
 * List configured targets with their connection details (host/port). Used by
 * the benchmark rail to flag non-local/remote destinations (B5).
 */
export async function fetchTargets(): Promise<TargetSummary[]> {
  const { data, response } = await typedClient.GET('/api/configure/targets')
  await throwIfNotOk(response, 'Failed to list targets')
  if (data && 'targets' in data) return data.targets
  return []
}

export type EnvRequirement = apiComponents['schemas']['EnvRequirement']
export type EnvRequirementKind = EnvRequirement['kind']
export type EnvRequirementSource = EnvRequirement['source']
export type EnvRequirementsResponse =
  apiComponents['schemas']['EnvRequirementsResponse']

export type SetEnvSecretRequest = apiComponents['schemas']['EnvSetRequest']
export type SetEnvSecretResponse = apiComponents['schemas']['EnvSetResponse']

export async function fetchEnvRequirements(): Promise<EnvRequirementsResponse> {
  const response = await fetch('/api/env/requirements')
  if (!response.ok) {
    throw new Error(`Failed to fetch env requirements: ${response.status}`)
  }
  return response.json()
}

export async function setEnvSecret(
  payload: SetEnvSecretRequest
): Promise<SetEnvSecretResponse> {
  const response = await fetch('/api/env/set', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: payload.name,
      value: payload.value,
      persist: payload.persist ?? true,
    }),
  })
  await throwIfNotOk(response, 'Failed to save secret')
  return response.json()
}

export type AnthropicKeyValidation = {
  valid: boolean
  reason: 'ok' | 'rejected' | 'no_key' | 'provider_error' | 'exhausted'
  model: string | null
  // 'trial' | 'trial_exhausted' | 'process_env' | 'keyring' | 'none' etc.
  source?: string | null
}

// Validity, not just presence: pings Anthropic with the resolved key so the UI
// can distinguish a "configured" key from a "working" one. Result is cached
// briefly server-side. Untyped by the generated client until gen:api runs.
export async function validateAnthropicKey(): Promise<AnthropicKeyValidation> {
  const response = await fetch('/api/env/anthropic/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  await throwIfNotOk(response, 'Failed to validate Anthropic key')
  return response.json()
}

export type SchemaResponse = apiComponents['schemas']['SchemaResponse']

export async function fetchSchema(target?: string): Promise<SchemaResponse> {
  const { data, error, response } = await typedClient.GET('/api/schema', {
    params: { query: { target: target ?? null } },
  })
  throwIfApiError(response, error, 'Failed to fetch schema')
  if (!data) throw new Error('Missing response body')
  return data
}

export type ParameterSuggestionsResponse =
  apiComponents['schemas']['ParameterSuggestionsResponse']
export type ParameterSuggestion =
  apiComponents['schemas']['ParameterSuggestion']
export type ParameterValueSuggestion =
  apiComponents['schemas']['ParameterValueSuggestion']

/**
 * Real values for a templated query's placeholders (captured statement
 * instance, sampled column values), each with a provenance label.
 */
export async function fetchParameterSuggestions(
  query: string,
  target: string,
  queryHash?: string | null
): Promise<ParameterSuggestionsResponse> {
  const { data, error, response } = await typedClient.POST(
    '/api/analyze/parameter-suggestions',
    { body: { query, target, query_hash: queryHash ?? null } }
  )
  throwIfApiError(response, error, 'Failed to fetch parameter suggestions')
  if (!data) throw new Error('Missing response body')
  return data
}

/**
 * The stored outcome of the last origin-vs-Readyset comparison for one query.
 * It lives on the registry row, so a comparison stays reportable on any
 * browser. Untyped by the generated client until gen:api runs.
 */
export type QueryCompareOutcome = {
  status?: string | null
  at?: string | null
  readyset_ms?: number | null
  origin_ms?: number | null
  detail?: string | null
}

/**
 * A registry row, plus the read-model fields added since the last gen:api run:
 * the user's star and the durable compare outcome. Both are optional, so a
 * server that predates them renders as an unstarred, never-compared query.
 */
export type QueryRegistryEntry =
  apiComponents['schemas']['QueryRegistryEntry'] & {
    starred?: boolean
    starred_at?: string | null
    last_compare?: QueryCompareOutcome | null
  }
export type QueryRegistryResponse =
  apiComponents['schemas']['QueryRegistryResponse']

export async function fetchQueryRegistry(
  limit?: number,
  offset = 0,
  target?: string | null
): Promise<QueryRegistryResponse> {
  const { data, response } = await typedClient.GET('/api/query-registry', {
    params: {
      query: { limit: limit ?? null, offset, target: target ?? undefined },
    },
  })
  await throwIfNotOk(response, 'Failed to fetch registry')
  if (!data) throw new Error('Missing response body')
  return data
}

/**
 * Facet counts computed server-side over the full target-scoped set. Each
 * dimension applies every other dimension's filter but not its own. Keys are
 * the Query Library filter values; the feature layer narrows them.
 */
export type QueryRegistryFacetCounts = {
  view: Record<string, number>
  source: Record<string, number>
  params: Record<string, number>
  activity: Record<string, number>
  impact: Record<string, number>
}

export type QueryRegistryFreshness = {
  state: string
  last_success_at: string | null
  epoch_id: string | null
}

export type QueryRegistryReadModelPage = {
  queries: QueryRegistryEntry[]
  facet_counts: QueryRegistryFacetCounts
  next_cursor: string | null
  total: number
  freshness: QueryRegistryFreshness | null
  error: string | null
}

export type QueryRegistryReadModelRequest = {
  target?: string | null
  search?: string
  view: string
  source: string
  params: string
  activity: string
  impact: string
  sort: string
  /** The star, an independent boolean that composes with every other filter. */
  starred?: boolean
  limit: number
  cursor?: string
}

/** The server no longer recognizes the page cursor; restart from page one. */
export class QueryRegistryCursorError extends Error {
  constructor() {
    super('Query registry page cursor is no longer valid')
    this.name = 'QueryRegistryCursorError'
  }
}

/**
 * Read-model mode of GET /api/query-registry: the server filters, sorts,
 * counts facets over the full set, and pages by opaque cursor. Sending the
 * filter params (even at their defaults) is what selects this mode over the
 * legacy limit/offset mode. Untyped by the generated client until gen:api
 * runs.
 */
export async function fetchQueryRegistryReadModel(
  request: QueryRegistryReadModelRequest
): Promise<QueryRegistryReadModelPage> {
  const params = new URLSearchParams({
    view: request.view,
    source: request.source,
    params: request.params,
    activity: request.activity,
    impact: request.impact,
    sort: request.sort,
    limit: String(request.limit),
  })
  if (request.starred) params.set('starred', '1')
  if (request.target) params.set('target', request.target)
  const search = request.search?.trim()
  if (search) params.set('search', search)
  if (request.cursor) params.set('cursor', request.cursor)

  const response = await fetch(`/api/query-registry?${params}`)
  if (response.status === 400) {
    const body: unknown = await response.json().catch(() => null)
    const detail = (body as { detail?: { code?: string } } | null)?.detail
    if (
      detail &&
      typeof detail === 'object' &&
      detail.code === 'cursor_invalid'
    ) {
      throw new QueryRegistryCursorError()
    }
    throw new Error(extractDetail(body) || 'Failed to fetch registry: 400')
  }
  await throwIfNotOk(response, 'Failed to fetch registry')
  return response.json()
}

/** Compact record of one stored analysis run for a query. */
export type QueryAnalysisSummary = {
  analysis_id: string
  analyzed_at: string
  target: string
  overall_rating: string
  efficiency_score: number | null
}

export type LatestAnalysisResponse = {
  found: boolean
  analysis: QueryAnalysisSummary | null
  error?: string | null
}

/**
 * Latest stored analysis summary for one query hash. Untyped by the
 * generated client until gen:api runs.
 */
export async function fetchLatestAnalysis(
  hash: string
): Promise<LatestAnalysisResponse> {
  const response = await fetch(
    `/api/query-registry/${encodeURIComponent(hash)}/analysis/latest`
  )
  await throwIfNotOk(response, 'Failed to fetch the latest analysis')
  return response.json()
}

/** One entry in a query's bounded analysis history, newest first. */
export type AnalysisHistoryEntry = {
  analysis_id: string
  created_at: string
  target: string
  overall_rating: string
  efficiency_score: number | null
}

export type AnalysisHistoryResponse = {
  hash: string
  analyses: AnalysisHistoryEntry[]
}

/**
 * The finished results view as the analyze run produced it: the same fields
 * the SSE `complete` event carries into the results presentation. Analyses
 * stored before the viewer shipped carry an empty payload.
 */
export type StoredAnalysisDisplayPayload = Omit<
  CompleteEvent,
  'type' | 'success'
>

/** One stored analysis, whole, for read-only redisplay. */
export type StoredAnalysis = {
  hash: string
  analysis_id: string
  created_at: string
  target: string
  overall_rating: string
  efficiency_score: number | null
  analysis: {
    display_payload?: StoredAnalysisDisplayPayload | null
    [key: string]: unknown
  }
}

/** A query's stored analyses, newest first. Never analyzed yields an empty list. */
export async function fetchAnalysisHistory(
  hash: string
): Promise<AnalysisHistoryResponse> {
  const response = await fetch(
    `/api/query-registry/${encodeURIComponent(hash)}/analyses`
  )
  await throwIfNotOk(response, 'Failed to fetch the analysis history')
  return response.json()
}

/** One stored analysis by id, so it can be reopened without a re-run. */
export async function fetchStoredAnalysis(
  hash: string,
  analysisId: string
): Promise<StoredAnalysis> {
  const response = await fetch(
    `/api/query-registry/${encodeURIComponent(hash)}/analysis/${encodeURIComponent(analysisId)}`
  )
  await throwIfNotOk(response, 'Failed to fetch the stored analysis')
  return response.json()
}

export async function addQueryToRegistry(
  sql: string,
  target?: string
): Promise<{ success: boolean; hash?: string | null; error?: string | null }> {
  const { data, response } = await typedClient.POST('/api/query-registry', {
    body: { sql, target },
  })
  await throwIfNotOk(response, 'Failed to add query')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function removeQueryFromRegistry(
  hash: string
): Promise<{ success: boolean; error?: string | null }> {
  const { data, response } = await typedClient.DELETE(
    '/api/query-registry/{query_hash}',
    {
      params: { path: { query_hash: hash } },
    }
  )
  await throwIfNotOk(response, 'Failed to remove query')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function markQueryReviewed(
  hash: string,
  target: string
): Promise<{ success: boolean; error?: string | null }> {
  const { data, response } = await typedClient.POST(
    '/api/query-registry/{query_hash}/reviewed',
    {
      params: { path: { query_hash: hash } },
      body: { target },
    }
  )
  await throwIfNotOk(response, 'Failed to mark query reviewed')
  if (!data) throw new Error('Missing response body')
  return data
}

export type StarredQueryResponse = {
  hash: string
  target: string
  starred: boolean
  starred_at: string | null
}

/**
 * Set or clear a query's star. The mark is stored per target, so the target is
 * part of the request. Untyped by the generated client until gen:api runs.
 */
export async function setQueryStarred(
  hash: string,
  starred: boolean,
  target?: string | null
): Promise<StarredQueryResponse> {
  const response = await fetch(
    `/api/query-registry/queries/${encodeURIComponent(hash)}/starred`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(target ? { starred, target } : { starred }),
    }
  )
  await throwIfNotOk(response, 'Failed to update the star')
  return response.json()
}

export async function updateQueryTag(
  hash: string,
  tag: string
): Promise<{ success: boolean; error?: string | null }> {
  const { data, response } = await typedClient.PATCH(
    '/api/query-registry/{query_hash}/tag',
    {
      params: { path: { query_hash: hash } },
      body: { tag },
    }
  )
  await throwIfNotOk(response, 'Failed to update tag')
  if (!data) throw new Error('Missing response body')
  return data
}

export type UpdateSqlResponse = apiComponents['schemas']['UpdateSqlResponse']

export async function updateQuerySql(
  hash: string,
  sql: string
): Promise<UpdateSqlResponse> {
  const { data, response } = await typedClient.PATCH(
    '/api/query-registry/{query_hash}/sql',
    {
      params: { path: { query_hash: hash } },
      body: { sql },
    }
  )
  await throwIfNotOk(response, 'Failed to update SQL')
  if (!data) throw new Error('Missing response body')
  return data
}

export type QueryParametersSource = 'user' | 'suggested'
export type UpdateQueryParametersResponse = {
  hash: string
  parameters: Record<string, unknown>
}

/**
 * Persist parameter values against a registry query so a later dialog open
 * or run reuses them instead of asking again. `source` distinguishes a
 * value the user typed and confirmed from one a background suggestion
 * filled in. Untyped by the generated client until gen:api picks up this
 * endpoint.
 */
export async function updateQueryParameters(
  hash: string,
  values: Record<string, string>,
  source: QueryParametersSource
): Promise<UpdateQueryParametersResponse> {
  const response = await fetch(
    `/api/query-registry/queries/${encodeURIComponent(hash)}/parameters`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values, source }),
    }
  )
  await throwIfNotOk(response, 'Failed to save parameter values')
  return response.json()
}

export type CompareOutcomeStatus =
  | 'improved'
  | 'regressed'
  | 'equivalent'
  | 'not_comparable'
  | 'error'

export interface CompareOutcomeRequest {
  target?: string
  status: CompareOutcomeStatus
  readyset_ms?: number | null
  origin_ms?: number | null
  detail?: string | null
  readyset_supported?: 'yes' | 'no' | 'pending' | null
  unsupported_reason?: string | null
}

export interface CompareOutcomeResponse {
  hash: string
  target: string
  comparison_count: number
  last_compared_at: string
  last_compare: Record<string, unknown>
  readyset_supported: string
}

/**
 * Report what one Compare run found for a query, so the Query Library shows
 * a durable outcome instead of one that only ever lived in a browser's
 * localStorage history. Untyped by the generated client until gen:api picks
 * up this endpoint.
 */
export async function reportCompareOutcome(
  hash: string,
  request: CompareOutcomeRequest
): Promise<CompareOutcomeResponse> {
  const response = await fetch(
    `/api/query-registry/queries/${encodeURIComponent(hash)}/compare-outcome`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }
  )
  await throwIfNotOk(response, 'Failed to report compare outcome')
  return response.json()
}

export type ImportQueriesResponse =
  apiComponents['schemas']['ImportQueriesResponse']

export async function importQueries(
  file: string,
  options?: { update?: boolean; target?: string }
): Promise<ImportQueriesResponse> {
  const { data, response } = await typedClient.POST(
    '/api/query-registry/import',
    {
      body: { file, update: options?.update, target: options?.target },
    }
  )
  await throwIfNotOk(response, 'Failed to import queries')
  if (!data) throw new Error('Missing response body')
  return data
}

// Interactive Mode Types

export type InteractiveState = 'idle' | 'sending' | 'receiving' | 'error'

export type ConversationStatus =
  apiComponents['schemas']['ConversationStatusResponse']

export type Message = apiComponents['schemas']['MessageResponse']

export type InteractiveMessageRequest =
  apiComponents['schemas']['InteractiveMessageRequest']

// Report/Feedback Types

export type ReportRequest = apiComponents['schemas']['ReportRequest']
export type ReportSentiment = NonNullable<ReportRequest['sentiment']>
export type ReportResponse = apiComponents['schemas']['ReportResponse']

export async function submitReport(
  request: ReportRequest
): Promise<ReportResponse> {
  const response = await fetch('/api/report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    throw new Error(`Failed to submit report: ${response.status}`)
  }
  return response.json()
}

// Report delivery: emails the exact HTML artifact the CLI sends. `email` is
// optional — omitted, the server uses the machine's stored identity.

export type RunEmailResponse = apiComponents['schemas']['RunEmailResponse']

export async function emailAuditReport(
  runId: string,
  email?: string
): Promise<RunEmailResponse> {
  const { data, response } = await typedClient.POST(
    '/api/audit/runs/{run_id}/email',
    {
      params: { path: { run_id: runId } },
      body: { email: email ?? null },
    }
  )
  await throwIfNotOk(response, 'Failed to email report')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function emailFleetReport(
  snapshotId: string,
  email?: string
): Promise<RunEmailResponse> {
  const { data, response } = await typedClient.POST(
    '/api/fleet/snapshots/{snapshot_id}/email',
    {
      params: { path: { snapshot_id: snapshotId } },
      body: { email: email ?? null },
    }
  )
  await throwIfNotOk(response, 'Failed to email report')
  if (!data) throw new Error('Missing response body')
  return data
}

// Benchmark Types

export type BenchmarkRequest = apiComponents['schemas']['BenchmarkRequest']
export type BenchmarkQueryInput =
  apiComponents['schemas']['BenchmarkQueryInput']
export type BenchmarkMode = BenchmarkRequest['mode']

// QueryBenchmarkStats comes from the generated schema; re-exported for consumers.
export type QueryBenchmarkStats =
  apiComponents['schemas']['QueryBenchmarkStats']

export type BenchmarkState = 'idle' | 'running' | 'complete' | 'error'

// Trial Types (re-exports from generated)

export type TrialRegisterResponse =
  apiComponents['schemas']['TrialRegisterResponse']
export type TrialActivateResponse =
  apiComponents['schemas']['TrialActivateResponse']
export type TrialStatusResponse =
  apiComponents['schemas']['TrialStatusResponse']
export type TrialSimulationResponse =
  apiComponents['schemas']['TrialSimulationResponse']

export async function resetLocalData(): Promise<void> {
  const { response } = await typedClient.POST('/api/settings/reset-local-data')
  await throwIfNotOk(response, 'Failed to remove local data')
}

export async function registerTrial(
  email: string
): Promise<TrialRegisterResponse> {
  const { data, error, response } = await typedClient.POST(
    '/api/trial/register',
    {
      body: { email },
    }
  )
  throwIfApiError(response, error, 'Failed to register trial')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function activateTrial(
  token: string,
  email: string,
  opts: {
    emailTier?: string
    limitCents?: number
    remainingCents?: number
  } = {}
): Promise<TrialActivateResponse> {
  const { data, response } = await typedClient.POST('/api/trial/activate', {
    body: {
      token,
      email,
      email_tier: opts.emailTier ?? null,
      limit_cents: opts.limitCents ?? null,
      remaining_cents: opts.remainingCents ?? null,
    },
  })
  await throwIfNotOk(response, 'Failed to activate trial')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function fetchTrialStatus(): Promise<TrialStatusResponse> {
  const { data, response } = await typedClient.GET('/api/trial/status', {})
  await throwIfNotOk(response, 'Failed to fetch trial status')
  if (!data) throw new Error('Missing response body')
  return data
}

// Browse (directory picker)

export type BrowseDirectoryEntry = apiComponents['schemas']['DirectoryEntry']
export type BrowseResponse = apiComponents['schemas']['BrowseResponse']

export async function fetchBrowse(
  path?: string,
  ext?: string
): Promise<BrowseResponse> {
  const params = new URLSearchParams()
  if (path) params.set('path', path)
  if (ext) params.set('ext', ext)
  const query = params.toString()
  const response = await fetch(query ? `/api/browse?${query}` : '/api/browse')
  if (!response.ok) {
    throw new Error(`Failed to browse directory: ${response.status}`)
  }
  return response.json()
}

export async function simulateTrialExhausted(): Promise<TrialSimulationResponse> {
  const { data, response } = await typedClient.POST(
    '/api/trial/simulate/exhaust',
    {}
  )
  await throwIfNotOk(response, 'Failed to simulate trial exhaustion')
  if (!data) throw new Error('Missing response body')
  return data
}

export type ClearKeyringResponse =
  apiComponents['schemas']['ClearKeyringResponse']

type ClearKeyringErrorPayload = {
  message?: string | null
  errors?: string[]
  detail?: string | { msg?: string }[]
}

function formatClearKeyringErrorMessage(
  payload: ClearKeyringErrorPayload | null | undefined,
  fallback: string
): string {
  if (!payload) return fallback

  const parts: string[] = []

  if (typeof payload.message === 'string' && payload.message.trim()) {
    parts.push(payload.message.trim())
  }

  const errors = Array.isArray(payload.errors)
    ? payload.errors.map((error) => error.trim()).filter(Boolean)
    : []
  if (errors.length > 0) {
    parts.push(errors.join(' '))
  }

  if (typeof payload.detail === 'string' && payload.detail.trim()) {
    parts.push(payload.detail.trim())
  }

  if (Array.isArray(payload.detail)) {
    const detailMessages = payload.detail
      .map((detail) =>
        typeof detail?.msg === 'string' ? detail.msg.trim() : ''
      )
      .filter(Boolean)
    if (detailMessages.length > 0) {
      parts.push(detailMessages.join(' '))
    }
  }

  return parts.join(' ').trim() || fallback
}

// Ask: schema-grounded example questions + per-target question history.
// Ports the /ask/examples and /ask/history endpoints from CL 14059.
export type AskExamplesResponse =
  apiComponents['schemas']['AskExamplesResponse']
export type AskHistoryResponse = apiComponents['schemas']['AskHistoryResponse']
export type AskHistoryItem = apiComponents['schemas']['AskHistoryItem']

export async function fetchAskExamples(
  target: string
): Promise<AskExamplesResponse> {
  const { data, response } = await typedClient.GET('/api/ask/examples', {
    params: { query: { target } },
  })
  await throwIfNotOk(response, 'Failed to fetch ask examples')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function fetchAskHistory(
  target?: string | null,
  limit = 50
): Promise<AskHistoryResponse> {
  const { data, response } = await typedClient.GET('/api/ask/history', {
    params: { query: { target: target ?? null, limit } },
  })
  await throwIfNotOk(response, 'Failed to fetch ask history')
  if (!data) throw new Error('Missing response body')
  return data
}

export type SchemaStatusResponse =
  apiComponents['schemas']['SchemaStatusResponse']

// Read-only schema status for a target. Takes react-query's AbortSignal so a
// superseded fetch is cancelled by react-query itself and never overwrites good
// data with a null. useSchema.checkStatus, whose shared AbortController aborts
// every prior fetch, made the Ask/Home "semantic layer" badge flap to "no
// semantic layer" on a target switch (rdst-e7s.27).
export async function fetchSchemaStatus(
  target: string,
  signal?: AbortSignal
): Promise<SchemaStatusResponse> {
  const { data, response } = await typedClient.GET(
    '/api/semantic-layer/status',
    {
      params: { query: { target } },
      signal,
    }
  )
  await throwIfNotOk(response, 'Failed to fetch schema status')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function clearKeyring(): Promise<ClearKeyringResponse> {
  const response = await fetch('/api/dev/clear-keyring', { method: 'POST' })

  let payload: ClearKeyringErrorPayload | ClearKeyringResponse | null = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    throw new Error(
      formatClearKeyringErrorMessage(
        payload,
        `Failed to clear keyring: ${response.status}`
      )
    )
  }

  const result = payload as ClearKeyringResponse | null
  if (!result?.success) {
    throw new Error(
      formatClearKeyringErrorMessage(result, 'Failed to clear keyring.')
    )
  }

  return result
}
