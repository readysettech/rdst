import { createFileRoute, redirect } from '@tanstack/react-router'

// Analysis is an action on the Database page's query list; old bookmarks land there.
export const Route = createFileRoute('/analyze')({
  beforeLoad: () => {
    throw redirect({ to: '/database' })
  },
})
