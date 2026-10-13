// The Home card's sparkline: queries per second since midnight, 56px tall, the
// line over a light fill, no y axis. Gaps in the readings are drawn as the
// kit's hatched pause bands and the line breaks across them.
import type { HomeSparkPoint } from '../../lib/monitor/types'
import { useElementWidth } from '../monitor-kit/charts/chartHooks'
import { PauseBands } from '../monitor-kit/charts/PauseBands'
import type { ChartPause } from '../monitor-kit/charts/pauseModel'
import { type AxisTick, ticksFor } from '../monitor-kit/charts/rangeModel'

const SERIES = 'var(--color-series-1)'
const PLOT_HEIGHT = 56
const AXIS_HEIGHT = 18
const DEFAULT_RES = 300
/** A span without readings longer than this many buckets is a pause. */
const GAP_BUCKETS = 3

export interface DayChartModel {
  domain: [number, number]
  /** Readings cover less than half the day: the axis spans the recorded window. */
  short: boolean
  /** The first reading, for "since H:MM AM". */
  since: number | null
  res: number
  /** Readings in consecutive runs; the line breaks between runs. */
  runs: HomeSparkPoint[][]
  pauses: ChartPause[]
  ticks: AxisTick[]
  /** Fewer than two readings: nothing to draw a line through yet. */
  collecting: boolean
}

export function dayChartModel(
  series: HomeSparkPoint[],
  now: number,
  midnight: number
): DayChartModel {
  const points = series
    .filter((point) => point.qps != null)
    .sort((a, b) => a.ts - b.ts)
  let res = DEFAULT_RES
  for (let i = 1; i < series.length; i++) {
    const step = series[i].ts - series[i - 1].ts
    if (step > 0 && (i === 1 || step < res)) res = step
  }
  const first = points[0]?.ts ?? null
  const short = first != null && now - first < (now - midnight) / 2
  const domain: [number, number] = [short ? first : midnight, now]
  const gap = res * GAP_BUCKETS

  const runs: HomeSparkPoint[][] = []
  const pauses: ChartPause[] = []
  for (const point of points) {
    const run = runs[runs.length - 1]
    const last = run?.[run.length - 1]
    if (last && point.ts - last.ts > gap) {
      pauses.push({
        from: last.ts + res,
        to: point.ts,
        cause: 'unknown',
        ongoing: false,
      })
      runs.push([point])
    } else if (run) run.push(point)
    else runs.push([point])
  }
  if (!short && first != null && first - midnight > gap)
    pauses.unshift({
      from: midnight,
      to: first,
      cause: 'unknown',
      ongoing: false,
    })
  const last = points[points.length - 1]?.ts
  if (last != null && now - last > gap)
    pauses.push({ from: last + res, to: now, cause: 'unknown', ongoing: true })

  return {
    domain,
    short,
    since: first,
    res,
    runs,
    pauses,
    ticks: ticksFor(domain, short ? 3 : 4),
    collecting: points.length < 2,
  }
}

export function DayChart({
  model,
  ariaLabel,
}: {
  model: DayChartModel
  ariaLabel: string
}) {
  const [ref, measured] = useElementWidth<HTMLDivElement>()
  const width = measured || 640
  const [x0, x1] = model.domain
  const span = Math.max(1, x1 - x0)
  const xFor = (x: number) => ((x - x0) / span) * width
  let max = 0
  for (const run of model.runs)
    for (const point of run)
      if (point.qps != null && point.qps > max) max = point.qps
  const yFor = (value: number) =>
    PLOT_HEIGHT - 1 - (max > 0 ? value / max : 0) * (PLOT_HEIGHT - 2)
  const lines = model.runs
    .filter((run) => run.length > 1)
    .map((run) =>
      run
        .map(
          (point, i) =>
            `${i ? 'L' : 'M'}${xFor(point.ts).toFixed(1)} ${yFor(point.qps as number).toFixed(1)}`
        )
        .join('')
    )
  const lastRun = model.runs[model.runs.length - 1]
  const end = lastRun?.[lastRun.length - 1]
  return (
    <div ref={ref} className="mk-chart w-full">
      <svg
        width={width}
        height={PLOT_HEIGHT + AXIS_HEIGHT}
        viewBox={`0 0 ${width} ${PLOT_HEIGHT + AXIS_HEIGHT}`}
        role="img"
        aria-label={ariaLabel}
        className="block overflow-visible"
        data-testid="home-day-chart"
        data-window={model.short ? 'short' : 'day'}
        data-collecting={model.collecting || undefined}
      >
        <PauseBands
          pauses={model.pauses}
          xFor={xFor}
          top={0}
          height={PLOT_HEIGHT}
        />
        {model.collecting ? (
          <line
            x1={0}
            x2={width}
            y1={PLOT_HEIGHT - 1}
            y2={PLOT_HEIGHT - 1}
            stroke="var(--color-border-layout-2)"
            strokeDasharray="2 3"
            data-testid="home-day-chart-collecting"
          />
        ) : (
          lines.map((d, i) => {
            const run = model.runs.filter((item) => item.length > 1)[i]
            const left = xFor(run[0].ts).toFixed(1)
            const right = xFor(run[run.length - 1].ts).toFixed(1)
            return (
              <g key={run[0].ts} data-testid="home-day-chart-run">
                <path
                  d={`${d}L${right} ${PLOT_HEIGHT}L${left} ${PLOT_HEIGHT}Z`}
                  fill={SERIES}
                  fillOpacity="0.1"
                />
                <path
                  d={d}
                  fill="none"
                  stroke={SERIES}
                  strokeWidth="1.5"
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            )
          })
        )}
        {!model.collecting && end && (
          <circle
            cx={xFor(end.ts)}
            cy={yFor(end.qps as number)}
            r={2.5}
            fill={SERIES}
          />
        )}
        {model.ticks.map((tick) => (
          <text
            key={tick.x}
            x={Math.min(width - 2, Math.max(2, xFor(tick.x)))}
            y={PLOT_HEIGHT + AXIS_HEIGHT - 4}
            textAnchor={
              xFor(tick.x) < 24
                ? 'start'
                : xFor(tick.x) > width - 24
                  ? 'end'
                  : 'middle'
            }
            className="fill-content-layout-3 text-label-extra-small"
          >
            {tick.label}
          </text>
        ))}
      </svg>
    </div>
  )
}
