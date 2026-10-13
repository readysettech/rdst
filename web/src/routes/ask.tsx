import { createFileRoute } from '@tanstack/react-router'
import { fillParams, type SampleParam } from '../features/ask/askModel'
import { AskPage } from './-ask-page'

function asParams(value: unknown): SampleParam[] | undefined {
  if (!Array.isArray(value)) return undefined
  const params = value.filter(
    (entry): entry is SampleParam =>
      typeof entry?.name === 'string' && typeof entry?.value === 'string'
  )
  return params.length ? params : undefined
}

export const Route = createFileRoute('/ask')({
  // `from` is set by the retired /agents URL and accepted so that redirect stays typed.
  // `params` carries an analysis's sample values for the placeholders in `sql`.
  validateSearch: (
    search: Record<string, unknown>
  ): {
    from?: 'agents'
    sql?: string
    params?: SampleParam[]
    db?: string
    monitorFixture?: string
  } => ({
    from: search.from === 'agents' ? 'agents' : undefined,
    sql: typeof search.sql === 'string' ? search.sql : undefined,
    params: asParams(search.params),
    db: typeof search.db === 'string' && search.db ? search.db : undefined,
    monitorFixture:
      typeof search.monitorFixture === 'string' && search.monitorFixture
        ? search.monitorFixture
        : undefined,
  }),
  staticData: { layout: 'page' },
  component: AskRoute,
})

function AskRoute() {
  const search = Route.useSearch()
  return (
    <AskPage
      initialQuestion={search.sql && fillParams(search.sql, search.params)}
    />
  )
}
