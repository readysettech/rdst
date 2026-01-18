import type { Variants } from 'motion/react'

const baseVariants = {
  scale: {
    initial: 0.95,
    animate: 1,
    exit: 0.95,
  },
  opacity: {
    initial: 0,
    animate: 1,
    exit: 0,
  },
  y: {
    initial: 24,
    animate: 0,
    exit: 24,
  },
  pathLength: {
    initial: 0,
    animate: 1,
    exit: 0,
  },
  filter: {
    initial: 'blur(8px)',
    animate: 'blur(0px)',
    exit: 'blur(8px)',
  },
}

export const getVariants = (
  ...properties: (keyof typeof baseVariants)[]
): Variants => {
  const initial: Record<string, any> = {}
  const animate: Record<string, any> = {}
  const exit: Record<string, any> = {}

  properties.forEach((property) => {
    const variant = baseVariants[property]
    initial[property] = variant.initial
    animate[property] = variant.animate
    exit[property] = variant.exit
  })

  return { initial, animate, exit }
}
