import { createFileRoute } from '@tanstack/react-router'
import {
  parseQueryLibrarySearch,
  type QueryLibrarySearch,
} from '../features/queries/library/queryLibraryState'
// The workspace component lives in the route-ignored `-queries-page` sibling so
// the code-splitter can relocate the CodeMirror SQL-editor stack (pulled in
// transitively by the query library and its actions) out of the eager entry
// chunk. `component:` must reference a non-exported local wrapper that TanStack
// `autoCodeSplitting` can move to the lazy route chunk; the `QueriesPage` import
// is used only there, so it rides along. See evidence/gates-final.md §Defect D-1.
import { QueriesPage } from './-queries-page'

export { parseQueryLibrarySearch }
export type { QueryLibrarySearch }

export const Route = createFileRoute('/queries')({
  validateSearch: parseQueryLibrarySearch,
  component: QueriesPageRoute,
})

function QueriesPageRoute() {
  const search = Route.useSearch()
  return <QueriesPage search={search} />
}
