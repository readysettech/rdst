import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { AskSchemaLoadedEvent, AskStatusEvent } from '../../lib/ask'

const STAGES = [
  {
    id: 'schema',
    phases: ['config', 'schema'],
    label: 'Inspect schema',
    icon: 'database' as const,
  },
  {
    id: 'understanding',
    phases: ['filter', 'clarify'],
    label: 'Understand question',
    icon: 'sparkles' as const,
  },
  {
    id: 'sql',
    phases: ['generate', 'validate', 'execute'],
    label: 'Build and verify SQL',
    icon: 'querypilot' as const,
  },
]

function sourceLabel(source?: string) {
  return source === 'semantic' ? 'semantic layer' : 'live database schema'
}

interface AskProgressProps {
  question: string
  status?: AskStatusEvent
  schemaLoaded?: AskSchemaLoadedEvent
  onCancel: () => void
}

export function AskProgress({
  question,
  status,
  schemaLoaded,
  onCancel,
}: AskProgressProps) {
  const phaseIndex = STAGES.findIndex((stage) =>
    stage.phases.includes(status?.phase ?? '')
  )
  const currentIndex = phaseIndex < 0 ? 0 : phaseIndex

  return (
    <Card className="overflow-hidden">
      <Card.Header className="border-b border-border-layout-1">
        <VStack className="items-start gap-1">
          <Card.Title>Preparing your answer</Card.Title>
          <Text level="body-small" className="text-content-layout-3">
            {question}
          </Text>
        </VStack>
      </Card.Header>
      <Card.Content className="p-6">
        <div className="grid gap-6 laptop:grid-cols-[minmax(18rem,0.8fr)_minmax(0,1.2fr)]">
          <VStack className="items-stretch gap-2">
            {STAGES.map((stage, index) => {
              const complete = index < currentIndex
              const current = index === currentIndex
              return (
                <HStack
                  key={stage.id}
                  className={`items-center gap-3 rounded-xl border px-4 py-3 ${
                    current
                      ? 'border-border-primary-soft bg-surface-primary-soft'
                      : 'border-border-layout-1 bg-surface-layout-1'
                  }`}
                >
                  <div
                    className={`flex size-9 items-center justify-center rounded-lg ${
                      complete
                        ? 'bg-surface-positive-soft'
                        : 'bg-surface-layout-2'
                    }`}
                  >
                    {current ? (
                      <Spinner size="base" color="primary-soft" />
                    ) : (
                      <Icon
                        name={complete ? 'tick-double' : stage.icon}
                        label=""
                        className={`size-4 ${
                          complete
                            ? 'text-content-positive-soft'
                            : 'text-content-layout-2'
                        }`}
                      />
                    )}
                  </div>
                  <VStack className="items-start gap-0.5">
                    <Text level="label-small" className="text-content-layout-1">
                      {stage.label}
                    </Text>
                    {current && (
                      <Text
                        level="caption"
                        className="text-content-primary-soft"
                      >
                        {status?.message || 'Working…'}
                      </Text>
                    )}
                  </VStack>
                </HStack>
              )
            })}
            {schemaLoaded && (
              <Text level="caption" className="px-1 pt-2 text-content-layout-3">
                {schemaLoaded.table_count} tables loaded from{' '}
                {sourceLabel(schemaLoaded.source)}.
              </Text>
            )}
          </VStack>

          <div className="rounded-xl border border-border-layout-1 bg-surface-layout-1 p-5">
            <VStack className="items-stretch gap-4">
              <HStack className="items-center justify-between gap-3">
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-wider"
                >
                  Answer preview
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  Read-only
                </Text>
              </HStack>
              <Skeleton className="h-5 w-2/5 rounded-md" />
              <Skeleton className="h-10 rounded-lg" />
              <Skeleton className="h-10 rounded-lg" />
              <Skeleton className="h-10 w-4/5 rounded-lg" />
            </VStack>
          </div>
        </div>
      </Card.Content>
      <Card.Footer className="border-t border-border-layout-1">
        <HStack className="w-full items-center justify-between gap-4">
          <Text level="caption" className="text-content-layout-3">
            You can cancel without changing the database.
          </Text>
          <Button
            variant="negative"
            modifier="outline"
            size="small"
            label="Cancel"
            onClick={onCancel}
          />
        </HStack>
      </Card.Footer>
    </Card>
  )
}
