import { useCallback, useEffect, useRef, useState } from 'react'
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
  // The check whose outcome the gate reports; a newer check or a reset replaces it.
  const checking = useRef<{ target: string } | null>(null)

  const reset = useCallback(() => {
    checking.current = null
    fleetStatus.reset()
    setFailure(null)
  }, [fleetStatus.reset])

  // A check already started for the newly chosen target, as when a request is sent there the
  // moment it is chosen, outlives the switch.
  useEffect(() => {
    if (checking.current?.target !== target) reset()
  }, [reset, target])

  const ensureReachable = useCallback(async () => {
    if (!target) {
      setFailure({
        target: 'Selected database',
        message: 'No database is selected.',
      })
      return false
    }

    setFailure(null)
    const run = { target }
    checking.current = run
    const completed = await fleetStatus.check(undefined, [target])
    // A replaced check was aborted, which says nothing about the database.
    if (checking.current !== run) return false
    checking.current = null
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
