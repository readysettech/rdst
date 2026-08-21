import type { PointerEvent as ReactPointerEvent } from 'react'
import { Fragment, useId, useMemo, useState } from 'react'

export interface ComparisonChartPoint {
  x: number
  y: number
}

export interface ComparisonChartSeries {
  id: string
  label: string
  color: string
  points: ComparisonChartPoint[]
}

export interface ComparisonChartAnnotation {
  x: number
  label: string
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function defaultNumber(value: number) {
  return value >= 1000
    ? `${Math.round(value / 1000)}k`
    : String(Math.round(value))
}

export function comparisonChartMax(value: number) {
  if (value <= 1) return 1
  const power = 10 ** Math.floor(Math.log10(value))
  const scaled = value / power
  const nice = scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10
  return nice * power
}

export function ComparisonLineChart({
  series,
  annotations = [],
  ariaLabel,
  emptyLabel = 'Waiting for measurements',
  xDomain,
  yMax,
  yScale = 'linear',
  xStartLabel,
  xEndLabel,
  formatAxisValue = defaultNumber,
  formatValue = defaultNumber,
  formatHoverX = (value) => String(value),
}: {
  series: ComparisonChartSeries[]
  annotations?: ComparisonChartAnnotation[]
  ariaLabel: string
  emptyLabel?: string
  xDomain?: [number, number]
  yMax?: number
  /**
   * A logarithmic y-axis keeps a lane two orders of magnitude below the other
   * readable instead of pinning it to the baseline. Its domain starts at 1,
   * so callers must say so on the axis.
   */
  yScale?: 'linear' | 'log'
  xStartLabel: string
  xEndLabel: string
  formatAxisValue?: (value: number) => string
  formatValue?: (value: number) => string
  formatHoverX?: (value: number) => string
}) {
  const chartId = useId().replaceAll(':', '')
  const [hoverX, setHoverX] = useState<number | null>(null)
  const width = 920
  const height = 250
  const margin = { left: 42, right: 92, top: 16, bottom: 40 }
  const plotWidth = width - margin.left - margin.right
  const plotHeight = height - margin.top - margin.bottom
  const allPoints = useMemo(
    () => series.flatMap((item) => item.points),
    [series]
  )
  const firstX = allPoints.length
    ? Math.min(...allPoints.map((point) => point.x))
    : 0
  const lastX = allPoints.length
    ? Math.max(...allPoints.map((point) => point.x))
    : 1
  const [domainStart, domainEnd] = xDomain ?? [firstX, lastX]
  const span = Math.max(1, domainEnd - domainStart)
  const maxValue =
    yMax ??
    comparisonChartMax(Math.max(1, ...allPoints.map((point) => point.y)))
  const xFor = (value: number) =>
    margin.left + ((value - domainStart) / span) * plotWidth
  // Decades between the axis floor of 1 and the top of the domain; at least
  // one, so a chart whose values never reach 10 still has a drawable span.
  const logSpan = Math.max(1, Math.log10(maxValue))
  const fractionOf = (value: number) =>
    yScale === 'log'
      ? clamp(Math.log10(Math.max(value, 1)) / logSpan, 0, 1)
      : clamp(value / maxValue, 0, 1)
  const axisValueAt = (fraction: number) =>
    yScale === 'log' ? 10 ** (fraction * logSpan) : fraction * maxValue
  const yFor = (value: number) =>
    margin.top + plotHeight - fractionOf(value) * plotHeight
  const points = (item: ComparisonChartSeries) =>
    item.points.map((point) => `${xFor(point.x)},${yFor(point.y)}`).join(' ')
  const anchorPoints =
    [...series].sort((a, b) => b.points.length - a.points.length)[0]?.points ??
    []
  const hoverValues =
    hoverX == null
      ? []
      : series.flatMap((item) => {
          const point = item.points.reduce<ComparisonChartPoint | null>(
            (nearest, candidate) =>
              !nearest ||
              Math.abs(candidate.x - hoverX) < Math.abs(nearest.x - hoverX)
                ? candidate
                : nearest,
            null
          )
          return point ? [{ ...item, point }] : []
        })
  const hoverAnnotation =
    hoverX == null
      ? null
      : annotations.reduce<ComparisonChartAnnotation | null>(
          (nearest, annotation) =>
            !nearest ||
            Math.abs(annotation.x - hoverX) < Math.abs(nearest.x - hoverX)
              ? annotation
              : nearest,
          null
        )
  const annotationThreshold = (span / plotWidth) * 7
  const visibleHoverAnnotation =
    hoverAnnotation &&
    hoverX != null &&
    Math.abs(hoverAnnotation.x - hoverX) <= annotationThreshold
      ? hoverAnnotation
      : null
  const tooltipHeight =
    36 + hoverValues.length * 20 + (visibleHoverAnnotation ? 22 : 0)

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!anchorPoints.length) return
    const rect = event.currentTarget.getBoundingClientRect()
    const svgX = ((event.clientX - rect.left) / (rect.width || width)) * width
    const nearest = anchorPoints.reduce<ComparisonChartPoint | null>(
      (best, point) =>
        !best || Math.abs(xFor(point.x) - svgX) < Math.abs(xFor(best.x) - svgX)
          ? point
          : best,
      null
    )
    setHoverX(nearest?.x ?? null)
  }

  const endLabels = series.flatMap((item) => {
    const point = item.points[item.points.length - 1]
    return point ? [{ ...item, point, y: yFor(point.y) }] : []
  })
  if (
    endLabels.length === 2 &&
    Math.abs(endLabels[0].y - endLabels[1].y) < 14
  ) {
    const higher =
      endLabels[0].point.y >= endLabels[1].point.y ? endLabels[0] : endLabels[1]
    const lower = higher === endLabels[0] ? endLabels[1] : endLabels[0]
    higher.y = clamp(higher.y - 7, margin.top + 6, margin.top + plotHeight - 20)
    lower.y = clamp(lower.y + 7, margin.top + 20, margin.top + plotHeight - 6)
  }

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={ariaLabel}
        className="min-w-[720px]"
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHoverX(null)}
      >
        <defs>
          {series.map((item) => (
            <linearGradient
              key={item.id}
              id={`${chartId}-${item.id}-area`}
              x1="0"
              y1="0"
              x2="0"
              y2="1"
            >
              <stop offset="0%" stopColor={item.color} stopOpacity="0.16" />
              <stop offset="100%" stopColor={item.color} stopOpacity="0" />
            </linearGradient>
          ))}
        </defs>

        {[0, 0.5, 1].map((fraction) => {
          const y = margin.top + plotHeight - fraction * plotHeight
          const value = axisValueAt(fraction)
          return (
            <Fragment key={fraction}>
              <line
                x1={margin.left}
                y1={y}
                x2={width - margin.right}
                y2={y}
                stroke="var(--color-border-layout-1)"
                strokeWidth="1"
              />
              <text
                x={margin.left - 8}
                y={y + 4}
                textAnchor="end"
                className="fill-content-layout-3 text-[11px]"
              >
                {formatAxisValue(value)}
              </text>
            </Fragment>
          )
        })}

        <text
          x={margin.left}
          y={height - 16}
          className="fill-content-layout-3 text-[11px]"
        >
          {xStartLabel}
        </text>
        <text
          x={width - margin.right}
          y={height - 16}
          textAnchor="end"
          className="fill-content-layout-3 text-[11px]"
        >
          {xEndLabel}
        </text>

        {annotations.map((annotation) => (
          <line
            key={`${annotation.x}-${annotation.label}`}
            x1={xFor(annotation.x)}
            y1={margin.top}
            x2={xFor(annotation.x)}
            y2={margin.top + plotHeight}
            stroke="var(--color-border-layout-1)"
            strokeDasharray="3 4"
          >
            <title>{annotation.label}</title>
          </line>
        ))}

        {series.map((item) => {
          const first = item.points[0]
          const last = item.points[item.points.length - 1]
          return (
            <Fragment key={item.id}>
              {first && last && (
                <polygon
                  points={`${xFor(first.x)},${margin.top + plotHeight} ${points(
                    item
                  )} ${xFor(last.x)},${margin.top + plotHeight}`}
                  fill={`url(#${chartId}-${item.id}-area)`}
                />
              )}
              <polyline
                points={points(item)}
                fill="none"
                stroke={item.color}
                strokeWidth={item.id === 'readyset' ? 2.6 : 2.2}
                strokeLinejoin="round"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
            </Fragment>
          )
        })}

        {endLabels.map((item) => (
          <Fragment key={item.id}>
            <circle
              cx={xFor(item.point.x)}
              cy={yFor(item.point.y)}
              r="3.5"
              fill={item.color}
            />
            <text
              x={width - margin.right + 8}
              y={item.y + 4}
              className="text-[11px] font-semibold"
              style={{ fill: item.color }}
            >
              {formatValue(item.point.y)}
            </text>
          </Fragment>
        ))}

        {hoverX != null && hoverValues.length > 0 && (
          <g pointerEvents="none">
            <line
              x1={xFor(hoverX)}
              y1={margin.top}
              x2={xFor(hoverX)}
              y2={margin.top + plotHeight}
              stroke="var(--color-content-layout-2)"
              strokeDasharray="2 3"
            />
            {hoverValues.map((item) => (
              <circle
                key={item.id}
                cx={xFor(item.point.x)}
                cy={yFor(item.point.y)}
                r="4"
                fill={item.color}
              />
            ))}
            <g
              transform={`translate(${Math.min(
                xFor(hoverX) + 10,
                width - 244
              )}, ${margin.top + 8})`}
            >
              <rect
                width="234"
                height={tooltipHeight}
                rx="8"
                fill="var(--color-surface-layout-1)"
                stroke="var(--color-border-layout-1)"
              />
              <text
                x="10"
                y="20"
                className="fill-content-layout-1 text-[11px] font-semibold"
              >
                {formatHoverX(hoverX)}
              </text>
              {hoverValues.map((item, index) => (
                <text
                  key={item.id}
                  x="10"
                  y={42 + index * 20}
                  className="text-[11px]"
                  style={{ fill: item.color }}
                >
                  {item.label}: {formatValue(item.point.y)}
                </text>
              ))}
              {visibleHoverAnnotation && (
                <text
                  x="10"
                  y={42 + hoverValues.length * 20}
                  className="fill-content-layout-1 text-[11px] font-semibold"
                >
                  {visibleHoverAnnotation.label}
                </text>
              )}
            </g>
          </g>
        )}

        {!allPoints.length && (
          <text
            x={width / 2}
            y={height / 2}
            textAnchor="middle"
            className="fill-content-layout-3 text-[12px]"
          >
            {emptyLabel}
          </text>
        )}
      </svg>
    </div>
  )
}
