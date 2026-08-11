'use client'

import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { memo, type ReactNode } from 'react'
import { Button } from '../element/button'
import { VStack } from '../element/stack'
import { Text } from '../element/text'
import { Icon } from '../svg/icon'

/** One call-to-action rendered as a button. */
export interface EmptyStateAction {
  label: string
  onClick: () => void
  icon?: IconStrokeName
}

// Neutral counterpart to ErrorState: same centered composition, no accent or
// glow. `block` is the page/panel state; `compact` fits inside a table or a
// short slot. The neutral tile keeps the icon out of the brand gradient (that
// vocabulary belongs to IconTile / hero surfaces, not a "nothing here" state).
const emptyStateStyles = tv({
  slots: {
    root: ['w-full', 'flex', 'flex-col', 'items-center', 'text-center'],
    tile: [
      'flex',
      'items-center',
      'justify-center',
      'rounded-2xl',
      'bg-surface-layout-2',
    ],
    glyph: ['text-content-layout-3'],
  },
  variants: {
    layout: {
      block: {
        root: ['px-6', 'py-16', 'gap-4'],
        tile: ['w-14', 'h-14'],
        glyph: ['w-7', 'h-7'],
      },
      compact: {
        root: ['px-4', 'py-8', 'gap-3'],
        tile: ['w-10', 'h-10'],
        glyph: ['w-5', 'h-5'],
      },
    },
  },
  defaultVariants: {
    layout: 'block',
  },
})

type EmptyStateVariants = VariantProps<typeof emptyStateStyles>

export interface EmptyStateProps extends EmptyStateVariants {
  /** Glyph shown in the neutral tile. Ignored when `media` is supplied. */
  icon?: IconStrokeName
  /** Custom visual (illustration, avatar) replacing the default icon tile. */
  media?: ReactNode
  /** Positive, specific headline ("No cached queries yet"). */
  title: string
  /** Supporting sentence explaining the state or the next step. */
  body?: ReactNode
  /** Primary call to action (solid button). */
  action?: EmptyStateAction
  /** Secondary call to action, rendered as a link-styled button. */
  secondaryAction?: EmptyStateAction
  className?: string
}

/**
 * The one neutral empty state: a replacement for a blank region, not a spacer.
 * Covers the first-use, no-results, and restricted/permission cases (all the
 * same shape — change the copy and glyph, not the component). For failures use
 * {@link ErrorState} instead, which carries the accent and recovery contract.
 */
function EmptyStateImpl({
  icon,
  media,
  title,
  body,
  action,
  secondaryAction,
  layout,
  className,
}: EmptyStateProps) {
  const styles = emptyStateStyles({ layout })

  return (
    <div className={styles.root({ class: className })}>
      {media ??
        (icon && (
          <div className={styles.tile()}>
            {/* Decorative: the visible title names the state, so labelling the
                icon would double the announcement. */}
            <Icon
              name={icon}
              label=""
              aria-hidden="true"
              className={styles.glyph()}
            />
          </div>
        ))}

      <VStack className="gap-1 items-center">
        <Text
          as="h3"
          level="subtitle-1"
          className="text-content-layout-1"
        >
          {title}
        </Text>
        {body && (
          <Text
            level="body-small"
            className="max-w-md text-content-layout-3 leading-relaxed"
          >
            {body}
          </Text>
        )}
      </VStack>

      {(action || secondaryAction) && (
        <div className={cn('flex flex-wrap items-center justify-center gap-3', body || icon || media ? 'mt-2' : '')}>
          {action && (
            <Button
              variant="primary"
              modifier="solid"
              label={action.label}
              icon={action.icon}
              iconPosition={action.icon ? 'left' : 'none'}
              onClick={action.onClick}
            />
          )}
          {secondaryAction && (
            <Button
              variant="primary"
              modifier="link"
              label={secondaryAction.label}
              icon={secondaryAction.icon}
              iconPosition={secondaryAction.icon ? 'left' : 'none'}
              onClick={secondaryAction.onClick}
            />
          )}
        </div>
      )}
    </div>
  )
}

export const EmptyState = memo(EmptyStateImpl)
