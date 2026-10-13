import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Dropdown } from '@rs/ui-new/dropdown'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { IconButton } from '@rs/ui-new/icon-button'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import {
  type AgentAction,
  type AgentActivityItem,
  useAgentActivity,
} from '@/lib/agentActivity'
import { useSystemStatus } from '@/lib/useSystemStatus'
import type { AgentActivitySearch } from '@/routes/agent-activity'
import {
  ACTION_META,
  ACTIONS,
  activityLine,
  clockTime,
  dayHeading,
  groupByDay,
  toQuery,
} from './activityModel'
import { ExportDialog } from './ExportDialog'
import { RunningNow } from './RunningNow'

/** The page lists this much by default. */
const DEFAULT_RANGE = '7d'

function FilterMenu({
  label,
  allLabel,
  value,
  options,
  optionLabel = (option) => option,
  onChange,
}: {
  label: string
  allLabel: string
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
      <Dropdown.Content align="end" className="min-w-56">
        <Dropdown.Item
          label={allLabel}
          active={!value}
          rightIcon={!value ? 'tick' : undefined}
          onSelect={() => onChange(undefined)}
        />
        <Dropdown.Separator />
        {options.map((option) => (
          <Dropdown.Item
            key={option}
            label={optionLabel(option)}
            active={value === option}
            rightIcon={value === option ? 'tick' : undefined}
            onSelect={() => onChange(option)}
          />
        ))}
      </Dropdown.Content>
    </Dropdown>
  )
}

function ActivityRow({
  item,
  named,
  removed,
}: {
  item: AgentActivityItem
  /** Several databases in the log: the row says which one. */
  named: boolean
  /** Its database is no longer configured: the row stays, with nowhere to go. */
  removed: boolean
}) {
  const line = activityLine(item)
  const cells = (
    <>
      <span className="text-mono-small tabular-nums text-content-layout-3">
        {clockTime(item.ts)}
      </span>
      <span className="flex min-w-0 flex-wrap items-center gap-2 text-body-small text-content-layout-1">
        {line.text}
        {line.detail && (
          <span
            className={
              line.warn ? 'text-content-warning-soft' : 'text-content-layout-3'
            }
          >
            {line.detail}
          </span>
        )}
        {(named || removed) && item.target && (
          <span className="text-content-layout-3">
            on {item.target}
            {removed && ', removed'}
          </span>
        )}
      </span>
      <span className="text-caption text-content-layout-3">{line.place}</span>
    </>
  )
  if (removed) return <ListRow className="min-h-11.5 py-2">{cells}</ListRow>
  return (
    <ListRow asChild interactive className="min-h-11.5 py-2">
      <Link to={line.link.to as never} search={line.link.search as never}>
        {cells}
      </Link>
    </ListRow>
  )
}

function ActivitySkeleton() {
  return (
    <output
      className="block space-y-3"
      aria-busy="true"
      aria-label="Loading activity"
    >
      <Skeleton className="h-4 w-16 rounded-control" />
      {[0, 1, 2, 3].map((row) => (
        <Skeleton key={row} className="h-10 rounded-control" />
      ))}
    </output>
  )
}

export function AgentActivityPage() {
  const search = useSearch({ from: '/agent-activity' }) as AgentActivitySearch
  const navigate = useNavigate({ from: '/agent-activity' })
  const update = (next: Partial<AgentActivitySearch>) =>
    void navigate({ search: (current) => ({ ...current, ...next }) })
  const [exporting, setExporting] = useState(false)
  // Day boundaries move once a day; a minute is plenty fresh for "Today".
  const now = Math.floor(Date.now() / 60_000) * 60
  const range = search.range ?? DEFAULT_RANGE
  const query = useMemo(
    () => toQuery({ target: search.target, action: search.action, range, now }),
    [search.target, search.action, range, now]
  )
  const activity = useAgentActivity(query)
  const items = useMemo(
    () => activity.data?.pages.flatMap((page) => page.items) ?? [],
    [activity.data]
  )
  const targets = activity.data?.pages[0]?.targets ?? []
  // The configured databases; until they load, every row keeps its link.
  const configured = useSystemStatus().data?.targets?.map((t) => t.name)
  const retention = activity.data?.pages[0]?.retention_days
  const groups = useMemo(() => groupByDay(items, now), [items, now])
  const filtered = Boolean(search.target || search.action)
  const targetOptions =
    search.target && !targets.includes(search.target)
      ? [...targets, search.target]
      : targets

  return (
    <>
      <PageBar
        title="Activity"
        meta={<span>what RDST did</span>}
        actions={
          <>
            <FilterMenu
              label="Database"
              allLabel="All databases"
              value={search.target}
              options={targetOptions}
              onChange={(target) => update({ target })}
            />
            <FilterMenu
              label="Events"
              allLabel="All events"
              value={search.action}
              options={[...ACTIONS]}
              optionLabel={(action) => ACTION_META[action as AgentAction].label}
              onChange={(action) =>
                update({ action: action as AgentAction | undefined })
              }
            />
            <Dropdown>
              <Dropdown.Trigger asChild>
                <IconButton
                  icon="more"
                  label="More actions"
                  modifier="ghost"
                  size="small"
                  tooltip={false}
                />
              </Dropdown.Trigger>
              <Dropdown.Content align="end" className="min-w-48">
                <Dropdown.Item
                  label="Export CSV"
                  onSelect={() => setExporting(true)}
                />
              </Dropdown.Content>
            </Dropdown>
          </>
        }
      />
      <PageContainer className="pt-9 pb-24">
        <RunningNow databases={configured ?? []} />
        {activity.error ? (
          <ErrorState
            tone="quiet"
            errorClass="rdst-service"
            title="Activity could not load"
            message="RDST did not answer. The log is safe; this only affects the view."
            onRetry={() => void activity.refetch()}
          />
        ) : activity.isPending ? (
          <ActivitySkeleton />
        ) : groups.length === 0 ? (
          <EmptyState
            tone="quiet"
            title={filtered ? 'Nothing matches these filters' : 'Nothing yet'}
            body={
              filtered
                ? 'Try another database or event.'
                : 'What RDST does on your databases shows up here.'
            }
          />
        ) : (
          groups.map((group, index) => {
            const heading = dayHeading(group.day, now)
            return (
              <section
                key={group.day}
                aria-label={heading}
                className={cn(index > 0 && 'mt-9')}
              >
                <h2 className="mb-2 text-caption text-content-layout-3">
                  {heading}
                </h2>
                <DividedList columns="72px 1fr auto">
                  {group.items.map((item) => (
                    <ActivityRow
                      key={item.key}
                      item={item}
                      named={targets.length > 1}
                      removed={
                        !!configured &&
                        !!item.target &&
                        !configured.includes(item.target)
                      }
                    />
                  ))}
                </DividedList>
              </section>
            )
          })
        )}
        {activity.hasNextPage && (
          <div className="mt-4">
            <Button
              label="Show older activity"
              modifier="ghost"
              size="small"
              loading={activity.isFetchingNextPage}
              onClick={() => void activity.fetchNextPage()}
            />
          </div>
        )}
        {!activity.isPending && !activity.error && retention != null && (
          <Text as="p" level="caption" className="mt-3.5 text-content-layout-3">
            Kept {retention} days.
          </Text>
        )}
      </PageContainer>
      <ExportDialog
        open={exporting}
        onClose={() => setExporting(false)}
        targets={targetOptions}
        retentionDays={retention}
        initial={{
          target: search.target,
          action: search.action,
          range: search.range ?? DEFAULT_RANGE,
        }}
      />
    </>
  )
}
