import { tv } from '@rs/tailwind-base'

/**
 * The inline-alert line behind `tone="quiet"` on ErrorState, InlineNotice and Alert: one
 * row on the sunk tone with no border, a 14px glyph, and the status tone carried by the
 * glyph and the lead sentence only.
 */
export const quietLineRecipe = tv({
  slots: {
    root: [
      'flex',
      'items-start',
      'gap-2.5',
      'rounded-panel',
      'bg-surface-layout-2',
      'px-3.5',
      'py-3',
      'text-body-small',
      'text-content-layout-2',
    ],
    // Centred on the first 24px line of body-small.
    glyph: ['mt-1.25', 'h-3.5', 'w-3.5', 'shrink-0'],
    lead: [],
  },
  variants: {
    status: {
      neutral: {
        glyph: 'text-content-layout-3',
        lead: 'text-content-layout-1',
      },
      negative: {
        glyph: 'text-content-negative-soft',
        lead: 'text-content-negative-soft',
      },
      warning: {
        glyph: 'text-content-warning-soft',
        lead: 'text-content-warning-soft',
      },
      positive: {
        glyph: 'text-content-positive-soft',
        lead: 'text-content-positive-soft',
      },
    },
  },
  defaultVariants: { status: 'neutral' },
})

export type QuietStatus = 'neutral' | 'negative' | 'warning' | 'positive'
