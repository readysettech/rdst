import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Label } from '@rs/ui-new/label'
import {
  Modal,
  ModalClose,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Text } from '@rs/ui-new/text'
import { useEffect, useId, useState } from 'react'
import { type AgentAction, exportCsvUrl } from '@/lib/agentActivity'
import {
  ACTION_META,
  ACTIONS,
  customBounds,
  dayKey,
  RANGE_LABEL,
  rangeBounds,
} from './activityModel'

type ExportRange = 'today' | '7d' | '30d' | 'custom'

const RANGES: { value: ExportRange; label: string }[] = [
  { value: 'today', label: RANGE_LABEL.today },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'custom', label: 'Custom' },
]

const ALL = '__all__'

export interface ExportChoice {
  target?: string
  action?: AgentAction
  range: 'today' | '7d' | '30d'
}

/** Where the download points for a choice, or null while a custom range is unfinished. */
export function exportHref(
  choice: {
    target?: string
    action?: AgentAction
    range: ExportRange
    from: string
    to: string
  },
  now: number
): string | null {
  const bounds =
    choice.range === 'custom'
      ? customBounds(choice.from, choice.to)
      : rangeBounds(choice.range, now)
  if (!bounds) return null
  return exportCsvUrl({
    target: choice.target,
    action: choice.action,
    ...bounds,
  })
}

export function ExportDialog({
  open,
  onClose,
  targets,
  initial,
}: {
  open: boolean
  onClose: () => void
  targets: string[]
  initial: ExportChoice
}) {
  const now = Date.now() / 1000
  const today = dayKey(now)
  const [range, setRange] = useState<ExportRange>(initial.range)
  const [target, setTarget] = useState(initial.target)
  const [action, setAction] = useState(initial.action)
  const [from, setFrom] = useState(dayKey(now - 6 * 86400))
  const [to, setTo] = useState(today)
  const ids = { from: useId(), to: useId(), target: useId(), action: useId() }

  // Each opening starts from what the page is showing.
  useEffect(() => {
    if (!open) return
    setRange(initial.range)
    setTarget(initial.target)
    setAction(initial.action)
  }, [open, initial.range, initial.target, initial.action])

  const href = exportHref({ target, action, range, from, to }, now)
  const download = () => {
    if (!href) return
    window.location.assign(href)
    onClose()
  }

  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContentContainer open={open}>
        <ModalContent size="base" className="overflow-hidden p-0">
          <div className="border-b border-border-layout-1 p-5">
            <ModalTitle className="h-auto">Export agent activity</ModalTitle>
            <ModalDescription className="mt-1">
              A CSV with one row for everything the agent did in the range, each
              attention check included. Result rows and sampled values are never
              in it.
            </ModalDescription>
          </div>
          <div className="space-y-5 p-5">
            <div className="space-y-2">
              <Text
                as="p"
                level="label-medium"
                className="text-content-layout-1"
              >
                Time range
              </Text>
              <SegmentedControl<ExportRange>
                aria-label="Time range"
                mode="radio"
                value={range}
                onValueChange={setRange}
                segments={RANGES}
              />
              {range === 'custom' ? (
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <div className="space-y-1.5">
                    <Label htmlFor={ids.from}>From</Label>
                    <BaseInputText
                      id={ids.from}
                      type="date"
                      value={from}
                      max={to || today}
                      onChange={(event) => setFrom(event.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={ids.to}>To</Label>
                    <BaseInputText
                      id={ids.to}
                      type="date"
                      value={to}
                      min={from}
                      max={today}
                      error={!href}
                      onChange={(event) => setTo(event.target.value)}
                    />
                  </div>
                  {!href && (
                    <Text
                      level="caption"
                      className="col-span-2 text-content-negative-plain"
                    >
                      Pick a start day on or before the end day.
                    </Text>
                  )}
                </div>
              ) : (
                <Text level="caption" className="block text-content-layout-3">
                  Whole days in your time zone, ending today. The log keeps 90
                  days.
                </Text>
              )}
            </div>
            <div className="grid gap-3 tablet:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor={ids.target}>Database</Label>
                <BaseInputSelect
                  id={ids.target}
                  name="target"
                  value={target ?? ALL}
                  options={[
                    { value: ALL, label: 'All databases' },
                    ...targets.map((name) => ({ value: name, label: name })),
                  ]}
                  onValueChange={(value) =>
                    setTarget(value === ALL ? undefined : value)
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={ids.action}>Action</Label>
                <BaseInputSelect
                  id={ids.action}
                  name="action"
                  value={action ?? ALL}
                  options={[
                    { value: ALL, label: 'All actions' },
                    ...ACTIONS.map((name) => ({
                      value: name,
                      label: ACTION_META[name].label,
                    })),
                  ]}
                  onValueChange={(value) =>
                    setAction(
                      value === ALL ? undefined : (value as AgentAction)
                    )
                  }
                />
              </div>
            </div>
          </div>
          <div className="flex justify-end gap-3 border-t border-border-layout-1 p-5">
            <ModalClose asChild>
              <Button label="Cancel" modifier="ghost" />
            </ModalClose>
            <Button
              label="Download CSV"
              icon="arrow-down"
              iconPosition="left"
              disabled={!href}
              onClick={download}
            />
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
