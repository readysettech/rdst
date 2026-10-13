// The day menu in the report head: the day shown as a ghost trigger, then every kept
// report with its date, "Latest" on the newest, and its fixes as a word; "All reports"
// under a hairline leads to Activity.
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { Tag } from '@rs/ui-new/tag'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import type { DayMenuEntry } from './reportModel'

export function DayPicker({
  days,
  selectedId,
  target,
  onPick,
}: {
  days: DayMenuEntry[]
  selectedId: number | null
  target: string
  onPick: (id: number) => void
}) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const selected = days.find((entry) => entry.id === selectedId) ?? days[0]
  if (!selected) return null
  return (
    <Dropdown open={open} onOpenChange={setOpen}>
      <Dropdown.Trigger asChild>
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          icon="chevron-down"
          iconPosition="right"
          label={selected.label}
          aria-label={`${selected.label}. Pick a day`}
          className="print:hidden"
          data-testid="day-picker-trigger"
        />
      </Dropdown.Trigger>
      <Dropdown.Content
        align="end"
        className="w-58 rounded-panel bg-surface-primary-soft p-1.5"
      >
        {days.map((entry) => (
          <Dropdown.ItemWithChildren
            key={entry.id}
            active={entry.id === selected.id}
            className="h-8 min-w-0 rounded-md px-2.5 text-body-small"
            onClick={() => onPick(entry.id)}
          >
            <span className="flex w-full items-center gap-2.5">
              <span className="inline-flex w-3.5 justify-center text-content-layout-2">
                {entry.id === selected.id && (
                  <Icon name="tick" label="" size="small" />
                )}
              </span>
              <span className="text-content-layout-1">{entry.label}</span>
              {entry.latest && (
                <Tag
                  size="small"
                  variant="neutral"
                  modifier="outline"
                  className="rounded-pill"
                  label="Latest"
                />
              )}
              <span className="ml-auto pl-4 text-caption text-content-layout-3">
                {entry.word}
              </span>
            </span>
          </Dropdown.ItemWithChildren>
        ))}
        <Dropdown.Separator />
        <Dropdown.ItemWithChildren
          className="h-8 min-w-0 rounded-md px-2.5 text-body-small"
          onClick={() =>
            void navigate({
              to: '/agent-activity',
              search: { target } as never,
            })
          }
        >
          <span className="flex w-full items-center gap-2.5">
            <span className="inline-flex w-3.5" />
            <span className="text-content-layout-1">All reports</span>
          </span>
        </Dropdown.ItemWithChildren>
      </Dropdown.Content>
    </Dropdown>
  )
}
