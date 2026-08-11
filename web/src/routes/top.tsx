import { createFileRoute, redirect } from '@tanstack/react-router'

// Historical discovery now feeds the Query Library automatically. Keep the
// old URL useful by opening its closest lifecycle view.
export const Route = createFileRoute('/top')({
  beforeLoad: () => {
    throw redirect({ to: '/queries', search: { view: 'high-impact' } })
  },
})
