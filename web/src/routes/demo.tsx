import { createFileRoute } from '@tanstack/react-router'
// The page component lives in the route-ignored `-demo-page` sibling so the
// code-splitter can relocate its `../components/SQLDisplay` (CodeMirror
// SQL-editor stack) import out of the eager entry chunk. `component:` must
// reference a non-exported local wrapper that TanStack `autoCodeSplitting` can
// move to the lazy route chunk; the `DemoPage` import is used only there, so it
// rides along. See evidence/gates-final.md §Defect D-1.
import { DemoPage } from './-demo-page'

export const Route = createFileRoute('/demo')({ component: DemoRoute })

function DemoRoute() {
  // Ungated (rdst-dma.3): no email wall before the user sees value. Identity
  // is asked once inside the demo, at the moment of proof — see the reciprocity
  // panel wired into the demo's `ready` phase in ./-demo-page.
  return <DemoPage />
}
