import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { thisComputer } from '@/lib/desktop'

export interface ShutdownDialogProps {
  isOpen: boolean
  onClose: () => void
  onConfirm: () => void
}

/** The destructive confirm behind "Shut down demo": what goes, what stays. */
export function ShutdownDialog({
  isOpen,
  onClose,
  onConfirm,
}: ShutdownDialogProps) {
  return (
    <ConfirmDialog
      tone="quiet"
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={onConfirm}
      title="Shut down the demo?"
      subtitle={`Removes the demo's four containers and the orders data. Nothing else on ${thisComputer()} changes; the images stay for next time.`}
      note="About 10 seconds."
      cancelLabel="Keep running"
      confirmLabel="Shut down"
      confirmVariant="negative"
    />
  )
}
