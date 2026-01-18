import * as ToastPrimitives from '@radix-ui/react-toast'
import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type ComponentRef,
  forwardRef,
  type ReactElement,
} from 'react'
import type { WithClassName } from '../../../helpers/types'
import type { Button } from '../../element/button'
import { Icon } from '../../svg/icon'

const toastStyles = tv({
  slots: {
    root: [
      'pointer-events-auto',
      'relative',
      'flex',
      'w-full',
      'justify-between',
      'items-center',
      'gap-2',
      'overflow-hidden',
      'rounded-xl',
      'border-(length:--border-base)',
      'p-4',
      'pr-8',
      'shadow-lg',
      'transition-all',

      //TODO: Fix animations
      'data-swipe-cancel:translate-x-0',
      'data-swipe-end:translate-x-var(--radix-toast-swipe-end-x)',
      'data-swipe-move:translate-x-var(--radix-toast-swipe-move-x)',
      'data-swipe-move:transition-none',
      'data-state-open:animate-in',
      'data-state-open:slide-in-from-bottom-100%',
      'data-state-closed:animate-out',
      'data-state-closed:fade-out-80%',
      'data-state-closed:slide-out-to-right-100%',
    ],
    viewport: [
      'fixed',
      'top-0',
      'z-100',
      'flex',
      'max-h-screen',
      'w-full',
      'flex-col-reverse',
      'p-4',
      'tablet:bottom-0',
      'tablet:right-0',
      'tablet:top-auto',
      'tablet:flex-col',
      'desktop:max-w-[34rem]',
    ],
    title: ['text-subtitle-1', 'flex', 'items-center', 'text-content-layout-1'],
    description: ['text-caption', 'text-content-layout-3'],
    close: [
      'absolute',
      'right-4',
      'top-4',
      'rounded-sm',
      'opacity-70',
      'text-content-layout-3',
      'transition',
      'duration-slow',
      'ease-base',
      'cursor-pointer',
      'hover:opacity-100',
      'focus:outline-2',
      'focus:outline-solid',
      'focus:outline-offset-2',
      'focus:outline-transparent',
      'shadow-[0_0_0_2px_#ccc]',
      'disabled:pointer-events-none',
    ],
  },

  variants: {
    variant: {
      primary: {
        root: [
          'border-border-layout-1',
          'bg-surface-layout-1',
          'text-content-layout-1',
        ],
      },
      negative: {
        root: [
          'border-border-negative-soft',
          'bg-surface-negative-soft',
          'text-content-negative-soft',
        ],
      },
      positive: {
        root: [
          'border-border-positive-soft',
          'bg-surface-positive-soft',
          'text-content-positive-soft',
        ],
      },
      warning: {
        root: [
          'border-border-warning-soft',
          'bg-surface-warning-soft',
          'text-content-warning-soft',
        ],
      },
      informative: {
        root: [
          'border-border-info-soft',
          'bg-surface-info-soft',
          'text-content-info-soft',
        ],
      },
    },
  },
  defaultVariants: {
    variant: 'primary',
  },
})

const ToastProvider = ToastPrimitives.Provider

type ToastViewportProps = ComponentPropsWithoutRef<
  typeof ToastPrimitives.Viewport
> &
  WithClassName

const ToastViewport = forwardRef<
  ComponentRef<typeof ToastPrimitives.Viewport>,
  ToastViewportProps
>(({ className, ...props }, ref) => (
  <ToastPrimitives.Viewport
    ref={ref}
    className={cn(toastStyles().viewport({ className }))}
    {...props}
  />
))

type ToastProps = ComponentPropsWithoutRef<typeof ToastPrimitives.Root> &
  VariantProps<typeof toastStyles> &
  WithClassName

const Toast = forwardRef<ComponentRef<typeof ToastPrimitives.Root>, ToastProps>(
  ({ className, variant, children, ...props }, ref) => (
    <ToastPrimitives.Root
      ref={ref}
      className={cn(toastStyles().root({ variant, className }), 'group')}
      {...props}
    >
      {children}
    </ToastPrimitives.Root>
  )
)

type ToastCloseProps = ComponentPropsWithoutRef<typeof ToastPrimitives.Close> &
  WithClassName

const ToastClose = ({ className, ...props }: ToastCloseProps) => (
  <ToastPrimitives.Close
    className={cn(toastStyles().close({ className }))}
    toast-close=""
    {...props}
  >
    <Icon name="close" label="Close" />
  </ToastPrimitives.Close>
)

type ToastTitleProps = ComponentPropsWithoutRef<typeof ToastPrimitives.Title> &
  WithClassName

const ToastTitle = ({ className, ...props }: ToastTitleProps) => (
  <ToastPrimitives.Title
    className={cn(toastStyles().title({ className }))}
    {...props}
  />
)

type ToastDescriptionProps = ComponentPropsWithoutRef<
  typeof ToastPrimitives.Description
> &
  WithClassName

const ToastDescription = ({ className, ...props }: ToastDescriptionProps) => (
  <ToastPrimitives.Description
    className={cn(toastStyles().description({ className }))}
    {...props}
  />
)

type ToastActionElement = ReactElement<typeof Button>

export {
  Toast,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
  type ToastActionElement,
  type ToastProps,
}
