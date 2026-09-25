// A shaded time window on a chart: the incident an Inbox item is about. Charts take it as
// a prop, or from ChartHighlightContext so a page can shade every chart inside it.
import { createContext, useContext } from 'react'

export interface ChartHighlight {
  start: number
  /** Null while it lasts: the shading runs to the chart's right edge. */
  end: number | null
  /** Read by assistive tech and on hover ('Traffic up 3x at 9:42 PM'). */
  label?: string
}

export const ChartHighlightContext = createContext<ChartHighlight | null>(null)

/** The chart's own highlight when given (null turns it off), else the page's. */
export function useChartHighlight(
  own?: ChartHighlight | null
): ChartHighlight | null {
  const shared = useContext(ChartHighlightContext)
  return own === undefined ? shared : own
}

const TINT = 'var(--color-content-warning-plain)'

/**
 * The window clipped to the plot: a light tint, a bar along the top and a dashed line
 * at the onset. Nothing when the window is outside the domain.
 */
export function HighlightBand({
  highlight,
  domain,
  xFor,
  top,
  height,
  bar = 3,
}: {
  highlight: ChartHighlight | null
  domain: [number, number]
  xFor: (x: number) => number
  top: number
  height: number
  /** Height of the top bar in px; 0 leaves it out (sparklines). */
  bar?: number
}) {
  if (!highlight) return null
  const [x0, x1] = domain
  const from = Math.max(highlight.start, x0)
  const to = Math.min(highlight.end ?? x1, x1)
  if (to <= from) return null
  const left = xFor(from)
  const width = Math.max(2, xFor(to) - left)
  const onset = highlight.start >= x0 && highlight.start <= x1
  return (
    <g pointerEvents="none" data-testid="chart-highlight">
      {highlight.label && <title>{highlight.label}</title>}
      <rect
        x={left}
        y={top}
        width={width}
        height={height}
        fill={TINT}
        fillOpacity="0.1"
      />
      {bar > 0 && (
        <rect
          x={left}
          y={top}
          width={width}
          height={bar}
          fill={TINT}
          fillOpacity="0.7"
          rx={bar / 2}
        />
      )}
      {onset && (
        <line
          x1={left}
          x2={left}
          y1={top}
          y2={top + height}
          stroke={TINT}
          strokeOpacity="0.8"
          strokeWidth="1.25"
          strokeDasharray="3 3"
        />
      )}
    </g>
  )
}
