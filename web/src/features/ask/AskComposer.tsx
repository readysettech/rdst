import { Card } from '@rs/ui-new/card'
import { InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { PromptComposer } from '../../components/PromptComposer'

interface AskComposerProps {
  question: string
  /** `null` when no target has been resolved: the composer names no database. */
  target: string | null
  disabled: boolean
  examples: string[]
  examplesLoading: boolean
  examplesError: boolean
  onQuestionChange: (question: string) => void
  onSubmit: () => void
  onExample: (question: string) => void
  onRetryExamples: () => void
}

export function AskComposer({
  question,
  target,
  disabled,
  examples,
  examplesLoading,
  examplesError,
  onQuestionChange,
  onSubmit,
  onExample,
  onRetryExamples,
}: AskComposerProps) {
  const shownExamples = examples.slice(0, 4)

  return (
    <Card className="h-full overflow-hidden">
      <Card.Header className="border-b border-border-layout-1">
        <VStack className="items-start gap-1">
          <Card.Title>Ask your database</Card.Title>
          <Text level="body-small" className="text-content-layout-3">
            Describe the answer you need. RDST will inspect the schema and run
            verified read-only SQL.
          </Text>
        </VStack>
      </Card.Header>
      <Card.Content className="p-5">
        <VStack className="items-stretch gap-4">
          <PromptComposer
            value={question}
            onChange={onQuestionChange}
            onSubmit={onSubmit}
            label="Database question"
            placeholder="For example: Which customers placed the most orders this month?"
            disabled={disabled}
            submitLabel="Get answer"
            submitIcon="sparkles"
            submitVariant="rising"
            fieldClassName="min-h-[7lh] max-h-[14lh]"
            accessory={
              /* Naming a database RDST was never told to use is worse than
                 naming none: it invites a question against the wrong one. */
              target ? (
                <HStack className="items-center gap-2 rounded-lg bg-surface-info-soft px-3 py-2">
                  <Icon
                    name="user-shield"
                    label="Read-only"
                    className="size-4 shrink-0 text-content-info-soft"
                  />
                  <Text level="caption" className="text-content-info-soft">
                    Read-only on <strong>{target}</strong> · writes blocked · up
                    to 1,000 rows
                  </Text>
                </HStack>
              ) : (
                <HStack className="items-center gap-2 rounded-lg bg-surface-warning-soft px-3 py-2">
                  <Icon
                    name="alert"
                    label="No database"
                    className="size-4 shrink-0 text-content-warning-soft"
                  />
                  <Text level="caption" className="text-content-warning-soft">
                    No database selected — choose one before asking
                  </Text>
                </HStack>
              )
            }
          />

          <div className="border-t border-border-layout-1 pt-4">
            <Text
              level="overline"
              className="mb-3 block text-content-layout-3 uppercase tracking-wider"
            >
              Questions for this schema
            </Text>

            {examplesLoading && (
              <div className="grid gap-2 tablet:grid-cols-2">
                <Skeleton className="h-11 rounded-lg" />
                <Skeleton className="h-11 rounded-lg" />
              </div>
            )}

            {examplesError && (
              <InlineNotice
                errorClass="rdst-service"
                title="Schema examples could not be loaded"
                message="You can still ask your own question."
                onRetry={onRetryExamples}
              />
            )}

            {!examplesLoading && !examplesError && examples.length > 0 && (
              // Equal rows, and an odd last card takes the whole row: the grid
              // reads as laid out at one, two, three or four examples (C-39).
              <div className="grid auto-rows-fr gap-2 tablet:grid-cols-2">
                {shownExamples.map((example, index) => (
                  <Pressable
                    key={example}
                    type="button"
                    disabled={disabled}
                    onClick={() => onExample(example)}
                    className={`h-full rounded-lg border border-border-layout-1 bg-surface-layout-1 px-3 py-2.5 text-left transition-colors hover:border-border-primary-soft hover:bg-surface-primary-soft disabled:opacity-50 ${
                      index === shownExamples.length - 1 &&
                      shownExamples.length % 2 === 1
                        ? 'tablet:col-span-2'
                        : ''
                    }`}
                  >
                    <Text
                      level="label-small"
                      className="line-clamp-2 text-content-layout-2"
                    >
                      {example}
                    </Text>
                  </Pressable>
                ))}
              </div>
            )}

            {!examplesLoading && !examplesError && examples.length === 0 && (
              <Text level="caption" className="text-content-layout-3">
                Ask any reporting, aggregation, or lookup question supported by
                this schema.
              </Text>
            )}
          </div>
        </VStack>
      </Card.Content>
    </Card>
  )
}
