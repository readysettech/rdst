'use client'

import * as Dialog from '@radix-ui/react-dialog'
import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import { AnimatePresence, m } from 'motion/react'
import {
  Children,
  type ComponentPropsWithoutRef,
  type ComponentRef,
  cloneElement,
  forwardRef,
  isValidElement,
  type ReactElement,
} from 'react'
import type { WithChildren } from '../../helpers/types'
import { useDisclosure } from '../../hooks/use-disclosure'
import { getTransition } from '../../motion/transition'
import { Button } from '../element/button'
import { Icon } from '../svg/icon'

const modalStyles = tv({
  slots: {
    overlay: [
      'fixed',
      'inset-0',
      'z-50',
      'backdrop-blur-[8px]',
      'bg-[black]/50',
    ],
    content: [
      'fixed',
      'top-1/2',
      'left-1/2',
      'z-50',
      'flex',
      'flex-col',
      'max-h-[90dvh]',
      'h-fit',
      'overflow-hidden',
      'gap-0',
      'p-6',
      'bg-surface-layout-2',
      'shadow-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'rounded-[1.25rem]',
      'notranslate',
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
      'transition-opacity',
      'duration-slower',
      'ease-base',
      'cursor-pointer',
      'hover:opacity-100',
      'focus-visible:outline-none',
      'focus-visible:ring-2',
      'focus-visible:ring-border-primary-soft',
      'focus-visible:ring-offset-2',
      'focus-visible:ring-offset-surface-layout-2',
      'disabled:pointer-events-none',
    ],
  },
  variants: {
    size: {
      base: {
        content: ['w-full', 'max-w-lg'],
      },
      large: {
        content: ['w-full', 'max-w-2xl'],
      },
      'extra-large': {
        content: ['w-full', 'max-w-4xl'],
      },
      full: {
        content: ['w-full', 'max-w-full', 'max-h-[100dvh]', 'h-full'],
      },
    },
  },
  defaultVariants: {
    size: 'base',
  },
})

type ModalVariants = VariantProps<typeof modalStyles>

type ModalChildProps = {
  open?: boolean
}

type ModalProps = Dialog.DialogProps & {
  blockClose?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export const Modal = ({
  open,
  blockClose,
  onOpenChange,
  children,
  ...props
}: ModalProps) => {
  const [isOpen, setOpen] = useDisclosure({ open, onOpenChange, blockClose })

  return (
    <Dialog.Root open={open} onOpenChange={setOpen} {...props}>
      {Children.map(children, (child) => {
        if (isValidElement(child)) {
          return cloneElement(child as ReactElement<ModalChildProps>, {
            open: isOpen,
          })
        }
        return child
      })}
    </Dialog.Root>
  )
}

export const ModalTrigger = Dialog.Trigger
export const ModalClose = Dialog.Close

export const ModalContentContainer = ({
  children,
  open,
}: WithChildren & { open?: boolean }) => (
  <AnimatePresence>{open && children}</AnimatePresence>
)

export type ModalContentProps = {
  layoutId?: string
  hideClose?: boolean
  overlayClassName?: string
  innerClassName?: string
  closeClassName?: string
  /**
   * Accessible dialog title. When the content has no visible `ModalTitle`,
   * pass this so Radix has a required `Dialog.Title` (rendered screen-reader
   * only) and stops warning "`DialogContent` requires a `DialogTitle`".
   */
  title?: string
  /**
   * Accessible dialog description, wired to `aria-describedby`. Rendered
   * screen-reader only when the content has no visible description.
   */
  description?: string
} & ComponentPropsWithoutRef<typeof Dialog.Content> &
  ModalVariants

export const ModalContent = forwardRef<
  ComponentRef<typeof Dialog.Content>,
  ModalContentProps
>(
  (
    {
      children,
      layoutId,
      hideClose,
      size,
      className,
      overlayClassName,
      innerClassName,
      closeClassName,
      title,
      description,
      ...props
    },
    ref
  ) => {
    const transition = getTransition()
    const styles = modalStyles({ size })

    return (
      <Dialog.Portal forceMount>
        <Dialog.Overlay
          className={styles.overlay({ class: overlayClassName })}
          asChild
        >
          <m.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={transition}
          />
        </Dialog.Overlay>
        <Dialog.Content
          ref={ref}
          className={styles.content({ class: className })}
          translate="no"
          contentEditable={false}
          {...props}
          asChild
        >
          <m.div
            layoutId={layoutId}
            initial={{ scale: 0.9, opacity: 0, y: '-50%', x: '-50%' }}
            animate={{ scale: 1, opacity: 1, y: '-50%', x: '-50%' }}
            exit={{ scale: 0.9, opacity: 0, y: '-50%', x: '-50%' }}
            transition={transition}
            className={cn(innerClassName)}
          >
            {title ? (
              <Dialog.Title className="sr-only">{title}</Dialog.Title>
            ) : null}
            {description ? (
              <Dialog.Description className="sr-only">
                {description}
              </Dialog.Description>
            ) : null}
            {children}
            {!hideClose && (
              <Dialog.Close className={styles.close({ class: closeClassName })}>
                <Icon name="close" label="Close modal" />
              </Dialog.Close>
            )}
          </m.div>
        </Dialog.Content>
      </Dialog.Portal>
    )
  }
)

export const ModalBackdropWithPortal = () => {
  const transition = getTransition()
  const styles = modalStyles()

  return (
    <Dialog.Portal forceMount>
      <Dialog.Overlay className={styles.overlay()} asChild>
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={transition}
        />
      </Dialog.Overlay>
    </Dialog.Portal>
  )
}

export const ModalTitle = ({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof Dialog.Title>) => (
  <Dialog.Title
    className={modalStyles().title({ class: className })}
    {...props}
  />
)

export const ModalDescription = ({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof Dialog.Description>) => (
  <Dialog.Description
    className={modalStyles().description({ class: className })}
    {...props}
  />
)

type ModalContentWithTextProps = {
  title: string
  description: string
} & ModalContentProps

export const ModalContentWithText = ({
  title,
  description,
  children,
  className,
  ...props
}: ModalContentWithTextProps) => {
  const styles = modalStyles()

  return (
    <ModalContent className={cn('p-0', className)} {...props}>
      <div
        className={cn(
          styles.header(),
          'border-b-(length:--border-base) border-border-layout-1 p-6'
        )}
      >
        <ModalTitle>{title}</ModalTitle>
        <ModalDescription>{description}</ModalDescription>
      </div>
      <div
        className={cn(
          styles.footer(),
          'gap-2 border-t-(length:--border-base) border-border-layout-1 p-6'
        )}
      >
        <ModalClose asChild>
          <Button label="Cancel" modifier="ghost" />
        </ModalClose>
        {children}
      </div>
    </ModalContent>
  )
}
