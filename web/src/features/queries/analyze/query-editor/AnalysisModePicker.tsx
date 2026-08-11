import { BaseInputRadioGroup } from '@rs/ui-new/base-input-radio-group'
import { Button } from '@rs/ui-new/button'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'

const ANALYSIS_MODE_OPTIONS = [
  {
    value: 'detailed',
    label: 'Detailed analysis',
    description:
      'Runs the query with EXPLAIN ANALYZE to measure actual performance.',
    badge: (
      <Tag
        size="small"
        variant="neutral"
        modifier="ghost"
        label="Recommended"
      />
    ),
  },
  {
    value: 'quick',
    label: 'Quick analysis',
    description:
      'Checks the estimated plan without running the query. Faster, with fewer runtime details.',
  },
]

interface AnalysisModePickerProps {
  fast: boolean
  onFastChange?: (fast: boolean) => void
  disabled?: boolean
}

export function AnalysisModePicker({
  fast,
  onFastChange,
  disabled,
}: AnalysisModePickerProps) {
  const [open, setOpen] = useDisclosure({})
  const mode = fast ? 'quick' : 'detailed'
  const modeLabel = fast ? 'Quick analysis' : 'Detailed analysis'

  function handleModeChange(nextMode: string) {
    onFastChange?.(nextMode === 'quick')
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="small"
          variant="primary"
          modifier="ghost"
          label={modeLabel}
          aria-label={`Analysis mode, ${modeLabel}`}
          icon="chevron-down"
          iconPosition="right"
          disabled={disabled || !onFastChange}
          classMerge="bg-transparent text-content-layout-3 hover:bg-surface-layout-2 hover:text-content-layout-2"
        />
      </PopoverTrigger>

      <PopoverContent
        side="top"
        align="start"
        variant="layout"
        className="w-96 flex-col p-0"
      >
        <VStack className="items-start gap-1 px-4 pb-3 pt-4">
          <Text level="label-medium" className="text-content-layout-1">
            Analysis mode
          </Text>
          <Text level="caption" className="text-content-layout-3">
            Choose whether to measure the query or inspect its estimated plan.
          </Text>
        </VStack>

        <div className="px-3 pb-3">
          <BaseInputRadioGroup
            aria-label="Analysis mode"
            value={mode}
            onValueChange={handleModeChange}
            options={ANALYSIS_MODE_OPTIONS}
          />
        </div>
      </PopoverContent>
    </Popover>
  )
}
