import * as RadixProgress from '@radix-ui/react-progress'
import { tv } from '@rs/tailwind-base'

export const progressRecipe = tv({
  slots: {
    // The track measures the wait against its container, so the fill reads
    // against a full-width reference; a caller that wants a narrower bar
    // sizes it through `className`.
    container: [
      'relative',
      'overflow-hidden',
      'bg-border-layout-1',
      'rounded-full',
      'w-full',
      'h-4',
    ],
    indicator: [
      'bg-content-rising-plain',
      'rounded-full',
      'w-full',
      'h-full',
      'transition-all',
      'duration-slower',
      'ease-base',
      'motion-reduce:transition-none',
    ],
  },
  variants: {
    mode: {
      determinate: {},
      // A wait with no known end is striped rather than filled, so a static
      // track under reduced motion is never read as a completed one; the
      // stripes breathe where motion is welcome.
      indeterminate: {
        indicator: [
          'bg-transparent',
          'bg-[repeating-linear-gradient(45deg,var(--color-content-rising-plain)_0_6px,transparent_6px_12px)]',
          'opacity-70',
          'motion-safe:animate-pulse',
        ],
      },
    },
    // `quiet`: a 4px track with a first-tone fill, so a wait reads as status, not accent.
    tone: {
      default: {},
      quiet: { container: ['h-1'], indicator: ['bg-content-layout-1'] },
    },
  },
  compoundVariants: [
    {
      tone: 'quiet',
      mode: 'indeterminate',
      class: {
        indicator: [
          'bg-transparent',
          'bg-[repeating-linear-gradient(45deg,var(--color-content-layout-1)_0_6px,transparent_6px_12px)]',
        ],
      },
    },
  ],
  defaultVariants: {
    mode: 'determinate',
    tone: 'default',
  },
})

type ProgressProps = {
  /** Completed amount. Omit for a wait whose end is not yet known. */
  value?: number
  /** The total `value` counts towards. */
  max?: number
  /** Accessible name, for a bar the surrounding copy does not already name. */
  label?: string
  /** `quiet`: 4px track, first-tone fill. */
  tone?: 'default' | 'quiet'
  className?: string
}

export const Progress = ({
  value,
  max = 100,
  label,
  tone,
  className,
}: ProgressProps) => {
  const indeterminate = value === undefined
  const percent = indeterminate
    ? 0
    : Math.min(100, Math.max(0, (value / Math.max(1, max)) * 100))
  const { container, indicator } = progressRecipe({
    mode: indeterminate ? 'indeterminate' : 'determinate',
    tone,
  })
  return (
    <RadixProgress.Root
      className={container({ className })}
      value={indeterminate ? null : value}
      max={max}
      aria-label={label}
    >
      <RadixProgress.Indicator
        className={indicator()}
        style={
          indeterminate
            ? undefined
            : { transform: `translateX(-${100 - percent}%)` }
        }
      />
    </RadixProgress.Root>
  )
}
