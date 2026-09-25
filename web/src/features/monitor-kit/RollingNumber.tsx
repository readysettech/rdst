// RollingNumber - a live figure whose changed characters roll in (300 ms ease-out;
// a crossfade under reduced motion). Unchanged characters stay still.
//
// Usage: <RollingNumber value="34.7" className="text-headline-2 tabular-nums" />
import { cn } from '@rs/tailwind-base'
import { useState } from 'react'

export function RollingNumber({
  value,
  className,
}: {
  value: string
  className?: string
}) {
  // Derived-from-props state: remember the previous value when it changes.
  const [seen, setSeen] = useState({ value, before: value, generation: 0 })
  if (seen.value !== value)
    setSeen({ value, before: seen.value, generation: seen.generation + 1 })
  const before = seen.value !== value ? seen.value : seen.before
  const generation =
    seen.value !== value ? seen.generation + 1 : seen.generation
  const chars = value.split('')
  // Align from the right so '9.8' -> '10.1' rolls the digits that moved.
  const offset = before.length - value.length
  return (
    <span className={cn('inline-flex overflow-hidden', className)}>
      <span className="sr-only">{value}</span>
      {chars.map((char, index) => {
        const changed = before !== value && before[index + offset] !== char
        return (
          <span
            key={changed ? `${index}-${generation}` : `${index}-${char}`}
            aria-hidden="true"
            className={changed ? 'mk-roll' : 'inline-block'}
          >
            {char}
          </span>
        )
      })}
    </span>
  )
}
