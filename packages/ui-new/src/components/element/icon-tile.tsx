'use client'

import { tv, type VariantProps } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { memo } from 'react'
import { Icon } from '../svg/icon'

// The brand gradient tile pasted across ~20 files: a rounded square with a
// `from-surface-primary-soft` base and a semantic soft surface as the second
// stop. The accent list mirrors the soft surfaces the app actually reaches for
// on these tiles; the glyph sits in `content-primary-soft` by default.
const iconTileStyles = tv({
  slots: {
    root: [
      'flex',
      'items-center',
      'justify-center',
      'shrink-0',
      'bg-gradient-to-br',
      'from-surface-primary-soft',
    ],
    glyph: ['text-content-primary-soft'],
  },
  variants: {
    size: {
      sm: { root: ['w-8', 'h-8', 'rounded-xl'], glyph: ['w-4', 'h-4'] },
      base: { root: ['w-10', 'h-10', 'rounded-xl'], glyph: ['w-5', 'h-5'] },
      lg: { root: ['w-12', 'h-12', 'rounded-2xl'], glyph: ['w-6', 'h-6'] },
    },
    accent: {
      primary: { root: ['to-surface-primary-soft'] },
      info: { root: ['to-surface-info-soft'] },
      warning: { root: ['to-surface-warning-soft'] },
      positive: { root: ['to-surface-positive-soft'] },
      negative: { root: ['to-surface-negative-soft'] },
      rising: { root: ['to-surface-rising-soft'] },
    },
  },
  defaultVariants: {
    size: 'lg',
    accent: 'info',
  },
})

type IconTileVariants = VariantProps<typeof iconTileStyles>

interface IconTileProps extends IconTileVariants {
  /** The glyph to render inside the tile. */
  icon: IconStrokeName
  /**
   * Accessible name. Defaults to decorative (empty label + `aria-hidden`),
   * since a tile almost always sits beside a visible heading. Provide a label
   * only when the tile is the sole carrier of meaning.
   */
  label?: string
  /** Override the default `content-primary-soft` glyph color. */
  iconClassName?: string
  className?: string
}

function IconTileImpl({
  icon,
  size,
  accent,
  label,
  iconClassName,
  className,
}: IconTileProps) {
  const styles = iconTileStyles({ size, accent })
  const decorative = !label

  return (
    <div className={styles.root({ class: className })}>
      <Icon
        name={icon}
        label={label ?? ''}
        aria-hidden={decorative ? 'true' : undefined}
        className={styles.glyph({ class: iconClassName })}
      />
    </div>
  )
}

export const IconTile = memo(IconTileImpl)
export { iconTileStyles }
export type { IconTileProps }
