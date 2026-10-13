// The full analysis's cards on a two-column grid: Fix (wide), Benchmark when one ran
// (wide), Plan and Rewrites tested side by side, Also worth doing and Readyset side by
// side, Query (wide) with its parameters and the EXPLAIN plan folded, then the footer.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Fragment, type ReactNode, useId, useState } from 'react'
import { SqlCode } from '@/features/database/queries/SqlCode'
import type { AnalysisView } from '@/lib/monitor/types'
import {
  formatInt,
  OUTCOME_WORD,
  planLines,
  rewriteRange,
  speedup,
} from './analysisView'

const hairlineLink =
  'border-b border-border-layout-2 hover:border-content-layout-2'

function Card({
  title,
  meta,
  right,
  wide,
  testId,
  children,
}: {
  title: string
  meta?: string | null
  right?: ReactNode
  wide?: boolean
  testId?: string
  children: ReactNode
}) {
  return (
    <section
      aria-label={title}
      data-testid={testId}
      className={cn(
        'rounded-card border border-border-layout-2 bg-surface-primary-soft px-5.5 py-5',
        wide && 'col-span-2'
      )}
    >
      <div className="mb-3.5 flex items-baseline gap-2.5">
        <Text as="h3" level="headline-5" className="text-content-layout-1">
          {title}
        </Text>
        {meta && (
          <Text as="span" level="caption" className="text-content-layout-3">
            {meta}
          </Text>
        )}
        {right && (
          <div className="ml-auto text-caption text-content-layout-3">
            {right}
          </div>
        )}
      </div>
      {children}
    </section>
  )
}

/** Key-value rows: a title with one line under it, a value at the right. */
function KeyValue({
  rows,
}: {
  rows: {
    key: string
    title: string
    detail: string | null
    value: ReactNode
  }[]
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] gap-x-4">
      {rows.map((row) => (
        <div key={row.key} className="contents">
          <span className="border-t border-border-layout-2 py-2.5 text-body-small text-content-layout-1">
            {row.title}
            {row.detail && (
              <span className="block text-label-extra-small font-normal text-content-layout-3">
                {row.detail}
              </span>
            )}
          </span>
          <span className="whitespace-nowrap border-t border-border-layout-2 py-2.5 text-right text-caption text-content-layout-3">
            {row.value}
          </span>
        </div>
      ))}
    </div>
  )
}

function Check({
  children,
  tone,
}: {
  children: ReactNode
  tone: 'positive' | 'off'
}) {
  return (
    <span className="flex items-center gap-2 text-caption text-content-layout-3">
      {tone === 'positive' ? (
        <Icon
          name="tick"
          label=""
          size="small"
          className="text-content-positive-soft"
        />
      ) : (
        <span className="h-1.75 w-1.75 shrink-0 rounded-full bg-content-layout-3" />
      )}
      {children}
    </span>
  )
}

export function FixCard({
  fix,
  onCopy,
  plannerSetup,
}: {
  fix: NonNullable<AnalysisView['fix_detail']>
  onCopy: (sql: string) => void
  /** Set when hypopg is missing: the statement that turns planner verification on. */
  plannerSetup?: { installSql: string } | null
}) {
  const bothBottlenecks = /\bsort\b/i.test(fix.serves ?? '')
  return (
    <Card
      title="Fix"
      meta={bothBottlenecks ? 'one index, both bottlenecks' : 'one index'}
      wide
      testId="analysis-fix"
    >
      <div className="grid grid-cols-[1.4fr_1fr] items-start gap-x-10 gap-y-3">
        <div>
          <SqlCode sql={fix.sql} />
          <div className="mt-2.5 flex items-center gap-2.5">
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              label="Copy fix"
              onClick={() => onCopy(fix.sql)}
            />
            <Text as="span" level="caption" className="text-content-layout-3">
              Builds without blocking writes. Confirmed by RDST once it appears.
            </Text>
          </div>
          {plannerSetup && (
            <div data-testid="planner-setup" className="mt-4">
              <Text as="p" level="caption" className="text-content-layout-3">
                Not checked against the planner: hypopg is not enabled. Run this
                as an administrator, then analyze again.
              </Text>
              <div className="mt-1.5 flex items-center gap-2.5">
                <SqlCode
                  sql={plannerSetup.installSql}
                  className="min-w-0 flex-1"
                />
                <CopyButton text={plannerSetup.installSql} label="Copy SQL" />
              </div>
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2.5 pt-0.5 text-body-small text-content-layout-2">
          {fix.serves && <span>{fix.serves}</span>}
          {fix.cost_before != null && fix.cost_after != null && (
            <Check tone="positive">
              Planner uses it: cost {formatInt(fix.cost_before)} to{' '}
              {formatInt(fix.cost_after)}
            </Check>
          )}
          {(fix.size || fix.write_cost) && (
            <Check tone="off">
              {[fix.size, fix.write_cost].filter(Boolean).join(', ')}
            </Check>
          )}
        </div>
      </div>
    </Card>
  )
}

export function BenchmarkCard({
  benchmark,
  onRerun,
}: {
  benchmark: NonNullable<AnalysisView['benchmark']>
  onRerun?: () => void
}) {
  const change = speedup(benchmark.before_ms, benchmark.after_ms)
  const numbers = [
    { key: 'before', label: 'Before', value: benchmark.before_ms, unit: 'ms' },
    { key: 'after', label: 'After', value: benchmark.after_ms, unit: 'ms' },
  ]
  return (
    <Card
      title="Benchmark"
      meta={benchmark.method}
      wide
      testId="analysis-benchmark"
      right={
        onRerun && (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Rerun"
            onClick={onRerun}
          />
        )
      }
    >
      <div className="grid grid-cols-3 border-y border-border-layout-2">
        {numbers.map((number) => (
          <div
            key={number.key}
            className="mr-5 border-r border-border-layout-2 py-3.5 pr-5"
          >
            <Text as="p" level="caption" className="text-content-layout-3">
              {number.label}
            </Text>
            <p className="mt-1 text-headline-2 tabular-nums text-content-layout-1">
              {number.value == null ? '\u2014' : number.value.toFixed(1)}
              <span className="ml-1 text-caption font-normal text-content-layout-3">
                {number.unit}
              </span>
            </p>
          </div>
        ))}
        <div className="py-3.5">
          <Text as="p" level="caption" className="text-content-layout-3">
            Change
          </Text>
          <p className="mt-1 text-headline-2 tabular-nums text-content-layout-1">
            {change?.value ?? '\u2014'}
            {change && (
              <span className="ml-1 text-caption font-normal text-content-layout-3">
                {change.unit}
              </span>
            )}
          </p>
        </div>
      </div>
    </Card>
  )
}

export function PlanCard({
  findings,
}: {
  findings: AnalysisView['plan_findings']
}) {
  return (
    <Card
      title="Plan"
      meta={`${findings.length} ${findings.length === 1 ? 'finding' : 'findings'}`}
      testId="analysis-plan"
    >
      <KeyValue
        rows={findings.map((finding) => ({
          key: finding.title,
          title: finding.title,
          detail: finding.detail,
          value: finding.value ?? '',
        }))}
      />
    </Card>
  )
}

/** A statement to run, with its copy button under it. */
function StatementCopy({ sql }: { sql: string }) {
  return (
    <div className="pt-1 pb-3" data-testid="statement-copy">
      <SqlCode sql={sql} />
      <div className="mt-1.5">
        <CopyButton text={sql} label="Copy SQL" />
      </div>
    </div>
  )
}

export function RewritesCard({
  rewrites,
  method,
}: {
  rewrites: AnalysisView['rewrites']
  method: string | null
}) {
  return (
    <Card
      title="Rewrites tested"
      meta={String(rewrites.length)}
      right={method}
      testId="analysis-rewrites"
    >
      <DividedList columns="1fr auto auto" className="[&>div]:border-t-0">
        {rewrites.map((rewrite, index) => (
          <Fragment key={`${index}-${rewrite.name}`}>
            <ListRow className="min-h-10 py-1.5">
              <span className="min-w-0 truncate text-body-small text-content-layout-1">
                {rewrite.name}
              </span>
              <span className="text-caption tabular-nums text-content-layout-3">
                {rewriteRange(rewrite)}
              </span>
              <Tag
                size="small"
                variant="neutral"
                modifier="solid"
                className="rounded-pill whitespace-nowrap"
                label={OUTCOME_WORD[rewrite.outcome]}
              />
            </ListRow>
            {rewrite.sql && <StatementCopy sql={rewrite.sql} />}
          </Fragment>
        ))}
      </DividedList>
    </Card>
  )
}

export function AlsoCard({ also }: { also: AnalysisView['also'] }) {
  const [shown, setShown] = useState<string | null>(null)
  // Further indexes carry their statement; the other suggestions are one line each.
  const statements = also.filter((item) => item.sql)
  const notes = also.filter((item) => !item.sql)
  return (
    <Card
      title="Also worth doing"
      meta={String(also.length)}
      testId="analysis-also"
    >
      {statements.map((item) => (
        <div
          key={item.title}
          className="border-t border-border-layout-2 pt-2.5"
          data-testid="also-index"
        >
          <span className="text-body-small text-content-layout-1">
            {item.title}
            {item.detail && (
              <span className="block text-label-extra-small font-normal text-content-layout-3">
                {item.detail}
              </span>
            )}
          </span>
          <div className="mt-2">
            <StatementCopy sql={item.sql ?? ''} />
          </div>
        </div>
      ))}
      <KeyValue
        rows={notes.map((item) => ({
          key: item.title,
          title: item.title,
          detail: shown === item.title ? null : item.detail,
          value:
            item.detail && shown !== item.title ? (
              <Pressable
                className={cn('cursor-pointer', hairlineLink)}
                onClick={() => setShown(item.title)}
              >
                Show
              </Pressable>
            ) : (
              ''
            ),
        }))}
      />
      {shown && (
        <Text as="p" level="caption" className="mt-2.5 text-content-layout-2">
          {notes.find((item) => item.title === shown)?.detail}
        </Text>
      )}
    </Card>
  )
}

export function ReadysetCard({
  readyset,
  onVerify,
  onCompare,
}: {
  readyset: NonNullable<AnalysisView['readyset']>
  onVerify?: () => void
  onCompare?: () => void
}) {
  const blockers = readyset.blockers.length
  const line =
    blockers > 0
      ? `${blockers} ${blockers === 1 ? 'blocker' : 'blockers'}. ${readyset.blockers[0]}`
      : readyset.verified
        ? 'No blockers. Verified.'
        : 'No blockers. Not verified yet.'
  return (
    <Card title="Readyset" testId="analysis-readyset">
      <div className="flex items-center gap-3 text-body-small text-content-layout-2">
        <span
          className={cn(
            'h-1.75 w-1.75 shrink-0 rounded-full',
            blockers > 0
              ? 'bg-content-warning-plain'
              : readyset.verified
                ? 'bg-content-positive-plain'
                : 'bg-content-layout-3'
          )}
        />
        <span>{line}</span>
        <div className="ml-auto flex gap-1">
          {onVerify && !readyset.verified && (
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              label="Verify"
              onClick={onVerify}
            />
          )}
          {onCompare && (
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Compare speed"
              onClick={onCompare}
            />
          )}
        </div>
      </div>
    </Card>
  )
}

export function QueryCard({
  sql,
  params,
  paramSource,
  explainText,
}: {
  sql: string
  params: AnalysisView['params']
  paramSource: string | null
  explainText: string | null
}) {
  const [open, setOpen] = useState(false)
  const planId = useId()
  return (
    <Card
      title="Query"
      meta="as the planner saw it"
      wide
      testId="analysis-query"
    >
      <SqlCode sql={sql} />
      {(params.length > 0 || paramSource) && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          {params.map((param) => (
            <Tag
              key={param.name}
              size="small"
              variant="neutral"
              modifier="solid"
              className="rounded-pill font-mono font-normal"
              label={`${param.name} = ${param.value}`}
            />
          ))}
          {paramSource && (
            <Text as="span" level="caption" className="text-content-layout-3">
              {paramSource}
            </Text>
          )}
        </div>
      )}
      {explainText && (
        <div className="mt-3.5">
          <Pressable
            aria-expanded={open}
            aria-controls={planId}
            className="flex cursor-pointer items-center gap-2 text-body-small text-content-layout-2"
            onClick={() => setOpen((value) => !value)}
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
            EXPLAIN plan
          </Pressable>
          {open && (
            <Scrollable
              orientation="horizontal"
              rootClassName="mt-3 rounded-control bg-surface-layout-2"
            >
              <pre
                id={planId}
                className="px-4 py-3.5 text-mono-small leading-relaxed whitespace-pre text-content-layout-2"
              >
                {planLines(explainText).map((line, index) => (
                  <span
                    key={`${index}-${line.text}`}
                    className={cn(
                      'block',
                      line.hot && 'text-content-warning-soft',
                      /Rows Removed/.test(line.text) && 'text-content-layout-1'
                    )}
                  >
                    {line.text}
                  </span>
                ))}
              </pre>
            </Scrollable>
          )}
        </div>
      )}
    </Card>
  )
}

export function AnalysisFoot({
  run,
}: {
  run: NonNullable<AnalysisView['run']>
}) {
  return (
    <div className="mt-7 flex gap-4 text-caption text-content-layout-3">
      {run.model && <span>{run.model}</span>}
      {run.tokens != null && <span>{formatInt(run.tokens)} tokens</span>}
      {run.seconds != null && <span>{Math.round(run.seconds)} s</span>}
      {run.id && <span className="ml-auto">Analysis #{run.id}</span>}
    </div>
  )
}
