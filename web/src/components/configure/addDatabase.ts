// The add-database dialog opens on any page through `?add=1`, or `?add=<provider>` for
// the provider import on that source. Pages that list databases hear about additions
// through `onDatabasesChanged`.
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useCallback } from 'react'
import { type AddTab, parseAddTab } from './addTabs'

/** What the dialog shows: the connection form, or one provider's import. */
export type AddRequest = 'form' | AddTab

/** Parse-only: `1` opens the form; the router reads it back as a number. */
export function parseAddRequest(value: unknown): AddRequest | null {
  if (value === 1 || value === '1') return 'form'
  return parseAddTab(value) ?? null
}

type Search = Record<string, unknown>

/** Open the dialog over the current page, keeping the page's own params. */
export function useOpenAddDatabase() {
  const navigate = useNavigate()
  return useCallback(
    (request: AddRequest = 'form') =>
      void navigate({
        to: '.',
        search: ((current: Search) => ({
          ...current,
          add: request === 'form' ? 1 : request,
        })) as never,
      }),
    [navigate]
  )
}

/** The dialog's request from the URL, and a close that drops it. */
export function useAddDatabaseRequest() {
  const navigate = useNavigate()
  const request = useRouterState({
    select: (state) => parseAddRequest((state.location.search as Search).add),
  })
  const close = useCallback(
    () =>
      void navigate({
        to: '.',
        search: (({ add: _add, ...rest }: Search) => rest) as never,
        replace: true,
      }),
    [navigate]
  )
  return { request, close }
}

const DATABASES_CHANGED = 'rdst_databases_changed'

export interface DatabasesChange {
  /** Re-check these databases' connections. */
  recheck?: string[]
  /** The credentials step was left: every connection result is stale. */
  recheckAll?: boolean
}

export function notifyDatabasesChanged(change: DatabasesChange = {}) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(
    new CustomEvent<DatabasesChange>(DATABASES_CHANGED, { detail: change })
  )
}

/** Subscribe to additions and saved credentials; returns the unsubscribe. */
export function onDatabasesChanged(
  listener: (change: DatabasesChange) => void
) {
  const handle = (event: Event) =>
    listener((event as CustomEvent<DatabasesChange>).detail ?? {})
  window.addEventListener(DATABASES_CHANGED, handle)
  return () => window.removeEventListener(DATABASES_CHANGED, handle)
}
