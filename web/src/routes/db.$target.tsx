import { createFileRoute, redirect } from '@tanstack/react-router'
import { selectGlobalTarget } from '../features/database/targetSelection'

// Legacy per-target URL: set the global target and land on /database (or the
// latest report for /db/$target/report), keeping a deep-linked query.
export const Route = createFileRoute('/db/$target')({
  beforeLoad: ({ params, location }) => {
    selectGlobalTarget(params.target)
    const search = location.search as Record<string, unknown>
    const query = typeof search.query === 'string' ? search.query : undefined
    if (location.pathname.replace(/\/$/, '').endsWith('/report'))
      throw redirect({
        to: '/database',
        search: { view: 'report' },
        replace: true,
      })
    throw redirect({
      to: '/database',
      search: query ? { query } : {},
      replace: true,
    })
  },
})
