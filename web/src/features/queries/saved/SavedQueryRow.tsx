import { cn } from '@rs/tailwind-base'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import type { ReactNode } from 'react'
import { QueryCacheStatus } from '../../../components/QueryCacheStatus'
import { QueryCard, type QueryCardProps } from '../../../components/QueryCard'
import { QueryCardImpact } from '../../../components/QueryCardImpact'
import { SQLInput } from '../../../components/SQLInput'
import { SqlTokens } from '../../../components/SqlTokens'
import {
  formatMeta,
  formatMs,
  formatTimestamp,
  shortHash,
} from '../../../lib/formatters'
import { OBSERVED_EVIDENCE_PROVENANCE } from '../../../lib/queryEvidence'
import { queryDisplayName } from '../../../lib/queryIdentity'
import {
  formatDbTime,
  formatImpactCaption,
  formatRunCount,
  isNotCacheable,
  queryImpactMs,
} from '../../../lib/queryImpact'
import { detectParameters } from '../../../lib/sqlParameters'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import type {
  QueryLibraryDisplayMode,
  QueryLibraryDisplayProperty,
} from '../library/queryLibraryDisplay'
import { SavedQueryDetails } from './SavedQueryDetails'
import { SavedQueryMenu } from './SavedQueryMenu'
import { getSourceMeta } from './savedQuerySelectors'
import type { SavedQueriesController } from './useSavedQueriesController'

interface SavedQueryRowProps {
  entry: QueryRegistryEntry
  target?: string | null
  state: SavedQueriesController['rowState']
  actions: SavedQueriesController['rowActions']
  displayMode?: QueryLibraryDisplayMode
  visibleProperties?: QueryLibraryDisplayProperty[]
  /** Disable the row's own entrance/exit motion inside a shared-layout owner. */
  animateEntry?: boolean
  /** Optional presentation seam for isolated previews and consumers. */
  renderCard?: (props: QueryCardProps) => ReactNode
}

function QueryImpactMetric({
  icon,
  label,
  value,
}: {
  icon: 'database' | 'observe'
  label: string
  value: string
}) {
  return (
    <HStack className="min-w-0 items-center gap-2">
      <Icon
        name={icon}
        label=""
        aria-hidden="true"
        className="h-4 w-4 shrink-0 text-content-layout-3"
      />
      <Text level="caption" className="truncate text-content-layout-3">
        {label}
      </Text>
      <Text
        level="mono-small"
        className="ml-auto shrink-0 text-content-layout-1"
      >
        {value}
      </Text>
    </HStack>
  )
}

function QueryImpactRail({ entry }: { entry: QueryRegistryEntry }) {
  const runs = entry.observation_count ?? entry.frequency ?? 0

  return (
    <VStack
      title={OBSERVED_EVIDENCE_PROVENANCE}
      className="h-full min-h-44 items-stretch justify-between gap-6"
    >
      <VStack className="items-start gap-1">
        <Text level="caption" className="text-content-layout-3">
          Database time
        </Text>
        <Text level="headline-1" className="text-content-layout-1 tabular-nums">
          {formatDbTime(queryImpactMs(entry))}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          Across the captured workload
        </Text>
      </VStack>
      <VStack className="items-stretch gap-3">
        <QueryImpactMetric
          icon="database"
          label="Observed runs"
          value={runs.toLocaleString()}
        />
        <QueryImpactMetric
          icon="observe"
          label="Avg latency"
          value={formatMs(entry.avg_duration_ms ?? 0)}
        />
      </VStack>
    </VStack>
  )
}

function parameterSummary(entry: QueryRegistryEntry) {
  const count = detectParameters(entry.original_sql || entry.sql).length
  if (count === 0) return 'no parameters'
  const ready = Object.keys(entry.most_recent_params ?? {}).length
  return ready >= count
    ? `${count} ${count === 1 ? 'parameter' : 'parameters'} ready`
    : `${ready}/${count} parameters ready`
}

function latestActivity(entry: QueryRegistryEntry) {
  const values = [
    entry.last_observed_at,
    entry.last_analyzed_at,
    entry.last_compared_at,
    entry.last_analyzed,
  ].filter((value): value is string => Boolean(value))
  if (values.length === 0) return null
  return values.reduce((latest, value) =>
    Date.parse(value) > Date.parse(latest) ? value : latest
  )
}

export function SavedQueryRow({
  entry,
  target,
  state,
  actions,
  displayMode = 'card-1',
  visibleProperties,
  animateEntry = true,
  renderCard,
}: SavedQueryRowProps) {
  const sourceMeta = getSourceMeta(entry.source)
  const displayName = queryDisplayName(entry)
  const isExpanded = state.expandedHash === entry.hash
  const isHighlighted = state.highlightedHash === entry.hash
  const isConfirmingDelete = state.confirmingHash === entry.hash
  const isRenaming = state.editingHash === entry.hash
  const isEditingSql = state.editingSqlHash === entry.hash
  const isDefaultState = !isConfirmingDelete && !isRenaming && !isEditingSql
  const cached = actions.isCached(entry.hash)
  const notCacheable = isNotCacheable(entry.readyset_supported)
  const cacheTestRun = actions.cacheRunFor(entry.hash)
  const isTesting =
    cacheTestRun?.status === 'running' ||
    cacheTestRun?.status === 'reconnecting'
  const runResult = cacheTestRun?.result
  const impactCaption = formatImpactCaption(entry)
  const runCount = formatRunCount(entry)
  const visible = visibleProperties
    ? new Set<QueryLibraryDisplayProperty>(visibleProperties)
    : null
  const showProperty = (property: QueryLibraryDisplayProperty) =>
    visible === null || visible.has(property)
  const activity = latestActivity(entry)
  const evidenceMeta = formatMeta([
    showProperty('impact') ? impactCaption : null,
    showProperty('frequency') ? runCount : null,
    showProperty('frequency') && !impactCaption
      ? entry.frequency > 0
        ? `${entry.frequency} ${entry.frequency === 1 ? 'run' : 'runs'}`
        : 'never run'
      : null,
    showProperty('impact') && (entry.avg_duration_ms ?? 0) > 0
      ? `avg ${formatMs(entry.avg_duration_ms)}`
      : null,
  ])
  const detailMeta = formatMeta([
    showProperty('parameters') ? parameterSummary(entry) : null,
    showProperty('activity') && activity
      ? `active ${formatTimestamp(activity).toLowerCase()}`
      : null,
    `hash ${shortHash(entry.hash)}`,
    entry.target || null,
  ])
  const meta = formatMeta([evidenceMeta || null, detailMeta || null])
  // Same text as `meta`, with the observed-evidence half carrying its
  // provenance note on hover.
  const metaContent = evidenceMeta ? (
    <>
      <span title={OBSERVED_EVIDENCE_PROVENANCE}>{evidenceMeta}</span>
      {detailMeta ? ` · ${detailMeta}` : null}
    </>
  ) : (
    meta
  )
  const impactMeta = formatMeta([
    showProperty('parameters') ? parameterSummary(entry) : null,
    showProperty('activity') && activity
      ? `active ${formatTimestamp(activity).toLowerCase()}`
      : null,
    `hash ${shortHash(entry.hash)}`,
    entry.target || null,
  ])
  const sourceBadge = (
    <Tag
      size="small"
      variant={sourceMeta.variant}
      modifier="ghost"
      label={sourceMeta.label}
    />
  )
  const defaultTitle = (
    <Text
      level="label-medium"
      className="text-content-layout-1 font-semibold truncate"
    >
      {displayName}
    </Text>
  )
  const title = isConfirmingDelete ? (
    <HStack className="gap-2 items-center">
      <Icon
        name="alert"
        label=""
        aria-hidden="true"
        className="w-4 h-4 text-content-negative-soft shrink-0"
      />
      <Text
        level="label-medium"
        className="text-content-layout-1 font-semibold"
      >
        Delete this query?
      </Text>
    </HStack>
  ) : isRenaming ? (
    <div className="min-w-60 flex-1">
      <BaseInputText
        name={`edit-tag-${entry.hash}`}
        placeholder="Enter name"
        value={state.tagDraft}
        onChange={(event) => actions.setTagDraft(event.target.value)}
      />
    </div>
  ) : (
    defaultTitle
  )
  const badges = isConfirmingDelete ? (
    <Tag size="small" variant="negative" modifier="ghost" label={displayName} />
  ) : isDefaultState ? (
    <>
      {entry.is_new ? (
        <Tag size="small" variant="rising" modifier="solid" label="New" />
      ) : null}
      {isHighlighted ? (
        <Tag
          size="small"
          variant="informative"
          modifier="solid"
          label="Selected query"
        />
      ) : null}
      {showProperty('source') ? sourceBadge : null}
      <QueryCacheStatus
        cached={cached}
        readysetSupported={entry.readyset_supported}
        testing={isTesting}
        speedup={runResult?.speedup_mean}
      />
    </>
  ) : (
    sourceBadge
  )
  const secondaryActions = isConfirmingDelete ? (
    <Button
      variant="primary"
      modifier="ghost"
      size="small"
      label="Cancel"
      onClick={actions.cancelDelete}
    />
  ) : isRenaming ? (
    <Button
      variant="primary"
      modifier="ghost"
      size="small"
      label="Cancel"
      onClick={actions.cancelRename}
    />
  ) : isEditingSql ? (
    <Button
      variant="primary"
      modifier="ghost"
      size="small"
      label="Cancel"
      onClick={actions.cancelEditSql}
    />
  ) : (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              icon="speedometer"
              iconPosition="left"
              label="Analyze"
              onClick={() =>
                actions.analyze(
                  entry.sql,
                  entry.target,
                  entry.most_recent_params
                )
              }
            />
          </div>
        </TooltipTrigger>
        <TooltipContent label="Analyze this query" />
      </Tooltip>
    </TooltipProvider>
  )
  const primaryAction = isConfirmingDelete ? (
    <Button
      variant="negative"
      modifier="solid"
      size="small"
      label="Delete"
      icon="trash"
      iconPosition="left"
      onClick={() => actions.deleteQuery(entry.hash)}
    />
  ) : isRenaming ? (
    <Button
      variant="rising"
      modifier="solid"
      size="small"
      icon="tick"
      iconPosition="left"
      label="Save"
      onClick={() => actions.rename(entry.hash, state.tagDraft.trim())}
    />
  ) : isEditingSql ? (
    <Button
      variant="rising"
      modifier="solid"
      size="small"
      label="Save"
      icon="tick"
      iconPosition="left"
      onClick={() => actions.saveSql(entry.hash)}
      loading={actions.updateSqlPending}
      disabled={!state.sqlDraft.trim()}
    />
  ) : (
    <>
      <Show when={!cached && !notCacheable && !isTesting}>
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <div>
                <Button
                  variant="rising"
                  modifier="solid"
                  size="small"
                  icon="database-settings"
                  iconPosition="left"
                  label="Compare & test"
                  loading={actions.cachingHash === entry.hash}
                  onClick={() => actions.cacheQuery(entry.hash, entry.sql)}
                />
              </div>
            </TooltipTrigger>
            <TooltipContent label="Run a short upstream vs Readyset comparison" />
          </Tooltip>
        </TooltipProvider>
      </Show>
      <Show when={cached && !isTesting}>
        <Button
          variant="rising"
          modifier="ghost"
          size="small"
          icon="database-settings"
          iconPosition="left"
          label={runResult ? 'Re-test' : 'Test'}
          onClick={() =>
            actions.runTest(
              entry.hash,
              entry.sql,
              entry.most_recent_params ?? {}
            )
          }
        />
      </Show>
    </>
  )

  if (displayMode === 'rows' && isDefaultState) {
    return (
      <m.div
        initial={animateEntry ? { opacity: 0, y: -4 } : false}
        animate={animateEntry ? { opacity: 1, y: 0 } : undefined}
        exit={animateEntry ? { opacity: 0, x: -12 } : undefined}
        transition={animateEntry ? { duration: 0.16 } : undefined}
      >
        <Card
          data-testid="query-registry-row"
          data-query-hash={entry.hash}
          className={cn(
            'p-0 overflow-hidden',
            isHighlighted &&
              'scroll-mt-28 bg-surface-primary-soft ring-2 ring-border-primary-soft ring-offset-2 ring-offset-surface-layout-1 shadow-elevation-2'
          )}
        >
          <Card.Content className="grid min-h-20 min-w-0 grid-cols-1 items-center gap-3 overflow-hidden rounded-none border-0 bg-transparent px-4 py-3 laptop:grid-cols-[minmax(0,0.8fr)_minmax(0,1.4fr)_minmax(0,1fr)_auto]">
            <HStack className="min-w-0 items-center gap-2">
              <div className="min-w-0">{defaultTitle}</div>
              <HStack className="shrink-0 gap-1.5">{badges}</HStack>
            </HStack>

            <div className="min-w-0 overflow-hidden font-mono text-mono-small text-content-layout-2 [mask-image:linear-gradient(to_right,black_85%,transparent)] [&>code]:truncate [&>code]:whitespace-nowrap">
              <SqlTokens
                sql={entry.sql.replace(/\s+/g, ' ')}
                title={entry.sql}
              />
            </div>

            <div className="min-w-0" title={meta}>
              <Text
                level="mono-small"
                className="truncate text-content-layout-3"
              >
                {metaContent}
              </Text>
            </div>

            <HStack className="items-center justify-end gap-1.5">
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Details"
                onClick={() => actions.toggleExpanded(entry.hash)}
                aria-expanded={isExpanded}
              />
              {secondaryActions}
              {primaryAction}
              <SavedQueryMenu
                onEditSql={() => actions.startEditSql(entry.hash, entry.sql)}
                onRename={() =>
                  actions.startRename(entry.hash, entry.tag || '')
                }
                onMarkReviewed={
                  entry.is_new
                    ? () => actions.markReviewed(entry.hash)
                    : undefined
                }
                onDelete={() => actions.confirmDelete(entry.hash)}
              />
            </HStack>
          </Card.Content>

          {isExpanded ? (
            <Card.Content className="px-4 py-3">
              <SavedQueryDetails
                entry={entry}
                cacheTestRun={cacheTestRun}
                onDismissRun={actions.dismissRun}
                onClose={() => actions.toggleExpanded(entry.hash)}
                onViewAnalysis={() =>
                  actions.analyze(
                    entry.sql,
                    entry.target,
                    entry.most_recent_params
                  )
                }
              />
            </Card.Content>
          ) : null}
        </Card>
      </m.div>
    )
  }

  return (
    <m.div
      initial={animateEntry ? { opacity: 0, y: -6 } : false}
      animate={animateEntry ? { opacity: 1, y: 0 } : undefined}
      exit={animateEntry ? { opacity: 0, x: -16 } : undefined}
      transition={animateEntry ? { duration: 0.18 } : undefined}
    >
      {(() => {
        const cardProps: QueryCardProps = {
          'data-testid': 'query-registry-row',
          'data-query-hash': entry.hash,
          sql: entry.sql,
          title,
          badges,
          meta: displayMode === 'card-2' ? impactMeta : metaContent,
          highlighted: isHighlighted,
          className: cn(
            isConfirmingDelete && 'ring-1 ring-border-negative-soft'
          ),
          editor: isEditingSql ? (
            <SQLInput
              value={state.sqlDraft}
              onChange={actions.setSqlDraft}
              placeholder="Edit SQL query..."
              minHeight="10rem"
              target={entry.target || target}
              showPrettify
            />
          ) : undefined,
          detailsOpen: isDefaultState && isExpanded,
          onToggleDetails: isDefaultState
            ? () => actions.toggleExpanded(entry.hash)
            : undefined,
          menu: isDefaultState ? (
            <SavedQueryMenu
              onEditSql={() => actions.startEditSql(entry.hash, entry.sql)}
              onRename={() => actions.startRename(entry.hash, entry.tag || '')}
              onMarkReviewed={
                entry.is_new
                  ? () => actions.markReviewed(entry.hash)
                  : undefined
              }
              onDelete={() => actions.confirmDelete(entry.hash)}
            />
          ) : undefined,
          secondaryActions,
          primaryAction,
          expansion:
            isDefaultState && isExpanded ? (
              <SavedQueryDetails
                entry={entry}
                cacheTestRun={cacheTestRun}
                onDismissRun={actions.dismissRun}
                onClose={() => actions.toggleExpanded(entry.hash)}
                onViewAnalysis={() =>
                  actions.analyze(
                    entry.sql,
                    entry.target,
                    entry.most_recent_params
                  )
                }
              />
            ) : undefined,
        }

        if (renderCard) return renderCard(cardProps)
        if (displayMode === 'card-2') {
          return (
            <QueryCardImpact
              {...cardProps}
              rail={<QueryImpactRail entry={entry} />}
            />
          )
        }
        return <QueryCard {...cardProps} />
      })()}
    </m.div>
  )
}
