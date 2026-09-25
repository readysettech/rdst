// The finish line: a large green check that scales in, draws its tick and sends
// one ring out. Reduced motion shows the finished mark with a fade.
import { cn } from '@rs/tailwind-base'
import { m, useReducedMotion } from '@rs/ui-new/motion'

export function ReadyMark({
  size = 'large',
  className,
}: {
  size?: 'large' | 'small'
  className?: string
}) {
  const reduced = useReducedMotion()
  const box = size === 'large' ? 'h-24 w-24' : 'h-12 w-12'
  const glyph = size === 'large' ? 'h-12 w-12' : 'h-6 w-6'
  return (
    <div
      role="img"
      aria-label="All checks passed"
      className={cn('relative grid place-items-center', box, className)}
      data-testid="prereq-ready-mark"
    >
      {!reduced && (
        <>
          <m.span
            aria-hidden="true"
            className="absolute inset-0 rounded-pill border-2 border-border-positive-solid"
            initial={{ scale: 0.7, opacity: 0.7 }}
            animate={{ scale: 1.9, opacity: 0 }}
            transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1], delay: 0.35 }}
          />
          <m.span
            aria-hidden="true"
            className="absolute inset-0 rounded-pill bg-surface-positive-soft"
            initial={{ scale: 0.8, opacity: 0.9 }}
            animate={{ scale: 1.45, opacity: 0 }}
            transition={{ duration: 1.4, ease: [0.16, 1, 0.3, 1], delay: 0.2 }}
          />
        </>
      )}
      <m.div
        aria-hidden="true"
        className={cn(
          'grid place-items-center rounded-pill bg-surface-positive-solid shadow-glow-positive',
          box
        )}
        initial={reduced ? { opacity: 0 } : { scale: 0.4, opacity: 0 }}
        animate={reduced ? { opacity: 1 } : { scale: 1, opacity: 1 }}
        transition={
          reduced
            ? { duration: 0.2 }
            : { type: 'spring', stiffness: 260, damping: 18, mass: 0.9 }
        }
      >
        <svg
          viewBox="0 0 48 48"
          fill="none"
          className={cn(glyph, 'text-content-positive-solid')}
        >
          <m.path
            d="M13 25.5 L20.5 33 L35.5 16"
            stroke="currentColor"
            strokeWidth={size === 'large' ? 4.5 : 5}
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={reduced ? { pathLength: 1 } : { pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{
              duration: 0.45,
              ease: [0.65, 0, 0.35, 1],
              delay: 0.25,
            }}
          />
        </svg>
      </m.div>
    </div>
  )
}
