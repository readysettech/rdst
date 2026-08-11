import { InlineNotice } from '@rs/ui-new/error-state'
import { VStack } from '@rs/ui-new/stack'
import { TargetLockNotice } from '../../../components/TargetLockNotice'
import type { TopDbLimitWarningEventData } from '../../../types/top'
import { DatabaseLimitWarning } from './DatabaseLimitWarning'
import type { SlowQueriesController } from './useSlowQueriesController'

interface SlowQueriesNoticesProps {
  targetName?: string | null
  passwordLock: SlowQueriesController['target']['passwordLock']
  databaseLimitWarning: TopDbLimitWarningEventData | null
}

export function SlowQueriesNotices({
  targetName,
  passwordLock,
  databaseLimitWarning,
}: SlowQueriesNoticesProps) {
  return (
    <VStack className="gap-3 items-stretch">
      {!targetName ? (
        <InlineNotice
          errorClass="database"
          accent="warning"
          icon="database"
          title="Choose a database"
          message="Select a target database from the sidebar to find slow queries."
        />
      ) : null}

      {passwordLock.isLocked ? (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      ) : null}

      {databaseLimitWarning ? (
        <DatabaseLimitWarning warning={databaseLimitWarning} />
      ) : null}
    </VStack>
  )
}
