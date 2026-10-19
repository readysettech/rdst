// The tour's timeline: one clock per scene, driven by motion, that every element in the
// scene reads its pose from. Pausing the clock pauses the whole scene at once.
import { animate } from '@rs/ui-new/motion'
import { useEffect, useRef, useState } from 'react'

type Ease = (x: number) => number

export const linear: Ease = (x) => x
export const outCubic: Ease = (x) => 1 - (1 - x) ** 3
export const outQuart: Ease = (x) => 1 - (1 - x) ** 4
export const inOutCubic: Ease = (x) =>
  x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2
/** A small overshoot, for things that land. */
export const outBack: Ease = (x) => {
  const c1 = 1.4
  const c3 = c1 + 1
  return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2
}

/** 0 to 1 as `t` moves from `a` to `b` seconds, eased and clamped. */
export function ramp(t: number, a: number, b: number, ease: Ease = outCubic) {
  if (t <= a) return 0
  if (t >= b) return 1
  return ease((t - a) / (b - a))
}

/** Fades in over [a, a + fade], holds, and fades out over [b - fade, b]. */
export function hold(t: number, a: number, b: number, fade = 0.3) {
  return Math.min(ramp(t, a, a + fade), 1 - ramp(t, b - fade, b, linear))
}

/** A gentle entrance: opacity and a short rise, both from one 0..1 value. */
export function rise(p: number, distance = 8) {
  return { opacity: p, transform: `translateY(${(1 - p) * distance}px)` }
}

/** The first `p` share of `text`, as if typed. */
export function typed(text: string, p: number) {
  return text.slice(0, Math.round(text.length * Math.min(1, Math.max(0, p))))
}

/** Counts from `from` to `to` over [a, b]. */
export function count(
  t: number,
  a: number,
  b: number,
  from: number,
  to: number
) {
  return from + (to - from) * ramp(t, a, b, outQuart)
}

/**
 * Seconds into the current scene. Restarts at 0 whenever `scene` changes, stops at
 * `duration` and calls `onDone`. Paused, it holds where it is. With reduced motion it
 * sits on the scene's final pose and never finishes on its own.
 */
export function useSceneClock({
  scene,
  duration,
  paused,
  reduced,
  onDone,
}: {
  scene: number
  duration: number
  paused: boolean
  reduced: boolean
  onDone: () => void
}): number {
  const [time, setTime] = useState(reduced ? duration : 0)
  const controls = useRef<ReturnType<typeof animate> | null>(null)
  const done = useRef(onDone)
  const pausedRef = useRef(paused)
  useEffect(() => {
    done.current = onDone
    pausedRef.current = paused
  })

  // A new scene restarts the clock; `paused` is read once here and kept up below.
  useEffect(() => {
    if (reduced) {
      setTime(duration)
      return
    }
    setTime(0)
    const playback = animate(0, duration, {
      duration,
      ease: 'linear',
      onUpdate: setTime,
      onComplete: () => done.current(),
    })
    if (pausedRef.current) playback.pause()
    controls.current = playback
    return () => {
      playback.stop()
      if (controls.current === playback) controls.current = null
    }
  }, [scene, duration, reduced])

  useEffect(() => {
    if (paused) controls.current?.pause()
    else controls.current?.play()
  }, [paused])

  return time
}
