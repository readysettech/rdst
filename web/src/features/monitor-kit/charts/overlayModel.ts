// The highlighted query on the load charts. A query that has its own band is raised in
// place; any other query (a later page, or folded into "Everything else") is drawn as
// its own line over the stack, from its series for the same window. Pure functions.
import { isPartialBucket } from './chartMath'
import type { LoadMetric, LoadModel } from './loadModel'

/** A query's own line over the chart, on its own timestamps. */
export interface ChartOverlay {
  id: string
  label: string
  color: string
  xs: number[]
  values: (number | null)[]
}

/** The line color of a query without a band: the page's ink, unlike any band. */
export const OVERLAY_COLOR = 'var(--color-content-layout-1)'

export interface HighlightPick {
  key: string
  /** 'band' raises its band; 'overlay' draws the query's own line. */
  mode: 'band' | 'overlay'
}

export interface SeriesPoint {
  ts: number
  qps: number | null
  mean_ms: number | null
}

/**
 * Which query the charts show. A hovered row wins while the pointer is on it, when its
 * series is at hand (hovering never fetches); otherwise the open query.
 */
export function pickHighlight(
  model: Pick<LoadModel, 'bands'> | null | undefined,
  {
    openKey,
    hoverKey,
    hasSeries,
  }: {
    openKey: string | null
    hoverKey: string | null
    hasSeries: (key: string) => boolean
  }
): HighlightPick | null {
  const inBand = (key: string) =>
    Boolean(model?.bands.some((band) => band.key === key && !band.isOther))
  const isOtherBand = (key: string) =>
    Boolean(model?.bands.some((band) => band.key === key && band.isOther))
  if (
    hoverKey &&
    hoverKey !== openKey &&
    !isOtherBand(hoverKey) &&
    !inBand(hoverKey) &&
    hasSeries(hoverKey)
  )
    return { key: hoverKey, mode: 'overlay' }
  if (!openKey) return null
  return { key: openKey, mode: inBand(openKey) ? 'band' : 'overlay' }
}

function stepOf(xs: number[], fallback: number): number {
  let best = Number.POSITIVE_INFINITY
  for (let i = 1; i < xs.length; i++) {
    const step = xs[i] - xs[i - 1]
    if (step > 0 && step < best) best = step
  }
  return Number.isFinite(best) ? best : fallback
}

/**
 * The query's values for a metric: calls per second, mean latency, or its share of the
 * database time the model recorded over the same bucket, in percent. The bucket still
 * filling up reads low and is left out.
 */
export function overlayValues(
  model: Pick<LoadModel, 'xs' | 'res' | 'load'>,
  metric: LoadMetric,
  points: SeriesPoint[],
  now = Date.now() / 1000
): { xs: number[]; values: (number | null)[] } {
  const sorted = [...points].sort((a, b) => a.ts - b.ts)
  const step = stepOf(
    sorted.map((point) => point.ts),
    model.res
  )
  const kept = sorted.filter((point) => !isPartialBucket(point.ts, step, now))
  const xs = kept.map((point) => point.ts)
  if (metric === 'calls') return { xs, values: kept.map((point) => point.qps) }
  if (metric === 'latency')
    return { xs, values: kept.map((point) => point.mean_ms) }
  const totals = model.xs.map((_, i) =>
    model.load.reduce((sum, band) => sum + (band[i] ?? 0), 0)
  )
  const totalAt = (ts: number): number | null => {
    let sum = 0
    let count = 0
    let nearest: number | null = null
    let nearestDistance = Math.max(step, model.res)
    for (let i = 0; i < model.xs.length; i++) {
      const x = model.xs[i]
      if (x >= ts && x < ts + step) {
        sum += totals[i]
        count++
      }
      const distance = Math.abs(x - ts)
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearest = totals[i]
      }
    }
    return count ? sum / count : nearest
  }
  const values = kept.map((point) => {
    if (point.qps == null || point.mean_ms == null) return null
    const total = totalAt(point.ts)
    if (!total || total <= 0) return null
    return Math.min(100, ((point.qps * point.mean_ms) / total) * 100)
  })
  return { xs, values }
}

/** The overlay for a picked query, or null when there's nothing to draw. */
export function buildOverlay(
  model: Pick<LoadModel, 'xs' | 'res' | 'load'> | null | undefined,
  metric: LoadMetric,
  query: { key: string; label: string; series: SeriesPoint[] } | null,
  now = Date.now() / 1000
): ChartOverlay | null {
  if (!model || !query) return null
  const { xs, values } = overlayValues(model, metric, query.series, now)
  return {
    id: query.key,
    label: query.label,
    color: OVERLAY_COLOR,
    xs,
    values,
  }
}
