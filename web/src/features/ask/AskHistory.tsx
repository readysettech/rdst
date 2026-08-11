import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { EmptyState } from '@rs/ui-new/empty-state'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMemo, useState } from 'react'
import type { AskHistoryItem } from '../../lib/api'
import { formatTimestamp } from '../../lib/formatters'

interface AskHistoryProps {
  items: AskHistoryItem[]
  disabled: boolean
  loading: boolean
  error: boolean
  onReask: (question: string) => void
  onRetry: () => void
}

export function AskHistory({
  items,
  disabled,
  loading,
  error,
  onReask,
  onRetry,
}: AskHistoryProps) {
  const [filter, setFilter] = useState('')
  const shown = useMemo(() => {
    const query = filter.trim().toLowerCase()
    return query
      ? items.filter((item) => item.question.toLowerCase().includes(query))
      : items
  }, [filter, items])

  return (
    <Card className="h-full overflow-hidden">
      <Card.Header className="border-b border-border-layout-1">
        <HStack className="w-full items-center justify-between gap-3">
          <VStack className="items-start gap-1">
            <Card.Title>Recent questions</Card.Title>
            <Text level="caption" className="text-content-layout-3">
              Revisit an answer with the latest data.
            </Text>
          </VStack>
          {items.length > 0 && (
            <Text level="caption" className="text-content-layout-3">
              {items.length}
            </Text>
          )}
        </HStack>
      </Card.Header>
      <Card.Content className="p-4">
        {loading && (
          <VStack className="items-stretch gap-3">
            <Skeleton className="h-9 rounded-lg" />
            <Skeleton className="h-16 rounded-lg" />
            <Skeleton className="h-16 rounded-lg" />
          </VStack>
        )}

        {error && (
          <VStack className="items-start gap-3 rounded-lg bg-surface-negative-soft/20 p-4">
            <HStack className="items-start gap-2">
              <Icon
                name="alert"
                label="Error"
                className="mt-0.5 size-4 text-content-negative-soft"
              />
              <Text level="body-small" className="text-content-layout-2">
                Recent questions could not be loaded. Asking a new question
                still works.
              </Text>
            </HStack>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Try again"
              onClick={onRetry}
            />
          </VStack>
        )}

        {!loading && !error && items.length === 0 && (
          <EmptyState
            layout="compact"
            icon="message-multiple"
            title="No questions yet"
            body="Your completed questions will appear here for quick reuse."
          />
        )}

        {!loading && !error && items.length > 0 && (
          <VStack className="items-stretch gap-3">
            <BaseInputText
              name="ask-history-search"
              placeholder="Search questions"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
            <VStack className="max-h-[25rem] items-stretch gap-2 overflow-y-auto pr-1">
              {shown.map((item) => (
                <Pressable
                  type="button"
                  key={item.hash}
                  disabled={disabled}
                  onClick={() => onReask(item.question)}
                  className="rounded-lg border border-border-layout-1 bg-surface-layout-1 p-3 text-left transition-colors hover:border-border-primary-soft hover:bg-surface-primary-soft disabled:opacity-50"
                >
                  <Text
                    level="label-small"
                    className="line-clamp-2 text-content-layout-1"
                  >
                    {item.question}
                  </Text>
                  <HStack className="mt-2 items-center gap-2">
                    <Text level="caption" className="text-content-layout-3">
                      {item.last_used
                        ? formatTimestamp(item.last_used)
                        : 'Previously asked'}
                    </Text>
                    <Icon
                      name="arrow-right"
                      label="Ask again"
                      className="ml-auto size-4 text-content-layout-3"
                    />
                  </HStack>
                </Pressable>
              ))}
              {shown.length === 0 && (
                <Text
                  level="body-small"
                  className="py-6 text-center text-content-layout-3"
                >
                  No recent question matches “{filter}”.
                </Text>
              )}
            </VStack>
          </VStack>
        )}
      </Card.Content>
    </Card>
  )
}
