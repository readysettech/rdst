// Where an inbox item opens. The backend decides the place (item.link); this maps it
// to a route. Items from before links existed open the Database page.
import type { InboxItem, InboxLink } from '@/lib/monitor/types'
import { incidentSearch } from './incidentModel'

export interface LinkRoute {
  to: string
  search: Record<string, string>
}

export function fallbackLink(item: InboxItem): InboxLink {
  const key = item.query_keys.length === 1 ? item.query_keys[0] : undefined
  return key
    ? {
        kind: 'query',
        target: item.target,
        query_key: key,
        tab: 'performance',
        label: 'Open query',
      }
    : { kind: 'database', target: item.target, label: 'Open database' }
}

export function itemLink(item: InboxItem): InboxLink {
  return item.link ?? fallbackLink(item)
}

/** The route and search params for a link; `queryKey` opens another query of an episode. */
export function linkRoute(link: InboxLink, queryKey?: string): LinkRoute {
  const target = link.target
  if (queryKey) return { to: '/database', search: { target, query: queryKey } }
  switch (link.kind) {
    case 'query': {
      const search: Record<string, string> = { target }
      if (link.query_key) search.query = link.query_key
      if (link.tab && link.tab !== 'performance') search.tab = link.tab
      return { to: '/database', search }
    }
    case 'report': {
      // Reports live on the Database page's Report tab.
      const search: Record<string, string> = { target, view: 'report' }
      if (link.report_id != null) search.report = String(link.report_id)
      return { to: '/database', search }
    }
    case 'settings':
      return { to: '/database', search: { target, panel: 'settings' } }
    case 'setup':
      return { to: '/database', search: { target, panel: 'setup' } }
    case 'configure':
      return {
        to: '/configure',
        search: { panel: 'connections', edit: target },
      }
    case 'sign_in':
      return { to: '/account-login', search: {} }
    default: {
      const search: Record<string, string> = { target }
      // Several queries: the list shows exactly these, not a status filter.
      if (link.query_keys?.length) search.queries = link.query_keys.join(',')
      else if (link.filter) search.filter = link.filter
      return { to: '/database', search }
    }
  }
}

/**
 * Where an item opens, carrying the item: a Database page link zooms the chart to the
 * incident and names the item in `from`, so the page can say why you're there.
 */
export function itemRoute(item: InboxItem, queryKey?: string): LinkRoute {
  const link = itemLink(item)
  const route = linkRoute(link, queryKey)
  const onChart =
    route.to === '/database' && !route.search.view && !route.search.panel
  if (!onChart) return route
  const key = queryKey ?? (link.kind === 'query' ? link.query_key : undefined)
  return {
    ...route,
    search: { ...route.search, ...incidentSearch(item, key) },
  }
}

/** The URL for a route, for real links (middle click, copy link address). */
export function routeHref(route: LinkRoute): string {
  const search = new URLSearchParams(route.search).toString()
  return search ? `${route.to}?${search}` : route.to
}

/** The Database page's Actions view, opened on the item's likely fix. */
export function fixRoute(item: InboxItem): LinkRoute | null {
  if (!item.action) return null
  return {
    to: '/database',
    search: {
      target: item.target,
      view: 'actions',
      action: String(item.action.id),
    },
  }
}
