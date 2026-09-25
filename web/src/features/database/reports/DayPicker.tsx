// The day picker beside a snapshot's title: previous and next day, and a clean list of
// every day with a snapshot (one row per day, its date first, one short line under it).
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownTrigger,
} from '@rs/ui-new/dropdown-rf'
import { IconButton } from '@rs/ui-new/icon-button'
import {
  type DayEntry,
  dayShort,
  neighbors,
  relativeDay,
} from './snapshotModel'

export function DayPicker({
  days,
  selectedId,
  onPick,
}: {
  days: DayEntry[]
  selectedId: number | null
  onPick: (id: number) => void
}) {
  const { older, newer } = neighbors(days, selectedId)
  const selected = days.find((entry) => entry.id === selectedId) ?? null
  return (
    <div
      className="flex items-center gap-1 print:hidden"
      data-testid="day-picker"
    >
      <IconButton
        icon="chevron-left"
        label={
          older ? `Previous day, ${dayShort(older.day)}` : 'No earlier day'
        }
        modifier="ghost"
        size="small"
        disabled={!older}
        onClick={() => older && onPick(older.id)}
      />
      <Dropdown>
        <DropdownTrigger asChild>
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label={selected ? dayShort(selected.day) : 'Days'}
            aria-label={`${selected ? dayShort(selected.day) : 'Days'}. Pick a day`}
            icon="chevron-down"
            iconPosition="right"
            data-testid="day-picker-trigger"
          />
        </DropdownTrigger>
        <DropdownContent
          align="end"
          className="max-h-[26rem] w-[20rem] overflow-y-auto"
        >
          {days.map((entry) => {
            const relative = relativeDay(entry.day)
            return (
              <DropdownItem
                key={entry.day}
                active={entry.id === selectedId}
                onSelect={() => onPick(entry.id)}
                className="h-auto items-start py-1.5"
              >
                <span className="grid w-full min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 py-1 text-left">
                  <span className="truncate text-label-medium text-content-layout-1">
                    {dayShort(entry.day)}
                    {relative ? (
                      <span className="text-content-layout-3"> {relative}</span>
                    ) : null}
                  </span>
                  <span
                    className={cn(
                      'self-center text-caption',
                      entry.sample
                        ? 'text-content-layout-3'
                        : entry.partial
                          ? 'text-content-primary-soft'
                          : 'text-content-layout-3'
                    )}
                  >
                    {entry.sample ? 'Sample' : entry.partial ? 'So far' : ''}
                  </span>
                  <span className="col-span-2 truncate text-caption text-content-layout-2">
                    {entry.line || 'No events'}
                  </span>
                </span>
              </DropdownItem>
            )
          })}
        </DropdownContent>
      </Dropdown>
      <IconButton
        icon="chevron-right"
        label={newer ? `Next day, ${dayShort(newer.day)}` : 'No later day'}
        modifier="ghost"
        size="small"
        disabled={!newer}
        onClick={() => newer && onPick(newer.id)}
      />
    </div>
  )
}
