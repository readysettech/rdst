import { createFileRoute, redirect } from '@tanstack/react-router'
// The workspace component lives in the route-ignored `-caching-page` sibling so
// the code-splitter can relocate the CodeMirror SQL-editor stack (pulled in
// transitively by the embedded Compare/Load test panes) out of the eager entry
// chunk. `component:` must reference a non-exported local wrapper that TanStack
// `autoCodeSplitting` can move to the lazy route chunk; the `CachingPage` import
// is used only there, so it rides along. See top.tsx §Defect D-1.
import { CachingPage } from './-caching-page'

export type PerformanceTestView = 'compare' | 'load-test'
type LegacyPerformanceTestView = PerformanceTestView | 'quick'

export const Route = createFileRoute('/cache')({
  // `view` is retained as a compatibility alias for existing links. Quick-test
  // links move back to the owning query in the unified Queries workspace;
  // legacy `benchmark` links continue to open Load test.
  validateSearch: (
    search: Record<string, unknown>
  ): { view?: LegacyPerformanceTestView; hash?: string; run?: string } => ({
    view:
      search.view === 'benchmark'
        ? 'load-test'
        : search.view === 'caches'
          ? 'quick'
          : search.view === 'quick' ||
              search.view === 'compare' ||
              search.view === 'load-test'
            ? search.view
            : undefined,
    hash: typeof search.hash === 'string' ? search.hash : undefined,
    run: typeof search.run === 'string' ? search.run : undefined,
  }),
  beforeLoad: ({ search }) => {
    if (search.view !== 'quick') return
    throw redirect({
      to: '/queries',
      search: search.hash ? { hash: search.hash } : {},
      replace: true,
    })
  },
  component: CachePageRoute,
})

function CachePageRoute() {
  const search = Route.useSearch()
  const view = search.view === 'load-test' ? 'load-test' : 'compare'
  return (
    <CachingPage
      view={view}
      deepLinkHash={search.hash}
      selectedRunId={search.run}
    />
  )
}
