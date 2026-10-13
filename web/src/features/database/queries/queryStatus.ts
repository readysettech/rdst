// One Status per query (DESIGN_V5 section 1): the first that applies of Needs attention,
// Watch, Fix suggested, Healthy and Not analyzed (yet), with one plain reason. Needs attention
// and Watch come from open Inbox items, so the list agrees with the Inbox. Pure, so the
// rules are unit-tested.
import { formatStampTime } from '@/features/inbox/inboxModel'
import type { AnalyzeJob, InboxItem, MonitorQuery } from '@/lib/monitor/types'
import type { Analyzability } from './analyzability'

export type QueryStatusId =
  | 'attention'
  | 'watch'
  | 'fix_ready'
  | 'healthy'
  | 'not_analyzed'

export const QUERY_STATUSES: readonly QueryStatusId[] = [
  'attention',
  'watch',
  'fix_ready',
  'healthy',
  'not_analyzed',
]

export const STATUS_LABELS: Record<QueryStatusId, string> = {
  attention: 'Needs attention',
  watch: 'Watch',
  fix_ready: 'Fix suggested',
  healthy: 'Healthy',
  not_analyzed: 'Not analyzed yet',
}

export type StatusTone =
  | 'negative'
  | 'warning'
  | 'positive'
  | 'informative'
  | 'neutral'

export const STATUS_TONES: Record<QueryStatusId, StatusTone> = {
  attention: 'negative',
  watch: 'warning',
  fix_ready: 'informative',
  healthy: 'neutral',
  not_analyzed: 'neutral',
}

export interface QueryStatus {
  id: QueryStatusId
  label: string
  tone: StatusTone
  /** One line in plain words: "Traffic up 3x since 9:42 PM". Null when there's nothing to say. */
  reason: string | null
  /** The Inbox item behind Needs attention or Watch. */
  itemId: number | null
}

/** What an open Inbox item says about one query. */
export interface InboxSignal {
  level: 'attention' | 'watch'
  reason: string
  itemId: number
  createdAt: number
}

const OPEN_STATES = new Set(['new', 'seen'])
const GOOD_NEWS_TYPES = new Set(['query_improved', 'daily_summary'])
/** A group member moved less than this: it's listed for impact, not for a change. */
const MEMBER_MIN_CHANGE = 0.1

function itemLevel(item: InboxItem): InboxSignal['level'] | null {
  if (!OPEN_STATES.has(item.state) || item.muted_by != null) return null
  if (item.severity_band === 'act_now') return 'attention'
  if (item.severity_band === 'watch') return 'watch'
  if (item.severity_band === 'recovered' || item.tone === 'positive')
    return null
  if (item.delivery === 'digest' && !GOOD_NEWS_TYPES.has(item.type))
    return 'watch'
  return null
}

/** '34%', '3x', '3.2x'. `change` is after / before - 1. */
export function changeWords(change: number): string {
  const size = Math.abs(change)
  if (change > 0 && size >= 1) {
    const ratio = change + 1
    const rounded =
      ratio >= 10 ? Math.round(ratio) : Math.round(ratio * 10) / 10
    return `${rounded}x`
  }
  return `${Math.max(1, Math.round(size * 100))}%`
}

const METRIC_WORD: Record<string, string> = {
  calls: 'Traffic',
  latency: 'Latency',
  load: 'Database time',
  share: 'Share of database time',
  running_time: 'Running time',
}

function movedSentence(
  metric: string,
  change: number,
  at: number,
  now: number
): string | null {
  const word = METRIC_WORD[metric]
  if (!word || !Number.isFinite(change) || change === 0) return null
  const direction = change > 0 ? 'up' : 'down'
  return `${word} ${direction} ${changeWords(change)} since ${formatStampTime(at, now)}`
}

function percent(ratio: number) {
  const value = ratio * 100
  return `${value >= 10 ? Math.round(value) : Math.round(value * 10) / 10}%`
}

/** The reason for a single-query item, from what was measured. */
function itemReason(item: InboxItem, now: number): string {
  const at = item.created_at
  const when = formatStampTime(at, now)
  const m = item.measurement
  if (
    item.type === 'query_stopped_running' ||
    (m?.metric === 'calls' && m.after === 0)
  )
    return `Stopped running at ${when}`
  if (item.type === 'stuck_query')
    return m?.text ? `${m.text}` : `A call got stuck at ${when}`
  if (
    item.type === 'new_heavy_query' ||
    (m?.metric === 'share' && m.change == null)
  )
    return m?.after != null
      ? `New since ${when}, ${percent(m.after)} of database time`
      : `New since ${when}`
  if (m && m.change != null) {
    const sentence = movedSentence(m.metric, m.change, at, now)
    if (sentence) return sentence
  }
  return `${item.title} at ${when}`
}

/**
 * The queries an item is about, each with its own reason. A group alert names its
 * members (the few with the most impact), never every query it counted, so one
 * "Traffic jumped on 66 queries" doesn't mark 66 rows.
 */
function itemQueries(
  item: InboxItem,
  now: number
): { key: string; reason: string }[] {
  const members = item.members ?? []
  const group = members.length > 0 || item.type === 'many_queries_changed'
  if (group) {
    const metric = item.measurement?.metric ?? 'latency'
    const direction = item.measurement?.direction
    return members.flatMap((member) => {
      const change = member.change
      if (change == null || Math.abs(change) < MEMBER_MIN_CHANGE) return []
      if (direction && (change > 0 ? 'up' : 'down') !== direction) return []
      const reason = movedSentence(metric, change, item.created_at, now)
      return reason ? [{ key: member.query_key, reason }] : []
    })
  }
  const keys = new Set(item.query_keys)
  if (item.subject && item.subject !== 'database' && keys.size === 0)
    keys.add(item.subject)
  // An item with many keys and no members predates group members; it names no one query.
  if (keys.size > 3) return []
  const reason = itemReason(item, now)
  return [...keys].map((key) => ({ key, reason }))
}

/** The strongest open Inbox signal per query key: attention over watch, then newest. */
export function inboxSignals(
  items: InboxItem[] | undefined,
  target?: string,
  now = Date.now() / 1000
): Map<string, InboxSignal> {
  const signals = new Map<string, InboxSignal>()
  for (const item of items ?? []) {
    if (target && item.target !== target) continue
    const level = itemLevel(item)
    if (!level) continue
    for (const { key, reason } of itemQueries(item, now)) {
      const current = signals.get(key)
      const stronger =
        !current ||
        (level === 'attention' && current.level === 'watch') ||
        (level === current.level && item.created_at > current.createdAt)
      if (stronger)
        signals.set(key, {
          level,
          reason,
          itemId: item.id,
          createdAt: item.created_at,
        })
    }
  }
  return signals
}

/** "Add an index on orders(total)" from the analysis headline and fix kind. */
export function fixReason(query: MonitorQuery): string | null {
  const summary = query.last_analysis?.summary
  if (!summary) return null
  const headline = summary.headline?.trim() ?? ''
  const index = headline.match(/^needs an index on (.+)$/i)
  if (index) return `Add an index on ${index[1]}`
  if (summary.fix_kind === 'index') return 'Add an index'
  if (summary.fix_kind === 'rewrite') return 'Rewrite the query'
  if (summary.fix_kind === 'config') return 'Change a database setting'
  return headline || null
}

/** Where a waiting analysis stands, in a few words. */
export function jobLine(
  job: AnalyzeJob | null | undefined,
  now: number
): string | null {
  if (!job) return null
  if (job.state === 'running') return 'Analyzing now'
  if (job.state !== 'queued') return null
  if (job.queue_state === 'held')
    return job.reason_text ?? 'Waiting, automatic analysis is off'
  if (job.estimated_start_at != null) {
    const minutes = Math.max(1, Math.round((job.estimated_start_at - now) / 60))
    return minutes >= 90
      ? `Queued, about ${Math.round(minutes / 60)} h`
      : `Queued, about ${minutes} min`
  }
  return 'Queued'
}

export interface AnalysisLine {
  text: string
  tone: 'negative' | 'neutral'
  /** retry: the last analysis failed. analyze_now: an automatic analysis is on hold. */
  action: 'retry' | 'analyze_now' | null
}

const clockTime = (at: number) =>
  new Date(at * 1000).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })

const sentence = (text: string) => (/[.!?]$/.test(text) ? text : `${text}.`)

/**
 * One line on an analysis that's waiting or didn't finish: on hold and why, queued and
 * when it starts, or the failure's reason. Null when there's nothing to say.
 */
export function analysisLine(
  query: MonitorQuery,
  job: AnalyzeJob | null | undefined,
  now: number
): AnalysisLine | null {
  const analysis = query.last_analysis
  const live = job?.state === 'queued' ? job : null
  if (live || analysis?.state === 'queued') {
    const held = (live?.queue_state ?? analysis?.queue_state) === 'held'
    if (held) {
      const why =
        live?.reason_text ??
        analysis?.reason ??
        'Automatic analysis is off. Click Analyze now to run this one.'
      return {
        text: `On hold: ${sentence(why)}`,
        tone: 'neutral',
        action: live?.reason === 'user' ? null : 'analyze_now',
      }
    }
    return {
      text: jobLine(live, now) ?? 'Queued',
      tone: 'neutral',
      action: null,
    }
  }
  if (analysis?.state !== 'failed') return null
  if (analysis.error_kind === 'retrying')
    return {
      text: analysis.retry_at
        ? `The AI service didn't answer this time. RDST tries again at ${clockTime(analysis.retry_at)}.`
        : 'Retrying soon.',
      tone: 'neutral',
      action: null,
    }
  return {
    text: `The last analysis didn't finish. ${sentence(analysis.reason ?? 'The analysis stopped before it wrote a result')}`,
    tone: 'negative',
    action: 'retry',
  }
}

function status(
  id: QueryStatusId,
  reason: string | null,
  itemId: number | null = null
): QueryStatus {
  return {
    id,
    label: STATUS_LABELS[id],
    tone: STATUS_TONES[id],
    reason,
    itemId,
  }
}

export interface StatusInputs {
  signal?: InboxSignal | null
  gate: Analyzability
  job?: AnalyzeJob | null
  now?: number
}

export function queryStatus(
  query: MonitorQuery,
  { signal, gate, job, now = Date.now() / 1000 }: StatusInputs
): QueryStatus {
  if (signal)
    return status(
      signal.level === 'attention' ? 'attention' : 'watch',
      signal.reason,
      signal.itemId
    )
  const analysis = query.last_analysis
  const summary = analysis?.summary
  if (summary?.fix_ready) return status('fix_ready', fixReason(query))
  if (summary || analysis?.state === 'done') return status('healthy', null)
  // A statement RDST never analyzes says so, whatever a stale job claims: no "yet".
  if (!gate.ok)
    return { ...status('not_analyzed', gate.reason), label: 'Not analyzed' }
  const state = analysis?.state
  if (state === 'running' && analysis?.error_kind === 'retrying')
    return status('not_analyzed', 'Retrying')
  const waiting =
    jobLine(job, now) ??
    (state === 'running'
      ? 'Analyzing now'
      : state === 'queued'
        ? 'Queued'
        : null)
  if (waiting) return status('not_analyzed', waiting)
  // A retry on its way shows on the row's activity, not as a failure line.
  if (state === 'failed' && analysis?.error_kind === 'retrying')
    return status('not_analyzed', null)
  if (state === 'failed')
    return status('not_analyzed', analysis?.reason ?? null)
  return status('not_analyzed', null)
}

/** Queries with an open Inbox item: the "Needs attention" count everywhere. */
export function attentionCount(
  queries: MonitorQuery[],
  signals: Map<string, InboxSignal>
): number {
  return queries.filter((query) => signals.has(query.key)).length
}
