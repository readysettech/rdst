'use client'

import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { type ButtonHTMLAttributes, forwardRef, memo } from 'react'
import { buttonStyles } from './button'
import { Icon } from '../svg/icon'
import { Spinner } from '../feedback/spinner'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '../overlay/tooltip'

// Reuses Button's color matrix (variant x modifier) and the shared focus /
// press affordances; only the axes that make sense for an icon-only control
// are exposed. Sizing is overridden below so the base hit target clears 44px
// while the glyph stays ~16px (Carbon's icon-button target rule).
type IconButtonVariant = 'primary' | 'rising' | 'negative'
type IconButtonModifier = 'solid' | 'outline' | 'ghost'
type IconButtonSize = 'large' | 'base' | 'small'

const sizeClass: Record<IconButtonSize, string> = {
  large: 'w-12 h-12',
  base: 'w-11 h-11 min-w-11',
  small: 'w-8 h-8 min-w-8',
}

const glyphClass: Record<IconButtonSize, string> = {
  large: 'w-5 h-5',
  base: 'w-4 h-4',
  small: 'w-4 h-4',
}

interface IconButtonProps
  extends Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    'className' | 'children'
  > {
  /** The glyph to render. */
  icon: IconStrokeName
  /**
   * Required accessible name — becomes the button's `aria-label` and the
   * default tooltip content. An icon-only control with no label is a trap.
   */
  label: string
  variant?: IconButtonVariant
  modifier?: IconButtonModifier
  size?: IconButtonSize
  disabled?: boolean
  loading?: boolean
  /**
   * `true` (default) shows a tooltip with `label`. Pass a string to show
   * different tooltip copy, or `false` to suppress the tooltip entirely.
   */
  tooltip?: boolean | string
  className?: string
  classMerge?: string
}

const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  (props, ref) => {
    const {
      icon,
      label,
      variant = 'primary',
      modifier = 'solid',
      size = 'base',
      disabled,
      loading,
      tooltip = true,
      className,
      classMerge,
      ...restProps
    } = props

    const isDisabled = Boolean(disabled || loading)
    const styles = buttonStyles({
      variant,
      modifier,
      size,
      iconPosition: 'icon',
      disabled: isDisabled ? true : undefined,
    })

    const button = (
      <button
        type="button"
        {...restProps}
        ref={ref}
        disabled={isDisabled}
        aria-label={label}
        className={styles.root({ class: [sizeClass[size], className, classMerge] })}
      >
        {loading ? (
          <Spinner />
        ) : (
          <Icon
            name={icon}
            label=""
            aria-hidden="true"
            className={glyphClass[size]}
          />
        )}
      </button>
    )

    if (tooltip === false) return button

    const tooltipLabel = typeof tooltip === 'string' ? tooltip : label

    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>{button}</TooltipTrigger>
          <TooltipContent label={tooltipLabel} />
        </Tooltip>
      </TooltipProvider>
    )
  }
)

IconButton.displayName = 'IconButton'

const MemoizedIconButton = memo(IconButton) as typeof IconButton & {
  displayName?: string
}
MemoizedIconButton.displayName = 'IconButton'

export { MemoizedIconButton as IconButton }
export type { IconButtonProps }
