import { cn } from '@rs/tailwind-base'
import { IconTile, type IconTileProps } from '@rs/ui-new/icon-tile'
import {
  ModalContent,
  type ModalContentProps,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import type { ReactNode } from 'react'

interface TaskDialogContentProps
  extends Omit<ModalContentProps, 'title' | 'description'> {
  title: ReactNode
  description?: ReactNode
  icon?: IconTileProps['icon']
  iconAccent?: IconTileProps['accent']
  children: ReactNode
  footer?: ReactNode
  bodyClassName?: string
  headerClassName?: string
  footerClassName?: string
}

/**
 * RDST's shared blocking-task dialog anatomy.
 *
 * What: compact identity header, focused task body, and a separated action
 * footer. Where: short forms and required choices that block the current page.
 * When: use a drawer or disclosure instead when the task need not interrupt
 * the page.
 */
export function TaskDialogContent({
  title,
  description,
  icon,
  iconAccent = 'primary',
  children,
  footer,
  bodyClassName,
  headerClassName,
  footerClassName,
  className,
  ...props
}: TaskDialogContentProps) {
  return (
    <ModalContent
      className={cn('gap-0 overflow-hidden p-0', className)}
      {...props}
    >
      <header
        className={cn(
          'border-b-(length:--border-base) border-border-layout-1 px-6 py-5 pr-16',
          headerClassName
        )}
      >
        <div className="flex min-w-0 items-start gap-3">
          {icon ? (
            <IconTile icon={icon} size="base" accent={iconAccent} />
          ) : null}
          <div className="min-w-0 space-y-1">
            <ModalTitle className="h-auto text-headline-4 text-content-layout-1">
              {title}
            </ModalTitle>
            {description ? (
              <ModalDescription className="max-w-[62ch] text-body-small text-content-layout-3">
                {description}
              </ModalDescription>
            ) : null}
          </div>
        </div>
      </header>

      <div className={cn('p-6', bodyClassName)}>{children}</div>

      {footer ? (
        <footer
          className={cn(
            'border-t-(length:--border-base) border-border-layout-1 bg-surface-layout-1/40 px-6 py-4',
            footerClassName
          )}
        >
          {footer}
        </footer>
      ) : null}
    </ModalContent>
  )
}
