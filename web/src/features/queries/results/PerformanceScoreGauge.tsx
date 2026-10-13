// The plan score: 37 ticks over a half circle light up in order to the score on open,
// neutral in color, the number in the middle and "Plan score" under it.
import { animate, m, useMotionValue, useReducedMotion } from '@rs/ui-new/motion'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { getTransition } from '@rs/ui-new/transition'
import { useEffect, useRef } from 'react'

const SEGMENT_COUNT = 37
const CENTER_X = 160
const CENTER_Y = 148
const INNER_RADIUS = 108
const OUTER_RADIUS = 136
const MARKER_RADIUS = 99
const START_ANGLE = 180
const GAUGE_DURATION_SECONDS = 1.1
const GAUGE_DELAY_SECONDS = 0.16

function polarPoint(radius: number, angle: number) {
  const radians = (angle * Math.PI) / 180
  return {
    x: CENTER_X + radius * Math.cos(radians),
    y: CENTER_Y + radius * Math.sin(radians),
  }
}

export function PerformanceScoreGauge({ score }: { score: number }) {
  const clampedScore = Math.min(100, Math.max(0, score))
  const shouldReduceMotion = useReducedMotion()
  const markerSweep = (clampedScore / 100) * 180
  const markerStart = polarPoint(MARKER_RADIUS, START_ANGLE)
  const entrance = {
    ...getTransition('cubicSlow'),
    duration: 0.7,
  }
  const gaugeTransition = {
    ...getTransition('cubicSlow'),
    duration: GAUGE_DURATION_SECONDS,
  }
  const markerRef = useRef<SVGGElement>(null)
  const scoreRef = useRef<HTMLSpanElement>(null)
  const markerMotion = useMotionValue(0)

  useEffect(() => {
    const unsubscribe = markerMotion.on('change', (angle) => {
      markerRef.current?.setAttribute(
        'transform',
        `rotate(${angle} ${CENTER_X} ${CENTER_Y})`
      )

      const animatedScore = (angle / 180) * 100
      if (scoreRef.current) {
        scoreRef.current.textContent = Math.round(animatedScore).toString()
      }
    })

    return unsubscribe
  }, [markerMotion])

  useEffect(() => {
    if (shouldReduceMotion) {
      markerMotion.set(markerSweep)
      return
    }

    markerMotion.set(0)
    const controls = animate(markerMotion, markerSweep, {
      ...gaugeTransition,
      delay: GAUGE_DELAY_SECONDS,
    })

    return () => controls.stop()
  }, [markerMotion, markerSweep, shouldReduceMotion])

  return (
    <VStack
      data-testid="plan-score"
      className="w-50 shrink-0 items-center gap-2"
    >
      <div
        role="img"
        aria-label={`Plan score ${score} out of 100`}
        className="relative w-full text-content-layout-1"
      >
        <svg aria-hidden="true" viewBox="0 0 320 165" className="block w-full">
          <path
            d="M 64 148 A 96 96 0 0 1 256 148"
            fill="none"
            strokeWidth="1"
            className="stroke-border-layout-1"
          />

          {Array.from({ length: SEGMENT_COUNT }, (_, index) => {
            const angle = 180 + (index / (SEGMENT_COUNT - 1)) * 180
            const start = polarPoint(INNER_RADIUS, angle)
            const end = polarPoint(OUTER_RADIUS, angle)
            const active = index / (SEGMENT_COUNT - 1) <= clampedScore / 100

            return (
              <m.line
                key={angle}
                x1={start.x}
                y1={start.y}
                x2={end.x}
                y2={end.y}
                strokeWidth="4"
                className={active ? 'stroke-current' : 'stroke-border-layout-2'}
                initial={
                  shouldReduceMotion
                    ? false
                    : {
                        opacity: active ? 0.12 : 0,
                      }
                }
                animate={{ opacity: 1 }}
                transition={
                  shouldReduceMotion
                    ? { duration: 0 }
                    : {
                        ...gaugeTransition,
                        delay: active
                          ? GAUGE_DELAY_SECONDS +
                            (index / (SEGMENT_COUNT - 1)) * 0.24
                          : 0,
                      }
                }
              />
            )
          })}

          <g
            ref={markerRef}
            data-testid="performance-score-marker"
            transform={`rotate(0 ${CENTER_X} ${CENTER_Y})`}
          >
            <g
              transform={`translate(${markerStart.x} ${markerStart.y}) rotate(${START_ANGLE + 90})`}
            >
              <path d="M 0 0 L -5 9 L 5 9 Z" className="fill-current" />
            </g>
          </g>
        </svg>

        <m.div
          className="absolute inset-x-0 bottom-1 flex items-baseline justify-center gap-1"
          initial={
            shouldReduceMotion
              ? false
              : {
                  opacity: 0,
                  scale: 0.96,
                  y: 4,
                }
          }
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={
            shouldReduceMotion
              ? { duration: 0 }
              : {
                  ...entrance,
                  delay: 0.2,
                }
          }
        >
          <Text
            ref={scoreRef}
            as="span"
            level="display-small"
            className="tabular-nums text-content-layout-1"
          >
            {shouldReduceMotion ? score : 0}
          </Text>
        </m.div>
      </div>

      <m.div
        initial={
          shouldReduceMotion
            ? false
            : {
                opacity: 0,
                y: 4,
              }
        }
        animate={{ opacity: 1, y: 0 }}
        transition={
          shouldReduceMotion
            ? { duration: 0 }
            : {
                ...entrance,
                delay: GAUGE_DELAY_SECONDS + GAUGE_DURATION_SECONDS,
              }
        }
      >
        <Text
          as="span"
          level="label-extra-small"
          className="font-normal text-content-layout-3"
        >
          Plan score
        </Text>
      </m.div>
    </VStack>
  )
}
