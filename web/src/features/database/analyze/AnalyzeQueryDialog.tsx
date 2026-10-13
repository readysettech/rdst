// "Analyze a query" on the Database page: paste SQL, give placeholders their values when
// it has any, and RDST adds the query to the list and runs its deep analysis there.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { ParameterDialog } from '@/components/top'
import {
  analysisDidntStart,
  useAddQuery,
  useEnqueueAnalysis,
} from '@/lib/monitor/hooks'
import { DialogShell } from '../../../components/DialogShell'
import { checkPaste, pastedSql } from './analyzeQueryModel'

const PLACEHOLDER =
  'SELECT id, title FROM posts\nWHERE owneruserid = $1\nORDER BY creationdate DESC LIMIT $2'

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
  /** Close the dialog and open the query's pane, once its analysis is queued. */
  onShowQuery: (key: string) => void
  initialSql?: string
}) {
  const [sql, setSql] = useState(initialSql)
  const [values, setValues] = useState(false)
  const add = useAddQuery()
  const enqueue = useEnqueueAnalysis(target, { inline: true })
  const check = checkPaste(sql)

  useEffect(() => {
    if (!open) return
    setSql(initialSql)
    setValues(false)
    add.reset()
    enqueue.reset()
  }, [open, initialSql])

  const { mutate: file } = add
  const { mutate: queue } = enqueue
  const start = (statement: string) =>
    file(
      { target, input: { sql: statement } },
      {
        // A refused analysis keeps the dialog open with the reason and the SQL to edit.
        onSuccess: (added) =>
          queue(added.key, {
            onSuccess: () => onShowQuery(added.key),
            onError: () => setValues(false),
          }),
        onError: () => setValues(false),
      }
    )

  const submit = () => {
    if (!check.ok) return
    if (check.needsValues) setValues(true)
    else start(pastedSql(sql))
  }

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title="Analyze a query"
      testId="analyze-query"
      note="Takes 10 to 30 seconds."
      action={
        values ? null : (
          <Button
            variant="primary"
            modifier="solid"
            label="Analyze"
            loading={add.isPending || enqueue.isPending}
            disabled={!check.ok}
            onClick={submit}
          />
        )
      }
    >
      {values ? (
        <ParameterDialog
          presentation="inline"
          isOpen
          query={pastedSql(sql)}
          target={target}
          onClose={() => setValues(false)}
          onSubmit={start}
          submitLabel="Analyze"
        />
      ) : (
        <>
          <BaseInputTextarea
            aria-label="SQL"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            placeholder={PLACEHOLDER}
            rows={5}
            spellCheck={false}
            className="mt-3 min-h-33 resize-none font-mono text-mono-medium"
          />
          <Text
            as="p"
            level="caption"
            className={
              !check.ok && check.reason
                ? 'mt-2.5 text-content-warning-soft'
                : 'mt-2.5 text-content-layout-3'
            }
          >
            {!check.ok && check.reason
              ? check.reason
              : 'Runs EXPLAIN and, when timings are on, a read-only sample with a time limit.'}
          </Text>
          {add.isError && (
            <Alert
              tone="quiet"
              variant="negative"
              icon="alert"
              className="mt-3"
              label={
                add.error instanceof Error
                  ? add.error.message
                  : "The query wasn't added to the list."
              }
            />
          )}
          {enqueue.isError && (
            <Alert
              tone="quiet"
              variant="negative"
              icon="alert"
              className="mt-3"
              label={analysisDidntStart(enqueue.error)}
            />
          )}
        </>
      )}
    </DialogShell>
  )
}
