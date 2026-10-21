import { useRouterState } from '@tanstack/react-router'
import { type ReactNode, useEffect } from 'react'
import { legacyTitle } from '../layout/Main'
import { trackEventOnce } from '../lib/analytics'
import { useAiGate } from '../lib/useAiGate'
import { AiProviderGate } from './AiProviderGate'

/** The bar title of each page the gate can stand in for. */
const PAGE_TITLES: Record<string, string> = {
  '/ask': 'Ask',
  '/results': 'Full analysis',
  '/agent-activity': 'Activity',
  '/platform': 'Readyset Platform',
  '/help': 'Help',
}

// Home, local monitoring, target setup and the account callback are available throughout AI
// setup. Turning on monitoring asks for a sign-in where AI starts.
const OPEN_PATHS = [
  '/',
  '/database',
  '/inbox',
  '/actions',
  '/configure',
  '/account-login',
]

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
  const open = OPEN_PATHS.includes(path)
  const settled = open || gate.status !== 'checking'
  const gated = !open && (gate.status === 'blocked' || gate.status === 'error')
  useEffect(() => {
    if (settled)
      trackEventOnce('first-screen', 'first_screen_rendered', { gated })
  }, [settled, gated])

  if (open) return children
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
