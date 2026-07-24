import { createFileRoute } from '@tanstack/react-router'
// The page component lives in the route-ignored `-benchmark-page` sibling so the
// code-splitter can relocate its SQLDisplay/CodeMirror imports out of the eager
// entry chunk. `component:` must reference a non-exported local wrapper that
// TanStack `autoCodeSplitting` can move to the lazy route chunk; the
// `BenchmarkPage` import is used only there, so it rides along. See
// evidence/gates-final.md §Defect D-1.
import { BenchmarkPage } from './-benchmark-page'

export const Route = createFileRoute('/benchmark')({
  validateSearch: (search: Record<string, unknown>) => ({
    run: typeof search.run === 'string' ? search.run : undefined,
  }),
  component: BenchmarkPageRoute,
})

function BenchmarkPageRoute() {
  const { run } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <BenchmarkPage
      selectedRunId={run}
      onClearSelectedRun={() =>
        navigate({ search: { run: undefined }, replace: true })
      }
    />
  )
}
