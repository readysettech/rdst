// What monitoring needs from the database. The checks, provider detection and
// per-provider fixes come from GET /api/configure/targets/{name}/prerequisites
// (features/prerequisites); this module maps that answer onto the enable flow.

import {
  type CheckerPhase,
  checkerPhase,
  type PrereqResult,
} from '@/features/prerequisites'
import type { MonitorSettings } from '@/lib/monitor/types'

/** Monitoring can start: every blocking check passed (hidden SQL still allowed). */
export function canEnable(phase: CheckerPhase) {
  return phase === 'ready' || phase === 'attention'
}

/** The enable button's state for a prerequisites answer (or none yet). */
export function enableState(result: PrereqResult | undefined, failed = false) {
  const phase = checkerPhase(result, failed)
  return { phase, enabled: canEnable(phase) }
}

export type ExecutionMode = 'plans_only' | 'execute'

/** Reads the consent setting; `query_execution` wins once the backend sends it. */
export function executionMode(
  settings: Pick<MonitorSettings, 'execute'> | undefined
): ExecutionMode {
  const explicit = (settings as { query_execution?: string } | undefined)
    ?.query_execution
  if (explicit === 'execute' || explicit === 'plans_only') return explicit
  return settings?.execute ? 'execute' : 'plans_only'
}

/** The per-target `execute` setting as the turn-on question asks it; measured first. */
export const EXECUTION_OPTIONS: {
  value: ExecutionMode
  label: string
  description: string
}[] = [
  {
    value: 'execute',
    label: 'Run EXPLAIN ANALYZE on problem queries',
    description:
      'Measured timing. RDST runs the read-only query with a time limit, which adds a little load to the database.',
  },
  {
    value: 'plans_only',
    label: 'EXPLAIN only',
    description:
      'The plan, without running the query. Nothing runs on your data, and there are no measured timings.',
  },
]

/** The enable button for a checker phase: blocked checks can still be skipped. */
export function enableAction(phase: CheckerPhase): {
  label: string
  enabled: boolean
  note: string | null
} {
  // Without its statement statistics a database gives no readings, so setup comes first.
  if (phase === 'blocked')
    return {
      label: 'Turn on monitoring',
      enabled: false,
      note: 'Run the setup step above first. This check updates on its own once it passes.',
    }
  if (phase === 'connecting' || phase === 'unreachable')
    return {
      label: 'Turn on monitoring',
      enabled: false,
      note:
        phase === 'unreachable'
          ? 'RDST needs to reach the database first.'
          : null,
    }
  return { label: 'Turn on monitoring', enabled: true, note: null }
}
