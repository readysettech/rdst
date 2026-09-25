import { createFileRoute } from '@tanstack/react-router'
import { InboxPage } from '@/features/inbox'

/**
 * Search params of /inbox: the tab (`open` is the default), whether muted alerts show,
 * a target filter and an item. Older links used `state=done` or `state=resolved` (now
 * Open, which lists recoveries for 24 hours) and `state=muted` (now the muted filter).
 */
export type InboxSearch = {
  state?: 'open' | 'act_now' | 'watch'
  muted?: boolean
  target?: string
  item?: number
  monitorFixture?: string
}

const STATES = ['open', 'act_now', 'watch'] as const

export const Route = createFileRoute('/inbox')({
  validateSearch: (search: Record<string, unknown>): InboxSearch => {
    const out: InboxSearch = {}
    const state = STATES.find((value) => value === search.state)
    if (state) out.state = state
    if (
      search.state === 'muted' ||
      search.muted === true ||
      search.muted === 'true' ||
      search.muted === 1 ||
      search.muted === '1'
    )
      out.muted = true
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
