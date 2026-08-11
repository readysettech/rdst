import { createFileRoute, redirect } from '@tanstack/react-router'

// Analysis is a Query Library action rather than a separate collection. Keep
// Keep old bookmarks working by landing in the unified query library.
export const Route = createFileRoute('/analyze')({
  beforeLoad: () => {
    throw redirect({ to: '/queries' })
  },
})
