import { createFileRoute } from '@tanstack/react-router'
// The page component lives in the route-ignored `-analyze-page` sibling so the
// code-splitter can relocate its `../components` barrel import (which re-exports
// the CodeMirror SQL-editor stack) out of the eager entry chunk. `component:`
// must reference a non-exported local wrapper that TanStack `autoCodeSplitting`
// can move to the lazy route chunk; the `AnalyzePage` import is used only there,
// so it rides along. [FIX-1 / Defect D-1]
import { AnalyzePage } from './-analyze-page'

export const Route = createFileRoute('/analyze')({
  component: AnalyzePageRoute,
})

function AnalyzePageRoute() {
  return <AnalyzePage />
}
