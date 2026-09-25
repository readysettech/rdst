// The five health tiles (brief 3.1): values, trend points, footers and tone rules.
// Pure, so the rules are unit-tested without rendering.
import {
  type ChartPoint,
  type ChartTone,
  type MonitorMetric,
  PRIOR_LABEL,
  SERIES_COLOR,
} from '@/features/monitor-kit/charts'
import {
  formatChange,
  formatCompact,
  formatMsWithUnit,
  formatQps,
  formatRatio,
  plural,
} from '@/features/monitor-kit/format'
import type { HealthPoint, MonitorHealth } from '@/lib/monitor/types'

export const HEALTH_METRICS: MonitorMetric[] = [
  'throughput',
  'latency',
  'connections',
  'hit',
  'rollback',
]

export const METRIC_TITLE: Record<MonitorMetric, string> = {
  throughput: 'Throughput',
  latency: 'Latency',
  connections: 'Connections',
  hit: 'Cache hit ratio',
  rollback: 'Rollbacks',
}

export interface TileModel {
  metric: MonitorMetric
  title: string
  color: string
  /** Latest reading ("now"), or the range total for rollbacks. */
  value: string
  unit: string
  /** Inline secondary figure next to the value ('0 deadlocks'). */
  aside: string | null
  footer: string
  tone: ChartTone
  /** Why the tile is toned (tooltip and screen readers). */
  toneReason: string | null
  points: ChartPoint[]
  formatPoint: (value: number) => string
  /** Chart hints. */
  yMin?: number | 'auto'
  yCap?: number
  unitShort: string
}

type Pick = (point: HealthPoint) => number | null

const PICK: Record<MonitorMetric, Pick> = {
  throughput: (point) => point.qps,
  latency: (point) => point.mean_ms,
  connections: (point) => point.active_conns,
  hit: (point) => point.cache_hit_ratio,
  rollback: (point) => point.xact_rollback,
}

export function metricPoints(
  health: MonitorHealth,
  metric: MonitorMetric
): ChartPoint[] {
  const pick = PICK[metric]
  return health.series.map((point) => ({ x: point.ts, y: pick(point) }))
}

function vsPrior(
  now: number | null | undefined,
  before: number | null | undefined,
  health: MonitorHealth
): string {
  if (!health.prior || health.range === 'all') return 'No prior period yet'
  const change = formatChange(now, before)
  if (!change) return 'No prior period yet'
  const prior = PRIOR_LABEL[health.range]
  return change.direction === 'flat'
    ? `No change vs ${prior}`
    : `${change.text} vs ${prior}`
}

export function tileModels(health: MonitorHealth): TileModel[] {
  const { now, period, prior } = health
  const latency = formatMsWithUnit(now.mean_ms)

  const maxConns = now.max_conns
  const connRatio =
    now.active_conns != null && maxConns ? now.active_conns / maxConns : null
  const connTone: ChartTone =
    connRatio != null && connRatio > 0.8 ? 'warning' : 'neutral'

  const hitMin = period.cache_hit_ratio_min
  const hitTone: ChartTone =
    hitMin != null && hitMin < 0.99 ? 'warning' : 'neutral'

  const rollbackChange =
    prior && prior.xact_rollback > 0
      ? (period.xact_rollback - prior.xact_rollback) / prior.xact_rollback
      : null
  const rollbackUp =
    rollbackChange != null && rollbackChange > 0.5 && period.xact_rollback > 100
  const rollbackTone: ChartTone =
    period.deadlocks > 0 ? 'negative' : rollbackUp ? 'warning' : 'neutral'

  return [
    {
      metric: 'throughput',
      title: METRIC_TITLE.throughput,
      color: SERIES_COLOR.throughput,
      value: formatQps(now.qps),
      unit: 'qps',
      aside: null,
      footer: vsPrior(period.qps, prior?.qps, health),
      tone: 'neutral',
      toneReason: null,
      points: metricPoints(health, 'throughput'),
      formatPoint: (value) => `${formatQps(value)} qps`,
      unitShort: 'qps',
    },
    {
      metric: 'latency',
      title: METRIC_TITLE.latency,
      color: SERIES_COLOR.latency,
      value: latency.value,
      unit: latency.unit,
      aside: null,
      footer: vsPrior(period.mean_ms, prior?.mean_ms, health),
      tone: 'neutral',
      toneReason: null,
      points: metricPoints(health, 'latency'),
      formatPoint: (value) => {
        const formatted = formatMsWithUnit(value)
        return `${formatted.value} ${formatted.unit}`
      },
      unitShort: 'ms',
    },
    {
      metric: 'connections',
      title: METRIC_TITLE.connections,
      color: SERIES_COLOR.connections,
      value: now.active_conns == null ? '-' : String(now.active_conns),
      unit: maxConns ? `of ${maxConns}` : 'active',
      aside: null,
      footer:
        period.active_conns_peak == null
          ? 'No readings in range'
          : `Peak ${period.active_conns_peak}${maxConns ? ` of ${maxConns}` : ''}`,
      tone: connTone,
      toneReason:
        connTone === 'warning' ? 'Over 80% of the connection limit' : null,
      points: metricPoints(health, 'connections'),
      formatPoint: (value) => `${Math.round(value)} active`,
      unitShort: 'active',
    },
    {
      metric: 'hit',
      title: METRIC_TITLE.hit,
      color: SERIES_COLOR.hit,
      value: formatRatio(now.cache_hit_ratio),
      unit: now.cache_hit_ratio == null ? '' : '%',
      aside: null,
      footer:
        hitMin == null ? 'No readings in range' : `Low ${formatRatio(hitMin)}%`,
      tone: hitTone,
      toneReason:
        hitTone === 'warning' ? 'Dipped below 99% in this range' : null,
      points: metricPoints(health, 'hit'),
      formatPoint: (value) => `${formatRatio(value, 2)}%`,
      yMin: 'auto',
      yCap: 1,
      unitShort: '%',
    },
    {
      metric: 'rollback',
      title: METRIC_TITLE.rollback,
      color: SERIES_COLOR.rollback,
      value: formatCompact(period.xact_rollback),
      unit: '',
      aside: plural(period.deadlocks, 'deadlock'),
      footer: vsPrior(period.xact_rollback, prior?.xact_rollback, health),
      tone: rollbackTone,
      toneReason:
        period.deadlocks > 0
          ? `${plural(period.deadlocks, 'deadlock')} in this range`
          : rollbackUp
            ? 'Up more than 50% vs the prior period'
            : null,
      points: metricPoints(health, 'rollback'),
      formatPoint: (value) => `${formatCompact(value)} rollbacks`,
      unitShort: 'rollbacks',
    },
  ]
}

/** Overall tone for the glyph halo: the worst tile tone. */
export function overallTone(tiles: TileModel[]): ChartTone {
  if (tiles.some((tile) => tile.tone === 'negative')) return 'warning'
  if (tiles.some((tile) => tile.tone === 'warning')) return 'warning'
  return 'positive'
}
