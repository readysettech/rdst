'use client'

import { tv, type VariantProps } from '@rs/tailwind-base'
import {
  forwardRef,
  type HTMLInputTypeAttribute,
  type InputHTMLAttributes,
  useState,
} from 'react'
import { Icon, type IconListType } from '../../svg/icon'
import { IconWithSpinner } from '../../svg/icon-with-spinner'

const inputTextStyles = tv({
  slots: {
    container: ['relative', 'w-full', 'text-content-layout-1'],
    input: [
      'flex',
      'h-10',
      'w-full',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-2',
      'px-3',
      'py-2',
      'text-body-medium',

      'placeholder:text-content-layout-3',

      'focus-visible:outline-none',
      'focus-visible:shadow-focus',

      'disabled:cursor-not-allowed',
      'disabled:opacity-50',
    ],
  },
  variants: {
    iconPosition: {
      none: {},
      left: { input: ['pl-8'] },
      right: { input: ['pr-8'] },
      both: { input: ['pl-8', 'pr-8'] },
    },
    error: {
      true: {
        input: ['border-border-negative-soft'],
      },
    },
    positive: {
      true: {
        input: ['border-border-positive-soft'],
      },
    },
  },
  defaultVariants: {
    iconPosition: 'none',
  },
})

type BaseInputTextVariants = VariantProps<typeof inputTextStyles>

export type BaseInputTextProps = BaseInputTextVariants & {
  icon?: IconListType
  iconClick?: () => void
  loading?: boolean
  justLoading?: boolean
  containerClassName?: string
} & InputHTMLAttributes<HTMLInputElement>

const BaseInputText = forwardRef<HTMLInputElement, BaseInputTextProps>(
  (
    {
      icon,
      iconClick,
      loading,
      justLoading,
      containerClassName,
      className,
      disabled,
      type = 'text',
      iconPosition,
      error,
      positive,
      ...restProps
    },
    ref
  ) => {
    const [inputType, setInputType] = useState<HTMLInputTypeAttribute>(type)

    const isPassword = type === 'password'
    const hasLeftIcon = Boolean(icon && iconPosition?.includes('left'))
    const hasRightIcon = Boolean(icon && iconPosition?.includes('right'))
    const hasRightAccessory = Boolean(
      hasRightIcon || loading || justLoading || isPassword
    )

    const resolvedIconPosition = (() => {
      if (iconPosition === 'left' && hasRightAccessory) return 'both' as const
      if (iconPosition === 'right') return 'right' as const
      if (iconPosition === 'left') return 'left' as const
      if (hasRightAccessory) return 'right' as const
      return 'none' as const
    })()

    const styles = inputTextStyles({
      iconPosition: resolvedIconPosition,
      error: error ? true : undefined,
      positive: positive ? true : undefined,
    })

    const isDisabled = Boolean(loading || disabled)

    const resolvedRightIcon = (() => {
      if (!hasRightAccessory) return icon
      if (isPassword) {
        return inputType === 'password' ? 'eye-off' : 'eye'
      }
      return icon
    })()

    const togglePassword = () => {
      setInputType((prev) => (prev === 'password' ? 'text' : 'password'))
    }

    const handleRightClick =
      iconClick ?? (isPassword ? togglePassword : undefined)

    const rightSlot = hasRightAccessory ? (
      handleRightClick ? (
        <button
          type="button"
          onClick={handleRightClick}
          className="absolute inset-y-0 right-0 flex items-center pr-3 text-content-layout-1"
          disabled={Boolean(loading || justLoading)}
          aria-label={
            isPassword
              ? inputType === 'password'
                ? 'Show password'
                : 'Hide password'
              : (icon ?? 'Toggle input action')
          }
        >
          <IconWithSpinner
            icon={resolvedRightIcon as IconListType}
            loading={Boolean(loading || justLoading)}
            label={
              isPassword
                ? 'Toggle password visibility'
                : (icon ?? 'status icon')
            }
            hasIcon={isPassword ? true : hasRightIcon}
          />
        </button>
      ) : (
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3 text-content-layout-1">
          <IconWithSpinner
            icon={resolvedRightIcon as IconListType}
            loading={Boolean(loading || justLoading)}
            label={
              isPassword
                ? 'Toggle password visibility'
                : (icon ?? 'status icon')
            }
            hasIcon={isPassword ? true : hasRightIcon}
          />
        </div>
      )
    ) : null

    const containerClasses = styles.container({ class: containerClassName })
    const inputClasses = styles.input({ class: className })

    return (
      <div className={containerClasses}>
        {hasLeftIcon && (
          <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-content-layout-3">
            <Icon name={icon!} label={icon!} />
          </div>
        )}
        <input
          ref={ref}
          disabled={isDisabled}
          type={inputType}
          className={inputClasses}
          {...restProps}
        />
        {rightSlot}
      </div>
    )
  }
)

BaseInputText.displayName = 'BaseInputText'

export { BaseInputText, inputTextStyles }
