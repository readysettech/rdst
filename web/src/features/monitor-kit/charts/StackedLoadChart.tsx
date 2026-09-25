// StackedLoadChart - "Where the time goes" (DESIGN_V4 section 2).
//
// Stacked areas, one band per top query, or one line per query (latency). Same
// grammar as MonitorTimeChart (three gridlines on a nice max, 11px axis labels,
// snapping crosshair and value card) plus band hit-testing: hovering a band raises
// it and dims the rest, clicking selects it. The value card lists the bands at the
// hovered time, top of the stack first.
//
// Usage:
//   <StackedLoadChart
//     ariaLabel="Queries per second, last hour"
//     xs={model.xs} domain={domain} resolution={model.res}
//     series={model.bands.map((band, i) => ({ id: band.key, label: band.label,
//       color: band.color, values: model.calls[i] }))}
//     mode="stack" formatValue={(value) => `${value}/s`}
//     activeId={hovered} focusId={focused}
//     onHover={setHovered} onSelect={toggleFocus}
//   />
import { cn } from '@rs/tailwind-base'
import {
  Fragment,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { fromScroll } from '../pointerIntent'
import { useElementWidth, useEntrance } from './chartHooks'
import { niceMax } from './chartMath'
import { GRID_COLOR } from './chartTheme'
import {
  type ChartHighlight,
  HighlightBand,
  useChartHighlight,
} from './highlight'
import type { ChartOverlay } from './overlayModel'
import { PauseBands } from './PauseBands'
import type { ChartPause } from './pauseModel'
import { hoverLabel, ticksFor } from './rangeModel'

export interface StackSeries {
  id: string
  label: string
  color: string
  values: (number | null)[]
  /** Dashed reference line (lines mode), never stacked or hit-tested. */
  reference?: boolean
}

export interface StackedLoadChartProps {
  xs: number[]
  series: StackSeries[]
  mode: 'stack' | 'lines'
  ariaLabel: string
  domain: [number, number]
  resolution: number
  height?: number
  formatValue: (value: number) => string
  formatAxis?: (value: number) => string
  /** The y axis unit for the axis maximum (for example 'queries/s'). */
  axisUnit?: (yMax: number) => { unit: string; divisor: number }
  /** Pins the axis top (100 for percentage stacks) instead of rounding up the data. */
  fixedMax?: number
  /** Leaves the per-band percent out of the value card (the values already are shares). */
  hideShare?: boolean
  /** Band raised from outside (a hovered query row). */
  activeId?: string | null
  /** Selected band: stays raised, the rest dim. */
  focusId?: string | null
  onHover?: (id: string | null) => void
  onSelect?: (id: string) => void
  /** Drag across the plot to zoom into [start, end] (Unix seconds). */
  onZoom?: (window: [number, number]) => void
  /** A shaded window (an incident); defaults to ChartHighlightContext. */
  highlight?: ChartHighlight | null
  /** One query's own line over the bands, labelled with its name (the open query). */
  overlay?: ChartOverlay | null
  /** Names the overlay at its end and states its scale; small charts name it elsewhere. */
  overlayLabels?: boolean
  /** Spans with no readings, hatched and labelled with the reason (pauseModel). */
  pauses?: ChartPause[]
  /** Shown in the value card under the bands ('Total'). */
  totalLabel?: string
  emptyLabel?: string
  /** The svg's test id. */
  testId?: string
  className?: string
}

const TOP = 22
const BOTTOM = 26
const RIGHT = 14
const DIM = 0.2
/** Band opacity under an overlay line, so the line reads first. */
const UNDER_OVERLAY = 0.35
/** An overlay peaking under this fraction of the axis is drawn on its own scale. */
const OWN_SCALE_BELOW = 0.2
const DRAG_SLOP = 4

interface Binned {
  xs: number[]
  values: (number | null)[][]
}

/** Averages consecutive buckets so there are at most `max` columns. */
function bin(xs: number[], values: (number | null)[][], max: number): Binned {
  if (xs.length <= max) return { xs, values }
  const size = Math.ceil(xs.length / max)
  const outXs: number[] = []
  const out = values.map(() => [] as (number | null)[])
  for (let start = 0; start < xs.length; start += size) {
    const end = Math.min(xs.length, start + size)
    outXs.push(xs[end - 1])
    values.forEach((row, s) => {
      let total = 0
      let count = 0
      for (let i = start; i < end; i++)
        if (row[i] != null) {
          total += row[i] as number
          count++
        }
      out[s].push(count ? total / count : null)
    })
  }
  return { xs: outXs, values: out }
}

function axisDefault(value: number): string {
  if (value >= 1000) return `${Math.round(value / 100) / 10}k`
  if (value >= 10 || value === 0) return String(Math.round(value))
  return String(Math.round(value * 100) / 100)
}

export function StackedLoadChart({
  xs: rawXs,
  series,
  mode,
  ariaLabel,
  domain,
  resolution,
  height = 300,
  formatValue,
  formatAxis = axisDefault,
  axisUnit,
  fixedMax,
  hideShare = false,
  activeId = null,
  focusId = null,
  onHover,
  onSelect,
  onZoom,
  highlight: ownHighlight,
  overlay = null,
  overlayLabels = true,
  pauses,
  totalLabel = 'Total',
  emptyLabel = 'No readings in this range yet',
  testId = 'load-chart',
  className,
}: StackedLoadChartProps) {
  const highlight = useChartHighlight(ownHighlight)
  const chartId = useId().replaceAll(':', '')
  const [containerRef, width] = useElementWidth<HTMLDivElement>()
  const [rawHoverIndex, setHoverIndex] = useState<number | null>(null)
  const [hoverBand, setHoverBand] = useState<string | null>(null)
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null)
  // The value card follows the pointer and ignores it; a click pins it so its
  // queries can be clicked.
  const [pinned, setPinned] = useState(false)
  // Where the pointer is, so the card and the pin hint sit by it; null for the keyboard.
  const [pointer, setPointer] = useState<{ px: number; py: number } | null>(
    null
  )
  const [cardHeight, setCardHeight] = useState(0)
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const clearLeave = () => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current)
    leaveTimer.current = null
  }
  const dismiss = () => {
    clearLeave()
    setPinned(false)
    setHoverIndex(null)
    setHoverBand(null)
    setPointer(null)
    // Only the hover this chart raised: a row hovered since keeps its own.
    if (hoverBand) onHover?.(null)
  }
  // A pinned card closes on a click outside the chart or Escape.
  useEffect(() => {
    if (!pinned) return
    const onDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) dismiss()
    }
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') dismiss()
    }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [pinned])
  const [x0, x1] = domain
  const spanSeconds = Math.max(1, x1 - x0)
  const left = width < 420 ? 40 : 52
  const plotWidth = Math.max(1, width - left - RIGHT)
  const plotHeight = Math.max(1, height - TOP - BOTTOM)
  const bottomY = TOP + plotHeight

  const { xs, values } = useMemo(() => {
    const inDomain = rawXs
      .map((x, i) => ({ x, i }))
      .filter(({ x }) => x >= x0 - resolution && x <= x1 + resolution)
    const cut = {
      xs: inDomain.map(({ x }) => x),
      values: series.map((item) =>
        inDomain.map(({ i }) => item.values[i] ?? null)
      ),
    }
    return bin(cut.xs, cut.values, Math.max(24, Math.floor((width || 640) / 3)))
  }, [rawXs, series, x0, x1, resolution, width])
  const binRes =
    xs.length > 1 ? Math.max(resolution, xs[1] - xs[0]) : resolution

  // The overlay keeps its own timestamps (its buckets can be coarser than the bands').
  const line = useMemo(() => {
    if (!overlay) return null
    const step =
      overlay.xs.length > 1
        ? Math.max(1, overlay.xs[1] - overlay.xs[0])
        : resolution
    const points = overlay.xs
      .map((x, i) => ({ x, value: overlay.values[i] ?? null }))
      .filter(({ x }) => x >= x0 - step && x <= x1 + step)
    return { overlay, step, points }
  }, [overlay, x0, x1, resolution])

  const stacked = mode === 'stack'
  // A new window can leave an old index behind; drop it.
  const hoverIndex =
    rawHoverIndex != null && rawHoverIndex < xs.length ? rawHoverIndex : null
  const hitSeries = series
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => !item.reference)

  // Cumulative tops per series (stack mode); raw values (lines mode).
  const cum = useMemo(() => {
    if (!stacked) return values
    const out: number[][] = []
    series.forEach((_, s) => {
      out.push(xs.map((__, i) => (s ? out[s - 1][i] : 0) + (values[s][i] ?? 0)))
    })
    return out
  }, [stacked, series, values, xs])

  const hasData =
    values.some((row) => row.some((value) => value != null)) ||
    Boolean(line?.points.some((point) => point.value != null))
  const entering = useEntrance(hasData && width > 0, 800)

  // An overlay too small to read against the bands gets its own scale (labelled at
  // the top right); otherwise it shares the axis.
  const { yMax, overlayMax } = useMemo(() => {
    let max = 0
    if (stacked) {
      const topRow = cum[cum.length - 1] ?? []
      for (const value of topRow) if (value != null && value > max) max = value
    } else {
      for (const row of values)
        for (const value of row) if (value != null && value > max) max = value
    }
    let lineMax = 0
    for (const point of line?.points ?? [])
      if (point.value != null && point.value > lineMax) lineMax = point.value
    const shared = fixedMax ?? niceMax(Math.max(max, lineMax))
    if (lineMax > 0 && lineMax < shared * OWN_SCALE_BELOW)
      return { yMax: fixedMax ?? niceMax(max), overlayMax: niceMax(lineMax) }
    return { yMax: shared, overlayMax: null }
  }, [stacked, cum, values, fixedMax, line])

  const unit = axisUnit?.(yMax)
  const xFor = (x: number) => left + ((x - x0) / spanSeconds) * plotWidth
  const yFor = (value: number) =>
    TOP + plotHeight - Math.min(1, Math.max(0, value / yMax)) * plotHeight
  const overlayY = (value: number) =>
    overlayMax == null
      ? yFor(value)
      : TOP +
        plotHeight -
        Math.min(1, Math.max(0, value / overlayMax)) * plotHeight

  // Columns with any reading; a run breaks where a gap exceeds three buckets.
  const runs = useMemo(() => {
    const out: number[][] = []
    let current: number[] = []
    xs.forEach((x, i) => {
      const any = values.some((row) => row[i] != null)
      const gap =
        current.length && x - xs[current[current.length - 1]] > binRes * 3
      if (!any || gap) {
        if (current.length) out.push(current)
        current = any ? [i] : []
        return
      }
      current.push(i)
    })
    if (current.length) out.push(current)
    return out
  }, [xs, values, binRes])

  const paths = useMemo(() => {
    if (!width) return []
    return series.map((item, s) => {
      let area = ''
      let line = ''
      for (const run of runs) {
        if (stacked) {
          const top = run.map(
            (i) =>
              `${xFor(xs[i]).toFixed(1)} ${yFor(cum[s][i] as number).toFixed(1)}`
          )
          const bottom = run
            .map(
              (i) =>
                `${xFor(xs[i]).toFixed(1)} ${yFor(s ? (cum[s - 1][i] as number) : 0).toFixed(1)}`
            )
            .reverse()
          if (run.length === 1) {
            // A lone reading spans its bucket, up to now.
            const x = xFor(xs[run[0]])
            const edge = Math.max(
              x + 2,
              xFor(Math.min(xs[run[0]] + binRes, Date.now() / 1000))
            )
            top.push(`${edge.toFixed(1)} ${top[0].split(' ')[1]}`)
            bottom.unshift(`${edge.toFixed(1)} ${bottom[0].split(' ')[1]}`)
          }
          area += `M${top.join('L')}L${bottom.join('L')}Z`
          line += `M${top.join('L')}`
        } else {
          let started = false
          for (const i of run) {
            const value = values[s][i]
            if (value == null) {
              started = false
              continue
            }
            line += `${started ? 'L' : 'M'}${xFor(xs[i]).toFixed(1)} ${yFor(value).toFixed(1)}`
            started = true
          }
        }
      }
      return { item, area, line }
    })
  }, [series, runs, stacked, cum, values, xs, width, yMax, x0, x1, binRes])

  // A key from outside raises its band only when the chart draws one.
  const drawn = (id: string | null) =>
    id != null && series.some((item) => item.id === id && !item.reference)
      ? id
      : null
  const raised = hoverBand ?? drawn(activeId) ?? drawn(focusId)
  const opacityFor = (id: string) =>
    raised && raised !== id ? DIM : line && raised !== id ? UNDER_OVERLAY : 1

  const overlayPath = useMemo(() => {
    if (!line || !width) return null
    let d = ''
    let previous: number | null = null
    let last: { x: number; value: number } | null = null
    for (const { x, value } of line.points) {
      if (value == null) {
        previous = null
        continue
      }
      const joined = previous != null && x - previous <= line.step * 3
      d += `${joined ? 'L' : 'M'}${xFor(x).toFixed(1)} ${overlayY(value).toFixed(1)}`
      // A lone reading still shows: a short tick.
      if (!joined) d += 'l0.01 0'
      previous = x
      last = { x, value }
    }
    return { d, last }
  }, [line, width, yMax, overlayMax, x0, x1])

  const ticks = useMemo(
    () =>
      width ? ticksFor([x0, x1], width < 480 ? 4 : width >= 900 ? 8 : 6) : [],
    [x0, x1, width]
  )

  function indexAt(px: number): number | null {
    if (!xs.length) return null
    const target = x0 + ((px - left) / plotWidth) * spanSeconds
    let best = 0
    for (let i = 1; i < xs.length; i++)
      if (Math.abs(xs[i] - target) < Math.abs(xs[best] - target)) best = i
    return best
  }

  function bandAt(index: number, py: number): string | null {
    if (stacked) {
      const value = ((bottomY - py) / plotHeight) * yMax
      if (value < 0) return null
      for (const { item, index: s } of hitSeries) {
        const lower = s ? (cum[s - 1][index] as number) : 0
        const upper = cum[s][index] as number
        if (upper > lower && value >= lower && value <= upper) return item.id
      }
      return null
    }
    let best: { id: string; distance: number } | null = null
    for (const { item, index: s } of hitSeries) {
      const value = values[s][index]
      if (value == null) continue
      const distance = Math.abs(yFor(value) - py)
      if (distance < 14 && (!best || distance < best.distance))
        best = { id: item.id, distance }
    }
    return best?.id ?? null
  }

  const clampPx = (px: number) => Math.min(left + plotWidth, Math.max(left, px))
  const timeAt = (px: number) =>
    x0 + ((clampPx(px) - left) / plotWidth) * spanSeconds

  // A press becomes a drag-to-zoom once it moves past DRAG_SLOP, wherever it started
  // (the pinned card included). A press that doesn't move pins or unpins the card.
  const gesture = useRef<{
    x: number
    onCard: boolean
    dragging: boolean
  } | null>(null)

  function pointAt(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    return { px: event.clientX - rect.left, py: event.clientY - rect.top }
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    clearLeave()
    gesture.current = {
      x: pointAt(event).px,
      onCard: Boolean(cardRef.current?.contains(event.target as Node)),
      dragging: false,
    }
  }

  function endGesture(event: ReactPointerEvent<HTMLDivElement>) {
    gesture.current = null
    setDrag(null)
    if (event.currentTarget.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId)
  }

  function onPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const current = gesture.current
    const brush = drag
    endGesture(event)
    if (!current) return
    if (current.dragging) {
      if (brush && Math.abs(brush.to - brush.from) > DRAG_SLOP && onZoom) {
        const start = timeAt(Math.min(brush.from, brush.to))
        const end = timeAt(Math.max(brush.from, brush.to))
        if (end - start >= resolution * 2)
          onZoom([Math.floor(start), Math.ceil(end)])
      }
      return
    }
    // Clicks inside the pinned card belong to its buttons.
    if (current.onCard) return
    const { px } = pointAt(event)
    if (pinned) {
      setPinned(false)
      setHoverIndex(indexAt(px))
      return
    }
    if (hoverIndex == null) setHoverIndex(indexAt(px))
    setPinned(true)
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    // The chart scrolling under a still pointer isn't a hover.
    if (!gesture.current && fromScroll(event)) return
    const { px, py } = pointAt(event)
    const current = gesture.current
    clearLeave()
    if (current && !current.dragging && onZoom) {
      if (Math.abs(px - current.x) <= DRAG_SLOP) return
      current.dragging = true
      event.currentTarget.setPointerCapture?.(event.pointerId)
      setPinned(false)
      setHoverBand(null)
      onHover?.(null)
    }
    if (current?.dragging) {
      setDrag({ from: clampPx(current.x), to: clampPx(px) })
      return
    }
    if (current || pinned) return
    setPointer({ px, py })
    const index = indexAt(px)
    setHoverIndex(index)
    const band = index == null ? null : bandAt(index, py)
    if (band !== hoverBand) {
      setHoverBand(band)
      onHover?.(band)
    }
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (!xs.length) return
    const ids = hitSeries.map(({ item }) => item.id)
    setPointer(null)
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      const step = event.key === 'ArrowLeft' ? -1 : 1
      const current = hoverIndex ?? xs.length - 1
      setHoverIndex(Math.min(xs.length - 1, Math.max(0, current + step)))
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault()
      const step = event.key === 'ArrowUp' ? 1 : -1
      const current = hoverBand ? ids.indexOf(hoverBand) : -1
      const next = ids[(current + step + ids.length) % ids.length] ?? null
      setHoverBand(next)
      onHover?.(next)
      if (hoverIndex == null) setHoverIndex(xs.length - 1)
    } else if ((event.key === 'Enter' || event.key === ' ') && hoverBand) {
      event.preventDefault()
      onSelect?.(hoverBand)
    } else if (event.key === 'Escape') {
      setHoverIndex(null)
      setHoverBand(null)
      onHover?.(null)
    }
  }

  const allRows =
    hoverIndex == null
      ? []
      : series
          .map((item, s) => ({ item, value: values[s][hoverIndex] }))
          .filter(({ value }) => value != null)
          // Biggest first; "Everything else" and reference lines last.
          .sort((a, b) => {
            const tail = (id: string, ref?: boolean) =>
              ref || id === 'other' ? 1 : 0
            return (
              tail(a.item.id, a.item.reference) -
                tail(b.item.id, b.item.reference) ||
              (b.value as number) - (a.value as number)
            )
          })
  // With a query open, the card reads that query alone: its own line, or its band.
  const focusOnly =
    focusId != null &&
    (line != null || allRows.some(({ item }) => item.id === focusId))
  const cardRows = focusOnly
    ? line
      ? []
      : allRows.filter(({ item }) => item.id === focusId)
    : allRows
  // The overlay's reading for the hovered time: the bucket holding it, else the nearest.
  const overlayAt = (() => {
    if (!line || hoverIndex == null) return null
    const x = xs[hoverIndex]
    let best: { x: number; value: number } | null = null
    let bestDistance = Math.max(line.step, binRes)
    for (const point of line.points) {
      if (point.value == null) continue
      if (point.x <= x && x < point.x + line.step)
        return { x: point.x, value: point.value }
      const distance = Math.abs(point.x - x)
      if (distance < bestDistance) {
        bestDistance = distance
        best = { x: point.x, value: point.value }
      }
    }
    return best
  })()
  const total =
    stacked && hoverIndex != null
      ? values.reduce((sum, row) => sum + (row[hoverIndex] ?? 0), 0)
      : null
  const hoverPx = hoverIndex == null ? null : xFor(xs[hoverIndex])
  const cardWidth = 300
  // The card follows the pointer: beside it, flipped left near the right edge, and
  // level with it, allowed to hang a little below the plot rather than cover it.
  const anchorPx = pointer?.px ?? hoverPx
  const cardLeft =
    anchorPx == null
      ? 0
      : anchorPx + 18 + cardWidth > width
        ? Math.max(0, anchorPx - 18 - cardWidth)
        : anchorPx + 18
  const cardTop = pointer
    ? Math.max(
        0,
        Math.min(pointer.py - 28, Math.max(0, height + 72 - cardHeight))
      )
    : TOP
  const cardOnRight = anchorPx != null && cardLeft > anchorPx
  const showHint =
    pointer != null && !pinned && hoverIndex != null && onSelect != null
  useLayoutEffect(() => {
    const next = cardRef.current?.offsetHeight ?? 0
    if (next !== cardHeight) setCardHeight(next)
  })

  const lastIndex = xs.length - 1
  const summary = hasData
    ? `${ariaLabel}. ${line ? `Showing ${line.overlay.label}. ` : ''}${series
        .filter((item) => !item.reference)
        .slice(0, 3)
        .map((item) => item.label)
        .join(', ')} lead.`
    : `${ariaLabel}. ${emptyLabel}.`

  return (
    <div
      ref={containerRef}
      className={cn(
        'mk-chart relative w-full touch-pan-y',
        drag && 'cursor-ew-resize select-none',
        className
      )}
      style={{ height }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={endGesture}
      onPointerLeave={(event) => {
        if (gesture.current?.dragging || pinned || fromScroll(event)) return
        clearLeave()
        leaveTimer.current = setTimeout(dismiss, 350)
      }}
    >
      {width > 0 && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={summary}
          // biome-ignore lint/a11y/noNoninteractiveTabindex: arrow keys move the crosshair and pick bands
          tabIndex={0}
          onKeyDown={onKeyDown}
          className={cn(
            'block touch-pan-y select-none overflow-visible outline-none',
            drag
              ? 'cursor-ew-resize'
              : hoverBand && onSelect
                ? 'cursor-pointer'
                : 'cursor-crosshair'
          )}
          data-testid={testId}
        >
          <defs>
            <clipPath id={`${chartId}-plot`}>
              <rect
                x={left}
                y={TOP - 2}
                width={plotWidth}
                height={plotHeight + 2}
                className={entering ? 'mk-reveal' : undefined}
              />
            </clipPath>
            {series.map((item) => (
              <linearGradient
                key={item.id}
                id={`${chartId}-g-${item.id}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="0%" stopColor={item.color} stopOpacity="0.78" />
                <stop offset="100%" stopColor={item.color} stopOpacity="0.42" />
              </linearGradient>
            ))}
          </defs>

          {/* A short strip keeps only the ends, so the axis labels don't collide. */}
          {(plotHeight < 56 ? [0, 1] : [0, 0.5, 1]).map((fraction) => {
            const gy = bottomY - fraction * plotHeight
            return (
              <Fragment key={fraction}>
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
                  {formatAxis((yMax * fraction) / (unit?.divisor ?? 1))}
                </text>
              </Fragment>
            )
          })}
          {line && overlayMax != null && overlayLabels && (
            <text
              x={left + plotWidth}
              y={TOP - 10}
              textAnchor="end"
              className="fill-content-layout-2 text-[11px] tabular-nums"
              data-testid="load-chart-overlay-scale"
            >
              {`${width < 480 ? 'Its own scale' : 'Highlighted query on its own scale'}, 0 to ${formatValue(overlayMax)}`}
            </text>
          )}
          {unit?.unit && (
            <text
              x={left - 8}
              y={TOP - 10}
              textAnchor="end"
              className="fill-content-layout-3 text-[11px]"
              data-testid="load-chart-unit"
            >
              {unit.unit}
            </text>
          )}
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

          <g clipPath={`url(#${chartId}-plot)`}>
            {paths.map(({ item, area, line }) => (
              <g
                key={item.id}
                data-band={item.id}
                style={{
                  opacity: item.reference ? 1 : opacityFor(item.id),
                  transition: 'opacity var(--duration-fast) var(--ease-base)',
                }}
              >
                {stacked && area && (
                  <path d={area} fill={`url(#${chartId}-g-${item.id})`} />
                )}
                {line && (
                  <path
                    d={line}
                    fill="none"
                    stroke={item.color}
                    strokeWidth={
                      item.reference
                        ? 1.6
                        : raised === item.id
                          ? 2.6
                          : stacked
                            ? 1.4
                            : 2.2
                    }
                    strokeDasharray={item.reference ? '5 5' : undefined}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    vectorEffect="non-scaling-stroke"
                  />
                )}
              </g>
            ))}
            {line && overlayPath?.d ? (
              <g
                data-overlay={line.overlay.id}
                data-testid="load-chart-overlay"
                pointerEvents="none"
              >
                <path
                  d={overlayPath.d}
                  fill="none"
                  stroke="var(--color-surface-layout-1)"
                  strokeOpacity="0.9"
                  strokeWidth={5.5}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
                <path
                  d={overlayPath.d}
                  fill="none"
                  stroke={line.overlay.color}
                  strokeWidth={2.4}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </g>
            ) : null}
          </g>

          {line && overlayPath?.last
            ? (() => {
                const px = xFor(overlayPath.last.x)
                const py = overlayY(overlayPath.last.value)
                const flip = px > left + plotWidth * 0.55
                const labelY = Math.max(TOP + 10, Math.min(bottomY - 6, py - 9))
                return (
                  <g
                    pointerEvents="none"
                    data-testid="load-chart-overlay-label"
                  >
                    <circle
                      cx={px}
                      cy={py}
                      r={3.5}
                      fill={line.overlay.color}
                      stroke="var(--color-surface-layout-1)"
                      strokeWidth="1.5"
                    />
                    {overlayLabels ? (
                      <text
                        x={flip ? px - 8 : px + 8}
                        y={labelY}
                        textAnchor={flip ? 'end' : 'start'}
                        stroke="var(--color-surface-layout-1)"
                        strokeWidth="4"
                        strokeLinejoin="round"
                        paintOrder="stroke"
                        className="fill-content-layout-1 font-semibold text-[11px]"
                      >
                        {line.overlay.label.length > 48
                          ? `${line.overlay.label.slice(0, 47)}...`
                          : line.overlay.label}
                      </text>
                    ) : null}
                  </g>
                )
              })()
            : null}

          <PauseBands
            pauses={pauses}
            xFor={xFor}
            top={TOP}
            height={plotHeight}
          />
          <HighlightBand
            highlight={highlight}
            domain={domain}
            xFor={xFor}
            top={TOP}
            height={plotHeight}
          />

          {!stacked &&
            lastIndex >= 0 &&
            series.map((item, s) => {
              const value = values[s][lastIndex]
              if (value == null || item.reference) return null
              return (
                <circle
                  key={item.id}
                  cx={xFor(xs[lastIndex])}
                  cy={yFor(value)}
                  r={3.5}
                  fill={item.color}
                  style={{ opacity: opacityFor(item.id) }}
                  pointerEvents="none"
                />
              )
            })}

          {drag && Math.abs(drag.to - drag.from) > 2 && (
            <g pointerEvents="none" data-testid="load-chart-brush">
              <rect
                x={Math.min(drag.from, drag.to)}
                y={TOP}
                width={Math.abs(drag.to - drag.from)}
                height={plotHeight}
                fill="var(--color-content-layout-1)"
                fillOpacity="0.08"
              />
              {[drag.from, drag.to].map((px) => (
                <line
                  key={px === drag.from ? 'from' : 'to'}
                  x1={px}
                  x2={px}
                  y1={TOP}
                  y2={bottomY}
                  stroke="var(--color-content-layout-2)"
                  strokeWidth="1.5"
                />
              ))}
            </g>
          )}

          {hoverPx != null && !drag && (
            <g pointerEvents="none">
              <line
                x1={hoverPx}
                x2={hoverPx}
                y1={TOP}
                y2={bottomY}
                stroke="var(--color-content-layout-3)"
                strokeWidth="1"
              />
              {series.map((item, s) => {
                const value = stacked
                  ? cum[s][hoverIndex as number]
                  : values[s][hoverIndex as number]
                if (
                  value == null ||
                  (stacked && values[s][hoverIndex as number] == null)
                )
                  return null
                return (
                  <circle
                    key={item.id}
                    cx={hoverPx}
                    cy={yFor(value)}
                    r={raised === item.id ? 4 : 2.8}
                    fill={item.color}
                    stroke="var(--color-surface-layout-1)"
                    strokeWidth="1.5"
                  />
                )
              })}
              {line && overlayAt ? (
                <circle
                  cx={hoverPx}
                  cy={overlayY(overlayAt.value)}
                  r={4}
                  fill={line.overlay.color}
                  stroke="var(--color-surface-layout-1)"
                  strokeWidth="1.5"
                />
              ) : null}
            </g>
          )}

          {!hasData && (
            <text
              x={left + plotWidth / 2}
              y={TOP + plotHeight / 2}
              textAnchor="middle"
              className="fill-content-layout-3 text-[13px]"
            >
              {emptyLabel}
            </text>
          )}
        </svg>
      )}

      {showHint && pointer && cardRows.length > 0 && !drag && (
        <span
          aria-hidden="true"
          data-testid="load-chart-pin-hint"
          className="pointer-events-none absolute z-40 whitespace-nowrap rounded-control bg-content-layout-1 px-1.5 py-0.5 text-caption text-surface-layout-1 shadow-elevation-2"
          style={{
            top: pointer.py + 16,
            ...(cardOnRight
              ? { right: width - pointer.px + 6 }
              : { left: pointer.px + 6 }),
          }}
        >
          Click to pin
        </span>
      )}

      {hoverIndex != null && (cardRows.length > 0 || overlayAt) && !drag && (
        <div
          ref={cardRef}
          className={cn(
            'absolute z-30 rounded-panel border bg-surface-layout-1 px-2 py-1.5 shadow-elevation-3',
            pinned
              ? 'border-border-layout-2'
              : 'pointer-events-none border-border-layout-1'
          )}
          style={{ left: cardLeft, top: cardTop, width: cardWidth }}
          data-testid="load-chart-card"
          data-pinned={pinned || undefined}
        >
          <div className="flex items-center gap-2 px-1">
            <p className="flex-1 text-label-small text-content-layout-2">
              {hoverLabel(xs[hoverIndex], spanSeconds, binRes)}
            </p>
            {pinned ? (
              <button
                type="button"
                aria-label="Close"
                onClick={dismiss}
                className="rounded-control px-1 text-caption text-content-layout-3 hover:bg-surface-layout-2 hover:text-content-layout-1"
              >
                Close
              </button>
            ) : null}
          </div>
          {line && overlayAt ? (
            <p
              className="mt-1 flex items-center gap-2 rounded-sm bg-surface-layout-2 px-1 py-px text-label-small"
              data-testid="load-chart-card-overlay"
            >
              <span
                aria-hidden="true"
                className="inline-block h-[3px] w-2.5 shrink-0 rounded-sm"
                style={{ background: line.overlay.color }}
              />
              <span className="min-w-0 flex-1 truncate text-content-layout-1">
                {line.overlay.label}
              </span>
              <span className="shrink-0 font-semibold tabular-nums text-content-layout-1">
                {formatValue(overlayAt.value)}
              </span>
              {total != null && !hideShare ? (
                <span className="w-9 shrink-0" />
              ) : null}
            </p>
          ) : null}
          <ul className="mt-1 space-y-0.5">
            {cardRows.slice(0, 9).map(({ item, value }) => {
              const clickable =
                Boolean(onSelect) && !item.reference && item.id !== 'other'
              const body = (
                <>
                  <span
                    className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                    style={{
                      background: item.color,
                      ...(item.reference
                        ? {
                            background: 'transparent',
                            border: `1.5px dashed ${item.color}`,
                          }
                        : {}),
                    }}
                  />
                  <span
                    className={cn(
                      'min-w-0 flex-1 truncate text-left',
                      raised === item.id
                        ? 'text-content-layout-1'
                        : 'text-content-layout-2'
                    )}
                  >
                    {item.label}
                  </span>
                  <span className="shrink-0 font-semibold tabular-nums text-content-layout-1">
                    {formatValue(value as number)}
                  </span>
                  {total && !hideShare ? (
                    <span className="w-9 shrink-0 text-right text-caption tabular-nums text-content-layout-3">
                      {Math.round(((value as number) / total) * 100)}%
                    </span>
                  ) : null}
                </>
              )
              return (
                <li key={item.id}>
                  {clickable ? (
                    <button
                      type="button"
                      onClick={() => {
                        onSelect?.(item.id)
                        dismiss()
                      }}
                      onPointerEnter={() => onHover?.(item.id)}
                      className={cn(
                        'flex w-full items-center gap-2 rounded-sm px-1 py-px text-label-small transition-colors duration-fast hover:bg-surface-layout-2 focus-visible:shadow-focus focus-visible:outline-none',
                        raised === item.id && 'bg-surface-layout-2'
                      )}
                      title={`Open ${item.label}`}
                    >
                      {body}
                    </button>
                  ) : (
                    <span className="flex items-center gap-2 px-1 py-px text-label-small">
                      {body}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
          {total != null && (
            <p className="mt-1 flex items-center gap-2 border-t border-border-layout-1 px-1 pt-1 text-label-small">
              <span className="text-content-layout-2">{totalLabel}</span>
              <span className="ml-auto font-semibold tabular-nums text-content-layout-1">
                {formatValue(total)}
              </span>
              <span className="w-9 shrink-0" />
            </p>
          )}
        </div>
      )}

      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <thead>
          <tr>
            <th scope="col">Series</th>
            <th scope="col">Latest</th>
          </tr>
        </thead>
        <tbody>
          {line ? (
            <tr>
              <th scope="row">{line.overlay.label}</th>
              <td>
                {(() => {
                  const last = [...line.points]
                    .reverse()
                    .find((point) => point.value != null)?.value
                  return last == null ? 'No reading' : formatValue(last)
                })()}
              </td>
            </tr>
          ) : null}
          {series.map((item, s) => {
            const last = [...values[s]].reverse().find((value) => value != null)
            return (
              <tr key={item.id}>
                <th scope="row">{item.label}</th>
                <td>{last == null ? 'No reading' : formatValue(last)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
