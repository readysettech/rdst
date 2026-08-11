import { useCallback, useEffect, useState } from 'react'
import { useFleetStatus } from './useFleet'

export interface TargetConnectivityFailure {
  target: string
  message?: string | null
  category?: string | null
  code?: string | null
}

export interface TargetConnectivityGate {
  failure: TargetConnectivityFailure | null
  isChecking: boolean
  ensureReachable: () => Promise<boolean>
  reset: () => void
}

export function useTargetConnectivityGate(
  target?: string | null
): TargetConnectivityGate {
  const fleetStatus = useFleetStatus()
  const [failure, setFailure] = useState<TargetConnectivityFailure | null>(null)

  const reset = useCallback(() => {
    fleetStatus.reset()
    setFailure(null)
  }, [fleetStatus.reset])

  useEffect(() => reset(), [reset, target])

  const ensureReachable = useCallback(async () => {
    if (!target) {
      setFailure({
        target: 'Selected database',
        message: 'No database is selected.',
      })
      return false
    }

    setFailure(null)
    const completed = await fleetStatus.check(undefined, [target])
    const result = completed[target]
    if (result?.status === 'ok') return true

    setFailure({
      target,
      message:
        result?.error ?? `RDST could not verify that '${target}' is reachable.`,
      category: result?.category,
      code: result?.code,
    })
    return false
  }, [fleetStatus.check, target])

  return {
    failure,
    isChecking: fleetStatus.state === 'running',
    ensureReachable,
    reset,
  }
}
