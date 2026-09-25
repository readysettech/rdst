import { ErrorState } from '@rs/ui-new/error-state'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMemo } from 'react'
import {
  chartTitle,
  domainFor,
  isPartialBucket,
  MonitorTimeChart,
  SERIES_COLOR,
} from '@/features/monitor-kit/charts'
import { useQuerySeries } from '@/lib/monitor/hooks'
import type {
  ChartMarker,
  MonitorQuery,
  MonitorRange,
} from '@/lib/monitor/types'
import { measureMs, measureQps } from './format'
import { LatencyStatusLine, latencyStatus } from './LatencyStatusLine'
import type { DetailTab } from './querySelectors'

// Only measured schema changes appear on a query's chart, labeled plainly; the event
// log (analyses, inbox filings, reports) stays off charts.
const QUERY_MARKERS = new Set<ChartMarker['kind']>([
  'index_added',
  'index_dropped',
])

function plainIndexLabel(marker: ChartMarker): string {
  const verb = marker.kind === 'index_added' ? 'Index added' : 'Index dropped'
  const on = marker.table ? ` on ${marker.table}` : ''
  return marker.index_name
    ? `${verb}${on}: ${marker.index_name}`
    : `${verb}${on}`
}

function ChartFrame({
  title,
  color,
  children,
}: {
  title: string
  color: string
  children: React.ReactNode
}) {
  return (
    <VStack className="min-w-0 items-stretch gap-2 rounded-panel border border-border-layout-1 bg-surface-layout-1/40 p-3 pt-2.5 transition-colors duration-fast hover:border-border-layout-2">
      <HStack className="items-center gap-2">
        <span
          aria-hidden="true"
          className="h-3.5 w-0.75 rounded-pill"
          style={{ background: color }}
        />
        <Text level="subtitle-2" className="text-content-layout-1">
          {title}
        </Text>
      </HStack>
      {children}
    </VStack>
  )
}

export function PerformanceTab({
  query,
  target,
  range,
  domain: windowDomain,
}: {
  query: MonitorQuery
  target: string
  range: MonitorRange
  /** The page's window (a zoom); defaults to the range. */
  domain?: [number, number]
  onOpenTab?: (tab: DetailTab) => void
}) {
  const series = useQuerySeries(
    target,
    query.key,
    range,
    true,
    windowDomain ? { from: windowDomain[0], to: windowDomain[1] } : undefined
  )
  const data = series.data
  const syncId = `detail-${query.key}`

  const { latency, qps, markers, domain, status } = useMemo(() => {
    // The bucket still filling up would read low; leave it out.
    const points = (data?.points ?? []).filter(
      (point) => !data || !isPartialBucket(point.ts, data.res)
    )
    const last = points.at(-1)?.ts ?? Math.floor(Date.now() / 1000)
    return {
      latency: points.map((point) => ({ x: point.ts, y: point.mean_ms })),
      qps: points.map((point) => ({ x: point.ts, y: point.qps })),
      markers: (data?.markers ?? [])
        .filter((marker) => QUERY_MARKERS.has(marker.kind))
        .map((marker) => ({ ...marker, label: plainIndexLabel(marker) })),
      domain:
        windowDomain ??
        (data ? domainFor(range, data.history, last) : undefined),
      status: latencyStatus(points),
    }
  }, [data, range, windowDomain])

  if (series.isError && !data)
    return (
      <ErrorState
        errorClass="database"
        title="This query's history couldn't be loaded"
        message="RDST couldn't read the stored readings for this query."
        trustworthy="Monitoring keeps recording in the background."
        onRetry={() => void series.refetch()}
      />
    )

  return (
    <VStack className="items-stretch gap-4">
      {data?.detail_note ? (
        <Text as="p" level="caption" className="text-content-layout-3">
          {data.detail_note}
        </Text>
      ) : null}
      {status ? <LatencyStatusLine status={status} /> : null}
      {!data ? (
        <div className="grid gap-4 laptop:grid-cols-2" aria-hidden="true">
          <Skeleton className="h-60 w-full rounded-panel" />
          <Skeleton className="h-60 w-full rounded-panel" />
        </div>
      ) : (
        <div className="grid gap-4 laptop:grid-cols-2">
          <ChartFrame title="Mean latency" color={SERIES_COLOR.latency}>
            <MonitorTimeChart
              ariaLabel={chartTitle('Mean latency', range, data.history)}
              series={[
                {
                  id: 'latency',
                  label: 'Mean latency',
                  color: SERIES_COLOR.latency,
                  points: latency,
                  emphasis: true,
                },
              ]}
              domain={domain}
              resolution={data.res}
              height={200}
              markers={markers}
              syncId={syncId}
              formatValue={(value) => {
                const m = measureMs(value)
                return `${m.value} ${m.unit}`
              }}
            />
          </ChartFrame>
          <ChartFrame title="Calls per second" color={SERIES_COLOR.throughput}>
            <MonitorTimeChart
              ariaLabel={chartTitle('Calls per second', range, data.history)}
              series={[
                {
                  id: 'qps',
                  label: 'Queries per second',
                  color: SERIES_COLOR.throughput,
                  points: qps,
                  emphasis: true,
                },
              ]}
              domain={domain}
              resolution={data.res}
              height={200}
              markers={markers}
              syncId={syncId}
              formatValue={(value) => {
                const m = measureQps(value)
                return `${m.value} ${m.unit}`
              }}
            />
          </ChartFrame>
        </div>
      )}
    </VStack>
  )
}
