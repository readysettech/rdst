import { createFileRoute } from '@tanstack/react-router'
import { AskPage } from './-ask-page'

export const Route = createFileRoute('/ask')({
  // `from` is set by the retired /agents URL so the arrival can be explained.
  validateSearch: (search: Record<string, unknown>): { from?: 'agents' } =>
    search.from === 'agents' ? { from: 'agents' } : {},
  component: AskRoute,
})

function AskRoute() {
  return <AskPage movedFrom={Route.useSearch().from} />
}
