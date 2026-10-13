// Benchmark the queries with fixes: one caption on what it compares, and the one action;
// the result lands in the pane.
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { plural } from '@/features/monitor-kit/format'
import { DialogShell } from '../../../components/DialogShell'

export function BenchmarkFixesDialog({
  count,
  title,
  open,
  onClose,
  onRun,
}: {
  /** How many queries have a fix. */
  count: number
  /** For one query's fix, opened from its pane. */
  title?: string
  open: boolean
  onClose: () => void
  onRun: () => void
}) {
  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title={
        title ?? `Benchmark ${plural(count, 'query', 'queries')} with fixes`
      }
      testId="benchmark-fixes"
      note="Takes a few seconds."
      action={
        <Button
          variant="primary"
          modifier="solid"
          label={title ? 'Estimate' : 'Run benchmark'}
          disabled={count === 0}
          onClick={onRun}
        />
      }
    >
      <Text as="p" level="body-small" className="text-content-layout-2">
        Compares each query's current time with the planner's estimate for its
        fix, the same estimate Actions shows. Nothing runs on the database.
      </Text>
    </DialogShell>
  )
}
