import { cn } from '@rs/tailwind-base'
import { BaseInputRadioGroup } from '@rs/ui-new/base-input-radio-group'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useEffect } from 'react'
import { TargetConnectivityNotice, TargetLockNotice } from '../../../components'
import { BenchmarkConfirmDialog } from '../../../components/BenchmarkConfirmDialog'
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
import {
  BENCHMARK_EXECUTION_CAP,
  type LoadTestController,
  type LoadTestProfile,
} from './useLoadTestController'

const LOAD_TEST_PROFILE_OPTIONS = [
  {
    value: 'paced',
    label: 'Paced load',
    description:
      'Adds rest after each completed request for a controlled, lower-pressure check.',
    badge: (
      <Tag size="small" variant="neutral" modifier="ghost" label="Default" />
    ),
  },
  {
    value: 'capacity',
    label: 'Capacity test',
    description:
      'Runs continuously with bounded concurrency to measure sustainable throughput.',
  },
]
const CAPACITY_CLIENT_OPTIONS = [
  { value: '2', label: '2 clients' },
  { value: '4', label: '4 clients' },
]

function SummaryRow({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'neutral' | 'positive' | 'warning'
}) {
  return (
    <HStack className="justify-between gap-4 border-b border-border-layout-soft py-3 last:border-0">
      <Text level="body-small" className="text-content-layout-3">
        {label}
      </Text>
      <Text
        level="label-small"
        className={cn(
          'text-right tabular-nums',
          tone === 'neutral' && 'text-content-layout-1',
          tone === 'positive' && 'text-content-positive-soft',
          tone === 'warning' && 'text-content-warning-soft'
        )}
      >
        {value}
      </Text>
    </HStack>
  )
}

export function LoadTestSetup({
  controller,
}: {
  controller: LoadTestController
}) {
  const {
    queries,
    registryLoading,
    listError,
    refetchRegistry,
    statusLoading,
    statusError,
    refetchStatus,
    destinationTarget,
    handleDestinationChange,
    destinationLock,
    connectivity,
    selectedQueries,
    setSelectedQueries,
    searchTerm,
    setSearchTerm,
    setSourceFilter,
    testProfile,
    setTestProfile,
    intervalMs,
    setIntervalMs,
    capacityClients,
    setCapacityClients,
    durationSeconds,
    setDurationSeconds,
    paramValues,
    parameterSources,
    updateParameter,
    suggestingParameters,
    suggestionMessage,
    suggestionSchemaUnavailable,
    suggestParameterValues,
    confirmOpen,
    setConfirmOpen,
    loadSettingsOpen,
    setLoadSettingsOpen,
    sourceOptions,
    normalizedSourceFilter,
    targetDetailsError,
    refetchTargetDetails,
    destinationOptions,
    destinationIsRemote,
    filteredQueries,
    selectedQueryById,
    selectedCount,
    runnableCount,
    hiddenSelectedCount,
    queriesWithParameters,
    missingParameterCount,
    residualQueryHash,
    missingTables,
    canStart,
    toggleQuery,
    clearFilters,
    handleStart,
    handleConfirmRun,
    confirmTarget,
    confirmIsRemote,
    confirmQueryCount,
    confirmLoadSummary,
    confirmEstimatedExecutions,
  } = controller
  const confirmDialog = (
    <BenchmarkConfirmDialog
      isOpen={confirmOpen}
      target={confirmTarget}
      isRemote={confirmIsRemote}
      queryCount={confirmQueryCount}
      loadSummary={confirmLoadSummary}
      estimatedExecutions={confirmEstimatedExecutions}
      executionCap={BENCHMARK_EXECUTION_CAP}
      onConfirm={handleConfirmRun}
      onClose={() => setConfirmOpen(false)}
    />
  )

  const filterEmpty =
    !registryLoading && queries.length > 0 && filteredQueries.length === 0
  const parameterSummary =
    queriesWithParameters.length === 0
      ? 'No parameters'
      : missingParameterCount === 0
        ? 'Values ready'
        : `${missingParameterCount} missing`
  const readinessMessage = residualQueryHash
    ? 'A parameter value did not apply. Re-enter the highlighted value and try again.'
    : runnableCount === 0
      ? 'Select at least one visible query.'
      : missingParameterCount > 0
        ? `Add ${missingParameterCount} missing parameter ${
            missingParameterCount === 1 ? 'value' : 'values'
          }.`
        : `Ready to run ${runnableCount} ${
            runnableCount === 1 ? 'query' : 'queries'
          } against ${destinationTarget ?? 'the selected database'}.`

  // A parameter value that didn't apply cleanly must not run silently as
  // broken SQL; steer the user straight back to the offending query's
  // inline parameter fields instead.
  useEffect(() => {
    if (!residualQueryHash) return
    const card = document.querySelector<HTMLElement>(
      `[data-query-hash="${residualQueryHash}"]`
    )
    card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const input = card?.querySelector<HTMLInputElement>('input')
    input?.focus()
  }, [residualQueryHash])

  return (
    <VStack className="h-full min-h-0 w-full items-stretch gap-6">
      {confirmDialog}
      <AnimatePresence>
        {destinationLock.isLocked && (
          <m.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
          >
            <TargetLockNotice
              message={destinationLock.message}
              requirements={destinationLock.missingTargetRequirements}
              keyringAvailable={destinationLock.keyringAvailable}
            />
          </m.div>
        )}
      </AnimatePresence>

      {(connectivity.failure || connectivity.isChecking) &&
        !destinationLock.isLocked && (
          <TargetConnectivityNotice
            target={destinationLock.targetName ?? destinationTarget}
            failure={connectivity.failure}
            isChecking={connectivity.isChecking}
            onRetry={() => void connectivity.ensureReachable()}
          />
        )}

      <Card className="min-h-0 flex-1">
        <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
          <HStack className="min-w-0 items-center gap-3">
            <IconTile icon="layers" size="base" accent="primary" />
            <VStack className="min-w-0 items-start gap-0.5">
              <Card.Title>Configure load test</Card.Title>
              <Card.Description>
                Observe database behavior or measure throughput with a
                controlled read-only workload.
              </Card.Description>
            </VStack>
          </HStack>
          <Tag
            size="small"
            variant="neutral"
            modifier="ghost"
            label={testProfile === 'capacity' ? 'Capacity test' : 'Paced load'}
          />
        </Card.Header>

        <Card.Content className="min-h-0 flex-1 overflow-hidden">
          <div className="grid h-full min-h-0 gap-8 tablet:grid-cols-3">
            <VStack className="min-h-0 min-w-0 items-stretch gap-6 tablet:col-span-2">
              <VStack className="items-stretch gap-2">
                <HStack className="items-center justify-between gap-3">
                  <VStack className="items-start gap-0.5">
                    <Text level="label-small" className="text-content-layout-1">
                      Database
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      The selected queries execute as real read-only traffic.
                    </Text>
                  </VStack>
                  {destinationTarget && (
                    <Tag
                      size="small"
                      variant={destinationIsRemote ? 'warning' : 'neutral'}
                      modifier="ghost"
                      label={destinationIsRemote ? 'Remote' : 'Local'}
                    />
                  )}
                </HStack>
                {statusError ? (
                  <div className="rounded-xl border border-border-negative-soft p-4">
                    <HStack className="items-center justify-between gap-4">
                      <Text
                        level="body-small"
                        className="text-content-negative-soft"
                      >
                        Databases could not be loaded.
                      </Text>
                      <Button
                        size="small"
                        variant="primary"
                        modifier="ghost"
                        label="Try again"
                        onClick={() => void refetchStatus()}
                      />
                    </HStack>
                  </div>
                ) : (
                  <BaseInputSelect
                    name="destination-target"
                    options={destinationOptions}
                    value={destinationTarget ?? ''}
                    onValueChange={handleDestinationChange}
                    placeholder={
                      statusLoading ? 'Loading databases…' : 'Select database'
                    }
                    disabled={statusLoading}
                  />
                )}
                {targetDetailsError && (
                  <HStack className="items-center justify-between gap-3 rounded-xl border border-border-warning-soft px-4 py-3">
                    <Text level="caption" className="text-content-warning-soft">
                      Target safety details are unavailable. Load testing is
                      paused until they can be verified.
                    </Text>
                    <Button
                      size="small"
                      variant="primary"
                      modifier="ghost"
                      label="Retry"
                      onClick={() => void refetchTargetDetails()}
                    />
                  </HStack>
                )}
              </VStack>

              <VStack className="min-h-0 flex-1 items-stretch gap-3">
                <HStack className="flex-wrap items-end justify-between gap-3">
                  <VStack className="items-start gap-0.5">
                    <Text level="label-small" className="text-content-layout-1">
                      Queries
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      Choose the workload you want to exercise.
                    </Text>
                  </VStack>
                  <Show when={filteredQueries.length > 0}>
                    <Button
                      size="small"
                      variant="primary"
                      modifier="ghost"
                      label={
                        filteredQueries.every((query) =>
                          selectedQueries.includes(query.tag || query.hash)
                        )
                          ? 'Clear visible'
                          : 'Select visible'
                      }
                      onClick={() => {
                        const visible = filteredQueries.map(
                          (query) => query.tag || query.hash
                        )
                        const allVisibleSelected = visible.every((identifier) =>
                          selectedQueries.includes(identifier)
                        )
                        setSelectedQueries((current) =>
                          allVisibleSelected
                            ? current.filter(
                                (identifier) => !visible.includes(identifier)
                              )
                            : Array.from(new Set([...current, ...visible]))
                        )
                      }}
                    />
                  </Show>
                </HStack>

                <HStack className="flex-wrap items-center gap-3">
                  <div className="min-w-56 flex-1">
                    <BaseInputText
                      name="load-query-search"
                      placeholder="Search name, hash, or SQL…"
                      icon="search"
                      iconPosition="left"
                      value={searchTerm}
                      onChange={(event) => setSearchTerm(event.target.value)}
                    />
                  </div>
                  <div className="w-48">
                    <BaseInputSelect
                      name="load-source-filter"
                      options={sourceOptions}
                      value={normalizedSourceFilter}
                      onValueChange={setSourceFilter}
                    />
                  </div>
                </HStack>

                {listError ? (
                  <div className="rounded-xl border border-border-negative-soft p-5">
                    <VStack className="items-start gap-3">
                      <Text
                        level="label-small"
                        className="text-content-negative-soft"
                      >
                        Queries could not be loaded
                      </Text>
                      <Text level="caption" className="text-content-layout-3">
                        {listError}
                      </Text>
                      <Button
                        size="small"
                        variant="primary"
                        modifier="ghost"
                        label="Try again"
                        onClick={() => void refetchRegistry()}
                      />
                    </VStack>
                  </div>
                ) : registryLoading ? (
                  <div className="rounded-xl border border-border-layout-soft p-10 text-center">
                    <Text level="body-small" className="text-content-layout-3">
                      Loading queries…
                    </Text>
                  </div>
                ) : queries.length === 0 ? (
                  <div className="rounded-xl border border-border-layout-soft p-10 text-center">
                    <VStack className="items-center gap-2">
                      <Text
                        level="label-small"
                        className="text-content-layout-1"
                      >
                        No queries available yet
                      </Text>
                      <Text level="caption" className="text-content-layout-3">
                        Add or discover queries in Queries before running a load
                        test.
                      </Text>
                    </VStack>
                  </div>
                ) : filterEmpty ? (
                  <div className="rounded-xl border border-border-layout-soft p-10 text-center">
                    <VStack className="items-center gap-3">
                      <Text
                        level="label-small"
                        className="text-content-layout-1"
                      >
                        No matching queries
                      </Text>
                      <Button
                        size="small"
                        variant="primary"
                        modifier="ghost"
                        label="Clear filters"
                        onClick={clearFilters}
                      />
                    </VStack>
                  </div>
                ) : (
                  <PerformanceQueryList
                    ariaLabel="Queries available for load testing"
                    footer={
                      selectedCount > 0 ? (
                        <HStack className="flex-wrap items-center justify-between gap-3 border-t border-border-layout-soft px-4 py-3">
                          <HStack className="flex-wrap items-center gap-2">
                            <Tag
                              size="small"
                              variant="primary"
                              modifier="ghost"
                              label={`${selectedCount} selected`}
                            />
                            {hiddenSelectedCount > 0 && (
                              <Text
                                level="caption"
                                className="text-content-layout-3"
                              >
                                {hiddenSelectedCount} hidden by filters
                              </Text>
                            )}
                          </HStack>
                          <Button
                            size="small"
                            variant="primary"
                            modifier="ghost"
                            label="Clear"
                            onClick={() => setSelectedQueries([])}
                          />
                        </HStack>
                      ) : null
                    }
                  >
                    {filteredQueries.map((query) => {
                      const identifier = query.tag || query.hash
                      const selected = selectedQueries.includes(identifier)
                      const selectedQuery = selectedQueryById.get(identifier)
                      const parameterCount = detectParameters(query.sql).length
                      return (
                        <PerformanceQueryCard
                          key={query.hash}
                          queryHash={query.hash}
                          selected={selected}
                          onSelect={() => toggleQuery(identifier)}
                          title={queryDisplayName(query)}
                          sql={query.sql}
                          parameterCount={parameterCount}
                          meta={
                            <>
                              <Text
                                level="caption"
                                className="text-content-layout-3"
                              >
                                {formatMeta([
                                  `hash ${shortHash(query.hash)}`,
                                  query.target ?? null,
                                ])}
                              </Text>
                              <PerformanceCacheabilityNote
                                readysetSupported={query.readyset_supported}
                                checkedAt={query.readyset_last_observed_at}
                              />
                            </>
                          }
                          parameterContent={
                            selectedQuery &&
                            selectedQuery.parameters.length > 0 ? (
                              <PerformanceQueryParameters
                                ownerId={identifier}
                                inputPrefix="load"
                                parameters={selectedQuery.parameters}
                                values={paramValues}
                                sources={parameterSources}
                                onValueChange={(parameter, value) => {
                                  const suffix =
                                    parameter.placeholder === '?'
                                      ? `?${parameter.index}`
                                      : parameter.placeholder
                                  const key = `${identifier}:${suffix}`
                                  updateParameter(key, value)
                                }}
                              />
                            ) : undefined
                          }
                        />
                      )
                    })}
                  </PerformanceQueryList>
                )}
              </VStack>
            </VStack>

            <VStack className="items-stretch gap-4">
              <VStack className="items-start gap-0.5">
                <Text level="label-small" className="text-content-layout-1">
                  Test goal
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  Choose the signal you want this run to produce.
                </Text>
              </VStack>
              <BaseInputRadioGroup
                aria-label="Load test goal"
                value={testProfile}
                onValueChange={(value) =>
                  setTestProfile(value as LoadTestProfile)
                }
                options={LOAD_TEST_PROFILE_OPTIONS}
              />

              {testProfile === 'capacity' && (
                <VStack className="items-stretch gap-1">
                  <Text level="label-small" className="text-content-layout-2">
                    Concurrent clients
                  </Text>
                  <BaseInputSelect
                    name="capacity-clients"
                    options={CAPACITY_CLIENT_OPTIONS}
                    value={String(capacityClients)}
                    onValueChange={(value) =>
                      setCapacityClients(value === '4' ? 4 : 2)
                    }
                  />
                  <Text level="caption" className="text-content-layout-3">
                    Bounded to 4 clients to protect this device and database.
                  </Text>
                </VStack>
              )}

              <VStack className="items-start gap-0.5">
                <Text level="label-small" className="text-content-layout-1">
                  Run summary
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  What this test will execute.
                </Text>
              </VStack>
              <div className="rounded-xl border border-border-layout-soft px-4">
                <SummaryRow
                  label="Database"
                  value={destinationTarget ?? 'Not selected'}
                />
                <SummaryRow
                  label="Queries"
                  value={`${runnableCount} selected`}
                />
                <SummaryRow
                  label="Load profile"
                  value={
                    testProfile === 'capacity' ? 'Capacity test' : 'Paced load'
                  }
                />
                <SummaryRow
                  label="Clients"
                  value={
                    testProfile === 'capacity'
                      ? `${capacityClients} clients`
                      : '1 client'
                  }
                />
                <SummaryRow
                  label="Rest"
                  value={
                    testProfile === 'capacity'
                      ? 'No rest'
                      : intervalMs === 0
                        ? 'No rest'
                        : `${intervalMs} ms`
                  }
                  tone={
                    testProfile === 'paced' && intervalMs === 0
                      ? 'warning'
                      : 'neutral'
                  }
                />
                <SummaryRow
                  label="Duration"
                  value={`${durationSeconds} seconds`}
                />
                <SummaryRow
                  label="Parameters"
                  value={parameterSummary}
                  tone={missingParameterCount > 0 ? 'warning' : 'positive'}
                />
              </div>

              <SuggestValuesPanel
                hasParameters={queriesWithParameters.length > 0}
                suggesting={suggestingParameters}
                missingParameterCount={missingParameterCount}
                message={suggestionMessage}
                schemaUnavailable={suggestionSchemaUnavailable}
                onSuggest={() => void suggestParameterValues()}
              />

              <div className="overflow-hidden rounded-xl border border-border-layout-soft">
                <Pressable
                  type="button"
                  aria-expanded={loadSettingsOpen}
                  onClick={() => setLoadSettingsOpen(!loadSettingsOpen)}
                  className="flex w-full cursor-pointer items-center justify-between gap-3 px-4 py-3 hover:bg-surface-layout-2/50"
                >
                  <HStack className="items-center gap-2">
                    <Icon
                      name="settings"
                      label="Advanced load settings"
                      className="h-4 w-4 text-content-layout-3"
                    />
                    <Text level="label-small" className="text-content-layout-1">
                      Advanced load settings
                    </Text>
                  </HStack>
                  <Icon
                    name="chevron-down"
                    label=""
                    className={cn(
                      'h-4 w-4 text-content-layout-3 transition-transform',
                      loadSettingsOpen && 'rotate-180'
                    )}
                  />
                </Pressable>
                <AnimatePresence initial={false}>
                  {loadSettingsOpen && (
                    <m.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      className="overflow-hidden"
                    >
                      <VStack className="items-stretch gap-4 border-t border-border-layout-soft p-4">
                        {testProfile === 'paced' && (
                          <VStack className="items-stretch gap-1">
                            <Text
                              level="label-small"
                              className="text-content-layout-2"
                            >
                              Rest after each request (ms)
                            </Text>
                            <BaseInputText
                              name="load-rest-ms"
                              type="number"
                              min="0"
                              value={String(intervalMs)}
                              onChange={(event) =>
                                setIntervalMs(
                                  Math.max(0, Number(event.target.value) || 0)
                                )
                              }
                            />
                            <Text
                              level="caption"
                              className={cn(
                                intervalMs === 0
                                  ? 'text-content-warning-soft'
                                  : 'text-content-layout-3'
                              )}
                            >
                              {intervalMs === 0
                                ? 'No rest: the next query starts immediately after completion.'
                                : `Wait ${intervalMs}ms after a query completes before sending the next one.`}
                            </Text>
                          </VStack>
                        )}
                        <VStack className="items-stretch gap-1">
                          <Text
                            level="label-small"
                            className="text-content-layout-2"
                          >
                            Duration (seconds)
                          </Text>
                          <BaseInputText
                            name="load-duration"
                            type="number"
                            min="1"
                            value={String(durationSeconds)}
                            onChange={(event) =>
                              setDurationSeconds(
                                Math.max(1, Number(event.target.value) || 30)
                              )
                            }
                          />
                        </VStack>
                      </VStack>
                    </m.div>
                  )}
                </AnimatePresence>
              </div>
            </VStack>
          </div>
        </Card.Content>

        <Card.Footer className="flex-wrap justify-between gap-3">
          <VStack className="mr-auto items-start gap-1">
            <Text
              level="caption"
              className={
                residualQueryHash
                  ? 'text-content-negative-soft'
                  : 'text-content-layout-3'
              }
            >
              {readinessMessage}
            </Text>
            {missingTables.length > 0 && (
              <Text level="caption" className="text-content-warning-soft">
                Missing from {destinationTarget}: {missingTables.join(', ')}
              </Text>
            )}
          </VStack>
          <Button
            variant="rising"
            modifier="solid"
            label="Run load test"
            icon="play"
            onClick={handleStart}
            disabled={!canStart}
          />
        </Card.Footer>
      </Card>
    </VStack>
  )
}
