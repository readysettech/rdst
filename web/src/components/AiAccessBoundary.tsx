import { useRouterState } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useAiGate } from '../lib/useAiGate'
import { AiProviderGate } from './AiProviderGate'

/** Keep local database monitoring available during AI setup. */
export function AiAccessBoundary({
  children,
  notice,
}: {
  children: ReactNode
  notice?: ReactNode
}) {
  const path = useRouterState({ select: (state) => state.location.pathname })
  const gate = useAiGate()

  // Local monitoring, target setup and the account callback are available throughout AI setup.
  if (['/database', '/inbox', '/configure', '/account-login'].includes(path))
    return children
  if (
    gate.status === 'checking' ||
    gate.status === 'error' ||
    gate.status === 'blocked'
  ) {
    return <AiProviderGate gate={gate} notice={notice} />
  }
  return children
}
