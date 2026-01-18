'use client'

import { tv, type VariantProps } from '@rs/tailwind-base'
import { forwardRef, type TextareaHTMLAttributes } from 'react'
import { Icon, type IconListType } from '../../svg/icon'
import { IconWithSpinner } from '../../svg/icon-with-spinner'

const textareaStyles = tv({
  slots: {
    container: ['relative', 'w-full', 'text-content-layout-1'],
    textarea: [
      'flex',
      'w-full',
      'min-h-[5lh]',
      'max-h-[10lh]',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-2',
      'px-3',
      'py-2',
      'text-body-medium',
      'transition',
      'duration-fast',
      'ease-base',
      'placeholder:text-content-layout-3',
      'focus-visible:outline-none',
      'focus-visible:shadow-focus',
      'disabled:cursor-not-allowed',
      'disabled:opacity-50',
      'resize-y',
    ],
  },
  variants: {
    iconPosition: {
      none: {},
      left: { textarea: ['pl-8'] },
      right: { textarea: ['pr-8'] },
      both: { textarea: ['pl-8', 'pr-8'] },
    },
    error: {
      true: {
        textarea: ['border-border-negative-soft'],
      },
    },
  },
  defaultVariants: {
    iconPosition: 'none',
  },
})

type BaseInputTextareaVariants = VariantProps<typeof textareaStyles>

export type BaseInputTextareaProps = BaseInputTextareaVariants & {
  icon?: IconListType
  iconClick?: () => void
  loading?: boolean
  containerClassName?: string
} & TextareaHTMLAttributes<HTMLTextAreaElement>

const BaseInputTextarea = forwardRef<
  HTMLTextAreaElement,
  BaseInputTextareaProps
>(
  (
    {
      icon,
      iconClick,
      loading,
      containerClassName,
      className,
      disabled,
      iconPosition,
      error,
      ...restProps
    },
    ref
  ) => {
    const hasLeftIcon = Boolean(icon && iconPosition?.includes('left'))
    const hasRightIcon = Boolean(icon && iconPosition?.includes('right'))
    const hasRightAccessory = Boolean(hasRightIcon || loading)

    const resolvedIconPosition = (() => {
      if (iconPosition === 'left' && hasRightAccessory) return 'both' as const
      if (iconPosition === 'right') return 'right' as const
      if (iconPosition === 'left') return 'left' as const
      if (hasRightAccessory) return 'right' as const
      return 'none' as const
    })()

    const styles = textareaStyles({
      iconPosition: resolvedIconPosition,
      error: error ? true : undefined,
    })

    const isDisabled = Boolean(loading || disabled)

    const handleRightClick = iconClick

    const rightSlot = hasRightAccessory ? (
      handleRightClick ? (
        <button
          type="button"
          onClick={handleRightClick}
          className="absolute inset-y-0 right-0 flex items-center pr-3 text-content-layout-3"
          disabled={Boolean(loading)}
          aria-label={icon ?? 'Input action'}
        >
          <IconWithSpinner
            icon={icon as IconListType}
            loading={Boolean(loading)}
            label={icon ?? 'status icon'}
            hasIcon={hasRightIcon}
          />
        </button>
      ) : (
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3 text-content-layout-3">
          <IconWithSpinner
            icon={icon as IconListType}
            loading={Boolean(loading)}
            label={icon ?? 'status icon'}
            hasIcon={hasRightIcon}
          />
        </div>
      )
    ) : null

    const containerClasses = styles.container({ class: containerClassName })
    const textareaClasses = styles.textarea({ class: className })

    return (
      <div className={containerClasses}>
        {hasLeftIcon && (
          <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-content-layout-3">
            <Icon name={icon as IconListType} label={icon as IconListType} />
          </div>
        )}
        <textarea
          ref={ref}
          disabled={isDisabled}
          className={textareaClasses}
          {...restProps}
        />
        {rightSlot}
      </div>
    )
  }
)

BaseInputTextarea.displayName = 'BaseInputTextarea'

export { BaseInputTextarea, textareaStyles }
