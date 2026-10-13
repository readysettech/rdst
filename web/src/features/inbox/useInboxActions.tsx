import { Button } from '@rs/ui-new/button'
import { useToast } from '@rs/ui-new/use-toast'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { trackEvent } from '@/lib/analytics'
import { actionsOpened } from '@/lib/monitor/api'
import { useInboxAction } from '@/lib/monitor/hooks'
import type { InboxItem } from '@/lib/monitor/types'
import { selectGlobalTarget } from '../database/targetSelection'
import { analysisAgeBucket } from '../queries/results/storedAnalysis'
import { itemLink, itemRoute, type LinkRoute } from './inboxLinks'
import { kindOf } from './inboxModel'

/** The Inbox's own verbs: an event is handled (Resolve) or not; Reopen undoes it. */
export type CloseAction = { kind: 'resolve' }

function count(items: InboxItem[]) {
  return items.length === 1 ? '' : ` ${items.length} alerts`
}

/**
 * Resolve with an optimistic exit and a 5 s undo toast. The page filters pending `hidden`
 * ids out so rows leave at once; undo reopens them.
 */
export function useInboxActions() {
  const mutation = useInboxAction()
  const { toast } = useToast()
  const navigate = useNavigate()
  const [hidden, setHidden] = useState<ReadonlySet<number>>(new Set())

  const setVisible = (ids: number[], visible: boolean) =>
    setHidden((current) => {
      const next = new Set(current)
      for (const id of ids) {
        if (visible) next.delete(id)
        else next.add(id)
      }
      return next
    })

  const reopen = (items: InboxItem[]) => {
    const ids = items.map((item) => item.id)
    setVisible(ids, true)
    for (const id of ids) mutation.mutate({ id, action: 'reopen' })
  }

  const close = (
    items: InboxItem[],
    action: CloseAction = { kind: 'resolve' }
  ) => {
    if (items.length === 0) return
    const ids = items.map((item) => item.id)
    setVisible(ids, false)
    Promise.all(
      ids.map((id) =>
        mutation
          .mutateAsync({ id, action: action.kind })
          .finally(() => setVisible([id], true))
      )
    )
      .then(() => {
        const handle = toast({
          title: `Resolved${count(items)}`,
          description: items.length === 1 ? items[0].title : undefined,
          duration: 5000,
          action: (
            <Button
              label="Undo"
              size="small"
              modifier="outline"
              onClick={() => {
                reopen(items)
                handle.dismiss()
              }}
            />
          ),
        })
      })
      .catch((error: unknown) => {
        toast({
          title: 'Could not update the inbox',
          description: error instanceof Error ? error.message : undefined,
          variant: 'negative',
        })
      })
  }

  const markSeen = (items: InboxItem[]) => {
    for (const item of items)
      if (item.state === 'new') mutation.mutate({ id: item.id, action: 'seen' })
  }

  /**
   * Opens the place the item points at (a query row and tab, the report, a setting) and
   * marks it seen. `queryKey` opens one query of a many-queries episode instead.
   */
  const opened = (item: InboxItem, via: 'database' | 'route') =>
    trackEvent('inbox_item_opened', {
      kind: item.type,
      band: item.severity_band,
      state: item.state,
      age_bucket: analysisAgeBucket(
        new Date(item.created_at * 1000).toISOString()
      ),
      via,
    })

  /**
   * Opens Actions on the item's database. Opening it is what closes a Changes ready event,
   * so the page tells the backend as it goes.
   */
  const openActions = (item: InboxItem) => {
    opened(item, 'route')
    markSeen([item])
    // The Inbox refetches when it opens again, and the stream carries the close.
    void actionsOpened(item.target).catch(() => undefined)
    void navigate({ to: '/actions', search: { db: item.target } as never })
  }

  const openInDatabase = (item: InboxItem, queryKey?: string) => {
    if (kindOf(item) === 'changes_ready') return openActions(item)
    opened(item, 'database')
    markSeen([item])
    const link = itemLink(item)
    const route = itemRoute(item, queryKey)
    if (link.kind === 'report') selectGlobalTarget(link.target)
    void navigate({ to: route.to as never, search: route.search as never })
  }

  /** Opens a route built for the item (a time frame, a query tab) and marks it seen. */
  const openRoute = (item: InboxItem, route: LinkRoute) => {
    opened(item, 'route')
    markSeen([item])
    void navigate({ to: route.to as never, search: route.search as never })
  }

  /** Acknowledges recoveries: they stay on Resolved without the unread mark. */
  const acknowledge = (items: InboxItem[]) => {
    const ids = items.map((item) => item.id)
    setVisible(ids, false)
    for (const id of ids) {
      void mutation
        .mutateAsync({ id, action: 'seen' })
        .finally(() => setVisible([id], true))
        .catch((error: unknown) => {
          toast({
            title: 'Could not update the inbox',
            description: error instanceof Error ? error.message : undefined,
            variant: 'negative',
          })
        })
    }
  }

  return {
    hidden,
    close,
    reopen,
    markSeen,
    openInDatabase,
    openActions,
    openRoute,
    acknowledge,
  }
}

export type InboxActions = ReturnType<typeof useInboxActions>
