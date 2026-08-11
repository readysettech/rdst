import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useState } from 'react'
import { AskClarification } from '../features/ask/AskClarification'
import { AskComposer } from '../features/ask/AskComposer'
import { AskErrorState } from '../features/ask/AskErrorState'
import { AskHistory } from '../features/ask/AskHistory'
import { AskProgress } from '../features/ask/AskProgress'
import { AskResult } from '../features/ask/AskResult'
import { fetchAskExamples, fetchAskHistory } from '../lib/api'
import { useAsk } from '../lib/ask'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { TrialRegistrationDialog } from './TrialRegistrationDialog'

interface AskPanelProps {
  target?: string | null
  disabled?: boolean
  beforeRun?: () => Promise<boolean>
}

function sourceLabel(source: string | undefined): string {
  return source === 'semantic' ? 'semantic layer' : 'live introspection'
}

export function AskPanel({
  target,
  disabled = false,
  beforeRun,
}: AskPanelProps) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [question, setQuestion] = useState('')
  const [askedTarget, setAskedTarget] = useState<string | null>(null)
  const [showTrialDialog, setShowTrialDialog] = useState(false)
  const {
    ask,
    resumeWithAnswers,
    cancel,
    state,
    status,
    schemaLoaded,
    clarification,
    sqlGenerated,
    result,
    error,
    reset,
  } = useAsk()

  const examplesQuery = useQuery({
    queryKey: ['ask', 'examples', target],
    queryFn: () => fetchAskExamples(target!),
    staleTime: 5 * 60_000,
    enabled: !!target,
  })
  const historyQuery = useQuery({
    queryKey: ['ask', 'history', target],
    queryFn: () => fetchAskHistory(target, 50),
    enabled: !!target,
  })

  const handleSubmit = useCallback(async () => {
    if (disabled || !question.trim()) return
    if (beforeRun && !(await beforeRun())) return
    setAskedTarget(target ?? null)
    await ask({ question: question.trim(), target: target || undefined })
  }, [ask, beforeRun, disabled, question, target])

  const handleRetry = useCallback(async () => {
    if (disabled || !question.trim()) return
    const retryTarget = askedTarget ?? target ?? null
    if (beforeRun && !(await beforeRun())) return
    setAskedTarget(retryTarget)
    await ask({
      question: question.trim(),
      target: retryTarget || undefined,
    })
  }, [ask, askedTarget, beforeRun, disabled, question, target])

  const handleClarificationSubmit = useCallback(
    async (answers: Record<string, string>) => {
      if (disabled) return
      if (beforeRun && !(await beforeRun())) return
      await resumeWithAnswers(answers)
    },
    [beforeRun, disabled, resumeWithAnswers]
  )

  const handleNewQuestion = useCallback(() => {
    reset()
    setQuestion('')
  }, [reset])

  const handleRefine = useCallback(() => {
    reset()
  }, [reset])

  const handleExampleClick = useCallback(
    (example: string) => {
      if (!disabled) setQuestion(example)
    },
    [disabled]
  )

  const handleReask = useCallback(
    async (pastQuestion: string) => {
      if (disabled || !pastQuestion.trim()) return
      if (beforeRun && !(await beforeRun())) return
      setQuestion(pastQuestion)
      setAskedTarget(target ?? null)
      await ask({
        question: pastQuestion.trim(),
        target: target || undefined,
      })
    },
    [ask, beforeRun, disabled, target]
  )

  const handleAnalyze = useCallback(() => {
    const sql = result?.sql || sqlGenerated?.sql
    if (!sql) return
    const answerTarget = schemaLoaded?.target || askedTarget || target
    navigate({
      to: '/results',
      search: { query: sql, target: answerTarget || undefined },
    })
  }, [result, sqlGenerated, schemaLoaded, askedTarget, target, navigate])

  const handleViewInQueries = useCallback(() => {
    navigate({
      to: '/queries',
      search: { hash: result?.query_hash || undefined },
    })
  }, [navigate, result?.query_hash])

  const isLoading = state === 'loading' || state === 'generating'
  const executedSql = result?.sql ?? sqlGenerated?.sql ?? ''
  const limitAdded =
    result?.limit_added ??
    Boolean(
      result?.sql &&
        sqlGenerated?.sql &&
        /\blimit\b/i.test(result.sql) &&
        !/\blimit\b/i.test(sqlGenerated.sql)
    )
  const runsAgainst = target ?? 'demo'
  const answeredFrom = schemaLoaded?.target || askedTarget || 'demo'

  return (
    <VStack className="gap-6 w-full">
      {state === 'idle' && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
          className="grid w-full gap-4 laptop:grid-cols-3"
        >
          <div className="min-w-0 laptop:col-span-2">
            <AskComposer
              question={question}
              target={runsAgainst}
              disabled={disabled}
              examples={examplesQuery.data?.examples ?? []}
              examplesLoading={examplesQuery.isLoading}
              examplesError={examplesQuery.isError}
              onQuestionChange={setQuestion}
              onSubmit={() => void handleSubmit()}
              onExample={handleExampleClick}
              onRetryExamples={() => void examplesQuery.refetch()}
            />
          </div>
          <div className="min-w-0">
            <AskHistory
              items={historyQuery.data?.items ?? []}
              disabled={disabled}
              loading={historyQuery.isLoading}
              error={historyQuery.isError}
              onReask={(pastQuestion) => void handleReask(pastQuestion)}
              onRetry={() => void historyQuery.refetch()}
            />
          </div>
        </m.div>
      )}

      {isLoading && (
        <m.div
          className="w-full"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
        >
          <AskProgress
            status={status}
            schemaLoaded={schemaLoaded}
            question={question}
            onCancel={cancel}
          />
        </m.div>
      )}

      {state === 'cancelled' && (
        <Card className="w-full">
          <Card.Content className="p-6">
            <HStack className="items-center justify-between gap-4 flex-wrap">
              <HStack className="items-center gap-3">
                <div className="flex size-10 items-center justify-center rounded-xl bg-surface-layout-2">
                  <Icon
                    name="close"
                    label="Cancelled"
                    className="size-5 text-content-layout-2"
                  />
                </div>
                <VStack className="items-start gap-1">
                  <Text level="subtitle-1" className="text-content-layout-1">
                    Question cancelled
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    Nothing changed in {answeredFrom}. Your question is ready to
                    run again.
                  </Text>
                </VStack>
              </HStack>
              <HStack className="items-center gap-2">
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Edit question"
                  onClick={handleRefine}
                />
                <Button
                  variant="primary"
                  modifier="solid"
                  label="Run again"
                  icon="observe"
                  iconPosition="left"
                  onClick={() => void handleRetry()}
                />
              </HStack>
            </HStack>
          </Card.Content>
        </Card>
      )}

      {state === 'clarification_needed' && clarification && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <AskClarification
            question={question}
            questions={clarification.questions}
            onSubmit={handleClarificationSubmit}
            onEditQuestion={handleRefine}
            disabled={disabled}
          />
        </m.div>
      )}

      {state === 'complete' && result && (
        <AskResult
          question={question}
          result={result}
          sqlGenerated={sqlGenerated}
          answeredFrom={answeredFrom}
          provenanceSource={sourceLabel(schemaLoaded?.source)}
          savedTag={result.query_tag || ''}
          executedSql={executedSql}
          limitAdded={limitAdded}
          onViewInQueries={handleViewInQueries}
          onAnalyze={handleAnalyze}
          onRefine={handleRefine}
          onNewQuestion={handleNewQuestion}
        />
      )}

      {state === 'error' && error && (
        <m.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
        >
          <AskErrorState
            error={error}
            target={target}
            onRetry={handleRetry}
            onNewQuestion={handleNewQuestion}
            onStartTrial={() => setShowTrialDialog(true)}
          />
        </m.div>
      )}

      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          setShowTrialDialog(false)
        }}
      />
    </VStack>
  )
}
