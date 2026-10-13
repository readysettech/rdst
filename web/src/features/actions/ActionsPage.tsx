// The global Actions page: one ranked plan across databases, grouped by category, priority or
// database, and the changes already applied, as the page's two tabs with the database picker
// beside the title; Group by and the kept indexes toggle sit over the table. A change opens in the side pane, as a query does
// on the Database page. Opening it for one database closes that database's Changes ready event.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Skeleton } from '@rs/ui-new/skeleton'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { TwoPane } from '@rs/ui-new/two-pane'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { DatabaseFilter } from '@/features/shared/filters/DatabaseFilter'
import { FilterChip } from '@/features/shared/filters/FilterRow'
import { useDbFilter } from '@/hooks/useDbFilter'
import {
  actionsNow,
  type GlobalActionsPage,
  useActionsOpened,
  useApplied,
  usePlan,
} from '@/lib/monitor/actions'
import { useConfiguredDatabases } from '@/lib/monitor/hooks'
import type { ActionsSearch } from '@/routes/actions'
import { AppliedView } from './AppliedView'
import {
  allSql,
  asGroup,
  asKind,
  GROUP_OPTIONS,
  type Group,
  inView,
  KIND_LABEL,
  pageMeta,
  planOrder,
  type Scope,
} from './actionsModel'
import { PlanPane } from './PlanPane'
import { keptCount, PlanView } from './PlanView'
import { useCopy } from './parts'

/** The Group by choice is remembered per browser, as the database filter is. */
const GROUP_KEY = 'rdst_actions_group'

function readGroup(): Group | null {
  try {
    return asGroup(window.localStorage.getItem(GROUP_KEY))
  } catch {
    return null
  }
}

function useActionsSearch() {
  const search = useSearch({ from: '/actions' }) as ActionsSearch
  const navigate = useNavigate()
  const update = (next: Partial<ActionsSearch>) =>
    void navigate({
      to: '/actions',
      search: ((current: ActionsSearch) => {
        const merged: Record<string, unknown> = { ...current, ...next }
        for (const key of Object.keys(merged))
          if (merged[key] === undefined) delete merged[key]
        return merged
      }) as never,
      replace: true,
      resetScroll: false,
    })
  // `?group=` when the URL names one, else the last choice, written to the URL on arrival.
  const [stored, setStored] = useState(readGroup)
  const fromUrl = asGroup(search.group)
  const arrived = useRef(false)
  useEffect(() => {
    if (arrived.current) return
    arrived.current = true
    if (!fromUrl && stored) update({ group: stored })
  })
  const setGroup = (group: Group) => {
    try {
      window.localStorage.setItem(GROUP_KEY, group)
    } catch {
      // Storage can be unavailable; the URL still carries the choice.
    }
    setStored(group)
    update({ group })
  }
  return {
    scope: (search.scope ?? 'plan') as Scope,
    kind: asKind(search.kind),
    group: fromUrl ?? stored ?? 'category',
    action: search.action ?? null,
    update,
    setGroup,
  }
}

/** Posts actions/opened once per visit for each database in view: the one the page is
 * narrowed to, or every monitored database in the all-databases view. */
function useOpenedOnce(
  db: string | null,
  databases: GlobalActionsPage['databases'] = []
) {
  const { mutate } = useActionsOpened()
  const posted = useRef(new Set<string>())
  const shown = db
    ? [db]
    : databases.filter((row) => row.monitored).map((row) => row.database)
  const names = shown.join('\n')
  useEffect(() => {
    for (const name of names ? names.split('\n') : []) {
      if (posted.current.has(name)) continue
      posted.current.add(name)
      mutate(name)
    }
  }, [names, mutate])
}

export function ActionsPage() {
  const { db: chosen, setDb } = useDbFilter('actions')
  // A remembered or linked database that's no longer configured falls back to all of them.
  const configured = useConfiguredDatabases()
  const gone =
    chosen !== null &&
    configured.settled === true &&
    !configured.databases.some((row) => row.name === chosen)
  const db = gone ? null : chosen
  useEffect(() => {
    if (gone) setDb(null)
  }, [gone, setDb])
  const { scope, kind, group, action, update, setGroup } = useActionsSearch()
  const [showKept, setShowKept] = useState(false)
  const plan = usePlan(db)
  const applied = useApplied(db)
  const now = actionsNow()
  const [copied, copy] = useCopy()
  const page = plan.data
  useOpenedOnce(db, page?.databases)
  const cards = useMemo(() => inView(page?.cards ?? [], kind), [page, kind])
  const appliedDays = useMemo(
    () =>
      (applied.data?.days ?? [])
        .map((day) => ({
          ...day,
          rows: day.rows.filter((row) => kind === null || row.kind === kind),
        }))
        .filter((day) => day.rows.length),
    [applied.data, kind]
  )
  const appliedCount = applied.data
    ? appliedDays.reduce((sum, day) => sum + day.rows.length, 0)
    : page?.applied_count
  // Every database's changes, whatever the filters.
  const toDo = (page?.databases ?? []).reduce((sum, row) => sum + row.to_do, 0)
  const across = (page?.databases ?? []).filter((row) => row.to_do > 0).length
  const narrowed = db !== null || kind !== null

  // Database groups only when the plan spans more than one.
  const groupOptions = GROUP_OPTIONS.filter(
    (option) =>
      option.value !== 'database' ||
      group === 'database' ||
      new Set(page?.cards.map((card) => card.database)).size > 1
  )
  const kept = page ? keptCount(page) : 0
  const onPlan = scope === 'plan'
  // A link that names a kind narrows the view; its chip clears it.
  const kindChip = kind ? (
    <FilterChip
      label={KIND_LABEL[kind]}
      on
      toggle={false}
      aria-label={`${KIND_LABEL[kind]}, clear`}
      onClick={() => update({ kind: undefined })}
    >
      <Icon name="close" label="" aria-hidden="true" size="small" />
    </FilterChip>
  ) : null
  const filters =
    onPlan || kindChip ? (
      <div
        className="flex flex-wrap items-center gap-x-3 gap-y-2"
        data-testid="actions-filters"
      >
        {onPlan ? (
          <>
            <span className="text-body-small text-content-layout-3">
              Group by
            </span>
            <SegmentedControl<Group>
              mode="radio"
              size="small"
              aria-label="Group by"
              value={group}
              onValueChange={setGroup}
              segments={groupOptions.map(({ value, label }) => ({
                value,
                label,
              }))}
            />
          </>
        ) : null}
        {kindChip}
        {onPlan && kept && (kind === null || kind === 'index') ? (
          <FilterChip
            label="Kept indexes"
            count={kept}
            on={showKept}
            onClick={() => setShowKept((value) => !value)}
            className="ml-auto"
          />
        ) : null}
      </div>
    ) : undefined

  const script = db ? allSql(cards) : ''

  // The opened change follows `?action=`, and is kept here too so it opens on the click.
  const [selectedId, setSelectedId] = useState(action)
  useEffect(() => setSelectedId(action), [action])
  const select = (id: number | null) => {
    setSelectedId(id)
    update({ action: id ?? undefined })
  }
  const ordered = useMemo(() => planOrder(cards, group), [cards, group])
  const opened =
    scope === 'plan'
      ? (ordered.find((card) => card.id === selectedId) ?? null)
      : null
  const openIndex = opened ? ordered.indexOf(opened) : -1
  const walk = (step: 1 | -1) => {
    if (!ordered.length) return
    const next =
      openIndex < 0
        ? step > 0
          ? 0
          : ordered.length - 1
        : Math.min(ordered.length - 1, Math.max(0, openIndex + step))
    select(ordered[next].id)
  }
  const paneOpen = opened !== null
  // Closing the pane hands focus back to its row, once the list shows again.
  const returnFocus = useRef<number | null>(null)
  const close = () => {
    returnFocus.current = opened?.id ?? null
    select(null)
  }
  useEffect(() => {
    if (paneOpen || returnFocus.current === null) return
    document.getElementById(`action-${returnFocus.current}-open`)?.focus()
    returnFocus.current = null
  }, [paneOpen])
  const keys = useRef({ close, walk, paneOpen, plan: scope === 'plan' })
  keys.current = { close, walk, paneOpen, plan: scope === 'plan' }
  // Escape closes the pane; j and k walk the plan.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        (el instanceof Element &&
          el.closest(
            'input, textarea, [contenteditable="true"], [role="dialog"], [role="menu"]'
          ))
      )
        return
      const { close, walk, paneOpen, plan } = keys.current
      if (event.key === 'Escape' && paneOpen) close()
      else if (plan && event.key === 'j') walk(1)
      else if (plan && event.key === 'k') walk(-1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const main = (
    <>
      <PageBar
        title="Actions"
        meta={page ? pageMeta(cards.length, toDo, across, narrowed) : undefined}
        actions={
          <>
            {scope === 'plan' && script ? (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label={copied ? 'Copied' : 'Copy all SQL'}
                onClick={() => copy(script)}
              />
            ) : null}
            <DatabaseFilter
              value={db}
              onChange={setDb}
              counts={page?.counts.by_database}
            />
          </>
        }
        tabs={
          <TabList aria-label="Plan or applied" className="border-b-0">
            <TabItemButton
              layoutPrefix="actions"
              label="Plan"
              count={page ? cards.length : null}
              active={scope === 'plan'}
              onClick={() => update({ scope: undefined })}
              className="h-10"
            />
            <TabItemButton
              layoutPrefix="actions"
              label="Applied"
              count={appliedCount ?? null}
              active={scope === 'applied'}
              onClick={() => update({ scope: 'applied' })}
              className="h-10"
            />
          </TabList>
        }
      />
      <PageContainer className="pt-9 pb-24" data-testid="actions-page">
        {scope === 'plan' ? (
          plan.isError && !page ? (
            <ErrorState
              tone="quiet"
              title="The plan didn't load"
              message="RDST couldn't read the changes to make. Your databases are unaffected."
              onRetry={() => void plan.refetch()}
            />
          ) : page ? (
            <PlanView
              page={page}
              cards={cards}
              kind={kind}
              group={group}
              showKept={showKept}
              now={now}
              selected={opened?.id ?? null}
              onSelect={select}
              filters={filters}
              onClearKind={() => update({ kind: undefined })}
            />
          ) : (
            <Loading />
          )
        ) : applied.isError && !applied.data ? (
          <ErrorState
            tone="quiet"
            title="Applied changes didn't load"
            message="RDST couldn't read the changes made so far."
            onRetry={() => void applied.refetch()}
          />
        ) : applied.data ? (
          <AppliedView
            page={applied.data}
            rows={appliedDays}
            now={now}
            filters={filters}
          />
        ) : (
          <Loading />
        )}
      </PageContainer>
    </>
  )

  return (
    <TwoPane
      open={paneOpen}
      className={cn(
        paneOpen &&
          'max-desktop:[&>div:first-child]:hidden max-desktop:[&>div:last-child]:ml-0 max-desktop:[&>div:last-child]:flex-1',
        // From 1280 px the pane gives up width before the plan drops under its minimum.
        paneOpen &&
          'desktop:[&>div:first-child]:min-w-[37.5rem] desktop:[&>div:last-child]:min-w-[25rem] desktop:[&>div:last-child]:shrink'
      )}
      primary={main}
      secondary={
        opened ? (
          <PlanPane
            key={opened.id}
            card={opened}
            now={now}
            onClose={close}
            onPrev={openIndex > 0 ? () => walk(-1) : undefined}
            onNext={openIndex < ordered.length - 1 ? () => walk(1) : undefined}
          />
        ) : null
      }
    />
  )
}

function Loading() {
  return (
    <div className="space-y-3" data-testid="actions-loading">
      <Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-4 w-1/3" />
      <div className="space-y-2 pt-8">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    </div>
  )
}
