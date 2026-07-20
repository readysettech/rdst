import { createFileRoute, redirect } from '@tanstack/react-router';
// The page component lives in the route-ignored `-results-page` sibling so the
// code-splitter can relocate its SQLDisplay/CodeMirror imports out of the eager
// entry chunk. `component:` must reference a non-exported local wrapper (below)
// that TanStack's `autoCodeSplitting` can move to the lazy route chunk; the
// `ResultsPage` import is used only there, so it rides along. See
// evidence/gates-final.md §Defect D-1.
import { ResultsPage } from './-results-page';

export type ResultsSearch = {
  query: string;
  target?: string;
  fast?: boolean;
  params?: string;
};

export const Route = createFileRoute('/results')({
  // Parse-only: never throw here. Throwing `redirect` from `validateSearch` is
  // wrapped by TanStack Router as a `SearchParamError` that bubbles to the root
  // boundary and replaces the whole app with a chrome-less error screen (B1).
  validateSearch: (search: Record<string, unknown>): ResultsSearch => ({
    query: typeof search.query === 'string' ? search.query : '',
    target: typeof search.target === 'string' ? search.target : undefined,
    fast: search.fast === true || search.fast === 'true',
    params: typeof search.params === 'string' ? search.params : undefined,
  }),
  // `throw redirect` IS honored in `beforeLoad`, so the missing-query guard
  // lives here — a bare `/results` cleanly redirects to the editor with the
  // app shell intact, instead of crashing.
  beforeLoad: ({ search }) => {
    if (!search.query) {
      throw redirect({ to: '/analyze' });
    }
  },
  component: ResultsRouteComponent,
});

function ResultsRouteComponent() {
  return <ResultsPage search={Route.useSearch()} />;
}
