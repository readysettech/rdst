/**
 * The one keyboard focus treatment in the design system.
 *
 * Every interactive primitive spreads `focusRing`, so a control that takes
 * focus looks the same wherever it is used. Pair it with `controlTransition`
 * rather than a bare `transition`: the ring is painted with `box-shadow`, and
 * a transition that covers `box-shadow` leaves the ring fading in behind the
 * caret during fast Tab traversal.
 */
export const focusRing = [
  'focus-visible:outline-none',
  'focus-visible:ring-2',
  'focus-visible:ring-border-primary-soft',
  'focus-visible:ring-offset-2',
  'focus-visible:ring-offset-surface-layout-1',
] as const

/** `focusRing` as a single class string, for `cn()` call sites. */
export const focusRingClass = focusRing.join(' ')

/**
 * The properties a control may animate on hover, press and selection.
 * `box-shadow` and `outline` are absent by design — see `focusRing`.
 */
export const controlTransition =
  'transition-[background-color,border-color,color,opacity,transform]'
