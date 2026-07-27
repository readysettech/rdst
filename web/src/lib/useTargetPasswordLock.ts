import { useMemo } from 'react'

import type { EnvRequirement } from './api'
import { useEnvRequirements } from './useEnvRequirements'
import { useSystemStatus } from './useSystemStatus'

export interface TargetPasswordLockState {
  isResolved: boolean
  isLocked: boolean
  targetName: string | null
  message: string
  missingTargetRequirements: EnvRequirement[]
  keyringAvailable: boolean
}

export function useTargetPasswordLock(
  selectedTarget?: string | null
): TargetPasswordLockState {
  const { data: status, isPending: statusPending } = useSystemStatus()
  const { data: envRequirements } = useEnvRequirements()

  return useMemo(() => {
    const targets = status?.targets ?? []
    const targetName =
      selectedTarget?.trim() ||
      status?.default_target ||
      targets[0]?.name ||
      null

    const targetInfo = targetName
      ? targets.find((item) => item.name === targetName)
      : undefined

    const missingTargetRequirements = (
      envRequirements?.requirements ?? []
    ).filter(
      (item) =>
        item.kind === 'target_password' &&
        !item.satisfied &&
        (item.target === targetName || item.target === null)
    )

    const isLocked = Boolean(targetInfo && !targetInfo.has_password)

    let message = ''
    if (isLocked && targetName) {
      message = `Enter the password for '${targetName}' again.`
    }

    return {
      isResolved: !statusPending,
      isLocked,
      targetName,
      message,
      missingTargetRequirements,
      keyringAvailable: envRequirements?.keyring_available ?? false,
    }
  }, [status, statusPending, envRequirements, selectedTarget])
}
