// Turning a database's monitoring off asks first.
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { useToast } from '@rs/ui-new/use-toast'
import { useUpdateMonitorSettings } from '@/lib/monitor/hooks'

const TURN_OFF_MESSAGE =
  'RDST stops reading this database and frees one of your AI monitoring spots. Charts keep the history recorded so far, and new changes go unnoticed until you turn it back on.'

/** Asks before monitoring turns off, then turns it off. */
export function TurnOffMonitoringDialog({
  target,
  open,
  onClose,
}: {
  target: string
  open: boolean
  onClose: () => void
}) {
  const update = useUpdateMonitorSettings(target)
  const { toast } = useToast()
  return (
    <ConfirmDialog
      tone="quiet"
      isOpen={open}
      onClose={onClose}
      onConfirm={() => {
        onClose()
        update.mutate(
          { enabled: false },
          {
            onSuccess: () =>
              toast({
                title: `Monitoring is off for ${target}`,
                variant: 'positive',
              }),
            onError: (error) =>
              toast({
                title: "Couldn't turn off monitoring",
                description: error instanceof Error ? error.message : undefined,
                variant: 'negative',
              }),
          }
        )
      }}
      title={`Turn off monitoring for ${target}?`}
      subtitle={TURN_OFF_MESSAGE}
      confirmLabel="Turn off monitoring"
      confirmVariant="negative"
    />
  )
}
