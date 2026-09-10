import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { lazy, Suspense, useState } from 'react'
import { QueryCard } from '../../../components/QueryCard'
import { SQLInput } from '../../../components/SQLInput'
import { TargetConnectivityNotice } from '../../../components/TargetConnectivityNotice'
import { TargetLockNotice } from '../../../components/TargetLockNotice'
import { ParameterDialog } from '../../../components/top'
import { AnalysisResults } from './AnalysisResults'
import { AnalyzeConsent } from './AnalyzeConsent'
import { StoredAnalysisHeader } from './StoredAnalysisHeader'
import type { ResultsController } from './useResultsController'

// The chat stack (AI SDK transport, markdown renderer) is only worth loading
// once someone actually asks a follow-up question, so the panel is a chunk of
// its own — the same one the analyze drawer's Follow-up tab pulls.
const InteractivePanel = lazy(() =>
  import('../../../components/InteractivePanel').then((module) => ({
    default: module.InteractivePanel,
  }))
)

/** Container placeholder while a stored record is read back. */
function StoredAnalysisSkeleton() {
  return (
    <Card aria-hidden="true">
      <Card.Content className="space-y-3 p-4">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </Card.Content>
    </Card>
  )
}

/**
 * The analysis itself: stored header, the query, the notices that explain why
 * it cannot run, and the results. `/results` and the Query Library's analyze
 * drawer both render this — the shell around it is the only difference.
 *
 * `followUp` is `'chat'` on the full page, where the interactive panel has room
 * to open. The drawer passes `'none'` and offers the full view instead.
 */
export function ResultsBody({
  controller,
  followUp = 'chat',
  prompts = 'modal',
}: {
  controller: ResultsController
  followUp?: 'chat' | 'none'
  /**
   * Where the pre-run steps (parameter values, EXPLAIN ANALYZE consent) are
   * asked. `'inline'` keeps them in this body, for a surface that is already
   * an overlay: a dialog opened over the analyze drawer covers it and, being
   * the higher dismissable layer, makes everything below it unclickable.
   */
  prompts?: 'modal' | 'inline'
}) {
  const {
    query,
    analysis,
    stored,
    passwordLock,
    connectivity,
    cache,
    parameters,
    consent,
    chat,
    actions,
  } = controller

  // A failure the database blames on the query itself is answered by changing
  // the query, so the card that shows the SQL becomes the place to fix it.
  // [B-21]
  const [sqlDraft, setSqlDraft] = useState<string | null>(null)
  const queryShapeFailed =
    analysis.state === 'error' && analysis.errorEnvelope?.code === 'invalid_sql'
  const isEditingSql = sqlDraft !== null

  const inlinePrompts = prompts === 'inline'
  // A pre-run step owns the body until the user answers it: showing an empty
  // results shell underneath would suggest the run had already started.
  const hasInlinePrompt =
    inlinePrompts && (parameters.isOpen || consent.isOpen) && !stored.isActive

  // A stored-analysis link that resolved to nothing: the record is gone, so
  // there is no body to explain in place and no live run to fall back on.
  const storedAnalysisMissing =
    stored.isActive && !stored.isLoading && !stored.record && !!stored.error
  const databaseEngine = analysis.results?.explain_results?.database_engine
  // A run exists once one has been asked for: before that the card is showing
  // the query as written, and calling it "analyzed" would be a claim about
  // work that has not started (B-04).
  const hasRun = stored.isActive || analysis.state !== 'idle'
  const queryMeta = [
    query.target ? `Target ${query.target}` : null,
    databaseEngine?.toUpperCase(),
    hasRun && parameters.isSubstituted ? 'with your values' : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <>
      {stored.record ? (
        <StoredAnalysisHeader
          createdAt={stored.record.created_at}
          target={stored.record.target || query.target || ''}
          overallRating={stored.record.overall_rating}
          efficiencyScore={stored.record.efficiency_score}
          hasBody={stored.hasBody}
          history={stored.history}
          currentAnalysisId={stored.record.analysis_id}
          onOpenAnalysis={actions.openStoredAnalysis}
          onReRun={actions.reRunStored}
        />
      ) : null}

      {/* One query block per screen. While the inline parameter form is
          collecting values it carries the query itself, with the placeholders
          highlighted, so the card stands down until there is something else to
          show (B-04). */}
      {hasInlinePrompt && parameters.isOpen ? null : (
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
              {hasRun ? 'Analyzed query' : 'Query'}
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
          editor={
            isEditingSql ? (
              <SQLInput
                value={sqlDraft}
                onChange={setSqlDraft}
                minHeight="10rem"
                target={query.target}
                showPrettify
              />
            ) : undefined
          }
          secondaryActions={
            isEditingSql ? (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Cancel"
                onClick={() => setSqlDraft(null)}
              />
            ) : undefined
          }
          primaryAction={
            isEditingSql ? (
              <Button
                variant="primary"
                modifier="solid"
                size="small"
                label="Analyze"
                icon="play"
                iconPosition="left"
                disabled={!sqlDraft.trim() || sqlDraft.trim() === query.sql}
                onClick={() => {
                  const edited = sqlDraft.trim()
                  setSqlDraft(null)
                  actions.editQuery(edited)
                }}
              />
            ) : queryShapeFailed && !stored.isActive ? (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Edit query"
                icon="edit"
                iconPosition="left"
                onClick={() => setSqlDraft(query.sql)}
              />
            ) : analysis.state === 'complete' && !stored.isActive ? (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Run analysis again"
                icon="play"
                iconPosition="left"
                onClick={actions.runAgainDeliberately}
              />
            ) : undefined
          }
        />
      )}

      {hasInlinePrompt ? (
        <AnalyzeConsent
          presentation="inline"
          isOpen={consent.isOpen}
          skipFuturePrompts={consent.skipFuturePrompts}
          onSkipFuturePrompts={actions.setSkipAnalyzeConsent}
          onCancel={actions.cancelAnalysis}
          onConfirm={actions.confirmAnalysis}
        />
      ) : null}

      {hasInlinePrompt && parameters.isOpen ? (
        <ParameterDialog
          presentation="inline"
          isOpen
          onClose={actions.cancelParameters}
          onEscape={actions.dismissParameters}
          onSubmit={actions.submitParameters}
          query={query.sql}
          target={passwordLock.targetName ?? query.target}
          initialValues={parameters.initialValues}
        />
      ) : null}

      {/* Reading a stored analysis needs no database access, so target lock
          and reachability are not in its way. */}
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

      {stored.isLoading ? <StoredAnalysisSkeleton /> : null}

      {stored.record && !stored.hasBody ? (
        <Alert
          variant="informative"
          modifier="outline"
          icon="info"
          iconPosition="left"
          label="This analysis ran before full results were saved, so only its date and score were kept. Re-run it to see the plan, rewrites and index findings."
        />
      ) : null}

      {!hasInlinePrompt &&
      (stored.isActive ||
        (!passwordLock.isLocked &&
          !connectivity.failure &&
          !connectivity.isChecking)) ? (
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
          onAskFollowUp={
            followUp === 'chat' ? actions.openInteractive : undefined
          }
          hasExistingChat={chat.hasExisting}
          storedAnalysisMissing={storedAnalysisMissing}
          storedAnalysisDetail={stored.error ?? undefined}
          onBrowseQueries={actions.goBack}
        />
      ) : null}

      {/* Consent is the step before values, so while it is on screen this card
          would be a second call to action for a question not yet reached. */}
      {parameters.hasParameters &&
      !parameters.isOpen &&
      !consent.isOpen &&
      !stored.isActive &&
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

      {followUp === 'chat' &&
      chat.isOpen &&
      analysis.state === 'complete' &&
      analysis.results?.query_hash ? (
        <Suspense fallback={null}>
          <InteractivePanel
            isOpen
            onClose={actions.closeInteractive}
            queryHash={analysis.results.query_hash}
            analysisResults={chat.results}
          />
        </Suspense>
      ) : null}

      {inlinePrompts ? null : (
        <ParameterDialog
          isOpen={parameters.isOpen}
          onClose={actions.cancelParameters}
          onSubmit={actions.submitParameters}
          query={query.sql}
          target={passwordLock.targetName ?? query.target}
          initialValues={parameters.initialValues}
        />
      )}

      <AnalyzeConsent
        presentation="modal"
        isOpen={!inlinePrompts && consent.isOpen}
        skipFuturePrompts={consent.skipFuturePrompts}
        onSkipFuturePrompts={actions.setSkipAnalyzeConsent}
        onCancel={actions.cancelAnalysis}
        onConfirm={actions.confirmAnalysis}
      />
    </>
  )
}
