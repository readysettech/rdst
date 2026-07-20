// Demo page component — moved out of the route config into this route-ignored
// sibling (TanStack skips `-`-prefixed files) so the code-splitter can relocate
// its `../components/SQLDisplay` (CodeMirror SQL-editor stack) import out of the
// eager entry chunk. The route file imports `DemoPage` only for its `component:`
// wrapper; the tests import `DemoPage` and the pure helpers from here. See
// evidence/gates-final.md §Defect D-1.
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { toast } from '@rs/ui-new/use-toast'
import { useNavigate } from '@tanstack/react-router'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { buildParameterHighlights } from '../components/parameterHighlighting'
import { SQLDisplay } from '../components/SQLDisplay'
import {
  type ContainerProgress,
  type DiscoveryMode,
  getPreflight,
  type LoadEvent,
  type LoadSample,
  type PatternReason,
  type PatternRow,
  type PatternStatus,
  type Phase,
  type PreflightChecks,
  useDemo,
} from '../lib/useDemo'

const DEMO_STYLE = `
/* All series/status colors consume design-system tokens (§4.5 content-viz-*
   + the semantic *-soft families) instead of the private raw hex the brief
   forbids; the token cascade (:root / [data-color-mode]) drives light/dark, so
   no per-scheme override block is needed. [demo NEEDS; VIS-001, VIS-011] */
.qpdemo {
  --qpdemo-router: var(--content-viz-cache);
  --qpdemo-control: var(--content-viz-origin);
  --qpdemo-grid: var(--border-layout-1);
  --qpdemo-chip: var(--surface-raised);
  --qpdemo-good: var(--content-positive-soft);
  --qpdemo-good-bg: var(--surface-positive-soft);
  --qpdemo-warn: var(--content-warning-soft);
  --qpdemo-warn-bg: var(--surface-warning-soft);
  --qpdemo-deny: var(--content-negative-soft);
  --qpdemo-deny-bg: var(--surface-negative-soft);
  --qpdemo-you: var(--content-rising-soft);
  --qpdemo-you-bg: var(--surface-rising-soft);
}
.qpdemo svg text {
  font: 11.5px var(--font-sans), system-ui, sans-serif;
  fill: var(--color-content-layout-3);
}
.qpdemo svg .endlabel {
  font-weight: 600;
}
.qpdemo-heartbeat {
  animation: qpdemo-pulse 1.2s ease-in-out infinite;
}
@keyframes qpdemo-pulse {
  0%, 100% { transform: scale(0.82); opacity: 0.55; }
  45% { transform: scale(1.18); opacity: 1; }
}
@keyframes qpdemo-enter {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}
.qpdemo-enter {
  animation: qpdemo-enter 300ms ease-out both;
}
@media (prefers-reduced-motion: reduce) {
  .qpdemo-enter { animation: none; }
}
`

const MODE_LABEL: Record<DiscoveryMode, string> = {
  count_star: 'Most frequent',
  sum_time: 'Most expensive',
}

const STATUS_LABEL: Record<PatternStatus, string> = {
  cached_querypilot: 'cached · QueryPilot',
  cached_manual: 'cached · manually',
  pass_through: 'pass-through',
  not_eligible: 'not eligible yet',
  denylisted: 'denylisted',
  unsupported: 'unsupported',
}

const MODE_SWITCH_CONFIRM =
  'This resets all counters and starts the comparison over. QueryPilot drops every cache and re-selects under the new policy.'
const MODE_RESET_COPY =
  'Counters resetting. Throughput dips while caches rebuild, then climbs as QueryPilot re-selects.'
const HEARTBEAT_TOOLTIP =
  'QueryPilot watches traffic and caches by the selected policy, re-evaluating every 15 seconds.'
const CHART_INFO_TOOLTIP =
  'Throughput on each path, side by side. This is a comparison, not an offload — the gap between the lines is how much faster queries run once Readyset caches them.'
const LEGEND_POSTGRES_TOOLTIP =
  'The same queries run directly against Postgres, uncached — the baseline.'
const LEGEND_READYSET_TOOLTIP =
  'Queries through Readyset: cached ones served from cache, everything else passed through untouched.'
const MANUAL_CACHE_DISABLED_TOOLTIP =
  "QueryPilot manages caching while it's on. Turn it off to cache by hand."
// A checked switch means "actively caching" everywhere in the demo; green
// makes that state readable at a glance.
const SWITCH_CHECKED_GREEN = 'data-[state=checked]:bg-surface-positive-solid'
const TOUR_STORAGE_KEY = 'qpdemo_walkthrough_done'

type SnapshotSortKey =
  | 'postgres_hits'
  | 'readyset_hits'
  | 'direct_avg_ms'
  | 'router_avg_ms'
  | 'status'
type WindowMode = '5m' | '1m'

// The one true row order: stable query identity. Every other order is a
// user-taken snapshot layered on top of it.
function rowIdentity(a: PatternRow, b: PatternRow) {
  return a.key.localeCompare(b.key) || a.title.localeCompare(b.title)
}
type TourStepId =
  | 'welcome'
  | 'traffic'
  | 'chart'
  | 'table'
  | 'manual'
  | 'manualResult'
  | 'querypilot'
  | 'qpWaiting'
  | 'autoCachedChart'
  | 'autoCached'
  | 'mode'
  | 'reselecting'
  | 'modeWhyNot'
  | 'finale'

function panel(extra = '') {
  return `rounded-lg border border-border-layout-1 bg-surface-layout-1 ${extra}`
}

function formatQps(value: number) {
  if (!Number.isFinite(value)) return '0'
  if (Math.abs(value) >= 1000) {
    const v = value / 1000
    return `${v >= 10 ? v.toFixed(0) : v.toFixed(1).replace(/\.0$/, '')}k`
  }
  return String(Math.round(value))
}

function formatAxis(value: number) {
  return value >= 1000
    ? `${Math.round(value / 1000)}k`
    : String(Math.round(value))
}

// Left x-axis label reflecting how much history is on screen: seconds while the
// series is still filling, minutes once it spans a full window.
function formatSpanLabel(seconds: number) {
  if (seconds >= 60) return `-${Math.round(seconds / 60)} min`
  return `-${Math.round(seconds)}s`
}

function formatMs(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value >= 10 ? value.toFixed(1) : value.toFixed(2).replace(/0$/, '')} ms`
}

function formatWholeMs(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${Math.round(value).toLocaleString()} ms`
}

function niceMax(value: number) {
  if (value <= 100) return 100
  const power = 10 ** Math.floor(Math.log10(value))
  const scaled = value / power
  const nice = scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10
  return nice * power
}

// Turn a workload query with literal values into the parameterized shape that
// QueryPilot actually caches: every string literal and comparison/value number
// becomes a positional placeholder, while ordinals like GROUP BY 1 stay intact.
export function parameterizeSql(sql: string): {
  text: string
  placeholders: string[]
} {
  const placeholders: string[] = []
  const next = () => {
    const token = `$${placeholders.length + 1}`
    placeholders.push(token)
    return token
  }
  // One left-to-right pass so placeholders are numbered by position: match a
  // string literal, or a value-position number (after = < > ( or ,).
  const text = sql.replace(
    /('(?:[^']|'')*')|([=<>(,]\s*)(-?\d+(?:\.\d+)?)/g,
    (_m, str: string | undefined, prefix: string | undefined) =>
      str != null ? next() : `${prefix}${next()}`
  )
  return { text, placeholders }
}

function metricIsTime(
  metric?: PatternReason['metric'] | PatternRow['alt_metric']
) {
  return metric === 'sum_time' || metric === 'sum_time_us'
}

function metricValueText(reason: PatternReason, row: PatternRow) {
  const value = reason.metric_value ?? row.hits
  if (metricIsTime(reason.metric)) return formatWholeMs(value / 1000)
  return `${value.toLocaleString()} hits`
}

function scoreFormula(reason: PatternReason, row: PatternRow) {
  if (!metricIsTime(reason.metric))
    return `${(reason.metric_value ?? row.hits).toLocaleString()} hits`
  return `${metricValueText(reason, row)} total time`
}

function latestSample(samples: LoadSample[]) {
  return samples.length ? samples[samples.length - 1] : null
}

function modeFromMetric(
  metric: PatternRow['alt_metric'] | undefined | null,
  fallback: DiscoveryMode
) {
  if (metric === 'count_star') return 'count_star'
  if (metric === 'sum_time' || metric === 'sum_time_us') return 'sum_time'
  return fallback
}

function statusClass(status: PatternStatus) {
  if (status === 'cached_querypilot')
    return 'bg-[var(--qpdemo-good-bg)] text-[var(--qpdemo-good)]'
  if (status === 'cached_manual')
    return 'bg-[var(--qpdemo-you-bg)] text-[var(--qpdemo-you)]'
  if (status === 'not_eligible')
    return 'bg-[var(--qpdemo-warn-bg)] text-[var(--qpdemo-warn)]'
  if (status === 'denylisted' || status === 'unsupported')
    return 'bg-[var(--qpdemo-deny-bg)] text-[var(--qpdemo-deny)]'
  return 'bg-[var(--qpdemo-chip)] text-content-layout-2'
}

function canManualCache(row: PatternRow) {
  return row.status === 'pass_through' || row.status === 'not_eligible'
}

// The manual "easy wins" are two fixed heavy queries (adjacent in the key
// sort, so the tour highlight is one clean two-row block). H01/H02 carry the
// slowest Postgres averages in the workload while their small result sets
// serve from cache in single-digit milliseconds, so caching them by hand
// shows the starkest possible Readyset-average drop (~500ms -> ~1-7ms).
// Fixed keys, not runtime latency, so the pick is stable and present the
// instant load starts (latency stats are still empty then).
const MANUAL_SUGGESTED_KEYS = ['H01', 'H02']

// Rows the why-not tour step can safely spotlight: the below-threshold probes
// rank at the bottom under BOTH policies (single-weight, sub-ms lookups), so
// they are guaranteed to still be uncached whenever the step is reached.
const TOUR_UNCACHED_PICKS = ['D03', 'D04']

// A QueryPilot pass runs every 15s; a cached set untouched for two passes
// (plus margin) means the re-selection is done even if the budget never
// quite fills - QueryPilot occasionally leaves its last pick or two unmade.
const RESELECT_SETTLE_MS = 35_000

function suggestedMidTier(patterns: PatternRow[], _budget = 10) {
  return MANUAL_SUGGESTED_KEYS.map((k) =>
    patterns.find((p) => p.key === k)
  ).filter((p): p is PatternRow => Boolean(p))
}

// Specific, truthful description of a recorded chart event, surfaced only when
// the crosshair sits on the event's own marker. The query name is pulled from
// the event's recorded label so caches name the query they acted on.
export function eventDescription(event: LoadEvent): string {
  switch (event.type) {
    case 'manual_cache': {
      const title = event.label.replace(/^you cached\s+/i, '').trim()
      return title ? `Cached '${title}' manually` : 'Cached a query manually'
    }
    case 'manual_uncache': {
      const title = event.label.replace(/^you uncached\s+/i, '').trim()
      return title ? `Removed cache on '${title}'` : 'Removed a cache'
    }
    case 'qp_on':
      return 'QueryPilot turned on'
    case 'qp_off':
      return 'QueryPilot turned off'
    case 'mode_change':
      return event.label.toLowerCase().includes('expensive')
        ? 'Policy switched to Most expensive'
        : 'Policy switched to Most frequent'
    default:
      return event.label
  }
}

// Mean router/direct throughput ratio over the most recent `seconds`, rounded
// to a whole number for the final tour beat's live "NNx" callout.
export function windowLiftRatio(
  samples: LoadSample[],
  seconds: number
): number {
  if (!samples.length) return 0
  const end = samples[samples.length - 1].t
  const recent = samples.filter((s) => s.t >= end - seconds)
  const pts = recent.length ? recent : samples.slice(-1)
  const direct = pts.reduce((sum, s) => sum + s.direct.qps, 0) / pts.length
  const router = pts.reduce((sum, s) => sum + s.router.qps, 0) / pts.length
  return direct > 0 ? router / direct : 0
}

function LegendSwatch({
  color,
  label,
  tooltip,
}: {
  color: string
  label: string
  tooltip: string
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex cursor-default items-center gap-1.5 text-content-layout-2">
          <span
            className="inline-block h-[3px] w-3.5 rounded-sm"
            style={{ background: color }}
          />
          {label}
        </span>
      </TooltipTrigger>
      <TooltipContent label={tooltip} />
    </Tooltip>
  )
}

function ThroughputChart({
  samples,
  events,
  windowMode,
  bare = false,
}: {
  samples: LoadSample[]
  events: LoadEvent[]
  windowMode: WindowMode
  // When docked inside the hero surface the chart drops its own border/bg so
  // the hero provides a single elevated container. [VIS-105, VIS-127]
  bare?: boolean
}) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const W = 920
  const H = 250
  // Wider right gutter reserves room for the end-of-line value labels that keep
  // both series legible at high lift (the baseline would otherwise flatten onto
  // the x-axis and vanish). [T16, audit LOW #9, VIS-011]
  const m = { left: 42, right: 70, top: 16, bottom: 40 }
  const plotW = W - m.left - m.right
  const plotH = H - m.top - m.bottom
  const windowSeconds = windowMode === '1m' ? 60 : 300
  const allPts = useMemo(() => samples.slice(-600), [samples])
  const latest = latestSample(allPts)
  const newestEvent = events.reduce((max, event) => Math.max(max, event.t), 0)
  const now = Math.max(latest?.t ?? 0, newestEvent || 0) || Date.now() / 1000
  const firstT = allPts.length ? allPts[0].t : now - windowSeconds
  // The series starts at the left edge and grows rightward until a full window
  // of history exists, then scrolls as a rolling window ending at now. This
  // removes the empty leading gap that pinned traffic to the right edge.
  const start = Math.min(now - 1, Math.max(firstT, now - windowSeconds))
  const end = now
  const span = Math.max(1, end - start)
  const pts = useMemo(
    () => allPts.filter((p) => p.t >= start - 1),
    [allPts, start]
  )
  const maxY = niceMax(
    Math.max(100, ...pts.flatMap((p) => [p.direct.qps, p.router.qps]))
  )
  const xFor = (t: number) => m.left + ((t - start) / span) * plotW
  const yFor = (qps: number) => m.top + plotH - (qps / maxY) * plotH
  const points = (sel: (p: LoadSample) => number) =>
    pts.map((p) => `${xFor(p.t)},${yFor(sel(p))}`).join(' ')
  const hover = hoverIndex == null ? null : pts[hoverIndex]
  const visibleEvents = events
    .filter((e) => e.t >= start && e.t <= end + 2)
    .slice(-8)

  const nearEvent = hover
    ? visibleEvents.reduce<{ event: LoadEvent; d: number } | null>(
        (best, event) => {
          const d = Math.abs(xFor(event.t) - xFor(hover.t))
          if (!best || d < best.d) return { event, d }
          return best
        },
        null
      )
    : null
  // Event text appears only when the crosshair sits within a few px of the
  // marker; ordinary points show throughput values with no event text.
  const hoverEvent = nearEvent && nearEvent.d <= 7 ? nearEvent.event : null
  const tipHeight = hoverEvent ? 96 : 74

  function onPointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    if (!pts.length) return
    const rect = e.currentTarget.getBoundingClientRect()
    const width = rect.width || W
    const svgX = ((e.clientX - rect.left) / width) * W
    let best = 0
    let bestDistance = Number.POSITIVE_INFINITY
    pts.forEach((p, i) => {
      const distance = Math.abs(xFor(p.t) - svgX)
      if (distance < bestDistance) {
        best = i
        bestDistance = distance
      }
    })
    setHoverIndex(best)
  }

  return (
    <div
      data-tour-anchor="chart"
      className={bare ? 'p-4 pb-2' : panel('p-4 pb-2')}
    >
      <TooltipProvider delayDuration={150}>
        <div className="mb-1 flex flex-wrap items-baseline gap-x-5 gap-y-2">
          <div className="flex items-center gap-1.5">
            <Text as="h2" level="subtitle-2" className="text-content-layout-1">
              Total throughput
            </Text>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label="About this chart"
                  className="inline-flex h-4 w-4 items-center justify-center text-content-layout-3 hover:text-content-layout-1"
                >
                  <Icon name="info" label="" className="h-4 w-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent label={CHART_INFO_TOOLTIP} />
            </Tooltip>
          </div>
          <div className="flex flex-wrap gap-4 text-label-small">
            <LegendSwatch
              color="var(--qpdemo-router)"
              label="Readyset path"
              tooltip={LEGEND_READYSET_TOOLTIP}
            />
            <LegendSwatch
              color="var(--qpdemo-control)"
              label="Postgres direct"
              tooltip={LEGEND_POSTGRES_TOOLTIP}
            />
          </div>
        </div>
      </TooltipProvider>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label={`Total throughput chart with ${pts.length} history samples`}
          className="min-w-[720px]"
          onPointerMove={onPointerMove}
          onPointerLeave={() => setHoverIndex(null)}
        >
          {[0, 0.5, 1].map((g) => {
            const y = m.top + plotH - g * plotH
            const value = g * maxY
            return (
              <Fragment key={g}>
                <line
                  x1={m.left}
                  y1={y}
                  x2={W - m.right}
                  y2={y}
                  stroke="var(--qpdemo-grid)"
                  strokeWidth="1"
                />
                <text x={m.left - 8} y={y + 4} textAnchor="end">
                  {formatAxis(value)}
                </text>
              </Fragment>
            )
          })}
          <text x={m.left} y={H - 16}>
            {formatSpanLabel(span)}
          </text>
          <text x={W - m.right} y={H - 16} textAnchor="end">
            now
          </text>

          {visibleEvents.map((event, i) => {
            const x = xFor(event.t)
            return (
              <line
                key={`${event.t}-${event.type}-${i}`}
                x1={x}
                y1={m.top}
                x2={x}
                y2={m.top + plotH}
                stroke="var(--color-border-layout-1)"
                strokeDasharray="3 4"
              >
                <title>{eventDescription(event)}</title>
              </line>
            )
          })}

          {pts.length > 0 && (
            <>
              <polyline
                points={points((p) => p.direct.qps)}
                fill="none"
                stroke="var(--qpdemo-control)"
                strokeWidth="2.2"
                strokeLinejoin="round"
              />
              <polyline
                points={points((p) => p.router.qps)}
                fill="none"
                stroke="var(--qpdemo-router)"
                strokeWidth="2.4"
                strokeLinejoin="round"
              />
            </>
          )}
          {latest &&
            (() => {
              // Keep BOTH series legible even when the cached line rockets to
              // ~3k qps and the ~16 qps baseline would otherwise collapse onto the
              // x-axis. The polylines stay exact; the endpoint dot for the baseline
              // is nudged just off the axis, and each series gets an end-of-line
              // value label in the right gutter. The gap between the lines is the
              // whole story, so the baseline must never vanish. [T16, LOW #9, VIS-011]
              const axisY = m.top + plotH
              const labelX = W - m.right + 8
              const routerVal = latest.router.qps
              const directVal = latest.direct.qps
              const routerDotY = clamp(yFor(routerVal), m.top + 2, axisY)
              const directDotY = Math.min(yFor(directVal), axisY - 4)
              const MIN_GAP = 14
              let ry = clamp(yFor(routerVal), m.top + 6, axisY - 6)
              let dy = clamp(yFor(directVal), m.top + 6, axisY - 6)
              if (Math.abs(ry - dy) < MIN_GAP) {
                if (routerVal >= directVal) {
                  dy = Math.min(axisY - 6, ry + MIN_GAP)
                  ry = dy - MIN_GAP
                } else {
                  ry = Math.min(axisY - 6, dy + MIN_GAP)
                  dy = ry - MIN_GAP
                }
              }
              const lifted = routerVal >= directVal * 1.5
              return (
                <>
                  <circle
                    cx={xFor(latest.t)}
                    cy={routerDotY}
                    r="3.5"
                    fill="var(--qpdemo-router)"
                  />
                  <circle
                    cx={xFor(latest.t)}
                    cy={directDotY}
                    r="3.5"
                    fill="var(--qpdemo-control)"
                  />
                  <text
                    className="endlabel"
                    x={labelX}
                    y={ry + 4}
                    style={{ fill: 'var(--qpdemo-router)' }}
                  >
                    {formatQps(routerVal)} qps
                  </text>
                  <text
                    className="endlabel"
                    x={labelX}
                    y={dy + 4}
                    style={{ fill: 'var(--qpdemo-control)' }}
                  >
                    {formatQps(directVal)} qps
                  </text>
                  {lifted && (
                    <text
                      x={labelX}
                      y={dy + 17}
                      style={{ fill: 'var(--color-content-layout-3)' }}
                    >
                      baseline
                    </text>
                  )}
                </>
              )
            })()}

          {hover && (
            <g pointerEvents="none">
              <line
                x1={xFor(hover.t)}
                y1={m.top}
                x2={xFor(hover.t)}
                y2={m.top + plotH}
                stroke="var(--color-content-layout-2)"
                strokeDasharray="2 3"
              />
              <circle
                cx={xFor(hover.t)}
                cy={yFor(hover.router.qps)}
                r="4"
                fill="var(--qpdemo-router)"
              />
              <circle
                cx={xFor(hover.t)}
                cy={yFor(hover.direct.qps)}
                r="4"
                fill="var(--qpdemo-control)"
              />
              <g
                transform={`translate(${Math.min(xFor(hover.t) + 10, W - 214)}, ${m.top + 8})`}
              >
                <rect
                  width="204"
                  height={tipHeight}
                  rx="8"
                  fill="var(--color-surface-layout-1)"
                  stroke="var(--color-border-layout-1)"
                />
                <text x="10" y="20" className="endlabel">
                  {new Date(hover.t * 1000).toLocaleTimeString([], {
                    minute: '2-digit',
                    second: '2-digit',
                  })}
                </text>
                <text x="10" y="42" style={{ fill: 'var(--qpdemo-router)' }}>
                  Readyset path: {formatQps(hover.router.qps)} qps
                </text>
                <text x="10" y="62" style={{ fill: 'var(--qpdemo-control)' }}>
                  Postgres direct: {formatQps(hover.direct.qps)} qps
                </text>
                {hoverEvent && (
                  <text
                    x="10"
                    y="84"
                    className="endlabel"
                    style={{ fill: 'var(--color-content-layout-1)' }}
                  >
                    {eventDescription(hoverEvent)}
                  </text>
                )}
              </g>
            </g>
          )}

          {!pts.length && (
            <text x={W / 2} y={H / 2} textAnchor="middle">
              Waiting for load history
            </text>
          )}
        </svg>
      </div>
    </div>
  )
}

function HeartbeatDot() {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className="qpdemo-heartbeat inline-block h-2.5 w-2.5 cursor-default rounded-full bg-surface-positive-solid"
            aria-label="QueryPilot activity"
          />
        </TooltipTrigger>
        <TooltipContent label={HEARTBEAT_TOOLTIP} />
      </Tooltip>
    </TooltipProvider>
  )
}

// Chart-window control. An expert setting deferred out of the hero into the
// Advanced disclosure; the chart defaults to the one useful (5-minute) window.
// [VIS-114, VIS-103]
function WindowToggle({
  windowMode,
  onWindowMode,
}: {
  windowMode: WindowMode
  onWindowMode: (mode: WindowMode) => void
}) {
  return (
    <div className="flex items-center gap-3">
      <Text as="span" level="body-small" className="text-content-layout-2">
        Chart window
      </Text>
      <div
        className="inline-flex overflow-hidden rounded-lg border border-border-layout-1 text-label-small"
        role="group"
        aria-label="Chart window"
      >
        {(['5m', '1m'] as WindowMode[]).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={windowMode === option}
            className={`px-2.5 py-1 ${windowMode === option ? 'bg-surface-primary-solid text-content-primary-solid' : 'text-content-layout-2 hover:bg-surface-layout-soft'}`}
            onClick={() => onWindowMode(option)}
          >
            {option === '5m' ? '5 min' : '1 min'}
          </button>
        ))}
      </div>
    </div>
  )
}

// QueryPilot caching-policy control. Deferred out of the primary controls into
// the Advanced disclosure — an expert setting, not part of the headline moment.
// Renders only while QueryPilot is on. [VIS-011, VIS-103, USE-006]
function PolicyControl({
  mode,
  disabled,
  onMode,
}: {
  mode: DiscoveryMode
  disabled: boolean
  onMode: (mode: DiscoveryMode) => void
}) {
  return (
    <div data-tour-anchor="mode" className="flex items-center gap-3">
      <Text as="span" level="body-small" className="text-content-layout-2">
        Caching policy
      </Text>
      <div className="inline-flex overflow-hidden rounded-xl border border-border-layout-1 text-button-small">
        {(['count_star', 'sum_time'] as DiscoveryMode[]).map((option) => (
          <button
            key={option}
            type="button"
            disabled={disabled}
            className={`px-3 py-1.5 disabled:opacity-50 ${mode === option ? 'bg-surface-primary-solid text-content-primary-solid' : 'text-content-layout-2 hover:bg-surface-layout-soft'}`}
            onClick={() => onMode(option)}
          >
            {MODE_LABEL[option]}
          </button>
        ))}
      </div>
    </div>
  )
}

function ControlsRow({
  loadRunning,
  querypilotOn,
  disabled,
  onStart,
  onStop,
  onToggleQueryPilot,
}: {
  loadRunning: boolean
  querypilotOn: boolean
  disabled: boolean
  onStart: () => void
  onStop: () => void
  onToggleQueryPilot: (next: boolean) => void
}) {
  return (
    <div className={panel('flex flex-wrap items-center gap-x-7 gap-y-3 p-4')}>
      <div data-tour-anchor="traffic">
        {loadRunning ? (
          <Button
            variant="primary"
            modifier="outline"
            size="base"
            label="Stop"
            disabled={disabled}
            onClick={onStop}
          />
        ) : (
          <Button
            variant="primary"
            size="base"
            icon="play"
            iconPosition="left"
            label="Start"
            disabled={disabled}
            onClick={onStart}
          />
        )}
      </div>

      <div data-tour-anchor="querypilot" className="flex items-center gap-3">
        <Text as="span" level="body-small" className="text-content-layout-2">
          QueryPilot
        </Text>
        <BaseInputSwitch
          name="querypilot"
          className={SWITCH_CHECKED_GREEN}
          checked={querypilotOn}
          disabled={disabled}
          onCheckedChange={onToggleQueryPilot}
        />
        <Text
          as="span"
          level="body-small"
          className="font-medium text-content-layout-1"
        >
          {querypilotOn ? 'On' : 'Off'}
        </Text>
        {querypilotOn && <HeartbeatDot />}
      </div>
    </div>
  )
}

// Friendly names for the images the demo pulls, so the provisioning substep
// never surfaces a raw container-registry path (or its internal account id).
const IMAGE_FRIENDLY: Record<string, string> = {
  pg: 'the orders database',
  postgres: 'the orders database',
  orders: 'the orders database',
  'orders-pg': 'the orders database',
  readyset: 'the Readyset cache engine',
  sqp: 'the QueryPilot router',
  'qp-cron': 'the QueryPilot accelerator',
  qpcron: 'the QueryPilot accelerator',
}

// Registry reference like `public.ecr.aws/i9r8k6s4/sqp:20260709-1722`: a
// dotted host (optionally with a port) followed by a slash-path.
const REGISTRY_REF = /\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?\/[^\s()]+/i

// The provision stream narrates image pulls with raw ECR URIs that also leak an
// internal account id, e.g. "Pulling public.ecr.aws/xxxx/sqp:tag (3/4, ~75 MB)".
// Surface a friendly component name and keep any "(n/m, ~X MB)" progress hint;
// plain status narration (no registry path) passes through untouched.
// [audit LOW #8, USE-025, VIS-011]
function friendlyProvisionDetail(detail: string): string {
  const ref = detail.match(REGISTRY_REF)
  if (!ref) return detail
  const progress = detail.match(/\([^)]*\)\s*$/)?.[0] ?? ''
  const image = (ref[0].split('/').pop() ?? '')
    .replace(/[:@].*$/, '')
    .toLowerCase()
  const friendly = IMAGE_FRIENDLY[image]
  const base = friendly ? `Downloading ${friendly}` : 'Downloading images'
  return progress ? `${base} ${progress}` : base
}

function ProvisionView({
  containers,
  pct,
  phase,
}: {
  containers: ContainerProgress[]
  pct: number
  phase: Phase
}) {
  const tearing = phase === 'tearing-down'
  // The four components start sequentially: the first not-ready step is the
  // active one. Completed steps collapse to a checked row, pending steps dim.
  const errorIndex = containers.findIndex((c) => c.state === 'error')
  const activeIndex =
    errorIndex >= 0
      ? errorIndex
      : containers.findIndex((c) => c.state !== 'ready')
  // Per-step elapsed ticker so the active step is visibly alive every second,
  // even if the event stream goes quiet for a moment.
  const [nowMs, setNowMs] = useState(() => Date.now())
  const activeSinceRef = useRef<{ index: number; at: number }>({
    index: activeIndex,
    at: Date.now(),
  })
  if (activeSinceRef.current.index !== activeIndex) {
    activeSinceRef.current = { index: activeIndex, at: Date.now() }
  }
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  const elapsedS = Math.max(
    0,
    Math.floor((nowMs - activeSinceRef.current.at) / 1000)
  )

  if (tearing) {
    return (
      <div className="qpdemo-enter max-w-2xl space-y-3">
        <Text as="h2" level="subtitle-1" className="text-content-layout-1">
          Removing demo containers and data...
        </Text>
        {containers.map((c) => (
          <div key={c.name} className={panel('flex items-center gap-3 p-3.5')}>
            <span
              className={`grid h-8 w-8 place-items-center rounded-lg ${c.state === 'ready' ? 'bg-surface-positive-soft text-content-positive-soft' : 'bg-surface-layout-2 text-content-layout-3'}`}
            >
              {c.state === 'ready' ? (
                <Icon name="tick" label="removed" className="h-4 w-4" />
              ) : (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <Text
                as="div"
                level="body-small"
                className="font-medium text-content-layout-1"
              >
                {c.label}
              </Text>
              <Text
                as="div"
                level="caption"
                className="truncate text-content-layout-3"
              >
                {c.state === 'ready' ? 'removed' : 'removing'}
              </Text>
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="qpdemo-enter max-w-2xl space-y-2.5">
      <Text as="h2" level="subtitle-1" className="text-content-layout-1">
        Setting up the demo
      </Text>
      {containers.map((c, i) => {
        const done = c.state === 'ready'
        const failed = c.state === 'error'
        const active = i === activeIndex && !done
        if (done) {
          return (
            <div
              key={c.name}
              className={panel('flex items-center gap-3 px-3.5 py-2')}
            >
              <span className="grid h-6 w-6 place-items-center rounded-lg bg-surface-positive-soft text-content-positive-soft">
                <Icon name="tick" label="ready" className="h-3.5 w-3.5" />
              </span>
              <Text
                as="div"
                level="body-small"
                className="min-w-0 flex-1 truncate font-medium text-content-layout-1"
              >
                {c.label}
              </Text>
              <Text as="span" level="caption" className="text-content-layout-3">
                ready
              </Text>
            </div>
          )
        }
        if (active) {
          return (
            <div
              key={c.name}
              className={panel('flex items-center gap-3 p-3.5')}
            >
              {/* Failure is red; the active step is a neutral spinner — no orange
                  during provisioning. */}
              <span
                className={`grid h-8 w-8 place-items-center rounded-lg ${failed ? 'bg-surface-negative-soft text-content-negative-soft' : 'bg-surface-layout-2 text-content-layout-3'}`}
              >
                {failed ? (
                  <Icon name="alert" label="error" className="h-4 w-4" />
                ) : (
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <Text
                  as="div"
                  level="body-small"
                  className="font-medium text-content-layout-1"
                >
                  {c.label}
                </Text>
                <Text
                  as="div"
                  level="caption"
                  data-testid="provision-substep"
                  className="truncate text-content-layout-3"
                >
                  {failed
                    ? 'failed'
                    : `${c.detail ? friendlyProvisionDetail(c.detail) : 'Starting...'}${elapsedS >= 3 ? ` · ${elapsedS}s` : ''}`}
                </Text>
              </div>
            </div>
          )
        }
        return (
          <div
            key={c.name}
            className={panel('flex items-center gap-3 px-3.5 py-2 opacity-45')}
          >
            <span className="grid h-6 w-6 place-items-center rounded-lg bg-surface-layout-2 text-content-layout-3">
              <span className="h-2 w-2 rounded-full bg-current opacity-50" />
            </span>
            <Text
              as="div"
              level="body-small"
              className="min-w-0 flex-1 truncate font-medium text-content-layout-2"
            >
              {c.label}
            </Text>
            <Text as="span" level="caption" className="text-content-layout-3">
              waiting
            </Text>
          </div>
        )
      })}
      <div className="flex items-center gap-3 pt-1">
        <div className="h-3 flex-1 overflow-hidden rounded-full bg-surface-layout-soft">
          <div
            className="h-full bg-content-rising-plain transition-[width]"
            style={{ width: `${pct}%` }}
          />
        </div>
        <Text
          as="span"
          level="label-small"
          data-testid="provision-percent"
          className="w-10 text-right tabular-nums text-content-layout-1"
        >
          {Math.round(pct)}%
        </Text>
      </div>
    </div>
  )
}

type PreflightState = 'checking' | 'ok' | 'blocked' | 'pending'

function PreflightItem({
  state,
  okLabel,
  pendingLabel,
}: {
  // 'blocked' is a hard requirement that is not met (red X, disables Start);
  // 'pending' is something the demo handles for you on start (blue dot, does
  // not block) — e.g. images that will download.
  state: PreflightState
  okLabel: string
  pendingLabel: string
}) {
  const badge =
    state === 'ok'
      ? 'bg-[var(--qpdemo-good-bg)] text-[var(--qpdemo-good)]'
      : state === 'blocked'
        ? 'bg-[var(--qpdemo-deny-bg)] text-[var(--qpdemo-deny)]'
        : state === 'pending'
          ? 'bg-blue-500/15 text-blue-600 dark:text-blue-400'
          : 'bg-surface-layout-2 text-content-layout-3'
  return (
    <li className="flex items-center gap-2">
      <span
        className={`grid h-4.5 w-4.5 shrink-0 place-items-center rounded-full ${badge}`}
      >
        {state === 'checking' ? (
          <span className="h-2.5 w-2.5 animate-spin rounded-full border-[1.5px] border-current border-t-transparent" />
        ) : state === 'ok' ? (
          <Icon name="tick" label="" className="h-3 w-3" />
        ) : state === 'blocked' ? (
          <Icon name="close" label="" className="h-3 w-3" />
        ) : (
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
        )}
      </span>
      <Text
        as="span"
        level="body-small"
        className={
          state === 'ok' ? 'text-content-layout-2' : 'text-content-layout-1'
        }
      >
        {state === 'ok' ? okLabel : pendingLabel}
      </Text>
    </li>
  )
}

function StartCard({ onStart }: { onStart: () => void }) {
  const [checks, setChecks] = useState<PreflightChecks | null>(null)
  const [checking, setChecking] = useState(true)
  const recheck = useCallback(async () => {
    setChecking(true)
    try {
      setChecks(await getPreflight())
    } catch {
      setChecks(null)
    } finally {
      setChecking(false)
    }
  }, [])
  useEffect(() => {
    void recheck()
  }, [recheck])

  return (
    <div className={panel('qpdemo-enter max-w-2xl p-5')}>
      <Text
        as="p"
        level="body-small"
        className="max-w-[58ch] text-content-layout-2"
      >
        The demo builds an orders database in local containers, puts it under a
        live workload, and routes it through Readyset Platform. Cache a query
        and the effect shows immediately. Everything runs in self-contained,
        temporary containers.
      </Text>
      <div className="mt-4 rounded-lg border border-border-layout-1 bg-surface-layout-soft/40 p-3">
        <div className="flex items-center justify-between gap-2">
          <Text
            as="p"
            level="caption"
            className="font-medium text-content-layout-2"
          >
            What's required
          </Text>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="filter-reset"
            iconPosition="left"
            label="Re-check"
            disabled={checking}
            onClick={recheck}
          />
        </div>
        <ul className="mt-2 space-y-1.5">
          <PreflightItem
            state={
              !checks
                ? 'checking'
                : !checks.docker_installed || !checks.docker_running
                  ? 'blocked'
                  : 'ok'
            }
            okLabel="Docker is running"
            pendingLabel={
              checks && !checks.docker_installed
                ? "Docker isn't installed — install Docker Desktop to continue"
                : "Docker isn't running — start Docker to continue"
            }
          />
          {/* Apple Silicon only, and only when the engine definitively cannot
              run amd64 containers: everyone else never sees this row. */}
          {checks?.amd64_emulation === 'unavailable' && (
            <PreflightItem
              state="blocked"
              okLabel="Intel-container support ready"
              pendingLabel="Enable 'Use Rosetta for x86_64/amd64 emulation' in Docker settings, then re-check"
            />
          )}
          <PreflightItem
            state={
              !checks
                ? 'checking'
                : !checks.disk_space_ok
                  ? 'blocked'
                  : checks.images_present
                    ? 'ok'
                    : 'pending'
            }
            okLabel="Container images downloaded"
            pendingLabel={
              checks && !checks.disk_space_ok
                ? 'Not enough free disk — the demo needs about 2 GB'
                : 'Container images not downloaded yet — about 2 GB'
            }
          />
        </ul>
      </div>
      <Text
        as="p"
        level="caption"
        className="mt-3 max-w-[58ch] rounded-lg border border-border-layout-1 bg-surface-layout-soft/40 p-2.5 text-content-layout-2"
      >
        <span className="font-semibold text-[var(--qpdemo-router)]">Note:</span>{' '}
        the environment cleans itself up after an hour, or remove it yourself
        anytime with Shut down demo.
      </Text>
      <Button
        variant="primary"
        size="base"
        icon="play"
        iconPosition="left"
        label="Start the demo"
        className="mt-4"
        disabled={
          checks
            ? !checks.docker_installed ||
              !checks.docker_running ||
              !checks.disk_space_ok ||
              checks.amd64_emulation === 'unavailable'
            : false
        }
        onClick={onStart}
      />
    </div>
  )
}

// Tight, collision-safe popover copy: at most a short sentence plus one numbers
// line. Numbers are preserved; the prose is kept to a few words per clause.
// Plain-words name for what the active policy ranks on.
function modePhrase(activeMode: DiscoveryMode) {
  return activeMode === 'sum_time' ? 'most expensive' : 'most frequently run'
}

function popoverCopy(
  row: PatternRow,
  mode: DiscoveryMode,
  cacheBudget: number
) {
  const reason = row.reason
  const activeMode = modeFromMetric(reason.metric, mode)
  const budget = cacheBudget
  const baseNumbers = `${row.hits.toLocaleString()} hits · Postgres avg ${formatMs(row.direct_avg_ms)} · Readyset avg ${formatMs(row.router_avg_ms)}`
  switch (reason.kind) {
    case 'selected':
      return {
        sentence: `Cached: one of the top ${budget} ${modePhrase(activeMode)} queries right now.`,
        numbers: `${scoreFormula(reason, row)} · budget: top ${budget}`,
      }
    case 'below_rank':
      return {
        sentence: `Not cached: not among the top ${budget} ${modePhrase(activeMode)} queries right now.`,
        numbers: `${scoreFormula(reason, row)} · budget: top ${budget}`,
      }
    case 'below_min_execution':
      return {
        sentence:
          "Not cached yet: this query hasn't run enough times to be considered.",
        numbers: `${reason.count ?? row.hits} of ${reason.threshold ?? 5} runs · ${baseNumbers}`,
      }
    case 'denylisted':
      return {
        sentence: 'Blocked: excluded from automatic caching in this demo.',
        numbers: baseNumbers,
      }
    case 'unsupported':
      return {
        sentence:
          "Passes through: Readyset can't cache this SQL shape in the demo.",
        numbers: baseNumbers,
      }
    case 'manual':
      return {
        sentence:
          'Cached manually. Turning QueryPilot on drops manual caches and lets it manage caching.',
        numbers: baseNumbers,
      }
    case 'not_select_shaped':
    default:
      return {
        sentence: 'Skipped: not shaped for automatic selection.',
        numbers: baseNumbers,
      }
  }
}

function StatusPopover({
  row,
  mode,
  cacheBudget,
  waiting,
  open,
  onOpenChange,
}: {
  row: PatternRow
  mode: DiscoveryMode
  cacheBudget: number
  // Pre-traffic, a benign "no traffic yet" row is a neutral waiting state, not
  // an amber "not eligible yet" warning. [T16, audit MEDIUM #3, VIS-102]
  waiting: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const copy = waiting
    ? {
        sentence:
          'Waiting for traffic. This query is measured once the workload starts.',
        numbers: '',
      }
    : popoverCopy(row, mode, cacheBudget)
  const label = waiting ? 'waiting for traffic' : STATUS_LABEL[row.status]
  const chipClass = waiting
    ? 'bg-[var(--qpdemo-chip)] text-content-layout-2'
    : statusClass(row.status)
  // Popovers open on hover with sensible delays as well as click; the pill gets
  // cursor, hover, and a subtle press affordance. The popover stays open while
  // hovered (trigger and content share the same open/close timers).
  const openTimer = useRef<number | null>(null)
  const closeTimer = useRef<number | null>(null)
  const clearTimers = () => {
    if (openTimer.current) window.clearTimeout(openTimer.current)
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
    openTimer.current = null
    closeTimer.current = null
  }
  useEffect(() => clearTimers, [])
  const hoverOpen = () => {
    clearTimers()
    openTimer.current = window.setTimeout(() => onOpenChange(true), 120)
  }
  const hoverClose = () => {
    clearTimers()
    closeTimer.current = window.setTimeout(() => onOpenChange(false), 220)
  }
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`${label} details`}
          onMouseEnter={hoverOpen}
          onMouseLeave={hoverClose}
          className={`inline-flex cursor-pointer items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-label-small font-semibold transition-transform hover:brightness-105 active:scale-95 ${chipClass}`}
        >
          {label}
          <Icon name="info" label="" className="h-3 w-3 shrink-0 opacity-70" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={6}
        className="w-[min(320px,86vw)] p-3 text-left"
        onMouseEnter={hoverOpen}
        onMouseLeave={hoverClose}
        // Keep focus where the pointer is: Radix's default open-autofocus
        // scrolls the content into view, which yanks the page, moves the pill
        // out from under the cursor, and immediately hover-closes the popover.
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <Text as="p" level="body-small" className="text-content-layout-1">
          {copy.sentence}
        </Text>
        {copy.numbers && (
          <Text
            as="p"
            level="mono-small"
            className="mt-2 block text-content-layout-3"
          >
            {copy.numbers}
          </Text>
        )}
      </PopoverContent>
    </Popover>
  )
}

function SqlExpander({ sql }: { sql: string }) {
  const shape = useMemo(() => parameterizeSql(sql), [sql])
  const highlights = useMemo(
    () => buildParameterHighlights(shape.placeholders),
    [shape.placeholders]
  )
  // Leading empty cell keeps the SQL block indented under the query title
  // rather than flush to the row's far left.
  return (
    <>
      <td className="w-8 px-3" />
      <td colSpan={7} className="px-3 pb-3 pt-0">
        <div className="overflow-hidden rounded-lg border border-border-layout-1">
          <SQLDisplay sql={shape.text} wrap parameterHighlights={highlights} />
        </div>
      </td>
    </>
  )
}

function PatternTable({
  patterns,
  mode,
  cacheBudget,
  suggestedKeys,
  querypilotOn,
  tourActive,
  tourCachedKeys,
  tourUncachedKey,
  onCache,
  onUncache,
}: {
  patterns: PatternRow[]
  mode: DiscoveryMode
  cacheBudget: number
  suggestedKeys: Set<string>
  querypilotOn: boolean
  tourActive: boolean
  tourCachedKeys: string[] | null
  tourUncachedKey: string | null
  onCache: (key: string, title: string) => void
  onUncache: (key: string, title: string) => void
}) {
  const [openSql, setOpenSql] = useState<Set<string>>(new Set())
  const [openPopover, setOpenPopover] = useState<string | null>(null)
  // Row-order invariants:
  // 1. Rows never re-sort on their own. The default order is the stable
  //    workload/query identity, and a column-header click captures a one-time
  //    SNAPSHOT of that column's values as a frozen key order - counters keep
  //    changing but rows hold their positions until the next click.
  // 2. While the tour is running, the order is ALWAYS the query identity, so
  //    every tour step knows exactly where its rows live; header sorting is
  //    disabled until the tour is over.
  const [frozenOrder, setFrozenOrder] = useState<{
    col: SnapshotSortKey
    dir: 'asc' | 'desc'
    keys: string[]
  } | null>(null)
  const sorted = useMemo(() => {
    const base = [...patterns].sort(rowIdentity)
    if (tourActive || !frozenOrder) return base
    const pos = new Map(frozenOrder.keys.map((k, i) => [k, i]))
    return base.sort(
      (a, b) =>
        (pos.get(a.key) ?? Number.MAX_SAFE_INTEGER) -
          (pos.get(b.key) ?? Number.MAX_SAFE_INTEGER) || rowIdentity(a, b)
    )
  }, [patterns, frozenOrder, tourActive])

  const snapshotSort = (col: SnapshotSortKey) => {
    if (tourActive) return
    const dir: 'asc' | 'desc' =
      frozenOrder?.col === col && frozenOrder.dir === 'desc' ? 'asc' : 'desc'
    const sign = dir === 'asc' ? 1 : -1
    const keys = [...patterns]
      .sort((a, b) => {
        if (col === 'status')
          return (
            sign *
              STATUS_LABEL[a.status].localeCompare(STATUS_LABEL[b.status]) ||
            rowIdentity(a, b)
          )
        const an = a[col] == null ? Number.NEGATIVE_INFINITY : Number(a[col])
        const bn = b[col] == null ? Number.NEGATIVE_INFINITY : Number(b[col])
        return sign * (an - bn) || rowIdentity(a, b)
      })
      .map((r) => r.key)
    setFrozenOrder({ col, dir, keys })
  }

  const resetOrder = () => {
    if (tourActive) return
    setFrozenOrder(null)
  }

  const sortMark = (col: SnapshotSortKey | 'query') => {
    if (col === 'query') return frozenOrder == null ? '▲' : ''
    if (frozenOrder?.col !== col) return ''
    return frozenOrder.dir === 'desc' ? ' ▼' : ' ▲'
  }

  const toggleSql = (key: string) => {
    setOpenSql((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const setPopover = (row: PatternRow, open: boolean) => {
    setOpenPopover(open ? row.key : null)
  }

  // Tour spotlights are PINNED by DemoPage at step entry and passed down, so
  // a step highlights the same rows for its whole lifetime no matter how
  // statuses evolve underneath. The live derivations below are only the
  // fallback for renders where nothing is pinned yet.
  const uncached = sorted.filter(
    (r) => r.status === 'pass_through' || r.status === 'not_eligible'
  )
  const firstUncachedKey =
    tourUncachedKey ??
    [...uncached].sort(
      (a, b) =>
        (b.direct_avg_ms ?? 0) - (a.direct_avg_ms ?? 0) ||
        a.key.localeCompare(b.key)
    )[0]?.key
  const highlightedCachedKeys = new Set(
    tourCachedKeys ??
      sorted
        .filter((r) => r.status === 'cached_querypilot')
        .slice(0, 2)
        .map((r) => r.key)
  )

  return (
    <div data-tour-anchor="table" className={panel('overflow-hidden')}>
      {/* Fixed height with internal scroll: rows may reorder within it, but the
          table's position and size never shift the page. */}
      <div className="max-h-[440px] overflow-auto">
        <table className="w-full border-collapse text-label-medium">
          <thead className="sticky top-0 z-10 bg-surface-layout-1">
            <tr className="border-b border-border-layout-1 text-left text-label-small text-content-layout-3">
              <th className="w-8 px-3 py-2" />
              {/* Header clicks take a one-time value snapshot; rows never
                  re-sort live, and sorting is inert while the tour runs. */}
              <th className="px-3 py-2">
                <button
                  className="font-semibold disabled:cursor-default"
                  disabled={tourActive}
                  onClick={resetOrder}
                >
                  Query{sortMark('query')}
                </button>
              </th>
              <th className="px-3 py-2 text-right">
                <button
                  className="font-semibold disabled:cursor-default"
                  disabled={tourActive}
                  onClick={() => snapshotSort('postgres_hits')}
                >
                  Postgres hits{sortMark('postgres_hits')}
                </button>
              </th>
              <th className="px-3 py-2 text-right">
                <button
                  className="font-semibold disabled:cursor-default"
                  disabled={tourActive}
                  onClick={() => snapshotSort('readyset_hits')}
                >
                  Readyset hits{sortMark('readyset_hits')}
                </button>
              </th>
              <th className="px-3 py-2 text-right">
                <button
                  className="font-semibold disabled:cursor-default"
                  disabled={tourActive}
                  onClick={() => snapshotSort('direct_avg_ms')}
                >
                  Postgres avg{sortMark('direct_avg_ms')}
                </button>
              </th>
              <th className="px-3 py-2 text-right">
                <button
                  className="font-semibold disabled:cursor-default"
                  disabled={tourActive}
                  onClick={() => snapshotSort('router_avg_ms')}
                >
                  Readyset avg{sortMark('router_avg_ms')}
                </button>
              </th>
              <th className="px-3 py-2">
                <button
                  className="font-semibold disabled:cursor-default"
                  disabled={tourActive}
                  onClick={() => snapshotSort('status')}
                >
                  Status{sortMark('status')}
                </button>
              </th>
              <th className="px-3 py-2 font-semibold">Cache status</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const expanded = openSql.has(row.key)
              const isSuggested = suggestedKeys.has(row.key)
              // A row that has never been hit and reads "not eligible yet" is a
              // benign no-traffic state, not a warning: show it as a neutral
              // "waiting for traffic" pill with no manual toggle, so the pre-load
              // table isn't a wall of amber alarms. [T16, audit MEDIUM #3]
              const isWaiting = row.hits === 0 && row.status === 'not_eligible'
              return (
                <Fragment key={row.key}>
                  <tr
                    data-tour-cached-querypilot={
                      highlightedCachedKeys.has(row.key) ? 'true' : undefined
                    }
                    data-tour-uncached={
                      row.key === firstUncachedKey ? 'true' : undefined
                    }
                    data-tour-manual-suggested={
                      isSuggested ? 'true' : undefined
                    }
                    className={`border-b border-border-layout-1 hover:bg-surface-layout-2/40 ${expanded ? 'border-b-0' : ''}`}
                  >
                    <td className="px-3 py-2 text-content-layout-3">
                      <button
                        aria-label={`Expand ${row.title}`}
                        onClick={() => toggleSql(row.key)}
                      >
                        {expanded ? '▾' : '▸'}
                      </button>
                    </td>
                    <td className="max-w-[360px] px-3 py-2">
                      <button
                        className="block text-left"
                        onClick={() => toggleSql(row.key)}
                      >
                        <Text
                          as="div"
                          level="body-small"
                          data-testid="pattern-title"
                          className="whitespace-nowrap font-medium text-content-layout-1"
                        >
                          {row.title}
                        </Text>
                        <Text
                          as="div"
                          level="mono-small"
                          className="max-w-[340px] truncate text-content-layout-3"
                        >
                          {parameterizeSql(row.sql).text}
                        </Text>
                      </button>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-content-layout-2">
                      {row.postgres_hits.toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-content-layout-2">
                      {row.readyset_hits.toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-content-layout-2">
                      {formatMs(row.direct_avg_ms)}
                    </td>
                    <td
                      className={`px-3 py-2 text-right tabular-nums ${row.router_avg_ms != null && row.direct_avg_ms != null && row.router_avg_ms < row.direct_avg_ms ? 'font-semibold text-content-positive-soft' : 'text-content-layout-2'}`}
                    >
                      {row.router_avg_ms == null &&
                      row.status.startsWith('cached_') ? (
                        // Freshly cached: the average is held until the cache is
                        // actually serving, so show that the number is on its way.
                        <span
                          className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent align-middle opacity-70"
                          role="status"
                          aria-label="warming cache"
                        />
                      ) : (
                        formatMs(row.router_avg_ms)
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <StatusPopover
                        row={row}
                        mode={mode}
                        cacheBudget={cacheBudget}
                        waiting={isWaiting}
                        open={openPopover === row.key}
                        onOpenChange={(o) => setPopover(row, o)}
                      />
                    </td>
                    <td className="px-3 py-2">
                      {!isWaiting &&
                      (row.status === 'cached_manual' ||
                        row.status === 'cached_querypilot' ||
                        canManualCache(row)) ? (
                        // Manual caching is the early hands-on beat. Once QueryPilot
                        // is on it owns all caching, so these switches gray out but
                        // stay visible, showing who currently caches each query.
                        querypilotOn ? (
                          // A disabled switch swallows hover, so the span is the
                          // tooltip trigger. The table has no TooltipProvider
                          // ancestor, so each switch carries its own.
                          <TooltipProvider delayDuration={150}>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="inline-flex">
                                  <BaseInputSwitch
                                    name={`manual-cache-${row.key}`}
                                    className={SWITCH_CHECKED_GREEN}
                                    checked={
                                      row.status === 'cached_manual' ||
                                      row.status === 'cached_querypilot'
                                    }
                                    disabled
                                  />
                                </span>
                              </TooltipTrigger>
                              <TooltipContent
                                label={MANUAL_CACHE_DISABLED_TOOLTIP}
                              />
                            </Tooltip>
                          </TooltipProvider>
                        ) : (
                          <BaseInputSwitch
                            name={`manual-cache-${row.key}`}
                            className={SWITCH_CHECKED_GREEN}
                            checked={
                              row.status === 'cached_manual' ||
                              row.status === 'cached_querypilot'
                            }
                            onCheckedChange={(next) =>
                              next
                                ? onCache(row.key, row.title)
                                : onUncache(row.key, row.title)
                            }
                          />
                        )
                      ) : null}
                    </td>
                  </tr>
                  {expanded && (
                    <tr className="border-b border-border-layout-1">
                      <SqlExpander sql={row.sql} />
                    </tr>
                  )}
                </Fragment>
              )
            })}
            {patterns.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center">
                  <Text
                    as="span"
                    level="body-small"
                    className="text-content-layout-3"
                  >
                    Workload queries appear once traffic starts.
                  </Text>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function readTourDone() {
  if (typeof window === 'undefined') return false
  return window.localStorage.getItem(TOUR_STORAGE_KEY) === '1'
}

function writeTourDone(done: boolean) {
  if (typeof window === 'undefined') return
  if (done) window.localStorage.setItem(TOUR_STORAGE_KEY, '1')
  else window.localStorage.removeItem(TOUR_STORAGE_KEY)
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

// Union of the anchor elements' viewport rects, padded but never distorted:
// clamping an off-screen rect to the viewport made the cutout fly to a wrong
// position; instead the anchor is scrolled into view before measuring.
function unionRects(rects: DOMRect[]) {
  if (!rects.length) return null
  const pad = 8
  return {
    left: Math.min(...rects.map((r) => r.left)) - pad,
    top: Math.min(...rects.map((r) => r.top)) - pad,
    right: Math.max(...rects.map((r) => r.right)) + pad,
    bottom: Math.max(...rects.map((r) => r.bottom)) + pad,
  }
}

function anchorSelector(anchor: string) {
  if (anchor === 'manual-suggestions')
    return '[data-tour-manual-suggested="true"]'
  if (anchor === 'cached-querypilot-rows')
    return '[data-tour-cached-querypilot="true"]'
  if (anchor === 'uncached-rows') return '[data-tour-uncached="true"]'
  return `[data-tour-anchor="${anchor}"]`
}

// Live Readyset-vs-Postgres throughput multiple for the permanent ratio line:
// one decimal below 2x (0.9x, 1.3x), whole numbers at or above (10x).
export function formatLiftRatio(ratio: number): string {
  if (!Number.isFinite(ratio) || ratio <= 0) return '0.0x'
  return ratio >= 2 ? `${Math.round(ratio)}x` : `${ratio.toFixed(1)}x`
}

// Intersect an element's rect with every scrollable ancestor. An element
// scrolled out of an inner scroll box (the query table) still reports a rect
// at the coordinates it WOULD occupy - clipped, invisible, and often on top
// of unrelated UI. The tour must never draw a cutout over such phantom
// coordinates: null here means "not actually visible anywhere".
export function clipToScrollAncestors(
  el: HTMLElement,
  r: DOMRect
): DOMRect | null {
  let top = r.top
  let left = r.left
  let bottom = r.bottom
  let right = r.right
  for (let node = el.parentElement; node; node = node.parentElement) {
    const s = window.getComputedStyle(node)
    if (
      /(auto|scroll|hidden)/.test(`${s.overflow}${s.overflowY}${s.overflowX}`)
    ) {
      const c = node.getBoundingClientRect()
      top = Math.max(top, c.top)
      left = Math.max(left, c.left)
      bottom = Math.min(bottom, c.bottom)
      right = Math.min(right, c.right)
    }
  }
  if (bottom - top < 2 || right - left < 2) return null
  return new DOMRect(left, top, right - left, bottom - top)
}

function useAnchorRect(anchor: string | null, deps: unknown[]) {
  const [rect, setRect] = useState<ReturnType<typeof unionRects>>(null)

  useLayoutEffect(() => {
    if (!anchor) {
      setRect(null)
      return undefined
    }

    const measure = () => {
      const elements = Array.from(
        document.querySelectorAll<HTMLElement>(anchorSelector(anchor))
      )
      const visible = elements
        .map((el) => {
          const r = el.getBoundingClientRect()
          return r.width > 0 && r.height > 0
            ? clipToScrollAncestors(el, r)
            : null
        })
        .filter((r): r is DOMRect => r != null)
      const next = unionRects(visible)
      setRect(next)
      return next
    }

    // Bring the anchor to the CENTER of the viewport before measuring. 'center'
    // scrolls every scrollable ancestor (the table's own scroll AND the window),
    // so a row below the page fold is actually revealed rather than leaving the
    // cutout stranded in empty space. Re-scroll if the first measure still lands
    // off-screen (layout can shift as rows settle).
    const bringIntoView = () => {
      const el = document.querySelector<HTMLElement>(anchorSelector(anchor))
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({
          block: 'center',
          inline: 'nearest',
          behavior: 'auto',
        })
      }
    }

    const update = () => {
      const r = measure()
      // No visible rect (anchor exists but is scrolled out of its container)
      // or a rect outside the window: bring the anchor back into view rather
      // than spotlighting empty space.
      const anchorExists =
        document.querySelector(anchorSelector(anchor)) != null
      if (
        (r == null && anchorExists) ||
        (r && (r.top < 0 || r.bottom > window.innerHeight))
      ) {
        bringIntoView()
        // Re-measure next frame after the scroll settles.
        window.requestAnimationFrame(measure)
      }
    }

    bringIntoView()
    window.requestAnimationFrame(update)
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    // Re-measure on a short cadence so the cutout stays glued to the anchor
    // after any layout settle (row data refreshes, fonts, images).
    const id = window.setInterval(update, 250)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
      window.clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchor, ...deps])

  return rect
}

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(mq.matches)
    update()
    mq.addEventListener?.('change', update)
    return () => mq.removeEventListener?.('change', update)
  }, [])
  return reduced
}

function tourConfig(step: TourStepId): {
  anchor: string | null
  copy: string
  primary?: string
  secondary?: string
} {
  switch (step) {
    case 'welcome':
      return {
        anchor: null,
        copy: 'This demo spins up a small database, puts it under load, and places Readyset in front of it. Everything runs in temporary containers, and one click cleans it all up.',
        primary: 'Show me',
        secondary: "I'll explore on my own",
      }
    case 'traffic':
      return {
        anchor: 'traffic',
        copy: 'Traffic starts flowing to both paths — Readyset and Postgres — establishing a baseline throughput to measure against.',
      }
    case 'chart':
      return {
        anchor: 'chart',
        copy: 'The same workload runs through Readyset (teal) and straight Postgres (amber). The lines match right now because Readyset proxies to the upstream database until caches exist. Once queries are cached, Readyset serves them from memory and the lines separate.',
        primary: 'Next',
      }
    case 'table':
      return {
        anchor: 'table',
        copy: 'Every query in the workload, with how often it runs and how long it takes on each path. Expand a row to see the query.',
        primary: 'Next',
      }
    case 'manual':
      return {
        anchor: 'manual-suggestions',
        copy: 'Two easy wins: cache these queries by hand to see what caching does to them.',
      }
    case 'manualResult':
      return {
        anchor: 'manual-suggestions',
        copy: 'These two rows are now served from cache: Readyset latency drops sharply against Postgres — the same query answered from memory instead of recomputed.',
        primary: 'Next',
      }
    case 'querypilot':
      return {
        anchor: 'querypilot',
        copy: 'That was manual caching. Now, turn on QueryPilot to automate it: it drops the manual picks and manages caching itself, watching traffic and caching the most frequent queries (top 20 in this demo). The first pass takes about a minute, after which the Readyset line pulls ahead of Postgres.',
      }
    case 'qpWaiting':
      return {
        anchor: 'chart',
        copy: 'QueryPilot is making its first pass while the caches build.',
      }
    case 'autoCachedChart':
      return {
        anchor: 'chart',
        copy: 'QueryPilot just cached the most frequent queries — the teal line climbs as Readyset serves them from memory.',
        primary: 'Next',
      }
    case 'autoCached':
      return {
        anchor: 'cached-querypilot-rows',
        copy: 'QueryPilot cached the most frequent queries. Hover a status chip to see why it was chosen.',
        primary: 'Next',
      }
    case 'mode':
      return {
        anchor: 'mode',
        copy: 'Switch to Most expensive — QueryPilot re-caches around the 20 most expensive queries instead.',
      }
    case 'reselecting':
      return {
        anchor: 'chart',
        copy: 'QueryPilot dropped its old picks and is re-selecting for Most expensive. The Readyset line climbs as the new caches build — continue when ready.',
        primary: 'Next',
      }
    case 'modeWhyNot':
      return {
        anchor: 'uncached-rows',
        copy: 'Hover the status of an uncached query to see why QueryPilot passed on it.',
        primary: 'Next',
      }
    case 'finale':
      return {
        anchor: null,
        copy: "That's the tour. QueryPilot keeps caching by the chosen policy, managing every cache automatically. Use Shut down demo anytime to remove the containers and the whole demo from your system.",
        primary: 'Finish',
      }
  }
}

function TourLayer({
  step,
  copyOverride,
  primaryDisabled,
  onPrimary,
  onSecondary,
  onSkip,
  deps,
}: {
  step: TourStepId
  copyOverride?: string
  primaryDisabled?: boolean
  onPrimary: () => void
  onSecondary: () => void
  onSkip: () => void
  deps: unknown[]
}) {
  const config = tourConfig(step)
  const copy = copyOverride ?? config.copy
  const rect = useAnchorRect(config.anchor, deps)
  const hasCutout = Boolean(config.anchor && rect)
  const reduceMotion = usePrefersReducedMotion()
  // Table-family anchors span most of the width, so the bubble docks to their
  // LEFT; the chart anchor docks directly above where the action is.
  const tableFamily =
    config.anchor === 'table' ||
    config.anchor === 'uncached-rows' ||
    config.anchor === 'cached-querypilot-rows'

  const bubbleW = 340
  const bubbleH = 200
  let bubbleStyle: CSSProperties = {
    position: 'fixed',
    left: '50%',
    top: '50%',
    transform: 'translate(-50%, -50%)',
  }
  if (hasCutout && rect) {
    const topBeside = clamp(rect.top, 16, window.innerHeight - bubbleH - 16)
    const leftRoom = rect.left - bubbleW - 16 >= 16
    const rightRoom = rect.right + bubbleW + 32 <= window.innerWidth
    if (tableFamily && leftRoom) {
      bubbleStyle = {
        position: 'fixed',
        left: rect.left - bubbleW - 16,
        top: topBeside,
      }
    } else if (config.anchor === 'chart' && rect.top >= bubbleH + 24) {
      bubbleStyle = {
        position: 'fixed',
        left: clamp(rect.left, 16, window.innerWidth - bubbleW - 16),
        top: rect.top - 16,
        transform: 'translateY(-100%)',
      }
    } else if (rightRoom) {
      bubbleStyle = { position: 'fixed', left: rect.right + 16, top: topBeside }
    } else if (leftRoom) {
      bubbleStyle = {
        position: 'fixed',
        left: rect.left - bubbleW - 16,
        top: topBeside,
      }
    } else if (rect.top >= bubbleH + 24) {
      bubbleStyle = {
        position: 'fixed',
        left: clamp(rect.left, 16, window.innerWidth - bubbleW - 16),
        top: rect.top - 16,
        transform: 'translateY(-100%)',
      }
    } else if (window.innerHeight - rect.bottom >= bubbleH + 24) {
      bubbleStyle = {
        position: 'fixed',
        left: clamp(rect.left, 16, window.innerWidth - bubbleW - 16),
        top: rect.bottom + 16,
      }
    } else {
      bubbleStyle = {
        position: 'fixed',
        left: '50%',
        bottom: 16,
        transform: 'translateX(-50%)',
      }
    }
  }

  const cutoutTransition = reduceMotion
    ? undefined
    : 'left 200ms ease, top 200ms ease, width 200ms ease, height 200ms ease'

  return (
    <>
      {hasCutout && rect ? (
        // ONE full-viewport dim via the box-shadow-cutout technique: a div sized
        // to the anchor rect whose 9999px box-shadow darkens the entire rest of
        // the screen at rgba(0,0,0,0.55), leaving only the anchor bright. Its
        // pointer-events are off so the highlighted control stays clickable
        // through the hole; the rect is re-measured on scroll/resize.
        <div
          aria-hidden
          data-testid="tour-dim-cutout"
          className="pointer-events-none fixed z-50 rounded-xl"
          style={{
            left: rect.left,
            top: rect.top,
            width: Math.max(0, rect.right - rect.left),
            height: Math.max(0, rect.bottom - rect.top),
            boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.55)',
            outline: '2px solid rgba(255, 255, 255, 0.85)',
            outlineOffset: '2px',
            transition: cutoutTransition,
          }}
        />
      ) : (
        <div
          aria-hidden
          data-testid="tour-dim-full"
          className="fixed inset-0 z-50"
          style={{ background: 'rgba(0, 0, 0, 0.55)' }}
        />
      )}
      <div
        role="dialog"
        aria-live="polite"
        className="fixed z-[60] w-[min(340px,calc(100vw-32px))] rounded-lg border border-border-layout-1 bg-surface-layout-1 p-4 shadow-xl"
        style={bubbleStyle}
      >
        <Text as="p" level="body-small" className="text-content-layout-1">
          {copy}
        </Text>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          {step === 'welcome' ? (
            <span />
          ) : (
            <Button
              variant="primary"
              modifier="link"
              size="small"
              label="Skip tour"
              onClick={onSkip}
            />
          )}
          <div className="ml-auto flex gap-2">
            {config.secondary && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label={config.secondary}
                onClick={onSecondary}
              />
            )}
            {config.primary && (
              <Button
                variant="primary"
                size="small"
                label={config.primary}
                disabled={primaryDisabled}
                onClick={onPrimary}
              />
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function ResetOverlay() {
  return (
    <div className="absolute inset-0 z-[70] flex items-center justify-center rounded-lg bg-black/35 p-4 backdrop-blur-[1px]">
      <div className="flex max-w-md items-center gap-3 rounded-lg border border-border-layout-1 bg-surface-layout-1 p-4 shadow-xl">
        <span className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-content-primary-soft border-t-transparent" />
        <Text as="p" level="body-small" className="text-content-layout-1">
          {MODE_RESET_COPY}
        </Text>
      </div>
    </div>
  )
}

// The demo's terminal "so-what" beat: the watched cache win is worth nothing if
// the convinced evaluator has nowhere to go. This is the flow's principal new
// contribution — a single conviction CTA that carries the already-collected
// identity forward to "connect your own database" with no re-gating.
// [demo-to-conviction step 7, MET-008, USE-022; VIS-102 reuse of the hero/empty
// block pattern — NEEDS VARIANT: conviction hand-off panel, folds into C-07/C-09]
function BridgePanel({ onConnect }: { onConnect: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-lg border border-border-primary-soft bg-surface-primary-soft/50 p-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="max-w-[62ch]">
        <Text as="h2" level="subtitle-2" className="text-content-layout-1">
          Ready to see this on your data?
        </Text>
        <Text as="p" level="body-small" className="mt-1 text-content-layout-2">
          You just saw it on a sample Orders database — point RDST at your own
          Postgres or MySQL to find the queries worth caching.
        </Text>
      </div>
      <Button
        variant="primary"
        size="base"
        icon="arrow-right"
        iconPosition="right"
        label="Get this for your database"
        className="shrink-0"
        onClick={onConnect}
      />
    </div>
  )
}

export function DemoPage() {
  const d = useDemo()
  const navigate = useNavigate()
  const showTeardown = d.phase !== 'idle'
  // Carry the demo identity forward: the email collected at the gate persists in
  // settings, and Connect (a normal in-shell route) never re-gates. `from=demo`
  // lets Connect acknowledge the hand-off in one line. [T16, USE-008, USE-022]
  const goConnect = useCallback(() => {
    void navigate({ to: '/onboarding', search: { from: 'demo' } })
  }, [navigate])
  const [confirmingTeardown, setConfirmingTeardown] = useState(false)
  const [modeResetting, setModeResetting] = useState(false)
  const [pendingMode, setPendingMode] = useState<DiscoveryMode | null>(null)
  const [windowMode, setWindowMode] = useState<WindowMode>('5m')
  // Deferred surfaces (VIS-103): expert controls live behind Advanced and the
  // 37-row detail table behind its own disclosure, both collapsed by default so
  // the hero comparison is the only thing competing for attention.
  const [advancedUserOpen, setAdvancedUserOpen] = useDisclosure({})
  const [perQueryUserOpen, setPerQueryUserOpen] = useDisclosure({})
  const [tourStep, setTourStep] = useState<TourStepId | null>(null)
  const [tourDone, setTourDone] = useState(readTourDone)
  // Once the tour has been started (auto or via Replay), the auto-open effect
  // must never fire again this session: re-opening at Welcome mid-tour was the
  // "tour resets to Welcome" bug.
  const tourStartedRef = useRef(false)
  const previousModeRef = useRef(d.mode)
  const suggestions = useMemo(
    () => suggestedMidTier(d.patterns, d.cacheBudget),
    [d.cacheBudget, d.patterns]
  )
  const suggestedKeys = useMemo(
    () => new Set(suggestions.map((p) => p.key)),
    [suggestions]
  )
  const hasQueryPilotCache = d.patterns.some(
    (p) => p.status === 'cached_querypilot'
  )
  // Gate for the 'reselecting' step: Next stays locked until QueryPilot's
  // Most-expensive pass is DONE - the budget is full, or the cached set has
  // stopped changing for two full QueryPilot passes. Only a finished pass
  // makes "this query stays uncached" a settled claim for the next step, so
  // the why-not spotlight can be pinned once and never proven wrong.
  const reselectTarget = Math.min(d.cacheBudget, d.patterns.length)
  const cachedSetSig = d.patterns
    .filter(
      (p) => p.status === 'cached_querypilot' || p.status === 'cached_manual'
    )
    .map((p) => p.key)
    .sort()
    .join(',')
  const reselectCached =
    cachedSetSig === '' ? 0 : cachedSetSig.split(',').length
  const [cachedSetChange, setCachedSetChange] = useState({ sig: '', at: 0 })
  useEffect(() => {
    if (cachedSetSig !== cachedSetChange.sig) {
      setCachedSetChange({ sig: cachedSetSig, at: Date.now() })
    }
  }, [cachedSetSig, cachedSetChange.sig])
  const reselectComplete =
    reselectCached > 0 &&
    (reselectCached >= reselectTarget ||
      Date.now() - cachedSetChange.at > RESELECT_SETTLE_MS)
  // Live Readyset-vs-Postgres throughput multiple over the recent window, for
  // the permanent ratio line. Always computed, never gated.
  const liftRatio = windowLiftRatio(d.samples, 15)
  // A multiplier is only meaningful once real traffic exists on the direct
  // path. Before that, the line shows an honest prompt instead of a fabricated
  // "0.0x" (which reads as "Readyset does zero"). [T16, audit MEDIUM #2]
  const hasComparison = d.samples.some((s) => s.direct.qps > 0)

  // The guided tour still needs the deferred surfaces on screen when it walks
  // them: the Advanced disclosure auto-opens for the policy step, and the
  // per-query table stays open for the whole tour. Neither changes the demo's
  // behavior — only which panels are expanded. [VIS-103]
  const advancedOpen = advancedUserOpen || tourStep === 'mode'
  const perQueryDetailOpen = perQueryUserOpen || tourStep != null

  const noticeRef = useRef<string | null | undefined>(undefined)
  const errorRef = useRef<string | null | undefined>(undefined)
  const healthRef = useRef<Record<string, ContainerProgress['state']>>({})

  useEffect(() => {
    if (d.error && d.error !== errorRef.current) {
      toast({
        title: 'Something went wrong',
        description: d.error,
        variant: 'negative',
      })
    }
    errorRef.current = d.error
  }, [d.error])

  useEffect(() => {
    if (d.notice && d.notice !== noticeRef.current) {
      toast({ description: d.notice })
    }
    noticeRef.current = d.notice
  }, [d.notice])

  useEffect(() => {
    for (const c of d.containers) {
      const prev = healthRef.current[c.name]
      if (prev !== c.state) {
        if (c.state === 'error')
          toast({
            title: 'A demo container failed',
            description: c.label,
            variant: 'negative',
          })
        else if (c.state === 'recovering')
          toast({
            title: 'Recovering a demo container',
            description: c.label,
            variant: 'warning',
          })
      }
      healthRef.current[c.name] = c.state
    }
  }, [d.containers])

  const completeTour = useCallback(() => {
    writeTourDone(true)
    setTourDone(true)
    setTourStep(null)
  }, [])

  const openTour = useCallback(() => {
    writeTourDone(false)
    setTourDone(false)
    tourStartedRef.current = true
    setTourStep('welcome')
  }, [])

  const nextTour = useCallback(() => {
    setTourStep((current) => {
      if (current === 'welcome') return 'traffic'
      if (current === 'chart') return 'table'
      if (current === 'table') return 'manual'
      if (current === 'manualResult') return 'querypilot'
      if (current === 'autoCachedChart') return 'autoCached'
      if (current === 'autoCached') return 'mode'
      if (current === 'reselecting') return 'modeWhyNot'
      if (current === 'modeWhyNot') return 'finale'
      if (current === 'finale') {
        writeTourDone(true)
        setTourDone(true)
        return null
      }
      return current
    })
  }, [])

  useEffect(() => {
    if (
      d.phase === 'ready' &&
      !tourDone &&
      !tourStartedRef.current &&
      tourStep == null
    ) {
      tourStartedRef.current = true
      setTourStep('welcome')
    }
  }, [d.phase, tourDone, tourStep])

  // Tour spotlights are pinned the moment their step activates and hold for
  // the step's whole lifetime. Deriving them live made the cutout chase the
  // data: more caches landing re-elected "the first two cached rows" (or a
  // new slowest-uncached row) and the spotlight leapt across the table.
  const [pinnedCachedKeys, setPinnedCachedKeys] = useState<string[] | null>(
    null
  )
  const [pinnedUncachedKey, setPinnedUncachedKey] = useState<string | null>(
    null
  )
  // Pins are strictly pin-once: a spotlight NEVER moves during its step. The
  // claims stay true by sequencing, not correction - cached rows stay cached
  // under the active policy, and the why-not step is only reachable after the
  // re-selection pass has finished, when D03/D04 (designed to never rank) are
  // settled pass-through rows.
  useEffect(() => {
    if (tourStep !== 'autoCached') {
      if (pinnedCachedKeys) setPinnedCachedKeys(null)
      return
    }
    if (pinnedCachedKeys) return
    const cached = d.patterns
      .filter((p) => p.status === 'cached_querypilot')
      .sort(rowIdentity)
      .slice(0, 2)
      .map((p) => p.key)
    if (cached.length) setPinnedCachedKeys(cached)
  }, [tourStep, d.patterns, pinnedCachedKeys])
  useEffect(() => {
    if (tourStep !== 'modeWhyNot') {
      if (pinnedUncachedKey) setPinnedUncachedKey(null)
      return
    }
    if (pinnedUncachedKey) return
    const uncached = d.patterns.filter(
      (p) => p.status === 'pass_through' || p.status === 'not_eligible'
    )
    const preferred = TOUR_UNCACHED_PICKS.find((k) =>
      uncached.some((p) => p.key === k)
    )
    const fallback = [...uncached].sort(rowIdentity)[0]?.key ?? null
    const pick = preferred ?? fallback
    if (pick) setPinnedUncachedKey(pick)
  }, [tourStep, d.patterns, pinnedUncachedKey])

  // traffic -> chart once load is actually running.
  useEffect(() => {
    if (tourStep === 'traffic' && d.loadRunning) setTourStep('chart')
  }, [d.loadRunning, tourStep])

  // manual -> querypilot once both suggested easy wins are cached by hand.
  useEffect(() => {
    if (
      tourStep === 'manual' &&
      suggestions.length >= 2 &&
      suggestions.every((p) => p.status === 'cached_manual')
    ) {
      setTourStep('manualResult')
    }
  }, [suggestions, tourStep])

  // querypilot -> a visible chart-anchored waiting step until QueryPilot's
  // first pass caches something. The tour never blanks its step mid-flow, so
  // completed steps (Welcome included) can never re-appear.
  useEffect(() => {
    if (tourStep === 'querypilot' && d.querypilotOn) {
      setTourStep(hasQueryPilotCache ? 'autoCachedChart' : 'qpWaiting')
    }
  }, [d.querypilotOn, hasQueryPilotCache, tourStep])

  useEffect(() => {
    if (tourStep === 'qpWaiting' && hasQueryPilotCache)
      setTourStep('autoCachedChart')
  }, [hasQueryPilotCache, tourStep])

  // mode -> reselecting once the visitor switches to Most expensive; the step
  // anchors the chart while the sum_time pass rebuilds caches, then advances
  // to modeWhyNot on the first re-cache event.
  useEffect(() => {
    const previous = previousModeRef.current
    previousModeRef.current = d.mode
    if (tourStep === 'mode' && previous !== d.mode && d.mode === 'sum_time') {
      setTourStep('reselecting')
    }
  }, [d.mode, tourStep])

  // 'reselecting' -> 'modeWhyNot' is now driven by the step's Next button (see
  // nextTour), so the visitor controls when to move on and the Readyset line has
  // time to climb and settle first.

  // The chart-anchored auto-cache beat zooms to the recent minute so the lift
  // is unmistakable.
  useEffect(() => {
    if (tourStep === 'autoCachedChart') setWindowMode('1m')
  }, [tourStep])

  const requestMode = useCallback(
    (nextMode: DiscoveryMode) => {
      if (nextMode === d.mode) return
      setPendingMode(nextMode)
    },
    [d.mode]
  )

  const cancelMode = useCallback(() => setPendingMode(null), [])

  const confirmMode = useCallback(async () => {
    const nextMode = pendingMode
    setPendingMode(null)
    if (!nextMode) return
    setModeResetting(true)
    try {
      await d.setDiscoveryMode(nextMode)
    } finally {
      setModeResetting(false)
    }
  }, [d, pendingMode])

  return (
    <div className="qpdemo relative mx-auto flex max-w-[1060px] flex-col gap-5 p-6">
      <style>{DEMO_STYLE}</style>
      {/* B — Header band: the job as the page name + one-line subhead. The
          replay/shut-down utilities are demoted to the quiet F row below.
          [VIS-116, VIS-011] */}
      <header>
        <Text as="h1" level="headline-3" className="text-content-layout-1">
          See Readyset Platform in action
        </Text>
        <Text
          as="p"
          level="body-small"
          className="mt-1 max-w-[72ch] text-content-layout-2"
        >
          The same workload runs against an orders database through Postgres and
          Readyset Platform, side by side — showing how much faster your queries
          get as QueryPilot automatically chooses the right queries to cache.
        </Text>
      </header>

      {d.phase === 'idle' && <StartCard onStart={d.provision} />}

      {(d.phase === 'provisioning' || d.phase === 'tearing-down') && (
        <ProvisionView
          containers={d.containers}
          pct={d.provisionPct}
          phase={d.phase}
        />
      )}

      {d.phase === 'ready' && (
        <div className="qpdemo-enter flex flex-col gap-5">
          {/* C — Hero stage: the comparison money-shot. One headline multiplier
              and the live chart that proves it, grouped on a single elevated
              surface so the claim and its evidence read together.
              [VIS-011, VIS-012, VIS-105, VIS-127] */}
          <section className="flex flex-col gap-2 rounded-xl bg-surface-raised p-4 shadow-elevation-1">
            {/* Permanent, fixed-min-height ratio headline: always rendered, so it
                never mounts/unmounts and jumps the layout. Before any traffic it
                holds an honest prompt instead of a false "0.0x". The multiplier
                carries meaning by weight + color, not size alone.
                [T16, audit MEDIUM #2, VIS-011, VIS-012] */}
            {hasComparison ? (
              <p
                data-testid="lift-ratio"
                className="flex min-h-[3.25rem] flex-wrap items-baseline gap-x-2.5 gap-y-1"
              >
                <Text
                  as="span"
                  level="stat-hero"
                  className={
                    liftRatio >= 1.3
                      ? 'text-content-positive-soft'
                      : 'text-content-layout-1'
                  }
                >
                  {formatLiftRatio(liftRatio)}
                </Text>
                <Text
                  as="span"
                  level="subtitle-2"
                  className="font-medium text-content-layout-2"
                >
                  Readyset throughput vs Postgres direct
                </Text>
              </p>
            ) : (
              <p
                data-testid="lift-ratio"
                className="flex min-h-[3.25rem] items-center"
              >
                <Text
                  as="span"
                  level="body-small"
                  className="text-content-layout-3"
                >
                  Start traffic to see how much faster Readyset serves cached
                  queries.
                </Text>
              </p>
            )}
            <ThroughputChart
              samples={d.samples}
              events={d.events}
              windowMode={windowMode}
              bare
            />
          </section>
          {/* 2nd — the phase action controls (traffic + QueryPilot). */}
          <ControlsRow
            loadRunning={d.loadRunning}
            querypilotOn={d.querypilotOn}
            disabled={modeResetting}
            onStart={d.beginLoad}
            onStop={d.endLoad}
            onToggleQueryPilot={d.toggleQueryPilot}
          />
          {/* E — per-query detail: the story is told by the chart + number, so
              the 37-row table (with its per-row toggles, sort, popovers and SQL
              expanders) is collapsed by default. Native <details> keeps it a
              real, accessible disclosure. [VIS-103, VIS-108, VIS-011] */}
          <details
            open={perQueryDetailOpen}
            className={panel('overflow-hidden')}
          >
            <summary
              onClick={(e) => {
                e.preventDefault()
                setPerQueryUserOpen(!perQueryUserOpen)
              }}
              className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-content-layout-1 hover:bg-surface-layout-2/40 [&::-webkit-details-marker]:hidden"
            >
              <Icon
                name="chevron-down"
                label=""
                className={`h-4 w-4 shrink-0 text-content-layout-3 transition-transform ${perQueryDetailOpen ? 'rotate-180' : ''}`}
              />
              <Text as="span" level="subtitle-2" className="font-medium">
                Show per-query detail
              </Text>
              <Text as="span" level="body-small" className="text-content-layout-3">
                ({d.patterns.length})
              </Text>
            </summary>
            <div className="border-t border-border-layout-1">
              <PatternTable
                patterns={d.patterns}
                mode={d.mode}
                cacheBudget={d.cacheBudget}
                suggestedKeys={suggestedKeys}
                querypilotOn={d.querypilotOn}
                tourActive={tourStep != null}
                tourCachedKeys={pinnedCachedKeys}
                tourUncachedKey={pinnedUncachedKey}
                onCache={d.cache}
                onUncache={d.uncache}
              />
            </div>
          </details>
          <Text
            as="p"
            level="caption"
            className="max-w-[78ch] text-content-layout-3"
          >
            Hit counts and latency averages cover the current comparison window
            and reset whenever QueryPilot is toggled or the policy changes. The
            chart keeps its full history and marks each event.
          </Text>
          <BridgePanel onConnect={goConnect} />
        </div>
      )}

      {/* F — quiet tertiary utility row: Advanced settings (disclosure),
          Replay tour, and the demoted, confirm-guarded Shut down demo. Rendered
          whenever the stack exists (non-idle), matching the prior top-bar
          affordances but out of the primary flow. [VIS-022, VIS-023, VIS-103] */}
      {showTeardown && (
        <div className="mt-1 flex flex-col gap-3 border-t border-border-layout-1 pt-4">
          {d.phase === 'ready' && (
            <details open={advancedOpen}>
              <summary
                onClick={(e) => {
                  e.preventDefault()
                  setAdvancedUserOpen(!advancedUserOpen)
                }}
                className="inline-flex cursor-pointer list-none items-center gap-1.5 text-content-layout-2 hover:text-content-layout-1 [&::-webkit-details-marker]:hidden"
              >
                <Icon
                  name="chevron-down"
                  label=""
                  className={`h-4 w-4 shrink-0 transition-transform ${advancedOpen ? 'rotate-180' : ''}`}
                />
                <Text as="span" level="body-small" className="font-medium">
                  Advanced
                </Text>
              </summary>
              <div className="mt-3 flex flex-wrap items-center gap-x-8 gap-y-3 pl-6">
                <WindowToggle
                  windowMode={windowMode}
                  onWindowMode={setWindowMode}
                />
                {d.querypilotOn && (
                  <PolicyControl
                    mode={d.mode}
                    disabled={modeResetting}
                    onMode={requestMode}
                  />
                )}
              </div>
            </details>
          )}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
            {d.phase === 'ready' && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                icon="filter-reset"
                iconPosition="left"
                label="Replay tour"
                onClick={openTour}
              />
            )}
            {/* Demoted from a prominent solid-red action to a quiet tertiary,
                guarded by a confirm — teardown wipes 4 containers + ~2 GB of
                images, so a mis-click must not fire silently. [T16, audit MEDIUM
                #4, USE-077, VIS-022] */}
            <Button
              variant="negative"
              modifier="ghost"
              size="small"
              icon="trash"
              iconPosition="left"
              label="Shut down demo"
              onClick={() => setConfirmingTeardown(true)}
            />
          </div>
        </div>
      )}

      {modeResetting && <ResetOverlay />}
      {/* Policy-switch + teardown confirmations now ride the shared
          ConfirmDialog primitive (T19/C-08): focus-trap, Escape and a titled
          dialog for free, replacing the two hand-rolled focus-trap-less dialogs. */}
      <ConfirmDialog
        isOpen={pendingMode != null}
        onClose={cancelMode}
        onConfirm={confirmMode}
        title="Switch caching policy?"
        notice={{ accent: 'info', icon: 'info', message: MODE_SWITCH_CONFIRM }}
        confirmLabel="Switch policy"
        confirmVariant="primary"
      />
      <ConfirmDialog
        isOpen={confirmingTeardown}
        onClose={() => setConfirmingTeardown(false)}
        onConfirm={() => {
          setConfirmingTeardown(false)
          void d.tearDown()
        }}
        title="Shut down demo?"
        notice={{
          accent: 'warning',
          icon: 'alert',
          message:
            'Remove all demo containers and data? You can start it again anytime.',
        }}
        confirmLabel="Shut down demo"
        confirmVariant="negative"
      />
      {!modeResetting && !pendingMode && tourStep && (
        <TourLayer
          step={tourStep}
          primaryDisabled={tourStep === 'reselecting' && !reselectComplete}
          copyOverride={
            tourStep === 'reselecting' && !reselectComplete
              ? 'QueryPilot dropped its old picks and is re-selecting for Most expensive. The Readyset line climbs as the new caches land.'
              : undefined
          }
          deps={[
            d.phase,
            d.loadRunning,
            d.mode,
            d.querypilotOn,
            d.patterns.length,
            suggestions.length,
          ]}
          onPrimary={nextTour}
          onSecondary={completeTour}
          onSkip={completeTour}
        />
      )}
    </div>
  )
}
