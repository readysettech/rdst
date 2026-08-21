import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/benchmark')({
  validateSearch: (search: Record<string, unknown>) => ({
    run: typeof search.run === 'string' ? search.run : undefined,
  }),
  // Load test is now one mode of the unified Benchmarks workspace.
  // Preserve old bookmarks and background-run links without keeping a second
  // user-facing surface alive.
  beforeLoad: ({ search }) => {
    throw redirect({
      to: '/cache',
      search: { view: 'load-test', run: search.run },
    })
  },
})
