// "What you can do" as tiles: each one a concrete next step that does something when
// clicked (opens the right chart, copies the SQL, closes the alert). Built from the
// item's own evidence and the backend's advice steps.
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import type { InboxItem } from '@/lib/monitor/types'
import { adviceSteps } from './adviceSteps'
import { itemLink, itemRoute, type LinkRoute, linkRoute } from './inboxLinks'
import { formatStampTime } from './inboxModel'
import { incidentWindow } from './incidentModel'
import { clusterRoute } from './onsetClusterModel'

export type TileAction =
  | { kind: 'open'; route: LinkRoute }
  | { kind: 'copy'; text: string }
  | { kind: 'resolve' }

/**
 * What a tile does, which sets its color: look at a chart, look around the moment,
 * check the plan, close the alert, or copy SQL to run yourself.
 */
export type TileKind = 'chart' | 'frame' | 'plan' | 'resolve' | 'copy'

export interface ActionTile {
  id: string
  kind: TileKind
  icon: IconStrokeName
  title: string
  detail: string
  action: TileAction
}

const LATENCY_TYPES = new Set([
  'latency_regression',
  'plan_change',
  'table_slowdown',
  'slow_creep',
])

/** Every query around the moment the change began, on the Database page. */
function aroundThen(item: InboxItem, now: number): ActionTile | null {
  const window = incidentWindow(item)
  if (!window) return null
  const at = formatStampTime(window.start, now)
  return {
    id: 'frame',
    kind: 'frame',
    icon: 'observe',
    title: 'See what else ran then',
    detail: `Every query around ${at}.`,
    action: {
      kind: 'open',
      route: clusterRoute(
        item,
        { start: window.start, last: window.start },
        now
      ),
    },
  }
}

/** The item's query on another tab of its detail. */
function queryTab(
  item: InboxItem,
  tab: 'performance' | 'analysis'
): LinkRoute | null {
  const link = itemLink(item)
  if (link.kind !== 'query' || !link.query_key) return null
  const route = itemRoute(item)
  const base = linkRoute({ ...link, tab }).search
  const search = { ...route.search, ...base }
  if (tab === 'performance') delete search.tab
  return { to: route.to, search }
}

function computedTiles(item: InboxItem, now: number): ActionTile[] {
  const tiles: (ActionTile | null)[] = []
  const window = incidentWindow(item)
  const when = window ? formatStampTime(window.start, now) : null
  const performance = queryTab(item, 'performance')
  const analysis = queryTab(item, 'analysis')
  switch (item.type) {
    case 'query_stopped_running':
      tiles.push(
        performance && {
          id: 'chart',
          kind: 'chart',
          icon: 'dashboard',
          title: 'See when it stopped',
          detail: window?.measured
            ? `Its last calls, around ${formatStampTime(window.start - 60, now)}.`
            : 'Its calls over the last day.',
          action: { kind: 'open', route: performance },
        },
        aroundThen(item, now),
        {
          id: 'resolve',
          kind: 'resolve',
          icon: 'tick',
          title: 'Removed on purpose?',
          detail: 'Resolve the alert.',
          action: { kind: 'resolve' },
        }
      )
      break
    case 'traffic_spike':
      tiles.push(
        performance && {
          id: 'chart',
          kind: 'chart',
          icon: 'dashboard',
          title: 'See the jump',
          detail: when
            ? `Its calls around ${when}.`
            : 'Its calls over the last day.',
          action: { kind: 'open', route: performance },
        },
        aroundThen(item, now)
      )
      break
    case 'new_heavy_query':
      tiles.push(
        analysis && {
          id: 'plan',
          kind: 'plan',
          icon: 'layers',
          title: 'Check its plan',
          detail:
            'Deep analysis shows how it runs and whether an index would help.',
          action: { kind: 'open', route: analysis },
        },
        aroundThen(item, now)
      )
      break
    case 'cache_hit_ratio_drop':
      tiles.push(aroundThen(item, now), {
        id: 'buffers',
        kind: 'copy',
        icon: 'database-settings',
        title: 'Check the buffer size',
        detail: 'If the working set grew, it may be too small.',
        action: { kind: 'copy', text: 'SHOW shared_buffers;' },
      })
      break
    case 'rollback_or_deadlock_spike':
      tiles.push(aroundThen(item, now))
      break
    default:
      if (LATENCY_TYPES.has(item.type))
        tiles.push(
          analysis && {
            id: 'plan',
            kind: 'plan',
            icon: 'layers',
            title: 'Check the plan',
            detail: 'How it runs now, from deep analysis.',
            action: { kind: 'open', route: analysis },
          },
          aroundThen(item, now)
        )
  }
  return tiles.filter((tile): tile is ActionTile => tile !== null)
}

/** Tiles for an open item: computed steps first, then the advice's copyable SQL. */
export function actionTiles(
  item: InboxItem,
  now = Date.now() / 1000
): ActionTile[] {
  if (item.state === 'resolved' || item.state === 'dismissed') return []
  if (item.type === 'many_queries_changed' || item.type === 'daily_summary')
    return []
  const tiles = computedTiles(item, now)
  const steps = adviceSteps(item.advice?.steps ?? [])
  steps.forEach((step, index) => {
    if (step.sql)
      tiles.push({
        id: `sql-${index}`,
        kind: 'copy',
        icon: 'copy',
        title: step.text,
        detail: '',
        action: { kind: 'copy', text: step.sql },
      })
  })
  return tiles
}

/**
 * Advice steps no tile covers, for items RDST has no tiles of its own for: they read
 * as plain sentences after the explanation.
 */
export function adviceNotes(
  item: InboxItem,
  now = Date.now() / 1000
): string[] {
  if (item.state === 'resolved' || item.state === 'dismissed') return []
  if (computedTiles(item, now).length > 0) return []
  return adviceSteps(item.advice?.steps ?? [])
    .filter((step) => !step.sql)
    .map((step) => step.text)
}
