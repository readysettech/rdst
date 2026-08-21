// Load-test workspace component. This stays route-ignored so the SQL editor
// stack remains outside the eager route chunk.
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { VStack } from '@rs/ui-new/stack'
import { TargetConnectivityNotice, TargetLockNotice } from '../components'
import { BenchmarkConfirmDialog } from '../components/BenchmarkConfirmDialog'
import { LoadTestResults } from '../features/caching/load-test/LoadTestResults'
import { LoadTestSetup } from '../features/caching/load-test/LoadTestSetup'
import { deriveLoadTestResultModel } from '../features/caching/load-test/loadTestModel'
import {
  BENCHMARK_EXECUTION_CAP,
  useLoadTestController,
} from '../features/caching/load-test/useLoadTestController'

export function BenchmarkPage({
  selectedRunId,
  onClearSelectedRun,
  onFindQueries,
}: {
  selectedRunId?: string
  onClearSelectedRun?: () => void
  onFindQueries?: () => void
}) {
  const controller = useLoadTestController({
    selectedRunId,
    onClearSelectedRun,
  })
  const {
    destinationLock,
    connectivity,
    state,
    runStage,
    runMessage,
    progress,
    timeline,
    activeRequest,
    runStatus,
    error,
    stop,
    pageState,
    intervalMs,
    durationSeconds,
    confirmOpen,
    setConfirmOpen,
    handleConfirmRun,
    handleBack,
    handleRunAgain,
    confirmTarget,
    confirmIsRemote,
    confirmQueryCount,
    confirmLoadSummary,
    confirmIncludesReadyset,
    confirmEstimatedExecutions,
  } = controller
  const confirmDialog = (
    <BenchmarkConfirmDialog
      isOpen={confirmOpen}
      target={confirmTarget}
      isRemote={confirmIsRemote}
      queryCount={confirmQueryCount}
      loadSummary={confirmLoadSummary}
      includesReadyset={confirmIncludesReadyset}
      estimatedExecutions={confirmEstimatedExecutions}
      executionCap={BENCHMARK_EXECUTION_CAP}
      onConfirm={handleConfirmRun}
      onClose={() => setConfirmOpen(false)}
    />
  )

  if (pageState === 'configure') {
    return (
      <LoadTestSetup controller={controller} onFindQueries={onFindQueries} />
    )
  }

  const resultModel = deriveLoadTestResultModel({
    state,
    status: runStatus,
    stage: runStage,
    progress,
    request: activeRequest,
    fallbackDurationSeconds: durationSeconds,
    fallbackIntervalMs: intervalMs,
  })

  return (
    <VStack className="w-full items-stretch gap-6">
      {confirmDialog}
      <AnimatePresence>
        {destinationLock.isLocked && (
          <m.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
          >
            <TargetLockNotice
              message={destinationLock.message}
              requirements={destinationLock.missingTargetRequirements}
              keyringAvailable={destinationLock.keyringAvailable}
            />
          </m.div>
        )}
      </AnimatePresence>

      {(connectivity.failure || connectivity.isChecking) &&
        !destinationLock.isLocked && (
          <TargetConnectivityNotice
            target={destinationLock.targetName}
            failure={connectivity.failure}
            isChecking={connectivity.isChecking}
            onRetry={() => void connectivity.ensureReachable()}
          />
        )}

      <LoadTestResults
        model={resultModel}
        progress={progress}
        timeline={timeline}
        request={activeRequest}
        runMessage={runMessage}
        error={error}
        targetLocked={destinationLock.isLocked}
        onStop={stop}
        onAdjust={handleBack}
        onRunAgain={handleRunAgain}
      />
    </VStack>
  )
}
