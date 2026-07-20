import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack } from "@rs/ui-new/stack";
import { isNotCacheable } from "../lib/queryImpact";

interface QueryCacheStatusProps {
  /** Live: the query is currently in the cache (from SHOW CACHES). */
  cached: boolean;
  /**
   * Persisted Readyset verdict from the registry (rdst-41p.1):
   * "" | "yes" | "pending" | "unsupported: <reason>".
   */
  readysetSupported?: string;
  /** A "Cache & test" run is in flight for this query (rdst-41p.3). */
  testing?: boolean;
  /**
   * Measured speedup over the origin from the most recent test, when the
   * cache won (rdst-41p.3). Turns "Cached" into proof: "Cached &middot; 24x faster".
   */
  speedup?: number;
}

// Per-query cache status for the Queries workbench. Only terminal states get a
// badge: a test in flight, then Cached (with any measured speedup), then a
// confirmed "Not cacheable" verdict. Cacheable-or-unknown queries show no badge
// (lazy EXPLAIN rarely confirms cacheability up front) -- the row's Cache action
// speaks for itself, so a positive badge would only imply the unmarked ones are
// not cacheable (rdst-41p.11).
export function QueryCacheStatus({ cached, readysetSupported, testing, speedup }: QueryCacheStatusProps) {
  if (testing) {
    return (
      <HStack className="gap-1.5 items-center">
        <span className="w-3 h-3 rounded-full border-2 border-content-primary-soft border-t-transparent animate-spin" />
        <Text level="label-small" className="text-content-layout-2">
          Testing
        </Text>
      </HStack>
    );
  }
  if (cached) {
    return (
      <HStack className="gap-1.5 items-center">
        <Icon name="tick-double" label="Cached" className="w-3.5 h-3.5 text-content-positive-soft" />
        <Text level="label-small" className="text-content-positive-soft">
          {speedup && speedup > 1 ? <>Cached &middot; {speedup.toFixed(1)}x faster</> : "Cached"}
        </Text>
      </HStack>
    );
  }

  if (isNotCacheable(readysetSupported)) {
    return (
      <HStack className="gap-1.5 items-center">
        <span className="w-1.5 h-1.5 rounded-full bg-content-layout-disabled" />
        <Text level="label-small" className="text-content-layout-3">
          Not cacheable
        </Text>
      </HStack>
    );
  }
  // Never cache-checked: assert nothing (lazy EXPLAIN), the action still shows.
  return null;
}
