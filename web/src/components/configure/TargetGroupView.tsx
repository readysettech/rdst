/**
 * Grouped presentation of the configured targets.
 *
 * Blocks are ordered groups-first with the ungrouped remainder last, and each
 * block renders the same row-cards the flat list uses under an always-visible
 * header that names the group and offers the group-scoped health check. When
 * nothing carries a real group the headers are dropped entirely, so a flat
 * fleet never grows an "Ungrouped" band.
 */

import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

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

export function TargetGroupView<T>({
  targets,
  groupOf,
  renderTargets,
  onHealthCheckGroup,
}: {
  targets: T[]
  groupOf: (target: T) => string | null
  /** Renders one block's targets — the same row-cards the flat list uses. */
  renderTargets: (targets: T[]) => ReactNode
  onHealthCheckGroup?: (group: string) => void
}) {
  const blocks = groupTargets(targets, groupOf)
  const hasNamedGroups = blocks.some((block) => block.group)
  if (!hasNamedGroups) {
    return <>{renderTargets(targets)}</>
  }

  return (
    <VStack className="gap-5 items-stretch w-full">
      {blocks.map((block) => (
        <VStack
          key={block.group ?? 'ungrouped'}
          className="gap-3 items-stretch"
        >
          <HStack className="justify-between items-center gap-3 flex-wrap rounded-xl bg-surface-layout-2/70 border border-border-layout-1 px-4 py-2.5">
            <HStack className="gap-2 items-center">
              <Icon
                name={block.group ? 'layers' : 'database'}
                label=""
                aria-hidden="true"
                className="w-4 h-4 text-content-layout-3"
              />
              <Text level="label-small" className="text-content-layout-1">
                {block.group ?? 'Ungrouped'}
              </Text>
              <Text level="caption" className="text-content-layout-3">
                {block.targets.length}{' '}
                {block.targets.length === 1 ? 'target' : 'targets'}
              </Text>
            </HStack>
            {block.group && onHealthCheckGroup && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label={`Health check this group (${block.targets.length})`}
                icon="chevron-right"
                iconPosition="left"
                onClick={() => onHealthCheckGroup(block.group as string)}
              />
            )}
          </HStack>
          {renderTargets(block.targets)}
        </VStack>
      ))}
    </VStack>
  )
}
