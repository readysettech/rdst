// The Benchmarks section of a query's detail: the run in progress, the latest
// result (latency, throughput, and Readyset's speedup when it ran), and the runs
// before it.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Progress } from '@rs/ui-new/progress'
import { Text } from '@rs/ui-new/text'
import {
  formatElapsed,
  formatMsWithUnit,
  formatQps,
} from '@/features/monitor-kit/format'
import type { BackgroundRunState } from '@/lib/backgroundRuns'
import { relativeTime } from '../queries/format'
import {
  type BenchmarkSummary,
  type LaneNumbers,
  type RunPhase,
  runPhase,
  runShape,
  runWantsReadyset,
  speedupLabel,
  summaryFromRun,
} from './benchmarkFlow'
import type { QueryBenchmark } from './useQueryBenchmark'

function Measure({ ms }: { ms: number | null | undefined }) {
  const { value, unit } = formatMsWithUnit(ms)
  return (
    <span className="tabular-nums">
      <span className="text-subtitle-1 text-content-layout-1">{value}</span>
      {unit ? (
        <span className="ml-1 text-caption text-content-layout-3">{unit}</span>
      ) : null}
    </span>
  )
}

/** Typical and slow-end latency and throughput, one row per side that ran. */
function LaneTable({
  database,
  readyset,
}: {
  database: LaneNumbers | null
  readyset: LaneNumbers | null
}) {
  const rows: Array<{
    name: string
    lane: LaneNumbers | null
    readyset?: boolean
  }> = database ? [{ name: 'Your database', lane: database }] : []
  if (readyset)
    rows.push({ name: 'Through Readyset', lane: readyset, readyset: true })
  return (
    <table
      className="w-full table-fixed text-left"
      data-testid="benchmark-numbers"
    >
      <thead>
        <tr className="text-caption text-content-layout-3">
          <th scope="col" className="w-[34%] pb-1 font-normal tablet:w-[28%]">
            <span className="sr-only">Where it ran</span>
          </th>
          <th scope="col" className="pb-1 font-normal">
            Typical <span className="max-tablet:hidden">(p50)</span>
          </th>
          <th scope="col" className="pb-1 font-normal">
            Slow end <span className="max-tablet:hidden">(p95)</span>
          </th>
          <th scope="col" className="pb-1 font-normal">
            Runs a second
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.name} className="border-t border-border-layout-1">
            <th
              scope="row"
              className={cn(
                'py-2 pr-2 text-label-small font-normal',
                row.readyset
                  ? 'text-content-positive-soft'
                  : 'text-content-layout-2'
              )}
            >
              {row.name}
            </th>
            <td className="py-2">
              <Measure ms={row.lane?.p50} />
            </td>
            <td className="py-2">
              <Measure ms={row.lane?.p95} />
            </td>
            <td className="py-2 text-subtitle-1 tabular-nums text-content-layout-1">
              {formatQps(row.lane?.qps ?? null)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const STEP_LABELS: Record<RunPhase, string> = {
  waiting: 'Waiting to start',
  readyset: 'Starting Readyset',
  measuring: 'Measuring',
  done: 'Done',
}

function RunProgress({
  run,
  onStop,
  grouped = false,
}: {
  grouped?: boolean
  run: BackgroundRunState
  onStop: () => void
}) {
  const phase = runPhase(run)
  const steps: RunPhase[] = runWantsReadyset(run)
    ? ['waiting', 'readyset', 'measuring', 'done']
    : ['waiting', 'measuring', 'done']
  const index = steps.indexOf(phase)
  const summary = summaryFromRun(run)
  const elapsed = run.loadResult?.elapsed_seconds ?? 0
  const duration = run.loadRequest?.duration_seconds ?? 30
  const percent = Math.min(100, Math.max(0, (elapsed / duration) * 100))
  const note =
    phase === 'waiting'
      ? 'Another benchmark is using the database. This one starts when it finishes.'
      : phase === 'readyset'
        ? 'Starting Readyset and caching these queries. The first time can take a few minutes; the clock starts after.'
        : `${formatElapsed(elapsed)} of ${formatElapsed(duration)}. Your database is under benchmark load until it ends.`
  return (
    <div
      className="space-y-4 rounded-panel border border-border-primary-soft bg-surface-primary-soft/10 p-4"
      data-testid="benchmark-progress"
      data-phase={phase}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <ol
          className="flex flex-wrap items-center gap-x-1 gap-y-2"
          aria-label="Benchmark steps"
        >
          {steps.map((step, i) => {
            const done = i < index
            const current = i === index
            return (
              <li key={step} className="flex items-center gap-1">
                <span
                  className={cn(
                    'inline-flex h-6 items-center gap-1.5 rounded-pill px-2.5 text-label-small',
                    done && 'text-content-positive-soft',
                    current &&
                      'bg-surface-primary-soft text-content-primary-soft',
                    !done && !current && 'text-content-layout-3'
                  )}
                  aria-current={current ? 'step' : undefined}
                >
                  {done ? (
                    <Icon
                      name="tick"
                      label=""
                      aria-hidden="true"
                      className="h-3 w-3"
                    />
                  ) : current ? (
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-content-primary-soft" />
                  ) : null}
                  {STEP_LABELS[step]}
                </span>
                {i < steps.length - 1 ? (
                  <span
                    aria-hidden="true"
                    className="h-px w-3 bg-border-layout-2"
                  />
                ) : null}
              </li>
            )
          })}
        </ol>
        <Button
          variant="negative"
          modifier="outline"
          size="small"
          icon="close"
          iconPosition="left"
          label={run.status === 'stopping' ? 'Stopping' : 'Stop'}
          disabled={run.status === 'stopping'}
          onClick={onStop}
        />
      </div>
      {phase === 'measuring' ? (
        <Progress value={percent} max={100} label="Benchmark progress" />
      ) : null}
      <Text as="p" level="body-small" className="text-content-layout-2">
        {note}
      </Text>
      {phase === 'measuring' &&
      !grouped &&
      (summary.database || summary.readyset) ? (
        <div className="space-y-2 border-t border-border-layout-1 pt-3">
          <Text as="p" level="caption" className="text-content-layout-3">
            So far
          </Text>
          {summary.readyset ? <Speedup summary={summary} /> : null}
          <LaneTable database={summary.database} readyset={summary.readyset} />
        </div>
      ) : null}
    </div>
  )
}

function Speedup({ summary }: { summary: BenchmarkSummary }) {
  const label = speedupLabel(summary.speedup)
  if (!label) return null
  const faster = (summary.speedup ?? 0) >= 1.1
  return (
    <p
      className="flex flex-wrap items-baseline gap-x-2"
      data-testid="benchmark-speedup"
    >
      <span
        className={cn(
          'text-headline-3 tabular-nums',
          faster ? 'text-content-positive-soft' : 'text-content-layout-1'
        )}
      >
        {label}
      </span>
      <span className="text-body-small text-content-layout-2">
        with Readyset, comparing typical latency
      </span>
    </p>
  )
}

const OUTCOME_NOTE: Record<BenchmarkSummary['outcome'], string | null> = {
  done: null,
  partial: 'Some runs failed.',
  failed: "The benchmark didn't finish.",
  cancelled: 'Stopped early.',
}

function LatestResult({ summary }: { summary: BenchmarkSummary }) {
  const shape = runShape(summary)
  const note = OUTCOME_NOTE[summary.outcome]
  return (
    <div className="space-y-4" data-testid="benchmark-latest">
      <Text as="p" level="caption" className="text-content-layout-3">
        {[
          summary.source === 'run'
            ? 'Latest'
            : `Latest, ${relativeTime(summary.ts)}`,
          shape,
          note,
        ]
          .filter(Boolean)
          .join(' · ')}
      </Text>
      {summary.readyset ? <Speedup summary={summary} /> : null}
      {summary.source === 'compare' ? (
        <Text as="p" level="body-small" className="text-content-layout-2">
          Typical latency {formatMsWithUnit(summary.database?.p50).value}{' '}
          {formatMsWithUnit(summary.database?.p50).unit} on your database,{' '}
          {formatMsWithUnit(summary.readyset?.p50).value}{' '}
          {formatMsWithUnit(summary.readyset?.p50).unit} through Readyset.
        </Text>
      ) : (
        <div className="space-y-1">
          <LaneTable database={summary.database} readyset={summary.readyset} />
          {summary.paced ? (
            <Text as="p" level="caption" className="text-content-layout-3">
              This run paused between runs, so runs a second shows its pace, not
              the most the query can do. Standard measures that.
            </Text>
          ) : null}
        </div>
      )}
      {summary.readysetNote ? (
        <Text as="p" level="caption" className="text-content-warning-soft">
          Readyset didn't run: {summary.readysetNote}
        </Text>
      ) : null}
    </div>
  )
}

function PastRuns({ runs }: { runs: BenchmarkSummary[] }) {
  if (runs.length === 0) return null
  return (
    <section aria-label="Past benchmarks" className="space-y-2">
      <Text as="h4" level="label-medium" className="text-content-layout-1">
        Earlier
      </Text>
      <ul
        className="divide-y divide-border-layout-1 rounded-panel border border-border-layout-1"
        data-testid="benchmark-history"
      >
        {runs.map((run) => {
          const lane = run.database ?? run.readyset
          const p50 = formatMsWithUnit(lane?.p50)
          const p95 = formatMsWithUnit(lane?.p95)
          const speedup = speedupLabel(run.speedup)
          return (
            <li
              key={`${run.source}-${run.ts}`}
              className="grid grid-cols-2 gap-x-3 gap-y-1 px-3 py-2 text-body-small tablet:grid-cols-[minmax(0,1.4fr)_repeat(4,minmax(0,1fr))] tablet:items-center"
            >
              <span className="col-span-2 min-w-0 tablet:col-span-1">
                <span className="text-content-layout-1">
                  {relativeTime(run.ts)}
                </span>
                <span className="block truncate text-caption text-content-layout-3">
                  {runShape(run)}
                </span>
              </span>
              <span className="tabular-nums text-content-layout-2">
                <span className="text-caption text-content-layout-3">p50 </span>
                {p50.value} {p50.unit}
              </span>
              <span className="tabular-nums text-content-layout-2">
                <span className="text-caption text-content-layout-3">p95 </span>
                {p95.value} {p95.unit}
              </span>
              <span className="tabular-nums text-content-layout-2">
                {lane?.qps != null ? `${formatQps(lane.qps)} a second` : '-'}
              </span>
              <span
                className={cn(
                  'tabular-nums',
                  speedup && (run.speedup ?? 0) >= 1.1
                    ? 'text-content-positive-soft'
                    : 'text-content-layout-3'
                )}
              >
                {speedup
                  ? `Readyset ${speedup}`
                  : run.readyset && !run.database
                    ? 'Readyset only'
                    : run.withReadyset
                      ? "Readyset didn't run"
                      : 'Database only'}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function QueryResults({ bench }: { bench: QueryBenchmark }) {
  const run = bench.run
  if (!run?.loadResult) return null
  return (
    <section className="space-y-4" aria-label="Per-query benchmark results">
      {run.loadResult.queries.map((query) => {
        const entry = bench.controller.queries.find(
          (item) =>
            item.hash === query.query_hash || item.tag === query.query_hash
        )
        const summary = summaryFromRun({
          ...run,
          loadResult: { ...run.loadResult!, queries: [query] },
        })
        return (
          <section
            key={query.query_hash}
            className="space-y-2 rounded-panel border border-border-layout-1 p-4"
          >
            <Text as="h4" level="label-medium">
              {entry?.tag || entry?.question || query.query_name}
            </Text>
            {entry ? (
              <p className="truncate font-mono text-caption text-content-layout-3">
                {entry.sql}
              </p>
            ) : null}
            <Speedup summary={summary} />
            <LaneTable
              database={summary.database}
              readyset={summary.readyset}
            />
            {query.failures > 0 ? (
              <p className="text-body-small text-content-warning-soft">
                {query.failures} runs failed. {query.last_error}
              </p>
            ) : null}
            {summary.readysetNote ? (
              <p className="text-body-small text-content-warning-soft">
                {summary.readysetNote}
              </p>
            ) : null}
          </section>
        )
      })}
      {run.loadResult.skipped_queries?.map((query) => (
        <p
          key={query.query_hash}
          className="text-body-small text-content-warning-soft"
        >
          {query.query_name} didn't run{query.lanes ? ' on every side' : ''}:{' '}
          {query.reason.replaceAll('_', ' ')}.
        </p>
      ))}
    </section>
  )
}

export function BenchmarksSection({
  bench,
  onOpen,
  blockedReason,
}: {
  bench: QueryBenchmark
  onOpen: () => void
  /** Why RDST won't benchmark this query, when it won't. */
  blockedReason: string | null
}) {
  const { run, live, timeline } = bench
  const latest = live ? null : timeline.latest
  const past = timeline.past
  const grouped = (run?.loadRequest?.queries.length ?? 0) > 1
  const hasGroupResults = grouped && Boolean(run?.loadResult?.queries.length)
  return (
    <div className="space-y-4" data-testid="benchmarks-section">
      {run && live ? (
        <RunProgress run={run} onStop={bench.stop} grouped={grouped} />
      ) : null}
      {run?.status === 'failed' ? (
        <p role="alert" className="text-body-small text-content-negative-soft">
          {run.message}
        </p>
      ) : null}
      {hasGroupResults ? <QueryResults bench={bench} /> : null}
      {!live && hasGroupResults ? null : latest ? (
        <LatestResult summary={latest} />
      ) : !live ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-panel border border-border-layout-1 bg-surface-layout-2/40 px-4 py-3">
          <div className="min-w-0">
            <Text as="p" level="label-medium" className="text-content-layout-1">
              {bench.historyLoading
                ? 'Reading past benchmarks'
                : 'Not benchmarked yet'}
            </Text>
            <Text as="p" level="caption" className="text-content-layout-3">
              {blockedReason ??
                'Runs your selected queries over and over and measures latency and throughput, with Readyset beside it if you like.'}
            </Text>
          </div>
          {blockedReason ? null : (
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              icon="speedometer"
              iconPosition="left"
              label="Benchmark"
              onClick={onOpen}
            />
          )}
        </div>
      ) : null}
      <PastRuns runs={past} />
    </div>
  )
}
