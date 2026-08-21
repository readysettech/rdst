import { createFileRoute, redirect } from '@tanstack/react-router'

// The saved list is now the Query Library's starred shortlist. Preserve
// exact-query and run handoffs.
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
      search: { starred: true, hash: search.hash, run: search.run },
    })
  },
})
