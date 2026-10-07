import { cn } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Dropdown } from '@rs/ui-new/dropdown'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { type ReactNode, useMemo, useState } from 'react'
import { SqlTokens } from '@/components/SqlTokens'
import { WorkspaceLayout } from '@/components/workspace/WorkspaceLayout'
import {
  type ActivityQuery,
  type AgentAction,
  type AgentActivityItem,
  type AgentAiCall,
  type AgentStatement,
  useAgentActivity,
  useAgentActivityEntry,
} from '@/lib/agentActivity'
import type { AgentActivitySearch } from '@/routes/agent-activity'
import {
  ACTION_META,
  ACTIONS,
  activityNote,
  allEntriesQuery,
  expandQuery,
  formatMs,
  formatTokens,
  groupByDay,
  itemStyle,
  itemTime,
  outcomeTag,
  plainReason,
  plural,
  purposeLabel,
  RANGE_LABEL,
  type RangePreset,
  reportDay,
  sentenceParts,
  statementKindLabel,
  statementTiming,
  type Tone,
  technicalSummary,
  toQuery,
} from './activityModel'
import { ExportDialog } from './ExportDialog'

const CARD =
  'overflow-hidden rounded-card border border-border-layout-1 bg-surface-layout-1 shadow-elevation-1'

const TONE_TILE: Record<Tone, string> = {
  rising: 'bg-surface-rising-soft text-content-rising-plain',
  info: 'bg-surface-info-soft text-content-info-plain',
  positive: 'bg-surface-positive-soft text-content-positive-plain',
  warning: 'bg-surface-warning-soft text-content-warning-plain',
  primary: 'bg-surface-primary-soft text-content-primary-plain',
  neutral: 'bg-surface-layout-2 text-content-layout-2',
}

export function AgentActivityPage() {
  const search = useSearch({ from: '/agent-activity' }) as AgentActivitySearch
  const navigate = useNavigate({ from: '/agent-activity' })
  const update = (next: Partial<AgentActivitySearch>) =>
    void navigate({ search: (current) => ({ ...current, ...next }) })
  const [exporting, setExporting] = useState(false)
  // Day boundaries move once a day; a minute is plenty fresh for "Today".
  const now = Math.floor(Date.now() / 60_000) * 60
  const query = useMemo(
    () =>
      toQuery({
        target: search.target,
        action: search.action,
        range: search.range,
        now,
      }),
    [search.target, search.action, search.range, now]
  )
  const activity = useAgentActivity(query)
  const items = useMemo(
    () => activity.data?.pages.flatMap((page) => page.items) ?? [],
    [activity.data]
  )
  const targets = activity.data?.pages[0]?.targets ?? []
  const groups = useMemo(() => groupByDay(items, now), [items, now])
  const filtered = Boolean(search.target || search.action || search.range)
  const targetOptions =
    search.target && !targets.includes(search.target)
      ? [...targets, search.target]
      : targets

  return (
    <WorkspaceLayout
      title="Agent audit"
      description="What the RDST agent decided and did on your databases, newest first."
      icon="user-shield"
      panelId="agent-activity-panel"
      headerActions={
        <div className="ml-auto flex items-center">
          <Button
            label="Export CSV"
            icon="arrow-down"
            iconPosition="left"
            modifier="outline"
            onClick={() => setExporting(true)}
          />
        </div>
      }
    >
      <div className="space-y-6">
        <TrustNote />
        <div
          role="toolbar"
          aria-label="Filter agent activity"
          className="flex flex-wrap items-center gap-2"
        >
          <FilterMenu
            label="Time"
            allLabel={RANGE_LABEL.all}
            icon="observe"
            value={search.range}
            options={['today', '7d', '30d']}
            optionLabel={(range) => RANGE_LABEL[range as RangePreset]}
            onChange={(range) =>
              update({ range: range as AgentActivitySearch['range'] })
            }
          />
          <FilterMenu
            label="Database"
            allLabel="All databases"
            icon="database"
            value={search.target}
            options={targetOptions}
            onChange={(target) => update({ target })}
          />
          <FilterMenu
            label="Action"
            allLabel="All actions"
            icon="filter"
            value={search.action}
            options={[...ACTIONS]}
            optionLabel={(action) => ACTION_META[action as AgentAction].label}
            onChange={(action) =>
              update({ action: action as AgentAction | undefined })
            }
          />
        </div>
        {activity.error ? (
          <ErrorState
            errorClass="rdst-service"
            title="Couldn't load agent activity"
            message="RDST didn't answer. The log is safe; this only affects the view."
            onRetry={() => void activity.refetch()}
          />
        ) : activity.isPending ? (
          <ActivitySkeleton />
        ) : groups.length === 0 ? (
          <EmptyState
            icon="user-shield"
            title={
              filtered
                ? 'Nothing matches these filters'
                : 'No agent activity yet'
            }
            body={
              filtered
                ? 'Try another time range, database or action.'
                : 'When RDST analyzes a query, answers a question or checks your database, it shows up here.'
            }
          />
        ) : (
          <div className="space-y-8">
            {groups.map((group) => (
              <section
                key={group.day}
                aria-label={group.label}
                className="space-y-3"
              >
                <div className="flex items-baseline gap-2 px-1">
                  <Text
                    as="h2"
                    level="subtitle-1"
                    className="text-content-layout-1"
                  >
                    {group.label}
                  </Text>
                  {group.date && (
                    <Text
                      as="span"
                      level="body-small"
                      className="text-content-layout-3"
                    >
                      {group.date}
                    </Text>
                  )}
                </div>
                <div
                  className={cn(
                    CARD,
                    '[&>*+*]:border-t [&>*+*]:border-border-layout-1'
                  )}
                >
                  {group.items.map((item) => (
                    <ActivityRow
                      key={item.key}
                      item={item}
                      showTarget={!search.target}
                    />
                  ))}
                </div>
              </section>
            ))}
            {activity.hasNextPage && (
              <div className="flex justify-center">
                <Button
                  label="Show older activity"
                  modifier="ghost"
                  loading={activity.isFetchingNextPage}
                  onClick={() => void activity.fetchNextPage()}
                />
              </div>
            )}
          </div>
        )}
      </div>
      <ExportDialog
        open={exporting}
        onClose={() => setExporting(false)}
        targets={targetOptions}
        initial={{
          target: search.target,
          action: search.action,
          range: search.range ?? '7d',
        }}
      />
    </WorkspaceLayout>
  )
}

const PROMISES: { icon: IconStrokeName; title: string; body: string }[] = [
  {
    icon: 'lock-sync',
    title: 'Only reads',
    body: 'RDST reads plans and statistics and runs SELECTs. It never changes your data or schema.',
  },
  {
    icon: 'speedometer',
    title: 'EXPLAIN ANALYZE is bounded',
    body: 'RDST stops read-only statements after 30 s, or 10 min when you start the analysis.',
  },
  {
    icon: 'eye',
    title: 'No result rows go to the AI',
    body: "It sees SQL, plans, schema and sampled values. This log doesn't keep the values.",
  },
]

function TrustNote() {
  // On a phone the promises read as titles; their detail opens on request.
  const [open, setOpen] = useState(false)
  return (
    <section
      aria-label="What RDST does on your database"
      className="rounded-card border border-border-layout-1 bg-surface-layout-2/40 px-4 py-3.5 tablet:px-5"
    >
      <ul className="grid gap-x-8 gap-y-2.5 tablet:grid-cols-3">
        {PROMISES.map((promise) => (
          <li key={promise.title} className="flex gap-2.5">
            <Icon
              name={promise.icon}
              label=""
              aria-hidden="true"
              className="mt-0.5 h-4 w-4 shrink-0 text-content-positive-plain"
            />
            <div className="min-w-0">
              <Text
                as="p"
                level="label-medium"
                className="text-content-layout-1"
              >
                {promise.title}
              </Text>
              <Text
                as="p"
                level="body-small"
                className={cn(
                  'mt-0.5 text-content-layout-2',
                  !open && 'hidden tablet:block'
                )}
              >
                {promise.body}
              </Text>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-2.5 flex items-baseline justify-between gap-3">
        <Text
          as="p"
          level="caption"
          className={cn(
            'text-content-layout-3',
            !open && 'hidden tablet:block'
          )}
        >
          The once-a-minute statistics read and the look at running sessions for
          parameter values aren't listed one by one.
        </Text>
        <Pressable
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
          className="shrink-0 text-label-small text-content-layout-2 hover:text-content-layout-1 tablet:hidden"
        >
          {open ? 'Show less' : 'Show details'}
        </Pressable>
      </div>
    </section>
  )
}

function FilterMenu({
  label,
  allLabel,
  icon,
  value,
  options,
  optionLabel = (option) => option,
  onChange,
}: {
  label: string
  allLabel: string
  icon: IconStrokeName
  value: string | undefined
  options: string[]
  optionLabel?: (option: string) => string
  onChange: (value: string | undefined) => void
}) {
  const current = value ? optionLabel(value) : allLabel
  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <Button
          label={current}
          icon="chevron-down"
          iconPosition="right"
          modifier="ghost"
          size="small"
          className={cn(value && 'text-content-layout-1')}
          aria-label={`${label}: ${current}`}
        />
      </Dropdown.Trigger>
      <Dropdown.Content align="start" className="min-w-56">
        <Dropdown.Item
          label={allLabel}
          leftIcon={icon}
          active={!value}
          rightIcon={!value ? 'tick' : undefined}
          onSelect={() => onChange(undefined)}
        />
        <Dropdown.Separator />
        {options.map((option) => (
          <Dropdown.Item
            key={option}
            label={optionLabel(option)}
            leftIcon={icon}
            active={value === option}
            rightIcon={value === option ? 'tick' : undefined}
            onSelect={() => onChange(option)}
          />
        ))}
      </Dropdown.Content>
    </Dropdown>
  )
}

function ToneTile({
  icon,
  tone,
  small = false,
}: {
  icon: IconStrokeName
  tone: Tone
  small?: boolean
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex shrink-0 items-center justify-center rounded-control',
        small ? 'h-6 w-6' : 'h-8 w-8',
        TONE_TILE[tone]
      )}
    >
      <Icon
        name={icon}
        label=""
        className={small ? 'h-3.5 w-3.5' : 'h-4 w-4'}
      />
    </span>
  )
}

function ActivityRow({
  item,
  showTarget,
  nested = false,
}: {
  item: AgentActivityItem
  showTarget: boolean
  nested?: boolean
}) {
  const [open, setOpen] = useState(false)
  const style = itemStyle(item)
  const tag = outcomeTag(item)
  const note = activityNote(item)
  const panelId = `activity-${item.key.replaceAll(/[^\w-]/g, '-')}`
  const meta = [itemTime(item), showTarget && item.target ? item.target : null]
    .filter(Boolean)
    .join(' · ')
  return (
    <div data-activity-key={item.key}>
      <Pressable
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-controls={panelId}
        className={cn(
          'group flex w-full cursor-pointer items-start gap-3 text-left transition-colors duration-fast hover:bg-surface-raised',
          nested ? 'px-3 py-2.5' : 'px-4 py-3.5 tablet:px-5',
          open && 'bg-surface-raised/60'
        )}
      >
        <ToneTile icon={style.icon} tone={style.tone} small={nested} />
        <div className="min-w-0 flex-1 space-y-0.5">
          <Text
            as="p"
            level={nested ? 'body-small' : 'body-medium'}
            className="text-content-layout-1"
          >
            {sentenceParts(item, nested).map((part, index) =>
              part.strong ? (
                <span key={index} className="font-medium">
                  {part.text}
                </span>
              ) : (
                <span key={index}>{part.text}</span>
              )
            )}
          </Text>
          <Text
            as="p"
            level="caption"
            className="tabular-nums text-content-layout-3"
          >
            {meta}
            {note && (
              <span
                className={cn(
                  item.outcome === 'failed' ||
                    (item.failed > 0 && item.failed === item.count)
                    ? 'text-content-negative-plain'
                    : item.failed > 0
                      ? 'text-content-warning-plain'
                      : 'text-content-layout-3'
                )}
              >
                {' · '}
                {note}
              </span>
            )}
          </Text>
        </div>
        {tag && (
          <Tag
            label={tag.label}
            variant={tag.variant}
            modifier="outline"
            size="small"
            className="mt-0.5 whitespace-nowrap"
          />
        )}
        <Icon
          name="chevron-right"
          label=""
          aria-hidden="true"
          className={cn(
            'mt-1 h-4 w-4 shrink-0 text-content-layout-3 transition-transform duration-fast group-hover:text-content-layout-1',
            open && 'rotate-90'
          )}
        />
      </Pressable>
      {open && (
        <div
          id={panelId}
          className={cn(
            'pb-4',
            nested
              ? 'pr-3 pl-3 tablet:pl-12'
              : 'pr-4 pl-4 tablet:pr-5 tablet:pl-16'
          )}
        >
          {item.kind === 'rollup' ? (
            <RollupDetail item={item} />
          ) : (
            <EntryDetail item={item} />
          )}
        </div>
      )}
    </div>
  )
}

function NestedList({ query, empty }: { query: ActivityQuery; empty: string }) {
  const entries = useAgentActivity(query)
  const rows = entries.data?.pages.flatMap((page) => page.items) ?? []
  if (entries.isPending) return <Skeleton className="h-20 rounded-panel" />
  if (entries.error)
    return (
      <ErrorState
        errorClass="rdst-service"
        title="Couldn't load these entries"
        message="RDST didn't answer. Try again in a moment."
        onRetry={() => void entries.refetch()}
      />
    )
  if (rows.length === 0)
    return (
      <Text level="body-small" className="text-content-layout-3">
        {empty}
      </Text>
    )
  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-panel border border-border-layout-1 bg-surface-layout-1 [&>*+*]:border-t [&>*+*]:border-border-layout-1">
        {rows.map((row) => (
          <ActivityRow key={row.key} item={row} showTarget={false} nested />
        ))}
      </div>
      {entries.hasNextPage && (
        <Button
          label="Show more"
          modifier="ghost"
          size="small"
          loading={entries.isFetchingNextPage}
          onClick={() => void entries.fetchNextPage()}
        />
      )}
    </div>
  )
}

const FOLD_EXPLAINER = {
  attention_check:
    'Each check compares the latest query statistics with their usual level and, when something changed, asks the AI whether it needs your attention.',
  classification:
    'The AI scores each new query from its SQL text and schema, so the Database page can show what to look at first.',
  skipped_analysis:
    "Deep analysis only runs statements that can't change anything. These were writes, schema changes or statements that failed the read-only check.",
} as const

function RollupDetail({ item }: { item: AgentActivityItem }) {
  const aside = item.count - item.done - item.failed
  return (
    <div className="space-y-3">
      {item.fold && item.group === 'day' && (
        <Text as="p" level="body-small" className="text-content-layout-2">
          {FOLD_EXPLAINER[item.fold]}
        </Text>
      )}
      <NestedList query={expandQuery(item)} empty="Nothing to list." />
      {item.fold === 'classification' && item.done > 0 && aside > 0 && (
        <Text as="p" level="caption" className="text-content-layout-3">
          {plural(aside, 'more query', 'more queries')} set aside before any AI
          call.
        </Text>
      )}
      {item.fold === 'classification' && (
        <TechnicalDisclosure
          summary={`${plural(item.count, 'attempt')}: ${technicalSummary(item).toLowerCase()}`}
        >
          <NestedList
            query={allEntriesQuery(item)}
            empty="No entries were kept."
          />
        </TechnicalDisclosure>
      )}
    </div>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 tablet:grid-cols-[8rem_1fr] tablet:gap-4">
      <dt>
        <Text as="span" level="label-small" className="text-content-layout-3">
          {label}
        </Text>
      </dt>
      <dd>
        <Text as="span" level="body-small" className="text-content-layout-1">
          {children}
        </Text>
      </dd>
    </div>
  )
}

function EntryFacts({ item }: { item: AgentActivityItem }) {
  const facts: { label: string; value: ReactNode }[] = []
  const reason = plainReason(item.summary)
  if (item.action === 'ask' && item.question)
    facts.push({ label: 'Question', value: item.question })
  if (item.action === 'ask' && item.subject)
    facts.push({ label: 'Tables', value: item.subject })
  if (item.action === 'daily_snapshot') {
    const { day, partial } = reportDay(item)
    facts.push({ label: 'Covers', value: partial ? `${day}, so far` : day })
  }
  if (item.decision)
    facts.push({
      label: item.action === 'deep_analysis' ? 'How' : 'Decision',
      value: item.decision,
    })
  if (item.parameters)
    facts.push({
      label: 'Parameters',
      value: `${item.parameters}. The values aren't kept in this log.`,
    })
  if (item.outcome !== 'done' && reason)
    facts.push({
      label: item.outcome === 'skipped' ? 'Why' : 'What happened',
      value: `${reason}.`,
    })
  if (item.action === 'benchmark' && item.summary && item.outcome === 'done')
    facts.push({ label: 'Result', value: item.summary })
  if (facts.length === 0) return null
  return (
    <dl className="space-y-2">
      {facts.map((fact) => (
        <Fact key={fact.label} label={fact.label}>
          {fact.value}
        </Fact>
      ))}
    </dl>
  )
}

function EntryLinks({ item }: { item: AgentActivityItem }) {
  if (
    item.action === 'deep_analysis' &&
    item.query_key &&
    item.outcome === 'done'
  )
    return (
      <Link
        to="/database"
        search={
          {
            query: item.query_key,
            target: item.target,
            tab: 'analysis',
          } as never
        }
        className="inline-flex items-center gap-1 text-label-medium text-content-info-plain hover:underline"
      >
        Open the analysis
        <Icon
          name="arrow-right"
          label=""
          aria-hidden="true"
          className="h-4 w-4"
        />
      </Link>
    )
  if (
    item.action === 'daily_snapshot' &&
    item.report_id != null &&
    item.outcome === 'done'
  )
    return (
      <Link
        to="/database"
        search={
          {
            view: 'report',
            report: item.report_id,
            target: item.target,
          } as never
        }
        className="inline-flex items-center gap-1 text-label-medium text-content-info-plain hover:underline"
      >
        Open the report
        <Icon
          name="arrow-right"
          label=""
          aria-hidden="true"
          className="h-4 w-4"
        />
      </Link>
    )
  return null
}

function TechnicalDisclosure({
  summary,
  children,
}: {
  summary: string
  children: ReactNode
}) {
  return (
    <Disclosure
      title="Technical details"
      subtitle={summary}
      className="bg-surface-layout-1"
    >
      {children}
    </Disclosure>
  )
}

function EntryDetail({ item }: { item: AgentActivityItem }) {
  return (
    <div className="space-y-4" data-testid="activity-detail">
      <EntryFacts item={item} />
      <EntryLinks item={item} />
      <TechnicalDisclosure summary={technicalSummary(item)}>
        <TechnicalDetail id={item.id} />
      </TechnicalDisclosure>
    </div>
  )
}

function DetailSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section aria-label={title} className="space-y-2">
      <Text as="h3" level="label-medium" className="text-content-layout-2">
        {title}
      </Text>
      {children}
    </section>
  )
}

function TechnicalDetail({ id }: { id: number }) {
  const entry = useAgentActivityEntry(id, true)
  if (entry.isPending) return <Skeleton className="h-24 rounded-panel" />
  if (entry.error || !entry.data)
    return (
      <ErrorState
        errorClass="rdst-service"
        title="Couldn't load this entry"
        message="RDST didn't answer. Try again in a moment."
        onRetry={() => void entry.refetch()}
      />
    )
  const data = entry.data
  const note = typeof data.details.note === 'string' ? data.details.note : null
  return (
    <div className="space-y-5">
      <DetailSection
        title={`Statements on your database (${data.statements.length})`}
      >
        {data.statements.length === 0 ? (
          <Text level="body-small" className="text-content-layout-3">
            {note ?? 'No statements ran on your database.'}
          </Text>
        ) : (
          <div className="space-y-3">
            {data.statements.map((statement, index) => (
              <StatementBlock key={index} statement={statement} />
            ))}
            {typeof data.details.statements_not_listed === 'number' && (
              <Text level="caption" className="text-content-layout-3">
                {plural(data.details.statements_not_listed, 'more statement')}{' '}
                ran but aren't listed.
              </Text>
            )}
          </div>
        )}
      </DetailSection>
      <DetailSection title={`AI calls (${data.ai_calls.length})`}>
        {data.ai_calls.length === 0 ? (
          <Text level="body-small" className="text-content-layout-3">
            {data.ai_call_count
              ? `${plural(data.ai_call_count, 'AI call')} billed; details weren't recorded before this log existed.`
              : 'No AI calls.'}
          </Text>
        ) : (
          <div className="space-y-2">
            {data.ai_calls.map((call, index) => (
              <AiCallRow key={index} call={call} />
            ))}
          </div>
        )}
      </DetailSection>
    </div>
  )
}

function StatementBlock({ statement }: { statement: AgentStatement }) {
  const timing = statementTiming(statement)
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <Text level="label-small" className="text-content-layout-2">
          {statementKindLabel(statement.kind)}
        </Text>
        {timing && (
          <Text level="caption" className="tabular-nums text-content-layout-3">
            {timing}
          </Text>
        )}
      </div>
      <div className="relative overflow-hidden rounded-control border border-border-layout-1 bg-surface-layout-2/40">
        <div className="max-h-48 overflow-auto px-3 pt-2 pb-2 pr-28">
          <SqlTokens sql={statement.sql} className="text-mono-small" />
        </div>
        <div className="absolute top-1 right-1">
          <CopyButton text={statement.sql} label="Copy SQL" />
        </div>
      </div>
      {statement.error && (
        <Text level="caption" className="text-content-negative-plain">
          {statement.error}
        </Text>
      )}
    </div>
  )
}

function AiCallRow({ call }: { call: AgentAiCall }) {
  return (
    <div className="rounded-control border border-border-layout-1 bg-surface-layout-2/40 px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <Text level="label-medium" className="text-content-layout-1">
          {purposeLabel(call)}
        </Text>
        <Text level="caption" className="tabular-nums text-content-layout-3">
          {[
            formatTokens(call),
            call.duration_ms ? formatMs(call.duration_ms) : null,
          ]
            .filter(Boolean)
            .join(', ')}
        </Text>
      </div>
      <Text level="body-small" className="mt-0.5 text-content-layout-2">
        {call.model}
        {call.data_kinds.length > 0 && `, sent ${call.data_kinds.join(', ')}`}
      </Text>
      {call.error && (
        <Text level="caption" className="text-content-negative-plain">
          Didn't complete: {call.error}
        </Text>
      )}
    </div>
  )
}

function ActivitySkeleton() {
  return (
    <output
      className="block space-y-3"
      aria-busy="true"
      aria-label="Loading agent activity"
    >
      <Skeleton className="h-5 w-24 rounded-control" />
      <div className="space-y-px overflow-hidden rounded-card border border-border-layout-1">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} className="h-16 rounded-none" />
        ))}
      </div>
    </output>
  )
}
