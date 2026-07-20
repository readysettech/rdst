import { createFileRoute } from '@tanstack/react-router'
// The page component lives in the route-ignored `-cache-page` sibling so the
// code-splitter can relocate its SQL-editor imports out of the eager entry
// chunk. `component:` references a non-exported local wrapper that TanStack
// `autoCodeSplitting` can move to the lazy route chunk. [FIX-1 / Defect D-1]
import { CachePage } from './-cache-page'

export const Route = createFileRoute('/cache')({
  component: CachePageRoute,
})

function CachePageRoute() {
  return <CachePage />
}
