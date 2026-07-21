import { createFileRoute } from '@tanstack/react-router'
// The page component lives in the route-ignored `-cache-page` sibling so the
// code-splitter can relocate its QueryCard → SQLDisplay/SQLInput imports (the
// CodeMirror SQL-editor stack) out of the eager entry chunk. `component:` must
// reference a non-exported local wrapper that TanStack `autoCodeSplitting` can
// move to the lazy route chunk; the `CachePage` import is used only there, so it
// rides along. The wrapper reads the validated `?query=` search here and feeds
// it in as the `pendingQuery` prop. [FIX-1 / Defect D-1]
import { CachePage } from './-cache-page'

type CacheSearch = {
  query?: string
}

export const Route = createFileRoute('/cache')({
  validateSearch: (search: Record<string, unknown>): CacheSearch => ({
    query: typeof search.query === 'string' ? search.query : undefined,
  }),
  component: CachePageRoute,
})

function CachePageRoute() {
  const { query } = Route.useSearch()
  return <CachePage pendingQuery={query} />
}
