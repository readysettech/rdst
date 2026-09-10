import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useEffect } from 'react'
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

export function CompareSetup({
  controller,
}: {
  controller: CompareController
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
          controller.targetIsRemote
            ? (controller.target ?? undefined)
            : undefined
        }
        loading={controller.starting}
        blockCloseWhileLoading
      />
    </>
  )
}
