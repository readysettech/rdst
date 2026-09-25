import { createFileRoute } from '@tanstack/react-router'
import { AgentActivityPage } from '@/features/agent-activity'
import { isAction, isRange } from '@/features/agent-activity/activityModel'
import type { AgentAction } from '@/lib/agentActivity'

/** Search params of /agent-activity: a database, an action and a time range. */
export type AgentActivitySearch = {
  target?: string
  action?: AgentAction
  range?: 'today' | '7d' | '30d'
}

export const Route = createFileRoute('/agent-activity')({
  validateSearch: (search: Record<string, unknown>): AgentActivitySearch => {
    const out: AgentActivitySearch = {}
    if (typeof search.target === 'string' && search.target)
      out.target = search.target
    if (isAction(search.action)) out.action = search.action
    if (isRange(search.range)) out.range = search.range
    return out
  },
  component: AgentActivityPage,
})
