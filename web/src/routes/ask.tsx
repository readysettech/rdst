import { createFileRoute } from '@tanstack/react-router'
import { AskPage } from './-ask-page'

export const Route = createFileRoute('/ask')({
  // `from` is set by the retired /agents URL so the arrival can be explained.
  validateSearch: (
    search: Record<string, unknown>
  ): { from?: 'agents'; sql?: string } => ({
    from: search.from === 'agents' ? 'agents' : undefined,
    sql: typeof search.sql === 'string' ? search.sql : undefined,
  }),
  component: AskRoute,
})

function AskRoute() {
  const search = Route.useSearch()
  return <AskPage movedFrom={search.from} initialQuestion={search.sql} />
}
