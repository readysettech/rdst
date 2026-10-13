import { useRouterState } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { legacyTitle } from '../layout/Main'
import { useAiGate } from '../lib/useAiGate'
import { AiProviderGate } from './AiProviderGate'

/** The bar title of each page the gate can stand in for. */
const PAGE_TITLES: Record<string, string> = {
  '/': 'Home',
  '/ask': 'Ask',
  '/results': 'Full analysis',
  '/agent-activity': 'Activity',
  '/platform': 'Readyset Platform',
  '/help': 'Help',
}

/** Keep local database monitoring available during AI setup. */
export function AiAccessBoundary({
  children,
  notice,
  frame = (gate) => gate,
}: {
  children: ReactNode
  notice?: ReactNode
  /** Puts the gate inside the app shell, in place of the page it stands in for. */
  frame?: (gate: ReactNode) => ReactNode
}) {
  const path = useRouterState({ select: (state) => state.location.pathname })
  const gate = useAiGate()

  // Local monitoring, target setup and the account callback are available throughout AI setup.
  if (
    [
      '/database',
      '/inbox',
      '/actions',
      '/configure',
      '/account-login',
    ].includes(path)
  )
    return children
  if (
    gate.status === 'checking' ||
    gate.status === 'error' ||
    gate.status === 'blocked'
  ) {
    return frame(
      <AiProviderGate
        gate={gate}
        title={PAGE_TITLES[path] ?? legacyTitle(path)}
        notice={notice}
      />
    )
  }
  return children
}
