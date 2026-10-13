import { createFileRoute, redirect } from '@tanstack/react-router'

// Retired first-run page: its links land on Home with the add-database dialog open.
export const Route = createFileRoute('/onboarding')({
  beforeLoad: () => {
    throw redirect({ to: '/', search: { add: 1 } as never, replace: true })
  },
  component: () => null,
})
