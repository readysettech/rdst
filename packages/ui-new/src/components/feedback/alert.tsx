import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import { forwardRef, memo } from 'react'
import type { WithChildren, WithClassName } from '../../helpers/types'
import { Icon } from '../svg/icon'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { IconWithSpinner } from '../svg/icon-with-spinner'

const alertRecipe = tv({
  base: [
    'flex',
    'justify-start',
    'transition-all',
    'duration-fast',
    'ease-base',
    'items-start',
    'cursor-pointer',
    'select-none',
    'focus-visible:outline-none',
    'focus-visible:shadow-focus',
    'active:scale-[0.99]',
    'active:origin-center',
  ],
  variants: {
    size: {
      base: ['text-label-small', 'py-3', 'px-3', 'rounded-2xl'],
      small: ['text-label-extra-small', 'py-1', 'px-2', 'rounded-md'],
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
        width: 'full',
      },
    },
    disabled: {
      true: ['opacity-50', 'cursor-not-allowed', 'pointer-events-none'],
    },
    iconPosition: {
      none: {},
      left: {},
      'left-full': ['justify-between'],
      right: {},
      'right-full': ['justify-between'],
      icon: ['justify-center'],
    },
  },
  compoundVariants: [
    {
      variant: 'primary',
      modifier: 'solid',
      class: [
        'text-content-primary-solid',
        'bg-surface-primary-solid',
        'hover:bg-surface-primary-solid-hover',
        'active:bg-surface-primary-solid-active',
        '[&_#loader]:border-t-content-primary-solid',
      ],
    },
    {
      variant: 'primary',
      modifier: 'outline',
      class: [
        'text-content-primary-soft',
        'border-(length:--border-base)',
        'border-border-primary-soft',
        'bg-transparent',
        'hover:bg-surface-primary-soft-hover',
        'active:bg-surface-primary-soft-active',
        '[&_#loader]:border-t-content-primary-soft',
      ],
    },
    {
      variant: 'primary',
      modifier: 'ghost',
      class: [
        'text-content-primary-soft',
        'border-none',
        'bg-surface-primary-soft',
        'hover:bg-surface-primary-soft-hover',
        'active:bg-surface-primary-soft-active',
        '[&_#loader]:border-t-content-primary-soft',
      ],
    },
    {
      variant: 'rising',
      modifier: 'solid',
      class: [
        'text-content-rising-solid',
        'bg-surface-rising-solid',
        'hover:bg-surface-rising-solid-hover',
        'active:bg-surface-rising-solid-active',
        '[&_#loader]:border-t-content-rising-solid',
      ],
    },
    {
      variant: 'rising',
      modifier: 'outline',
      class: [
        'text-content-rising-soft',
        'border-(length:--border-base)',
        'border-border-rising-soft',
        'bg-transparent',
        'hover:bg-surface-rising-soft-hover',
        'active:bg-surface-rising-soft-active',
        '[&_#loader]:border-t-content-rising-soft',
      ],
    },
    {
      variant: 'rising',
      modifier: 'ghost',
      class: [
        'text-content-rising-soft',
        'border-none',
        'bg-surface-rising-soft',
        'hover:bg-surface-rising-soft-hover',
        'active:bg-surface-rising-soft-active',
        '[&_#loader]:border-t-content-rising-soft',
      ],
    },
    {
      variant: 'negative',
      modifier: 'solid',
      class: [
        'text-content-negative-solid',
        'bg-surface-negative-solid',
        'hover:bg-surface-negative-solid-hover',
        'active:bg-surface-negative-solid-active',
        '[&_#loader]:border-t-content-negative-solid',
      ],
    },
    {
      variant: 'negative',
      modifier: 'outline',
      class: [
        'text-content-negative-soft',
        'border-(length:--border-base)',
        'border-border-negative-soft',
        'bg-surface-negative-soft',
        'hover:bg-surface-negative-soft-hover',
        'active:bg-surface-negative-soft-active',
        '[&_#loader]:border-t-content-negative-soft',
      ],
    },
    {
      variant: 'negative',
      modifier: 'ghost',
      class: [
        'text-content-negative-soft',
        'border-none',
        'bg-surface-negative-soft',
        'hover:bg-surface-negative-soft-hover',
        'active:bg-surface-negative-soft-active',
        '[&_#loader]:border-t-content-negative-soft',
      ],
    },

    {
      variant: 'informative',
      modifier: 'solid',
      class: [
        'text-content-info-solid',
        'bg-surface-info-solid',
        'hover:bg-surface-info-solid-hover',
        'active:bg-surface-info-solid-active',
        '[&_#loader]:border-t-content-info-solid',
      ],
    },
    {
      variant: 'informative',
      modifier: 'outline',
      class: [
        'text-content-info-soft',
        'border-(length:--border-base)',
        'border-border-info-soft',
        'bg-surface-info-soft',
        'hover:bg-surface-info-soft-hover',
        'active:bg-surface-info-soft-active',
        '[&_#loader]:border-t-content-info-soft',
      ],
    },
    {
      variant: 'warning',
      modifier: 'solid',
      class: [
        'text-content-warning-solid',
        'bg-surface-warning-solid',
        'hover:bg-surface-warning-solid-hover',
        'active:bg-surface-warning-solid-active',
        '[&_#loader]:border-t-content-warning-solid',
      ],
    },
    {
      variant: 'warning',
      modifier: 'outline',
      class: [
        'text-content-warning-soft',
        'border-(length:--border-base)',
        'border-border-warning-soft',
        'bg-surface-warning-soft',
        'hover:bg-surface-warning-soft-hover',
        'active:bg-surface-warning-soft-active',
        '[&_#loader]:border-t-content-warning-soft',
      ],
    },
    {
      variant: 'warning',
      modifier: 'ghost',
      class: [
        'text-content-warning-soft',
        'border-none',
        'bg-surface-warning-soft',
        'hover:bg-surface-warning-soft-hover',
        'active:bg-surface-warning-soft-active',
        '[&_#loader]:border-t-content-warning-soft',
      ],
    },

    {
      variant: 'positive',
      modifier: 'solid',
      class: [
        'text-content-positive-solid',
        'bg-surface-positive-solid',
        'hover:bg-surface-positive-solid-hover',
        'active:bg-surface-positive-solid-active',
        '[&_#loader]:border-t-content-positive-solid',
      ],
    },
    {
      variant: 'positive',
      modifier: 'outline',
      class: [
        'text-content-positive-soft',
        'border-(length:--border-base)',
        'border-border-positive-soft',
        'bg-surface-positive-soft',
        'hover:bg-surface-positive-soft-hover',
        'active:bg-surface-positive-soft-active',
        '[&_#loader]:border-t-content-positive-soft',
      ],
    },
    {
      variant: 'positive',
      modifier: 'ghost',
      class: [
        'text-content-positive-soft',
        'border-none',
        'bg-surface-positive-soft',
        'hover:bg-surface-positive-soft-hover',
        'active:bg-surface-positive-soft-active',
        '[&_#loader]:border-t-content-positive-soft',
      ],
    },
    {
      iconPosition: 'icon',
      size: 'base',
      class: ['w-7', 'px-3'],
    },
    {
      iconPosition: 'icon',
      size: 'small',
      class: ['w-8', 'px-2', 'py-2'],
    },
  ],

  defaultVariants: {
    size: 'base',
    variant: 'primary',
    modifier: 'solid',
    iconPosition: 'none',
  },
})

type AlertProps = VariantProps<typeof alertRecipe> & {
  disabled?: boolean
  loading?: boolean
  icon?: IconStrokeName
  label: string
  onClick?: () => void
} & WithClassName &
  WithChildren

const Alert = forwardRef<HTMLDivElement | HTMLAnchorElement, AlertProps>(
  (props, ref) => {
    const {
      size,
      variant,
      modifier,
      fullWidth,
      disabled,
      iconPosition,
      loading,
      label,
      icon,
      children,
      onClick,
      className,
      ...restProps
    } = props

    const hasLeftIcon = icon && iconPosition?.includes('left')
    const hasRightIcon = icon && iconPosition?.includes('right')
    const hasJustIcon = icon && iconPosition?.includes('icon')

    return (
      // biome-ignore lint/a11y/noStaticElementInteractions: <!>
      // biome-ignore lint/a11y/useKeyWithClickEvents: <!>
      <div
        {...restProps}
        className={alertRecipe({
          size,
          variant,
          modifier,
          fullWidth,
          disabled,
          iconPosition,
          className,
        })}
        ref={ref as React.Ref<HTMLDivElement>}
        onClick={onClick}
      >
        <IconWithSpinner
          loading={loading}
          hasIcon={hasLeftIcon || hasJustIcon}
          rightGap={!hasJustIcon}
          icon={icon as IconStrokeName}
          label={label}
        />
        {!hasJustIcon && label}
        {children ? (
          children
        ) : !hasJustIcon && hasRightIcon ? (
          <>
            <div className={cn('h-2', 'w-2')} />
            <Icon name={icon} label={label} />
          </>
        ) : null}
      </div>
    )
  }
)

const MemoizedAlert = memo(Alert)

export { MemoizedAlert as Alert, alertRecipe }
