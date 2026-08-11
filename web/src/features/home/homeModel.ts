import type { QueryRegistryEntry } from '../../lib/api'

export type HomeState = 'first-run' | 'connected' | 'active'

export interface PortfolioCounts {
  asked: number
  analyzed: number
  compared: number
  /** Verified as supported by Readyset but not yet measured in Compare. */
  candidates: number
}

export type ContinueKind = 'compared' | 'analyzed' | 'asked' | 'saved'

export interface ContinueItem {
  hash: string
  label: string
  kind: ContinueKind
  nextAction: 'Analyze' | 'Review' | 'Compare'
  sql: string
  mostRecentParams?: Record<string, unknown>
  lastActivity: string
  avgDurationMs: number
  frequency: number
}

export function deriveHomeState(
  targetCount: number,
  semanticLayerExists: boolean | undefined
): HomeState {
  if (targetCount === 0) return 'first-run'
  if (!semanticLayerExists) return 'connected'
  return 'active'
}

function scoped(entries: QueryRegistryEntry[], target: string | undefined) {
  if (!target) return entries
  return entries.filter((entry) => entry.target === target)
}

export function portfolioCounts(
  entries: QueryRegistryEntry[],
  target: string | undefined,
  comparedHashes: ReadonlySet<string> = new Set()
): PortfolioCounts {
  const targetEntries = scoped(entries, target)
  return {
    asked: targetEntries.filter((entry) => entry.source === 'ask').length,
    analyzed: targetEntries.filter((entry) => Boolean(entry.last_analyzed))
      .length,
    compared: targetEntries.filter((entry) => comparedHashes.has(entry.hash))
      .length,
    candidates: targetEntries.filter(
      (entry) =>
        entry.readyset_supported === 'yes' && !comparedHashes.has(entry.hash)
    ).length,
  }
}

function kindOf(
  entry: QueryRegistryEntry,
  comparedHashes: ReadonlySet<string>
): ContinueKind {
  if (comparedHashes.has(entry.hash)) return 'compared'
  if (entry.last_analyzed) return 'analyzed'
  if (entry.source === 'ask') return 'asked'
  return 'saved'
}

const NEXT_ACTION: Record<ContinueKind, ContinueItem['nextAction']> = {
  compared: 'Compare',
  analyzed: 'Review',
  asked: 'Analyze',
  saved: 'Analyze',
}

export function continueItems(
  entries: QueryRegistryEntry[],
  target: string | undefined,
  comparedHashes: ReadonlySet<string> = new Set(),
  limit = 3
): ContinueItem[] {
  return scoped(entries, target)
    .slice()
    .sort((a, b) =>
      (b.last_analyzed || b.first_analyzed || '').localeCompare(
        a.last_analyzed || a.first_analyzed || ''
      )
    )
    .slice(0, limit)
    .map((entry) => {
      const kind = kindOf(entry, comparedHashes)
      return {
        hash: entry.hash,
        label: entry.tag || entry.sql,
        kind,
        nextAction: NEXT_ACTION[kind],
        sql: entry.sql,
        mostRecentParams: entry.most_recent_params,
        lastActivity: entry.last_analyzed || entry.first_analyzed || '',
        avgDurationMs: entry.avg_duration_ms ?? 0,
        frequency: entry.frequency ?? 0,
      }
    })
}
