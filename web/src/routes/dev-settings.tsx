/**
 * Developer Settings — retired standalone route.
 *
 * The dev tools (Simulate Trial Exhausted, Clear Keyring) now live INSIDE the
 * Settings page as its last section (`components/configure/DevSettingsSection`,
 * rendered by `routes/configure.tsx` under `#dev`) — owner decision
 * "Dev Settings => Settings içine taşınmalı". This route is kept as a redirect
 * so any existing `/dev-settings` deep link survives the merge and lands on the
 * Developer settings section. The section is a development-build tool, so a
 * shipped build has no `#dev` to land on: `from` lets Settings say that instead
 * of quietly showing Targets. [USE-097 one nav layout, USE-077 graceful path,
 * A-12]
 */

import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/dev-settings')({
  // `throw redirect` is honored in `beforeLoad`: a bare `/dev-settings` cleanly
  // forwards to the merged section with the app shell intact.
  beforeLoad: () => {
    throw redirect({
      to: '/configure',
      search: { from: 'dev-settings' },
      hash: 'dev',
    })
  },
  component: () => null,
})
