// DatabaseGlyph - the animated database mark (brief 3.1). A cylinder in IconTile
// proportions; query streaks fall into its mouth at a rate proportional to
// log10(1 + qps) (at most 12), the fill level is connections / max_connections, and
// a soft halo carries the health tone. It pulses once per heartbeat, speeds up on
// hover, pauses while the tab is hidden, and is static under reduced motion.
//
// Usage:
//   <DatabaseGlyph qps={34.7} fill={9 / 100} tone="positive" beat={pulse.beat} />
//   <DatabaseGlyph size={96} tone="neutral" />            // empty states
import { cn } from '@rs/tailwind-base'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useEffect, useId, useState } from 'react'
import { SERIES_COLOR } from './charts/chartTheme'
import { formatQps } from './format'

export type GlyphTone = 'positive' | 'warning' | 'negative' | 'neutral'

export interface DatabaseGlyphProps {
  size?: number
  /** Current queries per second; 0 or undefined shows a still glyph. */
  qps?: number | null
  /** 0..1, connections in use over the connection limit. */
  fill?: number | null
  tone?: GlyphTone
  /** Increments on every heartbeat; each change pulses the glyph once. */
  beat?: number
  /** Tooltip; defaults to '34.7 queries per second right now'. */
  label?: string
  /** Round tile (inside PulseHalo); the default is the rounded square. */
  round?: boolean
  /** Skip the built-in tooltip (a wrapper shows its own). */
  noTooltip?: boolean
  className?: string
}

const HALO: Record<GlyphTone, string> = {
  positive: 'shadow-glow-positive border-border-positive-soft',
  warning: 'shadow-glow-warning border-border-warning-soft',
  negative: 'shadow-glow-negative border-border-negative-soft',
  neutral: 'border-border-layout-1',
}

// Fixed particle lanes so the stream reads as random but renders deterministically.
const LANES = [
  { x: 20, delay: 0 },
  { x: 27, delay: 0.43 },
  { x: 23.5, delay: 0.21 },
  { x: 30, delay: 0.66 },
  { x: 18, delay: 0.9 },
  { x: 25, delay: 0.12 },
  { x: 29, delay: 0.34 },
  { x: 21.5, delay: 0.78 },
  { x: 26, delay: 0.55 },
  { x: 19, delay: 0.29 },
  { x: 28, delay: 0.95 },
  { x: 24, delay: 0.62 },
]

function useDocumentHidden() {
  const [hidden, setHidden] = useState(
    () => typeof document !== 'undefined' && document.hidden
  )
  useEffect(() => {
    const update = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  return hidden
}

export function DatabaseGlyph({
  size = 48,
  qps,
  fill,
  tone = 'positive',
  beat = 0,
  label,
  round = false,
  noTooltip = false,
  className,
}: DatabaseGlyphProps) {
  const clipId = useId().replaceAll(':', '')
  const hidden = useDocumentHidden()
  const [hovered, setHovered] = useState(false)
  const rate = Math.max(0, qps ?? 0)
  const count = Math.min(12, Math.round(Math.log10(1 + rate) * 4))
  // Faster streams for busier databases; hover briefly doubles the pace.
  const baseDuration = Math.max(0.7, 2.2 - Math.log10(1 + rate) * 0.45)
  const duration = hovered ? baseDuration / 2 : baseDuration
  const level = Math.min(1, Math.max(0, fill ?? 0))
  // Body spans y 15..37; the liquid rises from the bottom.
  const liquidTop = 37 - level * 22
  const tooltip =
    label ??
    (qps == null
      ? 'Waiting for the first reading'
      : `${formatQps(rate)} queries per second right now`)

  const glyph = (
    <div
      className={cn(
        'relative inline-flex shrink-0 items-center justify-center border bg-surface-layout-2 transition-shadow duration-slow ease-out',
        round ? 'rounded-full' : 'rounded-2xl',
        HALO[tone],
        hidden && 'mk-glyph-paused',
        className
      )}
      style={{ width: size, height: size }}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
    >
      <svg
        viewBox="0 0 48 48"
        width={size}
        height={size}
        role="img"
        aria-label={tooltip}
        className="overflow-visible"
      >
        <defs>
          <clipPath id={`${clipId}-body`}>
            <path d="M12 15V36.5C12 39 17.4 41 24 41C30.6 41 36 39 36 36.5V15C36 17.5 30.6 19.5 24 19.5C17.4 19.5 12 17.5 12 15Z" />
          </clipPath>
          <clipPath id={`${clipId}-sky`}>
            <rect x="0" y="0" width="48" height="16" />
          </clipPath>
        </defs>

        <g clipPath={`url(#${clipId}-sky)`}>
          {LANES.slice(0, count).map((lane) => (
            <line
              key={lane.x}
              x1={lane.x}
              x2={lane.x}
              y1={5}
              y2={9}
              stroke={SERIES_COLOR.throughput}
              strokeWidth="1.4"
              strokeLinecap="round"
              className="mk-particle"
              style={{
                animationDuration: `${duration}s`,
                animationDelay: `${lane.delay * duration}s`,
              }}
            />
          ))}
        </g>

        <g key={beat} className={beat ? 'mk-glyph-beat' : undefined}>
          <g clipPath={`url(#${clipId}-body)`}>
            <rect
              x="10"
              y={liquidTop}
              width="28"
              height={42 - liquidTop}
              fill={SERIES_COLOR.connections}
              opacity="0.28"
              style={{
                transition:
                  'y 500ms var(--ease-out), height 500ms var(--ease-out)',
              }}
            />
            <ellipse
              cx="24"
              cy={liquidTop}
              rx="12"
              ry="3.6"
              fill={SERIES_COLOR.connections}
              opacity="0.4"
              style={{ transition: 'cy 500ms var(--ease-out)' }}
            />
          </g>
          <path
            d="M12 15V36.5C12 39 17.4 41 24 41C30.6 41 36 39 36 36.5V15"
            fill="none"
            stroke="var(--color-content-layout-2)"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
          <path
            d="M12 25.5C12 28 17.4 30 24 30C30.6 30 36 28 36 25.5"
            fill="none"
            stroke="var(--color-content-layout-3)"
            strokeWidth="1.2"
            opacity="0.7"
          />
          <ellipse
            cx="24"
            cy="15"
            rx="12"
            ry="4.5"
            fill="var(--color-surface-layout-1)"
            stroke="var(--color-content-layout-1)"
            strokeWidth="1.8"
          />
          <ellipse
            cx="24"
            cy="15"
            rx="6"
            ry="1.8"
            fill={SERIES_COLOR.throughput}
            opacity={count ? 0.5 : 0.15}
          />
        </g>
      </svg>
    </div>
  )

  if (noTooltip) return glyph
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>{glyph}</TooltipTrigger>
        <TooltipContent label={tooltip} />
      </Tooltip>
    </TooltipProvider>
  )
}
