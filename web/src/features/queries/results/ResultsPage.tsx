// The full analysis page: the crumb bar (database / Queries / Full analysis) with its
// four actions, the hero (plan score, name, provenance, verdict, three numbers) and the
// cards. A monitored query reads the contract's view from its digest; a run started
// here or a stored record is mapped into the same view. While a run is under way the
// gauge gives way to a ring, the steps replace the verdict and the rest dims.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { ErrorState } from '@rs/ui-new/error-state'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { Link, useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { TargetConnectivityNotice } from '@/components/TargetConnectivityNotice'
import { TargetLockNotice } from '@/components/TargetLockNotice'
import { ParameterDialog } from '@/components/top'
import { stepsAt } from '@/features/database/analyze/analyzingSteps'
import {
  provenance,
  useQueryDigest,
} from '@/features/database/queries/QueryDetail'
import { formatClock, formatRelative } from '@/features/monitor-kit/format'
import { isPlanOnly } from '@/lib/analysisPayload'
import { fixtureOverride } from '@/lib/monitor/contract'
import { asCaScenario, caFixtures } from '@/lib/monitor/fixturesCa'
import { useMonitorQuery } from '@/lib/monitor/hooks'
import type { AnalysisView } from '@/lib/monitor/types'
import { deriveQueryName } from '@/lib/queryIdentity'
import {
  AlsoCard,
  AnalysisFoot,
  BenchmarkCard,
  FixCard,
  PlanCard,
  QueryCard,
  ReadysetCard,
  RewritesCard,
} from './AnalysisCards'
import { AnalysisHero } from './AnalysisHero'
import { AnalyzeConsent } from './AnalyzeConsent'
import {
  EMPTY_VIEW,
  plannerSetup,
  stepForStage,
  viewFromDigest,
  viewFromResults,
} from './analysisView'
import { ResultsError } from './ResultsError'
import type { ResultsSearch } from './types'
import { useResultsController } from './useResultsController'

function useNow(): number {
  const scenario = asCaScenario(fixtureOverride())
  if (scenario) return caFixtures(scenario).now
  return Math.floor(Date.now() / 60000) * 60
}

const crumbLink = 'hover:text-content-layout-1'

function Crumb({ target }: { target: string | undefined }) {
  return (
    <span className="flex items-center gap-2 text-body-small font-normal text-content-layout-3">
      {target && (
        <>
          <Link
            to="/database"
            search={{ target } as never}
            className={crumbLink}
          >
            {target}
          </Link>
          <span>/</span>
        </>
      )}
      <Link
        to="/database"
        search={{ target, view: 'live' } as never}
        className={crumbLink}
      >
        Queries
      </Link>
      <span>/</span>
      <span className="text-content-layout-1">Full analysis</span>
    </span>
  )
}

function PageSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true">
      <Skeleton className="h-33 w-full" />
      <Skeleton className="h-48 w-full rounded-card" />
      <Skeleton className="h-40 w-full rounded-card" />
    </div>
  )
}

export function ResultsPage({ search }: { search: ResultsSearch }) {
  const navigate = useNavigate()
  const { toast } = useToast()
  const now = useNow()
  const controller = useResultsController(search, {
    jobLabel: deriveQueryName(search.query),
  })
  const {
    query,
    analysis,
    stored,
    passwordLock,
    connectivity,
    parameters,
    consent,
    actions,
  } = controller
  const target = query.target ?? ''
  const key = search.hash ?? ''
  const monitored = useMonitorQuery(target, key, '24h')
  const digest = useQueryDigest(target, key, Boolean(target && key))

  const running = analysis.state === 'analyzing'
  const [startedAt, setStartedAt] = useState<number | null>(null)
  useEffect(() => {
    if (running) setStartedAt((current) => current ?? now)
    else setStartedAt(null)
  }, [running, now])

  const digestMatches =
    digest.data &&
    (!search.analysisId ||
      (digest.data.analysis_ref ?? '').endsWith(`/${search.analysisId}`))
  const view: AnalysisView | null = useMemo(() => {
    const finished =
      analysis.state === 'complete' && analysis.results
        ? viewFromResults(
            analysis.results,
            analysis.rewriteTesting,
            analysis.readysetCacheability,
            stored.isActive ? undefined : parameters.initialValues
          )
        : null
    if (finished && !stored.isActive) return finished
    if (digest.data && digestMatches) return viewFromDigest(digest.data)
    if (finished) return finished
    return digest.data ? viewFromDigest(digest.data) : null
  }, [
    analysis,
    digest.data,
    digestMatches,
    stored.isActive,
    parameters.initialValues,
  ])

  // A link by key alone carries no SQL: the monitored row supplies it.
  const sql = query.sql || monitored.data?.text || ''
  const name =
    monitored.data?.display_name ??
    (sql ? deriveQueryName(sql) : 'Full analysis')
  const source = digest.data && digestMatches ? provenance(digest.data) : null
  const planOnly =
    source?.tag === 'Estimated' ||
    (!source && isPlanOnly(analysis.results?.explain_results))
  const tag = source?.tag ?? (planOnly ? 'Estimated' : 'Measured')
  const analyzedAt = digest.data?.analyzed_at ?? null
  const share = monitored.data?.db_time_share ?? null
  const meta = [
    running
      ? `started ${formatClock(startedAt ?? now)}`
      : source?.reason
        ? source.reason.split(',')[0]
        : planOnly
          ? 'planner only'
          : null,
    !running && analyzedAt != null
      ? `analyzed ${formatRelative(analyzedAt, now)}`
      : null,
    share != null ? `${Math.round(share * 100)}% of database time` : null,
  ].filter((item): item is string => Boolean(item))

  const fixSql = view?.fix_detail?.sql ?? null
  const copy = useCallback(
    (text: string) =>
      void navigator.clipboard
        ?.writeText(text)
        .then(() => toast({ title: 'Copied', duration: 2000 })),
    [toast]
  )
  const analyzeAgain = () => {
    if (!query.sql) {
      void navigate({
        to: '/results',
        search: { ...search, query: sql, analysisId: undefined },
      })
      return
    }
    if (stored.isActive) actions.reRunStored('manual')
    else actions.runAgainDeliberately()
  }

  const storedMissing =
    stored.isActive &&
    !stored.isLoading &&
    !stored.record &&
    Boolean(stored.error)
  const loading =
    (stored.isActive && stored.isLoading) ||
    (Boolean(key) && digest.isPending && !view) ||
    (!query.sql && monitored.isPending)

  const bar = (
    <PageBar
      title={<Crumb target={query.target} />}
      actions={
        running ? (
          <Button
            variant="primary"
            modifier="ghost"
            label="Cancel"
            onClick={actions.cancelRun}
          />
        ) : (
          <>
            {fixSql && (
              <Button
                variant="primary"
                modifier="solid"
                label="Copy fix"
                onClick={() => copy(fixSql)}
              />
            )}
            <Button
              variant="primary"
              modifier="ghost"
              label="Analyze again"
              onClick={analyzeAgain}
            />
            {key && (
              <Button
                variant="primary"
                modifier="ghost"
                label="Benchmark"
                onClick={() =>
                  void navigate({
                    to: '/database',
                    search: {
                      target,
                      view: 'live',
                      query: key,
                      panel: 'benchmark',
                    } as never,
                  })
                }
              />
            )}
            <Button
              variant="primary"
              modifier="ghost"
              label="Send to Ask"
              onClick={() =>
                void navigate({
                  to: '/ask',
                  search: { sql: query.sql } as never,
                })
              }
            />
          </>
        )
      }
    />
  )

  return (
    <>
      {bar}
      <PageContainer className="pt-9 pb-24">
        {stored.isActive ? null : passwordLock.isLocked ? (
          <TargetLockNotice
            message={passwordLock.message}
            requirements={passwordLock.missingTargetRequirements}
            keyringAvailable={passwordLock.keyringAvailable}
          />
        ) : (
          <TargetConnectivityNotice
            target={passwordLock.targetName ?? query.target}
            failure={connectivity.failure}
            isChecking={connectivity.isChecking}
            onRetry={() => void actions.runAgain()}
          />
        )}
        {storedMissing ? (
          <ErrorState
            tone="quiet"
            title="This analysis is no longer available"
            message="The link points at a saved analysis this install no longer has."
            detail={stored.error ?? undefined}
            action={{ label: 'Back to queries', onClick: actions.goBack }}
          />
        ) : analysis.state === 'error' ? (
          <ResultsError
            error={analysis.error}
            errorEnvelope={analysis.errorEnvelope}
            onRecover={actions.recover}
            onRetry={actions.runAgain}
          />
        ) : loading ? (
          <PageSkeleton />
        ) : (
          <>
            <AnalysisHero
              name={name}
              view={view ?? EMPTY_VIEW}
              tag={tag}
              meta={meta}
              meanMs={monitored.data?.mean_ms ?? null}
              steps={
                running ? stepsAt(stepForStage(analysis.progress?.stage)) : null
              }
            />
            {analysis.state === 'cancelled' && (
              <Text
                as="p"
                level="body-medium"
                className="mt-6 text-content-layout-2"
              >
                Analysis cancelled.
              </Text>
            )}
            {view && (
              <div
                data-testid="analysis-cards"
                aria-busy={running || undefined}
                className={cn(
                  'mt-8 grid grid-cols-2 gap-3',
                  running && 'pointer-events-none opacity-50'
                )}
              >
                {view.fix_detail && (
                  <FixCard
                    fix={view.fix_detail}
                    onCopy={copy}
                    plannerSetup={
                      analysis.state === 'complete'
                        ? plannerSetup(analysis.results?.index_testing)
                        : null
                    }
                  />
                )}
                {view.benchmark && view.benchmark.after_ms != null && (
                  <BenchmarkCard
                    benchmark={view.benchmark}
                    onRerun={
                      key
                        ? () =>
                            void navigate({
                              to: '/database',
                              search: {
                                target,
                                view: 'live',
                                query: key,
                              } as never,
                            })
                        : undefined
                    }
                  />
                )}
                {view.plan_findings.length > 0 && (
                  <PlanCard findings={view.plan_findings} />
                )}
                {view.rewrites.length > 0 && (
                  <RewritesCard
                    rewrites={view.rewrites}
                    method={view.benchmark?.method ?? null}
                  />
                )}
                {view.also.length > 0 && <AlsoCard also={view.also} />}
                {view.readyset && (
                  <ReadysetCard
                    readyset={view.readyset}
                    onVerify={actions.setUpCaching}
                    onCompare={
                      key
                        ? () =>
                            void navigate({
                              to: '/database',
                              search: {
                                target,
                                view: 'live',
                                query: key,
                              } as never,
                            })
                        : undefined
                    }
                  />
                )}
                <QueryCard
                  sql={sql}
                  params={view.params}
                  paramSource={view.param_source}
                  explainText={view.explain_text}
                />
              </div>
            )}
            {view?.run && (
              <div className={cn(running && 'opacity-50')}>
                <AnalysisFoot run={view.run} />
              </div>
            )}
          </>
        )}
      </PageContainer>

      <ParameterDialog
        isOpen={parameters.isOpen}
        onClose={actions.cancelParameters}
        onSubmit={actions.submitParameters}
        query={query.sql}
        target={passwordLock.targetName ?? query.target}
        initialValues={parameters.initialValues}
      />
      <AnalyzeConsent
        presentation="modal"
        isOpen={consent.isOpen}
        skipFuturePrompts={consent.skipFuturePrompts}
        onSkipFuturePrompts={actions.setSkipAnalyzeConsent}
        onCancel={actions.cancelAnalysis}
        onConfirm={actions.confirmAnalysis}
      />
    </>
  )
}
