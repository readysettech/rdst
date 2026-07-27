import { tv, type VariantProps } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import {
  type ButtonHTMLAttributes,
  forwardRef,
  memo,
  type ReactNode,
} from 'react'
import { Icon } from '../svg/icon'
import { IconWithSpinner } from '../svg/icon-with-spinner'

const buttonStyles = tv({
  slots: {
    root: [
      'relative',
      'flex',
      'items-center',
      'justify-center',
      'min-w-max',
      'cursor-pointer',
      'select-none',
      'transition',
      'duration-fast',
      'ease-base',
      'transform',
      'focus-visible:outline-none',
      'focus-visible:ring-2',
      'focus-visible:ring-border-primary-soft',
      'focus-visible:ring-offset-2',
      'focus-visible:ring-offset-surface-layout-1',
      'active:scale-[0.98]',
      'active:origin-center',
    ],
    inner: ['flex', 'items-center'],
  },
  variants: {
    size: {
      large: {
        root: ['text-button-medium', 'py-3', 'px-4', 'rounded-3xl', 'h-12'],
      },
      base: {
        root: ['text-button-medium', 'py-3', 'px-4', 'rounded-2xl', 'h-10'],
      },
      small: {
        root: ['text-button-small', 'py-3', 'px-3', 'rounded-xl', 'h-8'],
      },
    },
    variant: {
      primary: {},
      rising: {},
      negative: {},
    },
    modifier: {
      solid: {},
      outline: {},
      ghost: {},
      link: {},
    },
    fullWidth: {
      true: { root: ['w-full'] },
    },
    disabled: {
      true: {
        root: ['cursor-not-allowed', 'pointer-events-none', 'opacity-50'],
      },
    },
    iconPosition: {
      none: {},
      left: {},
      'left-full': { root: ['justify-between'], inner: ['justify-between'] },
      right: {},
      'right-full': {
        root: ['justify-between'],
        inner: ['justify-between', 'flex-1'],
      },
      icon: { root: ['justify-center'], inner: ['justify-center'] },
    },
  },
  compoundVariants: [
    {
      iconPosition: 'icon',
      size: 'base',
      class: {
        root: ['w-10', 'px-3'],
      },
    },
    {
      iconPosition: 'icon',
      size: 'small',
      class: {
        root: ['w-8', 'px-2', 'py-2'],
      },
    },
    {
      variant: 'primary',
      modifier: 'solid',
      class: {
        root: [
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
        root: [
          'text-content-primary-soft',
          'border',
          'border-border-primary-soft',
          'bg-transparent',
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
        root: [
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
      variant: 'primary',
      modifier: 'link',
      class: {
        root: [
          'text-content-primary-soft',
          'border-0',
          'bg-transparent',
          'underline',
          'hover:text-surface-primary-solid-hover',
          'active:text-surface-primary-solid-active',
          '[&_#loader]:border-t-content-primary-soft',
        ],
      },
    },
    {
      variant: 'rising',
      modifier: 'solid',
      class: {
        root: [
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
        root: [
          'text-content-rising-soft',
          'border',
          'border-border-rising-soft',
          'bg-transparent',
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
        root: [
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
      variant: 'rising',
      modifier: 'link',
      class: {
        root: [
          'text-content-rising-plain',
          'border-0',
          'bg-transparent',
          'underline',
          'hover:text-surface-rising-solid-hover',
          'active:text-surface-rising-solid-active',
          '[&_#loader]:border-t-content-rising-soft',
        ],
      },
    },
    {
      variant: 'negative',
      modifier: 'solid',
      class: {
        root: [
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
        root: [
          'text-content-negative-soft',
          'border',
          'border-border-negative-soft',
          'bg-transparent',
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
        root: [
          'text-content-negative-soft',
          'border-0',
          'bg-surface-negative-soft',
          'hover:bg-surface-negative-soft-hover',
          'active:bg-surface-negative-soft-active',
          '[&_#loader]:border-t-content-primary-soft',
        ],
      },
    },
    {
      variant: 'negative',
      modifier: 'link',
      class: {
        root: [
          'text-content-negative-plain',
          'border-0',
          'bg-transparent',
          'underline',
          'hover:text-surface-negative-solid-hover',
          'active:text-surface-negative-solid-active',
          '[&_#loader]:border-t-content-primary-soft',
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

type ButtonVariants = VariantProps<typeof buttonStyles>

type ButtonProps = ButtonVariants & {
  loading?: boolean
  icon?: IconStrokeName
  label: string
  children?: ReactNode
  className?: string
  classMerge?: string
  innerClassName?: string
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'>

const Button = forwardRef<HTMLButtonElement | HTMLAnchorElement, ButtonProps>(
  (props, ref) => {
    const {
      size,
      variant,
      modifier,
      fullWidth,
      disabled: disabledProp,
      iconPosition,
      loading,
      icon,
      label,
      children,
      className,
      classMerge,
      innerClassName,
      ...restProps
    } = props

    const isDisabled = Boolean(disabledProp || loading)
    const resolvedVariant = variant ?? 'primary'
    const resolvedModifier = modifier ?? 'solid'
    const resolvedIconPosition = iconPosition ?? 'none'
    const styles = buttonStyles({
      size,
      variant: resolvedVariant,
      modifier: resolvedModifier,
      fullWidth,
      iconPosition: resolvedIconPosition,
      disabled: isDisabled ? true : undefined,
    })

    const hasLeftIcon = icon && resolvedIconPosition.includes('left')
    const hasRightIcon = icon && resolvedIconPosition.includes('right')
    const hasJustIcon = icon && resolvedIconPosition.includes('icon')

    return (
      <button
        type="button"
        {...restProps}
        disabled={isDisabled}
        className={styles.root({ class: [className, classMerge] })}
        ref={ref as React.Ref<HTMLButtonElement>}
      >
        <IconWithSpinner
          loading={loading}
          hasIcon={Boolean(hasLeftIcon || hasJustIcon)}
          rightGap={!hasJustIcon}
          icon={icon as IconStrokeName}
          label={hasJustIcon ? label : ''}
        />
        <div className={styles.inner({ class: innerClassName })}>
          {children}
          {!hasJustIcon && label}
          {!hasJustIcon && hasRightIcon && (
            <>
              <div className="h-1 w-1" />
              <Icon name={icon} label="" aria-hidden="true" />
            </>
          )}
        </div>
      </button>
    )
  }
)

const MemoizedButton = memo(Button)

export { MemoizedButton as Button, buttonStyles }
export type { ButtonProps }
