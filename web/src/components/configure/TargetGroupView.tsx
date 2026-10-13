// Discovered and imported databases, bucketed by their group (an Aurora cluster, a
// provider project) for the import's lists.

export interface TargetGroupBlock<T> {
  group: string | null
  targets: T[]
}

/** Bucket targets by group: named groups in first-seen order, ungrouped last. */
export function groupTargets<T>(
  targets: T[],
  groupOf: (target: T) => string | null
): TargetGroupBlock<T>[] {
  const named = new Map<string, T[]>()
  const ungrouped: T[] = []
  for (const target of targets) {
    const group = groupOf(target)
    if (!group) {
      ungrouped.push(target)
      continue
    }
    const bucket = named.get(group)
    if (bucket) bucket.push(target)
    else named.set(group, [target])
  }
  const blocks: TargetGroupBlock<T>[] = [...named.entries()].map(
    ([group, members]) => ({ group, targets: members })
  )
  if (ungrouped.length > 0) blocks.push({ group: null, targets: ungrouped })
  return blocks
}
