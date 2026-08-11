import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Label } from '@rs/ui-new/label'
import { Show } from '@rs/ui-new/show'
import { HStack } from '@rs/ui-new/stack'
import type { TopState } from '../../../types/top'
import { SlowQueryContext } from './SlowQueryContext'

export interface SlowQueryResultsHeaderProps {
  targetLabel: string
  sourceLabel: string
  engineLabel?: string
  state: TopState
  isRealtime: boolean
  sort?: string
  setSort?: (sort: string) => void
  onSaveAll?: () => void
  canSave?: boolean
  autoSave?: boolean
  setAutoSave?: (autoSave: boolean) => void
}

const sortOptions = [
  { value: 'total_time', label: 'Total time' },
  { value: 'freq', label: 'Frequency' },
  { value: 'avg_time', label: 'Average time' },
  { value: 'load', label: 'Database load' },
]

export function SlowQueryResultsHeader({
  targetLabel,
  sourceLabel,
  engineLabel,
  state,
  isRealtime,
  sort,
  setSort,
  onSaveAll,
  canSave,
  autoSave,
  setAutoSave,
}: SlowQueryResultsHeaderProps) {
  const activeSort = sort ?? 'total_time'
  const sortLabel =
    sortOptions.find((option) => option.value === activeSort)?.label ??
    'Total time'
  const showSort = !!setSort && !isRealtime

  return (
    <Card.Content
      className="p-4"
      data-testid="slow-query-results-header-content"
    >
      <HStack className="justify-between items-center gap-4 flex-wrap">
        <SlowQueryContext
          targetLabel={targetLabel}
          sourceLabel={sourceLabel}
          engineLabel={engineLabel}
        />

        <HStack className="gap-3 items-center flex-wrap">
          <Show when={showSort}>
            <Dropdown>
              {/* Sort is a next-run parameter, so the trigger states that scope. */}
              <Dropdown.Trigger asChild>
                <Button
                  variant="primary"
                  modifier="link"
                  size="small"
                  label={`Sort: ${sortLabel}`}
                  icon="chevron-down"
                  iconPosition="right"
                  className="no-underline"
                />
              </Dropdown.Trigger>
              <Dropdown.Content align="end" className="min-w-44">
                <Dropdown.Label>Applies to the next run</Dropdown.Label>
                {sortOptions.map((option) => (
                  <Dropdown.Item
                    key={option.value}
                    label={option.label}
                    active={option.value === activeSort}
                    rightIcon={option.value === activeSort ? 'tick' : undefined}
                    onClick={() => setSort?.(option.value)}
                  />
                ))}
              </Dropdown.Content>
            </Dropdown>
          </Show>

          <Show when={!!setAutoSave}>
            <HStack className="gap-2 items-center">
              <BaseInputSwitch
                id="top-auto-save"
                name="auto-save"
                checked={!!autoSave}
                onCheckedChange={(checked) => setAutoSave?.(checked === true)}
                disabled={state === 'streaming'}
              />
              <Label
                htmlFor="top-auto-save"
                className="text-label-small text-content-layout-2"
              >
                Save automatically
              </Label>
            </HStack>
          </Show>

          <Show when={!!canSave && !!onSaveAll}>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Save all"
              icon="add"
              iconPosition="left"
              onClick={onSaveAll}
            />
          </Show>
        </HStack>
      </HStack>
    </Card.Content>
  )
}
