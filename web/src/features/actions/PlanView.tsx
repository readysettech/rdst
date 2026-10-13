// The plan: a lead sentence from the first change, then its changes by category (Indexes,
// Server settings, Maintenance, Query rewrites), by priority (Do first, Then, Later) or by
// database, with the kept indexes on request, and the quiet rows under them: smaller wins left
// out, and the changes set aside or dismissed, each with Bring back.
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { EmptyState } from '@rs/ui-new/empty-state'
import { Text } from '@rs/ui-new/text'
import { useQueries } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useOpenAddDatabase } from '@/components/configure/addDatabase'
import { MonitoringStatusAction } from '@/features/monitor-kit/AiMonitoringStatus'
import {
  type AiBlocker,
  aiCanRun,
  aiWaitCopy,
  type MonitoringStatus,
  monitoringStatus,
  needsUserAction,
} from '@/features/monitor-kit/aiMonitoring'
import { plural } from '@/features/monitor-kit/format'
import { useSignInHeld } from '@/features/monitor-kit/signInHold'
import {
  type ActionKind,
  type GlobalActionsPage,
  type KeptIndex,
  type PlanCard,
  useKeptIndexes,
  usePlanVerb,
} from '@/lib/monitor/actions'
import { getSettings } from '@/lib/monitor/api'
import { monitorKeys } from '@/lib/monitor/keys'
import {
  basis,
  categoryGroups,
  databaseGroups,
  dateOf,
  type Group,
  KIND_LABEL,
  type LeadPart,
  leadSentence,
  tierGroups,
} from './actionsModel'
import {
  KeptRows,
  PLAN_COLUMNS,
  PLAN_GRID,
  PLAN_GRID_COMPACT,
  PlanRow,
} from './PlanRow'
import { hairlineLink, QuietRow, SectionTitle } from './parts'

// "Change" spans the verb and the name.
const HEAD = [
  { label: '' },
  { label: 'Change', className: 'col-span-2' },
  { label: 'What it does', className: 'max-desktop:hidden' },
  { label: 'Saves', align: 'end' as const },
  { label: '' },
  { label: '' },
]
const HEAD_COMPACT = [
  { label: '' },
  { label: 'Change', className: 'col-span-2' },
  { label: 'Saves', align: 'end' as const },
]

/** The indexes kept across the plan's databases. */
export const keptCount = (page: GlobalActionsPage) =>
  Object.values(page.keep).reduce((sum, count) => sum + count, 0)

interface PlanGroup {
  id: string
  title: string
  count?: number
  meta: string
  cards: PlanCard[]
  kept: KeptIndex[]
}

export function PlanView({
  page,
  cards,
  kind,
  group,
  showKept = false,
  now,
  selected = null,
  onSelect,
  filters,
  onClearKind,
}: {
  page: GlobalActionsPage
  /** The changes in view, ranked 1 to N. */
  cards: PlanCard[]
  kind: ActionKind | null
  group: Group
  /** The kept indexes join the table. */
  showKept?: boolean
  now: number
  /** The change open in the side pane; the rows beside it go compact. */
  selected?: number | null
  onSelect: (id: number) => void
  /** Group by, a picked kind and the kept indexes toggle, over the table. */
  filters?: ReactNode
  onClearKind: () => void
}) {
  const lead = leadSentence(cards)
  // The change's name is one run of parts, which becomes the link.
  const start = lead.findIndex((part) => part.link)
  const linked = lead.filter((part) => part.link)
  const before = start < 0 ? lead : lead.slice(0, start)
  const after = start < 0 ? [] : lead.slice(start + linked.length)
  const compact = cards.some((card) => card.id === selected)
  const leadId = cards.find((card) => card.priority === 'first')?.id
  const keeps = keptCount(page)
  const keptShown = showKept && (kind === null || kind === 'index')
  const kept = useKeptIndexes(
    Object.keys(page.keep).filter((database) => page.keep[database] > 0),
    keptShown
  )
  const keptGroup: PlanGroup[] =
    keptShown && kept.length
      ? [
          {
            id: 'keep',
            title: 'Keep',
            meta: `${keeps}, no change needed`,
            cards: [],
            kept,
          },
        ]
      : []
  const groups: PlanGroup[] =
    group === 'category'
      ? categoryGroups(cards, keeps, null)
          .filter((entry) => kind === null || entry.kind === kind)
          .map((entry) => ({
            id: `kind-${entry.kind}`,
            title: entry.title,
            count: entry.cards.length,
            meta: entry.meta,
            cards: entry.cards,
            kept: entry.kind === 'index' && keptShown ? kept : [],
          }))
      : [
          ...(group === 'database'
            ? databaseGroups(cards).map((entry) => ({
                id: `db-${entry.database}`,
                title: entry.database,
                meta: entry.meta,
                cards: entry.cards,
                kept: [],
              }))
            : tierGroups(cards).map((entry) => ({
                id: `tier-${entry.tier}`,
                title: entry.title,
                meta: entry.meta,
                cards: entry.cards,
                kept: [],
              }))),
          ...keptGroup,
        ]
  const listed = (entry: PlanGroup) =>
    entry.cards.length > 0 || entry.kept.length > 0
  const headed = groups.find(listed)
  const firstRank = cards[0]?.rank
  const openAddDatabase = useOpenAddDatabase()
  const navigate = useNavigate()
  const inScope = page.databases.filter(
    (row) => !page.db || row.database === page.db
  )
  const monitored = inScope.filter((row) => row.monitored)
  const waitsForSignIn =
    useSignInHeld(monitored.map((row) => row.database)).size > 0
  const { aiOff, deepBlocked } = useAiOff(
    monitored.map((row) => row.database),
    !cards.length && !waitsForSignIn
  )
  // The first plan is still building on a monitored database in view, where its analyses
  // can run; where they can't, the page says what they wait on.
  const firstPass = monitored.filter(
    (row) => row.checkup?.state === 'first_pass'
  )
  const building = firstPass.filter((row) => !deepBlocked.has(row.database))
  const heldFirst = building.length
    ? null
    : (firstPass
        .map((row) => ({
          database: row.database,
          blocker: deepBlocked.get(row.database),
        }))
        .find((row) => row.blocker) ?? null)
  const heldCopy = heldFirst?.blocker ? aiWaitCopy(heldFirst.blocker) : null
  const analyzed = building.reduce(
    (sum, row) =>
      sum + Math.min(row.checkup?.analyzed ?? 0, row.checkup?.total ?? 0),
    0
  )
  const total = building.reduce(
    (sum, row) => sum + (row.checkup?.total ?? 0),
    0
  )
  return (
    <>
      {cards.length ? (
        <>
          <Text
            as="p"
            level="headline-3"
            className="max-w-4xl font-normal text-content-layout-2"
            data-testid="actions-lead"
          >
            <LeadParts parts={before} />
            {cards[0] && linked.length ? (
              <a
                href={`#action-${cards[0].id}`}
                className={hairlineLink}
                data-testid="actions-lead-link"
                onClick={(event) => {
                  event.preventDefault()
                  onSelect(cards[0].id)
                }}
              >
                <LeadParts parts={linked} />
              </a>
            ) : null}
            <LeadParts parts={after} />
          </Text>
          <Text
            as="p"
            level="caption"
            className="mt-2.5 text-content-layout-3"
            data-testid="actions-status"
          >
            {basis(page.observed_s, page.updated_at, now)}
          </Text>
          {filters ? <div className="mt-10">{filters}</div> : null}
          {groups.map((section, index) => (
            <section
              key={section.id}
              className={index === 0 && filters ? 'mt-6' : 'mt-10'}
              aria-labelledby={section.id}
              data-testid="plan-group"
            >
              <SectionTitle
                id={section.id}
                title={section.title}
                count={section.count}
                meta={section.meta}
                quiet={!section.cards.length}
              />
              {listed(section) ? (
                <DividedList
                  columns={PLAN_COLUMNS}
                  className={compact ? PLAN_GRID_COMPACT : PLAN_GRID}
                  head={
                    section === headed
                      ? compact
                        ? HEAD_COMPACT
                        : HEAD
                      : undefined
                  }
                >
                  {section.cards.map((card) => (
                    <PlanRow
                      key={card.id}
                      card={card}
                      primary={card.rank === firstRank}
                      lead={card.id === leadId}
                      selected={card.id === selected}
                      compact={compact}
                      withKind={section.count == null}
                      onOpen={onSelect}
                    />
                  ))}
                  <KeptRows
                    indexes={section.kept}
                    compact={compact}
                    withKind={section.count == null}
                  />
                </DividedList>
              ) : null}
            </section>
          ))}
        </>
      ) : kind && page.cards.length ? (
        <>
          {filters ? <div className="mb-8">{filters}</div> : null}
          <EmptyState
            tone="quiet"
            title={`No ${KIND_LABEL[kind].toLowerCase()} changes to do`}
            body="The other kinds still have changes waiting."
            action={{ label: 'Show every kind', onClick: onClearKind }}
          />
        </>
      ) : page.databases.length === 0 ? (
        <EmptyState
          tone="quiet"
          title="No database yet"
          body="Add a database to start. Changes to make show up here, ranked by the database time they save."
          action={{ label: 'Add a database', onClick: () => openAddDatabase() }}
        />
      ) : waitsForSignIn ? (
        <EmptyState
          tone="quiet"
          title="Sign in to run the first analyses"
          body="AI checks need a Readyset sign-in, even with your own key. Changes show up here once they run."
          action={{
            label: 'Sign in',
            onClick: () => void navigate({ to: '/account-login' }),
          }}
        />
      ) : aiOff ? (
        <EmptyState
          tone="quiet"
          title={aiOff.status.headline ?? aiOff.status.label}
          body={
            <span
              className="flex flex-col items-center gap-3"
              data-testid="actions-ai-off"
            >
              <span>
                {monitored.length > 1 ? `${aiOff.database}: ` : ''}
                {aiOff.status.detail}
              </span>
              <MonitoringStatusAction
                target={aiOff.database}
                status={aiOff.status}
              />
            </span>
          }
        />
      ) : heldFirst && heldCopy ? (
        <EmptyState
          tone="quiet"
          title={heldCopy.title}
          body={`${firstPass.length > 1 ? `${heldFirst.database}: ` : ''}${heldCopy.detail} Changes show up here once the first analyses run.`}
          action={
            heldFirst.blocker === 'deep_off'
              ? {
                  label: 'Monitoring settings',
                  onClick: () =>
                    void navigate({
                      to: '/database',
                      search: {
                        target: heldFirst.database,
                        panel: 'settings',
                      } as never,
                    }),
                }
              : undefined
          }
        />
      ) : building.length ? (
        <EmptyState
          tone="quiet"
          title="Building your first plan"
          body={
            total > 0 && analyzed >= total
              ? 'Every busy query is analyzed. The plan is coming together.'
              : total
                ? `Analyzing your busiest queries: ${analyzed} of ${total} done. Changes show up as each one finishes.`
                : 'Reading your queries. The 10 busiest get analyzed first.'
          }
        />
      ) : monitored.length === 0 ? (
        <EmptyState
          tone="quiet"
          title={
            page.db
              ? `${page.db} isn't monitored`
              : "Your databases aren't monitored"
          }
          body="Turn on monitoring and RDST checks the database every few minutes. Changes to make show up here, ranked by the database time they save."
          action={{
            label: 'Turn on monitoring',
            onClick: () =>
              void navigate({
                to: '/database',
                search: {
                  target: page.db ?? inScope[0]?.database,
                  panel: 'enable',
                } as never,
              }),
          }}
        />
      ) : (
        <EmptyState
          tone="quiet"
          title="Nothing to change right now"
          body="RDST checks each database every few minutes. New changes show up here, ranked by the database time they save."
        />
      )}
      <QuietRows page={page} />
      <Footer page={page} group={group} />
    </>
  )
}

/**
 * The first monitored database whose AI is off for a reason the user has to act on, and
 * every database whose deep analyses can't run now, with why.
 */
function useAiOff(
  databases: string[],
  enabled: boolean
): {
  aiOff: { database: string; status: MonitoringStatus } | null
  deepBlocked: ReadonlyMap<string, AiBlocker>
} {
  const settings = useQueries({
    queries: databases.map((database) => ({
      queryKey: monitorKeys.settings(database),
      queryFn: () => getSettings(database),
      enabled,
    })),
  })
  let aiOff: { database: string; status: MonitoringStatus } | null = null
  const deepBlocked = new Map<string, AiBlocker>()
  for (const [index, query] of settings.entries()) {
    const data = query.data
    if (!data?.enabled) continue
    const status = monitoringStatus({
      enabled: data.enabled,
      ai: data.ai_monitoring,
      reason: data.ai_reason,
      since: data.ai_since,
    })
    if (!aiOff && needsUserAction(status))
      aiOff = { database: databases[index], status }
    const deep = aiCanRun({ settings: data }).deep
    if (deep) deepBlocked.set(databases[index], deep)
  }
  return { aiOff, deepBlocked }
}

function LeadParts({ parts }: { parts: LeadPart[] }) {
  return parts.map((part) =>
    part.strong ? (
      <b key={part.text} className="font-medium text-content-layout-1">
        {part.text}
      </b>
    ) : (
      part.text
    )
  )
}

function QuietRows({ page }: { page: GlobalActionsPage }) {
  const verb = usePlanVerb()
  const leftOut = Object.values(page.left_out).reduce(
    (sum, count) => sum + count,
    0
  )
  const bringBack = (card: PlanCard) =>
    verb.mutate({ target: card.database, id: card.id, verb: 'bring_back' })
  // Checks the latest report flags that have no change here: a setting off a rule of thumb,
  // an index with no reads in the report's window.
  const reportOnly = Object.entries(page.report_checks ?? {}).filter(
    ([, checks]) => checks.not_in_actions.length > 0
  )
  if (
    !leftOut &&
    !reportOnly.length &&
    !page.set_aside.length &&
    !page.dismissed.length
  )
    return null
  return (
    <div className="mt-11 border-t border-border-layout-2">
      {reportOnly.map(([database, checks]) => (
        <QuietRow
          key={`report-${database}`}
          name={`The report flags ${plural(checks.not_in_actions.length, 'check')} on ${database} with no change here`}
          sub={
            <>
              {checks.not_in_actions.map((check) => check.label).join(', ')}.{' '}
              <Link
                to="/database"
                search={{
                  target: database,
                  view: 'report',
                  report: String(checks.report_id),
                }}
                className={hairlineLink}
              >
                Open the report
              </Link>
            </>
          }
        />
      ))}
      {leftOut ? (
        <QuietRow
          name={`${leftOut} smaller ${leftOut === 1 ? 'win' : 'wins'} left out`}
          sub="Each would speed up little and still cost every write."
        />
      ) : null}
      {page.set_aside.length ? (
        <QuietRow
          name={`Set aside (${page.set_aside.length})`}
          sub={
            page.set_aside.length === 1
              ? `${page.set_aside[0].title}, ${asideLine(page.set_aside[0])}`
              : 'Each comes back to the plan on its date'
          }
          testId="plan-set-aside"
        >
          <ClosedList
            cards={page.set_aside}
            line={asideLine}
            onBringBack={bringBack}
            pending={verb.isPending}
          />
        </QuietRow>
      ) : null}
      {page.dismissed.length ? (
        <QuietRow
          name={`Dismissed (${page.dismissed.length})`}
          sub={
            page.dismissed.some((card) => card.reason)
              ? 'With your reasons'
              : undefined
          }
          testId="plan-dismissed"
        >
          <ClosedList
            cards={page.dismissed}
            line={(card) =>
              card.reason ??
              `dismissed on ${dateOf(card.closed_at ?? card.updated_at)}`
            }
            onBringBack={bringBack}
            pending={verb.isPending}
          />
        </QuietRow>
      ) : null}
    </div>
  )
}

function asideLine(card: PlanCard): string {
  const until = card.set_aside_until
    ? `until ${dateOf(card.set_aside_until)}`
    : ''
  return [until, card.reason].filter(Boolean).join(', ') || 'set aside'
}

function ClosedList({
  cards,
  line,
  onBringBack,
  pending,
}: {
  cards: PlanCard[]
  line: (card: PlanCard) => string
  onBringBack: (card: PlanCard) => void
  pending: boolean
}) {
  return (
    <DividedList columns="minmax(0,1fr) auto">
      {cards.map((card) => (
        <ListRow key={card.id} data-testid="closed-row">
          <span className="min-w-0">
            <Text
              as="span"
              level="body-small"
              className="block truncate text-content-layout-1"
            >
              {card.title}
            </Text>
            <Text
              as="span"
              level="caption"
              className="mt-0.5 block text-content-layout-3"
            >
              {card.database}, {line(card)}
            </Text>
          </span>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Bring back"
            disabled={pending}
            onClick={() => onBringBack(card)}
          />
        </ListRow>
      ))}
    </DividedList>
  )
}

function Footer({ page, group }: { page: GlobalActionsPage; group: Group }) {
  const unmonitored = page.db === null ? page.unmonitored : []
  return (
    <div className="mt-10 flex flex-wrap gap-x-4 gap-y-1 border-t border-border-layout-2 pt-3.5 text-caption text-content-layout-3">
      <span>
        {group === 'category'
          ? "Each group is ranked by database time saved a minute. The number is the change's place in the whole plan."
          : `Ranked by database time saved a minute${page.db === null ? ', so changes on different databases compare.' : '.'}`}
      </span>
      {unmonitored.map((database) => (
        <Link
          key={database}
          to="/database"
          search={{ target: database }}
          className={hairlineLink}
        >
          {database} is not monitored
        </Link>
      ))}
    </div>
  )
}
