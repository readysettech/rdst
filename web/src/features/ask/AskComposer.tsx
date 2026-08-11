import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

interface AskComposerProps {
  question: string
  target: string
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
          <BaseInputTextarea
            aria-label="Database question"
            placeholder="For example: Which customers placed the most orders this month?"
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            rows={7}
            className="w-full text-body-medium"
            disabled={disabled}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                onSubmit()
              }
            }}
          />

          <HStack className="items-center justify-between gap-4 flex-wrap">
            <HStack className="items-center gap-2 rounded-lg bg-surface-info-soft px-3 py-2">
              <Icon
                name="user-shield"
                label="Read-only"
                className="size-4 shrink-0 text-content-info-soft"
              />
              <Text level="caption" className="text-content-info-soft">
                Read-only on <span className="font-semibold">{target}</span> ·
                writes blocked · up to 1,000 rows
              </Text>
            </HStack>
            <Button
              onClick={onSubmit}
              disabled={disabled || !question.trim()}
              variant="rising"
              modifier="solid"
              label="Get answer"
              icon="sparkles"
              iconPosition="left"
            />
          </HStack>

          <div className="border-t border-border-layout-1 pt-4">
            <HStack className="mb-3 items-center justify-between gap-3">
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                Questions for this schema
              </Text>
              <Text level="caption" className="text-content-layout-3">
                Enter to ask · Shift+Enter for a new line
              </Text>
            </HStack>

            {examplesLoading && (
              <div className="grid gap-2 tablet:grid-cols-2">
                <Skeleton className="h-11 rounded-lg" />
                <Skeleton className="h-11 rounded-lg" />
              </div>
            )}

            {examplesError && (
              <HStack className="items-center justify-between gap-3 rounded-lg bg-surface-layout-2 px-3 py-2">
                <Text level="caption" className="text-content-layout-3">
                  Schema examples could not be loaded. You can still ask your
                  own question.
                </Text>
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Try again"
                  onClick={onRetryExamples}
                />
              </HStack>
            )}

            {!examplesLoading && !examplesError && examples.length > 0 && (
              <div className="grid gap-2 tablet:grid-cols-2">
                {examples.slice(0, 4).map((example) => (
                  <Pressable
                    key={example}
                    type="button"
                    disabled={disabled}
                    onClick={() => onExample(example)}
                    className="rounded-lg border border-border-layout-1 bg-surface-layout-1 px-3 py-2.5 text-left transition-colors hover:border-border-primary-soft hover:bg-surface-primary-soft disabled:opacity-50"
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
