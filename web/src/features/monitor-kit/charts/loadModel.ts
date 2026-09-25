// Load broken down by query (DESIGN_V4 section 2). Pure functions only.
//
// Usage:
//   const model = loadFromQueries(page.queries, domain)          // from /queries series
//   const model = loadFromBreakdown(breakdown, domain)            // from /load when served
//   model.bands          top queries by recorded execution time, then 'other'
//   model.colorFor(key)  the band color a row's swatch uses (other rows share grey)
//
// A query's time in a bucket is calls/s x mean ms. It sums across queries, so bands
// stack; dividing by the bucket's total gives each query's share of database time.
import type { LoadBreakdown, MonitorQuery } from '@/lib/monitor/types'
import { isPartialBucket } from './chartMath'

/** Color of each band by query key, to keep colors steady across models. */
export function bandColors(model: LoadModel): Map<string, string> {
  return new Map(
    model.bands
      .filter((band) => !band.isOther)
      .map((band) => [band.key, band.color])
  )
}

export type LoadMetric = 'calls' | 'latency' | 'time'

export const OTHER_KEY = 'other'
export const BAND_COLORS = [
  'var(--mk-band-1)',
  'var(--mk-band-2)',
  'var(--mk-band-3)',
  'var(--mk-band-4)',
  'var(--mk-band-5)',
  'var(--mk-band-6)',
  'var(--mk-band-7)',
  'var(--mk-band-8)',
] as const
export const OTHER_COLOR = 'var(--mk-band-other)'

/** Bands below this share of the range fold into "Everything else". */
const MIN_BAND_SHARE = 0.005

export interface LoadBandModel {
  key: string
  label: string
  color: string
  /** 0..1 of recorded execution time in the domain. */
  share: number
  totalMs: number
  calls: number
  meanMs: number | null
  isOther: boolean
}

/** One query's numbers inside the chart's window (the list re-scopes to these). */
export interface QueryScope {
  share: number
  totalMs: number
  calls: number
  /** Mean calls per second over the buckets with readings. */
  qps: number
  meanMs: number | null
}

export interface LoadModel {
  detailNote?: string | null
  xs: number[]
  res: number
  bands: LoadBandModel[]
  /** Per band (same order as bands), per x. `null` means no reading. */
  load: (number | null)[][]
  calls: (number | null)[][]
  latency: (number | null)[][]
  /** Mean latency over every query per x (calls-weighted). */
  overallLatency: (number | null)[]
  totalMs: number
  /** Share of the domain's execution time per query key (all queries, not just bands). */
  shares: Map<string, number>
  /** Every query's numbers in the domain, by key. */
  scope: Map<string, QueryScope>
  colorFor: (key: string) => string
}

export interface LoadSourceBand {
  key: string
  label: string
  /** Per x: calls per second and mean ms (null = no reading). */
  qps: (number | null)[]
  meanMs: (number | null)[]
}

function inferRes(xs: number[]): number {
  let best = Number.POSITIVE_INFINITY
  for (let i = 1; i < xs.length; i++) {
    const step = xs[i] - xs[i - 1]
    if (step > 0 && step < best) best = step
  }
  return Number.isFinite(best) ? best : 60
}

const sumOrNull = (values: (number | null)[]) => {
  let total = 0
  let any = false
  for (const value of values)
    if (value != null && Number.isFinite(value)) {
      total += value
      any = true
    }
  return any ? total : null
}

/**
 * Builds the model from per-query series aligned on `xs`. Every query counts toward
 * totals and shares; the top `top` by execution time become bands, the rest "other".
 */
export interface BuildOptions {
  /** What picks the top bands: recorded execution time (default) or call count. */
  rankBy?: 'time' | 'calls'
  /** Colors already given to queries elsewhere; a band keeps its pinned color. */
  pinned?: ReadonlyMap<string, string>
  /** First run: draw the bucket still filling up, rated over the time readings cover. */
  coverage?: Coverage | null
}

/** The span readings cover: from the first reading to the latest one. */
export interface Coverage {
  from: number | null
  until: number
}

/** Seconds of the bucket [ts, ts + res) that readings cover; never under ten. */
export function coveredSeconds(ts: number, res: number, coverage: Coverage) {
  const from = Math.max(ts, coverage.from ?? ts)
  const until = Math.min(ts + res, coverage.until)
  return Math.max(10, until - from)
}

/** The backend rates a bucket over at least a minute, or the time since it began. */
function servedSeconds(ts: number, res: number, now: number) {
  return Math.max(60, Math.min(res, now - ts))
}

export function buildLoadModel(
  xs: number[],
  sources: LoadSourceBand[],
  top = 8,
  resolution?: number,
  options: BuildOptions = {}
): LoadModel {
  const byCalls = options.rankBy === 'calls'
  const res = resolution ?? inferRes(xs)
  const perQuery = sources.map((source) => {
    const load = xs.map((_, i) => {
      const qps = source.qps[i]
      const mean = source.meanMs[i]
      return qps == null || mean == null ? null : qps * mean
    })
    const totalMs =
      load.reduce<number>((sum, value) => sum + (value ?? 0), 0) * res
    const calls =
      source.qps.reduce<number>((sum, value) => sum + (value ?? 0), 0) * res
    return { source, load, totalMs, calls }
  })
  const totalMs = perQuery.reduce((sum, item) => sum + item.totalMs, 0)
  const shares = new Map<string, number>()
  const scope = new Map<string, QueryScope>()
  const readSeconds = Math.max(res, xs.length * res)
  for (const item of perQuery) {
    const share = totalMs > 0 ? item.totalMs / totalMs : 0
    shares.set(item.source.key, share)
    scope.set(item.source.key, {
      share,
      totalMs: item.totalMs,
      calls: item.calls,
      qps: item.calls / readSeconds,
      meanMs: item.calls > 0 ? item.totalMs / item.calls : null,
    })
  }

  const weight = (item: (typeof perQuery)[number]) =>
    byCalls ? item.calls : item.totalMs
  const grand = byCalls
    ? perQuery.reduce((sum, item) => sum + item.calls, 0)
    : totalMs
  const ranked = [...perQuery]
    .filter((item) => weight(item) > 0)
    .sort((a, b) => weight(b) - weight(a))
  const banded = ranked
    .filter((item) => grand > 0 && weight(item) / grand >= MIN_BAND_SHARE)
    .slice(0, top)
  const pinnedColors = options.pinned
  const taken = new Set(
    banded.flatMap((item) => pinnedColors?.get(item.source.key) ?? [])
  )
  const free = BAND_COLORS.filter((color) => !taken.has(color))
  const colorOf = (key: string) => {
    const pinned = pinnedColors?.get(key)
    if (pinned) return pinned
    return free.shift() ?? OTHER_COLOR
  }
  const bandKeys = new Set(banded.map((item) => item.source.key))
  const rest = perQuery.filter((item) => !bandKeys.has(item.source.key))

  const bands: LoadBandModel[] = banded.map((item, index) => ({
    key: item.source.key,
    label: item.source.label,
    color: pinnedColors
      ? colorOf(item.source.key)
      : (BAND_COLORS[index] ?? OTHER_COLOR),
    share: grand > 0 ? weight(item) / grand : 0,
    totalMs: item.totalMs,
    calls: item.calls,
    meanMs: item.calls > 0 ? item.totalMs / item.calls : null,
    isOther: false,
  }))
  const load = banded.map((item) => item.load)
  const calls = banded.map((item) => item.source.qps)
  const latency = banded.map((item) => item.source.meanMs)

  const restMs = rest.reduce((sum, item) => sum + item.totalMs, 0)
  const restWeight = rest.reduce((sum, item) => sum + weight(item), 0)
  if (restWeight > 0) {
    const restLoad = xs.map((_, i) =>
      sumOrNull(rest.map((item) => item.load[i]))
    )
    const restCalls = xs.map((_, i) =>
      sumOrNull(rest.map((item) => item.source.qps[i]))
    )
    const restCallCount = rest.reduce((sum, item) => sum + item.calls, 0)
    bands.push({
      key: OTHER_KEY,
      label: 'Everything else',
      color: OTHER_COLOR,
      share: grand > 0 ? restWeight / grand : 0,
      totalMs: restMs,
      calls: restCallCount,
      meanMs: restCallCount > 0 ? restMs / restCallCount : null,
      isOther: true,
    })
    load.push(restLoad)
    calls.push(restCalls)
    latency.push(
      xs.map((_, i) => {
        const l = restLoad[i]
        const c = restCalls[i]
        return l != null && c ? l / c : null
      })
    )
  }

  const overallLatency = xs.map((_, i) => {
    let ms = 0
    let qps = 0
    for (const item of perQuery) {
      const q = item.source.qps[i]
      const l = item.load[i]
      if (q != null && l != null) {
        ms += l
        qps += q
      }
    }
    return qps > 0 ? ms / qps : null
  })

  const colors = new Map(bands.map((band) => [band.key, band.color]))
  return {
    xs,
    res,
    bands,
    load,
    calls,
    latency,
    overallLatency,
    totalMs,
    shares,
    scope,
    colorFor: (key) => colors.get(key) ?? OTHER_COLOR,
  }
}

/** From the /queries page: each query's mini series inside `domain`. */
export function loadFromQueries(
  queries: MonitorQuery[],
  domain: [number, number],
  top = 8,
  label: (query: MonitorQuery) => string = (query) => query.display_name,
  now = Date.now() / 1000,
  options: BuildOptions = {}
): LoadModel {
  const [start, end] = domain
  const xsSet = new Set<number>()
  for (const query of queries)
    for (const point of query.series)
      if (point.ts >= start && point.ts <= end) xsSet.add(point.ts)
  const sorted = [...xsSet].sort((a, b) => a - b)
  // The bucket still filling up would read low; leave it out, unless the readings'
  // coverage says how much of it they span.
  const step = inferRes(sorted)
  const coverage = options.coverage
  const xs = coverage
    ? sorted
    : sorted.filter((x) => !isPartialBucket(x, step, now))
  const scale = (ts: number) =>
    coverage
      ? servedSeconds(ts, step, now) / coveredSeconds(ts, step, coverage)
      : 1
  const index = new Map(xs.map((x, i) => [x, i]))
  const sources = queries.map((query) => {
    const qps: (number | null)[] = xs.map(() => null)
    const meanMs: (number | null)[] = xs.map(() => null)
    for (const point of query.series) {
      const i = index.get(point.ts)
      if (i === undefined) continue
      qps[i] = point.qps == null ? null : point.qps * scale(point.ts)
      meanMs[i] = point.mean_ms
    }
    return { key: query.key, label: label(query), qps, meanMs }
  })
  return buildLoadModel(xs, sources, top, undefined, options)
}

/** Largest stacked total over x (for the y axis). */
export function stackMax(values: (number | null)[][]): number {
  let max = 0
  const length = values[0]?.length ?? 0
  for (let i = 0; i < length; i++) {
    let total = 0
    for (const band of values) total += band[i] ?? 0
    if (total > max) max = total
  }
  return max
}

/** Each band's share of database time per x, in percent; every bucket sums to 100. */
export function timeShareRows(load: (number | null)[][]): (number | null)[][] {
  const length = load[0]?.length ?? 0
  const totals = Array.from({ length }, (_, i) =>
    load.reduce((sum, band) => sum + (band[i] ?? 0), 0)
  )
  return load.map((band) =>
    band.map((value, i) =>
      value == null || totals[i] <= 0 ? null : (value / totals[i]) * 100
    )
  )
}

/** A percentage as '42%', or '0.4%' under one percent. */
export function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-'
  if (value === 0) return '0%'
  if (value < 1) return `${Math.round(value * 10) / 10}%`
  return `${Math.round(value)}%`
}

/** From the backend's /load breakdown (bands already ranked and colored). */
export function loadFromBreakdown(
  source: LoadBreakdown,
  now = Date.now() / 1000,
  coverage: Coverage | null = null
): LoadModel {
  // The bucket still filling up would read low; leave it out, unless the readings'
  // coverage says how much of it they span.
  const breakdown = {
    ...source,
    points: coverage
      ? source.points.map((point) => ({
          ...point,
          seconds: coveredSeconds(point.ts, source.res, coverage),
        }))
      : source.points.filter(
          (point) => !isPartialBucket(point.ts, source.res, now)
        ),
  }
  const xs = breakdown.points.map((point) => point.ts)
  const perSecond = (value: number | null | undefined, seconds: number) =>
    value == null ? null : value / Math.max(1, seconds)
  const bands: LoadBandModel[] = breakdown.bands.map((band) => ({
    key: band.kind === 'other' ? OTHER_KEY : band.key,
    label: band.kind === 'other' ? 'Everything else' : band.display_name,
    color:
      band.kind === 'other' || band.color_index == null
        ? OTHER_COLOR
        : (BAND_COLORS[band.color_index] ?? OTHER_COLOR),
    share: band.share,
    totalMs: band.total_ms,
    calls: band.calls,
    meanMs: band.mean_ms,
    isOther: band.kind === 'other',
  }))
  const load = bands.map((_, i) =>
    breakdown.points.map((point) =>
      perSecond(point.total_ms?.[i], point.seconds)
    )
  )
  const calls = bands.map((_, i) =>
    breakdown.points.map((point) => perSecond(point.calls?.[i], point.seconds))
  )
  const latency = bands.map((_, i) =>
    breakdown.points.map((point) => {
      const ms = point.total_ms?.[i]
      const count = point.calls?.[i]
      return ms != null && count ? ms / count : null
    })
  )
  const seconds = Math.max(1, breakdown.end - breakdown.start)
  const shares = new Map<string, number>()
  const scope = new Map<string, QueryScope>()
  for (const band of bands) {
    shares.set(band.key, band.share)
    scope.set(band.key, {
      share: band.share,
      totalMs: band.totalMs,
      calls: band.calls,
      qps: band.calls / seconds,
      meanMs: band.meanMs,
    })
  }
  const colors = new Map(bands.map((band) => [band.key, band.color]))
  return {
    xs,
    res: breakdown.res,
    detailNote: breakdown.detail_note,
    bands,
    load,
    calls,
    latency,
    overallLatency: breakdown.points.map((point) => point.mean_ms),
    totalMs: breakdown.total_ms,
    shares,
    scope,
    colorFor: (key) => colors.get(key) ?? OTHER_COLOR,
  }
}
