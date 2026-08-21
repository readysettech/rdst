import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import {
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@rs/ui-new/drawer'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { useNavigate } from '@tanstack/react-router'
import { lazy, type ReactNode, Suspense, useId, useState } from 'react'
import { QueryStarButton } from '../../../components/QueryStarButton'
import { useAnalysisRunForQuery } from '../../../lib/analysisRuns'
import type { QueryRegistryEntry } from '../../../lib/api'
import { formatMeta, shortHash } from '../../../lib/formatters'
import { queryDisplayName } from '../../../lib/queryIdentity'
import type { QueryLibrarySearch } from '../library/queryLibraryState'
import { ResultsBody } from '../results/ResultsBody'
import type { ResultsSearch } from '../results/types'
import {
  type ResultsShell,
  useResultsController,
} from '../results/useResultsController'
import { useLatestAnalysisQuery } from '../saved/useLatestAnalysis'
import {
  type AnalyzeDrawerLink,
  type AnalyzeDrawerTab,
  analyzeDrawerTab,
  analyzeReturnSearch,
  drawerResultsSearch,
} from './analyzeDrawerState'
import { useAnalyzeDrawerEntry } from './useAnalyzeDrawerEntry'

export interface AnalyzeDrawerContentProps {
  link: AnalyzeDrawerLink
  /** Rows the library already loaded, so a row's own link needs no request. */
  loaded: QueryRegistryEntry[]
  librarySearch: QueryLibrarySearch
  target?: string | null
  onClose: () => void
  onOpenLink: (link: AnalyzeDrawerLink, options?: { replace?: boolean }) => void
  onToggleStar: (hash: string, starred: boolean) => void
}

// Recall, analysis and conversation are separate reads with separate weight,
// so each tab carries its own chunk: opening an analysis never pays for the
// Overview, and neither pays for the chat stack until it is asked for.
const AnalyzeDrawerOverview = lazy(() =>
  import('./AnalyzeDrawerOverview').then((module) => ({
    default: module.AnalyzeDrawerOverview,
  }))
)

const AnalyzeDrawerFollowUp = lazy(() =>
  import('./AnalyzeDrawerFollowUp').then((module) => ({
    default: module.AnalyzeDrawerFollowUp,
  }))
)

const DRAWER_TITLE = 'Query'
const DRAWER_DESCRIPTION =
  'Measured performance, practical improvements, and Readyset fit.'

/** What the Follow-up tab is waiting for before it can be opened. */
const FOLLOW_UP_HINT = 'Run an analysis first'

const DRAWER_TABS: ReadonlyArray<{
  value: AnalyzeDrawerTab
  label: string
  icon: 'folder-file' | 'speedometer' | 'message-multiple'
}> = [
  { value: 'overview', label: 'Overview', icon: 'folder-file' },
  { value: 'analyze', label: 'Analyze', icon: 'speedometer' },
  { value: 'follow-up', label: 'Follow-up', icon: 'message-multiple' },
]

/** The identity line: shown once, in the header, never again below it. */
function identityLine(entry: QueryRegistryEntry) {
  return formatMeta([`hash ${shortHash(entry.hash)}`, entry.target || null])
}

function DrawerShell({
  title = DRAWER_TITLE,
  description = DRAWER_DESCRIPTION,
  action,
  tab,
  onOpenTab,
  unavailableTabs,
  children,
}: {
  title?: string
  /** What is being shown, once the drawer knows which query that is. */
  description?: string
  action?: ReactNode
  /** Absent until the drawer has a query, since there is nothing to tab over. */
  tab?: AnalyzeDrawerTab
  onOpenTab?: (tab: AnalyzeDrawerTab) => void
  /** Panes that cannot be opened yet, each mapped to the reason why. */
  unavailableTabs?: Partial<Record<AnalyzeDrawerTab, string>>
  children: ReactNode
}) {
  const id = useId()
  const panelId = `${id}-panel`
  const tabId = (value: AnalyzeDrawerTab) => `${id}-tab-${value}`
  // The three panel attributes stand or fall together: without a tab row there
  // is no tablist for a tabpanel to belong to.
  const panelProps = tab
    ? { id: panelId, role: 'tabpanel', 'aria-labelledby': tabId(tab) }
    : {}

  return (
    <DrawerContent
      // The analysis is a dense read — plan tables, rewrites, index findings.
      // It gets the design system's widest right drawer, the same one the
      // target-configuration drawer uses.
      size="XXLarge"
      direction="right"
      className="p-0"
      hideCloseButton
      data-testid="analyze-drawer"
    >
      <VStack
        className={cn(
          'items-stretch gap-1 p-5',
          tab ? 'pb-0' : 'border-b border-border-layout-1'
        )}
      >
        <HStack className="w-full items-center justify-between gap-2">
          <DrawerTitle className="truncate">{title}</DrawerTitle>
          <HStack className="shrink-0 items-center gap-2">
            {action}
            <DrawerClose asChild>
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                icon="close"
                iconPosition="icon"
                label="Close"
              />
            </DrawerClose>
          </HStack>
        </HStack>
        <DrawerDescription className="truncate">
          {description}
        </DrawerDescription>
        {tab && onOpenTab ? (
          <TabList aria-label="Query views" className="mt-2">
            {DRAWER_TABS.map((item) => {
              const unavailable = unavailableTabs?.[item.value]
              return (
                <TabItemButton
                  key={item.value}
                  id={tabId(item.value)}
                  aria-controls={panelId}
                  layoutPrefix={`${id}-drawer-tabs`}
                  label={item.label}
                  leftIcon={item.icon}
                  active={tab === item.value}
                  disabled={Boolean(unavailable)}
                  hint={unavailable}
                  onClick={() => onOpenTab(item.value)}
                />
              )
            })}
          </TabList>
        ) : null}
      </VStack>
      <Scrollable className="flex-1">
        <div {...panelProps} className="space-y-6 p-5">
          {children}
        </div>
      </Scrollable>
    </DrawerContent>
  )
}

/**
 * A query, packaged as a drawer over the Query Library (RDST UX plan, A5).
 *
 * Overview is recall — what this query is and what has already been learned
 * about it. Analyze is packaging and nothing else: the same
 * `useResultsController` and the same `ResultsBody` as `/results`, given a
 * shell that rewrites `?analyze=` instead of navigating. A bare `?analyze=`
 * link opens Analyze, exactly as it did before the tabs existed.
 */
export function AnalyzeDrawerContent({
  link,
  loaded,
  librarySearch,
  target,
  onClose,
  onOpenLink,
  onToggleStar,
}: AnalyzeDrawerContentProps) {
  const navigate = useNavigate()
  const [sqlOverride, setSqlOverride] = useState<string | undefined>(undefined)
  const { entry, isLoading, error } = useAnalyzeDrawerEntry({
    hash: link.hash,
    loaded,
    target,
  })

  // Opening a query rather than a specific run resumes the analysis it already
  // has. Only a deliberate re-run measures a query that was analyzed before.
  const wantsLatest = !link.analysisId && !link.rerun
  const latest = useLatestAnalysisQuery(link.hash, wantsLatest)
  const analysisId =
    link.analysisId ?? (wantsLatest ? latest.summary?.analysis_id : undefined)

  if (isLoading || (wantsLatest && !latest.isResolved)) {
    return (
      <DrawerShell>
        <VStack className="items-stretch gap-3" aria-hidden="true">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </VStack>
      </DrawerShell>
    )
  }

  if (!entry) {
    return (
      <DrawerShell>
        <ErrorState
          errorClass="valid-negative"
          title="This query is no longer in the library"
          message="The link points at a query this install no longer has."
          trustworthy="Your other saved queries are untouched."
          detail={error ?? undefined}
          action={{ label: 'Back to queries', onClick: onClose }}
        />
      </DrawerShell>
    )
  }

  return (
    <LoadedAnalyzeDrawer
      entry={entry}
      link={link}
      analysisId={analysisId}
      librarySearch={librarySearch}
      target={target}
      sqlOverride={sqlOverride}
      onSqlOverride={setSqlOverride}
      onClose={onClose}
      onOpenLink={onOpenLink}
      onToggleStar={onToggleStar}
      onOpenFullView={(search) => void navigate({ to: '/results', search })}
    />
  )
}

function LoadedAnalyzeDrawer({
  entry,
  link,
  analysisId,
  librarySearch,
  target,
  sqlOverride,
  onSqlOverride,
  onClose,
  onOpenLink,
  onToggleStar,
  onOpenFullView,
}: {
  entry: QueryRegistryEntry
  link: AnalyzeDrawerLink
  analysisId?: string
  librarySearch: QueryLibrarySearch
  target?: string | null
  sqlOverride?: string
  onSqlOverride: (sql: string) => void
  onClose: () => void
  onOpenLink: (link: AnalyzeDrawerLink, options?: { replace?: boolean }) => void
  onToggleStar: (hash: string, starred: boolean) => void
  onOpenFullView: (search: ResultsSearch) => void
}) {
  const tab = analyzeDrawerTab(link)
  const search = drawerResultsSearch({
    entry,
    hash: link.hash,
    analysisId,
    returnSearch: analyzeReturnSearch(librarySearch),
    sqlOverride,
    target,
  })

  // A conversation is about an analysis, so the tab waits for one: a stored
  // record the link resolved to, or a run this session finished. Reading the
  // run store starts nothing — it only reports what is already there.
  const run = useAnalysisRunForQuery({
    hash: link.hash,
    sql: search.query,
    target: search.target,
  })
  const hasAnalysis =
    Boolean(analysisId) ||
    (run?.state === 'complete' && Boolean(run.results?.query_hash))

  return (
    <DrawerShell
      title={queryDisplayName(entry)}
      description={identityLine(entry)}
      tab={tab}
      onOpenTab={(next) => onOpenLink({ ...link, tab: next })}
      unavailableTabs={
        hasAnalysis ? undefined : { 'follow-up': FOLLOW_UP_HINT }
      }
      action={
        <>
          <QueryStarButton
            starred={entry.starred === true}
            onToggle={(next) => onToggleStar(entry.hash, next)}
          />
          {/* `/results` has no overview mode, so the escape hatch belongs to
              the tabs that have an equivalent there — the analysis, and the
              conversation the full view opens over it. */}
          {tab === 'overview' ? null : (
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              icon="arrow-up-right"
              iconPosition="left"
              label="Open full view"
              onClick={() => onOpenFullView(search)}
            />
          )}
        </>
      }
    >
      {tab === 'overview' ? (
        <Suspense fallback={<Skeleton className="h-40 w-full" />}>
          <AnalyzeDrawerOverview
            entry={entry}
            currentAnalysisId={analysisId}
            target={target}
            onOpenAnalysis={(id) =>
              onOpenLink({ hash: link.hash, analysisId: id, tab: 'analyze' })
            }
            onAnalyzeAgain={() =>
              onOpenLink({ hash: link.hash, rerun: true, tab: 'analyze' })
            }
          />
        </Suspense>
      ) : tab === 'follow-up' && !hasAnalysis ? (
        // A hand-written link can ask for a conversation the query has not
        // earned yet. Say so rather than mounting a chat with no subject —
        // and never let asking for one start a measurement.
        <EmptyState
          icon="message-multiple"
          title={FOLLOW_UP_HINT}
          body="Follow-up questions are answered from a measured analysis, so this query needs one before it has anything to discuss."
          action={{
            label: 'Open Analyze',
            icon: 'speedometer',
            onClick: () => onOpenLink({ ...link, tab: 'analyze' }),
          }}
        />
      ) : (
        // The analysis controller only mounts on the tabs that show an
        // analysis: reading the Overview must never start a measurement.
        <AnalyzeDrawerResults
          entry={entry}
          hash={link.hash}
          tab={tab}
          search={search}
          onSqlOverride={onSqlOverride}
          onOpenLink={onOpenLink}
          onClose={onClose}
        />
      )}
    </DrawerShell>
  )
}

/**
 * The analysis, and the conversation about it. Both are the same read, so both
 * are the same controller: `/results` decides what a stored id or a live run
 * means, and the drawer inherits that decision instead of repeating it.
 */
function AnalyzeDrawerResults({
  entry,
  hash,
  tab,
  search,
  onSqlOverride,
  onOpenLink,
  onClose,
}: {
  entry: QueryRegistryEntry
  hash: string
  tab: AnalyzeDrawerTab
  search: ResultsSearch
  onSqlOverride: (sql: string) => void
  onOpenLink: (link: AnalyzeDrawerLink, options?: { replace?: boolean }) => void
  onClose: () => void
}) {
  const shell: ResultsShell = {
    // Every move inside the drawer is a URL change. Landing without a stored
    // id means the user asked for a fresh measurement — re-run or substituted
    // parameters — so the link says so rather than resuming the stored record.
    openSearch: (next, options) => {
      if (next.query && next.query !== search.query) onSqlOverride(next.query)
      onOpenLink(
        {
          hash,
          analysisId: next.analysisId,
          rerun: !next.analysisId,
        },
        options
      )
    },
    goBack: onClose,
  }

  const controller = useResultsController(search, shell, {
    jobLabel: queryDisplayName(entry),
  })

  if (tab === 'follow-up') {
    // Until the record is read back there is no subject to key the thread on,
    // and no way to tell a body-less record from one still in flight.
    if (controller.stored.isLoading) {
      return <Skeleton className="h-40 w-full" aria-hidden="true" />
    }
    return (
      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <AnalyzeDrawerFollowUp
          queryHash={controller.analysis.results?.query_hash ?? undefined}
          context={controller.chat.results}
          onReRun={() => controller.actions.reRunStored('missing-body')}
        />
      </Suspense>
    )
  }

  return (
    <ResultsBody controller={controller} followUp="none" prompts="inline" />
  )
}
