import { createFileRoute } from '@tanstack/react-router'
import { InboxPage } from '@/features/inbox'

/**
 * Search params of /inbox: the queue switch (`open` is the default), the database filter
 * (`db`; older links carry `target`) and the selected item. Older links carried
 * `state=act_now` or `state=watch`, which land on Open.
 */
export type InboxSearch = {
  state?: 'open' | 'closed'
  db?: string
  target?: string
  item?: number
  monitorFixture?: string
}

export const Route = createFileRoute('/inbox')({
  staticData: { layout: 'split' },
  validateSearch: (search: Record<string, unknown>): InboxSearch => {
    const out: InboxSearch = {}
    if (search.state === 'closed') out.state = 'closed'
    if (typeof search.db === 'string' && search.db) out.db = search.db
    if (typeof search.target === 'string' && search.target)
      out.target = search.target
    const item = Number(search.item)
    if (search.item !== undefined && Number.isInteger(item) && item > 0)
      out.item = item
    if (typeof search.monitorFixture === 'string' && search.monitorFixture)
      out.monitorFixture = search.monitorFixture
    return out
  },
  component: InboxPage,
})
