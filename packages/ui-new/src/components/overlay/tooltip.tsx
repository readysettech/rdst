'use client'

import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { tv, type VariantProps } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type ComponentRef,
  forwardRef,
} from 'react'
import { Icon } from '../svg/icon'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { IconWithSpinner } from '../svg/icon-with-spinner'

const tooltipStyles = tv({
  slots: {
    content: [
      'group',
      'z-50',
      'flex',
      'items-center',
      'overflow-hidden',
      'max-w-md',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-transparent',
      'px-3',
      'py-2',
      'shadow-large',
      'bg-surface-layout-1',
      'transition',
      'duration-fast',
      'ease-base',
      'data-state-open:animate-in',
      'data-state-open:fade-in-0',
      'data-state-open:zoom-in-95',
      'data-state-closed:animate-out',
      'data-state-closed:fade-out-0',
      'data-state-closed:zoom-out-95',
      'data-side-top:slide-in-from-bottom-2',
      'data-side-bottom:slide-in-from-top-2',
      'data-side-left:slide-in-from-right-2',
      'data-side-right:slide-in-from-left-2',
    ],
  },
  variants: {
    size: {
      base: {
        content: ['text-label-small', 'py-2', 'px-3', 'rounded-lg'],
      },
      small: {
        content: ['text-label-extra-small', 'py-1', 'px-2', 'rounded-md'],
      },
    },
    variant: {
      primary: {},
      rising: {},
      negative: {},
      informative: {},
      warning: {},
      positive: {},
    },
    modifier: {
      solid: {},
      outline: {},
      ghost: {},
    },
    fullWidth: {
      true: {
        content: ['w-full'],
      },
    },
    disabled: {
      true: {
        content: ['pointer-events-none', 'cursor-not-allowed', 'opacity-50'],
      },
    },
    iconPosition: {
      none: {},
      left: {},
      'left-full': {
        content: ['justify-between'],
      },
      right: {},
      'right-full': {
        content: ['justify-between'],
      },
      icon: {
        content: ['justify-center'],
      },
    },
  },
  compoundVariants: [
    {
      iconPosition: 'icon',
      size: 'base',
      class: {
        content: ['w-[1.75rem]', 'px-3'],
      },
    },
    {
      iconPosition: 'icon',
      size: 'small',
      class: {
        content: ['w-8', 'px-2', 'py-2'],
      },
    },
    {
      variant: 'primary',
      modifier: 'solid',
      class: {
        content: [
          'text-content-primary-solid',
          'bg-surface-primary-solid',
          'hover:bg-surface-primary-solid-hover',
          'active:bg-surface-primary-solid-active',
          '[&_#loader]:border-t-content-primary-solid',
        ],
      },
    },
    {
      variant: 'primary',
      modifier: 'outline',
      class: {
        content: [
          'text-content-primary-soft',
          'border-(length:--border-base)',
          'border-border-primary-soft',
          'bg-surface-primary-soft',
          'hover:bg-surface-primary-soft-hover',
          'active:bg-surface-primary-soft-active',
          '[&_#loader]:border-t-content-primary-soft',
        ],
      },
    },
    {
      variant: 'primary',
      modifier: 'ghost',
      class: {
        content: [
          'text-content-primary-soft',
          'border-0',
          'bg-surface-primary-soft',
          'hover:bg-surface-primary-soft-hover',
          'active:bg-surface-primary-soft-active',
          '[&_#loader]:border-t-content-primary-soft',
        ],
      },
    },
    {
      variant: 'rising',
      modifier: 'solid',
      class: {
        content: [
          'text-content-rising-solid',
          'bg-surface-rising-solid',
          'hover:bg-surface-rising-solid-hover',
          'active:bg-surface-rising-solid-active',
          '[&_#loader]:border-t-content-rising-solid',
        ],
      },
    },
    {
      variant: 'rising',
      modifier: 'outline',
      class: {
        content: [
          'text-content-rising-soft',
          'border-(length:--border-base)',
          'border-border-rising-soft',
          'bg-surface-rising-soft',
          'hover:bg-surface-rising-soft-hover',
          'active:bg-surface-rising-soft-active',
          '[&_#loader]:border-t-content-rising-soft',
        ],
      },
    },
    {
      variant: 'rising',
      modifier: 'ghost',
      class: {
        content: [
          'text-content-rising-soft',
          'border-0',
          'bg-surface-rising-soft',
          'hover:bg-surface-rising-soft-hover',
          'active:bg-surface-rising-soft-active',
          '[&_#loader]:border-t-content-rising-soft',
        ],
      },
    },
    {
      variant: 'negative',
      modifier: 'solid',
      class: {
        content: [
          'text-content-negative-solid',
          'bg-surface-negative-solid',
          'hover:bg-surface-negative-solid-hover',
          'active:bg-surface-negative-solid-active',
          '[&_#loader]:border-t-content-negative-solid',
        ],
      },
    },
    {
      variant: 'negative',
      modifier: 'outline',
      class: {
        content: [
          'text-content-negative-soft',
          'border-(length:--border-base)',
          'border-border-negative-soft',
          'bg-surface-negative-soft',
          'hover:bg-surface-negative-soft-hover',
          'active:bg-surface-negative-soft-active',
          '[&_#loader]:border-t-content-negative-soft',
        ],
      },
    },
    {
      variant: 'negative',
      modifier: 'ghost',
      class: {
        content: [
          'text-content-negative-soft',
          'border-0',
          'bg-surface-negative-soft',
          'hover:bg-surface-negative-soft-hover',
          'active:bg-surface-negative-soft-active',
          '[&_#loader]:border-t-content-negative-soft',
        ],
      },
    },
    {
      variant: 'informative',
      modifier: 'solid',
      class: {
        content: [
          'text-content-info-solid',
          'bg-surface-info-solid',
          'hover:bg-surface-info-solid-hover',
          'active:bg-surface-info-solid-active',
          '[&_#loader]:border-t-content-info-solid',
        ],
      },
    },
    {
      variant: 'informative',
      modifier: 'outline',
      class: {
        content: [
          'text-content-info-soft',
          'border-(length:--border-base)',
          'border-border-info-soft',
          'bg-surface-info-soft',
          'hover:bg-surface-info-soft-hover',
          'active:bg-surface-info-soft-active',
          '[&_#loader]:border-t-content-info-soft',
        ],
      },
    },
    {
      variant: 'informative',
      modifier: 'ghost',
      class: {
        content: [
          'text-content-info-soft',
          'border-0',
          'bg-surface-info-soft',
          'hover:bg-surface-info-soft-hover',
          'active:bg-surface-info-soft-active',
          '[&_#loader]:border-t-content-info-soft',
        ],
      },
    },
    {
      variant: 'warning',
      modifier: 'solid',
      class: {
        content: [
          'text-content-warning-solid',
          'bg-surface-warning-solid',
          'hover:bg-surface-warning-solid-hover',
          'active:bg-surface-warning-solid-active',
          '[&_#loader]:border-t-content-warning-solid',
        ],
      },
    },
    {
      variant: 'warning',
      modifier: 'outline',
      class: {
        content: [
          'text-content-warning-soft',
          'border-(length:--border-base)',
          'border-border-warning-soft',
          'bg-surface-warning-soft',
          'hover:bg-surface-warning-soft-hover',
          'active:bg-surface-warning-soft-active',
          '[&_#loader]:border-t-content-warning-soft',
        ],
      },
    },
    {
      variant: 'warning',
      modifier: 'ghost',
      class: {
        content: [
          'text-content-warning-soft',
          'border-0',
          'bg-surface-warning-soft',
          'hover:bg-surface-warning-soft-hover',
          'active:bg-surface-warning-soft-active',
          '[&_#loader]:border-t-content-warning-soft',
        ],
      },
    },
    {
      variant: 'positive',
      modifier: 'solid',
      class: {
        content: [
          'text-content-positive-solid',
          'bg-surface-positive-solid',
          'hover:bg-surface-positive-solid-hover',
          'active:bg-surface-positive-solid-active',
          '[&_#loader]:border-t-content-positive-solid',
        ],
      },
    },
    {
      variant: 'positive',
      modifier: 'outline',
      class: {
        content: [
          'text-content-positive-soft',
          'border-(length:--border-base)',
          'border-border-positive-soft',
          'bg-surface-positive-soft',
          'hover:bg-surface-positive-soft-hover',
          'active:bg-surface-positive-soft-active',
          '[&_#loader]:border-t-content-positive-soft',
        ],
      },
    },
    {
      variant: 'positive',
      modifier: 'ghost',
      class: {
        content: [
          'text-content-positive-soft',
          'border-0',
          'bg-surface-positive-soft',
          'hover:bg-surface-positive-soft-hover',
          'active:bg-surface-positive-soft-active',
          '[&_#loader]:border-t-content-positive-soft',
        ],
      },
    },
  ],
  defaultVariants: {
    size: 'base',
    variant: 'primary',
    modifier: 'solid',
    iconPosition: 'none',
  },
})

type TooltipVariants = VariantProps<typeof tooltipStyles>

export const TooltipProvider = TooltipPrimitive.Provider

export const Tooltip = TooltipPrimitive.Root

export const TooltipTrigger = TooltipPrimitive.Trigger

type TooltipContentProps = {
  loading?: boolean
  icon?: IconStrokeName
  label: string
} & TooltipVariants &
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>

export const TooltipContent = forwardRef<
  ComponentRef<typeof TooltipPrimitive.Content>,
  TooltipContentProps
>(
  (
    {
      sideOffset = 8,
      label,
      icon,
      loading,
      className,
      size,
      variant,
      modifier,
      fullWidth,
      disabled,
      iconPosition,
      ...restProps
    },
    ref
  ) => {
    const resolvedIconPosition = iconPosition ?? 'none'

    const styles = tooltipStyles({
      size,
      variant,
      modifier,
      fullWidth,
      disabled,
      iconPosition: resolvedIconPosition,
    })

    const hasLeftIcon = Boolean(icon && resolvedIconPosition.includes('left'))
    const hasRightIcon = Boolean(icon && resolvedIconPosition.includes('right'))
    const hasJustIcon = Boolean(icon && resolvedIconPosition.includes('icon'))

    return (
      <TooltipPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        className={styles.content({ class: [className, 'notranslate'] })}
        translate="no"
        contentEditable={false}
        {...restProps}
      >
        <IconWithSpinner
          loading={loading}
          hasIcon={hasLeftIcon || hasJustIcon}
          rightGap={!hasJustIcon}
          icon={icon as IconStrokeName}
          label={label}
        />
        {!hasJustIcon && label}
        {!hasJustIcon && hasRightIcon && (
          <>
            <div className="h-2 w-2" />
            <Icon name={icon!} label={label} />
          </>
        )}
      </TooltipPrimitive.Content>
    )
  }
)
TooltipContent.displayName = TooltipPrimitive.Content.displayName
