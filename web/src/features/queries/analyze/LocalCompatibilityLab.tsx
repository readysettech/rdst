import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { useMemo, useState } from 'react'
import { ComparisonCard } from '../../../components/CacheComparison'
import {
  startCacheTestRun,
  useBackgroundRuns,
} from '../../../lib/backgroundRuns'

interface LocalCompatibilityLabProps {
  target?: string | null
  query?: string
  disabled?: boolean
}

export function LocalCompatibilityLab({
  target,
  query,
  disabled = false,
}: LocalCompatibilityLabProps) {
  const [isOpen, setIsOpen] = useDisclosure({})
  const [runId, setRunId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const runs = useBackgroundRuns()
  const run = useMemo(
    () => (runId ? runs.find((candidate) => candidate.runId === runId) : null),
    [runId, runs]
  )
  const running =
    starting ||
    run?.status === 'running' ||
    run?.status === 'reconnecting' ||
    run?.status === 'stopping'

  const startTest = async () => {
    if (!target || !query?.trim() || disabled || running) return
    setStarting(true)
    try {
      const nextRunId = await startCacheTestRun({
        target,
        query,
        label: 'Analyze compatibility test',
        iterations: 15,
        warmup: 5,
      })
      setRunId(nextRunId)
    } finally {
      setStarting(false)
    }
  }

  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <Button
          variant="primary"
          modifier="ghost"
          label=""
          aria-expanded={isOpen}
          disabled={disabled}
          onClick={() => setIsOpen(!isOpen)}
          className="h-auto w-full min-w-0 justify-between rounded-none bg-transparent px-5 py-4 text-left text-content-layout-1 hover:bg-surface-layout-2/50"
          innerClassName="w-full justify-between gap-4"
        >
          <HStack className="min-w-0 items-center gap-3">
            <IconTile icon="test-tube" size="base" accent="primary" />
            <VStack className="min-w-0 items-start gap-0.5">
              <HStack className="items-center gap-2">
                <Text level="label-small" className="text-content-layout-1">
                  Local compatibility lab
                </Text>
                <Tag
                  size="small"
                  variant="neutral"
                  modifier="ghost"
                  label="Advanced"
                />
              </HStack>
              <Text level="caption" className="text-content-layout-3">
                Verify this query in an isolated Readyset sandbox.
              </Text>
            </VStack>
          </HStack>
          <Icon
            name="chevron-down"
            label="Toggle local compatibility lab"
            className={`h-4 w-4 shrink-0 text-content-layout-3 transition-transform ${
              isOpen ? 'rotate-180' : ''
            }`}
          />
        </Button>

        <AnimatePresence initial={false}>
          <Show when={isOpen}>
            <m.div
              className="overflow-hidden"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.25 }}
            >
              <VStack className="items-stretch gap-4 border-t border-border-layout-1 px-5 py-5">
                <HStack className="items-start gap-2">
                  <Icon
                    name="info"
                    label="Sandbox information"
                    className="mt-0.5 h-4 w-4 shrink-0 text-content-layout-3"
                  />
                  <Text level="caption" className="text-content-layout-3">
                    RDST prepares a throwaway Readyset cache, validates its
                    result against the selected database, measures both paths,
                    then removes the cache.
                  </Text>
                </HStack>

                {run?.result && <ComparisonCard result={run.result} />}

                {run?.status === 'failed' && (
                  <div className="rounded-xl border border-border-negative-soft bg-surface-negative-soft px-4 py-3">
                    <Text
                      level="caption"
                      className="text-content-negative-soft"
                    >
                      {run.message}
                    </Text>
                  </div>
                )}

                <HStack className="flex-wrap items-center justify-between gap-3 border-t border-border-layout-soft pt-4">
                  <Text level="caption" className="text-content-layout-3">
                    {running
                      ? run?.message || 'Preparing the Readyset sandbox...'
                      : run?.result
                        ? 'Run again with the current SQL.'
                        : 'No persistent cache or database changes.'}
                  </Text>
                  <Button
                    variant="rising"
                    modifier="solid"
                    label={
                      running
                        ? 'Testing query'
                        : run?.result
                          ? 'Run again'
                          : 'Test query'
                    }
                    icon="play"
                    loading={running}
                    disabled={disabled || !target || !query?.trim() || running}
                    onClick={() => void startTest()}
                  />
                </HStack>
              </VStack>
            </m.div>
          </Show>
        </AnimatePresence>
      </Card.Content>
    </Card>
  )
}
