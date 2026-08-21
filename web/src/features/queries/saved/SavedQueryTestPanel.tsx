import { Alert } from '@rs/ui-new/alert'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { ComparisonCard } from '../../../components/CacheComparison'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import { isCacheRunResult } from '../../../types/cache'

const LIVE_STATUSES = ['running', 'reconnecting']
const TERMINAL_REPORTABLE_STATUSES = ['failed', 'done', 'partial']

/**
 * Whether a card should be reporting this run. A test that is genuinely live,
 * or one that just finished while this tab was watching it, is news until the
 * user acknowledges it, so the strip appears on its own and stays until
 * dismissed rather than waiting behind a disclosure control. A completed run
 * reattached from a past session (`!seenLive`) never counts as news — it
 * already happened, and the drawer's Overview is where it stays reachable.
 */
export function reportsCacheTestRun(run?: BackgroundRunState): boolean {
  if (!run || run.hidden) return false
  if (LIVE_STATUSES.includes(run.status)) return true
  return (
    TERMINAL_REPORTABLE_STATUSES.includes(run.status) && Boolean(run.seenLive)
  )
}

interface SavedQueryTestPanelProps {
  run: BackgroundRunState
  /** Forget the run and its result entirely. */
  onDismissRun: (runId: string) => void
  /** Stop reporting the finished run while keeping its result. */
  onClose?: () => void
}

/**
 * A cache test as it happens, on the card that started it: progress while it
 * runs, the failure when it fails, and the paired-latency result when it lands.
 */
export function SavedQueryTestPanel({
  run,
  onDismissRun,
  onClose,
}: SavedQueryTestPanelProps) {
  const isTesting = run.status === 'running' || run.status === 'reconnecting'
  const comparisonResult = isCacheRunResult(run.result) ? run.result : undefined
  const resultUnavailable =
    (run.status === 'done' || run.status === 'partial') && !comparisonResult

  return (
    <VStack className="items-stretch gap-3">
      <Show when={isTesting}>
        <HStack className="items-center gap-2 rounded-lg border border-border-primary-soft/30 bg-surface-primary-soft/15 px-4 py-3">
          <span className="shrink-0">
            <Spinner size="base" color="primary-soft" />
          </span>
          <VStack className="items-start gap-0.5">
            <Text level="label-small" className="text-content-primary-soft">
              Testing in the background
            </Text>
            <Text level="caption" className="text-content-layout-3">
              {run.message || 'Connecting...'}
            </Text>
          </VStack>
        </HStack>
      </Show>

      <Show when={run.status === 'failed'}>
        <Alert
          variant="negative"
          modifier="outline"
          label={`Performance test failed: ${run.message || 'The comparison did not complete.'}`}
        />
      </Show>

      <Show when={resultUnavailable}>
        <Alert
          variant="warning"
          modifier="outline"
          icon="alert"
          iconPosition="left"
          label="Performance result unavailable. Run the test again to refresh it."
        />
      </Show>

      <Show when={comparisonResult}>
        {(result) => (
          <ComparisonCard
            result={result}
            onDelete={() => onDismissRun(run.runId)}
            onClose={onClose}
          />
        )}
      </Show>
    </VStack>
  )
}
