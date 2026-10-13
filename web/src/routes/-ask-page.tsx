// The Ask page: the bar with the database filter, then the question box as the page.
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Text } from '@rs/ui-new/text'
import { AiSetupNotice, useAiBlocked } from '../components/AiSetupNotice'
import { AskPanel } from '../components/AskPanel'
import { useOpenAddDatabase } from '../components/configure/addDatabase'
import { TargetConnectivityNotice } from '../components/TargetConnectivityNotice'
import { TargetLockNotice } from '../components/TargetLockNotice'
import { DatabaseFilter } from '../features/shared/filters/DatabaseFilter'
import { useDbFilter } from '../hooks/useDbFilter'
import { useTargetResolution } from '../hooks/useTarget'
import { useConfiguredDatabases } from '../lib/monitor/hooks'
import { useTargetConnectivityGate } from '../lib/useTargetConnectivityGate'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'

export function AskPage({
  initialQuestion,
}: {
  initialQuestion?: string
} = {}) {
  const resolution = useTargetResolution()
  const configured = useConfiguredDatabases()
  const { databases } = configured
  // A stored choice counts only while it names a configured database.
  const listed = (name: string | null) =>
    name &&
    (configured.isPending || databases.some((entry) => entry.name === name))
      ? name
      : null
  const filter = useDbFilter('ask')
  const db = listed(filter.db)
  const openAddDatabase = useOpenAddDatabase()
  // All databases asks the first monitored one; the filter row says so.
  const firstMonitored =
    databases.find((entry) => entry.state && entry.monitored)?.name ?? null
  const target = db ?? firstMonitored ?? listed(resolution.target)
  const isResolving = !target && resolution.isResolving
  const { isUnavailable, refetch } = resolution
  const noDatabase =
    !target && !isResolving && !configured.isPending && databases.length === 0
  const aiBlocked = useAiBlocked()
  const passwordLock = useTargetPasswordLock(target)
  const connectivity = useTargetConnectivityGate(
    passwordLock.targetName ?? target
  )

  return (
    <>
      <PageBar
        title="Ask"
        tabs={
          <div className="flex min-h-12 items-center gap-3">
            <DatabaseFilter
              value={db}
              onChange={filter.setDb}
              allOption={databases.length > 1}
            />
            {!db && target && databases.length > 1 && (
              <Text as="span" level="caption" className="text-content-layout-3">
                {target === firstMonitored
                  ? `Asks ${target}, the first monitored database.`
                  : `Asks ${target}.`}
              </Text>
            )}
          </div>
        }
      />
      <PageContainer width="reading" className="pt-9 pb-24">
        <div className="space-y-4">
          <AiSetupNotice />
          {passwordLock.isLocked && (
            <TargetLockNotice
              message={passwordLock.message}
              requirements={passwordLock.missingTargetRequirements}
              keyringAvailable={passwordLock.keyringAvailable}
            />
          )}
          {!passwordLock.isLocked && (
            <TargetConnectivityNotice
              target={passwordLock.targetName ?? target}
              failure={connectivity.failure}
              isChecking={connectivity.isChecking}
              onRetry={() => void connectivity.ensureReachable()}
            />
          )}
        </div>
        {noDatabase ? (
          <EmptyState
            tone="quiet"
            title="No database yet"
            body="Add a database to start. Ask answers questions from its tables."
            action={{
              label: 'Add a database',
              onClick: () => openAddDatabase(),
            }}
          />
        ) : isUnavailable ? (
          <ErrorState
            tone="quiet"
            errorClass="rdst-service"
            title="Couldn't list your databases"
            message="RDST could not list your databases, so Ask has nothing to run against yet."
            trustworthy="Nothing was asked or changed."
            onRetry={() => void refetch()}
          />
        ) : (
          <AskPanel
            initialQuestion={initialQuestion}
            target={target}
            targetResolving={isResolving}
            disabled={
              aiBlocked || passwordLock.isLocked || connectivity.isChecking
            }
            beforeRun={connectivity.ensureReachable}
          />
        )}
      </PageContainer>
    </>
  )
}
