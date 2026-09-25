// What the Database page's Inbox banner says: what happened to this query (or this time
// frame) and when, measured, then whether it's still happening.
import type { InboxItem } from '@/lib/monitor/types'
import { plural } from '../monitor-kit/format'
import { formatStampTime, timePhrase } from './inboxModel'
import { changeSize, incidentWindow } from './incidentModel'
import { latencyText, rateText } from './itemStory'
import {
  clusterLabel,
  clusterOnsets,
  clusterTime,
  clusterVerb,
  type MemberJump,
  memberJumps,
  type OnsetCluster,
} from './onsetClusterModel'

export interface BannerText {
  /** 'Calls went from 7.1 a second to 12.6 a second at 9:42 PM.' */
  lead: string
  /**
   * 'Latency measured back to normal at 10:15 PM.', 'Still happening.' or null when
   * unknown: which reading recovered, as RDST measured it.
   */
  state: string | null
  /** For a group member: '8 other queries jumped from 3:04 to 3:05 PM too.' */
  note: string | null
  tone: 'ongoing' | 'over' | 'unknown'
}

/** A move smaller than this isn't worth numbers in a sentence. */
const NOTICEABLE = 0.05

const LATENCY_TYPES = new Set([
  'latency_regression',
  'plan_change',
  'table_slowdown',
  'slow_creep',
  'query_improved',
])

/** What happened, for items whose numbers don't show a clear move. */
const HAPPENED: Record<string, string> = {
  latency_regression: 'It got slower',
  plan_change: 'Its plan may have changed',
  table_slowdown: 'Queries on this table got slower',
  slow_creep: 'Its latency crept up',
  query_improved: 'It got faster',
  traffic_spike: 'Its traffic jumped',
  cache_hit_ratio_drop: 'The cache hit ratio dropped',
  rollback_or_deadlock_spike: 'Rollbacks rose',
  connections_near_max: 'Connections neared the maximum',
  lock_contention: 'Sessions waited on locks',
}

const noticeable = (before: number | null, after: number | null) =>
  before != null &&
  after != null &&
  before > 0 &&
  Math.abs(after / before - 1) >= NOTICEABLE

/** 'Each call went from 1.7 ms to 3.5 ms', 'Calls went from 7.1 a second to 12.6 a second'. */
function fromTo(
  metric: 'latency' | 'calls',
  before: number,
  after: number,
  subject: 'item' | 'member'
): string {
  if (metric === 'latency')
    return `${subject === 'member' ? 'Each call of this query' : 'Each call'} went from ${latencyText(before)} to ${latencyText(after)}`
  return `${subject === 'member' ? 'This query' : 'Calls'} went from ${rateText(before)} to ${rateText(after)}`
}

/** 'Latency measured back to normal', 'Calls measured back to normal'. */
const measuredBack = (metric: 'latency' | 'calls' | null) =>
  metric === 'latency'
    ? 'Latency measured back to normal'
    : metric === 'calls'
      ? 'Calls measured back to normal'
      : 'Measured back to normal'

function stateOf(
  ongoing: boolean,
  end: number | null,
  now: number,
  over = 'Measured back to normal',
  still = 'Still happening.',
  word: 'at' | 'by' = 'at'
) {
  const state = ongoing
    ? still
    : end != null
      ? `${over} ${timePhrase(word, end, now)}.`
      : null
  return {
    state,
    tone: (ongoing
      ? 'ongoing'
      : state
        ? 'over'
        : 'unknown') as BannerText['tone'],
  }
}

/**
 * Where the item stands, as the Inbox shows it (`status` from the backend), or null when
 * the backend didn't say. A closed item keeps its measured end, never its closing time.
 */
function itemState(
  item: InboxItem
): { ongoing: boolean; end: number | null } | null {
  if (item.status === 'ongoing') return { ongoing: true, end: null }
  if (item.status === 'back_to_normal')
    return { ongoing: false, end: item.status_at ?? null }
  if (item.status === 'closed') return { ongoing: false, end: null }
  return null
}

/** The lead for one member of a many-queries alert. */
function memberLead(jump: MemberJump, at: string, others: number): string {
  if (jump.kind === 'stopped') return `This query stopped running ${at}.`
  if (jump.kind === 'new') return `This query first ran ${at}.`
  if (noticeable(jump.before, jump.after))
    return `${fromTo(jump.metric, jump.before as number, jump.after as number, 'member')} ${at}.`
  const { change } = jump
  if (change != null && Math.abs(change) >= NOTICEABLE) {
    const size =
      change >= 0 ? changeSize(change) : `${Math.round(-change * 100)}%`
    if (jump.metric === 'latency')
      return `This query got ${size} ${change >= 0 ? 'slower' : 'faster'} ${at}.`
    return `This query's calls ${change >= 0 ? 'rose' : 'fell'} ${size} ${at}.`
  }
  return `This query changed with ${plural(others, 'other query', 'other queries')} ${at}.`
}

function groupBanner(
  item: InboxItem,
  queryKey: string | null | undefined,
  now: number,
  zoom: [number, number] | null | undefined
): BannerText | null {
  const window = incidentWindow(item)
  if (!window) return null
  const status = itemState(item)
  const jumps = memberJumps(item, new Map(), now)
  const { clusters } = clusterOnsets(jumps)
  const member = queryKey
    ? jumps.find((jump) => jump.key === queryKey)
    : undefined
  if (member) {
    const others = Math.max(jumps.length - 1, 0)
    const holder = clusters.find((cluster) =>
      cluster.members.some((jump) => jump.key === member.key)
    )
    const at =
      member.start != null
        ? timePhrase('at', member.start, now)
        : timePhrase('by', item.created_at, now)
    const mates = holder ? holder.members.length - 1 : 0
    const ongoing =
      member.start != null
        ? member.end == null && (status?.ongoing ?? window.ongoing)
        : (status?.ongoing ?? window.ongoing)
    const { state, tone } = stateOf(
      ongoing,
      (member.start != null ? member.end : window.end) ?? status?.end ?? null,
      now,
      member.kind === 'stopped'
        ? 'Running again'
        : member.kind === 'new'
          ? 'Stopped running'
          : measuredBack(member.metric)
    )
    return {
      lead: memberLead(member, at, others),
      state,
      note:
        holder && mates > 0
          ? `${plural(mates, 'other query', 'other queries')} ${clusterVerb(holder)} ${clusterTime(holder, now)} too.`
          : null,
      tone,
    }
  }
  const frame = zoom ? clusterIn(clusters, zoom) : undefined
  if (frame) {
    const ends = frame.members.map((jump) => jump.end)
    const measured = ends.filter((end): end is number => end != null)
    // Once the item is back to normal, so is every time frame in it.
    const over = status?.ongoing === false || measured.length === ends.length
    const last = !over
      ? null
      : measured.length && measured.length === ends.length
        ? Math.max(...measured)
        : (status?.end ?? null)
    const { state, tone } = stateOf(
      !over,
      last,
      now,
      'Every query measured back to normal',
      undefined,
      'by'
    )
    return { lead: `${clusterLabel(frame, now)}.`, state, note: null, tone }
  }
  const count = Math.max(jumps.length, item.query_keys.length)
  const { state, tone } = stateOf(
    status?.ongoing ?? window.ongoing,
    status?.end ?? window.end,
    now,
    'Every query measured back to normal',
    undefined,
    'by'
  )
  return {
    lead: `${plural(count, 'query', 'queries')} changed, the first ${timePhrase(window.measured ? 'at' : 'by', window.start, now)}.`,
    state,
    note: null,
    tone,
  }
}

/** The cluster a time frame link opened: the one whose onsets fall in the window. */
function clusterIn(
  clusters: OnsetCluster[],
  [from, to]: [number, number]
): OnsetCluster | undefined {
  return clusters.find((cluster) => cluster.start <= to && cluster.last >= from)
}

/**
 * The banner's sentences for an item opened on one of its queries, or on one of its
 * time frames (`zoom`, for a many-queries alert).
 */
export function bannerText(
  item: InboxItem,
  queryKey?: string | null,
  now = Date.now() / 1000,
  zoom?: [number, number] | null
): BannerText | null {
  if (item.type === 'many_queries_changed' || item.query_keys.length > 1)
    return groupBanner(item, queryKey, now, zoom)
  const window = incidentWindow(item, queryKey)
  if (!window) return null
  const status = itemState(item)
  const ongoing = status?.ongoing ?? window.ongoing
  const end = window.end ?? status?.end ?? null
  // An onset the readings show happened 'at' a time; a filing time only says 'by'.
  const at = timePhrase(window.measured ? 'at' : 'by', window.start, now)
  const m = item.measurement
  const metric = LATENCY_TYPES.has(item.type)
    ? 'latency'
    : item.type === 'traffic_spike'
      ? 'calls'
      : null
  let { state, tone } = stateOf(
    ongoing,
    end,
    now,
    item.type === 'cache_hit_ratio_drop'
      ? 'Cache hit ratio measured back to normal'
      : item.type === 'rollback_or_deadlock_spike'
        ? 'Rollbacks measured back to normal'
        : measuredBack(metric)
  )
  let lead: string
  switch (item.type) {
    case 'query_stopped_running':
      lead =
        m?.before != null
          ? `It ran about ${rateText(m.before)}, then stopped ${at}.`
          : `Stopped running ${at}.`
      ;({ state, tone } = stateOf(
        ongoing,
        end,
        now,
        'Running again',
        `Hasn't run since ${formatStampTime(window.start, now)}.`
      ))
      break
    case 'new_heavy_query':
      lead = `New query, first seen ${at}.`
      ;({ state, tone } = stateOf(
        ongoing,
        end,
        now,
        'Stopped running',
        'Still running.'
      ))
      break
    case 'stuck_query':
      lead = `A query stuck since ${formatStampTime(window.start, now)}.`
      break
    default: {
      const shown = metric ?? 'calls'
      const measured =
        m &&
        (m.metric === 'latency' || m.metric === 'calls') &&
        noticeable(m.before, m.after)
      if (measured && m)
        lead = `${fromTo(m.metric as 'latency' | 'calls', m.before as number, m.after as number, 'item')} ${at}.`
      else if (window.change != null && Math.abs(window.change) >= NOTICEABLE)
        lead = `${shown === 'latency' ? 'Latency' : 'Calls'} ${window.change >= 0 ? `up ${changeSize(window.change)}` : `down ${Math.round(Math.abs(window.change) * 100)}%`} ${at}.`
      else
        lead = `${HAPPENED[item.type] ?? item.title.replace(/\.$/, '')} ${at}.`
    }
  }
  return { lead, state, note: null, tone }
}
