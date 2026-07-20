/**
 * Ranked list of slow queries (region C of the redesign — the primary content).
 *
 * Each row is the canonical QueryCard: the full syntax-highlighted SQL leads, and
 * the per-query metrics fold into ONE muted meta line beneath it — `hash · freq ·
 * total · avg · load` historically, swapping to the `seen · max · avg · load · qps`
 * variant in realtime. The footer carries the row actions: Analyze is the one
 * visible per-row action, Cache lives in a `⋯` overflow. The results header carries
 * the count, the Sort lens, the Save-automatically preference and Save all.
 * [VIS-011, VIS-016, VIS-017, VIS-110, VIS-119, VIS-127, USE-008]
 */

import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { Label } from '@rs/ui-new/label'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useMemo } from 'react'
import { formatMeta, shortHash } from '../../lib/formatters'
import type { TopQuery, TopState } from '../../types/top'
import { QueryCard } from '../QueryCard'

interface TopQueryTableProps {
  queries: TopQuery[]
  state: TopState
  isRealtime: boolean
  onAnalyze: (query: TopQuery) => void
  onCache?: (query: TopQuery) => void
  cachingHash?: string | null
  isCached?: (registryHash: string) => boolean
  /** Results-header controls (region C). */
  sort?: string
  setSort?: (sort: string) => void
  onSaveAll?: () => void
  canSave?: boolean
  autoSave?: boolean
  setAutoSave?: (autoSave: boolean) => void
  /** The single primary CTA shown in the idle empty state. */
  idleAction?: ReactNode
}

const sortOptions = [
  { value: 'total_time', label: 'Total time' },
  { value: 'freq', label: 'Frequency' },
  { value: 'avg_time', label: 'Avg time' },
  { value: 'load', label: 'Load %' },
]

function EmptyState({
  message,
  icon,
  title,
  action,
  gradient = false,
}: {
  message: string
  icon: 'observe' | 'speedometer' | 'folder-file'
  title?: string
  action?: ReactNode
  gradient?: boolean
}) {
  return (
    <Card className="w-full border-transparent shadow-elevation-1">
      <Card.Content className="py-16">
        <VStack className="gap-4 items-center">
          <div
            className={
              gradient
                ? 'w-14 h-14 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-warning-soft flex items-center justify-center'
                : 'w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center'
            }
          >
            <Icon
              name={icon}
              label="Empty"
              className={
                gradient
                  ? 'w-7 h-7 text-content-primary-soft'
                  : 'w-7 h-7 text-content-layout-3'
              }
            />
          </div>
          <VStack className="gap-1 items-center">
            <Show when={!!title}>
              <Text
                level="subtitle-1"
                className="text-content-layout-1 text-center"
              >
                {title}
              </Text>
            </Show>
            <Text
              level="body-small"
              className="text-content-layout-3 text-center max-w-md"
            >
              {message}
            </Text>
          </VStack>
          <Show when={!!action}>
            <div className="mt-2">{action}</div>
          </Show>
        </VStack>
      </Card.Content>
    </Card>
  )
}

/** Per-row `⋯` overflow — demotes Cache out of the primacy slot next to Analyze. */
function RowOverflowMenu({
  query,
  onCache,
  caching,
  cached,
}: {
  query: TopQuery
  onCache?: (query: TopQuery) => void
  caching: boolean
  cached: boolean
}) {
  if (!onCache) return null

  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <button
          type="button"
          aria-label="More actions"
          className="w-8 h-8 rounded-lg flex items-center justify-center text-content-layout-3 hover:text-content-layout-1 hover:bg-surface-layout-2 transition-colors cursor-pointer"
        >
          <Icon name="more" label="More actions" className="w-4 h-4" />
        </button>
      </Dropdown.Trigger>
      <Dropdown.Content align="end" className="min-w-52">
        <Dropdown.Item
          leftIcon="database-settings"
          label={cached ? 'Cached' : caching ? 'Caching…' : 'Cache query'}
          disabled={cached || caching}
          onClick={() => onCache(query)}
        />
      </Dropdown.Content>
    </Dropdown>
  )
}

export function TopQueryTable({
  queries,
  state,
  isRealtime,
  onAnalyze,
  onCache,
  cachingHash,
  isCached,
  sort,
  setSort,
  onSaveAll,
  canSave,
  autoSave,
  setAutoSave,
  idleAction,
}: TopQueryTableProps) {
  // Per-row view-models: the hash, the max-latency text, the one muted meta line
  // and the cached flag derive only from the row data, the realtime toggle and
  // the cached-SQL set behind `isCached` — never from the parent's transient
  // state (sort menu, auto-save, streaming ticks). Memoized so an unrelated
  // re-render doesn't rebuild meta strings for up to 200 rows. `isCached` is a
  // stable useCallback keyed on the cache set, so listing it as a dep recomputes
  // exactly when a cache op lands, and no more often. [PS5 item 7b]
  const rowViewModels = useMemo(
    () =>
      queries.map((query) => {
        const hash8 = shortHash(query.query_hash)
        const maxText =
          query.max_duration_ms != null
            ? `${query.max_duration_ms.toFixed(1)}ms`
            : query.total_time
        // The whole metric block collapses into one muted stat line under the
        // SQL — labels folded into values, dot-separated, the owner's approved
        // format. Realtime swaps freq→seen, total→max and appends QPS; the
        // "N running" status stays a top badge. [triage §1.1 /top; USE-002/003
        // fold labels, VIS-011 quiet secondary]
        const meta = isRealtime
          ? formatMeta([
              `hash ${hash8}`,
              `seen ${query.observation_count ?? query.freq}`,
              `max ${maxText}`,
              `avg ${query.avg_time}`,
              `load ${query.pct_load}`,
              query.qps != null ? `qps ${query.qps.toFixed(2)}` : null,
            ])
          : formatMeta([
              `hash ${hash8}`,
              `freq ${query.freq}`,
              `total ${query.total_time}`,
              `avg ${query.avg_time}`,
              `load ${query.pct_load}`,
            ])
        return {
          query,
          hasRunning: (query.current_instances_running ?? 0) > 0,
          meta,
          cached: !!isCached?.(query.query_hash),
        }
      }),
    [queries, isRealtime, isCached]
  )

  if (state === 'idle') {
    return (
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <EmptyState
          gradient
          icon="observe"
          message="No results yet — let's find your slowest, heaviest queries."
          action={idleAction}
        />
      </m.div>
    )
  }

  if (state === 'loading') {
    return (
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <EmptyState
          icon="speedometer"
          message="Reading your database's query statistics…"
        />
      </m.div>
    )
  }

  if (state === 'error') {
    return null
  }

  if (queries.length === 0 && (state === 'complete' || state === 'streaming')) {
    return (
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <EmptyState
          icon="folder-file"
          message="No queries matched. Loosen the filters or wait for more traffic."
        />
      </m.div>
    )
  }

  const activeSort = sort ?? 'total_time'
  const sortLabel =
    sortOptions.find((o) => o.value === activeSort)?.label ?? 'Total time'
  const showSort = !!setSort && !isRealtime

  return (
    <m.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.2 }}
    >
      <Card className="w-full overflow-hidden border-transparent shadow-elevation-2">
        <Card.Content className="p-0">
          {/* Results header — count + sort lens + save controls (grouped with the list). */}
          <div className="px-5 py-3 border-b border-border-layout-1">
            <HStack className="justify-between items-center gap-3 flex-wrap">
              <Text level="label-medium" className="text-content-layout-2">
                {queries.length} quer{queries.length === 1 ? 'y' : 'ies'}
              </Text>

              <HStack className="gap-3 items-center flex-wrap">
                <Show when={showSort}>
                  <Dropdown>
                    <Dropdown.Trigger asChild>
                      <button
                        type="button"
                        className="flex items-center gap-1.5 px-2.5 h-8 rounded-lg text-button-small text-content-layout-2 hover:text-content-layout-1 hover:bg-surface-layout-2 transition-colors cursor-pointer"
                      >
                        <span className="text-content-layout-3">Sort:</span>
                        <span>{sortLabel}</span>
                        <Icon
                          name="chevron-down"
                          label="Change sort"
                          className="w-3.5 h-3.5"
                        />
                      </button>
                    </Dropdown.Trigger>
                    <Dropdown.Content align="end" className="min-w-44">
                      {/* Sort is a next-run request param, not an instant
                          client-side re-sort — say so at the point of choice so
                          the control doesn't read as a live lens. [C-09] */}
                      <Dropdown.Label>Applies to the next run</Dropdown.Label>
                      {sortOptions.map((option) => (
                        <Dropdown.Item
                          key={option.value}
                          label={option.label}
                          active={option.value === activeSort}
                          rightIcon={
                            option.value === activeSort ? 'tick' : undefined
                          }
                          onClick={() => setSort?.(option.value)}
                        />
                      ))}
                    </Dropdown.Content>
                  </Dropdown>
                </Show>

                <Show when={!!setAutoSave}>
                  <HStack className="gap-2 items-center">
                    <BaseInputSwitch
                      id="top-auto-save"
                      name="auto-save"
                      checked={!!autoSave}
                      onCheckedChange={(checked) =>
                        setAutoSave?.(checked === true)
                      }
                      disabled={state === 'streaming'}
                    />
                    <Label
                      htmlFor="top-auto-save"
                      className="text-label-small text-content-layout-2"
                    >
                      Save automatically
                    </Label>
                  </HStack>
                </Show>

                <Show when={!!canSave && !!onSaveAll}>
                  <Button
                    variant="primary"
                    modifier="outline"
                    size="small"
                    label="Save all"
                    icon="add"
                    iconPosition="left"
                    onClick={onSaveAll}
                  />
                </Show>
              </HStack>
            </HStack>
          </div>

          {/* Query rows — stacked canonical cards (ref 21.47.45 anatomy), compact
              density for this dense list; the results header above stays intact
              (count + Sort "applies to next run" + auto-save + Save all = C-05/C-09). */}
          <div className="p-3 space-y-3 bg-surface-layout-1">
            <AnimatePresence mode="popLayout">
              {rowViewModels.map((row, index) => {
                const { query, hasRunning, meta, cached } = row

                return (
                  <m.div
                    key={query.query_hash}
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 10 }}
                    transition={{ duration: 0.2, delay: index * 0.03 }}
                  >
                    <QueryCard
                      data-testid="top-query-row"
                      data-query-hash={query.query_hash}
                      className={
                        hasRunning
                          ? 'ring-1 ring-border-positive-soft'
                          : undefined
                      }
                      sql={query.query_text}
                      leading={
                        <Text
                          as="span"
                          level="mono-small"
                          className="text-content-layout-3 tabular-nums pt-0.5 shrink-0 w-5 text-right"
                        >
                          {index + 1}
                        </Text>
                      }
                      badges={
                        hasRunning ? (
                          <Tag
                            size="small"
                            variant="positive"
                            modifier="solid"
                            label={`${query.current_instances_running} running`}
                          />
                        ) : undefined
                      }
                      meta={meta}
                      actions={
                        <>
                          <Button
                            variant="primary"
                            modifier="ghost"
                            size="small"
                            icon="speedometer"
                            iconPosition="left"
                            label="Analyze"
                            onClick={() => onAnalyze(query)}
                          />
                          <RowOverflowMenu
                            query={query}
                            onCache={onCache}
                            caching={cachingHash === query.query_hash}
                            cached={cached}
                          />
                        </>
                      }
                    />
                  </m.div>
                )
              })}
            </AnimatePresence>
          </div>
        </Card.Content>
      </Card>
    </m.div>
  )
}
