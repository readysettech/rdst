import { createFileRoute } from '@tanstack/react-router'
import { ActionsPage } from '@/features/actions/ActionsPage'

/**
 * Search params of /actions: the database filter, Plan or Applied, a kind, the grouping, and
 * the change to open on load.
 */
export type ActionsSearch = {
  db?: string
  action?: number
  scope?: 'plan' | 'applied'
  kind?: string
  group?: string
  monitorFixture?: string
}

const text = (value: unknown) =>
  typeof value === 'string' && value ? value : undefined

function parseActionsSearch(search: Record<string, unknown>): ActionsSearch {
  const out: ActionsSearch = {}
  const db = text(search.db)
  if (db) out.db = db
  const action = Number(search.action)
  if (Number.isInteger(action) && action > 0) out.action = action
  if (search.scope === 'plan' || search.scope === 'applied')
    out.scope = search.scope
  const kind = text(search.kind)
  if (kind) out.kind = kind
  const group = text(search.group)
  if (group) out.group = group
  const fixture = text(search.monitorFixture)
  if (fixture) out.monitorFixture = fixture
  return out
}

export const Route = createFileRoute('/actions')({
  staticData: { layout: 'split' },
  validateSearch: parseActionsSearch,
  component: ActionsPage,
})
