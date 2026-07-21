import { createFileRoute } from '@tanstack/react-router'
// The page component lives in the route-ignored `-query-registry-page` sibling
// so the code-splitter can relocate its QueryCard → SQLDisplay/SQLInput imports
// (the CodeMirror SQL-editor stack) out of the eager entry chunk. `component:`
// must reference a non-exported local wrapper that TanStack `autoCodeSplitting`
// can move to the lazy route chunk; the `QueryRegistryPage` import is used only
// there, so it rides along. [FIX-1 / Defect D-1]
import { QueryRegistryPage } from './-query-registry-page'

export const Route = createFileRoute('/query-registry')({
  component: QueryRegistryPageRoute,
})

function QueryRegistryPageRoute() {
  return <QueryRegistryPage />
}
