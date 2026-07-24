import { Icon } from '@rs/ui-new/icon'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { isNotCacheable } from '../lib/queryImpact'

interface QueryCacheStatusProps {
  /** This browser retains a completed Readyset comparison for the query. */
  cached: boolean
  /**
   * Persisted Readyset verdict from the registry (rdst-41p.1):
   * "" | "yes" | "pending" | "unsupported: <reason>".
   */
  readysetSupported?: string
  /** A temporary Readyset comparison is in flight for this query. */
  testing?: boolean
  /**
   * Measured speedup over the origin from the most recent test, when the
   * cache won.
   */
  speedup?: number
}

// Per-query cache status for the Queries workbench. Only terminal states get a
// badge: a test in flight, then measured speedup, then a confirmed unsupported
// verdict. Compatible-or-unknown queries show no badge
// (lazy EXPLAIN rarely confirms cacheability up front) -- the row's Cache action
// speaks for itself, so a positive badge would only imply the unmarked ones are
// not cacheable (rdst-41p.11).
export function QueryCacheStatus({
  cached,
  readysetSupported,
  testing,
  speedup,
}: QueryCacheStatusProps) {
  if (testing) {
    return (
      <HStack className="gap-1.5 items-center">
        <span className="w-3 h-3 rounded-full border-2 border-content-primary-soft border-t-transparent animate-spin" />
        <Text level="label-small" className="text-content-layout-2">
          Testing
        </Text>
      </HStack>
    )
  }
  if (cached) {
    return (
      <HStack className="gap-1.5 items-center">
        <Icon
          name="tick-double"
          label="Compared"
          className="w-3.5 h-3.5 text-content-positive-soft"
        />
        <Text level="label-small" className="text-content-positive-soft">
          {speedup && speedup > 1 ? (
            <>{speedup.toFixed(1)}x faster with Readyset</>
          ) : (
            'Compared'
          )}
        </Text>
      </HStack>
    )
  }

  if (isNotCacheable(readysetSupported)) {
    return (
      <HStack className="gap-1.5 items-center">
        <span className="w-1.5 h-1.5 rounded-full bg-content-layout-disabled" />
        <Text level="label-small" className="text-content-layout-3">
          Unsupported by Readyset
        </Text>
      </HStack>
    )
  }
  // Never cache-checked: assert nothing (lazy EXPLAIN), the action still shows.
  return null
}
