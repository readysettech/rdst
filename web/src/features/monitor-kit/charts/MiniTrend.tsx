// MiniTrend - the tile / row / inbox sparkline (brief 4.1). Area + line, no axes,
// end dot, optional single marker, hover crosshair with a tiny value tooltip.
// Fewer than `minPoints` readings renders a dashed baseline and "Building history".
//
// Usage:
//   <MiniTrend
//     ariaLabel="Throughput trend"
//     color={SERIES_COLOR.throughput}
//     points={series.map(p => ({ x: p.ts, y: p.qps }))}
//     domain={[start, now]}                       // optional; defaults to data extent
//     formatValue={(v) => `${formatQps(v)} qps`}
//     marker={{ x: fix.detected_at, label: 'idx_orders_total added' }}
//     height={40}
//   />
import { cn } from '@rs/tailwind-base'
import {
  type PointerEvent as ReactPointerEvent,
  useId,
  useMemo,
  useState,
} from 'react'
import { useElementWidth, useEntrance, useHoverX } from './chartHooks'
import {
  type ChartPoint,
  downsample,
  inferResolution,
  segmentPoints,
  yDomain,
  yFraction,
} from './chartMath'
import { type ChartHighlight, HighlightBand } from './highlight'
import { hoverLabel } from './rangeModel'

export interface MiniTrendProps {
  points: ChartPoint[]
  color: string
  ariaLabel: string
  domain?: [number, number]
  /** Height in px (default 40). */
  height?: number
  resolution?: number
  /** 0 by default; 'auto' fits the data (ratios). */
  yMin?: number | 'auto'
  yCap?: number
  formatValue?: (value: number) => string
  marker?: { x: number; label: string; color?: string }
  /** A shaded window (an incident), behind the line. */
  highlight?: ChartHighlight | null
  /** Below this many readings the placeholder shows instead (default 3). */
  minPoints?: number
  /** Placeholder copy; default 'Building history - N of 3 readings'. */
  buildingLabel?: string
  /** Share the crosshair with other charts. */
  syncId?: string
  /** Disable the hover crosshair (for dense lists). */
  interactive?: boolean
  className?: string
}

export function MiniTrend({
  points: rawPoints,
  color,
  ariaLabel,
  domain,
  height = 40,
  resolution,
  yMin,
  yCap,
  formatValue = (value) => String(Math.round(value * 100) / 100),
  marker,
  highlight = null,
  minPoints = 3,
  buildingLabel,
  syncId,
  interactive = true,
  className,
}: MiniTrendProps) {
  const gradientId = useId().replaceAll(':', '')
  const [ref, width] = useElementWidth<HTMLDivElement>(160)
  // One point per ~4px keeps a day of minute readings from turning into a comb.
  const points = useMemo(
    () => downsample(rawPoints, Math.max(12, Math.floor((width || 160) / 4))),
    [rawPoints, width]
  )
  const [hoverX, setHoverX] = useHoverX(syncId)
  // Synced charts show the crosshair; only the chart under the pointer shows its value.
  const [own, setOwn] = useState(false)
  const readings = useMemo(
    () =>
      points.filter(
        (point): point is { x: number; y: number } =>
          point.y != null && Number.isFinite(point.y)
      ),
    [points]
  )
  const building = readings.length < minPoints
  const entering = useEntrance(!building && width > 0)
  const res = Math.max(resolution ?? 0, inferResolution(points))
  const [x0, x1] = useMemo<[number, number]>(() => {
    if (domain) return domain
    if (!points.length) return [0, 1]
    const xs = points.map((point) => point.x)
    return [Math.min(...xs), Math.max(...xs)]
  }, [domain, points])
  const span = Math.max(1, x1 - x0)
  const pad = 4
  const plotWidth = Math.max(1, width - 2 * pad)
  const top = 4
  const plotHeight = height - top - 2
  const y = useMemo(
    () =>
      yDomain(
        readings.map((point) => point.y),
        { min: yMin, cap: yCap }
      ),
    [readings, yMin, yCap]
  )
  const xFor = (x: number) => pad + ((x - x0) / span) * plotWidth
  const yFor = (value: number) =>
    top + plotHeight - yFraction(value, y) * plotHeight
  const { runs } = useMemo(() => segmentPoints(points, res), [points, res])
  const last = readings[readings.length - 1]

  if (building) {
    const label =
      buildingLabel ??
      `Building history - ${readings.length} of ${minPoints} readings`
    return (
      <div
        className={cn('relative flex w-full items-end', className)}
        style={{ height }}
      >
        <div className="absolute inset-x-1 bottom-1 border-t border-dashed border-border-layout-2" />
        <span className="relative mb-2 text-caption text-content-layout-3">
          {label}
        </span>
      </div>
    )
  }

  const hovered =
    hoverX == null
      ? null
      : readings.reduce<{ x: number; y: number } | null>(
          (best, point) =>
            !best || Math.abs(point.x - hoverX) < Math.abs(best.x - hoverX)
              ? point
              : best,
          null
        )
  const showHover = hovered && Math.abs(hovered.x - (hoverX ?? 0)) <= res * 1.5

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!interactive || !readings.length) return
    const rect = event.currentTarget.getBoundingClientRect()
    const target = x0 + ((event.clientX - rect.left - pad) / plotWidth) * span
    let best = readings[0]
    for (const point of readings)
      if (Math.abs(point.x - target) < Math.abs(best.x - target)) best = point
    setHoverX(best.x)
  }

  const tooltipLeft = showHover && hovered ? xFor(hovered.x) : 0
  // Beside the crosshair, on the side with more room, so the card never crosses the
  // chart's right edge (rows clip with overflow-hidden).
  const tooltipStyle =
    tooltipLeft > width / 2
      ? { right: Math.max(0, width - tooltipLeft + 8) }
      : { left: Math.max(0, tooltipLeft + 8) }

  return (
    <div
      ref={ref}
      className={cn('mk-chart relative w-full', className)}
      style={{ height }}
    >
      {width > 0 && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={
            last ? `${ariaLabel}, latest ${formatValue(last.y)}` : ariaLabel
          }
          className="block overflow-visible"
          onPointerMove={(event) => {
            setOwn(true)
            onPointerMove(event)
          }}
          onPointerLeave={() => {
            setOwn(false)
            setHoverX(null)
          }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.22" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <HighlightBand
            highlight={highlight}
            domain={[x0, x1]}
            xFor={xFor}
            top={0}
            height={height}
            bar={0}
          />
          {marker && marker.x >= x0 && marker.x <= x1 && (
            <line
              x1={xFor(marker.x)}
              x2={xFor(marker.x)}
              y1={0}
              y2={height}
              stroke={marker.color ?? 'var(--color-content-layout-3)'}
              strokeDasharray="2 3"
            >
              <title>{marker.label}</title>
            </line>
          )}
          {runs.map((run, index) => {
            if (run.length === 1)
              return (
                <circle
                  key={index}
                  cx={xFor(run[0].x)}
                  cy={yFor(run[0].y)}
                  r="1.5"
                  fill={color}
                />
              )
            const d = run
              .map(
                (point, i) =>
                  `${i ? 'L' : 'M'}${xFor(point.x).toFixed(1)} ${yFor(point.y).toFixed(1)}`
              )
              .join('')
            return (
              <g key={index}>
                <path
                  d={`${d}L${xFor(run[run.length - 1].x).toFixed(1)} ${height}L${xFor(run[0].x).toFixed(1)} ${height}Z`}
                  fill={`url(#${gradientId})`}
                  className={entering ? 'mk-fade' : undefined}
                />
                <path
                  d={d}
                  fill="none"
                  stroke={color}
                  strokeWidth="1.6"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  pathLength={entering ? 1 : undefined}
                  className={entering ? 'mk-draw' : undefined}
                />
              </g>
            )
          })}
          {last && (
            <>
              <circle
                key={`ring-${last.x}`}
                cx={xFor(last.x)}
                cy={yFor(last.y)}
                r="2.5"
                fill={color}
                className={entering ? undefined : 'mk-ring'}
              />
              <circle
                cx={xFor(last.x)}
                cy={yFor(last.y)}
                r="2.5"
                fill={color}
              />
            </>
          )}
          {showHover && hovered && (
            <g pointerEvents="none">
              <line
                x1={xFor(hovered.x)}
                x2={xFor(hovered.x)}
                y1={0}
                y2={height}
                stroke="var(--color-content-layout-2)"
                strokeDasharray="2 2"
              />
              <circle
                cx={xFor(hovered.x)}
                cy={yFor(hovered.y)}
                r="3.5"
                fill={color}
                stroke="var(--color-surface-layout-1)"
                strokeWidth="1.5"
              />
            </g>
          )}
        </svg>
      )}
      {own && showHover && hovered && (
        <div
          className="pointer-events-none absolute bottom-full z-10 mb-1 whitespace-nowrap rounded-control border border-border-layout-1 bg-surface-overlay px-2 py-1 text-caption text-content-layout-1 shadow-elevation-2"
          style={tooltipStyle}
        >
          <span className="text-content-layout-2">
            {hoverLabel(hovered.x, span, res)}
          </span>
          <span className="ml-2 font-semibold tabular-nums">
            {formatValue(hovered.y)}
          </span>
        </div>
      )}
    </div>
  )
}
