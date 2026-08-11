import { DataResponse } from '../../../../components/data-response/DataResponse'
import {
  QueryListEmptyState,
  QueryListErrorState,
  QueryListSkeleton,
} from '../../../../components/query-list-state'
import type { QueryRegistryEntry } from '../../../../lib/useQueryRegistry'
import { AnalyzeHistoryHeader } from './AnalyzeHistoryHeader'
import { AnalyzeHistoryList } from './AnalyzeHistoryList'

interface AnalyzeHistoryProps {
  queries: QueryRegistryEntry[]
  onSelect: (entry: QueryRegistryEntry) => void
  isLoading?: boolean
  error?: unknown
  onRetry?: () => void
}

const RECENT_LIMIT = 5

/**
 * Recent-query container. It owns data selection only; section chrome, empty
 * state, and rows are independent presentation components. QueryCard rows stay
 * free-standing rather than being nested inside another Card.
 */
export function AnalyzeHistory({
  queries,
  onSelect,
  isLoading = false,
  error,
  onRetry,
}: AnalyzeHistoryProps) {
  const recentQueries = queries.slice(0, RECENT_LIMIT)
  return (
    <section aria-label="Recent queries">
      <DataResponse
        data={recentQueries}
        isLoading={isLoading}
        error={error}
        renderLoading={<QueryListSkeleton count={2} />}
        renderError={(loadError) => (
          <QueryListErrorState
            title="Recent queries couldn't be loaded"
            message="Your recent query history is unavailable right now."
            trustworthy="The query in the editor is unchanged."
            error={loadError}
            onRetry={onRetry}
          />
        )}
        renderEmpty={
          <QueryListEmptyState
            icon="folder-file"
            title="Your analyzed queries will show up here"
            body="Queries you analyze will appear here for quick reuse."
          />
        }
      >
        {(entries) => (
          <div className="space-y-3">
            <AnalyzeHistoryHeader count={entries.length} />
            <AnalyzeHistoryList queries={entries} onSelect={onSelect} />
          </div>
        )}
      </DataResponse>
    </section>
  )
}
