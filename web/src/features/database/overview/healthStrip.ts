// The three context tiles under the hero (DESIGN_V4 section 2): connections, cache
// hit ratio and rollbacks, computed from the health series inside the chart's window
// so they re-scope with a zoom. Ratios and utilization get fixed, honest axes.
import {
  type ChartPoint,
  type ChartTone,
  isPartialBucket,
  SERIES_COLOR,
} from '@/features/monitor-kit/charts'
import {
  formatHitRatio,
  formatQps,
  plural,
} from '@/features/monitor-kit/format'
import type { MonitorHealth } from '@/lib/monitor/types'
import type { HealthPanel } from '../useDatabaseUrl'

export interface StripTile {
  id: HealthPanel
  title: string
  color: string
  value: string
  unit: string
  footer: string
  tone: ChartTone
  toneReason: string | null
  points: ChartPoint[]
  formatPoint: (value: number) => string
  formatAxis: (value: number) => string
  /** Fixed axis: ratios and utilization never auto-scale. */
  yMin?: number
  yMax?: number
  /** Shown under the tile and above its chart. */
  explain?: string
  /** The chart's subtitle when this tile is the selected metric. */
  subtitle: string
}

export const ROLLBACK_EXPLAINER =
  'Rollbacks: transactions your app started and then cancelled. Apps roll back on errors and timeouts, and some frameworks roll back after every read. A sudden rise usually means something started failing.'

const inWindow = (health: MonitorHealth, [start, end]: [number, number]) =>
  health.series.filter((point) => point.ts >= start && point.ts <= end)

const percentAxis = (value: number) => `${Math.round(value * 1000) / 10}%`

/** Floor of the hit-ratio axis: 90% unless the data dips lower, then the next 10%. */
export function hitAxisFloor(min: number | null): number {
  if (min == null || min >= 0.9) return 0.9
  return Math.max(0, Math.floor(min * 10) / 10)
}

export function stripTiles(
  source: MonitorHealth,
  domain: [number, number],
  now = Date.now() / 1000
): StripTile[] {
  // The bucket still filling up would read low; leave it out.
  const health = {
    ...source,
    series: source.series.filter(
      (point) => !isPartialBucket(point.ts, source.res, now)
    ),
  }
  const points = inWindow(health, domain)
  const minutes = Math.max(1, (domain[1] - domain[0]) / 60)

  const maxConns = health.now.max_conns
  const conns = points
    .map((point) => point.active_conns)
    .filter((value): value is number => value != null)
  const peak = conns.length ? Math.max(...conns) : null
  const connTone: ChartTone =
    peak != null && maxConns && peak / maxConns > 0.8 ? 'warning' : 'neutral'

  const hits = points
    .map((point) => point.cache_hit_ratio)
    .filter((value): value is number => value != null)
  const low = hits.length ? Math.min(...hits) : null
  const hitTone: ChartTone = low != null && low < 0.99 ? 'warning' : 'neutral'
  // The newest reading, even when the latest bucket has none.
  const hitNow =
    [...health.series].reverse().find((point) => point.cache_hit_ratio != null)
      ?.cache_hit_ratio ?? health.now.cache_hit_ratio

  const rollbacks = points.reduce(
    (sum, point) => sum + (point.xact_rollback ?? 0),
    0
  )
  const deadlocks = points.reduce(
    (sum, point) => sum + (point.deadlocks ?? 0),
    0
  )
  const perMinute = rollbacks / minutes

  return [
    {
      id: 'connections',
      title: 'Connections',
      color: SERIES_COLOR.connections,
      value:
        health.now.active_conns == null ? '-' : String(health.now.active_conns),
      unit: maxConns ? `of ${maxConns}` : 'active',
      footer:
        peak == null
          ? 'No readings here'
          : `Peak ${peak}${maxConns ? ` of ${maxConns}` : ''}`,
      tone: connTone,
      toneReason:
        connTone === 'warning' ? 'Over 80% of the connection limit' : null,
      points: health.series.map((point) => ({
        x: point.ts,
        y: point.active_conns,
      })),
      formatPoint: (value) => `${Math.round(value)} active`,
      formatAxis: (value) => String(Math.round(value)),
      yMin: 0,
      yMax: maxConns ?? undefined,
      subtitle: maxConns
        ? `Active connections out of ${maxConns} allowed`
        : 'Active connections',
    },
    {
      id: 'hit',
      title: 'Cache hit ratio',
      color: SERIES_COLOR.hit,
      value: formatHitRatio(hitNow),
      unit: hitNow == null ? '' : '%',
      footer:
        low == null
          ? 'No readings here'
          : low >= 1
            ? '100%'
            : `Low ${formatHitRatio(low)}%`,
      tone: hitTone,
      toneReason: hitTone === 'warning' ? 'Dipped below 99% here' : null,
      points: health.series.map((point) => ({
        x: point.ts,
        y: point.cache_hit_ratio,
      })),
      formatPoint: (value) => `${formatHitRatio(value)}%`,
      formatAxis: percentAxis,
      yMin: hitAxisFloor(low),
      yMax: 1,
      explain: 'Share of reads served from memory instead of disk.',
      subtitle: 'Share of reads served from memory instead of disk',
    },
    {
      id: 'rollback',
      title: 'Rollbacks',
      color: SERIES_COLOR.rollback,
      value:
        perMinute >= 10
          ? String(Math.round(perMinute))
          : String(Math.round(perMinute * 10) / 10),
      unit: 'a minute',
      footer: plural(deadlocks, 'deadlock'),
      tone: deadlocks > 0 ? 'negative' : 'neutral',
      toneReason:
        deadlocks > 0 ? `${plural(deadlocks, 'deadlock')} here` : null,
      points: health.series.map((point) => ({
        x: point.ts,
        y: point.xact_rollback,
      })),
      formatPoint: (value) => `${formatQps(value)} rollbacks`,
      formatAxis: (value) => formatQps(value),
      yMin: 0,
      explain:
        'Transactions your app started and then cancelled. A sudden rise usually means something started failing.',
      subtitle: 'Transactions your app started and then cancelled',
    },
  ]
}
