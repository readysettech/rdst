import { cn } from '@rs/tailwind-base'
import { Button, buttonStyles } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Dropdown } from '@rs/ui-new/dropdown'
import { EmptyState } from '@rs/ui-new/empty-state'
import { Icon } from '@rs/ui-new/icon'
import { Metric, MetricRow } from '@rs/ui-new/metric-row'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Verdict } from '@rs/ui-new/verdict'
import { Link } from '@tanstack/react-router'
import { type ComponentProps, type ReactNode, useState } from 'react'
import { useMonitorSettings, useReport } from '@/lib/monitor/hooks'
import type {
  DatabaseReport,
  InboxItem,
  InboxRelated,
} from '@/lib/monitor/types'
import { monitorLinkTo } from '../home/homeModel'
import { plural } from '../monitor-kit/format'
import { MuteMenu } from '../monitor-kit/MuteMenu'
import {
  fixRoute,
  itemLink,
  itemRoute,
  type LinkRoute,
  routeHref,
} from './inboxLinks'
import {
  bandOf,
  bodyOf,
  clock12,
  closedAt,
  closedSentence,
  detailMeta,
  emptySentence,
  kindOf,
  noChangeText,
  runningText,
  sameWords,
  shortClock,
  summaryTokens,
  wholeSentences,
} from './inboxModel'
import { MuteRulesDialog } from './MuteRulesList'
import type { InboxActions } from './useInboxActions'

const solidLink = buttonStyles({
  variant: 'primary',
  modifier: 'solid',
  size: 'small',
}).root()
const DASH = '\u2014'
const hairlineLink =
  'border-b border-border-layout-2 hover:border-content-layout-2'

function RouteLink({
  route,
  children,
  ...rest
}: {
  route: LinkRoute | { to: string; search?: Record<string, unknown> }
  children: ReactNode
} & Omit<ComponentProps<'a'>, 'href' | 'children'>) {
  return (
    <Link to={route.to as never} search={route.search as never} {...rest}>
      {children}
    </Link>
  )
}

function Header({ item, children }: { item: InboxItem; children: ReactNode }) {
  const band = bandOf(item)
  return (
    // A narrow pane truncates the meta first, then moves the buttons under it.
    <div
      data-testid="inbox-detail-header"
      className="flex min-h-15 shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-border-layout-2 py-2.5 pr-5 pl-6 [-webkit-app-region:drag]"
    >
      <Tag
        size="small"
        variant={band === 'act_now' ? 'negative' : 'neutral'}
        modifier="ghost"
        className="rounded-pill whitespace-nowrap"
        label={
          band === 'act_now' ? 'Act now' : band === 'watch' ? 'Watch' : 'Closed'
        }
      />
      <span className="min-w-0 flex-1 truncate text-body-small text-content-layout-3">
        {detailMeta(item)}
      </span>
      <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-1.5 [-webkit-app-region:no-drag]">
        {children}
      </div>
    </div>
  )
}

/** The kebab: Mark resolved while open, the mute scopes, and the mute rules list. */
function Kebab({ item, actions }: { item: InboxItem; actions: InboxActions }) {
  const [rulesOpen, setRulesOpen] = useState(false)
  const singleKey = item.query_keys.length === 1 ? item.query_keys[0] : null
  const names = new Map(Object.entries(item.query_labels ?? {}))
  return (
    <>
      <MuteMenu
        target={item.target}
        queryKey={singleKey}
        queryLabel={singleKey ? item.subject_label : undefined}
        type={item.type}
        label={`More actions for ${item.title}`}
        extraItems={
          <>
            {bandOf(item) !== 'closed' && (
              <Dropdown.Item
                label="Mark resolved"
                onSelect={() => actions.close([item], { kind: 'resolve' })}
              />
            )}
            <Dropdown.Item
              label="Mute rules"
              onSelect={() => setRulesOpen(true)}
            />
          </>
        }
      />
      <MuteRulesDialog
        open={rulesOpen}
        onClose={() => setRulesOpen(false)}
        names={names}
      />
    </>
  )
}

function Section({
  title,
  meta,
  children,
  className,
}: {
  title: string
  meta?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section aria-label={title} className={cn('mt-9', className)}>
      <h3 className="mb-2.5 flex items-baseline gap-2.5 text-label-large text-content-layout-1">
        {title}
        {meta && (
          <span className="text-caption font-normal text-content-layout-3">
            {meta}
          </span>
        )}
      </h3>
      {children}
    </section>
  )
}

/** The item's own log: numbers in the first tone, the rest in the second. */
function History({ item }: { item: InboxItem }) {
  if (!item.history?.length) return null
  return (
    <Section title="History">
      <DividedList columns="52px 1fr">
        {item.history.map((entry, index) => (
          <ListRow key={`${entry.ts}-${index}`} className="min-h-9 py-1.5">
            <span className="text-mono-small tabular-nums text-content-layout-3">
              {shortClock(entry.ts)}
            </span>
            <span className="text-body-small text-content-layout-2">
              {summaryTokens(entry.text).map((token, position) =>
                token.kind === 'number' ? (
                  <span key={position} className="text-content-layout-1">
                    {token.text}
                  </span>
                ) : token.kind === 'text' ? (
                  <span key={position}>{token.text}</span>
                ) : (
                  <span key={position}> to </span>
                )
              )}
            </span>
          </ListRow>
        ))}
      </DividedList>
    </Section>
  )
}

function RelatedRow({ row }: { row: InboxRelated }) {
  return (
    <ListRow asChild interactive className="min-h-12">
      <RouteLink route={monitorLinkTo(row.link)}>
        <span className="min-w-0">
          <span className="block truncate text-body-medium font-medium text-content-layout-1">
            {row.label}
          </span>
          {row.detail && (
            <span className="block text-caption text-content-layout-3">
              {row.detail}
            </span>
          )}
        </span>
        <span className="text-caption text-content-layout-3">
          {row.link.label}
        </span>
        <Icon
          name="chevron-right"
          label=""
          size="small"
          className="text-content-layout-3"
        />
      </RouteLink>
    </ListRow>
  )
}

function Related({ item }: { item: InboxItem }) {
  if (!item.related?.length) return null
  return (
    <Section title="Related">
      <DividedList columns="1fr auto auto">
        {item.related.map((row) => (
          <RelatedRow key={`${row.link.kind}-${row.label}`} row={row} />
        ))}
      </DividedList>
    </Section>
  )
}

function Note({ children }: { children: ReactNode }) {
  return (
    <Text as="p" level="caption" className="mt-2.5 text-content-layout-3">
      {children}
    </Text>
  )
}

function Sql({ sql }: { sql: string }) {
  return (
    <pre className="mt-1.5 w-fit max-w-full whitespace-pre-wrap rounded-control bg-surface-layout-2 px-2.5 py-2 font-mono text-mono-medium text-content-layout-1">
      {sql}
    </pre>
  )
}

/** An older advice step, "Cancel it: SELECT ...", split so the SQL can be copied. */
function adviceStep(text: string): { title: string; sql: string | null } {
  const split = text.indexOf(': ')
  const rest = split > 0 ? text.slice(split + 2) : ''
  return /^[A-Z]{2,}\b/.test(rest)
    ? { title: text.slice(0, split), sql: rest }
    : { title: text, sql: null }
}

function Steps({
  steps,
  meta,
  note,
}: {
  steps: { title: string; sql: string | null }[]
  meta?: string
  note: string | null
}) {
  return (
    <Section title="What to do" meta={meta}>
      <DividedList columns="20px 1fr auto">
        {steps.map((step, index) => (
          <ListRow key={step.title} className="items-start py-3">
            <span className="pt-0.5 text-caption text-content-layout-3">
              {index + 1}
            </span>
            <span className="min-w-0">
              <span className="block text-body-small text-content-layout-1">
                {step.title}
              </span>
              {step.sql && <Sql sql={step.sql} />}
            </span>
            {step.sql ? <CopyButton text={step.sql} label="Copy" /> : <span />}
          </ListRow>
        ))}
      </DividedList>
      {note && <Note>{note}</Note>}
    </Section>
  )
}

/** The open action that would likely fix this item, linked to it on Actions. */
function LikelyFix({
  item,
  actions,
}: {
  item: InboxItem
  actions: InboxActions
}) {
  const route = fixRoute(item)
  if (!route || !item.action || bandOf(item) === 'closed') return null
  return (
    <Text
      as="p"
      level="body-small"
      data-testid="item-likely-fix"
      className="mt-2.5 text-content-layout-3"
    >
      Likely fix:{' '}
      <a
        href={routeHref(route)}
        onClick={(event) => {
          if (
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey
          )
            return
          event.preventDefault()
          actions.openRoute(item, route)
        }}
        className={cn('text-content-layout-1', hairlineLink)}
      >
        {item.action.title}
      </a>
    </Text>
  )
}

function Title({
  item,
  meta,
  actions,
}: {
  item: InboxItem
  meta: ReactNode
  actions: InboxActions
}) {
  return (
    <div>
      <Text as="h2" level="headline-2" className="text-content-layout-1">
        {item.title}
      </Text>
      <div className="mt-1.5 flex flex-wrap items-center gap-2.5 text-body-small text-content-layout-3">
        {meta}
      </div>
      <LikelyFix item={item} actions={actions} />
    </div>
  )
}

function Live({ text }: { text: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-content-negative-soft">
      <span
        aria-hidden="true"
        className="h-1.5 w-1.5 rounded-pill bg-content-negative-plain"
      />
      {text}
    </span>
  )
}

function StuckQuery({
  item,
  now,
  actions,
}: {
  item: InboxItem
  now: number
  actions: InboxActions
}) {
  const live = runningText(item, now)
  const steps = item.steps?.length
    ? item.steps
    : (item.advice?.steps ?? []).map(adviceStep)
  return (
    <>
      <Title
        item={item}
        actions={actions}
        meta={
          <>
            <span>{item.subject_label}</span>
            {live && <Live text={live} />}
            {item.pid != null && <span>pid {item.pid}</span>}
          </>
        }
      />
      {steps.length > 0 && (
        <Steps
          steps={steps}
          meta="in order, stop when it ends"
          note="Closes by itself when the statement ends."
        />
      )}
      <History item={item} />
      <Related item={item} />
    </>
  )
}

/** True for a plain left click, which the page handles in place. */
const plainClick = (event: MouseEvent) =>
  event.button === 0 &&
  !event.metaKey &&
  !event.ctrlKey &&
  !event.shiftKey &&
  !event.altKey

/** The link to the item's database on Actions; opening it closes a Changes ready event. */
function ActionsLink({
  item,
  actions,
  className,
  children,
}: {
  item: InboxItem
  actions: InboxActions
  className: string
  children: ReactNode
}) {
  return (
    <a
      href={routeHref(itemRoute(item))}
      onClick={(event) => {
        if (!plainClick(event.nativeEvent)) return
        event.preventDefault()
        actions.openActions(item)
      }}
      className={className}
    >
      {children}
    </a>
  )
}

function ChangesReady({
  item,
  actions,
}: {
  item: InboxItem
  actions: InboxActions
}) {
  const changes = item.changes
  const first = changes?.do_first ?? 0
  return (
    <>
      <Title
        item={item}
        actions={actions}
        meta={
          <span>
            {first
              ? `${first} to do first`
              : `${changes?.count ?? 0} to do, none urgent`}
          </span>
        }
      />
      {changes && changes.titles.length > 0 && (
        <Section
          title="Top changes"
          meta={
            changes.count > changes.titles.length
              ? `${changes.titles.length} of ${changes.count}`
              : undefined
          }
        >
          <DividedList columns="20px 1fr auto">
            {changes.titles.map((title, index) => (
              <ListRow key={title} className="min-h-12 py-3">
                <span className="text-caption tabular-nums text-content-layout-3">
                  {index + 1}
                </span>
                <span className="min-w-0 truncate text-body-small text-content-layout-1">
                  {title}
                </span>
                <span className="text-caption text-content-layout-3">
                  {index < first ? 'Do first' : ''}
                </span>
              </ListRow>
            ))}
          </DividedList>
          <Note>Opening Actions for {item.target} closes this item.</Note>
        </Section>
      )}
      <History item={item} />
      <Related item={item} />
    </>
  )
}

/** "14 ms", "9.6 ms" as a metric value. */
const msValue = (ms: number) =>
  ms < 10 ? Number(ms.toFixed(1)) : Math.round(ms)

function NoChangeYet({
  item,
  actions,
}: {
  item: InboxItem
  actions: InboxActions
}) {
  const body = bodyOf(item)
  const before = typeof body.before_ms === 'number' ? body.before_ms : null
  const after = typeof body.after_ms === 'number' ? body.after_ms : null
  const calls = typeof body.calls === 'number' ? body.calls : null
  return (
    <>
      <Title
        item={item}
        actions={actions}
        meta={<span>{noChangeText(item) ?? item.summary}</span>}
      />
      {before != null && after != null && (
        <MetricRow className="mt-7">
          <Metric
            label="Before"
            value={msValue(before)}
            unit="ms"
            detail="average a call, the 7 days before"
          />
          <Metric
            label="After"
            value={msValue(after)}
            unit="ms"
            detail={calls == null ? undefined : `over ${plural(calls, 'call')}`}
          />
        </MetricRow>
      )}
      <Note>
        The change is in and this query's time has not moved. Applied keeps it;
        undo it there if it is not worth keeping.
      </Note>
      <History item={item} />
      <Related item={item} />
    </>
  )
}

/** The report's three numbers, each opening its place. */
function reportTiles(
  report: DatabaseReport | null | undefined,
  item: InboxItem
) {
  const fixes = (report?.fix_first ?? []).filter((fix) => fix.state !== 'fixed')
  const share = fixes.reduce((sum, fix) => sum + (fix.share ?? 0), 0)
  const needs = (report?.health ?? []).filter(
    (row) => row.state === 'bad' || row.state === 'warn'
  )
  const checks =
    (report?.health ?? []).length + (report?.health_fine ?? []).length
  const run = report?.run ?? null
  const reportRoute = itemRoute(item)
  return {
    fixes: fixes.length,
    fixesDetail:
      share > 0 ? `${Math.round(share * 100)}% of database time` : undefined,
    needs: needs.length,
    checks,
    needsDetail: needs.length
      ? needs.map((row) => row.label.toLowerCase()).join(', ')
      : undefined,
    analyses: run?.analyses ?? null,
    rewrites: run?.rewrites ?? null,
    ranAt: run?.ran_at ?? item.created_at,
    reportRoute,
  }
}

function ReportReady({
  item,
  actions,
}: {
  item: InboxItem
  actions: InboxActions
}) {
  const report = useReport(item.target, item.source_report_id ?? 'latest')
  const settings = useMonitorSettings(item.target)
  const tiles = reportTiles(report.data, item)
  const nextHour = settings.data?.nightly_hour
  const next =
    nextHour == null
      ? null
      : clock12(new Date(2026, 0, 1, nextHour, 0).getTime() / 1000)
  const fixesRoute: LinkRoute = { to: '/actions', search: { db: item.target } }
  const queriesRoute: LinkRoute = {
    to: '/database',
    search: { target: item.target },
  }
  return (
    <>
      <Title
        item={item}
        actions={actions}
        meta={
          <span>
            {item.target}, run at {clock12(tiles.ranAt)}
            {tiles.analyses != null &&
              `, ${plural(tiles.analyses, 'analysis', 'analyses')}`}
          </span>
        }
      />
      {wholeSentences(item.summary) && (
        <Text as="p" level="body-medium" className="mt-5 text-content-layout-1">
          {wholeSentences(item.summary)}
        </Text>
      )}
      {report.data?.verdict && (
        <Verdict lead={report.data.verdict.lead} className="mt-7">
          {report.data.verdict.rest}
        </Verdict>
      )}
      <MetricRow className="mt-7">
        <Metric
          label="Fixes ready"
          value={
            <RouteLink route={fixesRoute} className={hairlineLink}>
              {tiles.fixes}
            </RouteLink>
          }
          detail={tiles.fixesDetail}
        />
        <Metric
          label="Checks needing you"
          value={
            <RouteLink route={tiles.reportRoute} className={hairlineLink}>
              {tiles.needs}
            </RouteLink>
          }
          unit={tiles.checks ? `of ${tiles.checks}` : undefined}
          detail={tiles.needsDetail}
        />
        <Metric
          label="Queries analyzed"
          value={
            <RouteLink route={queriesRoute} className={hairlineLink}>
              {tiles.analyses ?? DASH}
            </RouteLink>
          }
          detail={
            tiles.rewrites == null
              ? undefined
              : `${plural(tiles.rewrites, 'rewrite')} tested`
          }
        />
      </MetricRow>
      <History item={item} />
      {next && <Note>Next report at {next}.</Note>}
      <Related item={item} />
    </>
  )
}

function FixConfirmed({
  item,
  actions,
}: {
  item: InboxItem
  actions: InboxActions
}) {
  const before = item.before_ms
  const after = item.after_ms
  const faster =
    before != null && after != null && after > 0
      ? Math.floor(before / after)
      : null
  const by = item.resolved_by === 'user' ? 'you' : 'RDST'
  return (
    <>
      <Title
        item={item}
        actions={actions}
        meta={
          <span>
            {before != null && after != null
              ? `${before} ms to ${after} ms, `
              : ''}
            closed {clock12(closedAt(item))} by {by}
          </span>
        }
      />
      {before != null && after != null && (
        <MetricRow className="mt-7">
          <Metric
            label="Before"
            value={before}
            unit="ms"
            detail="average a call"
          />
          <Metric
            label="After"
            value={after}
            unit="ms"
            detail={
              faster != null && item.calls != null
                ? `${faster}x faster over ${plural(item.calls, 'call')}`
                : undefined
            }
          />
        </MetricRow>
      )}
      <History item={item} />
      <Related item={item} />
    </>
  )
}

function Other({ item, actions }: { item: InboxItem; actions: InboxActions }) {
  const steps = (item.advice?.steps ?? []).map(adviceStep)
  return (
    <>
      <Title
        item={item}
        actions={actions}
        meta={
          sameWords(item.summary, item.title) ? null : (
            <span>{item.summary}</span>
          )
        }
      />
      {bandOf(item) !== 'closed' && steps.length > 0 && (
        <Steps steps={steps} note={item.advice?.summary ?? null} />
      )}
      <History item={item} />
      <Related item={item} />
    </>
  )
}

function Body({ item, now, actions }: InboxDetailProps) {
  switch (kindOf(item)) {
    case 'stuck_query':
      return <StuckQuery item={item} now={now} actions={actions} />
    case 'changes_ready':
      return <ChangesReady item={item} actions={actions} />
    case 'no_change_yet':
      return <NoChangeYet item={item} actions={actions} />
    case 'report_ready':
      return <ReportReady item={item} actions={actions} />
    case 'fix_confirmed':
      return <FixConfirmed item={item} actions={actions} />
    default:
      return <Other item={item} actions={actions} />
  }
}

/** The header's actions: one solid at most, ghosts, then the kebab. */
function Actions({
  item,
  actions,
}: {
  item: InboxItem
  actions: InboxActions
}) {
  const kind = kindOf(item)
  const link = itemLink(item)
  const route = itemRoute(item)
  const primary =
    link.kind === 'actions' ? (
      <ActionsLink item={item} actions={actions} className={solidLink}>
        {link.label}
      </ActionsLink>
    ) : (
      <RouteLink route={route} className={solidLink}>
        {link.label}
      </RouteLink>
    )
  if (bandOf(item) === 'closed')
    return (
      <>
        {primary}
        <Button
          label="Reopen"
          modifier="ghost"
          size="small"
          onClick={() => actions.reopen([item])}
        />
        <Kebab item={item} actions={actions} />
      </>
    )
  return (
    <>
      {primary}
      {kind !== 'report_ready' && (
        <Button
          label="Resolve"
          modifier="ghost"
          size="small"
          onClick={() => actions.close([item], { kind: 'resolve' })}
        />
      )}
      <Kebab item={item} actions={actions} />
    </>
  )
}

interface InboxDetailProps {
  item: InboxItem
  now: number
  actions: InboxActions
}

/** The reading panel: the item's band and actions in the header, the item in full below. */
export function InboxDetail({ item, now, actions }: InboxDetailProps) {
  return (
    <section
      aria-label={item.title}
      data-inbox-detail={item.id}
      className="flex h-full min-h-0 flex-col"
    >
      <Header item={item}>
        <Actions item={item} actions={actions} />
      </Header>
      <Scrollable rootClassName="min-h-0 flex-1" className="px-14 pt-10 pb-20">
        <div className="mx-auto max-w-[820px]">
          <Body item={item} now={now} actions={actions} />
        </div>
      </Scrollable>
    </section>
  )
}

/** The detail panel with nothing selected. */
export function InboxDetailEmpty({
  actNow,
  watch,
  closed,
  listEmpty = false,
  onAddDatabase,
}: {
  actNow: number
  watch: number
  /** In the Closed view: how many closed items the list holds. */
  closed?: number
  /** The list beside it is empty, so there is nothing to pick. */
  listEmpty?: boolean
  /** There is no database yet: the pane offers to add one. */
  onAddDatabase?: () => void
}) {
  return (
    <section
      aria-label="Nothing selected"
      className="flex h-full min-h-0 flex-col"
    >
      <div className="flex h-15 shrink-0 items-center border-b border-border-layout-2 pl-6 text-body-small text-content-layout-3 [-webkit-app-region:drag]">
        Nothing selected
      </div>
      {onAddDatabase ? (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            tone="quiet"
            title="No database yet"
            body="Add a database to start. Its alerts show up here."
            action={{ label: 'Add a database', onClick: onAddDatabase }}
          />
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2.5 px-6 text-center text-body-small text-content-layout-3">
          <p className="text-headline-5 font-normal text-content-layout-2">
            {closed == null
              ? emptySentence(actNow, watch)
              : closedSentence(closed)}
          </p>
          {!listEmpty && (
            <p>
              Pick one on the left, or press{' '}
              <kbd className="rounded-sm border border-border-layout-1 px-1 font-mono text-mono-small text-content-layout-2">
                j
              </kbd>
              . Closed items stay 7 days.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
