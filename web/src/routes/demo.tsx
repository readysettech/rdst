import { createFileRoute, redirect } from '@tanstack/react-router'

// The demo is the Readyset Platform page now; the old URL keeps working.
export const Route = createFileRoute('/demo')({
  beforeLoad: () => {
    throw redirect({ to: '/platform', replace: true })
  },
})
