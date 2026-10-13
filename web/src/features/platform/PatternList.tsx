import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useMemo, useState } from 'react'
import type { DiscoveryMode, PatternRow } from '../../lib/useDemo'
import { plural } from '../monitor-kit/format'
import {
  cachedRows,
  canManualCache,
  DASH,
  formatMs,
  isCached,
  POLICY_WORD,
  rowIdentity,
  STATUS_WORD,
  sqlLine,
} from './platformModel'

const ROWS_SHOWN = 6
const COLUMNS = 'minmax(0,1fr) 90px 100px 100px 150px 80px'
const POLICIES: DiscoveryMode[] = ['count_star', 'sum_time']

const hairlineLink =
  'border-b border-border-layout-2 hover:border-content-layout-2'

function policyPhrase(mode: DiscoveryMode): string {
  return mode === 'sum_time' ? 'most expensive' : 'most frequently run'
}

function activePolicy(row: PatternRow, mode: DiscoveryMode): DiscoveryMode {
  const metric = row.reason.metric
  if (metric === 'count_star') return 'count_star'
  if (metric === 'sum_time' || metric === 'sum_time_us') return 'sum_time'
  return mode
}

/** One sentence on why the row has its status under the current policy. */
export function statusReason(
  row: PatternRow,
  mode: DiscoveryMode,
  cacheBudget: number
): string {
  if (row.hits === 0 && row.status === 'not_eligible')
    return 'Waiting for traffic. This query is measured once the workload starts.'
  const top = plural(
    cacheBudget,
    `${policyPhrase(activePolicy(row, mode))} query`,
    `${policyPhrase(activePolicy(row, mode))} queries`
  )
  switch (row.reason.kind) {
    case 'selected':
      return `Cached: one of the top ${top} right now.`
    case 'below_rank':
      return `Not cached: not among the top ${top} right now.`
    case 'below_min_execution':
      return "Not cached yet: this query hasn't run enough times to be considered."
    case 'denylisted':
      return 'Blocked: excluded from automatic caching in this demo.'
    case 'unsupported':
      return "Passes through: Readyset can't cache this SQL shape in the demo."
    case 'manual':
      return 'Cached manually. Turning QueryPilot on drops manual caches and lets it manage caching.'
    default:
      return 'Skipped: not shaped for automatic selection.'
  }
}

function Latency({ value }: { value: number | null }) {
  const text = formatMs(value)
  if (text == null)
    return <span className="text-content-layout-3 opacity-45">{DASH}</span>
  return (
    <>
      {text}
      <span className="ml-1 text-caption text-content-layout-3">ms</span>
    </>
  )
}

function PatternRowView({
  row,
  mode,
  cacheBudget,
  querypilotOn,
  highlighted,
  onCache,
  onUncache,
}: {
  row: PatternRow
  mode: DiscoveryMode
  cacheBudget: number
  querypilotOn: boolean
  highlighted: boolean
  onCache: (key: string, title: string) => void
  onUncache: (key: string, title: string) => void
}) {
  const cached = isCached(row.status)
  const action = cached ? 'Remove' : canManualCache(row) ? 'Cache' : null
  return (
    <ListRow
      open={highlighted}
      data-testid="pattern-row"
      data-key={row.key}
      className="min-h-14"
    >
      <div className="min-w-0">
        <Text
          as="div"
          level="body-medium"
          data-testid="pattern-title"
          className="truncate font-medium text-content-layout-1"
        >
          {row.title}
        </Text>
        <Text
          as="div"
          level="mono-medium"
          className="mt-0.5 truncate text-content-layout-2"
        >
          {sqlLine(row.sql)}
        </Text>
      </div>
      <div className="text-right text-body-small tabular-nums text-content-layout-1">
        {row.hits.toLocaleString()}
      </div>
      <div className="text-right text-body-small tabular-nums text-content-layout-1">
        <Latency value={row.direct_avg_ms} />
      </div>
      <div className="text-right text-body-small tabular-nums text-content-layout-1">
        <Latency value={cached ? row.router_avg_ms : null} />
      </div>
      <div className="text-right">
        <Tooltip>
          <TooltipTrigger asChild>
            <Pressable
              className={cn(
                'cursor-default text-caption',
                cached ? 'text-content-positive-soft' : 'text-content-layout-3'
              )}
            >
              {STATUS_WORD[row.status]}
            </Pressable>
          </TooltipTrigger>
          <TooltipContent label={statusReason(row, mode, cacheBudget)} />
        </Tooltip>
      </div>
      <div className="justify-self-end">
        {action && (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label={action}
            aria-label={`${action} ${row.title}`}
            disabled={querypilotOn}
            onClick={() =>
              action === 'Cache'
                ? onCache(row.key, row.title)
                : onUncache(row.key, row.title)
            }
          />
        )}
      </div>
    </ListRow>
  )
}

export interface PatternListProps {
  patterns: PatternRow[]
  mode: DiscoveryMode
  /** How many queries QueryPilot may cache under the policy. */
  cacheBudget: number
  querypilotOn: boolean
  /** Rows raised in the list while "Cache a query by hand" is the step. */
  highlightedKeys?: string[]
  /** A policy change is in flight: the menu waits for it. */
  busy?: boolean
  onCache: (key: string, title: string) => void
  onUncache: (key: string, title: string) => void
  onPolicy: (mode: DiscoveryMode) => void
}

/** The demo's queries: two latency columns, a status word, Cache / Remove on the row. */
export function PatternList({
  patterns,
  mode,
  cacheBudget,
  querypilotOn,
  highlightedKeys = [],
  busy = false,
  onCache,
  onUncache,
  onPolicy,
}: PatternListProps) {
  const [showAll, setShowAll] = useState(false)
  const sorted = useMemo(() => [...patterns].sort(rowIdentity), [patterns])
  const highlighted = new Set(highlightedKeys)
  const needsAll = sorted.some(
    (row, index) => index >= ROWS_SHOWN && highlighted.has(row.key)
  )
  const rows = showAll || needsAll ? sorted : sorted.slice(0, ROWS_SHOWN)
  const hidden = sorted.length - rows.length
  const cached = cachedRows(patterns).length
  return (
    <section aria-labelledby="platform-queries" className="mt-10">
      <div className="mb-3 flex items-baseline gap-3">
        <h2
          id="platform-queries"
          className="text-headline-5 text-content-layout-1"
        >
          Queries
        </h2>
        <Text as="span" level="caption" className="text-content-layout-3">
          {patterns.length} on orders, {cached} cached
        </Text>
        <div className="ml-auto">
          <Dropdown>
            <Dropdown.Trigger asChild>
              <Button
                label={`Policy: ${POLICY_WORD[mode]}`}
                icon="chevron-down"
                iconPosition="right"
                modifier="ghost"
                size="small"
                disabled={busy}
                aria-label={`Policy: ${POLICY_WORD[mode]}`}
              />
            </Dropdown.Trigger>
            <Dropdown.Content align="end" className="min-w-48">
              {POLICIES.map((option) => (
                <Dropdown.Item
                  key={option}
                  label={`Most ${POLICY_WORD[option].split(' ')[1]}`}
                  active={option === mode}
                  onClick={() => onPolicy(option)}
                />
              ))}
            </Dropdown.Content>
          </Dropdown>
        </div>
      </div>
      <TooltipProvider delayDuration={150}>
        <DividedList
          columns={COLUMNS}
          head={[
            { label: 'Query' },
            { label: 'Calls', align: 'end' },
            { label: 'Postgres', align: 'end' },
            { label: 'Readyset', align: 'end' },
            { label: 'Status', align: 'end' },
            { label: '' },
          ]}
        >
          {rows.map((row) => (
            <PatternRowView
              key={row.key}
              row={row}
              mode={mode}
              cacheBudget={cacheBudget}
              querypilotOn={querypilotOn}
              highlighted={highlighted.has(row.key)}
              onCache={onCache}
              onUncache={onUncache}
            />
          ))}
          {patterns.length === 0 && (
            <Text
              as="p"
              level="body-small"
              className="py-6 text-center text-content-layout-3"
            >
              Workload queries appear once traffic starts.
            </Text>
          )}
        </DividedList>
      </TooltipProvider>
      {hidden > 0 && (
        <p className="mt-3 text-caption text-content-layout-3">
          <Pressable
            onClick={() => setShowAll(true)}
            className={`cursor-pointer ${hairlineLink}`}
          >
            {plural(hidden, 'more query', 'more queries')}
          </Pressable>
        </p>
      )}
    </section>
  )
}
