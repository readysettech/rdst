import { Card } from '@rs/ui-new/card-2'
import { SlowQueryControls } from './SlowQueryControls'
import { SlowQueryResultsHeader } from './SlowQueryResultsHeader'
import { SlowQueryRunStatus } from './SlowQueryRunStatus'
import type { SlowQueriesController } from './useSlowQueriesController'

interface SlowQueriesControlCardProps {
  controller: SlowQueriesController
  showModeSwitcher?: boolean
}

export function SlowQueriesControlCard({
  controller,
  showModeSwitcher = true,
}: SlowQueriesControlCardProps) {
  const { target, filters, run, actions } = controller

  return (
    <Card role="region" aria-label="Slow query controls">
      <Card.Content
        className="flex flex-col gap-3 p-4"
        data-testid="slow-query-control-content"
      >
        <SlowQueryControls
          mode={filters.mode}
          setMode={filters.setMode}
          source={filters.source}
          setSource={filters.setSource}
          limit={filters.limit}
          setLimit={filters.setLimit}
          filterPattern={filters.filterPattern}
          setFilterPattern={filters.setFilterPattern}
          filterPatternError={filters.filterPatternError}
          duration={filters.duration}
          setDuration={filters.setDuration}
          minFreq={filters.minFreq}
          setMinFreq={filters.setMinFreq}
          minLoadPct={filters.minLoadPct}
          setMinLoadPct={filters.setMinLoadPct}
          autoSave={filters.autoSave}
          setAutoSave={filters.setAutoSave}
          state={run.state}
          onStart={actions.start}
          onStop={actions.stop}
          hasTarget={target.canRun}
          status={
            <SlowQueryRunStatus
              state={run.state}
              runtimeSeconds={run.runtimeSeconds}
              totalTracked={run.totalTracked}
              newlySaved={run.newlySaved}
              isRealtime={filters.mode === 'realtime'}
              sourceFallback={run.sourceFallback}
            />
          }
          queryCount={run.canSave ? run.queries.length : undefined}
          primaryActionHidden={run.state === 'idle'}
          showModeSwitcher={showModeSwitcher}
        />
      </Card.Content>

      {run.canSave ? (
        <SlowQueryResultsHeader
          targetLabel={target.label}
          sourceLabel={run.sourceLabel}
          engineLabel={run.connectionInfo?.engine}
          state={run.state}
          isRealtime={filters.mode === 'realtime'}
          sort={filters.sort}
          setSort={filters.setSort}
          onSaveAll={actions.saveAll}
          canSave={run.canSave}
          autoSave={filters.autoSave}
          setAutoSave={filters.setAutoSave}
        />
      ) : null}
    </Card>
  )
}
