import { cn } from '@rs/tailwind-base'
import { m, useMotionValue, useSpring } from 'motion/react'
import { useEffect, useMemo, useRef } from 'react'
import { getTransition } from '../../motion/transition'

type CircularProgressProps = {
  value: number
  max: number
  size?: number
  strokeWidth?: number
}

export const CircularProgress = ({
  value,
  max,
  size = 60,
  strokeWidth = 8,
}: CircularProgressProps) => {
  const ref = useRef<SVGTextElement>(null)

  const radius = useMemo(() => (size - strokeWidth) / 2, [size, strokeWidth])
  const dashArray = useMemo(() => radius * 2 * Math.PI, [radius])
  const isEmpty = useMemo(() => max <= 0, [max])
  const safeValue = useMemo(
    () => (isEmpty ? 0 : Math.max(0, Math.min(value, max))),
    [isEmpty, value, max]
  )
  const dashOffset = useMemo(
    () =>
      isEmpty
        ? dashArray
        : safeValue >= max
          ? 0
          : dashArray - (dashArray * safeValue) / max,
    [isEmpty, safeValue, max, dashArray]
  )
  const percentage = useMemo(
    () => (isEmpty ? 0 : Math.min(100, Math.round((safeValue / max) * 100))),
    [isEmpty, safeValue, max]
  )

  const transition = getTransition('springSlow')

  const motionValue = useMotionValue(0)
  const springValue = useSpring(motionValue, transition)

  useEffect(() => {
    motionValue.set(percentage)
  }, [motionValue, percentage])

  useEffect(() => {
    const unsubscribe = springValue.on('change', (latest) => {
      if (ref.current) {
        if (Number.isNaN(latest)) {
          ref.current.textContent = '0%'
          return
        } else {
          ref.current.textContent = `${latest.toFixed(0)}%`
          return
        }
      }
    })

    return () => {
      unsubscribe()
    }
  }, [springValue])

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        strokeWidth={`${strokeWidth}px`}
        r={radius}
        className={cn('fill-none', 'stroke-border-layout-1')}
      />
      <m.circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        strokeWidth={`${strokeWidth}px`}
        initial={{
          strokeDasharray: dashArray,
          strokeDashoffset: dashArray,
          stroke: 'var(--color-content-rising-plain)',
        }}
        animate={{
          strokeDasharray: dashArray,
          strokeDashoffset: dashOffset,
          stroke:
            percentage >= 80
              ? 'var(--color-content-negative-plain)'
              : 'var(--color-content-rising-plain)',
        }}
        transition={transition}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        className={'fill-none'}
      />
      <text
        ref={ref}
        x="50%"
        y="50%"
        dy=".3em"
        textAnchor="middle"
        className={cn(
          'text-subtitle-1',
          'text-content-layout-1',
          'select-none',
          'fill-current'
        )}
      />
    </svg>
  )
}
