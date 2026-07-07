'use client'

import { tv, type VariantProps } from '@rs/tailwind-base'
import { forwardRef, type HTMLAttributes, memo, type Ref } from 'react'
import { Icon } from '../svg/icon'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'

const tagStyles = tv({
  base: [
    'flex',
    'items-center',
    'justify-center',
    'transition-all',
    'duration-fast',
    'ease-base',
    'focus-visible:outline-none',
    'focus-visible:shadow-focus',
  ],
  variants: {
    clickable: {
      true: [
        'cursor-pointer',
        'select-none',
        'active:scale-[0.98]',
        'active:origin-center',
      ],
    },
    size: {
      base: ['text-label-small', 'py-2', 'px-3', 'rounded-md', 'h-7'],
      small: ['text-label-extra-small', 'py-1', 'px-2', 'rounded-sm', 'h-6'],
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
      true: ['w-full'],
    },
    disabled: {
      true: ['opacity-50', 'cursor-not-allowed', 'pointer-events-none'],
    },
    iconPosition: {
      none: [],
      left: [],
      'left-full': ['justify-between'],
      right: [],
      'right-full': ['justify-between'],
      icon: ['justify-center'],
    },
  },
  compoundVariants: [
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
        'border',
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
        'border-0',
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
        'border',
        'border-border-rising-soft',
        'bg-surface-rising-soft',
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
        'border-0',
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
        'border',
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
        'border-0',
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
        'border',
        'border-border-info-soft',
        'bg-surface-info-soft',
        'hover:bg-surface-info-soft-hover',
        'active:bg-surface-info-soft-active',
        '[&_#loader]:border-t-content-info-soft',
      ],
    },
    {
      variant: 'informative',
      modifier: 'ghost',
      class: [
        'text-content-info-soft',
        'border-0',
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
        'border',
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
        'border-0',
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
        'border',
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
        'border-0',
        'bg-surface-positive-soft',
        'hover:bg-surface-positive-soft-hover',
        'active:bg-surface-positive-soft-active',
        '[&_#loader]:border-t-content-positive-soft',
      ],
    },
  ],
  defaultVariants: {
    size: 'base',
    variant: 'primary',
    modifier: 'solid',
    iconPosition: 'none',
  },
})

type TagVariants = VariantProps<typeof tagStyles>

type TagProps = TagVariants & {
  icon?: IconStrokeName
  label: string
  className?: string
  classMerge?: string
} & Omit<HTMLAttributes<HTMLDivElement>, 'className'>

const Tag = forwardRef<HTMLDivElement, TagProps>((props, ref) => {
  const {
    clickable,
    size,
    variant,
    modifier,
    fullWidth,
    disabled,
    iconPosition,
    icon,
    label,
    className,
    classMerge,
    ...restProps
  } = props

  const resolvedIconPosition = iconPosition ?? 'none'

  const styles = tagStyles({
    clickable,
    size,
    variant,
    modifier,
    fullWidth,
    disabled,
    iconPosition: resolvedIconPosition,
    class: [className, classMerge],
  })

  const hasLeftIcon = icon && resolvedIconPosition.includes('left')
  const hasRightIcon = icon && resolvedIconPosition.includes('right')
  const hasJustIcon = icon && resolvedIconPosition.includes('icon')

  return (
    <div
      {...restProps}
      aria-disabled={disabled ? true : undefined}
      className={styles}
      ref={ref as Ref<HTMLDivElement>}
    >
      {(hasJustIcon || hasLeftIcon) && (
        <>
          <Icon name={icon} label={label} />
          <span className="inline-block h-1 w-1" />
        </>
      )}
      {!hasJustIcon && label}
      {!hasJustIcon && hasRightIcon && (
        <>
          <span className="inline-block h-1 w-1" />
          <Icon name={icon} label={label} />
        </>
      )}
    </div>
  )
})

const MemoizedTag = memo(Tag)

export { MemoizedTag as Tag, tagStyles, type TagProps }
