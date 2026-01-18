import { cubicBezier } from 'motion/react'

const transitions = {
  // springFast: {
  //   type: 'spring', // 260ms
  //   stiffness: 880,
  //   damping: 54,
  //   mass: 1,
  // },
  springFast: {
    type: 'spring', // 260ms
    stiffness: 300,
    damping: 32,
    mass: 1,
  },
  springMedium: {
    type: 'spring', // 640ms
    stiffness: 880,
    damping: 95,
    mass: 1,
  },
  springSlow: {
    type: 'spring', // 1200ms
    stiffness: 81,
    damping: 20,
    mass: 1,
  },
  cubicFast: {
    duration: 0.3,
    ease: cubicBezier(0.85, 0, 0.15, 1),
  },
  cubicSlow: {
    duration: 0.5,
    ease: cubicBezier(0.32, 0.72, 0, 1),
  },
  linear: {
    repeat: Number.POSITIVE_INFINITY,
    duration: 0.4,
    ease: 'linear',
  },
} as const

type TransitionKey = keyof typeof transitions

export const getTransition = <T extends TransitionKey>(
  type?: T
): (typeof transitions)[T] => {
  return transitions[type ?? 'cubicFast'] as (typeof transitions)[T]
}
