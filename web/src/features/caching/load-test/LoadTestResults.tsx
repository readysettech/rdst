import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { TableHeaderCell } from '../../../components/TableHeaderCell'
import type { BenchmarkRequest } from '../../../lib/api'
import type { LoadTestProgress } from '../../../lib/backgroundRuns'
import { formatMs, shortHash } from '../../../lib/formatters'
import { LoadTestLiveChart } from './LoadTestLiveChart'
import type { LoadTestResultModel } from './loadTestModel'

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
  } = model
  const failed = outcome === 'failed'
  const partial = outcome === 'partial'
  const noMeasurements = outcome === 'no_measurements'

  return (
    <VStack className="w-full items-stretch gap-6">
      <Card aria-live="polite">
        <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
          <HStack className="min-w-0 items-center gap-3">
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
            <VStack className="min-w-0 items-start gap-0.5">
              <Card.Title>{title}</Card.Title>
              <Card.Description>{description}</Card.Description>
            </VStack>
          </HStack>
          <Tag
            variant={
              running
                ? 'informative'
                : failed || noMeasurements
                  ? 'negative'
                  : outcome === 'cancelled'
                    ? 'neutral'
                    : partial
                      ? 'warning'
                      : 'positive'
            }
            modifier="solid"
            label={statusLabel}
          />
        </Card.Header>

        <Card.Content>
          {queued ? (
            <HStack className="items-start gap-3 rounded-xl border border-border-info-soft p-4">
              <Icon
                name="info"
                label="Queued"
                className="mt-0.5 h-5 w-5 shrink-0 text-content-info-soft"
              />
              <VStack className="items-start gap-1">
                <Text level="label-small" className="text-content-layout-1">
                  No action is required
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {runMessage ||
                    'The timer starts only after the first query begins.'}
                </Text>
              </VStack>
            </HStack>
          ) : (
            <>
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
                <VStack className="mt-4 items-stretch gap-2">
                  <HStack className="justify-between gap-3">
                    <Text level="caption" className="text-content-layout-3">
                      {progress?.elapsed_seconds.toFixed(1) ?? '0.0'}s elapsed
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      {durationSeconds}s duration
                    </Text>
                  </HStack>
                  <div className="h-1.5 overflow-hidden rounded-full bg-surface-layout-2">
                    <div
                      className="h-full rounded-full bg-surface-primary-solid transition-[width] duration-300"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                </VStack>
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
              label={queued ? 'Cancel queued test' : 'Stop test'}
              icon="close"
              onClick={onStop}
            />
          ) : (
            <HStack className="flex-wrap items-center gap-2">
              <Button
                variant="primary"
                modifier="ghost"
                label="Adjust load"
                icon="settings"
                onClick={onAdjust}
              />
              <Button
                variant="rising"
                modifier="solid"
                label="Run again"
                icon="play"
                onClick={onRunAgain}
                disabled={targetLocked || !request}
              />
            </HStack>
          )}
        </Card.Footer>
      </Card>

      {!queued && timeline.length > 0 && (
        <LoadTestLiveChart timeline={timeline} live={running} />
      )}

      {(progress?.queries.length ?? 0) > 0 && (
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
                  <Text
                    level="body-small"
                    className="max-w-sm text-right text-content-warning-soft"
                  >
                    {query.reason}
                  </Text>
                </HStack>
              ))}
            </div>
          </Card.Content>
        </Card>
      )}

      {error && (
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
