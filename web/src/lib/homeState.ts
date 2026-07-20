import type { QueryRegistryEntry } from './api';

export type HomeState = 'first-run' | 'connected' | 'active';

export interface PortfolioCounts {
  asked: number;
  analyzed: number;
  cached: number;
  /** Verified cacheable by Readyset but not yet cached — the unclaimed win. */
  candidates: number;
  /** Benchmark runs are not persisted anywhere yet (rdst-dma.5 note). */
  benchmarked: null;
}

export type ContinueKind = 'cached' | 'analyzed' | 'asked' | 'saved';

export interface ContinueItem {
  hash: string;
  label: string;
  kind: ContinueKind;
  nextAction: 'Analyze' | 'Cache' | 'Benchmark';
  sql: string;
  /** Captured parameter values, applied when handing the query to Analyze. */
  mostRecentParams?: Record<string, unknown>;
}

/**
 * Home state boundary (rdst-dma.1 open question, resolved in code as the
 * cheapest-to-change rule): a target is "active" once its semantic layer
 * exists. Activity without discovery keeps the user in "connected" — the
 * compulsion toward discovery is intended (rdst-dma.2), and that state keeps
 * every feature reachable via its "meanwhile" cards.
 */
export function deriveHomeState(
  targetCount: number,
  semanticLayerExists: boolean | undefined,
): HomeState {
  if (targetCount === 0) return 'first-run';
  if (!semanticLayerExists) return 'connected';
  return 'active';
}

function scoped(entries: QueryRegistryEntry[], target: string | undefined) {
  if (!target) return entries;
  return entries.filter((e) => e.target === target);
}

/**
 * cachedHashes holds the registry hashes currently served by Readyset, taken
 * from the live cache list (correlated by registry_hash). "Cached" reflects
 * that live set, not the registry's readyset_query_id -- that field records
 * the last-known Readyset id for lifecycle ops and survives a DROP CACHE, so
 * counting it kept dropped caches alive on the Home dashboard (rdst-e7s.32).
 */
export function portfolioCounts(
  entries: QueryRegistryEntry[],
  target: string | undefined,
  cachedHashes: ReadonlySet<string> = new Set(),
): PortfolioCounts {
  const s = scoped(entries, target);
  return {
    asked: s.filter((e) => e.source === 'ask').length,
    analyzed: s.filter((e) => Boolean(e.last_analyzed)).length,
    cached: s.filter((e) => cachedHashes.has(e.hash)).length,
    candidates: s.filter(
      (e) => e.readyset_supported === 'yes' && !cachedHashes.has(e.hash),
    ).length,
    benchmarked: null,
  };
}

function kindOf(
  e: QueryRegistryEntry,
  cachedHashes: ReadonlySet<string>,
): ContinueKind {
  if (cachedHashes.has(e.hash)) return 'cached';
  if (e.last_analyzed) return 'analyzed';
  if (e.source === 'ask') return 'asked';
  return 'saved';
}

const NEXT_ACTION: Record<ContinueKind, ContinueItem['nextAction']> = {
  cached: 'Benchmark',
  analyzed: 'Cache',
  asked: 'Analyze',
  saved: 'Analyze',
};

export function continueItems(
  entries: QueryRegistryEntry[],
  target: string | undefined,
  cachedHashes: ReadonlySet<string> = new Set(),
  limit = 3,
): ContinueItem[] {
  return scoped(entries, target)
    .slice()
    .sort((a, b) =>
      (b.last_analyzed || b.first_analyzed || '').localeCompare(
        a.last_analyzed || a.first_analyzed || '',
      ),
    )
    .slice(0, limit)
    .map((e) => {
      const kind = kindOf(e, cachedHashes);
      return {
        hash: e.hash,
        label: e.tag || e.sql,
        kind,
        nextAction: NEXT_ACTION[kind],
        sql: e.sql,
        mostRecentParams: e.most_recent_params,
      };
    });
}
