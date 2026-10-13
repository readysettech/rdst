import { createFileRoute, redirect } from '@tanstack/react-router'

// The query library is the Database page's query list now; the old URL keeps working.
export const Route = createFileRoute('/queries')({
  beforeLoad: () => {
    throw redirect({ to: '/database' })
  },
})
