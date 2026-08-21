import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { VStack } from '@rs/ui-new/stack'
import { TargetConnectivityNotice, TargetLockNotice } from '../../../components'
import { CompareHistory } from './CompareHistory'
import { CompareQueryCard } from './CompareQueryCard'
import { CompareSetup } from './CompareSetup'
import { CompareVerdictBand } from './CompareVerdictBand'
import { CompareSkeleton, compareErrorDetail } from './compareUi'
import {
  DEFAULT_COMPARE_DURATION,
  useCompareController,
} from './useCompareController'

export function ComparePage({
  initialQueryHash,
  onOpenQueries,
  onFindQueries,
}: {
  initialQueryHash?: string
  onOpenQueries: () => void
  onFindQueries: () => void
}) {
  const controller = useCompareController(initialQueryHash)
  const waitingForStatus =
    controller.statusQuery.isLoading || !controller.passwordLock.isResolved
  const sandboxUnavailable =
    controller.statusQuery.data &&
    (!controller.statusQuery.data.docker_installed ||
      !controller.statusQuery.data.docker_running)
  const hasRun = !!controller.batch
  const queryHashByCacheId = new Map(
    (controller.batch?.queries ?? []).map((query) => [
      query.cacheId,
      query.queryHash,
    ])
  )

  const statusError = compareErrorDetail(controller.statusQuery.error)
  const listError = controller.registry.listError

  return (
    <div className="h-full min-h-0 w-full space-y-6 overflow-y-auto">
      {controller.historyOpen && <CompareHistory controller={controller} />}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        (controller.connectivity.failure ||
          controller.connectivity.isChecking) && (
          <TargetConnectivityNotice
            target={controller.passwordLock.targetName ?? controller.target}
            failure={controller.connectivity.failure}
            isChecking={controller.connectivity.isChecking}
            onRetry={() => void controller.startComparison()}
            retryLabel="Try comparison again"
          />
        )}

      {!controller.historyOpen &&
        !hasRun &&
        controller.passwordLock.isLocked && (
          <TargetLockNotice
            message={controller.passwordLock.message}
            requirements={controller.passwordLock.missingTargetRequirements}
            keyringAvailable={controller.passwordLock.keyringAvailable}
          />
        )}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        waitingForStatus && <CompareSkeleton />}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        !waitingForStatus &&
        controller.statusQuery.isError && (
          <ErrorState
            errorClass="local-dependency"
            title="Cache status couldn't be checked"
            message="Readyset couldn't verify the deployment before comparing performance."
            trustworthy="No comparison was started."
            detail={statusError}
            onRetry={() => void controller.statusQuery.refetch()}
          />
        )}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        !waitingForStatus &&
        !controller.statusQuery.isError &&
        sandboxUnavailable && (
          <EmptyState
            icon="database-settings"
            title="Docker is required for comparisons"
            body="Start Docker so RDST can prepare its temporary Readyset sandbox."
            action={{
              label: 'Check again',
              icon: 'observe',
              onClick: () => void controller.statusQuery.refetch(),
            }}
          />
        )}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        !waitingForStatus &&
        !controller.statusQuery.isError &&
        !sandboxUnavailable &&
        controller.registry.isLoading && <CompareSkeleton />}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        !waitingForStatus &&
        !controller.statusQuery.isError &&
        !sandboxUnavailable &&
        !!controller.registry.listError && (
          <ErrorState
            errorClass="database"
            title="Queries couldn't be loaded"
            message="RDST couldn't read the queries available for comparison."
            trustworthy="The sandbox and previous comparison results were not changed."
            detail={listError ?? undefined}
            onRetry={() => void controller.registry.refetch()}
          />
        )}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        !waitingForStatus &&
        !controller.statusQuery.isError &&
        !sandboxUnavailable &&
        !controller.registry.isLoading &&
        !controller.registry.listError &&
        controller.queries.length === 0 && (
          <EmptyState
            icon="layers"
            title="No queries to compare"
            body="Add or discover a high-impact query before measuring its Readyset speedup. Cacheability is checked when the comparison runs."
            action={{
              label: 'Find queries',
              icon: 'search',
              onClick: onFindQueries,
            }}
          />
        )}

      {!controller.historyOpen &&
        !hasRun &&
        !controller.passwordLock.isLocked &&
        !waitingForStatus &&
        !controller.statusQuery.isError &&
        !sandboxUnavailable &&
        !controller.registry.isLoading &&
        !controller.registry.listError &&
        controller.queries.length > 0 &&
        !hasRun && <CompareSetup controller={controller} />}

      {/* Running and settled are the same screen: the band above swaps its
          verdict and each card settles in place, so the stack of queries never
          reorders and the measuring card stays where the reader left it. */}
      {!controller.historyOpen && hasRun && controller.snapshot && (
        <VStack className="items-stretch gap-4">
          <CompareVerdictBand controller={controller} />
          {controller.snapshot.queryOutcomes.map((outcome) => (
            <CompareQueryCard
              key={outcome.runId ?? outcome.cacheId}
              outcome={outcome}
              queryHash={queryHashByCacheId.get(outcome.cacheId)}
              durationSeconds={
                controller.batch?.durationSeconds ?? DEFAULT_COMPARE_DURATION
              }
              onOpenQueries={onOpenQueries}
            />
          ))}
        </VStack>
      )}
    </div>
  )
}
