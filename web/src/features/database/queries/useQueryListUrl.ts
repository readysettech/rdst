import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type DetailTab,
  parseFilter,
  parseQueryKeys,
  parseSort,
  parseTab,
  type QueryFilter,
  type QuerySort,
} from './querySelectors'

type ListParams = {
  query?: string
  tab?: DetailTab
  filter?: QueryFilter
  sort?: QuerySort
  q?: string
  table?: string
  /** Exactly these queries, comma-separated keys (an Inbox item's affected queries). */
  queries?: string
  /** Retired: the Jev filter, now part of `filter`. Written only to clear it. */
  jev?: string
  /** A query to bring into view and highlight, leaving filters and sort as they are. */
  focus?: string
  /** The page's side panel; written only to close it in the same write. */
  panel?: undefined
}

const DEFAULTS = {
  tab: 'performance',
  filter: 'all',
  sort: 'impact',
  q: '',
  table: '',
  queries: '',
  focus: '',
}

/**
 * List state lives in the URL (`query`, `tab`, `filter`, `sort`, `q`, `table`,
 * `queries`) so a link
 * from the Inbox or a report opens the same view. Every write replaces the entry;
 * the search box writes on a short delay while the list filters on each keystroke.
 */
export function useQueryListUrl() {
  const navigate = useNavigate()
  const search = useRouterState({
    select: (state) => state.location.search as Record<string, unknown>,
  })
  const openKey = typeof search.query === 'string' ? search.query : null
  const focusKey =
    typeof search.focus === 'string' && search.focus ? search.focus : null
  const tab = parseTab(search.tab)
  // Old links carry `jev=<finding>`; it reads as the same classification filter.
  const filter = parseFilter(search.filter, search.jev)
  const sort = parseSort(search.sort)
  const urlQ = typeof search.q === 'string' ? search.q : ''
  const table =
    typeof search.table === 'string' && search.table ? search.table : null
  const keys = parseQueryKeys(search.queries)
  const [q, setQState] = useState(urlQ)
  const pendingQ = useRef<string | null>(null)

  // Follow external URL changes (back/forward, a deep link) unless we are typing.
  useEffect(() => {
    if (pendingQ.current === null) setQState(urlQ)
  }, [urlQ])

  const searchRef = useRef(search)
  searchRef.current = search

  const write = useCallback(
    (patch: ListParams) => {
      const next: Record<string, unknown> = { ...searchRef.current }
      for (const [name, value] of Object.entries(patch)) {
        const fallback = DEFAULTS[name as keyof typeof DEFAULTS]
        if (value === undefined || value === null || value === fallback)
          delete next[name]
        else next[name] = value
      }
      // Several writes in one handler (Clear filters) each build on the last.
      searchRef.current = next
      void navigate({
        to: '.',
        search: next as never,
        replace: true,
        resetScroll: false,
      })
    },
    [navigate]
  )

  useEffect(() => {
    if (q === urlQ) {
      pendingQ.current = null
      return
    }
    pendingQ.current = q
    const timer = window.setTimeout(() => {
      pendingQ.current = null
      write({ q })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [q, urlQ, write])

  return {
    openKey,
    /** The query the Analyses list asked to show; cleared once it's in view. */
    focusKey,
    clearFocus: () => write({ focus: undefined }),
    /** Bring one query into view: its page, scrolled to and highlighted. */
    showQuery: (key: string) => write({ focus: key }),
    tab,
    filter,
    sort,
    q,
    setQ: setQState,
    // A new filter or order starts with every row closed.
    setFilter: (value: QueryFilter) =>
      write({
        filter: value,
        jev: undefined,
        query: undefined,
        tab: undefined,
      }),
    setSort: (value: QuerySort) =>
      write({ sort: value, query: undefined, tab: undefined }),
    table,
    setTable: (value: string | null) => write({ table: value ?? undefined }),
    /** The Inbox item's queries the list is narrowed to, or null. */
    keys,
    clearKeys: () => write({ queries: undefined }),
    /** Open one query's pane; `closePanel` also closes the page's side panel. */
    open: (
      key: string | null,
      nextTab?: DetailTab,
      { closePanel = false }: { closePanel?: boolean } = {}
    ) =>
      write({
        query: key ?? undefined,
        tab: key ? (nextTab ?? tab) : undefined,
        ...(closePanel ? { panel: undefined } : {}),
      }),
    setTab: (value: DetailTab) => write({ tab: value }),
  }
}
