import { useQuery } from '@tanstack/react-query'
import { fetchTargets } from '../lib/api'
import { isRemoteTargetHost } from '../lib/targetHost'

/**
 * Whether a named target lives off this machine.
 *
 * A run against a remote — possibly production — database is the same risk
 * whichever screen launched it, so both Compare and Load test read the
 * destination the same way and gate on the same answer (GUIDELINES §10).
 */
export function useTargetIsRemote(target: string | null): boolean {
  const { data } = useQuery({
    queryKey: ['configure-targets-hosts'],
    queryFn: fetchTargets,
    staleTime: 60_000,
  })
  if (!target) return false
  return (data ?? []).some(
    (item) => item.name === target && isRemoteTargetHost(item.host)
  )
}
