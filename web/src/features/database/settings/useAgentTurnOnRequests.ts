// A coding agent asking RDST to turn monitoring on for a database the user hasn't turned on
// before: RDST opens that database's Turn on monitoring dialog, so the user reads it and
// clicks. The add-database dialog hands over its own database, so it's left alone here.
// Databases and monitoring the agent changes are refetched, and the desktop tray follows.
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouter } from '@tanstack/react-router'
import { useEffect } from 'react'
import { selectGlobalTarget } from '@/features/database/targetSelection'
import { addFlowFor, handOffAddFlow, newAddAttempt } from '@/lib/addFlow'
import {
  type AgentEvent,
  type AgentStep,
  subscribeAgentConnect,
} from '@/lib/agentConnect'
import { monitorKeys } from '@/lib/monitor/keys'
import { invalidateTargetQueries } from '@/lib/targetQueries'

interface LocationLike {
  pathname: string
  search: Record<string, unknown>
}

/** Where to take the user for `event`, or null when nothing should move. */
export function turnOnRequestTarget(
  event: AgentEvent,
  location: LocationLike
): string | null {
  if (event.step !== 'awaiting_user' || !event.target) return null
  if (location.search.add != null) return null
  const showing =
    location.pathname === '/database' &&
    location.search.panel === 'enable' &&
    location.search.target === event.target
  return showing ? null : event.target
}

/** Steps after which the agent has changed the databases or their monitoring. */
export function changesMonitoring(step: AgentStep): boolean {
  return (
    step === 'database_added' ||
    step === 'monitoring_enabled' ||
    step === 'settings_changed'
  )
}

export function useAgentTurnOnRequests(): void {
  const navigate = useNavigate()
  const router = useRouter()
  const client = useQueryClient()
  useEffect(
    () =>
      subscribeAgentConnect((event) => {
        if (changesMonitoring(event.step)) {
          void client.invalidateQueries({ queryKey: monitorKeys.all })
          void invalidateTargetQueries(client)
          window.rdstDesktop?.monitoring?.changed?.()
        }
        const { pathname, search } = router.state.location
        const target = turnOnRequestTarget(event, {
          pathname,
          search: search as Record<string, unknown>,
        })
        if (!target) return
        // The dialog follows the agent from this request on.
        if (!addFlowFor(target))
          handOffAddFlow(target, {
            attempt: newAddAttempt(),
            via: 'agent',
            agentSince: event.ts ?? null,
          })
        selectGlobalTarget(target)
        void navigate({ to: '/database', search: { target, panel: 'enable' } })
      }),
    [navigate, router, client]
  )
}
