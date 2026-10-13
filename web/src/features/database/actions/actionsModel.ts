// One database's actions in numbers, for the Live tab's pointer and the report's summary.
import type { ActionCard, ActionsPage, IndexPlan } from '@/lib/monitor/actions'

export type Category =
  | 'indexes'
  | 'settings'
  | 'maintenance'
  | 'rewrites'
  | 'caching'

/** The kinds of change, in their default order. */
export const CATEGORIES: readonly { id: Category; label: string }[] = [
  { id: 'indexes', label: 'Indexes' },
  { id: 'settings', label: 'Server settings' },
  { id: 'maintenance', label: 'Maintenance' },
  { id: 'rewrites', label: 'Query rewrites' },
  { id: 'caching', label: 'Caching' },
]

/** Every open change, a grouped card's own changes counted one by one. */
export function flat(page: ActionsPage | undefined): ActionCard[] {
  return [...(page?.actions ?? []), ...(page?.more ?? [])].flatMap((card) =>
    card.children.length ? card.children : [card]
  )
}

/** How many changes are open: the index plan's, then every other kind's. */
export function changeCount(
  page: ActionsPage | undefined,
  plan?: IndexPlan | null
): number {
  if (!plan?.add || !plan.drop) return page?.open_count ?? 0
  return (
    plan.add.length +
    plan.drop.length +
    flat(page).filter((card) => card.category !== 'indexes').length
  )
}

/** The first plan is still on its way: the first deep analyses are running. */
export function buildingFirstPlan(page: ActionsPage | undefined): boolean {
  return page?.checkup?.state === 'first_pass'
}

/**
 * The index plan's statement for a query's open action, the one the plan helps it most
 * with. The plan is canonical: the query pane shows and copies this statement.
 */
export function planSqlFor(
  plan: IndexPlan | null | undefined,
  key: string
): string | null {
  let best: { sql: string; gain: number } | null = null
  for (const add of plan?.add ?? []) {
    if (plan?.set_aside?.includes(add.id)) continue
    const help = add.helps.find((entry) => entry.key === key)
    if (help && (!best || help.gain > best.gain))
      best = { sql: add.sql, gain: help.gain }
  }
  return best?.sql ?? null
}
