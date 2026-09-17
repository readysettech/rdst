import { useRouterState } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useAiGate } from '../lib/useAiGate'
import { AiProviderGate } from './AiProviderGate'

/** Require an AI provider before opening the app on a fresh installation. */
export function AiAccessBoundary({ children }: { children: ReactNode }) {
  const path = useRouterState({ select: (state) => state.location.pathname })
  const gate = useAiGate()

  // The browser callback must finish signing in before it can satisfy the gate.
  if (path === '/account-login') return children
  if (
    gate.status === 'checking' ||
    gate.status === 'error' ||
    gate.status === 'blocked'
  ) {
    return <AiProviderGate gate={gate} />
  }
  return children
}
