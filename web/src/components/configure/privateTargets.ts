export interface PrivateTargetCandidate {
  name: string
  publicly_accessible?: boolean | null
  vpc_id?: string | null
}

export interface PrivateTargetGroup {
  key: string
  label: string
  targetNames: string[]
}

/**
 * Private discovery results share one jump-host form per VPC when that
 * metadata exists. Providers without network IDs use one form for the batch.
 */
export function groupPrivateTargets(
  targets: PrivateTargetCandidate[]
): PrivateTargetGroup[] {
  const privateTargets = targets.filter(
    (target) => target.publicly_accessible === false
  )
  if (privateTargets.length === 0) return []

  const exposesVpc = privateTargets.some((target) => Boolean(target.vpc_id))
  if (!exposesVpc) {
    return [
      {
        key: 'private-batch',
        label: 'Imported batch',
        targetNames: privateTargets.map((target) => target.name),
      },
    ]
  }

  const groups = new Map<string, PrivateTargetGroup>()
  for (const target of privateTargets) {
    const vpc = target.vpc_id || 'unknown'
    const key = `vpc:${vpc}`
    const existing = groups.get(key)
    if (existing) {
      existing.targetNames.push(target.name)
    } else {
      groups.set(key, {
        key,
        label: target.vpc_id ? `VPC ${target.vpc_id}` : 'Unknown VPC',
        targetNames: [target.name],
      })
    }
  }
  return [...groups.values()]
}
