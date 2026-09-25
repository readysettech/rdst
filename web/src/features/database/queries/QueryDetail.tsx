// Everything about one query, inline under its row (DESIGN_V4 section 3): its own
// latency and calls charts, the full deep analysis, the SQL, Jev's structural read
// and its benchmarks (DESIGN_V5 section 4). "Deep analyze this query" leads the
// actions; a statement RDST won't run says why in its place. Nothing navigates away
// except Send to Ask. A deep link's `tab` scrolls to that section; `readyset` is the
// Benchmarks one.

import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { BenchmarkDialog } from '@/features/database/benchmark/BenchmarkDialog'
import { BenchmarksSection } from '@/features/database/benchmark/BenchmarksSection'
import { benchmarkBlockReason } from '@/features/database/benchmark/eligibility'
import { useQueryBenchmark } from '@/features/database/benchmark/useQueryBenchmark'
import { QueryContextSlot } from '@/features/inbox/InboxContextBanner'
import { MuteMenu } from '@/features/monitor-kit/MuteMenu'
import { QueryTextBody } from '@/features/monitor-kit/QueryTextBody'
import { JevAssessment } from '@/features/queries/saved/JevAssessment'
import type { JevAssessmentSummary } from '@/lib/api'
import type { MonitorQuery, MonitorRange } from '@/lib/monitor/types'
import { AnalysisTab } from './AnalysisTab'
import { analyzability, notAnalyzedLine } from './analyzability'
import { PerformanceTab } from './PerformanceTab'
import { type DetailTab, queryName } from './querySelectors'

function Section({
  id,
  title,
  aside,
  children,
  className,
}: {
  id?: string
  title: string
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      id={id}
      aria-label={title}
      className={cn(
        'min-w-0 scroll-mt-28 space-y-3 rounded-panel border border-border-layout-1 bg-surface-layout-1 p-4',
        className
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Text as="h3" level="label-large" className="text-content-layout-1">
          {title}
        </Text>
        {aside}
      </div>
      {children}
    </section>
  )
}

export interface QueryDetailProps {
  query: MonitorQuery
  target: string
  tab: DetailTab
  /** The range to fetch this query's series for, and the window to show. */
  range: MonitorRange
  domain?: [number, number]
  jev?: JevAssessmentSummary | null
  /** Why Jev's assessment isn't complete yet, from the backend. */
  jevWaiting?: string | null
  onTabChange: (tab: DetailTab) => void
}

export function QueryDetail({
  query,
  target,
  tab,
  range,
  domain,
  jev,
  jevWaiting = null,
  onTabChange,
}: QueryDetailProps) {
  const navigate = useNavigate()
  const canAsk = query.text_status !== 'unavailable' && query.text.trim()
  const analysisRef = useRef<HTMLDivElement>(null)
  const benchmarksRef = useRef<HTMLDivElement>(null)
  const name = queryName(query)
  const bench = useQueryBenchmark(query, target)
  const blocked =
    query.text_status === 'unavailable'
      ? 'RDST can benchmark this query once its text is captured.'
      : benchmarkBlockReason(query)
  const [dialogOpen, setDialogOpen] = useState(false)
  const { primeFrom, setWithReadyset } = bench
  const openBenchmark = useCallback(
    (withReadyset?: boolean) => {
      primeFrom()
      if (withReadyset !== undefined) setWithReadyset(withReadyset)
      setDialogOpen(true)
    },
    [primeFrom, setWithReadyset]
  )
  const closeBenchmark = useCallback(() => setDialogOpen(false), [])
  const gate = analyzability(query)
  const [startSignal, setStartSignal] = useState(0)
  const analysisState = query.last_analysis?.state
  const analyzeLabel =
    analysisState === 'done'
      ? 'View deep analysis'
      : analysisState === 'queued' || analysisState === 'running'
        ? 'View analysis progress'
        : 'Deep analyze this query'
  const showAnalysis = () =>
    analysisRef.current?.scrollIntoView?.({
      behavior: 'smooth',
      block: 'start',
    })
  // The section shows once there's something in it; the Benchmark button starts one.
  const hasBenchmarks =
    bench.live ||
    Boolean(bench.run) ||
    Boolean(bench.timeline.latest) ||
    bench.timeline.past.length > 0 ||
    tab === 'readyset'
  const deepAnalyze = () => {
    if (analyzeLabel === 'Deep analyze this query')
      setStartSignal((value) => value + 1)
    showAnalysis()
  }

  // A deep link to a section scrolls there once the detail has opened.
  useEffect(() => {
    if (tab === 'performance') return
    const node =
      tab === 'analysis' ? analysisRef.current : benchmarksRef.current
    const timer = window.setTimeout(
      () => node?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }),
      320
    )
    return () => window.clearTimeout(timer)
  }, [tab])

  const performance = (
    <Section title="Latency and calls">
      <PerformanceTab
        query={query}
        target={target}
        range={range}
        domain={domain}
        onOpenTab={onTabChange}
      />
    </Section>
  )

  return (
    <div className="space-y-3" data-testid="query-detail">
      <QueryContextSlot queryKey={query.key} />
      <div className="flex flex-wrap items-center gap-2">
        {gate.ok && query.text_status !== 'unavailable' ? (
          <Button
            variant="primary"
            modifier={analysisState === 'done' ? 'outline' : 'solid'}
            size="small"
            icon="speedometer"
            iconPosition="left"
            label={analyzeLabel}
            onClick={deepAnalyze}
          />
        ) : null}
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          icon="speedometer"
          iconPosition="left"
          label={bench.live ? 'Benchmark running' : 'Benchmark'}
          disabled={Boolean(blocked) || bench.live}
          title={blocked ?? undefined}
          onClick={() => openBenchmark()}
        />
        {canAsk ? (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Send to Ask"
            icon="arrow-up-right"
            iconPosition="right"
            onClick={() =>
              void navigate({ to: '/ask', search: { sql: query.text } })
            }
          />
        ) : null}
        <span className="ml-auto">
          <MuteMenu
            target={target}
            queryKey={query.key}
            queryLabel={name}
            label={`More actions for ${name}`}
            align="end"
          />
        </span>
      </div>

      {!gate.ok && query.text_status !== 'unavailable' ? (
        <div
          className="flex items-start gap-2.5 rounded-panel border border-border-layout-1 bg-surface-layout-1 px-4 py-3"
          data-testid="not-analyzed-line"
        >
          <Icon
            name="lock-sync"
            label=""
            size="small"
            className="mt-0.5 shrink-0 text-content-layout-3"
          />
          <Text as="p" level="body-small" className="text-content-layout-2">
            <span className="font-medium text-content-layout-1">
              No deep analysis.
            </span>{' '}
            {notAnalyzedLine(query, gate)}
          </Text>
        </div>
      ) : null}

      {analysisState === 'done' ? null : performance}
      <div ref={analysisRef} className="scroll-mt-24">
        <Section id={`analysis-${query.key}`} title="Deep analysis">
          <AnalysisTab
            query={query}
            target={target}
            startSignal={startSignal}
            onCompare={() => {
              openBenchmark(true)
              onTabChange('readyset')
            }}
          />
        </Section>
      </div>

      {analysisState === 'done' ? performance : null}

      <div className="grid gap-3 laptop:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Section title="SQL">
          <div className="-mx-4 -mb-4 overflow-hidden border-t border-border-layout-1">
            <QueryTextBody
              text={query.text}
              textStatus={query.text_status}
              textReason={query.text_reason}
              fingerprint={query.key}
              tables={query.tables}
              maxLines={10}
            />
          </div>
        </Section>

        {query.text_status !== 'unavailable' && (
          <Section title="Query classification">
            {jevWaiting ? (
              <Text
                as="p"
                level="body-small"
                className="text-content-layout-2"
                data-testid="jev-waiting"
              >
                {jevWaiting}
              </Text>
            ) : (
              <JevAssessment
                assessment={jev}
                hasDeepAnalysis={query.last_analysis?.state === 'done'}
                onDeepAnalyze={deepAnalyze}
              />
            )}
          </Section>
        )}
      </div>

      <div ref={benchmarksRef} className="scroll-mt-24">
        {hasBenchmarks ? (
          <Section id={`benchmarks-${query.key}`} title="Benchmarks">
            <BenchmarksSection
              bench={bench}
              onOpen={() => openBenchmark()}
              blockedReason={blocked}
            />
          </Section>
        ) : null}
      </div>
      <BenchmarkDialog
        open={dialogOpen}
        onClose={closeBenchmark}
        bench={bench}
        target={target}
        queryLabel={name}
      />
    </div>
  )
}
