// The database filter on Home, Inbox, Actions and Ask: a quiet menu button that reads the
// choice in its own label, over All databases and each database with its state and count.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { StatusRipple } from '@rs/ui-new/status'
import type { TargetMonitorLine } from '@/components/TargetMonitorStatus'
import {
  type ConfiguredDatabase,
  useConfiguredDatabases,
} from '@/lib/monitor/hooks'
import type { HomeDatabaseState } from '@/lib/monitor/types'

export type SwitcherTone = 'live' | 'warning' | 'negative' | 'off'

export interface SwitcherState {
  word: string
  tone: SwitcherTone
}

/** The one state word per database: Watching, AI checks paused, Can't reach, Not monitored. */
export function switcherState(
  line: TargetMonitorLine | null | undefined
): SwitcherState | null {
  if (!line) return null
  if (!line.monitored) return { word: 'Not monitored', tone: 'off' }
  if (line.tone === 'negative') return { word: "Can't reach", tone: 'negative' }
  if (!line.ai && line.tone === 'warning' && line.dot === 'live')
    return { word: 'AI checks paused', tone: 'warning' }
  return { word: 'Watching', tone: 'live' }
}

const STILL_DOT: Record<Exclude<SwitcherTone, 'live'>, string> = {
  warning: 'bg-content-warning-plain',
  negative: 'bg-content-negative-plain',
  off: 'bg-content-layout-3',
}

/** The dot beside a state word, in a fixed slot so every word starts on the same x. */
export function StateDot({ tone }: { tone: SwitcherTone }) {
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center">
      {tone === 'live' ? (
        <StatusRipple color="positive" />
      ) : (
        <span
          aria-hidden="true"
          className={cn('h-1.75 w-1.75 rounded-full', STILL_DOT[tone])}
        />
      )}
    </span>
  )
}

export const DATABASE_TONE: Record<HomeDatabaseState, SwitcherTone> = {
  watching: 'live',
  ai_paused: 'warning',
  cant_reach: 'negative',
  not_monitored: 'off',
}

export const DATABASE_WORD: Record<HomeDatabaseState, string> = {
  watching: 'Watching',
  ai_paused: 'AI checks paused',
  cant_reach: "Can't reach",
  not_monitored: 'Not monitored',
}

export const ALL_DATABASES = 'All databases'

/** A filter menu's item, shared by the database and sort menus. */
export const filterItemClass =
  'h-8.5 min-w-0 gap-2.5 rounded-md px-2.5 text-body-small'

export interface DatabaseFilterProps {
  /** The chosen database; null is all of them. */
  value: string | null
  onChange: (db: string | null) => void
  /** This page's count per database; each database's changes to do by default. */
  counts?: Readonly<Record<string, number>>
  /** The databases to offer; every configured database by default. */
  databases?: readonly ConfiguredDatabase[]
  /** Offer "All databases"; without it, no choice reads as the first database. */
  allOption?: boolean
}

/** The chosen item's tick, holding its place when not chosen. */
export function Tick({ on }: { on: boolean }) {
  return (
    <span className="flex w-3.5 shrink-0 justify-end">
      {on && (
        <Icon
          name="tick"
          label="Selected"
          size="small"
          className="text-content-layout-1"
        />
      )}
    </span>
  )
}

export function DatabaseFilter({
  value,
  onChange,
  counts,
  databases,
  allOption = true,
}: DatabaseFilterProps) {
  const configured = useConfiguredDatabases()
  const list = databases ?? configured.databases
  const label = value ?? (allOption ? null : list[0]?.name) ?? ALL_DATABASES
  const countOf = (db: ConfiguredDatabase) =>
    counts ? (counts[db.name] ?? 0) : db.toDo

  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          icon="chevron-down"
          iconPosition="right"
          label={label}
          aria-label={`Database: ${label}`}
          data-testid="database-filter"
        />
      </Dropdown.Trigger>
      <Dropdown.Content
        align="start"
        className="w-60 min-w-60 rounded-panel bg-surface-primary-soft p-1.5"
      >
        {allOption && (
          <>
            <Dropdown.ItemWithChildren
              active={value === null}
              className={filterItemClass}
              onClick={() => onChange(null)}
            >
              <span className="min-w-0 flex-1 truncate">{ALL_DATABASES}</span>
              <Tick on={value === null} />
            </Dropdown.ItemWithChildren>
            {list.length > 0 && <Dropdown.Separator className="-mx-1.5" />}
          </>
        )}
        {list.map((db) => {
          const count = countOf(db)
          return (
            <Dropdown.ItemWithChildren
              key={db.name}
              active={db.name === value}
              className={filterItemClass}
              onClick={() => onChange(db.name)}
            >
              <span className="flex min-w-0 flex-1 items-center gap-2.5">
                <StateDot tone={db.state ? DATABASE_TONE[db.state] : 'off'} />
                <span className="truncate">{db.name}</span>
                <span className="sr-only">
                  {db.state ? DATABASE_WORD[db.state] : ''}
                </span>
                {count != null && (
                  <span className="ml-auto shrink-0 text-caption tabular-nums text-content-layout-3">
                    {count}
                  </span>
                )}
              </span>
              <Tick on={db.name === value} />
            </Dropdown.ItemWithChildren>
          )
        })}
      </Dropdown.Content>
    </Dropdown>
  )
}
