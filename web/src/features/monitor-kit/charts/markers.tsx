// Chart markers (brief 4.4): tone per kind, SVG glyphs, and zoom-aware clustering.
//
// Usage: MonitorTimeChart renders these from `markers: ChartMarker[]`; other
// surfaces can reuse `markerTone`, `markerKindName` and `<MarkerGlyphIcon />` for
// legends. Markers come in two lanes:
//   - event markers (inbox, report, analysis) draw a 16px glyph above the plot,
//     clustered so glyphs never overlap at any width, with a count;
//   - index changes draw a subtle neutral tick on the x axis. They are context
//     only: no tone, no glyph, no claim about what they caused.
import type { ChartMarker, ChartMarkerKind } from '@/lib/monitor/types'
import { type ChartTone, TONE_COLOR } from './chartTheme'

/** Kinds that assert an index caused a change; charts never draw them. */
const CAUSAL_KINDS = new Set<ChartMarkerKind>([
  'fix_confirmed',
  'fix_no_effect',
])

export const isIndexMarker = (marker: ChartMarker) =>
  marker.kind === 'index_added' || marker.kind === 'index_dropped'

/** Splits markers into the glyph lane and the index tick lane. */
export function splitMarkers(markers: ChartMarker[]): {
  events: ChartMarker[]
  index: ChartMarker[]
} {
  const events: ChartMarker[] = []
  const index: ChartMarker[] = []
  for (const marker of markers) {
    if (CAUSAL_KINDS.has(marker.kind)) continue
    if (isIndexMarker(marker)) index.push(marker)
    else events.push(marker)
  }
  return { events, index }
}

export function markerTone(marker: ChartMarker): ChartTone {
  if (marker.kind !== 'inbox') return 'neutral'
  const severity = marker.severity ?? 0
  if (severity >= 0.7) return 'negative'
  if (severity >= 0.4) return 'warning'
  return 'neutral'
}

const KIND_NAME: Record<ChartMarkerKind, string> = {
  index_added: 'Index change',
  index_dropped: 'Index change',
  fix_confirmed: 'Latency change',
  fix_no_effect: 'Latency change',
  analysis: 'Analyzed',
  inbox: 'Inbox',
  report: 'Report written',
}

/** Short category name for legends and screen readers. */
export const markerKindName = (kind: ChartMarkerKind) => KIND_NAME[kind]

export interface MarkerCluster {
  /** Pixel x of the glyph: the mean of its markers' positions. */
  x: number
  markers: ChartMarker[]
}

/** Minimum pixel distance between two glyph clusters for a plot this wide. */
export function clusterGap(plotWidth: number): number {
  return Math.max(28, plotWidth / 14)
}

/**
 * Groups markers so no two clusters sit closer than `gap` px. Neighbours within
 * `gap` of a cluster's position join it; a final pass merges clusters whose mean
 * positions drifted closer than `gap`.
 */
export function clusterMarkers(
  markers: ChartMarker[],
  xFor: (ts: number) => number,
  gap = 28
): MarkerCluster[] {
  const sorted = [...markers].sort((a, b) => a.ts - b.ts)
  const groups: { xs: number[]; markers: ChartMarker[] }[] = []
  for (const marker of sorted) {
    const x = xFor(marker.ts)
    const last = groups[groups.length - 1]
    if (last && x - last.xs[0] < gap) {
      last.xs.push(x)
      last.markers.push(marker)
    } else groups.push({ xs: [x], markers: [marker] })
  }
  const mean = (xs: number[]) => xs.reduce((sum, x) => sum + x, 0) / xs.length
  const merged: { xs: number[]; markers: ChartMarker[] }[] = []
  for (const group of groups) {
    const last = merged[merged.length - 1]
    if (last && mean(group.xs) - mean(last.xs) < gap) {
      last.xs.push(...group.xs)
      last.markers.push(...group.markers)
    } else merged.push({ xs: [...group.xs], markers: [...group.markers] })
  }
  return merged.map((group) => ({ x: mean(group.xs), markers: group.markers }))
}

const TONE_ORDER: ChartTone[] = ['negative', 'warning', 'positive', 'neutral']

/** The most severe tone in a cluster drives its glyph color. */
export function clusterTone(cluster: MarkerCluster): ChartTone {
  const tones = cluster.markers.map(markerTone)
  return TONE_ORDER.find((tone) => tones.includes(tone)) ?? 'neutral'
}

/** The marker a click on the cluster opens: the most severe inbox item, else the latest. */
export function clusterLead(cluster: MarkerCluster): ChartMarker {
  const tone = clusterTone(cluster)
  const toned = cluster.markers.filter((marker) => markerTone(marker) === tone)
  return toned[toned.length - 1] ?? cluster.markers[cluster.markers.length - 1]
}

function GlyphShape({ kind }: { kind: ChartMarkerKind }) {
  const stroke = {
    stroke: 'var(--color-surface-layout-1)',
    strokeWidth: 1.6,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    fill: 'none',
  }
  switch (kind) {
    case 'report':
      return <path d="M-2.2 -3H1L2.4 -1.6V3H-2.2Z M-0.9 0.2H1.1" {...stroke} />
    case 'inbox':
      return <path d="M0 -3V0.8M0 2.8V2.9" {...stroke} />
    default:
      return <circle r="1.6" fill="var(--color-surface-layout-1)" />
  }
}

/** A marker glyph centered on (0, 0); a cluster shows its count in a pill. */
export function MarkerGlyph({
  kind,
  tone,
  count = 1,
  active = false,
}: {
  kind: ChartMarkerKind
  tone: ChartTone
  count?: number
  active?: boolean
}) {
  const color = TONE_COLOR[tone]
  if (count > 1) {
    const label = count > 99 ? '99+' : String(count)
    const width = Math.max(16, 7 + label.length * 6) + (active ? 2 : 0)
    const height = active ? 18 : 16
    return (
      <g>
        <rect
          x={-width / 2}
          y={-height / 2}
          width={width}
          height={height}
          rx={height / 2}
          fill={color}
          stroke="var(--color-surface-layout-1)"
          strokeWidth="1.5"
        />
        <text
          y="3.5"
          textAnchor="middle"
          fontSize="10"
          fontWeight="700"
          fill="var(--color-surface-layout-1)"
          className="tabular-nums"
        >
          {label}
        </text>
      </g>
    )
  }
  const small = kind === 'analysis'
  return (
    <g>
      <circle
        r={small ? 4.5 : active ? 8.5 : 7.5}
        fill={color}
        stroke="var(--color-surface-layout-1)"
        strokeWidth="1.5"
        style={{ transition: 'r 150ms var(--ease-base)' }}
      />
      {!small && <GlyphShape kind={kind} />}
    </g>
  )
}

/** Standalone 16px glyph for legends and lists outside a chart. */
export function MarkerGlyphIcon({
  marker,
  className,
}: {
  marker: ChartMarker
  className?: string
}) {
  return (
    <svg
      viewBox="-9 -9 18 18"
      width="16"
      height="16"
      aria-hidden="true"
      className={className}
    >
      {isIndexMarker(marker) ? (
        <path
          d="M0 -6V6"
          stroke="var(--color-content-layout-3)"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      ) : (
        <MarkerGlyph kind={marker.kind} tone={markerTone(marker)} />
      )}
    </svg>
  )
}
