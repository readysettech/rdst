import type { QueryRegistryEntry } from "./api";

// Impact ranking for the Queries workbench (rdst-41p.2). "Impact" is the total
// database time a query has accumulated: average latency times how often it was
// observed. Only Slow Queries (top) telemetry populates avg_duration_ms and
// observation_count, so ask/manual/analyze rows score 0 and fall to a recency
// tail behind the measured ones.

export function queryImpactMs(entry: QueryRegistryEntry): number {
  return (entry.avg_duration_ms ?? 0) * (entry.observation_count ?? 0);
}

// Impact descending. Array.prototype.sort is stable, so equal-impact rows
// (including the all-zero tail) keep the API's incoming recency order.
export function byImpact(a: QueryRegistryEntry, b: QueryRegistryEntry): number {
  return queryImpactMs(b) - queryImpactMs(a);
}

// Duration that scales past seconds into minutes and hours; formatDuration in
// lib/formatters stops at seconds, which reads poorly for accumulated DB time.
export function formatDbTime(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const min = s / 60;
  if (min < 60) return `${min.toFixed(1)} min`;
  return `${(min / 60).toFixed(1)} h`;
}

// Run count as a locale string, or null when the query has no measured
// telemetry. Rendered as a separate meta part alongside the DB-time headline.
export function formatRunCount(entry: QueryRegistryEntry): string | null {
  const count = entry.observation_count ?? 0;
  if (count <= 0 || queryImpactMs(entry) <= 0) return null;
  return `${count.toLocaleString()} runs`;
}

// Compact impact headline for a row ("9.9 min DB time"), or null when the query
// has no measured telemetry.
export function formatImpactCaption(entry: QueryRegistryEntry): string | null {
  const impact = queryImpactMs(entry);
  if (impact <= 0) return null;
  return `${formatDbTime(impact)} DB time`;
}

// A query worth promoting as the "biggest win": measured impact, not yet
// cached, and not known-uncacheable (an unknown verdict is still a candidate --
// cacheability is resolved lazily on Cache & test).
// A confirmed-uncacheable verdict ("unsupported: <reason>") from Readyset. An
// empty or "yes"/"pending" verdict is not a block. Shared by the status badge,
// the hero predicate, and the row action gate so the encoding lives in one spot.
export function isNotCacheable(readysetSupported?: string): boolean {
  return (readysetSupported ?? "").startsWith("unsupported");
}

export function isHeroCandidate(entry: QueryRegistryEntry, cached: boolean): boolean {
  return !cached && !isNotCacheable(entry.readyset_supported) && queryImpactMs(entry) > 0;
}
