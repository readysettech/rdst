import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { IconButton } from '@rs/ui-new/icon-button'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Show } from '@rs/ui-new/show'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

interface SavedQueriesControlCardProps {
  searchTerm: string
  onSearchChange: (value: string) => void
  sourceFilter: string
  sourceOptions: Array<{ label: string; count: number }>
  searchResultCount: number
  filteredCount: number
  total: number
  isFetching: boolean
  hasPreviousPage: boolean
  hasNextPage: boolean
  onSelectSource: (value: string) => void
  onPreviousPage: () => void
  onNextPage: () => void
  onOpenBenchmark: () => void
  onAddQuery: () => void
}

export function SavedQueriesControlCard({
  searchTerm,
  onSearchChange,
  sourceFilter,
  sourceOptions,
  searchResultCount,
  filteredCount,
  total,
  isFetching,
  hasPreviousPage,
  hasNextPage,
  onSelectSource,
  onPreviousPage,
  onNextPage,
  onOpenBenchmark,
  onAddQuery,
}: SavedQueriesControlCardProps) {
  const hasPagination = hasPreviousPage || hasNextPage

  return (
    <Card>
      <Card.Content className="flex flex-col gap-3 p-4">
        <HStack className="justify-between items-center gap-4 flex-wrap">
          <div className="relative w-72 max-w-full">
            <BaseInputText
              name="search"
              placeholder="Search name, hash, or SQL..."
              icon="search"
              iconPosition="left"
              value={searchTerm}
              onChange={(event) => onSearchChange(event.target.value)}
            />
            <Show when={searchTerm.length > 0}>
              <IconButton
                variant="primary"
                modifier="ghost"
                size="small"
                icon="close"
                label="Clear search"
                onClick={() => onSearchChange('')}
                className="absolute right-1 top-1/2 -translate-y-1/2"
              />
            </Show>
          </div>

          <HStack className="gap-2 items-center shrink-0">
            <Button
              variant="primary"
              modifier="ghost"
              label="Compare…"
              icon="play"
              iconPosition="left"
              onClick={onOpenBenchmark}
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Add query"
              icon="add"
              iconPosition="left"
              onClick={onAddQuery}
            />
          </HStack>
        </HStack>

        <HStack className="justify-between items-center gap-4 flex-wrap">
          <Show when={sourceOptions.length > 1}>
            <SegmentedControl
              aria-label="Source filter"
              mode="radio"
              size="small"
              value={sourceFilter}
              onValueChange={onSelectSource}
              segments={[
                { value: 'all', label: `All ${searchResultCount}` },
                ...sourceOptions.map(({ label, count }) => ({
                  value: label,
                  label: `${label} ${count}`,
                })),
              ]}
            />
          </Show>

          <HStack className="gap-3 items-center shrink-0 ml-auto">
            <Text level="body-small" className="text-content-layout-3">
              {searchTerm.trim()
                ? `${filteredCount} of ${total}`
                : `${total} ${total === 1 ? 'query' : 'queries'}`}
            </Text>
            <Show when={hasPagination}>
              <HStack className="gap-2">
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Previous"
                  icon="arrow-left"
                  iconPosition="left"
                  disabled={!hasPreviousPage || isFetching}
                  onClick={onPreviousPage}
                />
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Next"
                  icon="arrow-right"
                  iconPosition="right"
                  disabled={!hasNextPage || isFetching}
                  onClick={onNextPage}
                />
              </HStack>
            </Show>
          </HStack>
        </HStack>
      </Card.Content>
    </Card>
  )
}
