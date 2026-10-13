// How the agent activity log reads: one declarative sentence per decision, grouped by day.
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import type {
  ActivityQuery,
  AgentAction,
  AgentActivityItem,
  AgentAiCall,
  AgentOutcome,
  AgentStatement,
} from '@/lib/agentActivity'

export const ACTIONS: readonly AgentAction[] = [
  'deep_analysis',
  'ask',
  'daily_snapshot',
  'explain_actions',
  'classification',
  'attention_check',
  'benchmark',
]

/** The color family a kind of work wears: a soft tile behind its icon. */
export type Tone =
  | 'rising'
  | 'info'
  | 'positive'
  | 'warning'
  | 'primary'
  | 'neutral'

export const ACTION_META: Record<
  AgentAction,
  { label: string; icon: IconStrokeName; tone: Tone }
> = {
  deep_analysis: { label: 'Deep analysis', icon: 'test-tube', tone: 'rising' },
  ask: { label: 'Ask', icon: 'message-multiple', tone: 'info' },
  daily_snapshot: {
    label: 'Daily report',
    icon: 'document-validation',
    tone: 'positive',
  },
  explain_actions: {
    label: 'Action explanations',
    icon: 'sparkles',
    tone: 'primary',
  },
  classification: {
    label: 'Query classification',
    icon: 'layers',
    tone: 'primary',
  },
  attention_check: {
    label: 'Attention checks',
    icon: 'observe',
    tone: 'warning',
  },
  benchmark: { label: 'Benchmark', icon: 'speedometer', tone: 'neutral' },
}

const SKIPPED_ANALYSIS = {
  label: 'Skipped deep analysis',
  icon: 'lock-sync',
  tone: 'neutral',
} as const

export function itemStyle(item: AgentActivityItem): {
  label: string
  icon: IconStrokeName
  tone: Tone
} {
  if (
    item.fold === 'skipped_analysis' ||
    (item.action === 'deep_analysis' && item.outcome === 'skipped')
  )
    return SKIPPED_ANALYSIS
  return ACTION_META[item.action]
}

export type TagVariant = 'positive' | 'negative' | 'neutral' | 'warning'

export function isAction(value: unknown): value is AgentAction {
  return typeof value === 'string' && ACTIONS.includes(value as AgentAction)
}

export function plural(count: number, noun: string, many = `${noun}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? noun : many}`
}

/** A failure reason without the error code in front ("LOGIN_REQUIRED: Sign in..."). */
export function plainReason(text: string | null | undefined): string {
  const reason = (text ?? '').replace(/^[A-Z][A-Z0-9_]{2,}:\s*/, '').trim()
  return reason.replace(/[.\s]+$/, '')
}

function quoted(text: string): string {
  const trimmed = text.trim().replace(/\s+/g, ' ')
  return `"${trimmed.length > 90 ? `${trimmed.slice(0, 89)}...` : trimmed}"`
}

/** "Mon, Sep 28" for a local day. */
export function shortDay(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

/** The local day a daily report covers: its recorded period, else the day before a
 * nightly run or the day of one you asked for. */
export function reportDay(item: AgentActivityItem): {
  day: string
  partial: boolean
} {
  const nightly = item.subject !== 'On request'
  if (item.period_start != null && item.period_end != null)
    return {
      day: shortDay(item.period_start),
      partial: !nightly || item.period_end - item.period_start < 82_800,
    }
  return {
    day: shortDay(nightly ? item.ts - 43_200 : item.ts),
    partial: !nightly,
  }
}

/** A piece of an activity sentence; `strong` marks what it's about. */
export interface SentencePart {
  text: string
  strong?: boolean
}

interface Strong {
  strong: string
}

const b = (text: string): Strong => ({ strong: text })

/** A tagged template that keeps `b(...)` values apart, so the page can emphasize them. */
function s(
  strings: TemplateStringsArray,
  ...values: (string | Strong)[]
): SentencePart[] {
  const parts: SentencePart[] = []
  strings.forEach((text, index) => {
    if (text) parts.push({ text })
    const value = values[index]
    if (value === undefined) return
    if (typeof value === 'string') {
      if (value) parts.push({ text: value })
    } else parts.push({ text: value.strong, strong: true })
  })
  return parts
}

function analysisSentence(item: AgentActivityItem): SentencePart[] {
  const subject = b(item.subject || 'a query')
  switch (item.outcome) {
    case 'done':
      return item.finding
        ? s`The agent analyzed ${subject} and ${item.finding}.`
        : s`The agent analyzed ${subject}.`
    case 'failed':
      return s`The agent couldn't finish analyzing ${subject}.`
    case 'cancelled':
      return s`The agent stopped analyzing ${subject} before it finished.`
    case 'skipped':
      return s`The agent skipped ${subject} without running it.`
  }
}

function askSentence(item: AgentActivityItem): SentencePart[] {
  const question = item.question ? b(quoted(item.question)) : null
  const tables = item.subject ? ` using ${item.subject}` : ''
  if (item.outcome === 'failed' || item.outcome === 'cancelled')
    return question
      ? s`The agent couldn't answer ${question}.`
      : s`The agent couldn't answer a question.`
  if (item.decision?.startsWith('Asked you'))
    return question
      ? s`The agent asked you to clarify ${question}.`
      : s`The agent asked you to clarify a question.`
  if (question) return s`The agent answered ${question}${tables}.`
  return item.subject
    ? s`The agent answered a question about ${b(item.subject)}.`
    : s`The agent answered a question.`
}

function checksSentence(item: AgentActivityItem): SentencePart[] {
  if (item.kind === 'entry') {
    if (item.outcome === 'failed')
      return s`The agent's attention check didn't complete.`
    if (item.outcome === 'skipped')
      return s`The agent skipped an attention check${item.summary ? `: ${item.summary.toLowerCase()}` : ''}.`
    return item.items_filed
      ? s`The agent ran an attention check and flagged ${b(plural(item.items_filed, 'change'))} for the Inbox.`
      : s`The agent ran an attention check and found nothing new to file.`
  }
  const checks = b(plural(item.count, 'attention check'))
  return item.items_filed
    ? s`The agent ran ${checks} and flagged ${b(plural(item.items_filed, 'change'))} for the Inbox.`
    : s`The agent ran ${checks} and found nothing new to file.`
}

function classificationSentence(item: AgentActivityItem): SentencePart[] {
  if (item.kind === 'entry') {
    const subject = b(item.subject || 'a query')
    if (item.outcome === 'done')
      return item.decision
        ? s`The agent classified ${subject}: ${item.decision.toLowerCase()}.`
        : s`The agent classified ${subject}.`
    if (item.outcome === 'failed')
      return s`The agent couldn't classify ${subject}.`
    return s`The agent set ${subject} aside without classifying it.`
  }
  const aside = item.count - item.done - item.failed
  if (item.done === 0)
    return s`The agent set ${b(plural(item.count, 'query', 'queries'))} aside without classifying them.`
  const classified = b(plural(item.done, 'query', 'queries'))
  return aside > 0
    ? s`The agent classified ${classified} and set ${aside.toLocaleString()} aside.`
    : s`The agent classified ${classified}.`
}

function reportSentence(item: AgentActivityItem): SentencePart[] {
  const { day, partial } = reportDay(item)
  if (item.outcome === 'failed' || item.outcome === 'cancelled')
    return s`The agent couldn't finish the daily report for ${b(day)}.`
  return partial
    ? s`The agent wrote a report for ${b(day)} so far, on request.`
    : s`The agent wrote the daily report for ${b(day)}.`
}

function explainSentence(item: AgentActivityItem): SentencePart[] {
  const changes = b(item.subject || 'new changes')
  return item.outcome === 'done'
    ? s`The agent explained why ${changes} matter on Actions.`
    : s`The agent couldn't explain ${changes} on Actions.`
}

function benchmarkSentence(item: AgentActivityItem): SentencePart[] {
  const subject = b(item.subject || 'saved queries')
  return item.outcome === 'done'
    ? s`The agent benchmarked ${subject}.`
    : s`The agent's benchmark of ${subject} didn't finish.`
}

/** The one sentence that says what the agent did and how it went, in parts. Inside a
 * folded line the agent is already named, so a nested sentence starts at the verb. */
export function sentenceParts(
  item: AgentActivityItem,
  nested = false
): SentencePart[] {
  const parts = fullSentence(item)
  if (!nested || !parts[0]) return parts
  const first = parts[0].text
    .replace(/^The agent's /, 'The ')
    .replace(/^The agent /, '')
  return [
    { ...parts[0], text: first.charAt(0).toUpperCase() + first.slice(1) },
    ...parts.slice(1),
  ]
}

function fullSentence(item: AgentActivityItem): SentencePart[] {
  if (item.fold === 'skipped_analysis')
    return s`The agent skipped ${b(plural(item.count, 'query', 'queries'))} it doesn't run, such as writes and schema changes.`
  switch (item.action) {
    case 'deep_analysis':
      return analysisSentence(item)
    case 'ask':
      return askSentence(item)
    case 'attention_check':
      return checksSentence(item)
    case 'classification':
      return classificationSentence(item)
    case 'daily_snapshot':
      return reportSentence(item)
    case 'explain_actions':
      return explainSentence(item)
    case 'benchmark':
      return benchmarkSentence(item)
  }
}

export function activitySentence(item: AgentActivityItem): string {
  return sentenceParts(item)
    .map((part) => part.text)
    .join('')
}

/** The quieter line under the sentence: why it failed or was skipped, or how many parts
 * of a folded line didn't complete. */
export function activityNote(item: AgentActivityItem): string | null {
  if (item.kind === 'rollup') {
    if (item.fold === 'skipped_analysis')
      return 'No statements ran and no AI was used.'
    if (
      item.fold === 'classification' &&
      item.done === 0 &&
      !item.ai_call_count
    )
      return 'No AI was used.'
    if (item.failed)
      return item.failed === item.count
        ? 'None of them completed.'
        : `${item.failed.toLocaleString()} didn't complete.`
    return null
  }
  if (item.outcome !== 'done') {
    const reason = plainReason(item.summary)
    return reason ? `${reason}.` : null
  }
  if (item.action === 'benchmark' && item.summary) return `${item.summary}.`
  return null
}

/** A tag only when the outcome needs a second look; the sentence says the rest. */
export function outcomeTag(
  item: AgentActivityItem
): { label: string; variant: TagVariant } | null {
  if (item.kind === 'rollup') {
    if (item.failed && item.failed === item.count)
      return { label: 'Failed', variant: 'negative' }
    return null
  }
  const meta: Partial<
    Record<AgentOutcome, { label: string; variant: TagVariant }>
  > = {
    failed: { label: 'Failed', variant: 'negative' },
    cancelled: { label: 'Stopped', variant: 'neutral' },
  }
  return meta[item.outcome] ?? null
}

export function clockTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** When it happened: a clock time, or the span a folded line covers. */
export function itemTime(item: AgentActivityItem): string {
  if (
    item.kind === 'rollup' &&
    item.group === 'hour' &&
    item.bucket_start != null
  )
    return `${clockTime(item.bucket_start)} to ${clockTime(item.bucket_start + 3600)}`
  if (item.kind === 'rollup' && item.first_ts !== item.ts)
    return `${clockTime(item.first_ts)} to ${clockTime(item.ts)}`
  return clockTime(item.ts)
}

/** Local calendar day, `YYYY-MM-DD`. */
export function dayKey(ts: number): string {
  const date = new Date(ts * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** Epoch seconds of local midnight starting `YYYY-MM-DD`. */
export function dayStart(day: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!match) return null
  const start = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3])
  )
  return Number.isNaN(start.getTime()) ? null : start.getTime() / 1000
}

/** Local midnight `days` days after the one starting at `ts`. */
function addDays(ts: number, days: number): number {
  const date = new Date(ts * 1000)
  date.setHours(0, 0, 0, 0)
  date.setDate(date.getDate() + days)
  return date.getTime() / 1000
}

export function dayLabel(day: string, now: number): string {
  if (day === dayKey(now)) return 'Today'
  if (day === dayKey(addDays(now, -1))) return 'Yesterday'
  const start = dayStart(day)
  if (start === null) return day
  const date = new Date(start * 1000)
  return date.toLocaleDateString([], {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
    ...(date.getFullYear() !== new Date(now * 1000).getFullYear()
      ? { year: 'numeric' }
      : {}),
  })
}

export interface DayGroup {
  day: string
  label: string
  /** The date itself, beside "Today" and "Yesterday". */
  date: string | null
  items: AgentActivityItem[]
}

/** Newest first, one group per local day. */
export function groupByDay(
  items: AgentActivityItem[],
  now: number
): DayGroup[] {
  const groups: DayGroup[] = []
  for (const item of items) {
    const day = dayKey(item.ts)
    let group = groups[groups.length - 1]
    if (!group || group.day !== day) {
      const label = dayLabel(day, now)
      const start = dayStart(day)
      group = {
        day,
        label,
        date:
          (label === 'Today' || label === 'Yesterday') && start !== null
            ? shortDay(start)
            : null,
        items: [],
      }
      groups.push(group)
    }
    group.items.push(item)
  }
  return groups
}

/** The time ranges the page and the export offer. */
export type RangePreset = 'today' | '7d' | '30d' | 'all'

export const RANGE_LABEL: Record<RangePreset, string> = {
  today: 'Today',
  '7d': 'Last 7 days',
  '30d': 'Last 30 days',
  all: 'Last 90 days',
}

export function isRange(value: unknown): value is Exclude<RangePreset, 'all'> {
  return value === 'today' || value === '7d' || value === '30d'
}

/** [since, until) of a preset, in whole local days ending today. */
export function rangeBounds(
  range: RangePreset,
  now: number
): { since?: number; until?: number } {
  const days = { today: 1, '7d': 7, '30d': 30, all: 0 }[range]
  if (!days) return {}
  return { since: addDays(now, 1 - days), until: addDays(now, 1) }
}

/** [since, until) of a custom from/to pair of local days, both included. */
export function customBounds(
  from: string,
  to: string
): { since: number; until: number } | null {
  const since = dayStart(from)
  const last = dayStart(to)
  if (since === null || last === null || last < since) return null
  return { since, until: addDays(last, 1) }
}

export function toQuery(filters: {
  target?: string
  action?: AgentAction
  range?: RangePreset
  now: number
}): ActivityQuery {
  return {
    target: filters.target,
    action: filters.action,
    ...rangeBounds(filters.range ?? 'all', filters.now),
  }
}

/** What opening a folded line lists: a day of checks opens to its hours, an hour to
 * each check, a day of classifications to the queries classified. */
export function expandQuery(item: AgentActivityItem): ActivityQuery {
  const since = item.bucket_start ?? item.first_ts
  const until = item.bucket_end ?? item.ts + 1
  const base = { target: item.target, action: item.action, since, until }
  if (item.fold === 'attention_check' && item.group === 'day')
    return { ...base, group: 'hour' }
  if (item.fold === 'skipped_analysis')
    return { ...base, outcome: 'skipped', group: 'none' }
  if (item.fold === 'classification' && item.done > 0)
    return { ...base, outcome: 'done', group: 'none' }
  return { ...base, group: 'none' }
}

/** Every entry behind a folded line, for its technical details. */
export function allEntriesQuery(item: AgentActivityItem): ActivityQuery {
  return { ...expandQuery(item), outcome: undefined, group: 'none' }
}

export const STATEMENT_KIND_LABEL: Record<string, string> = {
  explain: 'EXPLAIN, plans the query without running it',
  explain_analyze: 'EXPLAIN ANALYZE, runs the query to time it',
  sample: 'Parameter sampling read',
  catalog: 'Statistics or catalog read',
  session: 'Session setting',
  hypothetical_index: 'Hypothetical index check, nothing is built',
  query: 'Your Ask query',
  benchmark: 'Benchmark runs',
  read: 'Read',
}

export function statementKindLabel(kind: string): string {
  return STATEMENT_KIND_LABEL[kind] ?? kind.replace(/_/g, ' ')
}

export function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10_000 ? 0 : 1)} s`
  if (ms >= 10) return `${Math.round(ms)} ms`
  return `${ms.toFixed(1)} ms`
}

/** "3.2 ms", "12 runs, 4.1 ms average", with the timeout when one applied. */
export function statementTiming(statement: AgentStatement): string {
  const parts: string[] = []
  const runs = statement.executions || 1
  if (statement.duration_ms !== null && statement.duration_ms !== undefined)
    parts.push(
      runs > 1
        ? `${plural(runs, 'run')}, ${formatMs(statement.duration_ms / runs)} average`
        : formatMs(statement.duration_ms)
    )
  else if (runs > 1) parts.push(plural(runs, 'run'))
  if (statement.timeout_ms)
    parts.push(`timeout ${formatMs(statement.timeout_ms)}`)
  return parts.join(', ')
}

export function formatCost(
  usd: number | null | undefined,
  estimated = false
): string {
  if (usd === null || usd === undefined) return 'Cost unknown'
  const text =
    usd === 0
      ? '$0'
      : usd < 0.0001
        ? 'under $0.0001'
        : `$${usd < 0.01 ? usd.toFixed(4) : usd.toFixed(2)}`
  return estimated && usd >= 0.0001 ? `about ${text}` : text
}

export function formatTokens(
  call: Pick<AgentAiCall, 'tokens_in' | 'tokens_out'>
): string {
  if (call.tokens_in === null || call.tokens_in === undefined)
    return 'Tokens unknown'
  return `${call.tokens_in.toLocaleString()} in, ${(call.tokens_out ?? 0).toLocaleString()} out`
}

/** "2 statements on your database, 1 AI call" for a technical details summary. */
export function technicalSummary(counts: {
  statement_count: number
  ai_call_count: number
}): string {
  const parts = [
    counts.statement_count
      ? plural(counts.statement_count, 'statement')
      : 'No statements',
    counts.ai_call_count
      ? plural(counts.ai_call_count, 'AI call')
      : 'no AI calls',
  ]
  return `${parts.join(', ')}`
}

const PURPOSE_LABEL: Record<string, string> = {
  monitor_analyze: 'Advice on the plan',
  analyze_query: 'Advice on the plan',
  analyze_index: 'Index advice',
  analyze_rewrite: 'Rewrite advice',
  analyze_cache: 'Caching advice',
  monitor_attention: 'Does anything need attention',
  query_registry_analysis: 'Classify the query',
  monitor_report: 'Write the daily report',
  sql_generation: 'Write SQL for your question',
  sql_generation_alternate: 'Write SQL for your question',
  sql_recovery: 'Fix SQL that failed',
  sql_refinement: 'Refine the SQL',
  sql_validation_repair: 'Fix SQL that failed checks',
  clarification: 'Check whether your question is ambiguous',
}

export function purposeLabel(
  call: Pick<AgentAiCall, 'operation' | 'purpose'>
): string {
  const label = PURPOSE_LABEL[call.operation] ?? PURPOSE_LABEL[call.purpose]
  if (label) return label
  const text = (call.operation || call.purpose || 'other').replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// The Activity page: one verb-first line per entry, where it leads, and the day it sits under.

/** The column at the right of a row: the page the row opens. */
export type ActivityPlace =
  | 'Analysis'
  | 'Database'
  | 'Queries'
  | 'Report'
  | 'Actions'
  | 'Ask'
  | 'Benchmarks'

export interface ActivityLine {
  /** Verb first: "Analyzed SELECT on users by id", "Checked for changes". */
  text: string
  /** One detail in the third tone, or in the warn tone when something did not complete. */
  detail: string | null
  warn: boolean
  place: ActivityPlace
  link: { to: string; search?: Record<string, unknown> }
}

/** "October 7" for a local day. */
export function longDay(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
  })
}

/** The day label over a group: Today, Yesterday, then the date. */
export function dayHeading(day: string, now: number): string {
  const label = dayLabel(day, now)
  if (label === 'Today' || label === 'Yesterday') return label
  const start = dayStart(day)
  return start === null ? day : longDay(start)
}

function target(item: AgentActivityItem) {
  return item.target ? { target: item.target } : {}
}

function analysisLine(item: AgentActivityItem): ActivityLine {
  const subject = item.subject || 'a query'
  const link =
    item.query_key && item.outcome === 'done'
      ? {
          to: '/database',
          search: { ...target(item), query: item.query_key, tab: 'analysis' },
        }
      : { to: '/database', search: target(item) }
  if (item.kind === 'rollup')
    return {
      text: `Skipped ${plural(item.count, 'query', 'queries')}`,
      detail: 'writes and schema changes',
      warn: false,
      place: 'Queries',
      link,
    }
  switch (item.outcome) {
    case 'done':
      return {
        text: `Analyzed ${subject}`,
        detail: item.finding ?? null,
        warn: false,
        place: 'Analysis',
        link,
      }
    case 'failed':
      return {
        text: `Couldn't finish analyzing ${subject}`,
        detail: plainReason(item.summary) || null,
        warn: true,
        place: 'Analysis',
        link,
      }
    case 'cancelled':
      return {
        text: `Stopped analyzing ${subject}`,
        detail: null,
        warn: false,
        place: 'Analysis',
        link,
      }
    case 'skipped':
      return {
        text: `Skipped ${subject}`,
        detail: plainReason(item.summary) || null,
        warn: false,
        place: 'Queries',
        link,
      }
  }
}

function checksLine(item: AgentActivityItem): ActivityLine {
  const link = { to: '/database', search: target(item) }
  const filed = item.items_filed
    ? `${plural(item.items_filed, 'change')} filed`
    : 'nothing new'
  if (item.kind === 'rollup')
    return {
      text: 'Checked for changes',
      detail: item.failed
        ? `${item.failed} of ${item.count} didn't complete`
        : `${plural(item.count, 'check')}, ${filed}`,
      warn: item.failed > 0,
      place: 'Database',
      link,
    }
  if (item.outcome === 'failed')
    return {
      text: 'Checked for changes',
      detail: `didn't complete${plainReason(item.summary) ? `, ${plainReason(item.summary).toLowerCase()}` : ''}`,
      warn: true,
      place: 'Database',
      link,
    }
  return {
    text: 'Checked for changes',
    detail:
      item.outcome === 'skipped' ? plainReason(item.summary) || null : filed,
    warn: false,
    place: 'Database',
    link,
  }
}

function classificationLine(item: AgentActivityItem): ActivityLine {
  const link = { to: '/database', search: target(item) }
  if (item.kind === 'rollup') {
    const aside = item.count - item.done - item.failed
    const notes = [
      aside > 0 ? `${aside} set aside` : null,
      item.failed > 0 ? `${item.failed} didn't complete` : null,
    ].filter((note) => note !== null)
    return {
      text: item.done
        ? `Classified ${plural(item.done, 'query', 'queries')}`
        : `Couldn't classify ${plural(item.count, 'query', 'queries')}`,
      detail: item.done && notes.length ? notes.join(', ') : null,
      warn: item.failed > 0,
      place: 'Queries',
      link,
    }
  }
  return {
    text:
      item.outcome === 'done'
        ? `Classified ${item.subject || 'a query'}`
        : `Set ${item.subject || 'a query'} aside`,
    detail: item.decision ? item.decision.toLowerCase() : null,
    warn: item.outcome === 'failed',
    place: 'Queries',
    link,
  }
}

function reportLine(item: AgentActivityItem): ActivityLine {
  const { partial } = reportDay(item)
  const start =
    item.period_start ??
    (item.subject !== 'On request' ? item.ts - 43_200 : item.ts)
  const link =
    item.report_id != null
      ? {
          to: '/database',
          search: {
            ...target(item),
            view: 'report',
            report: String(item.report_id),
          },
        }
      : { to: '/database', search: { ...target(item), view: 'report' } }
  if (item.outcome === 'failed' || item.outcome === 'cancelled')
    return {
      text: `Couldn't finish the daily report for ${longDay(start)}`,
      detail: plainReason(item.summary) || null,
      warn: true,
      place: 'Report',
      link,
    }
  return partial
    ? {
        text: 'Report ran',
        detail: 'on request',
        warn: false,
        place: 'Report',
        link,
      }
    : {
        text: `Daily report for ${longDay(start)}`,
        detail: item.finding ?? null,
        warn: false,
        place: 'Report',
        link,
      }
}

function askLine(item: AgentActivityItem): ActivityLine {
  const link = { to: '/ask', search: target(item) }
  const question = item.question ? quotedText(item.question) : 'a question'
  if (item.outcome === 'failed' || item.outcome === 'cancelled')
    return {
      text: `Couldn't answer ${question}`,
      detail: plainReason(item.summary) || null,
      warn: true,
      place: 'Ask',
      link,
    }
  return {
    text: item.decision?.startsWith('Asked you')
      ? `Asked you to clarify ${question}`
      : `Answered ${question}`,
    detail: item.subject || null,
    warn: false,
    place: 'Ask',
    link,
  }
}

function quotedText(text: string): string {
  const trimmed = text.trim().replace(/\s+/g, ' ')
  return `"${trimmed.length > 90 ? `${trimmed.slice(0, 89)}...` : trimmed}"`
}

function benchmarkLine(item: AgentActivityItem): ActivityLine {
  const subject = item.subject || 'saved queries'
  return {
    text:
      item.outcome === 'done'
        ? `Benchmarked ${subject}`
        : `Benchmark of ${subject} didn't finish`,
    detail: item.summary || null,
    warn: item.outcome !== 'done',
    place: 'Benchmarks',
    link: { to: '/cache', search: target(item) },
  }
}

function explainLine(item: AgentActivityItem): ActivityLine {
  const changes = item.subject || 'new changes'
  const done = item.outcome === 'done'
  return {
    text: done
      ? `Explained why ${changes} matter on Actions`
      : `Couldn't explain ${changes} on Actions`,
    detail: done ? null : plainReason(item.summary) || null,
    warn: !done,
    place: 'Actions',
    link: { to: '/actions', search: item.target ? { db: item.target } : {} },
  }
}

export function activityLine(item: AgentActivityItem): ActivityLine {
  switch (item.action) {
    case 'deep_analysis':
      return analysisLine(item)
    case 'attention_check':
      return checksLine(item)
    case 'classification':
      return classificationLine(item)
    case 'daily_snapshot':
      return reportLine(item)
    case 'explain_actions':
      return explainLine(item)
    case 'ask':
      return askLine(item)
    case 'benchmark':
      return benchmarkLine(item)
  }
}
