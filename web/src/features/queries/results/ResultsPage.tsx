import { BaseInputCheckbox } from '@rs/ui-new/base-input-checkbox'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { InteractivePanel } from '../../../components/InteractivePanel'
import { QueryCard } from '../../../components/QueryCard'
import { TargetConnectivityNotice } from '../../../components/TargetConnectivityNotice'
import { TargetLockNotice } from '../../../components/TargetLockNotice'
import { ParameterDialog } from '../../../components/top'
import { AnalysisResults } from './AnalysisResults'
import type { ResultsSearch } from './types'
import { useResultsController } from './useResultsController'

export function ResultsPage({ search }: { search: ResultsSearch }) {
  const controller = useResultsController(search)
  const {
    query,
    analysis,
    passwordLock,
    connectivity,
    cache,
    parameters,
    consent,
    chat,
    actions,
  } = controller
  const databaseEngine = analysis.results?.explain_results?.database_engine
  const queryMeta = [
    query.target ? `Target ${query.target}` : null,
    databaseEngine?.toUpperCase(),
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="w-full space-y-6">
      <m.header
        className="flex flex-col items-start justify-between gap-4 tablet:flex-row tablet:items-end"
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
      >
        <VStack className="items-start gap-2">
          <Button
            variant="primary"
            modifier="link"
            size="small"
            icon="arrow-left"
            iconPosition="left"
            label="Back to queries"
            onClick={actions.goToQueries}
            className="no-underline"
          />
          <VStack className="items-start gap-1">
            <Text as="h1" level="headline-2" className="text-content-layout-1">
              Query analysis
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Measured performance, practical improvements, and Readyset fit.
            </Text>
          </VStack>
        </VStack>
      </m.header>

      <QueryCard
        sql={query.sql}
        leading={
          <Icon
            name="querypilot"
            label=""
            aria-hidden="true"
            className="h-4 w-4 text-content-layout-3"
          />
        }
        title={
          <Text level="label-small" className="text-content-layout-1">
            Analyzed query
          </Text>
        }
        badges={
          <Tag
            variant="neutral"
            modifier="ghost"
            size="small"
            label={query.fast ? 'Fast analysis' : 'Detailed analysis'}
          />
        }
        meta={queryMeta}
        primaryAction={
          analysis.state === 'complete' ? (
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Run analysis again"
              icon="play"
              iconPosition="left"
              onClick={actions.runAgain}
            />
          ) : undefined
        }
      />

      {passwordLock.isLocked ? (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      ) : null}

      {!passwordLock.isLocked ? (
        <TargetConnectivityNotice
          target={passwordLock.targetName ?? query.target}
          failure={connectivity.failure}
          isChecking={connectivity.isChecking}
          onRetry={() => void actions.runAgain()}
          retryLabel="Try analysis again"
        />
      ) : null}

      {!passwordLock.isLocked &&
      !connectivity.failure &&
      !connectivity.isChecking ? (
        <AnalysisResults
          state={analysis.state}
          progress={analysis.progress}
          results={analysis.results}
          rewriteTesting={analysis.rewriteTesting}
          readysetCacheability={analysis.readysetCacheability}
          error={analysis.error}
          errorEnvelope={analysis.errorEnvelope}
          target={query.target}
          cacheDeployed
          onCacheQuery={actions.cacheQuery}
          onSetUpCaching={actions.setUpCaching}
          onRecover={actions.recover}
          onRetry={actions.runAgain}
          isCaching={cache.isPending}
          onAskFollowUp={actions.openInteractive}
          hasExistingChat={chat.hasExisting}
        />
      ) : null}

      {parameters.hasParameters &&
      !parameters.isOpen &&
      analysis.state === 'idle' ? (
        <Card>
          <Card.Content className="p-4">
            <HStack className="items-center justify-between gap-4 flex-wrap">
              <HStack className="items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface-warning-soft">
                  <Icon
                    name="alert"
                    label=""
                    aria-hidden="true"
                    className="h-4 w-4 text-content-warning-soft"
                  />
                </div>
                <VStack className="items-start gap-0.5">
                  <Text level="label-small" className="text-content-layout-1">
                    Parameter values required
                  </Text>
                  <Text level="body-small" className="text-content-layout-2">
                    Enter values before this query can be measured.
                  </Text>
                </VStack>
              </HStack>
              <Button
                variant="primary"
                modifier="outline"
                size="small"
                label="Enter parameter values"
                icon="edit"
                iconPosition="left"
                onClick={actions.openParameters}
              />
            </HStack>
          </Card.Content>
        </Card>
      ) : null}

      {analysis.state === 'complete' && analysis.results?.query_hash ? (
        <InteractivePanel
          isOpen={chat.isOpen}
          onClose={actions.closeInteractive}
          queryHash={analysis.results.query_hash}
          analysisResults={chat.results}
        />
      ) : null}

      <ParameterDialog
        isOpen={parameters.isOpen}
        onClose={actions.cancelParameters}
        onSubmit={actions.submitParameters}
        query={query.sql}
        target={passwordLock.targetName ?? query.target}
        initialValues={parameters.initialValues}
      />

      <ConfirmDialog
        isOpen={consent.isOpen}
        onClose={actions.cancelAnalysis}
        onConfirm={actions.confirmAnalysis}
        title="Run EXPLAIN ANALYZE?"
        notice={{
          accent: 'warning',
          icon: 'alert',
          message:
            'Analyze runs EXPLAIN ANALYZE, which executes your query once against the database to measure it. Cancel if this query should not be executed.',
        }}
        confirmLabel="Run analyze"
        confirmVariant="primary"
        cancelLabel="Cancel"
      >
        <HStack className="items-center gap-3">
          <BaseInputCheckbox
            checked={consent.skipFuturePrompts}
            onCheckedChange={(checked) =>
              actions.setSkipAnalyzeConsent(checked === true)
            }
            aria-label="Don't ask again"
          />
          <Text level="body-small" className="text-content-layout-2">
            Don't ask again
          </Text>
        </HStack>
      </ConfirmDialog>
    </div>
  )
}
