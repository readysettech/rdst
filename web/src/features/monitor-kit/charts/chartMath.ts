// Pure geometry for monitor charts: nice y domains, line segments with gaps.
import { comparisonChartMax } from '@/components/ComparisonLineChart'

export interface ChartPoint {
  x: number
  y: number | null
}

const NICE = [1, 2, 2.5, 5, 10]

function niceUp(value: number): number {
  if (value <= 0) return 1
  const power = 10 ** Math.floor(Math.log10(value))
  const scaled = value / power
  const nice = NICE.find((candidate) => scaled <= candidate + 1e-9) ?? 10
  return nice * power
}

/** Nice top of a zero-based axis. Uses the Demo's 1/2/5 x 10^n above 1. */
export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1
  if (value > 1) return comparisonChartMax(value)
  return niceUp(value)
}

export type YScale = 'linear' | 'log'

export interface YDomain {
  scale: YScale
  min: number
  max: number
  /** Three gridline values, bottom to top. */
  grid: [number, number, number]
}

/**
 * Y domain for a set of values.
 * - linear, zero-based (default): [0, niceMax(max)].
 * - linear, `min: 'auto'`: a tight nice window around the data (ratios near 1).
 * - log: decades around the positive values.
 */
export function yDomain(
  values: number[],
  {
    scale = 'linear',
    min,
    max,
    cap,
  }: {
    scale?: YScale
    min?: number | 'auto'
    max?: number
    cap?: number
  } = {}
): YDomain {
  const finite = values.filter((value) => Number.isFinite(value))
  if (scale === 'log') {
    const positive = finite.filter((value) => value > 0)
    const lo = positive.length ? Math.min(...positive) : 1
    const hi = positive.length ? Math.max(...positive) : 10
    const floor = 10 ** Math.floor(Math.log10(lo))
    let top = max ?? 10 ** Math.ceil(Math.log10(hi))
    if (top <= floor) top = floor * 10
    return {
      scale,
      min: floor,
      max: top,
      grid: [floor, Math.sqrt(floor * top), top],
    }
  }
  const dataMax = finite.length ? Math.max(...finite) : 0
  if (min === 'auto' && finite.length) {
    const dataMin = Math.min(...finite)
    const spread = dataMax - dataMin || Math.abs(dataMax) * 0.01 || 1
    let step = niceUp(spread / 2)
    let lo = Math.floor(dataMin / step) * step
    let hi = lo + 2 * step
    for (let guard = 0; hi < dataMax - 1e-12 && guard < 20; guard++) {
      step = niceUp(step * 1.01)
      lo = Math.floor(dataMin / step) * step
      hi = lo + 2 * step
    }
    if (cap != null && hi > cap) {
      hi = cap
      lo = Math.min(lo, cap - 2 * step)
    }
    const top = max ?? hi
    return { scale, min: lo, max: top, grid: [lo, (lo + top) / 2, top] }
  }
  const lo = typeof min === 'number' ? min : 0
  let top = max ?? niceMax(dataMax)
  if (cap != null) top = Math.min(top, cap)
  if (top <= lo) top = lo + 1
  return { scale, min: lo, max: top, grid: [lo, (lo + top) / 2, top] }
}

/** Fraction 0..1 of a value inside the domain (clamped). */
export function yFraction(value: number, domain: YDomain): number {
  if (domain.scale === 'log') {
    const lo = Math.log10(domain.min)
    const hi = Math.log10(domain.max)
    const v = Math.log10(Math.max(value, domain.min))
    return Math.min(1, Math.max(0, (v - lo) / (hi - lo || 1)))
  }
  return Math.min(
    1,
    Math.max(0, (value - domain.min) / (domain.max - domain.min || 1))
  )
}

/** Median spacing between consecutive x values (the bucket size when unknown). */
export function inferResolution(points: ChartPoint[]): number {
  const xs = points.map((point) => point.x).sort((a, b) => a - b)
  const diffs: number[] = []
  for (let i = 1; i < xs.length; i++) {
    const diff = xs[i] - xs[i - 1]
    if (diff > 0) diffs.push(diff)
  }
  if (!diffs.length) return 60
  diffs.sort((a, b) => a - b)
  return diffs[Math.floor(diffs.length / 2)]
}

export interface Gap {
  /** Last reading before the gap (or domain start). */
  from: number
  /** First reading after the gap (or domain end). */
  to: number
}

export interface Segments {
  /** Runs of consecutive readings; a null or a missing bucket ends a run. */
  runs: { x: number; y: number }[][]
  /** Missing spans longer than three buckets: drawn dotted, hover says "No readings". */
  gaps: Gap[]
}

export function segmentPoints(points: ChartPoint[], res: number): Segments {
  const sorted = [...points].sort((a, b) => a.x - b.x)
  const runs: { x: number; y: number }[][] = []
  const gaps: Gap[] = []
  let run: { x: number; y: number }[] = []
  let lastReading: number | null = null
  const tolerance = res * 1.5
  for (const point of sorted) {
    if (point.y == null || !Number.isFinite(point.y)) {
      if (run.length) runs.push(run)
      run = []
      continue
    }
    const previous = run[run.length - 1]
    if (previous && point.x - previous.x > tolerance) {
      runs.push(run)
      run = []
    }
    if (lastReading != null) {
      const missing = point.x - lastReading - res
      if (missing > 3 * res) gaps.push({ from: lastReading, to: point.x })
    }
    run.push({ x: point.x, y: point.y })
    lastReading = point.x
  }
  if (run.length) runs.push(run)
  return { runs, gaps }
}

/**
 * Averages consecutive points into at most `maxPoints` buckets so a day of minute
 * readings reads as a line, not a comb. A bucket whose points are all null stays
 * null (the gap survives); x is the bucket's last x so "now" stays at the edge.
 */
/**
 * The points a chart over `domain` draws: one step either side of it, so the line runs off
 * both edges. Clipping before downsampling keeps a zoom at full resolution; downsampling a
 * whole week into a ten-hour window would merge an hour of readings into each point and
 * start the line up to an hour late.
 */
export function clipToDomain(
  points: ChartPoint[],
  domain: [number, number] | undefined,
  step = inferResolution(points)
): ChartPoint[] {
  if (!domain) return points
  const [from, to] = domain
  return points.filter((point) => point.x >= from - step && point.x <= to + step)
}

export function downsample(
  points: ChartPoint[],
  maxPoints: number
): ChartPoint[] {
  if (maxPoints < 2 || points.length <= maxPoints) return points
  const size = Math.ceil(points.length / maxPoints)
  const out: ChartPoint[] = []
  for (let start = 0; start < points.length; start += size) {
    const bucket = points.slice(start, start + size)
    const values = bucket
      .map((point) => point.y)
      .filter(
        (value): value is number => value != null && Number.isFinite(value)
      )
    out.push({
      x: bucket[bucket.length - 1].x,
      y: values.length
        ? values.reduce((sum, value) => sum + value, 0) / values.length
        : null,
    })
  }
  return out
}

/**
 * True for a bucket still filling up: [ts, ts + res) hasn't ended. Charts leave it out
 * so the newest point never dips because only part of its minute is counted.
 */
export function isPartialBucket(
  ts: number,
  res: number,
  now = Date.now() / 1000
): boolean {
  return ts + res > now + 1
}
