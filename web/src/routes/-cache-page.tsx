import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ComparisonCard } from '../components/CacheComparison'
import { QueryCacheStatus } from '../components/QueryCacheStatus'
import { QueryCard } from '../components/QueryCard'
import { ParameterDialog } from '../components/top'
import { useTarget } from '../hooks/useTarget'
import { startCacheTestRun, useBackgroundRuns } from '../lib/backgroundRuns'
import { normalizeHttpError } from '../lib/errorContract'
import { isNotCacheable } from '../lib/queryImpact'
import { fillCapturedParams, hasParameters } from '../lib/sqlParameters'
import { useQueryRegistry } from '../lib/useQueryRegistry'

interface SandboxDiagnostics {
  phase: string
  current_target?: string | null
  generation: number
  lease_purpose?: string | null
  queued_requests: number
  dirty_reason?: string | null
  failed_target?: string | null
  last_error?: string | null
  last_released_at?: string | null
  expires_at?: string | null
  container_name: string
  healthy: boolean
  docker_installed: boolean
  docker_running: boolean
}

async function fetchSandboxDiagnostics(): Promise<SandboxDiagnostics> {
  const response = await fetch('/api/cache/sandbox')
  if (!response.ok) throw new Error('Sandbox status is unavailable')
  return response.json() as Promise<SandboxDiagnostics>
}

async function queueSandboxPrewarm(target: string): Promise<void> {
  const response = await fetch('/api/cache/sandbox/prewarm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => undefined)
    throw new Error(normalizeHttpError(response.status, body).message)
  }
}

const DOCKER_INSTALL_URL = 'https://docs.docker.com/get-started/get-docker/'

export function CachePage({ deepLinkHash }: { deepLinkHash?: string }) {
  const navigate = useNavigate()
  const { target } = useTarget()
  const runs = useBackgroundRuns()
  const {
    queries,
    isLoading: queriesLoading,
    listError,
  } = useQueryRegistry(25, target)
  const [startingHash, setStartingHash] = useState<string | null>(null)
  const [retryingPrewarm, setRetryingPrewarm] = useState(false)
  const [prewarmError, setPrewarmError] = useState<string | null>(null)
  const autoPrewarmTarget = useRef<string | null>(null)
  const [paramDialog, setParamDialog] = useState<{
    hash: string
    sql: string
    label: string
    initialValues: Record<string, unknown>
  } | null>(null)
  const { data, error, refetch } = useQuery({
    queryKey: ['readyset-sandbox'],
    queryFn: fetchSandboxDiagnostics,
    refetchInterval: 5_000,
  })
  const latestRunByQuery = useMemo(() => {
    const latest = new Map<string, (typeof runs)[number]>()
    for (const run of runs) {
      if (
        (run.kind === 'speed_test' || run.kind === 'cache_test') &&
        run.target === target &&
        run.queryHash
      ) {
        latest.set(run.queryHash, run)
      }
    }
    return latest
  }, [runs, target])
  const dockerReady =
    data?.docker_installed === true && data?.docker_running === true

  const startTest = async (hash: string, sql: string, label: string) => {
    if (!target || !dockerReady) return
    setStartingHash(hash)
    try {
      await startCacheTestRun({
        query: sql,
        target,
        query_hash: hash,
        label,
        iterations: 15,
        warmup: 5,
      })
    } finally {
      setStartingHash(null)
    }
  }

  const requestTest = (entry: (typeof queries)[number]) => {
    const label = entry.tag?.trim() || `Query ${entry.hash.slice(0, 8)}`
    const initialValues = entry.most_recent_params ?? {}
    const concreteSql = fillCapturedParams(entry.sql, initialValues)
    if (hasParameters(concreteSql)) {
      setParamDialog({
        hash: entry.hash,
        sql: entry.sql,
        label,
        initialValues,
      })
      return
    }
    void startTest(entry.hash, concreteSql, label)
  }

  const retryPrewarm = useCallback(async () => {
    if (!target) return
    setRetryingPrewarm(true)
    setPrewarmError(null)
    try {
      await queueSandboxPrewarm(target)
      await refetch()
    } catch (retryError) {
      setPrewarmError(
        retryError instanceof Error
          ? retryError.message
          : 'Readyset could not be prepared for this database.'
      )
    } finally {
      setRetryingPrewarm(false)
    }
  }, [refetch, target])

  useEffect(() => {
    setPrewarmError(null)
  }, [target])

  // Readyset work begins only on this explicit Comparisons surface and only
  // after the backend confirms Docker is available.
  useEffect(() => {
    if (
      !target ||
      !dockerReady ||
      data?.phase !== 'absent' ||
      autoPrewarmTarget.current === target
    ) {
      return
    }
    autoPrewarmTarget.current = target
    void retryPrewarm()
  }, [data?.phase, dockerReady, retryPrewarm, target])

  useEffect(() => {
    if (!deepLinkHash || queriesLoading) return
    const query = Array.from(
      document.querySelectorAll<HTMLElement>('[data-query-hash]')
    ).find((element) => element.dataset.queryHash === deepLinkHash)
    query?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }, [deepLinkHash, queriesLoading])

  const isPreparing =
    data?.phase === 'provisioning' || data?.phase === 'removing'
  const sandboxPreparationError =
    data?.last_error && (!data.failed_target || data.failed_target === target)
      ? data.last_error
      : null

  return (
    <div className="w-full space-y-6">
      <HStack className="items-center gap-4">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-primary-soft">
          <Icon
            name="database-settings"
            label=""
            className="h-6 w-6 text-content-primary-soft"
          />
        </div>
        <VStack className="items-start gap-1">
          <Text as="h1" level="headline-3" className="text-content-layout-1">
            Readyset Comparisons
          </Text>
          <Text level="body-small" className="text-content-layout-3">
            Temporary demo experiments. RDST removes each test cache and keeps
            one local sandbox warm for up to 24 hours.
          </Text>
        </VStack>
      </HStack>

      {data && !dockerReady && (
        <Card>
          <Card.Content className="p-5">
            <HStack className="items-start justify-between gap-5 flex-wrap">
              <HStack className="items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-surface-warning-soft">
                  <Icon
                    name="alert"
                    label=""
                    className="h-5 w-5 text-content-warning-soft"
                  />
                </div>
                <VStack className="items-start gap-1">
                  <Text level="headline-5" className="text-content-layout-1">
                    {data.docker_installed
                      ? 'Start Docker to try Readyset'
                      : 'Install Docker to try Readyset'}
                  </Text>
                  <Text
                    level="body-small"
                    className="max-w-2xl text-content-layout-2"
                  >
                    Analyze Query works without Docker. Docker is only required
                    to run this temporary local Readyset comparison.
                  </Text>
                </VStack>
              </HStack>
              <HStack className="items-center gap-2">
                {!data.docker_installed && (
                  <Button
                    variant="primary"
                    modifier="solid"
                    size="small"
                    label="Get Docker"
                    onClick={() =>
                      window.open(
                        DOCKER_INSTALL_URL,
                        '_blank',
                        'noopener,noreferrer'
                      )
                    }
                  />
                )}
                <Button
                  variant="primary"
                  modifier="outline"
                  size="small"
                  label="Check again"
                  onClick={() => void refetch()}
                />
              </HStack>
            </HStack>
          </Card.Content>
        </Card>
      )}

      {dockerReady && isPreparing && (
        <HStack className="items-center gap-3 rounded-lg border border-border-primary-soft/30 bg-surface-primary-soft/10 px-4 py-3">
          <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-content-primary-soft border-t-transparent" />
          <Text level="body-small" className="text-content-layout-2">
            Preparing Readyset for {target || 'this database'}… Choose a query
            now; its test will start when Readyset is ready.
          </Text>
        </HStack>
      )}

      {error && (
        <Text
          level="body-small"
          className="rounded-lg border border-border-negative-soft bg-surface-negative-soft/10 px-4 py-3 text-content-negative-soft"
        >
          Readyset status is temporarily unavailable. Tests may not start until
          the connection recovers.
        </Text>
      )}

      {data?.dirty_reason && (
        <Text
          level="body-small"
          className="rounded-lg border border-border-warning-soft bg-surface-warning-soft/10 px-4 py-3 text-content-warning-soft"
        >
          The previous test could not be cleaned up. RDST will start with a
          fresh Readyset instance for the next test.
        </Text>
      )}

      {sandboxPreparationError && (
        <HStack className="items-center justify-between gap-3 rounded-lg border border-border-negative-soft bg-surface-negative-soft/10 px-4 py-3">
          <Text level="body-small" className="text-content-negative-soft">
            {sandboxPreparationError}
          </Text>
          <Button
            variant="negative"
            modifier="outline"
            size="small"
            label="Retry preparation"
            loading={retryingPrewarm}
            disabled={!target || retryingPrewarm}
            onClick={() => void retryPrewarm()}
          />
        </HStack>
      )}

      {prewarmError && !sandboxPreparationError && (
        <HStack className="items-center justify-between gap-3 rounded-lg border border-border-negative-soft bg-surface-negative-soft/10 px-4 py-3">
          <Text level="body-small" className="text-content-negative-soft">
            {prewarmError}
          </Text>
          <Button
            variant="negative"
            modifier="outline"
            size="small"
            label="Retry preparation"
            loading={retryingPrewarm}
            disabled={!target || retryingPrewarm}
            onClick={() => void retryPrewarm()}
          />
        </HStack>
      )}

      <Card>
        <Card.Content className="space-y-4 p-5">
          <HStack className="items-center justify-between gap-3">
            <VStack className="items-start gap-1">
              <Text level="label-medium" className="text-content-layout-1">
                Queries to comparison
              </Text>
              <Text level="caption" className="text-content-layout-3">
                Compare your database with a temporary Readyset cache.
              </Text>
            </VStack>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Manage queries"
              onClick={() => navigate({ to: '/query-registry' })}
            />
          </HStack>

          {queriesLoading ? (
            <Text level="body-small" className="text-content-layout-3">
              Loading queries…
            </Text>
          ) : listError ? (
            <Text level="body-small" className="text-content-negative-soft">
              {listError}
            </Text>
          ) : queries.length === 0 ? (
            <VStack className="items-start gap-3 rounded-lg bg-surface-layout-2 p-4">
              <Text level="body-small" className="text-content-layout-2">
                No queries are available for {target || 'this target'}.
              </Text>
              <Button
                variant="rising"
                modifier="solid"
                size="small"
                label="Add a query"
                onClick={() => navigate({ to: '/query-registry' })}
              />
            </VStack>
          ) : (
            <VStack className="items-stretch gap-3">
              {queries.map((entry) => {
                const run = latestRunByQuery.get(entry.hash)
                const unsupported = isNotCacheable(entry.readyset_supported)
                const running =
                  (startingHash === entry.hash && run?.status !== 'failed') ||
                  run?.status === 'running' ||
                  run?.status === 'reconnecting' ||
                  run?.status === 'stopping'
                return (
                  <QueryCard
                    key={entry.hash}
                    data-testid="speed-test-query"
                    data-query-hash={entry.hash}
                    className={
                      deepLinkHash === entry.hash
                        ? 'ring-2 ring-border-primary-soft ring-offset-2 ring-offset-surface-layout-1'
                        : undefined
                    }
                    sql={entry.sql}
                    title={
                      <Text
                        level="label-medium"
                        className="text-content-layout-1"
                      >
                        {entry.tag?.trim() || `Query ${entry.hash.slice(0, 8)}`}
                      </Text>
                    }
                    badges={
                      <QueryCacheStatus
                        cached={run?.status === 'done' && Boolean(run.result)}
                        readysetSupported={entry.readyset_supported}
                        testing={running}
                        speedup={run?.result?.speedup_mean}
                      />
                    }
                    actions={
                      <Button
                        variant="rising"
                        modifier={run?.result ? 'ghost' : 'solid'}
                        size="small"
                        icon="database-settings"
                        iconPosition="left"
                        label={
                          unsupported
                            ? 'Unsupported'
                            : run?.result
                              ? 'Re-test'
                              : 'Compare with Readyset'
                        }
                        loading={running}
                        disabled={running || unsupported || !dockerReady}
                        onClick={() => requestTest(entry)}
                      />
                    }
                    expansion={
                      running || run?.result || run?.status === 'failed' ? (
                        <div className="space-y-3 border-t border-border-layout-1 px-4 py-4">
                          {running && (
                            <Text
                              level="body-small"
                              className="text-content-primary-soft"
                            >
                              {run?.message || 'Starting comparison…'}
                            </Text>
                          )}
                          {run?.status === 'failed' && (
                            <Text
                              level="body-small"
                              className="text-content-negative-soft"
                            >
                              {run.message}
                            </Text>
                          )}
                          {run?.result && (
                            <ComparisonCard result={run.result} />
                          )}
                        </div>
                      ) : undefined
                    }
                  />
                )
              })}
            </VStack>
          )}
        </Card.Content>
      </Card>

      {paramDialog && (
        <ParameterDialog
          isOpen
          query={paramDialog.sql}
          initialValues={paramDialog.initialValues}
          submitLabel="Run test"
          submitIcon="speedometer"
          onClose={() => setParamDialog(null)}
          onSubmit={(sql) => {
            const pending = paramDialog
            setParamDialog(null)
            void startTest(pending.hash, sql, pending.label)
          }}
        />
      )}
    </div>
  )
}
