/**
 * Fleet — retired standalone route.
 *
 * Target management (groups, AWS discovery, CSV import, connectivity) now lives
 * in the Database connections section of the Settings page. This route survives
 * as a redirect so existing `/fleet` and `/fleet?add=aws` links keep working and
 * land on that section. [USE-097 one nav layout, USE-077 graceful path]
 */

import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/fleet')({
  validateSearch: (
    search: Record<string, unknown>
  ): { add?: 'aws' | 'csv' } => {
    const add = search.add
    return add === 'aws' || add === 'csv' ? { add } : {}
  },
  // `throw redirect` is honored in `beforeLoad`, keeping the app shell intact
  // while forwarding the ?add tab through to the drawer.
  beforeLoad: () => {
    throw redirect({ to: '/configure', search: true, hash: 'connections' })
  },
  component: () => null,
})
