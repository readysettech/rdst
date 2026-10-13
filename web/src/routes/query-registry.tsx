import { createFileRoute, redirect } from '@tanstack/react-router'

// The saved list is the Database page's query list now. An exact-query deep link
// keeps its query in view there.
export const Route = createFileRoute('/query-registry')({
  // Keep parsing the deep-link param so it survives the redirect.
  validateSearch: (search: Record<string, unknown>): { hash?: string } => ({
    hash: typeof search.hash === 'string' ? search.hash : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({
      to: '/database',
      search: search.hash ? { focus: search.hash } : {},
    })
  },
})
