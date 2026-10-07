import { cn } from '@rs/tailwind-base'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { WorkspaceLayout } from '@/components/workspace/WorkspaceLayout'
import { useTarget } from '@/hooks/useTarget'
import { getInbox, getQueries } from '@/lib/monitor/api'
import {
  useDeleteMuteRule,
  useInbox,
  useMarkAllSeen,
  useMuteRules,
} from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import type {
  InboxItem,
  InboxQuery,
  MuteRule,
  SeverityBand,
} from '@/lib/monitor/types'
import type { InboxSearch } from '@/routes/inbox'
import { plural } from '../monitor-kit/format'
import { MonitorHeartbeat } from '../monitor-kit/MonitorHeartbeat'
import {
  BAND_COUNT_CLASS,
  BAND_DOT_CLASS,
  BAND_META,
  ruleMatches,
  TONE_TEXT_CLASS,
} from '../monitor-kit/severity'
import { useMonitorPulse } from '../monitor-kit/useMonitorPulse'
import { InboxGroupRow } from './InboxGroupRow'
import { InboxItemRow } from './InboxItemRow'
import {
  closedLabel,
  type InboxGroup,
  type InboxSection,
  type InboxView,
  inboxCounts,
  itemPlace,
  type LiveBand,
  recentlyClosedItems,
  shapeOpen,
} from './inboxModel'
import { NotificationSettingsDialog } from './NotificationSettingsDialog'
import { type InboxActions, useInboxActions } from './useInboxActions'
import { incidentInboxKey, useInboxStream } from './useInboxUnread'
import './inbox.css'

/** Watch shows this many rows before 'Show N more'. */
const WATCH_PREVIEW = 5
/** Recovered shows this many rows before 'Show N more'. */
const RECOVERED_PAGE = 5

type SearchUpdate = {
  state?: InboxView
  muted?: boolean
  target?: string
  item?: number
}

function useInboxSearch() {
  const search = useSearch({ from: '/inbox' })
  const navigate = useNavigate()
  const tab: InboxView = search.state ?? 'open'
  const showMuted = Boolean(search.muted)
  // The Inbox covers every database; a database filter lives in the URL only, so a
  // new visit never hides another database's alerts.
  const target = search.target
  const itemId = search.item
  const update = (next: SearchUpdate) => {
    const values: SearchUpdate & { monitorFixture?: string } = {
      state: tab,
      muted: showMuted,
      target,
      item: itemId,
      monitorFixture: search.monitorFixture,
      ...next,
    }
    const merged: InboxSearch = {}
    if (values.state && values.state !== 'open') merged.state = values.state
    if (values.muted) merged.muted = true
    if (values.target) merged.target = values.target
    if (values.item) merged.item = values.item
    if (values.monitorFixture) merged.monitorFixture = values.monitorFixture
    void navigate({
      to: '/inbox',
      search: merged,
      replace: true,
      resetScroll: false,
    })
  }
  return { tab, showMuted, target, itemId, update }
}

/** Display names for every query on the targets in view, from the queries list cache. */
function useQueryNames(targets: string[]) {
  const results = useQueries({
    queries: targets.map((target) => ({
      queryKey: monitorKeys.queries(target, '24h'),
      queryFn: () => getQueries(target, '24h'),
      staleTime: 60_000,
    })),
  })
  const signature = results.map((result) => result.dataUpdatedAt).join(',')
  return useMemo(() => {
    const names = new Map<string, string>()
    for (const result of results)
      for (const query of result.data?.queries ?? [])
        names.set(query.key, query.display_name)
    return names
  }, [signature])
}

/** Ids that arrived after the first load, flashed for 1.2 s when they slide in. */
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

/**
 * Open counts per target from the list the sidebar badge already loads, so a filtered
 * view can say what the badge counts elsewhere.
 */
function useTargetCounts() {
  const everywhere = useInbox({ state: 'open' })
  const rules = useMuteRules()
  return useMemo(() => {
    const byTarget = new Map<string, InboxItem[]>()
    for (const item of everywhere.data?.items ?? [])
      byTarget.set(item.target, [...(byTarget.get(item.target) ?? []), item])
    const counts = new Map<string, number>()
    for (const [name, items] of byTarget)
      counts.set(name, inboxCounts(items, rules.data?.rules).open)
    return counts
  }, [everywhere.data, rules.data])
}

/**
 * The open list with incident windows: the one costly request, made once per target.
 * The stream refreshes it at most once a minute.
 */
function useOpenInbox(target: string | undefined) {
  const query: InboxQuery = {
    state: 'open',
    target,
    include_muted: true,
    incidents: true,
  }
  return useQuery({
    queryKey: incidentInboxKey(query),
    queryFn: ({ signal }) => getInbox(query, signal),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
}

/** Alerts closed by you or by RDST: Open lists the last 24 hours of them. */
function useDoneInbox(target: string | undefined, enabled: boolean) {
  const query: InboxQuery = { state: 'done', target }
  return useQuery({
    queryKey: monitorKeys.inbox(query),
    queryFn: ({ signal }) => getInbox(query, signal),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    enabled,
  })
}

export function InboxPage() {
  const { tab, showMuted, target, itemId, update } = useInboxSearch()
  const { target: globalTarget } = useTarget()
  useInboxStream()
  const open = useOpenInbox(target)
  const openIds = useMemo(
    () => new Set((open.data?.items ?? []).map((item) => item.id)),
    [open.data]
  )
  // Open lists what closed in the last 24 hours, so the closed list loads with it.
  const linkedClosed = Boolean(itemId && open.data && !openIds.has(itemId))
  const done = useDoneInbox(target, tab === 'open' || linkedClosed)
  const rules = useMuteRules(target)
  const actions = useInboxActions()
  const markAllSeen = useMarkAllSeen()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const [watchAll, setWatchAll] = useState(false)

  const openItems = useMemo(
    () =>
      (open.data?.items ?? []).filter((item) => !actions.hidden.has(item.id)),
    [open.data, actions.hidden]
  )
  const shaped = useMemo(
    () => shapeOpen(openItems, rules.data?.rules),
    [openItems, rules.data]
  )
  // Closing an alert hides it from the live sections at once; the recovered list shows it
  // as the server reports it, so an alert closed here shows there too.
  const recovered = useMemo(
    () =>
      recentlyClosedItems(
        open.data?.items ?? [],
        done.data?.items ?? [],
        Date.now() / 1000
      ),
    [open.data, done.data]
  )
  const allItems = [...(open.data?.items ?? []), ...(done.data?.items ?? [])]
  const targets = [...new Set(allItems.map((item) => item.target))].sort()
  const viewTargets = target ? [target] : targets
  const names = useQueryNames(viewTargets)
  const fresh = useFreshIds(open.data?.items)
  // Every item names its database, filtered or not.
  const showTarget = true
  const { counts } = shaped
  const targetCounts = useTargetCounts()
  const elsewhere = target
    ? [...targetCounts.entries()].filter(
        ([name, open]) => name !== target && open > 0
      )
    : []
  // New items, plus recoveries not yet seen: Mark all seen clears both.
  const unread = openItems.filter(
    (item) =>
      (item.state === 'new' || item.severity_band === 'recovered') &&
      !shaped.muted.includes(item)
  ).length

  // Deep link: open the tab that holds the item, expand its group, scroll to it.
  const handledItem = useRef<number | null>(null)
  useEffect(() => {
    if (!itemId || handledItem.current === itemId || !open.data) return
    const item = allItems.find((candidate) => candidate.id === itemId)
    if (!item) {
      if (!done.isPending || !linkedClosed) handledItem.current = itemId
      return
    }
    handledItem.current = itemId
    const place = itemPlace(item)
    const muted = shaped.muted.includes(item)
    const nextTab: InboxView =
      place === 'resolved' || (tab !== 'open' && tab !== place) ? 'open' : tab
    if (nextTab !== tab || (muted && !showMuted))
      update({ state: nextTab, muted: showMuted || muted })
    if (place !== 'resolved') {
      const section = shaped.sections[place]
      const holder = section.groups.find((group) =>
        group.items.some((candidate) => candidate.id === itemId)
      )
      if (holder && holder.items.length > 1)
        setExpanded((current) => new Set(current).add(holder.id))
      if (
        place === 'watch' &&
        holder &&
        section.groups.indexOf(holder) >= WATCH_PREVIEW
      )
        setWatchAll(true)
    }
    actions.markSeen([item])
    // After the group has opened (200 ms), so the row is where it will stay.
    setTimeout(
      () =>
        document
          .querySelector(`[data-inbox-item="${itemId}"]`)
          ?.scrollIntoView({ block: 'center', behavior: 'smooth' }),
      260
    )
  })

  const toggleGroup = (id: string) =>
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const loading = open.isPending
  const error = open.error
  const ready = Boolean(open.data)
  const bands: LiveBand[] =
    tab === 'act_now'
      ? ['act_now']
      : tab === 'watch'
        ? ['watch']
        : ['act_now', 'watch']
  const inView = (item: InboxItem) =>
    bands.includes(itemPlace(item) as LiveBand)
  const snoozed = shaped.snoozed.filter(inView)
  const muted = shaped.muted.filter(inView)
  const rowProps = {
    actions,
    names,
    showTarget,
    expanded,
    onToggle: toggleGroup,
    highlightId: itemId,
    fresh,
  }

  return (
    <WorkspaceLayout
      title="Inbox"
      description="What changed on your databases, most urgent first."
      icon="notification"
      panelId="inbox-panel"
      headerActions={
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <Button
            label="Notification settings"
            icon="settings"
            iconPosition="left"
            modifier="outline"
            onClick={() => setSettingsOpen(true)}
          />
        </div>
      }
    >
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <DatabaseFilter
            value={target}
            targets={[...new Set([...targets, ...targetCounts.keys()])].sort()}
            counts={targetCounts}
            onChange={(next) => update({ target: next, item: undefined })}
          />
          <div className="ml-auto flex items-center gap-3">
            {
              <label
                htmlFor="inbox-show-muted"
                className="flex cursor-pointer items-center gap-2 text-label-medium text-content-layout-2"
              >
                <BaseInputSwitch
                  id="inbox-show-muted"
                  name="show-muted"
                  checked={showMuted}
                  onCheckedChange={(checked) =>
                    update({ muted: checked, item: undefined })
                  }
                />
                Show muted{ready ? ` (${counts.muted})` : ''}
              </label>
            }
            {
              <Button
                label="Mark all seen"
                icon="tick-double"
                iconPosition="left"
                modifier="ghost"
                size="small"
                disabled={unread === 0}
                loading={markAllSeen.isPending}
                onClick={() => markAllSeen.mutate(target)}
              />
            }
          </div>
        </div>

        {elsewhere.length > 0 && (
          <div className="-mt-3 flex flex-wrap items-center gap-x-2 px-1">
            <Text level="body-small" className="text-content-layout-3">
              {elsewhere
                .map(([name, open]) => `${open} more open on ${name}`)
                .join(', ')}
              .
            </Text>
            <Button
              label="Show all databases"
              modifier="ghost"
              size="small"
              onClick={() => update({ target: undefined, item: undefined })}
            />
          </div>
        )}

        {error ? (
          <ErrorState
            errorClass="rdst-service"
            title="Couldn't load the inbox"
            message="The monitor didn't answer. Your alerts are safe; this only affects the view."
            onRetry={() => void open.refetch()}
          />
        ) : loading ? (
          <InboxSkeleton />
        ) : (
          <div className="space-y-8">
            {bands.map((band) => {
              const section = shaped.sections[band]
              if (section.count === 0)
                return band === 'act_now' ? (
                  <CalmBand key={band} />
                ) : tab === 'watch' ? (
                  <QuietState
                    key={band}
                    target={target}
                    targetCount={viewTargets.length}
                    title="Nothing to watch"
                  />
                ) : null
              return (
                <BandSection
                  key={band}
                  section={section}
                  capped={band === 'watch' && !watchAll}
                  onShowAll={() => setWatchAll((value) => !value)}
                  {...rowProps}
                />
              )
            })}
            {tab === 'open' && recovered.length > 0 && (
              <RecoveredSection items={recovered} {...rowProps} />
            )}
            {snoozed.length > 0 && (
              <SnoozedSection items={snoozed} {...rowProps} />
            )}
            {showMuted && (
              <MutedSection
                items={muted}
                rules={rules.data?.rules}
                onManage={() => setSettingsOpen(true)}
                {...rowProps}
              />
            )}
          </div>
        )}
      </div>
      <NotificationSettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        target={target ?? globalTarget ?? targets[0]}
        rules={rules.data?.rules}
        loading={rules.isPending}
        names={names}
      />
    </WorkspaceLayout>
  )
}

interface RowProps {
  actions: InboxActions
  names: Map<string, string>
  showTarget: boolean
  expanded: ReadonlySet<string>
  onToggle: (id: string) => void
  highlightId?: number
  fresh: ReadonlySet<number>
}

function GroupOrItem({ group, ...props }: RowProps & { group: InboxGroup }) {
  return group.items.length > 1 ? (
    <InboxGroupRow
      group={group}
      actions={props.actions}
      names={props.names}
      expanded={props.expanded.has(group.id)}
      onToggle={() => props.onToggle(group.id)}
      highlightId={props.highlightId}
      freshIds={props.fresh}
    />
  ) : (
    <InboxItemRow
      item={group.lead}
      actions={props.actions}
      names={props.names}
      showTarget={props.showTarget}
      highlighted={group.lead.id === props.highlightId}
      fresh={props.fresh.has(group.lead.id)}
    />
  )
}

function BandSection({
  section,
  capped,
  onShowAll,
  ...props
}: RowProps & {
  section: InboxSection
  /** Watch shows its first rows and 'Show N more'. */
  capped: boolean
  onShowAll: () => void
}) {
  const { band } = section
  const meta = BAND_META[band]
  const cappable = band === 'watch' && section.groups.length > WATCH_PREVIEW + 1
  const groups =
    cappable && capped ? section.groups.slice(0, WATCH_PREVIEW) : section.groups
  const hiddenAlerts = section.groups
    .slice(WATCH_PREVIEW)
    .reduce((sum, group) => sum + group.items.length, 0)
  return (
    <section className="space-y-3" aria-label={meta.label}>
      <SectionHeader
        title={meta.label}
        count={section.count}
        band={band}
        hint={bandHint(band, section.unread)}
        toneClass={TONE_TEXT_CLASS[meta.tone]}
      />
      <SectionCard band={band}>
        <AnimatePresence initial={false}>
          {groups.map((group) => (
            <RowMotion key={group.id} id={group.id}>
              <GroupOrItem group={group} {...props} />
            </RowMotion>
          ))}
        </AnimatePresence>
        {cappable && (
          <MoreRow hidden={hiddenAlerts} open={!capped} onToggle={onShowAll} />
        )}
      </SectionCard>
    </section>
  )
}

function bandHint(_band: LiveBand, unread: number) {
  return unread > 0 ? `${unread} unread` : undefined
}

function SnoozedSection({
  items,
  ...props
}: RowProps & { items: InboxItem[] }) {
  const [show, setShow] = useState(false)
  return (
    <section className="space-y-3" aria-label="Snoozed">
      <div className="flex items-center justify-between gap-3 px-1">
        <Text level="body-small" className="text-content-layout-3">
          {plural(items.length, 'snoozed alert')} come back when the snooze
          ends.
        </Text>
        <Button
          label={show ? 'Hide snoozed' : 'Show snoozed'}
          modifier="ghost"
          size="small"
          onClick={() => setShow((value) => !value)}
        />
      </div>
      {show && (
        <SectionCard>
          {items.map((item) => (
            <InboxItemRow
              key={item.id}
              item={item}
              actions={props.actions}
              names={props.names}
              density="compact"
              showTarget={props.showTarget}
              trailing={
                <Button
                  label="Unsnooze"
                  modifier="ghost"
                  size="small"
                  onClick={() => props.actions.reopen([item])}
                />
              }
            />
          ))}
        </SectionCard>
      )}
    </section>
  )
}

/** Muted alerts in view, shown by the filter: recorded, never notified. */
function MutedSection({
  items,
  rules,
  onManage,
  ...props
}: RowProps & {
  items: InboxItem[]
  rules: MuteRule[] | undefined
  onManage: () => void
}) {
  const remove = useDeleteMuteRule()
  const ruleFor = (item: InboxItem) =>
    rules?.find((rule) => rule.id === item.muted_by) ??
    rules?.find((rule) => ruleMatches(rule, item))
  return (
    <section className="space-y-3" aria-label="Muted">
      <SectionHeader
        title="Muted"
        count={items.length}
        hint="Recorded but never notified"
        action={
          <Button
            label="Mute rules"
            icon="settings"
            iconPosition="left"
            modifier="ghost"
            size="small"
            onClick={onManage}
          />
        }
      />
      {items.length === 0 ? (
        <Text level="body-small" className="block px-1 text-content-layout-3">
          No open alerts here are muted.
        </Text>
      ) : (
        <SectionCard>
          {items.map((item) => {
            const rule = ruleFor(item)
            return (
              <InboxItemRow
                key={item.id}
                item={item}
                actions={props.actions}
                names={props.names}
                density="compact"
                showTarget={props.showTarget}
                highlighted={item.id === props.highlightId}
                quiet
                trailing={
                  rule ? (
                    <Button
                      label="Unmute"
                      modifier="ghost"
                      size="small"
                      loading={remove.isPending && remove.variables === rule.id}
                      onClick={() => remove.mutate(rule.id)}
                    />
                  ) : null
                }
              />
            )
          })}
        </SectionCard>
      )}
    </section>
  )
}

/** Recoveries and closed alerts from the last 24 hours, in green, each reopenable. */
function RecoveredSection({
  items,
  actions,
  names,
  showTarget,
  highlightId,
}: RowProps & { items: InboxItem[] }) {
  const [showAll, setShowAll] = useState(false)
  const highlightIndex = items.findIndex((item) => item.id === highlightId)
  const visible = showAll
    ? items
    : items.slice(0, Math.max(RECOVERED_PAGE, highlightIndex + 1))
  const left = items.length - visible.length
  return (
    <section className="space-y-3" aria-label="Recovered in the last 24 hours">
      <SectionHeader
        title="Recovered"
        count={items.length}
        band="recovered"
        hint="Last 24 hours"
        toneClass={TONE_TEXT_CLASS.positive}
      />
      <SectionCard band="recovered">
        {visible.map((item) => (
          <InboxItemRow
            key={item.id}
            item={item}
            actions={actions}
            names={names}
            density="compact"
            showTarget={showTarget}
            highlighted={item.id === highlightId}
            trailing={
              item.state === 'resolved' || item.state === 'dismissed' ? (
                <Button
                  label="Reopen"
                  modifier="ghost"
                  size="small"
                  aria-label={`Reopen ${item.title} (${closedLabel(item)})`}
                  onClick={() => actions.reopen([item])}
                />
              ) : undefined
            }
          />
        ))}
        {items.length > RECOVERED_PAGE && (left > 0 || showAll) && (
          <div className="flex items-center gap-1 px-2 py-1.5">
            <Button
              label={showAll ? 'Show fewer' : `Show ${left} more`}
              icon={showAll ? 'chevron-up' : 'chevron-down'}
              iconPosition="right"
              modifier="ghost"
              size="small"
              onClick={() => setShowAll((value) => !value)}
            />
          </div>
        )}
      </SectionCard>
    </section>
  )
}

/**
 * Which databases the Inbox shows, as chips with open counts. All databases is the
 * default: the Inbox is the one place that covers every database.
 */
function DatabaseFilter({
  value,
  targets,
  counts,
  onChange,
}: {
  value: string | undefined
  targets: string[]
  /** Open alerts per database, shown on each chip. */
  counts: Map<string, number>
  onChange: (target: string | undefined) => void
}) {
  const total = [...counts.values()].reduce((sum, count) => sum + count, 0)
  const options =
    value && !targets.includes(value) ? [...targets, value] : targets
  if (!options.length) return null
  const chip = (
    key: string,
    label: string,
    count: number | undefined,
    active: boolean,
    pick: () => void
  ) => (
    <Pressable
      key={key}
      type="button"
      aria-pressed={active}
      onClick={pick}
      className={cn(
        'inline-flex h-8 items-center gap-1.5 rounded-pill border px-3 text-label-medium transition-colors duration-fast ease-base focus-visible:shadow-focus focus-visible:outline-none',
        active
          ? 'border-border-primary-soft bg-surface-primary-soft text-content-primary-soft'
          : 'border-border-layout-1 bg-surface-layout-1 text-content-layout-2 hover:border-border-layout-2 hover:text-content-layout-1'
      )}
    >
      {key !== 'all' && (
        <Icon
          name="database"
          label=""
          aria-hidden="true"
          size="small"
          className="h-3.5 w-3.5"
        />
      )}
      {label}
      <span className="tabular-nums text-content-layout-3">{count ?? 0}</span>
    </Pressable>
  )
  return (
    <fieldset
      className="flex flex-wrap items-center gap-2"
      aria-label="Databases"
      data-testid="inbox-database-filter"
    >
      {chip('all', 'All databases', total, !value, () => onChange(undefined))}
      {options.map((name) =>
        chip(name, name, counts.get(name), value === name, () => onChange(name))
      )}
    </fieldset>
  )
}

function InboxSkeleton() {
  return (
    <output
      className="block space-y-6"
      aria-busy="true"
      aria-label="Loading alerts"
    >
      {[2, 3].map((rows, section) => (
        <div key={section} className="space-y-3">
          <Skeleton className="h-5 w-32 rounded-control" />
          <div className="space-y-px overflow-hidden rounded-card border border-border-layout-1">
            {Array.from({ length: rows }, (_, row) => (
              <Skeleton key={row} className="h-20 rounded-none" />
            ))}
          </div>
        </div>
      ))}
    </output>
  )
}

/** Rows leave by collapsing (200 ms ease-in) and arrive by sliding in. */
function RowMotion({ id, children }: { id: string; children: ReactNode }) {
  const reduced = useReducedMotion()
  return (
    <m.div
      key={id}
      layout={reduced ? false : 'position'}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0, height: 'auto' }}
      exit={
        reduced
          ? { opacity: 0, transition: { duration: 0.15 } }
          : {
              opacity: 0,
              height: 0,
              transition: { duration: 0.2, ease: 'easeIn' },
            }
      }
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="overflow-hidden"
    >
      {children}
    </m.div>
  )
}

/** A live section's card carries its band color as a full border that follows the radius. */
const BAND_BORDER_CLASS: Record<SeverityBand, string> = {
  act_now: 'border-border-negative-soft',
  watch: 'border-border-warning-soft',
  fyi: 'border-border-layout-1',
  recovered: 'border-border-positive-soft',
}

function SectionCard({
  band,
  children,
}: {
  band?: SeverityBand
  children: ReactNode
}) {
  return (
    <div
      data-band={band}
      className={cn(
        'overflow-hidden rounded-card border bg-surface-layout-1 shadow-elevation-1 [&>*+*]:border-t [&>*+*]:border-border-layout-1',
        band ? BAND_BORDER_CLASS[band] : 'border-border-layout-1'
      )}
    >
      {children}
    </div>
  )
}

function SectionHeader({
  title,
  count,
  hint,
  toneClass,
  band,
  action,
}: {
  title: string
  count?: number
  hint?: string
  toneClass?: string
  /** Colour-codes the header: a dot and a tinted count. */
  band?: SeverityBand
  /** A control at the end of the header. */
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 px-1">
      <div className="flex items-center gap-2 whitespace-nowrap">
        {band && (
          <span
            aria-hidden
            className={cn(
              'h-2 w-2 shrink-0 rounded-pill',
              BAND_DOT_CLASS[band]
            )}
          />
        )}
        <Text
          as="h2"
          level="subtitle-1"
          className={cn('text-content-layout-1', toneClass)}
        >
          {title}
        </Text>
        {count !== undefined && (
          <Text
            aria-label={`${count} ${count === 1 ? 'alert' : 'alerts'}`}
            level="label-medium"
            className={cn(
              'tabular-nums',
              band
                ? [
                    'rounded-pill px-2 py-0.5 leading-none',
                    BAND_COUNT_CLASS[band],
                  ]
                : 'text-content-layout-3'
            )}
          >
            {count}
          </Text>
        )}
      </div>
      <div className="flex items-center gap-2">
        {hint && (
          <Text level="caption" className="text-content-layout-3">
            {hint}
          </Text>
        )}
        {action}
      </div>
    </div>
  )
}

/** The last row of a capped list: 'Show 12 more' or 'Show fewer'. */
function MoreRow({
  hidden,
  open,
  onToggle,
}: {
  hidden: number
  open: boolean
  onToggle: () => void
}) {
  return (
    <Pressable
      onClick={onToggle}
      aria-expanded={open}
      className="flex w-full items-center justify-center gap-1.5 px-4 py-2.5 text-label-medium text-content-layout-2 transition-colors duration-fast hover:bg-surface-raised hover:text-content-layout-1"
    >
      {open ? 'Show fewer' : `Show ${hidden} more`}
      <Icon
        name="chevron-down"
        label=""
        aria-hidden="true"
        className={cn(
          'h-3.5 w-3.5 transition-transform duration-fast',
          open && 'rotate-180'
        )}
      />
    </Pressable>
  )
}

function CalmBand() {
  return (
    <section className="space-y-3" aria-label="Urgent">
      <SectionHeader title="Urgent" count={0} band="act_now" />
      <div className="flex items-center gap-3 rounded-card border border-dashed border-border-layout-1 px-5 py-4">
        <Text level="body-small" className="text-content-layout-2">
          Nothing is hurting your databases right now.
        </Text>
      </div>
    </section>
  )
}

function QuietState({
  target,
  targetCount,
  title = 'Nothing needs you right now',
}: {
  target?: string
  targetCount: number
  title?: string
}) {
  const { target: globalTarget } = useTarget()
  const pulse = useMonitorPulse(target ?? globalTarget ?? null)
  const watching = target
    ? `RDST is watching ${target}.`
    : `RDST is watching ${plural(Math.max(targetCount, 1), 'target')}.`
  return (
    <div className="rounded-card border border-border-layout-1 bg-surface-layout-1">
      <EmptyState
        icon="tick"
        title={title}
        body={`${watching} New alerts appear here the moment they happen.`}
      />
      <div className="flex justify-center pb-8">
        <MonitorHeartbeat pulse={pulse} compact />
      </div>
    </div>
  )
}
