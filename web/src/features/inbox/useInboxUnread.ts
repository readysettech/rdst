import {
  type Query,
  type QueryClient,
  useQueryClient,
} from '@tanstack/react-query'
import { useEffect } from 'react'
import { subscribeInbox } from '@/lib/monitor/api'
import { useInbox, useMuteRules } from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import type { InboxItem, InboxQuery } from '@/lib/monitor/types'
import { showDesktopNotification } from './desktopNotifications'
import { inboxCounts } from './inboxModel'

export { isMuted } from './inboxModel'

/** Upserts arrive in bursts (one per item a collector tick touches). */
export const INBOX_REFRESH_DELAY_MS = 400

/**
 * Lists measured with incident windows are costly and their windows change once per
 * 5-minute cycle, so the stream refreshes them at most this often.
 */
export const INCIDENT_REFRESH_MS = 60_000

/** The Inbox page's list with incident windows, kept apart from the plain lists. */
export const incidentInboxKey = (query: InboxQuery) =>
  [...monitorKeys.inbox(query), 'incidents'] as const

const isIncidentList = (query: Query) => query.queryKey.at(-1) === 'incidents'

/** One stream event's item and state; the connect event carries neither. */
type ItemChange = { id: number; state: string }

function itemChange(
  data: Record<string, unknown> | undefined
): ItemChange | null {
  return typeof data?.item_id === 'number' && typeof data.state === 'string'
    ? { id: data.item_id, state: data.state }
    : null
}

/** True when a change adds an item to the list, drops one, or moves one to a new state. */
function changesList(query: Query, changes: ItemChange[]) {
  const items = (query.state.data as { items?: InboxItem[] } | undefined)?.items
  if (!items) return false
  return changes.some((change) => {
    const listed = items.find((item) => item.id === change.id)
    if (listed) return listed.state !== change.state
    return change.state !== 'resolved' && change.state !== 'dismissed'
  })
}

/**
 * Refreshes inbox queries after a burst of stream events. A hidden tab only marks
 * them stale and refetches when it is shown again, so background tabs don't hold
 * connections the visible tab needs. Incident lists refresh at most once a minute for
 * updates to items they already show; a new alert, a state change or a stream
 * (re)connect refreshes them right away. The connect event carries no item because
 * whatever was filed before the stream opened never arrives as its own event.
 */
export function inboxRefresher(
  client: QueryClient,
  delayMs = INBOX_REFRESH_DELAY_MS
) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let changes: ItemChange[] = []
  let connected = false
  return {
    schedule(data?: Record<string, unknown>) {
      const change = itemChange(data)
      if (change) changes.push(change)
      else connected = true
      if (timer) return
      timer = setTimeout(() => {
        timer = null
        const burst = changes
        const resync = connected
        changes = []
        connected = false
        const hidden =
          typeof document !== 'undefined' &&
          document.visibilityState === 'hidden'
        const now = Date.now()
        void client.invalidateQueries({
          queryKey: monitorKeys.inboxAll,
          refetchType: hidden ? 'none' : 'active',
          predicate: (query) =>
            resync ||
            !isIncidentList(query) ||
            now - query.state.dataUpdatedAt >= INCIDENT_REFRESH_MS ||
            changesList(query, burst),
        })
      }, delayMs)
    },
    cancel() {
      if (timer) clearTimeout(timer)
      timer = null
      changes = []
      connected = false
    },
  }
}

/**
 * Keeps inbox queries fresh from /inbox/stream. Mount once per page that shows
 * inbox data; the sidebar badge (useInboxUnread) mounts it for the whole app.
 */
export function useInboxStream() {
  const client = useQueryClient()
  useEffect(() => {
    // One EventSource however many components listen, so a `notified` event
    // raises one desktop notification.
    shared.users += 1
    if (shared.users === 1) {
      const refresher = inboxRefresher(client)
      const close = subscribeInbox((event) => {
        refresher.schedule(event.data)
        if (event.kind === 'notified') showDesktopNotification(event.data)
      })
      shared.close = () => {
        refresher.cancel()
        close()
      }
    }
    return () => {
      shared.users -= 1
      if (shared.users === 0) {
        shared.close?.()
        shared.close = null
      }
    }
  }, [client])
}

const shared: { users: number; close: (() => void) | null } = {
  users: 0,
  close: null,
}

export interface InboxUnread {
  /** Open alerts: the Inbox's own Open count across every target. */
  count: number
  /** How many of those are "act now". */
  actNow: number
  loading: boolean
}

/** Source for the sidebar Inbox badge: the same counts the Inbox tabs show. */
export function useInboxUnread(): InboxUnread {
  useInboxStream()
  const inbox = useInbox({ state: 'open' })
  const rules = useMuteRules()
  const counts = inboxCounts(inbox.data?.items ?? [], rules.data?.rules)
  return {
    count: counts.open,
    actNow: counts.actNow,
    loading: inbox.isPending,
  }
}
