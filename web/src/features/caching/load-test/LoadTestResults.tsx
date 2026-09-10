import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { TableHeaderCell } from '../../../components/TableHeaderCell'
import type { BenchmarkRequest } from '../../../lib/api'
import type { LoadTestProgress } from '../../../lib/backgroundRuns'
import {
  formatElapsedOfWindow,
  formatMs,
  formatSecondsShort,
  shortHash,
} from '../../../lib/formatters'
import {
  BenchmarkProgress,
  useElapsedSeconds,
} from '../shared/BenchmarkProgress'
import { type StatusTone, statusToneIcon } from '../shared/statusTone'
import { LoadTestLiveChart } from './LoadTestLiveChart'
import {
  type LoadTestLaneStats,
  type LoadTestResultModel,
  readQueryLanes,
} from './loadTestModel'

function formatNumber(value: number) {
  return value.toLocaleString()
}

function LoadMetric({
  label,
  value,
  detail,
  tone = 'neutral',
}: {
  label: string
  value: string
  detail?: string
  tone?: 'neutral' | 'positive' | 'warning' | 'negative'
}) {
  return (
    <VStack className="min-w-0 items-start gap-1 px-4 py-4">
      <Text level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <Text
        level="headline-4"
        className={cn(
          'tabular-nums',
          tone === 'positive' && 'text-content-positive-soft',
          tone === 'warning' && 'text-content-warning-soft',
          tone === 'negative' && 'text-content-negative-soft',
          tone === 'neutral' && 'text-content-layout-1'
        )}
      >
        {value}
      </Text>
      {detail && (
        <Text level="caption" className="text-content-layout-3">
          {detail}
        </Text>
      )}
    </VStack>
  )
}

function LaneMetric({
  label,
  qps,
  p95,
  errors,
  lead,
  readyset = false,
}: {
  label: string
  qps: number
  p95: number
  errors: number
  /** The metric the run's verdict is computed from, shown as the big figure. */
  lead: 'qps' | 'p95'
  readyset?: boolean
}) {
  return (
    <VStack className="min-w-0 items-stretch gap-4 p-5">
      <HStack className="items-center gap-2">
        {/* Both lanes carry the chart's own colour key, so neither dot
            disappears into the surface behind it. [D-14] */}
        <span
          aria-hidden="true"
          className={cn(
            'h-2 w-2 rounded-full',
            readyset ? 'bg-content-viz-cache' : 'bg-content-viz-origin'
          )}
        />
        <Text level="label-small" className="text-content-layout-1">
          {label}
        </Text>
      </HStack>
      <HStack className="items-end justify-between gap-4">
        <VStack className="items-start gap-0.5">
          <Text
            level="headline-2"
            className={cn(
              'tabular-nums',
              readyset ? 'text-content-positive-soft' : 'text-content-layout-1'
            )}
          >
            {lead === 'qps' ? qps.toFixed(qps >= 10 ? 0 : 1) : formatMs(p95)}
          </Text>
          <Text level="caption" className="text-content-layout-3">
            {lead === 'qps' ? 'QPS' : 'p95 latency'}
          </Text>
        </VStack>
        <VStack className="items-end gap-1">
          <Text
            level="label-small"
            className="text-content-layout-1 tabular-nums"
          >
            {lead === 'qps'
              ? `${formatMs(p95)} p95`
              : `${qps.toFixed(qps >= 10 ? 0 : 1)} QPS`}
          </Text>
          <Text
            level="caption"
            className={cn(
              'tabular-nums',
              errors > 0 ? 'text-content-warning-soft' : 'text-content-layout-3'
            )}
          >
            {errors} {errors === 1 ? 'error' : 'errors'}
          </Text>
        </VStack>
      </HStack>
    </VStack>
  )
}

function LaneStatCell({
  label,
  stats,
  readyset = false,
}: {
  label: string
  stats: Pick<LoadTestLaneStats, 'successes' | 'failures' | 'avg_ms' | 'p95_ms'>
  readyset?: boolean
}) {
  const hasSuccess = stats.successes > 0
  return (
    <VStack className="items-stretch gap-1 px-3 py-2">
      <HStack className="items-center gap-2">
        <span
          aria-hidden="true"
          className={cn(
            'h-1.5 w-1.5 rounded-full',
            readyset ? 'bg-content-viz-cache' : 'bg-content-viz-origin'
          )}
        />
        <Text
          level="caption"
          className={
            readyset ? 'text-content-positive-soft' : 'text-content-layout-3'
          }
        >
          {label}
        </Text>
      </HStack>
      <Text level="label-small" className="text-content-layout-1 tabular-nums">
        {formatNumber(stats.successes)} completed
        {stats.failures > 0 ? ` · ${formatNumber(stats.failures)} errors` : ''}
      </Text>
      <Text level="caption" className="text-content-layout-3 tabular-nums">
        {hasSuccess ? formatMs(stats.avg_ms) : '—'} mean ·{' '}
        {hasSuccess ? formatMs(stats.p95_ms) : '—'} p95
      </Text>
    </VStack>
  )
}

export function LoadTestResults({
  model,
  progress,
  timeline,
  request,
  runMessage,
  error,
  targetLocked,
  onStop,
  onAdjust,
  onRunAgain,
}: {
  model: LoadTestResultModel
  progress: LoadTestProgress | undefined
  timeline: LoadTestProgress[]
  request: BenchmarkRequest | undefined
  runMessage: string | undefined
  error: string | undefined
  targetLocked: boolean
  onStop: () => void
  onAdjust: () => void
  onRunAgain: () => void
}) {
  const {
    outcome,
    running,
    queued,
    totalExecutions,
    totalSuccesses,
    totalFailures,
    errorRate,
    p95,
    meanLatency,
    durationSeconds,
    profile,
    clients,
    intervalMs,
    expectedPacedQps,
    progressPercent,
    title,
    description,
    statusLabel,
    skippedQueries,
    preparation,
    comparative,
    readysetSetup,
    laneAggregates,
    speedup,
  } = model
  // Preparation ticks carry no elapsed time of their own, so the minutes the
  // user is being asked to wait are counted here. [D-18]
  const preparingSeconds = useElapsedSeconds(!!preparation)
  const failed = outcome === 'failed'
  const partial = outcome === 'partial'
  const noMeasurements = outcome === 'no_measurements'
  // Why this run cannot be repeated, if it cannot. `null` means it can.
  const runAgainBlockedReason = targetLocked
    ? 'Unlock this database to run the test again.'
    : !request
      ? 'This run was restored without its settings, so it cannot be repeated. Adjust the load to configure a new one.'
      : null
  const tone: StatusTone = running
    ? 'informative'
    : failed || noMeasurements
      ? 'negative'
      : outcome === 'cancelled'
        ? 'neutral'
        : partial
          ? 'warning'
          : 'positive'

  return (
    <VStack className="w-full items-stretch gap-6">
      <Card aria-live="polite">
        <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
          <HStack className="min-w-0 items-center gap-3">
            {preparation ? (
              <HStack className="h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border-primary-soft">
                <Spinner size="large" color="primary-soft" />
              </HStack>
            ) : (
              <IconTile
                icon={
                  running
                    ? 'play'
                    : failed || noMeasurements
                      ? 'alert'
                      : outcome === 'cancelled'
                        ? 'close'
                        : 'tick-double'
                }
                size="base"
                accent={
                  running
                    ? 'primary'
                    : failed || noMeasurements
                      ? 'negative'
                      : outcome === 'cancelled'
                        ? 'rising'
                        : partial
                          ? 'warning'
                          : 'positive'
                }
              />
            )}
            <VStack className="min-w-0 items-start gap-0.5">
              <Card.Title>{title}</Card.Title>
              <Card.Description>{description}</Card.Description>
            </VStack>
          </HStack>
          <Tag
            variant={tone}
            modifier="solid"
            icon={statusToneIcon(tone)}
            iconPosition="left"
            label={statusLabel}
          />
        </Card.Header>

        <Card.Content>
          {preparation ? (
            <BenchmarkProgress
              value={preparation.preparedCount}
              max={preparation.prepareTotal}
              ariaLabel="Cache preparation"
              label={`${preparation.preparedCount} of ${preparation.prepareTotal} queries cached`}
              timing={`${formatSecondsShort(preparingSeconds)} elapsed`}
              detail="Measurement starts once every query is cached. On a cold sandbox that takes a few minutes; stopping the test here costs nothing."
            />
          ) : queued ? (
            <HStack className="items-start gap-3 rounded-xl border border-border-info-soft p-4">
              <Icon
                name="info"
                label="Queued"
                className="mt-0.5 h-5 w-5 shrink-0 text-content-info-soft"
              />
              <VStack className="items-start gap-1">
                <Text level="label-small" className="text-content-layout-1">
                  Waiting for the running measurement to finish
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {runMessage ||
                    'The timer starts only after the first query begins.'}
                </Text>
              </VStack>
            </HStack>
          ) : failed && totalExecutions === 0 ? (
            // A run that never completed a request has no metrics to grid and
            // no pacing to explain — only the reason it ended. [D-19]
            <HStack className="items-start gap-3 rounded-xl border border-border-negative-soft p-4">
              <Icon
                name="alert"
                label="Load test error"
                className="mt-0.5 h-5 w-5 shrink-0 text-content-negative-soft"
              />
              <VStack className="items-start gap-1">
                <Text
                  level="label-small"
                  className="text-content-negative-soft"
                >
                  The load test could not complete
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {error ||
                    'The run ended before a request completed, so nothing was measured.'}
                </Text>
              </VStack>
            </HStack>
          ) : (
            <>
              {readysetSetup?.status === 'unavailable' && (
                <HStack className="mb-4 items-start gap-3 rounded-xl border border-border-info-soft p-4">
                  <Icon
                    name="info"
                    label=""
                    className="mt-0.5 h-5 w-5 shrink-0 text-content-info-soft"
                  />
                  <VStack className="items-start gap-1">
                    <Text level="label-small" className="text-content-layout-1">
                      Readyset was unavailable for this run
                    </Text>
                    <Text level="body-small" className="text-content-layout-3">
                      {readysetSetup.detail ||
                        'This test ran against the origin database only.'}
                    </Text>
                  </VStack>
                </HStack>
              )}
              <div className="grid grid-cols-2 overflow-hidden rounded-xl border border-border-layout-soft tablet:grid-cols-4 tablet:divide-x tablet:divide-border-layout-soft">
                <LoadMetric
                  label={
                    profile === 'capacity'
                      ? running
                        ? 'Current QPS'
                        : 'Sustained QPS'
                      : running
                        ? 'Paced QPS'
                        : 'Observed QPS'
                  }
                  value={(progress?.qps ?? 0).toFixed(
                    (progress?.qps ?? 0) >= 10 ? 0 : 1
                  )}
                  detail="successful requests"
                  tone="positive"
                />
                <LoadMetric
                  label="p95 latency"
                  value={totalSuccesses > 0 ? formatMs(p95) : '—'}
                  detail="successful requests"
                />
                <LoadMetric
                  label="Error rate"
                  value={`${errorRate.toFixed(1)}%`}
                  detail={`${formatNumber(totalFailures)} failed`}
                  tone={
                    totalFailures === 0
                      ? 'positive'
                      : failed
                        ? 'negative'
                        : 'warning'
                  }
                />
                <LoadMetric
                  label="Completed"
                  value={formatNumber(totalSuccesses)}
                  detail={`${formatNumber(totalExecutions)} attempted`}
                />
              </div>
              <HStack className="mt-4 items-start gap-3 rounded-xl border border-border-layout-soft bg-surface-layout-2/40 px-4 py-3">
                <Icon
                  name={profile === 'capacity' ? 'speedometer' : 'info'}
                  label=""
                  className="mt-0.5 h-4 w-4 shrink-0 text-content-layout-3"
                />
                <VStack className="items-start gap-0.5">
                  <Text level="label-small" className="text-content-layout-1">
                    {profile === 'capacity'
                      ? 'Measuring sustainable throughput'
                      : 'This run is intentionally paced'}
                  </Text>
                  <Text level="caption" className="text-content-layout-3">
                    {profile === 'capacity'
                      ? `${clients} clients run continuously with no configured rest.`
                      : expectedPacedQps !== null
                        ? `At ${formatMs(meanLatency)} mean latency plus ${intervalMs}ms rest, one client is expected to top out near ${expectedPacedQps.toFixed(1)} QPS. Use Capacity test to measure throughput.`
                        : `${intervalMs}ms of rest is added after every completed request. This validates behavior at controlled traffic, not maximum capacity.`}
                  </Text>
                </VStack>
              </HStack>
              {running && (
                <div className="mt-4">
                  <BenchmarkProgress
                    value={progressPercent}
                    ariaLabel="Load test progress"
                    label="Measuring"
                    timing={formatElapsedOfWindow(
                      progress?.elapsed_seconds ?? 0,
                      durationSeconds
                    )}
                  />
                </div>
              )}
            </>
          )}
        </Card.Content>

        <Card.Footer className="flex-wrap justify-between gap-3">
          <Text level="caption" className="mr-auto text-content-layout-3">
            {request?.queries.length ?? 0}{' '}
            {(request?.queries.length ?? 0) === 1 ? 'query' : 'queries'} ·{' '}
            {clients} {clients === 1 ? 'client' : 'clients'} ·{' '}
            {profile === 'capacity'
              ? 'continuous'
              : intervalMs === 0
                ? 'no rest'
                : `${intervalMs}ms rest after completion`}{' '}
            · {durationSeconds}s
          </Text>
          {running ? (
            <Button
              variant="negative"
              modifier="outline"
              label={
                queued
                  ? 'Cancel queued test'
                  : preparation
                    ? 'Cancel preparation'
                    : 'Stop test'
              }
              icon="close"
              onClick={onStop}
            />
          ) : (
            <HStack className="flex-wrap items-center gap-2">
              {/* A dead primary says why it is dead, next to itself, rather
                  than leaving the reader to guess (GUIDELINES §10). */}
              {runAgainBlockedReason ? (
                <Text level="caption" className="text-content-warning-soft">
                  {runAgainBlockedReason}
                </Text>
              ) : null}
              <Button
                variant="primary"
                modifier="ghost"
                label="Adjust load"
                icon="settings"
                onClick={onAdjust}
              />
              <Button
                variant="primary"
                modifier="solid"
                label="Run again"
                icon="play"
                onClick={onRunAgain}
                disabled={Boolean(runAgainBlockedReason)}
              />
            </HStack>
          )}
        </Card.Footer>
      </Card>

      {comparative && laneAggregates && (
        <Card>
          <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
            <HStack className="items-center gap-3">
              <IconTile
                icon={
                  speedup !== null && speedup >= 1 ? 'tick-double' : 'alert'
                }
                size="base"
                accent={
                  speedup !== null && speedup >= 1 ? 'positive' : 'warning'
                }
              />
              <VStack className="items-start gap-0.5">
                <Card.Title>
                  {speedup === null
                    ? 'Origin vs Readyset'
                    : speedup >= 1
                      ? `${speedup.toFixed(speedup >= 10 ? 0 : 1)}× faster with Readyset`
                      : `${Math.abs((speedup - 1) * 100).toFixed(0)}% slower with Readyset`}
                </Card.Title>
                <Card.Description>
                  {/* Both lanes of a paced run are throttled to the same rate by
                      construction, so QPS is the one metric that cannot carry
                      the claim — latency is. [D-14] */}
                  {profile === 'capacity'
                    ? 'The same workload ran against both lanes side by side; the comparison is sustained throughput.'
                    : 'The same workload ran against both lanes side by side at the same paced rate; the comparison is p95 latency.'}
                </Card.Description>
              </VStack>
            </HStack>
          </Card.Header>
          <Card.Content>
            <div className="grid overflow-hidden rounded-xl border border-border-layout-soft tablet:grid-cols-2 tablet:divide-x tablet:divide-border-layout-soft">
              <LaneMetric
                label="Origin"
                qps={laneAggregates.origin.qps}
                p95={laneAggregates.origin.p95}
                errors={laneAggregates.origin.failures}
                lead={profile === 'capacity' ? 'qps' : 'p95'}
              />
              <LaneMetric
                label="Readyset"
                qps={laneAggregates.readyset.qps}
                p95={laneAggregates.readyset.p95}
                errors={laneAggregates.readyset.failures}
                lead={profile === 'capacity' ? 'qps' : 'p95'}
                readyset
              />
            </div>
          </Card.Content>
        </Card>
      )}

      {!queued && timeline.length > 0 && (
        <LoadTestLiveChart timeline={timeline} live={running} />
      )}

      {(progress?.queries.length ?? 0) > 0 && comparative && (
        <Card>
          <Card.Header>
            <Card.Title>Per-query results</Card.Title>
            <Card.Description>
              Origin and Readyset throughput, latency, and errors for each
              query.
            </Card.Description>
          </Card.Header>
          <Card.Content className="p-0">
            <div className="divide-y divide-border-layout-soft">
              {progress?.queries.map((query) => {
                const lanes = readQueryLanes(query)
                return (
                  <div
                    key={query.query_hash}
                    className="grid gap-5 px-6 py-5 tablet:grid-cols-[minmax(0,1fr)_minmax(20rem,1fr)]"
                  >
                    <VStack className="min-w-0 items-start gap-1">
                      <Text
                        level="label-small"
                        className="truncate text-content-layout-1"
                      >
                        {query.query_name}
                      </Text>
                      <Text
                        level="mono-small"
                        className="text-content-layout-3"
                      >
                        {shortHash(query.query_hash)}
                      </Text>
                      {query.last_error && (
                        <Text
                          level="caption"
                          className="line-clamp-2 text-content-negative-soft"
                        >
                          {query.last_error}
                        </Text>
                      )}
                    </VStack>
                    {lanes ? (
                      <div className="grid overflow-hidden rounded-lg border border-border-layout-soft tablet:grid-cols-2 tablet:divide-x tablet:divide-border-layout-soft">
                        <LaneStatCell label="Origin" stats={lanes.origin} />
                        <LaneStatCell
                          label="Readyset"
                          stats={lanes.readyset}
                          readyset
                        />
                      </div>
                    ) : (
                      <div className="rounded-lg border border-border-layout-soft">
                        <LaneStatCell
                          label="Origin"
                          stats={{
                            successes: query.successes,
                            failures: query.failures,
                            avg_ms: query.avg_ms,
                            p95_ms: query.p95_ms,
                          }}
                        />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </Card.Content>
        </Card>
      )}

      {(progress?.queries.length ?? 0) > 0 && !comparative && (
        <Card>
          <Card.Header>
            <Card.Title>Per-query results</Card.Title>
            <Card.Description>
              Throughput coverage, latency, and errors for each query.
            </Card.Description>
          </Card.Header>
          <Card.Content className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border-layout-soft">
                    <TableHeaderCell>Query</TableHeaderCell>
                    <TableHeaderCell align="right">Completed</TableHeaderCell>
                    <TableHeaderCell align="right">Errors</TableHeaderCell>
                    <TableHeaderCell align="right">Mean</TableHeaderCell>
                    <TableHeaderCell align="right">P95</TableHeaderCell>
                    <TableHeaderCell align="right">P99</TableHeaderCell>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-layout-soft">
                  {progress?.queries.map((query) => {
                    const hasSuccess = query.successes > 0
                    return (
                      <tr key={query.query_hash}>
                        <td className="px-4 py-4">
                          <VStack className="max-w-xl items-start gap-1">
                            <Text
                              level="label-small"
                              className="text-content-layout-1"
                            >
                              {query.query_name}
                            </Text>
                            <Text
                              level="mono-small"
                              className="text-content-layout-3"
                            >
                              {shortHash(query.query_hash)}
                            </Text>
                            {query.last_error && (
                              <Text
                                level="caption"
                                className="line-clamp-2 text-content-negative-soft"
                              >
                                {query.last_error}
                              </Text>
                            )}
                          </VStack>
                        </td>
                        <td className="px-4 py-4 text-right">
                          <Text
                            level="mono-small"
                            className="text-content-layout-1"
                          >
                            {formatNumber(query.successes)}
                          </Text>
                        </td>
                        <td className="px-4 py-4 text-right">
                          <Text
                            level="mono-small"
                            className={
                              query.failures > 0
                                ? 'text-content-negative-soft'
                                : 'text-content-layout-3'
                            }
                          >
                            {formatNumber(query.failures)}
                          </Text>
                        </td>
                        {[query.avg_ms, query.p95_ms, query.p99_ms].map(
                          (value, index) => (
                            <td
                              key={`${query.query_hash}-latency-${index}`}
                              className="px-4 py-4 text-right"
                            >
                              <Text
                                level="mono-small"
                                className="text-content-layout-2"
                              >
                                {hasSuccess ? formatMs(value) : '—'}
                              </Text>
                            </td>
                          )
                        )}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card.Content>
        </Card>
      )}

      {skippedQueries.length > 0 && (
        <Card className="border-border-warning-soft">
          <Card.Header>
            <Card.Title>
              {skippedQueries.length}{' '}
              {skippedQueries.length === 1 ? 'query' : 'queries'} skipped
            </Card.Title>
            <Card.Description>
              Excluded before the run started rather than counted as failures.
            </Card.Description>
          </Card.Header>
          <Card.Content className="p-0">
            <div className="divide-y divide-border-layout-soft">
              {skippedQueries.map((query) => (
                <HStack
                  key={query.query_hash}
                  className="items-start justify-between gap-4 px-6 py-4"
                >
                  <VStack className="min-w-0 items-start gap-1">
                    <Text level="label-small" className="text-content-layout-1">
                      {query.query_name || 'Query'}
                    </Text>
                    <Text level="mono-small" className="text-content-layout-3">
                      {shortHash(query.query_hash)}
                    </Text>
                  </VStack>
                  {query.laneReasons ? (
                    <VStack className="max-w-sm items-end gap-1">
                      {(['origin', 'readyset'] as const).map((lane) => {
                        const reason = query.laneReasons?.[lane]
                        if (!reason) return null
                        return (
                          <Text
                            key={lane}
                            level="body-small"
                            className="text-right text-content-warning-soft"
                          >
                            {lane === 'origin' ? 'Origin' : 'Readyset'}:{' '}
                            {reason}
                          </Text>
                        )
                      })}
                    </VStack>
                  ) : (
                    <Text
                      level="body-small"
                      className="max-w-sm text-right text-content-warning-soft"
                    >
                      {query.reason}
                    </Text>
                  )}
                </HStack>
              ))}
            </div>
          </Card.Content>
        </Card>
      )}

      {error && !(failed && totalExecutions === 0) && (
        <Card className="border-border-negative-soft">
          <Card.Content>
            <HStack className="items-start gap-3">
              <Icon
                name="alert"
                label="Load test error"
                className="mt-0.5 h-5 w-5 shrink-0 text-content-negative-soft"
              />
              <VStack className="items-start gap-1">
                <Text
                  level="label-small"
                  className="text-content-negative-soft"
                >
                  The load test could not complete
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {error}
                </Text>
              </VStack>
            </HStack>
          </Card.Content>
        </Card>
      )}
    </VStack>
  )
}
