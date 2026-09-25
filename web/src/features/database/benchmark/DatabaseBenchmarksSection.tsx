import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import type { MonitorQuery } from '@/lib/monitor/types'
import { BenchmarkDialog } from './BenchmarkDialog'
import { BenchmarksSection } from './BenchmarksSection'
import { useQueryBenchmark } from './useQueryBenchmark'

export function DatabaseBenchmarksSection({
  target,
  queries,
}: {
  target: string
  queries?: MonitorQuery[]
}) {
  const bench = useQueryBenchmark(undefined, target, queries)
  const [open, setOpen] = useState(false)
  const onOpen = () => {
    bench.primeFrom()
    setOpen(true)
  }
  const hasRuns =
    bench.live ||
    Boolean(bench.run) ||
    Boolean(bench.timeline.latest) ||
    bench.timeline.past.length > 0
  return (
    <section
      aria-label="Benchmarks"
      className="space-y-4 rounded-card border border-border-layout-1 bg-surface-layout-1 px-5 py-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <Text as="h2" level="headline-5">
            Benchmarks
          </Text>
          <Text as="p" level="body-small" className="text-content-layout-3">
            Measure several queries together in one run.
          </Text>
        </div>
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          icon="speedometer"
          iconPosition="left"
          label="Benchmark queries"
          onClick={onOpen}
        />
      </div>
      {hasRuns ? (
        <BenchmarksSection bench={bench} onOpen={onOpen} blockedReason={null} />
      ) : null}
      {open ? (
        <BenchmarkDialog
          open
          onClose={() => setOpen(false)}
          bench={bench}
          target={target}
          queryLabel="your selected query"
        />
      ) : null}
    </section>
  )
}
