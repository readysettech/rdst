'use client'

import * as PopoverPrimitive from '@radix-ui/react-popover'
import { tv, type VariantProps } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type ComponentRef,
  forwardRef,
} from 'react'

export const popoverRecipe = tv({
  slots: {
    overlay: [
      'fixed',
      'inset-0',
      'z-50',
      'backdrop-blur-[8px]',
      'bg-surface-scrim',
    ],
    content: [
      'z-50',
      'min-w-72',
      'flex',
      'max-w-md',
      'overflow-hidden',
      'rounded-lg',
      'border-(length:--border-base)',
      'shadow-2xl',
      'outline-none',
      'transition',
      'duration-fast',
      'ease-base',
      'data-[state=open]:animate-in',
      'data-[state=open]:fade-in-0',
      'data-[state=open]:zoom-in-95',
      'data-[state=closed]:animate-out',
      'data-[state=closed]:fade-out-0',
      'data-[state=closed]:zoom-out-95',
      'data-[side=top]:slide-in-from-bottom-2',
      'data-[side=bottom]:slide-in-from-top-2',
      'data-[side=left]:slide-in-from-right-2',
      'data-[side=right]:slide-in-from-left-2',
    ],
  },
  variants: {
    size: {
      base: {
        content: ['text-label-small', 'py-4', 'px-6', 'rounded-lg'],
      },
      small: {
        content: ['text-label-extra-small', 'py-1', 'px-2', 'rounded-md'],
      },
    },
    variant: {
      layout: {
        content: [
          'text-content-layout-1',
          'border-border-layout-1',
          'bg-surface-layout-1',
          '[&_#loader]:border-t-content-layout-1',
        ],
      },
      primary: {
        content: [
          'text-content-primary-solid',
          'bg-surface-primary-solid',
          '[&_#loader]:border-t-content-primary-solid',
        ],
      },
      rising: {
        content: [
          'text-content-rising-solid',
          'bg-surface-rising-solid',
          '[&_#loader]:border-t-content-rising-solid',
        ],
      },
      negative: {
        content: [
          'text-content-negative-solid',
          'bg-surface-negative-solid',
          '[&_#loader]:border-t-content-negative-solid',
        ],
      },
    },
    fullWidth: {
      true: {
        content: ['w-full'],
      },
    },
    disabled: {
      true: {
        content: ['opacity-50', 'cursor-not-allowed', 'pointer-events-none'],
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
        content: ['w-7', 'px-3'],
      },
    },
    {
      iconPosition: 'icon',
      size: 'small',
      class: {
        content: ['w-8', 'px-2', 'py-2'],
      },
    },
  ],
  defaultVariants: {
    size: 'base',
    variant: 'layout',
    iconPosition: 'none',
  },
})

export const Popover = PopoverPrimitive.Root

export const PopoverTrigger = PopoverPrimitive.Trigger

type PopoverVariants = VariantProps<typeof popoverRecipe>

type PopoverContentProps = {
  loading?: boolean
} & PopoverVariants &
  ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>

export const PopoverContent = forwardRef<
  ComponentRef<typeof PopoverPrimitive.Content>,
  PopoverContentProps
>(
  (
    {
      align = 'center',
      sideOffset = 8,
      className,
      size,
      variant,
      fullWidth,
      disabled,
      iconPosition,
      loading,
      ...rest
    },
    ref
  ) => {
    const styles = popoverRecipe({
      size,
      variant,
      fullWidth,
      disabled,
      iconPosition,
    })

    return (
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          ref={ref}
          align={align}
          sideOffset={sideOffset}
          className={styles.content({ class: ['notranslate', className] })}
          translate="no"
          contentEditable={false}
          {...rest}
        />
      </PopoverPrimitive.Portal>
    )
  }
)
