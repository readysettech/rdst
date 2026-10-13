// "Monitoring setup" (`/database?panel=setup`, the Inbox "Check setup" link): the live
// prerequisites checker for a target that is already monitored. It polls while the
// user fixes things elsewhere and finishes on the green tick.
import { Button } from '@rs/ui-new/button'
import { ModalDescription } from '@rs/ui-new/modal'
import {
  PrerequisitesChecker,
  usePrerequisites,
} from '@/features/prerequisites'
import { DialogShell } from '../../../components/DialogShell'

export function MonitoringSetupDialog({
  target,
  open,
  onClose,
}: {
  target: string
  open: boolean
  onClose: () => void
}) {
  const prerequisites = usePrerequisites(target, { enabled: open })
  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title="Monitoring setup"
      action={
        <Button
          variant="primary"
          modifier="ghost"
          label="Done"
          onClick={onClose}
        />
      }
      testId="monitoring-setup"
    >
      <ModalDescription className="mb-4">
        {`RDST checks what ${target} lets it read. Fix a step and it checks again on its own.`}
      </ModalDescription>
      <PrerequisitesChecker
        target={target}
        state={prerequisites}
        layout="panel"
      />
    </DialogShell>
  )
}
