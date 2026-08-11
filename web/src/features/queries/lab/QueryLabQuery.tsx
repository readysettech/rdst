import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { formatMs, shortHash } from '../../../lib/formatters'
import {
  formatDbTime,
  isNotCacheable,
  queryImpactMs,
} from '../../../lib/queryImpact'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { getSourceMeta } from '../saved/savedQuerySelectors'
import {
  type QueriesLabController,
  queryLabParameterLabel,
  queryLabTitle,
} from './queryLabModel'

export function QueryLabBadges({
  entry,
  controller,
}: {
  entry: QueryRegistryEntry
  controller: QueriesLabController
}) {
  const source = getSourceMeta(entry.source)
  const cached = controller.rowActions.isCached(entry.hash)

  return (
    <HStack className="items-center gap-1.5 flex-wrap">
      <Show when={Boolean(entry.is_new)}>
        <Tag size="small" variant="rising" modifier="solid" label="New" />
      </Show>
      <Tag
        size="small"
        variant={source.variant}
        modifier="ghost"
        label={source.label}
      />
      <Show when={cached}>
        <Tag
          size="small"
          variant="positive"
          modifier="ghost"
          label="Cached"
          icon="tick-double"
          iconPosition="left"
        />
      </Show>
    </HStack>
  )
}

export function QueryLabActions({
  entry,
  controller,
  compact = false,
}: {
  entry: QueryRegistryEntry
  controller: QueriesLabController
  compact?: boolean
}) {
  const cached = controller.rowActions.isCached(entry.hash)
  const cacheBlocked = isNotCacheable(entry.readyset_supported)

  return (
    <HStack className="items-center justify-end gap-2 flex-wrap">
      <Button
        size="small"
        variant="primary"
        modifier="ghost"
        icon="speedometer"
        iconPosition={compact ? 'icon' : 'left'}
        label={compact ? '' : 'Analyze'}
        aria-label="Analyze query"
        onClick={() =>
          controller.rowActions.analyze(
            entry.sql,
            entry.target,
            entry.most_recent_params
          )
        }
      />
      <Show when={!cacheBlocked}>
        <Button
          size="small"
          variant="rising"
          modifier={cached ? 'ghost' : 'solid'}
          icon="database-settings"
          iconPosition={compact ? 'icon' : 'left'}
          label={compact ? '' : cached ? 'Test' : 'Compare & test'}
          aria-label={cached ? 'Test cached query' : 'Compare and test query'}
          onClick={() =>
            cached
              ? controller.rowActions.runTest(
                  entry.hash,
                  entry.sql,
                  entry.most_recent_params ?? {}
                )
              : controller.rowActions.cacheQuery(entry.hash, entry.sql)
          }
        />
      </Show>
    </HStack>
  )
}

export type QueryLabMetricKind =
  | 'impact'
  | 'frequency'
  | 'latency'
  | 'parameters'

export function queryLabMetric(
  entry: QueryRegistryEntry,
  kind: QueryLabMetricKind
) {
  const runs = entry.observation_count ?? entry.frequency ?? 0
  const values = {
    impact: {
      icon: 'speedometer' as const,
      label: 'DB time',
      value: formatDbTime(queryImpactMs(entry)),
    },
    frequency: {
      icon: 'database' as const,
      label: 'Observed runs',
      value: runs.toLocaleString(),
    },
    latency: {
      icon: 'observe' as const,
      label: 'Avg latency',
      value: formatMs(entry.avg_duration_ms ?? 0),
    },
    parameters: {
      icon: 'adjustment-horizontal' as const,
      label: 'Parameters',
      value: queryLabParameterLabel(entry),
    },
  }
  return values[kind]
}

export function QueryLabMetric({
  entry,
  kind,
  layout = 'stacked',
  className,
}: {
  entry: QueryRegistryEntry
  kind: QueryLabMetricKind
  layout?: 'stacked' | 'inline'
  className?: string
}) {
  const metric = queryLabMetric(entry, kind)

  if (layout === 'inline') {
    return (
      <HStack className={cn('min-w-0 items-center gap-2', className)}>
        <Icon
          name={metric.icon}
          label=""
          aria-hidden="true"
          className="h-4 w-4 shrink-0 text-content-layout-3"
        />
        <Text level="caption" className="truncate text-content-layout-3">
          {metric.label}
        </Text>
        <Text
          level="mono-small"
          className="ml-auto shrink-0 text-content-layout-1"
        >
          {metric.value}
        </Text>
      </HStack>
    )
  }

  return (
    <HStack className={cn('min-w-0 items-center gap-3', className)}>
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface-layout-2">
        <Icon
          name={metric.icon}
          label=""
          aria-hidden="true"
          className="h-4 w-4 text-content-layout-2"
        />
      </div>
      <VStack className="min-w-0 items-start gap-0">
        <Text level="caption" className="text-content-layout-3">
          {metric.label}
        </Text>
        <Text level="mono-small" className="truncate text-content-layout-1">
          {metric.value}
        </Text>
      </VStack>
    </HStack>
  )
}

export function QueryLabIdentity({
  entry,
  controller,
  size = 'base',
}: {
  entry: QueryRegistryEntry
  controller: QueriesLabController
  size?: 'base' | 'large'
}) {
  return (
    <VStack className="min-w-0 items-start gap-2">
      <HStack className="min-w-0 items-center gap-2 flex-wrap">
        <Text
          as="h2"
          level={size === 'large' ? 'headline-4' : 'subtitle-2'}
          className="truncate text-content-layout-1"
        >
          {queryLabTitle(entry)}
        </Text>
        <QueryLabBadges entry={entry} controller={controller} />
      </HStack>
      <Text level="mono-small" className="text-content-layout-3">
        hash {shortHash(entry.hash)} · {entry.target ?? controller.target}
      </Text>
    </VStack>
  )
}
