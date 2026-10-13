import type {
  ContainerProgress,
  DiscoveryMode,
  LoadEvent,
  PatternRow,
  PatternStatus,
  Phase,
  PreflightChecks,
} from '../../lib/useDemo'
import { plural } from '../monitor-kit/format'

export const DASH = '\u2014'

/** The hour the backend keeps a demo alive, in seconds. */
export const DEMO_LIFETIME_S = 60 * 60

export const POLICY_WORD: Record<DiscoveryMode, string> = {
  count_star: 'most frequent',
  sum_time: 'most expensive',
}

export const STATUS_WORD: Record<PatternStatus, string> = {
  cached_querypilot: 'Cached by QueryPilot',
  cached_manual: 'Cached by hand',
  pass_through: 'Not cached yet',
  not_eligible: 'Not cached yet',
  denylisted: 'Passes through',
  unsupported: 'Passes through',
}

export function isCached(status: PatternStatus): boolean {
  return status === 'cached_querypilot' || status === 'cached_manual'
}

export function canManualCache(row: PatternRow): boolean {
  return row.status === 'pass_through' || row.status === 'not_eligible'
}

/**
 * The two queries "Cache a query by hand" points at: the slowest on Postgres
 * with the smallest result sets, so caching them shows the largest drop.
 */
export const MANUAL_SUGGESTED_KEYS = ['H01', 'H02']

export function suggestedRows(patterns: PatternRow[]): PatternRow[] {
  return MANUAL_SUGGESTED_KEYS.map((key) =>
    patterns.find((row) => row.key === key)
  ).filter((row): row is PatternRow => Boolean(row))
}

/** Stable row order: query identity, never a live metric. */
export function rowIdentity(a: PatternRow, b: PatternRow): number {
  return a.key.localeCompare(b.key) || a.title.localeCompare(b.title)
}

/**
 * The parameterized shape QueryPilot caches: every string literal and
 * comparison value becomes a positional placeholder; ordinals like GROUP BY 1
 * stay intact.
 */
export function parameterizeSql(sql: string): {
  text: string
  placeholders: string[]
} {
  const placeholders: string[] = []
  const next = () => {
    const token = `$${placeholders.length + 1}`
    placeholders.push(token)
    return token
  }
  const text = sql.replace(
    /('(?:[^']|'')*')|([=<>(,]\s*)(-?\d+(?:\.\d+)?)/g,
    (_m, str: string | undefined, prefix: string | undefined) =>
      str != null ? next() : `${prefix}${next()}`
  )
  return { text, placeholders }
}

/** One mono line of SQL for a list row: collapsed whitespace. */
export function sqlLine(sql: string): string {
  return parameterizeSql(sql).text.replace(/\s+/g, ' ').trim()
}

/** "41", "2.1", "0.4": a latency for a cell whose unit sits beside it. */
export function formatMs(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null
  if (value >= 10) return String(Math.round(value))
  if (value >= 0.1) return value.toFixed(1)
  return value.toFixed(2)
}

/** "9:52": a clock time in the running demo's own day. */
export function shortClock(ts: number): string {
  const date = new Date(ts * 1000)
  return `${date.getHours() % 12 || 12}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** "1.1 GB" or "640 MB"; the backend scales the figure to the missing images. */
export function formatDownloadSize(mb: number | null | undefined): string {
  if (!mb || mb <= 0) return '1.1 GB'
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

export type DockerState = 'checking' | 'ok' | 'not_running' | 'not_installed'

export function dockerState(checks: PreflightChecks | null): DockerState {
  if (!checks) return 'checking'
  if (!checks.docker_installed) return 'not_installed'
  if (!checks.docker_running) return 'not_running'
  return 'ok'
}

/** Whether the demo can start: Docker up, disk free, containers runnable. */
export function canStart(checks: PreflightChecks | null): boolean {
  if (!checks) return false
  return (
    checks.docker_installed &&
    checks.docker_running &&
    checks.disk_space_ok &&
    checks.amd64_emulation !== 'unavailable'
  )
}

const COMPONENT_BY_IMAGE: Record<string, string> = {
  pg: 'the orders database',
  postgres: 'the orders database',
  orders: 'the orders database',
  'orders-pg': 'the orders database',
  readyset: 'the Readyset cache engine',
  sqp: 'the QueryPilot router',
  'qp-cron': 'the QueryPilot accelerator',
  qpcron: 'the QueryPilot accelerator',
}

const REGISTRY_REF = /\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?\/[^\s()]+/i

export interface DownloadDetail {
  /** "Downloading the Readyset cache engine". */
  label: string
  /** "2 of 4 images", when the stream numbers the pull. */
  count?: string
  /** "~75 MB", when the stream sizes the pull. */
  size?: string
}

/**
 * The image pull narrated as a component: the provision stream names a
 * registry path, the page never shows one.
 */
export function downloadDetail(detail: string): DownloadDetail | null {
  const ref = detail.match(REGISTRY_REF)
  if (!ref) return null
  const image = (ref[0].split('/').pop() ?? '')
    .replace(/[:@].*$/, '')
    .toLowerCase()
  const component = COMPONENT_BY_IMAGE[image]
  const counted = detail.match(/\((\d+)\/(\d+)/)
  const sized = detail.match(/~\s*(\d+(?:\.\d+)?\s*[MG]B)/i)
  return {
    label: component ? `Downloading ${component}` : 'Downloading images',
    count: counted ? `${counted[1]} of ${counted[2]} images` : undefined,
    size: sized ? `~${sized[1]}` : undefined,
  }
}

export type StepState = 'done' | 'active' | 'pending'

export interface ProvisionStep {
  id: 'docker' | 'images' | 'pg' | 'readyset' | 'querypilot' | 'workload'
  label: string
  state: StepState
  /** The active step's live line, or a pending step's hint. */
  detail?: string
  /** The active step's second line: an image count, a size. */
  meta?: string
}

const STEP_LABEL: Record<ProvisionStep['id'], string> = {
  docker: 'Docker is running',
  images: 'Downloading images',
  pg: 'Start the orders database',
  readyset: 'Start the Readyset cache engine',
  querypilot: 'Start the QueryPilot router and accelerator',
  workload: 'Start the orders workload',
}

function container(containers: ContainerProgress[], name: string) {
  return containers.find((c) => c.name === name)
}

function isPullDetail(detail: string | undefined): boolean {
  return Boolean(
    detail && /pull|download|checking container images/i.test(detail)
  )
}

/**
 * The six steps of a start, derived from the container progress the provision
 * stream reports: Docker passed preflight, the images, then the four
 * components in order, then the workload that opens the comparison.
 */
/** "1.5" and "M" for 1,500,000 rows; "850" and "k"; a plain number under a thousand. */
export function formatRows(rows: number): { value: string; unit?: string } {
  if (rows >= 1_000_000)
    return { value: trimZero((rows / 1_000_000).toFixed(1)), unit: 'M' }
  if (rows >= 1_000)
    return { value: trimZero((rows / 1_000).toFixed(1)), unit: 'k' }
  return { value: String(Math.round(rows)) }
}

function trimZero(text: string): string {
  return text.endsWith('.0') ? text.slice(0, -2) : text
}

export function provisionSteps(
  containers: ContainerProgress[],
  phase: Phase,
  loadRunning: boolean,
  /** The whole first-run download, shown on the images step until a pull reports. */
  downloadMb?: number | null
): ProvisionStep[] {
  const pg = container(containers, 'pg')
  const readyset = container(containers, 'readyset')
  const sqp = container(containers, 'sqp')
  const cron = container(containers, 'qp-cron')
  const ready = (c: ContainerProgress | undefined) => c?.state === 'ready'
  const pullDetail = isPullDetail(pg?.detail) ? pg?.detail : undefined
  const download = pullDetail ? downloadDetail(pullDetail) : null
  const imagesDone =
    ready(pg) || Boolean(pg?.detail && !isPullDetail(pg.detail))
  const done: Record<ProvisionStep['id'], boolean> = {
    docker: true,
    images: imagesDone,
    pg: ready(pg),
    readyset: ready(readyset),
    querypilot: ready(sqp) && ready(cron),
    workload: phase === 'ready' && loadRunning,
  }
  const order: ProvisionStep['id'][] = [
    'docker',
    'images',
    'pg',
    'readyset',
    'querypilot',
    'workload',
  ]
  const activeIndex = order.findIndex((id) => !done[id])
  return order.map((id, index) => {
    const state: StepState = done[id]
      ? 'done'
      : index === activeIndex
        ? 'active'
        : 'pending'
    const step: ProvisionStep = { id, label: STEP_LABEL[id], state }
    if (id === 'images' && state === 'active' && download) {
      step.label = download.label
      step.meta = download.count
      step.detail = download.size
    } else if (id === 'images' && state === 'active' && downloadMb) {
      step.detail = formatDownloadSize(downloadMb)
    } else if (state === 'active') {
      const live =
        id === 'pg'
          ? pg?.detail
          : id === 'readyset'
            ? readyset?.detail
            : id === 'querypilot'
              ? (sqp?.detail ?? cron?.detail)
              : undefined
      if (live && !REGISTRY_REF.test(live)) step.detail = live
    }
    if (id === 'workload' && state !== 'done')
      step.detail = 'then the comparison opens'
    return step
  })
}

export function cachedRows(patterns: PatternRow[]): PatternRow[] {
  return patterns.filter((row) => isCached(row.status))
}

function mean(values: number[]): number | null {
  const present = values.filter((value) => Number.isFinite(value))
  if (!present.length) return null
  return present.reduce((sum, value) => sum + value, 0) / present.length
}

export interface VerdictCopy {
  lead: string
  rest: string
}

/**
 * The running page's one sentence: how many queries come from memory, at
 * what latency against Postgres, and the next thing to do.
 */
export function runningVerdict(
  patterns: PatternRow[],
  querypilotOn: boolean,
  mode: DiscoveryMode
): VerdictCopy {
  const cached = cachedRows(patterns)
  const total = patterns.length
  const next = querypilotOn
    ? `QueryPilot picks what to cache by policy: ${POLICY_WORD[mode]}.`
    : 'Turn on QueryPilot to let it pick the rest.'
  if (!cached.length)
    return {
      lead: `Readyset answers 0 of ${total} queries from memory.`,
      rest: next,
    }
  const readyset = formatMs(
    mean(cached.map((row) => row.router_avg_ms ?? Number.NaN))
  )
  const postgres = formatMs(
    mean(cached.map((row) => row.direct_avg_ms ?? Number.NaN))
  )
  const latency =
    readyset && postgres
      ? `${readyset} ms instead of ${postgres} ms, fresh as orders change.`
      : 'fresh as orders change.'
  return {
    lead: `Readyset answers ${cached.length} of ${total} queries from memory:`,
    rest: `${latency} ${next}`,
  }
}

export interface ChartMarker {
  x: number
  label: string
}

/**
 * The chart's markers: caches that landed in the same minute collapse into
 * one "2 caches 9:52"; QueryPilot and policy changes mark on their own.
 */
export function chartMarkers(events: LoadEvent[]): ChartMarker[] {
  const markers: ChartMarker[] = []
  const caches = new Map<number, { x: number; n: number }>()
  for (const event of events) {
    if (event.type === 'manual_cache') {
      const minute = Math.floor(event.t / 60)
      const group = caches.get(minute)
      if (group) group.n += 1
      else caches.set(minute, { x: event.t, n: 1 })
      continue
    }
    const clock = shortClock(event.t)
    const label =
      event.type === 'manual_uncache'
        ? `cache removed ${clock}`
        : event.type === 'qp_on'
          ? `QueryPilot on ${clock}`
          : event.type === 'qp_off'
            ? `QueryPilot off ${clock}`
            : `policy: ${event.label.toLowerCase().includes('expensive') ? POLICY_WORD.sum_time : POLICY_WORD.count_star} ${clock}`
    markers.push({ x: event.t, label })
  }
  for (const group of caches.values())
    markers.push({
      x: group.x,
      label: `${group.n} ${group.n === 1 ? 'cache' : 'caches'} ${shortClock(group.x)}`,
    })
  return markers.sort((a, b) => a.x - b.x)
}

/** "last 10 minutes" or "last 40 s": how much history the chart shows. */
export function spanLabel(seconds: number): string {
  if (seconds >= 60) return `last ${plural(Math.round(seconds / 60), 'minute')}`
  return `last ${Math.round(seconds)} s`
}

export type TryId = 'cache' | 'querypilot' | 'compare'

export interface TryItem {
  id: TryId
  name: string
  /** The sentence under the name; `mono` is the query name inside it. */
  before: string
  mono?: string
  after?: string
}

/** The three things to try, with the hand-cached query named by its title. */
export function tryItems(patterns: PatternRow[]): TryItem[] {
  const pick = suggestedRows(patterns)[0]?.title ?? 'a query'
  return [
    {
      id: 'cache',
      name: 'Cache a query by hand',
      before: 'Pick ',
      mono: pick,
      after: ' and watch its latency fall.',
    },
    {
      id: 'querypilot',
      name: 'Turn on QueryPilot',
      before:
        'It picks what to cache by policy: most frequent or most expensive.',
    },
    {
      id: 'compare',
      name: 'Compare the two paths',
      before:
        'The chart runs the same traffic through Readyset and straight to Postgres.',
    },
  ]
}
