/**
 * Top Queries page - Monitor and analyze slow queries
 */

import { createFileRoute } from '@tanstack/react-router';
// The page component lives in the route-ignored `-top-page` sibling so the
// code-splitter can relocate its `../components` barrel import (which re-exports
// the CodeMirror SQL-editor stack) out of the eager entry chunk. `component:`
// must reference a non-exported local wrapper that TanStack `autoCodeSplitting`
// can move to the lazy route chunk; the `TopPage` import is used only there, so
// it rides along. See evidence/gates-final.md §Defect D-1.
import { TopPage } from './-top-page';

export const Route = createFileRoute('/top')({
  component: TopPageRoute,
});

function TopPageRoute() {
  return <TopPage />;
}
