import { ParameterDialog } from '../../../components/top'
import type { TopMode } from '../../../types/top'
import { SlowQueriesControlCard } from './SlowQueriesControlCard'
import { SlowQueriesNotices } from './SlowQueriesNotices'
import { SlowQueryResults } from './SlowQueryResults'
import { useSlowQueriesController } from './useSlowQueriesController'

export function SlowQueriesPage({
  initialMode,
  realtimeOnly = false,
}: {
  initialMode?: TopMode
  realtimeOnly?: boolean
} = {}) {
  const controller = useSlowQueriesController({ initialMode })
  const { target, filters, run, cache, parameterDialog, actions } = controller

  return (
    <div className="space-y-6 w-full">
      <SlowQueriesControlCard
        controller={controller}
        showModeSwitcher={!realtimeOnly}
      />

      <SlowQueriesNotices
        targetName={target.name}
        passwordLock={target.passwordLock}
        databaseLimitWarning={run.dbLimitWarning}
      />

      <SlowQueryResults
        queries={run.queries}
        state={run.state}
        isRealtime={filters.mode === 'realtime'}
        onAnalyze={actions.analyze}
        onCache={actions.cache}
        cachingHash={cache.cachingHash}
        isCached={cache.isCached}
        error={run.error}
        onStart={target.canRun ? actions.start : undefined}
        onRetry={target.canRun ? actions.start : undefined}
      />

      <ParameterDialog
        isOpen={parameterDialog.query !== null}
        onClose={parameterDialog.close}
        onSubmit={parameterDialog.submit}
        query={parameterDialog.query ?? ''}
        target={target.name}
        queryHash={parameterDialog.queryHash}
        initialValues={parameterDialog.initialValues}
      />
    </div>
  )
}
