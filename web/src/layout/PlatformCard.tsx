// The sidebar's way into the Readyset Platform: a database cube on a faint isometric floor with
// the cache shelf floating above it, one row lit, over the card's name and one line of copy.
import { cn } from '@rs/tailwind-base'
import { focusRingClass } from '@rs/ui-new/focus'
import { Icon } from '@rs/ui-new/icon'
import { Link } from '@tanstack/react-router'
import { useId } from 'react'
import { trackEvent } from '../lib/analytics'

// An 8 px isometric lattice through the cube's front-bottom corner; the cube's footprint covers
// exactly 4x4 cells.
const FLOOR =
  'M194.7 0L200 3M180.9 0L200 11M167 0L200 19M153.2 0L200 27M139.3 0L200 35M125.5 0L200 43M111.6 0L200 51M97.7 0L200 59M83.9 0L200 67M70 0L200 75M56.2 0L200 83M42.3 0L200 91M28.5 0L194.7 96M14.6 0L180.9 96M0.8 0L167 96M0 7.6L153.2 96M0 15.6L139.3 96M0 23.6L125.5 96M0 31.6L111.6 96M0 39.6L97.7 96M0 47.6L83.9 96M0 55.6L70 96M0 63.6L56.2 96M0 71.6L42.3 96M0 79.6L28.5 96M0 87.6L14.6 96M0 95.6L0.8 96M5.3 0L0 3M19.1 0L0 11M33 0L0 19M46.8 0L0 27M60.7 0L0 35M74.5 0L0 43M88.4 0L0 51M102.3 0L0 59M116.1 0L0 67M130 0L0 75M143.8 0L0 83M157.7 0L0 91M171.5 0L5.3 96M185.4 0L19.1 96M199.2 0L33 96M200 7.6L46.8 96M200 15.6L60.7 96M200 23.6L74.5 96M200 31.6L88.4 96M200 39.6L102.3 96M200 47.6L116.1 96M200 55.6L130 96M200 63.6L143.8 96M200 71.6L157.7 96M200 79.6L171.5 96M200 87.6L185.4 96M200 95.6L199.2 96'

// Solid tones: a token laid over the card surface at the given strength.
const tone = (token: string, percent: number) =>
  `color-mix(in srgb, var(--color-${token}) ${percent}%, var(--color-surface-layout-1))`
const EDGE = tone('content-layout-3', 22)

function PlatformArt() {
  const id = useId()
  return (
    <svg
      viewBox="0 0 200 96"
      width="200"
      height="96"
      aria-hidden="true"
      data-testid="platform-card-art"
      className="absolute top-1.5 left-1/2 -ml-25 text-series-1"
    >
      <defs>
        <radialGradient id={`${id}fade`} cx="50%" cy="50%" r="60%">
          <stop offset=".25" stopColor="white" />
          <stop offset="1" stopColor="black" />
        </radialGradient>
        <filter id={`${id}soft`} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="2.5" />
        </filter>
        <mask id={`${id}floor`}>
          <rect width="200" height="96" fill={`url(#${id}fade)`} />
          <path
            d="M100 2L132 20.6L132 27.5L100 46L68 27.5L68 20.6Z"
            fill="black"
            fillOpacity="0.7"
            filter={`url(#${id}soft)`}
          />
        </mask>
        <filter id={`${id}glow`} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.5" />
        </filter>
      </defs>
      <g mask={`url(#${id}floor)`}>
        <path
          d={FLOOR}
          className="stroke-content-layout-3"
          strokeOpacity=".13"
          strokeWidth="1"
          fill="none"
        />
      </g>
      <ellipse
        cx="100"
        cy="22.8"
        rx="20"
        ry="5"
        transform="rotate(30 100 22.8)"
        fill="currentColor"
        fillOpacity=".1"
        filter={`url(#${id}glow)`}
      />
      <g style={{ stroke: EDGE }} strokeLinejoin="round">
        <path
          d="M72.3 73.3L100 89.3L100 57.3L72.3 41.3Z"
          style={{ fill: tone('content-layout-3', 6) }}
        />
        <path
          d="M127.7 73.3L100 89.3L100 57.3L127.7 41.3Z"
          style={{ fill: tone('content-layout-3', 3) }}
        />
        <path
          d="M100 25.3L127.7 41.3L100 57.3L72.3 41.3Z"
          style={{ fill: tone('content-layout-3', 14) }}
        />
        <path
          d="M72.3 62.6L100 78.6M127.7 62.6L100 78.6M72.3 52L100 68M127.7 52L100 68M94.5 28.5L122.2 44.5M88.9 31.7L116.6 47.7M83.4 34.9L111.1 50.9M77.8 38.1L105.5 54.1"
          fill="none"
        />
      </g>
      <path
        d="M127.7 41.3L127.7 25.3M100 57.3L100 41.3M72.3 41.3L72.3 25.3"
        className="stroke-content-layout-3"
        strokeOpacity=".4"
        strokeDasharray="1 2"
        fill="none"
      />
      <g stroke="currentColor" strokeLinejoin="round">
        <path
          d="M72.3 25.3L100 41.3L100 38.8L72.3 22.8Z"
          strokeOpacity=".55"
          style={{ fill: tone('series-1', 10) }}
        />
        <path
          d="M127.7 25.3L100 41.3L100 38.8L127.7 22.8Z"
          strokeOpacity=".55"
          style={{ fill: tone('series-1', 5) }}
        />
        <path
          d="M100 6.8L127.7 22.8L100 38.8L72.3 22.8Z"
          strokeOpacity=".55"
          style={{ fill: tone('series-1', 9) }}
        />
        <path
          d="M94.5 10L122.2 26M88.9 13.2L116.6 29.2M83.4 16.4L111.1 32.4M77.8 19.6L105.5 35.6"
          strokeOpacity=".22"
          fill="none"
        />
        <path
          d="M88.9 13.2L116.6 29.2L111.1 32.4L83.4 16.4Z"
          fill="currentColor"
          fillOpacity=".2"
          strokeWidth="1.5"
        />
      </g>
    </svg>
  )
}

export function PlatformCard({ className }: { className?: string }) {
  return (
    <Link
      to="/platform"
      aria-label="Readyset Platform: serve the slow queries from a cache, no code change"
      data-testid="platform-card"
      className={cn(
        'relative block h-42 shrink-0 overflow-hidden rounded-panel border border-border-layout-soft bg-surface-layout-1 transition-[border-color] duration-150 hover:border-border-layout-2',
        focusRingClass,
        className
      )}
      onClick={() =>
        trackEvent('nav_item_clicked', { label: 'Readyset Platform' })
      }
    >
      <PlatformArt />
      <div className="absolute right-3 bottom-2.75 left-3">
        <span className="flex items-center gap-1.5 text-label-small text-content-layout-1">
          Readyset Platform
          <Icon
            name="chevron-right"
            label=""
            size="small"
            className="h-3 w-3 text-content-layout-3"
          />
        </span>
        <span className="mt-0.75 block text-caption text-content-layout-3">
          Serve slow queries from a cache, no code change.
        </span>
      </div>
    </Link>
  )
}
