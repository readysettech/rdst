// Hatched bands over spans with no readings, labelled with the reason (pauseModel).
import { useId } from 'react'
import { type ChartPause, PAUSE_LABEL } from './pauseModel'

const LABEL_MIN_WIDTH = 280
const SHORT_LABEL_MIN_WIDTH = 120

export function PauseBands({
  pauses,
  xFor,
  top,
  height,
}: {
  pauses: ChartPause[] | undefined
  xFor: (x: number) => number
  top: number
  height: number
}) {
  const id = `pause-hatch-${useId().replaceAll(':', '')}`
  if (!pauses?.length) return null
  return (
    <g pointerEvents="none" data-testid="chart-pauses">
      <defs>
        <pattern
          id={id}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <line
            x1="0"
            y1="0"
            x2="0"
            y2="6"
            stroke="var(--color-content-layout-3)"
            strokeWidth="1.5"
            strokeOpacity="0.35"
          />
        </pattern>
      </defs>
      {pauses.map((pause) => {
        const x = xFor(pause.from)
        const width = Math.max(2, xFor(pause.to) - x)
        const label = PAUSE_LABEL[pause.cause]
        return (
          <g
            key={`${pause.cause}-${pause.from}`}
            data-testid="chart-pause"
            data-cause={pause.cause}
          >
            <title>{label}</title>
            <rect
              x={x}
              y={top}
              width={width}
              height={height}
              className="fill-surface-layout-2"
              fillOpacity="0.55"
            />
            <rect
              x={x}
              y={top}
              width={width}
              height={height}
              fill={`url(#${id})`}
            />
            {width >= SHORT_LABEL_MIN_WIDTH && width < LABEL_MIN_WIDTH && (
              <text
                x={x + width / 2}
                y={top + 14}
                textAnchor="middle"
                className="fill-content-layout-2 text-[11px]"
              >
                {pause.cause === 'unreachable' ? 'Monitoring paused' : 'No readings'}
              </text>
            )}
            {width >= LABEL_MIN_WIDTH && (
              <foreignObject x={x + 6} y={top + 6} width={width - 12} height={24}>
                <p className="truncate text-label-small leading-tight text-content-layout-2">
                  {pause.cause === 'unreachable' ? (
                    <>
                      <span className="font-medium text-content-layout-1">
                        Monitoring paused:
                      </span>{' '}
                      RDST couldn't reach the database
                    </>
                  ) : (
                    label
                  )}
                </p>
              </foreignObject>
            )}
          </g>
        )
      })}
    </g>
  )
}
