import { ErrorState } from '@rs/ui-new/error-state'
import { PageNoticeSlot } from '@rs/ui-new/page-notices'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { TwoPane } from '@rs/ui-new/two-pane'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useOpenAddDatabase } from '@/components/configure/addDatabase'
import { useDbFilter } from '@/hooks/useDbFilter'
import { useTarget } from '@/hooks/useTarget'
import {
  useConfiguredDatabases,
  useInbox,
  useMuteRules,
} from '@/lib/monitor/hooks'
import type { InboxItem } from '@/lib/monitor/types'
import type { InboxSearch } from '@/routes/inbox'
import { ruleIsActive } from '../monitor-kit/severity'
import { FilterRow } from '../shared/filters/FilterRow'
import { InboxDetail, InboxDetailEmpty } from './InboxDetail'
import { InboxItemRow } from './InboxItemRow'
import {
  asideItems,
  closedGroups,
  inboxClock,
  inboxCounts,
  isMuted,
  openGroups,
  type QueueGroup,
} from './inboxModel'
import { MuteRulesList } from './MuteRulesList'
import { NotificationSettings } from './NotificationSettingsDialog'
import { useInboxActions } from './useInboxActions'
import { useInboxStream, useIncidentInbox } from './useInboxUnread'
import './inbox.css'

/** The queue panel's width; the detail takes the rest. */
const QUEUE_WIDTH = 440

type View = 'open' | 'closed'

/**
 * The queue's search: Open or Closed, the selected item, and the database filter (`?db=`, then
 * an older link's `?target=`, then the page's last choice).
 */
function useInboxSearch() {
  const search = useSearch({ from: '/inbox' }) as InboxSearch
  const navigate = useNavigate()
  const { db: remembered, setDb } = useDbFilter('inbox')
  const view: View = search.state === 'closed' ? 'closed' : 'open'
  const db = search.db ?? search.target ?? remembered
  const update = (next: Partial<InboxSearch>) => {
    const values = { ...search, ...next }
    const merged: InboxSearch = {}
    if (values.state === 'closed') merged.state = 'closed'
    if (values.db) merged.db = values.db
    if (values.item) merged.item = values.item
    if (values.monitorFixture) merged.monitorFixture = values.monitorFixture
    void navigate({
      to: '/inbox',
      search: merged,
      replace: true,
      resetScroll: false,
    })
  }
  // An older link's `?target=` becomes the filter, remembered like a choice.
  const legacy = search.db ? undefined : search.target
  useEffect(() => {
    if (legacy) setDb(legacy)
  }, [legacy, setDb])
  const setDatabase = (next: string | null) => {
    setDb(next)
    update({ db: next ?? undefined, item: undefined })
  }
  return { view, db, itemId: search.item, update, setDatabase }
}

/** Ids that arrived after the first load, flashed once when they slide in. */
function useFreshIds(items: InboxItem[] | undefined) {
  const known = useRef<Set<number> | null>(null)
  const [fresh, setFresh] = useState<ReadonlySet<number>>(new Set())
  useEffect(() => {
    if (!items) return
    if (known.current === null) {
      known.current = new Set(items.map((item) => item.id))
      return
    }
    const added = items.filter((item) => !known.current?.has(item.id))
    for (const item of items) known.current.add(item.id)
    if (added.length === 0) return
    setFresh(new Set(added.map((item) => item.id)))
    const timer = setTimeout(() => setFresh(new Set()), 1200)
    return () => clearTimeout(timer)
  }, [items])
  return fresh
}

function inEditable(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  const tag = element.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    element.isContentEditable
  )
}

/** j / k move the selection, e resolves the selected item, o opens its place. */
function useQueueKeys(handlers: {
  next: () => void
  prev: () => void
  resolve: () => void
  open: () => void
}) {
  const latest = useRef(handlers)
  latest.current = handlers
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (inEditable(event.target)) return
      if (
        event.target instanceof HTMLElement &&
        event.target.closest('[role="menu"], [role="dialog"]')
      )
        return
      const action = { j: 'next', k: 'prev', e: 'resolve', o: 'open' }[
        event.key
      ] as keyof typeof handlers | undefined
      if (!action) return
      event.preventDefault()
      latest.current[action]()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])
}

function Group({
  group,
  selectedId,
  now,
  fresh,
  onSelect,
}: {
  group: QueueGroup
  selectedId: number | undefined
  now: number
  fresh: ReadonlySet<number>
  onSelect: (item: InboxItem) => void
}) {
  return (
    <section aria-label={group.label}>
      <h2 className="flex gap-2 px-6 pt-4 pb-1.5 text-label-extra-small font-normal text-content-layout-3">
        {group.label}
        <span className="text-content-layout-2">{group.items.length}</span>
      </h2>
      {group.items.map((item) => (
        <InboxItemRow
          key={item.id}
          item={item}
          selected={item.id === selectedId}
          now={now}
          fresh={fresh.has(item.id)}
          onSelect={onSelect}
        />
      ))}
    </section>
  )
}

function QueueSkeleton() {
  return (
    <output
      className="block space-y-3 px-6 pt-4"
      aria-busy="true"
      aria-label="Loading items"
    >
      {[0, 1, 2].map((row) => (
        <Skeleton key={row} className="h-12 rounded-panel" />
      ))}
    </output>
  )
}

export function InboxPage() {
  const { view, db, itemId, update, setDatabase } = useInboxSearch()
  const target = db ?? undefined
  const { target: globalTarget } = useTarget()
  useInboxStream()
  const now = inboxClock()
  const everywhere = useInbox({ state: 'open' })
  const open = useIncidentInbox(target)
  const done = useInbox({ state: 'done', target })
  const rules = useMuteRules()
  const actions = useInboxActions()
  const fresh = useFreshIds(open.data?.items)
  const [showMuted, setShowMuted] = useState(false)
  const configured = useConfiguredDatabases()
  const openAddDatabase = useOpenAddDatabase()
  const noDatabase = !configured.isPending && configured.databases.length === 0

  const aside = useMemo(
    () =>
      asideItems(
        (open.data?.items ?? []).filter((item) => !actions.hidden.has(item.id)),
        rules.data?.rules
      ),
    [open.data, actions.hidden, rules.data]
  )
  const asideGroup: QueueGroup = {
    id: 'muted',
    label: 'Muted',
    items: [...aside.muted, ...aside.snoozed],
  }
  const openItems = useMemo(
    () =>
      (open.data?.items ?? []).filter(
        (item) =>
          !actions.hidden.has(item.id) &&
          !isMuted(item, rules.data?.rules) &&
          item.state !== 'snoozed'
      ),
    [open.data, actions.hidden, rules.data]
  )
  const closedItems = done.data?.items ?? []
  const groups = useMemo(
    () =>
      view === 'open' ? openGroups(openItems) : closedGroups(closedItems, now),
    [view, openItems, closedItems, now]
  )
  // Muted stays reachable while any rule is active, even when it silences nothing in view.
  const activeRules = (rules.data?.rules ?? []).filter((rule) =>
    ruleIsActive(rule)
  )
  const mutedCount = asideGroup.items.length
  const hasMuted = mutedCount > 0 || activeRules.length > 0
  const mutedView = showMuted && hasMuted
  const order = useMemo(
    () =>
      mutedView ? asideGroup.items : groups.flatMap((group) => group.items),
    [mutedView, asideGroup.items, groups]
  )
  const queryNames = useMemo(
    () =>
      new Map(
        (open.data?.items ?? []).flatMap((item) =>
          Object.entries(item.query_labels ?? {})
        )
      ),
    [open.data]
  )
  const selected = order.find((item) => item.id === itemId)

  // Open items per database, for the database menu.
  const perTarget = useMemo(() => {
    const byTarget = new Map<string, InboxItem[]>()
    for (const item of everywhere.data?.items ?? [])
      byTarget.set(item.target, [...(byTarget.get(item.target) ?? []), item])
    return Object.fromEntries(
      [...byTarget].map(([name, items]) => [
        name,
        inboxCounts(items, rules.data?.rules).open,
      ])
    )
  }, [everywhere.data, rules.data])
  const inView = inboxCounts(openItems, rules.data?.rules)
  const closedCount = closedGroups(closedItems, now).reduce(
    (sum, group) => sum + group.items.length,
    0
  )

  // A link to a closed item lands on Closed.
  const handledItem = useRef<number | null>(null)
  useEffect(() => {
    if (!itemId || handledItem.current === itemId || !open.data || !done.data)
      return
    handledItem.current = itemId
    const onOpen = openGroups(openItems).some((group) =>
      group.items.some((item) => item.id === itemId)
    )
    const onClosed = closedItems.some((item) => item.id === itemId)
    if (view === 'open' && !onOpen && onClosed) update({ state: 'closed' })
  })

  const select = (item: InboxItem) => {
    actions.markSeen([item])
    update({ item: item.id })
  }
  const step = (delta: number) => {
    if (order.length === 0) return
    const index = order.findIndex((item) => item.id === itemId)
    const next = order[Math.min(order.length - 1, Math.max(0, index + delta))]
    if (next && next.id !== itemId) select(next)
    const row = document.querySelector(`[data-inbox-item="${next?.id}"]`)
    if (row && 'scrollIntoView' in row) row.scrollIntoView({ block: 'nearest' })
  }
  useQueueKeys({
    next: () => step(itemId ? 1 : 0),
    prev: () => step(-1),
    resolve: () =>
      selected &&
      selected.state !== 'resolved' &&
      selected.state !== 'dismissed' &&
      actions.close([selected], { kind: 'resolve' }),
    open: () => selected && actions.openInDatabase(selected),
  })

  const queue = (
    <section aria-label="Queue" className="flex h-full min-h-0 flex-col">
      <div className="flex h-15 shrink-0 items-center gap-3 border-b border-border-layout-2 pr-5 pl-6 [-webkit-app-region:drag]">
        <Text as="h1" level="headline-5" className="text-content-layout-1">
          Inbox
        </Text>
        <div className="ml-auto flex items-center gap-1.5 [-webkit-app-region:no-drag]">
          <NotificationSettings
            target={target ?? globalTarget ?? Object.keys(perTarget)[0]}
          />
        </div>
      </div>
      <FilterRow
        className="pr-5 pl-6"
        scope={{
          label: 'Open or closed',
          value: view,
          onChange: (next) => {
            setShowMuted(false)
            update({ state: next, item: undefined })
          },
          options: [
            {
              value: 'open',
              label: 'Open',
              count: open.isPending ? null : inView.open,
            },
            {
              value: 'closed',
              label: 'Closed',
              count: done.isPending ? null : closedCount,
            },
          ],
        }}
        database={{ value: db, onChange: setDatabase, counts: perTarget }}
        kinds={
          hasMuted
            ? {
                label: 'Muted',
                value: mutedView ? 'muted' : null,
                options: [
                  { value: 'muted', label: 'Muted', count: mutedCount || null },
                ],
                onChange: (next) => {
                  setShowMuted(next === 'muted')
                  update({ item: undefined })
                },
              }
            : undefined
        }
      />
      <PageNoticeSlot className="px-6 pt-4" />
      <Scrollable rootClassName="min-h-0 flex-1" className="pt-1 pb-6">
        {open.error || done.error ? (
          <div className="px-6 pt-4">
            <ErrorState
              tone="quiet"
              errorClass="rdst-service"
              title="The Inbox could not load"
              message="RDST could not read the items. Nothing was changed."
              onRetry={() => {
                void open.refetch()
                void done.refetch()
              }}
            />
          </div>
        ) : open.isPending || done.isPending ? (
          <QueueSkeleton />
        ) : mutedView ? (
          <>
            {mutedCount > 0 && (
              <Group
                group={asideGroup}
                selectedId={itemId}
                now={now}
                fresh={fresh}
                onSelect={select}
              />
            )}
            <section aria-label="Mute rules" className="px-2">
              <h2 className="px-4 pt-4 pb-1.5 text-label-extra-small font-normal text-content-layout-3">
                Mute rules
              </h2>
              <MuteRulesList
                rules={rules.data?.rules}
                loading={rules.isPending}
                names={queryNames}
              />
            </section>
          </>
        ) : (
          groups.map((group) => (
            <Group
              key={group.id}
              group={group}
              selectedId={itemId}
              now={now}
              fresh={fresh}
              onSelect={select}
            />
          ))
        )}
        {view === 'closed' && !mutedView && !done.isPending && (
          <Text
            as="p"
            level="caption"
            className="px-6 pt-4 text-content-layout-3"
          >
            Closed items stay 7 days.
          </Text>
        )}
      </Scrollable>
    </section>
  )

  return (
    <TwoPane
      primaryWidth={QUEUE_WIDTH}
      primaryScrolls={false}
      primary={queue}
      secondary={
        selected ? (
          <InboxDetail
            key={selected.id}
            item={selected}
            now={now}
            actions={actions}
          />
        ) : (
          <InboxDetailEmpty
            actNow={inView.actNow}
            watch={inView.watch}
            closed={
              view === 'closed' && !mutedView ? closedItems.length : undefined
            }
            listEmpty={order.length === 0}
            onAddDatabase={noDatabase ? () => openAddDatabase() : undefined}
          />
        )
      }
    />
  )
}
