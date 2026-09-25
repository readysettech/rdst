// "Monitoring setup" (`/database?panel=setup`, the Inbox "Check setup" link): the live
// prerequisites checker for a target that is already monitored. It polls while the
// user fixes things elsewhere, offers the connection editor when the database can't
// be reached, and finishes on the green check.
import { Button } from '@rs/ui-new/button'
import { ScrollDialog } from '@/features/monitor-kit/ScrollDialog'
import { PrerequisitesChecker } from '@/features/prerequisites'
import { ConnectionRepair } from './ConnectionRepair'
import { useConnectionRepair } from './EnableMonitoringDialog'

export function MonitoringSetupDialog({
  target,
  open,
  onClose,
}: {
  target: string
  open: boolean
  onClose: () => void
}) {
  const repair = useConnectionRepair(target, open)
  const { prerequisites } = repair
  return (
    <ScrollDialog
      open={open}
      onClose={onClose}
      title="Monitoring setup"
      description={`RDST checks what ${target} lets it read. Fix a step and it checks again on its own.`}
      footer={
        <div className="flex justify-end">
          <Button label="Done" modifier="ghost" onClick={onClose} />
        </div>
      }
      testId="monitoring-setup"
    >
      <div className="space-y-6">
        <PrerequisitesChecker
          target={target}
          state={prerequisites}
          layout="panel"
        />
        {prerequisites.phase === 'unreachable' || repair.editing ? (
          <ConnectionRepair
            target={target}
            editing={repair.editing}
            onEditingChange={repair.setEditing}
            onSaved={prerequisites.recheck}
          />
        ) : null}
      </div>
    </ScrollDialog>
  )
}
