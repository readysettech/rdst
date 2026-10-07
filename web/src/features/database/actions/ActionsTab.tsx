// The Actions tab: a fixed row of cards, one per kind of change (indexes, server settings,
// maintenance, query rewrites), each saying where it stands. The selected kind opens on a short
// lead, the detail behind it on request, then its changes; a change opens on how to do it.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useEffect, useMemo, useState } from 'react'
import {
  type ActionCard,
  type ActionsPage,
  type IndexPlan,
  useActions,
  useActionVerb,
  useIndexPlan,
} from '@/lib/monitor/actions'
import { CARD, CodeBlock } from '../reports/snapshotKit'
import type { ActionsSection } from '../useDatabaseUrl'
import {
  basedOn,
  CATEGORIES,
  type Category,
  type CategoryStatus,
  categoryStatus,
  checkedAgo,
  nextCheck,
  SEVERITY,
  type Severity,
  stepList,
} from './actionsModel'
import { CategoryStrip, type StripCard } from './CategoryStrip'
import { IndexChanges } from './IndexChanges'
import { Reveal } from './Reveal'

export interface ActionsTabProps {
  target: string
  section: ActionsSection
  onSectionChange: (section: ActionsSection) => void
  onShowQuery: (key: string) => void
  onOpenQueries: () => void
  /** A small live load chart, shown while the first plan is built. */
  liveCard?: ReactNode
}

/** Every open change, a grouped card's own changes counted one by one. */
export function flat(page: ActionsPage | undefined): ActionCard[] {
  return [...(page?.actions ?? []), ...(page?.more ?? [])].flatMap((card) =>
    card.children.length ? card.children : [card]
  )
}

/** How many changes the page shows: the index plan's, then every other kind's. */
export function changeCount(
  page: ActionsPage | undefined,
  plan?: IndexPlan | null
): number {
  if (!plan?.add || !plan.drop) return page?.open_count ?? 0
  return (
    plan.add.length +
    plan.drop.length +
    flat(page).filter((card) => card.category !== 'indexes').length
  )
}

/** The first plan is still on its way: the first deep analyses are running. */
export function buildingFirstPlan(page: ActionsPage | undefined): boolean {
  return page?.checkup?.state === 'first_pass'
}

const SEEN_KEY = 'rdst_actions_seen_keys'

/** Changes that arrived since this viewer last opened Actions (a per-viewer convenience). */
function useFreshKeys(target: string, keys: string[]): Set<string> {
  const storageKey = `${SEEN_KEY}:${target}`
  const [seen] = useState<Set<string> | null>(() => {
    try {
      const raw = window.localStorage.getItem(storageKey)
      return raw ? new Set(JSON.parse(raw) as string[]) : null
    } catch {
      return null
    }
  })
  const joined = keys.join('|')
  useEffect(() => {
    if (!joined) return
    const timer = setTimeout(() => {
      try {
        window.localStorage.setItem(
          storageKey,
          JSON.stringify(joined.split('|'))
        )
      } catch {
        // Storage can be unavailable; rows then show without the highlight.
      }
    }, 1500)
    return () => clearTimeout(timer)
  }, [storageKey, joined])
  // A first visit has nothing to compare with, so nothing is new.
  return useMemo(
    () => new Set(seen ? keys.filter((key) => !seen.has(key)) : []),
    [seen, joined]
  )
}

/** A soft glow that fades once, on a change that's new since the last visit. */
export function FreshGlow({ fresh }: { fresh: boolean }) {
  if (!fresh) return null
  return (
    <m.span
      aria-hidden="true"
      initial={{ opacity: 0.7 }}
      animate={{ opacity: 0 }}
      transition={{ duration: 1.6, ease: 'easeOut', delay: 0.3 }}
      className="pointer-events-none absolute inset-0 bg-surface-rising-soft"
    />
  )
}

const RANK: Severity[] = ['low', 'medium', 'high']

function worst(cards: ActionCard[]): Severity | null {
  return cards.reduce<Severity | null>(
    (top, card) =>
      top && RANK.indexOf(top) >= RANK.indexOf(card.severity)
        ? top
        : card.severity,
    null
  )
}

/** The four kinds RDST always checks, in a fixed order; Caching only when it has changes. */
function stripCards(
  page: ActionsPage,
  cards: ActionCard[],
  plan: IndexPlan | null | undefined
): StripCard[] {
  const progress = page.checkup
  // The index card counts the plan's changes; a grouped card lists only the first few. Until
  // the first analyses finish, the plan is half built and the card says it's checking.
  const indexCards =
    plan?.add && plan.drop && !buildingFirstPlan(page)
      ? [
          ...plan.add.map(() => ({
            type: 'add_index',
            severity: 'low' as Severity,
          })),
          ...plan.drop.map(() => ({
            type: 'drop_index',
            severity: 'low' as Severity,
          })),
        ]
      : null
  return CATEGORIES.filter(
    (category) =>
      category.id !== 'caching' ||
      cards.some((card) => card.category === 'caching')
  ).map((category) => ({
    kind: category.id,
    label: category.label,
    status: categoryStatus(
      category.id,
      category.id === 'indexes' && indexCards
        ? indexCards
        : cards.filter((card) => card.category === category.id),
      page.coverage?.[category.id] as
        | {
            state: CategoryStatus['state']
            checked_at: number | null
            next_at: number | null
          }
        | undefined,
      progress
    ),
  }))
}

/** The kind that opens first: the one with the most pressing changes, else Indexes. */
function defaultKind(cards: ActionCard[]): Category {
  const kinds = CATEGORIES.map((category) => category.id).filter((id) =>
    cards.some((card) => card.category === id)
  )
  const of = (id: Category) => cards.filter((card) => card.category === id)
  return (
    [...kinds].sort(
      (a, b) =>
        RANK.indexOf(worst(of(b)) ?? 'low') -
          RANK.indexOf(worst(of(a)) ?? 'low') || of(b).length - of(a).length
    )[0] ?? 'indexes'
  )
}

export function ActionsTab({
  target,
  section,
  onSectionChange,
  onShowQuery,
  onOpenQueries,
  liveCard,
}: ActionsTabProps) {
  const actions = useActions(target)
  const page = actions.data
  const building = buildingFirstPlan(page)
  const cards = flat(page)
  const plan = useIndexPlan(target, true, page?.computed_at).data
  const strip = page ? stripCards(page, cards, plan) : []
  const active: Category = strip.some((card) => card.kind === section)
    ? (section as Category)
    : defaultKind(cards)
  const status = strip.find((card) => card.kind === active)?.status
  const fresh = useFreshKeys(target, [
    ...cards.map((card) => card.key),
    ...(plan?.add ?? []).map((add) => add.id),
    ...(plan?.drop ?? []).map((drop) => drop.id),
  ])
  return (
    <div className="space-y-6" data-testid="actions-tab">
      {actions.isError && !page ? (
        <div className={cn(CARD, 'p-6')} data-testid="actions-error">
          <Text as="p" level="body-medium" className="text-content-layout-2">
            RDST couldn't load the actions for this database. It keeps trying.
          </Text>
        </div>
      ) : !page ? (
        <div className={cn(CARD, 'h-40 animate-pulse')} />
      ) : (
        <>
          {building ? (
            <BuildingPlan
              page={page}
              liveCard={liveCard}
              onOpenQueries={onOpenQueries}
            />
          ) : (
            <StatusLine page={page} />
          )}
          <CategoryStrip
            cards={strip}
            active={active}
            onSelect={onSectionChange}
          />
          <AnimatePresence mode="wait" initial={false}>
            <m.div
              key={active}
              initial={{ opacity: 0, y: 4 }}
              animate={{
                opacity: 1,
                y: 0,
                transition: { duration: 0.18, ease: 'easeOut' },
              }}
              exit={{ opacity: 0, transition: { duration: 0.1 } }}
            >
              {status &&
              (status.state === 'checking' ||
                status.state === 'waiting' ||
                (status.state === 'checked' &&
                  !(active === 'indexes' && page.sections.indexes))) ? (
                <Quiet
                  status={status}
                  note={page.coverage?.[active]?.note ?? null}
                  nextAt={page.coverage?.[active]?.next_at ?? null}
                  waitingOn={page.coverage?.[active]?.waiting_on ?? null}
                />
              ) : (
                <Section
                  target={target}
                  kind={active}
                  fresh={fresh}
                  page={page}
                  cards={cards.filter((card) => card.category === active)}
                  onShowQuery={onShowQuery}
                />
              )}
            </m.div>
          </AnimatePresence>
          {page.recently_done.length ? (
            <RecentlyDone cards={page.recently_done} />
          ) : null}
          {page.set_aside.length ? (
            <SetAside target={target} cards={page.set_aside} />
          ) : null}
        </>
      )}
    </div>
  )
}

/** A kind with nothing to do yet: what was checked, or what it's waiting on. */
function Quiet({
  status,
  note,
  nextAt,
  waitingOn = null,
}: {
  status: CategoryStatus
  note: string | null
  nextAt: number | null
  waitingOn?: string | null
}) {
  const busy = status.state === 'checking' || status.state === 'waiting'
  return (
    <div
      className={cn(CARD, 'flex items-start gap-4 p-6')}
      data-testid={busy ? 'actions-pending' : 'actions-all-clear'}
    >
      <span
        className={cn(
          'mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
          busy
            ? 'bg-surface-rising-soft'
            : 'bg-surface-positive-soft text-content-positive-soft'
        )}
      >
        {busy ? (
          <span
            aria-hidden="true"
            className="h-2 w-2 rounded-full bg-content-rising-plain mk-breathe"
          />
        ) : (
          <Icon name="tick" label="" aria-hidden="true" className="h-4 w-4" />
        )}
      </span>
      <div className="space-y-1">
        <Text as="h2" level="headline-5" className="text-content-layout-1">
          {busy ? status.line : 'Nothing to change'}
        </Text>
        <Text as="p" level="body-medium" className="text-content-layout-2">
          {status.state === 'waiting'
            ? 'Rewrites come from the deep analysis of your busiest queries. They show up here as those analyses finish.'
            : busy
              ? (waitingOn ??
                'Checking now. Anything worth changing shows up here.')
              : [
                  note ? `${note}.` : null,
                  nextAt ? `${nextCheck(nextAt)}.` : null,
                ]
                  .filter(Boolean)
                  .join(' ')}
        </Text>
      </div>
    </div>
  )
}

function Section({
  target,
  kind,
  fresh,
  page,
  cards,
  onShowQuery,
}: {
  target: string
  kind: Category
  fresh: Set<string>
  page: ActionsPage
  cards: ActionCard[]
  onShowQuery: (key: string) => void
}) {
  const plan = useIndexPlan(target, kind === 'indexes', page.computed_at).data
  const story = page.sections[kind]
  return (
    <div className="space-y-5" data-testid="action-section-list">
      {/* A lone setting or task says it all in its own row, opened below. */}
      {story && (kind === 'indexes' || cards.length > 1) ? (
        <Story
          story={story}
          tables={
            plan
              ? [...plan.add, ...plan.drop].map((change) => change.table)
              : []
          }
        />
      ) : null}
      {kind === 'indexes' ? (
        plan ? (
          <IndexChanges
            target={target}
            plan={plan}
            fresh={fresh}
            cards={cards}
            onShowQuery={onShowQuery}
          />
        ) : null
      ) : cards.length ? (
        <ul
          className={cn(
            CARD,
            'divide-y divide-border-layout-1 overflow-hidden'
          )}
        >
          {cards.map((card, index) => (
            <m.li
              key={card.key}
              className="relative"
              initial={{ opacity: 0, y: 6 }}
              animate={{
                opacity: 1,
                y: 0,
                transition: {
                  duration: 0.24,
                  ease: 'easeOut',
                  delay: Math.min(index, 8) * 0.035,
                },
              }}
            >
              <FreshGlow fresh={fresh.has(card.key)} />
              <ChangeRow
                target={target}
                card={card}
                startOpen={cards.length === 1}
                onShowQuery={onShowQuery}
              />
            </m.li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/** What to do and why up front; the numbers behind it on request. */
/** A line with each table it names set off as a badge. */
function Badged({ text, tables }: { text: string; tables: string[] }) {
  const names = [...new Set(tables)].filter(Boolean)
  if (!names.length) return <>{text}</>
  const escaped = names.map((name) =>
    name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  )
  const parts = text.split(new RegExp(`\\b(${escaped.join('|')})\\b`))
  return (
    <>
      {parts.map((part, index) =>
        index % 2 ? (
          <span
            key={index}
            className="mx-0.5 rounded-control bg-surface-layout-2 px-1.5 py-0.5 font-mono text-[0.9em] text-content-layout-1"
          >
            {part}
          </span>
        ) : (
          part
        )
      )}
    </>
  )
}

function Story({
  story,
  tables = [],
}: {
  story: ActionsPage['sections'][string]
  tables?: string[]
}) {
  const [open, setOpen] = useState(false)
  return (
    <section
      className={cn(CARD, 'space-y-4 p-6')}
      data-testid="action-story"
      aria-label={story.headline}
    >
      <div className="max-w-5xl space-y-2">
        <Text as="h2" level="headline-5" className="text-content-layout-1">
          <Badged text={story.lead[0] ?? story.headline} tables={tables} />
        </Text>
        {story.lead.slice(1).map((line) => (
          <Text
            key={line}
            as="p"
            level="body-large"
            className="text-content-layout-1"
          >
            <Badged text={line} tables={tables} />
          </Text>
        ))}
      </div>
      {story.details.length || story.facts.length ? (
        <div>
          <Pressable
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="inline-flex items-center gap-1 rounded-control text-label-medium text-content-layout-2 hover:text-content-layout-1"
          >
            {open ? 'Hide the numbers' : 'Show the numbers'}
            <Icon
              name="chevron-down"
              label=""
              aria-hidden="true"
              className={cn(
                'h-4 w-4 transition-transform duration-fast',
                open && 'rotate-180'
              )}
            />
          </Pressable>
          <Reveal open={open}>
            <div className="max-w-5xl space-y-3 pt-4">
              {story.details.map((paragraph) => (
                <Text
                  key={paragraph}
                  as="p"
                  level="body-medium"
                  className="text-content-layout-2"
                >
                  {paragraph}
                </Text>
              ))}
            </div>
            {story.facts.length ? (
              <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-border-layout-1 pt-5 tablet:grid-cols-4">
                {story.facts.map((fact) => (
                  <div key={fact.label} className="min-w-0 space-y-1">
                    <dt className="text-label-small text-content-layout-3">
                      {fact.label}
                    </dt>
                    <dd className="truncate text-label-large tabular-nums text-content-layout-1">
                      {fact.value}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </Reveal>
        </div>
      ) : null}
    </section>
  )
}

/** What the page rests on and when it last updated; each kind says when it checks next. */
function StatusLine({ page }: { page: ActionsPage }) {
  const parts = [
    page.window ? basedOn(page.window) : null,
    page.computed_at
      ? checkedAgo(page.computed_at).replace(/^checked/, 'Updated')
      : null,
  ].filter(Boolean)
  return (
    <Text
      as="p"
      level="body-small"
      className="text-content-layout-3"
      data-testid="actions-status"
    >
      {parts.join(' · ')}
    </Text>
  )
}

/** The first run: what RDST is doing right now, a bar for it, and the live load beside. */
function BuildingPlan({
  page,
  liveCard,
  onOpenQueries,
}: {
  page: ActionsPage
  liveCard?: ReactNode
  onOpenQueries: () => void
}) {
  const { analyzed, total } = page.checkup
  const checking = total > 0 && analyzed >= total
  const share = checking ? 1 : total ? Math.min(1, analyzed / total) : 0
  return (
    <div
      className="grid gap-3 laptop:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]"
      data-testid="actions-building"
    >
      <div className={cn(CARD, 'space-y-4 p-6')}>
        <div className="space-y-1">
          <Text as="h2" level="headline-5" className="text-content-layout-1">
            Building your first plan
          </Text>
          <Text as="p" level="body-medium" className="text-content-layout-2">
            {checking
              ? 'Every busy query is analyzed. The plan is coming together.'
              : total
                ? `Analyzing your busiest queries: ${Math.min(analyzed, total)} of ${total} done. Changes show up as each one finishes.`
                : 'Reading your queries. The 10 busiest get analyzed first.'}
          </Text>
        </div>
        <div className="space-y-2">
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-layout-2">
            <m.div
              className={cn(
                'h-full rounded-full bg-content-rising-plain',
                checking && 'mk-breathe'
              )}
              initial={false}
              animate={{ width: `${Math.max(4, share * 100)}%` }}
              transition={{ duration: 0.5, ease: 'easeOut' }}
            />
          </div>
          <Text as="p" level="body-small" className="text-content-layout-3">
            Deep analysis, a few queries at a time
          </Text>
        </div>
        <Pressable
          onClick={onOpenQueries}
          className="text-label-medium text-content-layout-2 underline underline-offset-4 hover:text-content-layout-1"
        >
          Watch the analyses come in on Queries
        </Pressable>
      </div>
      {liveCard ? (
        <div className="flex flex-col justify-center">{liveCard}</div>
      ) : null}
    </div>
  )
}

/** One change: what it is and what it does, opening on how to do it. */
function ChangeRow({
  target,
  card,
  startOpen,
  onShowQuery,
}: {
  target: string
  card: ActionCard
  startOpen: boolean
  onShowQuery: (key: string) => void
}) {
  const [open, setOpen] = useState(startOpen)
  const severity = SEVERITY[card.severity]
  return (
    <li data-testid="action-card" data-severity={card.severity}>
      <Pressable
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="grid w-full grid-cols-[minmax(0,1fr)_auto_16px] items-center gap-4 px-6 py-4 text-left hover:bg-surface-layout-2/40"
      >
        <span className="min-w-0 space-y-1">
          <span className="block text-label-large text-content-layout-1">
            {card.title}
          </span>
          <span className="block text-body-small text-content-layout-2">
            {card.summary}
          </span>
        </span>
        <Tag
          size="small"
          variant={severity.tag}
          modifier="ghost"
          label={severity.label}
        />
        <Icon
          name="chevron-down"
          label=""
          aria-hidden="true"
          className={cn(
            'h-4 w-4 text-content-layout-3 transition-transform duration-fast',
            open && 'rotate-180'
          )}
        />
      </Pressable>
      <Reveal open={open}>
        <div className="border-t border-border-layout-1 px-6 py-5">
          <ChangeDetail target={target} card={card} onShowQuery={onShowQuery} />
        </div>
      </Reveal>
    </li>
  )
}

/** How to do one change: the measurements behind it, the steps, the SQL. */
export function ChangeDetail({
  target,
  card,
  onShowQuery,
  children,
}: {
  target: string
  card: ActionCard
  onShowQuery: (key: string) => void
  children?: ReactNode
}) {
  const steps = stepList(card.steps)
  const detail = card.detail as { original?: unknown } | null
  const original = typeof detail?.original === 'string' ? detail.original : null
  return (
    <div className="space-y-5" data-testid="action-detail">
      {card.why && card.why !== card.summary ? (
        <Text as="p" level="body-medium" className="text-content-layout-2">
          {card.why}
        </Text>
      ) : null}
      {card.evidence?.length ? (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 tablet:grid-cols-4">
          {card.evidence.slice(0, 4).map((row) => (
            <div key={String(row.label)} className="min-w-0 space-y-0.5">
              <dt className="text-label-small text-content-layout-3">
                {String(row.label)}
              </dt>
              <dd className="truncate text-label-medium tabular-nums text-content-layout-1">
                {String(row.value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {children}
      {steps.length ? (
        <div className="space-y-2">
          <Text as="h3" level="label-large" className="text-content-layout-1">
            How to do it
          </Text>
          {steps.length === 1 ? (
            <Text as="p" level="body-medium" className="text-content-layout-2">
              {steps[0]}
            </Text>
          ) : (
            <ol className="list-decimal space-y-1.5 pl-5 text-body-medium text-content-layout-2">
              {steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
      {card.type === 'rewrite_query' && original ? (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Text
              as="h3"
              level="label-medium"
              className="text-content-layout-3"
            >
              Your query
            </Text>
            <CodeBlock sql={original} label="Copy your query" />
          </div>
          {card.sql ? (
            <div className="space-y-1.5">
              <Text
                as="h3"
                level="label-medium"
                className="text-content-layout-3"
              >
                The rewrite
              </Text>
              <CodeBlock sql={card.sql} label="Copy the rewrite" />
            </div>
          ) : null}
        </div>
      ) : card.sql ? (
        <CodeBlock sql={card.sql} label="Copy SQL" />
      ) : null}
      {card.rows?.length ? (
        <ul className="space-y-1 text-body-small text-content-layout-2">
          {card.rows.map((row) => (
            <li key={`${String(row.name)}-${String(row.value)}`}>
              <span className="text-content-layout-1">{String(row.name)}</span>
              {': '}
              {String(row.value ?? '')}
              {row.detail ? ` (${String(row.detail)})` : ''}
            </li>
          ))}
        </ul>
      ) : null}
      {card.note ? (
        <Text as="p" level="body-small" className="text-content-layout-3">
          {card.note}
        </Text>
      ) : null}
      {card.queries.length === 1 ? (
        <Pressable
          onClick={() => onShowQuery(card.queries[0].key)}
          className="self-start text-body-small text-content-layout-2 underline decoration-content-layout-3 underline-offset-4 hover:text-content-layout-1"
        >
          See this query on Queries
        </Pressable>
      ) : card.queries.length ? (
        <div className="space-y-1.5">
          <Text as="h3" level="label-medium" className="text-content-layout-3">
            Queries it affects
          </Text>
          <ul className="space-y-1">
            {card.queries.map((query) => (
              <li key={query.key}>
                <Pressable
                  onClick={() => onShowQuery(query.key)}
                  className="text-body-small text-content-layout-1 underline decoration-content-layout-3 underline-offset-4 hover:decoration-content-layout-1"
                >
                  {query.name}
                </Pressable>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {card.id != null ? <Verbs target={target} id={card.id} /> : null}
    </div>
  )
}

/** Mark a change done, put it off for a week, or dismiss it. */
export function Verbs({
  target,
  id,
  inline = false,
}: {
  target: string
  id: number
  /** Right under the change's sentence, with no rule above it. */
  inline?: boolean
}) {
  const verb = useActionVerb(target)
  const [checked, setChecked] = useState<string | null>(null)
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-2',
        !inline && 'border-t border-border-layout-1 pt-4'
      )}
    >
      <Button
        label="I did this"
        size="small"
        modifier="outline"
        disabled={verb.isPending}
        onClick={() =>
          verb.mutate(
            { id, verb: 'check' },
            {
              onSuccess: (page) =>
                setChecked(
                  flat(page).some((item) => item.id === id)
                    ? "The change doesn't show in the database yet. Next check in a few minutes."
                    : null
                ),
            }
          )
        }
      />
      <Button
        label="Not now"
        size="small"
        modifier="ghost"
        disabled={verb.isPending}
        onClick={() =>
          verb.mutate({
            id,
            verb: 'snooze',
            until: Math.floor(Date.now() / 1000) + 7 * 86400,
          })
        }
      />
      <Button
        label="Dismiss"
        size="small"
        modifier="ghost"
        disabled={verb.isPending}
        onClick={() => verb.mutate({ id, verb: 'dismiss' })}
      />
      {checked ? (
        <Text as="span" level="body-small" className="text-content-layout-3">
          {checked}
        </Text>
      ) : null}
    </div>
  )
}

function ms(value: number): string {
  return value >= 10
    ? `${value.toFixed(0)} ms`
    : value >= 1
      ? `${value.toFixed(1)} ms`
      : `${value.toFixed(2)} ms`
}

/** What happened after a change was made: measured faster, still measuring, or no gain. */
function outcome(card: ActionCard): { text: string; tone: string } {
  const result = card.result as { before_ms: number; after_ms: number } | null
  if (card.state === 'confirmed' && result)
    return {
      text: `Verified. Its queries went from ${ms(result.before_ms)} to ${ms(result.after_ms)}.`,
      tone: 'text-content-positive-soft',
    }
  if (card.state === 'no_effect' && result)
    return {
      text: `Didn't help. Its queries stayed near ${ms(result.after_ms)}, so it may be worth undoing.`,
      tone: 'text-content-warning-soft',
    }
  if (card.type === 'add_index' || card.type === 'rewrite_query')
    return {
      text: 'Done. Measuring the queries it speeds up.',
      tone: 'text-content-layout-3',
    }
  return { text: 'Done.', tone: 'text-content-layout-3' }
}

function RecentlyDone({ cards }: { cards: ActionCard[] }) {
  return (
    <section className="space-y-2" aria-label="Done recently">
      <Text as="h2" level="label-medium" className="text-content-layout-3">
        Done recently
      </Text>
      <ul className={cn(CARD, 'divide-y divide-border-layout-1')}>
        {cards.map((card) => {
          const result = outcome(card)
          return (
            <li
              key={card.key}
              className="flex items-start gap-3 px-5 py-3"
              data-testid="action-done"
              data-state={card.state}
            >
              <Icon
                name={card.state === 'no_effect' ? 'alert' : 'tick'}
                label=""
                aria-hidden="true"
                className={cn(
                  'mt-0.5 h-4 w-4 shrink-0',
                  card.state === 'no_effect'
                    ? 'text-content-warning-plain'
                    : 'text-content-positive-plain'
                )}
              />
              <span className="min-w-0 space-y-0.5">
                <span className="block text-label-medium text-content-layout-1">
                  {card.title}
                </span>
                <span className={cn('block text-body-small', result.tone)}>
                  {result.text}
                </span>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/** Changes someone dismissed or put off, closed, each with a way to bring it back. */
function SetAside({ target, cards }: { target: string; cards: ActionCard[] }) {
  const [open, setOpen] = useState(false)
  const verb = useActionVerb(target)
  return (
    <section
      className={cn(CARD, 'overflow-hidden')}
      data-testid="actions-set-aside"
    >
      <Pressable
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-4 px-6 py-4 text-left hover:bg-surface-layout-2/40"
      >
        <span className="text-label-large text-content-layout-2">
          Set aside ({cards.length})
        </span>
        <Icon
          name="chevron-down"
          label=""
          aria-hidden="true"
          className={cn(
            'h-4 w-4 text-content-layout-3 transition-transform duration-fast',
            open && 'rotate-180'
          )}
        />
      </Pressable>
      <Reveal open={open}>
        <ul className="divide-y divide-border-layout-1 border-t border-border-layout-1">
          {cards.map((card) => (
            <li
              key={card.key}
              className="flex items-center justify-between gap-4 px-6 py-3"
            >
              <span className="min-w-0 space-y-0.5">
                <span className="block truncate text-label-medium text-content-layout-1">
                  {card.title}
                </span>
                <span className="block text-body-small text-content-layout-3">
                  {card.state === 'snoozed' ? 'Not now' : 'Dismissed'}
                </span>
              </span>
              <Button
                label="Bring back"
                size="small"
                modifier="ghost"
                disabled={verb.isPending}
                onClick={() =>
                  card.id != null &&
                  verb.mutate({ id: card.id, verb: 'reopen' })
                }
              />
            </li>
          ))}
        </ul>
      </Reveal>
    </section>
  )
}
