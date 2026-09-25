// Why a query the Analyses list points at isn't in the list as filtered, and what to
// clear to show it. Only the narrowing that actually hides it is named and cleared; the
// sort and every other filter stay. Pure, so the rules are unit-tested.
import type { MonitorQuery } from '@/lib/monitor/types'
import {
  type FactsOf,
  FILTER_LABELS,
  matchesFilter,
  type QueryFilter,
  searchQueries,
} from './querySelectors'

export type Exclusion = 'writes' | 'picked' | 'table' | 'search' | 'filter'

export interface FocusContext {
  search: string
  filter: QueryFilter
  table: string | null
  keys: readonly string[] | null
  writesHidden: boolean
  isWrite: boolean
  factsOf: FactsOf
}

/** What hides `query` from the list, in the order the notice names them. */
export function exclusions(
  query: MonitorQuery,
  ctx: FocusContext
): Exclusion[] {
  const out: Exclusion[] = []
  if (ctx.writesHidden && ctx.isWrite) out.push('writes')
  if (ctx.keys && !ctx.keys.includes(query.key)) out.push('picked')
  if (ctx.table && !query.tables.includes(ctx.table)) out.push('table')
  if (ctx.search.trim() && !searchQueries([query], ctx.search).length)
    out.push('search')
  if (ctx.filter !== 'all' && !matchesFilter(ctx.factsOf(query), ctx.filter))
    out.push('filter')
  return out
}

function joinParts(parts: string[]) {
  if (parts.length <= 1) return parts.join('')
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/** "SELECT on orders is filtered out by your search and the 'Healthy' filter." */
export function exclusionSentence(
  name: string,
  reasons: readonly Exclusion[],
  ctx: Pick<FocusContext, 'filter' | 'table'>
): string {
  const parts = reasons.map((reason) => {
    switch (reason) {
      case 'writes':
        return 'the writes toggle'
      case 'picked':
        return "the alert's query list"
      case 'table':
        return `the ${ctx.table} table filter`
      case 'search':
        return 'your search'
      case 'filter':
        return `the '${FILTER_LABELS[ctx.filter]}' filter`
    }
  })
  return `${name} is filtered out by ${joinParts(parts)}.`
}
