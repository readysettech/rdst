// The answer: one status line, the lead sentence built from the top row, the
// explanation, the table as a divided list with a rank column and mono numbers, the
// ghost actions and "SQL that ran" folded. A query that matched nothing keeps the
// status line's shape with a grey icon and two ghosts.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useId, useMemo, useState } from 'react'
import { SqlCode } from '@/features/database/queries/SqlCode'
import type { AskResultEvent } from '@/lib/ask'
import { createCsvFilename, downloadCsv, toCsv } from '@/lib/csv'
import {
  addedLimit,
  cellText,
  leadSentence,
  numericColumns,
  ROWS_SHOWN,
  rowsWord,
  secondsWord,
} from './askModel'

const hairlineLink =
  'border-b border-border-layout-2 hover:border-content-layout-2'

export function StatusLine({
  tone,
  children,
}: {
  tone: 'positive' | 'off'
  children: ReactNode[]
}) {
  return (
    <div
      data-testid="ask-status"
      className="flex items-center gap-2.5 text-body-small text-content-layout-2"
    >
      {tone === 'positive' ? (
        <Icon
          name="tick"
          label="Answered"
          size="small"
          className="text-content-positive-soft"
        />
      ) : (
        <Icon
          name="minus"
          label="No rows"
          size="small"
          className="text-content-layout-3"
        />
      )}
      {children.map((part, index) => (
        <span key={index} className="flex items-center gap-2.5">
          {index > 0 && <span className="text-border-layout-2">|</span>}
          {part}
        </span>
      ))}
    </div>
  )
}

/** "SQL that ran", folded, with the added LIMIT named in the caption. */
export function SqlDisclosure({
  title,
  sql,
  limitAdded,
  open,
  onToggle,
}: {
  title: string
  sql: string
  limitAdded: boolean
  open: boolean
  onToggle: () => void
}) {
  const id = useId()
  return (
    <div className="mt-6">
      <Pressable
        aria-expanded={open}
        aria-controls={id}
        className="flex cursor-pointer items-center gap-2 text-body-small text-content-layout-2"
        onClick={onToggle}
      >
        <Icon
          name="chevron-right"
          label=""
          size="small"
          className={cn(
            'text-content-layout-3 transition-transform duration-fast',
            open && 'rotate-90'
          )}
        />
        {title}
        {limitAdded && (
          <Text
            as="span"
            level="caption"
            className="ml-1 text-content-layout-3"
          >
            LIMIT {addedLimit(sql)} added
          </Text>
        )}
      </Pressable>
      {open && (
        <div id={id} className="mt-3">
          <SqlCode sql={sql} testId="ask-sql" />
        </div>
      )}
    </div>
  )
}

function Table({ result }: { result: AskResultEvent }) {
  const [all, setAll] = useState(false)
  const numeric = useMemo(() => numericColumns(result), [result])
  const nameIndex = numeric.findIndex((isNumber) => !isNumber)
  const rows = all ? result.rows : result.rows.slice(0, ROWS_SHOWN)
  const more = result.rows.length - ROWS_SHOWN
  const columns = `36px ${result.columns
    .map((_, index) =>
      index === nameIndex ? 'minmax(0,1fr)' : 'minmax(96px,auto)'
    )
    .join(' ')}`
  return (
    <div data-testid="ask-table" className="mt-5">
      <DividedList
        columns={columns}
        head={[
          { label: '' },
          ...result.columns.map((column, index) => ({
            label: column,
            align: numeric[index] ? ('end' as const) : ('start' as const),
          })),
        ]}
      >
        {rows.map((row, rowIndex) => (
          <ListRow
            key={`${rowIndex}-${row.map((cell) => cellText(cell)).join('|')}`}
            data-testid="ask-row"
            className="min-h-10.5 py-1.5"
          >
            <span className="text-caption tabular-nums text-content-layout-3">
              {rowIndex + 1}
            </span>
            {row.map((cell, cellIndex) => (
              <span
                key={`${cellIndex}-${cellText(cell)}`}
                className={cn(
                  'min-w-0 truncate',
                  numeric[cellIndex]
                    ? 'text-right font-mono text-mono-medium tabular-nums text-content-layout-1'
                    : 'text-body-small text-content-layout-1',
                  rowIndex === 0 && cellIndex === nameIndex && 'font-medium',
                  cell === null && 'text-content-layout-3'
                )}
              >
                {cellText(cell, result.columns[cellIndex])}
              </span>
            ))}
          </ListRow>
        ))}
      </DividedList>
      <div className="mt-2.5 flex items-center gap-3.5 text-caption text-content-layout-3">
        {more > 0 && (
          <Pressable
            className={cn('cursor-pointer', hairlineLink)}
            onClick={() => setAll((value) => !value)}
          >
            {all ? 'Fewer rows' : `${more} more ${more === 1 ? 'row' : 'rows'}`}
          </Pressable>
        )}
        <span className="flex-1" />
        <Pressable
          className="cursor-pointer hover:text-content-layout-1"
          onClick={() =>
            downloadCsv(toCsv(result.columns, result.rows), createCsvFilename())
          }
        >
          Download CSV
        </Pressable>
      </div>
    </div>
  )
}

export function AskAnswer({
  result,
  answeredFrom,
  explanation,
  executedSql,
  limitAdded,
  onOpenInDatabase,
  onAnalyze,
  onAskAgain,
  onEdit,
}: {
  result: AskResultEvent
  answeredFrom: string
  explanation: string | null
  executedSql: string
  limitAdded: boolean
  onOpenInDatabase: (() => void) | null
  onAnalyze: () => void
  onAskAgain: () => void
  onEdit: () => void
}) {
  const [sqlOpen, setSqlOpen] = useState(false)
  const noRows = result.rows.length === 0
  const lead = noRows ? null : leadSentence(result)
  const ghost = { variant: 'primary', modifier: 'ghost' } as const
  return (
    <div data-testid="ask-answer" className="mt-9">
      {noRows ? (
        <StatusLine tone="off">
          {['Query ran, no rows', secondsWord(result.execution_time_ms)]}
        </StatusLine>
      ) : (
        <StatusLine tone="positive">
          {[
            `Answered from ${answeredFrom}`,
            rowsWord(result.row_count),
            secondsWord(result.execution_time_ms),
            ...(result.query_tag
              ? [
                  <span key="tag">
                    saved as{' '}
                    <span className="font-mono text-mono-medium text-content-layout-2">
                      {result.query_tag}
                    </span>
                  </span>,
                ]
              : []),
          ]}
        </StatusLine>
      )}
      {lead && (
        <Text
          as="p"
          level="headline-5"
          className="mt-3 font-normal text-content-layout-1"
        >
          {lead}
        </Text>
      )}
      {explanation && (
        <Text
          as="p"
          level={noRows && !lead ? 'headline-5' : 'body-small'}
          className={cn(
            'mt-1.5',
            noRows && !lead
              ? 'mt-3 font-normal text-content-layout-1'
              : 'text-content-layout-3'
          )}
        >
          {explanation}
        </Text>
      )}
      {!noRows && <Table result={result} />}
      <div className="mt-5 flex gap-1.5">
        {noRows ? (
          <>
            <Button {...ghost} label="Edit question" onClick={onEdit} />
            {executedSql && (
              <Button
                {...ghost}
                label={sqlOpen ? 'Hide the SQL' : 'Show the SQL'}
                onClick={() => setSqlOpen((value) => !value)}
              />
            )}
          </>
        ) : (
          <>
            {onOpenInDatabase && (
              <Button
                {...ghost}
                label="Open in Database"
                onClick={onOpenInDatabase}
              />
            )}
            <Button {...ghost} label="Analyze query" onClick={onAnalyze} />
            <Button
              {...ghost}
              label="Ask again with today's data"
              onClick={onAskAgain}
            />
          </>
        )}
      </div>
      {executedSql && (noRows ? sqlOpen : true) && (
        <SqlDisclosure
          title="SQL that ran"
          sql={executedSql}
          limitAdded={limitAdded}
          open={sqlOpen}
          onToggle={() => setSqlOpen((value) => !value)}
        />
      )}
    </div>
  )
}
