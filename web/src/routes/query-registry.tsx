import { createFileRoute, redirect } from '@tanstack/react-router'

// The saved list is now part of the Query Library. Preserve exact-query and
// run handoffs and leave the filters alone: a deep link that lands behind a
// starred filter the reader never set is a filter they cannot explain.
export const Route = createFileRoute('/query-registry')({
  // Keep parsing the deep-link params so they survive the redirect.
  validateSearch: (
    search: Record<string, unknown>
  ): { hash?: string; run?: string } => ({
    hash: typeof search.hash === 'string' ? search.hash : undefined,
    run: typeof search.run === 'string' ? search.run : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({
      to: '/queries',
      search: { hash: search.hash, run: search.run },
    })
  },
})
