// Series and tone colors for monitor charts, plus the kit's one style block.
//
// Usage:
//   <MonitorTimeChart series={[{ id: 'qps', color: SERIES_COLOR.throughput, ... }]} />
//   <MiniTrend color={SERIES_COLOR.latency} ... />
// A series color is identity, not judgment (brief 4.2): good/bad lives in markers,
// tags and copy, via TONE_COLOR.
import './monitorKit.css'

export type MonitorMetric =
  | 'throughput'
  | 'latency'
  | 'connections'
  | 'hit'
  | 'rollback'

export const SERIES_COLOR: Record<MonitorMetric | 'neutral', string> = {
  throughput: 'var(--color-content-viz-throughput)',
  latency: 'var(--color-content-viz-latency)',
  connections: 'var(--color-content-viz-connections)',
  hit: 'var(--color-content-viz-hit)',
  rollback: 'var(--color-content-viz-rollback)',
  neutral: 'var(--color-content-viz-neutral)',
}

export type ChartTone = 'positive' | 'warning' | 'negative' | 'neutral'

export const TONE_COLOR: Record<ChartTone, string> = {
  positive: 'var(--color-content-positive-plain)',
  warning: 'var(--color-content-warning-plain)',
  negative: 'var(--color-content-negative-plain)',
  neutral: 'var(--color-content-layout-2)',
}

export const GRID_COLOR = 'var(--color-border-layout-1)'
