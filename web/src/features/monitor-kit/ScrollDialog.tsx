// A dialog that always fits the window: the title stays at the top, the actions stay at
// the bottom, and only the body between them scrolls. Long checklists, fixes and
// results never push the actions out of reach, down to a 390 px phone and a short
// 1280x720 window.
import { cn } from '@rs/tailwind-base'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import type { ReactNode } from 'react'

export function ScrollDialog({
  open,
  onClose,
  title,
  description,
  footer,
  children,
  size = 'large',
  blockClose,
  testId,
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  /** Pinned under the body; usually the dialog's actions. */
  footer?: ReactNode
  children: ReactNode
  size?: 'base' | 'large' | 'extra-large'
  blockClose?: boolean
  testId?: string
}) {
  return (
    <Modal
      open={open}
      blockClose={blockClose}
      onOpenChange={(value) => !value && onClose()}
    >
      <ModalContentContainer open={open}>
        <ModalContent
          size={size}
          data-testid={testId}
          className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] gap-0 p-0 tablet:max-h-[min(90dvh,56rem)]"
          closeClassName="right-4 top-4"
        >
          <div className="shrink-0 space-y-1 border-b border-border-layout-1 py-4 pl-5 pr-14 tablet:px-6 tablet:pr-14">
            <ModalTitle className="h-auto min-h-8 text-left">
              {title}
            </ModalTitle>
            {description ? (
              <ModalDescription className="text-left">
                {description}
              </ModalDescription>
            ) : null}
          </div>
          <div
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 tablet:px-6"
            data-testid={testId ? `${testId}-body` : undefined}
          >
            {children}
          </div>
          {footer ? (
            <div
              className={cn(
                'shrink-0 border-t border-border-layout-1 bg-surface-layout-2 px-5 py-3.5 tablet:px-6'
              )}
            >
              {footer}
            </div>
          ) : null}
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
