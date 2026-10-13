// The Ask page's body: the question box, then whatever the question is doing (the
// steps, a choice to make, the answer, a failure), the first visit's three questions,
// and the Earlier list.
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AskAnswer } from '../features/ask/AskAnswer'
import { AskBox, type AskEmphasis } from '../features/ask/AskBox'
import { AskChoice } from '../features/ask/AskChoice'
import { AskEarlier } from '../features/ask/AskEarlier'
import { AskFailure } from '../features/ask/AskFailure'
import { AskSteps } from '../features/ask/AskSteps'
import { hasPlaceholders } from '../features/ask/askModel'
import { fetchAskExamples, fetchAskHistory } from '../lib/api'
import { useAsk } from '../lib/ask'

const SUGGESTIONS = 3

interface AskPanelProps {
  initialQuestion?: string
  target?: string | null
  /** The default target is still being looked up, so nothing is known yet. */
  targetResolving?: boolean
  disabled?: boolean
  beforeRun?: () => Promise<boolean>
}

function Suggestions({
  examples,
  disabled,
  onPick,
}: {
  examples: string[]
  disabled: boolean
  onPick: (question: string) => void
}) {
  if (examples.length === 0) return null
  return (
    <DividedList columns="1fr auto" className="mt-5">
      {examples.slice(0, SUGGESTIONS).map((example) => (
        <ListRow
          key={example}
          asChild
          interactive
          className="min-h-10.5 py-1.5"
        >
          <Pressable
            data-testid="ask-suggestion"
            disabled={disabled}
            className="w-full text-left"
            onClick={() => onPick(example)}
          >
            <span className="min-w-0 truncate text-body-small text-content-layout-2">
              {example}
            </span>
            <Icon
              name="chevron-right"
              label=""
              size="small"
              className="justify-self-end text-content-layout-3"
            />
          </Pressable>
        </ListRow>
      ))}
    </DividedList>
  )
}

export function AskPanel({
  initialQuestion,
  target,
  targetResolving = false,
  disabled = false,
  beforeRun,
}: AskPanelProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [question, setQuestion] = useState(initialQuestion ?? '')
  const [askedTarget, setAskedTarget] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // A question sent by link leaves the URL once it is asked.
  const fromLink = useRef(Boolean(initialQuestion))
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
  const runsAgainst = target ?? null

  const examples = useQuery({
    queryKey: ['ask', 'examples', target],
    queryFn: () => fetchAskExamples(target as string),
    staleTime: 5 * 60_000,
    enabled: Boolean(target),
  })
  const history = useQuery({
    queryKey: ['ask', 'history', target],
    queryFn: () => fetchAskHistory(target, 50),
    enabled: Boolean(target),
  })
  // A finished question joins Earlier questions.
  useEffect(() => {
    if (state === 'complete')
      void queryClient.invalidateQueries({ queryKey: ['ask', 'history'] })
  }, [state, queryClient])

  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (disabled || !trimmed || !runsAgainst) return
      if (beforeRun && !(await beforeRun())) return
      if (fromLink.current) {
        fromLink.current = false
        void navigate({
          to: '/ask',
          search: ((prev: Record<string, unknown>) => ({
            ...prev,
            sql: undefined,
            params: undefined,
          })) as never,
          replace: true,
        })
      }
      setAskedTarget(runsAgainst)
      await ask({ question: trimmed, target: runsAgainst })
    },
    [ask, beforeRun, disabled, runsAgainst, navigate]
  )
  const askAgain = useCallback(
    (text: string) => {
      setQuestion(text)
      void submit(text)
    },
    [submit]
  )
  const edit = useCallback(() => {
    reset()
    inputRef.current?.focus()
  }, [reset])
  const choose = useCallback(
    async (answers: Record<string, string>) => {
      if (disabled) return
      if (beforeRun && !(await beforeRun())) return
      await resumeWithAnswers(answers)
    },
    [beforeRun, disabled, resumeWithAnswers]
  )

  // The answer replaces the steps, so the keyboard is told where it went: focus lands
  // on the outcome and a live region names it.
  const outcomeRef = useRef<HTMLDivElement>(null)
  const outcome =
    state === 'complete' && result
      ? 'Answer ready.'
      : state === 'clarification_needed' && clarification
        ? 'One more detail needed before answering.'
        : state === 'error' && error
          ? 'The question could not be answered.'
          : ''
  useEffect(() => {
    if (outcome) outcomeRef.current?.focus()
  }, [outcome])

  const busy = state === 'loading' || state === 'generating'
  const answeredFrom = schemaLoaded?.target || askedTarget || target || ''
  const executedSql = result?.sql ?? sqlGenerated?.sql ?? ''
  const limitAdded =
    result?.limit_added ??
    Boolean(
      result?.sql &&
        sqlGenerated?.sql &&
        /\blimit\b/i.test(result.sql) &&
        !/\blimit\b/i.test(sqlGenerated.sql)
    )
  const emphasis: AskEmphasis =
    state === 'clarification_needed'
      ? 'ghost'
      : state === 'error'
        ? 'outline'
        : 'solid'
  const items = history.data?.items ?? []
  const firstVisit =
    state === 'idle' &&
    !history.isLoading &&
    !targetResolving &&
    items.length === 0

  return (
    <div className="w-full">
      {hasPlaceholders(question) && !busy && (
        <Text as="p" level="caption" className="mb-3 text-content-layout-3">
          This query has parameters. Say which values to use.
        </Text>
      )}
      <AskBox
        question={question}
        target={runsAgainst}
        disabled={disabled}
        submitDisabled={!runsAgainst}
        busy={busy}
        emphasis={emphasis}
        inputRef={inputRef}
        onChange={setQuestion}
        onSubmit={() => void submit(question)}
      />
      {firstVisit && (
        <Suggestions
          examples={examples.data?.examples ?? []}
          disabled={disabled}
          onPick={setQuestion}
        />
      )}
      {busy && <AskSteps phase={status?.phase} onCancel={cancel} />}
      <div
        ref={outcomeRef}
        tabIndex={-1}
        data-testid="ask-outcome"
        className="outline-none"
      >
        <span aria-live="polite" className="sr-only">
          {outcome}
        </span>
        {state === 'clarification_needed' && clarification && (
          <AskChoice
            clarification={clarification}
            disabled={disabled}
            onRun={(answers) => void choose(answers)}
          />
        )}
        {state === 'complete' && result && (
          <AskAnswer
            result={result}
            answeredFrom={answeredFrom}
            explanation={sqlGenerated?.explanation ?? null}
            executedSql={executedSql}
            limitAdded={limitAdded}
            onOpenInDatabase={
              result.query_hash
                ? () =>
                    void navigate({
                      to: '/database',
                      search: { query: result.query_hash } as never,
                    })
                : null
            }
            onAnalyze={() =>
              void navigate({
                to: '/results',
                search: {
                  query: executedSql,
                  target: answeredFrom || undefined,
                  origin: 'ask',
                },
              })
            }
            onAskAgain={() => askAgain(question)}
            onEdit={edit}
          />
        )}
        {state === 'error' && error && (
          <AskFailure
            error={error}
            target={askedTarget ?? target ?? null}
            sql={executedSql}
            limitAdded={limitAdded}
            onEdit={edit}
            onRetry={() => void submit(question)}
          />
        )}
      </div>
      <AskEarlier
        items={items}
        target={runsAgainst}
        loading={history.isLoading || targetResolving}
        disabled={disabled || busy}
        nowMs={Date.now()}
        onAskAgain={askAgain}
      />
    </div>
  )
}
