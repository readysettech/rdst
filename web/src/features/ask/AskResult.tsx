import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useCallback, useState } from 'react'
import { SQLDisplay } from '../../components/SQLDisplay'
import { TableHeaderCell } from '../../components/TableHeaderCell'
import type { AskResultEvent, AskSqlGeneratedEvent } from '../../lib/ask'
import { createCsvFilename, downloadCsv, toCsv } from '../../lib/csv'

interface AskResultProps {
  question: string
  result: AskResultEvent
  sqlGenerated?: AskSqlGeneratedEvent
  answeredFrom: string
  provenanceSource: string
  savedTag?: string
  executedSql: string
  limitAdded: boolean
  onViewInQueries: () => void
  onAnalyze: () => void
  onRefine: () => void
  onNewQuestion: () => void
}

export function AskResult({
  question,
  result,
  sqlGenerated,
  answeredFrom,
  provenanceSource,
  savedTag,
  executedSql,
  limitAdded,
  onViewInQueries,
  onAnalyze,
  onRefine,
  onNewQuestion,
}: AskResultProps) {
  const [showSql, setShowSql] = useState(false)

  return (
    <m.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="w-full"
    >
      <VStack className="gap-4 items-start w-full">
        <Card className="w-full overflow-hidden">
          <Card.Content className="p-5">
            <HStack className="w-full items-start justify-between gap-5 flex-wrap">
              <VStack className="min-w-0 items-start gap-2">
                <HStack className="items-center gap-2">
                  <Icon
                    name="tick-double"
                    label="Answered"
                    className="size-4 shrink-0 text-content-positive-soft"
                  />
                  <Text
                    level="overline"
                    className="uppercase tracking-wider text-content-positive-soft"
                  >
                    Verified answer
                  </Text>
                </HStack>
                {question && (
                  <Text level="headline-4" className="text-content-layout-1">
                    {question}
                  </Text>
                )}
                <HStack className="items-center gap-2 flex-wrap">
                  <Icon
                    name="database"
                    label="Source"
                    className="size-3.5 text-content-layout-3"
                  />
                  <Text level="caption" className="text-content-layout-3">
                    Answered from{' '}
                    <span className="font-medium text-content-layout-2">
                      {answeredFrom}
                    </span>{' '}
                    via {provenanceSource}
                  </Text>
                  {savedTag && (
                    <>
                      <span className="text-content-layout-3">·</span>
                      <Text level="caption" className="text-content-layout-3">
                        Saved as{' '}
                        <span className="font-medium text-content-layout-2">
                          {savedTag}
                        </span>
                      </Text>
                    </>
                  )}
                </HStack>
                {sqlGenerated?.explanation && (
                  <Text
                    level="body-small"
                    className="max-w-4xl text-content-layout-2"
                  >
                    {sqlGenerated.explanation}
                  </Text>
                )}
              </VStack>
              <HStack className="items-center gap-2">
                {result.query_hash && (
                  <Button
                    onClick={onViewInQueries}
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    label="Open in Queries"
                    icon="arrow-right"
                    iconPosition="right"
                  />
                )}
                <Button
                  onClick={onAnalyze}
                  variant="rising"
                  modifier="solid"
                  size="small"
                  label="Analyze query"
                  icon="querypilot"
                  iconPosition="left"
                />
              </HStack>
            </HStack>
          </Card.Content>
        </Card>

        <ResultsTable
          result={result}
          target={answeredFrom}
          onRefine={onRefine}
        />

        <SqlDisclosure
          sql={executedSql}
          open={showSql}
          onToggle={() => setShowSql((value) => !value)}
          limitAdded={limitAdded}
        />

        <div className="w-full border-t border-border-layout-1" />
        <HStack className="justify-between items-center gap-4 w-full flex-wrap">
          <VStack className="gap-0.5 items-start">
            <Text level="label-small" className="text-content-layout-1">
              Ask another question
            </Text>
            <Text level="caption" className="text-content-layout-3">
              Start fresh — this answer stays in Queries.
            </Text>
          </VStack>
          <Button
            onClick={onNewQuestion}
            variant="primary"
            modifier="outline"
            size="small"
            label="Ask another"
            icon="add"
            iconPosition="left"
          />
        </HStack>
      </VStack>
    </m.div>
  )
}

function SqlDisclosure({
  sql,
  open,
  onToggle,
  limitAdded,
}: {
  sql: string
  open: boolean
  onToggle: () => void
  limitAdded?: boolean
}) {
  if (!sql) return null

  return (
    <div className="relative w-full">
      <Disclosure
        className="w-full bg-surface-layout-1"
        open={open}
        onOpenChange={onToggle}
        panelClassName="p-0"
        trigger={
          <HStack className="gap-2 items-center min-w-0">
            <Text level="label-small" className="text-content-layout-2">
              Show the SQL that ran
            </Text>
            <Text level="caption" className="text-content-layout-3">
              · read-only, capped at 1,000
            </Text>
          </HStack>
        }
      >
        <div className="bg-surface-layout-2">
          <SQLDisplay sql={sql} className="p-4" />
        </div>
        {limitAdded && (
          <div className="p-4 border-t border-border-layout-1">
            <HStack className="gap-2 items-start">
              <Icon
                name="info"
                label="Note"
                className="size-4 shrink-0 mt-0.5 text-content-info-soft"
              />
              <Text
                level="body-small"
                className="text-content-layout-2 leading-relaxed"
              >
                A <code className="font-mono">LIMIT</code> was added to keep the
                result set bounded.
              </Text>
            </HStack>
          </div>
        )}
      </Disclosure>
      <div className="absolute right-10 top-1.5">
        <CopyButton text={sql} />
      </div>
    </div>
  )
}

function ResultsTable({
  result,
  target,
  onRefine,
}: {
  result: AskResultEvent
  target?: string
  onRefine: () => void
}) {
  const handleDownloadCsv = useCallback(() => {
    const csv = toCsv(result.columns, result.rows)
    downloadCsv(csv, createCsvFilename())
  }, [result])
  const hasRows = result.rows.length > 0

  return (
    <Card className="w-full overflow-hidden">
      <Card.Header className="border-b border-border-layout-1">
        <HStack className="justify-between items-center w-full gap-3 flex-wrap">
          <HStack className="gap-3 items-center">
            <div className="flex size-8 items-center justify-center rounded-lg bg-surface-info-soft">
              <Icon
                name="dashboard"
                label="Results"
                className="size-4 text-content-info-soft"
              />
            </div>
            <Card.Title>Answer</Card.Title>
          </HStack>
          <HStack className="gap-3 items-center flex-wrap">
            {hasRows && (
              <Button
                onClick={handleDownloadCsv}
                variant="primary"
                modifier="outline"
                size="small"
                label="Download CSV"
                icon="arrow-down"
                iconPosition="left"
              />
            )}
            <Tag
              variant="informative"
              modifier="ghost"
              size="small"
              label={`${result.row_count} rows`}
            />
            <Tag
              variant="positive"
              modifier="ghost"
              size="small"
              label={`${result.execution_time_ms.toFixed(1)}ms`}
            />
            {target && (
              <Tag
                variant="informative"
                modifier="ghost"
                size="small"
                label={target}
              />
            )}
          </HStack>
        </HStack>
      </Card.Header>
      <Card.Content className="p-0 overflow-hidden">
        {hasRows ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2 border-b border-border-layout-1">
                  {result.columns.map((column) => (
                    <TableHeaderCell key={column} className="whitespace-nowrap">
                      {column}
                    </TableHeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 50).map((row, rowIndex) => (
                  <m.tr
                    key={`${rowIndex}-${row.map(String).join('-')}`}
                    className="border-b border-border-layout-1 last:border-b-0 hover:bg-surface-layout-2/50 transition-colors"
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: Math.min(rowIndex * 0.02, 0.5) }}
                  >
                    {row.map((cell, cellIndex) => (
                      <td
                        key={`${cellIndex}-${String(cell)}`}
                        className="px-4 py-3 text-content-layout-1 text-mono-small whitespace-nowrap"
                      >
                        {cell === null ? (
                          <span className="text-content-layout-3 italic">
                            NULL
                          </span>
                        ) : (
                          String(cell)
                        )}
                      </td>
                    ))}
                  </m.tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-8 text-center">
            <Icon
              name="empty"
              label="No rows"
              className="size-12 mx-auto mb-3 text-content-layout-3"
            />
            <Text level="body-medium" className="text-content-layout-2">
              No rows matched
            </Text>
            <Text level="body-small" className="mt-1 text-content-layout-3">
              Try broadening or rephrasing your question.
            </Text>
            <Button
              onClick={onRefine}
              variant="primary"
              modifier="ghost"
              size="small"
              label="Refine question"
              className="mt-4"
            />
          </div>
        )}
      </Card.Content>
      {result.rows.length > 50 && (
        <Card.Footer className="border-t border-border-layout-1">
          <HStack className="gap-2 items-center">
            <Icon
              name="info"
              label="Info"
              className="size-4 text-content-layout-3"
            />
            <Text level="body-small" className="text-content-layout-3">
              Showing first 50 of {result.rows.length} rows
            </Text>
          </HStack>
        </Card.Footer>
      )}
    </Card>
  )
}
