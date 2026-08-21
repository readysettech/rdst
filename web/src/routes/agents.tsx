import { createFileRoute, redirect } from '@tanstack/react-router'

// Agents moved into the Ask workspace (Conversations view). The /agents URL is
// kept as a redirect so muscle memory and existing deep links still land there.
export const Route = createFileRoute('/agents')({
  beforeLoad: () => {
    throw redirect({ to: '/ask' })
  },
})
