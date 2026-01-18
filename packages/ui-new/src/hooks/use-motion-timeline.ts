import {
  type AnimationOptions,
  type DOMKeyframesDefinition,
  type ElementOrSelector,
  useAnimate,
} from 'motion/react'
import { useCallback, useEffect, useRef } from 'react'

type AnimateParams = [
  ElementOrSelector,
  DOMKeyframesDefinition,
  (AnimationOptions | undefined)?,
]

type Animation = AnimateParams | Animation[]

export const useMotionTimeline = (keyframes: Animation[], count = 1) => {
  const mounted = useRef(true)

  const [scope, animate] = useAnimate()

  const processAnimation = useCallback(
    async (animation: Animation) => {
      if (Array.isArray(animation[0])) {
        await Promise.all(
          (animation as Animation[]).map(async (a) => {
            await processAnimation(a as Animation)
          })
        )
      } else {
        await animate(...(animation as AnimateParams))
      }
    },
    [animate]
  )

  const handleAnimate = useCallback(async () => {
    for (let i = 0; i < count; i += 1) {
      // eslint-disable-next-line no-restricted-syntax
      for (const animation of keyframes) {
        if (!mounted.current) return
        // eslint-disable-next-line no-await-in-loop
        await processAnimation(animation)
      }
    }
  }, [keyframes, count, processAnimation])

  useEffect(() => {
    mounted.current = true

    handleAnimate()

    return () => {
      mounted.current = false
    }
  }, [handleAnimate])

  return scope
}
