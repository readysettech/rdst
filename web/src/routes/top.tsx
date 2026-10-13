import { createFileRoute, redirect } from '@tanstack/react-router'

// Slow queries are the Database page's query list now; the old URL keeps working.
export const Route = createFileRoute('/top')({
  beforeLoad: () => {
    throw redirect({ to: '/database' })
  },
})
