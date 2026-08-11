import { useMemo } from 'react'
import { queryImpactMs } from '../../../lib/queryImpact'
import { detectParameters } from '../../../lib/sqlParameters'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { useSavedQueriesController } from '../saved/useSavedQueriesController'

export type QueryLabVariant = '1' | '2'

export const QUERY_LAB_VARIANTS: Record<
  QueryLabVariant,
  { name: string; hypothesis: string }
> = {
  '1': {
    name: 'Current QueryCard',
    hypothesis:
      'The unchanged production card shown across its real interactive contexts.',
  },
  '2': {
    name: 'Impact rail',
    hypothesis:
      'A 16rem evidence rail makes workload cost scannable while preserving every production interaction.',
  },
}

export function queryLabTitle(entry: QueryRegistryEntry) {
  if (entry.tag?.trim()) return entry.tag.trim()
  const normalized = entry.sql.replace(/\s+/g, ' ').trim()
  const aggregate = normalized.match(/\b(count|sum|avg|min|max)\s*\(/i)?.[1]
  const table = normalized.match(
    /\b(?:from|into|update)\s+["'`[]?([\w.]+)/i
  )?.[1]
  if (aggregate && table) {
    return `${aggregate.toUpperCase()} on ${table.split('.').pop()}`
  }
  if (table) return `Query on ${table.split('.').pop()}`
  return 'Untitled query'
}

export function queryLabParameterLabel(entry: QueryRegistryEntry) {
  const count = detectParameters(entry.original_sql || entry.sql).length
  if (count === 0) return 'No parameters'
  const ready = Object.keys(entry.most_recent_params ?? {}).length
  return ready >= count ? `${count} ready` : `${count - ready} values needed`
}

export function useQueriesLabController() {
  const base = useSavedQueriesController({})
  const queries = useMemo(
    () =>
      [...base.registry.queries].sort(
        (left, right) => queryImpactMs(right) - queryImpactMs(left)
      ),
    [base.registry.queries]
  )

  return {
    ...base,
    lab: { queries },
  }
}

export type QueriesLabController = ReturnType<typeof useQueriesLabController>
