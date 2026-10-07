import { createFileRoute, redirect } from '@tanstack/react-router'
import { withoutLegacyBenchmarkParams } from '../features/database/benchmark/eligibility'
import { DatabasePage } from '../features/database/DatabasePage'
import { selectGlobalTarget } from '../features/database/targetSelection'

/**
 * Search params of /database (brief 2.3, DESIGN_V4). The query list params (query,
 * tab, filter, sort, q, table, queries) are read by the list itself; `view`, `report`,
 * `range`, `metric`, `health` and `panel` by the page (useDatabaseUrl); `target` sets the
 * global target and is stripped, and so are the old benchmark drawer's `bench`, `run`
 * and `select`.
 */
export type DatabaseSearch = {
  target?: string
  range?: string
  query?: string
  tab?: string
  filter?: string
  sort?: string
  q?: string
  /** Query list table filter. */
  table?: string
  /** Exactly these queries (comma-separated keys), from an Inbox item. */
  queries?: string
  /** A query to bring into view and highlight without changing filters or sort. */
  focus?: string
  /** Query list Jev filter. */
  jev?: string
  /** Zoom window, `start-end` in Unix seconds. */
  zoom?: string
  /** The Inbox item a link came from (`inbox:<id>`): the page says why you're here. */
  from?: string
  /** Old benchmark drawer links: rewritten away on load. */
  bench?: string
  run?: string
  select?: string
  /** `settings` opens the monitoring settings sheet, `setup` the prerequisites check and
   * `enable` the turn-on dialog. */
  panel?: string
  /** The tab: `actions`, `indexes`, `queries` (or `live`) or `report`; `report` picks one
   * (default latest). */
  view?: string
  /** An action to open on the Actions tab, from an Inbox item's likely fix. */
  action?: string
  report?: string
  /** Hero metric (`load`, `calls`, `latency`) and a health chart in its place. */
  metric?: string
  health?: string
  monitorFixture?: string
}

const KEYS = [
  'target',
  'range',
  'query',
  'tab',
  'filter',
  'sort',
  'q',
  'table',
  'queries',
  'focus',
  'jev',
  'zoom',
  'from',
  'bench',
  'run',
  'select',
  'panel',
  'view',
  'action',
  'report',
  'metric',
  'health',
  'monitorFixture',
] as const

export const Route = createFileRoute('/database')({
  validateSearch: (search: Record<string, unknown>): DatabaseSearch => {
    const out: DatabaseSearch = {}
    for (const key of KEYS) {
      const value = search[key]
      if (typeof value === 'string' && value) out[key] = value
      else if (typeof value === 'number') out[key] = String(value)
    }
    return out
  },
  beforeLoad: ({ search }) => {
    const legacy = withoutLegacyBenchmarkParams(search)
    if (!search.target && !legacy) return
    if (search.target) selectGlobalTarget(search.target)
    const { target: _target, ...rest } = legacy ?? search
    throw redirect({ to: '/database', search: rest, replace: true })
  },
  component: DatabasePage,
})
