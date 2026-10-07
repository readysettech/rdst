import { useQueries } from '@tanstack/react-query'
import { getSettings } from '../lib/monitor/api'
import { monitorKeys } from '../lib/monitor/keys'
import type { MonitorSettings } from '../lib/monitor/types'
import { useSystemStatus } from '../lib/useSystemStatus'

export interface MonitoredTargets {
  /** Every configured database, in the order the status lists them. */
  targets: string[]
  /** Databases with monitoring on. */
  monitored: string[]
  settings: Map<string, MonitorSettings | undefined>
  /** True until the target list and every database's settings have answered. */
  isPending: boolean
}

/** Which configured databases have monitoring on; Home and the nav depend on the count. */
export function useMonitoredTargets(): MonitoredTargets {
  const status = useSystemStatus()
  const targets = status.data?.targets?.map((target) => target.name) ?? []
  const results = useQueries({
    queries: targets.map((target) => ({
      queryKey: monitorKeys.settings(target),
      queryFn: () => getSettings(target),
      staleTime: 30_000,
    })),
  })
  const settings = new Map(
    targets.map((target, index) => [target, results[index]?.data])
  )
  return {
    targets,
    monitored: targets.filter((target) => settings.get(target)?.enabled),
    settings,
    isPending: status.isPending || results.some((result) => result.isPending),
  }
}
