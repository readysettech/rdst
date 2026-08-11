import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { m } from '@rs/ui-new/motion'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import type { ProgressEvent } from '../../../lib/api'

interface AnalysisStage {
  label: string
  description: string
  icon: 'querypilot' | 'speedometer' | 'sparkles' | 'database-settings'
}

const stages: AnalysisStage[] = [
  {
    label: 'Preparing query',
    description: 'Validating SQL and loading query context.',
    icon: 'querypilot',
  },
  {
    label: 'Measuring performance',
    description: 'Running EXPLAIN ANALYZE and collecting execution metrics.',
    icon: 'speedometer',
  },
  {
    label: 'Evaluating improvements',
    description: 'Testing rewrites and reviewing suggested indexes.',
    icon: 'sparkles',
  },
  {
    label: 'Checking Readyset fit',
    description: 'Verifying whether Readyset can cache this query.',
    icon: 'database-settings',
  },
]

const stageIndex: Record<string, number> = {
  loading_config: 0,
  validating: 0,
  normalizing: 0,
  executing_explain: 1,
  collecting_metrics: 1,
  collecting_schema: 1,
  analyzing_llm: 2,
  testing_rewrites: 2,
  checking_readyset: 3,
  storing_results: 3,
  complete: 3,
}

function ResultSkeleton() {
  return (
    <div className="space-y-4">
      <Card aria-hidden="true">
        <Card.Header className="flex-row items-center gap-3">
          <Skeleton className="h-10 w-10 shrink-0 rounded-xl" />
          <div className="space-y-2">
            <Skeleton className="h-5 w-36 rounded-lg" />
            <Skeleton className="h-3 w-64 rounded-lg" />
          </div>
        </Card.Header>
        <Card.Content className="grid gap-4 p-4 desktop:grid-cols-3">
          <Skeleton className="min-h-64 rounded-2xl" />
          <div className="space-y-4 p-6 desktop:col-span-2">
            <Skeleton className="h-7 w-48 rounded-lg" />
            <Skeleton className="h-4 w-full rounded-lg" />
            <Skeleton className="h-4 w-3/4 rounded-lg" />
            <Skeleton className="h-20 w-full rounded-xl" />
          </div>
        </Card.Content>
        <div className="grid gap-1 tablet:grid-cols-3">
          <Card.Content className="p-4">
            <Skeleton className="h-10 rounded-xl" />
          </Card.Content>
          <Card.Content className="p-4">
            <Skeleton className="h-10 rounded-xl" />
          </Card.Content>
          <Card.Content className="p-4">
            <Skeleton className="h-10 rounded-xl" />
          </Card.Content>
        </div>
      </Card>

      <div className="grid items-stretch gap-4 desktop:grid-cols-2">
        <Card aria-hidden="true" className="h-full">
          <Card.Header className="items-stretch gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
            <div className="flex items-center gap-3">
              <Skeleton className="h-10 w-10 shrink-0 rounded-xl" />
              <div className="space-y-2">
                <Skeleton className="h-5 w-40 rounded-lg" />
                <Skeleton className="h-3 w-56 rounded-lg" />
              </div>
            </div>
            <Skeleton className="h-7 w-20 self-end rounded-lg tablet:self-auto" />
          </Card.Header>
          <Card.Content className="flex flex-1 flex-col gap-8 p-6">
            <div className="space-y-3">
              <Skeleton className="h-6 w-3/4 rounded-lg" />
              <Skeleton className="h-4 w-full rounded-lg" />
              <Skeleton className="h-4 w-5/6 rounded-lg" />
            </div>
            <div className="mt-auto flex h-36 flex-col overflow-hidden rounded-xl border border-border-layout-1">
              <div className="border-b border-border-layout-1 px-4 py-3">
                <Skeleton className="h-4 w-32 rounded-lg" />
              </div>
              <div className="min-h-0 flex-1 p-4">
                <Skeleton className="h-full w-full rounded-xl" />
              </div>
            </div>
          </Card.Content>
          <Card.Footer className="min-h-18">
            <Skeleton className="h-8 w-24 rounded-xl" />
          </Card.Footer>
        </Card>

        <Card aria-hidden="true" className="h-full">
          <Card.Header className="items-stretch gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
            <div className="flex items-center gap-3">
              <Skeleton className="h-10 w-10 shrink-0 rounded-xl" />
              <div className="space-y-2">
                <Skeleton className="h-5 w-44 rounded-lg" />
                <Skeleton className="h-3 w-64 rounded-lg" />
              </div>
            </div>
            <Skeleton className="h-7 w-20 self-end rounded-lg tablet:self-auto" />
          </Card.Header>
          <Card.Content className="flex flex-1 flex-col gap-8 p-6">
            <div className="space-y-3">
              <Skeleton className="h-6 w-2/3 rounded-lg" />
              <Skeleton className="h-4 w-full rounded-lg" />
              <Skeleton className="h-4 w-4/5 rounded-lg" />
            </div>
            <div className="mt-auto flex h-36 flex-col overflow-hidden rounded-xl border border-border-layout-1">
              <div className="flex items-center justify-between gap-4 border-b border-border-layout-1 px-4 py-3">
                <Skeleton className="h-4 w-32 rounded-lg" />
                <Skeleton className="h-4 w-20 rounded-lg" />
              </div>
              <div className="min-h-0 flex-1 p-4">
                <Skeleton className="h-full w-full rounded-xl" />
              </div>
            </div>
          </Card.Content>
          <Card.Footer className="min-h-18">
            <Skeleton className="h-8 w-36 rounded-xl" />
          </Card.Footer>
        </Card>
      </div>
    </div>
  )
}

export function ResultsLoading({ progress }: { progress?: ProgressEvent }) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), 120)
    return () => window.clearTimeout(timer)
  }, [])

  if (!visible) return null

  const currentIndex = stageIndex[progress?.stage ?? 'normalizing'] ?? 0
  const currentStage = stages[currentIndex]

  return (
    <m.div
      className="space-y-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25 }}
    >
      <Card role="status" aria-live="polite" aria-label="Analysis in progress">
        <Card.Header className="items-stretch gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
          <HStack className="min-w-0 items-center gap-3">
            <IconTile icon="sparkles" accent="rising" size="base" />
            <VStack className="min-w-0 items-start gap-1">
              <Card.Title>Analysis in progress</Card.Title>
              <Card.Description>{currentStage.description}</Card.Description>
            </VStack>
          </HStack>
          <div className="self-end tablet:self-auto">
            <Tag
              variant="neutral"
              modifier="ghost"
              label={`Step ${currentIndex + 1} of ${stages.length}`}
            />
          </div>
        </Card.Header>

        <Card.Content className="p-4">
          <div className="grid gap-3 tablet:grid-cols-2 desktop:grid-cols-4">
            {stages.map((stage, index) => {
              const complete = index < currentIndex
              const active = index === currentIndex

              return (
                <div
                  key={stage.label}
                  className={`flex min-w-0 items-center gap-3 rounded-xl border p-3 ${
                    active
                      ? 'border-border-primary-soft bg-surface-primary-soft'
                      : 'border-border-layout-1 bg-surface-layout-2/40'
                  }`}
                >
                  <div
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${
                      complete
                        ? 'bg-surface-positive-soft'
                        : active
                          ? 'bg-surface-layout-1'
                          : 'bg-surface-layout-2'
                    }`}
                  >
                    {complete ? (
                      <Icon
                        name="tick-double"
                        label={`${stage.label} complete`}
                        className="h-4 w-4 text-content-positive-soft"
                      />
                    ) : active ? (
                      <Spinner size="base" />
                    ) : (
                      <Icon
                        name={stage.icon}
                        label=""
                        aria-hidden="true"
                        className="h-4 w-4 text-content-layout-3"
                      />
                    )}
                  </div>
                  <VStack className="min-w-0 items-start gap-0.5">
                    <Text level="caption" className="text-content-layout-3">
                      Step {index + 1}
                    </Text>
                    <Text
                      level="label-small"
                      className={
                        active
                          ? 'text-content-layout-1'
                          : complete
                            ? 'text-content-positive-soft'
                            : 'text-content-layout-2'
                      }
                    >
                      {stage.label}
                    </Text>
                  </VStack>
                </div>
              )
            })}
          </div>
        </Card.Content>
      </Card>

      <ResultSkeleton />
    </m.div>
  )
}
