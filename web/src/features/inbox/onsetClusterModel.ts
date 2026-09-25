// A many-queries alert, member by member: how much each query moved and when, and the
// members clustered by when they jumped, each cluster with a Database page link to that
// time frame. Timestamps are Unix seconds.
import type { InboxItem, Occurrence } from '@/lib/monitor/types'
import { formatClock, plural } from '../monitor-kit/format'
import type { LinkRoute } from './inboxLinks'
import { formatStampTime } from './inboxModel'
import { incidentZoom, type LineOnset, lineOnset } from './incidentModel'
import { type Figures, latencyText, rateText } from './itemStory'

/** Onsets further apart than this start a new cluster. */
export const CLUSTER_GAP = 10 * 60
/** A time frame link reaches this far before the first onset and after the last. */
export const FRAME_PAD_BEFORE = 3 * 60
export const FRAME_PAD_AFTER = 5 * 60
/** A time frame is never shorter than this. */
export const FRAME_MIN = 5 * 60

export type JumpMetric = 'latency' | 'calls'
export type JumpKind =
  | 'slowed'
  | 'sped up'
  | 'jumped'
  | 'dropped'
  | 'stopped'
  | 'new'

export interface MemberJump {
  key: string
  name: string
  /** When its own change began; null when the readings don't show it. */
  start: number | null
  /** Back to normal from here; null while it lasts or when unknown. */
  end: number | null
  metric: JumpMetric
  kind: JumpKind | null
  /** The typical level before and the level it moved to (ms, or calls a second). */
  before: number | null
  after: number | null
  /** after / before - 1. */
  change: number | null
  /** Its sparkline, with its own window shaded. */
  onset: LineOnset | null
}

const LATENCY_KINDS = new Set([
  'latency_regression',
  'plan_change',
  'table_slowdown',
  'slow_creep',
  'query_improved',
])

type Numbers = { qps?: unknown; mean_ms?: unknown }
type MemberBody = {
  kind?: unknown
  baseline_qps?: unknown
  baseline_mean_ms?: unknown
  at_filing?: Numbers
  before?: Numbers
}

const num = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

function body(item: InboxItem): Record<string, unknown> {
  const raw = item.body_json
  if (raw && typeof raw === 'object') return raw
  try {
    const parsed: unknown = JSON.parse(raw || '{}')
    return parsed && typeof parsed === 'object'
      ? (parsed as Record<string, unknown>)
      : {}
  } catch {
    return {}
  }
}

/**
 * Each member's own finding from the item body: still changed (`recovery_queries`,
 * measured at filing) or back to normal (`recovered_queries`, measured when it changed).
 */
function memberBodies(item: InboxItem): Map<string, MemberBody> {
  const out = new Map<string, MemberBody>()
  const data = body(item)
  for (const field of ['recovered_queries', 'recovery_queries']) {
    const entries = data[field]
    if (!entries || typeof entries !== 'object') continue
    for (const [key, value] of Object.entries(entries as object))
      if (value && typeof value === 'object') out.set(key, value as MemberBody)
  }
  return out
}

function jumpKind(
  findingKind: string | null,
  metric: JumpMetric,
  change: number | null
): JumpKind | null {
  if (findingKind === 'query_stopped_running') return 'stopped'
  if (findingKind === 'new_heavy_query') return 'new'
  if (change == null) return null
  if (metric === 'latency') return change >= 0 ? 'slowed' : 'sped up'
  return change >= 0 ? 'jumped' : 'dropped'
}

const ratio = (before: number | null, after: number | null) =>
  before != null && after != null && before > 0 ? after / before - 1 : null

function nameFor(item: InboxItem, key: string, names: Map<string, string>) {
  const member = item.members?.find((entry) => entry.query_key === key)
  return (
    member?.display_name ||
    item.query_labels?.[key] ||
    names.get(key) ||
    `Query ${key.slice(0, 8)}`
  )
}

/**
 * Every member of a many-queries alert with its own numbers. The member's own finding
 * says what moved (latency or calls); the item's top members and the measured window
 * fill in what it doesn't.
 */
export function memberJumps(
  item: InboxItem,
  names: Map<string, string>,
  now = Date.now() / 1000
): MemberJump[] {
  const bodies = memberBodies(item)
  const windows = new Map(
    (item.incident?.queries ?? []).map((query) => [query.query_key, query])
  )
  const groupMetric: JumpMetric =
    item.measurement?.metric === 'latency' ? 'latency' : 'calls'
  const keys = [...item.query_keys]
  for (const key of windows.keys()) if (!keys.includes(key)) keys.push(key)
  return keys.map((key) => {
    const finding = bodies.get(key)
    const window = windows.get(key)
    const findingKind = typeof finding?.kind === 'string' ? finding.kind : null
    let metric = groupMetric
    let before: number | null = null
    let after: number | null = null
    if (finding) {
      metric =
        findingKind && LATENCY_KINDS.has(findingKind) ? 'latency' : 'calls'
      const moved = finding.at_filing ?? finding.before
      before = num(
        metric === 'latency' ? finding.baseline_mean_ms : finding.baseline_qps
      )
      after = num(metric === 'latency' ? moved?.mean_ms : moved?.qps)
      if (findingKind === 'query_stopped_running') after = 0
      if (findingKind === 'new_heavy_query') before = null
    }
    const top = item.members?.find((entry) => entry.query_key === key)
    if (top && (after == null || (!finding && top.after != null))) {
      metric = groupMetric
      before = top.before
      after = top.after
    }
    const change = ratio(before, after) ?? top?.change ?? window?.change ?? null
    return {
      key,
      name: nameFor(item, key, names),
      start: window?.start ?? null,
      end: window?.end ?? null,
      metric,
      kind: jumpKind(findingKind, metric, change),
      before,
      after,
      change,
      onset: lineOnset(item, key, now),
    }
  })
}

export interface OnsetCluster {
  /** Stable within the item: the first onset. */
  id: string
  start: number
  /** The last onset in the cluster. */
  last: number
  members: MemberJump[]
}

/** Largest move first, then by name, so the rows that matter lead each page. */
export const bySize = (a: MemberJump, b: MemberJump) =>
  Math.abs(b.change ?? 0) - Math.abs(a.change ?? 0) ||
  a.name.localeCompare(b.name)

/**
 * Members grouped by when they jumped: an onset more than `gap` after the previous one
 * starts a new cluster. Newest cluster first; members without a measured onset come
 * back separately.
 */
export function clusterOnsets(
  jumps: MemberJump[],
  gap = CLUSTER_GAP
): { clusters: OnsetCluster[]; untimed: MemberJump[] } {
  const timed = jumps
    .filter((jump) => jump.start != null)
    .sort((a, b) => (a.start as number) - (b.start as number))
  const clusters: OnsetCluster[] = []
  for (const jump of timed) {
    const at = jump.start as number
    const current = clusters[clusters.length - 1]
    if (current && at - current.last <= gap) {
      current.members.push(jump)
      current.last = at
    } else
      clusters.push({ id: `onset-${at}`, start: at, last: at, members: [jump] })
  }
  for (const cluster of clusters) cluster.members.sort(bySize)
  return {
    clusters: clusters.reverse(),
    untimed: jumps.filter((jump) => jump.start == null).sort(bySize),
  }
}

/** A burst's time frame: its first and last member onsets. */
export type BurstFrame = Pick<OnsetCluster, 'start' | 'last'>

/** The frame of one burst item, from its members' onsets; null when none shows. */
export function burstFrame(jumps: MemberJump[]): BurstFrame | null {
  const starts = jumps
    .map((jump) => jump.start)
    .filter((start): start is number => start != null)
  if (starts.length === 0) return null
  return { start: Math.min(...starts), last: Math.max(...starts) }
}

/**
 * The time frame a burst shows: the latest occurrence when it repeated (the members'
 * onsets span every time), else the span of its members' onsets.
 */
export function shownFrame(
  jumps: MemberJump[],
  occurrences: Occurrence[]
): BurstFrame | null {
  if (occurrences.length < 2) return burstFrame(jumps)
  const latest = occurrences.reduce((a, b) => (b.start > a.start ? b : a))
  return { start: latest.start, last: latest.end ?? latest.start }
}

/** The Database page on a burst's time frame, carrying the item. */
export const burstRoute = (
  item: InboxItem,
  frame: BurstFrame,
  now = Date.now() / 1000
): LinkRoute => clusterRoute(item, frame, now)

/** The Database page window for a cluster: padded, at least 5 minutes, never past now. */
export function clusterFrame(
  cluster: Pick<OnsetCluster, 'start' | 'last'>,
  now = Date.now() / 1000
): [number, number] {
  const from = Math.floor(cluster.start - FRAME_PAD_BEFORE)
  let to = Math.ceil(cluster.last + FRAME_PAD_AFTER)
  if (to - from < FRAME_MIN) to = from + FRAME_MIN
  const current = Math.floor(now)
  if (to > current) return [Math.min(from, current - FRAME_MIN), current]
  return [from, to]
}

/**
 * The Database page on a cluster's time frame, with every query that ran then, carrying
 * the item so the page can say why you're there.
 */
export function clusterRoute(
  item: InboxItem,
  cluster: Pick<OnsetCluster, 'start' | 'last'>,
  now = Date.now() / 1000
): LinkRoute {
  const [from, to] = clusterFrame(cluster, now)
  const { range } = incidentZoom({ start: from, end: to }, now)
  return {
    to: '/database',
    search: {
      target: item.target,
      range,
      zoom: `${from}-${to}`,
      from: `inbox:${item.id}`,
    },
  }
}

/** 'at 3:04 PM', 'from 4:15 to 4:19 PM', 'yesterday from 5:41 to 5:55 PM'. */
export function clusterTime(
  cluster: Pick<OnsetCluster, 'start' | 'last'>,
  now = Date.now() / 1000
): string {
  const stamp = formatStampTime(cluster.start, now)
  const startClock = formatClock(cluster.start)
  const day = stamp
    .slice(0, stamp.length - startClock.length)
    .replace(/,?\s$/, '')
  const prefix = !day ? '' : day === 'yesterday' ? 'yesterday ' : `on ${day} `
  if (cluster.last - cluster.start < 60) return `${prefix}at ${startClock}`
  const endClock = formatClock(cluster.last)
  const meridiem = /\s([AP]M)$/
  const [startM, endM] = [meridiem.exec(startClock), meridiem.exec(endClock)]
  const from =
    startM && endM && startM[1] === endM[1]
      ? startClock.replace(meridiem, '')
      : startClock
  return `${prefix}from ${from} to ${endClock}`
}

const VERB_PAST: Record<JumpKind, string> = {
  slowed: 'slowed down',
  'sped up': 'sped up',
  jumped: 'jumped',
  dropped: 'dropped',
  stopped: 'stopped',
  new: 'first ran',
}

/** 'jumped' when every member moved the same way, else 'changed'. */
export function clusterVerb(cluster: Pick<OnsetCluster, 'members'>): string {
  const kinds = new Set(cluster.members.map((member) => member.kind))
  const [only] = kinds
  return kinds.size === 1 && only ? VERB_PAST[only] : 'changed'
}

/**
 * '31 queries jumped at 3:04 PM', '25 queries changed from 4:15 to 4:19 PM', or the
 * query's name when it jumped alone.
 */
export function clusterLabel(
  cluster: OnsetCluster,
  now = Date.now() / 1000
): string {
  const verb = clusterVerb(cluster)
  const [first] = cluster.members
  const who =
    cluster.members.length === 1 && first
      ? first.name
      : plural(cluster.members.length, 'query', 'queries')
  return `${who} ${verb} ${clusterTime(cluster, now)}`
}

const trim = (value: number) =>
  value.toLocaleString('en-US', { maximumFractionDigits: 1 })

/** How much a member moved, the same way on every row: '4.3x', '+24%', '-40%'. */
export function jumpSize(
  jump: Pick<MemberJump, 'kind' | 'change'>
): string | null {
  if (jump.kind === 'stopped') return 'stopped'
  if (jump.kind === 'new') return 'new'
  const { change } = jump
  if (change == null) return null
  if (change >= 0.95) return `${trim(1 + change)}x`
  const pct = Math.round(change * 100)
  return pct >= 0 ? `+${pct}%` : `${pct}%`
}

/** A member's level before and after, in its own unit. */
export function jumpFigures(
  jump: Pick<MemberJump, 'metric' | 'before' | 'after' | 'kind'>
): Figures | null {
  const { before, after } = jump
  if (after == null) return null
  const text = (value: number) =>
    jump.metric === 'latency' ? latencyText(value) : rateText(value)
  return {
    label: jump.metric === 'latency' ? 'Latency' : 'Calls',
    before: before == null ? null : text(before),
    after: jump.kind === 'stopped' || after === 0 ? 'none' : text(after),
  }
}

const KIND_PHRASE: Record<JumpKind, string> = {
  jumped: 'ran more often',
  dropped: 'ran less often',
  slowed: 'got slower',
  'sped up': 'got faster',
  stopped: 'stopped running',
  new: 'were new',
}

const phrase = (kind: JumpKind, count: number) =>
  kind === 'new' && count === 1 ? 'was new' : KIND_PHRASE[kind]

/** What the members did, largest share first: '40 ran more often, 29 got slower.' */
export function groupBreakdown(jumps: MemberJump[]): string | null {
  const counts = new Map<JumpKind, number>()
  for (const jump of jumps)
    if (jump.kind) counts.set(jump.kind, (counts.get(jump.kind) ?? 0) + 1)
  if (counts.size === 0) return null
  const parts = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([kind, count]) => `${count} ${phrase(kind, count)}`)
  const unknown = jumps.length - [...counts.values()].reduce((a, b) => a + b, 0)
  if (unknown > 0) parts.push(`${unknown} had no clear change`)
  const text = parts.join(', ')
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`
}
