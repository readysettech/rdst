import { createFileRoute, redirect } from '@tanstack/react-router'
import { selectGlobalTarget } from '../features/database/targetSelection'

// Legacy report URL: the parent route redirects first; this is the fallback.
export const Route = createFileRoute('/db/$target/report')({
  beforeLoad: ({ params }) => {
    selectGlobalTarget(params.target)
    throw redirect({
      to: '/database',
      search: { view: 'report' },
      replace: true,
    })
  },
})
