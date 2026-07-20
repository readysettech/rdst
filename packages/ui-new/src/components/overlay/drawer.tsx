'use client'

import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import { AnimatePresence, m } from 'motion/react'
import {
  Children,
  type ComponentProps,
  type ComponentPropsWithoutRef,
  type ComponentRef,
  cloneElement,
  forwardRef,
  isValidElement,
  type ReactElement,
} from 'react'
import { Drawer as DrawerPrimitive } from 'vaul'
import type { WithChildren } from '../../helpers/types'
import { useDisclosure } from '../../hooks/use-disclosure'
import { getTransition } from '../../motion/transition'
import { Icon } from '../svg/icon'

const drawerStyles = tv({
  slots: {
    overlay: ['fixed', 'inset-0', 'z-50', 'backdrop-blur-sm', 'bg-surface-scrim'],
    content: [
      'fixed',
      'z-50',
      'flex',
      'h-full',
      'flex-col',
      'overflow-hidden',
      'select-auto',
      'gap-0',
      'p-6',
      'bg-surface-layout-1',
      'shadow-large',
      'border-(length:--border-base)',
      'border-border-layout-1',
    ],
    header: ['flex', 'flex-col', 'gap-1.5', 'text-center', 'tablet:text-left'],
    footer: [
      'flex',
      'flex-col-reverse',
      'tablet:flex-row',
      'tablet:justify-end',
      'tablet:gap-2',
    ],
    title: [
      'text-headline-4',
      'h-10',
      'flex',
      'items-center',
      'text-content-layout-1',
    ],
    description: ['text-body-small', 'text-content-layout-3'],
    close: [
      'absolute',
      'right-6',
      'top-6',
      'rounded-md',
      'opacity-70',
      'text-content-layout-3',
      'transition-[opacity]',
      'duration-slower',
      'ease-base',
      'cursor-pointer',
      'hover:opacity-100',
      'focus-visible:outline-none',
      'focus-visible:ring-2',
      'focus-visible:ring-border-primary-soft',
      'focus-visible:ring-offset-2',
      'focus-visible:ring-offset-surface-layout-1',
      'disabled:pointer-events-none',
    ],
  },
  variants: {
    size: {
      base: {
        content: ['w-full'],
      },
      large: {
        content: ['w-full'],
      },
      XLarge: {
        content: ['w-full'],
      },
    },
    direction: {
      right: {
        content: [
          'inset-y-0',
          'right-0',
          'h-full',
          'max-h-[100dvh]',
          'rounded-l-[1.25rem]',
        ],
      },
      bottom: {
        content: [
          'inset-x-0',
          'bottom-0',
          'w-full',
          'max-h-[95dvh]',
          'max-w-[100vw]',
          'rounded-t-[1.25rem]',
        ],
      },
    },
  },
  compoundVariants: [
    {
      size: 'base',
      direction: 'right',
      class: {
        content: ['max-w-lg'],
      },
    },
    {
      size: 'large',
      direction: 'right',
      class: {
        content: ['max-w-2xl'],
      },
    },
    {
      size: 'XLarge',
      direction: 'right',
      class: {
        content: ['max-w-4xl'],
      },
    },
  ],
  defaultVariants: {
    size: 'base',
    direction: 'right',
  },
})

type DrawerVariants = VariantProps<typeof drawerStyles>

type DrawerChildProps = {
  open?: boolean
}

type DrawerProps = ComponentProps<typeof DrawerPrimitive.Root> & {
  blockClose?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export const Drawer = ({
  shouldScaleBackground = true,
  open,
  blockClose,
  onOpenChange,
  children,
  direction = 'right',
  ...props
}: DrawerProps) => {
  const [isOpen, setOpen] = useDisclosure({ open, onOpenChange, blockClose })

  return (
    <DrawerPrimitive.Root
      shouldScaleBackground={shouldScaleBackground}
      // dismissible={false}
      direction={direction}
      onOpenChange={setOpen}
      open={isOpen}
      {...props}
    >
      {Children.map(children, (child) => {
        if (isValidElement(child)) {
          return cloneElement(child as ReactElement<DrawerChildProps>, {
            open: isOpen,
          })
        }
        return child
      })}
    </DrawerPrimitive.Root>
  )
}

export const DrawerTrigger = DrawerPrimitive.Trigger
export const DrawerClose = DrawerPrimitive.Close

export const DrawerContentContainer = ({ children }: WithChildren) => (
  <AnimatePresence>{children}</AnimatePresence>
)

type DrawerContentProps = {
  layoutId?: string
  hideCloseButton?: boolean
  blockBackdropClose?: boolean
  overlayClassName?: string
  innerClassName?: string
  closeClassName?: string
} & ComponentPropsWithoutRef<typeof DrawerPrimitive.Content> &
  DrawerVariants

export const DrawerContent = forwardRef<
  ComponentRef<typeof DrawerPrimitive.Content>,
  DrawerContentProps
>(
  (
    {
      children,
      layoutId,
      size,
      direction,
      hideCloseButton,
      blockBackdropClose,
      className,
      overlayClassName,
      innerClassName,
      closeClassName,
      ...props
    },
    ref
  ) => {
    const transition = getTransition()
    const styles = drawerStyles({ size, direction })

    return (
      <DrawerPrimitive.Portal forceMount>
        {!blockBackdropClose ? (
          <DrawerPrimitive.Close>
            <DrawerPrimitive.Overlay
              className={styles.overlay({ class: overlayClassName })}
            >
              <m.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={transition}
              />
            </DrawerPrimitive.Overlay>
          </DrawerPrimitive.Close>
        ) : (
          <DrawerPrimitive.Overlay
            className={styles.overlay({ class: overlayClassName })}
          >
            <m.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={transition}
            />
          </DrawerPrimitive.Overlay>
        )}
        <DrawerPrimitive.Content
          ref={ref}
          className={styles.content({ class: className })}
          translate="no"
          contentEditable={false}
          {...props}
          asChild
        >
          <m.div
            layoutId={layoutId}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={transition}
            className={cn(innerClassName)}
          >
            {children}
            {!hideCloseButton && (
              <DrawerPrimitive.Close
                className={styles.close({ class: closeClassName })}
              >
                <Icon name="close" label="Close Drawer" />
              </DrawerPrimitive.Close>
            )}
          </m.div>
        </DrawerPrimitive.Content>
      </DrawerPrimitive.Portal>
    )
  }
)

export const DrawerHeader = ({
  className,
  ...props
}: ComponentPropsWithoutRef<'div'>) => (
  <div className={drawerStyles().header({ class: className })} {...props} />
)

export const DrawerFooter = ({
  className,
  ...props
}: ComponentPropsWithoutRef<'div'>) => (
  <div className={drawerStyles().footer({ class: className })} {...props} />
)

export const DrawerTitle = ({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof DrawerPrimitive.Title>) => (
  <DrawerPrimitive.Title
    className={drawerStyles().title({ class: className })}
    {...props}
  />
)

export const DrawerDescription = ({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof DrawerPrimitive.Description>) => (
  <DrawerPrimitive.Description
    className={drawerStyles().description({ class: className })}
    {...props}
  />
)
