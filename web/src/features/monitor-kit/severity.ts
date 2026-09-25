// Inbox severity, tone and alert-type vocabulary, shared by the Inbox and the Database
// page (Muted badge, row menu). The backend decides `severity_band` and `tone`
// (brief 5.7); the fallbacks here mirror its rules for items that predate those
// fields.
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import type {
  InboxItem,
  InboxTone,
  MuteRule,
  SeverityBand,
} from '@/lib/monitor/types'

export const SEVERITY_BANDS: readonly SeverityBand[] = [
  'act_now',
  'watch',
  'fyi',
  'recovered',
] as const

export interface BandMeta {
  label: string
  hint: string
  tone: InboxTone
}

export const BAND_META: Record<SeverityBand, BandMeta> = {
  act_now: {
    label: 'Act now',
    hint: 'Hurting the database now',
    tone: 'negative',
  },
  watch: {
    label: 'Watch',
    hint: 'Worth a look today',
    tone: 'warning',
  },
  fyi: {
    label: 'FYI',
    hint: 'Reports and small changes',
    tone: 'neutral',
  },
  recovered: {
    label: 'Recovered',
    hint: 'Measured back to normal, not counted as open',
    tone: 'positive',
  },
}

const ACT_NOW_TYPES = new Set(['stuck_query', 'monitoring_issue'])
const FYI_TYPES = new Set(['daily_summary', 'query_improved'])
const GOOD_NEWS_TYPES = new Set(['query_improved'])

export function bandFor(type: string, severity: number): SeverityBand {
  if (severity >= 0.85 || ACT_NOW_TYPES.has(type)) return 'act_now'
  if (FYI_TYPES.has(type)) return 'fyi'
  return severity >= 0.6 ? 'watch' : 'fyi'
}

export function itemBand(
  item: Pick<InboxItem, 'type' | 'severity'> & {
    severity_band?: SeverityBand | null
  }
): SeverityBand {
  return item.severity_band ?? bandFor(item.type, item.severity)
}

export const isGoodNews = (type: string) => GOOD_NEWS_TYPES.has(type)

export function itemTone(
  item: Pick<InboxItem, 'type' | 'severity'> & {
    severity_band?: SeverityBand | null
    tone?: InboxTone | null
  }
): InboxTone {
  if (item.tone) return item.tone
  if (isGoodNews(item.type)) return 'positive'
  return BAND_META[itemBand(item)].tone
}

/** Unread badge rule (brief 2.1): new, not muted, and act now or watch. */
export function countsAsUnread(item: InboxItem): boolean {
  return (
    item.state === 'new' && item.muted_by === null && itemBand(item) !== 'fyi'
  )
}

// Tone classes. Neutral stays neutral: color is reserved for state.
export const TONE_ICON_CLASS: Record<InboxTone, string> = {
  negative: 'bg-surface-negative-soft text-content-negative-soft',
  warning: 'bg-surface-warning-soft text-content-warning-soft',
  positive: 'bg-surface-positive-soft text-content-positive-soft',
  neutral: 'bg-surface-layout-2 text-content-layout-3',
}

/** Section colour coding: red act now, amber watch, grey FYI, green recovered. */
export const BAND_ACCENT_CLASS: Record<SeverityBand, string> = {
  act_now: 'before:bg-content-negative-soft',
  watch: 'before:bg-content-warning-soft',
  fyi: 'before:bg-border-layout-2',
  recovered: 'before:bg-content-positive-soft',
}

export const BAND_DOT_CLASS: Record<SeverityBand, string> = {
  act_now: 'bg-content-negative-soft',
  watch: 'bg-content-warning-soft',
  fyi: 'bg-content-layout-3',
  recovered: 'bg-content-positive-soft',
}

export const BAND_COUNT_CLASS: Record<SeverityBand, string> = {
  act_now: 'bg-surface-negative-soft text-content-negative-soft',
  watch: 'bg-surface-warning-soft text-content-warning-soft',
  fyi: 'bg-surface-layout-2 text-content-layout-2',
  recovered: 'bg-surface-positive-soft text-content-positive-soft',
}

export const TONE_EDGE_CLASS: Record<InboxTone, string> = {
  negative: 'border-l-border-negative-soft',
  warning: 'border-l-border-warning-soft',
  positive: 'border-l-border-positive-soft',
  neutral: 'border-l-transparent',
}

export const TONE_TEXT_CLASS: Record<InboxTone, string> = {
  negative: 'text-content-negative-soft',
  warning: 'text-content-warning-soft',
  positive: 'text-content-positive-soft',
  neutral: 'text-content-layout-3',
}

interface AlertTypeMeta {
  /** Sentence-case noun phrase used in menus and mute rules. */
  label: string
  icon: IconStrokeName
}

const ALERT_TYPES: Record<string, AlertTypeMeta> = {
  many_queries_changed: { label: 'Many queries changed', icon: 'layers' },
  stuck_query: { label: 'Stuck query', icon: 'alert' },
  latency_regression: { label: 'Query got slower', icon: 'speedometer' },
  slow_creep: { label: 'Latency crept up', icon: 'speedometer' },
  table_slowdown: { label: 'Table got slower', icon: 'database' },
  plan_change: { label: 'Plan may have changed', icon: 'layers' },
  traffic_spike: { label: 'Traffic spike', icon: 'arrow-up' },
  rollback_or_deadlock_spike: {
    label: 'Rollbacks or deadlocks rose',
    icon: 'alert',
  },
  connections_near_max: {
    label: 'Connections near maximum',
    icon: 'connect',
  },
  lock_contention: { label: 'Waiting on locks', icon: 'alert' },
  health_finding: { label: 'Health check', icon: 'database-settings' },
  new_heavy_query: { label: 'New heavy query', icon: 'database' },
  cache_candidate: { label: 'Cache candidate', icon: 'database-settings' },
  query_improved: { label: 'Query got faster', icon: 'arrow-down' },
  query_stopped_running: { label: 'Query stopped running', icon: 'minus' },
  daily_summary: { label: 'Daily report', icon: 'document-validation' },
  cache_hit_ratio_drop: {
    label: 'Cache hit ratio dropped',
    icon: 'arrow-down',
  },
  monitoring_issue: { label: 'Monitoring problem', icon: 'alert' },
}

function humanize(type: string): string {
  const words = type.replaceAll(/[_-]+/g, ' ').trim().toLowerCase()
  return words ? words[0].toUpperCase() + words.slice(1) : 'Alert'
}

export function alertTypeLabel(type: string): string {
  return ALERT_TYPES[type]?.label ?? humanize(type)
}

export function alertTypeIcon(type: string, tone: InboxTone): IconStrokeName {
  if (tone === 'positive') return 'tick'
  const known = ALERT_TYPES[type]?.icon
  if (known) return known
  if (tone === 'negative' || tone === 'warning') return 'alert'
  return 'notification'
}

// Mute rules

export type MuteScope = 'query_type' | 'query' | 'type_target' | 'type_all'

export function ruleIsActive(rule: MuteRule, now = Date.now() / 1000) {
  return rule.until === null || rule.until > now
}

export function ruleMatches(
  rule: MuteRule,
  item: Pick<InboxItem, 'target' | 'type' | 'subject' | 'query_keys'>,
  now = Date.now() / 1000
): boolean {
  if (!ruleIsActive(rule, now)) return false
  if (rule.target !== null && rule.target !== item.target) return false
  if (rule.type !== null && rule.type !== item.type) return false
  if (rule.query_key !== null) {
    const keys = item.query_keys?.length ? item.query_keys : [item.subject]
    if (!keys.includes(rule.query_key)) return false
  }
  return true
}

/** "Query stopped running on localdev", "All alerts for Select - orders on localdev". */
export function describeRule(
  rule: Pick<MuteRule, 'target' | 'type' | 'query_key'>,
  queryLabel?: string
): string {
  const where = rule.target ? `on ${rule.target}` : 'on all targets'
  const query = queryLabel ?? 'one query'
  if (rule.query_key && rule.type)
    return `${alertTypeLabel(rule.type)} for ${query} ${where}`
  if (rule.query_key) return `All alerts for ${query} ${where}`
  return `${alertTypeLabel(rule.type ?? '')} ${where}`
}

export function describeRuleDuration(
  rule: Pick<MuteRule, 'until'>,
  now = Date.now() / 1000
): string {
  if (rule.until === null) return 'Muted forever'
  const left = rule.until - now
  if (left <= 0) return 'Expired'
  const hours = Math.round(left / 3600)
  if (hours < 1) return 'Muted for less than an hour more'
  if (hours < 48)
    return `Muted for ${hours} more ${hours === 1 ? 'hour' : 'hours'}`
  const days = Math.round(hours / 24)
  return `Muted for ${days} more days`
}
