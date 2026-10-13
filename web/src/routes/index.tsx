import { createFileRoute } from '@tanstack/react-router'
import { HomePage } from '../features/home/HomePage'

/** Search params of /: the database filter. */
export type HomeSearch = { db?: string; monitorFixture?: string }

export const Route = createFileRoute('/')({
  staticData: { layout: 'page' },
  validateSearch: (search: Record<string, unknown>): HomeSearch => {
    const out: HomeSearch = {}
    if (typeof search.db === 'string' && search.db) out.db = search.db
    if (typeof search.monitorFixture === 'string' && search.monitorFixture)
      out.monitorFixture = search.monitorFixture
    return out
  },
  component: HomePage,
})
