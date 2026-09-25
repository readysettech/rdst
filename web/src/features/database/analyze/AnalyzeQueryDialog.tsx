// "Analyze a query" on the Database page: paste SQL, fill in parameter values when it
// has placeholders, and run a deep analysis against this database through the same
// analyze API as the rest of RDST. The result shows in place, and the query lands in
// the list with its analysis attached.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputRadioGroup } from '@rs/ui-new/base-input-radio-group'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { ParameterDialog } from '@/components/top'
import { ScrollDialog } from '@/features/monitor-kit/ScrollDialog'
import { AnalysisResults } from '@/features/queries/results/AnalysisResults'
import { startAnalysisRun, useAnalysisRun } from '@/lib/analysisRuns'
import { useAddQuery, useMonitorSettings } from '@/lib/monitor/hooks'
import {
  EXECUTION_OPTIONS,
  type ExecutionMode,
  executionMode,
} from '../settings/prerequisites'
import { addQueryInput, checkPaste, pastedSql } from './analyzeQueryModel'

type Step = 'edit' | 'values' | 'run'

export function AnalyzeQueryDialog({
  target,
  open,
  onClose,
  onShowQuery,
  initialSql = '',
}: {
  target: string
  open: boolean
  onClose: () => void
  /** Open the query's row in the list. */
  onShowQuery: (key: string) => void
  initialSql?: string
}) {
  const settings = useMonitorSettings(target).data
  const [sql, setSql] = useState(initialSql)
  const [step, setStep] = useState<Step>('edit')
  const [mode, setMode] = useState<ExecutionMode | null>(null)
  const [runKey, setRunKey] = useState<string | null>(null)
  const run = useAnalysisRun(runKey)
  const add = useAddQuery()
  // A monitored database's own choice, else EXPLAIN ANALYZE.
  const chosen: ExecutionMode =
    mode ?? (settings?.enabled ? executionMode(settings) : 'execute')
  const check = checkPaste(sql)

  useEffect(() => {
    if (!open) return
    setSql(initialSql)
    setStep('edit')
    setRunKey(null)
    add.reset()
  }, [open, initialSql])

  const { mutate: file } = add
  const start = (statement: string) => {
    setRunKey(
      startAnalysisRun(
        { query: statement, target, fast: chosen === 'plans_only' },
        {
          queryLabel: 'Pasted query',
          onComplete: (results) =>
            file({
              target,
              input: addQueryInput(sql, results, chosen === 'plans_only'),
            }),
        }
      )
    )
    setStep('run')
  }

  const submit = () => {
    if (!check.ok) return
    if (check.needsValues) setStep('values')
    else start(pastedSql(sql))
  }

  const close = onClose

  const addedKey =
    add.variables?.target === target &&
    add.variables.input.sql === pastedSql(sql)
      ? add.data?.key
      : undefined
  const footer =
    step === 'edit' ? (
      <div className="flex flex-col gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
        <Text as="p" level="caption" className="text-content-layout-3">
          {check.ok && check.needsValues
            ? 'It has placeholders, so you pick the values next.'
            : 'RDST analyzes it once and adds it to the list.'}
        </Text>
        <div className="flex shrink-0 justify-end gap-2">
          <Button label="Cancel" modifier="ghost" onClick={close} />
          <Button
            label={check.ok && check.needsValues ? 'Next' : 'Run deep analysis'}
            icon={check.ok && check.needsValues ? 'arrow-right' : 'speedometer'}
            iconPosition={check.ok && check.needsValues ? 'right' : 'left'}
            disabled={!check.ok}
            onClick={submit}
          />
        </div>
      </div>
    ) : step === 'run' ? (
      <div className="flex flex-col gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
        <Text
          as="p"
          level="caption"
          className="text-content-layout-3"
          data-testid="analyze-query-filed"
        >
          {run?.state === 'analyzing'
            ? 'You can close this window. The analysis keeps running.'
            : addedKey
              ? 'Added to the query list with this analysis.'
              : add.isError
                ? "The analysis finished, but it wasn't added to the list."
                : run?.state === 'complete'
                  ? 'Adding it to the query list'
                  : ''}
        </Text>
        <div className="flex shrink-0 justify-end gap-2">
          <Button
            label="Analyze another"
            modifier="ghost"
            disabled={run?.state === 'analyzing'}
            onClick={() => {
              setStep('edit')
              setRunKey(null)
              add.reset()
            }}
          />
          {addedKey ? (
            <Button
              label="Show in the list"
              icon="arrow-right"
              iconPosition="right"
              onClick={() => {
                onClose()
                onShowQuery(addedKey)
              }}
            />
          ) : (
            <Button label="Close" modifier="outline" onClick={close} />
          )}
        </div>
      </div>
    ) : null

  return (
    <ScrollDialog
      open={open}
      onClose={close}
      size="extra-large"
      title="Analyze a query"
      description={`Paste a query that runs on ${target}. RDST reads its plan on this database, suggests fixes and adds it to the list.`}
      footer={footer}
      testId="analyze-query"
    >
      {step === 'edit' ? (
        <div className="space-y-5">
          <Text as="p" level="caption" className="text-content-layout-3">
            Automatic analyses cover the busiest queries. You can analyze this
            one now. Raise how many run automatically in{' '}
            <a
              className="underline"
              href={`/database?target=${encodeURIComponent(target)}&panel=settings#monitor-cap`}
            >
              Settings
            </a>
            .
          </Text>
          <div className="space-y-2">
            <label
              htmlFor="analyze-query-sql"
              className="block text-label-medium text-content-layout-1"
            >
              SQL
            </label>
            <BaseInputTextarea
              id="analyze-query-sql"
              value={sql}
              onChange={(event) => setSql(event.target.value)}
              placeholder={
                'SELECT id, total FROM orders WHERE customer_id = $1'
              }
              rows={8}
              spellCheck={false}
              className="min-h-40 font-mono text-mono-small"
            />
            {!check.ok && check.reason ? (
              <Text
                as="p"
                level="caption"
                className="flex items-center gap-1.5 text-content-warning-plain"
              >
                <Icon name="alert" label="" size="small" />
                {check.reason}
              </Text>
            ) : (
              <Text as="p" level="caption" className="text-content-layout-3">
                One read statement. Placeholders like $1, ? or :name are fine;
                you give them values next.
              </Text>
            )}
          </div>
          <div className="space-y-2.5">
            <Text as="p" level="label-medium" className="text-content-layout-1">
              How to read it
            </Text>
            <BaseInputRadioGroup
              aria-label="How to read it"
              value={chosen}
              onValueChange={(value) => setMode(value as ExecutionMode)}
              options={EXECUTION_OPTIONS.map((option) => ({
                ...option,
                label:
                  option.value === 'execute'
                    ? 'Run EXPLAIN ANALYZE'
                    : option.label,
              }))}
            />
          </div>
        </div>
      ) : step === 'values' ? (
        <ParameterDialog
          presentation="inline"
          isOpen
          query={pastedSql(sql)}
          target={target}
          onClose={() => setStep('edit')}
          onSubmit={start}
          submitLabel="Run deep analysis"
          submitIcon="speedometer"
        />
      ) : (
        <div className="space-y-4" data-testid="analyze-query-result">
          {add.isError ? (
            <Alert
              variant="warning"
              modifier="outline"
              icon="alert"
              iconPosition="left"
              label={
                add.error instanceof Error
                  ? add.error.message
                  : "The query wasn't added to the list."
              }
            />
          ) : null}
          {run ? (
            <AnalysisResults
              state={run.state}
              progress={run.progress}
              results={run.results}
              rewriteTesting={run.rewriteTesting}
              readysetCacheability={run.readysetCacheability}
              error={run.error}
              errorEnvelope={run.errorEnvelope}
              target={target}
              onRetry={() => start(run.request.query)}
            />
          ) : null}
        </div>
      )}
    </ScrollDialog>
  )
}
