import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { StatusRipple } from '@rs/ui-new/status'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { formatTimestamp } from '../../../lib/formatters'
import type { QueryDiscoveryStats } from '../../../lib/useQueryDiscovery'
import type { QueryLibraryController } from './useQueryLibraryController'

/**
 * The honesty note: automatic discovery reads the database's own workload
 * statistics, so coverage follows what the database measures.
 */
export const DISCOVERY_COVERAGE_NOTE =
  'Automatic discovery observes the most active queries in your database workload from cumulative statistics, so queries that run rarely may not appear. System and catalog statements are excluded, and you can always add a query manually.'

export const DISCOVERY_BACKOFF_NOTE =
  'Checks are retried less often after repeated failures and resume automatically once the database is reachable.'

export const DISCOVERY_CADENCE_NOTE =
  'Checks run about once a minute while this page is open.'

/** Human summary of the last collection pass, from the SSE snapshot stats. */
export function discoveryCheckSummary(
  stats: QueryDiscoveryStats | null | undefined
): string | null {
  if (!stats) return null
  const parts: string[] = []
  if (stats.collection_duration_ms != null) {
    parts.push(`${Math.round(stats.collection_duration_ms)} ms`)
  }
  if (stats.rows_returned != null) {
    const noun = stats.rows_returned === 1 ? 'record' : 'records'
    parts.push(`scanned ${stats.rows_returned} database statement ${noun}`)
  }
  if (stats.new_identity_count != null) {
    parts.push(`${stats.new_identity_count} new`)
  }
  if (stats.system_skipped != null && stats.system_skipped > 0) {
    const noun = stats.system_skipped === 1 ? 'statement' : 'statements'
    parts.push(`${stats.system_skipped} system ${noun} excluded`)
  }
  if (parts.length === 0) return null
  return `Last check: ${parts.join(', ')}`
}

export function QueryLibraryDiscoveryStatus({
  controller,
}: {
  controller: QueryLibraryController
}) {
  const { discovery, freshness } = controller.library
  // Tick so the relative "last successful check" label stays accurate while
  // the page sits open. The clock is passed into formatTimestamp so the label
  // is derived from reactive state and recomputes on each tick.
  const [clock, setClock] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])
  const status = {
    starting: {
      color: 'informative' as const,
      label: 'Starting discovery',
    },
    watching: {
      color: 'positive' as const,
      label: `Watching ${controller.target ?? 'target'}`,
    },
    unavailable: {
      color: 'warning' as const,
      label: 'Discovery unavailable',
    },
  }[discovery.state]
  // The live snapshot carries the freshest timestamp; before the stream
  // connects, the read model's durable freshness answers the same question.
  const lastSuccessAt = discovery.updated_at || freshness?.last_success_at
  const lastSuccessLine = lastSuccessAt
    ? `Last successful check ${formatTimestamp(lastSuccessAt, clock).toLowerCase()}`
    : 'No successful check yet'
  const checkSummary = discoveryCheckSummary(discovery.stats)
  const checkMode =
    discovery.stats?.incremental != null
      ? discovery.stats.incremental
        ? 'Incremental check'
        : 'Full sweep'
      : null

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`${status.label}. Discovery details`}
          className="inline-flex cursor-pointer items-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-primary-soft"
        >
          <StatusRipple color={status.color} label={status.label} />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-80 flex-col gap-3 p-4"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <div className="space-y-1">
          <Text as="p" level="label-small" className="text-content-layout-1">
            {lastSuccessLine}
          </Text>
          {checkSummary ? (
            <Text as="p" level="caption" className="text-content-layout-2">
              {checkSummary}
            </Text>
          ) : null}
          {checkMode ? (
            <Text as="p" level="caption" className="text-content-layout-3">
              {checkMode}
            </Text>
          ) : null}
          {discovery.state === 'watching' ? (
            <Text as="p" level="caption" className="text-content-layout-3">
              {DISCOVERY_CADENCE_NOTE}
            </Text>
          ) : null}
        </div>
        {discovery.state === 'unavailable' ? (
          <div className="space-y-1">
            {discovery.error ? (
              <Text
                as="p"
                level="caption"
                className="text-content-negative-soft"
              >
                {discovery.error}
              </Text>
            ) : null}
            <Text as="p" level="caption" className="text-content-layout-3">
              {DISCOVERY_BACKOFF_NOTE}
            </Text>
          </div>
        ) : null}
        <Text as="p" level="caption" className="text-content-layout-3">
          {DISCOVERY_COVERAGE_NOTE}
        </Text>
      </PopoverContent>
    </Popover>
  )
}
