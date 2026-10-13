import { createFileRoute, redirect } from '@tanstack/react-router'

// Health checks live in the daily report now; the old URL keeps working.
export const Route = createFileRoute('/audit')({
  beforeLoad: () => {
    throw redirect({ to: '/database', search: { view: 'report' } })
  },
})
