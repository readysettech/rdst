import { m, useReducedMotion } from '@rs/ui-new/motion'
import type { CSSProperties } from 'react'

const noiseTexture =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")"

export type AnimatedSurfacePalette =
  | 'purple'
  | 'orange'
  | 'green'
  | 'blue'
  | 'red'

const animatedSurfacePalettes = {
  purple: {
    base: 'var(--color-surface-rising-solid)',
    glow: 'var(--color-surface-rising-solid-hover)',
    light: 'var(--neutral-white)',
    shadow: 'black',
    noiseOpacity: 0.38,
    foreground: 'text-content-rising-solid',
    foregroundMuted: 'text-content-rising-solid/90',
  },
  orange: {
    base: 'var(--color-surface-warning-solid)',
    glow: 'var(--color-surface-warning-solid-hover)',
    light: 'var(--neutral-white)',
    shadow: 'var(--color-surface-warning-solid-active)',
    noiseOpacity: 0.3,
    foreground: 'text-content-warning-solid',
    foregroundMuted: 'text-content-warning-solid/80',
  },
  green: {
    base: 'var(--color-surface-positive-solid)',
    glow: 'var(--color-surface-positive-solid-hover)',
    light: 'var(--neutral-white)',
    shadow: 'var(--color-surface-positive-solid-active)',
    noiseOpacity: 0.28,
    foreground: 'text-content-positive-solid',
    foregroundMuted: 'text-content-positive-solid/80',
  },
  blue: {
    base: 'var(--color-surface-info-solid)',
    glow: 'var(--color-surface-info-solid-hover)',
    light: 'var(--neutral-white)',
    shadow: 'var(--color-surface-info-solid-active)',
    noiseOpacity: 0.32,
    foreground: 'text-content-info-solid',
    foregroundMuted: 'text-content-info-solid/80',
  },
  red: {
    base: 'var(--color-surface-negative-solid)',
    glow: 'var(--color-surface-negative-solid-hover)',
    light: 'var(--neutral-white)',
    shadow: 'var(--color-surface-negative-solid-active)',
    noiseOpacity: 0.3,
    foreground: 'text-content-negative-solid',
    foregroundMuted: 'text-content-negative-solid/80',
  },
} as const satisfies Record<
  AnimatedSurfacePalette,
  {
    base: string
    glow: string
    light: string
    shadow: string
    noiseOpacity: number
    foreground: string
    foregroundMuted: string
  }
>

export function getAnimatedSurfaceForegroundClasses(
  palette: AnimatedSurfacePalette
) {
  const { foreground, foregroundMuted } = animatedSurfacePalettes[palette]
  return { foreground, foregroundMuted }
}

function AnimatedNoiseTexture({
  shouldReduceMotion,
  opacity,
}: {
  shouldReduceMotion: boolean | null
  opacity: number
}) {
  return (
    <m.div
      className="absolute -inset-4 mix-blend-overlay transform-gpu"
      style={{
        opacity,
        backgroundImage: noiseTexture,
        backgroundSize: '160px 160px',
      }}
      animate={
        shouldReduceMotion
          ? undefined
          : {
              x: [0, -7, 5, 0],
              y: [0, 5, -6, 0],
              opacity: [opacity * 0.9, opacity * 1.1, opacity * 0.95, opacity],
            }
      }
      transition={{
        duration: 16,
        ease: 'easeInOut',
        repeat: Number.POSITIVE_INFINITY,
      }}
    />
  )
}

export function AnimatedSurfaceBackdrop({
  palette = 'purple',
}: {
  palette?: AnimatedSurfacePalette
}) {
  const shouldReduceMotion = useReducedMotion()
  const paletteConfig = animatedSurfacePalettes[palette]
  const paletteVariables = {
    '--animated-surface-base': paletteConfig.base,
    '--animated-surface-glow': paletteConfig.glow,
    '--animated-surface-light': paletteConfig.light,
    '--animated-surface-shadow': paletteConfig.shadow,
  } as CSSProperties

  return (
    <div
      aria-hidden="true"
      data-testid="animated-rising-surface-backdrop"
      data-palette={palette}
      className="pointer-events-none absolute inset-0 overflow-hidden"
      style={paletteVariables}
    >
      <div
        className="absolute inset-0"
        style={{ background: 'var(--animated-surface-base)' }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(115deg, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-shadow) 20%) 0%, transparent 46%, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-shadow) 12%) 100%)',
        }}
      />
      <m.div
        className="absolute -inset-[34%] transform-gpu"
        style={{
          background:
            'radial-gradient(48% 56% at 72% 30%, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-light) 44%) 0%, transparent 72%)',
          mixBlendMode: 'screen',
        }}
        animate={
          shouldReduceMotion
            ? undefined
            : {
                x: ['-9%', '8%', '2%', '-9%'],
                y: ['-7%', '6%', '10%', '-7%'],
                rotate: [-3, 4, -1, -3],
                scale: [1, 1.1, 1.04, 1],
                opacity: [0.72, 0.94, 0.8, 0.72],
              }
        }
        transition={{
          duration: 18,
          ease: 'easeInOut',
          repeat: Number.POSITIVE_INFINITY,
        }}
      />
      <m.div
        className="absolute -inset-[32%] transform-gpu"
        style={{
          background:
            'radial-gradient(46% 58% at 26% 72%, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-glow) 38%) 0%, transparent 70%)',
          mixBlendMode: 'soft-light',
        }}
        animate={
          shouldReduceMotion
            ? undefined
            : {
                x: ['9%', '-7%', '-1%', '9%'],
                y: ['8%', '-8%', '-3%', '8%'],
                rotate: [3, -5, 2, 3],
                scale: [1.05, 0.97, 1.11, 1.05],
                opacity: [0.72, 0.9, 0.76, 0.72],
              }
        }
        transition={{
          duration: 23,
          ease: 'easeInOut',
          repeat: Number.POSITIVE_INFINITY,
        }}
      />
      <m.div
        className="absolute -inset-[38%] transform-gpu"
        style={{
          background:
            'conic-gradient(from 205deg at 52% 50%, transparent 0deg, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-light) 24%) 68deg, transparent 142deg, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-shadow) 38%) 224deg, transparent 308deg)',
          filter: 'blur(26px)',
          mixBlendMode: 'soft-light',
        }}
        animate={
          shouldReduceMotion
            ? undefined
            : {
                x: ['-3%', '7%', '-8%', '-3%'],
                y: ['6%', '-5%', '2%', '6%'],
                rotate: [-7, 6, -2, -7],
                scale: [1.08, 1, 1.12, 1.08],
                opacity: [0.48, 0.7, 0.56, 0.48],
              }
        }
        transition={{
          duration: 29,
          ease: 'easeInOut',
          repeat: Number.POSITIVE_INFINITY,
        }}
      />
      <m.div
        className="absolute -inset-y-[90%] left-[-35%] w-[28%] transform-gpu"
        style={{
          background:
            'linear-gradient(90deg, transparent 0%, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-light) 44%) 50%, transparent 100%)',
          filter: 'blur(22px)',
          opacity: 0.24,
        }}
        animate={
          shouldReduceMotion
            ? undefined
            : {
                x: ['0%', '520%'],
                rotate: [14, 18],
                opacity: [0, 0.26, 0.2, 0],
              }
        }
        transition={{
          duration: 14,
          ease: 'easeInOut',
          repeat: Number.POSITIVE_INFINITY,
          repeatDelay: 3,
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(180deg, transparent 45%, color-mix(in oklab, var(--animated-surface-base), var(--animated-surface-shadow) 20%) 100%)',
        }}
      />
      <AnimatedNoiseTexture
        shouldReduceMotion={shouldReduceMotion}
        opacity={paletteConfig.noiseOpacity}
      />
      <div
        className="absolute inset-x-0 top-0 h-px"
        style={{
          background:
            'color-mix(in oklab, var(--animated-surface-light), transparent 80%)',
        }}
      />
    </div>
  )
}
