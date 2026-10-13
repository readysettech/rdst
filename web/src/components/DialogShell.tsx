// The 480px dialog the app's dialogs share: a title with the close mark, the body, and a
// footer with a note at the left and the actions (a ghost escape, one solid) at the right.
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

export interface DialogShellProps {
  open: boolean
  onClose: () => void
  title: string
  /** The footer's left side. */
  note?: ReactNode
  /** The footer's right side: a ghost escape and the one solid action. */
  action?: ReactNode
  /** Screen-reader description, for a dialog whose body is not one paragraph. */
  description?: string
  /** An action is running: Escape, the overlay and the close mark do nothing. */
  busy?: boolean
  children: ReactNode
  testId?: string
}

export function DialogShell({
  open,
  onClose,
  title,
  note,
  action,
  description,
  busy,
  children,
  testId,
}: DialogShellProps) {
  return (
    <Modal open={open} onOpenChange={(next) => !next && !busy && onClose()}>
      <ModalContentContainer open={open}>
        <ModalContent
          tone="quiet"
          hideClose={busy}
          description={description}
          data-testid={testId}
        >
          <div className="shrink-0 pt-4.5 pr-14 pl-6">
            <ModalTitle>{title}</ModalTitle>
          </div>
          <Scrollable rootClassName="min-h-0 flex-1" className="px-6 pt-2 pb-5">
            {children}
          </Scrollable>
          {(note || action) && (
            <div className="flex shrink-0 items-center gap-3 border-t border-border-layout-2 px-6 py-3.5">
              <Text
                as="p"
                level="caption"
                className="flex-1 text-content-layout-3"
              >
                {note}
              </Text>
              {action}
            </div>
          )}
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
