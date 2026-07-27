import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useMemo, useState } from 'react'
import {
  groupHistoryByDay,
  HISTORY_PAGE_SIZE,
  type HistoryEntry,
} from '../../lib/auditHistory'
import { formatSecondsShort, formatTimestamp } from '../../lib/formatters'

const ABSOLUTE_TIMESTAMP_FORMAT = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

function formatAbsoluteTimestamp(iso: string): string {
  if (!iso) return 'Unknown date'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return ABSOLUTE_TIMESTAMP_FORMAT.format(date)
}

function fleetTargetsLabel(names: string[], count?: number): string {
  if (names.length === 0) {
    return count != null
      ? `${count} target${count === 1 ? '' : 's'}`
      : 'Saved fleet targets'
  }
  const visible = names.slice(0, 3)
  const remaining = names.length - visible.length
  return `${visible.join(', ')}${remaining > 0 ? `, +${remaining} more` : ''}`
}

function SectionCard({
  icon,
  title,
  children,
}: {
  icon: IconStrokeName
  title: string
  children: React.ReactNode
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="gap-2 items-center">
            <Icon
              name={icon}
              label={title}
              className="w-4 h-4 text-content-layout-3"
            />
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              {title}
            </Text>
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  )
}

function HistoryRow({
  entry,
  active,
  loading,
  onOpen,
}: {
  entry: HistoryEntry
  active: boolean
  loading: boolean
  onOpen: (entry: HistoryEntry) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(entry)}
      className={`group w-full text-left px-4 py-2 hover:bg-surface-layout-2/50 transition-colors cursor-pointer ${
        active ? 'bg-surface-primary-soft/10' : ''
      }`}
    >
      <HStack className="justify-between items-center gap-4">
        <VStack className="gap-1 items-start min-w-0">
          <HStack className="gap-2 items-center min-w-0 flex-wrap">
            <Tag
              size="small"
              variant={entry.kind === 'fleet' ? 'primary' : 'informative'}
              modifier="ghost"
              label={entry.kind === 'fleet' ? 'Fleet' : 'Single'}
            />
            <Text
              level="label-medium"
              className="text-content-layout-1 truncate"
            >
              {entry.scopeLabel}
            </Text>
          </HStack>
          <Text level="caption" className="text-content-layout-2 truncate">
            {entry.kind === 'fleet'
              ? `Fleet health check · ${fleetTargetsLabel(entry.targetNames, entry.targetsAudited)} · ${entry.durationSeconds > 0 ? `${formatSecondsShort(entry.durationSeconds)} window` : 'metrics only'}`
              : `Health check · ${entry.engine || 'database'} · ${entry.durationSeconds > 0 ? `${formatSecondsShort(entry.durationSeconds)} window` : 'metrics only'}`}
          </Text>
          <HStack className="gap-2 items-baseline flex-wrap">
            <Text level="caption" className="text-content-layout-3">
              {formatAbsoluteTimestamp(entry.startedAt)}
            </Text>
            <Text level="caption" className="text-content-layout-3">
              {formatTimestamp(entry.startedAt)}
            </Text>
          </HStack>
        </VStack>
        <HStack className="gap-2 items-center shrink-0">
          {loading && <Spinner size="base" />}
          {entry.queryCount != null && entry.queryCount > 0 && (
            <Text
              level="caption"
              className="text-content-layout-3 tabular-nums"
            >
              {entry.queryCount} {entry.queryCount === 1 ? 'query' : 'queries'}
            </Text>
          )}
          {entry.hasAnalysis && (
            <Tag
              size="small"
              variant="positive"
              modifier="ghost"
              label="Analyzed"
            />
          )}
          <Icon
            name="chevron-right"
            label="Open run"
            className="w-4 h-4 text-content-layout-3 opacity-0 group-hover:opacity-100 transition-opacity"
          />
        </HStack>
      </HStack>
    </button>
  )
}

/**
 * Unified history of single-target runs and fleet runs, grouped by day and
 * paginated ("Load more" reveals the next page) so a long history never dumps
 * the reader at the bottom of the page.
 */
export function RunHistory({
  entries,
  activeId,
  loadingId,
  onOpen,
}: {
  entries: HistoryEntry[]
  activeId: string | null
  loadingId: string | null
  onOpen: (entry: HistoryEntry) => void
}) {
  const [visible, setVisible] = useState(HISTORY_PAGE_SIZE)
  const shown = useMemo(() => entries.slice(0, visible), [entries, visible])
  const groups = useMemo(() => groupHistoryByDay(shown), [shown])
  const remaining = entries.length - shown.length

  if (entries.length === 0) return null

  return (
    <SectionCard icon="folder-file" title={`Reports (${entries.length})`}>
      {groups.map((group) => (
        <div key={group.day}>
          <div className="px-5 py-1.5 bg-surface-layout-2/30 border-y border-border-layout-1 first:border-t-0">
            <Text
              level="caption"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              {group.day}
            </Text>
          </div>
          <div className="divide-y divide-border-layout-1">
            {group.entries.map((entry) => (
              <HistoryRow
                key={`${entry.kind}-${entry.id}`}
                entry={entry}
                active={activeId === entry.id}
                loading={loadingId === entry.id}
                onOpen={onOpen}
              />
            ))}
          </div>
        </div>
      ))}
      <Show when={remaining > 0}>
        <div className="px-5 py-3 border-t border-border-layout-1">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label={`Load more (${remaining} older)`}
            onClick={() => setVisible((value) => value + HISTORY_PAGE_SIZE)}
          />
        </div>
      </Show>
      <Show when={entries.length > HISTORY_PAGE_SIZE}>
        <div className="px-5 py-3 border-t border-border-layout-1 flex justify-end">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Back to top"
            icon="chevron-up"
            iconPosition="left"
            onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
          />
        </div>
      </Show>
    </SectionCard>
  )
}
