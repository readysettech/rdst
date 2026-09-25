import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useEffect } from 'react'
import { formatMeta, shortHash } from '../../../lib/formatters'
import { queryDisplayName } from '../../../lib/queryIdentity'
import { detectParameters } from '../../../lib/sqlParameters'
import {
  PerformanceCacheabilityNote,
  PerformanceQueryCard,
  PerformanceQueryList,
  PerformanceQueryParameters,
} from '../shared/PerformanceQueryList'
import { SuggestValuesPanel } from '../shared/SuggestValuesPanel'
import { CompareSummaryRow, compareBatchDurationEstimate } from './compareUi'
import {
  type CompareController,
  MAX_COMPARE_CONCURRENCY,
  MAX_COMPARE_QUERIES,
} from './useCompareController'

function adaptiveLoadLabel(controller: CompareController) {
  return controller.concurrency < MAX_COMPARE_CONCURRENCY
    ? `${controller.concurrency} → ${MAX_COMPARE_CONCURRENCY}`
    : `${MAX_COMPARE_CONCURRENCY}`
}

function QuerySelector({ controller }: { controller: CompareController }) {
  const allSelected =
    controller.queries.length > 0 &&
    controller.selectedIds.length ===
      Math.min(controller.queries.length, MAX_COMPARE_QUERIES)
  const selectionLimitReached =
    controller.selectedIds.length >= MAX_COMPARE_QUERIES

  return (
    <VStack className="min-w-0 items-stretch gap-3 desktop:h-full desktop:min-h-0">
      <HStack className="flex-wrap items-center justify-between gap-4">
        <VStack className="items-start gap-0.5">
          <Text level="label-small" className="text-content-layout-1">
            Queries
          </Text>
          <Text level="caption" className="text-content-layout-3">
            Select up to {MAX_COMPARE_QUERIES}. RDST checks cacheability for
            each query when the comparison starts.
          </Text>
        </VStack>
        <Button
          size="small"
          variant="primary"
          modifier="ghost"
          label={allSelected ? 'Clear all' : 'Select all'}
          onClick={controller.selectAll}
        />
      </HStack>

      <PerformanceQueryList ariaLabel="Queries available for comparison">
        {controller.queries.map((entry) => {
          const selected = controller.selectedIds.includes(entry.hash)
          const disabled = !selected && selectionLimitReached
          const selection = controller.selectedWithParams.find(
            (item) => item.entry.hash === entry.hash
          )
          const parameters =
            selection?.parameters ?? detectParameters(entry.sql)
          return (
            <PerformanceQueryCard
              key={entry.hash}
              queryHash={entry.hash}
              selected={selected}
              disabled={disabled}
              onSelect={() => controller.toggleQuery(entry.hash)}
              title={queryDisplayName(entry)}
              sql={entry.sql}
              parameterCount={parameters.length}
              meta={
                <>
                  <Text level="caption" className="text-content-layout-3">
                    {formatMeta([
                      `hash ${shortHash(entry.hash)}`,
                      entry.target || controller.target,
                    ])}
                  </Text>
                  <PerformanceCacheabilityNote
                    readysetSupported={entry.readyset_supported}
                    checkedAt={entry.readyset_last_observed_at}
                  />
                </>
              }
              parameterContent={
                selection && selection.parameters.length > 0 ? (
                  <PerformanceQueryParameters
                    ownerId={entry.hash}
                    inputPrefix="compare"
                    parameters={selection.parameters}
                    values={controller.paramValues}
                    sources={controller.parameterSources}
                    onValueChange={(parameter, value) =>
                      controller.updateParameter(
                        entry.hash,
                        parameter.placeholder,
                        parameter.index,
                        value
                      )
                    }
                  />
                ) : undefined
              }
            />
          )
        })}
      </PerformanceQueryList>
    </VStack>
  )
}

function InlineSetup({ controller }: { controller: CompareController }) {
  const selection = controller.selectedWithParams[0]
  const readiness =
    controller.parameterCount === 0
      ? 'no parameters'
      : controller.missingParameterCount === 0
        ? `${controller.parameterCount} ${controller.parameterCount === 1 ? 'parameter' : 'parameters'} ready`
        : `${controller.missingParameterCount} ${controller.missingParameterCount === 1 ? 'parameter' : 'parameters'} missing`
  return (
    <VStack className="items-stretch gap-3">
      {selection && selection.parameters.length > 0 ? (
        <div className="overflow-hidden rounded-panel border border-border-layout-1 [&>div]:border-t-0">
          <PerformanceQueryParameters
            ownerId={selection.entry.hash}
            inputPrefix="compare-inline"
            parameters={selection.parameters}
            values={controller.paramValues}
            sources={controller.parameterSources}
            onValueChange={(parameter, value) =>
              controller.updateParameter(
                selection.entry.hash,
                parameter.placeholder,
                parameter.index,
                value
              )
            }
          />
        </div>
      ) : null}
      <SuggestValuesPanel
        hasParameters={controller.parameterCount > 0}
        suggesting={controller.suggestingParameters}
        missingParameterCount={controller.missingParameterCount}
        message={controller.suggestionMessage}
        schemaUnavailable={controller.suggestionSchemaUnavailable}
        onSuggest={() => void controller.suggestParameterValues()}
      />
      <HStack className="flex-wrap items-center justify-between gap-3">
        <Text
          level="caption"
          className={
            controller.residualQueryHash
              ? 'text-content-negative-soft'
              : 'text-content-layout-3'
          }
        >
          {controller.residualQueryHash
            ? 'A parameter value did not apply. Re-enter it and try again.'
            : (controller.blockedReason ??
              `Safe load, ${adaptiveLoadLabel(controller)} clients per lane · ${controller.durationSeconds} seconds · ${readiness}`)}
        </Text>
        <Button
          variant="primary"
          modifier="solid"
          size="small"
          label="Compare against Readyset"
          icon="play"
          iconPosition="left"
          disabled={!controller.canReview}
          onClick={() => controller.setReviewOpen(true)}
        />
      </HStack>
    </VStack>
  )
}

/**
 * The selected queries only, each with its parameters, then the run summary and
 * the start button in a footer that stays in view. The caller owns choosing the
 * queries (the Database page's row checkboxes) and passes its own heading.
 */
function SelectionSetup({
  controller,
  heading,
}: {
  controller: CompareController
  heading?: ReactNode
}) {
  const count = controller.selectedWithParams.length
  const batchCost = compareBatchDurationEstimate(
    count,
    controller.durationSeconds
  )
  const ready =
    count === 0
      ? 'No queries selected'
      : controller.parameterCount === 0
        ? 'No parameters'
        : controller.missingParameterCount === 0
          ? `${controller.parameterCount} ready`
          : `${controller.missingParameterCount} missing`
  return (
    <VStack className="items-stretch gap-5">
      {heading}
      {count > 0 ? (
        <VStack
          className="items-stretch gap-3"
          aria-label="Queries to compare"
          role="group"
        >
          {controller.selectedWithParams.map(({ entry, parameters }) => (
            <PerformanceQueryCard
              key={entry.hash}
              queryHash={entry.hash}
              selected
              onSelect={() => controller.toggleQuery(entry.hash)}
              title={queryDisplayName(entry)}
              sql={entry.sql}
              parameterCount={parameters.length}
              meta={
                <>
                  <Text level="caption" className="text-content-layout-3">
                    {`hash ${shortHash(entry.hash)}`}
                  </Text>
                  <PerformanceCacheabilityNote
                    readysetSupported={entry.readyset_supported}
                    checkedAt={entry.readyset_last_observed_at}
                  />
                </>
              }
              parameterContent={
                parameters.length > 0 ? (
                  <PerformanceQueryParameters
                    ownerId={entry.hash}
                    inputPrefix="compare"
                    parameters={parameters}
                    values={controller.paramValues}
                    sources={controller.parameterSources}
                    onValueChange={(parameter, value) =>
                      controller.updateParameter(
                        entry.hash,
                        parameter.placeholder,
                        parameter.index,
                        value
                      )
                    }
                  />
                ) : undefined
              }
            />
          ))}
        </VStack>
      ) : null}
      <VStack className="items-stretch gap-3">
        <dl className="grid overflow-hidden rounded-panel border border-border-layout-1 bg-surface-layout-1 divide-border-layout-1 tablet:grid-cols-3 tablet:divide-x max-tablet:divide-y">
          {[
            {
              label: 'Load',
              value: `Safe, ${adaptiveLoadLabel(controller)} clients per lane`,
              tone: 'neutral' as const,
            },
            {
              label: 'Duration',
              value: batchCost ?? `${controller.durationSeconds} seconds`,
              tone: 'neutral' as const,
            },
            {
              label: 'Parameters',
              value: ready,
              tone:
                count === 0
                  ? ('neutral' as const)
                  : controller.missingParameterCount > 0
                    ? ('warning' as const)
                    : ('positive' as const),
            },
          ].map((item) => (
            <div key={item.label} className="min-w-0 px-4 py-3">
              <dt>
                <Text
                  as="span"
                  level="caption"
                  className="text-content-layout-3"
                >
                  {item.label}
                </Text>
              </dt>
              <dd>
                <Text
                  as="span"
                  level="label-small"
                  className={
                    item.tone === 'positive'
                      ? 'text-content-positive-soft'
                      : item.tone === 'warning'
                        ? 'text-content-warning-soft'
                        : 'text-content-layout-1'
                  }
                >
                  {item.value}
                </Text>
              </dd>
            </div>
          ))}
        </dl>
        {controller.parameterCount > 0 ? (
          <SuggestValuesPanel
            hasParameters
            suggesting={controller.suggestingParameters}
            missingParameterCount={controller.missingParameterCount}
            message={controller.suggestionMessage}
            schemaUnavailable={controller.suggestionSchemaUnavailable}
            onSuggest={() => void controller.suggestParameterValues()}
          />
        ) : null}
      </VStack>
      <HStack className="sticky bottom-0 z-10 -mx-5 flex-wrap items-center justify-between gap-3 border-t border-border-layout-1 bg-surface-layout-2 px-5 py-4">
        <Text
          level="caption"
          className={
            controller.residualQueryHash
              ? 'mr-auto min-w-0 text-content-negative-soft'
              : 'mr-auto min-w-0 text-content-layout-3'
          }
        >
          {controller.residualQueryHash
            ? 'A parameter value did not apply. Re-enter the highlighted value and try again.'
            : (controller.blockedReason ??
              `Runs against ${controller.target} and a temporary Readyset cache, read-only.`)}
        </Text>
        <HStack className="items-center gap-2">
          {controller.historyEntries.length > 0 ? (
            <Button
              size="small"
              variant="primary"
              modifier="ghost"
              label={`History ${controller.historyEntries.length}`}
              icon="observe"
              onClick={() => controller.setHistoryOpen(true)}
            />
          ) : null}
          <Button
            variant="primary"
            modifier="solid"
            label="Compare against Readyset"
            icon="play"
            iconPosition="left"
            disabled={!controller.canReview}
            onClick={() => controller.setReviewOpen(true)}
          />
        </HStack>
      </HStack>
    </VStack>
  )
}

export function CompareSetup({
  controller,
  layout = 'page',
  heading,
}: {
  controller: CompareController
  /**
   * `inline` runs one preselected query from inside another page (the Database
   * query detail): no picker, header or history; its parameters, the run
   * summary and the start button. `selection` shows the queries another page
   * selected (the Database benchmark drawer), each removable, with the run
   * summary, history and the start button.
   */
  layout?: 'page' | 'inline' | 'selection'
  /** `selection` only: rendered above the selected queries. */
  heading?: ReactNode
}) {
  // The sandbox admits one comparison at a time, so a four-query batch is a
  // two-minute wait. Say so before the click, not during the wait.
  const batchCost = compareBatchDurationEstimate(
    controller.selectedIds.length,
    controller.durationSeconds
  )
  // Readiness only means something once queries are chosen: with an empty
  // selection the row reads neutral rather than claiming a green "ready".
  const nothingSelected = controller.selectedIds.length === 0
  const ready = nothingSelected
    ? 'No queries selected'
    : controller.parameterCount === 0
      ? 'No parameters'
      : controller.missingParameterCount === 0
        ? `${controller.parameterCount} ready`
        : `${controller.missingParameterCount} missing`

  // A parameter value that didn't apply cleanly must not run silently as
  // broken SQL; steer the user straight back to the offending query's
  // inline parameter fields instead.
  useEffect(() => {
    if (!controller.residualQueryHash) return
    const card = document.querySelector<HTMLElement>(
      `[data-query-hash="${controller.residualQueryHash}"]`
    )
    card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const input = card?.querySelector<HTMLInputElement>('input')
    input?.focus()
  }, [controller.residualQueryHash])

  const reviewDialog = (
    <ConfirmDialog
      isOpen={controller.reviewOpen}
      onClose={() => controller.setReviewOpen(false)}
      onConfirm={() => void controller.startComparison()}
      title={
        controller.targetIsRemote
          ? `Compare against ${controller.target}?`
          : 'Start this comparison?'
      }
      subtitle={
        batchCost
          ? `${batchCost} · ${adaptiveLoadLabel(controller)} clients per lane`
          : `${controller.selectedIds.length} ${
              controller.selectedIds.length === 1 ? 'query' : 'queries'
            } · ${adaptiveLoadLabel(controller)} clients per lane · ${
              controller.durationSeconds
            } seconds`
      }
      notice={
        controller.targetIsRemote
          ? {
              accent: 'negative',
              icon: 'alert',
              title: 'This is a remote database',
              message: `${controller.target} is not a local target. RDST will run the selected queries against it and against Readyset, stepping each lane up to ${MAX_COMPARE_CONCURRENCY} clients. Writes remain blocked by the database safety layer. Type the target name below to confirm you intend to run this load against it.`,
            }
          : {
              accent: 'warning',
              icon: 'play',
              title: 'This executes real read-only queries',
              message: `RDST will run the selected queries against both ${controller.target} and Readyset, then safely step each lane up to ${MAX_COMPARE_CONCURRENCY} clients. QPS is measured independently, so the faster lane can complete more work. Writes remain blocked by the database safety layer.`,
            }
      }
      confirmLabel={
        controller.targetIsRemote ? 'Run against remote' : 'Start comparison'
      }
      confirmVariant={controller.targetIsRemote ? 'negative' : 'primary'}
      confirmIcon="play"
      confirmDisabled={!controller.canReview}
      confirmDisabledReason={controller.blockedReason ?? undefined}
      requireTyped={
        controller.targetIsRemote ? (controller.target ?? undefined) : undefined
      }
      loading={controller.starting}
      blockCloseWhileLoading
    />
  )

  if (layout === 'selection')
    return (
      <>
        <SelectionSetup controller={controller} heading={heading} />
        {reviewDialog}
      </>
    )

  if (layout === 'inline')
    return (
      <>
        <InlineSetup controller={controller} />
        {reviewDialog}
      </>
    )

  return (
    <>
      <Card className="desktop:h-full desktop:min-h-0">
        <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
          <HStack className="min-w-0 items-center gap-3">
            <IconTile icon="play" size="base" accent="primary" />
            <VStack className="min-w-0 items-start gap-0.5">
              <Card.Title>Compare against Readyset</Card.Title>
              <Card.Description>
                See how much faster these queries are with Readyset.
              </Card.Description>
            </VStack>
          </HStack>
          <HStack className="w-full shrink-0 items-center justify-between gap-2 tablet:w-auto tablet:justify-start">
            {controller.historyEntries.length > 0 && (
              <Button
                size="small"
                variant="primary"
                modifier="ghost"
                label={`History ${controller.historyEntries.length}`}
                icon="observe"
                onClick={() => controller.setHistoryOpen(true)}
              />
            )}
          </HStack>
        </Card.Header>

        <Card.Content className="desktop:min-h-0 desktop:flex-1 desktop:overflow-hidden">
          {/* Two columns only where both stay readable; from desktop the row
              is bounded so each column scrolls inside the card. */}
          <div className="grid gap-8 desktop:h-full desktop:min-h-0 desktop:grid-cols-3 desktop:grid-rows-[minmax(0,1fr)]">
            <div className="min-h-0 min-w-0 desktop:col-span-2">
              <QuerySelector controller={controller} />
            </div>

            {/* Scrolls on its own, like the query list beside it: the summary
                grows with the selection and must stay reachable, and its rows
                keep their height rather than being squashed by the flex column. */}
            <VStack className="items-stretch gap-3 desktop:min-h-0 desktop:overflow-y-auto desktop:[&>*]:shrink-0">
              <VStack className="items-start gap-0.5">
                <Text level="label-small" className="text-content-layout-1">
                  Run summary
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  What this comparison will execute.
                </Text>
              </VStack>
              <div className="rounded-xl border border-border-layout-soft px-4">
                <CompareSummaryRow
                  label="Database"
                  value={controller.target ?? 'Not selected'}
                />
                <CompareSummaryRow
                  label="Selected queries"
                  value={`${controller.selectedIds.length} selected`}
                />
                <CompareSummaryRow
                  label="Load"
                  value={`Safe · ${adaptiveLoadLabel(controller)} clients per lane`}
                />
                <CompareSummaryRow
                  label="Duration"
                  value={batchCost ?? `${controller.durationSeconds} seconds`}
                />
                <CompareSummaryRow
                  label="Parameter readiness"
                  value={ready}
                  tone={
                    nothingSelected
                      ? 'neutral'
                      : controller.missingParameterCount > 0
                        ? 'warning'
                        : 'positive'
                  }
                />
              </div>
              <SuggestValuesPanel
                hasParameters={controller.parameterCount > 0}
                suggesting={controller.suggestingParameters}
                missingParameterCount={controller.missingParameterCount}
                message={controller.suggestionMessage}
                schemaUnavailable={controller.suggestionSchemaUnavailable}
                onSuggest={() => void controller.suggestParameterValues()}
              />
            </VStack>
          </div>
        </Card.Content>

        <Card.Footer className="flex-wrap justify-between">
          <Text
            level="caption"
            className={
              controller.residualQueryHash
                ? 'mr-auto text-content-negative-soft'
                : 'mr-auto text-content-layout-3'
            }
          >
            {controller.residualQueryHash
              ? 'A parameter value did not apply. Re-enter the highlighted value and try again.'
              : (controller.blockedReason ??
                (batchCost
                  ? `Ready to compare at ${adaptiveLoadLabel(controller)} clients per lane. ${batchCost}.`
                  : `Ready to compare ${controller.selectedIds.length} ${
                      controller.selectedIds.length === 1 ? 'query' : 'queries'
                    } at ${adaptiveLoadLabel(controller)} clients per lane for ${
                      controller.durationSeconds
                    } seconds.`))}
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Run comparison"
            icon="play"
            disabled={!controller.canReview}
            onClick={() => controller.setReviewOpen(true)}
          />
        </Card.Footer>
      </Card>

      {reviewDialog}
    </>
  )
}
