import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'

/** The pages that filter by database, each with its own memory. */
export type DbFilterPage = 'home' | 'inbox' | 'actions' | 'ask'

export const dbFilterStorageKey = (page: DbFilterPage) =>
  `rdst_filter_db:${page}`

function readStored(page: DbFilterPage): string | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage.getItem(dbFilterStorageKey(page)) || null
  } catch {
    return null
  }
}

function writeStored(page: DbFilterPage, db: string | null) {
  try {
    if (db) window.localStorage.setItem(dbFilterStorageKey(page), db)
    else window.localStorage.removeItem(dbFilterStorageKey(page))
  } catch {
    // Storage can be unavailable; the URL still carries the choice.
  }
}

/**
 * A page's database filter: `?db=` when the URL names one, else the page's last choice,
 * which is then written to the URL so the address shows the filter in effect. Null means
 * all databases. `setDb` writes the URL and the page's memory.
 */
export function useDbFilter(page: DbFilterPage): {
  db: string | null
  setDb: (db: string | null) => void
} {
  const navigate = useNavigate()
  const fromUrl = useRouterState({
    select: (state) => {
      const value = (state.location.search as Record<string, unknown>).db
      return typeof value === 'string' && value ? value : null
    },
  })
  const [stored, setStored] = useState(() => readStored(page))
  useEffect(() => setStored(readStored(page)), [page])
  // On arrival only: later choices go through setDb, which writes the URL itself.
  const arrived = useRef(false)
  useEffect(() => {
    if (arrived.current) return
    arrived.current = true
    if (fromUrl || !stored) return
    void navigate({
      to: '.',
      search: ((current: Record<string, unknown>) => ({
        ...current,
        db: stored,
      })) as never,
      replace: true,
    })
  }, [fromUrl, stored, navigate])

  const setDb = useCallback(
    (db: string | null) => {
      const next = db?.trim() || null
      writeStored(page, next)
      setStored(next)
      void navigate({
        to: '.',
        search: ((current: Record<string, unknown>) => {
          const { db: _db, ...rest } = current
          return next ? { ...rest, db: next } : rest
        }) as never,
        replace: true,
      })
    },
    [navigate, page]
  )

  return { db: fromUrl ?? stored, setDb }
}
