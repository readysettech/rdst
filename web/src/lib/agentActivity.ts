// /api/agent-activity: the audit log of everything the RDST agent did.
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'

export type AgentAction =
  | 'deep_analysis'
  | 'ask'
  | 'classification'
  | 'attention_check'
  | 'daily_snapshot'
  | 'explain_actions'
  | 'benchmark'

export type AgentOutcome = 'done' | 'failed' | 'skipped' | 'cancelled'

export type AgentFold =
  | 'attention_check'
  | 'classification'
  | 'skipped_analysis'
export type AgentGroup = 'day' | 'hour' | 'none'

/**
 * One line: a single agent decision, or the attention checks, classifications or
 * skipped deep analyses of one database folded per day or hour (`rollup`).
 */
export interface AgentActivityItem {
  key: string
  kind: 'entry' | 'rollup'
  id: number
  ts: number
  first_ts: number
  count: number
  done: number
  failed: number
  skipped: number
  target: string
  action: AgentAction
  subject: string
  outcome: AgentOutcome
  summary: string
  source: 'live' | 'backfill'
  statement_count: number
  ai_call_count: number
  tokens_in: number
  tokens_out: number
  cost_usd: number
  items_filed: number
  decision?: string | null
  question?: string | null
  query_key?: string | null
  analysis_ref?: string | null
  report_id?: number | null
  period_start?: number | null
  period_end?: number | null
  parameters?: string | null
  note?: string | null
  finding?: string | null
  fold?: AgentFold | null
  group?: AgentGroup | null
  bucket_start?: number | null
  bucket_end?: number | null
}

export interface AgentActivityPage {
  items: AgentActivityItem[]
  next_cursor?: string | null
  targets: string[]
}

export interface AgentStatement {
  sql: string
  kind: string
  executions: number
  duration_ms?: number | null
  max_ms?: number | null
  timeout_ms?: number | null
  error?: string | null
}

export interface AgentAiCall {
  ts?: number | null
  provider: string
  model: string
  operation: string
  purpose: string
  data_kinds: string[]
  tokens_in?: number | null
  tokens_out?: number | null
  cost_usd?: number | null
  cost_estimated: boolean
  duration_ms?: number | null
  request_id?: string | null
  error?: string | null
}

export interface AgentActivityDetail {
  id: number
  ts: number
  finished_at?: number | null
  target: string
  action: AgentAction
  subject: string
  outcome: AgentOutcome
  summary: string
  decision?: string | null
  details: Record<string, unknown>
  run_ref?: string | null
  source: 'live' | 'backfill'
  statement_count: number
  ai_call_count: number
  tokens_in: number
  tokens_out: number
  cost_usd: number
  items_filed: number
  statements: AgentStatement[]
  ai_calls: AgentAiCall[]
}

/** What a list asks for; `since`/`until` are epoch seconds, `until` exclusive. */
export interface ActivityQuery {
  target?: string
  action?: AgentAction
  outcome?: AgentOutcome
  since?: number
  until?: number
  group?: AgentGroup
}

/** Minutes east of UTC, so the server folds by the viewer's local day. */
export function utcOffsetMinutes(at = Date.now()): number {
  return -new Date(at).getTimezoneOffset()
}

const ROOT = '/api/agent-activity'

export function activitySearch(
  query: ActivityQuery,
  cursor?: string | null
): string {
  const search = new URLSearchParams()
  if (query.target) search.set('target', query.target)
  if (query.action) search.set('action', query.action)
  if (query.outcome) search.set('outcome', query.outcome)
  if (query.since !== undefined) search.set('since', String(query.since))
  if (query.until !== undefined) search.set('until', String(query.until))
  if (query.group && query.group !== 'day') search.set('group', query.group)
  if (query.group !== 'none')
    search.set('utc_offset', String(utcOffsetMinutes()))
  if (cursor) search.set('cursor', cursor)
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** Every entry in the range, one CSV row each, checks included. */
export function exportCsvUrl(
  query: Pick<ActivityQuery, 'target' | 'action' | 'since' | 'until'>
): string {
  const search = new URLSearchParams()
  if (query.target) search.set('target', query.target)
  if (query.action) search.set('action', query.action)
  if (query.since !== undefined) search.set('since', String(query.since))
  if (query.until !== undefined) search.set('until', String(query.until))
  search.set('utc_offset', String(utcOffsetMinutes()))
  return `${ROOT}/export.csv?${search.toString()}`
}

async function json<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok)
    throw new Error(`Agent activity request failed (${response.status})`)
  return response.json() as Promise<T>
}

export const agentActivityKeys = {
  all: ['agent-activity'] as const,
  list: (query: ActivityQuery) => ['agent-activity', 'list', query] as const,
  entry: (id: number) => ['agent-activity', 'entry', id] as const,
}

export function getAgentActivity(
  query: ActivityQuery,
  cursor?: string | null
): Promise<AgentActivityPage> {
  return json(`${ROOT}${activitySearch(query, cursor)}`)
}

export function getAgentActivityEntry(
  id: number
): Promise<AgentActivityDetail> {
  return json(`${ROOT}/${id}`)
}

export function useAgentActivity(query: ActivityQuery, enabled = true) {
  return useInfiniteQuery({
    queryKey: agentActivityKeys.list(query),
    queryFn: ({ pageParam }) => getAgentActivity(query, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? null,
    enabled,
    refetchInterval: 60_000,
  })
}

export function useAgentActivityEntry(id: number, enabled: boolean) {
  return useQuery({
    queryKey: agentActivityKeys.entry(id),
    queryFn: () => getAgentActivityEntry(id),
    enabled,
    // Entries are written once and never change.
    staleTime: Number.POSITIVE_INFINITY,
  })
}
