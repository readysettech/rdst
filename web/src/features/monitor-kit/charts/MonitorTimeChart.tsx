// MonitorTimeChart - the large time chart of the monitor pages (brief 4.1).
//
// Same visual grammar as the Demo's ComparisonLineChart (gradient area at 0.16,
// 2.2px lines, three gridlines on a nice max, end-of-line value labels, dashed
// annotation verticals, snapping crosshair + value card), plus: responsive width,
// gaps for missing data, typed markers, x ticks, draw-in, cross-chart hover sync.
//
// Usage:
//   <MonitorTimeChart
//     ariaLabel="Throughput, last 24 hours"
//     domain={domainFor(range, history, now)}      // from ./rangeModel
//     resolution={health.res}                      // bucket seconds; gap detection
//     series={[{ id: 'qps', label: 'Throughput', color: SERIES_COLOR.throughput,
//                unit: 'qps', points: health.series.map(p => ({ x: p.ts, y: p.qps })) }]}
//     formatValue={(v) => formatQps(v)}            // unit is appended from series.unit
//     markers={health.markers}                     // ChartMarker[] (brief 4.4)
//     onMarkerClick={(m) => ...}
//     syncId="query-abc"                           // share the crosshair with siblings
//     height={280}
//   />
// Timestamps are Unix seconds. A `null` y breaks the line; a missing span longer
// than three buckets draws a dotted baseline with the hover label "No readings".
import { cn } from '@rs/tailwind-base'
import {
  Fragment,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { ChartMarker } from '@/lib/monitor/types'
import { useElementWidth, useEntrance, useHoverX } from './chartHooks'
import {
  type ChartPoint,
  clipToDomain,
  downsample,
  inferResolution,
  segmentPoints,
  type YScale,
  yDomain,
  yFraction,
} from './chartMath'
import { GRID_COLOR, TONE_COLOR } from './chartTheme'
import {
  type ChartHighlight,
  HighlightBand,
  useChartHighlight,
} from './highlight'
import { PauseBands } from './PauseBands'
import { type ChartPause, PAUSE_LABEL, pauseAt } from './pauseModel'
import {
  clusterGap,
  clusterLead,
  clusterMarkers,
  clusterTone,
  type MarkerCluster,
  MarkerGlyph,
  markerTone,
  splitMarkers,
} from './markers'
import { hoverLabel, ticksFor } from './rangeModel'

export interface MonitorSeries {
  id: string
  label: string
  /** A CSS color, normally from SERIES_COLOR. */
  color: string
  points: ChartPoint[]
  /** Thicker line (2.6px) for the series the chart is about. */
  emphasis?: boolean
  /** Appended to values in end labels and the hover card ('qps', 'ms', '%'). */
  unit?: string
}

export interface MonitorTimeChartProps {
  series: MonitorSeries[]
  ariaLabel: string
  /** x domain in Unix seconds; defaults to the data extent. */
  domain?: [number, number]
  /** Plot height in px (default 280). */
  height?: number
  /** Bucket size in seconds; inferred from the data when omitted. */
  resolution?: number
  yScale?: YScale
  /** 0 by default; 'auto' fits a tight nice window (ratios near 100%). */
  yMin?: number | 'auto'
  yMax?: number
  /** Upper bound the axis never exceeds (1 for ratios). */
  yCap?: number
  formatValue?: (value: number, series: MonitorSeries) => string
  formatAxis?: (value: number) => string
  markers?: ChartMarker[]
  onMarkerClick?: (marker: ChartMarker) => void
  syncId?: string
  /** Drag across the plot to zoom into [start, end] (Unix seconds). */
  onZoom?: (window: [number, number]) => void
  /** A shaded window (an incident); defaults to ChartHighlightContext. */
  highlight?: ChartHighlight | null
  /** Spans with no readings, hatched and labelled with the reason (pauseModel). */
  pauses?: ChartPause[]
  emptyLabel?: string
  /** Hide end-of-line labels (they also hide below 360px). */
  hideEndLabels?: boolean
  className?: string
}

const TOP = 26
const BOTTOM = 26
const LEFT = 46
const END_LABEL_WIDTH = 76
const CARD_ROWS = 6

function clusterTimeLabel(
  cluster: MarkerCluster,
  spanSeconds: number,
  res: number
): string {
  const first = cluster.markers[0].ts
  const last = cluster.markers[cluster.markers.length - 1].ts
  const start = hoverLabel(first, spanSeconds, res)
  if (last - first < res) return start
  return `${start} to ${hoverLabel(last, spanSeconds, res)}`
}

function defaultFormat(value: number): string {
  if (Math.abs(value) >= 1000) return `${Math.round(value / 100) / 10}k`
  if (Math.abs(value) >= 10) return String(Math.round(value))
  if (Math.abs(value) >= 1) return String(Math.round(value * 10) / 10)
  return String(Math.round(value * 100) / 100)
}

function withUnit(text: string, unit?: string) {
  if (!unit) return text
  return unit === '%' ? `${text}%` : `${text} ${unit}`
}

function linePath(
  run: { x: number; y: number }[],
  xFor: (x: number) => number,
  yFor: (y: number) => number
) {
  return run
    .map(
      (point, index) =>
        `${index ? 'L' : 'M'}${xFor(point.x).toFixed(1)} ${yFor(point.y).toFixed(1)}`
    )
    .join('')
}

export function MonitorTimeChart({
  series: rawSeries,
  ariaLabel,
  domain,
  height = 280,
  resolution,
  yScale = 'linear',
  yMin,
  yMax,
  yCap,
  formatValue,
  formatAxis,
  markers = [],
  onMarkerClick,
  syncId,
  onZoom,
  highlight: ownHighlight,
  pauses,
  emptyLabel = 'No readings in this range yet',
  hideEndLabels = false,
  className,
}: MonitorTimeChartProps) {
  const highlight = useChartHighlight(ownHighlight)
  const chartId = useId().replaceAll(':', '')
  const [containerRef, width] = useElementWidth<HTMLDivElement>()
  const [hoverX, setHoverX] = useHoverX(syncId)
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null)
  const [hoverCluster, setHoverCluster] = useState<MarkerCluster | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  // At most one point per 2px of plot, counted inside the domain; the hover card reads the
  // same buckets.
  const series = useMemo(
    () =>
      rawSeries.map((item) => ({
        ...item,
        points: downsample(
          clipToDomain(
            item.points,
            domain,
            resolution ?? inferResolution(item.points)
          ),
          Math.max(24, Math.floor((width || 640) / 2))
        ),
      })),
    [rawSeries, width, domain, resolution]
  )

  const allPoints = useMemo(
    () => series.flatMap((item) => item.points),
    [series]
  )
  const readings = useMemo(
    () =>
      allPoints.filter(
        (point): point is { x: number; y: number } =>
          point.y != null && Number.isFinite(point.y)
      ),
    [allPoints]
  )
  const hasData = readings.length > 0
  const entering = useEntrance(hasData && width > 0)

  const res = Math.max(resolution ?? 0, inferResolution(allPoints))
  const [x0, x1] = useMemo<[number, number]>(() => {
    if (domain) return domain
    if (!allPoints.length) return [0, 1]
    const xs = allPoints.map((point) => point.x)
    return [Math.min(...xs), Math.max(...xs)]
  }, [domain, allPoints])
  const spanSeconds = Math.max(1, x1 - x0)

  const showEndLabels = !hideEndLabels && width >= 360
  const right = showEndLabels ? END_LABEL_WIDTH : 14
  const left = width < 360 ? 38 : LEFT
  const plotWidth = Math.max(1, width - left - right)
  const plotHeight = Math.max(1, height - TOP - BOTTOM)
  const bottomY = TOP + plotHeight

  const y = useMemo(
    () =>
      yDomain(
        readings.map((point) => point.y),
        { scale: yScale, min: yMin, max: yMax, cap: yCap }
      ),
    [readings, yScale, yMin, yMax, yCap]
  )
  const xFor = (x: number) => left + ((x - x0) / spanSeconds) * plotWidth
  const yFor = (value: number) =>
    TOP + plotHeight - yFraction(value, y) * plotHeight
  const fmt = (value: number, item: MonitorSeries) =>
    withUnit(
      formatValue ? formatValue(value, item) : defaultFormat(value),
      item.unit
    )
  const axisFmt = formatAxis ?? defaultFormat

  const shapes = useMemo(
    () =>
      series.map((item) => ({
        item,
        ...segmentPoints(
          item.points.filter(
            (point) => point.x >= x0 - res && point.x <= x1 + res
          ),
          res
        ),
      })),
    [series, res, x0, x1]
  )
  const gaps = shapes[0]?.gaps ?? []

  const ticks = useMemo(
    () =>
      width ? ticksFor([x0, x1], width < 480 ? 4 : width >= 900 ? 8 : 6) : [],
    [x0, x1, width]
  )

  const visibleMarkers = markers.filter(
    (marker) => marker.ts >= x0 && marker.ts <= x1
  )
  const { events: eventMarkers, index: indexMarkers } =
    splitMarkers(visibleMarkers)
  const clusters = width
    ? clusterMarkers(eventMarkers, xFor, clusterGap(plotWidth))
    : []
  // One tick per pixel column: index changes are context, not events.
  const indexTicks = width
    ? [...new Set(indexMarkers.map((marker) => Math.round(xFor(marker.ts))))]
    : []

  // Snap to the nearest reading of the longest series.
  const anchor = useMemo(() => {
    const longest = [...series].sort(
      (a, b) => b.points.length - a.points.length
    )[0]
    return (longest?.points ?? []).filter(
      (point) => point.x >= x0 && point.x <= x1
    )
  }, [series, x0, x1])

  const clampPx = (px: number) => Math.min(left + plotWidth, Math.max(left, px))
  const timeAt = (px: number) =>
    x0 + ((clampPx(px) - left) / plotWidth) * spanSeconds

  function onPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.button !== 0 || !onZoom) return
    const rect = event.currentTarget.getBoundingClientRect()
    const px = clampPx(event.clientX - rect.left)
    event.currentTarget.setPointerCapture?.(event.pointerId)
    setDrag({ from: px, to: px })
  }

  function onPointerUp(event: ReactPointerEvent<SVGSVGElement>) {
    const current = drag
    setDrag(null)
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    if (!current || Math.abs(current.to - current.from) <= 6 || !onZoom) return
    const start = timeAt(Math.min(current.from, current.to))
    const end = timeAt(Math.max(current.from, current.to))
    if (end - start >= res * 2) onZoom([Math.floor(start), Math.ceil(end)])
  }

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (drag) {
      const rect = event.currentTarget.getBoundingClientRect()
      setDrag({ from: drag.from, to: clampPx(event.clientX - rect.left) })
    }
    if (!anchor.length) return
    const rect = event.currentTarget.getBoundingClientRect()
    const px = event.clientX - rect.left
    const target = x0 + ((px - left) / plotWidth) * spanSeconds
    let best = anchor[0]
    for (const point of anchor)
      if (Math.abs(point.x - target) < Math.abs(best.x - target)) best = point
    setHoverX(best.x)
  }

  const hoverValues =
    hoverX == null
      ? []
      : series.flatMap((item) => {
          let best: ChartPoint | null = null
          for (const point of item.points)
            if (!best || Math.abs(point.x - hoverX) < Math.abs(best.x - hoverX))
              best = point
          if (!best || Math.abs(best.x - hoverX) > res * 1.5) return []
          return [{ item, point: best }]
        })
  const hoverGap =
    hoverX == null
      ? null
      : (gaps.find((gap) => hoverX > gap.from && hoverX < gap.to) ?? null)
  // Markers inside the hovered bucket (at least 6px either side of the crosshair).
  const hoverMarkers =
    hoverX == null
      ? []
      : [...eventMarkers, ...indexMarkers].filter(
          (marker) =>
            Math.abs(xFor(marker.ts) - xFor(hoverX)) <=
            Math.max(6, (res / spanSeconds) * plotWidth * 0.5)
        )

  // End-of-line labels with collision nudging (>= 14px apart).
  const endLabels = shapes
    .flatMap(({ item, runs }) => {
      const lastRun = runs[runs.length - 1]
      const last = lastRun?.[lastRun.length - 1]
      return last ? [{ item, point: last, labelY: yFor(last.y) }] : []
    })
    .sort((a, b) => a.labelY - b.labelY)
  for (let i = 1; i < endLabels.length; i++)
    if (endLabels[i].labelY - endLabels[i - 1].labelY < 14)
      endLabels[i].labelY = endLabels[i - 1].labelY + 14
  for (const label of endLabels)
    label.labelY = Math.min(bottomY - 2, Math.max(TOP + 6, label.labelY))

  const summary = useMemo(() => {
    const first = shapes[0]
    const firstReading = first?.runs[0]?.[0]
    const lastRun = first?.runs[first.runs.length - 1]
    const lastReading = lastRun?.[lastRun.length - 1]
    if (!first || !firstReading || !lastReading)
      return `${ariaLabel}. ${emptyLabel}.`
    const indexChanges = indexMarkers.length
    return `${ariaLabel}, from ${fmt(firstReading.y, first.item)} to ${fmt(lastReading.y, first.item)}${
      indexChanges
        ? `, ${indexChanges} index change${indexChanges === 1 ? '' : 's'}`
        : ''
    }`
  }, [shapes, ariaLabel, emptyLabel, indexMarkers.length])

  const cardLeft =
    hoverX == null
      ? 0
      : xFor(hoverX) + 14 + 220 > width
        ? xFor(hoverX) - 14 - 220
        : xFor(hoverX) + 14

  function onMarkerKey(
    event: KeyboardEvent<SVGGElement>,
    cluster: MarkerCluster
  ) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onMarkerClick?.(clusterLead(cluster))
    }
  }

  return (
    <div
      ref={containerRef}
      className={cn('mk-chart relative w-full', className)}
      style={{ height }}
    >
      {width > 0 && (
        <svg
          ref={svgRef}
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={summary}
          className={cn(
            'block touch-pan-y select-none overflow-visible',
            onZoom && (drag ? 'cursor-ew-resize' : 'cursor-crosshair')
          )}
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          onPointerMove={onPointerMove}
          onPointerLeave={() => {
            if (drag) return
            setHoverX(null)
            setHoverCluster(null)
          }}
        >
          <defs>
            {series.map((item) => (
              <linearGradient
                key={item.id}
                id={`${chartId}-${item.id}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="0%" stopColor={item.color} stopOpacity="0.16" />
                <stop offset="100%" stopColor={item.color} stopOpacity="0" />
              </linearGradient>
            ))}
            <clipPath id={`${chartId}-plot`}>
              <rect
                x={left - 2}
                y={TOP - 4}
                width={plotWidth + 8}
                height={plotHeight + 8}
              />
            </clipPath>
          </defs>

          {y.grid.map((value, index) => {
            const gy = TOP + plotHeight - (index / 2) * plotHeight
            return (
              <Fragment key={index}>
                <line
                  x1={left}
                  y1={gy}
                  x2={left + plotWidth}
                  y2={gy}
                  stroke={GRID_COLOR}
                  strokeWidth="1"
                />
                <text
                  x={left - 8}
                  y={gy + 4}
                  textAnchor="end"
                  className="fill-content-layout-3 text-[11px] tabular-nums"
                >
                  {axisFmt(value)}
                </text>
              </Fragment>
            )
          })}

          {ticks.map((tick) => {
            const tx = xFor(tick.x)
            if (tx < left + 14 || tx > left + plotWidth - 14) return null
            return (
              <Fragment key={tick.x}>
                <line
                  x1={tx}
                  y1={bottomY}
                  x2={tx}
                  y2={bottomY + 4}
                  stroke={GRID_COLOR}
                />
                <text
                  x={tx}
                  y={height - 6}
                  textAnchor="middle"
                  className="fill-content-layout-3 text-[11px]"
                >
                  {tick.label}
                </text>
              </Fragment>
            )
          })}

          <PauseBands
            pauses={pauses}
            xFor={xFor}
            top={TOP}
            height={plotHeight}
          />
          <HighlightBand
            highlight={highlight}
            domain={[x0, x1]}
            xFor={xFor}
            top={TOP}
            height={plotHeight}
          />

          {gaps.map((gap) => (
            <line
              key={`gap-${gap.from}`}
              x1={xFor(gap.from)}
              x2={xFor(gap.to)}
              y1={bottomY - 1}
              y2={bottomY - 1}
              stroke="var(--color-content-layout-3)"
              strokeWidth="1.5"
              strokeDasharray="1 4"
              strokeLinecap="round"
            />
          ))}

          {hoverCluster && (
            <line
              x1={hoverCluster.x}
              x2={hoverCluster.x}
              y1={TOP + 4}
              y2={bottomY}
              stroke={TONE_COLOR[clusterTone(hoverCluster)]}
              strokeWidth="1.5"
              strokeDasharray="3 4"
              pointerEvents="none"
            />
          )}

          <g pointerEvents="none">
            {indexTicks.map((tx) => (
              <line
                key={`itick-${tx}`}
                data-testid="index-tick"
                x1={tx}
                x2={tx}
                y1={bottomY - 5}
                y2={bottomY + 1}
                stroke="var(--color-content-layout-3)"
                strokeOpacity="0.7"
                strokeWidth="1.25"
                strokeLinecap="round"
              />
            ))}
          </g>

          <g clipPath={`url(#${chartId}-plot)`}>
            {shapes.map(({ item, runs }) => (
              <g key={item.id}>
                {runs.map((run, index) => {
                  if (run.length === 1)
                    return (
                      <circle
                        key={index}
                        cx={xFor(run[0].x)}
                        cy={yFor(run[0].y)}
                        r="2"
                        fill={item.color}
                      />
                    )
                  const d = linePath(run, xFor, yFor)
                  const firstPx = xFor(run[0].x).toFixed(1)
                  const lastPx = xFor(run[run.length - 1].x).toFixed(1)
                  return (
                    <Fragment key={index}>
                      <path
                        d={`${d}L${lastPx} ${bottomY}L${firstPx} ${bottomY}Z`}
                        fill={`url(#${chartId}-${item.id})`}
                        className={entering ? 'mk-fade' : undefined}
                      />
                      <path
                        d={d}
                        fill="none"
                        stroke={item.color}
                        strokeWidth={item.emphasis ? 2.6 : 2.2}
                        strokeLinejoin="round"
                        strokeLinecap="round"
                        pathLength={entering ? 1 : undefined}
                        className={entering ? 'mk-draw' : undefined}
                      />
                    </Fragment>
                  )
                })}
              </g>
            ))}
          </g>

          {endLabels.map(({ item, point, labelY }) => (
            <Fragment key={item.id}>
              <circle
                key={`ring-${point.x}`}
                cx={xFor(point.x)}
                cy={yFor(point.y)}
                r="3.5"
                fill={item.color}
                className={entering ? undefined : 'mk-ring'}
              />
              <circle
                key={`dot-${point.x}`}
                cx={xFor(point.x)}
                cy={yFor(point.y)}
                r="3.5"
                fill={item.color}
                className={entering ? undefined : 'mk-dot-pulse'}
              />
              {showEndLabels && (
                <text
                  x={left + plotWidth + 10}
                  y={labelY + 4}
                  className="text-[11px] font-semibold tabular-nums"
                  style={{ fill: item.color }}
                >
                  {fmt(point.y, item)}
                </text>
              )}
            </Fragment>
          ))}

          {drag && Math.abs(drag.to - drag.from) > 2 && (
            <rect
              pointerEvents="none"
              x={Math.min(drag.from, drag.to)}
              y={TOP}
              width={Math.abs(drag.to - drag.from)}
              height={plotHeight}
              fill="var(--color-content-layout-1)"
              fillOpacity="0.08"
              stroke="var(--color-content-layout-2)"
              strokeWidth="1"
            />
          )}
          {hoverX != null && !drag && (hoverValues.length > 0 || hoverGap) && (
            <g pointerEvents="none">
              <line
                x1={xFor(hoverX)}
                x2={xFor(hoverX)}
                y1={TOP}
                y2={bottomY}
                stroke="var(--color-content-layout-2)"
                strokeDasharray="2 3"
              />
              {hoverValues.map(({ item, point }) =>
                point.y == null ? null : (
                  <circle
                    key={item.id}
                    cx={xFor(point.x)}
                    cy={yFor(point.y)}
                    r="4.5"
                    fill={item.color}
                    stroke="var(--color-surface-layout-1)"
                    strokeWidth="2"
                  />
                )
              )}
            </g>
          )}

          {clusters.map((cluster) => {
            const first = cluster.markers[0]
            const lead = clusterLead(cluster)
            const active = cluster === hoverCluster
            return (
              // biome-ignore lint/a11y/noStaticElementInteractions: an SVG group cannot be a <button>; it takes role, tabIndex and keys.
              <g
                key={`glyph-${cluster.x}`}
                transform={`translate(${cluster.x}, ${TOP - 12})`}
                role={onMarkerClick ? 'button' : 'img'}
                tabIndex={onMarkerClick ? 0 : undefined}
                aria-label={cluster.markers
                  .map((marker) => marker.label)
                  .join('; ')}
                className={cn(
                  'outline-none',
                  onMarkerClick && 'cursor-pointer'
                )}
                onPointerEnter={() => setHoverCluster(cluster)}
                onPointerLeave={() => setHoverCluster(null)}
                onFocus={() => setHoverCluster(cluster)}
                onBlur={() => setHoverCluster(null)}
                onClick={() => onMarkerClick?.(lead)}
                onKeyDown={(event) => onMarkerKey(event, cluster)}
              >
                <circle r="11" fill="transparent" />
                <MarkerGlyph
                  kind={first.kind}
                  tone={
                    cluster.markers.length > 1
                      ? clusterTone(cluster)
                      : markerTone(first)
                  }
                  count={cluster.markers.length}
                  active={active}
                />
              </g>
            )
          })}

          {!hasData && (
            <text
              x={left + plotWidth / 2}
              y={TOP + plotHeight / 2}
              textAnchor="middle"
              className="fill-content-layout-3 text-[12px]"
            >
              {emptyLabel}
            </text>
          )}
        </svg>
      )}

      {hoverCluster && (
        <div
          className="pointer-events-none absolute z-10 w-64 rounded-panel border border-border-layout-1 bg-surface-overlay px-3 py-2 shadow-elevation-2 mk-enter"
          style={{
            left: Math.max(0, Math.min(hoverCluster.x - 128, width - 256)),
            top: TOP + 4,
          }}
        >
          <p className="text-label-small text-content-layout-2">
            {clusterTimeLabel(hoverCluster, spanSeconds, res)}
          </p>
          <ul className="mt-1 space-y-1">
            {hoverCluster.markers.slice(-CARD_ROWS).map((marker) => (
              <li
                key={`${marker.kind}-${marker.ts}-${marker.label}`}
                className="flex items-start gap-2 text-body-small text-content-layout-1"
              >
                <span
                  className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full"
                  style={{ background: TONE_COLOR[markerTone(marker)] }}
                />
                <span>{marker.label}</span>
              </li>
            ))}
          </ul>
          {hoverCluster.markers.length > CARD_ROWS && (
            <p className="mt-1 text-caption text-content-layout-2">
              {hoverCluster.markers.length - CARD_ROWS} earlier
            </p>
          )}
          {onMarkerClick && (
            <p className="mt-1 text-caption text-content-layout-2">
              {hoverCluster.markers.length > 1
                ? 'Click to open the most urgent'
                : 'Click to open'}
            </p>
          )}
        </div>
      )}

      {!hoverCluster &&
        hoverX != null &&
        !drag &&
        (hoverValues.length > 0 || hoverGap) && (
          <div
            className="pointer-events-none absolute z-10 w-56 rounded-panel border border-border-layout-1 bg-surface-overlay px-3 py-2 shadow-elevation-2"
            style={{ left: Math.max(0, cardLeft), top: TOP + 4 }}
          >
            <p className="text-label-small text-content-layout-2">
              {hoverLabel(hoverX, spanSeconds, res)}
            </p>
            {hoverGap ? (
              <p className="mt-1 text-body-small text-content-layout-1">
                {PAUSE_LABEL[pauseAt(pauses, hoverX)?.cause ?? 'unknown']}
              </p>
            ) : (
              <ul className="mt-1 space-y-0.5">
                {hoverValues.map(({ item, point }) => (
                  <li
                    key={item.id}
                    className="flex items-center gap-2 text-body-small"
                  >
                    <span
                      className="inline-block h-[3px] w-3.5 rounded-sm"
                      style={{ background: item.color }}
                    />
                    <span className="text-content-layout-2">{item.label}</span>
                    <span className="ml-auto font-semibold tabular-nums text-content-layout-1">
                      {point.y == null ? 'No reading' : fmt(point.y, item)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {hoverMarkers.length > 0 && (
              <ul className="mt-1.5 space-y-0.5 border-t border-border-layout-1 pt-1.5">
                {hoverMarkers.slice(0, 3).map((marker) => (
                  <li
                    key={`${marker.kind}-${marker.ts}-${marker.label}`}
                    className="truncate text-caption text-content-layout-2"
                  >
                    {marker.label}
                  </li>
                ))}
                {hoverMarkers.length > 3 && (
                  <li className="text-caption text-content-layout-3">
                    {hoverMarkers.length - 3} more
                  </li>
                )}
              </ul>
            )}
          </div>
        )}

      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            {series.map((item) => (
              <th key={item.id} scope="col">
                {item.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(shapes[0]?.item.points ?? [])
            .filter(
              (_, index, list) =>
                index % Math.max(1, Math.ceil(list.length / 24)) === 0
            )
            .map((point) => (
              <tr key={point.x}>
                <th scope="row">{hoverLabel(point.x, spanSeconds, res)}</th>
                {series.map((item) => {
                  const match = item.points.find(
                    (candidate) => candidate.x === point.x
                  )
                  return (
                    <td key={item.id}>
                      {match?.y == null ? 'No reading' : fmt(match.y, item)}
                    </td>
                  )
                })}
              </tr>
            ))}
        </tbody>
      </table>
      {indexMarkers.length > 0 && (
        <ul className="sr-only" aria-label="Index changes in this range">
          {indexMarkers.map((marker) => (
            <li key={`${marker.kind}-${marker.ts}-${marker.label}`}>
              {hoverLabel(marker.ts, spanSeconds, res)}: {marker.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
