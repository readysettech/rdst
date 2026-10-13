// The benchmark result in the pane: one verdict line, then a divided list with Before,
// After and Change in mono, then one caption on method, from the benchmark-fixes result.
// Healthy is neutral, so the gains carry no green.
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { SidePane } from '@rs/ui-new/side-pane'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Verdict } from '@rs/ui-new/verdict'
import { useMemo, useState } from 'react'
import { formatRelative, plural } from '@/features/monitor-kit/format'
import { useBenchmarkFixes } from '@/lib/monitor/hooks'
import type { MonitorQuery } from '@/lib/monitor/types'
import { DASH, latencyText } from '../databaseModel'
import {
  allFixesSql,
  benchmarkRows,
  benchmarkVerdict,
  changePercent,
  formatChange,
} from './fixBenchmarkModel'

const COLUMNS = 'minmax(0,1fr) 64px 64px 56px'

function CopyAll({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="primary"
      modifier="solid"
      size="small"
      label={copied ? 'Copied' : 'Copy all fixes'}
      disabled={!sql}
      onClick={() => {
        void navigator.clipboard?.writeText(sql).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 2000)
        })
      }}
    />
  )
}

export function BenchmarkResultPane({
  target,
  queries,
  ranAt,
  run,
  now,
  onRerun,
  onClose,
}: {
  target: string
  /** The queries with fixes the benchmark covered. */
  queries: MonitorQuery[]
  ranAt: number
  /** Identifies this run; a new one asks for a new estimate. */
  run: number
  now: number
  onRerun: () => void
  onClose: () => void
}) {
  const keys = useMemo(() => queries.map((query) => query.key), [queries])
  const result = useBenchmarkFixes(target, keys, run)
  const rows = useMemo(
    () => benchmarkRows(queries, result.data),
    [queries, result.data]
  )
  const verdict = benchmarkVerdict(rows, target, result.data?.share ?? null)

  return (
    <SidePane
      aria-label={`Benchmark, ${plural(queries.length, 'query', 'queries')}`}
      chip={
        <Tag
          size="small"
          variant="neutral"
          modifier="solid"
          className="rounded-pill whitespace-nowrap"
          label="Benchmark"
        />
      }
      meta={
        <span>
          {plural(queries.length, 'query', 'queries')},{' '}
          {formatRelative(ranAt, now)}
        </span>
      }
      onClose={onClose}
      footer={
        <>
          <CopyAll sql={allFixesSql(rows)} />
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Rerun"
            loading={result.isFetching}
            onClick={onRerun}
          />
        </>
      }
    >
      {result.isFetching ? (
        <Verdict lead="Estimating each fix." className="mb-5.5" />
      ) : (
        <Verdict lead={verdict.lead} className="mb-5.5">
          {verdict.rest}
        </Verdict>
      )}
      <DividedList
        columns={COLUMNS}
        head={[
          { label: 'Query' },
          { label: 'Before, ms', align: 'end' },
          {
            label: rows.every((row) => row.estimated)
              ? 'Estimate, ms'
              : 'After, ms',
            align: 'end',
          },
          { label: 'Change', align: 'end' },
        ]}
      >
        {rows.map((row) => {
          const change = formatChange(
            changePercent(row.before_ms, row.after_ms)
          )
          return (
            <ListRow key={row.key} className="min-h-11.5 gap-3 py-2">
              <span className="flex min-w-0 items-center gap-2.5 text-body-small text-content-layout-1">
                <span
                  aria-hidden="true"
                  className="h-2 w-2 shrink-0 rounded-sm"
                  style={{ background: row.color }}
                />
                <span className="truncate">{row.name}</span>
              </span>
              <span className="text-right text-mono-medium text-content-layout-2">
                {row.before_ms == null ? DASH : latencyText(row.before_ms)}
              </span>
              <span className="text-right text-mono-medium text-content-layout-1">
                {row.after_ms == null ? DASH : latencyText(row.after_ms)}
              </span>
              <span className="text-right text-mono-medium text-content-layout-2">
                {change ?? DASH}
              </span>
            </ListRow>
          )
        })}
      </DividedList>
      {result.data && (
        <Text as="p" level="caption" className="mt-3.5 text-content-layout-3">
          {result.data.method}
        </Text>
      )}
    </SidePane>
  )
}
