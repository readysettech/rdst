import { createFileRoute } from '@tanstack/react-router'
import { AgentActivityPage } from '@/features/agent-activity'
import { isAction, isRange } from '@/features/agent-activity/activityModel'
import type { AgentAction } from '@/lib/agentActivity'

/** Search params of /agent-activity: a database, an event kind and a time range. */
export type AgentActivitySearch = {
  target?: string
  action?: AgentAction
  range?: 'today' | '7d' | '30d'
  monitorFixture?: string
}

export const Route = createFileRoute('/agent-activity')({
  staticData: { layout: 'page' },
  validateSearch: (search: Record<string, unknown>): AgentActivitySearch => {
    const out: AgentActivitySearch = {}
    if (typeof search.target === 'string' && search.target)
      out.target = search.target
    if (isAction(search.action)) out.action = search.action
    if (isRange(search.range)) out.range = search.range
    if (typeof search.monitorFixture === 'string' && search.monitorFixture)
      out.monitorFixture = search.monitorFixture
    return out
  },
  component: AgentActivityPage,
})
